"""Local adaptation of one verified source Parquet for any single-adapter Mix-01 source.

The record stream is the certified selected-record serialization
(:func:`~xlm.data.acquisition.source_parquet.selected_payloads`), produced in
memory from the local file and parsed back exactly as ``xlm data adapt``
parses ``selected_records.jsonl``; the registered adapter then runs unchanged.
For the same rows and selection identity the documents are therefore the ones
the range path writes. The rejection ledger is stored zstd-compressed and is
identified by the SHA-256 of its uncompressed version-1 JSONL bytes.

Source-agnostic primitives also serve the frozen Essential-Web campaign.
Optional processing bounds preserve its call signatures and default behavior;
an additive compatibility record binds the shared-code repair.

A plan may bind intra-file row-group parallelism
(:mod:`~xlm.data.acquisition.source_rowgroups`). Workers then decode and adapt
row groups exactly as the serial loop does, and the coordinator replays their
rows in file order through the same bounds, writer and digests, so documents,
ledger, summary and the first raised error equal the serial ones.

A verified ``.jsonl.gz`` source (:mod:`~xlm.data.acquisition.jsonl_gz`) is
decoded serially to its verified end: every gzip member, CRC and length
trailer is checked, the decompressed bytes, line bytes, row count and
expansion ratio are bounded by the plan's limits, and each line must be one
strict JSON object. It always covers the whole file. Rows are located by
their zero-based line index and then follow exactly the serial loop above
(same selected-record serialization, adapter, ledger and summary).
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.acquisition import jsonl_gz
from xlm.data.acquisition.projection import (
    ProjectionRefusal,
    parquet_field_leaves,
    resolve_projection,
)
from xlm.data.acquisition.records import RecordLimitError, StreamingJsonlWriter
from xlm.data.acquisition.sampling import discover_layout_local
from xlm.data.acquisition.source_formats import (
    JSONL_GZ,
    REPRESENTATIONS,
    SourceFormatError,
    source_format,
)
from xlm.data.acquisition.source_growth import (
    GrowthLimitError,
    OutputBudget,
    ProcessingGrowth,
    bounded_json,
)
from xlm.data.acquisition.source_parquet import (
    DECODE_BATCH_ROWS,
    check_parquet_magic,
    file_sha256,
    located_record,
    promote_source,
    selected_payloads,
)
from xlm.data.acquisition.source_rowgroups import (
    DOCUMENT,
    REJECTION,
    GroupResult,
    GroupTask,
    RowGroupError,
    RowGroupParallel,
    RowGroupPool,
    SourceStat,
    check_adapter,
    configured,
    peak_rss,
)
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import RecordRejectedError
from xlm.data.adapters.registry import ADAPTERS_BY_ID as ADAPTERS_BY_ID
from xlm.data.adapters.rejections import (
    DOCUMENTS_FILENAME,
    SUMMARY_FILENAME,
    build_rejection_record,
    build_summary,
    serialize_document,
    serialize_rejection,
)
from xlm.data.sources.essential_web_bulk import BulkError, layout_record
from xlm.data.sources.essential_web_local import compress_ledger, publish_progress, read_ledger

LEDGER_FILENAME = "adaptation_rejections.jsonl.zst"
SUMMARY_VERSION = 2
RECEIPT_FILENAME = "receipt.json"


class SourceAdaptError(RuntimeError):
    """A local source file, its layout or its adaptation violates a bound."""


def check_layout(
    path: Path, source_file: str, adapter_id: str, view_id: str, limits: Mapping[str, Any]
) -> dict[str, Any]:
    """Footer-only safety checks before any row is decoded."""
    check_parquet_magic(path)
    ratio = float(limits["max_decompression_ratio"])
    try:
        record = layout_record(
            discover_layout_local(
                path,
                name=source_file,
                max_parser_bytes=int(limits["max_parser_bytes"]),
                max_decompression_ratio=ratio,
            ),
            columns_for(adapter_id, view_id),
            ratio,
        )
    except BulkError as exc:
        raise SourceAdaptError(str(exc)) from exc
    refused = {g["index"]: g["refusal"] for g in record["groups"] if g["refusal"] is not None}
    if refused:
        raise SourceAdaptError(f"{source_file}: row groups refused: {refused}")
    if int(record["rows"]) > int(limits["max_rows_per_file"]):
        raise SourceAdaptError(f"{source_file}: row count exceeds the per-file bound")
    return record


def jsonl_gz_bounds(limits: Mapping[str, Any]) -> jsonl_gz.JsonlGzBounds:
    """The decode bounds a plan's processing limits impose on one ``.jsonl.gz`` file."""
    return jsonl_gz.JsonlGzBounds(
        max_decoded_bytes=int(limits["max_decoded_bytes_per_file"]),
        max_line_bytes=int(limits["max_record_bytes"]),
        max_rows=int(limits["max_rows_per_file"]),
        max_decompression_ratio=float(limits["max_decompression_ratio"]),
    )


def check_jsonl_gz_layout(path: Path, source_file: str) -> dict[str, Any]:
    """Pre-decode checks of a verified ``.jsonl.gz``; it has no footer, so rows are counted."""
    try:
        jsonl_gz.check_gzip_header(path)
    except jsonl_gz.JsonlGzError as exc:
        raise SourceAdaptError(f"{source_file}: {exc}") from exc
    return {"rows": None, "groups": [], "compressed_bytes": path.stat().st_size}


def jsonl_gz_payloads(
    path: Path,
    *,
    source_file: str,
    locator: Mapping[str, Any],
    etag: str,
    max_record_bytes: int,
    bounds: jsonl_gz.JsonlGzBounds,
    counters: dict[str, int] | None = None,
) -> Iterator[tuple[int, bytes]]:
    """``(row_index, selected-record line)`` of every row of a verified local ``.jsonl.gz``.

    The serialization is the one :func:`selected_payloads` uses; the locator
    names the zero-based line index and the decompressed byte offset of the
    line. A format or bound violation refuses the whole file.
    """
    decoded = jsonl_gz.DecodeCounters()
    offset = 0
    try:
        with path.open("rb") as stream:
            for row, record, line in jsonl_gz.iter_records(stream, bounds, decoded):
                try:
                    raw, payload = located_record(
                        record,
                        {
                            "row_index": row,
                            "decoded_byte_offset": offset,
                            "format": jsonl_gz.FORMAT,
                            "etag": etag,
                            "original_record_hash_convention": (
                                "canonical JSON serialization, not compressed bytes"
                            ),
                            **locator,
                            "source_file": source_file,
                        },
                    )
                except ValueError as exc:
                    raise SourceAdaptError(f"{source_file} row {row}: {exc}") from exc
                offset += len(line) + 1
                if len(raw) > max_record_bytes:
                    raise RecordLimitError(
                        f"JSONL record byte bound exceeded: row={row} "
                        f"encoded_bytes={len(raw)} limit={max_record_bytes}"
                    )
                if len(payload) > max_record_bytes + 8192:
                    raise RecordLimitError(
                        "selected record plus locator exceeds bounded serialization"
                    )
                yield row, payload
    except jsonl_gz.JsonlGzError as exc:
        raise SourceAdaptError(f"{source_file}: {exc}") from exc
    finally:
        if counters is not None:
            counters["decoded_bytes"] = decoded.decoded_bytes
            counters["compressed_bytes"] = decoded.compressed_bytes
            counters["gzip_members"] = decoded.members
            counters["max_line_bytes"] = decoded.max_line_bytes


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass
class _Tally:
    """Running totals of one adaptation, in file row order."""

    rows: int = 0
    accepted: int = 0
    canonical_bytes: int = 0
    raw_bytes: int = 0
    max_record: int = 0
    decoded: int = 0
    raw_digest: Any = field(default_factory=hashlib.sha256)
    ledger: bytearray = field(default_factory=bytearray)
    codes: dict[str, int] = field(default_factory=dict)


def _decoded_error(source_file: str) -> RecordLimitError:
    return RecordLimitError(f"'{source_file}' exceeds its decoded byte bound")


def adapt_row_group(task: GroupTask) -> GroupResult:
    """Worker entry point: decode and adapt one whole row group, writing nothing.

    The decode, serialization, bounds and adapter calls are those of
    :func:`selected_payloads` and the serial loop of :func:`adapt_source_file`
    for the same rows; only totals that span the file (decoded, canonical and
    ledger bytes) are left to the coordinator's ordered replay.
    """
    cpu_started = time.process_time()
    result = GroupResult(group=task.group, pid=os.getpid())
    payloads: list[bytes] = []
    documents: list[bytes] = []
    rejections: list[bytes] = []
    start, stop = task.row_range
    try:
        task.source.check()
        parquet = pq.ParquetFile(
            task.source.path,
            pre_buffer=False,
            thrift_string_size_limit=task.max_parser_bytes,
            thrift_container_size_limit=task.max_parser_bytes,
        )
        try:
            if int(parquet.metadata.row_group(task.group).num_rows) != task.rows:
                raise RowGroupError(f"row group {task.group} differs from the planned layout")
            factory: Any = ADAPTERS_BY_ID[task.adapter_id]
            adapter = factory()
            decoded = local = 0
            for batch in parquet.iter_batches(
                batch_size=DECODE_BATCH_ROWS,
                row_groups=[task.group],
                columns=list(task.logical),
                use_threads=False,
            ):
                result.batches.append((int(batch.nbytes), 0))
                decoded += int(batch.nbytes)
                if decoded > task.max_decoded_bytes:
                    raise _decoded_error(task.source_file)
                low = max(0, start - task.base - local)
                high = min(batch.num_rows, stop - task.base - local)
                if low < high:
                    for offset, value in enumerate(
                        batch.slice(low, high - low).to_pylist(), start=low
                    ):
                        row_index = task.base + local + offset
                        raw, payload = located_record(
                            value,
                            {
                                "row_index": row_index,
                                "row_group": task.group,
                                "row_in_group": local + offset,
                                "format": "parquet",
                                "etag": task.etag,
                                "original_record_hash_convention": (
                                    "canonical JSON serialization, not compressed bytes"
                                ),
                                **task.locator,
                                "source_file": task.source_file,
                            },
                        )
                        if len(raw) > task.max_record_bytes:
                            raise RecordLimitError(
                                f"Parquet record byte bound exceeded: row={row_index} "
                                f"encoded_bytes={len(raw)} limit={task.max_record_bytes}"
                            )
                        if len(payload) > task.max_record_bytes + 8192:
                            raise RecordLimitError(
                                "selected record plus locator exceeds bounded serialization"
                            )
                        payloads.append(payload)
                        result.max_payload = max(result.max_payload, len(payload))
                        record = json.loads(payload)
                        try:
                            document = adapter.adapt(
                                record,
                                source_file=task.source_file,
                                source_row=row_index,
                                source_revision=task.revision,
                            )
                        except RecordRejectedError as exc:
                            line = (
                                serialize_rejection(
                                    build_rejection_record(
                                        input_line=row_index - start + 1,
                                        source_id=task.source_id,
                                        source_revision=task.revision,
                                        source_file=task.source_file,
                                        source_row=row_index,
                                        adapter_id=task.adapter_id,
                                        error=exc,
                                        original_record_sha256=record["_xlm_acquisition"][
                                            "original_record_sha256"
                                        ],
                                    )
                                ).encode("utf-8")
                                + b"\n"
                            )
                            rejections.append(line)
                            result.rejection_lengths.append(len(line))
                            result.rejection_codes.append(type(exc).__name__)
                            result.kinds.append(REJECTION)
                        else:
                            if (
                                document.source_id != task.source_id
                                or document.source_revision != task.revision
                            ):
                                raise SourceAdaptError(
                                    "adapter produced a document of another source or revision"
                                )
                            line = serialize_document(document).encode("utf-8") + b"\n"
                            documents.append(line)
                            result.document_lengths.append(len(line))
                            result.document_text_bytes.append(document.utf8_byte_count)
                            result.kinds.append(DOCUMENT)
                        result.batches[-1] = (result.batches[-1][0], result.batches[-1][1] + 1)
                local += batch.num_rows
                if task.base + local >= stop:
                    break
        finally:
            parquet.close()
    except Exception as exc:
        result.error = _portable(exc)
    result.payloads = b"".join(payloads)
    result.documents = b"".join(documents)
    result.rejections = b"".join(rejections)
    result.cpu_seconds = time.process_time() - cpu_started
    result.peak_rss_bytes = peak_rss()
    return result


def _portable(error: Exception) -> BaseException:
    """The worker's own exception when it survives a process boundary, else a summary."""
    try:
        restored = pickle.loads(pickle.dumps(error))
    except Exception:
        return RowGroupError(f"{type(error).__name__}: {error}")
    return restored if isinstance(restored, BaseException) else RowGroupError(str(error))


def group_tasks(
    path: Path,
    *,
    source_file: str,
    source_id: str,
    revision: str,
    adapter_id: str,
    locator: Mapping[str, Any],
    etag: str,
    columns: tuple[str, ...],
    limits: Mapping[str, Any],
    row_range: tuple[int, int],
) -> list[GroupTask]:
    """Deterministic row-group partition of ``row_range``, in file order.

    Projection and range refusals are the ones :func:`selected_payloads` raises.
    """
    max_parser = int(limits["max_parser_bytes"])
    source = SourceStat.of(path)
    parquet = pq.ParquetFile(
        path,
        pre_buffer=False,
        thrift_string_size_limit=max_parser,
        thrift_container_size_limit=max_parser,
    )
    try:
        try:
            logical = tuple(
                resolve_projection(parquet_field_leaves(parquet), columns).logical_fields
            )
        except ProjectionRefusal as exc:
            raise RecordLimitError(f"projection refused for '{source_file}': {exc}") from exc
        start, stop = row_range
        if not 0 <= start < stop <= int(parquet.metadata.num_rows):
            raise ValueError("selected row range extends beyond Parquet corpus")
        tasks: list[GroupTask] = []
        base = 0
        for group in range(parquet.num_row_groups):
            rows = int(parquet.metadata.row_group(group).num_rows)
            if base + rows > start and base < stop:
                tasks.append(
                    GroupTask(
                        source=source,
                        group=group,
                        base=base,
                        rows=rows,
                        row_range=(start, stop),
                        logical=logical,
                        source_file=source_file,
                        source_id=source_id,
                        revision=revision,
                        adapter_id=adapter_id,
                        locator=dict(locator),
                        etag=etag,
                        max_record_bytes=int(limits["max_record_bytes"]),
                        max_parser_bytes=max_parser,
                        max_decoded_bytes=int(limits["max_decoded_bytes_per_file"]),
                    )
                )
            base += rows
    finally:
        parquet.close()
    return tasks


def _replay(
    result: GroupResult,
    tally: _Tally,
    *,
    source_file: str,
    limits: Mapping[str, Any],
    growth: ProcessingGrowth | None,
    budget: OutputBudget | None,
    documents: StreamingJsonlWriter,
    progress: Callable[[], None],
) -> bytes:
    """Apply one row group's rows in order with the serial loop's file-wide checks.

    Every bound is checked at the row where the serial loop checks it, so the
    first raised error is the serial one. The group's documents are then
    written as one block: the bytes and digest are those of per-line writes,
    and the budget's byte arithmetic was already checked per line (the
    physical free-space check runs once for the block). Returns the payload
    bytes still to be hashed, in order, by the caller.
    """
    max_decoded = int(limits["max_decoded_bytes_per_file"])
    ledger_bound = int(limits["max_ledger_bytes"])
    row = document = rejection = 0
    document_offset = rejection_offset = 0
    for decoded, completed in result.batches:
        tally.decoded += decoded
        if tally.decoded > max_decoded:
            raise _decoded_error(source_file)
        for _ in range(completed):
            kind = result.kinds[row]
            row += 1
            tally.rows += 1
            if kind == REJECTION:
                length = result.rejection_lengths[rejection]
                if len(tally.ledger) + length > ledger_bound:
                    raise SourceAdaptError(f"{source_file}: rejection ledger exceeds its bound")
                tally.ledger += result.rejections[rejection_offset : rejection_offset + length]
                code = result.rejection_codes[rejection]
                tally.codes[code] = tally.codes.get(code, 0) + 1
                rejection += 1
                rejection_offset += length
                progress()
                continue
            text_bytes = result.document_text_bytes[document]
            if growth is not None and tally.canonical_bytes + text_bytes > int(
                limits["max_canonical_bytes_per_file"]
            ):
                raise GrowthLimitError("canonical text exceeds its byte ceiling")
            length = result.document_lengths[document]
            if budget is not None and budget.used + document_offset + length > budget.limit:
                # Raises exactly the per-line write's error; nothing was charged yet.
                budget.charge(document_offset + length)
            document += 1
            document_offset += length
            tally.accepted += 1
            tally.canonical_bytes += text_bytes
            progress()
    if row != len(result.kinds) or document_offset != len(result.documents):
        raise RowGroupError(f"row group {result.group} events do not reconcile")
    if result.error is not None:
        raise result.error
    if result.documents:
        documents.write_raw(result.documents)
    tally.raw_bytes += len(result.payloads)
    tally.max_record = max(tally.max_record, result.max_payload)
    return result.payloads


def adapt_source_file(
    path: Path,
    output_dir: Path,
    *,
    source_file: str,
    source_id: str,
    view_id: str,
    adapter_id: str,
    repository: str,
    revision: str,
    plan_id: str,
    plan_hash: str,
    selection_hash: str,
    identity: Mapping[str, Any],
    limits: Mapping[str, Any],
    row_range: tuple[int, int] | None = None,
    progress_path: Path | None = None,
    reserved_tail_bytes: int = 0,
) -> dict[str, Any]:
    """Adapt one verified local source Parquet into a fresh private ``output_dir``.

    Returns counts, hashes and timings, never record content.
    """
    ensure_plain_path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SourceAdaptError("local adaptation needs a fresh private output directory")
    started, cpu_started = time.monotonic(), time.process_time()
    growth = (
        ProcessingGrowth.model_validate(limits["processing_growth"])
        if "processing_growth" in limits
        else None
    )
    budget = (
        OutputBudget(
            growth.output_bytes,
            disk_path=output_dir.parent,
            min_free_bytes=int(limits.get("scratch_min_free_bytes", 0)),
        )
        if growth is not None
        else None
    )
    if budget is not None:
        budget.charge(reserved_tail_bytes)
    parallel = configured(limits)
    try:
        kind = source_format(source_file)
    except SourceFormatError as exc:
        raise SourceAdaptError(str(exc)) from exc
    if parallel is not None:
        if kind == JSONL_GZ:
            raise SourceAdaptError("row-group parallelism applies to Parquet sources only")
        try:
            check_adapter(adapter_id)
        except RowGroupError as exc:
            raise SourceAdaptError(str(exc)) from exc
    if kind == JSONL_GZ:
        if row_range is not None:
            raise SourceAdaptError(f"{source_file}: a .jsonl.gz unit covers the whole file")
        layout = check_jsonl_gz_layout(path, source_file)
        # The row count is known only once the verified stream ends.
        start, stop = 0, -1
    else:
        layout = check_layout(path, source_file, adapter_id, view_id, limits)
        start, stop = row_range or (0, int(layout["rows"]))
    output_dir.mkdir(parents=True)
    factory: Any = ADAPTERS_BY_ID[adapter_id]
    adapter = factory()
    documents = StreamingJsonlWriter(
        output_dir / DOCUMENTS_FILENAME, before_write=None if budget is None else budget.charge
    )
    tally = _Tally()
    counters: dict[str, int] = {}
    ledger_bound = int(limits["max_ledger_bytes"])
    last = 0.0
    locator = {
        "source_id": source_id,
        "repository": repository,
        "revision": revision,
        "selection_hash": selection_hash,
    }
    pool_stats: dict[str, Any] | None = None

    def progress() -> None:
        nonlocal last
        if progress_path is None:
            return
        now = time.monotonic()
        if now - last < 1 and tally.rows != stop - start:
            return
        snapshot = {
            "rows": tally.rows,
            # A .jsonl.gz total is unknown until its end: rows so far stand in for it.
            "total": stop - start if stop >= 0 else tally.rows,
            "documents": tally.accepted,
            "canonical_bytes": tally.canonical_bytes,
            "rejected": tally.rows - tally.accepted,
        }
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        if publish_progress(
            progress_path, snapshot, max_bytes=None if growth is None else growth.progress_bytes
        ):
            last = now

    progress()
    try:
        tasks = (
            []
            if parallel is None
            else group_tasks(
                path,
                source_file=source_file,
                source_id=source_id,
                revision=revision,
                adapter_id=adapter_id,
                locator=locator,
                etag=str(identity["etag"]),
                columns=tuple(columns_for(adapter_id, view_id)),
                limits=limits,
                row_range=(start, stop),
            )
        )
        if parallel is not None and len(tasks) > 1:
            pool_stats = _adapt_parallel(
                parallel,
                tasks,
                tally,
                source_file=source_file,
                limits=limits,
                growth=growth,
                budget=budget,
                documents=documents,
                progress=progress,
            )
        else:
            payloads = (
                jsonl_gz_payloads(
                    path,
                    source_file=source_file,
                    locator=locator,
                    etag=str(identity["etag"]),
                    max_record_bytes=int(limits["max_record_bytes"]),
                    bounds=jsonl_gz_bounds(limits),
                    counters=counters,
                )
                if kind == JSONL_GZ
                else selected_payloads(
                    path,
                    source_file=source_file,
                    locator=locator,
                    etag=str(identity["etag"]),
                    columns=columns_for(adapter_id, view_id),
                    max_record_bytes=int(limits["max_record_bytes"]),
                    max_parser_bytes=int(limits["max_parser_bytes"]),
                    max_decoded_bytes=int(limits["max_decoded_bytes_per_file"]),
                    row_range=(start, stop),
                    counters=counters,
                )
            )
            for row_index, payload in payloads:
                tally.raw_digest.update(payload)
                tally.raw_bytes += len(payload)
                tally.max_record = max(tally.max_record, len(payload))
                tally.rows += 1
                record = json.loads(payload)
                try:
                    document = adapter.adapt(
                        record,
                        source_file=source_file,
                        source_row=row_index,
                        source_revision=revision,
                    )
                except RecordRejectedError as exc:
                    line = serialize_rejection(
                        build_rejection_record(
                            input_line=tally.rows,
                            source_id=source_id,
                            source_revision=revision,
                            source_file=source_file,
                            source_row=row_index,
                            adapter_id=adapter_id,
                            error=exc,
                            original_record_sha256=record["_xlm_acquisition"][
                                "original_record_sha256"
                            ],
                        )
                    )
                    encoded = line.encode("utf-8") + b"\n"
                    if len(tally.ledger) + len(encoded) > ledger_bound:
                        raise SourceAdaptError(
                            f"{source_file}: rejection ledger exceeds its bound"
                        ) from None
                    tally.ledger += encoded
                    tally.codes[type(exc).__name__] = tally.codes.get(type(exc).__name__, 0) + 1
                    progress()
                    continue
                if document.source_id != source_id or document.source_revision != revision:
                    raise SourceAdaptError(
                        "adapter produced a document of another source or revision"
                    )
                if growth is not None and tally.canonical_bytes + document.utf8_byte_count > int(
                    limits["max_canonical_bytes_per_file"]
                ):
                    raise GrowthLimitError("canonical text exceeds its byte ceiling")
                documents.write_line(serialize_document(document).encode("utf-8") + b"\n")
                tally.accepted += 1
                tally.canonical_bytes += document.utf8_byte_count
                progress()
            tally.decoded = counters.get("decoded_bytes", 0)
    except BaseException:
        try:
            documents.close()
        except OSError:
            pass
        raise
    rows, accepted, canonical_bytes, codes = (
        tally.rows,
        tally.accepted,
        tally.canonical_bytes,
        tally.codes,
    )
    if kind == JSONL_GZ:
        if rows < 1:
            raise SourceAdaptError(f"{source_file}: the verified stream holds no row")
        stop = rows
        layout["rows"] = rows
    if rows != stop - start:
        raise SourceAdaptError(f"{source_file}: decoded rows differ from the footer row count")
    documents.close()
    data = bytes(tally.ledger)
    compressed = compress_ledger(data) if data else b""
    ledger_sha256 = hashlib.sha256(data).hexdigest()
    if budget is None:
        _write_bytes(output_dir / LEDGER_FILENAME, compressed)
    else:
        budget.write(output_dir / LEDGER_FILENAME, compressed, min(ledger_bound, budget.limit))
    if hashlib.sha256(read_ledger(output_dir / LEDGER_FILENAME, len(data))).hexdigest() != (
        ledger_sha256
    ):
        raise SourceAdaptError(f"{source_file}: compressed ledger does not round-trip")
    summary = build_summary(
        adapter_id=adapter_id,
        source_id=source_id,
        source_revision=revision,
        plan_id=plan_id,
        plan_hash=plan_hash,
        on_reject="record",
        total_input_records=rows,
        accepted_records=accepted,
        rejected_records=rows - accepted,
        rejection_counts_by_code=codes,
        document_sha256=documents.digest.hexdigest(),
        rejection_sha256=ledger_sha256,
    )
    summary["adaptation_summary_version"] = SUMMARY_VERSION
    summary["view"] = view_id
    summary["input"] = {
        "kind": REPRESENTATIONS[kind],
        "source_file": source_file,
        "sha256": identity["sha256"],
        "etag": identity["etag"],
        "remote_length": identity["length"],
        "row_range": [start, stop],
    }
    summary["rejections"].update(
        file=LEDGER_FILENAME,
        uncompressed_bytes=len(data),
        storage={
            "codec": "zstd",
            "level": 9,
            "compressed_bytes": len(compressed),
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
        },
    )
    summary_data = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if budget is None or growth is None:
        _write_bytes(output_dir / SUMMARY_FILENAME, summary_data)
    else:
        budget.write(output_dir / SUMMARY_FILENAME, summary_data, growth.metadata_bytes)
    progress()
    return {
        "source_file": source_file,
        "rows": rows,
        "row_range": [start, stop],
        "file_rows": int(layout["rows"]),
        "row_groups": len(layout["groups"]),
        "projected_compressed_bytes": int(layout["compressed_bytes"])
        if kind == JSONL_GZ
        else sum(int(group["projected_compressed_bytes"]) for group in layout["groups"]),
        "selected_records_sha256": tally.raw_digest.hexdigest(),
        "selected_records_bytes": tally.raw_bytes,
        "max_selected_record_bytes": tally.max_record,
        "decoded_bytes": tally.decoded,
        "documents": accepted,
        "rejected": rows - accepted,
        "rejection_counts_by_code": dict(sorted(codes.items())),
        "canonical_bytes": canonical_bytes,
        "documents_sha256": documents.digest.hexdigest(),
        "documents_file_bytes": documents.size,
        "adaptation_summary_sha256": file_sha256(output_dir / SUMMARY_FILENAME)[0],
        "rejections_sha256": ledger_sha256,
        "rejections_uncompressed_bytes": len(data),
        "rejections_file_bytes": len(compressed),
        "rejections_file_sha256": hashlib.sha256(compressed).hexdigest(),
        "processing_output_bytes": documents.size
        + len(compressed)
        + len(summary_data)
        + reserved_tail_bytes,
        "process_seconds": time.monotonic() - started,
        # Coordinator plus row-group worker CPU: all of this file's processing.
        "process_cpu_seconds": time.process_time()
        - cpu_started
        + (0.0 if pool_stats is None else float(pool_stats["worker_cpu_seconds"])),
        "row_group_workers": 1 if pool_stats is None else int(pool_stats["workers"]),
        "row_group_pool": pool_stats,
    }


def _adapt_parallel(
    config: RowGroupParallel,
    tasks: list[GroupTask],
    tally: _Tally,
    *,
    source_file: str,
    limits: Mapping[str, Any],
    growth: ProcessingGrowth | None,
    budget: OutputBudget | None,
    documents: StreamingJsonlWriter,
    progress: Callable[[], None],
) -> dict[str, Any]:
    """Row groups in worker processes, merged strictly in file order.

    One hashing thread digests the selected-record bytes group by group in
    file order (at most one group waits for it), overlapping the next replay.
    """
    hashed: Future[None] | None = None
    with ThreadPoolExecutor(1, thread_name_prefix="xlm-rowgroup-hash") as hasher:
        try:
            with RowGroupPool(config, tasks, adapt_row_group) as pool:
                for result in pool.results():
                    payloads = _replay(
                        result,
                        tally,
                        source_file=source_file,
                        limits=limits,
                        growth=growth,
                        budget=budget,
                        documents=documents,
                        progress=progress,
                    )
                    if hashed is not None:
                        hashed.result()
                    hashed = hasher.submit(tally.raw_digest.update, payloads)
                    pool.sample()
        finally:
            if hashed is not None:
                hashed.result()
    tasks[0].source.check()
    return pool.stats.as_dict()


def process_source_unit(job: Mapping[str, Any]) -> dict[str, Any]:
    """Worker entry point: promote one verified source file durably, then adapt it.

    Runs in a separate process. The durable raw copy exists before any
    canonical output; canonical outputs stay in private staging that the
    caller publishes only after sealing the unit receipt.
    """
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    source = Path(str(job["source_path"]))
    growth = (
        ProcessingGrowth.model_validate(job["limits"]["processing_growth"])
        if "processing_growth" in job["limits"]
        else None
    )
    if growth is not None:
        growth = growth.for_source(int(job["identity_record"]["length"]))
    tail_bytes = 0
    if growth is not None:
        if job.get("growth_reserved") != growth.model_dump():
            raise GrowthLimitError("worker has no matching processing-growth reservation")
        # Leave room for the caller's bounded receipt before writing documents.
        tail_bytes = growth.metadata_bytes + len(
            bounded_json(job["identity_record"], growth.metadata_bytes)
        )
    promote_seconds = 0.0
    promoted = False
    if job.get("durable_path") is not None:
        started = time.monotonic()
        promoted = promote_source(
            source,
            Path(str(job["durable_path"])),
            dict(job["identity_record"]),
            max_metadata_bytes=None if growth is None else growth.metadata_bytes,
        )
        promote_seconds = time.monotonic() - started
    staging = Path(str(job["staging_dir"])) / uuid.uuid4().hex[:12]
    result = adapt_source_file(
        source,
        staging,
        source_file=str(job["source_file"]),
        source_id=str(job["source_id"]),
        view_id=str(job["view_id"]),
        adapter_id=str(job["adapter_id"]),
        repository=str(job["repository"]),
        revision=str(job["revision"]),
        plan_id=str(job["plan_id"]),
        plan_hash=str(job["plan_hash"]),
        selection_hash=str(job["selection_hash"]),
        identity=dict(job["identity_record"]),
        limits={
            **job["limits"],
            **({"processing_growth": growth.model_dump()} if growth is not None else {}),
        },
        row_range=None if job.get("row_range") is None else tuple(job["row_range"]),
        progress_path=Path(str(job["progress_path"])) if job.get("progress_path") else None,
        reserved_tail_bytes=tail_bytes,
    )
    result.update(
        staging_dir=str(staging),
        promoted=promoted,
        promote_seconds=promote_seconds,
        key=job.get("key"),
    )
    return result


def scan_documents(path: Path) -> tuple[int, int, str]:
    """``(documents, canonical text bytes, file sha256)`` with per-line checks."""
    digest = hashlib.sha256()
    documents = total = 0
    with path.open("rb") as handle:
        for number, raw in enumerate(handle, start=1):
            digest.update(raw)
            try:
                record = json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                raise SourceAdaptError(f"{path.name} line {number} is not JSON") from exc
            text = record.get("text") if isinstance(record, dict) else None
            count = record.get("utf8_byte_count") if isinstance(record, dict) else None
            if not isinstance(text, str) or len(text.encode("utf-8")) != count:
                raise SourceAdaptError(f"{path.name} line {number} is not a canonical document")
            documents += 1
            total += count
    return documents, total, digest.hexdigest()
