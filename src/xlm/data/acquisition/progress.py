"""One locked, crash-consistent journal for progress and cumulative resource accounting."""

from __future__ import annotations

import math
import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from xlm.artifacts.manifest import canonical_payload_path, ensure_plain_path, validate_component


class ProgressCorruptionError(RuntimeError):
    """Progress is corrupt, incomplete or bound to another plan."""


class ResourceAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumed: dict[str, int] = Field(default_factory=dict)
    reservations: dict[str, dict[str, int]] = Field(default_factory=dict)
    occupancy: dict[str, int] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    deadline_at: float | None = None

    @model_validator(mode="after")
    def validate_account(self) -> ResourceAccount:
        values = [
            *self.consumed.values(),
            *self.occupancy.values(),
            *self.limits.values(),
            *(
                value
                for reservations in self.reservations.values()
                for value in reservations.values()
            ),
        ]
        if any(value < 0 for value in values) or (
            self.deadline_at is not None and not math.isfinite(self.deadline_at)
        ):
            raise ValueError("invalid negative/nonfinite resource account")
        return self


class FileProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_path: str
    bytes_downloaded: int = Field(default=0, ge=0)
    total_expected: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    verified_prefix_bytes: int = Field(default=0, ge=0)
    prefix_sha256: str | None = None
    content_sha256: str | None = None
    record_count: int | None = None
    status: str = "pending"
    error: str | None = None


class AcquisitionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    plan_id: str
    plan_hash: str
    status: str = "PENDING"
    transferred_bytes: int = 0
    decompressed_bytes: int = 0
    temp_disk_bytes: int = 0
    output_disk_bytes: int = 0
    requests_made: int = 0
    cache_hits: int = 0
    records_acquired: int = 0
    file_progress: dict[str, FileProgress] = Field(default_factory=dict)
    accounting: ResourceAccount = Field(default_factory=ResourceAccount)
    storage_roots: dict[str, str] = Field(default_factory=dict)
    source_validators: dict[str, dict[str, str | int | None]] = Field(default_factory=dict)
    authorization: dict[str, str | bool] = Field(default_factory=dict)
    published_artifacts: dict[str, int] = Field(default_factory=dict)
    nested_storage: dict[str, int] = Field(default_factory=dict)
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    error_reason: str | None = None


def apply_file_progress(
    state: AcquisitionState,
    rel_path: str,
    bytes_added: int,
    etag: str | None = None,
    total_expected: int | None = None,
    *,
    prefix_sha256: str | None = None,
) -> None:
    """Advance one file's durable verified prefix inside an open journal transaction."""
    fp = state.file_progress.setdefault(rel_path, FileProgress(file_path=rel_path))
    fp.bytes_downloaded += bytes_added
    fp.verified_prefix_bytes = fp.bytes_downloaded
    fp.prefix_sha256 = prefix_sha256
    fp.etag = etag or fp.etag
    fp.total_expected = total_expected if total_expected is not None else fp.total_expected
    fp.status = "downloading"


class ProgressJournal:
    """Reload under a filesystem lock before every mutation; never replace newer counters."""

    def __init__(self, journal_path: Path, plan_id: str, plan_hash: str) -> None:
        validate_component(plan_id)
        ensure_plain_path(journal_path)
        self.journal_path, self.plan_id, self.plan_hash = journal_path, plan_id, plan_hash
        self._lock = threading.RLock()
        # Observational IO counters (measurement only; never alter accounting).
        self.tx_total = 0
        self.tx_persisted = 0
        self.journal_fsyncs = 0
        self.journal_bytes_written = 0
        self.journal_reads_elided = 0
        # Bytes last read or written under the file lock plus the file identity
        # (file id, size, mtime) observed for them. A reload whose stat shows
        # the same identity validates these identical bytes instead of reopening
        # a freshly replaced file (each reopen costs a real-time scan on Windows).
        self._known: tuple[tuple[int, int, int], bytes] | None = None
        if journal_path.exists():
            lock = journal_path.with_suffix(".lock")
            ensure_plain_path(lock)
            with FileLock(str(lock), timeout=10):
                self.state = self._load()
        else:
            # A concurrent creator may publish after the existence check. Never
            # perform a second unlocked read; the first mutation reloads under lock.
            self.state = AcquisitionState(plan_id=plan_id, plan_hash=plan_hash)

    @staticmethod
    def _identity(stat: os.stat_result) -> tuple[int, int, int] | None:
        if not stat.st_ino:
            return None
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _load(self, *, checked: bool = False) -> AcquisitionState:
        try:
            before = self.journal_path.stat()
        except FileNotFoundError:
            return AcquisitionState(plan_id=self.plan_id, plan_hash=self.plan_hash)
        try:
            if not checked:
                ensure_plain_path(self.journal_path)
            if before.st_size > 8 * 1024**2:
                raise ValueError("journal exceeds 8 MiB")
            identity = self._identity(before)
            known = self._known
            if identity is not None and known is not None and known[0] == identity:
                payload = known[1]
                self.journal_reads_elided += 1
            else:
                payload = self.journal_path.read_bytes()
                after = self._identity(self.journal_path.stat())
                self._known = (identity, payload) if identity and identity == after else None
            state = AcquisitionState.model_validate_json(payload)
            if state.plan_id != self.plan_id or state.plan_hash != self.plan_hash:
                raise ValueError("journal plan identity mismatch")
            return state
        except Exception as exc:
            raise ProgressCorruptionError(f"Invalid acquisition journal: {exc}") from exc

    def _write(self) -> None:
        self.state.updated_at = datetime.now(UTC).isoformat()
        account = self.state.accounting
        self.state.transferred_bytes = account.consumed.get("transfer", 0)
        self.state.decompressed_bytes = account.consumed.get("decompressed", 0)
        self.state.requests_made = account.consumed.get("requests", 0)
        self.state.temp_disk_bytes = account.occupancy.get("temp", 0)
        self.state.output_disk_bytes = account.occupancy.get("output", 0) + account.occupancy.get(
            "published", 0
        )
        self.state.records_acquired = sum(
            fp.record_count or 0
            for fp in self.state.file_progress.values()
            if fp.status == "completed"
        )
        payload = self.state.model_dump_json().encode("utf-8")
        if len(payload) > 8 * 1024**2:
            raise ProgressCorruptionError("journal exceeds 8 MiB")
        # Account for the current journal plus the private atomic replacement.
        control = self.journal_path.stat().st_size if self.journal_path.exists() else 0
        occupied = (
            account.occupancy.get("temp", 0)
            + account.occupancy.get("nested_temp", 0)
            + sum(account.reservations.get("temp", {}).values())
        )
        if account.limits and occupied + control + len(payload) > account.limits["temp"]:
            raise ProgressCorruptionError("scratch limit including atomic journal storage exceeded")
        temporary = self.journal_path.with_name(
            self.journal_path.name + "." + uuid.uuid4().hex + ".tmp"
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal_path)
            identity = self._identity(self.journal_path.stat())
            self._known = (identity, payload) if identity is not None else None
            self.tx_persisted += 1
            self.journal_fsyncs += 1
            self.journal_bytes_written += len(payload)
        finally:
            temporary.unlink(missing_ok=True)

    def io_stats(self) -> dict[str, int]:
        """Observational journal IO counters (measurement only)."""
        with self._lock:
            return {
                "journal_transactions": self.tx_total,
                "journal_persisted_writes": self.tx_persisted,
                "journal_fsyncs": self.journal_fsyncs,
                "journal_bytes_written": self.journal_bytes_written,
                "journal_reads_elided": self.journal_reads_elided,
            }

    @contextmanager
    def transaction(self, *, persist: bool = True) -> Iterator[AcquisitionState]:
        with self._lock:
            self.tx_total += 1
            if not self.journal_path.parent.is_dir():
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            lock = self.journal_path.with_suffix(".lock")
            # One walk covers every shared ancestor plus the journal; the lock
            # leaf is checked separately. Same checks as before, not repeated.
            ensure_plain_path(self.journal_path)
            if lock.is_symlink() or lock.is_junction():
                raise ValueError(f"Artifact path contains a symlink/junction: {lock}")
            with FileLock(str(lock), timeout=10):
                self.state = self._load(checked=True)
                if self.state.schema_version != 2:
                    raise ProgressCorruptionError(
                        "legacy journal accounting is unresolved; cannot resume"
                    )
                yield self.state
                if persist:
                    self._write()

    def save(self) -> None:
        with self.transaction(persist=False):
            pass

    def set_status(self, status: str, error: str | None = None) -> None:
        with self.transaction() as state:
            state.status, state.error_reason = status, error

    def bind_roots(self, **roots: Path) -> None:
        resolved = {key: str(value.resolve()) for key, value in roots.items()}
        with self.transaction() as state:
            if state.storage_roots and state.storage_roots != resolved:
                raise ProgressCorruptionError("changing storage roots cannot reset accounting")
            state.storage_roots = resolved

    def record_request(self, maximum: int) -> None:
        from xlm.data.sources.transport import BudgetExhaustedError

        with self.transaction() as state:
            used = state.accounting.consumed.get("requests", 0)
            if used >= maximum:
                raise BudgetExhaustedError(f"acquisition request limit reached: {maximum}")
            state.accounting.consumed["requests"] = used + 1

    def bind_source(self, name: str, etag: str | None, length: int | None) -> None:
        with self.transaction() as state:
            value = {"etag": etag, "length": length}
            if name in state.source_validators and state.source_validators[name] != value:
                raise ProgressCorruptionError(
                    "original source validator changed across requests/restarts"
                )
            state.source_validators[name] = value

    def reconcile_with_disk(self, output_dir: Path, partial_dir: Path) -> None:
        from xlm.data.acquisition.disk import AtomicFileWriter

        with self.transaction() as state:
            for rel_path, fp in state.file_progress.items():
                canonical_payload_path(rel_path)
                final_file, partial_file = output_dir / rel_path, partial_dir / f"{rel_path}.part"
                ensure_plain_path(final_file)
                ensure_plain_path(partial_file)
                if fp.status == "completed":
                    if not final_file.is_file() or not fp.content_sha256:
                        raise ProgressCorruptionError(
                            "completed original missing or lacks integrity evidence"
                        )
                    if (
                        final_file.stat().st_size != fp.bytes_downloaded
                        or AtomicFileWriter.hash_durable_prefix(final_file, fp.bytes_downloaded)
                        != fp.content_sha256
                    ):
                        raise ProgressCorruptionError(
                            "completed original integrity checksum mismatch"
                        )
                elif partial_file.exists():
                    size = partial_file.stat().st_size
                    if fp.verified_prefix_bytes and not fp.prefix_sha256:
                        raise ProgressCorruptionError(
                            "partial prefix lacks verified integrity evidence"
                        )
                    if size < fp.verified_prefix_bytes:
                        raise ProgressCorruptionError("durable partial prefix is missing")
                    if (
                        fp.prefix_sha256
                        and AtomicFileWriter.hash_durable_prefix(
                            partial_file, fp.verified_prefix_bytes
                        )
                        != fp.prefix_sha256
                    ):
                        raise ProgressCorruptionError("partial prefix integrity checksum mismatch")
                    if size > fp.verified_prefix_bytes:
                        AtomicFileWriter.truncate_to_length(partial_file, fp.verified_prefix_bytes)
                    fp.bytes_downloaded = fp.verified_prefix_bytes
                elif fp.verified_prefix_bytes:
                    raise ProgressCorruptionError("durable partial file is missing")

    def update_file_progress(
        self,
        rel_path: str,
        bytes_added: int,
        etag: str | None = None,
        total_expected: int | None = None,
        *,
        prefix_sha256: str | None = None,
    ) -> None:
        with self.transaction() as state:
            apply_file_progress(
                state,
                rel_path,
                bytes_added,
                etag,
                total_expected,
                prefix_sha256=prefix_sha256,
            )

    def mark_file_completed(
        self,
        rel_path: str,
        total_bytes: int,
        etag: str | None = None,
        *,
        digest: str | None = None,
        records: int | None = None,
    ) -> None:
        with self.transaction() as state:
            fp = state.file_progress.setdefault(rel_path, FileProgress(file_path=rel_path))
            fp.bytes_downloaded = fp.verified_prefix_bytes = total_bytes
            fp.etag = etag or fp.etag
            fp.content_sha256, fp.record_count, fp.status = digest, records, "completed"

    def mark_file_failed(self, rel_path: str, error: str) -> None:
        with self.transaction() as state:
            fp = state.file_progress.setdefault(rel_path, FileProgress(file_path=rel_path))
            fp.status, fp.error = "failed", error
