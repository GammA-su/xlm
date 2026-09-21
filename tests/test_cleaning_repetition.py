"""Regression tests for structural-Markdown-aware repetition filtering.

Covers the IFM live-preparation finding where valid Markdown horizontal rules
(``---``) and table delimiter rows were misclassified as low-quality
repetition, plus the quarantine-metrics defect (``word_count = 0`` /
``line_count = 0`` / hardcoded ``duplicate_line_ratio = 1.0`` on rejections).

All fixtures are authored synthetic text; no operator or live-source data.
"""

from __future__ import annotations

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.repetition import (
    RepetitionConfig,
    RepetitionFilter,
    is_structural_separator_line,
)
from xlm.data.cleaning.types import TransformAction
from xlm.data.normalization import compute_sha256


def make_doc(doc_id: str, text: str) -> CanonicalDocument:
    body = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_repetition",
        source_revision="rev_1",
        source_file="test.md",
        source_row=1,
        raw_hash=compute_sha256(body),
        clean_hash=compute_sha256(body),
        text=text,
        utf8_byte_count=len(body),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


_TOPICS = (
    "estuarine sediment transport",
    "permafrost thaw subsidence",
    "nocturnal pollinator networks",
    "gothic vault construction",
    "swahili coastal trade",
    "quantum error mitigation",
    "alpine glacier retreat",
    "byzantine mosaic restoration",
    "mangrove carbon sequestration",
    "tokugawa urban planning",
    "abyssal hydrothermal vents",
    "baroque fugue composition",
    "sahelian agroforestry systems",
    "cretaceous amber inclusions",
    "inuit celestial navigation",
    "andean terrace irrigation",
)

_LENSES = (
    "isotopic fingerprinting",
    "archival cartography",
    "acoustic telemetry",
    "petrographic microscopy",
    "oral-history transcription",
    "cryogenic spectroscopy",
    "photogrammetric surveying",
    "paleographic comparison",
    "eddy-covariance flux towers",
    "cadastral record linkage",
    "submersible videography",
    "manuscript collation",
    "dendrochronological cross-dating",
    "amber microtomography",
    "star-compass triangulation",
    "hydrological tracer tests",
)

_PLACES = (
    "the Severn estuary",
    "the Yamal peninsula",
    "Madagascan vanilla groves",
    "the Chartres choir",
    "the Lamu archipelago",
    "a dilution refrigerator laboratory",
    "the Aletsch forefield",
    "the Chora monastery",
    "the Sundarbans delta",
    "the Nihonbashi district",
    "the Endeavour segment",
    "the Thomaskirche loft",
    "the Yatenga plateau",
    "the Hukawng valley",
    "the Igloolik sound",
    "the Colca canyon",
)


def unique_section(idx: int) -> str:
    """One headed section whose wording shares no 5-gram with any other."""
    topic = _TOPICS[idx % len(_TOPICS)]
    lens = _LENSES[idx % len(_LENSES)]
    place = _PLACES[idx % len(_PLACES)]
    return (
        f"## Section {idx}: {topic} revisited\n\n"
        f"Case {idx} examines {topic} through {lens} across {place}, "
        f"yielding evidence {idx} that reframes earlier assumptions {idx}."
    )


# -------------------------------------------------------------------------
# Structural separator recognition
# -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "---",
        "***",
        "___",
        "- - -",
        "* * *",
        "----------",
        "-" * 60,
        "*" * 55,
        "_" * 52,
        "===",
        "=" * 40,
        "|---|---|",
        "| --- | --- |",
        "|--------|-------|----------|----------------------------------|",
    ],
)
def test_structural_separator_lines_recognized(line: str) -> None:
    assert is_structural_separator_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "## 1. Executive Summary",
        "2. **Mediating processes:**",
        "- a list item with text",
        "| Lesson | Activity | Design |",
        "| **M0** | Orientation | 2 hrs |",
        "***bold italic***",
        "--",
        "-",
        "",
        "Hello world",
    ],
)
def test_content_lines_not_structural(line: str) -> None:
    assert is_structural_separator_line(line) is False


# -------------------------------------------------------------------------
# False-positive repairs: legitimate structured Markdown must be accepted
# -------------------------------------------------------------------------


def test_repeated_horizontal_rules_in_structured_doc_accepted() -> None:
    """Fourteen `---` separators across unique sections must not reject."""
    filt = RepetitionFilter()
    sections = "\n\n---\n\n".join(unique_section(i) for i in range(15))
    doc = make_doc("doc_hr", sections)
    res = filt.apply(doc)
    assert res.action == TransformAction.ACCEPT
    assert res.reasons == []


def test_markdown_tables_with_long_delimiter_rows_accepted() -> None:
    """Table delimiter rows containing 50+ dash spans are valid syntax."""
    filt = RepetitionFilter()
    tables: list[str] = []
    for t in range(3):
        tables.append(
            f"## Curriculum table {t} for cohort {t}\n\n"
            f"| Module | Title | Duration | Objectives for cohort {t} |\n"
            "|--------|-------|----------"
            f"|{'-' * 52}|\n"
            f"| **M{t}** | Orientation phase {t} | 2 hrs | Baseline survey {t} |\n"
            f"| **N{t}** | Diagnostic phase {t} | 3 hrs | Skills mapping {t} |"
        )
    text = "\n\n---\n\n".join(tables)
    doc = make_doc("doc_tables", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.ACCEPT
    assert res.reasons == []


def test_long_runs_of_structural_punctuation_accepted() -> None:
    """Standalone divider/underline runs of `-`, `=`, `*`, `_` are accepted."""
    filt = RepetitionFilter()
    text = (
        f"{unique_section(0)}\n\n"
        f"{'-' * 60}\n\n"
        f"{unique_section(1)}\n\n"
        f"{'=' * 55}\n\n"
        f"{unique_section(2)}\n\n"
        f"{'*' * 52}\n\n"
        f"{unique_section(3)}\n\n"
        f"{'_' * 51}\n\n"
        f"{unique_section(4)}"
    )
    doc = make_doc("doc_dividers", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.ACCEPT
    assert res.reasons == []


def test_blank_line_repetition_accepted() -> None:
    """Heavy blank-line spacing around unique content is not repetition."""
    filt = RepetitionFilter()
    text = ("\n\n\n\n").join(unique_section(i) for i in range(8)) + "\n\n\n"
    doc = make_doc("doc_blanks", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.ACCEPT


# -------------------------------------------------------------------------
# Genuine repetition must still be rejected (thresholds unchanged)
# -------------------------------------------------------------------------


def test_repeated_alphabetic_garbage_rejected() -> None:
    """A 51+ run of a letter inside a content line still rejects."""
    filt = RepetitionFilter()
    doc = make_doc("doc_alpha_garbage", "Error detected " + "a" * 51 + " ending.")
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("char_run_exceeded_50" in r for r in res.reasons)


def test_repeated_punctuation_garbage_in_prose_rejected() -> None:
    """Punctuation was not broadly exempted: `!` runs in prose reject."""
    filt = RepetitionFilter()
    doc = make_doc("doc_bang_garbage", "Amazing sale " + "!" * 55 + " buy now today.")
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("char_run_exceeded_50" in r for r in res.reasons)


def test_dash_run_inside_prose_line_rejected() -> None:
    """A 51+ dash run mixed with prose is not separator syntax: rejects."""
    filt = RepetitionFilter()
    doc = make_doc("doc_dash_prose", "Note to reader " + "-" * 55 + " end of note.")
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("char_run_exceeded_50" in r for r in res.reasons)


def test_repeated_prose_line_rejected() -> None:
    """Eleven identical content lines exceed the unchanged threshold of 10."""
    filt = RepetitionFilter()
    repeated = "\n".join(["Click here to see more links and offers!"] * 11)
    doc = make_doc("doc_prose_loop", repeated)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert res.reasons == ["excessive_repetition:line_frequency_11"]


def test_separators_plus_repeated_prose_still_caught() -> None:
    """Structural separators are ignored but the prose loop is still caught."""
    filt = RepetitionFilter()
    parts: list[str] = []
    for i in range(14):
        parts.append(unique_section(i))
        parts.append("---")
    parts.extend(["Subscribe now for unlimited access today!"] * 11)
    doc = make_doc("doc_mixed", "\n\n".join(parts))
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    # Content max frequency is 11 (prose loop), not 14 (separators).
    assert res.reasons == ["excessive_repetition:line_frequency_11"]


def test_repeated_headings_rejected() -> None:
    """Repeated Markdown headings carry content and remain detectable."""
    filt = RepetitionFilter()
    repeated = "\n\n".join(["## Breaking: winners announced"] * 12)
    doc = make_doc("doc_headings", repeated)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("line_frequency_12" in r for r in res.reasons)


def test_duplicate_line_ratio_still_rejects_content_duplication() -> None:
    """High content duplication via many distinct pairs still trips the ratio."""
    filt = RepetitionFilter(
        RepetitionConfig(max_duplicate_line_ratio=0.30, max_line_frequency=10_000)
    )
    pairs: list[str] = []
    for i in range(20):
        pairs.append(f"Unique filler sentence number {i} about topic {i}.")
        pairs.append(f"Repeated paragraph variant {i % 5} duplicated twice here.")
    doc = make_doc("doc_dup_ratio", "\n".join(pairs))
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("duplicate_line_ratio" in r for r in res.reasons)


# -------------------------------------------------------------------------
# N-gram behavior preserved
# -------------------------------------------------------------------------


def test_ngram_loop_still_rejected() -> None:
    filt = RepetitionFilter()
    text = " ".join(["alpha beta gamma delta epsilon"] * 30)
    doc = make_doc("doc_ngram", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("ngram_repeat_ratio" in r for r in res.reasons)


def test_ngram_normal_doc_accepted() -> None:
    filt = RepetitionFilter()
    text = " ".join(unique_section(i) for i in range(6))
    doc = make_doc("doc_ngram_ok", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.ACCEPT


# -------------------------------------------------------------------------
# Metrics honesty on rejections
# -------------------------------------------------------------------------


def test_rejected_doc_reports_measured_word_and_line_counts() -> None:
    """Quarantine metrics must not serialize unmeasured zeros."""
    filt = RepetitionFilter()
    repeated = "\n".join(["Click here to see more links and offers!"] * 11)
    doc = make_doc("doc_metrics", repeated)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert res.metrics.word_count == len(repeated.split())
    assert res.metrics.word_count > 0
    assert res.metrics.line_count == 11
    assert res.metrics.utf8_byte_count == doc.utf8_byte_count


def test_char_run_rejection_reports_measured_counts() -> None:
    filt = RepetitionFilter()
    text = "Error detected " + "x" * 60 + " ending."
    doc = make_doc("doc_char_metrics", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert res.metrics.word_count == len(text.split())
    assert res.metrics.line_count == 1


def test_line_frequency_rejection_reports_honest_duplicate_ratio() -> None:
    """The hardcoded 1.0 is replaced by the measured content-line ratio."""
    filt = RepetitionFilter()
    lines = ["Click here to see more links and offers!"] * 11
    lines.append(unique_section(99))
    text = "\n".join(lines)
    doc = make_doc("doc_dup_honest", text)
    res = filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert 0.0 < res.metrics.duplicate_line_ratio < 1.0


def test_threshold_defaults_unchanged() -> None:
    cfg = RepetitionConfig()
    assert cfg.max_line_frequency == 10
    assert cfg.max_char_run == 50
    assert cfg.max_duplicate_line_ratio == 0.30
    assert cfg.max_ngram_repetition_ratio == 0.25


def test_filter_version_bumped_for_behavior_change() -> None:
    assert RepetitionFilter.version == "2"
