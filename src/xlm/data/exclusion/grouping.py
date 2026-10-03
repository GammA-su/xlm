"""Compact external C05 grouping over immutable fact units (no SQLite).

Logical contract: identical to the historical ``DiskGroups.group`` (reference
engine). Every historical union links the lexicographically larger root under
the smaller one, so each final root is the minimum document id of its connected
component, independent of union order. Dense document numbers are assigned in
exact UTF-8 byte order of document ids (SQLite BINARY collation), so "minimum id"
is "minimum dense number" and every ``ORDER BY id`` is ascending dense order.

* duplicate components: equal exact hashes, plus near pairs whose historical
  candidate generation (below) and ``estimated_jaccard >= near_threshold`` hold;
* family components: duplicate components, equal lineage keys and parent edges
  whose parent document exists.

Near candidates are replayed exactly, document by document in id order: band keys
in ``MinHasher.band_keys`` order; a bucket (all documents sharing the key) larger
than ``max_bucket_size`` is skipped and counted (against the ceiling) each time a
member visits it; otherwise members with smaller ids are added in ascending order,
skipping repeats, until adding one more would exceed
``max_candidates_per_document`` (the document is then "capped" and its remaining
bands are not visited); candidates are compared in ascending id order, each
comparison reserved before it happens. Buckets are built per band by sorting
(12-byte key, dense) pairs; only buckets with at least two members are retained.
"""

from __future__ import annotations

import hashlib
import heapq
import os
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.extsort import ExternalSorter
from xlm.data.exclusion.factstore import IndexLedger, Unit, open_unit, tree_bytes
from xlm.data.exclusion.policy import C05Error, ProductionPolicy, Resources
from xlm.data.exclusion.progress import NullProgress, Progress
from xlm.data.exclusion.scanpool import Pool, ordered_jobs, run_jobs
from xlm.data.exclusion.scanprep import PERMUTATIONS, FileContext

U32_LIMIT: Final = 2**32 - 2
BANDS: Final = 32
SPLIT_NAMES: Final = ("train", "diagnostic_val", "audit")
POSTING: Final = np.dtype([("doc", "<u4"), ("start", "<u4"), ("pos", "<u4"), ("length", "<u4")])
LINEAGE: Final = np.dtype(
    [
        ("w0", "<u8"),
        ("w1", "<u8"),
        ("w2", "<u8"),
        ("w3", "<u8"),
        ("dense", "<u4"),
        ("unit", "<u4"),
        ("key", "<u8"),
    ]
)
LINEAGE_KEYS: Final = ("w0", "w1", "w2", "w3", "dense")
#: Group artifacts kept for publication and bound by the signed group seal.
SEALED: Final = (
    "ids.bin",
    "ids.off",
    "where.u32",
    "dup.u32",
    "fam.u32",
    "survivor.u8",
    "families.u32",
    "fam_ordering.b32",
    "fam_bytes.i64",
    "fam_hit.u8",
    "fam_split.u8",
    "fam_quick.u8",
)


@dataclass(frozen=True)
class MemoryPlan:
    """Deterministic buffer sizes derived from the reviewed RAM ceiling.

    Budgets are fractions of ``ram_bytes`` minus a fixed allowance for the
    interpreter, the scan workers' matcher mappings and OS headroom; they change
    only performance (passes, runs), never results.
    """

    band_pass: int  # bands whose keys are resident at once
    lineage_run_records: int
    near_chunk_docs: int
    job_docs: int

    @staticmethod
    def derive(resources: Resources, documents: int) -> MemoryPlan:
        usable = max(512 * 1024**2, resources.ram_bytes - 4 * 1024**3) // 2
        per_band = max(1, documents) * 40  # 12-byte key + sort temporaries per document
        band_pass = int(max(1, min(BANDS, usable // 2 // per_band)))
        lineage_run = int(max(1 << 16, usable // 4 // (LINEAGE.itemsize * 3)))
        return MemoryPlan(
            band_pass=band_pass,
            lineage_run_records=lineage_run,
            near_chunk_docs=1 << 18,
            job_docs=1 << 14,
        )


@dataclass(frozen=True)
class GroupInit:
    """Content-free paths and constants a group child needs (pickled once)."""

    units: tuple[str, ...]
    offsets: tuple[int, ...]
    group: str
    seed: int
    permutations: int
    bands: int
    contexts: tuple[FileContext, ...]


def _band_payload_keys(signature: Sequence[int], bands: Sequence[int], rows: int) -> bytes:
    """``MinHasher.band_keys`` restricted to ``bands`` (raw 12-byte digests)."""
    out = []
    for band in bands:
        chunk = signature[band * rows : (band + 1) * rows]
        payload = f"{band}:" + ",".join(str(v) for v in chunk)
        out.append(hashlib.blake2b(payload.encode("utf-8"), digest_size=12).digest())
    return b"".join(out)


class _Arrays:
    """Lazily memory-mapped read-only grouping files inside a child or parent."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.maps: dict[str, Any] = {}

    def get(self, name: str, dtype: Any) -> Any:
        mapped = self.maps.get(name)
        if mapped is None:
            path = self.directory / name
            mapped = (
                np.memmap(path, dtype=dtype, mode="r")
                if path.stat().st_size
                else np.zeros(0, dtype=dtype)
            )
            self.maps[name] = mapped
        return mapped

    def id_strings(self, dense: npt.NDArray[Any]) -> list[str]:
        blob = self.get("ids.bin", np.uint8)
        offsets = self.get("ids.off", "<u8")
        return [
            bytes(blob[int(offsets[d]) : int(offsets[d + 1])]).decode("utf-8")
            for d in dense.tolist()
        ]

    def close(self) -> None:
        self.maps.clear()


class GroupRole:
    """Child handler for grouping/publication jobs (read-only mmaps only)."""

    def __init__(self, init: GroupInit) -> None:
        self.init = init
        self.units = [open_unit(Path(path)) for path in init.units]
        self.offsets = np.array(init.offsets, dtype=np.int64)
        self.arrays = _Arrays(Path(init.group))

    def close(self) -> None:
        for unit in self.units:
            unit.close()
        self.arrays.close()

    def __call__(self, job: tuple[Any, ...]) -> Any:
        kind = job[0]
        if kind == "bands":
            return self._bands(*job[1:])
        if kind == "orderings":
            return self._orderings(*job[1:])
        if kind == "digest_docs":
            return self._digest_docs(*job[1:])
        if kind == "digest_families":
            return self._digest_families(*job[1:])
        if kind == "publish":
            return self._publish(*job[1:])
        raise C05Error("unknown grouping job")

    def _bands(self, unit: int, lo: int, hi: int, bands: tuple[int, ...]) -> tuple[int, int, bytes]:
        signatures = self.units[unit].signatures()
        rows = self.init.permutations // self.init.bands
        keys = b"".join(
            _band_payload_keys(signatures[r].tolist(), bands, rows) for r in range(lo, hi)
        )
        return unit, lo, keys

    def _orderings(self, lo: int, hi: int) -> tuple[int, bytes]:
        families = self.arrays.get("families.u32", "<u4")[lo:hi]
        seed = self.init.seed
        digests = b"".join(
            hashlib.sha256(canonical.canonical_bytes([seed, family])).digest()
            for family in self.arrays.id_strings(np.asarray(families))
        )
        return lo, digests

    def _digest_docs(self, index: int, lo: int, hi: int) -> tuple[int, bytes]:
        dense = np.arange(lo, hi, dtype=np.int64)
        dup = np.asarray(self.arrays.get("dup.u32", "<u4")[lo:hi])
        fam = np.asarray(self.arrays.get("fam.u32", "<u4")[lo:hi])
        survivor = np.asarray(self.arrays.get("survivor.u8", np.uint8)[lo:hi]).tolist()
        ids = self.arrays.id_strings(dense)
        dups = self.arrays.id_strings(dup)
        fams = self.arrays.id_strings(fam)
        rows = [
            canonical.canonical_bytes([a, b, c, s])
            for a, b, c, s in zip(ids, dups, fams, survivor, strict=True)
        ]
        return index, b"".join(rows)

    def _digest_families(self, index: int, lo: int, hi: int) -> tuple[int, bytes]:
        families = np.asarray(self.arrays.get("families.u32", "<u4")[lo:hi])
        ordering = np.asarray(self.arrays.get("fam_ordering.b32", np.uint8)).reshape(-1, 32)
        sizes = self.arrays.get("fam_bytes.i64", "<i8")[lo:hi].tolist()
        hits = self.arrays.get("fam_hit.u8", np.uint8)[lo:hi].tolist()
        splits = self.arrays.get("fam_split.u8", np.uint8)[lo:hi].tolist()
        quick = self.arrays.get("fam_quick.u8", np.uint8)[lo:hi].tolist()
        ids = self.arrays.id_strings(families)
        rows = [
            canonical.canonical_bytes(
                [
                    ids[i],
                    bytes(ordering[lo + i]).hex(),
                    sizes[i],
                    hits[i],
                    SPLIT_NAMES[splits[i]],
                    quick[i],
                ]
            )
            for i in range(hi - lo)
        ]
        return index, b"".join(rows)

    def _publish(self, index: int, lo: int, hi: int) -> tuple[int, bytes, bytes, dict[str, Any]]:
        from xlm.data.exclusion.publish import publish_rows

        return publish_rows(self, index, lo, hi)


# --- union-find over dense ids ------------------------------------------------------


def _compress(parent: npt.NDArray[np.int64]) -> int:
    passes = 0
    while True:
        grand = parent[parent]
        passes += 1
        if np.array_equal(grand, parent):
            return passes
        parent[:] = grand


def components(
    size: int,
    left: npt.NDArray[Any],
    right: npt.NDArray[Any],
    initial: npt.NDArray[Any] | None = None,
) -> npt.NDArray[np.uint32]:
    """Connected components with every root the minimum member (historical union).

    ``initial`` must already map each node to a smaller-or-equal node of its
    component (a previous result). Hooking always sets ``parent[larger root]`` to
    the smallest connected root, so ``parent[i] <= i`` is invariant and the final
    root of each component is its minimum. Union order is irrelevant.
    """
    parent = (
        np.arange(size, dtype=np.int64) if initial is None else initial.astype(np.int64, copy=True)
    )
    a = np.asarray(left, dtype=np.int64)
    b = np.asarray(right, dtype=np.int64)
    while a.size:
        _compress(parent)
        ra, rb = parent[a], parent[b]
        keep = ra != rb
        if not keep.any():
            break
        a, b, ra, rb = a[keep], b[keep], ra[keep], rb[keep]
        np.minimum.at(parent, np.maximum(ra, rb), np.minimum(ra, rb))
    _compress(parent)
    if parent.size and (parent > np.arange(size)).any():  # pragma: no cover - invariant
        raise C05Error("corrupt union-find ordering")
    return parent.astype(np.uint32)


# --- the grouping engine --------------------------------------------------------------


@dataclass
class GroupResult:
    stats: dict[str, int]
    digest: str
    files: dict[str, dict[str, Any]]
    metrics: dict[str, Any]


class Grouper:
    def __init__(
        self,
        units: Sequence[Unit],
        contexts: Sequence[FileContext],
        policy: ProductionPolicy,
        resources: Resources,
        directory: Path,
        ledger: IndexLedger,
        pool: Pool,
        *,
        check: Callable[[], None],
        spend_comparison: Callable[[], None],
        progress: Progress | None = None,
    ) -> None:
        self.units, self.contexts = list(units), list(contexts)
        self.policy, self.resources = policy, resources
        self.directory, self.ledger, self.pool = directory, ledger, pool
        self.check, self.spend = check, spend_comparison
        self.progress: Progress = progress or NullProgress()
        sizes = [u.rows for u in self.units]
        self.offsets = np.zeros(len(sizes) + 1, dtype=np.int64)
        np.cumsum(sizes, out=self.offsets[1:])
        self.n = int(self.offsets[-1])
        if self.n > U32_LIMIT or self.n > resources.records:
            raise C05Error("document count exceeds the dense uint32 / records ceiling")
        self.memory = MemoryPlan.derive(resources, self.n)
        self.metrics: dict[str, Any] = {"memory_plan": self.memory.__dict__.copy()}
        self.arrays = _Arrays(directory)

    # -- persistence helpers ------------------------------------------------------------

    def _write(self, name: str, data: bytes | npt.NDArray[Any]) -> None:
        raw = data if isinstance(data, bytes) else np.ascontiguousarray(data).tobytes()
        self.ledger.charge(len(raw))
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _remove_tree(self, name: str) -> None:
        path = self.directory / name
        if path.exists():
            size = tree_bytes(path)
            for child in sorted(path.iterdir()):
                child.unlink()
            path.rmdir()
            self.ledger.release(size)

    # -- stages -------------------------------------------------------------------------

    def run(self) -> GroupResult:
        self.directory.mkdir(parents=True, exist_ok=False)
        where, dense_of = self.prepare()
        exact_left, exact_right = self.exact(dense_of)
        near_left, near_right, stats = self.near(where, dense_of)
        lineage_left, lineage_right = self.lineage(dense_of)
        parent_left, parent_right = self.parents(dense_of)
        self.progress.stage("GROUP: FAMILY PROPAGATION", self.n, "docs")
        dup = components(
            self.n,
            np.concatenate([exact_left, near_left]),
            np.concatenate([exact_right, near_right]),
        )
        self.progress.update(self.n // 2, edges=int(exact_left.size + near_left.size))
        fam = components(
            self.n,
            np.concatenate([lineage_left, parent_left]),
            np.concatenate([lineage_right, parent_right]),
            initial=dup,
        )
        self.progress.update(self.n, edges=int(lineage_left.size + parent_left.size))
        self.progress.stage("GROUP: PATH COMPRESSION", self.n, "docs")
        self._write("dup.u32", dup)
        self._write("fam.u32", fam)
        self.progress.update(self.n)
        survivor = self.survivors(dup, where)
        families = self.families(fam, survivor, where)
        digest = self.digest(stats)
        files = {name: _binding(self.directory / name) for name in SEALED}
        self.metrics["group_bytes"] = tree_bytes(self.directory)
        return GroupResult(stats=stats, digest=digest, files=files, metrics=self.metrics | families)

    def prepare(self) -> tuple[npt.NDArray[np.uint32], npt.NDArray[np.uint32]]:
        """Dense ids: k-way merge of each unit's id order (exact UTF-8 byte order)."""
        self.progress.stage("GROUP: PREPARE", self.n, "docs")
        where = np.empty(self.n, dtype=np.uint32)
        blob_path = self.directory / "ids.bin"
        offsets = np.empty(self.n + 1, dtype="<u8")
        offsets[0] = 0
        position = 0
        previous: bytes | None = None
        buffer: list[bytes] = []
        buffered = 0
        with blob_path.open("xb") as stream:
            for dense, (identity, row) in enumerate(heapq.merge(*self._id_streams())):
                if identity == previous:
                    raise C05Error("duplicate canonical document id")
                previous = identity
                where[dense] = row
                position += len(identity)
                offsets[dense + 1] = position
                buffer.append(identity)
                buffered += len(identity)
                if buffered >= 1 << 22:
                    self._append(stream, buffer)
                    buffer, buffered = [], 0
                if dense % 65536 == 0:
                    self.check()
                    self.progress.update(dense)
            self._append(stream, buffer)
            stream.flush()
            os.fsync(stream.fileno())
        self._write("ids.off", offsets)
        self._write("where.u32", where)
        dense_of = np.empty(self.n, dtype=np.uint32)
        dense_of[where] = np.arange(self.n, dtype=np.uint32)
        self.progress.update(self.n)
        return where, dense_of

    def _append(self, stream: Any, buffer: list[bytes]) -> None:
        raw = b"".join(buffer)
        self.ledger.charge(len(raw))
        stream.write(raw)

    def _id_streams(self) -> list[Iterator[tuple[bytes, int]]]:
        def stream(unit: Unit, base: int) -> Iterator[tuple[bytes, int]]:
            blob, offsets = unit.ids()
            order = unit.id_order()
            for start in range(0, unit.rows, 8192):
                rows = np.asarray(order[start : start + 8192], dtype=np.int64)
                begins = offsets[rows].tolist()
                ends = offsets[rows + 1].tolist()
                for row, a, b in zip(rows.tolist(), begins, ends, strict=True):
                    yield bytes(blob[a:b]), base + row

        return [stream(u, int(self.offsets[i])) for i, u in enumerate(self.units)]

    def _column(self, getter: Callable[[Unit], npt.NDArray[Any]], dtype: Any) -> Any:
        parts = [np.asarray(getter(u)) for u in self.units]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=dtype)

    def exact(self, dense_of: npt.NDArray[np.uint32]) -> tuple[Any, Any]:
        self.progress.stage("GROUP: EXACT", self.n, "docs")
        exact = self._column(lambda u: u.records()["exact"], np.uint8).reshape(-1, 32)
        words = np.ascontiguousarray(exact).view("<u8").reshape(-1, 4)
        order = np.lexsort((dense_of, words[:, 3], words[:, 2], words[:, 1], words[:, 0]))
        self.progress.update(self.n // 2)
        left, right = _equal_runs(words[order], dense_of[order])
        self.progress.update(self.n, edges=int(left.size))
        self.metrics["exact_edges"] = int(left.size)
        return left, right

    def near(
        self, where: npt.NDArray[np.uint32], dense_of: npt.NDArray[np.uint32]
    ) -> tuple[Any, Any, dict[str, int]]:
        near_dir = self.directory / "near"
        near_dir.mkdir()
        group = self.memory.band_pass
        passes = 0
        self.progress.stage("GROUP: BAND INDEX BUILD", self.n * BANDS, "postings")
        built = 0
        layout: dict[int, tuple[int, int, int, int]] = {}
        post_path, mem_path = near_dir / "postings.bin", near_dir / "members.bin"
        with post_path.open("xb") as post, mem_path.open("xb") as mem:
            for first in range(0, BANDS, group):
                passes += 1
                bands = tuple(range(first, min(BANDS, first + group)))
                keys = np.empty((self.n, len(bands), 12), dtype=np.uint8)
                jobs = [
                    ("bands", u, lo, min(lo + self.memory.job_docs, unit.rows), bands)
                    for u, unit in enumerate(self.units)
                    for lo in range(0, unit.rows, self.memory.job_docs)
                ]

                def place(
                    result: tuple[int, int, bytes],
                    keys: Any = keys,
                    bands: Any = bands,
                    current_pass: int = passes,
                ) -> None:
                    nonlocal built
                    unit, lo, raw = result
                    block = np.frombuffer(raw, dtype=np.uint8).reshape(-1, len(bands), 12)
                    base = int(self.offsets[unit]) + lo
                    keys[base : base + block.shape[0]] = block
                    built += block.shape[0] * len(bands)
                    self.progress.update(built, passes=current_pass, **self.pool.telemetry())

                run_jobs(self.pool, jobs, place, self.check)
                for index, band in enumerate(bands):
                    self.check()
                    postings, members = self._band_index(keys[:, index, :], dense_of)
                    layout[band] = (
                        post.tell() // POSTING.itemsize,
                        int(postings.shape[0]),
                        mem.tell() // 4,
                        int(members.shape[0]),
                    )
                    for stream, data in ((post, postings.tobytes()), (mem, members.tobytes())):
                        self.ledger.charge(len(data))
                        stream.write(data)
                del keys
            for stream in (post, mem):
                stream.flush()
                os.fsync(stream.fileno())
        self.metrics["band_passes"] = passes
        self.metrics["band_index_bytes"] = tree_bytes(near_dir)
        left, right, stats = self._near_pass(where, near_dir, layout)
        self._remove_tree("near")
        return left, right, stats

    def _band_index(
        self, keys: npt.NDArray[np.uint8], dense_of: npt.NDArray[np.uint32]
    ) -> tuple[npt.NDArray[Any], npt.NDArray[Any]]:
        """Postings (doc order) of buckets with >= 2 members, and member lists."""
        packed = np.ascontiguousarray(keys)
        high = np.ascontiguousarray(packed[:, :8]).view("<u8").ravel()
        low = np.ascontiguousarray(packed[:, 8:]).view("<u4").ravel()
        # (last 4 key bytes, dense) packed into one uint64: ordering by (high, tail)
        # is ordering by (key, dense), with one fewer sort key.
        tail = (low.astype(np.uint64) << np.uint64(32)) | dense_of.astype(np.uint64)
        del low
        order = np.lexsort((tail, high))
        sorted_tail = tail[order]
        sorted_dense = (sorted_tail & np.uint64(0xFFFFFFFF)).astype(np.uint32)
        same = np.zeros(self.n, dtype=bool)
        if self.n > 1:
            sorted_high = high[order]
            sorted_low = sorted_tail >> np.uint64(32)
            same[1:] = (sorted_high[1:] == sorted_high[:-1]) & (sorted_low[1:] == sorted_low[:-1])
        starts = np.flatnonzero(~same)
        lengths = np.diff(np.append(starts, self.n))
        run_of = np.cumsum(~same) - 1
        run_length = lengths[run_of]
        interesting = np.flatnonzero(run_length >= 2)
        member_runs = (lengths >= 2) & (lengths <= self.policy.max_bucket_size)
        members = sorted_dense[member_runs[run_of]].astype("<u4")
        member_start = np.zeros(starts.size, dtype=np.int64)
        if starts.size > 1:
            np.cumsum(np.where(member_runs, lengths, 0)[:-1], out=member_start[1:])
        postings = np.zeros(interesting.size, dtype=POSTING)
        postings["doc"] = sorted_dense[interesting]
        runs = run_of[interesting]
        postings["start"] = np.where(member_runs[runs], member_start[runs], 0)
        postings["pos"] = interesting - starts[runs]
        postings["length"] = run_length[interesting]
        return postings[np.argsort(postings["doc"], kind="stable")], members

    def _signature_reader(
        self, where: npt.NDArray[np.uint32]
    ) -> Callable[[Sequence[int]], npt.NDArray[np.uint64]]:
        sigs = [u.signatures() for u in self.units]
        offsets = self.offsets

        def read(dense: Sequence[int]) -> npt.NDArray[np.uint64]:
            rows = where[np.asarray(dense, dtype=np.int64)].astype(np.int64)
            unit = np.searchsorted(offsets, rows, side="right") - 1
            return np.stack(
                [sigs[u][r - offsets[u]] for u, r in zip(unit.tolist(), rows.tolist(), strict=True)]
            )

        return read

    def _near_pass(
        self,
        where: npt.NDArray[np.uint32],
        near_dir: Path,
        layout: dict[int, tuple[int, int, int, int]],
    ) -> tuple[Any, Any, dict[str, int]]:
        policy, resources = self.policy, self.resources
        self.progress.stage("GROUP: NEAR", self.n, "docs")
        all_postings = _mapped(near_dir / "postings.bin", POSTING)
        all_members = _mapped(near_dir / "members.bin", np.dtype("<u4"))
        postings = [all_postings[layout[b][0] : layout[b][0] + layout[b][1]] for b in range(BANDS)]
        members = [all_members[layout[b][2] : layout[b][2] + layout[b][3]] for b in range(BANDS)]
        read = self._signature_reader(where)
        stats = {"comparisons": 0, "oversized_bands": 0, "candidate_cap_documents": 0}
        left: list[int] = []
        right: list[int] = []
        maximum, cap = policy.max_bucket_size, policy.max_candidates_per_document
        threshold = policy.near_threshold
        chunk = self.memory.near_chunk_docs
        visited = 0
        for lo in range(0, self.n, chunk):
            self.check()
            hi = min(self.n, lo + chunk)
            parts = []
            for band in range(BANDS):
                docs = postings[band]["doc"]
                a, b = np.searchsorted(docs, [lo, hi], side="left")
                if b > a:
                    block = np.array(postings[band][a:b])
                    parts.append((np.full(b - a, band, dtype=np.uint8), block))
            if not parts:
                self.progress.update(hi, **_fields(stats))
                continue
            band_ids = np.concatenate([p[0] for p in parts])
            block = np.concatenate([p[1] for p in parts])
            order = np.lexsort((band_ids, block["doc"]))
            docs_l = block["doc"][order].tolist()
            bands_l = band_ids[order].tolist()
            starts_l = block["start"][order].tolist()
            pos_l = block["pos"][order].tolist()
            length_l = block["length"][order].tolist()
            i = 0
            count = len(docs_l)
            while i < count:
                doc = docs_l[i]
                j = i
                while j < count and docs_l[j] == doc:
                    j += 1
                candidates: set[int] = set()
                capped = False
                for k in range(i, j):
                    length = length_l[k]
                    if length > maximum:
                        stats["oversized_bands"] += 1
                        if stats["oversized_bands"] > resources.oversized_buckets:
                            raise C05Error("oversized bucket event ceiling")
                        continue
                    start = starts_l[k]
                    for other in members[bands_l[k]][start : start + pos_l[k]].tolist():
                        if other in candidates:
                            continue
                        if len(candidates) == cap:
                            capped = True
                            break
                        candidates.add(other)
                    if capped:
                        break
                stats["candidate_cap_documents"] += int(capped)
                if candidates:
                    ordered = sorted(candidates)
                    for _ in ordered:
                        self.spend()
                        stats["comparisons"] += 1
                        if stats["comparisons"] > resources.comparisons:
                            raise C05Error("candidate comparison ceiling")
                    signatures = read([doc, *ordered])
                    agreements = (signatures[1:] == signatures[0]).sum(axis=1).tolist()
                    for other, agree in zip(ordered, agreements, strict=True):
                        if agree / PERMUTATIONS >= threshold:
                            left.append(doc)
                            right.append(other)
                visited += 1
                i = j
            self.progress.update(hi, candidates=visited, **_fields(stats))
        del postings, members, all_postings, all_members
        self.metrics["near_documents_visited"] = visited
        self.metrics["near_edges"] = len(left)
        return np.array(left, dtype=np.int64), np.array(right, dtype=np.int64), stats

    def lineage(self, dense_of: npt.NDArray[np.uint32]) -> tuple[Any, Any]:
        total = sum(int(np.asarray(u.strings("lineage")[2]).sum()) for u in self.units)
        self.progress.stage("GROUP: LINEAGE INDEX BUILD", total, "keys")
        sorter = ExternalSorter(
            LINEAGE,
            LINEAGE_KEYS,
            run_records=self.memory.lineage_run_records,
            directory=self.directory / "sort",
            ledger=self.ledger,
            check=self.check,
        )
        done = 0
        for u, unit in enumerate(self.units):
            _blob, _offsets, counts, digests = unit.strings("lineage")
            keys = int(np.asarray(counts).sum())
            if not keys:
                continue
            rows = np.repeat(np.arange(unit.rows, dtype=np.int64), np.asarray(counts))
            records = np.zeros(keys, dtype=LINEAGE)
            words = np.ascontiguousarray(digests).view("<u8").reshape(-1, 4)
            for column in range(4):
                records[f"w{column}"] = words[:, column]
            records["dense"] = dense_of[int(self.offsets[u]) + rows]
            records["unit"] = u
            records["key"] = np.arange(keys, dtype=np.uint64)
            sorter.add(records)
            done += keys
            self.progress.update(done, runs=sorter.total_runs)
        self.metrics["lineage_postings"] = sorter.records
        self.metrics["lineage_runs"] = max(1, sorter.total_runs)
        self.metrics["lineage_spill_bytes"] = sorter.spilled_bytes
        self.progress.stage("GROUP: LINEAGE", total, "keys")
        left: list[npt.NDArray[Any]] = []
        right: list[npt.NDArray[Any]] = []
        carry: npt.NDArray[Any] | None = None
        done = 0
        verified = 0
        for chunk in sorter.merged():
            data = chunk if carry is None else np.concatenate([carry, chunk])
            words = np.stack([data[f"w{c}"] for c in range(4)], axis=1)
            tail = _tail_start(words)
            body, carry = data[:tail], data[tail:]
            verified += self._lineage_runs(body, left, right)
            done += chunk.shape[0]
            self.progress.update(done, edges=sum(x.size for x in left))
        if carry is not None and carry.shape[0]:
            verified += self._lineage_runs(carry, left, right)
        self.metrics["lineage_verified_members"] = verified
        joined_left = np.concatenate(left) if left else np.zeros(0, np.int64)
        joined_right = np.concatenate(right) if right else np.zeros(0, np.int64)
        self._remove_tree("sort")
        return joined_left, joined_right

    def _lineage_runs(self, data: npt.NDArray[Any], left: list[Any], right: list[Any]) -> int:
        if not data.shape[0]:
            return 0
        words = np.stack([data[f"w{c}"] for c in range(4)], axis=1)
        a, b = _equal_runs(words, data["dense"].astype(np.int64))
        if not a.size:
            return 0
        # Digest equality is only a candidate: verify the exact key bytes.
        same = np.zeros(data.shape[0], dtype=bool)
        same[1:] = (words[1:] == words[:-1]).all(axis=1)
        run_start = np.maximum.accumulate(np.where(~same, np.arange(data.shape[0]), 0))
        members = np.flatnonzero(same)
        for m in members.tolist():
            first = int(run_start[m])
            if self._lineage_key(data[first]) != self._lineage_key(data[m]):
                raise C05Error("lineage key digest collision; refusing")
        left.append(a)
        right.append(b)
        return int(members.size)

    def _lineage_key(self, record: Any) -> bytes:
        blob, offsets, _counts, _digests = self.units[int(record["unit"])].strings("lineage")
        k = int(record["key"])
        return bytes(blob[int(offsets[k]) : int(offsets[k + 1])])

    def parents(self, dense_of: npt.NDArray[np.uint32]) -> tuple[Any, Any]:
        total = sum(int(np.asarray(u.strings("parents")[2]).sum()) for u in self.units)
        self.progress.stage("GROUP: PARENTS", total, "edges")
        if not total:
            self.progress.update(0)
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        digests = self._column(lambda u: u.records()["id_digest"], np.uint8).reshape(-1, 32)
        words = np.ascontiguousarray(digests).view("<u8").reshape(-1, 4)
        order = np.lexsort((words[:, 3], words[:, 2], words[:, 1], words[:, 0]))
        first = words[order, 0]
        left: list[int] = []
        right: list[int] = []
        done = 0
        for u, unit in enumerate(self.units):
            blob, offsets, counts, pdigests = unit.strings("parents")
            refs = int(np.asarray(counts).sum())
            if not refs:
                continue
            self.check()
            rows = np.repeat(np.arange(unit.rows, dtype=np.int64), np.asarray(counts))
            pwords = np.ascontiguousarray(pdigests).view("<u8").reshape(-1, 4)
            lo = np.searchsorted(first, pwords[:, 0], side="left")
            hi = np.searchsorted(first, pwords[:, 0], side="right")
            for k in np.flatnonzero(hi > lo).tolist():
                text = bytes(blob[int(offsets[k]) : int(offsets[k + 1])])
                for position in range(int(lo[k]), int(hi[k])):
                    g = int(order[position])
                    if not (words[g] == pwords[k]).all():
                        continue
                    if self._id_at_global(g) == text:
                        left.append(int(dense_of[int(self.offsets[u]) + int(rows[k])]))
                        right.append(int(dense_of[g]))
            done += refs
            self.progress.update(done, edges=len(left))
        self.metrics["parent_edges"] = len(left)
        return np.array(left, dtype=np.int64), np.array(right, dtype=np.int64)

    def _id_at_global(self, g: int) -> bytes:
        u = int(np.searchsorted(self.offsets, g, side="right") - 1)
        unit = self.units[u]
        blob, offsets = unit.ids()
        r = g - int(self.offsets[u])
        return bytes(blob[int(offsets[r]) : int(offsets[r + 1])])

    def survivors(
        self, dup: npt.NDArray[np.uint32], where: npt.NDArray[np.uint32]
    ) -> npt.NDArray[np.uint8]:
        """First of ``ORDER BY duplicate, bytes DESC, source, id`` per duplicate group."""
        self.progress.stage("GROUP: SURVIVORS", self.n, "docs")
        sizes = self._column(lambda u: u.records()["bytes"], "<u8")[where].astype(np.int64)
        names = sorted({c.source_id for c in self.contexts}, key=lambda s: s.encode("utf-8"))
        rank = {name: i for i, name in enumerate(names)}
        unit_rank = np.array([rank[c.source_id] for c in self.contexts], dtype=np.int64)
        unit_of = np.searchsorted(self.offsets, where.astype(np.int64), side="right") - 1
        source = unit_rank[unit_of]
        order = np.lexsort((np.arange(self.n), source, -sizes, dup))
        first = np.ones(self.n, dtype=bool)
        if self.n > 1:
            first[1:] = dup[order][1:] != dup[order][:-1]
        survivor = np.zeros(self.n, dtype=np.uint8)
        survivor[order[first]] = 1
        self._write("survivor.u8", survivor)
        self.progress.update(self.n)
        self._sizes = sizes
        return survivor

    def families(
        self,
        fam: npt.NDArray[np.uint32],
        survivor: npt.NDArray[np.uint8],
        where: npt.NDArray[np.uint32],
    ) -> dict[str, Any]:
        self.progress.stage("GROUP: FAMILY BUILD", self.n, "docs")
        roots = np.flatnonzero(fam == np.arange(self.n, dtype=np.uint32)).astype(np.uint32)
        total = np.zeros(self.n, dtype=np.int64)
        np.add.at(total, fam.astype(np.int64), self._sizes * survivor.astype(np.int64))
        hit_g = np.zeros(self.n, dtype=np.uint8)
        for u, unit in enumerate(self.units):
            rows = np.asarray(unit.hits()["row"], dtype=np.int64)
            hit_g[int(self.offsets[u]) + rows] = 1
        hit = np.zeros(self.n, dtype=np.uint8)
        np.maximum.at(hit, fam.astype(np.int64), hit_g[where])
        fam_bytes = total[roots]
        fam_hit = hit[roots]
        self._write("families.u32", roots)
        self._write("fam_bytes.i64", fam_bytes.astype("<i8"))
        self._write("fam_hit.u8", fam_hit)
        self.progress.update(self.n // 2, families=int(roots.size))
        ordering = np.zeros((roots.size, 32), dtype=np.uint8)
        step = self.memory.job_docs
        jobs = [("orderings", lo, min(roots.size, lo + step)) for lo in range(0, roots.size, step)]
        done = 0

        def place(result: tuple[int, bytes]) -> None:
            nonlocal done
            lo, raw = result
            block = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 32)
            ordering[lo : lo + block.shape[0]] = block
            done += block.shape[0]
            self.progress.update(
                self.n // 2 + int(done * (self.n - self.n // 2) / max(1, roots.size)),
                families=int(roots.size),
            )

        run_jobs(self.pool, jobs, place, self.check)
        self._write("fam_ordering.b32", ordering)
        split, quick = self.splits(roots, ordering, fam_bytes, fam_hit)
        self._write("fam_split.u8", split)
        self._write("fam_quick.u8", quick)
        return {
            "families": int(roots.size),
            "diagnostic_families": int((split == 1).sum()),
            "audit_families": int((split == 2).sum()),
        }

    def splits(
        self,
        roots: npt.NDArray[np.uint32],
        ordering: npt.NDArray[np.uint8],
        sizes: npt.NDArray[np.int64],
        hits: npt.NDArray[np.uint8],
    ) -> tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8]]:
        """Historical greedy allocation over hit-free families ``ORDER BY ordering, id``."""
        self.progress.stage("GROUP: SPLITS", int(roots.size), "families")
        split = np.zeros(roots.size, dtype=np.uint8)
        quick = np.zeros(roots.size, dtype=np.uint8)
        clean = np.flatnonzero(hits == 0)
        words = np.ascontiguousarray(ordering[clean]).view(">u8").reshape(-1, 4)
        order = clean[
            np.lexsort((roots[clean], words[:, 3], words[:, 2], words[:, 1], words[:, 0]))
        ]
        policy = self.policy
        diagnostic = audit = quick_bytes = 0
        for done, family in enumerate(order.tolist()):
            if diagnostic >= policy.diagnostic_bytes and audit >= policy.audit_bytes:
                break
            size = int(sizes[family])
            if diagnostic < policy.diagnostic_bytes:
                split[family] = 1
                diagnostic += size
                if quick_bytes < policy.quick_bytes:
                    quick[family] = 1
                    quick_bytes += size
            elif audit < policy.audit_bytes:
                split[family] = 2
                audit += size
            if done % 65536 == 0:
                self.check()
                self.progress.update(done)
        self.progress.update(int(roots.size))
        return split, quick

    def digest(self, stats: dict[str, int]) -> str:
        """Historical ``group_digest``: docs, families, then stats rows, canonical bytes."""
        families = int(self.arrays.get("families.u32", "<u4").shape[0])
        self.progress.stage("GROUP: DIGEST", self.n + families, "rows")
        value = hashlib.sha256()
        step = self.memory.job_docs
        done = 0

        def feed(result: tuple[int, bytes]) -> None:
            nonlocal done
            value.update(result[1])
            done += step
            self.progress.update(min(done, self.n + families))

        doc_jobs = [
            ("digest_docs", i, lo, min(self.n, lo + step))
            for i, lo in enumerate(range(0, self.n, step))
        ]
        ordered_jobs(self.pool, doc_jobs, lambda r: r[0], feed, self.check)
        done = self.n
        family_jobs = [
            ("digest_families", i, lo, min(families, lo + step))
            for i, lo in enumerate(range(0, families, step))
        ]
        ordered_jobs(self.pool, family_jobs, lambda r: r[0], feed, self.check)
        for key in sorted(stats):
            value.update(canonical.canonical_bytes([key, stats[key]]))
        self.progress.update(self.n + families)
        return value.hexdigest()


def _equal_runs(
    keys: npt.NDArray[Any], dense: npt.NDArray[Any]
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Edges (run minimum, member) for runs of equal ``keys`` rows (sorted, dense asc)."""
    count = keys.shape[0]
    if count < 2:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    same = np.zeros(count, dtype=bool)
    same[1:] = (keys[1:] == keys[:-1]).all(axis=1) if keys.ndim == 2 else keys[1:] == keys[:-1]
    run_start = np.maximum.accumulate(np.where(~same, np.arange(count), 0))
    members = np.flatnonzero(same)
    values = np.asarray(dense, dtype=np.int64)
    return values[run_start[members]], values[members]


def _tail_start(words: npt.NDArray[Any]) -> int:
    """Index where the final run of equal rows begins (carried to the next chunk)."""
    count = words.shape[0]
    if count == 0:
        return 0
    last = words[-1]
    start = count - 1
    while start > 0 and bool((words[start - 1] == last).all()):
        start -= 1
    return int(start)


def _fields(values: dict[str, int]) -> dict[str, Any]:
    """Counters as display-only progress fields."""
    return dict(values)


def _mapped(path: Path, dtype: np.dtype[Any]) -> Any:
    if path.stat().st_size == 0:
        return np.zeros(0, dtype=dtype)
    return np.memmap(path, dtype=dtype, mode="r")


def _binding(path: Path) -> dict[str, Any]:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            value.update(block)
    return {"bytes": path.stat().st_size, "sha256": value.hexdigest()}


def verify_group_files(directory: Path, files: dict[str, Any]) -> None:
    if set(files) != set(SEALED):
        raise C05Error("group seal file table")
    present = {p.name for p in directory.iterdir()}
    if present != set(SEALED):
        raise C05Error("group directory has unexpected entries")
    for name, entry in files.items():
        if _binding(directory / name) != entry:
            raise C05Error("group artifact changed")
