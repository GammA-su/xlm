"""Authored fixtures for the Phase-B cleaning dry run (synthetic text only, no real corpus).

``cleaning_layout`` spreads targeted documents over the component names the v1 policy
singles out (finewiki_en, finepdfs_en, common_pile_prose, essential_science,
ultrax_ultrafineweb) plus filler prose, so every rule, review stratum and protected
case is exercised. ``TEST_CUTS`` are authored thresholds that tests substitute into a
genuinely frozen policy (re-digested) to make decisions exactly predictable;
``override_thresholds`` writes such a variant.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quality_fixtures import PROSE, TEXTS, document
from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning_policy import dump_yaml, parse_yaml

# Authored test cuts (not Phase-A values): comparator and cut per signal.
TEST_CUTS: dict[str, tuple[str, float | int]] = {
    "compression_ratio": ("<", 0.17),
    "ngram10_excess_ratio": (">=", 0.5),
    "dup_line_byte_ratio": (">=", 0.5),
    "dup_paragraph_byte_ratio": (">=", 0.5),
    "repeated_char_ratio": (">=", 0.3),
    "max_char_run": (">=", 64),
    "page_number_line_ratio": (">=", 0.2),
    "repeated_header_ratio": (">=", 0.2),
    "single_char_line_ratio": (">=", 0.3),
}


def unique_prose(n: int) -> str:
    return (
        f"{PROSE.replace('Rivers', f'Rivers of region {n}')} Survey {n} recorded "
        f"{n * 7 + 3} distinct measurements near station {n * 13 % 97}."
    )


def ocr_pages(distinct: bool, pages: int = 5) -> str:
    parts = []
    for n in range(pages):
        body = unique_prose(100 + n) if distinct else PROSE
        parts += ["Journal of Applied Things, Vol. 3", body, str(n + 1), ""]
    return "\n".join(parts)


def mojibake_text(hits: int) -> str:
    return " ".join(f"Note {n} says cafÃ© value {n * 31}." for n in range(hits))


def replacement_text(count: int) -> str:
    return " ".join(f"Line {n} lost � glyph {n * 17}." for n in range(count))


CODE = (
    "def read(path):\n    with open(path) as handle:\n        return handle.read()\n"
    "def write(path, data):\n    with open(path, 'w') as handle:\n        handle.write(data)\n"
)

DOCS: dict[str, dict[str, str]] = {
    "finewiki_en": {
        "rep_paragraphs": "\n\n".join([PROSE] * 5),
        "one_run": PROSE + "\n" + "=" * 80 + "\n" + unique_prose(1),
        "generated_loop": TEXTS["generated_loop"],
    },
    "finepdfs_en": {
        "ocr_repetition": ocr_pages(distinct=False),
        "ocr_review": ocr_pages(distinct=True),
        "ocr_one": "\n".join(f"{unique_prose(200 + n)}\n{n + 11}" for n in range(4)),
        "single_chars": TEXTS["single_char_lines"],
    },
    "common_pile_prose": {
        "char_spam": TEXTS["char_spam"],
        "accented_latin": TEXTS["accented_latin"],
        "japanese": TEXTS["japanese"],
        "arabic_hebrew": "مرحبا بالعالم، هذا نص عربي سليم. שלום עולם, זה טקסט עברי תקין.",
        "emoji": TEXTS["emoji"],
        "combining_marks": TEXTS["combining_marks"],
        "math_unicode": "For all x ∈ ℝ, ∑ aᵢ ≤ ∫ f(x) dx and √2 ≠ π; hence α ≥ β holds.",
        "format_marks": "zero​width, bom﻿inside, bidi‮evil‬ fine prose here.",
        "urls": TEXTS["url_list"] + unique_prose(3),
        "boilerplate": TEXTS["boilerplate"],
        "light_markup": TEXTS["light_markup"],
        "page_numbers": TEXTS["page_numbers"],
        "markdown_table": TEXTS["markdown_table"],
        "latex": TEXTS["latex_equation"],
    },
    "essential_science": {
        "replacement_8": replacement_text(8),
        "replacement_5": replacement_text(5),
        "mojibake_16": mojibake_text(16),
        "mojibake_10": mojibake_text(10),
        "both": replacement_text(8) + " " + mojibake_text(16),
        "replacement_control": replacement_text(8) + " bell\x07 here",
        "mojibake_c1": mojibake_text(16) + " next\u0085line",
        "control_only": "A stray bell\x07 in otherwise ordinary prose about rivers.",
        "code_two": CODE + "#" + "#" * 120 + "\n",
        "code_three": "\n\n".join([CODE] * 4),
        "table_two": "| a | b |\n|" + "-" * 70 + "|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n",
        "table_one": "| a | b |\n|"
        + "-" * 70
        + "|\n"
        + "".join(f"| {n} | {n * n} | {n * 3 + 1} |\n" for n in range(14)),
    },
    "ultrax_ultrafineweb": {
        "html_page": TEXTS["html_page"],
        "code_example": (
            "Use this snippet in a page:\n\n```html\n<!DOCTYPE html>\n<html><head></head>"
            "<body><p>Hi</p></body></html>\n```\n\nThen reload the page."
        ),
        "nul": "abc\x00def plain words after a NUL byte in prose.",
        "noncharacter": "A noncharacter ﷐ inside otherwise fine prose text.",
        "rep_paragraphs": "\n\n".join([unique_prose(9)] * 4),
        "mojibake_16": mojibake_text(16),
        "markup_repeat": "\n\n".join(
            ["<div class='item'><span>Buy cheap items today number one</span></div>"] * 6
        ),
    },
}


def cleaning_layout(filler: int = 12) -> dict[str, list[dict[str, Any]]]:
    """``component/view/name`` -> rows; targeted documents followed by filler prose."""
    layout: dict[str, list[dict[str, Any]]] = {}
    for component, docs in DOCS.items():
        rows = [
            document(f"{component}-{name}", text, n + 1)
            for n, (name, text) in enumerate(sorted(docs.items()))
        ]
        rows += [
            document(f"{component}-filler-{k}", unique_prose(1000 + k), len(rows) + k + 1)
            for k in range(filler)
        ]
        layout[f"{component}/default/a"] = rows
    return layout


def override_thresholds(
    frozen: Path,
    destination: Path,
    cuts: Mapping[str, tuple[str, float | int]] = TEST_CUTS,
    per_component: Mapping[str, Mapping[str, tuple[str, float | int]]] | None = None,
) -> Path:
    """A re-digested copy of a FROZEN policy with authored cuts (test-only variant)."""
    body = parse_yaml(frozen.read_bytes())
    for component, entry in body["thresholds"].items():
        chosen = {**cuts, **(per_component or {}).get(component, {})}
        for family in ("repetition", "ocr"):
            signals = entry[family]
            if signals is None:
                continue
            for metric in signals:
                comparator, cut = chosen[metric]
                signals[metric] = {
                    "comparator": comparator,
                    "cut": cut,
                    "cut_bin": 1,
                    "quantile_bin": 1,
                    "phase_a_rule_id": f"{component}.{metric}",
                    "phase_a_impact": {"docs": 0, "bytes": 0, "docs_pct": 0.0, "bytes_pct": 0.0},
                }
    body["digest"] = canonical.self_digest(body)
    destination.write_bytes(dump_yaml(body))
    return destination
