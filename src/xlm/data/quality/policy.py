"""Frozen, data-only detector policy for the Phase-A quality audit.

Everything here is a MEASUREMENT definition: what is counted, at which histogram
resolution, which review strata exist and which document-characteristic classes
are used to interpret detector precision. Nothing here is a removal threshold.
Constants that look like cutoffs (minimum comparison lengths, class rules, review
strata edges) only define what a metric or an interpretation class means; every
metric is also reported as a full distribution so that any later threshold is
chosen from the real data and its reviewed samples.

The policy identity (:func:`policy_identity`) digests every definition below,
including regex sources, so a resumed audit refuses a changed detector set.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Any, Literal

from xlm.data.evidence_v2 import canonical

POLICY_VERSION = "xlm-quality-audit-detectors-v3"
STATUS_PROPOSAL = "PROPOSAL_ONLY"
REVIEW_SEED = "xlm-quality-audit-review-v2"

Kind = Literal["count", "ratio", "real"]
Direction = Literal["high", "low", "none"]

# -- measurement definitions (not thresholds) -------------------------------------------

MIN_LINE_CHARS = 16  # whitespace-collapsed line length for duplicate-line comparison
MIN_PARAGRAPH_CHARS = 64  # whitespace-collapsed paragraph length for duplicate paragraphs
RUN_MIN = 8  # shortest same-character run that is recorded at all
NGRAM_SIZES = (5, 10)  # whitespace lexical units
NGRAM_MAX_WORDS = 100_000  # analysed word prefix for n-gram/lexical statistics
COMPRESSION_MIN_BYTES = 256
COMPRESSION_MAX_BYTES = 1024**2
ANCHOR_LINE_MAX_CHARS = 30  # menu/anchor-like line: short, few words, no terminal punct.
ANCHOR_LINE_MAX_WORDS = 3
FRAGMENT_LINE_CHARS = 40  # OCR fragmentation: non-empty line shorter than this
HEADER_LINE_MIN_CHARS = 16
HEADER_LINE_MAX_CHARS = 100
HEADER_MIN_REPEATS = 3
BOILERPLATE_LINE_MAX_CHARS = 160
CODE_MIN_LINES = 3

# Interpretation classes (precision analysis only; never training labels).
CLASS_ORDER = ("empty", "markup_like", "code_like", "math_table_like", "prose_like", "other")
CLASS_RULES: dict[str, Any] = {
    "empty": "zero characters or whitespace only",
    "markup_like": {"full_html_structure": True, "or_html_tag_char_ratio_at_least": 0.10},
    "code_like": {"code_line_ratio_at_least": 0.30, "nonempty_lines_at_least": CODE_MIN_LINES},
    "math_table_like": {
        "table_line_ratio_at_least": 0.30,
        "or_math_symbol_ratio_at_least": 0.03,
        "or_latex_commands_per_kchar_at_least": 5.0,
        "or_digit_ratio_at_least": 0.30,
    },
    "prose_like": {"alpha_ratio_at_least": 0.60, "words_or_chars_at_least": [5, 32]},
    "other": "none of the above",
}

COUNT_EDGES = (1, 2, 4, 8, 16, 32, 64, 128, 256, 1024, 4096)
SIZE_EDGES = (16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304)
RATIO_EDGES = (0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0)
COMPRESSION_EDGES = (0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0)


@dataclass(frozen=True)
class MetricSpec:
    """One per-document metric. ``direction`` says which tail is suspicious."""

    name: str
    dimension: str
    kind: Kind
    direction: Direction
    review: bool
    candidate: bool
    edges: tuple[float, ...]
    description: str


def _m(
    name: str,
    dimension: str,
    kind: Kind,
    direction: Direction,
    description: str,
    *,
    review: bool = False,
    candidate: bool = False,
    edges: tuple[float, ...] | None = None,
) -> MetricSpec:
    if edges is None:
        edges = RATIO_EDGES if kind == "ratio" else COUNT_EDGES
    return MetricSpec(name, dimension, kind, direction, review, candidate, edges, description)


METRICS: tuple[MetricSpec, ...] = (
    # A. basic statistics
    _m("utf8_bytes", "A", "count", "none", "canonical text UTF-8 bytes", edges=SIZE_EDGES),
    _m("chars", "A", "count", "low", "Unicode code points", edges=SIZE_EDGES),
    _m("lines", "A", "count", "none", "lines (a trailing newline adds no line)"),
    _m("nonempty_lines", "A", "count", "none", "lines that are not whitespace-only"),
    _m("max_line_chars", "A", "count", "high", "longest line in code points", edges=SIZE_EDGES),
    _m("mean_line_chars", "A", "real", "none", "mean stripped length of non-empty lines"),
    _m("whitespace_ratio", "A", "ratio", "high", "str.isspace characters / characters"),
    _m("alpha_ratio", "A", "ratio", "low", "str.isalpha characters / characters", review=True),
    _m("digit_ratio", "A", "ratio", "high", "str.isdigit characters / characters", review=True),
    _m("punct_ratio", "A", "ratio", "high", "Unicode P* characters / characters", review=True),
    _m("symbol_ratio", "A", "ratio", "high", "Unicode S* characters / characters", review=True),
    _m("non_ascii_ratio", "A", "ratio", "none", "code points >= U+0080 / characters"),
    _m("unique_chars", "A", "count", "low", "distinct code points"),
    # C. markup
    _m("html_tags", "C", "count", "high", "known HTML element tags", review=True),
    _m(
        "html_tag_char_ratio",
        "C",
        "ratio",
        "high",
        "characters inside known HTML element tags / characters",
        review=True,
        candidate=True,
    ),
    _m(
        "generic_tags",
        "C",
        "count",
        "high",
        "tag-shaped <name ...> tokens of any name, outside fenced code examples",
    ),
    _m(
        "code_example_tags",
        "C",
        "count",
        "none",
        "tag-shaped tokens inside code/example regions (fenced, indented, inline code)",
    ),
    _m("html_entities", "C", "count", "high", "HTML/XML character entities", review=True),
    _m("script_style_blocks", "C", "count", "high", "<script / <style openings"),
    # D. boilerplate / web junk
    _m(
        "boilerplate_lines",
        "D",
        "count",
        "high",
        "DISTINCT short lines (<=160 chars) with at least one boilerplate category match",
        review=True,
        candidate=True,
    ),
    _m(
        "boilerplate_phrase_hits",
        "D",
        "count",
        "high",
        "every boilerplate category match on short lines (one line may hold several)",
    ),
    _m("urls", "D", "count", "high", "http(s):// or www. links"),
    _m("url_char_ratio", "D", "ratio", "high", "characters inside links / characters", review=True),
    _m("domain_mentions", "D", "count", "high", "common-TLD domain suffixes"),
    _m(
        "anchor_line_ratio",
        "D",
        "ratio",
        "high",
        "menu/anchor-like lines / non-empty lines",
        review=True,
        candidate=True,
    ),
    # E. character repetition (0 means no run >= RUN_MIN)
    _m(
        "max_char_run",
        "E",
        "count",
        "high",
        "longest same-character run",
        review=True,
        candidate=True,
    ),
    _m("max_punct_run", "E", "count", "high", "longest punctuation/symbol run"),
    _m("max_space_run", "E", "count", "high", "longest whitespace run"),
    _m("max_alnum_run", "E", "count", "high", "longest letter/digit run", review=True),
    _m("max_other_run", "E", "count", "high", "longest run of any other code point"),
    _m(
        "repeated_char_ratio",
        "E",
        "ratio",
        "high",
        "characters inside runs >= RUN_MIN / characters",
        review=True,
        candidate=True,
    ),
    # F. line / paragraph repetition
    _m("dup_lines", "F", "count", "high", "repeat occurrences of eligible lines"),
    _m(
        "dup_line_byte_ratio",
        "F",
        "ratio",
        "high",
        "UTF-8 bytes of repeat line occurrences / document bytes",
        review=True,
        candidate=True,
    ),
    _m("top_line_count", "F", "count", "high", "occurrences of the most frequent eligible line"),
    _m(
        "top_line_byte_ratio",
        "F",
        "ratio",
        "high",
        "bytes of the most frequent eligible line x occurrences / document bytes",
        review=True,
    ),
    _m("unique_line_ratio", "F", "ratio", "low", "distinct / eligible lines"),
    _m("dup_paragraphs", "F", "count", "high", "repeat occurrences of eligible paragraphs"),
    _m(
        "dup_paragraph_byte_ratio",
        "F",
        "ratio",
        "high",
        "UTF-8 bytes of repeat paragraph occurrences / document bytes",
        review=True,
        candidate=True,
    ),
    # G. n-gram / template loops (heuristic)
    _m(
        "ngram5_excess_ratio",
        "G",
        "ratio",
        "high",
        "(word 5-gram positions - distinct 5-grams) / positions",
        review=True,
    ),
    _m(
        "ngram10_excess_ratio",
        "G",
        "ratio",
        "high",
        "(word 10-gram positions - distinct 10-grams) / positions",
        review=True,
        candidate=True,
    ),
    _m(
        "ngram10_top_share",
        "G",
        "ratio",
        "high",
        "occurrences of the most frequent 10-gram / positions",
        review=True,
    ),
    _m(
        "compression_ratio",
        "G",
        "ratio",
        "low",
        "zlib level-1 bytes / bytes of the first 1 MiB (>= 256 bytes only; clamped to 1)",
        review=True,
        candidate=True,
        edges=COMPRESSION_EDGES,
    ),
    # H. Unicode / control / encoding
    _m("nul", "H", "count", "high", "U+0000"),
    _m("c0_controls", "H", "count", "high", "C0 controls and DEL except LF and TAB", review=True),
    _m("c1_controls", "H", "count", "high", "U+0080..U+009F", review=True),
    _m("replacement_chars", "H", "count", "high", "U+FFFD", review=True, candidate=True),
    _m("zero_width", "H", "count", "high", "U+200B, U+2060, U+180E"),
    _m("zwj_zwnj", "H", "count", "none", "U+200C/U+200D (legitimate in emoji and scripts)"),
    _m("bom", "H", "count", "high", "U+FEFF anywhere in the text"),
    _m("bidi_controls", "H", "count", "high", "bidirectional format controls"),
    _m("private_use", "H", "count", "high", "private-use code points"),
    _m("noncharacters", "H", "count", "high", "Unicode noncharacters"),
    _m("surrogates", "H", "count", "high", "lone surrogate code points"),
    _m(
        "mojibake_hits",
        "H",
        "count",
        "high",
        "conservative UTF-8-as-Latin-1/cp1252 sequences",
        review=True,
        candidate=True,
    ),
    # I. OCR / PDF
    _m(
        "single_char_line_ratio",
        "I",
        "ratio",
        "high",
        "one-character lines / non-empty lines",
        review=True,
        candidate=True,
    ),
    _m(
        "fragment_line_ratio",
        "I",
        "ratio",
        "high",
        "non-empty lines shorter than 40 characters / non-empty lines",
        review=True,
    ),
    _m(
        "page_number_line_ratio",
        "I",
        "ratio",
        "high",
        "page-number-like lines / non-empty lines",
        review=True,
        candidate=True,
    ),
    _m(
        "repeated_header_ratio",
        "I",
        "ratio",
        "high",
        "occurrences of 16-100 char lines repeated >= 3 times / non-empty lines",
        review=True,
        candidate=True,
    ),
    _m(
        "hyphen_break_ratio",
        "I",
        "ratio",
        "high",
        "letter-hyphen-newline-letter breaks / non-empty lines",
        review=True,
    ),
    _m(
        "soft_break_ratio",
        "I",
        "ratio",
        "high",
        "lowercase/comma line ends continued in lowercase / newlines",
        review=True,
    ),
    # J. composition / lexical
    _m("words", "J", "count", "low", "whitespace lexical units", edges=SIZE_EDGES),
    _m("type_token_ratio", "J", "ratio", "low", "distinct / analysed words", review=True),
    _m("distinct_words", "J", "count", "low", "distinct analysed words"),
    _m("code_line_ratio", "J", "ratio", "none", "code-shaped lines / non-empty lines"),
    _m(
        "table_line_ratio",
        "J",
        "ratio",
        "none",
        "lines with >= 2 '|' that start or end with '|', or >= 2 tabs / non-empty",
    ),
    _m("math_symbol_ratio", "J", "ratio", "none", "fixed math-symbol set / characters"),
    _m("latex_commands", "J", "count", "none", "\\command tokens"),
)
METRIC_INDEX = {m.name: n for n, m in enumerate(METRICS)}

SIZE_FLAGS = (
    "empty",
    "whitespace_only",
    "chars_lt16",
    "chars_lt32",
    "chars_lt64",
    "chars_lt128",
    "chars_lt256",
    "bytes_gt64KiB",
    "bytes_gt256KiB",
    "bytes_gt1MiB",
    "bytes_gt4MiB",
)
MARKUP_FLAGS = (
    "has_html_doctype",
    "has_other_doctype",
    "has_html_open",
    "has_head",
    "has_body",
    "has_script",
    "has_style",
    "has_nav",
    "has_footer",
    "has_form",
    "has_closing_tag",
    "has_entity",
    "has_xml_decl",
    "has_html_comment",
    "markup_full_html",
    "markup_light",
    "markup_xml",
    "markup_ambiguous_code",
    "markup_code_example",
    "markup_any",
)
BOILERPLATE_CATEGORIES = (
    "cookie",
    "privacy",
    "terms",
    "subscribe",
    "account",
    "copyright",
    "navigation",
    "social",
)
BOILERPLATE_FLAGS = (*(f"bp_{c}" for c in BOILERPLATE_CATEGORIES), "boilerplate_any", "has_url")
SIGNAL_FLAGS = ("control_any", "truncated_analysis")
FLAGS: tuple[str, ...] = (*SIZE_FLAGS, *MARKUP_FLAGS, *BOILERPLATE_FLAGS, *SIGNAL_FLAGS)
FLAG_INDEX = {f: n for n, f in enumerate(FLAGS)}

# Boolean intersections: operands are flags, ``class:<name>`` or ``any:<count metric>``.
BOOL_INTERSECTIONS: tuple[tuple[str, str, str], ...] = (
    ("markup+boilerplate", "markup_any", "boilerplate_any"),
    ("full_html+boilerplate", "markup_full_html", "boilerplate_any"),
    ("markup+code_like", "markup_any", "class:code_like"),
    ("ambiguous_markup+code_like", "markup_ambiguous_code", "class:code_like"),
    ("url+tiny", "has_url", "chars_lt128"),
    ("control+prose_like", "control_any", "class:prose_like"),
    ("mojibake+prose_like", "any:mojibake_hits", "class:prose_like"),
    ("replacement+page_numbers", "any:replacement_chars", "any:page_number_line_ratio"),
)

# Threshold-free joint distributions on the coarse (review) edges of each metric.
JOINT_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("markup x boilerplate", "html_tag_char_ratio", "boilerplate_lines"),
    ("markup x code", "html_tag_char_ratio", "code_line_ratio"),
    ("10-gram repetition x lexical diversity", "ngram10_excess_ratio", "type_token_ratio"),
    ("duplicate lines x lexical diversity", "dup_line_byte_ratio", "type_token_ratio"),
    ("OCR single-char lines x control chars", "single_char_line_ratio", "c0_controls"),
    ("OCR fragmentation x replacement chars", "fragment_line_ratio", "replacement_chars"),
    ("URL density x size", "url_char_ratio", "chars"),
    ("symbols x code", "symbol_ratio", "code_line_ratio"),
    ("symbols x alphabetic", "symbol_ratio", "alpha_ratio"),
    ("character runs x character diversity", "repeated_char_ratio", "unique_chars"),
    ("mojibake x non-ASCII", "mojibake_hits", "non_ascii_ratio"),
    ("page numbers x repeated headers", "page_number_line_ratio", "repeated_header_ratio"),
    ("compression x size", "compression_ratio", "chars"),
)

# Language evidence already present in canonical metadata (no new classifier).
LANGUAGE_SCORE_KEYS = (
    "fasttext_english",
    "upstream_full_doc_lid_score",
    "upstream_page_average_lid_score",
)
LANGUAGE_LABEL_KEYS = ("upstream_full_doc_lid", "upstream_page_average_lid")
LANGUAGE_PROVENANCE_KEY = "language_provenance"

# Privacy: no source-derived free string ever reaches an artifact. Every categorical
# value is mapped onto a bounded vocabulary first; anything else is UNRECOGNIZED.
UNRECOGNIZED = "<unrecognized>"
# Language-code-shaped values only (``en``, ``eng``, ``eng_Latn``, ``zh-Hans``).
LANGUAGE_CODE_PATTERN = r"[a-z]{2,3}(?:[_-][A-Za-z]{4}|[_-][A-Z]{2})?"
SPLIT_VALUES = ("train", "diagnostic_val", "audit")
DOCUMENT_KIND_VALUES = ("prose", "code", "dialogue", "qa", "math", "story", "structured")
# SHA-256 of each exact Mix-01 adapter ``language_provenance`` literal -> category.
PROVENANCE_CATEGORIES = {
    "92f9274b236893a7d9b261b0cb6d1673bec1639f4af4903fbc51496e24976ace": ("view_english_registry"),
    "b833ad47ad43329802a7caf71cb633d1c4db9ed2c709389226ccd588113bf08c": (
        "essential_web_fasttext_preserved"
    ),
    "acc42903ef190bd6031e6f0419bc871e11d167fa7388703111f6fae703597ea4": (
        "ultrax_documented_english_no_row_field"
    ),
    "469b41954dc88346db8ca8a128acb6a8a1e96f8c5d45a33e886e864cb1ccb406": (
        "nemotron_wiki_rewrite_documented_english_no_row_field"
    ),
    "3a7872db8892faa3f0518ace9aa5cb504018558c29967f2226a697c24bafdd23": (
        "finepdfs_routing_label_eng_latn"
    ),
    "f9f3e55119356979d240cb7c5d79dc89670e775c0eb0d1c3259caf75901118b9": (
        "simple_stories_documented_english_no_row_field"
    ),
    "2c650d6b74d29c8b5c3b57e7b84aaafdc9e80e8d287ee039c36153c6da6bb309": (
        "common_pile_no_row_field_external_selection"
    ),
}
# Exact categorical maps are merged without caps (order independent); a map larger
# than this refuses deterministically (its final union decides). Artifacts publish
# the top values by (count desc, value asc) plus an exact ``<other>`` remainder.
CATEGORY_HARD_LIMIT = 100_000
PUBLISHED_CATEGORY_VALUES = 64

# Prior detector scope proposals per source type (PROPOSAL_ONLY; the real
# distribution and reviewed samples decide).
COMPONENT_SOURCE_TYPE = {
    "essential_science": "web",
    "essential_practical": "web",
    "essential_prose": "web",
    "ultrax_ultrafineweb": "web",
    "finepdfs_en": "pdf",
    "synth_en_explanations": "synthetic",
    "nemotron_wiki_rewrite": "synthetic",
    "ifm_behaviors_general_planning": "synthetic",
    "finewiki_en": "encyclopedic",
    "common_pile_prose": "mixed_public_domain",
    "simple_stories": "synthetic_stories",
}
DETECTOR_SCOPE = {
    "universal": ["empty", "whitespace_only", "H:nul/c0/c1/replacement/noncharacters", "E", "F"],
    "web_specific": ["C", "D"],
    "pdf_ocr_specific": ["I"],
    "prose_specific": ["alpha_ratio", "type_token_ratio", "symbol_ratio", "punct_ratio"],
    "protected_classes": {
        "code_like": ["C:generic_tags", "E:max_punct_run", "J:symbol/punct ratios"],
        "math_table_like": ["J:digit/symbol ratios", "I:fragment_line_ratio"],
    },
}

QUANTILES = (
    ("p50", 1, 2),
    ("p75", 3, 4),
    ("p90", 9, 10),
    ("p95", 19, 20),
    ("p99", 99, 100),
    ("p99.9", 999, 1000),
)
# Candidate bands: tail census at these component quantiles (PROPOSAL_ONLY).
CANDIDATE_BANDS = (("conservative", 999, 1000), ("moderate", 99, 100), ("aggressive", 19, 20))
CANDIDATE_RULE = (
    "high-is-suspicious: cut bin c = the bin holding the q-quantile, but never the clean "
    "bin 0; rule `value >= lo(c)`, impact = documents in bins >= c. low-is-suspicious: "
    "cut bin c = the bin holding the (1-q)-quantile, but never the clean top bin; rule "
    "`value < hi(c)` (or `value <= 1.0` never), impact = documents in bins <= c. Bins are "
    "half-open on exact edges, so the printed comparator and the impact agree exactly."
)
REVIEW_PER_STRATUM = 4


# -- histogram bins --------------------------------------------------------------------


def count_bin(value: int) -> int:
    """Exact below 16; above, 8 sub-bins per octave on exact integer boundaries."""
    if value < 16:
        return max(value, 0)
    octave = value.bit_length() - 1
    sub = ((value - (1 << octave)) << 3) >> octave
    return 16 + (octave - 4) * 8 + sub


def count_bin_bounds(index: int) -> tuple[int, int]:
    """Half-open integer range ``[lo, hi)`` of a count bin."""
    if index < 16:
        return index, index + 1
    octave, sub = (index - 16) // 8 + 4, (index - 16) % 8
    step = 1 << (octave - 3)
    return (1 << octave) + sub * step, (1 << octave) + (sub + 1) * step


# Exact float edges: bin k holds values v with RATIO_FINE_EDGES[k] <= v < edges[k+1];
# bin 1000 holds exactly 1.0. ``v >= edges[k]`` is therefore EXACTLY ``bin(v) >= k``
# for the very float printed as the cut (no ``int(v * 1000)`` rounding drift).
RATIO_FINE_EDGES = tuple(k / 1000 for k in range(1001))


def ratio_bin(value: float) -> int:
    """1000 bins on [0, 1) at the exact float edges ``k / 1000``; 1.0 is bin 1000."""
    return min(max(bisect_right(RATIO_FINE_EDGES, value) - 1, 0), 1000)


def ratio_bin_bounds(index: int) -> tuple[float, float]:
    if index >= 1000:
        return 1.0, 1.0
    return RATIO_FINE_EDGES[index], RATIO_FINE_EDGES[index + 1]


def metric_bin(spec: MetricSpec, value: float) -> int:
    if spec.kind == "ratio":
        return ratio_bin(value)
    return count_bin(int(value))


def bin_bounds(spec: MetricSpec, index: int) -> tuple[float, float]:
    if spec.kind == "ratio":
        return ratio_bin_bounds(index)
    return count_bin_bounds(index)


def coarse_index(spec: MetricSpec, value: float) -> int:
    return bisect_right(spec.edges, value)


def coarse_label(spec: MetricSpec, index: int) -> str:
    edges = spec.edges
    lo = "-inf" if index == 0 else repr(edges[index - 1])
    hi = "inf" if index >= len(edges) else repr(edges[index])
    return f"[{lo},{hi})"


# -- identity -------------------------------------------------------------------------


def policy_body() -> dict[str, Any]:
    from xlm.data.quality import detectors

    return {
        "version": POLICY_VERSION,
        "status": "DIAGNOSTIC_DEFINITIONS_NOT_THRESHOLDS",
        "constants": {
            "min_line_chars": MIN_LINE_CHARS,
            "min_paragraph_chars": MIN_PARAGRAPH_CHARS,
            "run_min": RUN_MIN,
            "ngram_sizes": list(NGRAM_SIZES),
            "ngram_max_words": NGRAM_MAX_WORDS,
            "compression_min_bytes": COMPRESSION_MIN_BYTES,
            "compression_max_bytes": COMPRESSION_MAX_BYTES,
            "anchor_line_max_chars": ANCHOR_LINE_MAX_CHARS,
            "anchor_line_max_words": ANCHOR_LINE_MAX_WORDS,
            "fragment_line_chars": FRAGMENT_LINE_CHARS,
            "header_line_chars": [HEADER_LINE_MIN_CHARS, HEADER_LINE_MAX_CHARS],
            "header_min_repeats": HEADER_MIN_REPEATS,
            "boilerplate_line_max_chars": BOILERPLATE_LINE_MAX_CHARS,
            "code_min_lines": CODE_MIN_LINES,
            "review_per_stratum": REVIEW_PER_STRATUM,
            "review_seed": REVIEW_SEED,
        },
        "class_order": list(CLASS_ORDER),
        "class_rules": CLASS_RULES,
        "metrics": [{**asdict(m), "edges": list(m.edges)} for m in METRICS],
        "flags": list(FLAGS),
        "bool_intersections": [list(x) for x in BOOL_INTERSECTIONS],
        "joint_pairs": [list(x) for x in JOINT_PAIRS],
        "language": {
            "score_keys": list(LANGUAGE_SCORE_KEYS),
            "label_keys": list(LANGUAGE_LABEL_KEYS),
            "provenance_key": LANGUAGE_PROVENANCE_KEY,
            "numeric": "dense 1001-bin histograms plus out-of-range and absent counts",
            "language_code_pattern": LANGUAGE_CODE_PATTERN,
            "split_values": list(SPLIT_VALUES),
            "document_kind_values": list(DOCUMENT_KIND_VALUES),
            "provenance_categories": dict(sorted(PROVENANCE_CATEGORIES.items())),
            "unrecognized": UNRECOGNIZED,
            "category_hard_limit": CATEGORY_HARD_LIMIT,
            "published_category_values": PUBLISHED_CATEGORY_VALUES,
        },
        "quantiles": [list(q) for q in QUANTILES],
        "candidate_bands": [list(b) for b in CANDIDATE_BANDS],
        "candidate_rule": CANDIDATE_RULE,
        "patterns": detectors.pattern_sources(),
        "histogram": {
            "count": "exact below 16; 8 sub-bins per octave on integer boundaries",
            "ratio": "bins [k/1000, (k+1)/1000) on exact float edges; 1.0 exact (bin 1000)",
            "real": "floor, then count bins",
        },
    }


def policy_identity() -> str:
    return canonical.digest(policy_body())
