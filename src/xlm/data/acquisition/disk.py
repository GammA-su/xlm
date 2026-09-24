"""Shared reservations, measured consumption and separately reconciled storage occupancy."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from xlm.artifacts.manifest import bounded_children, ensure_plain_path
from xlm.data.acquisition.progress import AcquisitionState, ProgressJournal, ResourceAccount
from xlm.data.sources.transport import BudgetExhaustedError

#: Execution-only durable lease windows (never plan identity, never output
#: bytes). A lease reserves a window in one persisted transaction and settles
#: the exact actual later, so hot paths pay one journal write per window instead
#: of several per 64 KiB read. Windows grow geometrically per stream: the first
#: is ``ACCOUNTING_INITIAL_WINDOW_BYTES`` and each next equals the amount already
#: used, capped at ``ACCOUNTING_WINDOW_BYTES``. A crash therefore strands at most
#: max(initial, already used) per active streaming lease, up to the cap.
#: Indivisible ``consume(amount)`` charges can reserve more than the cap when
#: amount exceeds it (e.g. one bounded Parquet group); their bound is
#: max(cap, amount). Older crash reservations remain charged across retries,
#: so the per-lease bound is not a bound on cumulative stranded allowance.
#: All reservations still share the persisted resource ceiling.
ACCOUNTING_INITIAL_WINDOW_BYTES = 64 * 1024
ACCOUNTING_WINDOW_BYTES = 16 * 1024 * 1024


def grown_window(
    used: int,
    cap: int = ACCOUNTING_WINDOW_BYTES,
    initial: int = ACCOUNTING_INITIAL_WINDOW_BYTES,
) -> int:
    """Next geometric lease window after ``used`` units of one stream."""
    return min(cap, max(initial, used))


class DiskCeilingExceededError(RuntimeError):
    """A logical storage ceiling or physical headroom guard was reached."""


def _limit_error(resource: str) -> type[RuntimeError]:
    return DiskCeilingExceededError if resource in ("temp", "output") else BudgetExhaustedError


class StorageCapacityManager:
    """Use the journal's existing lock/transaction for cross-process accounting.

    Transfer means response-body bytes returned to the application, not TCP/TLS
    wire bytes. A crash leaves the pending reservation unavailable; only a read
    whose outcome is known may release its unused allowance. Disk reservations
    are released only after measuring the owned tree under the plan execution lock.
    """

    def __init__(
        self,
        max_transferred_bytes: int,
        max_decompressed_bytes: int,
        max_temp_disk_bytes: int,
        max_output_disk_bytes: int,
        min_free_headroom_bytes: int = 50 * 1024 * 1024,
        *,
        journal: ProgressJournal | None = None,
        extra_limits: dict[str, int] | None = None,
    ) -> None:
        self.max_transferred_bytes = max_transferred_bytes
        self.max_decompressed_bytes = max_decompressed_bytes
        self.max_temp_disk_bytes = max_temp_disk_bytes
        self.max_output_disk_bytes = max_output_disk_bytes
        self.min_free_headroom_bytes = min_free_headroom_bytes
        self.journal = journal
        self._lock = threading.RLock()
        self._account = ResourceAccount()
        self._cache_hits = 0
        # A bound deadline is write-once (bind_deadline never replaces it), so
        # once observed it is checked in memory without reloading the journal.
        self._deadline_at: float | None = None
        limits = {
            "transfer": max_transferred_bytes,
            "decompressed": max_decompressed_bytes,
            "temp": max_temp_disk_bytes,
            "output": max_output_disk_bytes,
        }
        limits.update(extra_limits or {})
        if any(value < 1 for value in limits.values()):
            raise ValueError("resource limits must be positive")
        with self._transaction() as account:
            if account.limits and account.limits != limits:
                raise ValueError("resource limits changed under an existing account")
            account.limits = limits

    @contextmanager
    def _transaction(self, *, persist: bool = True) -> Iterator[ResourceAccount]:
        with self._lock:
            if self.journal is None:
                yield self._account
            else:
                with self.journal.transaction(persist=persist) as state:
                    yield state.accounting

    def bind_deadline(self, seconds: float) -> None:
        with self._transaction() as account:
            if account.deadline_at is None:
                account.deadline_at = time.time() + seconds

    def check_deadline(self) -> None:
        deadline = self._deadline_at
        if deadline is None:
            with self._transaction(persist=False) as account:
                deadline = account.deadline_at
            self._deadline_at = deadline
        if deadline is not None and time.time() >= deadline:
            raise TimeoutError(
                "cumulative acquisition deadline reached; restart grants no new time"
            )

    def remaining(self, resource: str) -> int:
        with self._transaction(persist=False) as account:
            used = (
                account.occupancy.get(resource, 0)
                if resource in ("temp", "output")
                else account.consumed.get(resource, 0)
            )
            if resource == "output":
                used += account.occupancy.get("published", 0)
            if resource == "temp":
                used += account.occupancy.get("nested_temp", 0)
            control = (
                self.journal.control_bytes() + self.journal.journal_path.stat().st_size
                if resource == "temp" and self.journal and self.journal.journal_path.exists()
                else 0
            )
            return (
                account.limits[resource]
                - used
                - sum(account.reservations.get(resource, {}).values())
                - control
            )

    def reserve(
        self,
        resource: str,
        amount: int,
        *,
        publication: bool = False,
        persistent: bool = False,
        on_reserved: Callable[[AcquisitionState, str], None] | None = None,
    ) -> str:
        if amount < 0:
            raise ValueError("negative reservation")
        with self._transaction() as account:
            pending = account.reservations.setdefault(resource, {})
            used = (
                account.occupancy.get(resource, 0)
                if resource in ("temp", "output")
                else account.consumed.get(resource, 0)
            )
            if resource == "output":
                used += account.occupancy.get("published", 0)
            if resource == "temp":
                used += account.occupancy.get("nested_temp", 0)
            if used + sum(pending.values()) + amount > account.limits[resource]:
                error = (
                    DiskCeilingExceededError
                    if resource in ("temp", "output")
                    else BudgetExhaustedError
                )
                raise error(f"{resource} limit reached including outstanding reservations")
            token = (
                "external_" if persistent else "publication_" if publication else ""
            ) + uuid.uuid4().hex
            pending[token] = amount
            if on_reserved is not None:
                if self.journal is None:
                    raise ValueError("publication intent requires a journal")
                on_reserved(self.journal.state, token)
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

    def _record(self, resource: str, amount: int) -> None:
        """Reserve-then-settle in one transaction: identical limit check and outcome."""
        if amount < 0:
            raise ValueError("negative reservation")
        with self._transaction() as account:
            used = account.consumed.get(resource, 0)
            pending = sum(account.reservations.get(resource, {}).values())
            if used + pending + amount > account.limits[resource]:
                raise _limit_error(resource)(
                    f"{resource} limit reached including outstanding reservations"
                )
            account.consumed[resource] = used + amount

    def record_transfer(self, actual_bytes: int) -> None:
        self._record("transfer", actual_bytes)

    def record_decompressed(self, bytes_count: int) -> None:
        self._record("decompressed", bytes_count)

    def exchange(
        self,
        *,
        settle: tuple[tuple[str, str, int, int], ...] = (),
        reserve: tuple[tuple[str, int, int] | tuple[str, int, int, int], ...] = (),
        progress: Callable[[AcquisitionState], None] | None = None,
    ) -> tuple[list[tuple[str, int]], dict[str, int]]:
        """Settle leases, apply one progress mutation, then reserve: one durable write.

        ``settle`` entries are ``(resource, token, actual, hold)``: ``actual`` is
        charged and ``hold`` stays reserved under the same token (an in-flight
        amount whose outcome is unknown); ``hold == 0`` retires the token.
        ``reserve`` entries are ``(resource, wanted, minimum[, limit])`` and
        grant ``min(wanted, available)`` when that is at least ``minimum``, else
        an empty token; ``limit`` supplies a caller-bound ceiling for counters
        outside the persisted limit map (e.g. scanned records). Settlement and
        progress always persist; callers raise
        after a refused grant, so refusals never discard durable settlement.
        Returns ``(token, amount)`` grants in request order plus the
        post-transaction consumed counters.
        """
        grants: list[tuple[str, int]] = []
        with self._transaction() as account:
            for resource, token, actual, hold in settle:
                reserved = account.reservations.get(resource, {}).get(token)
                if reserved is None or actual < 0 or hold < 0 or actual + hold > reserved:
                    raise ValueError("invalid reservation reconciliation")
                target = account.occupancy if resource in ("temp", "output") else account.consumed
                target[resource] = target.get(resource, 0) + actual
                if hold:
                    account.reservations[resource][token] = hold
                else:
                    del account.reservations[resource][token]
            if progress is not None:
                if self.journal is None:
                    raise ValueError("progress mutation requires a journal")
                progress(self.journal.state)
            for entry in reserve:
                resource, wanted, minimum = entry[0], entry[1], entry[2]
                limit = entry[3] if len(entry) == 4 else None
                if wanted < 0 or minimum < 0:
                    raise ValueError("negative reservation")
                pending = account.reservations.setdefault(resource, {})
                amount = min(wanted, self._available(account, resource, limit))
                if amount < max(1, minimum):
                    grants.append(("", 0))
                    continue
                token = uuid.uuid4().hex
                pending[token] = amount
                grants.append((token, amount))
            consumed = dict(account.consumed)
        return grants, consumed

    @staticmethod
    def _available(account: ResourceAccount, resource: str, limit: int | None = None) -> int:
        used = (
            account.occupancy.get(resource, 0)
            if resource in ("temp", "output")
            else account.consumed.get(resource, 0)
        )
        if resource == "output":
            used += account.occupancy.get("published", 0)
        if resource == "temp":
            used += account.occupancy.get("nested_temp", 0)
        ceiling = account.limits[resource] if limit is None else limit
        return ceiling - used - sum(account.reservations.get(resource, {}).values())

    def record_units(self, name: str, amount: int, maximum: int) -> None:
        with self._transaction() as account:
            used = account.consumed.get(name, 0)
            if amount < 0 or used + amount > maximum:
                raise BudgetExhaustedError(f"cumulative {name} limit exceeded")
            account.consumed[name] = used + amount

    def consumed(self, name: str) -> int:
        """Read-only consumed counter (no persistence, no mutation)."""
        with self._transaction(persist=False) as account:
            return account.consumed.get(name, 0)

    def commit_batch(
        self,
        *,
        temp_bytes: int = 0,
        scanned_records: int = 0,
        scanned_maximum: int | None = None,
    ) -> None:
        """Atomically commit batched exact consumption in one journal transaction.

        Collapses ``O(records)`` persisted transactions toward ``O(batches)``
        for temp staging occupancy and scanned-record counts. Every bound is
        enforced inside the single transaction with the same error types as
        the per-record path (``BudgetExhaustedError`` for scanned overuse,
        ``DiskCeilingExceededError`` for temp overuse), so limits stay exact.

        Crash model (unchanged staging/orphan semantics): uncommitted batch
        deltas live only in RAM. A crash orphans staging bytes that the next
        run's ``reconcile_disk`` measures and charges, while publication
        still requires a later persisted ``mark_file_completed`` — so a crash
        can never publish unaccounted usage, duplicate records (fresh staging
        per attempt), or exceed limits through commits. The conservative
        window is one batch, versus one record on the per-record path.
        Physical disk headroom is NOT rechecked here (fail-closed ``OSError``
        on write, same as any disk-full event); logical temp limits are.
        """
        if temp_bytes < 0 or scanned_records < 0:
            raise ValueError("negative batch delta")
        if temp_bytes == 0 and scanned_records == 0:
            with self._transaction(persist=False) as account:
                if account.deadline_at is not None and time.time() >= account.deadline_at:
                    raise TimeoutError(
                        "cumulative acquisition deadline reached; restart grants no new time"
                    )
            return
        scanned_overflow = False
        with self._transaction() as account:
            if account.deadline_at is not None and time.time() >= account.deadline_at:
                raise TimeoutError(
                    "cumulative acquisition deadline reached; restart grants no new time"
                )
            if scanned_records:
                if scanned_maximum is None:
                    raise ValueError("scanned batch requires its maximum")
                used = account.consumed.get("records_scanned", 0)
                if used + scanned_records > scanned_maximum:
                    # Fill exactly to the ceiling (mirroring the per-record
                    # refusal point) and refuse after persisting; temp staging
                    # from this batch is left for orphan reconciliation.
                    account.consumed["records_scanned"] = max(used, scanned_maximum)
                    scanned_overflow = True
                else:
                    account.consumed["records_scanned"] = used + scanned_records
            if temp_bytes and not scanned_overflow:
                used = account.occupancy.get("temp", 0) + account.occupancy.get("nested_temp", 0)
                pending = sum(account.reservations.get("temp", {}).values())
                if used + pending + temp_bytes > account.limits["temp"]:
                    raise DiskCeilingExceededError(
                        "temp limit reached including outstanding reservations"
                    )
                account.occupancy["temp"] = account.occupancy.get("temp", 0) + temp_bytes
        if scanned_overflow:
            raise BudgetExhaustedError("cumulative records_scanned limit exceeded")

    def reserve_disk_space(
        self,
        target_dir: Path,
        estimated_bytes: int,
        is_temp: bool = True,
        *,
        publication: bool = False,
        on_reserved: Callable[[AcquisitionState, str], None] | None = None,
    ) -> str:
        ensure_plain_path(target_dir)
        existing = target_dir
        while not existing.exists():
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        if usage.free - estimated_bytes < self.min_free_headroom_bytes:
            raise DiskCeilingExceededError("physical disk headroom guard reached")
        return self.reserve(
            "temp" if is_temp else "output",
            estimated_bytes,
            publication=publication,
            on_reserved=on_reserved,
        )

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
            # Publication staging is outside the fetched-file tree. A crash's
            # unresolved publication reservation cannot be released by this scan.
            account.reservations[resource] = {
                key: value
                for key, value in account.reservations.get(resource, {}).items()
                if key.startswith(("publication_", "external_"))
            }
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
        with self._transaction(persist=False) as account:
            return {
                "transferred_bytes": account.consumed.get("transfer", 0),
                "decompressed_bytes": account.consumed.get("decompressed", 0),
                "temp_disk_bytes": account.occupancy.get("temp", 0),
                "output_disk_bytes": account.occupancy.get("output", 0)
                + account.occupancy.get("published", 0),
                "published_artifact_bytes": account.occupancy.get("published", 0),
                "journal_bytes": self.journal.journal_path.stat().st_size
                if self.journal and self.journal.journal_path.exists()
                else 0,
                "control_disk_bytes": self.journal.control_bytes() if self.journal else 0,
                "cache_hits": self.journal.state.cache_hits if self.journal else self._cache_hits,
                **{
                    f"reserved_{key}_bytes": sum(value.values())
                    for key, value in account.reservations.items()
                },
            }


class CapacityLease:
    """One stream's durable pre-reserved allowance for a single resource.

    :meth:`take` grants bounded amounts from the reservation in memory and
    renews through :meth:`StorageCapacityManager.exchange`, settling the exact
    amount used so far in the same transaction. :meth:`close` settles the
    exact actual; :meth:`fail` settles known usage and keeps the in-flight
    amount reserved, exactly like an interrupted single read. Totals for a
    completed stream equal per-read accounting. Not thread-safe: one lease per
    response or stream, owned by one worker.
    """

    def __init__(
        self,
        manager: StorageCapacityManager,
        resource: str,
        *,
        window: int = ACCOUNTING_WINDOW_BYTES,
        initial: int = ACCOUNTING_INITIAL_WINDOW_BYTES,
        cap: int | None = None,
        limit: int | None = None,
        message: str | None = None,
        on_consumed: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        if window < 1 or initial < 1 or (cap is not None and cap < 0):
            raise ValueError("lease windows must be positive and cap non-negative")
        self.manager, self.resource, self.window, self.cap = manager, resource, window, cap
        self.initial, self.limit = min(initial, window), limit
        self.message = message or f"{resource} limit reached including outstanding reservations"
        self.on_consumed = on_consumed
        self.token, self.granted, self.used, self.total = "", 0, 0, 0

    @property
    def left(self) -> int:
        return self.granted - self.used

    def settlement(self, hold: int = 0) -> tuple[str, str, int, int] | None:
        """Pending ``exchange`` settle entry for this lease (``None`` when idle)."""
        return (self.resource, self.token, self.used, hold) if self.token else None

    def next_request(self, minimum: int = 1) -> tuple[str, int, int] | tuple[str, int, int, int]:
        """``exchange`` reserve entry for the next window of this lease."""
        want = grown_window(self.total, self.window, self.initial)
        if self.cap is not None:
            want = min(want, max(0, self.cap - self.total))
        if self.limit is None:
            return (self.resource, max(want, minimum), minimum)
        return (self.resource, max(want, minimum), minimum, self.limit)

    def adopt(self, grant: tuple[str, int], consumed: dict[str, int] | None = None) -> None:
        """Install a grant produced by an ``exchange`` that settled this lease."""
        self.token, self.granted = grant
        self.used = 0
        if consumed is not None and self.on_consumed is not None:
            self.on_consumed(consumed)
        if not self.token:
            raise _limit_error(self.resource)(self.message)

    def retire(self, consumed: dict[str, int]) -> None:
        self.token, self.granted, self.used = "", 0, 0
        if self.on_consumed is not None:
            self.on_consumed(consumed)

    def take(self, wanted: int, *, minimum: int = 1) -> int:
        """Grant up to ``wanted`` (at least ``minimum``) or raise the limit error."""
        if wanted < minimum:
            raise ValueError("lease request below its minimum")
        if self.left < max(1, minimum):
            settle = self.settlement()
            grants, consumed = self.manager.exchange(
                settle=(settle,) if settle else (), reserve=(self.next_request(minimum),)
            )
            self.retire(consumed)
            self.adopt(grants[0])
        return min(wanted, self.left)

    def commit(self, amount: int) -> None:
        """Charge ``amount`` of the current grant as used."""
        if amount < 0 or amount > self.left:
            raise ValueError("lease usage exceeds its grant")
        self.used += amount
        self.total += amount

    def consume(self, amount: int) -> None:
        """Exact record-style charge: all of ``amount`` or the limit error."""
        if amount:
            self.take(amount, minimum=amount)
            self.commit(amount)

    def consume_to_ceiling(self, amount: int) -> None:
        """Charge ``amount``; if it does not fit, fill exactly to the ceiling and refuse."""
        try:
            self.consume(amount)
        except (BudgetExhaustedError, DiskCeilingExceededError):
            granted = 0
            try:
                granted = self.take(amount, minimum=1)
            except (BudgetExhaustedError, DiskCeilingExceededError):
                pass
            if granted:
                self.commit(granted)
            self.close()
            raise

    def close(self) -> None:
        """Settle the exact actual and release the unused allowance."""
        settle = self.settlement()
        if settle is not None:
            _, consumed = self.manager.exchange(settle=(settle,))
            self.retire(consumed)

    def fail(self, in_flight: int) -> None:
        """Settle known usage; keep an unresolved in-flight amount reserved."""
        settle = self.settlement(hold=max(0, min(in_flight, self.left)))
        if settle is not None:
            _, consumed = self.manager.exchange(settle=(settle,))
            self.retire(consumed)


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
