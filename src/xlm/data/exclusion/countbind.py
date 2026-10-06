"""C06 binding of exact counts: the counted tokenizer is the signed fit's, the kept index agrees.

Counting never trusts the C06 kept index for membership or locations: kept membership
comes from the authenticated C05 ``membership.jsonl`` stream (fast path) or the C05
SQLite gate (reference). With ``--c06-fit`` the count job additionally proves that it
belongs to exactly one C06 fit:

* the signed fit manifest verifies against the C05 trust root, names this C05 plan,
  completion, input manifest, kept membership and source seals, and is a fast-path fit;
* the counted tokenizer's identity (fingerprint, files digest, vocabulary, C05 binding)
  equals the tokenizer the fit signed;
* the fit's kept index verifies (signature, C05 binding, private snapshot, structure)
  and is the index the fit signed;
* the kept index's train rows agree with the counted documents per allocation (both
  paths); the fast path also compares every kept-index column with authenticated
  membership before any source byte is counted;
* before publication the fit manifest and every kept-index section are unchanged.

The signed counts payload then carries ``c06_fit`` = {fit digest, kept-index digest}.
Operator pins (expected digests, fingerprint, train document count) refuse with fixed
literal reasons. Content-free: no ids, texts or paths are reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from xlm.data.exclusion.artifacts import verify_signed
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.keptindex import KeptIndex, open_index, reverify_sections
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selection import allocation_key, check_binding
from xlm.data.exclusion.tokenizer_fit import FIT_KIND, FIT_MANIFEST

if TYPE_CHECKING:
    from xlm.data.exclusion.fitfast import Membership
    from xlm.data.exclusion.gates import C05View

#: Directory of the kept index inside a fast-path fit (``fitfast.KEPT_INDEX_DIR``).
KEPT_INDEX_DIR = "kept-index"
#: ``fitfast.FIT_PATH``; repeated to keep this module free of the fit pipeline imports.
FIT_PATH = "c06-fast-v1"


@dataclass(frozen=True)
class CountPins:
    """Operator-pinned expectations; ``None`` means not pinned."""

    tokenizer_fingerprint: str | None = None
    fit_digest: str | None = None
    kept_index_digest: str | None = None
    documents: int | None = None

    def needs_fit(self) -> bool:
        return self.fit_digest is not None or self.kept_index_digest is not None

    def check_tokenizer(self, identity: Mapping[str, Any]) -> None:
        pinned = self.tokenizer_fingerprint
        if pinned is not None and identity.get("fingerprint") != pinned:
            raise C05Error("tokenizer is not the pinned fingerprint")

    def check_documents(self, documents: int) -> None:
        if self.documents is not None and documents != self.documents:
            raise C05Error("kept train document count differs from the pinned count")


@dataclass
class C06Binding:
    """A verified C06 fit and its kept index; ``index`` is released after comparison."""

    directory: Path
    fit_digest: str
    kept_index_digest: str
    manifest_sha256: str
    sections: dict[str, Any]
    index: KeptIndex | None = field(repr=False, default=None)
    train_by_allocation: dict[str, int] = field(default_factory=dict)

    def record(self) -> dict[str, str]:
        """The signed counts payload's ``c06_fit`` value."""
        return {"fit_digest": self.fit_digest, "kept_index_digest": self.kept_index_digest}

    def release(self) -> None:
        self.index = None

    def reverify(self) -> None:
        """Before publication: the fit manifest and every kept-index section are unchanged."""
        if file_sha(self.directory / FIT_MANIFEST) != self.manifest_sha256:
            raise C05Error("C06 fit manifest changed during counting")
        reverify_sections(self.directory / KEPT_INDEX_DIR, self.sections)


def open_c06(
    directory: Path, view: C05View, identity: Mapping[str, Any], pins: CountPins
) -> C06Binding:
    """Verify the fit and its kept index against this C05 and the counted tokenizer."""
    manifest_path = directory / FIT_MANIFEST
    manifest_sha = file_sha(manifest_path)
    envelope = read_metadata(manifest_path, digested=False)
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, FIT_KIND)
    if body.get("fit_path") != FIT_PATH:
        raise C05Error("not a C06 fast-path fit")
    if pins.fit_digest is not None and envelope["digest"] != pins.fit_digest:
        raise C05Error("C06 fit is not the pinned fit")
    signed_tokenizer = body.get("tokenizer")
    if not isinstance(signed_tokenizer, dict) or any(
        signed_tokenizer.get(name) != value for name, value in identity.items()
    ):
        raise C05Error("counted tokenizer is not the C06 fit's tokenizer")
    kept = body.get("kept_index")
    if not isinstance(kept, dict) or kept.get("directory") != KEPT_INDEX_DIR:
        raise C05Error("C06 fit names no kept index")
    signed_index = kept.get("manifest_digest")
    if pins.kept_index_digest is not None and signed_index != pins.kept_index_digest:
        raise C05Error("C06 kept index is not the pinned kept index")
    index = open_index(directory / KEPT_INDEX_DIR, view)
    if index.manifest["digest"] != signed_index:
        raise C05Error("kept index differs from the signed fit")
    rows = index.rows
    train = rows["assigned_split"] == 0
    names = [allocation_key(*a) for a in index.allocations]
    per = np.bincount(rows["allocation"][train], minlength=len(names))
    if len(per) != len(names):
        raise C05Error("kept index allocation out of range")
    return C06Binding(
        directory=directory,
        fit_digest=str(envelope["digest"]),
        kept_index_digest=str(signed_index),
        manifest_sha256=manifest_sha,
        sections=dict(index.manifest["payload"]["sections"]),
        index=index,
        train_by_allocation={names[n]: int(per[n]) for n in range(len(names)) if per[n]},
    )


def compare_membership(binding: C06Binding, m: Membership, keys: list[str]) -> None:
    """Every kept-index column equals authenticated membership (fast path, before counting)."""
    index = binding.index
    if index is None:
        raise C05Error("kept index was released before its comparison")
    rows = index.rows
    if len(rows) != m.rows:
        raise C05Error("kept index row count differs from authenticated membership")
    position = {key: n for n, key in enumerate(keys)}
    try:
        mapping = np.asarray(
            [position[allocation_key(*a)] for a in index.allocations], dtype=np.int64
        )
    except KeyError as exc:
        raise C05Error("kept index names an allocation outside the frozen plan") from exc
    allocation = mapping[rows["allocation"].astype(np.int64)] if len(rows) else rows["allocation"]
    for column, values, expected in (
        ("file", rows["file"], m.file),
        ("row", rows["row"], m.row),
        ("bytes", rows["bytes"], m.nbytes),
        ("content", rows["content"], m.content),
        ("assigned_split", rows["assigned_split"], m.split),
        ("allocation", allocation, m.allocation),
        ("id_offsets", index.id_offsets, m.id_offsets),
    ):
        if not np.array_equal(values, expected):
            raise C05Error("kept index differs from authenticated membership: " + column)
    if not np.array_equal(index.ids, np.frombuffer(m.ids, dtype=np.uint8)):
        raise C05Error("kept index differs from authenticated membership: ids")


def check_totals(binding: C06Binding, allocations: Mapping[str, Mapping[str, int]]) -> None:
    """The counted documents per allocation equal the kept index's train rows (both paths)."""
    counted = {key: int(row["documents"]) for key, row in allocations.items() if row["documents"]}
    if counted != binding.train_by_allocation:
        raise C05Error("counted train documents differ from the C06 kept index")
