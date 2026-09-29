"""Metadata-only Parquet footer checks and layout reconstruction.

Input is only structural bytes the engine already fetched and identity-checked:
footer metadata plus its 8-byte trailer. PyArrow reads ``PAR1 | footer |
le32(L) | PAR1`` as metadata only, with Thrift limits set to the frozen
parser bound. Column statistics and key/value metadata are never accessed,
logged or rendered (T footers may carry text statistics). Any contradiction
with the frozen bindings is STOP; nothing is refreshed or widened.
"""

from __future__ import annotations

from typing import Any

from xlm.data.evidence_v4 import frozen


class LayoutError(ValueError):
    """Footer parse or frozen-binding mismatch: STOP."""


def trailer_length(trailer: bytes) -> int:
    if len(trailer) != 8 or trailer[4:] != b"PAR1":
        raise LayoutError("trailer is not <le32 length>PAR1")
    return int.from_bytes(trailer[:4], "little")


def _metadata(footer: bytes, trailer: bytes) -> Any:
    if trailer_length(trailer) != len(footer):
        raise LayoutError(f"trailer length {trailer_length(trailer)} != footer bytes {len(footer)}")
    if len(footer) + 8 > frozen.BODY_BYTES_PER_RESPONSE_MAX:
        raise LayoutError("footer exceeds the 4 MiB bounded parser input")
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        parquet = pq.ParquetFile(
            pa.BufferReader(b"PAR1" + footer + trailer),
            pre_buffer=False,
            thrift_string_size_limit=frozen.THRIFT_LIMIT_BYTES,
            thrift_container_size_limit=frozen.THRIFT_LIMIT_BYTES,
        )
        return parquet.metadata
    except Exception as exc:  # pyarrow raises several unrelated exception types
        raise LayoutError(f"footer metadata does not parse: {type(exc).__name__}") from exc


def _chunk(column: Any) -> dict[str, Any]:
    start = int(column.data_page_offset)
    dictionary = column.dictionary_page_offset
    if dictionary is not None and int(dictionary) > 0:
        start = min(start, int(dictionary))
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


def _groups(meta: Any) -> list[tuple[int, int, int, Any]]:
    groups: list[tuple[int, int, int, Any]] = []
    first = 0
    for index in range(int(meta.num_row_groups)):
        group = meta.row_group(index)
        rows = int(group.num_rows)
        groups.append((index, first, rows, group))
        first += rows
    if first != int(meta.num_rows):
        raise LayoutError("row-group rows do not sum to the file row count")
    return groups


def m_layout(body: bytes, *, footer_start: int, bindings: frozen.MBindings) -> dict[str, Any]:
    """M footer+trailer body: the window's row group must reproduce the frozen chunks."""
    meta = _metadata(body[:-8], body[-8:])
    window_start, window_end = bindings.window
    hits = [g for g in _groups(meta) if g[1] <= window_start and window_end <= g[1] + g[2]]
    if len(hits) != 1:
        raise LayoutError("the frozen window is not inside exactly one row group")
    index, first, rows, group = hits[0]
    chunks = [
        _chunk(group.column(i))
        for i in range(int(group.num_columns))
        if str(group.column(i).path_in_schema).split(".")[0] in bindings.projection
    ]
    if len(chunks) != bindings.data_chunk_count:
        raise LayoutError(f"{len(chunks)} projected chunks != frozen {bindings.data_chunk_count}")
    if sum(c["compressed_bytes"] for c in chunks) != bindings.data_payload_bytes:
        raise LayoutError("projected compressed bytes differ from the frozen binding")
    if sum(c["uncompressed_bytes"] for c in chunks) != bindings.data_uncompressed_bytes:
        raise LayoutError("projected uncompressed bytes differ from the frozen binding")
    chunks.sort(key=lambda c: (c["start"], c["path"]))
    cursor = 4
    for chunk in chunks:
        if chunk["compressed_bytes"] <= 0 or chunk["start"] < cursor:
            raise LayoutError("projected chunks are empty or overlap")
        if chunk["end_exclusive"] > footer_start:
            raise LayoutError("a projected chunk reaches into the footer")
        cursor = chunk["end_exclusive"]
    return {
        "num_rows": int(meta.num_rows),
        "num_row_groups": int(meta.num_row_groups),
        "window": list(bindings.window),
        "window_row_group": index,
        "window_row_group_first_row": first,
        "window_row_group_rows": rows,
        "footer_length": len(body) - 8,
        "footer_range": [footer_start, footer_start + len(body) - 1],
        "projected_chunks": chunks,
        "projected_span_half_open": [chunks[0]["start"], chunks[-1]["end_exclusive"]],
    }


def t_layout(
    footer: bytes, trailer: bytes, *, footer_start: int, bindings: frozen.TBindings
) -> dict[str, Any]:
    """T footer: exactly one text chunk must equal the frozen dictionary-inclusive span."""
    meta = _metadata(footer, trailer)
    matches: list[dict[str, Any]] = []
    text_chunks = 0
    for index, first, rows, group in _groups(meta):
        for i in range(int(group.num_columns)):
            column = group.column(i)
            if str(column.path_in_schema) != bindings.text_column:
                continue
            text_chunks += 1
            chunk = _chunk(column)
            if chunk["end_exclusive"] > footer_start:
                raise LayoutError("a text chunk reaches into the footer")
            if (chunk["start"], chunk["end_exclusive"]) == (bindings.span_start, bindings.span_end):
                matches.append({**chunk, "row_group": index, "first_row": first, "num_rows": rows})
    if len(matches) != 1:
        raise LayoutError(f"{len(matches)} text chunks match the frozen span (need exactly 1)")
    if matches[0]["compressed_bytes"] != bindings.data_payload_bytes:
        raise LayoutError("text chunk compressed bytes differ from the frozen binding")
    return {
        "num_rows": int(meta.num_rows),
        "num_row_groups": int(meta.num_row_groups),
        "text_chunks": text_chunks,
        "footer_length": len(footer),
        "footer_range": [footer_start, footer_start + len(footer) - 1],
        "selected_text_chunk": matches[0],
        "frozen_data_range_count": bindings.data_range_count,
    }
