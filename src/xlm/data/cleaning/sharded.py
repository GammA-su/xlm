"""Deterministic sharded and parallel cleaning execution (P27B-B/C/D/E).

Architecture::

    sharded canonical input (single file ranges, manifest shards, plain-dir files)
        |
    N independent work units (one input shard = one unit; single files are
    split at newline boundaries without materializing any document list)
        |
    worker processes (or one in-process worker): each builds the pipeline
    once, streams its units through the *unchanged* ``run_stream``
    orchestration with a buffered quarantine sink and a buffered accepted
    writer, and records local exact integer metrics
        |
    parent assembles in unit order: accepted bytes -> legacy single file or
    deterministic target-sized shards; quarantine bytes -> capped single
    file, optionally resharded; integer metrics merged in order.

Determinism contract:

- Global document, quarantine, and metrics order is always unit order;
  worker completion order never affects a single output byte.
- No document crosses a process boundary: workers receive paths and byte
  ranges only, never pickled records.
- One worker failure aborts publication: no manifest, no summary, staging
  removed.
- Memory is bounded per worker (streaming within its units plus bounded
  doc-id lists); the parent only streams bytes.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import itertools
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.cleaning.base import CleaningBudgetExhaustedError
from xlm.data.cleaning.pipeline import (
    CleaningPipeline,
    PipelineExecutionSummary,
    StageStats,
    create_pipeline_preset,
)
from xlm.data.cleaning.quarantine import (
    QuarantinePolicy,
    build_quarantine_entry,
)
from xlm.data.cleaning.types import QualityMetrics
from xlm.data.datasets.shards import (
    MANIFEST_FILENAME,
    SHARD_MANIFEST_VERSION,
    ShardEntry,
    ShardManifest,
    aggregate_identity,
    load_manifest,
    shard_filename,
    verify_manifest,
)

CLEAN_MANIFEST_FILENAME = "clean-manifest.json"
QUARANTINE_MANIFEST_FILENAME = "quarantine-manifest.json"
QUARANTINE_FILENAME = "quarantine.jsonl"

# Binary read chunk for input splitting and streaming copies.
_COPY_CHUNK_BYTES = 1024 * 1024

# Quarantine sink buffer: accepted-write buffer parity, one durable
# transaction per flush instead of one open/write/close/chmod per document.
_QUARANTINE_BUFFER_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CleanUnit:
    """One deterministic cleaning work unit."""

    index: int
    kind: str  # "range" (byte slice of one file), "shard" (whole manifest shard), "parquet"
    path: str  # input file path
    start: int  # byte offset for ranges, else 0
    end: int  # byte offset for ranges, else 0
    doc_count: int  # verified or counted documents in this unit
    lines_before: int  # global line offset for identical error messages
    max_docs: int | None  # processing allocation for this unit


@dataclass
class UnitTiming:
    """Wall-clock accumulators for one unit (telemetry only, never identity)."""

    parse_seconds: float = 0.0
    serialize_seconds: float = 0.0
    write_seconds: float = 0.0
    quarantine_seconds: float = 0.0
    fsync_seconds: float = 0.0
    wall_seconds: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "parse_seconds": self.parse_seconds,
            "serialize_seconds": self.serialize_seconds,
            "write_seconds": self.write_seconds,
            "quarantine_seconds": self.quarantine_seconds,
            "fsync_seconds": self.fsync_seconds,
            "wall_seconds": self.wall_seconds,
        }

    def add(self, other: UnitTiming) -> None:
        self.parse_seconds += other.parse_seconds
        self.serialize_seconds += other.serialize_seconds
        self.write_seconds += other.write_seconds
        self.quarantine_seconds += other.quarantine_seconds
        self.fsync_seconds += other.fsync_seconds
        self.wall_seconds += other.wall_seconds


class _UnitQuarantineSink:
    """Buffered quarantine sink with the reference entry bytes.

    Entries are built by the shared :func:`build_quarantine_entry`, so the
    bytes are exactly the reference manager's bytes. Storage ceilings and
    record caps are *not* enforced here: the parent applies them in global
    unit order during assembly, which reproduces the reference semantics
    (first-N in global encounter order) that no worker can decide locally.
    """

    def __init__(self, path: Path, policy: QuarantinePolicy) -> None:
        self._stream = path.open("wb", buffering=_QUARANTINE_BUFFER_BYTES)
        self._policy = policy
        self._path = path
        self.recorded_count = 0
        self.total_bytes_written = 0
        self.quarantine_seconds = 0.0

    def record_rejection(
        self,
        doc: CanonicalDocument,
        reasons: list[str],
        stage_name: str,
        metrics: QualityMetrics | None = None,
    ) -> None:
        started = time.monotonic()
        payload, line_bytes = build_quarantine_entry(
            doc,
            reasons,
            stage_name,
            metrics,
            self._policy,
            datetime.now(UTC).isoformat(),
        )
        self._stream.write(payload)
        self.recorded_count += 1
        self.total_bytes_written += line_bytes
        self.quarantine_seconds += time.monotonic() - started

    def close(self) -> float:
        """Flush, fsync, and close; returns fsync-phase seconds."""
        started = time.monotonic()
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        return max(0.0, time.monotonic() - started)


def _iter_unit_lines(unit: CleanUnit) -> Iterator[tuple[int, bytes]]:
    """Yield ``(global_line_no, raw_bytes)`` for one unit, streaming.

    Blank lines are skipped exactly like the reference JSONL reader; the
    yielded line numbers count every physical line, also exactly like it.
    """
    path = Path(unit.path)
    if unit.kind == "parquet":
        raise AssertionError("parquet units do not frame lines")
    limit = unit.end - unit.start if unit.kind == "range" else -1
    with path.open("rb") as stream:
        if unit.kind == "range" and unit.start > 0:
            stream.seek(unit.start)
        line_no = unit.lines_before
        pending = bytearray()
        while limit != 0:
            block = stream.read(_COPY_CHUNK_BYTES if limit < 0 else min(_COPY_CHUNK_BYTES, limit))
            if not block:
                break
            if limit > 0:
                limit -= len(block)
            pending += block
            *lines, remainder = bytes(pending).split(b"\n")
            pending = bytearray(remainder)
            for raw in lines:
                line_no += 1
                if raw.strip():
                    yield line_no, raw
        if pending.strip():
            line_no += 1
            yield line_no, bytes(pending)


def _process_unit(
    pipeline: CleaningPipeline,
    policy: QuarantinePolicy,
    unit: CleanUnit,
    staging_dir: Path,
) -> dict[str, Any]:
    """Clean one unit through the reference orchestration; return its metrics."""
    timing = UnitTiming()
    wall_start = time.monotonic()
    accepted_tmp = staging_dir / f"unit-{unit.index:05d}.accepted.tmp"
    quarantine_tmp = staging_dir / f"unit-{unit.index:05d}.quarantine.tmp"
    accepted_writer = StreamingJsonlWriter(accepted_tmp)
    quarantine_sink = _UnitQuarantineSink(quarantine_tmp, policy)
    accepted_ids: list[str] = []

    def documents() -> Iterator[CanonicalDocument]:
        if unit.kind == "parquet":
            for doc in CanonicalDatasetReader.read_parquet(Path(unit.path)):
                yield doc
            return
        for line_no, raw in _iter_unit_lines(unit):
            started = time.monotonic()
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON at line {line_no} in {unit.path}: {exc}") from exc
            try:
                doc = CanonicalDocument(**data)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid canonical record at line {line_no}: {exc}") from exc
            timing.parse_seconds += time.monotonic() - started
            yield doc

    accepted_iter, summary = pipeline.run_stream(
        documents(),
        quarantine_mgr=quarantine_sink,
        max_docs=unit.max_docs,
    )
    for doc in accepted_iter:
        started = time.monotonic()
        payload = (json.dumps(doc.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")
        timing.serialize_seconds += time.monotonic() - started
        started = time.monotonic()
        accepted_writer.write_raw(payload)
        timing.write_seconds += time.monotonic() - started
        accepted_ids.append(doc.doc_id)
    timing.fsync_seconds += quarantine_sink.close()
    # Rejected ids in global order are recovered at assembly from the
    # quarantine file; record the count here for the merge.
    started = time.monotonic()
    accepted_writer.close()
    timing.fsync_seconds += max(0.0, time.monotonic() - started)
    timing.wall_seconds = time.monotonic() - wall_start
    timing.quarantine_seconds = quarantine_sink.quarantine_seconds

    return {
        "index": unit.index,
        "stage_metrics": [
            {
                "stage_name": st.stage_name,
                "transform_id": st.transform_id,
                "transform_version": st.transform_version,
                "input_docs": st.input_docs,
                "output_docs": st.output_docs,
                "input_bytes": st.input_bytes,
                "output_bytes": st.output_bytes,
                "rejected_docs": st.rejected_docs,
                "duration_ms": st.duration_ms,
            }
            for st in summary.stage_metrics
        ],
        "reason_counts": dict(summary.reason_counts),
        "total_input_docs": summary.total_input_docs,
        "total_output_docs": summary.total_output_docs,
        "total_input_bytes": summary.total_input_bytes,
        "total_output_bytes": summary.total_output_bytes,
        "total_rejected_docs": summary.total_rejected_docs,
        "is_partial_sample": summary.is_partial_sample,
        "completed": summary.completed,
        "accepted_tmp": str(accepted_tmp),
        "accepted_ids": accepted_ids,
        "quarantine_tmp": str(quarantine_tmp),
        "quarantine_count": quarantine_sink.recorded_count,
        "quarantine_bytes": quarantine_sink.total_bytes_written,
        "timing": timing.to_dict(),
    }


def clean_unit_worker(spec: dict[str, Any]) -> dict[str, Any]:
    """Process one worker's units (module-level so spawn can pickle it).

    The pipeline is constructed once per worker process: every static
    matcher, detector, and lookup table is compiled exactly once per
    process (P27B-F), and no mutable detector state crosses processes.
    With a pool initializer the construction happens in the initializer;
    otherwise it happens lazily here on first use.
    """
    global _WORKER_STATE
    key = (spec["preset"], tuple(sorted(spec["quarantine_policy"].items())))
    if _WORKER_STATE is None or _WORKER_STATE[0] != key:
        _WORKER_STATE = (
            key,
            create_pipeline_preset(spec["preset"]),
            QuarantinePolicy(**spec["quarantine_policy"]),
        )
    _, pipeline, policy = _WORKER_STATE
    staging_dir = Path(spec["staging_dir"])
    unit_results = []
    for unit_spec in spec["units"]:
        unit = CleanUnit(**unit_spec)
        unit_results.append(_process_unit(pipeline, policy, unit, staging_dir))
    hashes = pipeline.compute_pipeline_hash()
    return {"pipeline_hash": hashes, "units": unit_results}


_WORKER_STATE: (
    tuple[tuple[str, tuple[tuple[str, Any], ...]], CleaningPipeline, QuarantinePolicy] | None
) = None


def _init_worker(preset: str, quarantine_policy: dict[str, Any]) -> None:
    """Pool initializer: build the pipeline once per worker process."""
    global _WORKER_STATE
    # QuarantinePolicy values are JSON scalars; sort by key for a stable key.
    key = (preset, tuple(sorted(quarantine_policy.items(), key=lambda item: item[0])))
    _WORKER_STATE = (key, create_pipeline_preset(preset), QuarantinePolicy(**quarantine_policy))


# Bytes that never constitute document content for blank-line purposes.
# This matches the worker framing check (``raw.strip()`` on bytes) exactly:
# ASCII whitespace only, since non-ASCII whitespace still reaches the JSON
# parser, exactly like the reference text reader's blank skip after decode.
_BLANK_BYTES = b" \t\r\x0b\x0c"


def _count_file_docs(path: Path) -> tuple[int, int]:
    """Count ``(non-blank lines, all lines)`` in one streaming pass."""
    docs = 0
    lines = 0
    carry_live = False
    with path.open("rb") as stream:
        while True:
            block = stream.read(_COPY_CHUNK_BYTES)
            if not block:
                break
            cursor = 0
            while True:
                newline = block.find(b"\n", cursor)
                if newline == -1:
                    if block[cursor:].strip(_BLANK_BYTES):
                        carry_live = True
                    break
                lines += 1
                if carry_live or block[cursor:newline].strip(_BLANK_BYTES):
                    docs += 1
                carry_live = False
                cursor = newline + 1
        if carry_live:
            docs += 1
            lines += 1
    return docs, lines


def _split_file_units(
    path: Path,
    start_index: int,
    lines_before: int,
    target_bytes: int,
    max_docs: int | None,
    remaining: list[int],
) -> tuple[list[CleanUnit], int]:
    """Split one JSONL file into newline-aligned range units (streaming scan).

    ``doc_count`` counts non-blank lines only (the worker skips blanks, like
    the reference reader), while the returned line cursor counts every
    physical line so worker error messages carry identical line numbers.
    """
    size = path.stat().st_size
    if size == 0:
        allocation: int | None = 0 if max_docs is not None else None
        return [
            CleanUnit(
                index=start_index,
                kind="range",
                path=str(path),
                start=0,
                end=0,
                doc_count=0,
                lines_before=lines_before,
                max_docs=allocation,
            )
        ], lines_before
    cuts = [0]
    unit_docs: list[int] = []
    unit_lines: list[int] = []
    docs = 0
    lines = 0
    carry_live = False  # the line spanning into this block already has content
    position = 0
    unit_start = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(_COPY_CHUNK_BYTES)
            if not block:
                break
            cursor = 0
            while True:
                newline = block.find(b"\n", cursor)
                if newline == -1:
                    if block[cursor:].strip(_BLANK_BYTES):
                        carry_live = True
                    break
                segment = block[cursor:newline]
                lines += 1
                if carry_live or segment.strip(_BLANK_BYTES):
                    docs += 1
                carry_live = False
                absolute = position + newline + 1
                cursor = newline + 1
                if absolute - unit_start >= target_bytes:
                    cuts.append(absolute)
                    unit_docs.append(docs)
                    unit_lines.append(lines)
                    docs = 0
                    lines = 0
                    unit_start = absolute
            position += len(block)
        # Trailing fragment without a terminator is a final line (worker
        # yields the pending remainder), blank or not by the same rule.
        if carry_live:
            docs += 1
            lines += 1
    cuts.append(size)
    unit_docs.append(docs)
    unit_lines.append(lines)
    units: list[CleanUnit] = []
    line_cursor = lines_before
    for ordinal, (start, end) in enumerate(zip(cuts[:-1], cuts[1:], strict=True)):
        doc_count = unit_docs[ordinal]
        allocation = None
        if max_docs is not None:
            allocation = min(doc_count, remaining[0])
            remaining[0] -= allocation
        units.append(
            CleanUnit(
                index=start_index + ordinal,
                kind="range",
                path=str(path),
                start=start,
                end=end,
                doc_count=doc_count,
                lines_before=line_cursor,
                max_docs=allocation,
            )
        )
        line_cursor += unit_lines[ordinal]
    return units, line_cursor


def plan_clean_units(
    input_path: Path,
    *,
    input_shard_bytes: int,
    max_docs: int | None,
) -> tuple[list[CleanUnit], dict[str, Any]]:
    """Plan deterministic work units for any supported clean input.

    Returns ``(units, input_info)`` where ``input_info`` describes the
    validated input (manifest totals or file sizes) for telemetry and the
    input byte budget check.
    """
    remaining = [max_docs] if max_docs is not None else [0]
    if input_path.is_dir():
        manifest_path = input_path / MANIFEST_FILENAME
        if manifest_path.is_file():
            manifest = load_manifest(manifest_path)
            verify_manifest(input_path, manifest)
            units: list[CleanUnit] = []
            lines_before = 0
            for entry in sorted(manifest.shards, key=lambda item: item.ordinal):
                allocation: int | None = None
                if max_docs is not None:
                    allocation = min(entry.doc_count, remaining[0])
                    remaining[0] -= allocation
                units.append(
                    CleanUnit(
                        index=entry.ordinal,
                        kind="shard",
                        path=str(input_path / entry.path),
                        start=0,
                        end=0,
                        doc_count=entry.doc_count,
                        lines_before=lines_before,
                        max_docs=allocation,
                    )
                )
                lines_before += entry.doc_count
            return units, {
                "kind": "manifest",
                "shards": len(units),
                "total_documents": manifest.total_documents,
                "total_bytes": manifest.total_bytes,
            }
        files = sorted(
            [p for p in input_path.iterdir() if p.is_file() and p.suffix == ".jsonl"],
            key=lambda p: p.name,
        )
        if not files:
            raise FileNotFoundError(f"No .jsonl shards found in: {input_path}")
        units = []
        lines_before = 0
        total_docs = 0
        for position, shard in enumerate(files):
            doc_count, line_count = _count_file_docs(shard)
            allocation = None
            if max_docs is not None:
                allocation = min(doc_count, remaining[0])
                remaining[0] -= allocation
            units.append(
                CleanUnit(
                    index=position,
                    kind="shard",
                    path=str(shard),
                    start=0,
                    end=0,
                    doc_count=doc_count,
                    lines_before=lines_before,
                    max_docs=allocation,
                )
            )
            lines_before += line_count
            total_docs += doc_count
        return units, {
            "kind": "plain_dir",
            "shards": len(units),
            "total_documents": total_docs,
            "total_bytes": sum(p.stat().st_size for p in files),
        }
    if not input_path.is_file():
        raise FileNotFoundError(f"Clean input not found: {input_path}")
    if input_path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("pyarrow is required to read Parquet input.") from exc
        rows = int(pq.ParquetFile(input_path).metadata.num_rows)
        allocation = None
        if max_docs is not None:
            allocation = min(rows, remaining[0])
        return [
            CleanUnit(
                index=0,
                kind="parquet",
                path=str(input_path),
                start=0,
                end=0,
                doc_count=rows,
                lines_before=0,
                max_docs=allocation,
            )
        ], {
            "kind": "parquet",
            "shards": 1,
            "total_documents": rows,
            "total_bytes": input_path.stat().st_size,
        }
    units, _ = _split_file_units(input_path, 0, 0, max(1, input_shard_bytes), max_docs, remaining)
    return units, {
        "kind": "single_file",
        "shards": len(units),
        "total_documents": sum(u.doc_count for u in units),
        "total_bytes": input_path.stat().st_size,
    }


def merge_unit_results(
    unit_results: list[dict[str, Any]],
    pipeline: CleaningPipeline,
    max_docs: int | None,
) -> tuple[PipelineExecutionSummary, UnitTiming]:
    """Merge per-unit metrics in unit order (deterministic; integers exact)."""
    ordered = sorted(unit_results, key=lambda item: item["index"])
    stage_names = [t.transform_id for t in pipeline.transforms]
    merged_stages = [
        StageStats(
            stage_name=t.transform_id,
            transform_id=t.transform_id,
            transform_version=t.version,
        )
        for t in pipeline.transforms
    ]
    reason_counts: dict[str, int] = {}
    total = {
        "input_docs": 0,
        "output_docs": 0,
        "input_bytes": 0,
        "output_bytes": 0,
        "rejected_docs": 0,
    }
    is_partial = False
    completed = True
    timing = UnitTiming()
    for result in ordered:
        by_name = {entry["stage_name"]: entry for entry in result["stage_metrics"]}
        for position, name in enumerate(stage_names):
            entry = by_name[name]
            merged = merged_stages[position]
            merged.input_docs += entry["input_docs"]
            merged.output_docs += entry["output_docs"]
            merged.input_bytes += entry["input_bytes"]
            merged.output_bytes += entry["output_bytes"]
            merged.rejected_docs += entry["rejected_docs"]
            merged.duration_ms += entry["duration_ms"]
        for reason, count in result["reason_counts"].items():
            reason_counts[reason] = reason_counts.get(reason, 0) + count
        total["input_docs"] += result["total_input_docs"]
        total["output_docs"] += result["total_output_docs"]
        total["input_bytes"] += result["total_input_bytes"]
        total["output_bytes"] += result["total_output_bytes"]
        total["rejected_docs"] += result["total_rejected_docs"]
        is_partial = is_partial or result["is_partial_sample"]
        completed = completed and result["completed"]
        unit_timing = result["timing"]
        timing.add(
            UnitTiming(
                parse_seconds=unit_timing["parse_seconds"],
                serialize_seconds=unit_timing["serialize_seconds"],
                write_seconds=unit_timing["write_seconds"],
                quarantine_seconds=unit_timing["quarantine_seconds"],
                fsync_seconds=unit_timing["fsync_seconds"],
                wall_seconds=unit_timing["wall_seconds"],
            )
        )
    max_docs_declared: int | None = max_docs
    summary = PipelineExecutionSummary(
        pipeline_hash=pipeline.compute_pipeline_hash(),
        domain_preset=pipeline.domain_preset,
        total_input_docs=total["input_docs"],
        total_output_docs=total["output_docs"],
        total_input_bytes=total["input_bytes"],
        total_output_bytes=total["output_bytes"],
        total_rejected_docs=total["rejected_docs"],
        document_yield_ratio=0.0,
        byte_yield_ratio=0.0,
        stage_metrics=merged_stages,
        reason_counts=reason_counts,
        elapsed_seconds=0.0,
        is_partial_sample=is_partial,
        declared_max_docs=max_docs_declared,
        completed=completed,
    )
    CleaningPipeline._refresh_yields(summary)
    return summary, timing


def _stream_concat(sources: list[Path], target: Path) -> int:
    """Concatenate files in order with one fsynced durable write; return bytes."""
    bytes_written = 0
    with target.open("wb") as out:
        for source in sources:
            with source.open("rb") as stream:
                while True:
                    block = stream.read(_COPY_CHUNK_BYTES)
                    if not block:
                        break
                    out.write(block)
                    bytes_written += len(block)
        out.flush()
        os.fsync(out.fileno())
    return bytes_written


def assemble_output_shards(
    source_files: list[Path],
    doc_ids: list[list[str | None]],
    output_dir: Path,
    manifest_filename: str,
    *,
    dataset_id: str,
    target_shard_bytes: int,
    artifact_type: str,
    source_artifact: dict[str, Any],
    producer: dict[str, Any],
) -> ShardManifest:
    """Assemble ordered source files into deterministic target-sized shards.

    Bytes stream through once; no document is parsed. Shard endpoint ids
    come from the caller-supplied per-unit doc-id lists, which run in the
    same order as the source files.
    """
    if target_shard_bytes < 1:
        raise ValueError("shard target bytes must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / manifest_filename
    if manifest_path.exists():
        raise FileExistsError(
            f"refusing to overwrite published manifest '{manifest_path}'; use a fresh output dir"
        )
    id_cursor = [iter(ids) for ids in doc_ids]
    id_unit = 0

    def next_id() -> str | None:
        nonlocal id_unit
        while id_unit < len(id_cursor):
            try:
                return next(id_cursor[id_unit])
            except StopIteration:
                id_unit += 1
        return None

    entries: list[ShardEntry] = []
    current_tmp: Path | None = None
    current_stream: Any = None
    current_size = 0
    current_count = 0
    current_first: str | None = None
    current_last: str | None = None
    current_digest: Any = None

    def roll() -> None:
        nonlocal current_tmp, current_stream, current_size, current_count
        nonlocal current_first, current_last, current_digest
        assert current_tmp is not None and current_stream is not None
        current_stream.flush()
        os.fsync(current_stream.fileno())
        current_stream.close()
        final = output_dir / shard_filename(len(entries))
        os.replace(current_tmp, final)
        oversize = current_size > target_shard_bytes and current_count <= 1
        entries.append(
            ShardEntry(
                ordinal=len(entries),
                path=final.name,
                doc_count=current_count,
                byte_count=current_size,
                sha256=current_digest.hexdigest(),
                oversize=oversize,
                first_doc_id=current_first,
                last_doc_id=current_last,
            )
        )
        current_tmp = None
        current_stream = None
        current_size = 0
        current_count = 0
        current_first = None
        current_last = None
        current_digest = None

    def emit(line: bytes) -> None:
        nonlocal current_tmp, current_stream, current_size, current_count
        nonlocal current_first, current_last, current_digest
        if current_stream is None:
            current_tmp = output_dir / f"{shard_filename(len(entries))}.{uuid.uuid4().hex}.tmp"
            current_stream = current_tmp.open("wb")
            current_digest = hashlib.sha256()
        if current_count > 0 and current_size >= target_shard_bytes:
            roll()
            current_tmp = output_dir / f"{shard_filename(len(entries))}.{uuid.uuid4().hex}.tmp"
            current_stream = current_tmp.open("wb")
            current_digest = hashlib.sha256()
        current_stream.write(line)
        current_digest.update(line)
        current_size += len(line)
        current_count += 1
        doc_id = next_id()
        if current_first is None:
            current_first = doc_id
        current_last = doc_id

    try:
        for source in source_files:
            with source.open("rb") as stream:
                pending = bytearray()
                while True:
                    block = stream.read(_COPY_CHUNK_BYTES)
                    if not block:
                        break
                    pending += block
                    *lines, remainder = bytes(pending).split(b"\n")
                    pending = bytearray(remainder)
                    for raw in lines:
                        # Worker outputs never contain blank lines; skipping a
                        # foreign blank without consuming a doc id keeps the
                        # endpoint accounting aligned.
                        if raw:
                            emit(raw + b"\n")
                if pending.strip():
                    # A final fragment without a terminator is still a record;
                    # worker outputs always terminate lines, so this only
                    # fires on foreign inputs, which fail closed elsewhere.
                    emit(bytes(pending) + b"\n")
                elif pending:
                    raise ValueError(
                        "shard assembly found a blank trailing fragment; "
                        "inputs must be newline-terminated JSONL"
                    )
        if current_stream is not None:
            roll()
    except BaseException:
        if current_stream is not None:
            try:
                current_stream.close()
            except OSError:
                pass
        if current_tmp is not None:
            current_tmp.unlink(missing_ok=True)
        for entry in entries:
            (output_dir / entry.path).unlink(missing_ok=True)
        raise
    manifest = ShardManifest(
        schema_version=SHARD_MANIFEST_VERSION,
        artifact_type=artifact_type,
        dataset_id=dataset_id,
        source_artifact=source_artifact,
        producer=producer,
        shard_target_bytes=target_shard_bytes,
        shards=tuple(entries),
        total_documents=sum(entry.doc_count for entry in entries),
        total_bytes=sum(entry.byte_count for entry in entries),
        aggregate_sha256=aggregate_identity(entries),
    )
    verify_manifest(output_dir, manifest)
    temporary = output_dir / f"{manifest_filename}.{uuid.uuid4().hex}.tmp"
    try:
        payload = (json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n").encode()
        with temporary.open("xb") as manifest_stream:
            manifest_stream.write(payload)
            manifest_stream.flush()
            os.fsync(manifest_stream.fileno())
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


@dataclass
class AssembledClean:
    """Filenames published by :func:`run_sharded_clean`."""

    accepted_files: list[str] = field(default_factory=list)
    quarantine_files: list[str] = field(default_factory=list)
    manifest_file: str | None = None
    quarantine_manifest_file: str | None = None
    summary_file: str = "cleaning_summary.json"
    throughput_file: str = "cleaning_throughput.json"


def run_sharded_clean(
    *,
    input_path: Path,
    output_dir: Path,
    preset: str,
    workers: int,
    input_shard_bytes: int,
    output_shard_bytes: int | None,
    quarantine_dir: Path,
    quarantine_shard_bytes: int | None,
    quarantine_policy: QuarantinePolicy,
    max_docs: int | None,
    max_input_bytes: int,
    progress_echo: Any = None,
    scheduling: str = "static",
) -> tuple[
    PipelineExecutionSummary, UnitTiming, AssembledClean, dict[str, Any], ShardManifest | None
]:
    """Execute deterministic sharded cleaning; publish nothing on failure."""
    if workers < 1:
        raise ValueError(f"workers must be positive, got {workers}")
    if scheduling not in ("static", "dynamic"):
        raise ValueError("scheduling must be static or dynamic")
    wall_start = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    units, input_info = plan_clean_units(
        input_path, input_shard_bytes=input_shard_bytes, max_docs=max_docs
    )
    if input_info["total_bytes"] > max_input_bytes:
        raise ValueError(
            f"clean input exceeds limit ({input_info['total_bytes']:,} > "
            f"{max_input_bytes:,} bytes); refusing to stream an unbounded job."
        )

    staging_dir = output_dir / f".staging-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=False)
    policy_payload = {
        "max_records": quarantine_policy.max_records,
        "max_quarantine_bytes": quarantine_policy.max_quarantine_bytes,
        "max_preview_chars": quarantine_policy.max_preview_chars,
        "retention_days": quarantine_policy.retention_days,
        "store_previews": quarantine_policy.store_previews,
        "owner_only_permissions": quarantine_policy.owner_only_permissions,
    }

    def worker_spec(chunk: list[CleanUnit]) -> dict[str, Any]:
        return {
            "preset": preset,
            "units": [
                {
                    "index": unit.index,
                    "kind": unit.kind,
                    "path": unit.path,
                    "start": unit.start,
                    "end": unit.end,
                    "doc_count": unit.doc_count,
                    "lines_before": unit.lines_before,
                    "max_docs": unit.max_docs,
                }
                for unit in chunk
            ],
            "staging_dir": str(staging_dir),
            "quarantine_policy": policy_payload,
        }

    pipeline_hash = ""
    unit_results: list[dict[str, Any]] = []
    try:
        if workers == 1 or len(units) <= 1:
            result = clean_unit_worker(worker_spec(list(units)))
            pipeline_hash = result["pipeline_hash"]
            unit_results = result["units"]
        else:
            # Static remains the default. Dynamic whole-unit dispatch is explicit,
            # with at most two tasks per worker pending and ordered assembly below.
            chunks = iter(
                [units[i::workers] for i in range(min(workers, len(units)))]
                if scheduling == "static"
                else ([unit] for unit in units)
            )
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_worker,
                initargs=(preset, policy_payload),
            ) as pool:
                pending_futures = {
                    pool.submit(clean_unit_worker, worker_spec(chunk))
                    for chunk in itertools.islice(chunks, 2 * workers)
                }
                try:
                    while pending_futures:
                        done, _ = concurrent.futures.wait(
                            pending_futures, return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        for future in done:
                            pending_futures.remove(future)
                            result = future.result()
                            if pipeline_hash and result["pipeline_hash"] != pipeline_hash:
                                raise ValueError(
                                    "worker pipeline hash mismatch; refusing to assemble"
                                )
                            pipeline_hash = result["pipeline_hash"]
                            unit_results.extend(result["units"])
                            chunk = next(chunks, None)
                            if chunk is not None:
                                pending_futures.add(
                                    pool.submit(clean_unit_worker, worker_spec(chunk))
                                )
                except BaseException:
                    for future in pending_futures:
                        future.cancel()
                    raise
        unit_results.sort(key=lambda item: item["index"])
        if [r["index"] for r in unit_results] != sorted(u.index for u in units):
            raise ValueError("missing or duplicate cleaning units; refusing to assemble")

        reference_pipeline = create_pipeline_preset(preset)
        if reference_pipeline.compute_pipeline_hash() != pipeline_hash:
            raise ValueError("worker pipeline hash mismatch; refusing to assemble")
        summary, timing = merge_unit_results(unit_results, reference_pipeline, max_docs)
        summary.elapsed_seconds = round(time.monotonic() - wall_start, 3)

        ordered_tmps = [staging_dir / f"unit-{u.index:05d}.accepted.tmp" for u in units]
        ordered_ids = []
        ordered_qtmps = []
        by_index = {result["index"]: result for result in unit_results}
        for unit in units:
            result = by_index[unit.index]
            ordered_ids.append(result["accepted_ids"])
            ordered_qtmps.append(staging_dir / f"unit-{unit.index:05d}.quarantine.tmp")

        assembled = AssembledClean()
        source_artifact = {"input_kind": input_info["kind"], "preset": preset}
        producer = {"tool": "xlm-data-clean", "workers": workers}

        # Ordered quarantine assembly FIRST with the reference caps: the first
        # max_records rejections in global unit order survive, and the byte
        # ceiling raises at exactly the record where the reference raises.
        # Quarantine raises before any accepted output is published, so an
        # abort never leaves a partial final manifest behind.
        quarantine_tmp = staging_dir / "quarantine.jsonl"
        recorded = 0
        quarantine_bytes = 0
        with quarantine_tmp.open("wb") as quarantine_out:
            for qtmp in ordered_qtmps:
                if recorded >= quarantine_policy.max_records:
                    break
                with qtmp.open("rb") as stream:
                    pending = bytearray()
                    while True:
                        block = stream.read(_COPY_CHUNK_BYTES)
                        if not block and not pending:
                            break
                        pending += block
                        *lines, remainder = bytes(pending).split(b"\n")
                        pending = bytearray(remainder) if block else bytearray()
                        for raw in lines:
                            if not raw:
                                continue
                            if recorded >= quarantine_policy.max_records:
                                break
                            payload = raw + b"\n"
                            if quarantine_bytes >= quarantine_policy.max_quarantine_bytes:
                                raise CleaningBudgetExhaustedError(
                                    "Quarantine storage exceeded ceiling of "
                                    f"{quarantine_policy.max_quarantine_bytes:,} bytes."
                                )
                            quarantine_out.write(payload)
                            recorded += 1
                            quarantine_bytes += len(payload)
                        if recorded >= quarantine_policy.max_records:
                            break
            quarantine_out.flush()
            os.fsync(quarantine_out.fileno())

        if quarantine_shard_bytes is not None:
            # Recover rejected doc ids in assembly order for shard endpoints:
            # the assembled file holds exactly the surviving records, so
            # attributing them to units in order (trimmed by the caps) is exact.
            flat_qids: list[str | None] = []
            with quarantine_tmp.open("rb") as stream:
                for raw in stream.read().split(b"\n"):
                    if raw.strip():
                        flat_qids.append(json.loads(raw.decode("utf-8"))["doc_id"])
            assert len(flat_qids) == recorded
            trimmed: list[list[str | None]] = []
            cursor = 0
            for result in sorted(unit_results, key=lambda item: item["index"]):
                take = max(0, min(result["quarantine_count"], recorded - cursor))
                trimmed.append(flat_qids[cursor : cursor + take])
                cursor += take
            q_manifest = assemble_output_shards(
                [quarantine_tmp],
                trimmed,
                quarantine_dir,
                QUARANTINE_MANIFEST_FILENAME,
                dataset_id=f"quarantine_{preset}_{pipeline_hash[:12]}",
                target_shard_bytes=quarantine_shard_bytes,
                artifact_type="cleaning_quarantine_sharded",
                source_artifact=source_artifact,
                producer={**producer, "quarantine_shard_bytes": quarantine_shard_bytes},
            )
            assembled.quarantine_files = [entry.path for entry in q_manifest.shards]
            assembled.quarantine_manifest_file = QUARANTINE_MANIFEST_FILENAME
        else:
            quarantine_stage = staging_dir / "quarantine.jsonl.tmp"
            _stream_concat([quarantine_tmp], quarantine_stage)
            final_quarantine = quarantine_dir / QUARANTINE_FILENAME
            os.replace(quarantine_stage, final_quarantine)
            if quarantine_policy.owner_only_permissions and os.name == "posix":
                try:
                    os.chmod(quarantine_dir, 0o700)
                    os.chmod(final_quarantine, 0o600)
                except OSError:
                    pass
            assembled.quarantine_files = [QUARANTINE_FILENAME]

        # Accepted assembly runs only after quarantine caps cleared: no abort
        # from here on can precede the accepted manifest.
        if output_shard_bytes is not None:
            manifest = assemble_output_shards(
                ordered_tmps,
                ordered_ids,
                output_dir,
                CLEAN_MANIFEST_FILENAME,
                dataset_id=f"clean_{preset}_{pipeline_hash[:12]}",
                target_shard_bytes=output_shard_bytes,
                artifact_type="clean_documents_sharded",
                source_artifact=source_artifact,
                producer={**producer, "output_shard_bytes": output_shard_bytes},
            )
            assembled.accepted_files = [entry.path for entry in manifest.shards]
            assembled.manifest_file = CLEAN_MANIFEST_FILENAME
        else:
            manifest = None
            legacy_tmp = staging_dir / "documents.jsonl.tmp"
            _stream_concat(ordered_tmps, legacy_tmp)
            os.replace(legacy_tmp, output_dir / "documents.jsonl")
            assembled.accepted_files = ["documents.jsonl"]

        throughput = {
            "input_documents": summary.total_input_docs,
            "accepted_documents": summary.total_output_docs,
            "rejected_documents": summary.total_rejected_docs,
            "quarantine_records": recorded,
            "input_bytes": summary.total_input_bytes,
            "output_bytes": summary.total_output_bytes,
            "parse_seconds": timing.parse_seconds,
            "stage_seconds": {
                st.stage_name: st.duration_ms / 1000.0 for st in summary.stage_metrics
            },
            "serialization_seconds": timing.serialize_seconds,
            "accepted_write_seconds": timing.write_seconds,
            "quarantine_seconds": timing.quarantine_seconds,
            "fsync_seconds": timing.fsync_seconds,
            "wall_seconds": round(time.monotonic() - wall_start, 3),
            "docs_per_second": (
                summary.total_input_docs / max(1e-9, time.monotonic() - wall_start)
            ),
            "input_mib_per_second": (
                (summary.total_input_bytes / (1024**2)) / max(1e-9, time.monotonic() - wall_start)
            ),
            "output_mib_per_second": (
                (summary.total_output_bytes / (1024**2)) / max(1e-9, time.monotonic() - wall_start)
            ),
            "workers": workers,
            "input_shards": input_info["shards"],
            "input_kind": input_info["kind"],
            "output_shards": len(assembled.accepted_files),
            "peak_rss_bytes": _peak_rss(),
        }
        throughput_path = output_dir / assembled.throughput_file
        throughput_stage = staging_dir / "cleaning_throughput.json.tmp"
        with throughput_stage.open("wb") as throughput_stream:
            throughput_stream.write(
                (json.dumps(throughput, indent=2, sort_keys=True) + "\n").encode()
            )
            throughput_stream.flush()
            os.fsync(throughput_stream.fileno())
        os.replace(throughput_stage, throughput_path)

        summary_path = output_dir / assembled.summary_file
        summary_stage = staging_dir / "cleaning_summary.json.tmp"
        with summary_stage.open("wb") as summary_stream:
            summary_stream.write((json.dumps(summary.to_dict(), indent=2) + "\n").encode())
            summary_stream.flush()
            os.fsync(summary_stream.fileno())
        os.replace(summary_stage, summary_path)

        shutil.rmtree(staging_dir, ignore_errors=True)
        if progress_echo is not None:
            progress_echo(summary, timing, throughput, assembled)
        return summary, timing, assembled, throughput, manifest
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _peak_rss() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0
