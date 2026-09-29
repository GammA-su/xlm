"""Phase-D decoding: exact selected rows from retained ranges plus Phase-P footers.

Inputs are only bytes the engine has already identity-verified and retained:
the Phase-D range payloads and the hash-verified Phase-P footer/trailer
payloads. A :class:`SparseSource` presents them as a virtual file of the
frozen remote length; any read outside an acquired segment fails, so decoding
cannot silently depend on bytes that were not acquired. PyArrow opens it with
the pre-parsed footer metadata (no footer read) and ``pre_buffer=False`` (no
read coalescing across gaps). Column statistics are never requested.

M: decode only the projected columns of the frozen window's row group and
retain exactly the frozen window rows. T: decode the ``text`` chunk of the
frozen row group in 256-row batches up to the batch holding the greatest
selected row, touch only selected row values, and classify each selected
locator with the frozen v2.0 retention rule (:func:`sparse.classify_retained`).
Unselected values are never converted, retained, logged or returned; error
messages carry exception type names only, never data.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from xlm.data.evidence_v2 import canonical, sparse
from xlm.data.evidence_v4 import frozen, layout
from xlm.data.evidence_v4 import phase_d_plan as pd

LOCATOR_FIELD = "_xlm_acquisition"
T_TERMINAL = (
    "full_text_available",
    "unreviewable_full_document_due_to_size",
    "unreviewable_missing_or_invalid_text",
)


class DecodeError(ValueError):
    """Decode or exact-identity failure: the arm is INCOMPLETE; nothing is refilled."""


class SparseReadError(OSError):
    """A decoder read outside the acquired byte segments."""


class SparseSource(io.RawIOBase):
    """A read-only virtual file of ``size`` bytes holding only acquired segments."""

    def __init__(self, size: int, segments: Sequence[tuple[int, bytes]]) -> None:
        super().__init__()
        self._size = size
        self._segments = tuple(segments)
        self._pos = 0
        self.reads: list[tuple[int, int]] = []
        self.refused = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        if base + offset < 0:
            raise SparseReadError("negative seek")
        self._pos = base + offset
        return self._pos

    def read(self, size: int | None = -1) -> bytes:
        amount = self._size - self._pos if size is None or size < 0 else size
        start, end = self._pos, self._pos + amount
        for offset, data in self._segments:
            if offset <= start and end <= offset + len(data):
                self.reads.append((start, amount))
                self._pos = end
                return data[start - offset : end - offset]
        self.refused += 1
        raise SparseReadError("decoder read outside the acquired byte ranges")

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _metadata(footer: bytes, trailer: bytes) -> Any:
    try:
        return layout._metadata(footer, trailer)
    except layout.LayoutError as exc:
        raise DecodeError(f"Phase-P footer does not parse: {exc}") from exc


def _row_group_bounds(meta: Any, index: int) -> tuple[int, int]:
    first = 0
    for i in range(int(meta.num_row_groups)):
        rows = int(meta.row_group(i).num_rows)
        if i == index:
            return first, rows
        first += rows
    raise DecodeError(f"row group {index} is not in the footer")


def _leaf(column: Any) -> pd.Chunk:
    start = int(column.data_page_offset)
    if column.dictionary_page_offset is not None and int(column.dictionary_page_offset) > 0:
        start = min(start, int(column.dictionary_page_offset))
    size = int(column.total_compressed_size)
    return pd.Chunk(str(column.path_in_schema), start, start + size, size)


def _open(source: SparseSource, meta: Any) -> Any:
    import pyarrow as pa
    import pyarrow.parquet as pq

    return pq.ParquetFile(
        pa.PythonFile(source, mode="r"),
        metadata=meta,
        pre_buffer=False,
        thrift_string_size_limit=frozen.THRIFT_LIMIT_BYTES,
        thrift_container_size_limit=frozen.THRIFT_LIMIT_BYTES,
    )


def _failure(what: str, exc: BaseException, source: SparseSource) -> DecodeError:
    if source.refused:
        return DecodeError(f"{what}: decoding required bytes outside the acquired ranges")
    return DecodeError(f"{what}: decoding failed ({type(exc).__name__})")


# -- M ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MResult:
    ordinal: int
    rows: tuple[int, ...]  # absolute source rows, in output order
    lines: tuple[bytes, ...]  # one canonical JSON record + "\n" per row
    decoded_rows: int  # rows of the row group decoded (mechanically necessary)
    reads: int


def decode_m_file(
    f: pd.MFile, payload: bytes, footer_body: bytes, source: frozen.Source
) -> MResult:
    """Exactly the frozen window rows of the projected columns of one M file."""
    what = f"M-{f.ordinal:02d}"
    if len(payload) != f.range_half_open[1] - f.range_half_open[0]:
        raise DecodeError(f"{what}: retained range payload has the wrong length")
    meta = _metadata(footer_body[:-8], footer_body[-8:])
    first, rows = _row_group_bounds(meta, f.row_group)
    if (first, rows) != (f.row_group_first_row, f.row_group_rows):
        raise DecodeError(f"{what}: footer row group differs from the frozen row group")
    group = meta.row_group(f.row_group)
    leaves = sorted(
        (
            _leaf(group.column(i))
            for i in range(int(group.num_columns))
            if str(group.column(i).path_in_schema).split(".")[0] in frozen.PROJECTION
        ),
        key=lambda c: (c.start, c.path),
    )
    if tuple(leaves) != f.chunks:
        raise DecodeError(f"{what}: footer projected chunks differ from the frozen chunks")
    src = SparseSource(f.remote_length, [(f.range_half_open[0], payload)])
    try:
        table = _open(src, meta).read_row_group(
            f.row_group, columns=list(frozen.PROJECTION), use_threads=False
        )
    except Exception as exc:  # pyarrow raises several unrelated exception types
        raise _failure(what, exc, src) from exc
    if table.num_rows != rows or table.column_names != list(frozen.PROJECTION):
        raise DecodeError(f"{what}: decoded row group has the wrong rows or columns")
    low, high = f.window
    try:
        records = table.slice(low - first, high - low).to_pylist()
    except Exception as exc:
        raise _failure(what, exc, src) from exc
    if len(records) != high - low:
        raise DecodeError(f"{what}: decoded window has the wrong number of rows")
    lines: list[bytes] = []
    for offset, record in enumerate(records):
        row = low + offset
        locator = {
            "repository": source.repository,
            "revision": source.revision,
            "source_file": f.file,
            "row": row,
            "row_group": f.row_group,
            "row_in_group": row - first,
            "m_ordinal": f.ordinal,
        }
        if set(record) != set(frozen.PROJECTION):
            raise DecodeError(f"{what}: decoded record fields differ from the projection")
        try:
            line = canonical.canonical_bytes({LOCATOR_FIELD: locator, **record}) + b"\n"
        except canonical.CanonicalError as exc:
            raise DecodeError(f"{what}: row {row} is not canonical JSON") from exc
        if len(line) > pd.M_RECORD_BYTES_MAX:
            raise DecodeError(f"{what}: row {row} exceeds {pd.M_RECORD_BYTES_MAX} bytes")
        lines.append(line)
    return MResult(f.ordinal, tuple(range(low, high)), tuple(lines), rows, len(src.reads))


def assemble_m(files: Sequence[pd.MFile], results: Sequence[MResult]) -> bytes:
    """The M bundle: every frozen window row exactly once, in file then row order."""
    if [r.ordinal for r in results] != [f.ordinal for f in files]:
        raise DecodeError("M results must be exactly one per frozen M file, in order")
    for f, r in zip(files, results, strict=True):
        if r.rows != tuple(range(*f.window)) or len(r.lines) != len(r.rows):
            raise DecodeError(f"M-{f.ordinal:02d}: duplicate, missing or reordered window rows")
    bundle = b"".join(line for r in results for line in r.lines)
    if len(bundle) > pd.M_OUTPUT_BYTES_MAX:
        raise DecodeError(f"M bundle exceeds {pd.M_OUTPUT_BYTES_MAX} bytes")
    return bundle


# -- T ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TDocument:
    locator: pd.Locator
    t_ordinal: int
    row_group: int
    status: str
    utf8_bytes: int | None
    sha256: str | None
    text: str | None  # only for full_text_available

    def record(self) -> dict[str, Any]:
        return {
            "locator": list(self.locator.identity),
            "t_ordinal": self.t_ordinal,
            "row_group": self.row_group,
            "row_in_group": self.locator.row_in_group,
            "status": self.status,
            "utf8_bytes": self.utf8_bytes,
            "sha256": self.sha256,
            "text": self.text,
        }


@dataclass(frozen=True)
class TResult:
    ordinal: int
    documents: tuple[TDocument, ...]
    decoded_rows: int  # rows decoded up to the batch holding the greatest selected row
    reads: int


def classify(raw: bytes | None, locator: pd.Locator, t_ordinal: int, row_group: int) -> TDocument:
    """The frozen v2.0 status of one selected value; invalid UTF-8 is never coerced."""
    text: str | None
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else None
    except UnicodeDecodeError:
        text = None
    status = sparse.classify_retained(text, locator=list(locator.identity))
    kind = status["status"]
    if kind not in T_TERMINAL:
        raise DecodeError(f"unexpected retention status {kind!r}")
    return TDocument(
        locator,
        t_ordinal,
        row_group,
        kind,
        status.get("utf8_bytes"),
        status.get("sha256"),
        text if kind == "full_text_available" else None,
    )


def _as_binary(column: Any) -> Any:
    import pyarrow as pa

    if pa.types.is_string(column.type):
        return column.view(pa.binary())
    if pa.types.is_large_string(column.type):
        return column.view(pa.large_binary())
    if pa.types.is_binary(column.type) or pa.types.is_large_binary(column.type):
        return column
    raise DecodeError("the text column is not a string/binary column")


def decode_t_file(f: pd.TFile, chunk: bytes, footer: bytes, trailer: bytes) -> TResult:
    """Only the frozen selected rows of one T file's dictionary-inclusive text chunk."""
    what = f"T-{f.ordinal:02d}"
    if len(chunk) != f.span[1] - f.span[0]:
        raise DecodeError(f"{what}: retained chunk has the wrong length")
    meta = _metadata(footer, trailer)
    first, rows = _row_group_bounds(meta, f.row_group)
    if (first, rows) != (f.row_group_first_row, f.row_group_rows):
        raise DecodeError(f"{what}: footer row group differs from the frozen row group")
    group = meta.row_group(f.row_group)
    text = [
        group.column(i)
        for i in range(int(group.num_columns))
        if str(group.column(i).path_in_schema) == "text"
    ]
    if len(text) != 1:
        raise DecodeError(f"{what}: the row group must hold exactly one text chunk")
    leaf = _leaf(text[0])
    offsets = (int(text[0].dictionary_page_offset or 0), int(text[0].data_page_offset))
    if (leaf.start, leaf.end_exclusive) != f.span or offsets != (
        f.dictionary_page_offset,
        f.data_page_offset,
    ):
        raise DecodeError(f"{what}: footer text chunk differs from the frozen chunk")
    wanted = {loc.row_in_group: loc for loc in f.locators}
    greatest = max(wanted)
    found: dict[int, bytes | None] = {}
    src = SparseSource(f.remote_length, [(f.span[0], chunk)])
    cursor = 0
    try:
        batches = _open(src, meta).iter_batches(
            batch_size=pd.DECODE_BATCH_ROWS,
            row_groups=[f.row_group],
            columns=["text"],
            use_threads=False,
        )
        for batch in batches:
            column = _as_binary(batch.column(0))
            size = len(column)
            for row in [r for r in wanted if cursor <= r < cursor + size]:
                found[row] = column[row - cursor].as_py()
            cursor += size
            if cursor > greatest:
                break
    except DecodeError:
        raise
    except Exception as exc:  # pyarrow raises several unrelated exception types
        raise _failure(what, exc, src) from exc
    if set(found) != set(wanted) or cursor > rows:
        raise DecodeError(f"{what}: selected rows are missing from the decoded row group")
    documents = tuple(
        classify(found[loc.row_in_group], loc, f.ordinal, f.row_group) for loc in f.locators
    )
    return TResult(f.ordinal, documents, cursor, len(src.reads))


def assemble_t(files: Sequence[pd.TFile], results: Sequence[TResult]) -> tuple[bytes, int, int]:
    """The sealed T documents: every frozen locator exactly once, within the text limits.

    Returns (JSONL bytes, retained text bytes, retained unique text bytes).
    """
    if [r.ordinal for r in results] != [f.ordinal for f in files]:
        raise DecodeError("T results must be exactly one per frozen T file, in order")
    seen: set[tuple[str, str, str, int]] = set()
    for f, r in zip(files, results, strict=True):
        got = [d.locator for d in r.documents]
        if got != list(f.locators):
            raise DecodeError(f"T-{f.ordinal:02d}: duplicate, missing or foreign locator")
        for d in r.documents:
            if d.locator.identity in seen:
                raise DecodeError(f"duplicate selected locator {list(d.locator.identity)}")
            seen.add(d.locator.identity)
            if d.status not in T_TERMINAL:
                raise DecodeError(f"{list(d.locator.identity)}: no terminal status")
            if d.status == "full_text_available" and (
                d.text is None
                or d.utf8_bytes is None
                or d.utf8_bytes > pd.T_DOCUMENT_BYTES_MAX
                or d.sha256 != hashlib.sha256(d.text.encode("utf-8")).hexdigest()
            ):
                raise DecodeError(f"{list(d.locator.identity)}: retained text violates the rule")
            if d.status != "full_text_available" and d.text is not None:
                raise DecodeError(f"{list(d.locator.identity)}: text retained for a non-full row")
    documents = [d for r in results for d in r.documents]
    items = [
        {"status": d.status, "sha256": d.sha256, "utf8_bytes": d.utf8_bytes} for d in documents
    ]
    try:
        unique = sparse.check_retained_budget(items)
    except sparse.SparseError as exc:
        raise DecodeError(str(exc)) from exc
    total = sum(d.utf8_bytes or 0 for d in documents if d.status == "full_text_available")
    if total > pd.T_RETAINED_TEXT_BYTES_MAX:
        raise DecodeError(f"retained text exceeds {pd.T_RETAINED_TEXT_BYTES_MAX} bytes")
    bundle = b"".join(canonical.canonical_bytes(d.record()) + b"\n" for d in documents)
    if len(bundle) > pd.T_OUTPUT_BYTES_MAX:
        raise DecodeError(f"T documents exceed {pd.T_OUTPUT_BYTES_MAX} bytes")
    return bundle, total, unique
