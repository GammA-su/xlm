"""Phase-A quality detectors on authored adversarial fixtures (measurement, not decisions)."""

from __future__ import annotations

import random

import numpy as np
import pytest

from quality_fixtures import TEXTS
from xlm.data.quality.aggregate import Population, bin_matrix, coarse_matrix
from xlm.data.quality.detectors import Analysis, analyze
from xlm.data.quality.policy import (
    CLASS_ORDER,
    FLAGS,
    METRIC_INDEX,
    METRICS,
    count_bin,
    count_bin_bounds,
    metric_bin,
    policy_body,
    policy_identity,
    ratio_bin,
)
from xlm.data.quality.review import ChunkDocs, merge_samples, sample_chunk


def run(name: str) -> Analysis:
    text = TEXTS[name]
    return analyze(text, len(text.encode("utf-8")))


def value(result: Analysis, metric: str) -> float | int | None:
    return result.values[METRIC_INDEX[metric]]


def flags(result: Analysis) -> set[str]:
    return {FLAGS[f] for f in result.flags}


# -- HTML / markup ------------------------------------------------------------------------


def test_real_html_page_is_full_markup_with_structure_indicators() -> None:
    result = run("html_page")
    assert result.doc_class == "markup_like"
    assert {
        "has_doctype",
        "has_html_open",
        "has_head",
        "has_body",
        "has_script",
        "has_style",
        "has_nav",
        "has_footer",
        "has_closing_tag",
        "has_entity",
        "markup_full_html",
        "markup_any",
    } <= flags(result)
    assert "markup_ambiguous_code" not in flags(result)
    assert value(result, "script_style_blocks") == 2
    assert float(value(result, "html_tag_char_ratio") or 0) > 0.3


def test_light_markup_is_not_full_html() -> None:
    result = run("light_markup")
    assert {"markup_light", "markup_any", "has_entity"} <= flags(result)
    assert "markup_full_html" not in flags(result)
    assert value(result, "html_tags") == 2
    assert result.doc_class == "prose_like"


@pytest.mark.parametrize("name", ["cpp_templates", "xml_code"])
def test_templates_and_xml_are_ambiguous_not_html(name: str) -> None:
    result = run(name)
    assert value(result, "html_tags") == 0
    assert int(value(result, "generic_tags") or 0) > 0
    assert "markup_ambiguous_code" in flags(result)
    assert not {"markup_full_html", "markup_light"} & flags(result)


def test_xml_declaration_is_reported() -> None:
    assert "has_xml_decl" in flags(run("xml_code"))


def test_mathematical_angle_brackets_are_not_markup() -> None:
    result = run("math_angles")
    assert value(result, "html_tags") == 0
    assert value(result, "generic_tags") == 0
    assert not {f for f in flags(result) if f.startswith(("markup", "has_"))}


def test_cpp_generics_are_code_like() -> None:
    assert run("cpp_templates").doc_class == "code_like"


# -- repetition -----------------------------------------------------------------------------


def test_character_spam_records_runs_by_class() -> None:
    result = run("char_spam")
    assert value(result, "max_char_run") == 300
    assert value(result, "max_punct_run") == 300
    assert value(result, "max_alnum_run") == 0
    assert float(value(result, "repeated_char_ratio") or 0) > 0.9


def test_markdown_separator_is_measured_but_document_stays_prose() -> None:
    result = run("markdown_separator")
    assert value(result, "max_punct_run") == 40
    assert result.doc_class == "prose_like"
    assert float(value(result, "repeated_char_ratio") or 0) < 0.1


def test_ascii_art_has_no_long_runs_or_duplicate_eligible_lines() -> None:
    result = run("ascii_art")
    assert value(result, "max_char_run") == 0  # no run reaches RUN_MIN
    assert value(result, "dup_lines") == 0  # "+---+---+" is shorter than MIN_LINE_CHARS


def test_repeated_paragraphs_are_counted_on_the_comparison_form() -> None:
    result = run("repeated_paragraphs")
    assert value(result, "dup_paragraphs") == 4
    assert value(result, "dup_lines") == 4
    assert float(value(result, "dup_paragraph_byte_ratio") or 0) == pytest.approx(0.8, abs=0.01)


def test_whitespace_variants_of_a_paragraph_are_duplicates() -> None:
    text = TEXTS["ordinary_prose"] + "\n\n" + TEXTS["ordinary_prose"].replace(" ", "   ")
    result = analyze(text, len(text.encode()))
    assert value(result, "dup_paragraphs") == 1


def test_code_braces_are_not_repeated_prose() -> None:
    result = run("code_braces")
    assert value(result, "dup_lines") == 0
    assert result.doc_class == "code_like"


def test_generated_loop_is_a_strong_ngram_and_compression_signal() -> None:
    loop = run("generated_loop")
    prose = run("ordinary_prose")
    assert float(value(loop, "ngram10_excess_ratio") or 0) > 0.95
    assert float(value(loop, "compression_ratio") or 1) < 0.1
    assert float(value(loop, "type_token_ratio") or 1) < 0.05
    assert value(prose, "ngram10_excess_ratio") == 0
    assert value(prose, "compression_ratio") is None  # below COMPRESSION_MIN_BYTES
    varied = TEXTS["ordinary_prose"] + " " + TEXTS["clean_scientific"] + TEXTS["accented_latin"]
    assert float(value(analyze(varied, len(varied.encode())), "compression_ratio") or 0) > 0.5


# -- Unicode ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["accented_latin", "japanese", "emoji", "combining_marks", "ordinary_prose"]
)
def test_legitimate_unicode_is_not_corruption(name: str) -> None:
    result = run(name)
    for metric in (
        "nul",
        "c0_controls",
        "c1_controls",
        "replacement_chars",
        "mojibake_hits",
        "private_use",
        "noncharacters",
        "bidi_controls",
        "zero_width",
    ):
        assert value(result, metric) == 0, metric
    assert "control_any" not in flags(result)


def test_emoji_joiners_are_reported_separately_from_zero_width_junk() -> None:
    result = run("emoji")
    assert int(value(result, "zwj_zwnj") or 0) == 2
    assert value(result, "zero_width") == 0


def test_japanese_prose_is_prose_like() -> None:
    assert run("japanese").doc_class == "prose_like"


def test_control_junk_is_counted_per_kind() -> None:
    result = run("control_junk")
    assert value(result, "nul") == 1
    assert value(result, "c0_controls") == 2  # BEL and ESC; LF/TAB are allowed
    assert value(result, "c1_controls") == 1
    assert value(result, "replacement_chars") == 1
    assert "control_any" in flags(result)


def test_format_junk_is_counted_per_kind() -> None:
    result = run("format_junk")
    assert value(result, "zero_width") == 1
    assert value(result, "bom") == 1
    assert value(result, "bidi_controls") == 2
    assert value(result, "private_use") == 1
    assert value(result, "noncharacters") == 1


def test_mojibake_sequences_are_counted() -> None:
    assert value(run("mojibake"), "mojibake_hits") == 5


# -- OCR / PDF -------------------------------------------------------------------------------


def test_page_headers_and_numbers() -> None:
    result = run("page_headers")
    assert float(value(result, "repeated_header_ratio") or 0) > 0.3
    assert float(value(result, "page_number_line_ratio") or 0) > 0.3


def test_hyphenation_breaks_are_measured_not_joined() -> None:
    text = TEXTS["hyphenation"]
    result = analyze(text, len(text.encode()))
    assert value(result, "hyphen_break_ratio") == pytest.approx(0.8)
    assert TEXTS["hyphenation"] == text  # the analysis never alters its input


def test_page_number_forms() -> None:
    assert value(run("page_numbers"), "page_number_line_ratio") == pytest.approx(0.5)


def test_clean_scientific_text_has_no_ocr_signals() -> None:
    result = run("clean_scientific")
    for metric in (
        "page_number_line_ratio",
        "hyphen_break_ratio",
        "single_char_line_ratio",
        "repeated_header_ratio",
        "replacement_chars",
    ):
        assert value(result, metric) == 0, metric
    assert result.doc_class == "prose_like"


def test_isolated_single_character_lines() -> None:
    assert value(run("single_char_lines"), "single_char_line_ratio") == 1.0


# -- composition / classes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("python_code", "code_like"),
        ("code_braces", "code_like"),
        ("latex_equation", "math_table_like"),
        ("markdown_table", "math_table_like"),
        ("ordinary_prose", "prose_like"),
        ("garbage_symbols", "other"),
        ("html_page", "markup_like"),
        ("empty", "empty"),
        ("whitespace_only", "empty"),
    ],
)
def test_document_classes(name: str, expected: str) -> None:
    assert run(name).doc_class == expected


def test_url_list_is_url_dense() -> None:
    result = run("url_list")
    assert "has_url" in flags(result)
    assert value(result, "urls") == 3
    assert float(value(result, "url_char_ratio") or 0) > 0.9


def test_garbage_symbols_are_symbol_heavy_and_low_alpha() -> None:
    result = run("garbage_symbols")
    symbols = float(value(result, "symbol_ratio") or 0) + float(value(result, "punct_ratio") or 0)
    assert symbols > 0.7
    assert value(result, "alpha_ratio") == 0


def test_boilerplate_categories_record_only_the_category() -> None:
    result = run("boilerplate")
    assert {
        "bp_cookie",
        "bp_privacy",
        "bp_terms",
        "bp_subscribe",
        "bp_account",
        "bp_copyright",
        "bp_navigation",
        "bp_social",
        "boilerplate_any",
    } <= flags(result)
    assert value(run("ordinary_prose"), "boilerplate_lines") == 0


def test_size_buckets() -> None:
    assert {"empty", "chars_lt16"} <= flags(run("empty"))
    assert "whitespace_only" in flags(run("whitespace_only"))
    tiny = flags(run("tiny"))
    assert {"chars_lt16", "chars_lt32", "chars_lt256"} <= tiny
    big = "x " * (40 * 1024)
    assert "bytes_gt64KiB" in flags(analyze(big, len(big)))


def test_no_detector_emits_a_decision_or_text() -> None:
    for name, text in TEXTS.items():
        result = analyze(text, len(text.encode("utf-8")))
        assert result.doc_class in CLASS_ORDER
        assert all(isinstance(v, int | float) or v is None for v in result.values), name
        assert len(result.values) == len(METRICS)


def test_analysis_bounds_set_the_truncation_flag() -> None:
    text = "word " * 120_000
    result = analyze(text, len(text))
    assert "truncated_analysis" in flags(result)


# -- histograms / aggregation / review ---------------------------------------------------------


def test_vectorized_bins_equal_scalar_bins() -> None:
    rng = random.Random(3)
    ints = [0, 1, 15, 16, 17, 18, 31, 32, 33, 1023, 1024, 2**26 + 5] + [
        rng.randrange(0, 2**30) for _ in range(500)
    ]
    for n in ints:
        lo, hi = count_bin_bounds(count_bin(n))
        assert lo <= n < hi
    rows = []
    for _ in range(300):
        rows.append(
            [rng.random() if spec.kind == "ratio" else float(rng.choice(ints)) for spec in METRICS]
        )
    rows.append([1.0 if spec.kind == "ratio" else 0.0 for spec in METRICS])
    matrix = np.array(rows)
    vectorized = bin_matrix(matrix)
    for i, row in enumerate(rows):
        for j, spec in enumerate(METRICS):
            assert vectorized[i, j] == metric_bin(spec, row[j])
    assert ratio_bin(1.0) == 1000


def test_population_merge_is_associative_and_order_independent() -> None:
    names = sorted(TEXTS)
    results = [analyze(TEXTS[n], len(TEXTS[n].encode())) for n in names]
    matrix = np.array([[np.nan if v is None else float(v) for v in r.values] for r in results])
    sizes = np.array([len(TEXTS[n].encode()) for n in names], dtype=np.int64)
    bits = np.array([sum(1 << f for f in r.flags) for r in results], dtype=np.int64)
    classes = np.array([CLASS_ORDER.index(r.doc_class) for r in results], dtype=np.int64)

    def build(parts: list[np.ndarray]) -> Population:
        total = Population()
        for idx in parts:
            pop = Population()
            pop.add_batch(matrix[idx], sizes[idx], 0, bits[idx], classes[idx])
            total.merge(pop)
        return total

    order = np.arange(len(names))
    whole = build([order]).to_json()
    split = build([order[:7], order[7:20], order[20:]]).to_json()
    shuffled = np.random.default_rng(1).permutation(order)
    reordered = build([shuffled[:11], shuffled[11:]]).to_json()
    assert whole == split == reordered


def test_review_sampling_is_input_order_independent() -> None:
    names = sorted(TEXTS)
    results = [analyze(TEXTS[n], len(TEXTS[n].encode())) for n in names]
    matrix = np.array([[np.nan if v is None else float(v) for v in r.values] for r in results])
    classes = np.array([CLASS_ORDER.index(r.doc_class) for r in results], dtype=np.int64)

    def sample(order: list[int], cuts: list[int]) -> dict[str, list[object]]:
        merged: dict[str, list[object]] = {}
        bounds = [0, *cuts, len(order)]
        for a, b in zip(bounds, bounds[1:], strict=False):
            idx = order[a:b]
            docs = ChunkDocs(
                path="p",
                rows=[i + 1 for i in idx],
                offsets=[i * 10 for i in idx],
                doc_ids=[f"d{i}" for i in idx],
                kept=[None] * len(idx),
                classes=classes[idx],
            )
            merge_samples(merged, sample_chunk(matrix[idx], docs))
        return merged

    order = list(range(len(names)))
    reversed_order = order[::-1]
    assert sample(order, []) == sample(reversed_order, [5, 17]) == sample(order, [3, 30])


def test_coarse_index_matches_bisect() -> None:
    from xlm.data.quality.policy import coarse_index

    spec_index = METRIC_INDEX["dup_line_byte_ratio"]
    values = np.full((5, len(METRICS)), np.nan)
    probe = [0.0, 0.001, 0.0099, 0.5, 1.0]
    values[:, spec_index] = probe
    got = coarse_matrix(values, spec_index).tolist()
    assert got == [coarse_index(METRICS[spec_index], p) for p in probe]


def test_policy_identity_is_stable_and_declares_no_thresholds() -> None:
    body = policy_body()
    assert body["status"] == "DIAGNOSTIC_DEFINITIONS_NOT_THRESHOLDS"
    assert policy_identity() == policy_identity()
    assert "patterns" in body and body["patterns"]["known_tag"]
