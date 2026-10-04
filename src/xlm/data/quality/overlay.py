"""Optional C05 kept-membership overlay from an explicit, authenticated proof.

Only the PUBLIC kept-membership output is read: ``open_streamed`` verifies the proof
specification, the plan identity, the detached-volume guard (it refuses while the
protected benchmark root is mounted) and the signed completion envelope. The
membership stream is then hashed exactly as parsed; no parsed row is used until its
SHA-256, byte size and row count equal the signed completion, and the per-allocation
kept / train-byte / non-kept totals reconcile with the signed completion.

Every kept row keeps its FULL identity: the complete SHA-256 of its ``doc_id``, the
complete 32-byte C05 ``content`` digest (``canonical.digest`` of the canonical row)
and its canonical byte count. The source scan verifies all three against the
canonical row at that file/row and refuses on any mismatch. Rows are stored in dense
per-file arrays as they stream, so grouping is linear in the kept rows. Nothing is
written to any C05 artifact; no protected or private C05 material is opened.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

MEMBERSHIP_CHUNK = 16 * 1024**2
# Full digests only: no prefix or truncated digest can authorize an identity.
IDENTITY = np.dtype([("doc", "u1", (32,)), ("content", "u1", (32,)), ("bytes", "<u8")])
HEX = frozenset("0123456789abcdef")


class OverlayError(ValueError):
    """Content-free overlay failure."""


def doc_digest(doc_id: str) -> bytes:
    return hashlib.sha256(doc_id.encode("utf-8", "surrogatepass")).digest()


@dataclass
class KeptOverlay:
    binding: dict[str, Any]
    kept: dict[str, npt.NDArray[np.bool_]]
    identity: dict[str, npt.NDArray[Any]]
    forbidden_roots: tuple[str, ...]

    def bitmap(self, path: str) -> npt.NDArray[np.bool_]:
        return self.kept[path]


def load_overlay(
    proof: Path,
    *,
    manifest_digest: str,
    documents: dict[str, int],
    allow_authored: bool,
    consumes: list[Path | str],
    check: Any = None,
) -> KeptOverlay:
    """Authenticated per-file kept bitmaps and identities (row ``r`` is index ``r - 1``)."""
    from xlm.data.evidence_v2 import canonical
    from xlm.data.exclusion.fitfast import open_streamed
    from xlm.data.exclusion.fitscan import MEMBERSHIP_KEYS, SPLIT_CODES
    from xlm.data.exclusion.policy import C05Error
    from xlm.data.exclusion.selection import allocation_key

    try:
        view = open_streamed(proof, allow_authored=allow_authored, consumes=consumes)
    except C05Error as exc:
        raise OverlayError(f"C05 proof refused: {exc}") from None
    if view.plan.input_manifest_digest != manifest_digest:
        raise OverlayError("C05 proof was computed over a different input manifest")
    plan_files = {f.path: f for f in view.plan.files}
    if {p: f.documents for p, f in plan_files.items()} != documents:
        raise OverlayError("C05 plan files differ from the audited manifest files")
    completion = view.completion
    expected_bytes = int(completion["membership_bytes"])
    expected_rows = int(completion["kept"])
    ceiling = int(view.plan.resources.document_bytes)
    kept = {path: np.zeros(count, dtype=np.bool_) for path, count in documents.items()}
    identity = {path: np.zeros(count, dtype=IDENTITY) for path, count in documents.items()}
    allocations = {
        p: allocation_key(f.component, f.view, f.upstream_component) for p, f in plan_files.items()
    }
    kept_by_allocation: dict[str, int] = {}
    train_bytes: dict[str, int] = {}
    kept_by_split = dict.fromkeys(SPLIT_CODES, 0)
    digest = hashlib.sha256()
    size = rows = 0
    pending = b""
    previous: bytes | None = None

    def stage(line: bytes) -> None:
        nonlocal previous, rows
        try:
            row = canonical.loads_bytes_strict(line)
        except ValueError:
            raise OverlayError("C05 membership row is not strict JSON") from None
        if type(row) is not dict or row.keys() != MEMBERSHIP_KEYS or row["decision"] != "kept":
            raise OverlayError("C05 membership row schema")
        doc_id, path, number, nbytes = row["doc_id"], row["file"], row["row"], row["bytes"]
        content, split = row["content"], row["split"]
        if (
            type(doc_id) is not str
            or not doc_id
            or type(path) is not str
            or type(number) is not int
            or type(nbytes) is not int
            or nbytes < 0
            or split not in SPLIT_CODES
            or type(content) is not str
            or len(content) != 64
            or not HEX.issuperset(content)
        ):
            raise OverlayError("C05 membership row schema")
        item = plan_files.get(path)
        if item is None or not 1 <= number <= item.documents:
            raise OverlayError("C05 membership row outside its plan file")
        if (row["component"], row["view"], row["upstream_component"], row["source_id"]) != (
            item.component,
            item.view,
            item.upstream_component,
            item.source_id,
        ):
            raise OverlayError("C05 membership row allocation differs from its plan file")
        raw = doc_id.encode("utf-8", "surrogatepass")
        if previous is not None and raw <= previous:
            raise OverlayError("C05 membership is not in strictly ascending document id order")
        previous = raw
        bitmap = kept[path]
        if bitmap[number - 1]:
            raise OverlayError("C05 membership repeats a source location")
        bitmap[number - 1] = True
        table = identity[path]
        table["doc"][number - 1] = np.frombuffer(doc_digest(doc_id), dtype=np.uint8)
        table["content"][number - 1] = np.frombuffer(bytes.fromhex(content), dtype=np.uint8)
        table["bytes"][number - 1] = nbytes
        key = allocations[path]
        kept_by_allocation[key] = kept_by_allocation.get(key, 0) + 1
        if split == "train":
            train_bytes[key] = train_bytes.get(key, 0) + nbytes
        kept_by_split[split] += 1
        rows += 1
        if rows > expected_rows:
            raise OverlayError("C05 membership count disagrees with its completion")

    membership = view.directory / "membership.jsonl"
    with membership.open("rb", buffering=0) as stream:
        while block := stream.read(min(MEMBERSHIP_CHUNK, expected_bytes - size + 1)):
            if check is not None:
                check()
            digest.update(block)
            size += len(block)
            if size > expected_bytes:
                raise OverlayError("C05 membership changed")
            data = pending + block
            cut = data.rfind(b"\n") + 1
            pending = data[cut:]
            if len(pending) > ceiling:
                raise OverlayError("C05 membership record ceiling")
            for line in data[:cut].split(b"\n")[:-1]:
                stage(line)
    if pending:
        stage(pending)
    # Authenticate before any parsed value is used.
    if size != expected_bytes or digest.hexdigest() != completion["membership_sha256"]:
        raise OverlayError("C05 membership changed")
    if rows != expected_rows:
        raise OverlayError("C05 membership count disagrees with its completion")
    _reconcile(completion, plan_files, allocations, kept_by_allocation, train_bytes)
    forbidden: list[str] = [str(view.plan.scratch_root)]
    if view.plan.isolation is not None:
        forbidden.append(str(view.plan.isolation.protected_root.path))
    return KeptOverlay(
        binding={
            "proof_sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
            "plan_digest": view.plan_digest,
            "completion_digest": view.receipt_digest,
            "membership_sha256": completion["membership_sha256"],
            "kept": expected_rows,
            "kept_by_split": kept_by_split,
            "mode": view.mode,
            "identity_verification": (
                "per kept row: full doc_id SHA-256, full 32-byte C05 content digest, bytes"
            ),
        },
        kept=kept,
        identity=identity,
        forbidden_roots=tuple(forbidden),
    )


def _reconcile(
    completion: dict[str, Any],
    plan_files: dict[str, Any],
    allocations: dict[str, str],
    kept: dict[str, int],
    train_bytes: dict[str, int],
) -> None:
    """Kept / train-byte / non-kept totals of the signed completion, per allocation."""
    documents: dict[str, int] = {}
    for path, item in plan_files.items():
        documents[allocations[path]] = documents.get(allocations[path], 0) + item.documents
    signed = completion.get("allocations", {})
    if not set(signed) <= set(documents):
        raise OverlayError("C05 completion allocation outside the plan")
    for key, total in documents.items():
        counts = signed.get(key, {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0})
        actual = (kept.get(key, 0), train_bytes.get(key, 0), total - kept.get(key, 0))
        expected = (
            counts["kept"],
            counts["train_bytes"],
            counts["excluded"] + counts["duplicate"],
        )
        if actual != expected:
            raise OverlayError("C05 membership disagrees with its signed completion accounting")
