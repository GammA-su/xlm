"""Bounded acquisition record inspection and explicit selected-record serialization."""
from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

import pyarrow.parquet as pq

from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.adapters.jsonl import _pairs_hook_reject_duplicates


class RecordLimitError(ValueError):
    """Record, parser or decompression bound exceeded."""


def jsonl_records(stream: BinaryIO, limits: AcquisitionLimits,
                  capacity: StorageCapacityManager | None = None) -> Iterator[tuple[int, int, bytes, dict[str, Any]]]:
    offset, row = 0, 0
    while True:
        raw = stream.readline(limits.max_record_bytes + 1)
        if not raw:
            return
        if capacity:
            capacity.check_deadline()
            capacity.record_decompressed(len(raw))
        if len(raw) > limits.max_record_bytes:
            raise RecordLimitError("record byte limit exceeded")
        if raw.strip():
            value = json.loads(raw, object_pairs_hook=_pairs_hook_reject_duplicates)
            if not isinstance(value, dict):
                raise ValueError("corpus JSONL record must be an object")
            yield row, offset, raw, value
            row += 1
        offset += len(raw)


def inspect_records(path: Path, name: str, limits: AcquisitionLimits,
                    capacity: StorageCapacityManager | None = None) -> int | None:
    if name.endswith((".jsonl", ".jsonl.gz")):
        stream = gzip.open(path, "rb") if name.endswith(".gz") else path.open("rb")
        count, produced = 0, 0
        with stream:
            for _, offset, raw, _ in jsonl_records(stream, limits, capacity):
                count += 1
                produced = offset + len(raw)
                if count > limits.max_records:
                    raise RecordLimitError("whole-file record limit exceeded; original cannot be truncated")
                if produced > limits.max_decompressed_bytes or (name.endswith(".gz") and produced > path.stat().st_size * limits.max_decompression_ratio):
                    raise RecordLimitError("decompression byte/ratio limit exceeded")
        return count
    if name.endswith(".parquet"):
        parquet = pq.ParquetFile(path, pre_buffer=False,
                                 thrift_string_size_limit=limits.max_parser_bytes,
                                 thrift_container_size_limit=limits.max_parser_bytes)
        if parquet.metadata.num_rows > limits.max_records:
            raise RecordLimitError("whole-file record limit exceeded")
        count = 0
        for group in range(parquet.num_row_groups):
            check_row_group(parquet, group, limits)
            for batch in parquet.iter_batches(batch_size=1, row_groups=[group], use_threads=False):
                for record in batch.to_pylist():
                    raw = encode_record(record)
                    if len(raw) > limits.max_record_bytes:
                        raise RecordLimitError("Parquet record byte limit exceeded")
                    if capacity:
                        capacity.check_deadline()
                        capacity.record_decompressed(batch.nbytes)
                    count += 1
        return count
    # Opaque transport objects are explicitly not proof of any corpus records.
    return None


def check_row_group(parquet: pq.ParquetFile, group: int, limits: AcquisitionLimits) -> None:
    metadata = parquet.metadata.row_group(group)
    if metadata.total_byte_size > limits.max_parser_bytes:
        raise RecordLimitError("Parquet row group exceeds parser byte bound")
    for index in range(metadata.num_columns):
        column = metadata.column(index)
        if column.total_uncompressed_size > limits.max_decompression_ratio * max(1, column.total_compressed_size):
            raise RecordLimitError("Parquet decompression ratio exceeded")


def encode_record(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def selected_record(record: dict[str, Any], locator: dict[str, Any], raw: bytes) -> bytes:
    if "_xlm_acquisition" in record:
        raise ValueError("source record conflicts with reserved acquisition locator field")
    return encode_record({**record, "_xlm_acquisition": {
        **locator, "original_record_sha256": hashlib.sha256(raw).hexdigest(),
    }})
