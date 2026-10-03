"""Reusable post-C05 KEPT-membership index (public kept rows only), mmap-friendly.

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

A consumer must still verify each source file's SHA-256 when it reads it; the index
binds where records are, not that the files are unchanged afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
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
HASH_BLOCK = 16 * 1024**2


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
) -> dict[str, Any]:
    """Write every section, then the signed manifest last (inside a staging tree)."""
    n = len(rows)
    if rows.dtype != ROW_DTYPE or len(id_offsets) != n + 1 or int(id_offsets[-1]) != len(ids):
        raise C05Error("kept index sections are inconsistent")
    if n != view.completion["kept"] or np.any(rows["original_split"] == NOT_PARSED):
        raise C05Error("kept index does not cover every kept record")
    order = np.lexsort((rows["row"], rows["file"])).astype(np.uint32)
    directory.mkdir(parents=True)
    tables = canonical.canonical_bytes(
        {
            "allocations": allocation_keys,
            "files": plan_file_table(view),
            "splits": list(SPLIT_NAMES),
        }
    )
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


@dataclass
class KeptIndex:
    """Verified read-only view; arrays are memory-mapped, never loaded wholesale."""

    directory: Path
    manifest: dict[str, Any]
    rows: npt.NDArray[Any]
    ids: npt.NDArray[np.uint8]
    id_offsets: npt.NDArray[np.uint64]
    by_location: npt.NDArray[np.uint32]
    allocations: list[tuple[str, str, str | None]]
    files: list[dict[str, Any]]

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


def _section_sha(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def open_index(directory: Path, view: C05View, *, verify_sections: bool = True) -> KeptIndex:
    """Signature, C05/plan binding and (by default) every section hash, then mmap."""
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
    sections = body.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTIONS):
        raise C05Error("kept index section set mismatch")
    for name in SECTIONS:
        path = directory / name
        if path.stat().st_size != sections[name]["bytes"]:
            raise C05Error("kept index section changed: " + name)
        if verify_sections and _section_sha(path)[1] != sections[name]["sha256"]:
            raise C05Error("kept index section changed: " + name)
    n = int(body["rows"])
    tables = json.loads((directory / "tables.json").read_bytes())
    if tables.get("files") != plan_file_table(view) or tables.get("splits") != list(SPLIT_NAMES):
        raise C05Error("kept index tables mismatch")
    rows = np.memmap(directory / "rows.bin", dtype=ROW_DTYPE, mode="r", shape=(n,))
    offsets = np.memmap(directory / "ids.off", dtype="<u8", mode="r", shape=(n + 1,))
    ids = np.memmap(directory / "ids.bin", dtype=np.uint8, mode="r")
    by_location = np.memmap(directory / "by_location.u4", dtype="<u4", mode="r", shape=(n,))
    return KeptIndex(
        directory=directory,
        manifest=envelope,
        rows=rows,
        ids=ids,
        id_offsets=offsets,
        by_location=by_location,
        allocations=[(a[0], a[1], a[2]) for a in tables["allocations"]],
        files=tables["files"],
    )
