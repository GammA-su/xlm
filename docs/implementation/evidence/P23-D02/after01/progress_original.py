"""Crash-consistent progress journal, state tracking, and reconciliation adhering to C04."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from xlm.data.acquisition.disk import AtomicFileWriter


class ProgressCorruptionError(RuntimeError):
    """Raised when the persisted acquisition progress journal is corrupt or mismatched."""


class FileProgress(BaseModel):
    """Per-file progress record tracking durable byte offsets and ETag validation."""

    model_config = ConfigDict(extra="forbid")

    file_path: str
    bytes_downloaded: int = 0
    total_expected: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    verified_prefix_bytes: int = 0
    status: str = "pending"  # 'pending' | 'downloading' | 'completed' | 'failed'
    error: str | None = None


class AcquisitionState(BaseModel):
    """Cumulative state of an acquisition plan across restart and interruptions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    plan_id: str
    plan_hash: str
    status: str = "PENDING"  # 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'INTERRUPTED' | 'FAILED'
    transferred_bytes: int = 0
    decompressed_bytes: int = 0
    temp_disk_bytes: int = 0
    output_disk_bytes: int = 0
    requests_made: int = 0
    cache_hits: int = 0
    records_acquired: int = 0
    file_progress: dict[str, FileProgress] = Field(default_factory=dict)
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    error_reason: str | None = None


class ProgressJournal:
    """Thread-safe, crash-consistent progress journal manager for acquisition execution."""

    def __init__(self, journal_path: Path, plan_id: str, plan_hash: str) -> None:
        self.journal_path = journal_path
        self.plan_id = plan_id
        self.plan_hash = plan_hash
        self._lock = threading.Lock()
        self.state: AcquisitionState = self._initialize_or_load()

    def _initialize_or_load(self) -> AcquisitionState:
        """Load existing journal if present, or initialize fresh state bound to plan."""
        if not self.journal_path.exists():
            return AcquisitionState(
                plan_id=self.plan_id,
                plan_hash=self.plan_hash,
                status="PENDING",
            )

        try:
            with self.journal_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            state = AcquisitionState.model_validate(data)
            if state.plan_id != self.plan_id or state.plan_hash != self.plan_hash:
                raise ProgressCorruptionError(
                    f"Journal mismatch: journal bound to ({state.plan_id}, {state.plan_hash}) "
                    f"does not match current execution plan ({self.plan_id}, {self.plan_hash})."
                )
            return state
        except Exception as e:
            raise ProgressCorruptionError(
                f"Failed to load acquisition progress journal at '{self.journal_path}': {e}"
            ) from e

    def save(self) -> None:
        """Atomically persist state to journal on storage media."""
        with self._lock:
            self.state.updated_at = datetime.now(UTC).isoformat()
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.journal_path.with_suffix(f".tmp.{os.getpid()}")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self.state.model_dump(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.journal_path)

    def record_request(self, maximum: int) -> None:
        """Reserve a request before opening a connection, across retries/restarts."""
        from xlm.data.sources.transport import BudgetExhaustedError

        with self._lock:
            if self.state.requests_made >= maximum:
                raise BudgetExhaustedError(f"acquisition request limit reached: {maximum}")
            self.state.requests_made += 1
        self.save()

    def reconcile_with_disk(self, output_dir: Path, partial_dir: Path) -> None:
        """Reconcile recorded journal progress with actual local filesystem state."""
        with self._lock:
            for rel_path, fp in self.state.file_progress.items():
                final_file = output_dir / rel_path
                partial_file = partial_dir / f"{rel_path}.part"

                if fp.status == "completed":
                    if not final_file.exists():
                        # Final file was deleted externally; reset to pending
                        fp.status = "pending"
                        fp.bytes_downloaded = 0
                        fp.verified_prefix_bytes = 0
                    continue

                if partial_file.exists():
                    actual_disk_len = partial_file.stat().st_size
                    verified_len = fp.verified_prefix_bytes

                    if actual_disk_len < verified_len:
                        # Hard crash before data write flushed to disk
                        fp.bytes_downloaded = actual_disk_len
                        fp.verified_prefix_bytes = actual_disk_len
                    elif actual_disk_len > verified_len:
                        # Data was written past last journaled verified prefix
                        # Truncate to verified prefix length for crash-consistency
                        AtomicFileWriter.truncate_to_length(partial_file, verified_len)
                        fp.bytes_downloaded = verified_len
                    else:
                        fp.bytes_downloaded = verified_len
                else:
                    fp.bytes_downloaded = 0
                    fp.verified_prefix_bytes = 0

    def update_file_progress(
        self,
        rel_path: str,
        bytes_added: int,
        etag: str | None = None,
        total_expected: int | None = None,
    ) -> None:
        """Record progress for a chunk written to a file."""
        with self._lock:
            fp = self.state.file_progress.get(rel_path)
            if not fp:
                fp = FileProgress(file_path=rel_path)
                self.state.file_progress[rel_path] = fp

            fp.bytes_downloaded += bytes_added
            fp.verified_prefix_bytes = fp.bytes_downloaded
            if etag:
                fp.etag = etag
            if total_expected:
                fp.total_expected = total_expected
            fp.status = "downloading"

    def mark_file_completed(self, rel_path: str, total_bytes: int, etag: str | None = None) -> None:
        """Mark a file as completely downloaded and verified."""
        with self._lock:
            fp = self.state.file_progress.get(rel_path)
            if not fp:
                fp = FileProgress(file_path=rel_path)
                self.state.file_progress[rel_path] = fp
            fp.bytes_downloaded = total_bytes
            fp.verified_prefix_bytes = total_bytes
            if etag:
                fp.etag = etag
            fp.status = "completed"

    def mark_file_failed(self, rel_path: str, error: str) -> None:
        """Record failure for a specific file."""
        with self._lock:
            fp = self.state.file_progress.get(rel_path)
            if not fp:
                fp = FileProgress(file_path=rel_path)
                self.state.file_progress[rel_path] = fp
            fp.status = "failed"
            fp.error = error
