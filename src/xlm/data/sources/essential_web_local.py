"""Local adaptation of a verified Essential-Web source Parquet into the three views.

The record stream is the certified selected-record serialization, produced in
memory from the local file and parsed back exactly as ``xlm data adapt`` parses
``selected_records.jsonl``. The frozen adapters then run unchanged, once per
view. Documents, rejection lines and their hashes are therefore the ones the
certified path writes for the same rows; the ledger bytes are stored compressed
and identified by the hash of the uncompressed stream.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.acquisition.sampling import discover_layout_local
from xlm.data.acquisition.source_parquet import (
    ScratchBudget,
    ScratchCapError,
    SourceTransferError,
    TransferLimits,
    TransferMeter,
    TransferResult,
    check_parquet_magic,
    download_source,
    file_sha256,
    promote_source,
    selected_payloads,
)
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.malformed import MalformedCounter
from xlm.data.adapters.mix01_adapters import (
    ADAPTERS_BY_ID,
    EssentialWebMalformedRowError,
    RecordRejectedError,
)
from xlm.data.adapters.rejections import (
    DOCUMENTS_FILENAME,
    SUMMARY_FILENAME,
    build_rejection_record,
    build_summary,
    serialize_document,
    serialize_rejection,
)
from xlm.data.sources import essential_web_bulk as bulk

ADAPTER_ID = bulk.ADAPTER_ID
LEDGER_FILENAME = "adaptation_rejections.jsonl.zst"
LEDGER_CODEC = "zstd"
LEDGER_LEVEL = 9
SUMMARY_VERSION = 2
RECEIPT_FILENAME = "receipt.json"
MIB = 1024**2


class LocalAdaptError(RuntimeError):
    """A local source file, its layout or its adaptation violates a bound."""


def compress_ledger(data: bytes) -> bytes:
    """One zstd frame at the fixed level; identity stays the uncompressed SHA-256."""
    codec = pa.Codec(LEDGER_CODEC, compression_level=LEDGER_LEVEL)
    return bytes(codec.compress(data, asbytes=True))


def read_ledger(path: Path, uncompressed_bytes: int, *, max_bytes: int = 4096 * MIB) -> bytes:
    """The exact version-1 rejection JSONL bytes of a compressed ledger."""
    if uncompressed_bytes < 0 or uncompressed_bytes > max_bytes:
        raise LocalAdaptError("ledger size is outside its bound")
    if path.stat().st_size > max_bytes:
        raise LocalAdaptError("compressed ledger exceeds its bound")
    if uncompressed_bytes == 0:
        return b""
    codec = pa.Codec(LEDGER_CODEC)
    return bytes(
        codec.decompress(path.read_bytes(), decompressed_size=uncompressed_bytes, asbytes=True)
    )


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass
class _View:
    name: str
    adapter: Any
    documents: StreamingJsonlWriter
    ledger: bytearray = field(default_factory=bytearray)
    malformed: MalformedCounter = field(default_factory=MalformedCounter)
    codes: dict[str, int] = field(default_factory=dict)
    accepted: int = 0
    rejected: int = 0
    canonical_bytes: int = 0


def check_layout(path: Path, source_file: str, limits: Mapping[str, Any]) -> dict[str, Any]:
    """Footer-only safety checks of a local source file before any row is decoded."""
    check_parquet_magic(path)
    ratio = float(limits["max_decompression_ratio"])
    record = bulk.layout_record(
        discover_layout_local(
            path,
            name=source_file,
            max_parser_bytes=int(limits["max_parser_bytes"]),
            max_decompression_ratio=ratio,
        ),
        columns_for(ADAPTER_ID, bulk.PLAN_VIEW),
        ratio,
    )
    refused = {g["index"]: g["refusal"] for g in record["groups"] if g["refusal"] is not None}
    if refused:
        raise LocalAdaptError(f"{source_file}: row groups refused: {refused}")
    if int(record["rows"]) > int(limits["max_rows_per_file"]):
        raise LocalAdaptError(f"{source_file}: row count exceeds the per-file bound")
    return record


def adapt_source_file(
    path: Path,
    output_dir: Path,
    *,
    source_file: str,
    views: Sequence[str],
    source_id: str,
    repository: str,
    revision: str,
    plan_id: str,
    plan_hash: str,
    selection_hash: str,
    identity: Mapping[str, Any],
    limits: Mapping[str, Any],
    row_range: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Adapt one verified local source Parquet into every view under ``output_dir``.

    ``output_dir`` must be a fresh private directory; nothing is published
    here. Returns counts, hashes and timings, never record content.
    """
    ensure_plain_path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise LocalAdaptError("local adaptation needs a fresh private output directory")
    started, cpu_started = time.monotonic(), time.process_time()
    layout = check_layout(path, source_file, limits)
    start, stop = row_range or (0, int(layout["rows"]))
    states: list[_View] = []
    for view in views:
        (output_dir / view).mkdir(parents=True)
        states.append(
            _View(
                name=view,
                adapter=ADAPTERS_BY_ID[ADAPTER_ID](view),
                documents=StreamingJsonlWriter(output_dir / view / DOCUMENTS_FILENAME),
            )
        )
    raw_digest = hashlib.sha256()
    raw_bytes = rows = max_record = 0
    counters: dict[str, int] = {}
    ledger_bound = int(limits["max_ledger_bytes"])
    try:
        for row_index, payload in selected_payloads(
            path,
            source_file=source_file,
            locator={
                "source_id": source_id,
                "repository": repository,
                "revision": revision,
                "selection_hash": selection_hash,
            },
            etag=str(identity["etag"]),
            columns=columns_for(ADAPTER_ID, bulk.PLAN_VIEW),
            max_record_bytes=int(limits["max_record_bytes"]),
            max_parser_bytes=int(limits["max_parser_bytes"]),
            max_decoded_bytes=int(limits["max_decoded_bytes_per_file"]),
            row_range=(start, stop),
            counters=counters,
        ):
            raw_digest.update(payload)
            raw_bytes += len(payload)
            max_record = max(max_record, len(payload))
            rows += 1
            record = json.loads(payload)
            locator = record["_xlm_acquisition"]
            for state in states:
                try:
                    document = state.adapter.adapt(
                        record,
                        source_file=source_file,
                        source_row=row_index,
                        source_revision=revision,
                    )
                except RecordRejectedError as exc:
                    state.malformed.observe(isinstance(exc, EssentialWebMalformedRowError))
                    line = serialize_rejection(
                        build_rejection_record(
                            input_line=rows,
                            source_id=source_id,
                            source_revision=revision,
                            source_file=source_file,
                            source_row=row_index,
                            adapter_id=ADAPTER_ID,
                            error=exc,
                            original_record_sha256=locator["original_record_sha256"],
                        )
                    )
                    state.ledger += line.encode("utf-8") + b"\n"
                    if len(state.ledger) > ledger_bound:
                        raise LocalAdaptError(
                            f"{source_file}: rejection ledger exceeds its bound"
                        ) from None
                    code = type(exc).__name__
                    state.codes[code] = state.codes.get(code, 0) + 1
                    state.rejected += 1
                    continue
                state.malformed.observe(False)
                if document.source_id != source_id:
                    raise LocalAdaptError("adapter produced a document of another source")
                state.documents.write_line(serialize_document(document).encode("utf-8") + b"\n")
                state.accepted += 1
                state.canonical_bytes += document.utf8_byte_count
    except BaseException:
        for state in states:
            try:
                state.documents.close()
            except OSError:
                pass
        raise
    if rows != stop - start:
        raise LocalAdaptError(f"{source_file}: decoded rows differ from the footer row count")
    result: dict[str, Any] = {
        "source_file": source_file,
        "rows": rows,
        "row_range": [start, stop],
        "file_rows": int(layout["rows"]),
        "row_groups": len(layout["groups"]),
        "projected_compressed_bytes": sum(
            int(group["projected_compressed_bytes"]) for group in layout["groups"]
        ),
        "selected_records_sha256": raw_digest.hexdigest(),
        "selected_records_bytes": raw_bytes,
        "max_selected_record_bytes": max_record,
        "decoded_bytes": counters.get("decoded_bytes", 0),
        "views": {},
    }
    for state in states:
        state.documents.close()
        ledger = bytes(state.ledger)
        compressed = compress_ledger(ledger) if ledger else b""
        ledger_sha256 = hashlib.sha256(ledger).hexdigest()
        directory = output_dir / state.name
        _write_bytes(directory / LEDGER_FILENAME, compressed)
        if hashlib.sha256(read_ledger(directory / LEDGER_FILENAME, len(ledger))).hexdigest() != (
            ledger_sha256
        ):
            raise LocalAdaptError(f"{source_file}: compressed ledger does not round-trip")
        summary = build_summary(
            adapter_id=ADAPTER_ID,
            source_id=source_id,
            source_revision=revision,
            plan_id=plan_id,
            plan_hash=plan_hash,
            on_reject="record",
            total_input_records=state.accepted + state.rejected,
            accepted_records=state.accepted,
            rejected_records=state.rejected,
            rejection_counts_by_code=state.codes,
            document_sha256=state.documents.digest.hexdigest(),
            rejection_sha256=ledger_sha256,
        )
        summary["adaptation_summary_version"] = SUMMARY_VERSION
        summary["view"] = state.name
        summary["input"] = {
            "kind": "verified_source_parquet",
            "source_file": source_file,
            "sha256": identity["sha256"],
            "etag": identity["etag"],
            "remote_length": identity["length"],
            "row_range": [start, stop],
        }
        summary["rejections"].update(
            file=LEDGER_FILENAME,
            uncompressed_bytes=len(ledger),
            storage={
                "codec": LEDGER_CODEC,
                "level": LEDGER_LEVEL,
                "compressed_bytes": len(compressed),
                "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            },
        )
        _write_bytes(
            directory / SUMMARY_FILENAME,
            (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        result["views"][state.name] = {
            "documents": state.accepted,
            "canonical_bytes": state.canonical_bytes,
            "documents_sha256": state.documents.digest.hexdigest(),
            "documents_file_bytes": state.documents.size,
            "adaptation_summary_sha256": file_sha256(directory / SUMMARY_FILENAME)[0],
            "rejections_sha256": ledger_sha256,
            "rejections_uncompressed_bytes": len(ledger),
            "rejections_file_bytes": len(compressed),
            "rejections_file_sha256": hashlib.sha256(compressed).hexdigest(),
            "rejection_counts_by_code": dict(sorted(state.codes.items())),
        }
    result["process_seconds"] = time.monotonic() - started
    result["process_cpu_seconds"] = time.process_time() - cpu_started
    return result


def process_unit(job: Mapping[str, Any]) -> dict[str, Any]:
    """Worker entry point: promote one verified source file, then adapt it locally.

    Runs in a separate process. The durable raw copy is written before any
    canonical output exists; the canonical outputs stay in a private staging
    directory that the caller publishes after sealing its receipt.
    """
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    source = Path(str(job["source_path"]))
    promote_seconds = 0.0
    promoted = False
    if job.get("durable_path") is not None:
        started = time.monotonic()
        promoted = promote_source(
            source, Path(str(job["durable_path"])), dict(job["identity_record"])
        )
        promote_seconds = time.monotonic() - started
    staging = Path(str(job["staging_dir"])) / uuid.uuid4().hex[:12]
    result = adapt_source_file(
        source,
        staging,
        source_file=str(job["source_file"]),
        views=list(job["views"]),
        source_id=str(job["source_id"]),
        repository=str(job["repository"]),
        revision=str(job["revision"]),
        plan_id=str(job["plan_id"]),
        plan_hash=str(job["plan_hash"]),
        selection_hash=str(job["selection_hash"]),
        identity=dict(job["identity_record"]),
        limits=dict(job["limits"]),
        row_range=None if job.get("row_range") is None else tuple(job["row_range"]),
    )
    result.update(
        staging_dir=str(staging),
        promoted=promoted,
        promote_seconds=promote_seconds,
        key=job.get("key"),
    )
    return result


# ------------------------------------------------------------------ pipeline


@dataclass
class Unit:
    """One source file moving through download, local processing and sealing."""

    key: str
    source_file: str
    #: None when a verified durable source already exists (restart after promotion).
    url: str | None
    partial: Path
    state: Path
    #: Process job without its source identity; None for a download-only unit.
    job: dict[str, Any] | None
    identity_record: dict[str, Any] | None = None
    expected_sha256: str | None = None


def run_pipeline(
    units: Sequence[Unit],
    *,
    limits: TransferLimits,
    revision: str | None,
    download_workers: int,
    process_workers: int,
    scratch: ScratchBudget,
    meter: TransferMeter | None,
    deadline_seconds: float,
    identity_for: Callable[[Unit, TransferResult], dict[str, Any]],
    on_done: Callable[[Unit, TransferResult | None, dict[str, Any] | None], None],
    process: Callable[[Mapping[str, Any]], dict[str, Any]] = process_unit,
    max_in_flight: int | None = None,
) -> dict[str, Any]:
    """Download files concurrently while finished ones are processed in parallel.

    Concurrency is per file only: one sequential stream per file on a thread,
    one process per file for the CPU-bound adaptation. ``on_done`` runs in the
    calling thread, in completion order, which no output depends on. With
    ``process_workers=0`` processing runs inline. After the first failure no
    new work starts, running streams stop at their next read and verified
    scratch files stay for the next run; the first error is then raised.
    """
    if not 1 <= download_workers <= 16 or not 0 <= process_workers <= 16:
        raise ValueError("pipeline needs 1..16 download workers and 0..16 process workers")
    bound = max_in_flight or download_workers + 2 * max(1, process_workers)
    cancel = threading.Event()
    started = time.monotonic()
    queue = deque(units)
    downloads: dict[Future[TransferResult], Unit] = {}
    processes: dict[Future[dict[str, Any]], tuple[Unit, TransferResult | None]] = {}
    held: set[str] = set()
    failures: list[BaseException] = []
    stats: dict[str, Any] = {
        "units": len(units),
        "peak_downloads": 0,
        "peak_in_flight": 0,
        "processed": 0,
    }

    def fail(error: BaseException) -> None:
        failures.append(error)
        cancel.set()

    def finish(unit: Unit, transfer: TransferResult | None, result: dict[str, Any] | None) -> None:
        try:
            on_done(unit, transfer, result)
        except BaseException as exc:
            fail(exc)
        if unit.key in held and not unit.partial.exists():
            scratch.release(unit.key)
            held.discard(unit.key)

    def job_for(unit: Unit, transfer: TransferResult | None) -> dict[str, Any]:
        if unit.job is None:
            raise ValueError("download-only unit has no process job")
        record = unit.identity_record if transfer is None else identity_for(unit, transfer)
        return {**unit.job, "identity_record": record, "key": unit.key}

    pool = ProcessPoolExecutor(process_workers) if process_workers else None

    def start(unit: Unit, transfer: TransferResult | None) -> None:
        job = job_for(unit, transfer)
        if pool is not None:
            processes[pool.submit(process, job)] = (unit, transfer)
            return
        try:
            result = process(job)
        except BaseException as exc:
            fail(exc)
            return
        stats["processed"] += 1
        finish(unit, transfer, result)

    try:
        with ThreadPoolExecutor(download_workers, thread_name_prefix="xlm-source") as streams:
            while queue or downloads or processes:
                while not failures and queue:
                    unit = queue[0]
                    if unit.url is None:
                        queue.popleft()
                        start(unit, None)
                        continue
                    if len(downloads) >= download_workers or len(held) >= bound:
                        break
                    if not scratch.reserve(unit.key, limits.max_file_bytes, unit.partial):
                        if not held:
                            fail(ScratchCapError("scratch cap leaves no room for one file"))
                        break
                    queue.popleft()
                    held.add(unit.key)
                    downloads[
                        streams.submit(
                            download_source,
                            unit.url,
                            unit.partial,
                            unit.state,
                            name=unit.source_file,
                            limits=limits,
                            revision=revision,
                            expected_sha256=unit.expected_sha256,
                            scratch=scratch,
                            scratch_key=unit.key,
                            meter=meter,
                            cancel=cancel,
                        )
                    ] = unit
                stats["peak_downloads"] = max(stats["peak_downloads"], len(downloads))
                stats["peak_in_flight"] = max(stats["peak_in_flight"], len(held))
                if not downloads and not processes:
                    break
                pending: list[Future[Any]] = [*downloads, *processes]
                done, _ = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
                if not failures and time.monotonic() - started > deadline_seconds:
                    fail(SourceTransferError("batch deadline exceeded; rerun to resume"))
                for stream in [future for future in downloads if future in done]:
                    unit = downloads.pop(stream)
                    try:
                        transfer = stream.result()
                    except BaseException as exc:
                        fail(exc)
                        continue
                    if unit.job is None:
                        finish(unit, transfer, None)
                    elif not failures:
                        start(unit, transfer)
                for worker in [future for future in processes if future in done]:
                    unit, prior = processes.pop(worker)
                    try:
                        result = worker.result()
                    except BaseException as exc:
                        fail(exc)
                        continue
                    stats["processed"] += 1
                    finish(unit, prior, result)
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    stats["wall_seconds"] = time.monotonic() - started
    stats["peak_scratch_reserved_bytes"] = scratch.peak_reserved_bytes
    if failures:
        raise failures[0]
    return stats
