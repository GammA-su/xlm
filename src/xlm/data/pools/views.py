"""Source families, deterministic source views and overlap treatment policy.

A **source family** is an upstream corpus (a crawl, a book collection, an
encyclopedia). A **view** is a deterministic selector over actual canonical record
fields that carves a subset out of one family. The distinction matters for sampling
weight: if two views both select the same document and both are given a mixture
weight, that document is silently exposed twice. This module makes that overlap
explicit and forces a declared treatment for it.

Selectors are data, not code. They match on fields the adapter actually recorded --
source IDs, document kind, language, split and metadata equality -- so a view can be
re-evaluated later and reproduce exactly the same membership.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument

VIEW_SELECTOR_VERSION = "1"


class OverlapPolicy(StrEnum):
    """How a document selected by more than one view is treated.

    ``EXCLUSIVE_ASSIGNMENT`` gives the document to exactly one view, so total
    sampling weight is conserved. ``MULTI_VIEW`` keeps it in every matching view and
    requires the caller to account for the repeated exposure explicitly. There is no
    implicit third option: an undeclared overlap is an error.
    """

    EXCLUSIVE_ASSIGNMENT = "exclusive_assignment"
    MULTI_VIEW = "multi_view"


class ViewSelector(StrictConfigModel):
    """A deterministic, data-only selector over canonical record fields."""

    source_ids: list[str] = Field(
        default_factory=list, description="Match any of these source IDs."
    )
    document_kinds: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    splits: list[str] = Field(default_factory=list)
    metadata_equals: dict[str, str] = Field(
        default_factory=dict,
        description="Require source_metadata[key] == value for every entry.",
    )
    min_utf8_bytes: int = Field(default=0, ge=0)
    max_utf8_bytes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_byte_window(self) -> ViewSelector:
        if self.max_utf8_bytes is not None and self.max_utf8_bytes < self.min_utf8_bytes:
            raise ValueError(
                f"max_utf8_bytes ({self.max_utf8_bytes}) is below "
                f"min_utf8_bytes ({self.min_utf8_bytes})"
            )
        return self

    def matches(self, doc: CanonicalDocument) -> bool:
        """Return whether ``doc`` is selected. Empty criteria are unconstrained."""
        if self.source_ids and doc.source_id not in self.source_ids:
            return False
        if self.document_kinds and doc.document_kind not in self.document_kinds:
            return False
        if self.languages and doc.language not in self.languages:
            return False
        if self.splits and doc.split not in self.splits:
            return False
        if doc.utf8_byte_count < self.min_utf8_bytes:
            return False
        if self.max_utf8_bytes is not None and doc.utf8_byte_count > self.max_utf8_bytes:
            return False
        for key, expected in self.metadata_equals.items():
            if str(doc.source_metadata.get(key, "")) != expected:
                return False
        return True

    def identity(self) -> str:
        """Stable identity of this selector, so a changed selector is visible."""
        payload = f"selector:v{VIEW_SELECTOR_VERSION}:{self.model_dump_json()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class SourceView(StrictConfigModel):
    """A named, deterministic selection over one source family."""

    view_id: str = Field(min_length=1)
    family_id: str = Field(min_length=1, description="The upstream corpus this view draws from.")
    selector: ViewSelector = Field(default_factory=ViewSelector)
    declared_raw_byte_share: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Declared share of raw bytes, used to balance tokenizer-fit sampling.",
    )
    priority: int = Field(
        default=0,
        description=(
            "Exclusive-assignment priority. Higher wins; ties break on view_id, so "
            "assignment never depends on view declaration order."
        ),
    )
    notes: list[str] = Field(default_factory=list)

    def identity(self) -> str:
        payload = (
            f"view:{self.view_id}:family:{self.family_id}:"
            f"{self.selector.identity()}:prio={self.priority}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class ViewOverlap:
    """A document selected by more than one view."""

    doc_id: str
    view_ids: list[str]
    family_ids: list[str]
    assigned_view_id: str | None
    utf8_byte_count: int

    @property
    def is_cross_family(self) -> bool:
        """Overlap spanning two families is a provenance signal, not just a duplicate."""
        return len(set(self.family_ids)) > 1

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "is_cross_family": self.is_cross_family}


@dataclass
class ViewMembership:
    """Resolved membership of every view after the overlap policy has been applied."""

    policy: OverlapPolicy
    doc_ids_by_view: dict[str, list[str]]
    bytes_by_view: dict[str, int]
    overlaps: list[ViewOverlap]
    family_of_view: dict[str, str]
    documents_considered: int = 0

    @property
    def overlap_document_count(self) -> int:
        return len(self.overlaps)

    @property
    def cross_family_overlaps(self) -> list[ViewOverlap]:
        return [o for o in self.overlaps if o.is_cross_family]

    def total_assigned_documents(self) -> int:
        """Documents counted across all views, repeats included."""
        return sum(len(ids) for ids in self.doc_ids_by_view.values())

    def distinct_documents(self) -> int:
        """Documents counted once, however many views selected them."""
        return len({d for ids in self.doc_ids_by_view.values() for d in ids})

    def view_count_exceeds_families(self) -> bool:
        """True when several views draw on fewer distinct families than views."""
        return len(self.family_of_view) > len(set(self.family_of_view.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": str(self.policy),
            "documents_considered": self.documents_considered,
            "doc_ids_by_view": self.doc_ids_by_view,
            "bytes_by_view": self.bytes_by_view,
            "family_of_view": self.family_of_view,
            "overlap_document_count": self.overlap_document_count,
            "cross_family_overlap_count": len(self.cross_family_overlaps),
            "total_assigned_documents": self.total_assigned_documents(),
            "distinct_documents": self.distinct_documents(),
            "overlaps": [o.to_dict() for o in self.overlaps],
        }


class OverlappingViewError(ValueError):
    """Raised when views overlap and the configured policy forbids it."""


def resolve_view_membership(
    documents: Iterable[CanonicalDocument],
    views: list[SourceView],
    policy: OverlapPolicy = OverlapPolicy.EXCLUSIVE_ASSIGNMENT,
) -> ViewMembership:
    """Evaluate ``views`` over ``documents`` and apply the overlap policy.

    Under exclusive assignment, an overlapping document goes to the highest-priority
    view, breaking ties on ``view_id``. Assignment therefore depends only on the view
    definitions, never on the order they were declared or documents arrived.
    """
    if not views:
        raise ValueError("at least one source view is required to build a pool")

    duplicate_ids = [
        v for v in {view.view_id for view in views} if [x.view_id for x in views].count(v) > 1
    ]
    if duplicate_ids:
        raise ValueError(f"duplicate view_id values are ambiguous: {sorted(duplicate_ids)}")

    ordered_views = sorted(views, key=lambda v: (-v.priority, v.view_id))
    matched: dict[str, list[SourceView]] = {}
    byte_counts: dict[str, int] = {}
    considered = 0

    for doc in documents:
        considered += 1
        hits = [view for view in ordered_views if view.selector.matches(doc)]
        if not hits:
            continue
        matched[doc.doc_id] = hits
        byte_counts[doc.doc_id] = doc.utf8_byte_count

    doc_ids_by_view: dict[str, list[str]] = {view.view_id: [] for view in views}
    bytes_by_view: dict[str, int] = {view.view_id: 0 for view in views}
    overlaps: list[ViewOverlap] = []

    for doc_id in sorted(matched):
        hits = matched[doc_id]
        assigned: str | None = None

        if len(hits) > 1:
            if policy is OverlapPolicy.EXCLUSIVE_ASSIGNMENT:
                assigned = hits[0].view_id
            overlaps.append(
                ViewOverlap(
                    doc_id=doc_id,
                    view_ids=sorted(v.view_id for v in hits),
                    family_ids=sorted({v.family_id for v in hits}),
                    assigned_view_id=assigned,
                    utf8_byte_count=byte_counts[doc_id],
                )
            )

        targets = [hits[0]] if (len(hits) > 1 and assigned is not None) else hits
        for view in targets:
            doc_ids_by_view[view.view_id].append(doc_id)
            bytes_by_view[view.view_id] += byte_counts[doc_id]

    return ViewMembership(
        policy=policy,
        doc_ids_by_view={k: sorted(v) for k, v in doc_ids_by_view.items()},
        bytes_by_view=bytes_by_view,
        overlaps=overlaps,
        family_of_view={view.view_id: view.family_id for view in views},
        documents_considered=considered,
    )


def independent_family_count(membership: ViewMembership) -> int:
    """Count distinct source families, so overlapping views are not double-counted.

    Two views over the same family are one source of text, not two. Reporting the
    view count as a source count would overstate corpus independence.
    """
    return len(set(membership.family_of_view.values()))


@dataclass
class OverlapAudit:
    """A summary of how much independence the view set actually provides."""

    view_count: int
    family_count: int
    distinct_documents: int
    repeated_assignments: int
    cross_family_overlap_count: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def audit_overlap(membership: ViewMembership) -> OverlapAudit:
    """Report view-versus-family independence and any repeated sampling weight."""
    repeated = membership.total_assigned_documents() - membership.distinct_documents()
    warnings: list[str] = []

    if membership.view_count_exceeds_families():
        warnings.append(
            f"{len(membership.family_of_view)} views draw on only "
            f"{independent_family_count(membership)} distinct source families; "
            "views are not independent sources."
        )
    if repeated > 0:
        warnings.append(
            f"{repeated} document assignment(s) are repeats under the "
            f"'{membership.policy}' policy; those documents carry extra sampling weight."
        )
    if membership.cross_family_overlaps:
        warnings.append(
            f"{len(membership.cross_family_overlaps)} document(s) appear in more than one "
            "source family; upstream provenance overlap is not independent text."
        )

    return OverlapAudit(
        view_count=len(membership.family_of_view),
        family_count=independent_family_count(membership),
        distinct_documents=membership.distinct_documents(),
        repeated_assignments=repeated,
        cross_family_overlap_count=len(membership.cross_family_overlaps),
        warnings=warnings,
    )
