"""Pool assembly, sufficiency accounting and train/validation leak checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.pools.manifest import (
    POOL_MANIFEST_VERSION,
    FrozenPoolManifest,
    PoolBinding,
    PoolPublicationError,
    SplitInventory,
    assert_publishable,
    build_pool_id,
    compute_content_digest,
    compute_membership_digest,
    derive_tier,
    new_manifest_timestamp,
)
from xlm.data.pools.views import (
    OverlapPolicy,
    SourceView,
    audit_overlap,
    resolve_view_membership,
)

# Conservative planning constant for turning canonical bytes into a token estimate
# before a tokenizer exists. Deliberately labelled an estimate everywhere it is used:
# the real figure comes from the frozen tokenizer in P12.
ESTIMATED_BYTES_PER_TOKEN = 4.0


class LeakDetectedError(RuntimeError):
    """Raised when pool membership would leak held-out text into training."""


@dataclass
class SufficiencyReport:
    """Whether the pool holds enough distinct text for a planned token budget."""

    distinct_train_bytes: int
    distinct_train_documents: int
    estimated_unique_tokens: int
    planned_budget_tokens: int
    required_epochs: float
    repeated_exposure_tokens: int
    is_sufficient_without_repetition: bool
    token_estimate_basis: str = (
        f"canonical UTF-8 bytes / {ESTIMATED_BYTES_PER_TOKEN} "
        "(planning estimate only; exact counts require the frozen tokenizer)"
    )
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_sufficiency(
    distinct_train_bytes: int,
    distinct_train_documents: int,
    planned_budget_tokens: int,
) -> SufficiencyReport:
    """Report whether distinct text covers a planned budget, and by how much.

    A shortfall is surfaced as a required repetition factor rather than hidden: C07
    forbids unreported repeated exposure, so the deficit has to be visible before a
    run is planned, not discovered from a loss curve afterwards.
    """
    estimated_tokens = int(distinct_train_bytes / ESTIMATED_BYTES_PER_TOKEN)
    warnings: list[str] = []

    if planned_budget_tokens <= 0:
        required_epochs = 0.0
        repeated = 0
        sufficient = True
    else:
        if estimated_tokens <= 0:
            required_epochs = float("inf")
            repeated = planned_budget_tokens
            sufficient = False
            warnings.append(
                "pool holds no distinct training text; no budget can be met without "
                "acquiring more sources."
            )
        else:
            required_epochs = planned_budget_tokens / estimated_tokens
            repeated = max(0, planned_budget_tokens - estimated_tokens)
            sufficient = estimated_tokens >= planned_budget_tokens

    if not sufficient and estimated_tokens > 0:
        warnings.append(
            f"planned budget of {planned_budget_tokens:,} target tokens exceeds the "
            f"estimated {estimated_tokens:,} unique tokens; roughly "
            f"{required_epochs:.2f} passes over the pool would be required, so "
            f"about {repeated:,} target tokens would be repeated exposure."
        )

    return SufficiencyReport(
        distinct_train_bytes=distinct_train_bytes,
        distinct_train_documents=distinct_train_documents,
        estimated_unique_tokens=estimated_tokens,
        planned_budget_tokens=planned_budget_tokens,
        required_epochs=required_epochs,
        repeated_exposure_tokens=repeated,
        is_sufficient_without_repetition=sufficient,
        warnings=warnings,
    )


def detect_split_leaks(documents: Iterable[CanonicalDocument]) -> list[str]:
    """Report duplicate-cluster or lineage links that straddle the split boundary.

    C05 requires duplicate and parent IDs to be train-disjoint. A cluster with one
    member in training and another in diagnostic validation is a leak, whatever the
    split policy intended.
    """
    doc_list = list(documents)
    split_of = {doc.doc_id: doc.split for doc in doc_list}
    problems: list[str] = []

    clusters: dict[str, set[str]] = {}
    for doc in doc_list:
        for key in ("duplicate_cluster", "split_group"):
            cluster_id = doc.cluster_ids.get(key)
            if cluster_id:
                clusters.setdefault(f"{key}:{cluster_id}", set()).add(doc.split)

    for cluster_key, splits in sorted(clusters.items()):
        if len(splits) > 1:
            problems.append(
                f"{cluster_key} spans splits {sorted(splits)}; duplicate or grouped "
                "documents must be train-disjoint"
            )

    for doc in doc_list:
        for parent_id in doc.parent_ids:
            parent_split = split_of.get(parent_id)
            if parent_split is not None and parent_split != doc.split:
                problems.append(
                    f"document '{doc.doc_id}' is in split '{doc.split}' but its parent "
                    f"'{parent_id}' is in '{parent_split}'"
                )

    return problems


class PoolBuildConfig(StrictConfigModel):
    """Configuration for assembling a pool from source views."""

    pool_label: str = Field(default="pool", min_length=1)
    overlap_policy: OverlapPolicy = Field(default=OverlapPolicy.EXCLUSIVE_ASSIGNMENT)
    planned_budget_tokens: int = Field(default=0, ge=0)
    require_no_leaks: bool = Field(
        default=True,
        description="Refuse to build a pool whose groups straddle the split boundary.",
    )


@dataclass
class PoolAssembly:
    """A built, not-yet-published pool together with its evidence."""

    manifest: FrozenPoolManifest
    accepted_documents: list[CanonicalDocument]
    leaks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "accepted_document_count": len(self.accepted_documents),
            "leaks": self.leaks,
        }


def build_pool(
    documents: Iterable[CanonicalDocument],
    views: list[SourceView],
    binding: PoolBinding,
    config: PoolBuildConfig | None = None,
    quick_val_doc_ids: Iterable[str] | None = None,
) -> PoolAssembly:
    """Assemble a frozen pool from deterministic source views.

    ``quick_val_doc_ids`` carries the fixed quick-validation subset forward from the
    split assignment. It is recorded on the pool so the regime can freeze it once for
    the campaign, and it is narrowed to documents that actually reached the pool and
    sit in the diagnostic split.

    Publication is refused when a required binding component is missing, or when the
    pool would leak held-out text into training.
    """
    cfg = config or PoolBuildConfig()
    doc_list = list(documents)

    membership = resolve_view_membership(doc_list, views, cfg.overlap_policy)
    overlap = audit_overlap(membership)
    # Declared shares travel with the pool so a later tokenizer fit can balance by
    # raw-byte share without re-reading the view definition file.
    declared_shares = {view.view_id: view.declared_raw_byte_share for view in views}

    selected_ids = {d for ids in membership.doc_ids_by_view.values() for d in ids}
    accepted = [doc for doc in doc_list if doc.doc_id in selected_ids]
    if not accepted:
        raise PoolPublicationError(
            "cannot publish pool: no document matched any view selector. "
            "A pool with no accepted text is not a usable corpus."
        )

    leaks = detect_split_leaks(accepted)
    if leaks and cfg.require_no_leaks:
        raise LeakDetectedError(
            f"refusing to freeze a leaking pool: {len(leaks)} issue(s) found. First: {leaks[0]}"
        )

    split_of_doc = {doc.doc_id: doc.split for doc in accepted}
    documents_per_split: dict[str, int] = {}
    bytes_per_split: dict[str, int] = {}
    for doc in accepted:
        documents_per_split[doc.split] = documents_per_split.get(doc.split, 0) + 1
        bytes_per_split[doc.split] = bytes_per_split.get(doc.split, 0) + doc.utf8_byte_count

    diagnostic_ids = {d for d, split in split_of_doc.items() if split == "diagnostic_val"}
    quick_ids = sorted(set(quick_val_doc_ids or ()) & diagnostic_ids)
    byte_of = {doc.doc_id: doc.utf8_byte_count for doc in accepted}

    inventory = SplitInventory(
        documents=documents_per_split,
        bytes=bytes_per_split,
        quick_val_doc_ids=quick_ids,
        quick_val_bytes=sum(byte_of[d] for d in quick_ids),
    )

    train_bytes = bytes_per_split.get("train", 0)
    train_docs = documents_per_split.get("train", 0)
    sufficiency = assess_sufficiency(train_bytes, train_docs, cfg.planned_budget_tokens)

    # The binding carries the view identities and overlap policy actually used, so a
    # changed selector produces a different pool rather than silently new contents.
    bound = PoolBinding(**{**binding.to_dict()})
    bound.view_identities = {view.view_id: view.identity() for view in views}
    bound.overlap_policy = str(cfg.overlap_policy)

    assert_publishable(bound)

    membership_digest = compute_membership_digest(split_of_doc, split_of_doc)
    content_digest = compute_content_digest(accepted)
    tier = derive_tier(train_bytes)

    notes = list(overlap.warnings) + list(sufficiency.warnings)
    if tier.value == "demo_pilot":
        notes.append(
            f"DEMO/PILOT POOL: {train_bytes:,} distinct training bytes is below the "
            "production threshold. Not suitable for a production training campaign."
        )

    manifest = FrozenPoolManifest(
        pool_id=build_pool_id(bound, membership_digest, cfg.pool_label),
        manifest_version=POOL_MANIFEST_VERSION,
        created_at=new_manifest_timestamp(),
        tier=str(tier),
        binding=bound,
        accepted_doc_ids=sorted(split_of_doc),
        split_of_doc=split_of_doc,
        inventory=inventory,
        membership_digest=membership_digest,
        content_digest=content_digest,
        view_membership={**membership.to_dict(), "declared_shares": declared_shares},
        overlap_audit=overlap.to_dict(),
        sufficiency=sufficiency.to_dict(),
        notes=notes,
    )

    return PoolAssembly(manifest=manifest, accepted_documents=accepted, leaks=leaks)
