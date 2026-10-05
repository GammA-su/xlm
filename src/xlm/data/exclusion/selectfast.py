"""Exact quota selection, FAST: authenticated streams, positional proof, rank-prefix buckets.

Byte-identical to :func:`selection.select` (the reference oracle, ``select-reference``)
on every input it accepts: the same ``selected.jsonl`` rows in the same order, the same
signed ``selection.json`` and the same deficit report. Only the work changes; SQLite is
gone from the whole command:

1. **Membership** (``membership.jsonl``) is streamed once and authenticated exactly as
   count-tokens and the C06 fast fit do (:func:`fitfast.stream_membership`): SHA-256,
   size and row count equal the signed completion; rows are schema-checked, strictly
   ascending by doc id (unique), bound to a frozen plan file/row and its allocation,
   and reconciled against the completion's per-allocation accounting.
2. **Counts envelope**: signature, C05 binding, counting rule, tokenizer identity and
   size are checked as the reference does. ``counts.jsonl`` is hashed in the same single
   sequential pass that consumes it; nothing it yields is used for any decision or
   output unless the SHA-256, byte size and row count equal the signed envelope.
3. **Positional proof** (an ordered merge with membership): count row ``k`` must be,
   byte for byte, the canonical count row of the ``k``-th kept *train* membership row
   (its doc id, C05 content digest and allocation) with a canonical non-negative integer
   count. Membership is strictly ascending, so this proves the counts are exactly the
   kept-train membership: no row missing, extra, repeated, reordered, changed or moved
   to another allocation, and no kept non-train row. A count artifact the reference
   accepts but whose rows are not in canonical doc-id order or not canonical bytes (no
   ``count-tokens`` path writes either) is refused, never interpreted differently.
4. **Rank** is the reference digest, ``sha256(C([seed, allocation, doc_id, content]))``,
   built from the same canonical bytes; workers return its first 64 bits per row.
5. **Exact selection by rank-prefix buckets** (:func:`select_by_buckets`): per
   allocation, eligible valid targets are summed per 16-bit rank prefix; only the bucket
   where the cumulative sum first reaches the quota is sorted by full rank then doc id.
   See that function for the equivalence proof.
6. **Export** in membership (doc-id byte) order, which equals the reference
   ``ORDER BY id`` (SQLite BINARY collation is UTF-8 byte order); the row serializer is
   tested byte-equal to ``canonical_bytes``. Stage directory, fsync, re-read hash,
   signature and input re-checks, then one directory rename.

Worker count is operational only: tasks are consumed strictly in submission order.
Interruption or failure terminates and reaps every worker and removes exactly the files
this job created; a deficit publishes nothing (the caller writes the content-free
deficit report). Resume is not supported (a rerun starts over; it takes minutes).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from json.encoder import encode_basestring
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.countfast import _Telemetry, count_tables, preflight_output
from xlm.data.exclusion.fitfast import (
    Membership,
    OwnedPaths,
    StreamedC05,
    open_streamed,
    reconcile,
    stream_membership,
)
from xlm.data.exclusion.fitscan import MembershipTables, OrderedPool, _cancel_check, init_worker
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.quotas import view_requirements
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selection import (
    COUNT_RULE,
    MAX_TOKENIZER_FILE_BYTES,
    MAX_TOKENIZER_FILES,
    SelectionDeficit,
    SelectionPolicy,
    binding_of,
    check_binding,
    tokenizer_identity,
)
from xlm.data.exclusion.supervisor import Deadline, Supervisor
from xlm.data.exclusion.tokenizer_fit import _Reporter as FitReporter

LABEL = "SELECT"
WORKER_CHOICES = (1, 2, 4, 8, 16)
ARTIFACTS = ("selected.jsonl", "selection.json")
# The reference reads count rows with readline(64 KiB + 1): a longer row (LF included) refuses.
COUNT_LINE_CEILING = 64 * 1024
# counts.jsonl bytes per rank task (complete lines; a block never splits a row).
RANK_BLOCK_BYTES = 4 * MiB
# Selected rows serialized by one export task.
EXPORT_ROWS = 32_768
PREFIX_BITS = 16
HASH_BLOCK = 8 * MiB
# A canonical count above 18 digits cannot be a real count (and would overflow int64).
MAX_COUNT_DIGITS = 18
COUNT_KEYS = frozenset({"doc_id", "content", "allocation", "valid_targets"})


# -- canonical bytes ------------------------------------------------------------------------


def json_string(raw_id: bytes) -> bytes:
    """``canonical_bytes(doc_id)``: the C encoder ``json.dumps(..., ensure_ascii=False)`` uses."""
    return encode_basestring(raw_id.decode("utf-8")).encode("utf-8")


def rank_heads(seed: int, keys: Sequence[str]) -> tuple[bytes, ...]:
    """Per allocation, the bytes of ``C([seed, allocation, doc_id, content])`` before doc_id."""
    return tuple(
        b"[" + canonical.canonical_bytes(seed) + b"," + canonical.canonical_bytes(key) + b","
        for key in keys
    )


def rank_digest(head: bytes, doc_id: bytes, content: bytes) -> bytes:
    """SHA-256 of the reference rank preimage; ``doc_id`` is canonical JSON, ``content`` hex."""
    return hashlib.sha256(b"".join((head, doc_id, b',"', content, b'"]'))).digest()


def count_head(fragment: bytes, content: bytes, doc_id: bytes) -> bytes:
    """``canonical_bytes(count row)`` up to the count digits (keys in sorted order)."""
    return b"".join(
        (
            b'{"allocation":',
            fragment,
            b',"content":"',
            content,
            b'","doc_id":',
            doc_id,
            b',"valid_targets":',
        )
    )


def selected_row(
    fragment: bytes, content: bytes, doc_id: bytes, counted: int, chosen: int
) -> bytes:
    """Exactly ``canonical_bytes(reference selected row) + LF`` (keys in sorted order)."""
    return b"".join(
        (
            b'{"allocation":',
            fragment,
            b',"content":"',
            content,
            b'","counted_valid_targets":',
            str(counted).encode("ascii"),
            b',"doc_id":',
            doc_id,
            b',"selected_valid_targets":',
            str(chosen).encode("ascii"),
            b"}\n",
        )
    )


# Bytes of one selected row besides the fragment, the raw doc id and the two counts.
SELECTED_FIXED_BYTES = len(selected_row(b"", b"0" * 64, b'""', 0, 0)) - 2


# -- worker side ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectTables:
    """Sent once to every worker: membership tables plus per-allocation canonical bytes."""

    membership: MembershipTables
    fragments: tuple[bytes, ...]  # canonical allocation list, by allocation code
    heads: tuple[bytes, ...]  # rank preimage head, by allocation code


_SELECT: SelectTables | None = None


def init_select_worker(tables: SelectTables) -> None:
    global _SELECT
    init_worker(tables.membership)
    _SELECT = tables


def _tables() -> SelectTables:
    if _SELECT is None:
        raise C05Error("selection worker tables missing")
    return _SELECT


@dataclass(frozen=True)
class RankTask:
    """Consecutive count rows and the membership span holding their expected train rows."""

    first: int  # train ordinal of the first row
    rows: int
    data: bytes  # complete count lines
    final: bool  # the last line has no LF (end of file)
    ids: bytes  # membership ids of the span
    id_offsets: npt.NDArray[np.int64]  # span rows + 1, relative to ``ids``
    content: bytes  # 32 bytes per span row
    allocation: npt.NDArray[np.uint16]  # per span row
    train: npt.NDArray[np.int64]  # span-relative index of each expected train row


@dataclass
class RankResult:
    first: int
    rows: int
    tokens: npt.NDArray[np.int64]
    prefix: npt.NDArray[np.uint64]  # first 64 bits of the selection rank
    mismatch: int = -1  # first row whose doc id differs from the expected train row
    found: bytes = b""  # that row's doc id: parent-side diagnosis only, never printed


def _diagnose(line: bytes, doc_id: bytes, content: bytes, fragment: bytes) -> bytes:
    """Fixed-literal refusal for a non-matching row; returns its doc id if that differs."""
    try:
        value = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise C05Error("count record is not strict canonical JSON") from exc
    if type(value) is not dict or value.keys() != COUNT_KEYS:
        raise C05Error("count record schema")
    tokens, found = value["valid_targets"], value["doc_id"]
    if type(tokens) is not int or tokens < 0:
        raise C05Error("count record value")
    if type(found) is not str or type(value["content"]) is not str:
        raise C05Error("count record schema")
    if found.encode("utf-8") != doc_id:
        return found.encode("utf-8")
    if value["content"].encode("utf-8") != content:
        raise C05Error("record differs from C05 kept membership")
    try:
        allocation = canonical.canonical_bytes(value["allocation"])
    except canonical.CanonicalError as exc:
        raise C05Error("count record schema") from exc
    if allocation != fragment:
        raise C05Error("record allocation differs from C05 membership")
    if tokens >= 10**MAX_COUNT_DIGITS:
        raise C05Error("count record value")
    raise C05Error("count record is not canonical bytes")


def rank_chunk(task: RankTask) -> RankResult:
    """Prove each row is the canonical count row of its train record; rank it."""
    tables = _tables()
    lines = task.data.split(b"\n")
    if task.final:
        if not lines[-1]:
            raise C05Error("count rank task is inconsistent")
    elif lines.pop():
        raise C05Error("count rank task is inconsistent")
    if len(lines) != task.rows or len(task.train) != task.rows:
        raise C05Error("count rank task is inconsistent")
    ids, content = task.ids, task.content
    offsets = task.id_offsets.tolist()
    allocation = task.allocation.tolist()
    fragments, heads = tables.fragments, tables.heads
    tokens = np.zeros(task.rows, dtype=np.int64)
    digests: list[bytes] = []
    last = task.rows - 1
    for number, (line, row) in enumerate(zip(lines, task.train.tolist(), strict=True)):
        if number & 4095 == 4095:
            _cancel_check()
        if len(line) + (0 if task.final and number == last else 1) > COUNT_LINE_CEILING:
            raise C05Error("count record ceiling")
        raw_id = ids[offsets[row] : offsets[row + 1]]
        doc_id = json_string(raw_id)
        hexed = content[32 * row : 32 * row + 32].hex().encode("ascii")
        code = allocation[row]
        head = count_head(fragments[code], hexed, doc_id)
        digits = line[len(head) : -1]
        if (
            not line.startswith(head)
            or line[-1:] != b"}"
            or not 0 < len(digits) <= MAX_COUNT_DIGITS
            or not digits.isdigit()
            or (digits[0] == 48 and len(digits) > 1)
        ):
            found = _diagnose(line, raw_id, hexed, fragments[code])
            prefix = np.frombuffer(b"".join(digests), dtype=">u8").astype(np.uint64)
            return RankResult(task.first, task.rows, tokens, prefix, number, found)
        tokens[number] = int(digits)
        digests.append(rank_digest(heads[code], doc_id, hexed)[:8])
    prefix = np.frombuffer(b"".join(digests), dtype=">u8").astype(np.uint64)
    return RankResult(task.first, task.rows, tokens, prefix)


@dataclass(frozen=True)
class ExportTask:
    ids: bytes
    id_lengths: npt.NDArray[np.int64]
    content: bytes  # 32 bytes per row
    allocation: npt.NDArray[np.uint16]
    counted: npt.NDArray[np.int64]
    chosen: npt.NDArray[np.int64]


def export_chunk(task: ExportTask) -> bytes:
    """Canonical selected rows, in task order."""
    fragments = _tables().fragments
    ends = np.cumsum(task.id_lengths).tolist()
    starts = [0, *ends[:-1]]
    allocation = task.allocation.tolist()
    counted, chosen = task.counted.tolist(), task.chosen.tolist()
    rows: list[bytes] = []
    for n in range(len(ends)):
        if n & 4095 == 4095:
            _cancel_check()
        rows.append(
            selected_row(
                fragments[allocation[n]],
                task.content[32 * n : 32 * n + 32].hex().encode("ascii"),
                json_string(task.ids[starts[n] : ends[n]]),
                counted[n],
                chosen[n],
            )
        )
    return b"".join(rows)


# -- exact selection --------------------------------------------------------------------------


@dataclass
class AllocationResult:
    quota: int
    eligible_documents: int
    eligible_valid_targets: int
    selected_documents: int
    selected_valid_targets: int
    truncated_documents: int
    crossing_candidates: int  # rows fully sorted for this allocation (diagnostic only)

    def report(self) -> dict[str, Any]:
        """The reference ``_select_allocation`` report, field for field."""
        return {
            "quota": self.quota,
            "eligible_documents": self.eligible_documents,
            "eligible_valid_targets": self.eligible_valid_targets,
            "selected_documents": self.selected_documents,
            "selected_valid_targets": self.selected_valid_targets,
            "truncated_documents": self.truncated_documents,
            "deficit": self.quota - self.selected_valid_targets,
            "status": "EXACT" if self.selected_valid_targets == self.quota else "DEFICIT",
        }


def select_by_buckets(
    allocation: npt.NDArray[np.uint16],
    tokens: npt.NDArray[np.int64],
    prefix: npt.NDArray[np.uint64],
    quotas: Sequence[int],
    full_rank: Callable[[npt.NDArray[np.int64]], list[bytes]],
    *,
    bits: int = PREFIX_BITS,
    progress: RunProgress | NullProgress | None = None,
) -> tuple[npt.NDArray[np.int64], list[AllocationResult]]:
    """Exact per-allocation selection; returns selected valid targets per row and totals.

    Rows are in doc-id order (row index IS the reference tie-break). ``prefix`` holds the
    first 64 bits of each row's 256-bit rank, ``full_rank(rows)`` the full rank bytes.

    Reference (``_select_allocation``): walk the allocation's rows by (rank, doc id); skip
    zero-count rows; take ``min(count, quota - total)`` until ``total == quota``.

    Equivalence. Let ``E`` be the allocation's positive-count rows and ``b(r)`` the top
    ``bits`` bits of ``r``'s rank. ``b`` is monotone in the (rank, doc id) order, so every
    row of a lower bucket precedes every row of a higher one. Let ``C[j]`` be the summed
    counts of ``E`` with ``b <= j``. If ``C[last] <= quota`` the walk takes all of ``E``
    whole (each step has ``quota - total >= count``). Otherwise let ``j*`` be the first
    ``j`` with ``C[j] >= quota``. Rows below ``j*`` sum to ``C[j*-1] < quota``, so the walk
    takes each whole and has not stopped; rows above ``j*`` come after ``C[j*] >= quota``
    is reached inside ``j*``, so none is taken. Inside ``j*`` the walk is replayed exactly
    on (full rank, row index) with ``quota - C[j*-1] > 0`` remaining. Prefix collisions,
    and even equal full ranks, are ordered by that same key; nothing is approximated.
    """
    if not 1 <= bits <= 24:
        raise C05Error("rank prefix width outside its reviewed bounds")
    rows = len(tokens)
    if len(allocation) != rows or len(prefix) != rows:
        raise C05Error("selection columns are inconsistent")
    codes = len(quotas)
    buckets = 1 << bits
    eligible = tokens > 0
    bucket = (prefix >> np.uint64(64 - bits)).astype(np.int64)
    cell = allocation.astype(np.int64) * buckets + bucket
    sums = np.zeros(codes * buckets, dtype=np.int64)
    np.add.at(sums, cell[eligible], tokens[eligible])
    cumulative = np.cumsum(sums.reshape(codes, buckets), axis=1)
    documents = np.bincount(allocation[eligible], minlength=codes)
    threshold = np.full(codes, buckets, dtype=np.int64)  # buckets taken whole: b < threshold
    remaining = np.zeros(codes, dtype=np.int64)
    for code in range(codes):
        total = int(cumulative[code, -1])
        if total > quotas[code]:
            crossing = int(np.searchsorted(cumulative[code], quotas[code], side="left"))
            threshold[code] = crossing
            below = int(cumulative[code, crossing - 1]) if crossing else 0
            remaining[code] = quotas[code] - below
    selected = np.where(eligible & (bucket < threshold[allocation]), tokens, 0)
    crossing_cell = np.where(
        remaining > 0, np.arange(codes, dtype=np.int64) * buckets + threshold, -1
    )
    candidates = np.flatnonzero(eligible & (cell == crossing_cell[allocation]))
    by_code = allocation[candidates]
    counts = np.zeros(codes, dtype=np.int64)
    crossings = np.flatnonzero(remaining > 0).tolist()
    progress = progress or NullProgress()
    progress.update(codes, force=True)
    progress.stage("CROSSING SORT", len(crossings), "allocations")
    for done, code in enumerate(crossings, 1):
        rows_in = candidates[by_code == code]
        counts[code] = len(rows_in)
        ranks = full_rank(rows_in)
        order = sorted(range(len(rows_in)), key=lambda n: (ranks[n], int(rows_in[n])))
        left = int(remaining[code])
        for n in order:
            row = int(rows_in[n])
            used = min(int(tokens[row]), left)
            selected[row] = used
            left -= used
            if left == 0:
                break
        if left != 0:
            raise C05Error("rank bucket sums are inconsistent")
        progress.update(done, crossing_rows=int(counts.sum()))
    chosen = selected > 0
    truncated = chosen & (selected < tokens)
    eligible_targets = np.zeros(codes, dtype=np.int64)
    np.add.at(eligible_targets, allocation[eligible], tokens[eligible])
    selected_targets = np.zeros(codes, dtype=np.int64)
    np.add.at(selected_targets, allocation[chosen], selected[chosen])
    selected_documents = np.bincount(allocation[chosen], minlength=codes)
    truncated_documents = np.bincount(allocation[truncated], minlength=codes)
    results = [
        AllocationResult(
            quota=int(quotas[code]),
            eligible_documents=int(documents[code]),
            eligible_valid_targets=int(eligible_targets[code]),
            selected_documents=int(selected_documents[code]),
            selected_valid_targets=int(selected_targets[code]),
            truncated_documents=int(truncated_documents[code]),
            crossing_candidates=int(counts[code]),
        )
        for code in range(codes)
    ]
    return selected, results


# -- parent: membership columns ---------------------------------------------------------------


def _membership_id(m: Membership, position: int) -> bytes:
    return m.ids[int(m.id_offsets[position]) : int(m.id_offsets[position + 1])]


def _find(m: Membership, raw: bytes) -> int | None:
    """Membership position of a doc id (membership is strictly ascending), else None."""
    low, high = 0, m.rows
    while low < high:
        middle = (low + high) // 2
        if _membership_id(m, middle) < raw:
            low = middle + 1
        else:
            high = middle
    return low if low < m.rows and _membership_id(m, low) == raw else None


def _id_refusal(m: Membership, found: bytes, expected: int) -> C05Error:
    """Fixed-literal reason why a count row's doc id is not the expected train record."""
    position = _find(m, found)
    if position is None:
        return C05Error("record is not covered by C05 kept membership")
    if m.split[position] != 0:
        return C05Error("non-training record in exact counts")
    if found < _membership_id(m, expected):
        return C05Error("repeated or out-of-order record in exact counts")
    return C05Error("exact counts omit or reorder a kept training record")


@dataclass
class _CountsState:
    sha: Any
    bytes: int = 0


def _count_blocks(path: Path, expected: int, state: _CountsState, block: int) -> Iterator[bytes]:
    """Complete count lines in order; every byte read is hashed; never past the signed size."""
    pending = b""
    with path.open("rb", buffering=0) as stream:
        while data := stream.read(min(block, expected - state.bytes + 1)):
            state.sha.update(data)
            state.bytes += len(data)
            if state.bytes > expected:
                raise C05Error("exact count artifact changed")
            data = pending + data if pending else data
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
            else:
                pending = data[cut:]
                yield data[:cut]
            if len(pending) > COUNT_LINE_CEILING:
                raise C05Error("count record ceiling")
    if pending:
        yield pending


def _nth_line_end(data: bytes, lines: int) -> int:
    """Byte length of the first ``lines`` lines of ``data``."""
    end = 0
    for _ in range(lines):
        end = data.index(b"\n", end) + 1
    return end


def rank_pass(
    path: Path,
    counts: Mapping[str, Any],
    m: Membership,
    train: npt.NDArray[np.int64],
    pool: OrderedPool,
    progress: RunProgress | NullProgress,
    *,
    block_bytes: int = 0,
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.uint64]]:
    """One hashed sequential pass over counts.jsonl, verified and ranked in parallel."""
    expected_bytes = int(counts["counts_bytes"])
    total = len(train)
    tokens = np.zeros(total, dtype=np.int64)
    prefix = np.zeros(total, dtype=np.uint64)
    state = _CountsState(hashlib.sha256())
    overflow = [False]
    submitted = [0]
    capacity = 2 * pool.workers
    progress.stage("RANK PASS", total, "rows")

    def tasks() -> Iterator[RankTask]:
        cursor = 0
        for data in _count_blocks(path, expected_bytes, state, block_bytes or RANK_BLOCK_BYTES):
            final = not data.endswith(b"\n")
            rows = data.count(b"\n") + int(final)
            if cursor + rows > total:
                # Rows beyond kept train membership: rank the in-range ones first so an
                # earlier mismatch is reported precisely, then refuse.
                overflow[0] = True
                rows = total - cursor
                if rows == 0:
                    return
                data, final = data[: _nth_line_end(data, rows)], False
            positions = train[cursor : cursor + rows]
            start, end = int(positions[0]), int(positions[-1]) + 1
            low, high = int(m.id_offsets[start]), int(m.id_offsets[end])
            yield RankTask(
                first=cursor,
                rows=rows,
                data=data,
                final=final,
                ids=m.ids[low:high],
                id_offsets=m.id_offsets[start : end + 1].astype(np.int64) - low,
                content=np.ascontiguousarray(m.content[start:end]).tobytes(),
                allocation=np.ascontiguousarray(m.allocation[start:end]),
                train=positions - start,
            )
            submitted[0] += 1
            cursor += rows
            if overflow[0]:
                return

    done = results = 0
    started = time.monotonic()
    for result in pool.map(rank_chunk, tasks(), capacity):
        results += 1
        if (result.first, len(result.tokens)) != (done, result.rows):
            raise C05Error("count rank result differs from its task")
        if result.mismatch >= 0:
            raise _id_refusal(m, result.found, int(train[done + result.mismatch]))
        if len(result.prefix) != result.rows:
            raise C05Error("count rank result differs from its task")
        tokens[done : done + result.rows] = result.tokens
        prefix[done : done + result.rows] = result.prefix
        done += result.rows
        elapsed = max(time.monotonic() - started, 1e-9)
        progress.update(
            done,
            bytes_done=state.bytes,
            bytes_total=expected_bytes,
            mib_per_s=state.bytes / MiB / elapsed,
            workers=pool.workers,
            busy=min(pool.workers, submitted[0] - results),
            tasks=submitted[0] - results,
            capacity=capacity,
            results=results,
        )
    if overflow[0]:
        raise C05Error("exact counts contain records beyond kept training membership")
    # Authenticate before any parsed value is used.
    if state.bytes != expected_bytes or state.sha.hexdigest() != counts["counts_sha256"]:
        raise C05Error("exact count artifact changed")
    if done != counts["documents"] or done != total:
        raise C05Error("exact counts do not cover every kept training record")
    return tokens, prefix


def _full_ranks(
    m: Membership, train: npt.NDArray[np.int64], heads: Sequence[bytes], prefix: Any
) -> Callable[[npt.NDArray[np.int64]], list[bytes]]:
    """Full ranks of train ordinals, recomputed from membership; must match the pass."""

    def ranks(ordinals: npt.NDArray[np.int64]) -> list[bytes]:
        values: list[bytes] = []
        for ordinal in ordinals.tolist():
            position = int(train[ordinal])
            digest = rank_digest(
                heads[int(m.allocation[position])],
                json_string(_membership_id(m, position)),
                m.content[position].tobytes().hex().encode("ascii"),
            )
            if int.from_bytes(digest[:8], "big") != int(prefix[ordinal]):
                raise C05Error("selection rank differs between passes")
            values.append(digest)
        return values

    return ranks


# -- parent: export, verification, publication ---------------------------------------------


def export_lower_bound(
    m: Membership,
    positions: npt.NDArray[np.int64],
    counted: npt.NDArray[np.int64],
    chosen: npt.NDArray[np.int64],
    fragments: Sequence[bytes],
) -> int:
    """Smallest possible ``selected.jsonl`` size (unescaped ids): exact for plain ids."""
    if len(positions) == 0:
        return 0
    powers = np.asarray([10**n for n in range(1, MAX_COUNT_DIGITS + 1)], dtype=np.int64)

    def digits(values: npt.NDArray[np.int64]) -> int:
        return int((np.searchsorted(powers, values, side="right") + 1).sum())

    sizes = np.asarray([len(f) for f in fragments], dtype=np.int64)
    id_bytes = (m.id_offsets[positions + 1] - m.id_offsets[positions]).astype(np.int64)
    return int(
        SELECTED_FIXED_BYTES * len(positions)
        + sizes[m.allocation[positions]].sum()
        + id_bytes.sum()
        + digits(counted)
        + digits(chosen)
    )


def export_selection(
    path: Path,
    m: Membership,
    positions: npt.NDArray[np.int64],
    counted: npt.NDArray[np.int64],
    chosen: npt.NDArray[np.int64],
    pool: OrderedPool,
    ceiling: int,
    progress: RunProgress | NullProgress,
    *,
    rows_per_task: int = 0,
) -> tuple[str, int]:
    """Selected rows in membership (doc-id byte) order, serialized in parallel; one fsync."""
    total = len(positions)
    step = rows_per_task or EXPORT_ROWS
    progress.stage("EXPORT SELECTION", total, "rows")

    submitted = [0]

    def tasks() -> Iterator[ExportTask]:
        for start in range(0, total, step):
            submitted[0] += 1
            chunk = positions[start : start + step]
            starts = m.id_offsets[chunk].tolist()
            ends = m.id_offsets[chunk + 1].tolist()
            yield ExportTask(
                ids=b"".join(m.ids[s:e] for s, e in zip(starts, ends, strict=True)),
                id_lengths=np.asarray(ends, dtype=np.int64) - np.asarray(starts, dtype=np.int64),
                content=np.ascontiguousarray(m.content[chunk]).tobytes(),
                allocation=np.ascontiguousarray(m.allocation[chunk]),
                counted=counted[start : start + step],
                chosen=chosen[start : start + step],
            )

    digest = hashlib.sha256()
    written = done = results = 0
    capacity = 2 * pool.workers
    with path.open("xb") as stream:
        for block in pool.map(export_chunk, tasks(), capacity):
            results += 1
            written += len(block)
            if written > ceiling:
                raise C05Error("selection artifact output ceiling")
            stream.write(block)
            digest.update(block)
            done = min(total, done + step)
            progress.update(
                done,
                written_bytes=written,
                workers=pool.workers,
                busy=min(pool.workers, submitted[0] - results),
                tasks=submitted[0] - results,
                capacity=capacity,
                results=results,
            )
        progress.stage("FSYNC", None, "steps")
        stream.flush()
        os.fsync(stream.fileno())
    return digest.hexdigest(), written


def _file_sha(path: Path, progress: RunProgress | NullProgress, size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            total += len(block)
            progress.update(total, bytes_done=total, bytes_total=size)
    return digest.hexdigest(), total


def _tokenizer_files_digest(directory: Path) -> str:
    """The reference ``files_digest`` (every artifact file, bounded)."""
    entries = sorted(directory.iterdir())
    if len(entries) > MAX_TOKENIZER_FILES:
        raise C05Error("tokenizer artifact file ceiling")
    files: dict[str, str] = {}
    for entry in entries:
        if not entry.is_file() or entry.stat().st_size > MAX_TOKENIZER_FILE_BYTES:
            raise C05Error("tokenizer artifact entry is not a bounded regular file")
        files[entry.name] = file_sha(entry)
    return canonical.digest(files)


def verify_counts_envelope(
    directory: Path, view: StreamedC05, identity: Mapping[str, Any]
) -> dict[str, Any]:
    """The reference ``verify_counts`` except the content hash (taken in the rank pass)."""
    envelope = read_metadata(directory / "counts.json", digested=False)
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, "c05_exact_token_counts_v1")
    if body.get("rule") != COUNT_RULE or body.get("tokenizer") != dict(identity):
        raise C05Error("exact counts use a different tokenizer or counting rule")
    size, sha, documents = (
        body.get("counts_bytes"),
        body.get("counts_sha256"),
        body.get("documents"),
    )
    if type(size) is not int or type(documents) is not int or type(sha) is not str:
        raise C05Error("exact count artifact changed")
    if (directory / "counts.jsonl").stat().st_size != size:
        raise C05Error("exact count artifact changed")
    return envelope


def preflight_deficit_report(path: Path | None) -> None:
    """A deficit report must be writable later: plain path, absent, no file in its ancestry."""
    if path is None:
        return
    ensure_plain_path(path)
    if path.exists():
        raise C05Error("deficit report already exists (write-once)")
    ancestor = path.parent
    while not ancestor.exists() and ancestor.parent != ancestor:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise C05Error("deficit report parent is not a directory")


def preflight_scratch(scratch: Path, owned: OwnedPaths) -> None:
    """Scratch exists and accepts a content-free write+fsync (nothing else uses it)."""
    scratch.mkdir(parents=True, exist_ok=True)
    probe = owned.file(scratch / f"select-preflight-{uuid.uuid4().hex}.probe")
    with probe.open("xb") as stream:
        stream.write(b"select preflight\n")
        stream.flush()
        os.fsync(stream.fileno())
    probe.unlink()


class _Reporter(FitReporter):
    """Adapter for the shared membership stream: SELECT stage names and notes."""

    NAMES = {"MEMBERSHIP STREAM": "MEMBERSHIP VERIFY"}

    def __init__(self, progress: RunProgress | NullProgress) -> None:
        self.progress = progress

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        self.progress.stage(self.NAMES.get(name, name), total, unit)

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        self.progress.update(done, force=force, **fields)

    def note(self, text: str) -> None:
        """Fixed explanatory line; callers pass literals and numbers only."""
        if isinstance(self.progress, RunProgress):
            line = (
                json.dumps({"event": "note", "label": LABEL, "text": text}, sort_keys=True)
                if self.progress.fmt == "jsonl"
                else f"[{LABEL}] {text}"
            )
            print(line, file=self.progress.stream or sys.stderr, flush=True)


def _check_roots(
    view: StreamedC05, tokenizer_dir: Path, counts_dir: Path, scratch: Path, output: Path
) -> None:
    """Scratch and output never overlap the corpus, C05 output, tokenizer, counts or each other."""
    from xlm.data.exclusion.isolation import overlaps

    immutable = (
        ("C05 data root", view.plan.data_root),
        ("C05 completion", str(view.directory)),
        ("tokenizer", str(tokenizer_dir)),
        ("exact counts", str(counts_dir)),
    )
    for name, path in (("selection scratch", scratch), ("selection output", output)):
        for role, other in immutable:
            if overlaps(path, other):
                raise C05Error(f"{name} overlaps the {role}")
    if overlaps(scratch, output):
        raise C05Error("selection scratch overlaps the selection output")


def select_fast(
    proof: Path,
    counts_dir: Path,
    tokenizer_dir: Path,
    quotas: Path,
    ifm_split: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    scratch: Path,
    workers: int,
    deficit_report: Path | None = None,
    progress: RunProgress | NullProgress | None = None,
    consumes: list[Path | str] | None = None,
    policy: SelectionPolicy | None = None,
    inline: bool = False,
    allow_authored: bool = True,
    block_bytes: int = 0,
    export_rows: int = 0,
    prefix_bits: int = PREFIX_BITS,
) -> dict[str, Any]:
    """Select exactly each frozen allocation quota (see module doc); returns the envelope.

    Raises :class:`selection.SelectionDeficit` (nothing published) like the reference.
    """
    if workers not in WORKER_CHOICES:
        raise C05Error("selection workers must be 1, 2, 4, 8 or 16")
    policy = policy or SelectionPolicy()
    progress = progress or NullProgress()
    current = ["PROOF VERIFY"]

    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        current[0] = name
        progress.stage(name, total, unit)

    owned = OwnedPaths()
    started = time.monotonic()
    try:
        begin("PROOF VERIFY")
        view = open_streamed(
            proof,
            allow_authored=allow_authored,
            consumes=[tokenizer_dir, scratch, output, counts_dir, *(consumes or [])],
        )
        if view.trusted.get(issuer) != key:
            raise C05Error("allocation signer is not trusted")
        _check_roots(view, tokenizer_dir, counts_dir, scratch, output)
        if output.exists():
            raise C05Error("selection artifacts are write-once")
        stage = output.with_name(output.name + f".partial-{uuid.uuid4().hex}")
        begin("OUTPUT PREFLIGHT")
        preflight_deficit_report(deficit_report)
        preflight_output(stage, owned, names=ARTIFACTS, probe=b"select preflight\n")
        preflight_scratch(scratch, owned)
        begin("TOKENIZER VERIFY")
        _, identity = tokenizer_identity(tokenizer_dir, view)
        begin("COUNTS VERIFY")
        counts_envelope = verify_counts_envelope(counts_dir, view, identity)
        counts: dict[str, Any] = counts_envelope["payload"]
        counts_path = counts_dir / "counts.jsonl"
        counts_stat = counts_path.stat()
        requirements = view_requirements(view, quotas, ifm_split)
        if identity["vocab_size"] != requirements["tokenizer_vocab_size"]:
            raise C05Error("tokenizer vocabulary differs from the frozen quota table")
        quota_of: dict[str, int] = requirements["allocations"]
        membership_tables, keys = count_tables(view)
        position = {k: n for n, k in enumerate(keys)}
        fragments = tuple(canonical.canonical_bytes(canonical.loads_strict(k)) for k in keys)
        tables = SelectTables(membership_tables, fragments, rank_heads(policy.seed, keys))
        supervisor = Supervisor(Deadline(None, started), view.plan.resources.ram_bytes)
        with supervisor:
            if isinstance(progress, RunProgress):
                progress.attach(_Telemetry(supervisor, output, 0))
            with OrderedPool(
                workers, tables, supervisor, inline=inline, initializer=init_select_worker
            ) as pool:
                current[0] = "MEMBERSHIP VERIFY"
                m = stream_membership(
                    view, membership_tables, pool, _Reporter(progress), supervisor, {}
                )
                reconcile(view, m, keys)
                train = np.flatnonzero(m.split == 0).astype(np.int64)
                allocation = np.ascontiguousarray(m.allocation[train])
                for code in np.unique(allocation).tolist():
                    if keys[code] not in quota_of:
                        raise C05Error("count outside the frozen allocations")
                current[0] = "RANK PASS"
                tokens, prefix = rank_pass(
                    counts_path, counts, m, train, pool, progress, block_bytes=block_bytes
                )
                supervisor.check()
                begin("QUOTA CROSSINGS", len(keys), "allocations")
                chosen, results = select_by_buckets(
                    allocation,
                    tokens,
                    prefix,
                    [quota_of.get(k, 0) for k in keys],
                    _full_ranks(m, train, tables.heads, prefix),
                    bits=prefix_bits,
                    progress=progress,
                )
                current[0] = "CROSSING SORT"
                report: dict[str, dict[str, Any]] = {}
                for name in sorted(quota_of):
                    if name in position:
                        report[name] = results[position[name]].report()
                    else:  # A frozen allocation with no kept train row at all.
                        report[name] = AllocationResult(quota_of[name], 0, 0, 0, 0, 0, 0).report()
                selected_documents = sum(r["selected_documents"] for r in report.values())
                progress.update(
                    force=True,
                    selected_docs=selected_documents,
                    truncated_docs=sum(r["truncated_documents"] for r in report.values()),
                    crossing_rows=sum(r.crossing_candidates for r in results),
                )
                if any(r["status"] != "EXACT" for r in report.values()):
                    raise SelectionDeficit(
                        {
                            "kind": "c05_selection_deficit_v1",
                            **binding_of(view),
                            "counts_digest": counts_envelope["digest"],
                            "quota_sha256": requirements["quota_sha256"],
                            "allocations": report,
                            "rule": "same allocation only; separate acquisition authorization "
                            "and renewed global C05 required",
                        }
                    )
                components: dict[str, dict[str, int]] = {}
                for name, row in report.items():
                    component = canonical.loads_strict(name)[0]
                    total = components.setdefault(
                        component, {"quota": 0, "selected_valid_targets": 0}
                    )
                    total["quota"] += row["quota"]
                    total["selected_valid_targets"] += row["selected_valid_targets"]
                if {c: v["quota"] for c, v in components.items()} != requirements["final_quotas"]:
                    raise C05Error("selected component totals differ from frozen quotas")
                picked = np.flatnonzero(chosen > 0)
                positions = train[picked]
                counted, kept = tokens[picked], chosen[picked]
                ceiling = view.plan.resources.output_bytes
                needed = export_lower_bound(m, positions, counted, kept, fragments)
                if needed > ceiling:
                    raise C05Error("selection artifact output ceiling")
                if shutil.disk_usage(stage).free < needed:
                    raise C05Error("output volume lacks space for the selection")
                current[0] = "EXPORT SELECTION"
                selected_path = owned.file(stage / "selected.jsonl")
                sha, size = export_selection(
                    selected_path,
                    m,
                    positions,
                    counted,
                    kept,
                    pool,
                    ceiling,
                    progress,
                    rows_per_task=export_rows,
                )
            supervisor.check()
            envelope = signed(
                {
                    "kind": "c05_selected_pool_v1",
                    **binding_of(view),
                    "counts_digest": counts_envelope["digest"],
                    "counts_sha256": counts["counts_sha256"],
                    "tokenizer": identity,
                    "rule": COUNT_RULE,
                    "quota_sha256": requirements["quota_sha256"],
                    "ifm_split_digest": requirements["ifm_split_digest"],
                    "common_pile_split_digest": requirements["common_pile_split_digest"],
                    "requirements_digest": canonical.digest(requirements),
                    "selection_policy": policy.model_dump(mode="json"),
                    "selection_policy_digest": policy.identity(),
                    "allocations": report,
                    "components": dict(sorted(components.items())),
                    "selected_documents": selected_documents,
                    "selected_valid_targets": requirements["valid_target_quota"],
                    "valid_target_quota": requirements["valid_target_quota"],
                    "selected_membership_sha256": sha,
                    "selected_membership_bytes": size,
                },
                issuer,
                key,
            )
            begin("SIGN")
            write_once(owned.file(stage / "selection.json"), envelope)
            begin("VERIFY", size, "bytes")
            if _file_sha(selected_path, progress, size) != (sha, size):
                raise C05Error("selected training membership changed")
            verify_signed(envelope, view.trusted)
            now = counts_path.stat()
            if (now.st_size, now.st_mtime_ns) != (counts_stat.st_size, counts_stat.st_mtime_ns):
                raise C05Error("exact count artifact changed during selection")
            if _tokenizer_files_digest(tokenizer_dir) != identity["files_digest"]:
                raise C05Error("tokenizer changed during selection")
            supervisor.check()
            begin("PUBLISH")
            if output.exists():
                raise C05Error("selection artifacts are write-once")
            os.rename(stage, output)  # The staged names are gone; cleanup skips them.
        progress.complete()
        return envelope
    except Exception as exc:
        # A fixed stage literal for the content-free CLI refusal (no paths or values).
        setattr(exc, "select_stage", current[0])  # noqa: B010
        raise
    finally:
        owned.cleanup()
