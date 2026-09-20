"""Acceptance tests for lineage grouping of known document families.

C05 requires source document and page IDs, book chapters, conversation IDs, URL
variants and synthetic examples sharing a seed to stay in one lineage group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup import DeduplicationEngine, canonical_url, lineage_key
from xlm.data.normalization import compute_sha256


def make_doc(
    doc_id: str,
    text: str,
    source_id: str = "src",
    metadata: dict[str, Any] | None = None,
    parent_ids: list[str] | None = None,
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
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata=metadata or {},
        parent_ids=parent_ids or [],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_synthetic_examples_sharing_a_seed_group_together() -> None:
    """Shared-seed synthetic examples belong to one lineage group (C05)."""
    left = make_doc(
        "syn_a", "Question one generated from the seed.", "synth", {"synthetic_seed": "seed_42"}
    )
    right = make_doc(
        "syn_b", "Question two generated from the seed.", "synth", {"synthetic_seed": "seed_42"}
    )
    other = make_doc(
        "syn_c", "Question three from a different seed.", "synth", {"synthetic_seed": "seed_99"}
    )

    assert lineage_key(left) == lineage_key(right)
    assert lineage_key(left)[0] == "synthetic_seed"
    assert lineage_key(left) != lineage_key(other)


def test_book_chapters_and_conversation_turns_group_together() -> None:
    """Chapters of one book, and turns of one conversation, share a lineage key."""
    chapters = [
        make_doc(f"ch_{i}", f"Chapter {i} text.", "books", {"book_id": "vol_1"}) for i in range(3)
    ]
    assert len({lineage_key(c) for c in chapters}) == 1
    assert lineage_key(chapters[0])[0] == "book"

    turns = [
        make_doc(f"turn_{i}", f"Turn {i}.", "chat", {"conversation_id": "conv_7"}) for i in range(4)
    ]
    assert len({lineage_key(t) for t in turns}) == 1
    assert lineage_key(turns[0])[0] == "conversation"


def test_url_variants_collapse_to_one_lineage_key() -> None:
    """http/https, www, tracking parameters and trailing slashes are the same article."""
    variants = [
        "https://www.example.com/articles/transformers",
        "http://example.com/articles/transformers/",
        "https://example.com/articles/transformers?utm_source=newsletter&utm_medium=email",
        "https://www.example.com/articles/transformers#section-2",
    ]
    keys = {canonical_url(v) for v in variants}
    assert len(keys) == 1, f"URL variants did not collapse: {keys}"

    # A genuinely different article must not collapse into the same key.
    assert canonical_url("https://example.com/articles/attention") not in keys

    # A meaningful query parameter is preserved, since it selects different content.
    assert canonical_url("https://example.com/view?id=7") != canonical_url(
        "https://example.com/view?id=8"
    )

    docs = [make_doc(f"u{i}", f"Body {i}", "web", {"url": v}) for i, v in enumerate(variants)]
    assert len({lineage_key(d) for d in docs}) == 1


def test_explicit_lineage_id_takes_priority() -> None:
    """An explicit lineage_id overrides derived keys."""
    doc = make_doc(
        "explicit",
        "Body",
        "src",
        {"lineage_id": "family_1", "url": "https://example.com/a", "book_id": "vol_1"},
    )
    rule, key = lineage_key(doc)
    assert rule == "explicit"
    assert "family_1" in key


def test_parent_ids_are_used_before_falling_back_to_singleton() -> None:
    """Explicit parent links are a structured lineage signal."""
    child = make_doc("child", "Derived body", "src", parent_ids=["parent_doc"])
    rule, key = lineage_key(child)
    assert rule == "parent"
    assert "parent_doc" in key

    orphan = make_doc("orphan", "Standalone body", "src")
    assert lineage_key(orphan)[0] == "singleton"


def test_lineage_families_are_reported_but_never_deduplicated_away(tmp_path: Path) -> None:
    """Family membership must not cause distinct chapters to be dropped as duplicates.

    Chapters are related, not identical. Treating lineage as duplication would silently
    delete most of a book.
    """
    chapter_texts = [
        "The harbour master recorded every vessel that entered before the autumn storms arrived.",
        "Ledgers from the guild describe apprenticeship terms in precise detail.",
        "A cartographer redrew the northern coastline after the survey expedition returned home.",
        "Correspondence between the two families reveals a quarrel over inherited orchard land.",
    ]
    chapters = [
        make_doc(f"ch_{i}", text, "books", {"book_id": "vol_1"})
        for i, text in enumerate(chapter_texts)
    ]
    result = DeduplicationEngine().run(chapters, tmp_path / "work")

    assert result.clusters == [], "lineage family was wrongly collapsed as duplicates"
    assert result.survivor_doc_ids == ["ch_0", "ch_1", "ch_2", "ch_3"]
    assert result.dropped_doc_ids == []
    # The family is still recorded, for split-safe co-assignment downstream.
    assert list(result.lineage_groups.values()) == [["ch_0", "ch_1", "ch_2", "ch_3"]]
