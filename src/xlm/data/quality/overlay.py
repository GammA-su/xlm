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
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from xlm.data.quality.strictjson import loads_strict_bytes

if TYPE_CHECKING:
    from xlm.data.quality.progress import Telemetry

MEMBERSHIP_CHUNK = 16 * 1024**2
# Full digests only: no prefix or truncated digest can authorize an identity.
IDENTITY = np.dtype([("doc", "u1", (32,)), ("content", "u1", (32,)), ("bytes", "<u8")])
IDENTITY_SIZE = 72
_OFFSETS = (
    [int(IDENTITY.fields[name][1]) for name in ("doc", "content", "bytes")]
    if IDENTITY.fields
    else []
)
if IDENTITY.itemsize != IDENTITY_SIZE or _OFFSETS != [0, 32, 64]:
    raise RuntimeError("IDENTITY record layout is not the packed 32+32+8 byte layout")
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


class MembershipParser:
    """Strict per-row parsing of the authenticated kept membership, in stream order.

    Rows are written into zero-filled byte buffers laid out exactly as the final arrays
    (``IDENTITY`` is a packed 72-byte record; ``bool_`` is one 0/1 byte) and viewed as
    those arrays without a copy at the end: per-row slice writes instead of numpy scalar
    assignments, identical array contents. No parsed value may be used before the
    caller has authenticated the whole stream.
    """

    def __init__(
        self, plan_files: dict[str, Any], documents: dict[str, int], expected_rows: int
    ) -> None:
        from xlm.data.exclusion.fitscan import MEMBERSHIP_KEYS, SPLIT_CODES
        from xlm.data.exclusion.selection import allocation_key

        self.keys = MEMBERSHIP_KEYS
        self.splits = SPLIT_CODES
        self.plan_files = plan_files
        self.expected_rows = expected_rows
        self.kept_bytes = {path: bytearray(count) for path, count in documents.items()}
        self.identity_bytes = {
            path: bytearray(count * IDENTITY_SIZE) for path, count in documents.items()
        }
        self.allocations = {
            p: allocation_key(f.component, f.view, f.upstream_component)
            for p, f in plan_files.items()
        }
        self.kept_by_allocation: dict[str, int] = {}
        self.train_bytes: dict[str, int] = {}
        self.kept_by_split = dict.fromkeys(SPLIT_CODES, 0)
        self.rows = 0
        self.previous: bytes | None = None

    def stage(self, line: bytes) -> None:
        try:
            row = loads_strict_bytes(line)
        except ValueError:
            raise OverlayError("C05 membership row is not strict JSON") from None
        if type(row) is not dict or row.keys() != self.keys or row["decision"] != "kept":
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
            or split not in self.splits
            or type(content) is not str
            or len(content) != 64
            or not HEX.issuperset(content)
        ):
            raise OverlayError("C05 membership row schema")
        item = self.plan_files.get(path)
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
        if self.previous is not None and raw <= self.previous:
            raise OverlayError("C05 membership is not in strictly ascending document id order")
        self.previous = raw
        bitmap = self.kept_bytes[path]
        if bitmap[number - 1]:
            raise OverlayError("C05 membership repeats a source location")
        bitmap[number - 1] = 1
        offset = (number - 1) * IDENTITY_SIZE
        record = doc_digest(doc_id) + bytes.fromhex(content) + nbytes.to_bytes(8, "little")
        self.identity_bytes[path][offset : offset + IDENTITY_SIZE] = record
        key = self.allocations[path]
        self.kept_by_allocation[key] = self.kept_by_allocation.get(key, 0) + 1
        if split == "train":
            self.train_bytes[key] = self.train_bytes.get(key, 0) + nbytes
        self.kept_by_split[split] += 1
        self.rows += 1
        if self.rows > self.expected_rows:
            raise OverlayError("C05 membership count disagrees with its completion")

    def arrays(
        self,
    ) -> tuple[dict[str, npt.NDArray[np.bool_]], dict[str, npt.NDArray[Any]]]:
        kept = {p: np.frombuffer(b, dtype=np.bool_) for p, b in self.kept_bytes.items()}
        identity = {p: np.frombuffer(b, dtype=IDENTITY) for p, b in self.identity_bytes.items()}
        return kept, identity


def load_overlay(
    proof: Path,
    *,
    manifest_digest: str,
    documents: dict[str, int],
    allow_authored: bool,
    consumes: list[Path | str],
    check: Any = None,
    telemetry: Telemetry | None = None,
) -> KeptOverlay:
    """Authenticated per-file kept bitmaps and identities (row ``r`` is index ``r - 1``)."""
    from xlm.data.exclusion.fitfast import open_streamed
    from xlm.data.exclusion.policy import C05Error

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
    parser = MembershipParser(plan_files, documents, expected_rows)
    digest = hashlib.sha256()
    size = 0
    pending = b""
    stage = parser.stage

    membership = view.directory / "membership.jsonl"
    if telemetry is not None:
        telemetry.set_phase("overlay", expected_bytes, "bytes")
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
            if telemetry is not None:
                telemetry.advance(len(block))
    if pending:
        stage(pending)
    # Authenticate before any parsed value is used.
    if size != expected_bytes or digest.hexdigest() != completion["membership_sha256"]:
        raise OverlayError("C05 membership changed")
    if parser.rows != expected_rows:
        raise OverlayError("C05 membership count disagrees with its completion")
    _reconcile(
        completion,
        plan_files,
        parser.allocations,
        parser.kept_by_allocation,
        parser.train_bytes,
    )
    kept, identity = parser.arrays()
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
            "kept_by_split": parser.kept_by_split,
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
