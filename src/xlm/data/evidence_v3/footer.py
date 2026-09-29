"""Bounded Parquet footer metadata parsing and frozen-binding comparison.

Input is only bytes the executor already metered and staged: the footer
metadata (L bytes) plus its 8-byte trailer. The parser reconstructs the
compact buffer ``PAR1 | footer | le32(L) | PAR1`` and lets PyArrow read
metadata only, with Thrift string/container limits equal to the frozen
32 MiB parser cap. Input size is bounded by the 4 MiB response cap before
any parse. Column statistics, key/value metadata and row data are never
read, retained, logged or rendered (T footers may carry text statistics).

Comparison is fail-closed: any contradiction with the frozen bindings is
STOP; nothing is reranked, refreshed or widened.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xlm.data.evidence_v3 import frozen_v3


class FooterError(ValueError):
    """Footer parse or binding mismatch: STOP."""


def trailer_length(trailer: bytes) -> int:
    if len(trailer) != 8 or trailer[4:] != b"PAR1":
        raise FooterError("trailer is not <le32 length>PAR1")
    return int.from_bytes(trailer[:4], "little")


def _metadata(footer: bytes, trailer: bytes) -> Any:
    length = trailer_length(trailer)
    if length != len(footer):
        raise FooterError(f"trailer length {length} != footer bytes {len(footer)}")
    if len(footer) + 8 > frozen_v3.RESPONSE_BODY_BYTES_MAX:
        raise FooterError("footer exceeds the 4 MiB bounded parser input")
    import pyarrow as pa
    import pyarrow.parquet as pq

    compact = b"PAR1" + footer + trailer
    try:
        parquet = pq.ParquetFile(
            pa.BufferReader(compact),
            pre_buffer=False,
            thrift_string_size_limit=frozen_v3.PARSER_BYTES_MAX,
            thrift_container_size_limit=frozen_v3.PARSER_BYTES_MAX,
        )
        return parquet.metadata
    except Exception as exc:  # pyarrow raises several unrelated exception types
        raise FooterError(f"footer metadata does not parse: {type(exc).__name__}: {exc}") from exc


def _chunk(column: Any) -> dict[str, Any]:
    data_offset = int(column.data_page_offset)
    dict_offset = column.dictionary_page_offset
    start = data_offset
    if dict_offset is not None and int(dict_offset) > 0:
        start = min(start, int(dict_offset))
    compressed = int(column.total_compressed_size)
    return {
        "path": str(column.path_in_schema),
        "physical_type": str(column.physical_type),
        "compression": str(column.compression),
        "start": start,
        "end_exclusive": start + compressed,
        "compressed_bytes": compressed,
        "uncompressed_bytes": int(column.total_uncompressed_size),
    }


def _groups(meta: Any, check: Any) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    start_row = 0
    for index in range(int(meta.num_row_groups)):
        check()
        group = meta.row_group(index)
        rows = int(group.num_rows)
        groups.append({"index": index, "start_row": start_row, "num_rows": rows, "meta": group})
        start_row += rows
    if start_row != int(meta.num_rows):
        raise FooterError("row-group rows do not sum to the file row count")
    return groups


def compare_m(
    footer: bytes,
    trailer: bytes,
    *,
    footer_start: int,
    bindings: Mapping[str, Any],
    check: Any,
) -> dict[str, Any]:
    """M: the window's row group must reproduce the frozen projected chunks."""
    meta = _metadata(footer, trailer)
    check()
    window_start, window_end = (int(v) for v in bindings["window"])
    projection = set(bindings["projection"])
    hits = [
        g
        for g in _groups(meta, check)
        if g["start_row"] <= window_start and window_end <= g["start_row"] + g["num_rows"]
    ]
    if len(hits) != 1:
        raise FooterError("the frozen window is not inside exactly one row group")
    group = hits[0]
    chunks: list[dict[str, Any]] = []
    for position in range(int(group["meta"].num_columns)):
        check()
        column = group["meta"].column(position)
        if str(column.path_in_schema).split(".")[0] in projection:
            chunks.append(_chunk(column))
    if len(chunks) != int(bindings["data_chunk_count"]):
        raise FooterError(
            f"{len(chunks)} projected chunks != frozen {bindings['data_chunk_count']}"
        )
    if sum(c["compressed_bytes"] for c in chunks) != int(bindings["data_payload_bytes"]):
        raise FooterError("projected compressed bytes differ from the frozen binding")
    if sum(c["uncompressed_bytes"] for c in chunks) != int(bindings["data_uncompressed_bytes"]):
        raise FooterError("projected uncompressed bytes differ from the frozen binding")
    chunks.sort(key=lambda c: (c["start"], c["path"]))
    cursor = 4
    for chunk in chunks:
        if not 0 < chunk["compressed_bytes"] <= frozen_v3.RESPONSE_BODY_BYTES_MAX:
            raise FooterError("a projected chunk exceeds the 4 MiB buffer")
        if chunk["start"] < cursor or chunk["end_exclusive"] > footer_start:
            raise FooterError("projected chunks overlap or reach into the footer")
        cursor = chunk["end_exclusive"]
    return {
        "num_rows": int(meta.num_rows),
        "num_row_groups": int(meta.num_row_groups),
        "window_row_group": group["index"],
        "window_row_group_start_row": group["start_row"],
        "window_row_group_rows": group["num_rows"],
        "projected_chunks": chunks,
    }


def compare_t(
    footer: bytes,
    trailer: bytes,
    *,
    footer_start: int,
    bindings: Mapping[str, Any],
    check: Any,
) -> dict[str, Any]:
    """T: exactly one text chunk must span the frozen dictionary-inclusive range."""
    meta = _metadata(footer, trailer)
    check()
    column_name = str(bindings["text_column"])
    span = [int(v) for v in bindings["data_span_half_open"]]
    matches: list[dict[str, Any]] = []
    text_chunks = 0
    for group in _groups(meta, check):
        for position in range(int(group["meta"].num_columns)):
            check()
            column = group["meta"].column(position)
            if str(column.path_in_schema) != column_name:
                continue
            text_chunks += 1
            chunk = _chunk(column)
            if [chunk["start"], chunk["end_exclusive"]] == span:
                matches.append(
                    {
                        **chunk,
                        "row_group": group["index"],
                        "start_row": group["start_row"],
                        "num_rows": group["num_rows"],
                    }
                )
    if len(matches) != 1:
        raise FooterError(f"{len(matches)} text chunks match the frozen span (need exactly 1)")
    match = matches[0]
    if match["compressed_bytes"] != int(bindings["data_payload_bytes"]):
        raise FooterError("text chunk compressed bytes differ from the frozen binding")
    if match["end_exclusive"] > footer_start:
        raise FooterError("text chunk reaches into the footer")
    return {
        "num_rows": int(meta.num_rows),
        "num_row_groups": int(meta.num_row_groups),
        "text_chunks": text_chunks,
        "selected_text_chunk": match,
    }
