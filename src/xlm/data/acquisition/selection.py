"""Selected JSONL records and Parquet row groups through the existing fetcher."""

from __future__ import annotations

import hashlib
import io
import json
import os
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import compute_file_sha256
from xlm.data.acquisition.disk import AtomicFileWriter
from xlm.data.acquisition.progress import ProgressCorruptionError
from xlm.data.acquisition.records import (
    RecordLimitError,
    check_row_group,
    encode_record,
    selected_record,
)
from xlm.data.adapters.jsonl import _pairs_hook_reject_duplicates

if TYPE_CHECKING:
    from xlm.data.acquisition.fetcher import BoundedFetcher


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


def _jsonl_selection(fetcher: BoundedFetcher, name: str, start: int, stop: int) -> Any:
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
            fetcher.journal.bind_source(name, etag, remaining)
        while row < stop:
            # Bounded lookahead is charged, but the whole shard is never fetched as fallback.
            if b"\n" not in pending and remaining != 0:
                amount = min(8192, fetcher.plan.limits.max_record_bytes + 1 - len(pending))
                if remaining is not None:
                    amount = min(amount, remaining)
                if amount <= 0:
                    raise RecordLimitError("selected JSONL record byte bound exceeded")
                with fetcher.perf.timed("body", file=name):
                    chunk = fetcher.budget.read_chunk(response, amount)
                fetcher.capacity_mgr.record_decompressed(len(chunk))
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


def _parquet_selection(fetcher: BoundedFetcher, name: str, start: int, stop: int) -> Any:
    limits = fetcher.plan.limits
    with RangeReader(fetcher, name) as stream:
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
                fetcher.capacity_mgr.record_decompressed(group_bytes)
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


def acquire_selection(fetcher: BoundedFetcher) -> None:
    """Retry private selections with spent budgets intact; completed outputs are immutable."""
    plan = fetcher.plan
    if not plan.row_ranges:
        raise ValueError("selected acquisition requires explicit row ranges")
    unsupported = [
        name for name in plan.selected_files if not name.endswith((".jsonl", ".parquet"))
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
    import uuid

    temporary = fetcher.partial_dir / f"selection-{uuid.uuid4().hex}.part"
    ensure_plain_path(temporary)
    fetcher.capacity_mgr.reconcile_disk("temp", fetcher.partial_dir)
    fetcher.capacity_mgr.reconcile_disk("output", fetcher.output_dir)
    digest, count, size = hashlib.sha256(), 0, 0
    with temporary.open("xb") as output:
        for source in plan.selected_files:
            start, stop = plan.row_ranges[source]
            with fetcher.perf.file_worker(source):
                iterator = (
                    _jsonl_selection(fetcher, source, start, stop)
                    if source.endswith(".jsonl")
                    else _parquet_selection(fetcher, source, start, stop)
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
                                "selection_hash": plan.compute_behavioral_hash(),
                            },
                            raw,
                        )
                    if len(payload) > plan.limits.max_record_bytes + 8192:
                        raise RecordLimitError(
                            "selected record plus locator exceeds bounded serialization"
                        )
                    token = fetcher.capacity_mgr.reserve_disk_space(
                        fetcher.scratch_dir, len(payload)
                    )
                    with fetcher.perf.timed("serialize", file=source):
                        output.write(payload)
                        output.flush()
                        os.fsync(output.fileno())
                    fetcher.capacity_mgr.settle("temp", token, len(payload))
                    fetcher.perf.record_file_bytes(name, len(payload))
                    digest.update(payload)
                    count, size = count + 1, size + len(payload)
                    if count > plan.limits.max_records:
                        raise RecordLimitError("selected record limit exceeded")
    token = fetcher.capacity_mgr.reserve_disk_space(fetcher.output_dir, size, is_temp=False)
    with fetcher.perf.timed("serialize", file=name):
        AtomicFileWriter.atomic_complete(temporary, destination)
    fetcher.capacity_mgr.settle("output", token, size)
    with fetcher.perf.timed("accounting"):
        fetcher.journal.mark_file_completed(name, size, digest=digest.hexdigest(), records=count)
