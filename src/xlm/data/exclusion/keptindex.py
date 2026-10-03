"""Reusable post-C05 KEPT-membership index (public kept rows only).

Built by the C06 fast fit during its single source pass. Downstream (exact counting,
selected tokenization, quota support) can locate and re-read any kept record
directly instead of reparsing every canonical file. It holds no benchmark matches,
matcher facts, excluded/duplicate ledgers, group arrays or C05 scratch.

Layout (all little-endian, rows in membership order = ascending UTF-8 doc_id)::

    rows.bin         ROW_DTYPE x n (64 bytes per kept record)
    ids.bin          concatenated UTF-8 doc ids
    ids.off          <u8 x (n + 1) offsets into ids.bin
    by_location.u4   <u4 x n row positions sorted by (file ordinal, row)
    tables.json      allocations, ordered plan files (identity), split names
    kept-index.json  signed envelope binding every section SHA-256 to the C05
                     plan/completion/membership/input-manifest and the producer

Reading is a **private verified snapshot**: each section is read exactly once into
memory while hashed, the hash must equal the signed manifest, and only those same
bytes are ever consumed (no memory map whose backing file could change). The
snapshot is then structurally validated against the plan and the signed completion.
:meth:`KeptIndex.reverify` re-hashes the on-disk sections before a consumer reports
success, so a later on-disk change cannot pass unnoticed.

A consumer must still verify each source file's SHA-256 when it reads it; the index
binds where records are, not that the files are unchanged afterwards.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.fitscan import NOT_PARSED, SPLIT_NAMES
from xlm.data.exclusion.gates import C05View
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error

INDEX_KIND = "c05_kept_membership_index_v1"
INDEX_FORMAT = 1
MANIFEST = "kept-index.json"
ROW_DTYPE = np.dtype(
    [
        ("file", "<u4"),
        ("row", "<u4"),
        ("offset", "<u8"),
        ("length", "<u4"),
        ("bytes", "<u8"),
        ("content", "u1", (32,)),
        ("assigned_split", "u1"),
        ("original_split", "u1"),
        ("allocation", "<u2"),
    ]
)
SECTIONS = ("rows.bin", "ids.bin", "ids.off", "by_location.u4", "tables.json")
OWNED_FILES = (*SECTIONS, MANIFEST)
HASH_BLOCK = 16 * 1024**2
TABLES_CEILING = 16 * 1024**2


def plan_file_table(view: C05View) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": n,
            "path": item.path,
            "documents_sha256": item.documents_sha256,
            "file_bytes": item.file_bytes,
            "documents": item.documents,
        }
        for n, item in enumerate(view.plan.files)
    ]


def binding(view: C05View) -> dict[str, Any]:
    return {
        "mode": view.mode,
        "plan_digest": view.plan_digest,
        "completion_digest": view.receipt_digest,
        "input_manifest_digest": view.plan.input_manifest_digest,
        "membership_sha256": view.completion["membership_sha256"],
        "membership_bytes": view.completion["membership_bytes"],
        "kept": view.completion["kept"],
    }


def index_bound(kept: int, membership_bytes: int) -> int:
    """Upper bound of all sections plus manifest: ids cannot exceed the membership file."""
    return kept * (ROW_DTYPE.itemsize + 8 + 4) + 8 + membership_bytes + TABLES_CEILING + 8 * 1024**2


def _write_section(path: Path, data: bytes | memoryview) -> dict[str, Any]:
    digest = hashlib.sha256(data)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"bytes": len(data), "sha256": digest.hexdigest()}


def write_index(
    directory: Path,
    view: C05View,
    rows: npt.NDArray[Any],
    ids: bytes,
    id_offsets: npt.NDArray[np.uint64],
    allocation_keys: list[list[Any]],
    implementation: Mapping[str, Any],
    issuer: str,
    key: bytes,
    *,
    ceiling: int | None = None,
) -> dict[str, Any]:
    """Write every section, then the signed manifest last (inside a staging tree)."""
    n = len(rows)
    if rows.dtype != ROW_DTYPE or len(id_offsets) != n + 1 or int(id_offsets[-1]) != len(ids):
        raise C05Error("kept index sections are inconsistent")
    if n != view.completion["kept"] or np.any(rows["original_split"] == NOT_PARSED):
        raise C05Error("kept index does not cover every kept record")
    order = np.lexsort((rows["row"], rows["file"])).astype(np.uint32)
    tables = canonical.canonical_bytes(
        {
            "allocations": allocation_keys,
            "files": plan_file_table(view),
            "splits": list(SPLIT_NAMES),
        }
    )
    planned = rows.nbytes + len(ids) + 8 * (n + 1) + 4 * n + len(tables)
    if len(tables) > TABLES_CEILING or (ceiling is not None and planned > ceiling):
        raise C05Error("kept index exceeds its reviewed byte ceiling")
    directory.mkdir()
    sections = {
        "rows.bin": _write_section(directory / "rows.bin", memoryview(rows).cast("B")),
        "ids.bin": _write_section(directory / "ids.bin", ids),
        "ids.off": _write_section(
            directory / "ids.off", memoryview(id_offsets.astype("<u8")).cast("B")
        ),
        "by_location.u4": _write_section(
            directory / "by_location.u4", memoryview(order.astype("<u4")).cast("B")
        ),
        "tables.json": _write_section(directory / "tables.json", tables),
    }
    envelope = signed(
        {
            "kind": INDEX_KIND,
            "format": INDEX_FORMAT,
            **binding(view),
            "rows": n,
            "row_dtype": ROW_DTYPE.descr.__repr__(),
            "files_digest": canonical.digest(plan_file_table(view)),
            "sections": sections,
            "implementation": dict(implementation),
        },
        issuer,
        key,
    )
    write_once(directory / MANIFEST, envelope)
    return envelope


def _section_sha(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _snapshot(path: Path, size: int, sha256: str) -> bytes:
    """Read a section once, bounded to its signed size; the hashed bytes ARE the result."""
    with path.open("rb") as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise C05Error("kept index section changed: " + path.name)
    return data


def reverify_sections(directory: Path, sections: Mapping[str, Mapping[str, Any]]) -> None:
    """On-disk sections still equal the signed snapshot (sizes and SHA-256)."""
    for name in SECTIONS:
        size, sha = _section_sha(directory / name)
        if (size, sha) != (sections[name]["bytes"], sections[name]["sha256"]):
            raise C05Error("kept index section changed: " + name)
    names = sorted(p.name for p in directory.iterdir())
    if names != sorted(OWNED_FILES):
        raise C05Error("kept index directory holds unaccounted files")


@dataclass
class KeptIndex:
    """Verified read-only view over an in-memory snapshot of the signed sections."""

    directory: Path
    manifest: dict[str, Any]
    rows: npt.NDArray[Any]
    ids: npt.NDArray[np.uint8]
    id_offsets: npt.NDArray[np.uint64]
    by_location: npt.NDArray[np.uint32]
    allocations: list[tuple[str, str, str | None]]
    files: list[dict[str, Any]]
    snapshot: dict[str, bytes] = field(repr=False, default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)

    def doc_id(self, position: int) -> str:
        start, end = int(self.id_offsets[position]), int(self.id_offsets[position + 1])
        return bytes(self.ids[start:end]).decode("utf-8")

    def find(self, doc_id: str) -> int | None:
        """Exact binary search over ascending UTF-8 doc ids."""
        target = doc_id.encode("utf-8")
        lo, hi = 0, len(self.rows)
        while lo < hi:
            mid = (lo + hi) // 2
            start, end = int(self.id_offsets[mid]), int(self.id_offsets[mid + 1])
            current = bytes(self.ids[start:end])
            if current < target:
                lo = mid + 1
            elif current > target:
                hi = mid
            else:
                return mid
        return None

    def file_positions(self, ordinal: int) -> npt.NDArray[np.uint32]:
        """Row positions of one plan file in ascending row order."""
        files = self.rows["file"][self.by_location]
        lo, hi = np.searchsorted(files, [ordinal, ordinal + 1])
        return np.asarray(self.by_location[lo:hi])

    def iter_locations(self) -> Iterator[tuple[int, npt.NDArray[np.uint32]]]:
        for ordinal in range(len(self.files)):
            yield ordinal, self.file_positions(ordinal)

    def reverify(self) -> None:
        """Before reporting success: the on-disk sections still equal this snapshot."""
        reverify_sections(self.directory, self.manifest["payload"]["sections"])


def open_index(directory: Path, view: C05View, *, structural: bool = True) -> KeptIndex:
    """Signature, C05/plan binding, private snapshot of every section, structure."""
    envelope = read_metadata(directory / MANIFEST, digested=False)
    body = verify_signed(envelope, view.trusted)
    if body.get("kind") != INDEX_KIND or body.get("format") != INDEX_FORMAT:
        raise C05Error("kept index kind or format mismatch")
    for name, expected in binding(view).items():
        if body.get(name) != expected:
            raise C05Error("kept index is stale against C05: " + name)
    if body.get("files_digest") != canonical.digest(plan_file_table(view)):
        raise C05Error("kept index is stale against the frozen plan files")
    if body.get("row_dtype") != ROW_DTYPE.descr.__repr__():
        raise C05Error("kept index row layout mismatch")
    n = body.get("rows")
    if type(n) is not int or n != view.completion["kept"]:
        raise C05Error("kept index row count differs from the C05 kept count")
    sections = body.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTIONS):
        raise C05Error("kept index section set mismatch")
    expected_sizes = {
        "rows.bin": n * ROW_DTYPE.itemsize,
        "ids.off": (n + 1) * 8,
        "by_location.u4": n * 4,
    }
    for name in SECTIONS:
        size = sections[name].get("bytes")
        if type(size) is not int or size < 0:
            raise C05Error("kept index section size invalid: " + name)
        if name in expected_sizes and size != expected_sizes[name]:
            raise C05Error("kept index section length differs from its schema: " + name)
    if sections["tables.json"]["bytes"] > TABLES_CEILING:
        raise C05Error("kept index tables exceed their ceiling")
    snapshot = {
        name: _snapshot(directory / name, sections[name]["bytes"], sections[name]["sha256"])
        for name in SECTIONS
    }
    tables = canonical.loads_bytes_strict(snapshot["tables.json"])
    if (
        not isinstance(tables, dict)
        or set(tables) != {"allocations", "files", "splits"}
        or tables["files"] != plan_file_table(view)
        or tables["splits"] != list(SPLIT_NAMES)
        or not isinstance(tables["allocations"], list)
        or not all(
            isinstance(a, list)
            and len(a) == 3
            and type(a[0]) is str
            and type(a[1]) is str
            and (a[2] is None or type(a[2]) is str)
            for a in tables["allocations"]
        )
    ):
        raise C05Error("kept index tables mismatch")
    rows = np.frombuffer(snapshot["rows.bin"], dtype=ROW_DTYPE, count=n)
    offsets = np.frombuffer(snapshot["ids.off"], dtype="<u8", count=n + 1)
    ids = np.frombuffer(snapshot["ids.bin"], dtype=np.uint8)
    by_location = np.frombuffer(snapshot["by_location.u4"], dtype="<u4", count=n)
    index = KeptIndex(
        directory=directory,
        manifest=envelope,
        rows=rows,
        ids=ids,
        id_offsets=offsets,
        by_location=by_location,
        allocations=[(a[0], a[1], a[2]) for a in tables["allocations"]],
        files=tables["files"],
        snapshot=snapshot,
    )
    if structural:
        validate_structure(index, view)
    return index


def validate_structure(index: KeptIndex, view: C05View) -> None:
    """Semantic validation of a signed snapshot; a trusted-but-inconsistent index refuses."""
    rows, offsets, ids, order = index.rows, index.id_offsets, index.ids, index.by_location
    n = len(rows)
    files = view.plan.files
    if int(offsets[0]) != 0 or int(offsets[-1]) != len(ids):
        raise C05Error("kept index id offsets out of range")
    if n and not bool(np.all(np.diff(offsets.astype(np.int64)) > 0)):
        raise C05Error("kept index id offsets are not strictly increasing")
    raw = index.snapshot.get("ids.bin") or bytes(ids)
    starts, ends = offsets[:-1].tolist(), offsets[1:].tolist()
    previous = b""
    for number, (start, end) in enumerate(zip(starts, ends, strict=True)):
        current = raw[start:end]
        if number and current <= previous:
            raise C05Error("kept index doc ids are not strictly ascending")
        previous = current
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise C05Error("kept index doc ids are not UTF-8") from exc
    file_ordinal = rows["file"].astype(np.int64)
    if n and int(file_ordinal.max()) >= len(files):
        raise C05Error("kept index file ordinal out of range")
    documents = np.asarray([f.documents for f in files], dtype=np.int64)
    sizes = np.asarray([f.file_bytes for f in files], dtype=np.int64)
    row = rows["row"].astype(np.int64)
    if np.any(row < 1) or np.any(row > documents[file_ordinal]):
        raise C05Error("kept index row number outside its plan file")
    offset = rows["offset"].astype(np.int64)
    length = rows["length"].astype(np.int64)
    if np.any(length < 1) or np.any(length > view.plan.resources.document_bytes):
        raise C05Error("kept index line length outside its bound")
    if np.any(offset + length > sizes[file_ordinal]):
        raise C05Error("kept index location outside its plan file")
    if np.any(rows["bytes"].astype(np.int64) >= length):
        raise C05Error("kept index canonical byte count exceeds its line")
    assigned, original = rows["assigned_split"], rows["original_split"]
    if np.any(assigned > 2) or np.any(original > 2):
        raise C05Error("kept index split code invalid")
    if np.any((assigned == 0) & (original != 0)):
        raise C05Error("kept index holds a train row whose original split is not train")
    allocations = len(index.allocations)
    if n and int(rows["allocation"].max()) >= allocations:
        raise C05Error("kept index allocation reference invalid")
    if n and (int(order.max()) >= n or not bool(np.all(np.bincount(order, minlength=n) == 1))):
        raise C05Error("kept index location order is not a permutation")
    located = (file_ordinal[order] << 32) | row[order]
    if n > 1 and not bool(np.all(np.diff(located) > 0)):
        raise C05Error("kept index location order is not strictly increasing")
    same_file = file_ordinal[order][1:] == file_ordinal[order][:-1]
    gap = offset[order][1:] - (offset[order][:-1] + length[order][:-1])
    if np.any(same_file & (gap < 0)):
        raise C05Error("kept index lines overlap within a file")
    keys = [canonical.canonical_bytes(list(a)).decode() for a in index.allocations]
    kept = np.bincount(rows["allocation"], minlength=allocations)
    train = assigned == 0
    train_bytes = np.zeros(allocations, dtype=np.uint64)
    np.add.at(train_bytes, rows["allocation"][train], rows["bytes"][train])
    expected = view.completion["allocations"]
    for number, key in enumerate(keys):
        counts = expected.get(key, {"kept": 0, "train_bytes": 0})
        if (int(kept[number]), int(train_bytes[number])) != (counts["kept"], counts["train_bytes"]):
            raise C05Error("kept index accounting differs from the C05 completion")
    if set(expected) - set(keys) and any(expected[k]["kept"] for k in set(expected) - set(keys)):
        raise C05Error("kept index accounting differs from the C05 completion")
