"""Acceptance tests for development benchmark contamination matching.

Covers C05 and EVALUATION_POLICY: full-example and informative-span matching over
synthetic benchmark fixtures, protection of ordinary English that merely shares a
common phrase, and reports that carry no benchmark text or labels.

Every benchmark example here is authored synthetic fixture data. No real benchmark
split is read, and nothing in this file constitutes a sealed-final claim.
"""

from __future__ import annotations

import json

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.exclusion import (
    BenchmarkExample,
    BenchmarkExclusionMatcher,
    ExclusionConfig,
)
from xlm.data.normalization import compute_sha256

# Distinctive synthetic strings. If any of these appear in a report, the report leaked.
CANARY_PROMPT = (
    "A gardener planted seventeen tulip bulbs in a spiral pattern beside the old quarry wall."
)
CANARY_ANSWERS = [
    "They bloomed unusually early that April",
    "The badger dug up every single bulb",
    "Frost destroyed the entire spiral overnight",
]


def make_doc(doc_id: str, text: str, source_id: str = "web") -> CanonicalDocument:
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
        source_metadata={},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def synthetic_example() -> BenchmarkExample:
    return BenchmarkExample(
        example_id="synthetic_ex_1",
        task_id="synthetic_task",
        prompt=CANARY_PROMPT,
        candidate_answers=list(CANARY_ANSWERS),
    )


def test_full_example_contamination_is_detected() -> None:
    """A document reproducing the whole example is a contamination hit."""
    example = synthetic_example()
    docs = [
        make_doc("contaminated", example.rendered()),
        make_doc("clean", "Harbour dredging schedules balance silt against shipping demand."),
    ]
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])
    report = matcher.scan(docs)

    assert report.excluded_doc_ids == ["contaminated"]
    assert report.hits[0].match_kind == "full_example"
    assert report.hits[0].example_id == "synthetic_ex_1"


def test_informative_span_contamination_is_detected() -> None:
    """An embedded informative span is a hit even without the whole example."""
    example = synthetic_example()
    docs = [
        make_doc(
            "embedded",
            "Introductory background paragraph. " + CANARY_PROMPT + " Further discussion follows.",
        ),
        make_doc("clean", "Lichen growth rates allow approximate dating of exposed rock surfaces."),
    ]
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])
    report = matcher.scan(docs)

    assert report.excluded_doc_ids == ["embedded"]
    assert report.hits[0].match_kind == "informative_span"
    assert report.hits[0].span_token_length >= ExclusionConfig().span_length


def test_all_candidate_answers_are_indexed_not_just_the_gold_one() -> None:
    """Contamination means the answer-bearing text is present, whichever answer it is."""
    example = synthetic_example()
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])

    # A document carrying a non-first candidate answer in context is still a hit.
    doc = make_doc(
        "carries_distractor",
        "Report text. " + CANARY_PROMPT + " " + CANARY_ANSWERS[1] + " End of report.",
    )
    report = matcher.scan([doc])
    assert report.excluded_doc_ids == ["carries_distractor"]

    # The example object itself exposes no gold-label field to leak.
    assert not hasattr(example, "gold")
    assert not hasattr(example, "label")
    assert not hasattr(example, "answer_index")


def test_ordinary_english_sharing_a_common_phrase_is_not_excluded() -> None:
    """Common stock phrases must not delete ordinary corpus text (C05)."""
    # Long enough that a whole informative span fits inside the stock phrase itself.
    common = (
        "one of the most important and widely cited results in the entire field of modern study"
    )
    example = BenchmarkExample(
        example_id="common_ex",
        task_id="synthetic_task",
        prompt=f"{common} according to the published review.",
        candidate_answers=["Yes", "No"],
    )
    corpus = [
        make_doc(f"ordinary_{i}", f"Paper {i} argues the theorem is {common}, which many dispute.")
        for i in range(8)
    ]
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])
    report = matcher.scan(corpus)

    assert report.excluded_doc_ids == [], "common English was indiscriminately excluded"
    assert report.spans_suppressed_as_common > 0


def test_span_frequency_suppression_threshold_is_configurable() -> None:
    """The commonness threshold is policy, and it actually changes behaviour."""
    phrase = "the quarterly report describes an unusual seasonal variation in observed demand"
    example = BenchmarkExample(
        example_id="freq_ex", task_id="synthetic_task", prompt=phrase, candidate_answers=["A", "B"]
    )
    corpus = [
        make_doc(f"doc_{i}", f"Section {i}. {phrase} Additional notes follow here.")
        for i in range(5)
    ]

    permissive = BenchmarkExclusionMatcher(ExclusionConfig(max_corpus_span_frequency=10))
    permissive.index_examples([example])
    assert permissive.scan(corpus).excluded_doc_ids == [f"doc_{i}" for i in range(5)]

    strict = BenchmarkExclusionMatcher(ExclusionConfig(max_corpus_span_frequency=2))
    strict.index_examples([example])
    assert strict.scan(corpus).excluded_doc_ids == []


def test_short_span_length_is_rejected_by_the_schema() -> None:
    """A span too short to be informative is a configuration error, not a silent risk."""
    with pytest.raises(ValueError):
        ExclusionConfig(span_length=2)


def test_report_contains_no_benchmark_text_or_labels() -> None:
    """Reports carry digests and counts, never protected content."""
    example = synthetic_example()
    docs = [
        make_doc("contaminated", example.rendered()),
        make_doc("also_contaminated", "Preamble. " + CANARY_PROMPT + " Postscript."),
        make_doc(
            "clean", "Coral reef bleaching correlates with sea surface temperature anomalies."
        ),
    ]
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])
    report = matcher.scan(docs)

    serialized = json.dumps(report.to_dict())
    for fragment in ("tulip", "quarry", "badger", "bloomed", "Frost"):
        assert fragment.lower() not in serialized.lower(), f"report leaked '{fragment}'"

    assert len(report.hits) == 2
    for hit in report.hits:
        assert len(hit.evidence_digest) == 32
        assert "tulip" not in hit.evidence_digest


def test_report_states_its_own_limits_honestly() -> None:
    """Coverage notes must not imply proof of an uncontaminated corpus."""
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([synthetic_example()])
    report = matcher.scan([make_doc("d", "Unrelated body text about harbour dredging schedules.")])

    joined = " ".join(report.coverage_notes).lower()
    assert "paraphras" in joined
    assert "not proof" in joined or "cannot" in joined
    assert report.excluded_doc_ids == []


def test_paraphrase_is_acknowledged_as_undetected() -> None:
    """A paraphrase is not caught, and the report does not pretend otherwise.

    This records a real limit of the method rather than asserting a capability the
    implementation does not have.
    """
    example = synthetic_example()
    paraphrase = make_doc(
        "paraphrased",
        "Beside the disused quarry, someone set out seventeen tulip corms in a spiral arrangement.",
    )
    matcher = BenchmarkExclusionMatcher()
    matcher.index_examples([example])
    report = matcher.scan([paraphrase])

    assert report.excluded_doc_ids == []
    assert any("paraphras" in note.lower() for note in report.coverage_notes)


def test_exclusion_policy_identity_changes_with_behavioral_settings() -> None:
    base = ExclusionConfig()
    assert base.identity() == ExclusionConfig().identity()
    assert base.identity() != ExclusionConfig(span_length=20).identity()
    assert base.identity() != ExclusionConfig(max_corpus_span_frequency=1).identity()
    assert base.identity() != ExclusionConfig(match_informative_spans=False).identity()
