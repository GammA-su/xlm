"""Explicit recorded per-record adapter rejections (opt-in batch mode).

Default adaptation stays fail-closed: the first policy rejection aborts.
With an explicit opt-in, recognized ``RecordRejectedError`` policy rejects
are recorded as bounded deterministic evidence while adaptation continues.
Schema/provenance errors and unexpected exceptions always abort, in every
mode. Only canonical accepted documents are published as ``documents.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.adapters.mix01_adapters import RecordRejectedError

#: Ledger/summary contract version. Bump explicitly if either schema changes.
ADAPTATION_REJECTION_VERSION = 1

#: Filenames published atomically by record mode (never partial).
DOCUMENTS_FILENAME = "documents.jsonl"
REJECTIONS_FILENAME = "adaptation_rejections.jsonl"
SUMMARY_FILENAME = "adaptation_summary.json"

#: Bound on a recorded rejection reason (adapter reasons are short policy
#: templates plus small field values; the full rejected text is never stored).
REJECTION_REASON_MAX_CHARS = 500

#: Staged write buffer for adaptation outputs (bounded; flushes in blocks).
ADAPT_WRITE_BUFFER_BYTES = 262144

#: Selected-records input size ceiling shared with the CLI gate.
ADAPT_INPUT_MAX_BYTES = 64 * 1024 * 1024

#: Binary input read chunk for streaming adaptation (framing only).
ADAPT_READ_CHUNK_BYTES = 65536


def is_recordable_rejection(error: BaseException) -> bool:
    """True only for recognized record-level policy rejections."""
    return isinstance(error, RecordRejectedError)


def rejection_code(error: RecordRejectedError) -> str:
    """Stable machine code for a policy rejection (exception class name)."""
    return type(error).__name__


def build_rejection_record(
    *,
    input_line: int,
    source_id: str,
    source_revision: str,
    source_file: str,
    source_row: int,
    adapter_id: str,
    error: RecordRejectedError,
    original_record_sha256: str | None,
) -> dict[str, Any]:
    """Bounded deterministic evidence for one policy-rejected input row.

    Carries lineage (input line, source coordinates, adapter, reason, upstream
    row hash) but never the rejected text or arbitrary source fields.
    """
    return {
        "input_line": input_line,
        "source_id": source_id,
        "source_revision": source_revision,
        "source_file": source_file,
        "source_row": source_row,
        "adapter_id": adapter_id,
        "rejection_code": rejection_code(error),
        "rejection_category": "policy",
        "reason": str(error)[:REJECTION_REASON_MAX_CHARS],
        "original_record_sha256": original_record_sha256,
    }


def serialize_document(document: CanonicalDocument) -> str:
    """Canonical accepted-document line, byte-identical to the dataset writer."""
    return json.dumps(document.to_dict(), ensure_ascii=False)


def serialize_rejection(record: dict[str, Any]) -> str:
    """Deterministic rejection ledger line (sorted keys)."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def build_summary(
    *,
    adapter_id: str,
    source_id: str,
    source_revision: str,
    plan_id: str,
    plan_hash: str,
    on_reject: str,
    total_input_records: int,
    accepted_records: int,
    rejected_records: int,
    rejection_counts_by_code: dict[str, int],
    document_sha256: str,
    rejection_sha256: str,
    max_input_bytes: int | None = None,
    output_shard_bytes: int | None = None,
) -> dict[str, Any]:
    """Deterministic adaptation summary (no timestamps, paths, or timing)."""
    return {
        "adaptation_summary_version": ADAPTATION_REJECTION_VERSION,
        "adapter_id": adapter_id,
        "source_id": source_id,
        "source_revision": source_revision,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "on_reject": on_reject,
        "total_input_records": total_input_records,
        "accepted_records": accepted_records,
        "rejected_records": rejected_records,
        "rejection_counts_by_code": dict(sorted(rejection_counts_by_code.items())),
        "max_input_bytes": max_input_bytes,
        "output_shard_bytes": output_shard_bytes,
        "documents": {
            "file": DOCUMENTS_FILENAME,
            "count": accepted_records,
            "sha256": document_sha256,
        },
        "rejections": {
            "file": REJECTIONS_FILENAME,
            "count": rejected_records,
            "sha256": rejection_sha256,
        },
    }


class StagedAdaptation:
    """Buffered staged adapt outputs with incremental hashes; atomic replace.

    Fail mode stages ``documents.jsonl`` only; record mode stages the full
    triple. Overwrite refusal matches current per-mode semantics. No final
    file exists until :meth:`finish` replaces staging temps; :meth:`abort`
    removes temps, never finals. Hashes/counts accumulate in the single
    streaming pass (written, counted, and hashed together).
    """

    def __init__(self, output_dir: Path, *, with_ledger: bool) -> None:
        self._directory = output_dir
        self._with_ledger = with_ledger
        expected = [DOCUMENTS_FILENAME]
        if with_ledger:
            expected.extend((REJECTIONS_FILENAME, SUMMARY_FILENAME))
        for filename in expected:
            if (output_dir / filename).exists():
                raise FileExistsError(
                    f"refusing to overwrite existing '{output_dir / filename}'; "
                    "use a fresh output dir"
                )
        self._made_directory = False
        self._writers: dict[str, StreamingJsonlWriter] = {}
        self._temps: dict[str, Path] = {}
        self.accepted_records = 0
        self.rejected_records = 0
        self.rejection_counts: dict[str, int] = {}

    def _ensure_directory(self) -> None:
        if not self._made_directory:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._made_directory = True

    def _writer(self, filename: str) -> StreamingJsonlWriter:
        writer = self._writers.get(filename)
        if writer is None:
            self._ensure_directory()
            temporary = self._directory / f"{filename}.{uuid.uuid4().hex}.tmp"
            writer = StreamingJsonlWriter(temporary, buffer_bytes=ADAPT_WRITE_BUFFER_BYTES)
            self._writers[filename] = writer
            self._temps[filename] = temporary
        return writer

    def write_document_line(self, line: str) -> None:
        self._writer(DOCUMENTS_FILENAME).write_line(line.encode("utf-8") + b"\n")
        self.accepted_records += 1

    def write_rejection_line(self, line: str, code: str) -> None:
        if not self._with_ledger:
            raise ValueError("rejection ledger is not staged in fail mode")
        self._writer(REJECTIONS_FILENAME).write_line(line.encode("utf-8") + b"\n")
        self.rejected_records += 1
        self.rejection_counts[code] = self.rejection_counts.get(code, 0) + 1

    @property
    def document_digest(self) -> str:
        if DOCUMENTS_FILENAME not in self._writers:
            return hashlib.sha256().hexdigest()
        return self._writers[DOCUMENTS_FILENAME].digest.hexdigest()

    @property
    def rejection_digest(self) -> str:
        if not self._with_ledger or REJECTIONS_FILENAME not in self._writers:
            return hashlib.sha256().hexdigest()
        return self._writers[REJECTIONS_FILENAME].digest.hexdigest()

    @property
    def flush_seconds(self) -> float:
        return sum(writer.flush_seconds for writer in self._writers.values())

    @property
    def peak_rss_bytes(self) -> int:
        return max((writer.peak_rss_bytes for writer in self._writers.values()), default=0)

    def finish(self, summary: dict[str, Any] | None) -> dict[str, Path]:
        """Write the summary temp, then atomically replace every staged file.

        Empty outputs are still published as empty files (matching current
        behavior); only a fatal error prevents publication.
        """
        ordered = [DOCUMENTS_FILENAME] + (
            [REJECTIONS_FILENAME, SUMMARY_FILENAME] if self._with_ledger else []
        )
        for filename in ordered:
            self._writer(filename)
        if self._with_ledger:
            if summary is None:
                raise ValueError("record mode requires a summary to publish")
            temporary = self._directory / f"{SUMMARY_FILENAME}.{uuid.uuid4().hex}.tmp"
            payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._temps[SUMMARY_FILENAME] = temporary
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        published: dict[str, Path] = {}
        try:
            for filename in ordered:
                writer = self._writers.get(filename)
                if writer is not None:
                    writer.close()
            for filename in ordered:
                temporary = self._temps[filename]
                final = self._directory / filename
                os.replace(temporary, final)
                published[filename] = final
            return published
        finally:
            for temporary in self._temps.values():
                temporary.unlink(missing_ok=True)

    def abort(self) -> None:
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception:
                pass
        for temporary in self._temps.values():
            temporary.unlink(missing_ok=True)
