"""P29 exact offline performance regression checks."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning import features
from xlm.data.cleaning.features import CharStats, compute_char_stats
from xlm.data.cleaning.pipeline import create_pipeline_preset
from xlm.data.normalization import canonical_normalize


def document(text: str, index: int = 0) -> CanonicalDocument:
    text = canonical_normalize(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return CanonicalDocument(
        str(index),
        "fixture",
        "v1",
        "authored.jsonl",
        index,
        digest,
        digest,
        text,
        len(text.encode()),
        "en",
        1.0,
        "prose",
        {},
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def reference_chars(text: str) -> CharStats:
    return CharStats(
        alpha=sum(c.isalpha() for c in text),
        latin=sum(c.isascii() and c.isalpha() for c in text),
        alnum=sum(c.isalnum() for c in text),
        non_ws=sum(not c.isspace() for c in text),
        symbols=sum(not c.isspace() and not c.isalnum() for c in text),
        digits=sum(not c.isspace() and c.isdigit() for c in text),
    )


def test_character_counts_entire_unicode_domain() -> None:
    # Every ASCII control and every Unicode code point, including unusual
    # whitespace/digits, exercises the gate and the unchanged Unicode path.
    texts = ["", "".join(chr(i) for i in range(128)) * 4]
    texts.extend(
        "".join(map(chr, range(i, min(i + 1024, 0x110000)))) for i in range(0, 0x110000, 1024)
    )
    for text in texts:
        expected = reference_chars(text)
        actual = compute_char_stats(text)
        assert [getattr(actual, k) for k in CharStats.__slots__] == [
            getattr(expected, k) for k in CharStats.__slots__
        ]


def test_cleaning_decisions_and_metrics_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(93)
    alphabet = "the and river forest village mountain garden apple bright quiet study walk ".split()
    docs = [document(" ".join(rng.choices(alphabet, k=100)), i) for i in range(64)]
    docs += [
        document(text, i + 64)
        for i, text in enumerate(
            [
                "| Name | Value |\n| --- | --- |\n| River | 13 |",
                "Contact sample@example.com. A visitor wrote the story in the village.",
                "Contact real@authored.invalid. A visitor wrote the story in the village.",
                "sample@example.org with SSN 123-45-6789 and a story about the river.",
                "Unicode caf\u00e9, \u6771\u4eac, \u00b2 \u2167 and \u2003 spaces.",
            ]
        )
    ]
    output, summary = create_pipeline_preset("prose").run_stream(docs)
    actual = [d.to_dict() for d in output]
    monkeypatch.setattr(features, "compute_char_stats", reference_chars)
    expected_output, expected_summary = create_pipeline_preset("prose").run_stream(docs)
    assert actual == [d.to_dict() for d in expected_output]
    left, right = asdict(summary), asdict(expected_summary)
    left.pop("elapsed_seconds")
    right.pop("elapsed_seconds")
    for payload in (left, right):
        for stage in payload["stage_metrics"]:
            stage.pop("duration_ms")
    assert left == right
