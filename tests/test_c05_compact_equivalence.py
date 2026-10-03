"""Compact parallel C05 engine == accepted SQLite reference engine (authored fixtures).

Scientific equivalence is byte-level: membership, private decisions, completion
scientific fields, every per-file facts digest, the group digest and every stored
fact (MinHash signature, band keys, exact hash, hit, lineage keys, parents).
"""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from c05_compact_support import (
    PROMPT,
    assert_equivalent,
    execute_with,
    mixed_corpus,
    near_cluster_corpus,
    outcome,
    setup_files,
    words,
)
from test_c05_engine import document, small_resources
from xlm.data.exclusion.policy import ProductionPolicy, ReviewPolicy


def both(
    tmp_path: Path,
    files: list[list[Any]],
    *,
    policy: ProductionPolicy | None = None,
    workers: tuple[int, ...] = (1,),
    **resources: Any,
) -> list[dict[str, Any]]:
    limits = small_resources(**resources)
    plan, index, receipt = setup_files(
        tmp_path / "reference", files, resources=limits, policy=policy
    )
    execute_with("reference", plan, index, receipt)
    expected = outcome(plan, "reference")
    results = []
    for count in workers:
        plan, index, receipt = setup_files(
            tmp_path / f"compact-{count}",
            files,
            resources=small_resources(**{**resources, "workers": count}),
            policy=policy,
        )
        execute_with("compact", plan, index, receipt)
        actual = outcome(plan, "compact")
        assert_equivalent(expected, actual)
        results.append(actual)
    return results


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_mixed_corpus_matches_reference(tmp_path: Path, seed: int) -> None:
    both(tmp_path, mixed_corpus(seed), workers=(1,))


def test_worker_counts_are_byte_identical(tmp_path: Path) -> None:
    results = both(tmp_path, mixed_corpus(7, files=5, per_file=20), workers=(1, 2, 4))
    first = results[0]
    for other in results[1:]:
        assert other["membership_sha256"] == first["membership_sha256"]
        assert other["facts"] == first["facts"]
        assert other["group_digest"] == first["group_digest"]


@pytest.mark.parametrize(
    "max_bucket,cap,seed",
    [(2, 1, 11), (3, 2, 12), (4, 3, 13), (8, 2, 14), (3, 64, 15), (256, 1, 16)],
)
def test_near_candidate_cap_and_oversized_buckets(
    tmp_path: Path, max_bucket: int, cap: int, seed: int
) -> None:
    policy = ProductionPolicy(
        max_bucket_size=max_bucket,
        max_candidates_per_document=cap,
        near_threshold=0.5,
        diagnostic_bytes=0,
        quick_bytes=0,
        audit_bytes=0,
    )
    result = both(tmp_path, near_cluster_corpus(seed), policy=policy)[0]
    stats = result["completion"]["dedup_stats"]
    assert stats["comparisons"] > 0


def test_bucket_exactly_at_and_above_the_maximum(tmp_path: Path) -> None:
    text = " ".join(f"authoredword{i}" for i in range(60))
    for size, oversized in ((3, False), (4, True)):
        policy = ProductionPolicy(
            max_bucket_size=3, diagnostic_bytes=0, quick_bytes=0, audit_bytes=0
        )
        docs = [[document(f"b{i}", text, source="z")] for i in range(size)]
        result = both(tmp_path / str(size), docs, policy=policy)[0]
        assert (result["completion"]["dedup_stats"]["oversized_bands"] > 0) == oversized


def test_jaccard_exactly_at_and_one_step_below_threshold(tmp_path: Path) -> None:
    from xlm.data.dedup.minhash import MinHasher, estimated_jaccard

    text = " ".join(f"authoredword{i}" for i in range(60))
    longer = text + " additional content"
    similarity = estimated_jaccard(MinHasher().signature(text), MinHasher().signature(longer))
    for name, threshold in (
        ("equal", similarity),
        ("below", similarity - 1 / 128),
        ("above", similarity + 1 / 128),
    ):
        policy = ProductionPolicy(
            near_threshold=threshold, diagnostic_bytes=0, quick_bytes=0, audit_bytes=0
        )
        docs = [[document("a", text)], [document("b", longer)]]
        result = both(tmp_path / name, docs, policy=policy)[0]
        assert result["completion"]["duplicates"] == (0 if name == "above" else 1)


def test_identical_signatures_and_candidates_on_both_sides(tmp_path: Path) -> None:
    text = words(random.Random(5), 80)
    ids = ["m", "a", "z", "k", "b", "y"]
    docs = [[document(i, text, source="s")] for i in ids]
    both(tmp_path, docs, policy=ProductionPolicy(max_candidates_per_document=2))


def test_survivor_ties_bytes_source_and_id(tmp_path: Path) -> None:
    text = " ".join(f"tieword{i}" for i in range(40))
    docs = [
        [document("t3", text, source="beta"), document("t1", text, source="beta")],
        [document("t2", text, source="alpha")],
        [document("t0", text + " longer", source="zeta")],
        [document("t4", text, source="alpha")],
    ]
    both(tmp_path, docs, policy=ProductionPolicy(near_threshold=0.5))


def test_splits_quick_and_audit_allocation(tmp_path: Path) -> None:
    policy = ProductionPolicy(diagnostic_bytes=4096, quick_bytes=1024, audit_bytes=4096)
    both(tmp_path, mixed_corpus(21, files=4, per_file=30), policy=policy)


def test_lineage_parents_and_url_variants(tmp_path: Path) -> None:
    parent = document("parent", PROMPT, source="synth", book_id="book-1")
    child = replace(
        document("child", "Attached child text.", source="other"), parent_ids=["parent"]
    )
    url_a = document("u1", "First variant.", url="https://www.example.invalid/x?utm_a=1")
    url_b = document("u2", "Second variant.", url="http://example.invalid/x/")
    orphan = replace(document("orphan", "No parent here."), parent_ids=["ghost", ""])
    both(tmp_path, [[parent], [url_a, orphan], [child], [url_b]])


def test_gutenberg_and_upstream_allocations(tmp_path: Path) -> None:
    docs = [
        [document("g1", "Chapter one text.", source="cp", book_id="b1")],
        [document("g2", "Chapter two text.", source="cp", book_id="b1")],
    ]
    plan_policy = ProductionPolicy(gutenberg="require_book_ids")
    limits = small_resources()
    reference_plan, index, receipt = setup_files(
        tmp_path / "ref",
        docs,
        resources=limits,
        policy=plan_policy,
        upstream={0: "project_gutenberg"},
    )
    execute_with("reference", reference_plan, index, receipt)
    compact_plan, cindex, creceipt = setup_files(
        tmp_path / "cmp",
        docs,
        resources=limits,
        policy=plan_policy,
        upstream={0: "project_gutenberg"},
    )
    execute_with("compact", compact_plan, cindex, creceipt)
    assert_equivalent(outcome(reference_plan, "reference"), outcome(compact_plan, "compact"))


def test_review_enabled_queue_matches_reference(tmp_path: Path) -> None:
    policy = ProductionPolicy(
        diagnostic_bytes=0,
        quick_bytes=0,
        audit_bytes=0,
        review=ReviewPolicy(
            enabled=True,
            min_tokens=5,
            min_distinct=3,
            candidates_per_document=1,
            candidates_per_benchmark=2,
        ),
    )
    docs = [
        [document(str(n), "Why do silver bridges expand during summer?") for n in range(3)],
        [document(str(n), "Why do silver bridges expand during summer?") for n in range(3, 6)],
    ]
    for workers in (1, 2):
        result = both(
            tmp_path / str(workers), docs, policy=policy, workers=(workers,), review_candidates=3
        )[0]
        assert result["completion"]["review"]["candidates"] == 2


def test_empty_short_and_unicode_documents(tmp_path: Path) -> None:
    docs = [
        [document("e1", ""), document("e2", ""), document("s1", "one"), document("s2", "one")],
        [document("ü-1", "Straße façade naïve 東京 ﬁ"), document("ü-0", "strasse facade naive")],
        [],
    ]
    both(tmp_path, docs, workers=(1, 2))
