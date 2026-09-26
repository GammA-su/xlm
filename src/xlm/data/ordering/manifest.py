"""Versioned independent within-source document-order manifests (P35 M5, §H).

An order manifest (``m5-independent-document-order-v1``) freezes, before
training, one permutation of each source's admitted train documents. It binds:

- the manifest version, the within-source order policy and the derivation
  (``sha256_keyed_sort_permutation_v1`` with an explicit ``order_seed``);
- the canonical train membership (its summary and ``canonical_membership_id``);
- each source's physical shard identity (checksums of the unchanged payload);
- each source's ordered doc-id sequence, through ``ordered_doc_ids_digest``;
- the invariants the intervention preserves.

``order_manifest_id`` is the canonical digest of the **header**: the manifest
without its (possibly long) ``ordered_doc_ids`` lists, which the header binds by
digest. A header can therefore be embedded in a comparison manifest and still
be verified; the full manifest additionally re-derives every sequence from its
seed, so a hand-edited order with recomputed digests is refused.

Derivation (documented, deterministic, no process state): within source ``s``,
documents are sorted ascending by
``SHA-256(json([algorithm, order_seed, canonical_membership_id, s, doc_id]))``
with ``doc_id`` as the tie-break. Keys depend only on the document identity, so
the order is independent of physical shard order, filesystem enumeration,
Python hash randomization, global RNG state and time. Sorting by 256-bit
pseudorandom keys gives a uniform permutation up to key collisions
(probability below ``n**2 / 2**257``).

This is **independent within-source document-order evidence** only. It says
nothing about robustness to other data-order interventions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.data.ordering.membership import (
    TRAIN_SPLIT,
    CanonicalMembership,
    MembershipError,
    summary_membership_id,
)

ORDER_MANIFEST_VERSION = "m5-independent-document-order-v1"
ORDER_ALGORITHM = "sha256_keyed_sort_permutation_v1"
ORDER_POLICY = "m5_within_source_document_permutation_v1"
#: The pre-M5 policy: within-source order is the prepared shard's physical order.
SHARD_NATIVE_POLICY = "shard_native_offset_order_v1"
SEQUENCE_DIGEST_VERSION = "xlm-ordered-doc-ids-v1"
MAX_MANIFEST_FILE_BYTES = 512 * 1024**2
INVARIANTS: dict[str, str] = {
    "split": TRAIN_SPLIT,
    "granularity": "whole_document",
    "document_content": "unchanged: token bytes, internal token order, byte spans, lineage, "
    "source, split and index metadata are read from the same shard payload",
    "scope": "within_source_only",
    "mixture": "unchanged: component weights, quotas, scheduler and exhaustion policy",
    "epochs": "every epoch replays the same frozen order (no reshuffle)",
    "runtime_shuffle": "none: the order is frozen before training",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEADER_KEYS = {
    "manifest_version",
    "within_source_order_policy",
    "derivation",
    "canonical_membership_id",
    "canonical_membership",
    "invariants",
    "sources",
}
_SOURCE_HEADER_KEYS = {"shard", "document_count", "token_count", "ordered_doc_ids_digest"}


class OrderManifestError(ValueError):
    """An order manifest, reference or pair is malformed, tampered or not independent."""

    def __init__(self, message: str, code: str = "order_manifest_invalid") -> None:
        super().__init__(message)
        self.code = code


def _is_seed(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _key(seed: int, membership_id: str, source_id: str, doc_id: str) -> bytes:
    raw = json.dumps(
        [ORDER_ALGORITHM, seed, membership_id, source_id, doc_id],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).digest()


def derive_order(
    doc_ids: Sequence[str], *, order_seed: int, membership_id: str, source_id: str
) -> list[str]:
    """The declared permutation of one source's documents (see module docstring)."""
    if not _is_seed(order_seed):
        raise OrderManifestError("order_seed must be an integer in [0, 2**63)")
    return sorted(
        doc_ids, key=lambda doc_id: (_key(order_seed, membership_id, source_id, doc_id), doc_id)
    )


def sequence_digest(doc_ids: Sequence[str]) -> str:
    """Digest of an ordered doc-id sequence (sensitive to every position)."""
    digest = hashlib.sha256(f"{SEQUENCE_DIGEST_VERSION}\n".encode())
    for doc_id in doc_ids:
        digest.update(json.dumps(doc_id, ensure_ascii=False).encode("utf-8") + b"\n")
    return digest.hexdigest()


def order_header(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest without ``ordered_doc_ids`` lists and without its own id."""
    header = {k: copy.deepcopy(v) for k, v in manifest.items() if k != "order_manifest_id"}
    sources = header.get("sources")
    if isinstance(sources, dict):
        header["sources"] = {
            sid: {k: v for k, v in entry.items() if k != "ordered_doc_ids"}
            if isinstance(entry, Mapping)
            else entry
            for sid, entry in sources.items()
        }
    return header


def header_with_id(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """A verifiable, compact header (id included) for embedding in comparisons."""
    header = order_header(manifest)
    header["order_manifest_id"] = manifest.get("order_manifest_id")
    return header


def build_order_manifest(membership: CanonicalMembership, *, order_seed: int) -> dict[str, Any]:
    """Freeze one independent within-source order over ``membership``."""
    if not _is_seed(order_seed):
        raise OrderManifestError("order_seed must be an integer in [0, 2**63)")
    membership_id = membership.membership_id
    sources: dict[str, Any] = {}
    for source_id in sorted(membership.sources):
        source = membership.sources[source_id]
        ordered = derive_order(
            source.doc_ids,
            order_seed=order_seed,
            membership_id=membership_id,
            source_id=source_id,
        )
        sources[source_id] = {
            "shard": dict(source.shard),
            "document_count": source.document_count,
            "token_count": source.token_count,
            "ordered_doc_ids_digest": sequence_digest(ordered),
            "ordered_doc_ids": ordered,
        }
    manifest: dict[str, Any] = {
        "manifest_version": ORDER_MANIFEST_VERSION,
        "within_source_order_policy": ORDER_POLICY,
        "derivation": {"algorithm": ORDER_ALGORITHM, "order_seed": order_seed},
        "canonical_membership_id": membership_id,
        "canonical_membership": membership.summary(),
        "invariants": dict(INVARIANTS),
        "sources": sources,
    }
    manifest["order_manifest_id"] = identity_digest(order_header(manifest))
    return manifest


def verify_order_header(header: Mapping[str, Any]) -> str:
    """Structure, version, membership binding and id of a header; returns the id."""
    if not isinstance(header, Mapping) or set(header) != _HEADER_KEYS | {"order_manifest_id"}:
        raise OrderManifestError("order manifest header has an unexpected shape")
    if header["manifest_version"] != ORDER_MANIFEST_VERSION:
        raise OrderManifestError(
            f"unsupported order manifest version {header['manifest_version']!r}"
        )
    if header["within_source_order_policy"] != ORDER_POLICY:
        raise OrderManifestError("order manifest declares an unknown within-source policy")
    derivation = header["derivation"]
    if (
        not isinstance(derivation, Mapping)
        or set(derivation) != {"algorithm", "order_seed"}
        or derivation["algorithm"] != ORDER_ALGORITHM
        or not _is_seed(derivation["order_seed"])
    ):
        raise OrderManifestError("order manifest derivation is not a seeded v1 permutation")
    if header["invariants"] != INVARIANTS:
        raise OrderManifestError("order manifest invariants differ from the M5 v1 invariants")
    try:
        membership_id = summary_membership_id(header["canonical_membership"])
    except MembershipError as exc:
        raise OrderManifestError(str(exc)) from exc
    if header["canonical_membership_id"] != membership_id:
        raise OrderManifestError(
            "canonical_membership_id does not match its membership summary",
            code="order_manifest_tampered",
        )
    summary_sources = header["canonical_membership"]["sources"]
    sources = header["sources"]
    if not isinstance(sources, Mapping) or set(sources) != set(summary_sources):
        raise OrderManifestError("order manifest sources differ from its membership")
    for sid, entry in sources.items():
        if not isinstance(entry, Mapping) or set(entry) != _SOURCE_HEADER_KEYS:
            raise OrderManifestError(f"order manifest source '{sid}' is malformed")
        summary = summary_sources[sid]
        if (
            entry["document_count"] != summary["document_count"]
            or entry["token_count"] != summary["token_count"]
        ):
            raise OrderManifestError(f"source '{sid}' counts differ from the membership")
        if not isinstance(entry["ordered_doc_ids_digest"], str) or not _HEX64.match(
            entry["ordered_doc_ids_digest"]
        ):
            raise OrderManifestError(f"source '{sid}' lacks an ordered-sequence digest")
        shard = entry["shard"]
        if not isinstance(shard, Mapping) or shard.get("source_id") != sid:
            raise OrderManifestError(f"source '{sid}' shard identity is malformed")
    payload = {k: v for k, v in header.items() if k != "order_manifest_id"}
    expected = identity_digest(dict(payload))
    if header["order_manifest_id"] != expected:
        raise OrderManifestError(
            "order_manifest_id does not match the manifest contents (tampered)",
            code="order_manifest_tampered",
        )
    return expected


def verify_order_manifest(manifest: Mapping[str, Any]) -> str:
    """Full verification: header, every sequence digest, and seed re-derivation."""
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("sources"), Mapping):
        raise OrderManifestError("order manifest has an unexpected shape")
    order_id = verify_order_header(header_with_id(manifest))
    seed = manifest["derivation"]["order_seed"]
    membership_id = manifest["canonical_membership_id"]
    for sid, entry in manifest["sources"].items():
        ordered = entry.get("ordered_doc_ids")
        if not isinstance(ordered, list) or not all(
            isinstance(doc, str) and doc for doc in ordered
        ):
            raise OrderManifestError(f"source '{sid}' has no ordered doc-id list")
        if len(ordered) != entry["document_count"] or len(set(ordered)) != len(ordered):
            raise OrderManifestError(
                f"source '{sid}' ordered list is not a permutation of its documents",
                code="order_manifest_tampered",
            )
        if sequence_digest(ordered) != entry["ordered_doc_ids_digest"]:
            raise OrderManifestError(
                f"source '{sid}' ordered list does not match its digest (tampered)",
                code="order_manifest_tampered",
            )
        derived = derive_order(
            ordered, order_seed=seed, membership_id=membership_id, source_id=str(sid)
        )
        if derived != ordered:
            raise OrderManifestError(
                f"source '{sid}' order is not the declared seeded derivation",
                code="order_manifest_tampered",
            )
    return order_id


def check_against_membership(manifest: Mapping[str, Any], membership: CanonicalMembership) -> None:
    """The (verified) manifest orders exactly this membership over these shards."""
    if manifest["canonical_membership_id"] != membership.membership_id:
        raise OrderManifestError(
            f"order manifest orders membership {manifest['canonical_membership_id']}, the "
            f"bound shards have membership {membership.membership_id}",
            code="order_membership_mismatch",
        )
    if manifest["canonical_membership"] != membership.summary():
        raise OrderManifestError(
            "order manifest membership summary differs from the bound shards",
            code="order_membership_mismatch",
        )
    for sid, source in membership.sources.items():
        entry = manifest["sources"][sid]
        if entry["shard"] != source.shard:
            raise OrderManifestError(
                f"source '{sid}' shard artifact differs from the order manifest's shard",
                code="order_shard_mismatch",
            )
        if sorted(entry["ordered_doc_ids"]) != sorted(source.doc_ids):
            raise OrderManifestError(
                f"source '{sid}' ordered documents differ from the shard membership",
                code="order_membership_mismatch",
            )


def write_order_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    """Atomic, never-overwriting publication of a verified manifest."""
    verify_order_manifest(manifest)
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite order manifest '{path}'")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(manifest, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode()
    if len(payload) > MAX_MANIFEST_FILE_BYTES:
        raise OrderManifestError("order manifest exceeds its byte bound")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_order_manifest(path: Path, *, expected_id: str | None = None) -> dict[str, Any]:
    """Bounded read + full verification; ``expected_id`` pins the identity."""
    path = Path(path)
    if not path.is_file():
        raise OrderManifestError(f"order manifest not found: {path}", code="order_manifest_missing")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_FILE_BYTES + 1)
    if len(raw) > MAX_MANIFEST_FILE_BYTES:
        raise OrderManifestError("order manifest exceeds its byte bound")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrderManifestError(f"order manifest is not JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise OrderManifestError("order manifest must be an object")
    order_id = verify_order_manifest(manifest)
    if expected_id is not None and order_id != expected_id:
        raise OrderManifestError(
            f"order manifest identity {order_id} differs from the pinned {expected_id}",
            code="order_manifest_pin_mismatch",
        )
    return manifest


def independence_problems(first: Mapping[str, Any], second: Mapping[str, Any]) -> list[str]:
    """Why two order headers/manifests are *not* independent orders of one membership.

    Independent means: both verify, different identities, the identical canonical
    membership, different seeds and, for every source with at least two
    documents, a different ordered sequence. Identical sequences under different
    seed labels are refused rather than presented as independent evidence.
    """
    problems: list[str] = []
    headers = []
    for label, candidate in (("first", first), ("second", second)):
        header = header_with_id(candidate)
        sources = candidate.get("sources")
        full = isinstance(sources, Mapping) and any(
            isinstance(entry, Mapping) and "ordered_doc_ids" in entry for entry in sources.values()
        )
        try:
            # A full manifest is also re-derived from its seed; a header binds by digest.
            verify_order_manifest(candidate) if full else verify_order_header(header)
        except OrderManifestError as exc:
            problems.append(f"{label} order manifest does not verify: {exc}")
        headers.append(header)
    if problems:
        return problems
    a, b = headers
    if a["order_manifest_id"] == b["order_manifest_id"]:
        problems.append("both orders are the same manifest")
    if a["canonical_membership_id"] != b["canonical_membership_id"] or (
        a["canonical_membership"] != b["canonical_membership"]
    ):
        problems.append(
            "orders are over different canonical memberships; a different dataset is not "
            "another order of the same membership"
        )
        return problems
    if a["derivation"]["order_seed"] == b["derivation"]["order_seed"]:
        problems.append("orders share one order seed")
    for sid in sorted(a["sources"]):
        count = a["sources"][sid]["document_count"]
        if (
            count >= 2
            and a["sources"][sid]["ordered_doc_ids_digest"]
            == b["sources"][sid]["ordered_doc_ids_digest"]
        ):
            problems.append(
                f"source '{sid}' has the identical document sequence in both orders; the seed "
                "labels differ but the order does not (choose another seed)"
            )
    return problems


def require_independent_orders(first: Mapping[str, Any], second: Mapping[str, Any]) -> None:
    problems = independence_problems(first, second)
    if problems:
        raise OrderManifestError("; ".join(problems), code="orders_not_independent")
