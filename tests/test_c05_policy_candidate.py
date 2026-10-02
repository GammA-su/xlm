"""Authored informativeness controls; these do not estimate population precision."""

from __future__ import annotations

import pytest

from xlm.data.dedup.matchview import match_tokens
from xlm.data.exclusion.policy import MatcherPolicy
from xlm.data.exclusion.streaming import StreamingMatcher, patterns, render


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "no",
        "A B C D E F G H",
        "1234 2345 3456 4567 5678",
        "Water is wet",
        "Force equals mass",
        "one one one one one one one one",
    ],
)
@pytest.mark.parametrize("kind", ["prompt", "answer", "combined"])
def test_uninformative_controls_never_compile(text: str, kind: str) -> None:
    assert list(patterns([(kind, text)], "authored", MatcherPolicy())) == []


@pytest.mark.parametrize(
    "kind,text",
    [
        ("prompt", "Which mineral scratches quartz?"),
        ("prompt", "Secure the wobbling shelf"),
        ("sentence", "Several wrens chirped"),
        ("sentence", "Several wrens chirps"),
        ("answer", "Apply gentle pressure while turning the threaded collar clockwise"),
    ],
)
def test_candidate_short_informative_signatures(kind: str, text: str) -> None:
    entries = list(patterns([(kind, text)], "authored", MatcherPolicy()))
    assert entries
    matcher = StreamingMatcher(entries, max_patterns=100, max_nodes=1000)
    for _ in range(100):
        assert matcher.match(match_tokens("Preface: " + text.upper() + ". Appendix."))


def test_common_grammatical_sentence_needs_frozen_background_decision() -> None:
    phrase = "The cat is sleeping"
    # Length alone cannot distinguish a benchmark sentence from ordinary prose.
    assert list(patterns([("sentence", phrase)], "authored", MatcherPolicy()))
    policy = MatcherPolicy(background=(phrase,))
    assert not list(patterns([("sentence", phrase)], "authored", policy))
    assert policy.identity() != MatcherPolicy().identity()


def test_hellaswag_matches_pinned_harness_preprocessing_and_empty_suffix() -> None:
    row = {
        "ctx_a": "A fictional sailor [aside] repairs the striped sail.",
        "ctx_b": "",
        "activity_label": "Repairing sails",
        "endings": ["yes", "no", "A", "B"],
    }
    variants = render("hellaswag", row)
    assert ("prompt", "A fictional sailor repairs the striped sail.") in variants
    matcher = StreamingMatcher(
        patterns(variants, "authored", MatcherPolicy()), max_patterns=1000, max_nodes=10000
    )
    assert matcher.match(match_tokens("A fictional sailor repairs the striped sail."))


def test_repetition_and_partition_permutation_leave_index_immutable() -> None:
    entries = list(
        patterns([("prompt", "Which mineral scratches quartz?")], "one", MatcherPolicy())
    )
    entries += list(
        patterns([("prompt", "Which mineral scratches quartz?")], "two", MatcherPolicy())
    )
    scan = StreamingMatcher(entries, max_patterns=20, max_nodes=100)
    identity, provenance = scan.identity, dict(scan.provenance)
    texts = ["Which mineral scratches quartz?" for _ in range(100)] + ["Unrelated brief prose"]
    for width in (1, 4, 17, 101):
        found = 0
        for start in reversed(range(0, len(texts), width)):
            found += sum(
                scan.match(match_tokens(t)) is not None
                for t in reversed(texts[start : start + width])
            )
        assert found == 100
    assert scan.identity == identity and dict(scan.provenance) == provenance
