"""Bounded authored-fixture characterization; failed coverage is reported, never hidden."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.source_run import write_once
from xlm.data.dedup.lineage import lineage_key
from xlm.data.exclusion.benchmark import BenchmarkExample, BenchmarkExclusionMatcher
from xlm.data.normalization import compute_sha256


def document(
    identifier: str, text: str, metadata: dict[str, Any] | None = None
) -> CanonicalDocument:
    return CanonicalDocument(
        doc_id=identifier,
        source_id="authored",
        source_revision="authored-v1",
        source_file="authored.jsonl",
        source_row=0,
        raw_hash=compute_sha256(text),
        clean_hash=compute_sha256(text),
        text=text,
        utf8_byte_count=len(text.encode()),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata=metadata or {},
        parent_ids=[],
        license_reference="authored",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def audit() -> dict[str, Any]:
    prompt = (
        "Seventeen silver telescopes followed violet comets above the northern "
        "observatory last Tuesday evening"
    )
    example = BenchmarkExample("authored-1", "authored-task", prompt, ["yes", "no"])
    cases: list[tuple[str, BenchmarkExample, list[str], int | None]] = [
        ("whole_rendered_example", example, [example.rendered()], 1),
        (
            "normalized_prompt_in_context",
            example,
            [f"Context: {prompt.upper()}! Explanation follows."],
            1,
        ),
        ("option_permutation_long_prompt", example, [f"{prompt} no yes"], 1),
        ("four_cross_source_prompt_copies", example, [f"Context: {prompt}" for _ in range(4)], 4),
        (
            "paraphrase_no_exact_span",
            example,
            [
                "Last Tuesday night, a northern observatory used seventeen silver telescopes "
                "to track purple comets."
            ],
            None,
        ),
        (
            "short_prompt_without_answers",
            BenchmarkExample("short", "authored", "Which moon is turquoise?", ["Io", "Luna"]),
            ["Context: Which moon is turquoise? Explanation follows."],
            1,
        ),
        (
            "short_answer_labels_common_phrases",
            example,
            [
                "yes",
                "no",
                "A B C D",
                "Water freezes when sufficiently cold.",
                "The cat sat on the mat.",
            ],
            0,
        ),
    ]
    results = []
    for name, benchmark, texts, expected in cases:
        matcher = BenchmarkExclusionMatcher()
        matcher.index_examples([benchmark])
        docs = [
            replace(document(f"doc-{i}", text), source_id=f"authored-source-{i}")
            for i, text in enumerate(texts)
        ]
        report = matcher.scan(docs)
        observed = len(report.excluded_doc_ids)
        results.append(
            {
                "case": name,
                "documents": len(docs),
                "excluded": observed,
                "expected_for_production_requirement": expected,
                "production_requirement_met": None if expected is None else observed == expected,
                "common_spans_suppressed": report.spans_suppressed_as_common,
            }
        )
    seed_a = document(
        "a", "Authored context one.", {"query_seed_url": "https://example.invalid/seed"}
    )
    seed_b = document(
        "b", "Authored context two.", {"query_seed_url": "https://example.invalid/seed"}
    )
    return {
        "evidence_class": "authored synthetic fixtures only; no benchmark material",
        "cases": results,
        "generic_negative_fixture_false_positives": results[-1]["excluded"],
        "generic_negative_fixture_count": len(cases[-1][2]),
        "population_false_positive_rate": None,
        "synth_query_seed_url_grouped": lineage_key(seed_a) == lineage_key(seed_b),
        "production_ready": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit()
    write_once(args.output, result)
    print(result)
    return 0  # Characterization saved; production_ready above remains false.


if __name__ == "__main__":
    raise SystemExit(main())
