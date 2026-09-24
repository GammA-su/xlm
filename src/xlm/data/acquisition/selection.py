"""Selected JSONL records and Parquet row groups through the existing fetcher."""

from __future__ import annotations

import io
import json
import threading
import uuid
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import compute_file_sha256
from xlm.data.acquisition.disk import CapacityLease, StorageCapacityManager
from xlm.data.acquisition.progress import ProgressCorruptionError
from xlm.data.acquisition.records import (
    RecordLimitError,
    StreamingJsonlWriter,
    check_row_group,
    encode_record,
    selected_record,
)
from xlm.data.adapters.jsonl import _pairs_hook_reject_duplicates

if TYPE_CHECKING:
    from xlm.data.acquisition.fetcher import BoundedFetcher

#: Decode batch size for the projected/buffered Parquet path. Larger batches
#: amortize Arrow->Python conversion; order is preserved, so output bytes are
#: unaffected. The legacy exact-range path keeps batch size 1.
PROJECTED_BATCH_SIZE = 512

#: Compressed read chunk for streaming gzip selected-record decode. Tuned by
#: offline benchmark (see tests); correctness never depends on the value.
GZ_SELECT_CHUNK_BYTES = 65536

#: Initial scanned-record lease window (records); windows then grow with use
#: up to ``SCAN_LEASE_MAX_RECORDS``. Execution-only tuning; never plan
#: identity, never output bytes.
ACCOUNTING_BATCH_RECORDS = 256
SCAN_LEASE_MAX_RECORDS = 65536


class RangeReader(io.RawIOBase):
    """Seekable Parquet input; every read is an exact bounded, charged HTTP range."""

    def __init__(self, fetcher: BoundedFetcher, name: str) -> None:
        self.fetcher, self.name, self.position = fetcher, name, 0
        magic, self.length, self.etag = fetcher.fetch_range(name, 0, 3, purpose="parquet-header")
        if magic != b"PAR1":
            raise ValueError("selected Parquet input lacks PAR1 header")

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = (
            offset if whence == 0 else (self.position if whence == 1 else self.length) + offset
        )
        if whence not in (0, 1, 2) or not 0 <= position <= self.length:
            raise ValueError("invalid Parquet range seek")
        self.position = position
        return position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("unbounded Parquet read refused")
        size = min(size, self.length - self.position)
        if size == 0:
            return b""
        value, length, etag = self.fetcher.fetch_range(
            self.name, self.position, self.position + size - 1, purpose="parquet-range"
        )
        if length != self.length or etag != self.etag:
            raise ValueError("Parquet source identity changed between ranges")
        self.position += size
        return value


def _jsonl_selection(
    fetcher: BoundedFetcher,
    name: str,
    start: int,
    stop: int,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    with fetcher._open(name, {}) as response:
        if (
            response.status != 200
            or response.headers.get("Content-Encoding", "identity") != "identity"
        ):
            raise ValueError("selected JSONL requires uncompressed original byte offsets")
        pending = bytearray()
        row, offset = 0, 0
        remaining = (
            int(response.headers["Content-Length"])
            if "Content-Length" in response.headers
            else None
        )
        etag = response.headers.get("ETag")
        if not etag or etag.startswith("W/"):
            raise ValueError("selected JSONL requires a stable strong ETag")
        with fetcher.perf.timed("accounting"):
            fetcher.bind_source(name, etag, remaining)
        # Historical 8 KiB reads and per-read charges, against durable leases.
        transfer = fetcher.budget.transfer_lease(remaining)
        decompressed = CapacityLease(fetcher.capacity_mgr, "decompressed")
        try:
            while row < stop:
                # Bounded lookahead is charged, but the whole shard is never fetched as fallback.
                if b"\n" not in pending and remaining != 0:
                    amount = min(8192, fetcher.plan.limits.max_record_bytes + 1 - len(pending))
                    if remaining is not None:
                        amount = min(amount, remaining)
                    if amount <= 0:
                        raise RecordLimitError("selected JSONL record byte bound exceeded")
                    with fetcher.perf.timed("body", file=name):
                        chunk = fetcher.budget.read_leased(response, transfer, amount)
                    decompressed.consume(len(chunk))
                    fetcher.perf.record_decompressed(len(chunk))
                    fetcher.perf.record_file_bytes(name, len(chunk))
                    pending.extend(chunk)
                    if remaining is not None:
                        remaining -= len(chunk)
                    if not chunk:
                        remaining = 0
                    if b"\n" not in pending and remaining != 0:
                        continue
                if not pending:
                    raise ValueError("selected row range extends beyond original corpus")
                boundary = pending.find(b"\n") + 1
                size = boundary or len(pending)
                if size > fetcher.plan.limits.max_record_bytes:
                    raise RecordLimitError("selected JSONL record byte bound exceeded")
                raw = bytes(pending[:size])
                del pending[:size]
                if raw.strip():
                    if scanned_counter is not None:
                        scanned_counter()
                    else:
                        fetcher.capacity_mgr.record_units(
                            "records_scanned", 1, fetcher.plan.limits.max_scanned_records
                        )
                    fetcher.perf.record_scanned(1, file=name)
                    with fetcher.perf.timed("decode", file=name):
                        record = json.loads(raw, object_pairs_hook=_pairs_hook_reject_duplicates)
                    if not isinstance(record, dict):
                        raise ValueError("selected record must be a JSON object")
                    if row >= start:
                        fetcher.perf.record_retained(1, file=name)
                        yield (
                            record,
                            raw,
                            {
                                "row_index": row,
                                "byte_offset": offset,
                                "byte_length": len(raw),
                                "format": "jsonl",
                                "etag": response.headers.get("ETag"),
                            },
                        )
                    row += 1
                offset += len(raw)
        finally:
            transfer.close()
            decompressed.close()


def _gz_jsonl_selection(
    fetcher: BoundedFetcher,
    name: str,
    start: int,
    stop: int,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    """Bounded selected rows from ``.jsonl.gz`` via incremental streaming decode.

    Reads compressed bytes from the start, incrementally gunzips, frames
    lines, and stops early once ``stop`` is reached: the connection is closed
    and remaining bytes are never transferred. Decompressed offsets identify
    rows (the compressed file has no stable per-row byte offsets).
    """
    limits = fetcher.plan.limits
    with fetcher._open(name, {}) as response:
        if response.status != 200:
            raise ValueError("selected JSONL.GZ requires a 200 full-body response")
        remaining = (
            int(response.headers["Content-Length"])
            if "Content-Length" in response.headers
            else None
        )
        etag = response.headers.get("ETag")
        if not etag or etag.startswith("W/"):
            raise ValueError("selected JSONL.GZ requires a stable strong ETag")
        with fetcher.perf.timed("accounting"):
            fetcher.bind_source(name, etag, remaining)
        decompressor = zlib.decompressobj(31)
        pending = bytearray()
        row, offset = 0, 0
        compressed_read = 0
        decompressed_out = 0
        input_exhausted = False
        stream_ended = False
        # Historical read sizes and per-piece charges, against durable leases.
        transfer = fetcher.budget.transfer_lease(remaining)
        decompressed = CapacityLease(fetcher.capacity_mgr, "decompressed")
        try:
            while row < stop:
                if b"\n" not in pending and not stream_ended:
                    if input_exhausted:
                        tail = decompressor.flush()
                        if tail:
                            decompressed_out += len(tail)
                            decompressed.consume(len(tail))
                            fetcher.perf.record_decompressed(len(tail))
                            pending.extend(tail)
                        stream_ended = True
                    else:
                        if remaining is not None and remaining <= 0:
                            input_exhausted = True
                            continue
                        amount = min(GZ_SELECT_CHUNK_BYTES, limits.max_record_bytes + 1)
                        if remaining is not None:
                            amount = min(amount, remaining)
                        with fetcher.perf.timed("body", file=name):
                            chunk = fetcher.budget.read_leased(response, transfer, amount)
                        fetcher.perf.record_file_bytes(name, len(chunk))
                        if remaining is not None:
                            remaining -= len(chunk)
                        compressed_read += len(chunk)
                        if not chunk:
                            input_exhausted = True
                        else:
                            try:
                                piece = decompressor.decompress(chunk)
                            except Exception as exc:
                                raise RecordLimitError(
                                    f"bounded gzip decode failed: {type(exc).__name__}"
                                ) from exc
                            decompressed_out += len(piece)
                            if decompressed_out > limits.max_decompressed_bytes or (
                                decompressed_out
                                > limits.max_decompression_ratio * max(1, compressed_read)
                            ):
                                raise RecordLimitError("decompression byte/ratio limit exceeded")
                            decompressed.consume(len(piece))
                            fetcher.perf.record_decompressed(len(piece))
                            pending.extend(piece)
                            if b"\n" not in pending and len(pending) > limits.max_record_bytes:
                                raise RecordLimitError("selected JSONL record byte bound exceeded")
                    if b"\n" not in pending and not stream_ended:
                        continue
                if not pending:
                    raise ValueError("selected row range extends beyond original corpus")
                boundary = pending.find(b"\n") + 1
                size = boundary or len(pending)
                if size > limits.max_record_bytes:
                    raise RecordLimitError("selected JSONL record byte bound exceeded")
                raw = bytes(pending[:size])
                del pending[:size]
                if raw.strip():
                    if scanned_counter is not None:
                        scanned_counter()
                    else:
                        fetcher.capacity_mgr.record_units(
                            "records_scanned", 1, limits.max_scanned_records
                        )
                    fetcher.perf.record_scanned(1, file=name)
                    with fetcher.perf.timed("decode", file=name):
                        record = json.loads(raw, object_pairs_hook=_pairs_hook_reject_duplicates)
                    if not isinstance(record, dict):
                        raise ValueError("selected record must be a JSON object")
                    if row >= start:
                        fetcher.perf.record_retained(1, file=name)
                        yield (
                            record,
                            raw,
                            {
                                "row_index": row,
                                "byte_offset": offset,
                                "byte_length": len(raw),
                                "format": "jsonl",
                                "encoding": "gzip",
                                "etag": response.headers.get("ETag"),
                            },
                        )
                    row += 1
                offset += len(raw)
        finally:
            transfer.close()
            decompressed.close()


def _selection_iterator(
    fetcher: BoundedFetcher,
    source: str,
    start: int,
    stop: int,
    *,
    columns: list[str] | None = None,
    coalesce_bytes: int | None = None,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    """Dispatch selected-record iteration by file kind (JSONL, GZ, Parquet)."""
    if source.endswith(".jsonl"):
        return _jsonl_selection(fetcher, source, start, stop, scanned_counter)
    if source.endswith(".jsonl.gz"):
        return _gz_jsonl_selection(fetcher, source, start, stop, scanned_counter)
    return _parquet_selection(
        fetcher,
        source,
        start,
        stop,
        columns=columns,
        coalesce_bytes=coalesce_bytes,
        scanned_counter=scanned_counter,
    )


def _parquet_selection(
    fetcher: BoundedFetcher,
    name: str,
    start: int,
    stop: int,
    *,
    columns: list[str] | None = None,
    coalesce_bytes: int | None = None,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    """Selected Parquet rows; legacy exact ranges unless projection/coalescing set."""
    if columns is None and coalesce_bytes is None:
        return _parquet_selection_exact(fetcher, name, start, stop, scanned_counter)
    return _parquet_selection_projected(
        fetcher, name, start, stop, columns, coalesce_bytes, scanned_counter
    )


def _parquet_selection_exact(
    fetcher: BoundedFetcher,
    name: str,
    start: int,
    stop: int,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    limits = fetcher.plan.limits
    with (
        RangeReader(fetcher, name) as stream,
        closing(CapacityLease(fetcher.capacity_mgr, "decompressed")) as decompressed,
    ):
        with fetcher.perf.timed("metadata", file=name):
            parquet = pq.ParquetFile(
                stream,
                pre_buffer=False,
                buffer_size=0,
                thrift_string_size_limit=limits.max_parser_bytes,
                thrift_container_size_limit=limits.max_parser_bytes,
            )
        if stop > parquet.metadata.num_rows:
            raise ValueError("selected row range extends beyond Parquet corpus")
        base = 0
        for group in range(parquet.num_row_groups):
            end = base + parquet.metadata.row_group(group).num_rows
            if end > start and base < stop:
                with fetcher.perf.timed("metadata", file=name):
                    check_row_group(parquet, group, limits)
                # Charge whole decoded row group; skipped rows are still decoded work.
                group_bytes = parquet.metadata.row_group(group).total_byte_size
                decompressed.consume(group_bytes)
                fetcher.perf.record_decompressed(group_bytes)
                fetcher.perf.record_parquet_group()
                local = 0
                with fetcher.perf.timed("decode", file=name):
                    batches = list(
                        parquet.iter_batches(batch_size=1, row_groups=[group], use_threads=False)
                    )
                for batch in batches:
                    fetcher._check_deadline()
                    with fetcher.perf.timed("decode", file=name):
                        rows = batch.to_pylist()
                    for record in rows:
                        if scanned_counter is not None:
                            scanned_counter()
                        else:
                            fetcher.capacity_mgr.record_units(
                                "records_scanned", 1, limits.max_scanned_records
                            )
                        fetcher.perf.record_scanned(1, file=name)
                        with fetcher.perf.timed("decode", file=name):
                            raw = encode_record(record)
                        if len(raw) > limits.max_record_bytes:
                            raise RecordLimitError("Parquet record byte bound exceeded")
                        if start <= base + local < stop:
                            fetcher.perf.record_retained(1, file=name)
                            yield (
                                record,
                                raw,
                                {
                                    "row_index": base + local,
                                    "row_group": group,
                                    "row_in_group": local,
                                    "format": "parquet",
                                    "etag": stream.etag,
                                    "original_record_hash_convention": (
                                        "canonical JSON serialization, not compressed bytes"
                                    ),
                                },
                            )
                        local += 1
            base = end


def _resolve_projection(
    parquet: pq.ParquetFile, name: str, columns: list[str] | None
) -> tuple[list[str], list[int]]:
    """Resolve projected names to schema indices, failing closed on unknown fields."""
    schema_names = parquet.schema.names
    if columns is None:
        return list(schema_names), list(range(len(schema_names)))
    unknown = [field for field in columns if field not in schema_names]
    if unknown:
        raise ValueError(f"projected fields not present in '{name}': {sorted(unknown)}")
    # Preserve caller order deterministically (callers pass sorted or contract order).
    seen: list[str] = []
    for field in columns:
        if field not in seen:
            seen.append(field)
    return seen, [schema_names.index(field) for field in seen]


def _column_chunk_spans(
    parquet: pq.ParquetFile, group: int, col_indices: list[int]
) -> tuple[list[tuple[int, int]], int, int]:
    """Byte spans ``(start, end_exclusive)`` of selected column chunks plus totals.

    Returns ``(spans, selected_compressed, group_total)``. Spans follow the
    Parquet column-chunk layout (dictionary page first when present), so only
    stored bytes of required columns are ever requested.
    """
    row_group = parquet.metadata.row_group(group)
    group_total = int(row_group.total_byte_size)
    spans: list[tuple[int, int]] = []
    selected = 0
    for position in col_indices:
        column = row_group.column(position)
        data_offset = int(column.data_page_offset)
        dict_offset = column.dictionary_page_offset
        chunk_total = int(column.total_compressed_size)
        if dict_offset is None:
            start = data_offset
        else:
            start = min(data_offset, int(dict_offset))
        spans.append((start, start + chunk_total))
        selected += chunk_total
    spans.sort()
    return spans, selected, group_total


def _merge_spans(
    spans: list[tuple[int, int]], gap_threshold: int, max_span: int
) -> tuple[list[tuple[int, int]], int]:
    """Deterministically merge spans separated by gaps ``<= threshold``.

    Returns ``(merged, gap_bytes)``. Merged spans longer than ``max_span``
    are split so no single range exceeds the parser-bound contract.
    Correctness never depends on the threshold: only framing changes.
    """
    merged: list[tuple[int, int]] = []
    gap_bytes = 0
    current_start: int | None = None
    current_end = 0
    for start, end in spans:
        if current_start is None:
            current_start, current_end = start, end
        elif start - current_end <= gap_threshold:
            gap_bytes += max(0, start - current_end)
            current_end = max(current_end, end)
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end
    if current_start is not None:
        merged.append((current_start, current_end))
    if max_span < 1:
        raise ValueError("merged span bound must be positive")
    split: list[tuple[int, int]] = []
    for start, end in merged:
        while end - start > max_span:
            split.append((start, start + max_span))
            start += max_span
        split.append((start, end))
    return split, gap_bytes


class BufferedGroupInput(io.RawIOBase):
    """Serve one row group's prefetched column spans; footer reads delegate.

    The Parquet footer/metadata phase passes every read through to bounded
    range fetches exactly like :class:`RangeReader`. Once the caller starts a
    row group, its merged column spans are prefetched (each charged as
    transfer) and subsequent reads inside those spans are served from memory.
    Anything outside (e.g. footer re-reads) falls back to a direct bounded
    range fetch, so correctness never depends on prefetch coverage.
    """

    def __init__(self, fetcher: BoundedFetcher, name: str) -> None:
        self.fetcher, self.name, self.position = fetcher, name, 0
        magic, self.length, self.etag = fetcher.fetch_range(name, 0, 3, purpose="parquet-header")
        if magic != b"PAR1":
            raise ValueError("selected Parquet input lacks PAR1 header")
        self._spans: list[tuple[int, bytes]] = []

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = (
            offset if whence == 0 else (self.position if whence == 1 else self.length) + offset
        )
        if whence not in (0, 1, 2) or not 0 <= position <= self.length:
            raise ValueError("invalid Parquet range seek")
        self.position = position
        return position

    def prefetch_group(self, spans: list[tuple[int, int]]) -> None:
        """Fetch merged ``(start, end_exclusive)`` spans for one row group."""
        buffered: list[tuple[int, bytes]] = []
        for start, end in spans:
            if not 0 <= start < end <= self.length:
                raise ValueError("column span outside source bounds")
            value, total, etag = self.fetcher.fetch_range(
                self.name, start, end - 1, purpose="parquet-column"
            )
            if total != self.length or etag != self.etag:
                raise ValueError("Parquet source identity changed between ranges")
            buffered.append((start, value))
            self.fetcher.perf.record_coalesced_ranges(ranges=1, gap_bytes=0)
        self._spans = buffered

    def release_group(self) -> None:
        self._spans = []

    def _covered(self, start: int, end: int) -> bytes | None:
        for span_start, data in self._spans:
            if span_start <= start and end <= span_start + len(data):
                return data[start - span_start : end - span_start]
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("unbounded Parquet read refused")
        size = min(size, self.length - self.position)
        if size == 0:
            return b""
        start, end = self.position, self.position + size
        covered = self._covered(start, end)
        if covered is not None:
            self.position = end
            return covered
        value, length, etag = self.fetcher.fetch_range(
            self.name, start, end - 1, purpose="parquet-range"
        )
        if length != self.length or etag != self.etag:
            raise ValueError("Parquet source identity changed between ranges")
        self.position = end
        return value


def _parquet_selection_projected(
    fetcher: BoundedFetcher,
    name: str,
    start: int,
    stop: int,
    columns: list[str] | None,
    coalesce_bytes: int | None,
    scanned_counter: Callable[[], None] | None = None,
) -> Any:
    """Projected narrow decode: required column chunks only, merged spans."""
    limits = fetcher.plan.limits
    gap_threshold = coalesce_bytes if coalesce_bytes is not None else 0
    with (
        BufferedGroupInput(fetcher, name) as stream,
        closing(CapacityLease(fetcher.capacity_mgr, "decompressed")) as decompressed,
    ):
        with fetcher.perf.timed("metadata", file=name):
            parquet = pq.ParquetFile(
                stream,
                pre_buffer=False,
                buffer_size=0,
                thrift_string_size_limit=limits.max_parser_bytes,
                thrift_container_size_limit=limits.max_parser_bytes,
            )
        if stop > parquet.metadata.num_rows:
            raise ValueError("selected row range extends beyond Parquet corpus")
        projected_names, col_indices = _resolve_projection(parquet, name, columns)
        base = 0
        for group in range(parquet.num_row_groups):
            end = base + parquet.metadata.row_group(group).num_rows
            if end > start and base < stop:
                with fetcher.perf.timed("metadata", file=name):
                    check_row_group(parquet, group, limits)
                spans, selected_sum, group_total = _column_chunk_spans(parquet, group, col_indices)
                # Fail-closed before any column bytes move: only selected work
                # is charged, never the skipped columns.
                decompressed.consume(selected_sum)
                fetcher.perf.record_decompressed(selected_sum)
                fetcher.perf.record_projection(
                    selected_bytes=selected_sum, skipped_bytes=group_total - selected_sum
                )
                fetcher.perf.record_parquet_group()
                fetcher.perf.record_column_chunks(len(col_indices))
                merged, gap_bytes = _merge_spans(spans, gap_threshold, limits.max_parser_bytes)
                if gap_bytes:
                    fetcher.perf.record_coalesced_ranges(ranges=0, gap_bytes=gap_bytes)
                # Network ahead of decode for this group; inner range fetches
                # carry their own open/body timings (no double counting here).
                stream.prefetch_group(merged)
                try:
                    local = 0
                    with fetcher.perf.timed("decode", file=name):
                        batches = list(
                            parquet.iter_batches(
                                batch_size=PROJECTED_BATCH_SIZE,
                                row_groups=[group],
                                columns=projected_names,
                                use_threads=False,
                            )
                        )
                    for batch in batches:
                        fetcher._check_deadline()
                        with fetcher.perf.timed("decode", file=name):
                            rows = batch.to_pylist()
                        for record in rows:
                            if scanned_counter is not None:
                                scanned_counter()
                            else:
                                fetcher.capacity_mgr.record_units(
                                    "records_scanned", 1, limits.max_scanned_records
                                )
                            fetcher.perf.record_scanned(1, file=name)
                            with fetcher.perf.timed("decode", file=name):
                                raw = encode_record(record)
                            if len(raw) > limits.max_record_bytes:
                                raise RecordLimitError("Parquet record byte bound exceeded")
                            if start <= base + local < stop:
                                fetcher.perf.record_retained(1, file=name)
                                yield (
                                    record,
                                    raw,
                                    {
                                        "row_index": base + local,
                                        "row_group": group,
                                        "row_in_group": local,
                                        "format": "parquet",
                                        "etag": stream.etag,
                                        "original_record_hash_convention": (
                                            "canonical JSON serialization, not compressed bytes"
                                        ),
                                    },
                                )
                            local += 1
                finally:
                    stream.release_group()
            base = end


class _BatchCommitter:
    """Scanned counts and staging bytes on durable pre-reserved leases.

    Scanned records and temp staging bytes are charged against leases that
    were durably reserved before the work, so a crash leaves them counted
    (conservative) instead of losing an uncommitted batch. Limits refuse at
    the same logical record as per-record charges: scanned overuse fills
    exactly to the ceiling before refusing, and staging overuse refuses
    before the line is written. Windows grow geometrically (from
    ``ACCOUNTING_BATCH_RECORDS`` records / 64 KiB) so journal writes are
    O(log n + n / cap) per source. :meth:`close` settles the exact usage.
    """

    def __init__(self, capacity_mgr: StorageCapacityManager, scanned_maximum: int) -> None:
        self._capacity_mgr = capacity_mgr
        self._scan = CapacityLease(
            capacity_mgr,
            "records_scanned",
            initial=ACCOUNTING_BATCH_RECORDS,
            window=SCAN_LEASE_MAX_RECORDS,
            limit=scanned_maximum,
            message="cumulative records_scanned limit exceeded",
        )
        self._temp = CapacityLease(capacity_mgr, "temp")

    def scanned(self) -> None:
        """Reserve scan allowance before parsing, including skipped records."""
        self._scan.consume_to_ceiling(1)

    def add_retained(self, payload_len: int) -> None:
        self._temp.consume(payload_len)

    def maybe_commit(self, *, force: bool = False) -> None:
        self._capacity_mgr.check_deadline()
        if force:
            self.close()

    def close(self) -> None:
        try:
            self._scan.close()
        finally:
            self._temp.close()


def _select_one_source_to_chunk(
    fetcher: BoundedFetcher,
    source: str,
    chunk_path: Path,
    selection_hash: str,
    destination_name: str,
    stop_event: threading.Event,
    retained_total: list[int],
    retained_lock: threading.Lock,
) -> dict[str, Any]:
    """Fetch one independent selected file into its own bounded chunk file.

    All global budgets (transfer/decompressed/requests/scanned/deadline) reuse
    the fetcher's shared durable reservation model, so concurrent workers
    contend exactly. Returns per-file count/size for the ordered merge.
    Raises the original worker exception unchanged for deterministic reporting.
    """
    plan = fetcher.plan
    start, stop = plan.row_ranges[source] if plan.row_ranges else (0, 0)
    columns = plan.projected_fields
    coalesce_bytes = plan.range_coalesce_bytes
    batcher = _BatchCommitter(fetcher.capacity_mgr, plan.limits.max_scanned_records)

    try:
        with fetcher.perf.file_worker(source):
            ensure_plain_path(chunk_path)
            count = 0
            fetcher.capacity_mgr.check_deadline()
            with StreamingJsonlWriter(chunk_path) as writer:
                iterator = _selection_iterator(
                    fetcher,
                    source,
                    start,
                    stop,
                    columns=columns,
                    coalesce_bytes=coalesce_bytes,
                    scanned_counter=batcher.scanned,
                )
                for record, raw, locator in iterator:
                    if stop_event.is_set():
                        raise RuntimeError("concurrent file worker cancelled after sibling failure")
                    with fetcher.perf.timed("serialize", file=source):
                        payload = selected_record(
                            record,
                            {
                                **locator,
                                "source_id": plan.source_id,
                                "repository": plan.repository,
                                "revision": plan.revision,
                                "source_file": source,
                                "selection_hash": selection_hash,
                            },
                            raw,
                        )
                    if len(payload) > plan.limits.max_record_bytes + 8192:
                        raise RecordLimitError(
                            "selected record plus locator exceeds bounded serialization"
                        )
                    with retained_lock:
                        retained_total[0] += 1
                        if retained_total[0] > plan.limits.max_records:
                            raise RecordLimitError("selected record limit exceeded")
                    batcher.add_retained(len(payload))
                    writer.write_line(payload)
                    fetcher.perf.record_file_bytes(destination_name, len(payload))
                    count += 1
                    batcher.maybe_commit()
                batcher.maybe_commit(force=True)
            fetcher.perf.record_write_seconds(writer.flush_seconds, file=source)
            fetcher.perf.record_peak_rss(writer.peak_rss_bytes)
            return {"source": source, "count": count, "size": writer.size, "path": str(chunk_path)}
    finally:
        batcher.close()


def _acquire_selection_serial(
    fetcher: BoundedFetcher, destination: Path, name: str, selection_hash: str
) -> None:
    """Original serial path: plan order, single temp file, byte-identical merge."""
    plan = fetcher.plan
    temporary = fetcher.partial_dir / f"selection-{uuid.uuid4().hex}.part"
    ensure_plain_path(temporary)
    count = 0
    columns = plan.projected_fields
    coalesce_bytes = plan.range_coalesce_bytes
    with StreamingJsonlWriter(temporary) as writer:
        for source in plan.selected_files:
            start, stop = plan.row_ranges[source] if plan.row_ranges else (0, 0)
            write_mark = writer.flush_seconds
            batcher = _BatchCommitter(fetcher.capacity_mgr, plan.limits.max_scanned_records)

            try:
                with fetcher.perf.file_worker(source):
                    fetcher.capacity_mgr.check_deadline()
                    iterator = _selection_iterator(
                        fetcher,
                        source,
                        start,
                        stop,
                        columns=columns,
                        coalesce_bytes=coalesce_bytes,
                        scanned_counter=batcher.scanned,
                    )
                    for record, raw, locator in iterator:
                        with fetcher.perf.timed("serialize", file=source):
                            payload = selected_record(
                                record,
                                {
                                    **locator,
                                    "source_id": plan.source_id,
                                    "repository": plan.repository,
                                    "revision": plan.revision,
                                    "source_file": source,
                                    "selection_hash": selection_hash,
                                },
                                raw,
                            )
                        if len(payload) > plan.limits.max_record_bytes + 8192:
                            raise RecordLimitError(
                                "selected record plus locator exceeds bounded serialization"
                            )
                        batcher.add_retained(len(payload))
                        writer.write_line(payload)
                        fetcher.perf.record_file_bytes(name, len(payload))
                        count += 1
                        if count > plan.limits.max_records:
                            raise RecordLimitError("selected record limit exceeded")
                        batcher.maybe_commit()
                    batcher.maybe_commit(force=True)
            finally:
                batcher.close()
            fetcher.perf.record_write_seconds(writer.flush_seconds - write_mark, file=source)
    fetcher.perf.record_peak_rss(writer.peak_rss_bytes)
    digest, size = writer.digest.hexdigest(), writer.size
    with fetcher.perf.timed("serialize", file=name):
        from xlm.data.acquisition.publication import publish_output

        publish_output(fetcher, name, temporary, size, digest, count, written=writer.written)


def _acquire_selection_parallel(
    fetcher: BoundedFetcher,
    destination: Path,
    name: str,
    selection_hash: str,
    workers: int,
) -> None:
    """Bounded file-level parallelism with deterministic ordered merge.

    Unit of concurrency is independent selected source files (never
    per-request/per-column). Workers write per-file chunk files concurrently;
    the main thread merges chunks in ``plan.selected_files`` order so
    completion order never affects bytes. Memory stays bounded by streaming
    chunk files (no in-memory buffering of large selections).
    """
    plan = fetcher.plan
    run_id = uuid.uuid4().hex
    chunk_paths = [
        fetcher.partial_dir / f"selection-{run_id}-{index}.part"
        for index, _ in enumerate(plan.selected_files)
    ]
    for path in chunk_paths:
        ensure_plain_path(path)
    temporary = fetcher.partial_dir / f"selection-{run_id}.part"
    ensure_plain_path(temporary)
    stop_event = threading.Event()
    retained_total: list[int] = [0]
    retained_lock = threading.Lock()

    def _run_source(index_source: tuple[int, str]) -> dict[str, Any]:
        index, source = index_source
        return _select_one_source_to_chunk(
            fetcher,
            source,
            chunk_paths[index],
            selection_hash,
            name,
            stop_event,
            retained_total,
            retained_lock,
        )

    indexed = list(enumerate(plan.selected_files))
    per_file: list[dict[str, Any]] | None = None
    try:
        with ThreadPoolExecutor(
            max_workers=min(workers, len(plan.selected_files)),
            thread_name_prefix="xlm-select",
        ) as pool:
            futures = [pool.submit(_run_source, item) for item in indexed]
            try:
                # Collect in plan order for deterministic failure reporting.
                ordered = [future.result() for future in futures]
            except BaseException:
                stop_event.set()
                for future in futures:
                    future.cancel()
                raise
            per_file = ordered
        assert per_file is not None
        by_source = {entry["source"]: entry for entry in per_file}
        total_size = sum(int(entry["size"]) for entry in per_file)
        # Reserve the merged staging before copying: fail-closed if 2x temp exceeds bound.
        merge_token = fetcher.capacity_mgr.reserve_disk_space(fetcher.scratch_dir, total_size)
        count = 0
        try:
            with StreamingJsonlWriter(temporary) as writer:
                for source in plan.selected_files:
                    entry = by_source[source]
                    chunk = Path(str(entry["path"]))
                    with chunk.open("rb") as stream:
                        while True:
                            block = stream.read(65536)
                            if not block:
                                break
                            writer.write_raw(block)
                    count += int(entry["count"])
                    if count > plan.limits.max_records:
                        raise RecordLimitError("selected record limit exceeded")
                size, digest = writer.size, writer.digest.hexdigest()
            fetcher.perf.record_write_seconds(writer.flush_seconds, file=name)
            fetcher.perf.record_peak_rss(writer.peak_rss_bytes)
        except BaseException:
            temporary.unlink(missing_ok=True)
            fetcher.capacity_mgr.settle("temp", merge_token, 0)
            raise
        # Account the merged staging, then retire chunk files (bounded 2x peak).
        fetcher.capacity_mgr.settle("temp", merge_token, total_size)
        with fetcher.perf.timed("serialize", file=name):
            from xlm.data.acquisition.publication import publish_output

            publish_output(fetcher, name, temporary, size, digest, count, written=writer.written)
    finally:
        for path in chunk_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def acquire_selection(fetcher: BoundedFetcher) -> None:
    """Retry private selections with spent budgets intact; completed outputs are immutable."""
    plan = fetcher.plan
    if not plan.row_ranges:
        raise ValueError("selected acquisition requires explicit row ranges")
    unsupported = [
        name
        for name in plan.selected_files
        if not name.endswith((".jsonl", ".jsonl.gz", ".parquet"))
    ]
    if unsupported:
        raise ValueError("selected format unsupported; no whole-shard fallback")
    name = "selected_records.jsonl"
    destination = fetcher.output_dir / name
    ensure_plain_path(destination)
    with fetcher.perf.timed("accounting"):
        fetcher.journal.save()
    prior = fetcher.journal.state.file_progress.get(name)
    if destination.exists():
        if not prior or prior.status != "completed" or not prior.content_sha256:
            raise ProgressCorruptionError("incomplete selection destination; refusing replacement")
        if (
            compute_file_sha256(destination, max_bytes=plan.limits.max_output_disk_bytes)
            != prior.content_sha256
        ):
            raise ProgressCorruptionError("selected artifact integrity checksum mismatch")
        fetcher.capacity_mgr.record_cache_hit()
        fetcher.perf.record_cache_hit()
        return
    if prior and prior.status == "completed":
        raise ProgressCorruptionError("completed selection missing; refusing repair")
    fetcher.partial_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive fresh private attempt; earlier interrupted staging remains charged.
    fetcher.capacity_mgr.reconcile_disk("temp", fetcher.partial_dir)
    fetcher.capacity_mgr.reconcile_disk("output", fetcher.output_dir)
    # Worker-independent locator identity: max_workers must not change bytes.
    selection_hash = plan.compute_selection_hash()
    workers = max(1, min(plan.limits.max_workers, len(plan.selected_files)))
    if workers <= 1:
        _acquire_selection_serial(fetcher, destination, name, selection_hash)
    else:
        _acquire_selection_parallel(fetcher, destination, name, selection_hash, workers)
