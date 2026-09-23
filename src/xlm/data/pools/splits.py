"""Group-safe, order-independent corpus split assignment.

Contract C05: diagnostic validation is reserved independently of candidate mixture
weights; validation sampling is deterministic and group-safe; size targets are met at
document boundaries with the actual bytes recorded; and a fixed quick-validation
subset is nested inside the full diagnostic set.

Splitting operates on *split groups*, the connected components of duplicate clusters
combined with lineage groups. Assigning whole groups is what prevents a book's other
chapters, a conversation's other turns or a near-duplicate of a held-out document
from sitting on the training side.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.clusters import UnionFind
from xlm.data.dedup.engine import DedupResult
from xlm.data.dedup.lineage import LINEAGE_RULE_VERSION, lineage_key

SPLIT_POLICY_VERSION = "1"

VALID_SPLITS = ("train", "diagnostic_val", "audit")


class SplitConfig(StrictConfigModel):
    """Frozen split policy.

    Defaults follow C05's initial targets: 50 MB of canonical text for diagnostic
    validation with a fixed 5 MB quick subset nested inside it. Actual achieved bytes
    will differ because groups are assigned whole, and the achieved figures are what
    gets recorded.
    """

    diagnostic_val_target_bytes: int = Field(default=50 * 1024 * 1024, ge=0)
    quick_val_target_bytes: int = Field(default=5 * 1024 * 1024, ge=0)
    audit_target_bytes: int = Field(default=5 * 1024 * 1024, ge=0)
    seed: int = Field(default=20260919, ge=0, description="Frozen split assignment seed.")

    def identity(self) -> str:
        """Stable behavioral identity of the split policy."""
        payload = (
            f"split:v{SPLIT_POLICY_VERSION}:lineage{LINEAGE_RULE_VERSION}:"
            f"val={self.diagnostic_val_target_bytes}:quick={self.quick_val_target_bytes}:"
            f"audit={self.audit_target_bytes}:seed={self.seed}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class SplitGroup:
    """One indivisible unit of split assignment."""

    group_id: str
    doc_ids: list[str]
    total_bytes: int
    origin_rules: list[str] = field(default_factory=list)


@dataclass
class SplitAssignment:
    """The frozen result of assigning every document to a split."""

    policy_identity: str
    dedup_identity: str
    assignments: dict[str, str]
    quick_val_doc_ids: list[str]
    group_of_doc: dict[str, str]
    achieved_bytes: dict[str, int]
    achieved_documents: dict[str, int]
    group_count: int
    quick_val_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def membership_digest(self) -> str:
        """Digest of the full assignment, used to bind a pool freeze to its splits."""
        payload = "|".join(
            f"{doc_id}={self.assignments[doc_id]}" for doc_id in sorted(self.assignments)
        )
        quick = ",".join(sorted(self.quick_val_doc_ids))
        return hashlib.sha256(f"{payload}#quick:{quick}".encode()).hexdigest()


def build_split_groups(
    documents: Iterable[CanonicalDocument],
    dedup_result: DedupResult | None = None,
) -> list[SplitGroup]:
    """Compute indivisible split groups over duplicate clusters and lineage families.

    A document joins a group through any relation: sharing a duplicate cluster, or
    sharing a lineage key. Groups are the connected components of that relation, so a
    chain (A duplicates B, B is a chapter of C) keeps all three together.
    """
    union = UnionFind()
    byte_counts: dict[str, int] = {}
    origin: dict[str, set[str]] = {}
    lineage_first: dict[str, str] = {}

    for doc in documents:
        union.add(doc.doc_id)
        byte_counts[doc.doc_id] = doc.utf8_byte_count
        rule, key = lineage_key(doc)
        if rule != "singleton":
            anchor = lineage_first.setdefault(key, doc.doc_id)
            if anchor != doc.doc_id:
                union.union(anchor, doc.doc_id)
                origin.setdefault(anchor, set()).add(f"lineage:{rule}")
                origin.setdefault(doc.doc_id, set()).add(f"lineage:{rule}")

    if dedup_result is not None:
        for cluster in dedup_result.clusters:
            present = [d for d in cluster.member_doc_ids if d in byte_counts]
            for other in present[1:]:
                union.union(present[0], other)
            for member in present:
                origin.setdefault(member, set()).add("duplicate_cluster")

    groups: list[SplitGroup] = []
    for members in union.groups().values():
        rules: set[str] = set()
        for member in members:
            rules |= origin.get(member, set())
        groups.append(
            SplitGroup(
                # Derived from sorted membership, so the ID does not depend on the
                # order documents arrived or on how the work was sharded.
                group_id="grp_"
                + hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()[:20],
                doc_ids=members,
                total_bytes=sum(byte_counts[m] for m in members),
                origin_rules=sorted(rules) or ["singleton"],
            )
        )
    groups.sort(key=lambda g: g.group_id)
    return groups


def _group_order_key(group: SplitGroup, seed: int) -> str:
    """Deterministic, content-derived shuffle key for a group."""
    return hashlib.blake2b(f"{seed}:{group.group_id}".encode(), digest_size=16).hexdigest()


def assign_splits(
    documents: Iterable[CanonicalDocument],
    dedup_result: DedupResult | None = None,
    config: SplitConfig | None = None,
) -> SplitAssignment:
    """Assign every document to a split, group-safely and deterministically.

    Assignment depends only on group content and the frozen seed, never on mixture
    weights (C05) and never on input order. Diagnostic validation is filled first,
    then audit, and everything else becomes training data.
    """
    cfg = config or SplitConfig()
    # Retain only metadata needed after grouping, not every document's text,
    # nested metadata and transform log. Consume one-shot inputs exactly once.
    byte_counts: dict[str, int] = {}

    def counted_documents() -> Iterable[CanonicalDocument]:
        for doc in documents:
            byte_counts[doc.doc_id] = doc.utf8_byte_count
            yield doc

    groups = build_split_groups(counted_documents(), dedup_result)

    ordered = sorted(groups, key=lambda g: (_group_order_key(g, cfg.seed), g.group_id))

    assignments: dict[str, str] = {}
    group_of_doc: dict[str, str] = {}
    achieved_bytes = dict.fromkeys(VALID_SPLITS, 0)
    achieved_documents = dict.fromkeys(VALID_SPLITS, 0)
    quick_val_doc_ids: list[str] = []
    quick_val_bytes = 0

    for group in ordered:
        # Whole groups only: a group is never partially held out, because a partial
        # hold-out is exactly the leak the grouping exists to prevent.
        if achieved_bytes["diagnostic_val"] < cfg.diagnostic_val_target_bytes:
            target_split = "diagnostic_val"
        elif achieved_bytes["audit"] < cfg.audit_target_bytes:
            target_split = "audit"
        else:
            target_split = "train"

        for doc_id in group.doc_ids:
            assignments[doc_id] = target_split
            group_of_doc[doc_id] = group.group_id
            achieved_bytes[target_split] += byte_counts[doc_id]
            achieved_documents[target_split] += 1

        # The quick subset is a prefix of the same group ordering, so it is nested
        # inside the diagnostic set by construction rather than by a later filter.
        if target_split == "diagnostic_val" and quick_val_bytes < cfg.quick_val_target_bytes:
            for doc_id in group.doc_ids:
                quick_val_doc_ids.append(doc_id)
                quick_val_bytes += byte_counts[doc_id]

    return SplitAssignment(
        policy_identity=cfg.identity(),
        dedup_identity=dedup_result.config_identity if dedup_result else "no_dedup",
        assignments=assignments,
        quick_val_doc_ids=sorted(quick_val_doc_ids),
        group_of_doc=group_of_doc,
        achieved_bytes=achieved_bytes,
        achieved_documents=achieved_documents,
        group_count=len(groups),
        quick_val_bytes=quick_val_bytes,
    )


def apply_splits(
    documents: Iterable[CanonicalDocument],
    assignment: SplitAssignment,
) -> Iterable[CanonicalDocument]:
    """Yield documents with their assigned split and split group recorded."""
    for doc in documents:
        split = assignment.assignments.get(doc.doc_id)
        if split is None:
            raise KeyError(f"document '{doc.doc_id}' has no split assignment")

        cluster_ids = dict(doc.cluster_ids)
        group_id = assignment.group_of_doc.get(doc.doc_id)
        if group_id:
            cluster_ids["split_group"] = group_id

        yield CanonicalDocument(
            doc_id=doc.doc_id,
            source_id=doc.source_id,
            source_revision=doc.source_revision,
            source_file=doc.source_file,
            source_row=doc.source_row,
            raw_hash=doc.raw_hash,
            clean_hash=doc.clean_hash,
            text=doc.text,
            utf8_byte_count=doc.utf8_byte_count,
            language=doc.language,
            language_confidence=doc.language_confidence,
            document_kind=doc.document_kind,
            source_metadata=dict(doc.source_metadata),
            parent_ids=list(doc.parent_ids),
            license_reference=doc.license_reference,
            transform_log=list(doc.transform_log),
            quality_reasons=list(doc.quality_reasons),
            cluster_ids=cluster_ids,
            split=split,
        )


def verify_group_disjointness(
    assignment: SplitAssignment,
    groups: Mapping[str, list[str]] | None = None,
) -> list[str]:
    """Return group IDs whose members landed in more than one split.

    An empty list is the invariant. A non-empty list is a leak, not a warning.
    """
    members_by_group: dict[str, set[str]] = {}
    for doc_id, group_id in assignment.group_of_doc.items():
        members_by_group.setdefault(group_id, set()).add(assignment.assignments[doc_id])

    if groups:
        for group_id, doc_ids in groups.items():
            splits = {assignment.assignments[d] for d in doc_ids if d in assignment.assignments}
            if splits:
                members_by_group.setdefault(group_id, set()).update(splits)

    return sorted(group_id for group_id, splits in members_by_group.items() if len(splits) > 1)
