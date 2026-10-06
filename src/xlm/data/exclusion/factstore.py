"""Immutable per-file C05 fact units (``c05-facts-v2``) and the working-index ledger.

One plan file becomes one unit file ``facts/<ordinal>.unit``. Prepared batches
stream into section files inside ``facts/<ordinal>.staging/``; at commit the
sections are assembled behind a signed header into ``<ordinal>.unit.staging``,
fsynced, and renamed into place, then the staging directory is removed. Any
``*.staging`` entry is untrusted: the next run discards it (its bytes always
stayed inside the hard working-index ceiling).

Unit file: ``MAGIC`` | uint64 header length | canonical signed header | zero
padding to 64 | sections, each 64-byte aligned. The header (attestation) binds the
plan, input identity and counts, the format version, the logical facts digest
(historical ``facts_digest`` definition) and every section's offset, size and
SHA-256. Sections (little-endian; no pickle):

* ``records``: ``scanprep.RECORD`` per document (bytes, exact, content, id digest)
* ``signatures``: 128 x uint64 MinHash per document
* ``ids``/``ids_off``: document ids (blob, uint64 offsets); ``id_order``: rows in
  exact UTF-8 byte order of ids (uint32)
* ``lineage_*``/``parents_*``: per-document sorted keys (blob, offsets, counts,
  32-byte SHA-256 digests)
* ``hits``: (row uint32, 32-byte matched pattern identity) per matched document
* ``review``: private review postings (heuristic review only)
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, Final

import numpy as np
import numpy.typing as npt

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import verify_signed
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.scanprep import FORMAT, FORMAT_V3, PERMUTATIONS, RECORD, PreparedBatch

FACTS_DIR: Final = "facts"
GROUP_DIR: Final = "group"
UNIT_KIND: Final = "c05_facts_unit_v2"
UNIT_SUFFIX: Final = ".unit"
STAGING_SUFFIX: Final = ".staging"
MAGIC: Final = b"C05FACT2"
ALIGN: Final = 64
HEADER_BYTES: Final = 256 * 1024
COPY_BLOCK: Final = 8 * 1024 * 1024
HIT: Final = np.dtype([("row", "<u4"), ("pattern", "u1", (32,))])
STRING_LISTS: Final = ("lineage", "parents")
SECTIONS: Final = (
    "records",
    "signatures",
    "ids",
    "ids_off",
    "id_order",
    *(f"{name}_{part}" for name in STRING_LISTS for part in ("blob", "off", "cnt", "dig")),
    "hits",
)
REVIEW_SECTION: Final = "review"
#: c05-facts-v3 only: uint8 per lineage key, in key order (1 = split-only key).
SCOPE_SECTION: Final = "lineage_scope"


def section_names(*, review: bool, scoped: bool = False) -> list[str]:
    return (
        list(SECTIONS) + ([SCOPE_SECTION] if scoped else []) + ([REVIEW_SECTION] if review else [])
    )


class IndexLedger:
    """Hard byte ceiling (``index_bytes``) over every working-index file.

    Fact units, their staging, grouping artifacts and sort spills are charged
    before the bytes are written; it replaces SQLite's ``max_page_count``.
    """

    def __init__(self, limit: int, used: int) -> None:
        if used > limit:
            raise C05Error("existing working-index bytes exceed the index_bytes ceiling")
        self.limit, self.used = limit, used
        self.peak = used

    def charge(self, amount: int) -> None:
        if self.used + amount > self.limit:
            raise C05Error("working-index ceiling (index_bytes) would be exceeded")
        self.used += amount
        self.peak = max(self.peak, self.used)

    def release(self, amount: int) -> None:
        self.used = max(0, self.used - amount)


def tree_bytes(directory: Path) -> int:
    if not directory.exists():
        return 0
    if directory.is_file():
        return directory.stat().st_size
    total = 0
    for root, _dirs, names in os.walk(directory):
        for name in names:
            total += os.stat(os.path.join(root, name)).st_size
    return total


def _pad(size: int) -> int:
    return (-size) % ALIGN


#: Section bytes one unit may hold in parent memory before spilling to staging files
#: (most units never create a staging file: file creation is the costly operation).
SPILL_BYTES: Final = 64 * 1024 * 1024


class _Sink:
    """One section: ledger-charged writes, an incremental SHA-256, memory or spill file."""

    def __init__(self, name: str, owner: UnitWriter) -> None:
        self.name, self.owner = name, owner
        self.buffer = bytearray()
        self.stream: BinaryIO | None = None
        self.hash = hashlib.sha256()
        self.size = 0

    @property
    def spilled(self) -> bool:
        return self.stream is not None

    def write(self, data: bytes) -> None:
        if not data:
            return
        self.owner.ledger.charge(len(data))
        self.hash.update(data)
        self.size += len(data)
        if self.stream is not None:
            self.stream.write(data)
            return
        self.buffer += data
        self.owner.buffered += len(data)
        if self.owner.buffered > SPILL_BYTES:
            self.owner.spill()

    def spill(self) -> None:
        if self.stream is None and self.buffer:
            self.owner.staging.mkdir(exist_ok=True)
            self.stream = (self.owner.staging / self.name).open("xb")
            self.stream.write(self.buffer)
            self.owner.buffered -= len(self.buffer)
            self.buffer = bytearray()

    def copy_to(self, out: BinaryIO) -> None:
        if self.stream is None:
            out.write(self.buffer)
            return
        self.stream.close()
        with (self.owner.staging / self.name).open("rb") as source:
            shutil.copyfileobj(source, out, COPY_BLOCK)

    def close(self) -> None:
        if self.stream is not None and not self.stream.closed:
            self.stream.close()


class UnitWriter:
    """Streams prepared batches of one plan file, then publishes one unit file."""

    def __init__(
        self, facts: Path, ordinal: int, ledger: IndexLedger, *, review: bool, scoped: bool = False
    ) -> None:
        self.final = facts / f"{ordinal:05d}{UNIT_SUFFIX}"
        self.staging = facts / f"{ordinal:05d}{STAGING_SUFFIX}"
        self.assembled = facts / f"{ordinal:05d}{UNIT_SUFFIX}{STAGING_SUFFIX}"
        self.ordinal, self.ledger, self.review, self.scoped = ordinal, ledger, review, scoped
        for leftover in (self.staging, self.assembled):
            discard(leftover, ledger)
        self.buffered = 0
        names = section_names(review=review, scoped=scoped)
        self.sinks = {name: _Sink(name, self) for name in names}
        self.rows = 0
        self.bases = {"ids": 0, "lineage": 0, "parents": 0}
        for name in ("ids_off", "lineage_off", "parents_off"):
            self.sinks[name].write(np.zeros(1, "<u8").tobytes())
        self.ids: list[bytes] = []

    def spill(self) -> None:
        for sink in self.sinks.values():
            sink.spill()

    def append(self, batch: PreparedBatch) -> None:
        if batch.first_row != self.rows + 1:
            raise C05Error("prepared batches out of order")
        sinks = self.sinks
        sinks["records"].write(batch.records)
        sinks["signatures"].write(batch.signatures)
        sinks["ids"].write(batch.ids)
        sinks["ids_off"].write(self._rebased("ids", batch.id_offsets))
        offsets = np.frombuffer(batch.id_offsets, "<u8").tolist()
        self.ids.extend(batch.ids[a:b] for a, b in zip(offsets[:-1], offsets[1:], strict=True))
        for name, strings in (("lineage", batch.lineage), ("parents", batch.parents)):
            sinks[f"{name}_blob"].write(strings.blob)
            sinks[f"{name}_off"].write(self._rebased(name, strings.offsets))
            sinks[f"{name}_cnt"].write(strings.counts)
            sinks[f"{name}_dig"].write(strings.digests)
        if self.scoped:
            keys = int(np.frombuffer(batch.lineage.counts, "<u4").sum())
            if len(batch.lineage_scope) != keys:
                raise C05Error("lineage scope does not cover every lineage key")
            sinks[SCOPE_SECTION].write(batch.lineage_scope)
        elif batch.lineage_scope:
            raise C05Error("lineage scope in an unscoped fact unit")
        if batch.hits:
            hits = np.frombuffer(batch.hits, dtype=HIT).copy()
            hits["row"] += self.rows
            sinks["hits"].write(hits.tobytes())
        self.rows += batch.count

    def _rebased(self, name: str, raw: bytes) -> bytes:
        offsets = np.frombuffer(raw, "<u8")
        out = (offsets[1:] + np.uint64(self.bases[name])).astype("<u8")
        self.bases[name] += int(offsets[-1])
        return out.tobytes()

    def add_review(self, lines: bytes) -> None:
        self.sinks[REVIEW_SECTION].write(lines)

    def commit(
        self,
        attestation: Callable[[dict[str, Any]], dict[str, Any]],
        before_rename: Callable[[], None] = lambda: None,
    ) -> dict[str, Any]:
        """Order ids, assemble sections behind the signed header, fsync, rename."""
        order = sorted(range(len(self.ids)), key=self.ids.__getitem__)
        for left, right in zip(order, order[1:], strict=False):
            if self.ids[left] == self.ids[right]:
                raise C05Error("duplicate canonical document id")
        self.sinks["id_order"].write(np.array(order, dtype="<u4").tobytes())
        names = section_names(review=self.review, scoped=self.scoped)
        layout: dict[str, dict[str, Any]] = {}
        position = 0
        padding = 0
        for name in names:
            item = self.sinks[name]
            layout[name] = {"offset": position, "bytes": item.size, "sha256": item.hash.hexdigest()}
            position += item.size + _pad(item.size)
            padding += _pad(item.size)
        envelope = attestation(layout)
        header = canonical.canonical_bytes(envelope)
        if len(header) > HEADER_BYTES:
            raise C05Error("fact unit header ceiling")
        prefix = MAGIC + len(header).to_bytes(8, "little") + header
        prefix += b"\0" * _pad(len(prefix))
        # Memory sections were charged when written; spilled ones exist twice until
        # their staging copies are removed below (which releases them again).
        spilled = sum(s.size for s in self.sinks.values() if s.spilled)
        self.ledger.charge(len(prefix) + padding + spilled)
        with self.assembled.open("xb") as out:
            out.write(prefix)
            for name in names:
                self.sinks[name].copy_to(out)
                out.write(b"\0" * _pad(self.sinks[name].size))
            out.flush()
            os.fsync(out.fileno())
        for sink in self.sinks.values():
            sink.close()
            sink.buffer = bytearray()
        discard(self.staging, self.ledger)
        before_rename()
        os.rename(self.assembled, self.final)
        return envelope

    def abort(self) -> None:
        in_memory = 0
        for sink in self.sinks.values():
            sink.close()
            if not sink.spilled:
                in_memory += sink.size
            sink.buffer = bytearray()
        if not self.assembled.exists():
            # Otherwise the assembled copy holds these bytes and its discard releases them.
            self.ledger.release(in_memory)
        discard(self.staging, self.ledger)
        discard(self.assembled, self.ledger)


def discard(staging: Path, ledger: IndexLedger | None = None) -> None:
    """Remove an untrusted staging entry (never a published unit)."""
    if not staging.exists() and not staging.is_symlink():
        return
    if staging.is_symlink() or not staging.name.endswith(STAGING_SUFFIX):
        raise C05Error("refusing to discard an unexpected fact staging entry")
    size = tree_bytes(staging)
    if staging.is_dir():
        shutil.rmtree(staging)
    else:
        staging.unlink()
    if ledger is not None:
        ledger.release(size)


def unit_body(
    *,
    plan: str,
    ordinal: int,
    file: str,
    sha: str,
    documents: int,
    canonical_bytes: int,
    facts: str,
    sections: Mapping[str, Any],
    scoped: bool = False,
) -> dict[str, Any]:
    return {
        "kind": UNIT_KIND,
        "format": FORMAT_V3 if scoped else FORMAT,
        "plan": plan,
        "ordinal": ordinal,
        "file": file,
        "sha": sha,
        "documents": documents,
        "canonical_bytes": canonical_bytes,
        "facts": facts,
        "sections": dict(sections),
    }


def read_header(path: Path) -> tuple[dict[str, Any], int]:
    """(signed envelope, byte offset of the first section)."""
    with path.open("rb") as stream:
        head = stream.read(len(MAGIC) + 8)
        if len(head) != len(MAGIC) + 8 or head[: len(MAGIC)] != MAGIC:
            raise C05Error("fact unit magic")
        length = int.from_bytes(head[len(MAGIC) :], "little")
        if length > HEADER_BYTES:
            raise C05Error("fact unit header ceiling")
        raw = stream.read(length)
    if len(raw) != length:
        raise C05Error("fact unit header truncated")
    start = len(MAGIC) + 8 + length
    try:
        envelope = canonical.loads_bytes_strict(raw)
    except ValueError as exc:
        raise C05Error("fact unit header is not canonical JSON") from exc
    if not isinstance(envelope, dict):
        raise C05Error("fact unit header schema")
    return envelope, start + _pad(start)


class Unit:
    """A verified, read-only published fact unit (one memory-mapped file)."""

    def __init__(self, path: Path, body: Mapping[str, Any], start: int) -> None:
        self.path = path
        self.body = dict(body)
        self.rows = int(body["documents"])
        self.start = start
        self.sections: dict[str, Any] = dict(body["sections"])
        self._map: Any = None
        self._views: dict[str, Any] = {}

    def _raw(self) -> Any:
        if self._map is None:
            self._map = np.memmap(self.path, dtype=np.uint8, mode="r")
        return self._map

    def section(self, name: str, dtype: Any, shape: tuple[int, ...] | None = None) -> Any:
        view = self._views.get(name)
        if view is None:
            entry = self.sections[name]
            size = int(entry["bytes"])
            if size == 0:
                view = np.zeros(0, dtype=dtype)
            else:
                begin = self.start + int(entry["offset"])
                view = self._raw()[begin : begin + size].view(dtype)
            self._views[name] = view
        return view if shape is None else view.reshape(shape)

    def records(self) -> Any:
        return self.section("records", RECORD)

    def signatures(self) -> npt.NDArray[np.uint64]:
        values: npt.NDArray[np.uint64] = self.section("signatures", "<u8", (-1, PERMUTATIONS))
        return values

    def strings(self, name: str) -> tuple[Any, Any, Any, Any]:
        """(blob uint8, offsets uint64, per-document counts uint32, digests (n, 32))."""
        return (
            self.section(f"{name}_blob", np.uint8),
            self.section(f"{name}_off", "<u8"),
            self.section(f"{name}_cnt", "<u4"),
            self.section(f"{name}_dig", np.uint8, (-1, 32)),
        )

    def ids(self) -> tuple[Any, Any]:
        return self.section("ids", np.uint8), self.section("ids_off", "<u8")

    def id_order(self) -> Any:
        return self.section("id_order", "<u4")

    def hits(self) -> Any:
        return self.section("hits", HIT)

    def lineage_scope(self) -> Any:
        """uint8 per lineage key (1 = split-only); all zero for an unscoped unit."""
        if SCOPE_SECTION in self.sections:
            return self.section(SCOPE_SECTION, np.uint8)
        return np.zeros(int(np.asarray(self.strings("lineage")[2]).sum()), dtype=np.uint8)

    def review_lines(self) -> bytes:
        return (
            bytes(self.section(REVIEW_SECTION, np.uint8))
            if REVIEW_SECTION in self.sections
            else b""
        )

    def close(self) -> None:
        self._views.clear()
        self._map = None


def open_unit(path: Path) -> Unit:
    """Open a unit without verification (children: the parent verified it)."""
    envelope, start = read_header(path)
    return Unit(path, envelope["payload"], start)


def load_unit(
    path: Path,
    trusted: Mapping[str, bytes],
    expected: Mapping[str, Any],
    *,
    review: bool,
    verify_sections: bool = True,
    scoped: bool = False,
) -> Unit:
    """Verify the signed header, its bindings, the layout and (optionally) every hash."""
    if not path.is_file() or path.is_symlink():
        raise C05Error("reusable fact unit missing")
    envelope, start = read_header(path)
    body = verify_signed(envelope, trusted)
    for name, value in expected.items():
        if body.get(name) != value:
            raise C05Error("reusable fact unit binding changed: " + name)
    if body.get("kind") != UNIT_KIND or body.get("format") != (FORMAT_V3 if scoped else FORMAT):
        raise C05Error("reusable fact unit format")
    sections = body.get("sections")
    names = section_names(review=review, scoped=scoped)
    if not isinstance(sections, dict) or set(sections) != set(names):
        raise C05Error("reusable fact unit section table")
    position = 0
    for name in names:
        entry = sections[name]
        if entry.get("offset") != position or not isinstance(entry.get("bytes"), int):
            raise C05Error("reusable fact unit section layout")
        position += entry["bytes"] + _pad(entry["bytes"])
    if path.stat().st_size != start + position:
        raise C05Error("reusable fact unit size changed")
    if verify_sections:
        with path.open("rb") as stream:
            header_end = len(MAGIC) + 8 + int.from_bytes(stream.read(len(MAGIC) + 8)[-8:], "little")
            stream.seek(header_end)
            if any(stream.read(start - header_end)):
                raise C05Error("reusable fact unit padding changed")
            for name in names:
                entry = sections[name]
                stream.seek(start + entry["offset"])
                remaining, value = entry["bytes"], hashlib.sha256()
                while remaining:
                    block = stream.read(min(COPY_BLOCK, remaining))
                    if not block:
                        raise C05Error("reusable fact unit truncated")
                    value.update(block)
                    remaining -= len(block)
                if value.hexdigest() != entry.get("sha256"):
                    raise C05Error("reusable fact unit section hash changed")
                # Alignment padding is never hashed, so it must be exactly zero.
                if any(stream.read(_pad(entry["bytes"]))):
                    raise C05Error("reusable fact unit padding changed")
    return Unit(path, body, start)


def verify_units_parallel(
    jobs: list[tuple[Path, Mapping[str, Any]]],
    trusted: Mapping[str, bytes],
    *,
    review: bool,
    threads: int,
    check: Callable[[], None],
    scoped: bool = False,
) -> list[Unit]:
    """Verify many units; SHA-256 releases the GIL so hashing overlaps on threads."""
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        futures = [
            pool.submit(load_unit, path, trusted, expected, review=review, scoped=scoped)
            for path, expected in jobs
        ]
        units = []
        for future in futures:
            check()
            units.append(future.result())
    return units
