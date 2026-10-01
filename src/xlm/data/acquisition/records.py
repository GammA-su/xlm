"""Bounded acquisition record inspection and explicit selected-record serialization."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

from xlm.data.acquisition.disk import CapacityLease, StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionLimits, ParquetWindowDecode
from xlm.data.acquisition.written import WrittenPayload
from xlm.data.adapters.jsonl import _pairs_hook_reject_duplicates


class RecordLimitError(ValueError):
    """Record, parser or decompression bound exceeded."""


class RecordStream(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


#: Initial and maximum scanned-record lease windows (records).
SCAN_LEASE_INITIAL_RECORDS = 256
SCAN_LEASE_MAX_RECORDS = 65536


class ScanAccounting:
    """Per-record decompressed charges and scan counts on durable leases.

    Totals equal per-record accounting. Each lease reserves its window before
    the work, so a crash leaves it counted (conservative); a decompressed
    charge refuses exactly when the per-record reservation would, and scanned
    overuse fills exactly to the ceiling before refusing at the same record.
    """

    def __init__(self, capacity: StorageCapacityManager, limits: AcquisitionLimits) -> None:
        self.decompressed = CapacityLease(capacity, "decompressed")
        self.scans = CapacityLease(
            capacity,
            "records_scanned",
            initial=SCAN_LEASE_INITIAL_RECORDS,
            window=SCAN_LEASE_MAX_RECORDS,
            limit=limits.max_scanned_records,
            message="cumulative records_scanned limit exceeded",
        )

    def charge_decompressed(self, amount: int) -> None:
        self.decompressed.consume(amount)

    def scanned(self) -> None:
        self.scans.consume_to_ceiling(1)

    def close(self) -> None:
        try:
            self.scans.close()
        finally:
            self.decompressed.close()


def jsonl_records(
    stream: RecordStream, limits: AcquisitionLimits, capacity: StorageCapacityManager | None = None
) -> Iterator[tuple[int, int, bytes, dict[str, Any]]]:
    offset, row = 0, 0
    accounting = ScanAccounting(capacity, limits) if capacity else None
    try:
        while True:
            raw = stream.readline(limits.max_record_bytes + 1)
            if not raw:
                return
            if offset + len(raw) > limits.max_decompressed_bytes:
                raise RecordLimitError("decompression limit: decompressed byte ceiling exceeded")
            if capacity and accounting:
                capacity.check_deadline()
                accounting.charge_decompressed(len(raw))
            if len(raw) > limits.max_record_bytes:
                raise RecordLimitError("record byte limit exceeded")
            if raw.strip():
                if accounting:
                    accounting.scanned()
                value = json.loads(raw, object_pairs_hook=_pairs_hook_reject_duplicates)
                if not isinstance(value, dict):
                    raise ValueError("corpus JSONL record must be an object")
                yield row, offset, raw, value
                row += 1
            offset += len(raw)
    finally:
        if accounting:
            accounting.close()


def inspect_records(
    path: Path,
    name: str,
    limits: AcquisitionLimits,
    capacity: StorageCapacityManager | None = None,
    *,
    perf: Any | None = None,
) -> int | None:
    """Count records without altering bytes, hashes, or identities.

    ``perf`` is observational only (monotonic timings + counters). When
    supplied, Parquet footer/metadata opening is timed as ``metadata`` and
    row-group decode/iteration as ``decode``; JSONL scanning is timed as
    ``decode``. No data path changes.
    """
    if name.endswith((".jsonl", ".jsonl.gz")):
        parser_limits = limits
        if name.endswith(".gz"):
            parser_limits = limits.model_copy(
                update={
                    "max_decompressed_bytes": min(
                        limits.max_decompressed_bytes,
                        int(path.stat().st_size * limits.max_decompression_ratio),
                    )
                }
            )
        stream = gzip.open(path, "rb") if name.endswith(".gz") else path.open("rb")
        count, produced = 0, 0
        with stream:
            for _, offset, raw, _ in jsonl_records(stream, parser_limits, capacity):
                if perf is not None:
                    with perf.timed("decode", file=name):
                        pass
                count += 1
                produced = offset + len(raw)
                if count > limits.max_records:
                    raise RecordLimitError(
                        "whole-file record limit exceeded; original cannot be truncated"
                    )
                if produced > limits.max_decompressed_bytes or (
                    name.endswith(".gz")
                    and produced > path.stat().st_size * limits.max_decompression_ratio
                ):
                    raise RecordLimitError("decompression byte/ratio limit exceeded")
        return count
    if name.endswith(".parquet"):
        if perf is not None:
            with perf.timed("metadata", file=name):
                parquet = pq.ParquetFile(
                    path,
                    pre_buffer=False,
                    thrift_string_size_limit=limits.max_parser_bytes,
                    thrift_container_size_limit=limits.max_parser_bytes,
                )
        else:
            parquet = pq.ParquetFile(
                path,
                pre_buffer=False,
                thrift_string_size_limit=limits.max_parser_bytes,
                thrift_container_size_limit=limits.max_parser_bytes,
            )
        if parquet.metadata.num_rows > limits.max_records:
            raise RecordLimitError("whole-file record limit exceeded")
        count = 0
        accounting = ScanAccounting(capacity, limits) if capacity else None
        try:
            count = _inspect_parquet_groups(parquet, name, limits, capacity, accounting, perf)
        finally:
            if accounting:
                accounting.close()
        return count
    # Opaque transport objects are explicitly not proof of any corpus records.
    return None


def _inspect_parquet_groups(
    parquet: pq.ParquetFile,
    name: str,
    limits: AcquisitionLimits,
    capacity: StorageCapacityManager | None,
    accounting: ScanAccounting | None,
    perf: Any | None,
) -> int:
    """Decode every row exactly as before (batch size 1) and charge it."""
    count = 0
    for group in range(parquet.num_row_groups):
        if perf is not None:
            with perf.timed("metadata", file=name):
                check_row_group(parquet, group, limits)
        else:
            check_row_group(parquet, group, limits)
        if perf is not None:
            perf.record_parquet_group()
        if perf is not None:
            with perf.timed("decode", file=name):
                batches = list(
                    parquet.iter_batches(batch_size=1, row_groups=[group], use_threads=False)
                )
        else:
            batches = list(
                parquet.iter_batches(batch_size=1, row_groups=[group], use_threads=False)
            )
        for batch in batches:
            if perf is not None:
                with perf.timed("decode", file=name):
                    rows = batch.to_pylist()
            else:
                rows = batch.to_pylist()
            for record in rows:
                if perf is not None:
                    with perf.timed("decode", file=name):
                        raw = encode_record(record)
                else:
                    raw = encode_record(record)
                if len(raw) > limits.max_record_bytes:
                    raise RecordLimitError("Parquet record byte limit exceeded")
                if capacity and accounting:
                    capacity.check_deadline()
                    accounting.charge_decompressed(batch.nbytes)
                    accounting.scanned()
                count += 1
    return count


def check_row_group(parquet: pq.ParquetFile, group: int, limits: AcquisitionLimits) -> None:
    metadata = parquet.metadata.row_group(group)
    if metadata.total_byte_size > limits.max_parser_bytes:
        # All quantities below come from the already-loaded footer metadata;
        # no additional network IO is performed to build this diagnostic.
        columns = [metadata.column(index) for index in range(metadata.num_columns)]
        raise RecordLimitError(
            f"Parquet row group {group} exceeds parser byte bound: "
            f"compared total_byte_size={metadata.total_byte_size} against "
            f"max_parser_bytes={limits.max_parser_bytes}; "
            f"num_rows={metadata.num_rows}; "
            f"num_columns={metadata.num_columns}; "
            f"columns_total_compressed_size="
            f"{sum(column.total_compressed_size for column in columns)}; "
            f"columns_total_uncompressed_size="
            f"{sum(column.total_uncompressed_size for column in columns)}"
        )
    for index in range(metadata.num_columns):
        column = metadata.column(index)
        if column.total_uncompressed_size > limits.max_decompression_ratio * max(
            1, column.total_compressed_size
        ):
            raise RecordLimitError("Parquet decompression ratio exceeded")


def check_window_group(
    parquet: pq.ParquetFile,
    group: int,
    col_indices: list[int],
    limits: AcquisitionLimits,
    window: ParquetWindowDecode,
    stop_in_group: int,
) -> int:
    """Projection-aware physical checks for one streamed window; returns scan rows.

    Unlike :func:`check_row_group` (legacy: whole-group logical size against
    the parser bound), each risk is checked against its own quantity:

    - footer/Thrift parser allocation: bounded when the footer is opened;
    - per HTTP range: every stream read is <= ``stream_buffer_bytes`` <=
      ``max_parser_bytes`` and ``fetch_range`` refuses anything larger;
    - decompression ratio: PROJECTED column chunks only (unprojected chunks
      are never read, so they cannot expand), per column above
      ``ratio_exempt_bytes`` and over the projected aggregate;
    - scanned rows: whole decode batches from the group start through the
      window stop, against ``max_window_scan_rows``;
    - decoded bytes: charged per decoded batch at runtime.
    """
    metadata = parquet.metadata.row_group(group)
    rows = int(metadata.num_rows)
    scan_rows = window.expected_scan_rows(rows, stop_in_group)
    if scan_rows > window.max_window_scan_rows:
        raise RecordLimitError(
            f"Parquet row group {group} window scan exceeds bound: "
            f"expected_scan_rows={scan_rows} against "
            f"max_window_scan_rows={window.max_window_scan_rows}; num_rows={rows}"
        )
    projected = [metadata.column(index) for index in col_indices]
    refusal = window.ratio_refusal(
        (
            (
                str(column.path_in_schema),
                int(column.total_compressed_size),
                int(column.total_uncompressed_size),
            )
            for column in projected
        ),
        limits.max_decompression_ratio,
    )
    if refusal is not None:
        raise RecordLimitError(f"Parquet row group {group} {refusal}")
    return scan_rows


LOCATOR_FIELD = "_xlm_acquisition"
_LOCATOR_FIELD_JSON = json.dumps(LOCATOR_FIELD)


class CanonicalRecordBytes(bytes):
    """``encode_record`` output that remembers the record object it encodes.

    Lets :func:`selected_record` splice the locator into the already
    serialized record instead of serializing the whole record a second time.
    """

    source: dict[str, Any]
    source_snapshot: Any


def _record_snapshot(value: Any) -> Any:
    """Immutable, type-sensitive guard against mutation after canonical encoding.

    Ordinary equality conflates True/1 and +0.0/-0.0 although JSON bytes differ.
    Preserve those distinctions and nested mutable values for the splice guard.
    """
    if isinstance(value, dict):
        return (
            type(value),
            tuple((_record_snapshot(key), _record_snapshot(item)) for key, item in value.items()),
        )
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_record_snapshot(item) for item in value))
    if isinstance(value, float):
        return (type(value), value.hex())
    return (type(value), value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def encode_record(record: dict[str, Any]) -> bytes:
    data = CanonicalRecordBytes((_canonical_json(record) + "\n").encode("utf-8"))
    data.source = record
    data.source_snapshot = _record_snapshot(record)
    return data


def selected_record(record: dict[str, Any], locator: dict[str, Any], raw: bytes) -> bytes:
    """Canonical record plus its locator field; byte-identical to one full encode.

    When ``raw`` is :func:`encode_record` output for this very (unmodified)
    record object and every key sorts on one side of the locator field, the
    locator pair is spliced at that edge of ``raw``: sort-keyed compact JSON
    places it exactly there. Anything else takes the full re-encode.
    """
    if LOCATOR_FIELD in record:
        raise ValueError("source record conflicts with reserved acquisition locator field")
    value = {**locator, "original_record_sha256": hashlib.sha256(raw).hexdigest()}
    if (
        isinstance(raw, CanonicalRecordBytes)
        and getattr(raw, "source", None) is record
        and all(type(key) is str for key in record)
        and getattr(raw, "source_snapshot", None) == _record_snapshot(record)
    ):
        pair = (_LOCATOR_FIELD_JSON + ":" + _canonical_json(value)).encode("utf-8")
        if not record:
            return b"{" + pair + b"}\n"
        if all(key > LOCATOR_FIELD for key in record):
            return b"{" + pair + b"," + raw[1:]
        if all(key < LOCATOR_FIELD for key in record):
            return raw[:-2] + b"," + pair + b"}\n"
    return encode_record({**record, LOCATOR_FIELD: value})


class StreamingJsonlWriter:
    """High-throughput deterministic JSONL staging writer.

    Buffers encoded lines and flushes large binary blocks, so a dense
    selection costs O(blocks) file writes and one fsync instead of O(records)
    writes plus per-record fsync. Content hash, byte size, and record count
    accumulate in the single streaming pass (written, counted, and hashed
    together — no re-hashing). Row order is call order, so output bytes are
    identical to the per-record writer for the same inputs. Peak RSS is
    sampled per flush on a best-effort basis and never fails the write.
    """

    def __init__(
        self,
        path: Path,
        *,
        buffer_bytes: int = 262144,
        before_write: Callable[[int], None] | None = None,
    ) -> None:
        self._stream = path.open("xb")
        self._buffer = bytearray()
        self._bound = max(65536, buffer_bytes)
        self._before_write = before_write
        self.digest = hashlib.sha256()
        self.count = 0
        self.size = 0
        self.peak_rss_bytes = 0
        self.flush_seconds = 0.0
        self.written: WrittenPayload | None = None

    def write_line(self, payload: bytes) -> None:
        """Append one ``\\n``-terminated encoded record."""
        if self._before_write is not None:
            self._before_write(len(payload))
        self._buffer += payload
        self.digest.update(payload)
        self.count += 1
        self.size += len(payload)
        if len(self._buffer) >= self._bound:
            self.flush()

    def write_raw(self, block: bytes) -> None:
        """Append pre-framed bytes (ordered-merge path); hash/size only."""
        if self._before_write is not None:
            self._before_write(len(block))
        self._buffer += block
        self.digest.update(block)
        self.size += len(block)
        if len(self._buffer) >= self._bound:
            self.flush()

    def _sample_rss(self) -> None:
        try:
            import psutil

            rss = int(psutil.Process().memory_info().rss)
        except Exception:
            return
        if rss > self.peak_rss_bytes:
            self.peak_rss_bytes = rss

    def flush(self) -> None:
        if self._buffer:
            started = time.monotonic()
            self._stream.write(self._buffer)
            self._stream.flush()
            self._buffer = bytearray()
            self.flush_seconds += max(0.0, time.monotonic() - started)
            self._sample_rss()

    def close(self) -> None:
        try:
            self.flush()
            started = time.monotonic()
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self.written = WrittenPayload.capture(self._stream, self.size, self.digest)
            self.flush_seconds += max(0.0, time.monotonic() - started)
        finally:
            self._stream.close()

    def __enter__(self) -> StreamingJsonlWriter:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
