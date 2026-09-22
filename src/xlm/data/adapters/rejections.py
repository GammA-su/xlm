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


def _sha256_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def serialize_documents(documents: list[CanonicalDocument]) -> list[str]:
    """Canonical accepted-document lines, byte-identical to the dataset writer."""
    return [json.dumps(doc.to_dict(), ensure_ascii=False) for doc in documents]


def serialize_rejections(records: list[dict[str, Any]]) -> list[str]:
    """Deterministic rejection ledger lines (sorted keys, input order kept)."""
    return [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]


def build_summary(
    *,
    adapter_id: str,
    source_id: str,
    source_revision: str,
    plan_id: str,
    plan_hash: str,
    on_reject: str,
    total_input_records: int,
    document_lines: list[str],
    rejection_lines: list[str],
    rejection_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic adaptation summary (no timestamps, paths, or timing)."""
    counts: dict[str, int] = {}
    for record in rejection_records:
        code = str(record.get("rejection_code", "unknown"))
        counts[code] = counts.get(code, 0) + 1
    return {
        "adaptation_summary_version": ADAPTATION_REJECTION_VERSION,
        "adapter_id": adapter_id,
        "source_id": source_id,
        "source_revision": source_revision,
        "plan_id": plan_id,
        "plan_hash": plan_hash,
        "on_reject": on_reject,
        "total_input_records": total_input_records,
        "accepted_records": len(document_lines),
        "rejected_records": len(rejection_lines),
        "rejection_counts_by_code": dict(sorted(counts.items())),
        "documents": {
            "file": DOCUMENTS_FILENAME,
            "count": len(document_lines),
            "sha256": _sha256_lines(document_lines),
        },
        "rejections": {
            "file": REJECTIONS_FILENAME,
            "count": len(rejection_lines),
            "sha256": _sha256_lines(rejection_lines),
        },
    }


def publish_atomically(
    output_dir: Path,
    document_lines: list[str],
    rejection_lines: list[str],
    summary: dict[str, Any],
) -> dict[str, Path]:
    """Stage all three outputs, then atomically publish (never partial).

    Refuses when any final file already exists, preserving current overwrite
    refusal semantics. Staging temporaries are removed on any failure.
    """
    targets = {
        DOCUMENTS_FILENAME: document_lines,
        REJECTIONS_FILENAME: rejection_lines,
        SUMMARY_FILENAME: [json.dumps(summary, indent=2, sort_keys=True)],
    }
    for filename in targets:
        if (output_dir / filename).exists():
            raise FileExistsError(
                f"refusing to overwrite existing '{output_dir / filename}'; use a fresh output dir"
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    try:
        for filename, lines in targets.items():
            temporary = output_dir / f"{filename}.{uuid.uuid4().hex}.tmp"
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                for line in lines:
                    stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, output_dir / filename))
        published: dict[str, Path] = {}
        for temporary, final in staged:
            os.replace(temporary, final)
            published[final.name] = final
        return published
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
