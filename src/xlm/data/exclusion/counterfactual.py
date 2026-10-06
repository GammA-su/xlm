"""Read-only, content-free C05 contamination-policy counterfactual: matcher x lineage.

Stage 1 of the policy audit (operator; the protected volume is attached). It reads the
finished C05 run and changes nothing:

* the C05 plan, the signed group seal and its group arrays (``dup``/``fam``/``survivor``
  /family orderings), every fact unit, the private decision ledger;
* the protected index (``index.jsonl``) and the content-free benchmark receipt, to give
  every frozen pattern its features, provenance kinds and benchmark items;
* the published compiled matcher (opened read-only, every file re-hashed);
* the cleaned corpus: every production direct-hit document of the rescanned allocations
  (all exact occurrences, all candidates) and every SYNTH member of a lineage family that
  could change (its lineage keys, recomputed with and without ``additional_seed_url``);
* optionally the protected benchmark material (injected-copy recall, render slots).

Matchers are :data:`candidates.CANDIDATES`. Lineage policies:

* ``current_transitive`` (A): C05 known-lineage-v3 as run: one undirected transitive
  closure over duplicates, parents and every lineage key, both seed-URL fields included;
* ``query_seed_family`` (B): the same closure with ``additional_seed_url`` ignored, so
  ``query_seed_url`` (plus duplicates, parents and every other key) defines the family;
* ``query_seed_one_hop`` (C): B families; a hit family also excludes every B family that
  shares an ``additional_seed_url`` link with it (one hop), never further.

Split-integrity grouping is a different question: B and C here only change which
families are EXCLUDED; the report keeps A's grouping statistics for split leakage.

Self-checks (refusals are content-free): the current matcher with lineage A must
reproduce every ledger decision and split; the rebuilt lineage graph must reproduce the
production family of every rebuilt document; every rescanned document must reproduce its
recorded first hit; recomputed lineage keys must equal the fact units'; every corpus file
read must equal the plan (size, SHA-256, rows); the protected index must equal the
receipt (SHA-256, bytes) and the compiled matcher's pattern set.

stdout is only the JSON report; ``[POLICY AUDIT]`` progress goes to stderr. With
``--state-out`` the per-document train membership changes (plan file and row only, no
text) are written for stage 2 (:mod:`xlm.data.exclusion.supply`), which must run with
the protected volume detached.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.lineage import lineage_keys_v3
from xlm.data.dedup.matchview import match_normalize, match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import candidates as cand
from xlm.data.exclusion.artifacts import ExecutionPlan, MaterialFile
from xlm.data.exclusion.factstore import FACTS_DIR, GROUP_DIR, open_unit
from xlm.data.exclusion.fitscan import OrderedPool
from xlm.data.exclusion.forensics import Fingerprint, Telemetry, blocks, key_of
from xlm.data.exclusion.grouping import components, verify_group_files
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.supervisor import Deadline, Supervisor

LABEL = "POLICY AUDIT"
WORKER_CHOICES = (1, 2, 4, 8, 16)
LINEAGES = ("current_transitive", "query_seed_family", "query_seed_one_hop")
DECISIONS = ("kept", "duplicate", "excluded")
SPLITS = ("train", "diagnostic_val", "audit")
KEPT, DUPLICATE, EXCLUDED = 0, 1, 2
ADDITIONAL = "additional_seed_url"
SYNTH_SOURCE = "synth"
RESCAN, ATTRIBUTE = 1, 2
STATE_KIND = "c05_policy_counterfactual_state_v1"
SEAL_NAME = "seal.json"
MATCHER_NAME = "matcher"
MAX_OCCURRENCES = 5_000_000  # per document; refuses rather than truncating evidence
ALLOWED_HEX_FIELDS = frozenset(
    {"plan_digest", "plan_code_commit", "completion_digest", "index_sha256", "candidate_digest"}
)
LABEL_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")

Progress = RunProgress | NullProgress
U8 = npt.NDArray[np.uint8]
I64 = npt.NDArray[np.int64]


class AuditError(Exception):
    """A refusal with a fixed, content-free reason."""


def combo_name(candidate: str, lineage: str) -> str:
    return f"{candidate}|{lineage}"


COMBOS: tuple[tuple[int, int], ...] = tuple(
    (c, lineage) for c in range(len(cand.CANDIDATES)) for lineage in range(len(LINEAGES))
)


# -- worker side ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tables:
    files: dict[str, int] | None = None  # plan path -> ordinal
    allocations: dict[str, int] | None = None  # allocation key -> code
    refs: dict[str, int] | None = None  # provenance reference -> benchmark item
    encoded: list[bytes] | None = None  # canonical JSON of each matcher vocabulary word
    matcher: str | None = None
    index_sha256: str = ""
    index_bytes: int = 0
    standalone: U8 | None = None  # per compiled pattern: bit c = triggers alone under c
    short: tuple[cand.ShortItems | None, ...] = ()
    material: dict[int, tuple[str, str]] | None = None  # material file -> (task, group)
    material_base: tuple[int, ...] = ()  # first item number of each material file
    watched: frozenset[str] = frozenset()  # hex identities of reported top patterns


_TABLES: Tables | None = None
_MATCHER: cand.OccurrenceMatcher | None = None


def init_worker(tables: Tables) -> None:
    global _TABLES, _MATCHER
    _TABLES, _MATCHER = tables, None
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"
    if tables.matcher is not None:
        _MATCHER = cand.OccurrenceMatcher(
            Path(tables.matcher), index_sha256=tables.index_sha256, index_bytes=tables.index_bytes
        )


def _tables() -> Tables:
    if _TABLES is None:
        raise AuditError("policy audit worker tables missing")
    return _TABLES


def _matcher() -> cand.OccurrenceMatcher:
    if _MATCHER is None:
        raise AuditError("policy audit worker matcher missing")
    return _MATCHER


# ledger ------------------------------------------------------------------------------------

LEDGER_KEYS = frozenset(
    {
        "doc_id",
        "source_id",
        "component",
        "view",
        "file",
        "row",
        "content",
        "bytes",
        "duplicate_group",
        "lineage_group",
        "decision",
        "split",
        "quick",
        "upstream_component",
    }
)


@dataclass
class LedgerPart:
    rows: int
    file: npt.NDArray[np.int32]
    row: npt.NDArray[np.int32]  # 0-based
    decision: npt.NDArray[np.int8]
    split: npt.NDArray[np.int8]
    allocation: npt.NDArray[np.int16]
    nbytes: I64


def parse_ledger(block: bytes) -> LedgerPart:
    tables = _tables()
    assert tables.files is not None and tables.allocations is not None
    lines = block.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    n = len(lines)
    file = np.empty(n, np.int32)
    row = np.empty(n, np.int32)
    decision = np.empty(n, np.int8)
    split = np.empty(n, np.int8)
    allocation = np.empty(n, np.int16)
    nbytes = np.empty(n, np.int64)
    decisions = {name: k for k, name in enumerate(DECISIONS)}
    splits = {name: k for k, name in enumerate(SPLITS)}
    cache: dict[tuple[Any, Any, Any], int] = {}
    for k, line in enumerate(lines):
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise AuditError("decision ledger record is not JSON") from exc
        if type(value) is not dict or value.keys() != LEDGER_KEYS:
            raise AuditError("decision ledger record schema")
        code = decisions.get(value["decision"]) if type(value["decision"]) is str else None
        part = splits.get(value["split"]) if type(value["split"]) is str else None
        size, number, path = value["bytes"], value["row"], value["file"]
        if (
            code is None
            or part is None
            or type(size) is not int
            or size < 0
            or type(number) is not int
            or number < 1
            or type(path) is not str
        ):
            raise AuditError("decision ledger record value")
        ordinal = tables.files.get(path)
        if ordinal is None:
            raise AuditError("decision ledger names a file outside the C05 plan")
        label = (value["component"], value["view"], value["upstream_component"])
        alloc = cache.get(label)
        if alloc is None:
            if not all(type(x) is str for x in label[:2]) or (
                label[2] is not None and type(label[2]) is not str
            ):
                raise AuditError("decision ledger record value")
            found = tables.allocations.get(key_of(*label))
            if found is None:
                raise AuditError("decision ledger allocation outside the C05 plan")
            alloc = cache[label] = found
        file[k], row[k], decision[k], split[k], allocation[k], nbytes[k] = (
            ordinal,
            number - 1,
            code,
            part,
            alloc,
            size,
        )
    return LedgerPart(n, file, row, decision, split, allocation, nbytes)


# protected index ---------------------------------------------------------------------------


@dataclass
class IndexPart:
    identity: bytes  # 32 bytes per provenance row
    length: npt.NDArray[np.int32]
    characters: npt.NDArray[np.int32]
    distinct: npt.NDArray[np.int32]
    kind: U8
    item: I64


def parse_index(block: bytes) -> IndexPart:
    tables = _tables()
    assert tables.refs is not None
    identity = bytearray()
    length: list[int] = []
    characters: list[int] = []
    distinct: list[int] = []
    kinds: list[int] = []
    items: list[int] = []
    for line in block.split(b"\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise AuditError("protected index record is not JSON") from exc
        if type(entry) is not dict or set(entry) != {"tokens", "provenance"}:
            raise AuditError("protected index record schema")
        tokens, provenance = entry["tokens"], entry["provenance"]
        if (
            type(tokens) is not list
            or not tokens
            or any(type(t) is not str or not t for t in tokens)
            or type(provenance) is not list
            or not provenance
            or any(type(p) is not str for p in provenance)
        ):
            raise AuditError("protected index record values")
        digest = hashlib.sha256(canonical.canonical_bytes(tokens)).digest()
        size, chars, unique = cand.features(tokens)
        for reference in provenance:
            ref, _, kind = reference.rpartition(":")
            bit = cand.KIND_BIT.get(kind)
            item = tables.refs.get(ref)
            if bit is None:
                raise AuditError("protected index provenance kind is unknown")
            if item is None:
                raise AuditError("protected index provenance is not in the benchmark receipt")
            identity += digest
            length.append(size)
            characters.append(chars)
            distinct.append(unique)
            kinds.append(bit)
            items.append(item)
    return IndexPart(
        bytes(identity),
        np.asarray(length, np.int32),
        np.asarray(characters, np.int32),
        np.asarray(distinct, np.int32),
        np.asarray(kinds, np.uint8),
        np.asarray(items, np.int64),
    )


def identity_chunk(task: tuple[npt.NDArray[np.uint32], I64]) -> bytes:
    tables = _tables()
    assert tables.encoded is not None
    tokens, offsets = task
    return cand.identities(tables.encoded, tokens, offsets)


# corpus ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineSpan:
    """Wanted lines of one block as a byte span that the worker re-reads itself.

    Shipping line bytes through Windows multiprocessing pipes caps throughput at
    roughly 150-200 MB/s; the span was just read (and hashed) by the parent, so the
    worker's read is served from the OS page cache. Size and modification time must
    still equal what the parent saw (the count-tokens fast-path rule).
    """

    path: str
    size: int
    mtime_ns: int
    offset: int
    starts: tuple[int, ...]  # line starts relative to ``offset``
    ends: tuple[int, ...]

    def lines(self) -> list[bytes]:
        with open(self.path, "rb") as stream:
            status = os.fstat(stream.fileno())
            if (status.st_size, status.st_mtime_ns) != (self.size, self.mtime_ns):
                raise AuditError("corpus file changed while it was being read")
            stream.seek(self.offset)
            data = stream.read(self.ends[-1])
        if len(data) != self.ends[-1]:
            raise AuditError("corpus file changed while it was being read")
        return [data[a:b] for a, b in zip(self.starts, self.ends, strict=True)]


@dataclass(frozen=True)
class CorpusTask:
    span: LineSpan  # the wanted complete canonical lines of one plan file
    dense: tuple[int, ...]  # C05 dense document number of each line
    modes: tuple[int, ...]
    recorded: tuple[bytes, ...]  # 32-byte recorded first hit (rescan rows), else b""
    source_id: str
    revision: str


@dataclass
class TaskOut:
    """Per-task arrays, integrated by the parent in bulk (never one call per document)."""

    rescanned: I64  # dense numbers of rescanned documents
    bits: I64  # candidate hit bitmask of each
    longest: I64  # longest occurring pattern of each, tokens
    df_owner: I64  # index into ``rescanned`` of each distinct occurring pattern
    df_pattern: I64  # compiled pattern index
    key_dense: I64  # recomputed lineage key digests (attributed rows)
    key_words: bytes
    drop_dense: I64  # digests of keys only additional_seed_url produces
    drop_words: bytes
    attributed: int


def canonical_document(line: bytes, source_id: str, revision: str) -> CanonicalDocument:
    try:
        value = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise AuditError("canonical record is not strict canonical JSON") from exc
    if type(value) is not dict:
        raise AuditError("canonical record must be an object")
    try:
        doc = CanonicalDocument(**value)
    except (TypeError, ValueError) as exc:
        raise AuditError("canonical record is invalid") from exc
    if not isinstance(doc.text, str) or doc.source_id != source_id:
        raise AuditError("canonical record differs from its plan file")
    if doc.source_revision != revision or not isinstance(doc.source_metadata, dict):
        raise AuditError("canonical record differs from its plan file")
    return doc


def wanted_span(
    data: bytes, first: int, offset: int, wanted: npt.NDArray[np.int64]
) -> tuple[int, list[int], int, list[int], list[int]]:
    """Locate the wanted rows of one block of complete lines starting at byte ``offset``.

    Returns (lines in the block, wanted absolute rows in it, span offset, line starts
    and ends relative to the span). ``wanted`` is sorted.
    """
    newline = np.flatnonzero(np.frombuffer(data, dtype=np.uint8) == 10)
    count = int(newline.size) + (0 if data.endswith(b"\n") else 1)
    lo, hi = np.searchsorted(wanted, [first, first + count])
    rows = wanted[int(lo) : int(hi)]
    if not rows.size:
        return count, [], offset, [], []
    local = rows - first
    starts = np.r_[0, newline + 1][local]
    ends = np.r_[newline, len(data)][local]
    base = int(starts[0])
    return (
        count,
        rows.tolist(),
        offset + base,
        (starts - base).tolist(),
        (ends - base).tolist(),
    )


def scan_corpus(task: CorpusTask) -> TaskOut:
    tables = _tables()
    rescanned: list[int] = []
    bits: list[int] = []
    longest: list[int] = []
    df_owner: list[I64] = []
    df_pattern: list[I64] = []
    key_dense: list[int] = []
    key_words: list[bytes] = []
    drop_dense: list[int] = []
    drop_words: list[bytes] = []
    attributed = 0
    for dense, line, mode, recorded in zip(
        task.dense, task.span.lines(), task.modes, task.recorded, strict=True
    ):
        doc = canonical_document(line, task.source_id, task.revision)
        if mode & RESCAN:
            matcher = _matcher()
            assert tables.standalone is not None
            normalized = match_normalize(doc.text)
            tokens = normalized.split(" ") if normalized else []
            pattern, start, end = matcher.occurrences(tokens)
            if not pattern.size:
                raise AuditError("rescan found no occurrence for a recorded C05 hit")
            if pattern.size > MAX_OCCURRENCES:
                raise AuditError("rescan occurrence ceiling")
            if bytes.fromhex(matcher.identity(int(pattern[0]))) != recorded:
                raise AuditError("rescan first hit differs from the recorded C05 hit")
            distinct = np.unique(pattern)
            df_owner.append(np.full(distinct.size, len(rescanned), np.int64))
            df_pattern.append(distinct)
            rescanned.append(dense)
            bits.append(cand.candidate_hits(pattern, start, end, tables.standalone, tables.short))
            longest.append(int((end - start).max()))
        if mode & ATTRIBUTE:
            attributed += 1
            full = lineage_keys_v3(doc)
            metadata = dict(doc.source_metadata)
            reduced = full
            if ADDITIONAL in metadata:
                del metadata[ADDITIONAL]
                reduced = lineage_keys_v3(replace(doc, source_metadata=metadata))
            for key in full:
                key_dense.append(dense)
                key_words.append(hashlib.sha256(key.encode()).digest())
            for key in set(full) - set(reduced):
                drop_dense.append(dense)
                drop_words.append(hashlib.sha256(key.encode()).digest())

    def joined(parts: list[I64]) -> I64:
        return np.concatenate(parts) if parts else np.zeros(0, np.int64)

    return TaskOut(
        np.asarray(rescanned, np.int64),
        np.asarray(bits, np.int64),
        np.asarray(longest, np.int64),
        joined(df_owner),
        joined(df_pattern),
        np.asarray(key_dense, np.int64),
        b"".join(key_words),
        np.asarray(drop_dense, np.int64),
        b"".join(drop_words),
        attributed,
    )


@dataclass
class ReadState:
    """Parent-side counters of the sequential, hashed corpus read (progress only)."""

    bytes: int = 0
    docs: int = 0
    files: int = 0


CHUNK_ROWS = 128  # wanted lines per task: bounds work so small files still spread out
CHUNK_BYTES = 1 * MiB


def span_tasks(
    root: Path,
    files: Sequence[Any],
    read: Sequence[int],
    wanted: Mapping[int, I64],
    state: ReadState,
) -> Iterator[tuple[int, LineSpan, list[int]]]:
    """One sequential, hashed read of every needed plan file, as bounded span tasks.

    Yields (plan file, span, wanted rows) continuously across files (no per-file pool
    drain). After each file the bytes, SHA-256 and row count must equal the plan, and
    its size/modification time must be unchanged; otherwise the run refuses.
    """
    for f in read:
        item = files[f]
        path = root / item.path
        status = path.stat()
        stream: dict[str, Any] = {"bytes": 0}
        digest = hashlib.sha256()
        rows = offset = 0
        wanted_rows = wanted[f]
        for data in blocks(path, stream, digest=digest):
            count, picked, at, starts, ends = wanted_span(data, rows, offset, wanted_rows)
            rows, offset = rows + count, offset + len(data)
            state.bytes += len(data)
            state.docs += count
            lo = 0
            while lo < len(picked):
                hi = lo + 1
                while (
                    hi < len(picked)
                    and hi - lo < CHUNK_ROWS
                    and ends[hi] - starts[lo] <= CHUNK_BYTES
                ):
                    hi += 1
                base = starts[lo]
                span = LineSpan(
                    str(path),
                    status.st_size,
                    status.st_mtime_ns,
                    at + base,
                    tuple(x - base for x in starts[lo:hi]),
                    tuple(x - base for x in ends[lo:hi]),
                )
                yield f, span, picked[lo:hi]
                lo = hi
        after = path.stat()
        if (stream["bytes"], digest.hexdigest(), rows) != (
            int(item.file_bytes),
            item.documents_sha256,
            int(item.documents),
        ) or (after.st_size, after.st_mtime_ns) != (status.st_size, status.st_mtime_ns):
            raise AuditError("cleaned corpus file differs from the C05 plan")
        state.files += 1


# protected material (optional) -------------------------------------------------------------


@dataclass
class MaterialOut:
    detected: dict[tuple[str, str, str], int]  # (group, form, candidate) -> items detected
    items: dict[tuple[str, str], int]  # (group, form) -> items
    slots: dict[str, Counter[str]]  # watched identity -> slot -> items


def scan_material(task: Any) -> MaterialOut:
    from xlm.data.exclusion.prepare_workers import _rows
    from xlm.data.exclusion.streaming import render

    tables = _tables()
    matcher = _matcher()
    assert tables.material is not None and tables.standalone is not None
    name, group = tables.material[task.file]
    out = MaterialOut({}, {}, defaultdict(Counter))
    for row in _rows(task, 64 * MiB, lambda: None):
        slots = cand.render_slots(name, row)
        if [(k, t) for k, _, t in slots] != render(name, row):
            raise AuditError("render-slot mirror differs from the frozen renderer")
        for _kind, slot, text in slots:
            tokens = match_tokens(text)
            if tokens and len(tokens) <= cand.V4.span_tokens:
                identity = canonical.digest(tokens)
                if identity in tables.watched:
                    out.slots[identity][slot] += 1
        for form, text in cand.injection_forms(name, row):
            pattern, start, end = matcher.occurrences(cand.injected(text, matcher.vocabulary))
            bits = cand.candidate_hits(pattern, start, end, tables.standalone, tables.short)
            out.items[(group, form)] = out.items.get((group, form), 0) + 1
            for c, candidate in enumerate(cand.CANDIDATES):
                key = (group, form, candidate.name)
                out.detected[key] = out.detected.get(key, 0) + (bits >> c & 1)
    return out


# -- parent ----------------------------------------------------------------------------------


@dataclass
class Ledger:
    file: npt.NDArray[np.int32]
    row: npt.NDArray[np.int32]
    decision: npt.NDArray[np.int8]
    split: npt.NDArray[np.int8]
    allocation: npt.NDArray[np.int16]
    nbytes: I64


def read_ledger(path: Path, total: int, pool: OrderedPool, progress: Progress) -> Ledger:
    size = path.stat().st_size
    state: dict[str, Any] = {"bytes": 0}
    parts: list[LedgerPart] = []
    done = 0
    started = time.monotonic()
    for part in pool.map(parse_ledger, blocks(path, state), 2 * pool.workers):
        parts.append(part)
        done += part.rows
        progress.update(
            done,
            bytes_done=state["bytes"],
            bytes_total=size,
            mib_per_s=state["bytes"] / MiB / max(time.monotonic() - started, 1e-9),
            workers=pool.workers,
            busy=pool.workers,
        )
    if done != total:
        raise AuditError("decision ledger row count differs from the C05 plan")

    def joined(name: str, dtype: Any) -> Any:
        return np.concatenate([getattr(p, name) for p in parts]) if parts else np.zeros(0, dtype)

    return Ledger(
        joined("file", np.int32),
        joined("row", np.int32),
        joined("decision", np.int8),
        joined("split", np.int8),
        joined("allocation", np.int16),
        joined("nbytes", np.int64),
    )


@dataclass
class PatternTable:
    """Distinct frozen patterns, sorted by identity (big-endian 4 x uint64 words)."""

    words: npt.NDArray[np.uint64]  # (n, 4)
    length: I64
    characters: I64
    distinct: I64
    kinds: U8
    item_start: I64  # CSR into ``items``
    items: I64
    records: int  # provenance rows (index lines)


def build_table(parts: Sequence[IndexPart]) -> PatternTable:
    raw = b"".join(p.identity for p in parts)
    words = np.frombuffer(raw, dtype=">u8").reshape(-1, 4).astype(np.uint64)
    length = np.concatenate([p.length for p in parts]).astype(np.int64)
    characters = np.concatenate([p.characters for p in parts]).astype(np.int64)
    distinct = np.concatenate([p.distinct for p in parts]).astype(np.int64)
    kind = np.concatenate([p.kind for p in parts])
    item = np.concatenate([p.item for p in parts])
    if not words.shape[0]:
        raise AuditError("protected index has no patterns")
    order = np.lexsort((item, words[:, 3], words[:, 2], words[:, 1], words[:, 0]))
    words, length, characters, distinct = (
        words[order],
        length[order],
        characters[order],
        distinct[order],
    )
    kind, item = kind[order], item[order]
    first = np.r_[True, np.any(words[1:] != words[:-1], axis=1)]
    starts = np.flatnonzero(first)
    if np.any(length[1:][~first[1:]] != length[:-1][~first[1:]]):
        raise AuditError("protected index identity collision")
    item_start = np.r_[starts, item.size].astype(np.int64)
    return PatternTable(
        words=words[starts],
        length=length[starts],
        characters=characters[starts],
        distinct=distinct[starts],
        kinds=np.bitwise_or.reduceat(kind, starts).astype(np.uint8),
        item_start=item_start,
        items=item,
        records=int(item.size),
    )


def find_rows(table: npt.NDArray[np.uint64], query: npt.NDArray[np.uint64]) -> I64:
    """Row of each query identity in the sorted table, -1 when absent (exact 4-word match)."""
    if not query.shape[0]:
        return np.zeros(0, np.int64)
    position = np.searchsorted(table[:, 0], query[:, 0], side="left")
    result = np.full(query.shape[0], -1, np.int64)
    pending = np.arange(query.shape[0])
    while pending.size:
        at = position[pending]
        inside = at < table.shape[0]
        pending, at = pending[inside], at[inside]
        same_first = table[at, 0] == query[pending, 0]
        pending, at = pending[same_first], at[same_first]
        full = np.all(table[at] == query[pending], axis=1)
        result[pending[full]] = at[full]
        pending = pending[~full]
        position[pending] += 1
    return result


def by_file(values: I64, owner: I64, files: int) -> list[I64]:
    """``values`` split by plan file (one stable sort instead of a scan per file)."""
    order = np.argsort(owner, kind="stable")
    bounds = np.searchsorted(owner[order], np.arange(files + 1))
    ordered = values[order]
    return [ordered[bounds[f] : bounds[f + 1]] for f in range(files)]


def as_words(raw: bytes) -> npt.NDArray[np.uint64]:
    return np.frombuffer(raw, dtype=">u8").reshape(-1, 4).astype(np.uint64)


def void_rows(values: npt.NDArray[Any]) -> npt.NDArray[Any]:
    values = np.ascontiguousarray(values)
    return values.view(np.dtype((np.void, values.dtype.itemsize * values.shape[1]))).ravel()


@dataclass
class Partition:
    """Families of one lineage policy: every document's root and the split order."""

    fam: npt.NDArray[np.uint32]
    roots: npt.NDArray[np.int64]  # sorted by (ordering, root): the historical split order
    fam_bytes: I64  # survivor bytes per root (indexed by dense root)


def split_assignment(
    partition: Partition, fam_hit: npt.NDArray[np.bool_], diagnostic: int, audit: int, quick: int
) -> U8:
    """Historical greedy diagnostic/audit allocation over hit-free families (per root)."""
    del quick  # The quick subset never changes the train split.
    split = np.zeros(partition.fam.size, np.uint8)
    used_d = used_a = 0
    for root in partition.roots.tolist():
        if used_d >= diagnostic and used_a >= audit:
            break
        if fam_hit[root]:
            continue
        size = int(partition.fam_bytes[root])
        if used_d < diagnostic:
            split[root] = 1
            used_d += size
        elif used_a < audit:
            split[root] = 2
            used_a += size
    return split


def ordering_digests(seed: int, ids: Sequence[bytes]) -> npt.NDArray[np.uint64]:
    raw = b"".join(
        hashlib.sha256(canonical.canonical_bytes([seed, i.decode("utf-8")])).digest() for i in ids
    )
    return as_words(raw)


def run(args: argparse.Namespace, progress: Progress, stage: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    telemetry = Telemetry()

    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        telemetry()
        stage[0] = name
        progress.stage(name, total, unit)

    begin("PLAN VERIFY")
    plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
    identity = plan.identity()
    work = Path(plan.scratch_root) / identity
    decisions_path = args.decisions or work / "decisions.jsonl"
    facts = args.facts or work / FACTS_DIR
    group = args.group or work / GROUP_DIR
    seal_path = args.seal or work / SEAL_NAME
    matcher_dir = args.matcher or work / MATCHER_NAME
    files = list(plan.files)
    allocation_names = sorted({key_of(f.component, f.view, f.upstream_component) for f in files})
    allocation_code = {name: n for n, name in enumerate(allocation_names)}
    rescan_scope = set(range(len(allocation_names)))
    if args.rescan_allocation:
        rescan_scope = set()
        for item in args.rescan_allocation:
            component, view, upstream = item.split("/")
            name = key_of(component, view, None if upstream == "-" else upstream)
            if name not in allocation_code:
                raise AuditError("rescan allocation is not in the C05 plan")
            rescan_scope.add(allocation_code[name])
    salt = os.environ[args.salt_env].encode() if args.salt_env else os.urandom(32)
    fp = Fingerprint(salt)
    offsets = np.zeros(len(files) + 1, np.int64)
    np.cumsum([int(f.documents) for f in files], out=offsets[1:])
    total = int(offsets[-1])

    begin("INPUT DISCOVERY", 7)
    for path, what in (
        (decisions_path, "decision ledger"),
        (seal_path, "group seal"),
        (args.benchmark_index, "protected index"),
        (args.benchmark_receipt, "benchmark receipt"),
    ):
        if not path.is_file():
            raise AuditError(f"{what} is missing")
    for path, what in (
        (facts, "fact unit directory"),
        (group, "group directory"),
        (matcher_dir, "compiled matcher"),
    ):
        if not path.is_dir():
            raise AuditError(f"{what} is missing")
    if args.benchmark_material is not None and not args.benchmark_material.is_dir():
        raise AuditError("benchmark material root is missing")
    if args.state_out is not None and (
        args.state_out.exists() or not args.state_out.parent.is_dir()
    ):
        raise AuditError("state output must be a new file in an existing directory")
    receipt = read_metadata(args.benchmark_receipt, digested=False)
    receipt_body = receipt.get("payload", receipt)
    if receipt_body.get("index_bytes") != args.benchmark_index.stat().st_size:
        raise AuditError("protected index differs from the benchmark receipt")
    completion_path = Path(plan.output_root) / identity / "completion.json"
    completion = (
        read_metadata(completion_path, digested=False) if completion_path.is_file() else None
    )
    progress.update(7, force=True, files=len(files), documents=total)

    # -- group arrays ------------------------------------------------------------------------
    begin("GROUP VERIFY", 2)
    seal = canonical.loads_bytes_strict(seal_path.read_bytes())
    seal_body = seal.get("payload", seal) if isinstance(seal, dict) else {}
    if seal_body.get("plan") != identity:
        raise AuditError("group seal names a different C05 plan")
    try:
        verify_group_files(group, seal_body["files"])
    except (C05Error, KeyError, TypeError) as exc:
        raise AuditError("group artifacts differ from the signed group seal") from exc
    progress.update(1)
    where = np.fromfile(group / "where.u32", dtype="<u4").astype(np.int64)
    dup = np.fromfile(group / "dup.u32", dtype="<u4")
    fam_a = np.fromfile(group / "fam.u32", dtype="<u4")
    survivor = np.fromfile(group / "survivor.u8", dtype=np.uint8).astype(bool)
    families_a = np.fromfile(group / "families.u32", dtype="<u4").astype(np.int64)
    ordering_a = np.fromfile(group / "fam_ordering.b32", dtype=np.uint8).reshape(-1, 32)
    split_a = np.fromfile(group / "fam_split.u8", dtype=np.uint8)
    id_offsets = np.fromfile(group / "ids.off", dtype="<u8").astype(np.int64)
    id_blob = np.memmap(group / "ids.bin", dtype=np.uint8, mode="r")
    if not (where.size == dup.size == fam_a.size == survivor.size == total == id_offsets.size - 1):
        raise AuditError("group arrays do not cover the C05 plan")
    dense_of = np.empty(total, np.int64)
    dense_of[where] = np.arange(total, dtype=np.int64)

    def id_bytes(dense: int) -> bytes:
        return bytes(id_blob[int(id_offsets[dense]) : int(id_offsets[dense + 1])])

    progress.update(2, force=True)

    supervisor = Supervisor(Deadline(None, time.monotonic()), None)
    with supervisor:
        light = Tables(
            files={f.path: n for n, f in enumerate(files)},
            allocations=allocation_code,
            refs=None,
        )
        # -- ledger ------------------------------------------------------------------------
        begin("DECISION LEDGER", total, "rows")
        with OrderedPool(
            args.workers, light, supervisor, inline=args.workers == 1, initializer=init_worker
        ) as pool:
            ledger = read_ledger(decisions_path, total, pool, progress)
        global_rows = offsets[ledger.file.astype(np.int64)] + ledger.row
        if not np.array_equal(global_rows, where):
            raise AuditError("decision ledger is not in C05 dense order")
        alloc = ledger.allocation.astype(np.int64)
        nbytes = ledger.nbytes
        prod_decision = ledger.decision.astype(np.int8)
        prod_split = ledger.split.astype(np.int8)
        tally = np.zeros((len(allocation_names), 3), np.int64)
        np.add.at(tally, (alloc, prod_decision.astype(np.int64)), 1)
        if completion is not None:
            signed = completion.get("payload", {}).get("allocations", {})
            for a, name in enumerate(allocation_names):
                counts = signed.get(name, {})
                if (int(tally[a, KEPT]), int(tally[a, DUPLICATE]), int(tally[a, EXCLUDED])) != (
                    counts.get("kept", 0),
                    counts.get("duplicate", 0),
                    counts.get("excluded", 0),
                ):
                    raise AuditError("decision ledger disagrees with the C05 completion")

        # -- fact units: hits, lineage of the families that may change -------------------
        synth_file = np.asarray([f.source_id == SYNTH_SOURCE for f in files], dtype=bool)
        synth_doc = synth_file[ledger.file.astype(np.int64)]
        in_r = np.isin(fam_a, np.unique(fam_a[synth_doc]))
        r_dense = np.flatnonzero(in_r)
        local_of = np.full(total, -1, np.int64)
        local_of[r_dense] = np.arange(r_dense.size)
        r_by_file = by_file(r_dense, ledger.file[r_dense].astype(np.int64), len(files))
        begin("FACT UNITS", len(files), "files")
        hit_dense: list[I64] = []
        hit_pattern: list[bytes] = []
        inc_dense: list[I64] = []
        inc_words: list[npt.NDArray[np.uint64]] = []
        parent_edges: list[tuple[int, int]] = []
        r_id_local: dict[bytes, int] | None = None
        for f, item in enumerate(files):
            path = facts / f"{f:05d}.unit"
            if not path.is_file():
                raise AuditError("a fact unit is missing")
            unit = open_unit(path)
            if unit.rows != int(item.documents):
                raise AuditError("fact unit does not match the C05 plan")
            hits = unit.hits()
            if hits.shape[0]:
                hit_dense.append(dense_of[offsets[f] + hits["row"].astype(np.int64)])
                hit_pattern.append(np.ascontiguousarray(hits["pattern"]).tobytes())
            members = r_by_file[f]
            if members.size:
                _blob, _off, counts, digests = unit.strings("lineage")
                counts = np.asarray(counts, dtype=np.int64)
                starts = np.r_[0, np.cumsum(counts)]
                rows = where[members] - offsets[f]
                n_k = counts[rows]
                gather = np.repeat(starts[rows], n_k) + (
                    np.arange(int(n_k.sum())) - np.repeat(np.cumsum(n_k) - n_k, n_k)
                )
                inc_dense.append(np.repeat(members, n_k))
                inc_words.append(
                    as_words(np.ascontiguousarray(np.asarray(digests)[gather]).tobytes())
                )
                pblob, poffsets, pcounts, _ = unit.strings("parents")
                pcounts = np.asarray(pcounts, dtype=np.int64)
                pstarts = np.r_[0, np.cumsum(pcounts)]
                with_parents = np.flatnonzero(pcounts[rows] > 0)
                if with_parents.size:
                    if r_id_local is None:
                        r_id_local = {id_bytes(int(d)): int(local_of[d]) for d in r_dense.tolist()}
                    pb, po = bytes(pblob), np.asarray(poffsets).tolist()
                    for k in with_parents.tolist():
                        d, r = int(members[k]), int(rows[k])
                        for j in range(int(pstarts[r]), int(pstarts[r + 1])):
                            target = r_id_local.get(pb[po[j] : po[j + 1]])
                            if target is not None:
                                parent_edges.append((int(local_of[d]), target))
            unit.close()
            progress.update(
                f + 1,
                hits=sum(x.size for x in hit_dense),
                incidences=sum(x.size for x in inc_dense),
            )
        hits_dense = np.concatenate(hit_dense) if hit_dense else np.zeros(0, np.int64)
        hit_blob = b"".join(hit_pattern)
        del hit_pattern
        hits_words = as_words(hit_blob)
        prod_hit = np.zeros(total, bool)
        prod_hit[hits_dense] = True
        if int(prod_hit.sum()) != hits_dense.size:
            raise AuditError("fact units record two hits for one document")
        inc_d = np.concatenate(inc_dense) if inc_dense else np.zeros(0, np.int64)
        inc_w = np.concatenate(inc_words) if inc_words else np.zeros((0, 4), np.uint64)
        del inc_dense, inc_words

        # -- protected index ---------------------------------------------------------------
        material_files = [MaterialFile.model_validate(f) for f in receipt_body.get("files", [])]
        refs: dict[str, int] = {}
        item_group_name: list[str] = []
        item_file: list[int] = []
        base: list[int] = []
        for n, entry in enumerate(material_files):
            dumped = entry.model_dump(mode="json")
            base.append(len(item_group_name))
            for row_number in range(1, entry.items + 1):
                refs[canonical.digest([dumped, row_number])] = len(item_group_name)
                item_group_name.append(f"{entry.task}/{entry.split}")
                item_file.append(n)
        group_names = sorted(set(item_group_name))
        item_group = np.asarray([group_names.index(g) for g in item_group_name], np.int64)
        size = args.benchmark_index.stat().st_size
        begin("BENCHMARK INDEX", size, "bytes")
        index_state: dict[str, Any] = {"bytes": 0}
        index_digest = hashlib.sha256()
        parts: list[IndexPart] = []
        with OrderedPool(
            args.workers,
            Tables(refs=refs),
            supervisor,
            inline=args.workers == 1,
            initializer=init_worker,
        ) as pool:
            for part in pool.map(
                parse_index,
                blocks(args.benchmark_index, index_state, digest=index_digest),
                2 * pool.workers,
            ):
                parts.append(part)
                progress.update(
                    index_state["bytes"],
                    records=sum(p.item.size for p in parts),
                    workers=pool.workers,
                    busy=pool.workers,
                )
        if index_digest.hexdigest() != receipt_body.get("index_sha256"):
            raise AuditError("protected index differs from the benchmark receipt")
        table = build_table(parts)
        del parts
        if table.records != receipt_body.get("patterns", table.records):
            raise AuditError("protected index record count differs from the receipt")

        # -- compiled matcher identities ---------------------------------------------------
        matcher = cand.OccurrenceMatcher(
            matcher_dir, index_sha256=index_digest.hexdigest(), index_bytes=size
        )
        unique = int(matcher.manifest["counts"]["unique_patterns"])
        if unique != table.words.shape[0]:
            raise AuditError("compiled matcher and protected index pattern sets differ")
        begin("MATCHER IDENTITIES", unique, "patterns")
        encoded = cand.word_encodings(matcher.words())
        compiled_raw = bytearray()
        chunk = 1 << 16
        tokens_all, offsets_all = matcher.flat_patterns()

        def identity_jobs() -> Iterator[tuple[npt.NDArray[np.uint32], I64]]:
            for lo in range(0, unique, chunk):
                hi = min(unique, lo + chunk)
                o = offsets_all[lo : hi + 1]
                yield np.array(tokens_all[o[0] : o[-1]]), o - o[0]

        with OrderedPool(
            args.workers,
            Tables(encoded=encoded),
            supervisor,
            inline=args.workers == 1,
            initializer=init_worker,
        ) as pool:
            for raw in pool.map(identity_chunk, identity_jobs(), 2 * pool.workers):
                compiled_raw += raw
                progress.update(len(compiled_raw) // 32, workers=pool.workers, busy=pool.workers)
        compiled_words = as_words(bytes(compiled_raw))
        del compiled_raw
        row_of_compiled = find_rows(table.words, compiled_words)
        if np.any(row_of_compiled < 0) or np.unique(row_of_compiled).size != unique:
            raise AuditError("compiled matcher and protected index pattern sets differ")
        del compiled_words
        hit_rows = find_rows(table.words, hits_words)
        if np.any(hit_rows < 0):
            raise AuditError("a recorded C05 hit is not a protected index pattern")

        # -- candidate flags -----------------------------------------------------------------
        n_cand = len(cand.CANDIDATES)
        flags_row = np.zeros(table.words.shape[0], np.uint8)
        short_row = np.zeros(table.words.shape[0], np.uint8)
        for c, candidate in enumerate(cand.CANDIDATES):
            mask = cand.standalone_mask(
                table.length, table.characters, table.distinct, table.kinds, candidate
            )
            flags_row |= mask.astype(np.uint8) << c
            if candidate.pair is not None:
                short_row |= (~mask).astype(np.uint8) << c
        standalone = flags_row[row_of_compiled]
        compiled_of_row = np.empty(unique, np.int64)
        compiled_of_row[row_of_compiled] = np.arange(unique)
        short_tables: list[cand.ShortItems | None] = []
        for c, candidate in enumerate(cand.CANDIDATES):
            if candidate.pair is None:
                short_tables.append(None)
                continue
            rows = np.flatnonzero((short_row >> c) & 1)
            compiled = compiled_of_row[rows]
            order = np.argsort(compiled, kind="stable")
            rows, compiled = rows[order], compiled[order]
            counts = table.item_start[rows + 1] - table.item_start[rows]
            starts = np.r_[0, np.cumsum(counts)].astype(np.int64)
            gather = np.repeat(table.item_start[rows], counts) + (
                np.arange(int(counts.sum())) - np.repeat(starts[:-1], counts)
            )
            short_tables.append(cand.ShortItems(compiled, starts, table.items[gather]))

        # -- corpus: rescans and SYNTH lineage attribution ---------------------------------
        hit_alloc = alloc[hits_dense]
        rescan_dense = hits_dense[np.isin(hit_alloc, sorted(rescan_scope))]
        attribute_dense = r_dense[synth_doc[r_dense]]
        mode = np.zeros(total, np.int8)
        mode[rescan_dense] |= RESCAN
        mode[attribute_dense] |= ATTRIBUTE
        recorded_of = {
            int(d): hit_blob[32 * k : 32 * k + 32]
            for k, d in enumerate(hits_dense.tolist())
            if mode[d] & RESCAN
        }
        wanted_dense = np.flatnonzero(mode)
        wanted_file = ledger.file[wanted_dense].astype(np.int64)
        grouped = by_file(wanted_dense, wanted_file, len(files))
        read = [f for f in range(len(files)) if grouped[f].size]
        local_rows = {f: np.sort(where[grouped[f]] - offsets[f]) for f in read}
        total_bytes = sum(int(files[f].file_bytes) for f in read)
        total_docs = sum(int(files[f].documents) for f in read)
        begin("CORPUS RESCAN", total_docs, "docs")
        bits = np.full(total, -1, np.int64)
        longest = np.zeros(total, np.int64)
        df_pairs: list[I64] = []
        dropped_dense: list[I64] = []
        dropped_words: list[npt.NDArray[np.uint64]] = []
        key_dense: list[I64] = []
        key_words: list[npt.NDArray[np.uint64]] = []
        attributed = 0
        root = Path(plan.data_root)
        corpus_tables = Tables(
            matcher=str(matcher_dir),
            index_sha256=index_digest.hexdigest(),
            index_bytes=size,
            standalone=standalone,
            short=tuple(short_tables),
        )
        read_state = ReadState()

        def corpus_jobs() -> Iterator[CorpusTask]:
            for f, span, rows in span_tasks(root, files, read, local_rows, read_state):
                dense = (offsets[f] + np.asarray(rows, np.int64)).tolist()
                dense = [int(dense_of[g]) for g in dense]
                yield CorpusTask(
                    span,
                    tuple(dense),
                    tuple(int(mode[d]) for d in dense),
                    tuple(recorded_of.get(d, b"") for d in dense),
                    files[f].source_id,
                    files[f].source_revision,
                )

        corpus_started = time.monotonic()
        with OrderedPool(
            args.workers,
            corpus_tables,
            supervisor,
            inline=args.workers == 1,
            initializer=init_worker,
        ) as pool:
            for out in pool.map(scan_corpus, corpus_jobs(), 4 * pool.workers):
                bits[out.rescanned] = out.bits
                longest[out.rescanned] = out.longest
                if out.df_pattern.size:
                    df_pairs.append(out.df_pattern * 64 + alloc[out.rescanned[out.df_owner]])
                attributed += out.attributed
                if out.key_dense.size:
                    key_dense.append(out.key_dense)
                    key_words.append(as_words(out.key_words))
                if out.drop_dense.size:
                    dropped_dense.append(out.drop_dense)
                    dropped_words.append(as_words(out.drop_words))
                elapsed = max(time.monotonic() - corpus_started, 1e-9)
                progress.update(
                    read_state.docs,
                    bytes_done=read_state.bytes,
                    bytes_total=total_bytes,
                    files_committed=read_state.files,
                    files_total=len(read),
                    mib_per_s=read_state.bytes / MiB / elapsed,
                    workers=pool.workers,
                    busy=pool.workers,
                )
        scanned_bytes = read_state.bytes
        if read_state.files != len(read):
            raise AuditError("corpus pass did not reach every wanted file")
        if np.any(bits[rescan_dense] < 0) or attributed != attribute_dense.size:
            raise AuditError("corpus pass did not reach every wanted document")
        # Recomputed known-lineage-v3 keys of every attributed SYNTH row == its fact unit.
        unit_side = np.column_stack([inc_d.astype(np.uint64), inc_w])[synth_doc[inc_d]]
        corpus_side = np.column_stack(
            [
                (np.concatenate(key_dense) if key_dense else np.zeros(0, np.int64)).astype(
                    np.uint64
                ),
                np.concatenate(key_words) if key_words else np.zeros((0, 4), np.uint64),
            ]
        )
        del key_dense, key_words
        if unit_side.shape != corpus_side.shape or not np.array_equal(
            np.sort(void_rows(unit_side)), np.sort(void_rows(corpus_side))
        ):
            raise AuditError("lineage keys recomputed from the corpus differ from the C05 facts")

        # -- lineage partitions --------------------------------------------------------------
        begin("LINEAGE", 4)
        n_r = int(r_dense.size)
        drop_d = np.concatenate(dropped_dense) if dropped_dense else np.zeros(0, np.int64)
        drop_w = np.concatenate(dropped_words) if dropped_words else np.zeros((0, 4), np.uint64)
        both = np.column_stack([inc_w, inc_d.astype(np.uint64)])
        gone = np.column_stack([drop_w, drop_d.astype(np.uint64)])
        is_dropped = (
            np.isin(void_rows(both), void_rows(gone))
            if gone.shape[0]
            else np.zeros(inc_d.size, bool)
        )
        if int(is_dropped.sum()) != gone.shape[0]:
            raise AuditError("additional-seed lineage keys are not in the C05 facts")
        key_order = np.lexsort((inc_d, inc_w[:, 3], inc_w[:, 2], inc_w[:, 1], inc_w[:, 0]))
        sorted_w, sorted_d = inc_w[key_order], inc_d[key_order]
        sorted_drop = is_dropped[key_order]
        new_key = (
            np.r_[True, np.any(sorted_w[1:] != sorted_w[:-1], axis=1)]
            if sorted_w.shape[0]
            else np.zeros(0, bool)
        )
        key_id = np.cumsum(new_key) - 1

        def key_edges(keep: npt.NDArray[np.bool_]) -> tuple[I64, I64]:
            k, d = key_id[keep], local_of[sorted_d[keep]]
            if not k.size:
                return np.zeros(0, np.int64), np.zeros(0, np.int64)
            first = np.r_[True, k[1:] != k[:-1]]
            anchor = d[np.flatnonzero(first)][np.cumsum(first) - 1]
            other = anchor != d
            return anchor[other], d[other]

        dup_r = dup[r_dense].astype(np.int64)
        if np.any(local_of[dup_r] < 0):
            raise AuditError("a duplicate component crosses the rebuilt lineage families")
        dup_a, dup_b = local_of[dup_r], np.arange(n_r)
        pe = np.asarray(parent_edges, np.int64).reshape(-1, 2)
        base_a = np.concatenate([dup_a, pe[:, 0]])
        base_b = np.concatenate([dup_b, pe[:, 1]])
        ka, kb = key_edges(np.ones(sorted_d.size, bool))
        rebuilt = r_dense[
            components(n_r, np.concatenate([base_a, ka]), np.concatenate([base_b, kb]))
        ]
        reconstructed = bool(np.array_equal(rebuilt, fam_a[r_dense].astype(np.int64)))
        if not reconstructed:
            raise AuditError("lineage graph does not reproduce the production families")
        progress.update(1)
        ka, kb = key_edges(~sorted_drop)
        fam_b = fam_a.copy()
        fam_b[r_dense] = r_dense[
            components(n_r, np.concatenate([base_a, ka]), np.concatenate([base_b, kb]))
        ].astype(np.uint32)
        progress.update(2)
        # One-hop links: every key with an additional-only incidence, all its incidences.
        hot_keys = np.unique(key_id[sorted_drop])
        hop_mask = np.isin(key_id, hot_keys)
        hop_key, hop_fam = key_id[hop_mask], fam_b[sorted_d[hop_mask]].astype(np.int64)

        survivor_bytes = nbytes * survivor

        def partition(fam: npt.NDArray[np.uint32]) -> Partition:
            fam64 = fam.astype(np.int64)
            roots = np.flatnonzero(fam64 == np.arange(total))
            # Exact: integer byte sums stay far below 2**53.
            fam_bytes = np.bincount(fam64, weights=survivor_bytes, minlength=total).astype(np.int64)
            ordering = np.zeros((roots.size, 4), np.uint64)
            known = np.full(total, -1, np.int64)
            known[families_a] = np.arange(families_a.size)
            have = known[roots] >= 0
            ordering[have] = as_words(
                np.ascontiguousarray(ordering_a[known[roots[have]]]).tobytes()
            )
            missing = roots[~have]
            if missing.size:
                ordering[~have] = ordering_digests(
                    plan.policy.seed, [id_bytes(int(r)) for r in missing.tolist()]
                )
            order = np.lexsort(
                (roots, ordering[:, 3], ordering[:, 2], ordering[:, 1], ordering[:, 0])
            )
            return Partition(fam, roots[order], fam_bytes)

        part_a = partition(fam_a)
        progress.update(3)
        part_b = partition(fam_b)
        progress.update(4)

        # -- matcher hit sets ----------------------------------------------------------------
        recorded_flags = flags_row[hit_rows]
        hit_sets: list[npt.NDArray[np.bool_]] = []
        conservative: list[I64] = []
        out_scope = ~np.isin(hit_alloc, sorted(rescan_scope))
        for c in range(n_cand):
            h = np.zeros(total, bool)
            alone = ((recorded_flags >> c) & 1).astype(bool)
            h[hits_dense[alone]] = True
            h[rescan_dense[(bits[rescan_dense] >> c & 1).astype(bool)]] = True
            unsure = hits_dense[out_scope & ~alone]
            h[unsure] = True
            conservative.append(np.bincount(alloc[unsure], minlength=len(allocation_names)))
            hit_sets.append(h)
        if not np.array_equal(hit_sets[0], prod_hit):
            raise AuditError("current matcher hit set differs from the C05 facts")

        # -- the matrix ----------------------------------------------------------------------
        begin("COUNTERFACTUAL MATRIX", len(COMBOS), "combinations")
        prod_train = (prod_decision == KEPT) & (prod_split == 0)
        matrix: dict[str, Any] = {}
        train_bits = np.zeros(total, np.uint32)
        reproduction: dict[str, Any] = {}
        policy = plan.policy
        for done, (c, lineage) in enumerate(COMBOS, 1):
            grouping = part_a if lineage == 0 else part_b
            fam64 = grouping.fam.astype(np.int64)
            fam_hit = np.zeros(total, bool)
            fam_hit[fam64[hit_sets[c]]] = True
            if lineage == 2 and hop_key.size:
                hot = np.zeros(int(key_id.max()) + 1 if key_id.size else 0, bool)
                hot[hop_key[fam_hit[hop_fam]]] = True
                fam_hit[hop_fam[hot[hop_key]]] = True
            excluded = fam_hit[fam64]
            decision = np.where(excluded, EXCLUDED, np.where(survivor, KEPT, DUPLICATE)).astype(
                np.int8
            )
            split_root = split_assignment(
                grouping, fam_hit, policy.diagnostic_bytes, policy.audit_bytes, policy.quick_bytes
            )
            split = np.where(excluded, 0, split_root[fam64]).astype(np.int8)
            if c == 0 and lineage == 0:
                reproduction = {
                    "decisions_equal_ledger": bool(np.array_equal(decision, prod_decision)),
                    "splits_equal_ledger": bool(np.array_equal(split, prod_split)),
                    "family_splits_equal_group": bool(
                        np.array_equal(split_root[families_a], split_a)
                    ),
                }
                if not all(reproduction.values()):
                    raise AuditError("current matcher x current lineage does not reproduce C05")
            train = (decision == KEPT) & (split == 0)
            train_bits |= train.astype(np.uint32) << (done - 1)
            direct = hit_sets[c]
            name = combo_name(cand.CANDIDATES[c].name, LINEAGES[lineage])
            matrix[name] = allocation_stats(
                allocation_names,
                alloc,
                nbytes,
                decision,
                direct,
                train,
                prod_decision,
                prod_train,
                conservative[c],
            )
            progress.update(done)

        # -- reports -------------------------------------------------------------------------
        begin("PATTERN CENSUS", 3)
        top = top_patterns(
            args.top,
            fp,
            table,
            hit_rows,
            hit_alloc,
            allocation_names,
            flags_row,
            df_pairs,
            row_of_compiled,
            item_group,
            group_names,
        )
        census = short_census(
            table,
            hit_rows,
            hit_alloc,
            allocation_names,
            item_group,
            group_names,
            longest,
            rescan_dense,
            alloc,
            bits,
        )
        progress.update(2)
        recall = index_recall(table, flags_row, short_row, item_group, group_names)
        progress.update(3)
        material_report: dict[str, Any] | None = None
        if args.benchmark_material is not None:
            watched = {
                bytes(np.asarray(table.words[r], dtype=">u8").tobytes()).hex(): t["fingerprint"]
                for r, t in zip(top["rows"], top["patterns"], strict=True)
            }
            material_report = material_recall(
                args,
                material_files,
                base,
                group_names,
                item_group_name,
                corpus_tables,
                frozenset(watched),
                watched,
                supervisor,
                progress,
                begin,
            )
        top_list = top["patterns"]
        if material_report is not None:
            for entry in top_list:
                entry["render_slots"] = material_report["slots"].get(entry["fingerprint"], {})
            material_report = {k: v for k, v in material_report.items() if k != "slots"}

        begin("REPORT VERIFY", 2)
        if args.state_out is not None:
            write_state(
                args.state_out, plan, completion, allocation_names, ledger, prod_train, train_bits
            )
        body: dict[str, Any] = {
            "kind": "c05_contamination_policy_audit_v1",
            "content_free": True,
            "fingerprints": "HMAC-SHA256 with a per-run salt (or --salt-env), 16 hex",
            "plan_digest": identity,
            "plan_code_commit": plan.code_commit,
            "completion_digest": (completion or {}).get("digest"),
            "index_sha256": index_digest.hexdigest(),
            "frozen_matcher_floors": {
                k: v.model_dump(mode="json") for k, v in cand.FROZEN_FLOORS.items()
            },
            "candidates": [
                {
                    "name": c.name,
                    "description": c.description,
                    "floors": None
                    if c.floors is None
                    else {k: v.model_dump(mode="json") for k, v in c.floors.items()},
                    "pair_rule": None if c.pair is None else c.pair.model_dump(mode="json"),
                    "candidate_digest": c.identity(),
                }
                for c in cand.CANDIDATES
            ],
            "lineage_policies": list(LINEAGES),
            "reproduction": {
                **reproduction,
                "rebuilt_lineage_families_equal_group": reconstructed,
                "rebuilt_documents": n_r,
                "rescanned_first_hits_equal_facts": int(rescan_dense.size),
                "recomputed_synth_lineage_keys_equal_facts": attributed,
                "ledger_matches_completion": completion is not None,
            },
            "inputs": {
                "documents": total,
                "allocations": len(allocation_names),
                "benchmark_items": len(item_group_name),
                "index_records": table.records,
                "distinct_patterns": int(table.words.shape[0]),
                "production_direct_hit_documents": int(hits_dense.size),
                "rescanned_documents": int(rescan_dense.size),
                "rescan_allocations": sorted(allocation_names[a] for a in rescan_scope),
                "corpus_files_read": len(read),
                "corpus_bytes_read": scanned_bytes,
            },
            "lineage": {
                "rebuilt_families_A": int(np.unique(fam_a[r_dense]).size),
                "rebuilt_families_B": int(np.unique(fam_b[r_dense]).size),
                "additional_seed_only_incidences": int(gone.shape[0]),
                "keys_with_additional_seed_links": int(hot_keys.size),
                "families_total_A": int(part_a.roots.size),
                "families_total_B": int(part_b.roots.size),
                "split_grouping_note": (
                    "B/C change only exclusion propagation; split-leakage grouping may keep A"
                ),
            },
            "benchmark_recall_index_level": recall,
            "injected_copy_recall": material_report,
            "top_patterns": top_list,
            "short_pattern_census": census,
            "matrix": matrix,
            "resources": {
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "peak_rss_bytes": int(telemetry()["peak_rss"]),
            },
        }
        progress.update(1)
        verify_content_free(body)
        progress.update(2)
        return body


def allocation_stats(
    names: Sequence[str],
    alloc: I64,
    nbytes: I64,
    decision: npt.NDArray[np.int8],
    direct: npt.NDArray[np.bool_],
    train: npt.NDArray[np.bool_],
    prod_decision: npt.NDArray[np.int8],
    prod_train: npt.NDArray[np.bool_],
    conservative: I64,
) -> dict[str, Any]:
    n = len(names)

    def count(mask: npt.NDArray[np.bool_]) -> I64:
        return np.bincount(alloc[mask], minlength=n)

    def total_bytes(mask: npt.NDArray[np.bool_]) -> I64:
        return np.bincount(alloc[mask], weights=nbytes[mask], minlength=n).astype(np.int64)

    excluded = decision == EXCLUDED
    kept = decision == KEPT
    prod_kept = prod_decision == KEPT
    columns = {
        "documents": np.bincount(alloc, minlength=n),
        "direct_hit_documents": count(direct),
        "excluded": count(excluded),
        "propagated_exclusions": count(excluded & ~direct),
        "duplicate": count(decision == DUPLICATE),
        "kept": count(kept),
        "kept_canonical_bytes": total_bytes(kept),
        "train_documents": count(train),
        "train_canonical_bytes": total_bytes(train),
        "recovered_kept_documents": count(kept) - count(prod_kept),
        "recovered_kept_bytes": total_bytes(kept) - total_bytes(prod_kept),
        "moved_into_train": count(train & ~prod_train),
        "moved_out_of_train": count(prod_train & ~train),
        "conservative_unverified_hits": conservative,
    }
    allocations = {name: {k: int(v[a]) for k, v in columns.items()} for a, name in enumerate(names)}
    totals = {k: int(v.sum()) for k, v in columns.items()}
    return {"allocations": allocations, "totals": totals}


def top_patterns(
    top: int,
    fp: Fingerprint,
    table: PatternTable,
    hit_rows: I64,
    hit_alloc: I64,
    names: Sequence[str],
    flags_row: U8,
    df_pairs: Sequence[I64],
    row_of_compiled: I64,
    item_group: I64,
    group_names: Sequence[str],
) -> dict[str, Any]:
    """Patterns ranked by production first-hit document frequency (content-free)."""
    rows, counts = np.unique(hit_rows, return_counts=True)
    order = np.lexsort((rows, -counts))
    rank_of = {int(rows[i]): k + 1 for k, i in enumerate(order.tolist())}
    pairs = np.concatenate(df_pairs) if df_pairs else np.zeros(0, np.int64)
    all_rows = row_of_compiled[pairs // 64] if pairs.size else np.zeros(0, np.int64)
    out = []
    picked = [int(rows[i]) for i in order[:top].tolist()]
    for r in picked:
        mine = hit_rows == r
        by_alloc = Counter(names[int(a)] for a in hit_alloc[mine].tolist())
        everywhere = Counter(names[int(a)] for a in (pairs[all_rows == r] % 64).tolist())
        items = table.items[table.item_start[r] : table.item_start[r + 1]]
        out.append(
            {
                "fingerprint": fp(np.asarray(table.words[r], dtype=">u8").tobytes()),
                "first_hit_rank": rank_of[r],
                "first_hit_documents": int(mine.sum()),
                "first_hit_documents_by_allocation": dict(sorted(by_alloc.items())),
                "allocations_hit": len(by_alloc),
                "documents_containing_it_among_rescanned": sum(everywhere.values()),
                "rescanned_documents_by_allocation": dict(sorted(everywhere.items())),
                "token_length": int(table.length[r]),
                "characters": int(table.characters[r]),
                "distinct_two_letter_tokens": int(table.distinct[r]),
                "kinds": [k for k in cand.KINDS if table.kinds[r] & cand.KIND_BIT[k]],
                "provenance_records": int(items.size),
                "benchmark_items": int(np.unique(items).size),
                "items_by_task_split": dict(
                    sorted(Counter(group_names[int(item_group[i])] for i in items.tolist()).items())
                ),
                "triggers_alone_under": [
                    c.name for k, c in enumerate(cand.CANDIDATES) if flags_row[r] >> k & 1
                ],
            }
        )
    return {"patterns": out, "rows": picked}


def kind_length_counts(
    table: PatternTable, mask: npt.NDArray[np.bool_] | None = None
) -> dict[str, dict[str, int]]:
    """Distinct patterns by provenance kind and token-length bucket (optionally masked)."""
    codes = cand.bucket_codes(table.length)
    out: dict[str, dict[str, int]] = {}
    for kind in cand.KINDS:
        has = (table.kinds & cand.KIND_BIT[kind]) != 0
        if mask is not None:
            has &= mask
        counts = np.bincount(codes[has], minlength=len(cand.BUCKETS))
        out[kind] = {b: int(n) for b, n in zip(cand.BUCKETS, counts.tolist(), strict=True) if n}
    return out


def short_census(
    table: PatternTable,
    hit_rows: I64,
    hit_alloc: I64,
    names: Sequence[str],
    item_group: I64,
    group_names: Sequence[str],
    longest: I64,
    rescan_dense: I64,
    alloc: I64,
    bits: I64,
) -> dict[str, Any]:
    """Global census of short (3-5 token) patterns and their exclusion footprint."""
    short = (table.length >= 3) & (table.length <= 5)
    rows = np.flatnonzero(short)
    counts = table.item_start[rows + 1] - table.item_start[rows]
    gather = np.repeat(table.item_start[rows], counts) + (
        np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
    )
    items = np.unique(table.items[gather])
    by_group = np.bincount(item_group[items], minlength=len(group_names))
    recorded_short = short[hit_rows]
    only_short = rescan_dense[(longest[rescan_dense] >= 3) & (longest[rescan_dense] <= 5)]

    def per_allocation(codes: I64) -> dict[str, int]:
        found = np.bincount(codes, minlength=len(names))
        return {names[a]: int(n) for a, n in enumerate(found.tolist()) if n}

    return {
        "distinct_patterns_by_kind_and_length": kind_length_counts(table),
        "short_3_to_5_token_patterns": int(rows.size),
        "short_3_to_5_token_patterns_by_length": {
            str(n): int(np.count_nonzero(table.length == n)) for n in (3, 4, 5)
        },
        "items_with_a_3_to_5_token_pattern_by_task_split": {
            g: int(by_group[n]) for n, g in enumerate(group_names) if by_group[n]
        },
        "documents_whose_recorded_first_hit_is_3_to_5_tokens": per_allocation(
            hit_alloc[recorded_short]
        ),
        "rescanned_documents_whose_every_occurrence_is_3_to_5_tokens": per_allocation(
            alloc[only_short]
        ),
        "rescanned_documents_still_hit_by_candidate": {
            c.name: int(np.count_nonzero(bits[rescan_dense] >> k & 1))
            for k, c in enumerate(cand.CANDIDATES)
        },
    }


def index_recall(
    table: PatternTable, flags_row: U8, short_row: U8, item_group: I64, group_names: Sequence[str]
) -> dict[str, Any]:
    """Per candidate and task/split: items that keep a standalone signature (an exact copy
    of that signed variant is detected by it alone), items left with only short pair
    evidence, unsigned items; plus patterns kept by kind and length bucket."""
    rows = np.repeat(np.arange(table.words.shape[0]), np.diff(table.item_start))
    order = np.argsort(table.items, kind="stable")
    tallies = cand.item_recall(
        table.items[order], rows[order], flags_row, short_row, item_group, group_names
    )
    kept: dict[str, Any] = {}
    for c, candidate in enumerate(cand.CANDIDATES):
        alone = ((flags_row >> c) & 1).astype(bool)
        kept[candidate.name] = kind_length_counts(table, alone)
    return {
        "items_by_candidate_and_task_split": tallies,
        "distinct_patterns_by_kind_length": kind_length_counts(table),
        "standalone_patterns_by_candidate_kind_length": kept,
        "note": (
            "standalone = an exact copy of a signed variant is detected by that signature "
            "alone; pair_only = only >= 2 short patterns remain (detected by floor8_pair "
            "when co-located); injected_copy_recall measures whole-item copies"
        ),
    }


def material_recall(
    args: argparse.Namespace,
    material_files: Sequence[MaterialFile],
    base: Sequence[int],
    group_names: Sequence[str],
    item_group_name: Sequence[str],
    corpus_tables: Tables,
    watched: frozenset[str],
    fingerprint_of: Mapping[str, str],
    supervisor: Supervisor,
    progress: Progress,
    begin: Any,
) -> dict[str, Any]:
    """Injected exact-copy detection of every benchmark item, per candidate (operator)."""
    from xlm.data.exclusion.prepare_workers import plan_tasks
    from xlm.data.exclusion.runner import file_sha

    root = args.benchmark_material
    paths = [root / f.path for f in material_files]
    for entry, path in zip(material_files, paths, strict=True):
        if (
            not path.is_file()
            or path.stat().st_size != entry.bytes
            or file_sha(path) != entry.sha256
        ):
            raise AuditError("benchmark material differs from the benchmark receipt")
    tasks = plan_tasks(paths, material_files, 64 * MiB)
    begin("INJECTED-COPY RECALL", len(tasks), "tasks")
    tables = replace(
        corpus_tables,
        material={n: (f.task, f"{f.task}/{f.split}") for n, f in enumerate(material_files)},
        material_base=tuple(base),
        watched=watched,
    )
    detected: Counter[tuple[str, str, str]] = Counter()
    items: Counter[tuple[str, str]] = Counter()
    slots: dict[str, Counter[str]] = defaultdict(Counter)
    with OrderedPool(
        args.workers, tables, supervisor, inline=args.workers == 1, initializer=init_worker
    ) as pool:
        for done, out in enumerate(pool.map(scan_material, tasks, 2 * pool.workers), 1):
            detected.update(out.detected)
            items.update(out.items)
            for identity, counter in out.slots.items():
                slots[identity].update(counter)
            progress.update(done, workers=pool.workers, busy=pool.workers)
    if sum(n for (_, form), n in items.items() if form == "item_composite") != len(item_group_name):
        raise AuditError("injected-copy recall did not cover every benchmark item")
    report: dict[str, Any] = {}
    for (group_name, form), n in sorted(items.items()):
        report.setdefault(group_name, {})[form] = {
            "items": n,
            "detected": {c.name: detected[(group_name, form, c.name)] for c in cand.CANDIDATES},
        }
    return {
        "by_task_split_and_form": report,
        "forms": {
            "item_composite": "label-free whole item, publisher field order (item-composite-v1)",
            "prompt_only": "the complete prompt field alone, without options",
            "single_sentence": "one BLiMP sentence alone",
        },
        "slots": {
            fingerprint_of[i]: {
                slot: {"items": n, "class": cand.slot_class(slot)} for slot, n in sorted(c.items())
            }
            for i, c in slots.items()
        },
    }


def write_state(
    path: Path,
    plan: ExecutionPlan,
    completion: Mapping[str, Any] | None,
    names: Sequence[str],
    ledger: Ledger,
    prod_train: npt.NDArray[np.bool_],
    train_bits: npt.NDArray[np.uint32],
) -> None:
    """Train-membership changes for stage 2: plan file, row, allocation, bit per combo."""
    every = (1 << len(COMBOS)) - 1
    current = np.where(prod_train, every, 0).astype(np.uint32)
    changed = np.flatnonzero(train_bits != current)
    header = {
        "kind": STATE_KIND,
        "plan_digest": plan.identity(),
        "completion_digest": (completion or {}).get("digest"),
        "allocations": list(names),
        "combinations": [
            combo_name(cand.CANDIDATES[c].name, LINEAGES[lineage]) for c, lineage in COMBOS
        ],
        "documents": int(changed.size),
    }
    arrays: dict[str, npt.NDArray[Any]] = {
        "plan_file": ledger.file[changed].astype(np.int32),
        "row": ledger.row[changed].astype(np.int32),
        "allocation": ledger.allocation[changed].astype(np.int16),
        "production_train": prod_train[changed],
        "train_bits": train_bits[changed],
    }
    header["arrays_sha256"] = hashlib.sha256(
        b"".join(np.ascontiguousarray(arrays[k]).tobytes() for k in sorted(arrays))
    ).hexdigest()
    staging = path.with_name(path.name + ".partial")
    with staging.open("xb") as stream:
        np.savez(
            stream,
            header=np.frombuffer(canonical.canonical_bytes(header), dtype=np.uint8),
            plan_file=arrays["plan_file"],
            row=arrays["row"],
            allocation=arrays["allocation"],
            production_train=arrays["production_train"],
            train_bits=arrays["train_bits"],
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, path)


def verify_content_free(body: Mapping[str, Any]) -> None:
    """Refuse a report that could carry a URL or an id/hash-like value."""

    def scrub(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return None if key in ALLOWED_HEX_FIELDS else value

    text = json.dumps(scrub(body), sort_keys=True)
    if "://" in text or re.search(r"https?:", text) or re.search(r"[0-9a-f]{32,}", text):
        raise AuditError("report would not be content-free")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--plan", type=Path, required=True, help="the C05 plan (pNNNN.json)")
    result.add_argument("--benchmark-index", type=Path, required=True, help="protected index.jsonl")
    result.add_argument(
        "--benchmark-receipt", type=Path, required=True, help="benchmark-preparation.receipt.json"
    )
    result.add_argument(
        "--benchmark-material", type=Path, help="protected material root (optional)"
    )
    result.add_argument("--decisions", type=Path, help="default: <scratch>/<plan>/decisions.jsonl")
    result.add_argument("--facts", type=Path, help="default: <scratch>/<plan>/facts")
    result.add_argument("--group", type=Path, help="default: <scratch>/<plan>/group")
    result.add_argument("--seal", type=Path, help="default: <scratch>/<plan>/seal.json")
    result.add_argument("--matcher", type=Path, help="default: <scratch>/<plan>/matcher")
    result.add_argument(
        "--rescan-allocation",
        action="append",
        default=[],
        metavar="COMPONENT/VIEW/UPSTREAM",
        help="rescan only these allocations' hits (default: all; others stay hit, conservatively)",
    )
    result.add_argument("--state-out", type=Path, help="new file for stage 2 (project-tokens)")
    result.add_argument("--salt-env", help="env var holding a fingerprint salt (default random)")
    result.add_argument("--top", type=int, default=20)
    # Operational only: never changes a report byte.
    result.add_argument("--workers", type=int, choices=WORKER_CHOICES, default=8)
    result.add_argument("--progress-interval", type=float, default=5.0)
    result.add_argument("--progress-format", choices=["text", "jsonl"], default="text")
    result.add_argument("--no-progress", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    progress: Progress
    if args.no_progress:
        progress = NullProgress()
    else:
        progress = RunProgress(
            interval=args.progress_interval,
            fmt=args.progress_format,
            label=LABEL,
            telemetry_interval=2.0,
        )
        progress.attach(Telemetry())
    stage = ["PLAN VERIFY"]
    try:
        body = run(args, progress, stage)
    except KeyboardInterrupt:
        _refusal("KeyboardInterrupt", None, stage[0])
        return 130
    except (AuditError, C05Error) as exc:
        _refusal(type(exc).__name__, str(exc), stage[0])
        return 1
    except Exception as exc:  # noqa: BLE001 - content-free refusal: type and stage only
        _refusal(type(exc).__name__, None, stage[0])
        return 1
    text = json.dumps(body, sort_keys=True, indent=1) + "\n"
    progress.complete()
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


def _refusal(kind: str, reason: str | None, stage: str) -> None:
    record = {"refused": True, "error_type": kind, "stage": stage}
    if reason is not None:
        record["reason"] = reason
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
