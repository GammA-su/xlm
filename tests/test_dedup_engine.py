"""Acceptance tests for cross-source deduplication and deterministic clustering.

Covers C05: exact and near duplicates across source families, deterministic survivor
selection independent of how work was sharded, bounded candidate verification, and
a disk-backed index that does not hold the corpus in RAM.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup import (
    DedupConfig,
    DeduplicationEngine,
    DocumentFacts,
    MinHashConfig,
    MinHasher,
    PartitionedKeyIndex,
    compute_cluster_id,
    estimated_jaccard,
    iter_surviving_documents,
    match_normalize,
    partition_for,
    select_survivor,
    shingles,
    stable_hash64,
)
from xlm.data.normalization import compute_sha256

SENTENCES = [
    "The transformer architecture uses self attention to relate every position in the sequence.",
    "Multi head attention projects queries keys and values into several independent subspaces.",
    "Residual connections and layer normalization keep gradients stable through deep stacks.",
    "Positional information must be injected because attention itself is permutation invariant.",
    "Feed forward blocks apply the same transformation independently at each sequence position.",
    "Training uses cross entropy over next token targets with a cosine learning rate decay.",
    "Checkpoints store optimizer slots and sampler cursors so that a run can resume exactly.",
]
LONG_TEXT = " ".join(SENTENCES)


def make_doc(
    doc_id: str,
    text: str,
    source_id: str = "src_default",
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


def test_exact_duplicates_are_detected_across_source_families(tmp_path: Path) -> None:
    """Identical text from different sources must collapse into one cluster (C05)."""
    shared = (
        "Photosynthesis converts light energy into chemical energy stored as glucose molecules."
    )
    docs = [
        make_doc("enc_a", shared, "encyclopedia"),
        make_doc("web_b", shared, "web_crawl"),
        make_doc("book_c", shared, "books"),
        make_doc(
            "other",
            "Sourdough starters need consistent feeding and a warm stable environment.",
            "web_crawl",
        ),
    ]
    result = DeduplicationEngine().run(docs, tmp_path / "work")

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.member_doc_ids == ["book_c", "enc_a", "web_b"]
    assert "exact" in cluster.match_kinds
    # Every contributing source is retained as an alias on the survivor.
    assert cluster.source_aliases == ["books", "encyclopedia", "web_crawl"]
    assert result.dropped_doc_ids == sorted(cluster.dropped_doc_ids)
    assert "other" in result.survivor_doc_ids


def test_exact_match_ignores_case_and_punctuation_only_differences(tmp_path: Path) -> None:
    """The match view collapses cosmetic differences that are not real content."""
    base = "Gradient descent follows the negative gradient of the loss function."
    docs = [
        make_doc("plain", base, "src_a"),
        make_doc("shouted", base.upper(), "src_b"),
        make_doc("punctuated", base.replace(".", " ... "), "src_c"),
    ]
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assert len(result.clusters) == 1
    assert result.clusters[0].size == 3


def test_near_duplicates_are_detected_and_unrelated_text_is_not(tmp_path: Path) -> None:
    """Boilerplate-prefixed and lightly edited copies cluster; unrelated text does not."""
    docs = [
        make_doc("original", LONG_TEXT, "src_a"),
        make_doc("with_header", "Published on the engineering blog. " + LONG_TEXT, "src_b"),
        make_doc("one_word_changed", LONG_TEXT.replace("cosine", "linear"), "src_c"),
        make_doc(
            "unrelated",
            "Sourdough bread needs a mature starter, a long fermentation and a very hot oven.",
            "src_d",
        ),
    ]
    result = DeduplicationEngine().run(docs, tmp_path / "work")

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.member_doc_ids == ["one_word_changed", "original", "with_header"]
    assert "near" in cluster.match_kinds
    assert "unrelated" in result.survivor_doc_ids
    assert result.stats.near_duplicate_pairs_confirmed >= 2


def test_documents_below_the_frozen_threshold_are_not_merged(tmp_path: Path) -> None:
    """A frozen threshold must actually hold: related-but-distinct text stays separate."""
    partial = (
        " ".join(SENTENCES[:4]) + " An entirely different closing discussion appears here instead."
    )
    docs = [make_doc("full", LONG_TEXT, "src_a"), make_doc("partial", partial, "src_b")]

    hasher = MinHasher()
    similarity = estimated_jaccard(hasher.signature(LONG_TEXT), hasher.signature(partial))
    assert similarity < MinHashConfig().jaccard_threshold

    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assert result.clusters == []


@pytest.mark.parametrize("partition_count", [1, 3, 8, 32])
def test_clusters_and_survivors_are_identical_across_worker_counts(
    tmp_path: Path, partition_count: int
) -> None:
    """Sharding is an IO decision: it must not change clusters, IDs or survivors.

    Partition count stands in for worker count here -- it is what actually changes how
    the index is split and the order in which buckets are read back.
    """
    docs = [
        make_doc(
            "a_long", LONG_TEXT + " Extra trailing sentence making this the longest.", "src_b"
        ),
        make_doc("b_short", LONG_TEXT, "src_a"),
        make_doc("c_header", "Blog header. " + LONG_TEXT, "src_c"),
        make_doc(
            "solo", "An unrelated paragraph about tidal patterns along the northern coast.", "src_d"
        ),
    ]
    reference = DeduplicationEngine(DedupConfig(partition_count=4)).run(
        list(docs), tmp_path / "reference"
    )

    shuffled = list(docs)
    random.Random(partition_count).shuffle(shuffled)
    candidate = DeduplicationEngine(DedupConfig(partition_count=partition_count)).run(
        shuffled, tmp_path / f"work_{partition_count}"
    )

    assert [c.to_dict() for c in candidate.clusters] == [c.to_dict() for c in reference.clusters]
    assert candidate.survivor_doc_ids == reference.survivor_doc_ids
    assert candidate.dropped_doc_ids == reference.dropped_doc_ids
    assert candidate.config_identity == reference.config_identity


def test_survivor_rule_is_frozen_and_content_determined() -> None:
    """Survivor selection follows byte count, then source_id, then doc_id."""
    facts = {
        "short": DocumentFacts("short", "src_a", "h1", 100, "lin", "singleton"),
        "long": DocumentFacts("long", "src_z", "h2", 500, "lin", "singleton"),
        "tie_b": DocumentFacts("tie_b", "src_b", "h3", 500, "lin", "singleton"),
    }
    # Largest byte count wins; src_b beats src_z on the tie-break.
    assert select_survivor(["short", "long", "tie_b"], facts) == "tie_b"
    # Order of the member iterable is irrelevant.
    assert select_survivor(["tie_b", "long", "short"], facts) == "tie_b"

    with pytest.raises(KeyError):
        select_survivor(["missing"], facts)
    with pytest.raises(ValueError):
        select_survivor([], facts)


def test_cluster_id_depends_only_on_membership() -> None:
    """Cluster IDs are order-independent and change when membership changes."""
    assert compute_cluster_id(["b", "a", "c"]) == compute_cluster_id(["a", "b", "c"])
    assert compute_cluster_id(["a", "a", "b"]) == compute_cluster_id(["a", "b"])
    assert compute_cluster_id(["a", "b"]) != compute_cluster_id(["a", "b", "c"])
    with pytest.raises(ValueError):
        compute_cluster_id([])


def test_partitioned_index_is_disk_backed_and_stable(tmp_path: Path) -> None:
    """The index must live on disk and route keys stably (C05 bounded index)."""
    index = PartitionedKeyIndex(tmp_path / "idx", partition_count=8)
    with index:
        for i in range(200):
            index.add(f"key_{i % 20}", f"doc_{i}")

    files = sorted((tmp_path / "idx").glob("part_*.bin"))
    assert files, "index wrote nothing to disk"
    assert sum(index.partition_sizes()) > 0
    assert index.entries_written == 200

    groups = dict(index.groups(min_size=2))
    assert len(groups) == 20
    for doc_ids in groups.values():
        assert doc_ids == sorted(doc_ids), "group membership must be sorted"

    # Key routing is stable across processes, so partitioning is reproducible.
    assert partition_for("key_3", 8) == partition_for("key_3", 8)
    assert all(0 <= partition_for(f"k{i}", 8) < 8 for i in range(50))

    with pytest.raises(RuntimeError):
        index.add("late", "doc")


def test_oversized_buckets_are_reported_not_expanded(tmp_path: Path) -> None:
    """A bucket above the cap must be counted, not expanded pairwise."""
    shared = "Identical boilerplate footer text repeated verbatim across very many pages."
    docs = [make_doc(f"boiler_{i}", shared, f"src_{i}") for i in range(12)]
    config = DedupConfig(max_bucket_size=4)
    result = DeduplicationEngine(config).run(docs, tmp_path / "work")

    assert result.stats.oversized_buckets > 0
    # Exact matching still catches them, so nothing is lost by refusing to expand.
    assert len(result.clusters) == 1
    assert result.clusters[0].size == 12
    assert result.stats.near_duplicate_pairs_confirmed == 0


def test_candidate_verification_is_budget_bounded(tmp_path: Path) -> None:
    """Per-document candidate budget caps verification work."""
    docs = [
        make_doc(f"v_{i}", LONG_TEXT + f" Variant number {i} of this document.", f"src_{i}")
        for i in range(10)
    ]
    config = DedupConfig(max_candidates_per_document=2)
    result = DeduplicationEngine(config).run(docs, tmp_path / "work")

    assert result.stats.candidate_pairs_skipped_budget > 0
    # 10 documents would be 45 all-pairs comparisons; the budget must hold it well below.
    assert result.stats.near_candidate_pairs_considered < 45


def test_signatures_are_only_retained_for_candidate_documents(tmp_path: Path) -> None:
    """Documents that share no LSH band must not have their signature loaded."""
    topics = [
        "Tidal patterns along the northern coast shift with lunar declination each fortnight.",
        "Sourdough fermentation depends on ambient temperature and flour protein content.",
        "Medieval guild records list apprenticeship terms in unusually precise detail.",
        "Volcanic ash layers provide chronological markers across widely separated basins.",
        "Bee colonies regulate hive temperature by coordinated wing fanning behaviour.",
        "Harbour dredging schedules balance silt accumulation against shipping demand.",
        "Cuneiform tablets record barley rations issued to temple workers by quantity.",
        "Alpine glaciers retreat at rates that vary sharply with slope aspect and debris cover.",
    ]
    docs = [make_doc(f"distinct_{i}", text, f"src_{i}") for i, text in enumerate(topics)]
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assert result.clusters == []
    # No document shares an LSH band with another, so no signature is ever loaded.
    assert result.stats.signatures_retained == 0
    assert result.stats.near_candidate_pairs_considered == 0


def test_shingling_and_hashing_are_deterministic() -> None:
    """Hashing must not use Python's per-process randomized hash (C02)."""
    assert stable_hash64("abc") == stable_hash64("abc")
    assert stable_hash64("abc") != stable_hash64("abd")
    assert shingles("one two three four five six", 5) == [
        "one two three four five",
        "two three four five six",
    ]
    # Short documents still yield a usable single shingle.
    assert shingles("only three words", 5) == ["only three words"]
    assert shingles("", 5) == []


def test_match_view_never_alters_canonical_text(tmp_path: Path) -> None:
    """The dedup match view is lossy; surviving canonical text must be untouched."""
    original = "def f(x):\n    return x**2  # keeps indentation, math and case"
    docs = [make_doc("code_doc", original, "src_a")]
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    survivors = list(iter_surviving_documents(docs, result))

    assert survivors[0].text == original
    assert match_normalize(original) != original


def test_surviving_documents_carry_cluster_and_alias_provenance(tmp_path: Path) -> None:
    """Dropping duplicates must not lose the provenance of the other sources."""
    shared = "A shared paragraph appearing verbatim in two independently collected corpora."
    docs = [make_doc("first", shared, "src_alpha"), make_doc("second", shared, "src_beta")]
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    survivors = list(iter_surviving_documents(docs, result))

    assert len(survivors) == 1
    survivor = survivors[0]
    assert survivor.cluster_ids["duplicate_cluster"] == result.clusters[0].cluster_id
    assert survivor.source_metadata["duplicate_source_aliases"] == ["src_alpha", "src_beta"]
    assert survivor.source_metadata["duplicate_cluster_size"] == 2


def test_dedup_identity_changes_with_behavioral_configuration() -> None:
    """A threshold or seed change must invalidate downstream artifacts."""
    base = DedupConfig()
    assert base.identity() == DedupConfig().identity()
    assert base.identity() != DedupConfig(minhash=MinHashConfig(jaccard_threshold=0.5)).identity()
    assert base.identity() != DedupConfig(minhash=MinHashConfig(seed=1)).identity()
    assert base.identity() != DedupConfig(enable_near_duplicates=False).identity()
    # Partition count is an IO choice and must NOT change behavioral identity.
    assert base.identity() == DedupConfig(partition_count=64).identity()


def test_minhash_banding_must_divide_permutations() -> None:
    """Uneven banding is rejected rather than silently producing unstable keys."""
    with pytest.raises(ValueError, match="must divide"):
        MinHashConfig(num_permutations=128, bands=7)


def test_build_clusters_applies_the_frozen_survivor_rule(tmp_path: Path) -> None:
    """Cluster construction must use the frozen rule, not a union-find artefact.

    Selecting the union-find root instead would still be deterministic, so a
    determinism test alone cannot catch it; this pins the actual rule.
    """
    short_text = LONG_TEXT
    long_text = LONG_TEXT + " A closing remark adds a little further detail here."

    # IDs chosen so the largest document is NOT the lexically smallest member.
    # Otherwise a survivor picked by union-find root would coincide with the frozen
    # rule by accident, and this test would not discriminate between them.
    docs = [
        make_doc("aa_truncated", short_text, "src_a"),
        make_doc("zz_complete", long_text, "src_b"),
    ]
    result = DeduplicationEngine().run(docs, tmp_path / "work")
    assert len(result.clusters) == 1

    cluster = result.clusters[0]
    byte_counts = {d.doc_id: d.utf8_byte_count for d in docs}
    assert min(cluster.member_doc_ids) == "aa_truncated", "fixture precondition"
    # The frozen rule keeps the most complete rendering, not the first member.
    assert cluster.survivor_doc_id == "zz_complete"
    assert byte_counts[cluster.survivor_doc_id] == max(byte_counts.values())
    assert cluster.dropped_doc_ids == ["aa_truncated"]

    # And the engine's own survivor list agrees with the rule.
    assert (
        select_survivor(
            cluster.member_doc_ids,
            {
                d.doc_id: DocumentFacts(
                    d.doc_id, d.source_id, d.clean_hash, d.utf8_byte_count, "lin", "singleton"
                )
                for d in docs
            },
        )
        == cluster.survivor_doc_id
    )
