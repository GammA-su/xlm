"""Acceptance tests for source views, families and overlap treatment.

Covers the P11 requirements that views are deterministic selectors over actual source
fields, that overlapping membership has an explicit treatment policy, and that
overlapping views are not counted as independent sources.
"""

from __future__ import annotations

from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.pools import (
    OverlapPolicy,
    SourceView,
    ViewSelector,
    audit_overlap,
    independent_family_count,
    resolve_view_membership,
)


def make_doc(
    doc_id: str,
    text: str = "A canonical document body with enough words to be realistic.",
    source_id: str = "src_a",
    split: str = "train",
    kind: str = "prose",
    language: str = "en",
    metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision="rev_1",
        source_file="shard.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language=language,
        language_confidence=1.0,
        document_kind=kind,
        source_metadata=metadata or {},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split=split,
    )


def test_selector_matches_on_actual_record_fields() -> None:
    """A view is a selector over fields the adapter really recorded."""
    selector = ViewSelector(source_ids=["src_a"], document_kinds=["code"], splits=["train"])
    assert selector.matches(make_doc("a", source_id="src_a", kind="code", split="train"))
    assert not selector.matches(make_doc("b", source_id="src_b", kind="code"))
    assert not selector.matches(make_doc("c", source_id="src_a", kind="prose"))
    assert not selector.matches(make_doc("d", source_id="src_a", kind="code", split="audit"))


def test_selector_matches_metadata_and_byte_window() -> None:
    selector = ViewSelector(metadata_equals={"domain": "science"}, min_utf8_bytes=10)
    assert selector.matches(make_doc("a", metadata={"domain": "science"}))
    assert not selector.matches(make_doc("b", metadata={"domain": "sports"}))
    assert not selector.matches(make_doc("c", metadata={}))
    assert not selector.matches(make_doc("d", text="tiny", metadata={"domain": "science"}))


def test_empty_selector_is_unconstrained() -> None:
    """An empty selector selects everything, rather than silently selecting nothing."""
    assert ViewSelector().matches(make_doc("anything"))


def test_invalid_byte_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="below"):
        ViewSelector(min_utf8_bytes=100, max_utf8_bytes=10)


def test_selector_identity_changes_with_criteria() -> None:
    """A changed selector must be visible in downstream artifact identity."""
    base = ViewSelector(source_ids=["src_a"])
    assert base.identity() == ViewSelector(source_ids=["src_a"]).identity()
    assert base.identity() != ViewSelector(source_ids=["src_b"]).identity()
    assert base.identity() != ViewSelector(source_ids=["src_a"], languages=["fr"]).identity()


def test_exclusive_assignment_conserves_sampling_weight() -> None:
    """An overlapping document is assigned once, so it gains no extra weight."""
    docs = [make_doc(f"d{i}", kind="code", metadata={"domain": "science"}) for i in range(4)]
    views = [
        SourceView(
            view_id="by_kind",
            family_id="fam_a",
            selector=ViewSelector(document_kinds=["code"]),
            priority=10,
        ),
        SourceView(
            view_id="by_domain",
            family_id="fam_b",
            selector=ViewSelector(metadata_equals={"domain": "science"}),
            priority=1,
        ),
    ]
    membership = resolve_view_membership(docs, views, OverlapPolicy.EXCLUSIVE_ASSIGNMENT)

    assert membership.overlap_document_count == 4
    assert membership.total_assigned_documents() == 4, "exclusive assignment must not duplicate"
    assert membership.distinct_documents() == 4
    # Higher priority wins.
    assert sorted(membership.doc_ids_by_view["by_kind"]) == [f"d{i}" for i in range(4)]
    assert membership.doc_ids_by_view["by_domain"] == []
    assert all(o.assigned_view_id == "by_kind" for o in membership.overlaps)


def test_multi_view_policy_keeps_and_reports_repeated_weight() -> None:
    """Multi-view treatment is allowed, but the repeated exposure is made visible."""
    docs = [make_doc(f"d{i}", kind="code", metadata={"domain": "science"}) for i in range(3)]
    views = [
        SourceView(
            view_id="by_kind", family_id="fam_a", selector=ViewSelector(document_kinds=["code"])
        ),
        SourceView(
            view_id="by_domain",
            family_id="fam_b",
            selector=ViewSelector(metadata_equals={"domain": "science"}),
        ),
    ]
    membership = resolve_view_membership(docs, views, OverlapPolicy.MULTI_VIEW)

    assert membership.total_assigned_documents() == 6
    assert membership.distinct_documents() == 3
    assert all(o.assigned_view_id is None for o in membership.overlaps)

    audit = audit_overlap(membership)
    assert audit.repeated_assignments == 3
    assert any("extra sampling weight" in w for w in audit.warnings)


def test_assignment_does_not_depend_on_view_declaration_order() -> None:
    """Priority and view_id decide assignment, never the order views were listed."""
    docs = [make_doc(f"d{i}", kind="code", metadata={"domain": "science"}) for i in range(3)]
    high = SourceView(
        view_id="alpha",
        family_id="fam_a",
        selector=ViewSelector(document_kinds=["code"]),
        priority=5,
    )
    low = SourceView(
        view_id="beta",
        family_id="fam_b",
        selector=ViewSelector(metadata_equals={"domain": "science"}),
        priority=1,
    )
    forward = resolve_view_membership(docs, [high, low])
    reversed_order = resolve_view_membership(docs, [low, high])
    assert forward.doc_ids_by_view == reversed_order.doc_ids_by_view


def test_equal_priority_breaks_ties_on_view_id() -> None:
    """A deterministic tie-break keeps assignment reproducible."""
    docs = [make_doc("d0", kind="code", metadata={"domain": "science"})]
    views = [
        SourceView(view_id="zulu", family_id="f", selector=ViewSelector(document_kinds=["code"])),
        SourceView(
            view_id="alpha",
            family_id="f",
            selector=ViewSelector(metadata_equals={"domain": "science"}),
        ),
    ]
    membership = resolve_view_membership(docs, views)
    assert membership.doc_ids_by_view["alpha"] == ["d0"]
    assert membership.doc_ids_by_view["zulu"] == []


def test_overlapping_views_are_not_counted_as_independent_sources() -> None:
    """Two views over one family are one source of text, not two."""
    docs = [make_doc(f"d{i}", source_id="wiki") for i in range(5)]
    views = [
        SourceView(
            view_id="wiki_short", family_id="wikipedia", selector=ViewSelector(max_utf8_bytes=1000)
        ),
        SourceView(
            view_id="wiki_en", family_id="wikipedia", selector=ViewSelector(languages=["en"])
        ),
    ]
    membership = resolve_view_membership(docs, views)

    assert independent_family_count(membership) == 1
    audit = audit_overlap(membership)
    assert audit.view_count == 2
    assert audit.family_count == 1
    assert any("not independent sources" in w for w in audit.warnings)


def test_cross_family_overlap_is_flagged_as_provenance_overlap() -> None:
    """A document appearing in two families is an upstream provenance overlap."""
    docs = [make_doc("shared", source_id="mirror", kind="prose")]
    views = [
        SourceView(
            view_id="crawl", family_id="web_crawl", selector=ViewSelector(document_kinds=["prose"])
        ),
        SourceView(
            view_id="books", family_id="book_corpus", selector=ViewSelector(source_ids=["mirror"])
        ),
    ]
    membership = resolve_view_membership(docs, views, OverlapPolicy.MULTI_VIEW)

    assert len(membership.cross_family_overlaps) == 1
    assert membership.cross_family_overlaps[0].is_cross_family
    audit = audit_overlap(membership)
    assert any("more than one" in w and "family" in w for w in audit.warnings)


def test_duplicate_view_ids_are_rejected() -> None:
    views = [
        SourceView(view_id="same", family_id="a", selector=ViewSelector()),
        SourceView(view_id="same", family_id="b", selector=ViewSelector()),
    ]
    with pytest.raises(ValueError, match="duplicate view_id"):
        resolve_view_membership([make_doc("d")], views)


def test_empty_view_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one source view"):
        resolve_view_membership([make_doc("d")], [])


def test_non_overlapping_views_produce_no_warnings() -> None:
    """A clean view set must not emit spurious independence warnings."""
    docs = [
        make_doc("p1", kind="prose", source_id="wiki"),
        make_doc("c1", kind="code", source_id="gh"),
    ]
    views = [
        SourceView(
            view_id="prose", family_id="wikipedia", selector=ViewSelector(source_ids=["wiki"])
        ),
        SourceView(view_id="code", family_id="github", selector=ViewSelector(source_ids=["gh"])),
    ]
    audit = audit_overlap(resolve_view_membership(docs, views))
    assert audit.warnings == []
    assert audit.family_count == 2
    assert audit.repeated_assignments == 0
