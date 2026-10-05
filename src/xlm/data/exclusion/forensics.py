"""Read-only, content-free forensics of one C05 exclusion family, FAST path with progress.

Same analysis and report as ``scripts/c05_component_forensics_reference.py`` (the
oracle, 858eb9e); see that module's doc for the C05 graph semantics. What changed:

* **one** pass over ``decisions.jsonl`` (was three): a sequential reader cuts complete
  lines into bounded blocks; ``--workers`` processes strict-parse and validate every row
  into compact columns; the parent integrates them strictly in block order;
* fact units are read once; lineage keys are interned in the same deterministic order
  as the reference (plan file order, then ledger order), so key indices, tie orders and
  every report list are identical;
* connectivity uses C05's own exact union (``grouping.components``) over integer edge
  arrays; stages replay incrementally from the previous partition and removal probes
  start from one shared base partition (no rebuild per counterfactual);
* ``--read-corpus`` reads only the target component's majority allocation (every row,
  for the state breakdown) plus the files holding its other members, each sequentially,
  hashed and verified against the C05 plan (size, SHA-256, rows). The reference also
  scanned every row of every passenger allocation (e.g. all UltraX files) and mixed those
  rows into the SYNTH state counts; the breakdown is now one allocation's, stated in
  ``corpus_attribution.allocation``;
* the protected index is scanned once, in parallel, and every record is validated;
* live ``[FORENSICS]`` progress on stderr; stdout is only the final JSON report.

Nothing is written. Errors and progress are content-free: no text, document or group
ids, URLs, lineage keys, benchmark text or patterns, salts or paths.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
from array import array
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np
import numpy.typing as npt
import psutil

from xlm.data.dedup.lineage import canonical_url
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan
from xlm.data.exclusion.factstore import FACTS_DIR, open_unit
from xlm.data.exclusion.fitscan import MEMBERSHIP_KEYS, OrderedPool
from xlm.data.exclusion.grouping import components
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.supervisor import Deadline, Supervisor

LABEL = "FORENSICS"
WORKER_CHOICES = (1, 2, 4, 8, 16)
BLOCK_BYTES = 8 * MiB
LINE_CEILING = 64 * MiB
DECISIONS = ("kept", "duplicate", "excluded")
KEPT, DUPLICATE, EXCLUDED = 0, 1, 2
LABEL_RE = re.compile(r"^[a-z0-9_\-]{1,32}$")
URL_FIELDS = ("query_seed_url", "additional_seed_url")
METADATA_URL_FIELDS = ("url", "source_url", "canonical_url")
ALLOWED_HEX_FIELDS = ("plan_digest", "plan_code_commit")

Progress = RunProgress | NullProgress


class ForensicsError(Exception):
    """A refusal with a fixed, content-free reason."""


def key_of(component: str, view: str, upstream: str | None) -> str:
    return str(canonical.canonical_bytes([component, view, upstream]).decode())


class Fingerprint:
    def __init__(self, salt: bytes) -> None:
        self.salt = salt

    def __call__(self, value: bytes) -> str:
        return hmac.new(self.salt, value, hashlib.sha256).hexdigest()[:16]


def classify_key(key: bytes, own: bytes) -> str:
    if key.startswith(b"url:"):
        return "url"
    if key.startswith(b"parent:"):
        return "parent_self" if key.endswith(b":" + own) else "parent_ref"
    if key.startswith(b"metadata:"):
        parts = key[len(b"metadata:") :].split(b":", 3)
        rule = parts[2].decode("ascii", "replace") if len(parts) > 2 else "unknown"
        return "metadata:" + (rule if LABEL_RE.match(rule) else "unknown")
    return "other"


def url_shape(key: bytes) -> dict[str, Any]:
    """Content-free structure of a canonical URL key ``url://host/path?query``."""
    text = key[len(b"url:") :].decode("utf-8", "replace").removeprefix("//")
    host, _, rest = text.partition("/")
    path, _, query = rest.partition("?")
    segments = [s for s in path.split("/") if s]
    if host.endswith("wikipedia.org"):
        host_class = "wikipedia"
    elif host.endswith(("wikimedia.org", "wikidata.org", "wikibooks.org", "wiktionary.org")):
        host_class = "other_wikimedia"
    elif host in ("huggingface.co", "github.com", "arxiv.org"):
        host_class = host.split(".")[0]
    elif not host:
        host_class = "empty"
    else:
        host_class = "other"
    return {
        "host_class": host_class,
        "path_segments": len(segments),
        "has_query": bool(query),
        "domain_level": not segments,
    }


# -- worker side ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Tables:
    files: dict[str, int]  # plan path -> ordinal
    allocations: dict[str, int]  # allocation key -> code


_TABLES: Tables | None = None


def init_worker(tables: Tables) -> None:
    global _TABLES
    _TABLES = tables


def _tables() -> Tables:
    if _TABLES is None:
        raise ForensicsError("forensics worker tables missing")
    return _TABLES


@dataclass
class LedgerPart:
    rows: int
    file: npt.NDArray[np.int32]
    row: npt.NDArray[np.int32]  # 0-based unit row
    decision: npt.NDArray[np.int8]
    allocation: npt.NDArray[np.int16]
    nbytes: npt.NDArray[np.int64]
    families: list[str]  # lineage_group of each excluded row, in row order
    groups: list[str]  # duplicate_group of each excluded row, in row order


def parse_ledger(block: bytes) -> LedgerPart:
    """Strict parse + schema check of complete ledger lines (fixed refusals only)."""
    tables = _tables()
    lines = block.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    count = len(lines)
    file = np.empty(count, np.int32)
    row = np.empty(count, np.int32)
    decision = np.empty(count, np.int8)
    allocation = np.empty(count, np.int16)
    nbytes = np.empty(count, np.int64)
    families: list[str] = []
    groups: list[str] = []
    codes = {name: n for n, name in enumerate(DECISIONS)}
    cache: dict[tuple[str, str, str | None], int] = {}
    for n, line in enumerate(lines):
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ForensicsError("decision ledger record is not JSON") from exc
        if type(value) is not dict or value.keys() != MEMBERSHIP_KEYS:
            raise ForensicsError("decision ledger record schema")
        component, view, upstream = value["component"], value["view"], value["upstream_component"]
        size, number, path = value["bytes"], value["row"], value["file"]
        family, group = value["lineage_group"], value["duplicate_group"]
        code = codes.get(value["decision"]) if type(value["decision"]) is str else None
        if (
            code is None
            or type(component) is not str
            or type(view) is not str
            or (upstream is not None and type(upstream) is not str)
            or type(size) is not int
            or size < 0
            or type(number) is not int
            or number < 1
            or type(path) is not str
            or type(family) is not str
            or type(group) is not str
            or not family
            or not group
        ):
            raise ForensicsError("decision ledger record value")
        ordinal = tables.files.get(path)
        if ordinal is None:
            raise ForensicsError("decision ledger names a file outside the C05 plan")
        label = (component, view, upstream)
        alloc = cache.get(label)
        if alloc is None:
            alloc = tables.allocations.get(key_of(component, view, upstream))
            if alloc is None:
                raise ForensicsError("decision ledger allocation outside the C05 plan")
            cache[label] = alloc
        file[n], row[n], decision[n], allocation[n], nbytes[n] = (
            ordinal,
            number - 1,
            code,
            alloc,
            size,
        )
        if code == EXCLUDED:
            families.append(family)
            groups.append(group)
    return LedgerPart(count, file, row, decision, allocation, nbytes, families, groups)


@dataclass(frozen=True)
class CorpusTask:
    data: bytes  # complete canonical lines
    first: int  # 0-based row of the first line
    wanted: tuple[int, ...]  # rows (absolute) to parse
    members: frozenset[int]  # wanted rows whose URL keys are needed


@dataclass
class CorpusRow:
    row: int
    flags: str
    label: str
    keys: list[tuple[bytes, tuple[str, ...]]]


def scan_corpus(task: CorpusTask) -> list[CorpusRow]:
    """Seed-URL metadata of the wanted canonical rows (no text leaves the worker)."""
    lines = task.data.split(b"\n")
    out: list[CorpusRow] = []
    for row in task.wanted:
        line = lines[row - task.first]
        try:
            document = json.loads(line)
        except ValueError as exc:
            raise ForensicsError("cleaned corpus record is not JSON") from exc
        metadata = (document.get("source_metadata") or {}) if type(document) is dict else {}
        if type(metadata) is not dict:
            raise ForensicsError("cleaned corpus record metadata")
        flags: list[str] = []
        keys_here: dict[bytes, set[str]] = defaultdict(set)
        for name in URL_FIELDS + METADATA_URL_FIELDS:
            raw_url = metadata.get(name)
            if not isinstance(raw_url, str) or not raw_url.strip():
                continue
            parsed = urlsplit(raw_url) if name in URL_FIELDS else None
            if parsed is not None and not (
                parsed.scheme.lower() in ("http", "https") and parsed.netloc
            ):
                flags.append(name + ":non_http")
                continue
            flags.append(name)
            keys_here[b"url:" + canonical_url(raw_url).encode("utf-8")].add(name)
        label = metadata.get("exercise")
        out.append(
            CorpusRow(
                row,
                "+".join(sorted(flags)) or "no_url_metadata",
                label if isinstance(label, str) and LABEL_RE.match(label) else "other/absent",
                [(k, tuple(sorted(v))) for k, v in keys_here.items()]
                if row in task.members
                else [],
            )
        )
    return out


@dataclass(frozen=True)
class IndexTask:
    data: bytes
    wanted: frozenset[bytes]


def scan_index(task: IndexTask) -> list[tuple[bytes, int, list[str]]]:
    """(pattern identity, token length, provenance kinds) for wanted patterns, in order."""
    out: list[tuple[bytes, int, list[str]]] = []
    for line in task.data.split(b"\n"):
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise ForensicsError("protected index record is not JSON") from exc
        tokens = entry.get("tokens") if type(entry) is dict else None
        provenance = entry.get("provenance", []) if type(entry) is dict else None
        if type(tokens) is not list or type(provenance) is not list:
            raise ForensicsError("protected index record schema")
        try:
            identity = bytes.fromhex(canonical.digest(tokens))
        except canonical.CanonicalError as exc:
            raise ForensicsError("protected index record schema") from exc
        if identity in task.wanted:
            kinds = [str(ref).rsplit(":", 1)[-1] for ref in provenance]
            out.append((identity, len(tokens), kinds))
    return out


# -- parent: input streams ------------------------------------------------------------------


def blocks(path: Path, state: dict[str, Any], *, digest: Any = None) -> Iterator[bytes]:
    """Complete lines in order (a final line may lack LF); every byte read is counted."""
    pending = b""
    with path.open("rb", buffering=0) as stream:
        while data := stream.read(BLOCK_BYTES):
            state["bytes"] += len(data)
            if digest is not None:
                digest.update(data)
            data = pending + data if pending else data
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
            else:
                pending = data[cut:]
                yield data[:cut]
            if len(pending) > LINE_CEILING:
                raise ForensicsError("record exceeds the line ceiling")
    if pending:
        yield pending


def corpus_tasks(
    path: Path,
    state: dict[str, Any],
    digest: Any,
    cursor: dict[str, int],
    wanted_rows: Mapping[int, int],
    member_rows: set[int],
) -> Iterator[CorpusTask]:
    """Sequential, hashed blocks of one corpus file with the rows to parse in each."""
    for data in blocks(path, state, digest=digest):
        first = cursor["rows"]
        lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
        wanted = tuple(r for r in range(first, first + lines) if r in wanted_rows)
        yield CorpusTask(data, first, wanted, frozenset(r for r in wanted if r in member_rows))
        cursor["rows"] = first + lines


class Telemetry:
    """Process-tree RSS, peak and CPU (display only)."""

    def __init__(self) -> None:
        self.process = psutil.Process()
        self.peak = 0
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
        self.peak = max(self.peak, int(rss))
        values: dict[str, int | float] = {
            "rss": int(rss),
            "peak_rss": self.peak,
            "ram_limit": int(psutil.virtual_memory().total),
        }
        now = time.monotonic()
        if self.last is not None and now > self.last[0]:
            share = (cpu - self.last[1]) / (now - self.last[0]) / (psutil.cpu_count() or 1)
            values["cpu_percent"] = round(max(0.0, 100.0 * share), 1)
        self.last = (now, cpu)
        return values


# -- parent: the analysis -----------------------------------------------------------------


@dataclass
class Ledger:
    rows: int
    file: npt.NDArray[np.int32]
    row: npt.NDArray[np.int32]
    decision: npt.NDArray[np.int8]
    allocation: npt.NDArray[np.int16]
    nbytes: npt.NDArray[np.int64]
    excluded: npt.NDArray[np.int64]  # ledger positions of excluded rows, in order
    family: npt.NDArray[np.int64]  # family id per excluded row (first-seen order)
    group: npt.NDArray[np.int64]  # duplicate group id per excluded row


def read_ledger(path: Path, total_rows: int, pool: OrderedPool, progress: Progress) -> Ledger:
    size = path.stat().st_size
    state: dict[str, Any] = {"bytes": 0}
    parts: list[LedgerPart] = []
    family_ids: dict[str, int] = {}
    group_ids: dict[str, int] = {}
    family: array[int] = array("q")
    group: array[int] = array("q")
    done = results = 0
    started = time.monotonic()
    capacity = 2 * pool.workers
    for part in pool.map(parse_ledger, blocks(path, state), capacity):
        results += 1
        parts.append(part)
        for name in part.families:
            family.append(family_ids.setdefault(name, len(family_ids)))
        for name in part.groups:
            group.append(group_ids.setdefault(name, len(group_ids)))
        done += part.rows
        elapsed = max(time.monotonic() - started, 1e-9)
        progress.update(
            done,
            bytes_done=state["bytes"],
            bytes_total=size,
            mib_per_s=state["bytes"] / MiB / elapsed,
            workers=pool.workers,
            busy=pool.workers,
            results=results,
        )
    if done != total_rows:
        raise ForensicsError("decision ledger row count differs from the C05 plan")
    decision = np.concatenate([p.decision for p in parts]) if parts else np.zeros(0, np.int8)
    return Ledger(
        rows=done,
        file=np.concatenate([p.file for p in parts]) if parts else np.zeros(0, np.int32),
        row=np.concatenate([p.row for p in parts]) if parts else np.zeros(0, np.int32),
        decision=decision,
        allocation=np.concatenate([p.allocation for p in parts])
        if parts
        else np.zeros(0, np.int16),
        nbytes=np.concatenate([p.nbytes for p in parts]) if parts else np.zeros(0, np.int64),
        excluded=np.flatnonzero(decision == EXCLUDED).astype(np.int64),
        family=np.frombuffer(family, dtype=np.int64).copy(),
        group=np.frombuffer(group, dtype=np.int64).copy(),
    )


def run(args: argparse.Namespace, progress: Progress, stage: list[str]) -> dict[str, Any]:
    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        stage[0] = name
        progress.stage(name, total, unit)

    begin("PLAN VERIFY")
    plan = ExecutionPlan.model_validate(read_metadata(args.plan, digested=False))
    identity = plan.identity()
    work = Path(plan.scratch_root) / identity
    decisions_path = args.decisions or work / "decisions.jsonl"
    facts = args.facts or work / FACTS_DIR
    salt = os.environ[args.salt_env].encode() if args.salt_env else os.urandom(32)
    fp = Fingerprint(salt)
    files = list(plan.files)
    ordinal = {f.path: n for n, f in enumerate(files)}
    allocation_names = sorted({key_of(f.component, f.view, f.upstream_component) for f in files})
    allocation_code = {name: n for n, name in enumerate(allocation_names)}
    file_alloc = np.asarray(
        [allocation_code[key_of(f.component, f.view, f.upstream_component)] for f in files],
        dtype=np.int16,
    )
    focus_names: set[str] = set()
    for item in args.focus_allocation:
        component, view, upstream = item.split("/")
        focus_names.add(key_of(component, view, None if upstream == "-" else upstream))
    focus = {allocation_code[name] for name in focus_names if name in allocation_code}

    begin("INPUT DISCOVERY", 3)
    if not decisions_path.is_file():
        raise ForensicsError("decision ledger is missing")
    if not facts.is_dir():
        raise ForensicsError("fact unit directory is missing")
    if args.benchmark_index is not None and not args.benchmark_index.is_file():
        raise ForensicsError("protected index is missing")
    total_rows = sum(int(f.documents) for f in files)
    progress.update(3, force=True, files=len(files), documents=total_rows)

    tables = Tables(ordinal, allocation_code)
    supervisor = Supervisor(Deadline(None, time.monotonic()), None)
    with supervisor:
        with OrderedPool(
            args.workers, tables, supervisor, inline=args.workers == 1, initializer=init_worker
        ) as pool:
            begin("DECISION LEDGER", total_rows, "rows")
            ledger = read_ledger(decisions_path, total_rows, pool, progress)
            return analyse(
                args,
                plan,
                ledger,
                pool,
                progress,
                begin,
                fp,
                files,
                file_alloc,
                allocation_names,
                focus,
                focus_names,
                facts,
            )


def analyse(  # noqa: C901, PLR0912, PLR0915 - one linear forensic pipeline
    args: argparse.Namespace,
    plan: ExecutionPlan,
    ledger: Ledger,
    pool: OrderedPool,
    progress: Progress,
    begin: Any,
    fp: Fingerprint,
    files: Sequence[Any],
    file_alloc: npt.NDArray[np.int16],
    allocation_names: list[str],
    focus: set[int],
    focus_names: set[str],
    facts: Path,
) -> dict[str, Any]:

    # -- decisions by allocation; completion cross-check --------------------------------------
    begin("MAJOR COMPONENT", 3)
    tally = np.zeros((len(allocation_names), 3), np.int64)
    np.add.at(tally, (ledger.allocation.astype(np.int64), ledger.decision.astype(np.int64)), 1)
    decisions_by_allocation = {
        allocation_names[a]: {DECISIONS[d]: int(tally[a, d]) for d in range(3) if tally[a, d]}
        for a in range(len(allocation_names))
        if tally[a].any()
    }
    completion_path = Path(plan.output_root) / plan.identity() / "completion.json"
    completion_checked: bool | None = None
    if completion_path.is_file():
        payload = read_metadata(completion_path, digested=False).get("payload", {})
        signed = payload.get("allocations", {})
        for name in allocation_names:
            counts = signed.get(name, {"kept": 0, "duplicate": 0, "excluded": 0})
            row = tally[allocation_names.index(name)]
            if (int(row[KEPT]), int(row[DUPLICATE]), int(row[EXCLUDED])) != (
                counts.get("kept", 0),
                counts.get("duplicate", 0),
                counts.get("excluded", 0),
            ):
                raise ForensicsError("decision ledger disagrees with the C05 completion")
        completion_checked = True
    progress.update(1)
    family_sizes = np.bincount(ledger.family) if ledger.family.size else np.zeros(0, np.int64)
    if family_sizes.size < args.component_rank:
        raise ForensicsError("fewer excluded families than the requested rank")
    by_size = np.argsort(-family_sizes, kind="stable")
    target = int(by_size[args.component_rank - 1])
    selected = np.flatnonzero(ledger.family == target)
    member_pos = ledger.excluded[selected]  # ledger positions, ledger order
    n = int(member_pos.size)
    member_file = ledger.file[member_pos].astype(np.int64)
    member_row = ledger.row[member_pos].astype(np.int64)
    member_alloc = ledger.allocation[member_pos].astype(np.int64)
    member_bytes = ledger.nbytes[member_pos]
    member_group = ledger.group[selected]
    progress.update(3, force=True, documents=n)

    focus_mask = np.isin(ledger.allocation, np.asarray(sorted(focus), dtype=np.int16))
    focus_pos = np.flatnonzero(focus_mask)
    excluded_family_of = np.full(ledger.rows, -1, np.int64)
    excluded_family_of[ledger.excluded] = ledger.family

    # -- fact units -----------------------------------------------------------------------------
    begin("FACT UNITS", len(files), "files")
    by_file: dict[int, list[int]] = defaultdict(list)
    for m, f in enumerate(member_file.tolist()):
        by_file[f].append(m)
    focus_by_file: dict[int, list[int]] = defaultdict(list)
    for p in focus_pos.tolist():
        focus_by_file[int(ledger.file[p])].append(p)
    key_ids: dict[bytes, int] = {}
    key_class: list[str] = []
    inc_key: array[int] = array("q")
    inc_member: array[int] = array("q")
    member_ids: list[bytes] = [b""] * n
    member_parents: list[tuple[bytes, ...]] = [()] * n
    direct: dict[int, bytes] = {}
    focus_hits: dict[int, bytes] = {}  # ledger position -> pattern
    hits_by_allocation: Counter[str] = Counter()
    processed = 0
    for f, item in enumerate(files):
        hit_rows: dict[int, bytes] = {}
        path = facts / f"{f:05d}.unit"
        if not path.is_file():
            if f in by_file or f in focus_by_file:
                raise ForensicsError("a needed fact unit is missing")
            progress.update(f + 1)
            continue
        unit = open_unit(path)
        if unit.rows != int(item.documents):
            raise ForensicsError("fact unit does not match the C05 plan")
        hits = unit.hits()
        hits_by_allocation[allocation_names[int(file_alloc[f])]] += int(hits.shape[0])
        if f in by_file or f in focus_by_file:
            hit_rows = {int(h["row"]): bytes(h["pattern"]) for h in hits}
        if f in by_file:
            ids_blob, ids_off = unit.ids()
            blob, offsets, counts, _ = unit.strings("lineage")
            pblob, poffsets, pcounts, _ = unit.strings("parents")
            ids_b, ids_o = bytes(ids_blob), ids_off.tolist()
            blob_b, off = bytes(blob), offsets.tolist()
            pblob_b, poff = bytes(pblob), poffsets.tolist()
            starts = np.concatenate([[0], np.cumsum(counts, dtype=np.int64)]).tolist()
            pstarts = np.concatenate([[0], np.cumsum(pcounts, dtype=np.int64)]).tolist()
            for m in by_file[f]:
                r = int(member_row[m])
                own = ids_b[ids_o[r] : ids_o[r + 1]]
                member_ids[m] = own
                for k in range(starts[r], starts[r + 1]):
                    text = blob_b[off[k] : off[k + 1]]
                    index = key_ids.get(text)
                    if index is None:
                        index = key_ids[text] = len(key_class)
                        key_class.append(classify_key(text, own))
                    inc_key.append(index)
                    inc_member.append(m)
                member_parents[m] = tuple(
                    pblob_b[poff[k] : poff[k + 1]] for k in range(pstarts[r], pstarts[r + 1])
                )
                if r in hit_rows:
                    direct[m] = hit_rows[r]
                processed += 1
        for p in focus_by_file.get(f, []):
            r = int(ledger.row[p])
            if r in hit_rows:
                focus_hits[p] = hit_rows[r]
        unit.close()
        progress.update(f + 1, members=processed, keys=len(key_class), direct_hits=len(direct))
    key_bytes = list(key_ids)
    incidence_key = np.frombuffer(inc_key, dtype=np.int64).copy()
    incidence_member = np.frombuffer(inc_member, dtype=np.int64).copy()

    # -- protected index ----------------------------------------------------------------------
    wanted_patterns = frozenset({*direct.values(), *focus_hits.values()})
    pattern_info: dict[bytes, dict[str, Any]] = {}
    if args.benchmark_index is not None and wanted_patterns:
        size = args.benchmark_index.stat().st_size
        begin("BENCHMARK INDEX", size, "bytes")
        state: dict[str, Any] = {"bytes": 0}
        index_jobs = (IndexTask(b, wanted_patterns) for b in blocks(args.benchmark_index, state))
        for matches in pool.map(scan_index, index_jobs, 2 * pool.workers):
            for identity, length, kinds in matches:
                info = pattern_info.setdefault(
                    identity, {"token_length": length, "kinds": Counter()}
                )
                for kind in kinds:
                    info["kinds"][kind if LABEL_RE.match(kind) else "other"] += 1
            progress.update(
                state["bytes"],
                matched_patterns=len(pattern_info),
                workers=pool.workers,
                busy=pool.workers,
            )

    # -- corpus: seed-URL field attribution -----------------------------------------------------
    member_index = {(int(member_file[m]), int(member_row[m])): m for m in range(n)}
    qsu_key = np.full(n, -1, np.int64)
    asu_key = np.full(n, -1, np.int64)
    other_names: dict[tuple[int, int], set[str]] = {}
    attribution = False
    corpus: dict[str, Any] = {}
    allocations = Counter(member_alloc.tolist())
    majority = (
        max(allocations, key=lambda a: (allocations[a], -int(np.argmax(member_alloc == a))))
        if allocations
        else None
    )
    if args.read_corpus and majority is not None:
        root = Path(plan.data_root)
        read = sorted(
            {f for f in range(len(files)) if int(file_alloc[f]) == majority} | set(by_file)
        )
        state_of = np.full(ledger.rows, -1, np.int8)  # majority rows: 0/1/2 decision, 3 target
        majority_pos = np.flatnonzero(ledger.allocation == majority)
        state_of[majority_pos] = ledger.decision[majority_pos]
        state_of[member_pos[member_alloc == majority]] = 3  # breakdown: majority rows only
        position_of: dict[int, dict[int, int]] = defaultdict(dict)
        for p in majority_pos.tolist():
            position_of[int(ledger.file[p])][int(ledger.row[p])] = p
        for m in range(n):
            position_of[int(member_file[m])][int(member_row[m])] = int(member_pos[m])
        total_bytes = sum(int(files[f].file_bytes) for f in read)
        total_docs = sum(int(files[f].documents) for f in read)
        begin("CORPUS SCAN", total_docs, "docs")
        presence: dict[str, Counter[str]] = defaultdict(Counter)
        exercise: dict[str, Counter[str]] = defaultdict(Counter)
        found_keys = missing_keys = 0
        scanned_bytes = scanned_docs = 0
        started = time.monotonic()
        state_name = {0: "kept", 1: "duplicate", 2: "excluded", 3: "target_component"}
        for count, f in enumerate(read, 1):
            item = files[f]
            wanted_rows = position_of[f]
            stream_state: dict[str, Any] = {"bytes": 0}
            digest = hashlib.sha256()
            cursor = {"rows": 0}
            member_rows = {r for (g, r) in member_index if g == f}
            jobs = corpus_tasks(
                root / item.path, stream_state, digest, cursor, wanted_rows, member_rows
            )
            for result in pool.map(scan_corpus, jobs, 2 * pool.workers):
                for scanned in result:
                    code = int(state_of[wanted_rows[scanned.row]])
                    if code >= 0:
                        presence[state_name[code]][scanned.flags] += 1
                        exercise[state_name[code]][scanned.label] += 1
                    member = member_index.get((f, scanned.row))
                    if member is None:
                        continue
                    for text, names in scanned.keys:
                        found = key_ids.get(text)
                        if found is None:
                            missing_keys += 1
                            continue
                        found_keys += 1
                        attribution = True
                        rest = set(names)
                        if "query_seed_url" in rest:
                            qsu_key[member] = found
                            rest.discard("query_seed_url")
                        if "additional_seed_url" in rest:
                            asu_key[member] = found
                            rest.discard("additional_seed_url")
                        if rest:
                            other_names.setdefault((member, found), set()).update(rest)
                elapsed = max(time.monotonic() - started, 1e-9)
                progress.update(
                    scanned_docs + cursor["rows"],
                    bytes_done=scanned_bytes + stream_state["bytes"],
                    bytes_total=total_bytes,
                    files_committed=count - 1,
                    files_total=len(read),
                    mib_per_s=(scanned_bytes + stream_state["bytes"]) / MiB / elapsed,
                    workers=pool.workers,
                    busy=pool.workers,
                )
            if (
                stream_state["bytes"],
                digest.hexdigest(),
                cursor["rows"],
            ) != (int(item.file_bytes), item.documents_sha256, int(item.documents)):
                raise ForensicsError("cleaned corpus file differs from the C05 plan")
            scanned_bytes += stream_state["bytes"]
            scanned_docs += cursor["rows"]
            progress.update(
                scanned_docs,
                bytes_done=scanned_bytes,
                bytes_total=total_bytes,
                files_committed=count,
                files_total=len(read),
            )
        corpus = {
            "allocation": allocation_names[majority],
            "files_read": len(read),
            "files_verified": len(read),
            "bytes_read": scanned_bytes,
            "url_metadata_presence_by_state": {k: dict(v) for k, v in sorted(presence.items())},
            "exercise_label_by_state": {
                k: dict(v.most_common(args.top)) for k, v in sorted(exercise.items())
            },
            "recomputed_url_keys_found_in_units": found_keys,
            "recomputed_url_keys_missing_from_units": missing_keys,
        }

    # -- graph build ----------------------------------------------------------------------------
    begin("GRAPH BUILD", 4)
    keys = len(key_class)
    class_names = sorted(set(key_class))
    key_code = np.asarray([class_names.index(c) for c in key_class], np.int64)

    def names_of(m: int, k: int) -> set[str]:
        names = set(other_names.get((m, k), ()))
        if qsu_key[m] == k:
            names.add("query_seed_url")
        if asu_key[m] == k:
            names.add("additional_seed_url")
        return names

    def incidence_classes(ks: npt.NDArray[np.int64], ms: npt.NDArray[np.int64]) -> list[str]:
        base = [class_names[c] for c in key_code[ks].tolist()]
        if not attribution:
            return base
        out = []
        for c, k, m in zip(base, ks.tolist(), ms.tolist(), strict=True):
            if c != "url":
                out.append(c)
                continue
            names = names_of(m, k)
            if names == {"query_seed_url"} or "query_seed_url" in names:
                out.append("url:query_seed_url")
            elif names == {"additional_seed_url"}:
                out.append("url:additional_seed_url")
            else:
                out.append("url:" + ("+".join(sorted(names)) or "unattributed"))
        return out

    # Per key, in incidence (= reference posting) order.
    order = np.argsort(incidence_key, kind="stable")
    sorted_keys = incidence_key[order]
    sorted_members = incidence_member[order]
    bounds = np.searchsorted(sorted_keys, np.arange(keys + 1))
    sizes = np.diff(bounds)
    progress.update(1, force=True, nodes=n, keys=keys, incidences=int(order.size))
    edge_a: dict[str, list[npt.NDArray[np.int64]]] = defaultdict(list)
    edge_b: dict[str, list[npt.NDArray[np.int64]]] = defaultdict(list)
    edge_k: dict[str, list[npt.NDArray[np.int64]]] = defaultdict(list)
    # Duplicate relation (star from each group's first member) and parent edges.
    if n:
        _, first_index, group_of = np.unique(member_group, return_index=True, return_inverse=True)
        anchors = first_index[group_of]
        keep = anchors != np.arange(n)
        edge_a["duplicate"].append(anchors[keep].astype(np.int64))
        edge_b["duplicate"].append(np.flatnonzero(keep).astype(np.int64))
        edge_k["duplicate"].append(np.full(int(keep.sum()), -1, np.int64))
    id_to_member = {member_ids[m]: m for m in range(n)}
    parents = [
        (m, id_to_member[p]) for m in range(n) for p in member_parents[m] if p in id_to_member
    ]
    if parents:
        edge_a["parent_edge"].append(np.asarray([a for a, _ in parents], np.int64))
        edge_b["parent_edge"].append(np.asarray([b for _, b in parents], np.int64))
        edge_k["parent_edge"].append(np.full(len(parents), -1, np.int64))
    progress.update(2, force=True, nodes=n, keys=keys)
    multi = np.flatnonzero(sizes >= 2)
    if multi.size:
        # Incidences of keys shared by 2+ members, grouped by key in posting order.
        rows = np.flatnonzero(np.repeat(sizes >= 2, sizes))
        ks, ms = sorted_keys[rows], sorted_members[rows]
        classes = incidence_classes(ks, ms)
        is_seed = np.asarray([c == "url:query_seed_url" for c in classes], dtype=bool)
        # Anchor (reference): the key's first query-seed incidence, else its first one.
        first_any = np.searchsorted(rows, bounds[multi])
        seed_pos = np.where(is_seed, np.arange(rows.size), rows.size)
        first_seed = np.minimum.reduceat(seed_pos, first_any)
        anchor_pos = np.where(first_seed < rows.size, first_seed, first_any)
        anchor_member = ms[anchor_pos[np.searchsorted(multi, ks)]]
        keep_edge = ms != anchor_member
        cls_array = np.asarray(classes, dtype=object)
        for cls in sorted(set(classes)):
            mask = keep_edge & (cls_array == cls)
            edge_a[cls].append(anchor_member[mask])
            edge_b[cls].append(ms[mask])
            edge_k[cls].append(ks[mask])
    edges = {
        c: (np.concatenate(edge_a[c]), np.concatenate(edge_b[c]), np.concatenate(edge_k[c]))
        for c in edge_a
    }
    edges = {c: v for c, v in edges.items() if v[0].size}
    progress.update(4, force=True, nodes=n, edges=sum(v[0].size for v in edges.values()))

    def stage_rank(cls: str) -> int:
        fixed = {"duplicate": 0, "parent_edge": 1, "url:query_seed_url": 3}
        return fixed.get(cls, 4 if cls.startswith("url") else 2)

    classes_order = sorted(edges, key=lambda c: (stage_rank(c), c))
    hit_members = np.asarray(sorted(direct), np.int64)

    def roots_of(
        a: npt.NDArray[np.int64], b: npt.NDArray[np.int64], initial: Any = None
    ) -> npt.NDArray[np.uint32]:
        return components(n, a, b, initial=initial)

    def summary(roots: npt.NDArray[np.uint32]) -> tuple[int, int, int]:
        if not n:
            return 0, 0, 0
        count = int(np.count_nonzero(roots == np.arange(n, dtype=np.uint32)))
        largest = int(np.bincount(roots).max())
        hit_roots = np.unique(roots[hit_members]) if hit_members.size else np.zeros(0, np.uint32)
        excluded = int(np.count_nonzero(np.isin(roots, hit_roots)))
        return count, largest, excluded

    probe_keys_sorted = np.lexsort((-np.arange(keys), -sizes)) if keys else np.zeros(0, np.int64)
    shared = [int(k) for k in probe_keys_sorted.tolist() if sizes[k] > 1]
    probe_keys = shared[: args.removal_probes]
    begin("UNION / COMPONENTS", len(classes_order) * 2, "unions")
    done_unions = 0
    stages = []
    roots: Any = None
    previous = n
    for cls in classes_order:
        a, b, _ = edges[cls]
        roots = roots_of(a, b, roots)
        count, largest, excluded = summary(roots)
        stages.append(
            {
                "after_class": cls,
                "candidate_edges": int(a.size),
                "merges": previous - count,
                "components": count,
                "largest_component": largest,
                "would_be_excluded": excluded,
            }
        )
        previous = count
        done_unions += 1
        progress.update(done_unions, components=count, largest=largest)
    final_count = previous if classes_order else n
    reconstruction_complete = final_count == 1
    alone = {}
    for cls in classes_order:
        a, b, _ = edges[cls]
        count, largest, _ = summary(roots_of(a, b))
        alone[cls] = {"components": count, "largest_component": largest}
        done_unions += 1
        progress.update(done_unions, components=count, largest=largest)

    begin("COUNTERFACTUALS", 3 + len(probe_keys), "unions")

    def union_of(names: Sequence[str]) -> npt.NDArray[np.uint32]:
        picked = [edges[c] for c in names if c in edges]
        if not picked:
            return np.arange(n, dtype=np.uint32)
        return roots_of(
            np.concatenate([p[0] for p in picked]), np.concatenate([p[1] for p in picked])
        )

    seed_roots = union_of(["duplicate", "parent_edge", "url:query_seed_url"])
    hit_roots = np.unique(seed_roots[hit_members]) if hit_members.size else np.zeros(0, np.uint32)
    same_seed = np.isin(seed_roots, hit_roots)
    one_hop = same_seed.copy()
    for cls, (a, b, _) in edges.items():
        if cls.startswith("url:") and cls != "url:query_seed_url" and a.size:
            one_hop[b[np.isin(seed_roots[a], hit_roots)]] = True
    progress.update(1)
    dup_only = union_of(["duplicate", "parent_edge"])
    counterfactuals = {
        "duplicate_and_parent_only": summary(dup_only)[2],
        "same_seed_family": int(np.count_nonzero(same_seed)),
        "same_seed_family_plus_one_hop_additional_seed": int(np.count_nonzero(one_hop)),
        "current_transitive_family": n,
    }
    progress.update(2)
    # Removal probes: one shared base without every probed key, then add the others back.
    all_a = np.concatenate([v[0] for v in edges.values()]) if edges else np.zeros(0, np.int64)
    all_b = np.concatenate([v[1] for v in edges.values()]) if edges else np.zeros(0, np.int64)
    all_k = np.concatenate([v[2] for v in edges.values()]) if edges else np.zeros(0, np.int64)
    probed = np.isin(all_k, np.asarray(probe_keys, np.int64))
    base = roots_of(all_a[~probed], all_b[~probed])
    progress.update(3)
    probes = []
    for done, k in enumerate(probe_keys, 4):
        back = probed & (all_k != k)
        count, largest, _ = summary(roots_of(all_a[back], all_b[back], base))
        probes.append(
            {
                "removed_fingerprint": fp(key_bytes[k]),
                "components": count,
                "largest_component": largest,
            }
        )
        progress.update(done)

    begin("EDGE ATTRIBUTION", 4)
    dominant = []
    for k in shared[: args.top]:
        members_k = sorted_members[bounds[k] : bounds[k + 1]].tolist()
        entry: dict[str, Any] = {
            "fingerprint": fp(key_bytes[k]),
            "class": key_class[k],
            "members": int(sizes[k]),
            "allocations": dict(Counter(allocation_names[int(member_alloc[m])] for m in members_k)),
        }
        if key_class[k] == "url":
            entry["shape"] = url_shape(key_bytes[k])
            if attribution:
                entry["fields"] = dict(
                    Counter("+".join(sorted(names_of(m, k))) or "unattributed" for m in members_k)
                )
        dominant.append(entry)
    progress.update(1)
    histogram: dict[str, Counter[str]] = defaultdict(Counter)
    for k in range(keys):
        size = int(sizes[k])
        bucket = (
            "1"
            if size == 1
            else "2"
            if size == 2
            else "3-10"
            if size <= 10
            else ("11-100" if size <= 100 else "101-1000" if size <= 1000 else ">1000")
        )
        histogram[key_class[k]][bucket] += 1
    pairs: dict[str, Counter[str]] = {}
    for cls, (a, b, _) in edges.items():
        low = np.minimum(member_alloc[a], member_alloc[b])
        high = np.maximum(member_alloc[a], member_alloc[b])
        codes, counts = np.unique(low * 65536 + high, return_counts=True)
        pairs[cls] = Counter(
            {
                "|".join(
                    sorted({allocation_names[int(c) // 65536], allocation_names[int(c) % 65536]})
                ): int(v)
                for c, v in zip(codes.tolist(), counts.tolist(), strict=True)
            }
        )
    progress.update(2)
    outsiders = [m for m in range(n) if member_alloc[m] != majority][:50]
    touching: dict[int, Counter[str]] = {m: Counter() for m in outsiders}
    partners: dict[int, Counter[str]] = {m: Counter() for m in outsiders}
    if outsiders:
        wanted = np.asarray(outsiders, np.int64)
        for cls, (a, b, _) in edges.items():
            for one, other in ((a, b), (b, a)):
                for i in np.flatnonzero(np.isin(one, wanted)).tolist():
                    touching[int(one[i])][cls] += 1
                    partners[int(one[i])][allocation_names[int(member_alloc[other[i]])]] += 1
    group_size = Counter(member_group.tolist())
    attachments = [
        {
            "allocation": allocation_names[int(member_alloc[m])],
            "direct_hit": m in direct,
            "edges_by_class": dict(touching[m]),
            "partner_allocations": dict(partners[m]),
            "duplicate_group_size": group_size[int(member_group[m])],
        }
        for m in outsiders
    ]
    progress.update(4)

    def pattern_summary(hits: Mapping[Any, bytes]) -> dict[str, Any]:
        per_pattern = Counter(hits.values())
        top = []
        for pattern, docs in per_pattern.most_common(args.top):
            item: dict[str, Any] = {"fingerprint": fp(pattern), "documents": docs}
            if pattern in pattern_info:
                item["token_length"] = pattern_info[pattern]["token_length"]
                item["kinds"] = dict(pattern_info[pattern]["kinds"])
            top.append(item)
        return {
            "direct_hit_documents": len(hits),
            "distinct_patterns": len(per_pattern),
            "top_patterns": top,
            "documents_in_top_5_patterns": sum(d for _, d in per_pattern.most_common(5)),
        }

    begin("FOCUS ANALYSIS", len(focus_names), "allocations")
    focus_report: dict[str, Any] = {}
    for done, name in enumerate(sorted(focus_names), 1):
        code = allocation_names.index(name) if name in allocation_names else -1
        positions = focus_pos[ledger.allocation[focus_pos] == code]
        hits = {p: focus_hits[p] for p in positions.tolist() if p in focus_hits}
        decision = ledger.decision[positions]
        excluded = positions[decision == EXCLUDED]
        direct_excluded = sum(1 for p in excluded.tolist() if p in hits)
        sizes_of = family_sizes[excluded_family_of[excluded]] if excluded.size else np.zeros(0)
        buckets = Counter(
            "1" if s == 1 else "2-10" if s <= 10 else ">10" for s in sizes_of.tolist()
        )
        size_buckets: dict[str, Counter[str]] = defaultdict(Counter)
        for p, b in zip(positions.tolist(), ledger.nbytes[positions].tolist(), strict=True):
            bucket = (
                "<10KB"
                if b < 10_000
                else "10-100KB"
                if b < 100_000
                else ("100KB-1MB" if b < 1_000_000 else ">=1MB")
            )
            size_buckets[bucket]["documents"] += 1
            size_buckets[bucket]["direct_hits"] += int(p in hits)
        counts = Counter(DECISIONS[d] for d in decision.tolist())
        focus_report[name] = {
            "documents": int(positions.size),
            "decisions": dict(counts),
            "excluded_with_direct_hit": direct_excluded,
            "excluded_only_through_propagation": int(excluded.size) - direct_excluded,
            "excluded_family_size_buckets": dict(buckets),
            "distinct_excluded_families": int(np.unique(excluded_family_of[excluded]).size),
            "direct_hit_rate_by_document_size": {
                k: dict(v) for k, v in sorted(size_buckets.items())
            },
            "hit_patterns": pattern_summary(hits),
        }
        progress.update(done)

    begin("REPORT VERIFY", 2)
    member_hits = Counter(allocation_names[int(member_alloc[m])] for m in direct)
    body: dict[str, Any] = {
        "kind": "c05_component_forensics_v1",
        "content_free": True,
        "fingerprints": "HMAC-SHA256 with a per-run salt (or --salt-env), 16 hex",
        "plan_digest": plan.identity(),
        "plan_code_commit": plan.code_commit,
        "decisions_by_allocation": dict(sorted(decisions_by_allocation.items())),
        "direct_hit_documents_by_allocation": dict(sorted(hits_by_allocation.items())),
        "excluded_families": int(family_sizes.size),
        "ledger_matches_completion": completion_checked,
        "target_component": {
            "rank": args.component_rank,
            "documents": n,
            "bytes": int(member_bytes.sum()),
            "documents_by_allocation": dict(
                sorted((allocation_names[a], c) for a, c in allocations.items())
            ),
            "duplicate_groups": len(group_size),
            "distinct_lineage_keys": keys,
            "lineage_keys_shared_by_2plus_members": int(np.count_nonzero(sizes > 1)),
            "distinct_url_keys": sum(1 for c in key_class if c == "url"),
            "distinct_seed_url_keys": int(np.unique(qsu_key[qsu_key >= 0]).size)
            if attribution
            else None,
            "graph_nodes": n,
            "graph_candidate_edges": int(sum(v[0].size for v in edges.values())),
            "edges_by_class": {c: int(v[0].size) for c, v in sorted(edges.items())},
            "edges_by_allocation_pair": {c: dict(v) for c, v in sorted(pairs.items())},
            "lineage_key_posting_histogram": {c: dict(v) for c, v in sorted(histogram.items())},
            "direct_hit_documents": len(direct),
            "direct_hits_by_allocation": dict(member_hits),
            "excluded_only_through_propagation": n - len(direct),
            "hit_roots_before_bridging": int(hit_roots.size),
            "exclusion_counterfactuals": counterfactuals,
            "hit_patterns": pattern_summary(direct),
            "staged_union": stages,
            "each_class_alone": alone,
            "reconstruction_complete": reconstruction_complete,
            "dominant_keys": dominant,
            "single_key_removal_probes": probes,
            "non_majority_members": attachments,
        },
        "corpus_attribution": corpus or None,
        "focus_allocations": focus_report,
    }
    progress.update(1)
    verify_content_free(body)
    progress.update(2)
    return body


def verify_content_free(body: Mapping[str, Any]) -> None:
    """Refuse a report that could carry a URL, an id-like or hash-like value."""

    def scrub(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return None if key in ALLOWED_HEX_FIELDS else value

    text = json.dumps(scrub(body), sort_keys=True)
    if "://" in text or re.search(r"https?:", text) or re.search(r"[0-9a-f]{32,}", text):
        raise ForensicsError("report would not be content-free")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--plan", type=Path, required=True, help="the C05 plan (pNNNN.json)")
    result.add_argument("--decisions", type=Path, help="default: <scratch>/<plan>/decisions.jsonl")
    result.add_argument("--facts", type=Path, help="default: <scratch>/<plan>/facts")
    result.add_argument("--component-rank", type=int, default=1, help="1 = largest excluded")
    result.add_argument(
        "--focus-allocation",
        action="append",
        default=[],
        metavar="COMPONENT/VIEW/UPSTREAM",
        help="per-allocation exclusion analysis (use '-' for a null upstream)",
    )
    result.add_argument("--read-corpus", action="store_true", help="attribute seed-URL fields")
    result.add_argument("--benchmark-index", type=Path, help="protected index.jsonl (optional)")
    result.add_argument("--salt-env", help="env var holding a fingerprint salt (default random)")
    result.add_argument("--top", type=int, default=20)
    result.add_argument("--removal-probes", type=int, default=5)
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
    except (ForensicsError, C05Error) as exc:
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
    """Content-free refusal on stderr; stdout stays empty."""
    record = {"refused": True, "error_type": kind, "stage": stage}
    if reason is not None:
        record["reason"] = reason
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
