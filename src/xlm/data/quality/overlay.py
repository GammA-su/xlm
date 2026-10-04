"""Optional C05 kept-membership overlay from an explicit, authenticated proof.

Only the public kept-membership output is read: ``open_streamed`` verifies the proof
specification, the plan identity, the detached-volume guard (it refuses while the
protected benchmark root is mounted) and the signed completion envelope. The
membership stream is then hashed exactly as parsed; no parsed row is used until its
SHA-256, byte size and row count equal the signed completion. Nothing is written to
any C05 artifact.
"""

from __future__ import annotations

import hashlib
import json
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

MEMBERSHIP_CHUNK = 16 * 1024**2


class OverlayError(ValueError):
    """Content-free overlay failure."""


@dataclass
class KeptOverlay:
    binding: dict[str, Any]
    kept: dict[str, npt.NDArray[np.bool_]]

    def bitmap(self, path: str) -> npt.NDArray[np.bool_]:
        return self.kept[path]


def load_overlay(
    proof: Path,
    *,
    manifest_digest: str,
    documents: dict[str, int],
    allow_authored: bool,
    consumes: list[Path | str],
) -> KeptOverlay:
    """Authenticated per-file kept bitmaps (row ``r`` of a file is index ``r - 1``)."""
    from xlm.data.exclusion.fitfast import open_streamed
    from xlm.data.exclusion.policy import C05Error

    try:
        view = open_streamed(proof, allow_authored=allow_authored, consumes=consumes)
    except C05Error as exc:
        raise OverlayError(f"C05 proof refused: {exc}") from None
    if view.plan.input_manifest_digest != manifest_digest:
        raise OverlayError("C05 proof was computed over a different input manifest")
    plan_files = {f.path: f.documents for f in view.plan.files}
    if plan_files != documents:
        raise OverlayError("C05 plan files differ from the audited manifest files")
    completion = view.completion
    expected_bytes = int(completion["membership_bytes"])
    expected_rows = int(completion["kept"])
    kept = {path: np.zeros(count, dtype=np.bool_) for path, count in documents.items()}
    digest = hashlib.sha256()
    size = rows = 0
    pending = b""
    previous: bytes | None = None
    ceiling = int(view.plan.resources.document_bytes)
    membership = view.directory / "membership.jsonl"
    order = sorted(documents)
    index = {path: n for n, path in enumerate(order)}
    staged = (array("I"), array("I"))
    with membership.open("rb", buffering=0) as stream:
        while block := stream.read(min(MEMBERSHIP_CHUNK, expected_bytes - size + 1)):
            digest.update(block)
            size += len(block)
            if size > expected_bytes:
                raise OverlayError("C05 membership changed")
            data = pending + block
            cut = data.rfind(b"\n") + 1
            pending = data[cut:]
            if len(pending) > ceiling:
                raise OverlayError("C05 membership record ceiling")
            for line in data[:cut].splitlines():
                previous = _stage(line, index, staged, previous)
                rows += 1
    if pending:
        previous = _stage(pending, index, staged, previous)
        rows += 1
    # Authenticate before any parsed value is used.
    if size != expected_bytes or digest.hexdigest() != completion["membership_sha256"]:
        raise OverlayError("C05 membership changed")
    if rows != expected_rows:
        raise OverlayError("C05 membership count disagrees with its completion")
    files = np.frombuffer(staged[0], dtype=np.uint32)
    numbers = np.frombuffer(staged[1], dtype=np.uint32).astype(np.int64)
    for n, path in enumerate(order):
        selected = numbers[files == n]
        bitmap = kept[path]
        if selected.size and (selected.min() < 1 or selected.max() > bitmap.shape[0]):
            raise OverlayError("C05 membership row outside its plan file")
        bitmap[selected - 1] = True
        if int(bitmap.sum()) != selected.size:
            raise OverlayError("C05 membership repeats a source location")
    return KeptOverlay(
        binding={
            "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
            "plan_digest": view.plan_digest,
            "completion_digest": view.receipt_digest,
            "membership_sha256": completion["membership_sha256"],
            "kept": expected_rows,
            "mode": view.mode,
        },
        kept=kept,
    )


def _stage(
    line: bytes,
    index: dict[str, int],
    staged: tuple[array[int], array[int]],
    previous: bytes | None,
) -> bytes:
    row = json.loads(line)
    if not isinstance(row, dict) or row.get("decision") != "kept":
        raise OverlayError("C05 membership row schema")
    doc_id, path, number = row.get("doc_id"), row.get("file"), row.get("row")
    if type(doc_id) is not str or type(path) is not str or type(number) is not int:
        raise OverlayError("C05 membership row schema")
    ordinal = index.get(path)
    if ordinal is None or not 1 <= number < 2**32:
        raise OverlayError("C05 membership row outside its plan file")
    raw = doc_id.encode("utf-8")
    if previous is not None and raw <= previous:
        raise OverlayError("C05 membership is not in strictly ascending document id order")
    staged[0].append(ordinal)
    staged[1].append(number)
    return raw
