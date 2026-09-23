"""Bounded acquisition record inspection and explicit selected-record serialization."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.adapters.jsonl import _pairs_hook_reject_duplicates


class RecordLimitError(ValueError):
    """Record, parser or decompression bound exceeded."""


class RecordStream(Protocol):
    def readline(self, size: int = -1, /) -> bytes: ...


def jsonl_records(
    stream: RecordStream, limits: AcquisitionLimits, capacity: StorageCapacityManager | None = None
) -> Iterator[tuple[int, int, bytes, dict[str, Any]]]:
    offset, row = 0, 0
    while True:
        raw = stream.readline(limits.max_record_bytes + 1)
        if not raw:
            return
        if offset + len(raw) > limits.max_decompressed_bytes:
            raise RecordLimitError("decompression limit: decompressed byte ceiling exceeded")
        if capacity:
            capacity.check_deadline()
            capacity.record_decompressed(len(raw))
        if len(raw) > limits.max_record_bytes:
            raise RecordLimitError("record byte limit exceeded")
        if raw.strip():
            if capacity:
                capacity.record_units("records_scanned", 1, limits.max_scanned_records)
            value = json.loads(raw, object_pairs_hook=_pairs_hook_reject_duplicates)
            if not isinstance(value, dict):
                raise ValueError("corpus JSONL record must be an object")
            yield row, offset, raw, value
            row += 1
        offset += len(raw)


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
                    if capacity:
                        capacity.check_deadline()
                        capacity.record_decompressed(batch.nbytes)
                        capacity.record_units("records_scanned", 1, limits.max_scanned_records)
                    count += 1
        return count
    # Opaque transport objects are explicitly not proof of any corpus records.
    return None


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


def encode_record(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def selected_record(record: dict[str, Any], locator: dict[str, Any], raw: bytes) -> bytes:
    if "_xlm_acquisition" in record:
        raise ValueError("source record conflicts with reserved acquisition locator field")
    return encode_record(
        {
            **record,
            "_xlm_acquisition": {
                **locator,
                "original_record_sha256": hashlib.sha256(raw).hexdigest(),
            },
        }
    )


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

    def __init__(self, path: Path, *, buffer_bytes: int = 262144) -> None:
        self._stream = path.open("xb")
        self._buffer = bytearray()
        self._bound = max(65536, buffer_bytes)
        self.digest = hashlib.sha256()
        self.count = 0
        self.size = 0
        self.peak_rss_bytes = 0
        self.flush_seconds = 0.0

    def write_line(self, payload: bytes) -> None:
        """Append one ``\\n``-terminated encoded record."""
        self._buffer += payload
        self.digest.update(payload)
        self.count += 1
        self.size += len(payload)
        if len(self._buffer) >= self._bound:
            self.flush()

    def write_raw(self, block: bytes) -> None:
        """Append pre-framed bytes (ordered-merge path); hash/size only."""
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
            self.flush_seconds += max(0.0, time.monotonic() - started)
        finally:
            self._stream.close()

    def __enter__(self) -> StreamingJsonlWriter:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
