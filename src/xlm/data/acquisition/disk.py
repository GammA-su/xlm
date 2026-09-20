"""Shared reservations, measured consumption and separately reconciled storage occupancy."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from xlm.artifacts.manifest import bounded_children, ensure_plain_path
from xlm.data.acquisition.progress import ProgressJournal, ResourceAccount
from xlm.data.sources.transport import BudgetExhaustedError


class DiskCeilingExceededError(RuntimeError):
    """A logical storage ceiling or physical headroom guard was reached."""


class StorageCapacityManager:
    """Use the journal's existing lock/transaction for cross-process accounting.

    Transfer means response-body bytes returned to the application, not TCP/TLS
    wire bytes. A crash leaves the pending reservation unavailable; only a read
    whose outcome is known may release its unused allowance. Disk reservations
    are released only after measuring the owned tree under the plan execution lock.
    """

    def __init__(self, max_transferred_bytes: int, max_decompressed_bytes: int,
                 max_temp_disk_bytes: int, max_output_disk_bytes: int,
                 min_free_headroom_bytes: int = 50 * 1024 * 1024, *,
                 journal: ProgressJournal | None = None) -> None:
        self.max_transferred_bytes = max_transferred_bytes
        self.max_decompressed_bytes = max_decompressed_bytes
        self.max_temp_disk_bytes = max_temp_disk_bytes
        self.max_output_disk_bytes = max_output_disk_bytes
        self.min_free_headroom_bytes = min_free_headroom_bytes
        self.journal = journal
        self._lock = threading.RLock()
        self._account = ResourceAccount()
        self._cache_hits = 0
        limits = {"transfer": max_transferred_bytes, "decompressed": max_decompressed_bytes,
                  "temp": max_temp_disk_bytes, "output": max_output_disk_bytes}
        if any(value < 1 for value in limits.values()):
            raise ValueError("resource limits must be positive")
        with self._transaction() as account:
            if account.limits and account.limits != limits:
                raise ValueError("resource limits changed under an existing account")
            account.limits = limits

    @contextmanager
    def _transaction(self) -> Iterator[ResourceAccount]:
        with self._lock:
            if self.journal is None:
                yield self._account
            else:
                with self.journal.transaction() as state:
                    yield state.accounting

    def bind_deadline(self, seconds: float) -> None:
        with self._transaction() as account:
            if account.deadline_at is None:
                account.deadline_at = time.time() + seconds

    def check_deadline(self) -> None:
        with self._transaction() as account:
            if account.deadline_at is not None and time.time() >= account.deadline_at:
                raise TimeoutError("cumulative acquisition deadline reached; restart grants no new time")

    def remaining(self, resource: str) -> int:
        with self._transaction() as account:
            used = account.occupancy.get(resource, 0) if resource in ("temp", "output") else account.consumed.get(resource, 0)
            return account.limits[resource] - used - sum(account.reservations.get(resource, {}).values())

    def reserve(self, resource: str, amount: int) -> str:
        if amount < 0:
            raise ValueError("negative reservation")
        with self._transaction() as account:
            pending = account.reservations.setdefault(resource, {})
            used = account.occupancy.get(resource, 0) if resource in ("temp", "output") else account.consumed.get(resource, 0)
            if used + sum(pending.values()) + amount > account.limits[resource]:
                error = DiskCeilingExceededError if resource in ("temp", "output") else BudgetExhaustedError
                raise error(f"{resource} limit reached including outstanding reservations")
            token = uuid.uuid4().hex
            pending[token] = amount
            return token

    def settle(self, resource: str, token: str, actual: int) -> None:
        with self._transaction() as account:
            reserved = account.reservations.get(resource, {}).get(token)
            if reserved is None or not 0 <= actual <= reserved:
                raise ValueError("invalid reservation reconciliation")
            target = account.occupancy if resource in ("temp", "output") else account.consumed
            target[resource] = target.get(resource, 0) + actual
            del account.reservations[resource][token]

    def reserve_transfer(self, estimated_bytes: int) -> str:
        return self.reserve("transfer", estimated_bytes)

    def record_transfer(self, actual_bytes: int) -> None:
        token = self.reserve_transfer(actual_bytes)
        self.settle("transfer", token, actual_bytes)

    def record_decompressed(self, bytes_count: int) -> None:
        token = self.reserve("decompressed", bytes_count)
        self.settle("decompressed", token, bytes_count)

    def reserve_disk_space(self, target_dir: Path, estimated_bytes: int, is_temp: bool = True) -> str:
        ensure_plain_path(target_dir)
        existing = target_dir
        while not existing.exists():
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        if usage.free - estimated_bytes < self.min_free_headroom_bytes:
            raise DiskCeilingExceededError("physical disk headroom guard reached")
        return self.reserve("temp" if is_temp else "output", estimated_bytes)

    def reconcile_disk(self, resource: str, directory: Path) -> int:
        """Caller holds the plan execution lock; no writer may remain active."""
        if resource not in ("temp", "output"):
            raise ValueError("not a storage resource")
        total, count = 0, 0
        pending = [directory]
        while pending:
            path = pending.pop()
            ensure_plain_path(path)
            if not path.exists():
                continue
            count += 1
            if count > 10000:
                raise DiskCeilingExceededError("owned storage tree exceeds 10000 entries")
            if path.is_dir():
                pending.extend(bounded_children(path))
            else:
                total += path.stat().st_size
        with self._transaction() as account:
            if total > account.limits[resource]:
                raise DiskCeilingExceededError(f"{resource} occupancy exceeds limit")
            account.occupancy[resource] = total
            account.reservations[resource] = {}
        return total

    def release_temp_disk(self, byte_count: int) -> None:
        raise ValueError("disk release requires verified owned-tree reconciliation")

    def record_cache_hit(self) -> None:
        if self.journal is None:
            with self._lock:
                self._cache_hits += 1
        else:
            with self.journal.transaction() as state:
                state.cache_hits += 1

    def snapshot(self) -> dict[str, int]:
        with self._transaction() as account:
            return {
                "transferred_bytes": account.consumed.get("transfer", 0),
                "decompressed_bytes": account.consumed.get("decompressed", 0),
                "temp_disk_bytes": account.occupancy.get("temp", 0),
                "output_disk_bytes": account.occupancy.get("output", 0),
                "cache_hits": self.journal.state.cache_hits if self.journal else self._cache_hits,
                **{f"reserved_{key}_bytes": sum(value.values()) for key, value in account.reservations.items()},
            }


class AtomicFileWriter:
    @staticmethod
    def hash_durable_prefix(path: Path, length: int) -> str:
        hasher, read = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while read < length:
                chunk = stream.read(min(65536, length - read))
                if not chunk:
                    raise ValueError("cannot hash a missing durable prefix")
                hasher.update(chunk)
                read += len(chunk)
        return hasher.hexdigest()

    @staticmethod
    def truncate_to_length(path: Path, length: int) -> None:
        with path.open("r+b") as stream:
            stream.truncate(length)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def atomic_complete(partial_path: Path, final_path: Path) -> Path:
        ensure_plain_path(final_path)
        if final_path.exists():
            raise ValueError("immutable original already exists; refusing replacement")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        with partial_path.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is exclusive on Windows and POSIX. No rename-overwrite.
        os.link(partial_path, final_path)
        partial_path.unlink()
        return final_path
