"""Streaming readers and writers for CanonicalDocument datasets in JSONL and Parquet."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False


_JSON_ENCODED_COLUMNS = ("source_metadata", "transform_log", "cluster_ids")


def _parquet_schema() -> Any:
    """Return the explicit Parquet schema for canonical records.

    An explicit schema keeps every row group identical, so a streamed write cannot
    drift between chunks the way per-chunk type inference can.
    """
    return pa.schema(
        [
            pa.field("doc_id", pa.string()),
            pa.field("source_id", pa.string()),
            pa.field("source_revision", pa.string()),
            pa.field("source_file", pa.string()),
            pa.field("source_row", pa.int64()),
            pa.field("raw_hash", pa.string()),
            pa.field("clean_hash", pa.string()),
            pa.field("text", pa.string()),
            pa.field("utf8_byte_count", pa.int64()),
            pa.field("language", pa.string()),
            pa.field("language_confidence", pa.float64()),
            pa.field("document_kind", pa.string()),
            pa.field("source_metadata", pa.string()),
            pa.field("parent_ids", pa.list_(pa.string())),
            pa.field("license_reference", pa.string()),
            pa.field("transform_log", pa.string()),
            pa.field("quality_reasons", pa.list_(pa.string())),
            pa.field("cluster_ids", pa.string()),
            pa.field("split", pa.string()),
        ]
    )


def _flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    """JSON-encode the nested columns so they survive a Parquet round trip."""
    flattened = dict(record)
    for column in _JSON_ENCODED_COLUMNS:
        flattened[column] = json.dumps(record[column], ensure_ascii=False)
    return flattened


def _inflate_record(row: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON-encoded nested columns produced by :func:`_flatten_record`."""
    for column in _JSON_ENCODED_COLUMNS:
        row[column] = json.loads(row[column])
    return row


class CanonicalDatasetWriter:
    """Writer for saving CanonicalDocument sequences in JSONL and Parquet."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_jsonl(
        self,
        documents: Iterable[CanonicalDocument],
        filename: str = "documents.jsonl",
    ) -> Path:
        """Write documents to a JSONL file, validating records on write."""
        target_path = self.output_dir / filename
        with target_path.open("w", encoding="utf-8", newline="\n") as f:
            for doc in documents:
                # doc.__post_init__() already validated byte count and split
                line = json.dumps(doc.to_dict(), ensure_ascii=False)
                f.write(line + "\n")
        return target_path

    def write_parquet(
        self,
        documents: Iterable[CanonicalDocument],
        filename: str = "documents.parquet",
    ) -> Path:
        """Write documents to a Parquet file preserving all C02 fields.

        This materializes every record; prefer :meth:`write_parquet_stream` for
        inputs whose size is not known to be small.
        """
        return self.write_parquet_stream(documents, filename=filename)

    def write_parquet_stream(
        self,
        documents: Iterable[CanonicalDocument],
        filename: str = "documents.parquet",
        row_group_size: int = 512,
    ) -> Path:
        """Write documents to Parquet in bounded row-group batches.

        At most ``row_group_size`` records are buffered at a time, so peak memory is
        set by the batch size rather than by the length of ``documents`` (C07).
        """
        if not HAS_PYARROW:
            raise RuntimeError("pyarrow is required to write Parquet files.")
        if row_group_size < 1:
            raise ValueError(f"row_group_size must be positive, got {row_group_size}")

        target_path = self.output_dir / filename
        schema = _parquet_schema()
        batch: list[dict[str, Any]] = []
        writer: Any = None

        try:
            writer = pq.ParquetWriter(target_path, schema)
            for doc in documents:
                batch.append(_flatten_record(doc.to_dict()))
                if len(batch) >= row_group_size:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        finally:
            if writer is not None:
                writer.close()
        return target_path


class CanonicalDatasetReader:
    """Reader for loading CanonicalDocument sequences from JSONL and Parquet."""

    @staticmethod
    def read_jsonl(path: Path) -> Iterator[CanonicalDocument]:
        """Read documents from a JSONL file in streaming fashion."""
        if not path.is_file():
            raise FileNotFoundError(f"Canonical dataset JSONL file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean_line = line.strip()
                if not clean_line:
                    continue
                try:
                    data = json.loads(clean_line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON at line {line_no} in {path}: {e}") from e

                yield CanonicalDocument(**data)

    @staticmethod
    def read_parquet(path: Path, batch_size: int = 512) -> Iterator[CanonicalDocument]:
        """Read documents from a Parquet file one bounded row-group batch at a time.

        Only ``batch_size`` rows are decoded at once, so a shard larger than RAM is
        still readable (C07).
        """
        if not HAS_PYARROW:
            raise RuntimeError("pyarrow is required to read Parquet files.")
        if not path.is_file():
            raise FileNotFoundError(f"Canonical dataset Parquet file not found: {path}")

        parquet_file = pq.ParquetFile(path)
        for record_batch in parquet_file.iter_batches(batch_size=batch_size):
            for row in record_batch.to_pylist():
                yield CanonicalDocument(**_inflate_record(row))

    @staticmethod
    def read_shards(directory: Path, batch_size: int = 512) -> Iterator[CanonicalDocument]:
        """Stream every canonical shard in a directory, in stable sorted order.

        Parquet and JSONL shards are both accepted. Shards are opened one at a time
        and never concatenated in memory, so document count is bounded by the input
        rather than by available RAM.
        """
        if not directory.is_dir():
            raise NotADirectoryError(f"Canonical shard directory not found: {directory}")

        shards = sorted(
            [p for p in directory.iterdir() if p.suffix in (".parquet", ".jsonl")],
            key=lambda p: p.name,
        )
        if not shards:
            raise FileNotFoundError(f"No .parquet or .jsonl shards found in: {directory}")

        for shard in shards:
            if shard.suffix == ".parquet":
                yield from CanonicalDatasetReader.read_parquet(shard, batch_size=batch_size)
            else:
                yield from CanonicalDatasetReader.read_jsonl(shard)


def create_dataset_manifest(
    documents: Iterable[CanonicalDocument],
    source_id: str,
    source_revision: str,
) -> dict[str, Any]:
    """Compute summary statistics and manifest for a collection of canonical documents."""
    doc_ids: list[str] = []
    total_bytes = 0
    splits_count: dict[str, int] = {}
    clean_hashes: list[str] = []

    for doc in documents:
        doc_ids.append(doc.doc_id)
        total_bytes += doc.utf8_byte_count
        splits_count[doc.split] = splits_count.get(doc.split, 0) + 1
        clean_hashes.append(doc.clean_hash)

    manifest_hash = compute_sha256("".join(clean_hashes))

    return {
        "source_id": source_id,
        "source_revision": source_revision,
        "num_documents": len(doc_ids),
        "total_utf8_bytes": total_bytes,
        "split_counts": splits_count,
        "doc_ids": doc_ids,
        "content_hash": manifest_hash,
    }
