"""C07 ``tokenize-selection``, FAST: parallel exact tokenization of the signed selection.

Scientifically identical to :func:`freeze.tokenize_selection` (the reference, kept as
``tokenize-selection-reference``): the same documents in the same order, the same
token IDs, the same truncation of the allocation-crossing records, the same records
and counters. With ``index_schema="c07-offsets-v1"`` every published byte equals the
reference's (tokens, index, counters, manifest and C05 attestation). The default
``c07-offsets-v2`` writes byte-identical ``tokens.bin`` and index records without the
per-token ``token_byte_spans`` (see :mod:`xlm.data.tokens`); the spans are re-derived
exactly on read from a SHA-bound per-ID byte table.

What is verified, exactly as the reference does (see :class:`selection.SelectionGate`
and :class:`gates.MembershipGate`), but without SQLite:

1. **C05 membership** is streamed once and authenticated (SHA-256, size, row count,
   strict schema, ascending unique doc ids, frozen plan file/row and allocation), then
   reconciled against the signed completion (:mod:`fitfast`).
2. **Selection**: the signed envelope (issuer, C05 binding, count rule, EXACT
   allocations, membership bytes) and one hashed strict pass over ``selected.jsonl``.
   Every row must be a kept *train* membership row with the same content digest and
   allocation; ``0 < selected <= counted``; per-allocation documents, targets and
   truncations and per-component quotas must equal the signed report.
3. **Tokenizer**: the artifact is snapshotted once; its identity must equal the one the
   exact counts (and so the selection) were made with. Workers load a private copy and
   must reproduce the fingerprint and the byte table.
4. **Source**: every plan file of every produced component is hashed completely, once
   (size, SHA-256 and row count equal the plan). Each selected row is located at its
   C05 membership position, then strict-parsed, built as a ``CanonicalDocument`` and
   must carry the membership doc id, the exact C05 content digest and ``split=train``.
   Re-reads of located rows refuse unless the file's size and mtime are unchanged.
5. **Exact count**: every selected document is re-tokenized in full and
   ``len(ids) - 1`` must equal its counted valid targets; the stored prefix is exactly
   ``selected + 1`` tokens. Token byte lengths must sum to the canonical text length
   (the identity that makes v2 spans exact).

Order: components sorted; within a component plan-file order, then row order (the
reference's ``iter_plan_documents`` order). Worker count and scheduling never change a
byte: results are written strictly in that order.

Publication: each component is staged in a hidden ``.<component>.stage-<uuid>``
directory beside its final path (payloads fsynced, manifest last) and published by one
directory rename, so ``<root>/<component>`` exists only complete. ``resume`` verifies
each published component completely (bytes, attestation, selection and tokenizer
binding, every index record) and skips it; stale stage directories of this tool are
removed, anything else refuses. Interruption or failure reaps every worker and removes
exactly what this run created.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from array import array
from collections.abc import Iterator, Mapping
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import psutil
from filelock import FileLock, Timeout

from xlm.core.contracts import CanonicalDocument, TokenShardManifest
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.countfast import (
    TokenizerSnapshot,
    _directory_digests,
    count_tables,
    snapshot_tokenizer,
)
from xlm.data.exclusion.fitfast import (
    Membership,
    OwnedPaths,
    StreamedC05,
    open_streamed,
    reconcile,
    stream_membership,
)
from xlm.data.exclusion.fitscan import (
    SPLIT_CODES,
    MembershipTables,
    OrderedPool,
    _cancel_check,
    init_worker,
    walk_rows,
)
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import GiB, MiB, NullProgress, RunProgress
from xlm.data.exclusion.selection import COUNT_RULE, allocation_key, check_binding
from xlm.data.exclusion.supervisor import Deadline, Supervisor
from xlm.data.exclusion.tokenizer_fit import _Reporter as FitReporter
from xlm.data.tokens import (
    INDEX_SCHEMA_V1,
    INDEX_SCHEMA_V2,
    INDEX_SCHEMAS,
    TOKEN_BYTES_FILE,
    TokenShardReader,
    _write_synced,
    token_byte_lengths,
)

if TYPE_CHECKING:
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

LABEL = "TOKENIZE"
WORKER_CHOICES = (1, 2, 4, 8, 16)
SHARD_KIND = "c05_token_shard_v2"  # gates.MembershipGate._shard_binding
SELECTION_KIND = "c05_selected_pool_v1"
SELECTED_KEYS = frozenset(
    {"doc_id", "content", "allocation", "counted_valid_targets", "selected_valid_targets"}
)
SELECTED_LINE_CEILING = 64 * 1024  # SelectionGate's record ceiling
SELECTION_BLOCK_BYTES = 8 * MiB
#: Selected line bytes handed to one tokenization task (bounds result memory).
CHUNK_TEXT_BYTES = 4 * MiB
#: Text handed to one native batch call; documents per call.
BATCH_TEXT_BYTES = 1 * MiB
BATCH_DOCUMENTS = 64
#: Located rows closer than this are re-read with one read call.
READ_GAP_BYTES = 256 * 1024
#: Source bytes hashed but not yet fully tokenized (keeps re-reads in the page cache).
LOOKAHEAD_BYTES = 8 * GiB
#: BPE word-cache capacity (speed only; the serialized tokenizer stays byte-identical).
TOKENIZE_BPE_CACHE = 100_000
V1_NAMES = (
    "tokens.bin",
    "offsets.jsonl",
    "shard_counters.json",
    "shard_manifest.json",
    "c05-attestation.json",
)
V2_NAMES = (*V1_NAMES, TOKEN_BYTES_FILE)
STAGE_MARK = ".stage-"
LOCK_NAME = ".tokenize-selection.tokenize.lock"
#: Conservative per-document v2 index line bound beyond its strings (fixed keys, the
#: three 64-hex digests, numbers and positions).
INDEX_FIXED_BYTES = 640
#: Free bytes that must remain on the output and scratch volumes (also watched live).
DEFAULT_OUTPUT_RESERVE = 32 * GiB
DEFAULT_SCRATCH_RESERVE = 4 * GiB


# -- worker side ------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenizeTables:
    """Sent once to every worker."""

    membership: MembershipTables
    tokenizer: str  # private snapshot copy
    fingerprint: str
    token_bytes_sha256: str
    receipt: str  # C05 completion digest (``c05_receipt`` of every record)
    selection: str  # selection digest (``c05_selection`` of every record)
    schema: str
    dtype: str


_STATE: dict[str, Any] = {}


def init_tokenize_worker(tables: TokenizeTables) -> None:
    """Single-threaded native tokenizer bound to its snapshot fingerprint and byte table."""
    init_worker(tables.membership)  # Also pins TOKENIZERS_PARALLELISM/RAYON to one thread.
    _STATE.clear()
    from xlm.data.exclusion.selection import load_tokenizer
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer, with_bpe_cache

    try:
        tokenizer = load_tokenizer(Path(tables.tokenizer))
    except (OSError, ValueError, KeyError, TypeError):
        _STATE["error"] = "tokenize tokenizer snapshot cannot be loaded"
        return
    if not isinstance(tokenizer, ByteLevelBPETokenizer) or tokenizer.fingerprint != (
        tables.fingerprint
    ):
        _STATE["error"] = "tokenize tokenizer differs from its verified identity"
        return
    table = token_byte_lengths(tokenizer)
    if hashlib.sha256(table).hexdigest() != tables.token_bytes_sha256:
        _STATE["error"] = "tokenize tokenizer byte table differs from the parent's"
        return
    try:
        with_bpe_cache(tokenizer, TOKENIZE_BPE_CACHE)
    except ValueError:
        _STATE["error"] = "tokenize tokenizer cannot take a larger BPE cache unchanged"
        return
    _STATE.update(
        tokenizer=tokenizer,
        tables=tables,
        lengths=np.frombuffer(table, dtype="<u2").astype(np.int64),
    )


def _worker() -> tuple[ByteLevelBPETokenizer, TokenizeTables, npt.NDArray[np.int64]]:
    if "tokenizer" not in _STATE:
        raise C05Error(_STATE.get("error", "tokenize worker was not initialized"))
    return _STATE["tokenizer"], _STATE["tables"], _STATE["lengths"]


@dataclass(frozen=True)
class ScanTask:
    """Hash one plan file completely and locate its selected rows (ascending)."""

    ordinal: int
    path: str
    sha256: str
    file_bytes: int
    documents: int
    line_ceiling: int
    rows: npt.NDArray[np.uint32]
    block_bytes: int = 0


@dataclass
class ScanResult:
    ordinal: int
    offsets: npt.NDArray[np.uint64]
    lengths: npt.NDArray[np.uint32]
    mtime_ns: int


def scan_file(task: ScanTask) -> ScanResult:
    """Every byte hashed once (size, SHA-256, row count); selected rows located."""
    path = Path(task.path)
    before = path.stat()
    count = len(task.rows)
    offsets = np.zeros(count, dtype=np.uint64)
    lengths = np.zeros(count, dtype=np.uint32)
    for position, line, offset in walk_rows(
        path,
        task.sha256,
        task.file_bytes,
        task.documents,
        task.line_ceiling,
        task.rows.tolist(),
        task.block_bytes,
    ):
        offsets[position] = offset
        lengths[position] = len(line)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise C05Error("input file changed while it was verified")
    return ScanResult(task.ordinal, offsets, lengths, after.st_mtime_ns)


@dataclass(frozen=True)
class TokenTask:
    """Re-read located selected rows of one verified file; tokenize them exactly."""

    ordinal: int
    first: int  # index of the first row among the file's selected rows
    component: str
    path: str
    file_bytes: int
    mtime_ns: int
    offsets: npt.NDArray[np.uint64]
    lengths: npt.NDArray[np.uint32]
    ids: list[bytes]
    content: bytes  # 32 bytes per row: the C05 content digest
    counted: npt.NDArray[np.int64]
    chosen: npt.NDArray[np.int64]

    def __len__(self) -> int:
        return len(self.ids)


@dataclass
class TokenResult:
    ordinal: int
    first: int
    tokens: bytes  # emitted IDs of every row, concatenated, little-endian
    token_counts: npt.NDArray[np.int64]
    byte_counts: npt.NDArray[np.int64]
    covered: npt.NDArray[np.int64]
    eos: npt.NDArray[np.int64]
    heads: list[bytes]
    mids: list[bytes]
    tails: list[bytes]
    text_bytes: int

    def __len__(self) -> int:
        return len(self.heads)


def _dumps(value: Any) -> str:
    """``json.dumps(value, ensure_ascii=False)``; strings take the same C escaper directly."""
    if type(value) is str:
        return _encode_string(value)
    return json.dumps(value, ensure_ascii=False)


_encode_string = json.encoder.encode_basestring  # json.dumps' ensure_ascii=False escaper


def index_line_parts(
    doc: CanonicalDocument,
    component: str,
    token_count: int,
    covered: int,
    spans: list[list[int]] | None,
    bos_positions: list[int],
    eos_positions: list[int],
    receipt: str,
    selection: str,
    content: str,
    counted: int,
    chosen: int,
) -> tuple[bytes, bytes, bytes]:
    """The reference index line minus its three shard-positional numbers.

    ``head + str(token_start) + mid + str(byte_start) + ', "byte_end": ' +
    str(byte_end) + tail`` is exactly ``json.dumps(record, ensure_ascii=False) + "\\n"``
    of :meth:`TokenShardWriter._write_documents` with a gate and a selection, in its key
    order (``source_id`` keeps its first position and takes the component value).
    ``spans`` is None for the v2 schema, which omits only ``token_byte_spans``.
    """
    head = f'{{"doc_id": {_dumps(doc.doc_id)}, "source_id": {_dumps(component)}, "token_start": '
    mid = (
        f', "token_count": {token_count}, "byte_count": {_dumps(doc.utf8_byte_count)}'
        f', "covered_bytes": {covered}'
        + ("" if spans is None else f', "token_byte_spans": {_dumps(spans)}')
        + f', "lineage_id": {_dumps(doc.cluster_ids.get("duplicate_cluster", ""))}'
        f', "split_group": {_dumps(doc.cluster_ids.get("split_group", ""))}, "byte_start": '
    )
    tail = (
        f', "valid_targets": {max(0, token_count - 1)}'
        f', "bos_positions": {_dumps(bos_positions)}, "eos_positions": {_dumps(eos_positions)}'
        f', "split": {_dumps(doc.split)}, "c05_content": {_dumps(content)}'
        f', "c05_receipt": {_dumps(receipt)}'
        f', "c05_canonical_source_id": {_dumps(doc.source_id)}'
        f', "c05_selection": {_dumps(selection)}'
        f', "c05_counted_valid_targets": {counted}, "c05_selected_valid_targets": {chosen}}}\n'
    )
    return head.encode("utf-8"), mid.encode("utf-8"), tail.encode("utf-8")


def assemble_line(
    head: bytes, mid: bytes, tail: bytes, token_start: int, byte_start: int, byte_end: int
) -> bytes:
    return b'%s%d%s%d, "byte_end": %d%s' % (head, token_start, mid, byte_start, byte_end, tail)


def _verified_document(line: bytes, doc_id: bytes, content: bytes) -> tuple[CanonicalDocument, str]:
    """The reference's per-record checks; returns the document and its content digest.

    Strict canonical JSON, a valid ``CanonicalDocument``, the membership doc id at this
    location, the exact C05 content digest (``digest(doc.to_dict())``) and
    ``split=train`` (``MembershipGate.verify``).
    """
    try:
        value = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise C05Error("canonical record is not strict canonical JSON") from exc
    if type(value) is not dict:
        raise C05Error("canonical record must be an object")
    try:
        document = CanonicalDocument(**value)
    except (TypeError, ValueError) as exc:
        raise C05Error("canonical record is invalid") from exc
    if type(document.doc_id) is not str or document.doc_id.encode("utf-8") != doc_id:
        raise C05Error("source row differs from its C05 membership location")
    # ``digest(document.to_dict())`` without the deep copy: the constructor accepted
    # exactly the dataclass fields, so ``asdict`` would rebuild this same JSON value and
    # the canonical serialization (sorted keys) is byte-identical.
    raw = hashlib.sha256(canonical.canonical_bytes(value)).digest()
    if raw != content:
        raise C05Error("document is not exact screened training membership")
    if document.split != "train" or type(document.text) is not str:
        raise C05Error("document is not exact screened training membership")
    if type(document.cluster_ids) is not dict:
        raise C05Error("canonical record cluster ids are invalid")
    return document, raw.hex()


def _task_lines(task: TokenTask) -> Iterator[tuple[int, bytes]]:
    """Located lines of an unchanged file, coalescing nearby rows into one read."""
    path = Path(task.path)
    offsets, lengths = task.offsets.tolist(), task.lengths.tolist()
    count = len(offsets)
    with path.open("rb", buffering=0) as stream:
        start = 0
        while start < count:
            end = start + 1
            while (
                end < count
                and offsets[end] - (offsets[end - 1] + lengths[end - 1]) <= READ_GAP_BYTES
                and offsets[end] + lengths[end] - offsets[start] <= 4 * CHUNK_TEXT_BYTES
            ):
                end += 1
            base = offsets[start]
            stop = offsets[end - 1] + lengths[end - 1]
            if stop > task.file_bytes:
                raise C05Error("tokenize chunk lies outside its file")
            stream.seek(base)
            data = stream.read(stop - base)
            if len(data) != stop - base:
                raise C05Error("input file changed after it was verified")
            for position in range(start, end):
                at = offsets[position] - base
                line = data[at : at + lengths[position]]
                final = offsets[position] + lengths[position] == task.file_bytes
                if (not line.endswith(b"\n") and not final) or b"\n" in line[:-1]:
                    raise C05Error("tokenize chunk line boundary differs from the verified pass")
                yield position, line
            start = end


def tokenize_chunk(task: TokenTask) -> TokenResult:
    """Verify, tokenize, re-prove the exact count and truncate one run of selected rows."""
    from xlm.data.normalization import canonical_normalize

    tokenizer, tables, lengths = _worker()
    path = Path(task.path)

    def unchanged() -> None:
        now = path.stat()
        if (now.st_size, now.st_mtime_ns) != (task.file_bytes, task.mtime_ns):
            raise C05Error("input file changed after it was verified")

    unchanged()
    count = len(task)
    if (
        count == 0
        or len(task.offsets) != count
        or len(task.lengths) != count
        or len(task.content) != 32 * count
        or len(task.counted) != count
        or len(task.chosen) != count
    ):
        raise C05Error("tokenize chunk is inconsistent")
    backend = tokenizer._tok
    bos, eos = tokenizer.bos_token_id, tokenizer.eos_token_id
    # Native ``array`` items viewed as explicit little-endian NumPy dtypes: refuse on a
    # big-endian host or an unexpected item size rather than write other bytes.
    code = "H" if tables.dtype == "uint16" else "I"
    if sys.byteorder != "little" or array(code).itemsize != (2 if code == "H" else 4):
        raise C05Error("tokenize host cannot write little-endian token IDs natively")
    dtype = "<u2" if tables.dtype == "uint16" else "<u4"
    limit = 65535 if tables.dtype == "uint16" else 4294967295
    v1 = tables.schema == INDEX_SCHEMA_V1
    counted, chosen = task.counted.tolist(), task.chosen.tolist()
    result = TokenResult(
        task.ordinal,
        task.first,
        b"",
        np.zeros(count, dtype=np.int64),
        np.zeros(count, dtype=np.int64),
        np.zeros(count, dtype=np.int64),
        np.zeros(count, dtype=np.int64),
        [],
        [],
        [],
        0,
    )
    blocks: list[bytes] = []
    pending: list[tuple[int, CanonicalDocument, str, str]] = []
    pending_bytes = 0

    def flush() -> None:
        nonlocal pending_bytes
        if not pending:
            return
        _cancel_check()
        encodings = backend.encode_batch_fast([p[3] for p in pending], add_special_tokens=False)
        if len(encodings) != len(pending):
            raise C05Error("tokenizer returned a different number of encodings")
        for (position, doc, content, clean), encoding in zip(pending, encodings, strict=True):
            ids = encoding.ids
            # ByteLevelBPETokenizer._offsets_from_encoding framing (BOS/EOS once).
            packed = array(code)
            if not ids or ids[0] != bos:
                packed.append(bos)
            try:
                packed.extend(ids)  # Range-checked: a negative or wide ID refuses.
            except OverflowError as exc:
                raise ValueError(f"Token ID cannot fit in declared dtype {tables.dtype}") from exc
            if not ids or ids[-1] != eos:
                packed.append(eos)
            if max(0, len(packed) - 1) != counted[position]:
                raise C05Error("exact token count drifted from the bound count artifact")
            if not 0 < chosen[position] <= counted[position]:
                raise C05Error("selected valid targets outside the exact count")
            full = np.frombuffer(packed, dtype=dtype)
            if int(full.max()) > limit or int(full.max()) >= len(lengths):
                raise C05Error("token ID outside the tokenizer byte table")
            sizes = lengths[full]
            if int(sizes.sum()) != len(clean.encode("utf-8")):
                raise C05Error("token byte lengths do not reproduce the canonical text")
            emitted = full[: chosen[position] + 1]
            sizes = sizes[: len(emitted)]
            spans: list[list[int]] | None = None
            if v1:
                ends = np.cumsum(sizes)
                spans = np.column_stack((ends - sizes, ends)).tolist()
            bos_positions = np.flatnonzero(emitted == bos).tolist()
            eos_positions = np.flatnonzero(emitted == eos).tolist()
            covered = int(sizes.sum())
            parts = index_line_parts(
                doc,
                task.component,
                len(emitted),
                covered,
                spans,
                bos_positions,
                eos_positions,
                tables.receipt,
                tables.selection,
                content,
                counted[position],
                chosen[position],
            )
            result.heads.append(parts[0])
            result.mids.append(parts[1])
            result.tails.append(parts[2])
            result.token_counts[position] = len(emitted)
            result.byte_counts[position] = doc.utf8_byte_count
            result.covered[position] = covered
            result.eos[position] = len(eos_positions)
            result.text_bytes += doc.utf8_byte_count
            blocks.append(emitted.tobytes())
        pending.clear()
        pending_bytes = 0

    expected = 0
    for position, line in _task_lines(task):
        if position != expected:
            raise C05Error("tokenize chunk rows out of order")
        expected += 1
        doc, content = _verified_document(
            line, task.ids[position], task.content[32 * position : 32 * position + 32]
        )
        clean = canonical_normalize(doc.text)
        pending.append((position, doc, content, clean))
        pending_bytes += len(clean)
        if len(pending) >= BATCH_DOCUMENTS or pending_bytes >= BATCH_TEXT_BYTES:
            flush()
    flush()
    if expected != count or len(result) != count:
        raise C05Error("tokenize chunk did not cover its rows")
    unchanged()
    result.tokens = b"".join(blocks)
    return result


def run_task(task: ScanTask | TokenTask) -> ScanResult | TokenResult:
    return tokenize_chunk(task) if isinstance(task, TokenTask) else scan_file(task)


# -- parent: selection --------------------------------------------------------------------


@dataclass
class Selected:
    """Authenticated selected rows (doc-id order) joined to their membership positions."""

    rows: int
    position: npt.NDArray[np.int64]  # membership position of each selected row
    counted: npt.NDArray[np.int64]
    chosen: npt.NDArray[np.int64]
    component: list[str]  # sorted component names
    component_of: npt.NDArray[np.uint16]  # index into ``component``


@dataclass
class SelectedChunk:
    rows: int
    ids: list[bytes]
    content: bytes
    allocation: npt.NDArray[np.uint16]
    counted: npt.NDArray[np.int64]
    chosen: npt.NDArray[np.int64]


def parse_selected_chunk(chunk: bytes) -> SelectedChunk:
    """Strict parse of complete ``selected.jsonl`` lines (SelectionGate's row checks)."""
    from xlm.data.exclusion.fitscan import _TABLES

    tables = _TABLES
    if tables is None:
        raise C05Error("selection worker tables missing")
    position = {key: n for n, key in enumerate(tables.allocation_keys)}
    ids: list[bytes] = []
    contents: list[bytes] = []
    allocations: list[int] = []
    counted: list[int] = []
    chosen: list[int] = []
    start, end_of_chunk = 0, len(chunk)
    while start < end_of_chunk:
        end = chunk.find(b"\n", start)
        stop = end_of_chunk if end < 0 else end + 1
        if stop - start > SELECTED_LINE_CEILING:
            raise C05Error("selected record ceiling")
        try:
            row = canonical.loads_bytes_strict(chunk[start:stop])
        except canonical.CanonicalError as exc:
            raise C05Error("selected record is not strict canonical JSON") from exc
        start = stop
        if len(ids) % 4096 == 4095:
            _cancel_check()
        if type(row) is not dict or row.keys() != SELECTED_KEYS:
            raise C05Error("selected record schema")
        doc_id, content, allocation = row["doc_id"], row["content"], row["allocation"]
        if type(doc_id) is not str or not doc_id:
            raise C05Error("selected document id type")
        if type(content) is not str or len(content) != 64:
            raise C05Error("selected content digest")
        try:
            digest = bytes.fromhex(content)
            key = allocation_key(*allocation) if type(allocation) is list else None
        except (TypeError, ValueError) as exc:
            raise C05Error("selected record value") from exc
        if key is None or key not in position or digest.hex() != content:
            raise C05Error("selected record outside the signed allocations")
        total, take = row["counted_valid_targets"], row["selected_valid_targets"]
        if type(take) is not int or type(total) is not int or not 0 < take <= total:
            raise C05Error("selected valid targets outside the exact count")
        ids.append(doc_id.encode("utf-8"))
        contents.append(digest)
        allocations.append(position[key])
        counted.append(total)
        chosen.append(take)
    return SelectedChunk(
        rows=len(ids),
        ids=ids,
        content=b"".join(contents),
        allocation=np.asarray(allocations, dtype=np.uint16),
        counted=np.asarray(counted, dtype=np.int64),
        chosen=np.asarray(chosen, dtype=np.int64),
    )


def verify_selection_envelope(directory: Path, view: StreamedC05) -> dict[str, Any]:
    """``selection.verify_selection`` minus the file hash (taken by the streamed pass)."""
    envelope = read_metadata(directory / "selection.json", digested=False)
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, SELECTION_KIND)
    if body.get("rule") != COUNT_RULE:
        raise C05Error("selection counting rule changed")
    if body["selected_valid_targets"] != sum(
        r["selected_valid_targets"] for r in body["allocations"].values()
    ) or any(r["status"] != "EXACT" for r in body["allocations"].values()):
        raise C05Error("selection is not exact")
    path = directory / "selected.jsonl"
    if path.stat().st_size != body["selected_membership_bytes"]:
        raise C05Error("selected training membership changed")
    return envelope


def _selection_blocks(path: Path, expected: int, state: dict[str, Any]) -> Iterator[bytes]:
    digest = state["sha"]
    pending = b""
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(min(SELECTION_BLOCK_BYTES, expected - state["bytes"] + 1)):
            digest.update(block)
            state["bytes"] += len(block)
            if state["bytes"] > expected:
                raise C05Error("selected training membership changed")
            data = pending + block if pending else block
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
                if len(pending) > SELECTED_LINE_CEILING:
                    raise C05Error("selected record ceiling")
                continue
            pending = data[cut:]
            yield data[:cut]
    if pending:
        yield pending


def stream_selection(
    directory: Path,
    body: Mapping[str, Any],
    view: StreamedC05,
    m: Membership,
    keys: list[str],
    pool: OrderedPool,
    progress: RunProgress | NullProgress,
) -> Selected:
    """One hashed strict pass over ``selected.jsonl``, joined to kept train membership."""
    expected_bytes = int(body["selected_membership_bytes"])
    total_rows = int(body["selected_documents"])
    progress.stage("SELECTION VERIFY", total_rows, "rows")
    state: dict[str, Any] = {"sha": hashlib.sha256(), "bytes": 0}
    positions = np.zeros(total_rows, dtype=np.int64)
    counted = np.zeros(total_rows, dtype=np.int64)
    chosen = np.zeros(total_rows, dtype=np.int64)
    allocation = np.zeros(total_rows, dtype=np.uint16)
    rows = 0
    cursor = 0  # membership position: both files ascend by doc-id bytes
    ids, offsets = m.ids, m.id_offsets.tolist()
    previous: bytes | None = None
    for part in pool.map(
        parse_selected_chunk, _selection_blocks(directory / "selected.jsonl", expected_bytes, state)
    ):
        if rows + part.rows > total_rows:
            raise C05Error("selected membership changed during import")
        for n, doc_id in enumerate(part.ids):
            if previous is not None and doc_id <= previous:
                raise C05Error("selected membership is not in ascending document id order")
            previous = doc_id
            while cursor < m.rows and ids[offsets[cursor] : offsets[cursor + 1]] < doc_id:
                cursor += 1
            if cursor >= m.rows or ids[offsets[cursor] : offsets[cursor + 1]] != doc_id:
                raise C05Error("record is not covered by C05 kept membership")
            positions[rows + n] = cursor
            cursor += 1
        block = slice(rows, rows + part.rows)
        counted[block], chosen[block], allocation[block] = (
            part.counted,
            part.chosen,
            part.allocation,
        )
        found = positions[block]
        content = np.frombuffer(part.content, dtype=np.uint8).reshape(part.rows, 32)
        if not np.array_equal(m.content[found], content):
            raise C05Error("record differs from C05 kept membership")
        if not np.array_equal(m.allocation[found], part.allocation):
            raise C05Error("record allocation differs from C05 membership")
        if np.any(m.split[found] != SPLIT_CODES["train"]):
            raise C05Error("selected record is not kept training membership")
        rows += part.rows
        progress.update(rows, bytes_done=state["bytes"], bytes_total=expected_bytes)
    if (state["sha"].hexdigest(), state["bytes"], rows) != (
        body["selected_membership_sha256"],
        expected_bytes,
        total_rows,
    ) or int(chosen.sum()) != body["selected_valid_targets"]:
        raise C05Error("selected membership changed during import")
    # Re-prove each internal allocation from the actual rows (SelectionGate.__init__).
    report: Mapping[str, Mapping[str, Any]] = body["allocations"]
    if set(report) - set(keys):
        raise C05Error("selected record outside the signed allocations")
    components: dict[str, int] = {}
    for n, key in enumerate(keys):
        mask = allocation == n
        docs = int(np.count_nonzero(mask))
        if key not in report:
            if docs:
                raise C05Error("selected record outside the signed allocations")
            continue
        tokens = int(chosen[mask].sum())
        truncated = int(np.count_nonzero(chosen[mask] < counted[mask]))
        signed_row = report[key]
        if (
            tokens != signed_row["quota"]
            or tokens != signed_row["selected_valid_targets"]
            or docs != signed_row["selected_documents"]
            or truncated != signed_row["truncated_documents"]
            or truncated > 1
        ):
            raise C05Error("selected allocation totals differ from the signed allocation")
        component = canonical.loads_strict(key)[0]
        components[component] = components.get(component, 0) + tokens
    if {c: v["quota"] for c, v in body["components"].items()} != components or sum(
        components.values()
    ) != body["valid_target_quota"]:
        raise C05Error("selected component totals differ from the signed allocations")
    names = sorted(components)
    index = {name: n for n, name in enumerate(names)}
    by_key = np.array([index.get(canonical.loads_strict(k)[0], 0) for k in keys], dtype=np.uint16)
    return Selected(rows, positions, counted, chosen, names, by_key[allocation])


# -- parent: work plan, preflight ---------------------------------------------------------


@dataclass
class FileWork:
    ordinal: int
    component: int
    selected: npt.NDArray[np.int64]  # selected-row indices, ascending source row


@dataclass
class ComponentPlan:
    name: str
    files: list[FileWork]
    documents: int
    valid_targets: int
    tokens: int
    canonical_bytes: int
    token_bytes: int  # tokens.bin size
    index_bound: int  # conservative offsets.jsonl upper bound


def work_plan(
    view: StreamedC05, m: Membership, s: Selected, schema: str, dtype_bytes: int
) -> list[ComponentPlan]:
    """Per component, plan files in plan order, rows ascending (reference order)."""
    files = m.file[s.position].astype(np.int64)
    rows = m.row[s.position].astype(np.int64)
    order = np.lexsort((rows, files))
    plans = [ComponentPlan(name, [], 0, 0, 0, 0, 0, 0) for name in s.component]
    bounds = np.searchsorted(files[order], np.arange(len(view.plan.files) + 1))
    names = {name: n for n, name in enumerate(s.component)}
    sizes = m.nbytes[s.position].astype(np.int64)
    id_bytes = np.diff(m.id_offsets.astype(np.int64))[s.position]
    for ordinal, item in enumerate(view.plan.files):
        chosen = order[bounds[ordinal] : bounds[ordinal + 1]]
        component = names.get(item.component)
        if component is None:
            if chosen.size:
                raise C05Error("selected record outside the selected components")
            continue
        if np.any(s.component_of[chosen] != component):
            raise C05Error("selected record component differs from its source file")
        plan = plans[component]
        plan.files.append(FileWork(ordinal, component, chosen))
    for n, plan in enumerate(plans):
        members = np.flatnonzero(s.component_of == n)
        plan.documents = len(members)
        plan.valid_targets = int(s.chosen[members].sum())
        plan.tokens = int((s.chosen[members] + 1).sum())
        plan.canonical_bytes = int(sizes[members].sum())
        plan.token_bytes = plan.tokens * dtype_bytes
        # Strings appear once each, JSON-escaped at most 6x (\uXXXX); the cluster ids
        # are bounded by the membership doc id length heuristic plus fixed margin.
        per_doc = INDEX_FIXED_BYTES + 6 * (3 * id_bytes[members] + 64)
        bound = int(per_doc.sum())
        if schema == INDEX_SCHEMA_V1:
            digits = np.floor(np.log10(np.maximum(sizes[members], 1))).astype(np.int64) + 1
            bound += int(((s.chosen[members] + 1) * (2 * digits + 6)).sum())
        plan.index_bound = bound
    return plans


def output_requirement(plans: list[ComponentPlan], schema: str) -> int:
    table = 2 * 65536 if schema == INDEX_SCHEMA_V2 else 0
    return sum(p.token_bytes + p.index_bound + 64 * 1024 + table for p in plans)


def resource_plan(
    plans: list[ComponentPlan],
    schema: str,
    workers: int,
    output_root: Path,
    scratch: Path,
    output_reserve: int,
    scratch_reserve: int,
    skipped: set[str],
) -> dict[str, Any]:
    pending = [p for p in plans if p.name not in skipped]
    need = output_requirement(pending, schema)
    return {
        "index_schema": schema,
        "workers": workers,
        "components": {
            p.name: {
                "documents": p.documents,
                "valid_targets": p.valid_targets,
                "token_ids": p.tokens,
                "canonical_bytes": p.canonical_bytes,
                "tokens_bin_bytes": p.token_bytes,
                "index_bytes_upper_bound": p.index_bound,
                "files": len(p.files),
                "resume_skip": p.name in skipped,
            }
            for p in plans
        },
        "documents": sum(p.documents for p in plans),
        "valid_targets": sum(p.valid_targets for p in plans),
        "token_ids": sum(p.tokens for p in plans),
        "tokens_bin_bytes": sum(p.token_bytes for p in plans),
        "output_bytes_required": need,
        "output_reserve_bytes": output_reserve,
        "output_free_bytes": shutil.disk_usage(_existing(output_root)).free,
        "scratch_reserve_bytes": scratch_reserve,
        "scratch_free_bytes": shutil.disk_usage(_existing(scratch)).free,
    }


def _existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def preflight(plan: Mapping[str, Any]) -> None:
    """Refuse before any source byte is read when the volumes are clearly too small."""
    if plan["output_free_bytes"] < plan["output_bytes_required"] + plan["output_reserve_bytes"]:
        raise C05Error("insufficient free space on the shard output volume")
    if plan["scratch_free_bytes"] < plan["scratch_reserve_bytes"]:
        raise C05Error("insufficient free space on the tokenize scratch volume")


# -- parent: shard staging and publication ------------------------------------------------


@dataclass
class ShardStage:
    """One component's hidden stage directory; published by a single rename."""

    root: Path
    plan: ComponentPlan
    schema: str
    dtype: str
    ceiling: int
    owned: OwnedPaths
    directory: Path = field(init=False)
    tokens_path: Path = field(init=False)
    index_path: Path = field(init=False)
    written: int = 0
    num_tokens: int = 0
    num_documents: int = 0
    valid_targets: int = 0
    eos_tokens: int = 0
    canonical_bytes: int = 0
    covered_bytes: int = 0

    def __post_init__(self) -> None:
        self.directory = self.owned.directory(
            self.root / f".{self.plan.name}{STAGE_MARK}{uuid.uuid4().hex}"
        )
        self.tokens_path = self.owned.file(self.directory / "tokens.bin")
        self.index_path = self.owned.file(self.directory / "offsets.jsonl")
        self.tokens = self.tokens_path.open("xb")
        self.index = self.index_path.open("xb")
        self.tokens_sha = hashlib.sha256()
        self.index_sha = hashlib.sha256()

    def charge(self, size: int) -> None:
        self.written += size
        if self.written > self.ceiling:
            raise ValueError("token shard output byte limit exceeded")

    def write(self, result: TokenResult) -> None:
        self.charge(len(result.tokens))
        self.tokens.write(result.tokens)
        self.tokens_sha.update(result.tokens)
        lines: list[bytes] = []
        token_start, byte_end = self.num_tokens, self.canonical_bytes
        counts, sizes = result.token_counts.tolist(), result.byte_counts.tolist()
        for n, (head, mid, tail) in enumerate(
            zip(result.heads, result.mids, result.tails, strict=True)
        ):
            byte_start, byte_end = byte_end, byte_end + sizes[n]
            lines.append(assemble_line(head, mid, tail, token_start, byte_start, byte_end))
            token_start += counts[n]
        raw = b"".join(lines)
        self.charge(len(raw))
        self.index.write(raw)
        self.index_sha.update(raw)
        if token_start - self.num_tokens != len(result.tokens) // (
            2 if self.dtype == "uint16" else 4
        ):
            raise C05Error("tokenize result tokens differ from its index")
        self.num_tokens = token_start
        self.canonical_bytes = byte_end
        self.num_documents += len(result)
        self.valid_targets += int(np.maximum(result.token_counts - 1, 0).sum())
        self.eos_tokens += int(result.eos.sum())
        self.covered_bytes += int(result.covered.sum())

    def finish(
        self,
        tokenizer_hash: str,
        pool_hash: str,
        table: bytes,
        seal: Any,
    ) -> Path:
        """Payloads fsynced, then table/counters/attestation, manifest last; one rename."""
        plan = self.plan
        if (
            self.num_documents,
            self.valid_targets,
            self.num_tokens,
            self.canonical_bytes,
        ) != (plan.documents, plan.valid_targets, plan.tokens, plan.canonical_bytes):
            raise C05Error("shard does not contain exactly the selected component membership")
        for output in (self.tokens, self.index):
            output.flush()
            os.fsync(output.fileno())
            output.close()
        ratio = (
            float(self.covered_bytes) / float(self.canonical_bytes)
            if self.canonical_bytes > 0
            else 1.0
        )
        manifest = TokenShardManifest(
            shard_id=plan.name,
            source_id=plan.name,
            num_tokens=self.num_tokens,
            num_documents=self.num_documents,
            token_dtype=self.dtype,
            endianness="little",
            tokenizer_hash=tokenizer_hash,
            pool_hash=pool_hash,
            checksum_sha256=self.tokens_sha.hexdigest(),
            offsets_checksum_sha256=self.index_sha.hexdigest(),
            byte_coverage_ratio=min(1.0, max(0.0, ratio)),
        )
        manifest_text = json.dumps(manifest.to_dict(), indent=2)
        self.charge(len(manifest_text.replace("\n", os.linesep).encode("utf-8")))
        counters: dict[str, Any] = {
            "shard_id": plan.name,
            "source_id": plan.name,
            "num_tokens": self.num_tokens,
            "num_documents": self.num_documents,
            "valid_targets": self.valid_targets,
            "eos_tokens": self.eos_tokens,
            "content_tokens": self.num_tokens - self.eos_tokens,
            "canonical_bytes": self.canonical_bytes,
            "covered_bytes": self.covered_bytes,
            "token_dtype": self.dtype,
        }
        if self.schema == INDEX_SCHEMA_V2:
            counters["index_schema"] = INDEX_SCHEMA_V2
            counters["token_bytes_sha256"] = hashlib.sha256(table).hexdigest()
            self.charge(len(table))
            path = self.owned.file(self.directory / TOKEN_BYTES_FILE)
            with path.open("xb") as stream:
                stream.write(table)
                stream.flush()
                os.fsync(stream.fileno())
        counter_text = json.dumps(counters, indent=2, sort_keys=True)
        self.charge(len(counter_text.replace("\n", os.linesep).encode("utf-8")))
        _write_synced(self.owned.file(self.directory / "shard_counters.json"), counter_text)
        proof_text = json.dumps(seal(manifest.to_dict(), counters), sort_keys=True)
        self.charge(len(proof_text.encode()))
        _write_synced(self.owned.file(self.directory / "c05-attestation.json"), proof_text)
        _write_synced(self.owned.file(self.directory / "shard_manifest.json"), manifest_text)
        final = self.root / plan.name
        if final.exists():
            raise C05Error("token shard component is write-once")
        os.rename(self.directory, final)  # The staged names are gone; cleanup skips them.
        return final

    def close(self) -> None:
        for output in (self.tokens, self.index):
            if not output.closed:
                output.close()


def shard_binding(
    view: StreamedC05, manifest: Mapping[str, Any], counters: Mapping[str, Any]
) -> dict[str, Any]:
    """``MembershipGate._shard_binding``: the signed C05 token-shard attestation body."""
    return {
        "kind": SHARD_KIND,
        "mode": view.mode,
        "plan_digest": view.plan_digest,
        "receipt_digest": view.receipt_digest,
        "shard_manifest_digest": canonical.digest(dict(manifest)),
        "counters_digest": canonical.digest(dict(counters)),
    }


@dataclass(frozen=True)
class Expected:
    """What a published component must contain, for resume verification."""

    fingerprint: str
    selection: str
    receipt: str
    schema: str
    dtype: str
    table_sha256: str


def verify_published(
    directory: Path,
    plan: ComponentPlan,
    m: Membership,
    s: Selected,
    view: StreamedC05,
    expected: Expected,
) -> dict[str, Any]:
    """Complete verification of a published component (resume): never trusts a name."""
    names = V2_NAMES if expected.schema == INDEX_SCHEMA_V2 else V1_NAMES
    present = sorted(p.name for p in directory.iterdir())
    if present != sorted(names):
        raise C05Error("published token shard has an unexpected file set")
    reader = TokenShardReader(directory)
    if reader.index_schema != expected.schema:
        raise C05Error("published token shard uses a different index schema")
    reader.verify_integrity()
    manifest, counters = reader.manifest.to_dict(), reader.counters
    proof = verify_signed(
        read_metadata(directory / "c05-attestation.json", digested=False), view.trusted
    )
    if any(proof.get(k) != v for k, v in shard_binding(view, manifest, counters).items()):
        raise C05Error("token shard protected attestation mismatch")
    if (
        manifest["shard_id"],
        manifest["source_id"],
        manifest["tokenizer_hash"],
        manifest["pool_hash"],
        manifest["token_dtype"],
        manifest["num_documents"],
        manifest["num_tokens"],
    ) != (
        plan.name,
        plan.name,
        expected.fingerprint,
        expected.selection,
        expected.dtype,
        plan.documents,
        plan.tokens,
    ):
        raise C05Error("published token shard differs from the selected component")
    if (
        counters.get("valid_targets"),
        counters.get("canonical_bytes"),
        counters.get("num_documents"),
        counters.get("num_tokens"),
    ) != (plan.valid_targets, plan.canonical_bytes, plan.documents, plan.tokens):
        raise C05Error("published token shard counters differ from the selection")
    if expected.schema == INDEX_SCHEMA_V2 and (
        counters.get("token_bytes_sha256") != expected.table_sha256
    ):
        raise C05Error("published token shard byte table differs from the tokenizer's")
    rows = np.concatenate([f.selected for f in plan.files]) if plan.files else np.zeros(0, np.int64)
    offsets = m.id_offsets
    cursor = documents = 0
    for record in reader.iter_document_offsets():
        if documents >= len(rows):
            raise C05Error("shard document outside selected training membership")
        n = int(rows[documents])
        position = int(s.position[n])
        doc_id = m.ids[int(offsets[position]) : int(offsets[position + 1])].decode("utf-8")
        chosen, counted = int(s.chosen[n]), int(s.counted[n])
        if (
            record.get("doc_id") != doc_id
            or record.get("c05_content") != m.content[position].tobytes().hex()
            or record.get("source_id") != plan.name
            or record.get("split") != "train"
            or record.get("c05_receipt") != expected.receipt
            or record.get("c05_selection") != expected.selection
            or record.get("c05_counted_valid_targets") != counted
            or record.get("c05_selected_valid_targets") != chosen
            or record.get("valid_targets") != chosen
            or record.get("token_count") != chosen + 1
            or record.get("token_start") != cursor
            or ("token_byte_spans" in record) != (expected.schema == INDEX_SCHEMA_V1)
        ):
            raise C05Error("shard exact count or selection drifted")
        cursor += chosen + 1
        documents += 1
    if documents != plan.documents or cursor != plan.tokens:
        raise C05Error("shard does not contain exactly the selected component membership")
    return manifest


def clear_stale_stages(root: Path, components: list[str]) -> list[str]:
    """Remove this tool's stale stage directories; anything unexpected refuses."""
    removed: list[str] = []
    if not root.is_dir():
        return removed
    allowed = set(V2_NAMES)
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") and entry.name.endswith(".tokenize.lock"):
            continue
        if entry.name.startswith(".") and STAGE_MARK in entry.name:
            component = entry.name[1:].split(STAGE_MARK, 1)[0]
            if component not in components or not entry.is_dir():
                raise C05Error("unexpected entry in the token shard root")
            inner = list(entry.iterdir())
            if any(p.name not in allowed or not p.is_file() for p in inner):
                raise C05Error("stale token stage holds unexpected files; refusing to remove")
            for path in inner:
                os.chmod(path, 0o600)
                path.unlink()
            entry.rmdir()
            removed.append(component)
            continue
        if entry.name not in components or not entry.is_dir():
            raise C05Error("unexpected entry in the token shard root")
    return removed


# -- parent: the source pass ----------------------------------------------------------------


@dataclass
class _FileState:
    work: FileWork
    scanned: ScanResult | None = None
    chunks: list[tuple[int, int]] = field(default_factory=list)
    submitted: int = 0  # chunk tasks submitted
    written: int = 0  # chunk results written
    scanning: bool = False


class _Telemetry:
    """Content-free resource sampling for progress lines (display only)."""

    def __init__(self, supervisor: Supervisor, output: Path) -> None:
        self.supervisor, self.output = supervisor, output
        self.process = psutil.Process()
        self.last: tuple[float, float] | None = None

    def __call__(self) -> dict[str, int | float]:
        rss = cpu = 0.0
        try:
            tree = [self.process, *self.process.children(recursive=True)]
        except psutil.Error:
            tree = [self.process]
        for process in tree:
            try:
                rss += process.memory_info().rss
                times = process.cpu_times()
                cpu += times.user + times.system
            except psutil.Error:
                continue
        now = time.monotonic()
        values: dict[str, int | float] = {
            "rss": int(rss),
            "peak_rss": max(int(rss), self.supervisor.peak_rss),
            "ram_limit": int(self.supervisor.ram_ceiling or 0),
        }
        if self.last is not None and now > self.last[0]:
            share = (cpu - self.last[1]) / (now - self.last[0]) / (psutil.cpu_count() or 1)
            values["cpu_percent"] = round(max(0.0, 100.0 * share), 1)
        self.last = (now, cpu)
        try:
            values["free"] = shutil.disk_usage(_existing(self.output)).free
        except OSError:
            pass
        return values


def _chunks(lengths: npt.NDArray[np.uint32]) -> list[tuple[int, int]]:
    """Consecutive selected-row ranges of at most ``CHUNK_TEXT_BYTES`` line bytes."""
    ends = np.cumsum(lengths.astype(np.int64))
    out: list[tuple[int, int]] = []
    start, count = 0, len(lengths)
    while start < count:
        base = int(ends[start - 1]) if start else 0
        end = max(start + 1, int(np.searchsorted(ends, base + CHUNK_TEXT_BYTES, side="right")))
        out.append((start, end))
        start = end
    return out


def source_pass(
    view: StreamedC05,
    m: Membership,
    s: Selected,
    plans: list[ComponentPlan],
    skipped: set[str],
    pool: OrderedPool,
    progress: RunProgress | NullProgress,
    stage_of: Any,
    publish: Any,
    *,
    block_bytes: int = 0,
    measured: dict[str, Any],
) -> None:
    """Hash every file of the produced components once; tokenize; write strictly in order."""
    plan = view.plan
    root = Path(plan.data_root)
    active = [p for p in plans if p.name not in skipped]
    order: list[_FileState] = [_FileState(w) for p in active for w in p.files]
    owner = {id(state): plans[state.work.component] for state in order}
    total_docs = sum(p.documents for p in active)
    total_text = sum(p.canonical_bytes for p in active)
    total_targets = sum(p.valid_targets for p in active)
    total_input = sum(plan.files[st.work.ordinal].file_bytes for st in order)
    workers = pool.workers
    capacity = 3 * workers
    scan_limit = max(1, workers // 4)
    progress.stage("SOURCE TOKENIZE", total_docs, "docs", work_total=total_text)
    begun = time.monotonic()
    in_flight: dict[Future[Any], tuple[str, int, int]] = {}
    buffered: dict[tuple[int, int], TokenResult] = {}
    scans = 0
    scan_cursor = 0  # next file (in write order) whose scan is not yet submitted
    write_file = 0  # file currently being written
    submit_file = 0  # file whose chunks are currently being submitted
    hashed = files_done = docs = tokens = text = targets = 0
    stages: dict[str, ShardStage] = {}
    results = 0

    def lookahead_bytes() -> int:
        return sum(
            plan.files[order[k].work.ordinal].file_bytes
            for k in range(write_file, scan_cursor)
            if order[k].scanned is not None or order[k].scanning
        )

    def scan_task(state: _FileState) -> ScanTask:
        item = plan.files[state.work.ordinal]
        return ScanTask(
            ordinal=state.work.ordinal,
            path=str(contained(root, item.path)),
            sha256=item.documents_sha256,
            file_bytes=item.file_bytes,
            documents=item.documents,
            line_ceiling=plan.resources.document_bytes,
            rows=np.ascontiguousarray(m.row[s.position[state.work.selected]]),
            block_bytes=block_bytes,
        )

    def token_task(state: _FileState, number: int) -> TokenTask:
        start, end = state.chunks[number]
        item = plan.files[state.work.ordinal]
        scanned = state.scanned
        assert scanned is not None
        rows = state.work.selected[start:end]
        positions = s.position[rows]
        starts = m.id_offsets[positions].tolist()
        ends = m.id_offsets[positions + 1].tolist()
        return TokenTask(
            ordinal=state.work.ordinal,
            first=start,
            component=owner[id(state)].name,
            path=str(contained(root, item.path)),
            file_bytes=item.file_bytes,
            mtime_ns=scanned.mtime_ns,
            offsets=scanned.offsets[start:end],
            lengths=scanned.lengths[start:end],
            ids=[m.ids[a:b] for a, b in zip(starts, ends, strict=True)],
            content=np.ascontiguousarray(m.content[positions]).tobytes(),
            counted=np.ascontiguousarray(s.counted[rows]),
            chosen=np.ascontiguousarray(s.chosen[rows]),
        )

    def report() -> None:
        elapsed = max(time.monotonic() - begun, 1e-9)
        progress.update(
            docs,
            work=text,
            files_committed=files_done,
            files_total=len(order),
            bytes_done=hashed,
            bytes_total=total_input,
            token_ids=tokens,
            valid_targets=targets,
            valid_targets_total=total_targets,
            output_gib=round(sum(st.written for st in stages.values()) / GiB, 3),
            mtokens_per_s=round(tokens / elapsed / 1e6, 3),
            workers=workers,
            busy=min(workers, len(in_flight)),
            tasks=len(in_flight),
            capacity=capacity,
            results=results,
            buffered=len(buffered),
        )

    while write_file < len(order):
        # 1. Scans run ahead of the writer, bounded by count and by page-cache bytes.
        while (
            scan_cursor < len(order)
            and scans < scan_limit
            and len(in_flight) < capacity + scan_limit
            and (scan_cursor == write_file or lookahead_bytes() < LOOKAHEAD_BYTES)
        ):
            state = order[scan_cursor]
            state.scanning = True
            in_flight[pool.submit(run_task, scan_task(state))] = ("scan", scan_cursor, 0)
            scans += 1
            scan_cursor += 1
        # 2. Token tasks strictly in write order, bounded with the unwritten results.
        while submit_file < len(order) and len(in_flight) - scans + len(buffered) < capacity:
            state = order[submit_file]
            if state.scanned is None:
                break
            if state.submitted < len(state.chunks):
                number = state.submitted
                in_flight[pool.submit(run_task, token_task(state, number))] = (
                    "chunk",
                    submit_file,
                    number,
                )
                state.submitted += 1
            if state.submitted == len(state.chunks):
                submit_file += 1
        if not in_flight:
            raise C05Error("tokenize scheduler stalled")
        for future in pool.completed(set(in_flight)):
            kind, index, number = in_flight.pop(future)
            result = future.result()
            results += 1
            state = order[index]
            if kind == "scan":
                scans -= 1
                if not isinstance(result, ScanResult) or result.ordinal != state.work.ordinal:
                    raise C05Error("tokenize scan differs from its task")
                if len(result.offsets) != len(state.work.selected):
                    raise C05Error("tokenize scan located a different number of rows")
                state.scanned, state.scanning = result, False
                state.chunks = _chunks(result.lengths)
                hashed += plan.files[state.work.ordinal].file_bytes
            else:
                start, end = state.chunks[number]
                if not isinstance(result, TokenResult) or (
                    result.ordinal,
                    result.first,
                    len(result),
                ) != (
                    state.work.ordinal,
                    start,
                    end - start,
                ):
                    raise C05Error("tokenize result differs from its task")
                buffered[(index, number)] = result
        # 3. Write every result that is next in order; publish finished components.
        while write_file < len(order):
            state = order[write_file]
            if state.scanned is None:
                break
            key = (write_file, state.written)
            if state.written < len(state.chunks):
                chunk = buffered.pop(key, None)
                if chunk is None:
                    break
                component = owner[id(state)]
                stage = stages.get(component.name)
                if stage is None:
                    stage = stages[component.name] = stage_of(component)
                stage.write(chunk)
                state.written += 1
                docs += len(chunk)
                text += chunk.text_bytes
                tokens += int(chunk.token_counts.sum())
                targets += int(np.maximum(chunk.token_counts - 1, 0).sum())
                continue
            files_done += 1
            write_file += 1
            component = owner[id(state)]
            last = write_file == len(order) or owner[id(order[write_file])] is not component
            if last:
                stage = stages.get(component.name)
                if stage is None:  # A component without documents cannot exist here.
                    raise C05Error(
                        "shard does not contain exactly the selected component membership"
                    )
                publish(stage)
                del stages[component.name]
        report()
    seconds = time.monotonic() - begun
    if docs != total_docs or targets != total_targets or hashed != total_input:
        raise C05Error("tokenization does not cover every selected document")
    measured.update(
        source_seconds=round(seconds, 3),
        source_docs_per_s=round(docs / max(seconds, 1e-9), 1),
        source_text_mib_per_s=round(text / MiB / max(seconds, 1e-9), 2),
        source_input_mib_per_s=round(hashed / MiB / max(seconds, 1e-9), 2),
        source_mtokens_per_s=round(tokens / 1e6 / max(seconds, 1e-9), 3),
    )


# -- parent: entry point -------------------------------------------------------------------


class _Reporter(FitReporter):
    NAMES = {"MEMBERSHIP STREAM": "MEMBERSHIP VERIFY"}

    def __init__(self, progress: RunProgress | NullProgress) -> None:
        self.progress = progress

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        self.progress.stage(self.NAMES.get(name, name), total, unit)

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        self.progress.update(done, force=force, **fields)

    def note(self, text: str) -> None:
        return None


def _signer(proof: Path, view: StreamedC05) -> tuple[str, bytes]:
    """The proof's downstream signing identity (``open_gate``), trusted before any work."""
    from xlm.data.exclusion.control import key_from_env
    from xlm.data.exclusion.transport import ProofSpec

    spec = ProofSpec.model_validate(read_metadata(proof, digested=False))
    if spec.signer is None or spec.signer_key_env is None:
        if spec.signer is not None or spec.signer_key_env is not None:
            raise C05Error("incomplete downstream signing identity")
        raise C05Error("screened token publication requires an explicit trusted signing identity")
    key = key_from_env(spec.signer_key_env)
    if view.trusted.get(spec.signer) != key:
        raise C05Error("downstream signer is not a trusted issuer")
    return spec.signer, key


def _check_roots(
    view: StreamedC05, selection: Path, tokenizer_dir: Path, scratch: Path, output_root: Path
) -> None:
    from xlm.data.exclusion.isolation import overlaps

    immutable = [
        ("C05 data root", view.plan.data_root),
        ("C05 completion", str(view.directory)),
        ("tokenizer", str(tokenizer_dir)),
        ("selection", str(selection)),
    ]
    for name, path in (("tokenize scratch", scratch), ("shard output", output_root)):
        for role, other in immutable:
            if overlaps(path, other):
                raise C05Error(f"{name} overlaps the {role}")
    if overlaps(scratch, output_root):
        raise C05Error("tokenize scratch overlaps the shard output")


@dataclass(frozen=True)
class TokenizePins:
    """Operator expectations; any mismatch refuses before a source byte is read."""

    selection_digest: str | None = None
    selected_membership_sha256: str | None = None
    tokenizer_fingerprint: str | None = None
    plan_digest: str | None = None
    completion_digest: str | None = None


def tokenize_selection_fast(
    proof: Path,
    selection_dir: Path,
    tokenizer_dir: Path,
    output_root: Path,
    *,
    scratch: Path,
    workers: int,
    index_schema: str = INDEX_SCHEMA_V2,
    resume: bool = False,
    plan_only: bool = False,
    progress: RunProgress | NullProgress | None = None,
    pins: TokenizePins | None = None,
    output_reserve: int = DEFAULT_OUTPUT_RESERVE,
    scratch_reserve: int = DEFAULT_SCRATCH_RESERVE,
    inline: bool = False,
    block_bytes: int = 0,
    allow_authored: bool = True,
    consumes: list[Path | str] | None = None,
) -> dict[str, Any]:
    """Tokenize the signed selection into one shard per component (see module doc).

    Returns ``{"shards": {component: manifest}, "skipped": [...], "plan": {...},
    "measured": {...}}``; ``plan_only`` returns after the resource plan, before any
    source byte is read.
    """
    if workers not in WORKER_CHOICES:
        raise C05Error("tokenize workers must be 1, 2, 4, 8 or 16")
    if index_schema not in INDEX_SCHEMAS:
        raise C05Error("unknown C07 index schema")
    pins = pins or TokenizePins()
    progress = progress or NullProgress()
    current = ["PROOF VERIFY"]

    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        current[0] = name
        progress.stage(name, total, unit)

    owned = OwnedPaths()
    started = time.monotonic()
    measured: dict[str, Any] = {}
    lock: FileLock | None = None
    finished = False
    try:
        begin("PROOF VERIFY")
        view = open_streamed(
            proof,
            allow_authored=allow_authored,
            consumes=[selection_dir, tokenizer_dir, scratch, output_root, *(consumes or [])],
        )
        if pins.plan_digest is not None and pins.plan_digest != view.plan_digest:
            raise C05Error("C05 plan digest differs from the operator pin")
        if pins.completion_digest is not None and pins.completion_digest != view.receipt_digest:
            raise C05Error("C05 completion digest differs from the operator pin")
        # Publication needs the trusted downstream signer; refuse before any work. The
        # read-only resource plan publishes nothing and needs no key.
        signer = None if plan_only else _signer(proof, view)
        _check_roots(view, selection_dir, tokenizer_dir, scratch, output_root)
        begin("SELECTION ENVELOPE")
        envelope = verify_selection_envelope(selection_dir, view)
        body: dict[str, Any] = envelope["payload"]
        digest = str(envelope["digest"])
        if pins.selection_digest is not None and pins.selection_digest != digest:
            raise C05Error("selection digest differs from the operator pin")
        if pins.selected_membership_sha256 is not None and (
            pins.selected_membership_sha256 != body["selected_membership_sha256"]
        ):
            raise C05Error("selected membership SHA-256 differs from the operator pin")
        if output_root.is_dir() and not plan_only:
            # One run per root: the lock is held before the root is inspected.
            lock = FileLock(str(output_root / LOCK_NAME))
            lock.acquire(timeout=0)
        if (
            not resume
            and output_root.exists()
            and any(p.name != LOCK_NAME for p in output_root.iterdir())
        ):
            raise C05Error("token shard output root is not fresh (use resume)")
        if resume and not plan_only and not output_root.is_dir():
            raise C05Error("resume requires an existing token shard output root")
        # Coarse early refusal from signed totals alone: tokens.bin of a fresh run is
        # exactly (targets + documents) IDs; the exact plan is checked again later.
        floor = 2 * (int(body["selected_valid_targets"]) + int(body["selected_documents"]))
        if (
            not plan_only
            and not resume
            and (shutil.disk_usage(_existing(output_root)).free < floor + output_reserve)
        ):
            raise C05Error("insufficient free space on the shard output volume")
        scratch.mkdir(parents=True, exist_ok=True)
        begin("TOKENIZER VERIFY")
        snapshot: TokenizerSnapshot = snapshot_tokenizer(tokenizer_dir, view, scratch, owned)
        identity = snapshot.identity
        if identity != body["tokenizer"]:
            raise C05Error("tokenization tokenizer differs from the exact-count tokenizer")
        if pins.tokenizer_fingerprint is not None and (
            pins.tokenizer_fingerprint != identity["fingerprint"]
        ):
            raise C05Error("tokenizer fingerprint differs from the operator pin")
        from xlm.data.exclusion.selection import load_tokenizer
        from xlm.tokenizers.bpe import ByteLevelBPETokenizer

        tokenizer = load_tokenizer(snapshot.copy)
        if not isinstance(tokenizer, ByteLevelBPETokenizer):
            raise C05Error("fast tokenize-selection requires the byte-level BPE tokenizer")
        if tokenizer.fingerprint != identity["fingerprint"]:
            raise C05Error("tokenizer snapshot differs from its verified identity")
        table = token_byte_lengths(tokenizer)
        table_sha = hashlib.sha256(table).hexdigest()
        dtype = "uint16" if tokenizer.actual_vocab_size <= 65536 else "uint32"
        membership, keys = count_tables(view)
        tables = TokenizeTables(
            membership,
            str(snapshot.copy),
            str(identity["fingerprint"]),
            table_sha,
            view.receipt_digest,
            digest,
            index_schema,
            dtype,
        )
        volumes = {}
        if not plan_only:
            volumes = {
                str(_existing(output_root)): output_reserve,
                str(_existing(scratch)): scratch_reserve,
            }
        supervisor = Supervisor(
            Deadline(None, started), view.plan.resources.ram_bytes, volumes=volumes
        )
        result: dict[str, Any] = {"shards": {}, "skipped": [], "measured": measured}
        with supervisor:
            if isinstance(progress, RunProgress):
                progress.attach(_Telemetry(supervisor, output_root))
            with OrderedPool(
                workers, tables, supervisor, inline=inline, initializer=init_tokenize_worker
            ) as pool:
                current[0] = "MEMBERSHIP VERIFY"
                m = stream_membership(view, membership, pool, _Reporter(progress), supervisor, {})
                reconcile(view, m, keys)
                current[0] = "SELECTION VERIFY"
                s = stream_selection(selection_dir, body, view, m, keys, pool, progress)
                begin("RESOURCE PLAN")
                plans = work_plan(view, m, s, index_schema, 2 if dtype == "uint16" else 4)
                expected = Expected(
                    str(identity["fingerprint"]),
                    digest,
                    view.receipt_digest,
                    index_schema,
                    dtype,
                    table_sha,
                )
                skipped: set[str] = set()
                names = [p.name for p in plans]
                if resume and output_root.is_dir():
                    if not plan_only:
                        clear_stale_stages(output_root, names)
                    begin("RESUME VERIFY", len(plans), "components")
                    for n, plan in enumerate(plans):
                        directory = output_root / plan.name
                        if directory.exists():
                            current[0] = "RESUME VERIFY"
                            result["shards"][plan.name] = verify_published(
                                directory, plan, m, s, view, expected
                            )
                            skipped.add(plan.name)
                        progress.update(n + 1)
                resources = resource_plan(
                    plans,
                    index_schema,
                    workers,
                    output_root,
                    scratch,
                    output_reserve,
                    scratch_reserve,
                    skipped,
                )
                result["plan"] = resources
                result["skipped"] = sorted(skipped)
                if plan_only:
                    return result
                preflight(resources)
                if lock is None:
                    output_root.mkdir(parents=True, exist_ok=True)
                    lock = FileLock(str(output_root / LOCK_NAME))
                    lock.acquire(timeout=0)
                ceiling = view.plan.resources.output_bytes
                live: list[ShardStage] = []

                def stage_of(plan: ComponentPlan) -> ShardStage:
                    stage = ShardStage(output_root, plan, index_schema, dtype, ceiling, owned)
                    live.append(stage)
                    return stage

                def seal(manifest: dict[str, Any], counters: dict[str, Any]) -> dict[str, Any]:
                    if signer is None:
                        raise C05Error("screened token publication requires a signing identity")
                    return signed(shard_binding(view, manifest, counters), *signer)

                def publish(stage: ShardStage) -> None:
                    final = stage.finish(str(identity["fingerprint"]), digest, table, seal)
                    result["shards"][stage.plan.name] = TokenShardReader(final).manifest.to_dict()

                current[0] = "SOURCE TOKENIZE"
                try:
                    source_pass(
                        view,
                        m,
                        s,
                        plans,
                        skipped,
                        pool,
                        progress,
                        stage_of,
                        publish,
                        block_bytes=block_bytes,
                        measured=measured,
                    )
                finally:
                    for stage in live:
                        stage.close()
            supervisor.check()
            begin("FINAL VERIFY")
            digests = snapshot.digests()
            if _directory_digests(tokenizer_dir) != digests:
                raise C05Error("tokenizer changed after tokenization started")
            if _directory_digests(snapshot.copy) != digests:
                raise C05Error("private tokenizer copy changed during tokenization")
            if sorted(result["shards"]) != names:
                raise C05Error("token shards do not cover every selected component")
            measured["peak_rss"] = supervisor.peak_rss
            measured["seconds"] = round(time.monotonic() - started, 3)
        finished = True
    except Timeout as exc:
        raise C05Error("another tokenize-selection holds this shard output root") from exc
    except Exception as exc:
        # A fixed stage literal for the content-free CLI refusal (no paths or values).
        setattr(exc, "tokenize_stage", current[0])  # noqa: B010
        raise
    finally:
        residue = owned.cleanup()
        if lock is not None:
            lock.release()
    if residue:
        raise C05Error("tokenize-selection could not remove all of its own files")
    if finished:
        progress.complete()
    return result
