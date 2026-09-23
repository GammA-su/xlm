"""Authored exact oracles for P29C; no downloads, timing gates or secret-bearing logs."""

from __future__ import annotations

import collections
import random
import re
from dataclasses import replace

import pytest

from test_performance_cleaning import document, reference_chars
from xlm.data.cleaning.features import CharStats, TextFeatures, compute_char_stats
from xlm.data.cleaning.language import ENGLISH_STOP_WORDS, LanguageFilter
from xlm.data.cleaning.pii import (
    SECRET_PATTERNS,
    PiiConfig,
    PiiSecretFilter,
    is_reserved_example_email,
)
from xlm.data.cleaning.repetition import (
    RepetitionConfig,
    RepetitionFilter,
    is_structural_separator_line,
)


def reference_language(text: str, technical: bool, educational: bool) -> tuple[str, float | None]:
    text = text.strip()
    if not text:
        return "unknown", None
    words = [w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", text)]
    if len(words) < 8 and (technical or educational):
        return "ambiguous", None
    alpha = sum(c.isalpha() for c in text)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    if len(words) < 8:
        return ("non_en", 0.85) if alpha and latin / alpha < 0.5 else ("ambiguous", None)
    if not alpha:
        return "ambiguous", None
    if latin / alpha < 0.7:
        return "non_en", min(1.0, 1.0 - latin / alpha + 0.5)
    stop = sum(w in ENGLISH_STOP_WORDS for w in words)
    if stop >= 2 and stop / len(words) >= 0.08:
        return "en", round(min(0.99, 0.5 + stop / len(words) * 1.5), 3)
    if technical:
        return "ambiguous", None
    if not stop and len(words) >= 15:
        return "non_en", 0.75
    return "ambiguous", None


def test_language_frozen_counts_case_and_unicode_boundaries() -> None:
    rng = random.Random(29)
    words = [
        "THE",
        "And",
        "river",
        "ROCK",
        "of",
        "to",
        "abc_def",
        "7and",
        "THE7",
        "İthe",
        "Kthe",
        "theſ",
        "東京",
        "i̇",
    ]
    texts = ["", " \t ", "THE AND of river " * 4]
    texts += [" ".join(rng.choices(words, k=rng.randrange(1, 80))) for _ in range(250)]
    for text in texts:
        for kind, educational in (("prose", False), ("code", False), ("math", True)):
            doc = replace(
                document(text),
                document_kind=kind,
                source_metadata={"is_educational_density": educational},
            )
            expected = reference_language(doc.text, kind != "prose", educational)
            for features in (None, TextFeatures()):
                assert LanguageFilter()._heuristic_classify(doc, features) == expected


@pytest.mark.parametrize("cap", [1, 49, 50, 51, 50000])
def test_ngram_prefix_multiset_is_exact(cap: int) -> None:
    rng = random.Random(98)
    filt = RepetitionFilter(
        RepetitionConfig(max_analysis_chars=cap, max_ngram_repetition_ratio=1.0)
    )
    for count in (1, 9, 10, 11, 50, 1000):
        doc = document(" ".join(rng.choices(["the", "river", "a", "forest", "hills"], k=count)))
        words = doc.text[:cap].split()
        expected = 0.0
        if len(words) >= 10:
            counts = collections.Counter(tuple(words[i : i + 5]) for i in range(len(words) - 4))
            expected = sum(v for v in counts.values() if v > 1) / sum(counts.values())
        result = filt.apply(doc, TextFeatures())
        assert result.metrics.max_ngram_repetition_ratio == expected
        assert result.metrics.word_count == len(doc.text.split())


def test_structural_separator_fast_rejection_preserves_exact_syntax() -> None:
    def reference(line: str) -> bool:
        stripped = line.strip()
        if len(stripped) < 3:
            return False
        compact = stripped.replace(" ", "").replace("\t", "")
        if len(compact) < 3:
            return False
        chars = set(compact)
        return any(chars <= {c} for c in "-*_=") or (
            "|" in compact and chars <= set("|-:+=") and ("-" in compact or "=" in compact)
        )

    rng = random.Random(29)
    cases = ["---", "| --- | :---: |", "-" * 1000, "legitimate prose ---", "___", "\u2003---\u2003"]
    cases += [
        "".join(rng.choices("|-:+=*_ \t\rabc123東", k=rng.randrange(80))) for _ in range(1000)
    ]
    for text in cases:
        assert is_structural_separator_line(text) == reference(text)


@pytest.mark.parametrize("action", ["reject", "redact"])
def test_pii_frozen_order_placeholders_and_unicode_ssn(action: str) -> None:
    atoms = [
        "ordinary prose",
        "placeholder@example.com",
        "user@sub.example.org",
        "user@real-company.com",
        "user@notactuallyexample.com",
        "user@example.org.attacker.invalid",
        "123-45-6789",
        "١٢٣-٤٥-٦٧٨٩",
        "hf_" + "A" * 34,
        "ghp_" + "B" * 36,
        "AKIA" + "X" * 16,
        "bEaReR " + "c" * 24,
        "-----BEGIN RSA PRIVATE KEY-----",
        "CANARY_SECRET_AUTHORED",
    ]
    rng = random.Random(17)
    for _ in range(150):
        text = " / ".join(rng.choices(atoms, k=rng.randrange(1, 12)))
        detected = []
        placeholders = 0
        for kind, pattern in SECRET_PATTERNS:
            if kind == "email_address":
                matches = [is_reserved_example_email(m.group()) for m in pattern.finditer(text)]
                placeholders = sum(matches)
                found = False in matches
            else:
                found = pattern.search(text) is not None
            if found:
                detected.append(kind)
        result = PiiSecretFilter(PiiConfig(action=action)).apply(document(text), TextFeatures())
        assert result.metrics.detected_secrets == detected
        assert result.metrics.reserved_email_placeholders_ignored == placeholders
        prefix = "detected_secret" if action == "reject" else "redacted_secret"
        assert result.reasons == [f"{prefix}:{kind}" for kind in detected]


def test_mixed_unicode_character_counts_and_feature_invalidation() -> None:
    for code in range(0, 0x110000, 1013):
        text = "ASCII prose THE and 123 + \t " * 40 + chr(code)
        expected, actual = reference_chars(text), compute_char_stats(text)
        assert [getattr(actual, k) for k in CharStats.__slots__] == [
            getattr(expected, k) for k in CharStats.__slots__
        ]
    features = TextFeatures()
    for text in ("the old words", "new 東京 text\nchanged", "999 \t ", ""):
        assert features.words(text) == text.split()
        assert features.lines(text) == text.splitlines()
        assert features.chars(text).alpha == sum(c.isalpha() for c in text)
