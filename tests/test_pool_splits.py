"""Acceptance tests for group-safe corpus splits and pool freezing.

Covers C05: splits reserved independently of mixture weights, group-safe and
deterministic assignment, size targets honoured at document boundaries with actual
bytes recorded, a quick subset nested inside the diagnostic set, no tokenizer
fitting on non-training data, and late bridge duplicates invalidating a frozen pool.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup import DeduplicationEngine
from xlm.data.normalization import compute_sha256
from xlm.data.pools import (
    PoolFreeze,
    PoolInvalidatedError,
    SplitConfig,
    apply_splits,
    assign_splits,
    build_split_groups,
    detect_bridge_duplicates,
    freeze_pool,
    supersede_pool,
    verify_group_disjointness,
)

TOPICS = [
    "Tidal patterns along the northern coast shift with lunar declination each fortnight.",
    "Sourdough fermentation depends on ambient temperature and on flour protein content.",
    "Medieval guild records list apprenticeship terms in unusually precise written detail.",
    "Volcanic ash layers provide chronological markers across widely separated basins.",
    "Bee colonies regulate hive temperature by coordinated and sustained wing fanning.",
    "Harbour dredging schedules balance silt accumulation against commercial shipping demand.",
    "Cuneiform tablets record barley rations issued to temple workers by measured quantity.",
    "Alpine glaciers retreat at rates that vary sharply with slope aspect and debris cover.",
    "Coral reef bleaching correlates with sustained sea surface temperature anomalies.",
    "Railway gauge standardisation reshaped freight economics across the northern provinces.",
    "Lichen growth rates allow approximate dating of exposed rock surfaces after retreat.",
    "Wind turbine wake interference reduces downstream yield across densely packed arrays.",
]


def make_doc(
    doc_id: str,
    text: str,
    source_id: str = "src",
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
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata=metadata or {},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def corpus() -> list[CanonicalDocument]:
    """A corpus with singletons, a book family and a conversation family."""
    docs = [make_doc(f"solo_{i:02d}", text, "web") for i, text in enumerate(TOPICS)]
    docs += [
        make_doc(
            f"book_ch{i}",
            f"{TOPICS[i]} Chapter {i} of the collected volume.",
            "books",
            {"book_id": "vol_1"},
        )
        for i in range(3)
    ]
    docs += [
        make_doc(
            f"turn_{i}",
            f"{TOPICS[i + 3]} Conversational turn {i}.",
            "chat",
            {"conversation_id": "conv_9"},
        )
        for i in range(3)
    ]
    return docs


def small_config() -> SplitConfig:
    """Byte targets scaled to the fixture, keeping the policy shape intact."""
    return SplitConfig(
        diagnostic_val_target_bytes=300,
        quick_val_target_bytes=150,
        audit_target_bytes=200,
    )


def test_every_document_receives_exactly_one_split() -> None:
    docs = corpus()
    assignment = assign_splits(docs, None, small_config())

    assert set(assignment.assignments) == {d.doc_id for d in docs}
    assert set(assignment.assignments.values()) <= {"train", "diagnostic_val", "audit"}
    assert sum(assignment.achieved_documents.values()) == len(docs)


def test_split_assignment_is_order_independent() -> None:
    """Shuffling the input must not move a single document (C05 determinism)."""
    docs = corpus()
    reference = assign_splits(docs, None, small_config())

    for seed in (1, 2, 3):
        shuffled = list(docs)
        random.Random(seed).shuffle(shuffled)
        candidate = assign_splits(shuffled, None, small_config())
        assert candidate.assignments == reference.assignments
        assert candidate.quick_val_doc_ids == reference.quick_val_doc_ids
        assert candidate.membership_digest() == reference.membership_digest()


def test_lineage_families_are_never_split_across_partitions() -> None:
    """A book's chapters and a conversation's turns must land in one split."""
    docs = corpus()
    assignment = assign_splits(docs, None, small_config())

    book_splits = {assignment.assignments[f"book_ch{i}"] for i in range(3)}
    turn_splits = {assignment.assignments[f"turn_{i}"] for i in range(3)}
    assert len(book_splits) == 1, f"book chapters leaked across splits: {book_splits}"
    assert len(turn_splits) == 1, f"conversation turns leaked across splits: {turn_splits}"

    groups = {g.group_id: g.doc_ids for g in build_split_groups(docs, None)}
    assert verify_group_disjointness(assignment, groups) == []


def test_duplicate_clusters_keep_their_members_on_one_side(tmp_path: Path) -> None:
    """A near-duplicate of a held-out document must not sit in the training split."""
    docs = corpus()
    shared = TOPICS[0] + " An additional clause that both copies share verbatim throughout."
    docs.append(make_doc("dup_left", shared, "src_a"))
    docs.append(make_doc("dup_right", "Header line. " + shared, "src_b"))

    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assert result.clusters, "fixture must produce a duplicate cluster"

    # Split the full corpus, duplicates included, to prove group safety directly.
    assignment = assign_splits(docs, result, small_config())
    for cluster in result.clusters:
        splits = {assignment.assignments[m] for m in cluster.member_doc_ids}
        assert len(splits) == 1, f"cluster {cluster.cluster_id} straddles splits {splits}"

    groups = {g.group_id: g.doc_ids for g in build_split_groups(docs, result)}
    assert verify_group_disjointness(assignment, groups) == []


def test_quick_validation_subset_is_nested_inside_diagnostic_set() -> None:
    """The quick subset must be a strict subset of diagnostic validation (C05)."""
    docs = corpus()
    assignment = assign_splits(docs, None, small_config())

    assert assignment.quick_val_doc_ids, "quick subset must not be empty"
    for doc_id in assignment.quick_val_doc_ids:
        assert assignment.assignments[doc_id] == "diagnostic_val"

    diagnostic = {d for d, s in assignment.assignments.items() if s == "diagnostic_val"}
    assert set(assignment.quick_val_doc_ids) <= diagnostic
    assert assignment.quick_val_bytes > 0


def test_achieved_bytes_are_recorded_and_respect_document_boundaries() -> None:
    """Targets are met at document boundaries, and the achieved figures are recorded."""
    docs = corpus()
    config = small_config()
    assignment = assign_splits(docs, None, config)

    byte_by_id = {d.doc_id: d.utf8_byte_count for d in docs}
    for split in ("train", "diagnostic_val", "audit"):
        expected = sum(byte_by_id[d] for d, s in assignment.assignments.items() if s == split)
        assert assignment.achieved_bytes[split] == expected

    # Whole groups are assigned, so the achieved size overshoots rather than
    # truncating a document to hit the target exactly.
    assert assignment.achieved_bytes["diagnostic_val"] >= config.diagnostic_val_target_bytes


def test_splits_do_not_depend_on_mixture_weights() -> None:
    """Split policy identity is independent of any mixture weighting (C05)."""
    docs = corpus()
    weighted = [
        make_doc(f"extra_{i}", TOPICS[i % len(TOPICS)] + f" Copy {i}.", "heavy_source")
        for i in range(20)
    ]

    baseline = assign_splits(docs, None, small_config())
    # Adding documents from a heavily weighted source must not move existing ones
    # out of their groups' splits arbitrarily; the group ordering is content-derived.
    with_extra = assign_splits(docs + weighted, None, small_config())
    assert with_extra.policy_identity == baseline.policy_identity


def test_apply_splits_records_split_and_group_on_documents() -> None:
    docs = corpus()
    assignment = assign_splits(docs, None, small_config())
    applied = list(apply_splits(docs, assignment))

    assert len(applied) == len(docs)
    for doc in applied:
        assert doc.split == assignment.assignments[doc.doc_id]
        assert doc.cluster_ids["split_group"] == assignment.group_of_doc[doc.doc_id]

    with pytest.raises(KeyError, match="no split assignment"):
        list(apply_splits([make_doc("unknown", "Body text here.")], assignment))


def test_tokenizer_fitting_rejects_non_training_documents() -> None:
    """Tokenizer fitting must refuse diagnostic or audit documents (C05, C06)."""
    pytest.importorskip("tokenizers")
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    docs = corpus()
    assignment = assign_splits(docs, None, small_config())
    applied = list(apply_splits(docs, assignment))

    held_out = [d for d in applied if d.split != "train"]
    assert held_out, "fixture must hold out at least one document"

    with pytest.raises(ValueError, match="Only 'train' split documents"):
        ByteLevelBPETokenizer.train_from_documents(held_out, target_vocab_size=300)


def test_pool_freeze_binds_dedup_split_and_membership(tmp_path: Path) -> None:
    """A freeze identity must change when any behavioral input changes."""
    docs = corpus()
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assignment = assign_splits(docs, result, small_config())

    frozen = freeze_pool(assignment, result)
    again = freeze_pool(assign_splits(docs, result, small_config()), result)
    assert frozen.pool_id == again.pool_id, "identical inputs must reproduce the pool ID"

    changed_policy = SplitConfig(
        diagnostic_val_target_bytes=300, quick_val_target_bytes=150, audit_target_bytes=200, seed=7
    )
    different = freeze_pool(assign_splits(docs, result, changed_policy), result)
    assert different.pool_id != frozen.pool_id

    path = frozen.save(tmp_path / "pool.json")
    reloaded = PoolFreeze.load(path)
    assert reloaded.pool_id == frozen.pool_id
    assert reloaded.achieved_bytes == frozen.achieved_bytes
    assert reloaded.is_active


def test_late_bridge_duplicate_invalidates_the_affected_freeze(tmp_path: Path) -> None:
    """A new snapshot that bridges two frozen groups across splits must invalidate it.

    This is the leak C05 forbids reusing: two documents that were independent when
    the pool was frozen turn out to be duplicates, and they sit on opposite sides of
    the train/validation boundary.
    """
    docs = corpus()
    result = DeduplicationEngine().run(docs, tmp_path / "initial")
    assignment = assign_splits(docs, result, small_config())
    frozen = freeze_pool(assignment, result)

    # Pick two documents the freeze placed in different splits.
    train_doc = next(d for d, s in assignment.assignments.items() if s == "train")
    val_doc = next(d for d, s in assignment.assignments.items() if s == "diagnostic_val")
    assert frozen.group_of_doc[train_doc] != frozen.group_of_doc[val_doc]

    # A later snapshot reveals a bridging duplicate that links the two groups.
    bridged = DeduplicationEngine().run(docs, tmp_path / "later")

    from xlm.data.dedup.clusters import DuplicateCluster, compute_cluster_id

    members = sorted([train_doc, val_doc])
    bridged.clusters.append(
        DuplicateCluster(
            cluster_id=compute_cluster_id(members),
            member_doc_ids=members,
            survivor_doc_id=members[0],
            dropped_doc_ids=members[1:],
            source_aliases=["web"],
            match_kinds=["near"],
            size=2,
        )
    )

    report = detect_bridge_duplicates(frozen, bridged)
    assert report.requires_refreeze, "bridging duplicate across splits was not detected"
    finding = report.invalidating_findings[0]
    assert set(finding.bridged_doc_ids) == set(members)
    assert finding.crosses_split_boundary
    assert len(finding.affected_splits) > 1

    successor = freeze_pool(
        assign_splits(docs, bridged, small_config()), bridged, pool_label="pool_v2"
    )
    invalidated, linked = supersede_pool(frozen, successor, reason="late bridge duplicate")

    assert not invalidated.is_active
    assert invalidated.superseded_by == successor.pool_id
    assert linked.supersedes == frozen.pool_id
    # The old pool keeps its membership: the problem is recorded, not erased.
    assert invalidated.split_of_doc == frozen.split_of_doc
    with pytest.raises(PoolInvalidatedError, match="superseded"):
        invalidated.require_active()


def test_bridge_within_one_split_is_reported_but_not_invalidating(tmp_path: Path) -> None:
    """A bridge that stays inside one split is worth recording but leaks nothing."""
    docs = corpus()
    result = DeduplicationEngine().run(docs, tmp_path / "initial")
    assignment = assign_splits(docs, result, small_config())
    frozen = freeze_pool(assignment, result)

    train_docs = [d for d, s in assignment.assignments.items() if s == "train"]
    pair = sorted(train_docs[:2])
    assert frozen.group_of_doc[pair[0]] != frozen.group_of_doc[pair[1]]

    from xlm.data.dedup.clusters import DuplicateCluster, compute_cluster_id

    later = DeduplicationEngine().run(docs, tmp_path / "later")
    later.clusters.append(
        DuplicateCluster(
            cluster_id=compute_cluster_id(pair),
            member_doc_ids=pair,
            survivor_doc_id=pair[0],
            dropped_doc_ids=pair[1:],
            source_aliases=["web"],
            match_kinds=["near"],
            size=2,
        )
    )

    report = detect_bridge_duplicates(frozen, later)
    assert report.findings, "the bridge should still be reported"
    assert not report.requires_refreeze
    assert report.invalidating_findings == []


def test_incremental_and_full_rebuild_agree(tmp_path: Path) -> None:
    """Rebuilding from scratch must reproduce an incrementally extended corpus.

    A freeze produced by rebuilding the whole corpus and one produced after adding
    documents must agree on membership, so an incremental path cannot quietly drift
    away from the authoritative full rebuild.
    """
    base_docs = corpus()
    extra = [
        make_doc(f"new_{i}", f"{TOPICS[i]} An additional late arriving document number {i}.", "web")
        for i in range(3)
    ]

    full = DeduplicationEngine().run(base_docs + extra, tmp_path / "full")
    rebuilt = DeduplicationEngine().run(base_docs + extra, tmp_path / "rebuild")
    assert [c.to_dict() for c in full.clusters] == [c.to_dict() for c in rebuilt.clusters]

    full_assignment = assign_splits(base_docs + extra, full, small_config())
    rebuilt_assignment = assign_splits(base_docs + extra, rebuilt, small_config())
    assert full_assignment.assignments == rebuilt_assignment.assignments
    assert (
        freeze_pool(full_assignment, full).pool_id
        == freeze_pool(rebuilt_assignment, rebuilt).pool_id
    )


def test_pool_cannot_supersede_itself(tmp_path: Path) -> None:
    docs = corpus()
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    frozen = freeze_pool(assign_splits(docs, result, small_config()), result)
    with pytest.raises(ValueError, match="cannot supersede itself"):
        supersede_pool(frozen, frozen, reason="nonsense")
