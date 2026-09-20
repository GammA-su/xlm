"""Frozen clean-text pool manifests, tiering and offline verification.

A frozen pool manifest binds every input that determined the pool's contents: source
revisions, the selected raw files and row locators, the cleaning / dedup / exclusion /
split policy identities, the accepted document IDs, byte counts, license and
provenance receipts, and the code and schema versions in force.

Two rules follow from that binding, and both are enforced here rather than documented
and hoped for:

* A **missing required component blocks publication.** An unbound pool cannot be
  distinguished later from one built under different rules.
* A **behavioral change invalidates the pool.** Changing a normalization rule, a
  filter threshold, a dedup threshold or a source revision produces a different
  pool identity, so downstream token shards cannot silently reuse stale text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument

POOL_MANIFEST_VERSION = "1"
POOL_SCHEMA_VERSION = "1"

# Components a pool must bind before it may be published. Each maps to a field that
# must be present and non-empty on PoolBinding.
REQUIRED_BINDING_FIELDS = (
    "source_revisions",
    "cleaning_policy_identity",
    "dedup_policy_identity",
    "split_policy_identity",
    "license_receipts",
    "producer_code_version",
)


class PoolTier(StrEnum):
    """Whether a pool is a tiny demonstration or a production-ready corpus.

    The tier is derived from measured size, never asserted by the caller, so a demo
    pool cannot be relabelled as production by editing a field.
    """

    DEMO_PILOT = "demo_pilot"
    PRODUCTION_READY = "production_ready"


# Minimum distinct training bytes for a pool to be considered production-ready.
# This is a project policy threshold, not an external standard.
PRODUCTION_MIN_TRAIN_BYTES = 1 * 1024 * 1024 * 1024


class PoolPublicationError(RuntimeError):
    """Raised when a pool is incomplete, unverifiable or fails its bindings."""


@dataclass
class PoolBinding:
    """Every input that determined the pool's contents."""

    source_revisions: dict[str, str] = field(default_factory=dict)
    selected_raw_files: dict[str, list[str]] = field(default_factory=dict)
    row_locator_digest: str = ""
    cleaning_policy_identity: str = ""
    dedup_policy_identity: str = ""
    exclusion_policy_identity: str = ""
    split_policy_identity: str = ""
    view_identities: dict[str, str] = field(default_factory=dict)
    overlap_policy: str = ""
    license_receipts: dict[str, str] = field(default_factory=dict)
    producer_code_version: str = ""
    schema_version: str = POOL_SCHEMA_VERSION

    def missing_components(self) -> list[str]:
        """Return the required binding fields that are absent or empty."""
        missing = []
        for name in REQUIRED_BINDING_FIELDS:
            value = getattr(self, name)
            if not value:
                missing.append(name)
        return missing

    def identity(self) -> str:
        """Digest of every behavioral binding.

        Exclusion policy is included even though it is optional to *supply*: once
        supplied it changes membership, so it must change identity.
        """
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SplitInventory:
    """Per-split document and byte counts actually achieved."""

    documents: dict[str, int] = field(default_factory=dict)
    bytes: dict[str, int] = field(default_factory=dict)
    quick_val_doc_ids: list[str] = field(default_factory=list)
    quick_val_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FrozenPoolManifest:
    """An immutable, verifiable record of a frozen clean-text pool."""

    pool_id: str
    manifest_version: str
    created_at: str
    tier: str
    binding: PoolBinding
    accepted_doc_ids: list[str]
    split_of_doc: dict[str, str]
    inventory: SplitInventory
    membership_digest: str
    content_digest: str
    view_membership: dict[str, Any] = field(default_factory=dict)
    overlap_audit: dict[str, Any] = field(default_factory=dict)
    sufficiency: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_production_ready(self) -> bool:
        return self.tier == PoolTier.PRODUCTION_READY

    @property
    def document_count(self) -> int:
        return len(self.accepted_doc_ids)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["binding"] = self.binding.to_dict()
        data["inventory"] = self.inventory.to_dict()
        return data

    def save(self, path: Path) -> Path:
        """Write the manifest atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> FrozenPoolManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["binding"] = PoolBinding(**data["binding"])
        data["inventory"] = SplitInventory(**data["inventory"])
        return FrozenPoolManifest(**data)


def compute_membership_digest(doc_ids: Iterable[str], split_of_doc: dict[str, str]) -> str:
    """Digest of which documents are in the pool and where each one sits."""
    payload = "|".join(f"{doc_id}={split_of_doc.get(doc_id, '?')}" for doc_id in sorted(doc_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_content_digest(documents: Iterable[CanonicalDocument]) -> str:
    """Digest of the actual canonical text, by clean hash.

    Membership alone cannot detect text that changed under a fixed set of document
    IDs; this digest can, which is what makes offline verification meaningful.
    """
    entries = sorted(f"{doc.doc_id}:{doc.clean_hash}" for doc in documents)
    return hashlib.sha256("|".join(entries).encode("utf-8")).hexdigest()


def derive_tier(train_bytes: int) -> PoolTier:
    """Derive the pool tier from measured distinct training bytes."""
    if train_bytes >= PRODUCTION_MIN_TRAIN_BYTES:
        return PoolTier.PRODUCTION_READY
    return PoolTier.DEMO_PILOT


def verify_pool_offline(
    manifest: FrozenPoolManifest,
    documents: Iterable[CanonicalDocument],
) -> list[str]:
    """Re-derive the pool's digests from documents on disk and report discrepancies.

    Returns an empty list when the pool verifies. Requires no network and no
    knowledge beyond the manifest and the stored canonical records.
    """
    doc_list = list(documents)
    problems: list[str] = []

    present_ids = {doc.doc_id for doc in doc_list}
    expected_ids = set(manifest.accepted_doc_ids)

    missing = sorted(expected_ids - present_ids)
    if missing:
        problems.append(f"{len(missing)} accepted document(s) are absent: {missing[:5]}")

    unexpected = sorted(present_ids - expected_ids)
    if unexpected:
        problems.append(
            f"{len(unexpected)} document(s) are present but not accepted: {unexpected[:5]}"
        )

    for doc in doc_list:
        expected_split = manifest.split_of_doc.get(doc.doc_id)
        if expected_split is not None and doc.split != expected_split:
            problems.append(
                f"document '{doc.doc_id}' is in split '{doc.split}' but the manifest "
                f"records '{expected_split}'"
            )
            break

    recomputed_membership = compute_membership_digest(
        [d.doc_id for d in doc_list], {d.doc_id: d.split for d in doc_list}
    )
    if recomputed_membership != manifest.membership_digest:
        problems.append("membership digest mismatch: pool contents differ from the manifest")

    recomputed_content = compute_content_digest(doc_list)
    if recomputed_content != manifest.content_digest:
        problems.append(
            "content digest mismatch: canonical text changed under unchanged document IDs"
        )

    return problems


def assert_publishable(binding: PoolBinding, tier_notes: list[str] | None = None) -> None:
    """Refuse to publish a pool whose required components are unbound."""
    missing = binding.missing_components()
    if missing:
        raise PoolPublicationError(
            "cannot publish pool: required binding component(s) missing or empty: "
            f"{missing}. An unbound pool cannot later be distinguished from one built "
            "under different policies."
        )
    if tier_notes:
        for note in tier_notes:
            if not note.strip():
                raise PoolPublicationError("tier notes must not be blank")


def build_pool_id(binding: PoolBinding, membership_digest: str, label: str = "pool") -> str:
    """Derive the pool identity from its bindings and membership.

    Any behavioral change -- a normalization rule, a filter threshold, a source
    revision -- flows into ``binding.identity()`` and therefore into this ID.
    """
    payload = f"v{POOL_MANIFEST_VERSION}:{binding.identity()}:{membership_digest}"
    return f"{label}_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def new_manifest_timestamp() -> str:
    return datetime.now(UTC).isoformat()
