"""Per-document quality measurements (pure, bounded, content-free outputs).

:func:`analyze` returns numbers, flag indices and one interpretation class. It never
returns or retains document text. Every detector is a heuristic measurement; none
is a decision. Comparison representations (stripped / whitespace-collapsed lines and
paragraphs, whitespace word units) exist only inside this function and never alter
the source text.

Bounds: n-gram and lexical statistics read at most ``NGRAM_MAX_WORDS`` words and the
compression estimate at most ``COMPRESSION_MAX_BYTES``; when a bound applies the
``truncated_analysis`` flag is set. Everything else is linear in the document.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter
from operator import itemgetter
from typing import Any

import numpy as np

from xlm.data.quality.policy import (
    ANCHOR_LINE_MAX_CHARS,
    ANCHOR_LINE_MAX_WORDS,
    BOILERPLATE_CATEGORIES,
    BOILERPLATE_LINE_MAX_CHARS,
    CODE_MIN_LINES,
    COMPRESSION_MAX_BYTES,
    COMPRESSION_MIN_BYTES,
    FLAG_INDEX,
    FRAGMENT_LINE_CHARS,
    HEADER_LINE_MAX_CHARS,
    HEADER_LINE_MIN_CHARS,
    HEADER_MIN_REPEATS,
    METRIC_INDEX,
    METRICS,
    MIN_LINE_CHARS,
    MIN_PARAGRAPH_CHARS,
    NGRAM_MAX_WORDS,
    RUN_MIN,
)

# -- character classes -------------------------------------------------------------------

ALPHA, DIGIT, SPACE, PUNCT, SYMBOL, NONASCII, MATH = 1, 2, 4, 8, 16, 32, 64
NUL, C0, C1, FFFD, ZW, ZWJ, BOM, BIDI, PUA, NONCHAR, SURR = (1 << n for n in range(7, 18))

# Unambiguous math operators only: ASCII = + < > ^ ~ also mean code, markup and prose.
MATH_CHARS = frozenset("±×÷−∑∏∫√∞≤≥≠≈≡∝∂∇∈∉∋⊂⊃⊆⊇∪∩∀∃∄→←↔⇒⇐⇔∧∨¬⊕⊗⋅∘′″")
ZERO_WIDTH = frozenset("\u200b\u2060\u180e")
ZWJ_ZWNJ = frozenset("\u200c\u200d")
BIDI_CONTROLS = frozenset(
    "\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
MOJIBAKE_LEADS = frozenset("ÃÂâïÐÑ")

_CLASS_CACHE: dict[str, int] = {}


def char_mask(ch: str) -> int:
    """Bit mask of every class the code point belongs to (cached per worker)."""
    mask = _CLASS_CACHE.get(ch)
    if mask is not None:
        return mask
    code = ord(ch)
    mask = 0
    if ch.isalpha():
        mask |= ALPHA
    if ch.isdigit():
        mask |= DIGIT
    if ch.isspace():
        mask |= SPACE
    category = unicodedata.category(ch)
    if category[0] == "P":
        mask |= PUNCT
    elif category[0] == "S":
        mask |= SYMBOL
    if code >= 0x80:
        mask |= NONASCII
    if ch in MATH_CHARS:
        mask |= MATH
    if code == 0:
        mask |= NUL
    elif (code < 0x20 and ch not in "\n\t") or code == 0x7F:
        mask |= C0
    elif 0x80 <= code <= 0x9F:
        mask |= C1
    if code == 0xFFFD:
        mask |= FFFD
    if ch in ZERO_WIDTH:
        mask |= ZW
    if ch in ZWJ_ZWNJ:
        mask |= ZWJ
    if code == 0xFEFF:
        mask |= BOM
    if ch in BIDI_CONTROLS:
        mask |= BIDI
    if category == "Co":
        mask |= PUA
    if 0xFDD0 <= code <= 0xFDEF or (code & 0xFFFE) == 0xFFFE:
        mask |= NONCHAR
    if category == "Cs":
        mask |= SURR
    _CLASS_CACHE[ch] = mask
    return mask


# -- patterns -----------------------------------------------------------------------------

HTML_ELEMENTS = (
    "html|head|body|title|meta|link|script|style|nav|footer|header|form|input|button|"
    "div|span|p|a|ul|ol|li|dl|dt|dd|table|tr|td|th|thead|tbody|tfoot|br|hr|img|iframe|"
    "section|article|aside|main|h[1-6]|strong|em|b|i|u|small|sup|sub|label|select|option|"
    "textarea|noscript|svg|figure|figcaption|blockquote|pre|code|center|font|tt|abbr|cite"
)
# A closing tag of a known element anywhere (``word</b>`` is never C++), or an opening
# tag of a known element NOT preceded by a word character or '<' (keeps ``vector<a>``,
# ``x<b>`` and ``a<<b`` out), within one line.
KNOWN_TAG_RE = re.compile(
    r"</(?:" + HTML_ELEMENTS + r")\s*>"
    r"|(?<![\w<])<(?:" + HTML_ELEMENTS + r")(?=[\s/>])[^<>\n]{0,256}>",
    re.IGNORECASE,
)
GENERIC_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]{0,31}(?:\s[^<>\n]{0,256})?/?>")
STRUCTURE_RE = re.compile(
    r"<(!doctype|\?xml|html|head|body|script|style|nav|footer|form)(?=[\s>/])",
    re.IGNORECASE,
)
CLOSING_RE = re.compile(r"</(?:" + HTML_ELEMENTS + r")\s*>", re.IGNORECASE)
ENTITY_RE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6});")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'\]\)]{1,2048}", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\.(?:com|org|net|edu|gov|io|info|biz)\b", re.IGNORECASE)
# Boilerplate phrase categories: a literal keyword gate on the lower-cased short lines,
# then one category regex. Bounded: a handful of categories, no phrase dictionary.
BOILERPLATE_PHRASES: tuple[tuple[str, tuple[str, ...], re.Pattern[str]], ...] = (
    (
        "cookie",
        ("cookie",),
        re.compile(
            r"\bcookies?\b.{0,60}?\b(?:accept|consent|policy|settings|preferences)\b"
            r"|\b(?:accept|allow|reject|manage)\b.{0,60}?\bcookies?\b"
            r"|\b(?:this|our)\s+(?:site|website)\s+uses\s+cookies\b"
        ),
    ),
    (
        "privacy",
        ("privacy",),
        re.compile(r"\bprivacy\s+(?:policy|notice|statement|settings|preferences)\b"),
    ),
    (
        "terms",
        ("terms",),
        re.compile(r"\bterms\s+(?:of\s+(?:use|service)|and\s+conditions|&\s+conditions)\b"),
    ),
    (
        "subscribe",
        ("subscri", "newsletter", "sign up for"),
        re.compile(r"\b(?:subscribe|unsubscribe|newsletter)\b|\bsign\s+up\s+for\s+(?:our|the)\b"),
    ),
    (
        "copyright",
        ("©", "(c)", "copyright", "rights reserved"),
        re.compile(r"(?:©|\(c\)|\bcopyright\b)\s*(?:\d{4}|by\b)|\ball\s+rights\s+reserved\b"),
    ),
    (
        "social",
        ("share on", "share this", "follow us", "tweet this"),
        re.compile(r"\b(?:share\s+(?:on|this)|follow\s+us|tweet\s+this)\b"),
    ),
)
# Whole-line menu items (after lower-casing, trimming decoration, collapsing spaces).
BOILERPLATE_LINES: dict[str, str] = {
    **dict.fromkeys(
        (
            "login",
            "log in",
            "logout",
            "log out",
            "sign in",
            "signin",
            "sign up",
            "signup",
            "sign out",
            "register",
            "my account",
            "create account",
            "create an account",
            "forgot password",
            "forgot your password",
        ),
        "account",
    ),
    **dict.fromkeys(
        (
            "home",
            "about",
            "about us",
            "contact",
            "contact us",
            "menu",
            "search",
            "skip to content",
            "skip to main content",
            "back to top",
            "read more",
            "click here",
            "next",
            "previous",
            "prev",
            "sitemap",
            "faq",
            "help",
        ),
        "navigation",
    ),
    **dict.fromkeys(
        (
            "facebook",
            "twitter",
            "linkedin",
            "pinterest",
            "instagram",
            "whatsapp",
            "reddit",
            "youtube",
        ),
        "social",
    ),
}
MENU_LINE_MAX_CHARS = 40
MENU_STRIP = " \t|•·*#>»«<-–—:;.,!?()[]{}/\\\"'"
PARAGRAPH_RE = re.compile(r"\n[ \t]*\n")
PAGE_NUMBER_RE = re.compile(
    r"^[ \t]*(?:(?:page|p\.)[ \t]*)?(?:\d{1,4}|[ivxlcdm]{1,7})"
    r"(?:[ \t]*(?:of|/)[ \t]*\d{1,4})?[ \t]*$"
    r"|^[ \t]*[-–—][ \t]*\d{1,4}[ \t]*[-–—][ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
HYPHEN_BREAK_RE = re.compile(r"[^\W\d_]-\n[ \t]*[^\W\d_]")
SOFT_BREAK_RE = re.compile(r"[a-z,;]\n[a-z]")
LATEX_RE = re.compile(r"\\[A-Za-z]{2,}")
MOJIBAKE_RE = re.compile(
    "[ÃÂÐÑ][\u0080-¿]|â[\u0080-\u009f]|â€[\u0080-¿ŒœŠšŸŽžƒˆ˜–—‘-„†-•…‰‹›€™]|ï»¿"
)
CODE_PREFIXES = (
    "#include",
    "#define",
    "#!",
    "import ",
    "from ",
    "def ",
    "class ",
    "function",
    "return",
    "public ",
    "private ",
    "protected ",
    "static ",
    "const ",
    "let ",
    "var ",
    "fn ",
    "func ",
    "if (",
    "if(",
    "for (",
    "for(",
    "while (",
    "while(",
    "else",
    "}",
    "{",
    "//",
    "/*",
    "*/",
    "<?php",
    "print(",
    "console.",
)
CODE_SUFFIXES = (";", "{", "}")
TERMINAL_PUNCT = ".!?:;"


def pattern_sources() -> dict[str, Any]:
    """Every detector definition that is not a number, for the policy identity."""
    return {
        "known_tag": KNOWN_TAG_RE.pattern,
        "generic_tag": GENERIC_TAG_RE.pattern,
        "structure": STRUCTURE_RE.pattern,
        "closing": CLOSING_RE.pattern,
        "entity": ENTITY_RE.pattern,
        "url": URL_RE.pattern,
        "domain": DOMAIN_RE.pattern,
        "boilerplate_phrases": [[c, list(k), p.pattern] for c, k, p in BOILERPLATE_PHRASES],
        "boilerplate_lines": sorted(BOILERPLATE_LINES.items()),
        "menu_line": [MENU_LINE_MAX_CHARS, MENU_STRIP],
        "run": "probe ch*RUN_MIN, then escaped ch{RUN_MIN,}",
        "dense_code_limit": DENSE_CODE_LIMIT,
        "paragraph": PARAGRAPH_RE.pattern,
        "page_number": PAGE_NUMBER_RE.pattern,
        "hyphen_break": HYPHEN_BREAK_RE.pattern,
        "soft_break": SOFT_BREAK_RE.pattern,
        "latex": LATEX_RE.pattern,
        "mojibake": MOJIBAKE_RE.pattern,
        "math_chars": "".join(sorted(MATH_CHARS)),
        "zero_width": sorted(ZERO_WIDTH),
        "zwj_zwnj": sorted(ZWJ_ZWNJ),
        "bidi": sorted(BIDI_CONTROLS),
        "code_prefixes": list(CODE_PREFIXES),
        "code_suffixes": list(CODE_SUFFIXES),
        "terminal_punct": TERMINAL_PUNCT,
    }


# -- analysis -----------------------------------------------------------------------------

M = METRIC_INDEX
F = FLAG_INDEX
METRIC_COUNT = len(METRICS)
_BP_FLAG = {c: F[f"bp_{c}"] for c in BOILERPLATE_CATEGORIES}


ZERO_WHEN_EMPTY = (
    "nonempty_lines",
    "html_tags",
    "generic_tags",
    "html_entities",
    "script_style_blocks",
    "boilerplate_lines",
    "urls",
    "domain_mentions",
    "dup_lines",
    "top_line_count",
    "dup_paragraphs",
    "mojibake_hits",
    "latex_commands",
    "distinct_words",
)


class Analysis:
    """Numbers only: metric values (``None`` = not applicable), flag indices, class."""

    __slots__ = ("values", "flags", "doc_class")

    def __init__(self, values: list[float | int | None], flags: list[int], doc_class: str):
        self.values = values
        self.flags = flags
        self.doc_class = doc_class


DENSE_CODE_LIMIT = 0x3000


def char_counts(text: str) -> dict[str, int]:
    """Exact code-point counts: ``bincount`` for small code points, else ``unique``."""
    if not text:
        return {}
    codes = np.frombuffer(text.encode("utf-32-le", "surrogatepass"), dtype="<u4")
    if int(codes.max()) < DENSE_CODE_LIMIT:
        dense = np.bincount(codes)
        present = np.flatnonzero(dense)
        values: list[int] = present.tolist()
        numbers: list[int] = dense[present].tolist()
    else:
        unique_codes, unique_counts = np.unique(codes, return_counts=True)
        values, numbers = unique_codes.tolist(), unique_counts.tolist()
    return {chr(code): n for code, n in zip(values, numbers, strict=True)}


def _run_class(mask: int) -> str:
    if mask & SPACE:
        return "space"
    if mask & (PUNCT | SYMBOL):
        return "punct"
    if mask & (ALPHA | DIGIT):
        return "alnum"
    return "other"


def analyze(text: str, utf8_bytes: int) -> Analysis:
    """Measure one canonical document. ``utf8_bytes`` is its verified UTF-8 size."""
    v: list[float | int | None] = [None] * METRIC_COUNT
    flags: list[int] = []
    chars = len(text)
    v[M["utf8_bytes"]] = utf8_bytes
    v[M["chars"]] = chars

    # Character classes: one vectorized count, then per distinct code point.
    by_mask: dict[int, int] = {}
    counts = char_counts(text)
    unique = len(counts)
    for ch, n in counts.items():
        mask = _CLASS_CACHE.get(ch)
        if mask is None:
            mask = char_mask(ch)
        by_mask[mask] = by_mask.get(mask, 0) + n
    totals = dict.fromkeys(
        (ALPHA, DIGIT, SPACE, PUNCT, SYMBOL, NONASCII, MATH)
        + (NUL, C0, C1, FFFD, ZW, ZWJ, BOM, BIDI, PUA, NONCHAR, SURR),
        0,
    )
    for mask, n in by_mask.items():
        for bit in totals:
            if mask & bit:
                totals[bit] += n
    v[M["unique_chars"]] = unique
    for name, bit in (
        ("nul", NUL),
        ("c0_controls", C0),
        ("c1_controls", C1),
        ("replacement_chars", FFFD),
        ("zero_width", ZW),
        ("zwj_zwnj", ZWJ),
        ("bom", BOM),
        ("bidi_controls", BIDI),
        ("private_use", PUA),
        ("noncharacters", NONCHAR),
        ("surrogates", SURR),
    ):
        v[M[name]] = totals[bit]
    if totals[NUL] or totals[C0] or totals[C1] or totals[FFFD]:
        flags.append(F["control_any"])

    # Size buckets (cumulative "<" in code points; ">" in UTF-8 bytes).
    whitespace_only = chars > 0 and totals[SPACE] == chars
    if chars == 0:
        flags.append(F["empty"])
    elif whitespace_only:
        flags.append(F["whitespace_only"])
    for limit in (16, 32, 64, 128, 256):
        if chars < limit:
            flags.append(F[f"chars_lt{limit}"])
    for limit, label in ((64, "64KiB"), (256, "256KiB"), (1024, "1MiB"), (4096, "4MiB")):
        if utf8_bytes > limit * 1024:
            flags.append(F[f"bytes_gt{label}"])

    words = text.split()
    word_count = len(words)
    v[M["words"]] = word_count

    if chars == 0 or whitespace_only:
        lines_all = text.split("\n") if chars else []
        if lines_all and lines_all[-1] == "":
            lines_all.pop()
        v[M["lines"]] = len(lines_all)
        v[M["max_line_chars"]] = max(map(len, lines_all), default=0)
        for name in ZERO_WHEN_EMPTY:
            v[M[name]] = 0
        _runs(text, counts, v)
        if whitespace_only:
            v[M["whitespace_ratio"]] = 1.0
        return Analysis(v, flags, "empty")

    inv = 1.0 / chars
    v[M["whitespace_ratio"]] = totals[SPACE] * inv
    v[M["alpha_ratio"]] = totals[ALPHA] * inv
    v[M["digit_ratio"]] = totals[DIGIT] * inv
    v[M["punct_ratio"]] = totals[PUNCT] * inv
    v[M["symbol_ratio"]] = totals[SYMBOL] * inv
    v[M["non_ascii_ratio"]] = totals[NONASCII] * inv
    v[M["math_symbol_ratio"]] = totals[MATH] * inv

    # Lines.
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    v[M["lines"]] = len(lines)
    v[M["max_line_chars"]] = max(map(len, lines), default=0)
    stripped = [s for s in (line.strip() for line in lines) if s]
    nonempty = len(stripped)
    v[M["nonempty_lines"]] = nonempty
    lengths = list(map(len, stripped))
    v[M["mean_line_chars"]] = sum(lengths) / nonempty if nonempty else 0.0
    if nonempty:
        ninv = 1.0 / nonempty
        v[M["single_char_line_ratio"]] = lengths.count(1) * ninv
        v[M["fragment_line_ratio"]] = sum(1 for n in lengths if n < FRAGMENT_LINE_CHARS) * ninv
        anchors = sum(
            1
            for s in stripped
            if len(s) <= ANCHOR_LINE_MAX_CHARS
            and s[-1] not in TERMINAL_PUNCT
            and s.count(" ") < ANCHOR_LINE_MAX_WORDS
        )
        v[M["anchor_line_ratio"]] = anchors * ninv
        code_lines = sum(
            1 for s in stripped if s.endswith(CODE_SUFFIXES) or s.startswith(CODE_PREFIXES)
        )
        v[M["code_line_ratio"]] = code_lines * ninv
        if "|" in text or "\t" in text:
            tables = sum(
                1
                for s in stripped
                if (s.count("|") >= 2 and (s[0] == "|" or s[-1] == "|")) or s.count("\t") >= 2
            )
        else:
            tables = 0
        v[M["table_line_ratio"]] = tables * ninv
    else:
        code_lines = 0

    # Exact duplicate lines on a whitespace-collapsed comparison form.
    eligible = [c for c in (" ".join(s.split()) for s in stripped) if len(c) >= MIN_LINE_CHARS]
    line_counts = Counter(eligible)
    if eligible:
        v[M["dup_lines"]] = len(eligible) - len(line_counts)
        dup_bytes = 0
        header_occurrences = 0
        for line, count in line_counts.items():
            if count > 1:
                dup_bytes += (count - 1) * len(line.encode("utf-8"))
                if count >= HEADER_MIN_REPEATS and (
                    HEADER_LINE_MIN_CHARS <= len(line) <= HEADER_LINE_MAX_CHARS
                ):
                    header_occurrences += count
        top_line, top_count = max(line_counts.items(), key=itemgetter(1))
        v[M["top_line_count"]] = top_count
        v[M["dup_line_byte_ratio"]] = min(1.0, dup_bytes / utf8_bytes)
        v[M["top_line_byte_ratio"]] = min(
            1.0, top_count * len(top_line.encode("utf-8")) / utf8_bytes
        )
        v[M["unique_line_ratio"]] = len(line_counts) / len(eligible)
        v[M["repeated_header_ratio"]] = header_occurrences / nonempty
    else:
        v[M["dup_lines"]] = 0
        v[M["top_line_count"]] = 0
        v[M["dup_line_byte_ratio"]] = 0.0
        v[M["top_line_byte_ratio"]] = 0.0
        v[M["repeated_header_ratio"]] = 0.0

    # Duplicate paragraphs (blank-line separated, whitespace-collapsed).
    if "\n" in text:
        paragraphs = [
            c
            for c in (" ".join(p.split()) for p in PARAGRAPH_RE.split(text))
            if len(c) >= MIN_PARAGRAPH_CHARS
        ]
    else:
        collapsed = " ".join(words)
        paragraphs = [collapsed] if len(collapsed) >= MIN_PARAGRAPH_CHARS else []
    paragraph_counts = Counter(paragraphs)
    v[M["dup_paragraphs"]] = len(paragraphs) - len(paragraph_counts)
    dup_paragraph_bytes = sum(
        (count - 1) * len(p.encode("utf-8")) for p, count in paragraph_counts.items() if count > 1
    )
    v[M["dup_paragraph_byte_ratio"]] = min(1.0, dup_paragraph_bytes / utf8_bytes)

    # Character runs.
    _runs(text, counts, v)

    # Markup.
    html_tags = generic_tags = entities = script_style = 0
    tag_chars = 0
    if "<" in text:
        for match in KNOWN_TAG_RE.finditer(text):
            html_tags += 1
            tag_chars += match.end() - match.start()
        generic_tags = sum(1 for _ in GENERIC_TAG_RE.finditer(text))
        seen: set[str] = set()
        for match in STRUCTURE_RE.finditer(text):
            key = match.group(1).lower().rstrip()
            seen.add(key)
            if key in ("script", "style"):
                script_style += 1
        structure = {
            "!doctype": "has_doctype",
            "?xml": "has_xml_decl",
            "html": "has_html_open",
            "head": "has_head",
            "body": "has_body",
            "script": "has_script",
            "style": "has_style",
            "nav": "has_nav",
            "footer": "has_footer",
            "form": "has_form",
        }
        for key, flag in structure.items():
            if key in seen:
                flags.append(F[flag])
        if CLOSING_RE.search(text):
            flags.append(F["has_closing_tag"])
        if "<!--" in text:
            flags.append(F["has_html_comment"])
        full = "!doctype" in seen or "html" in seen or ("head" in seen and "body" in seen)
    else:
        full = False
    if "&" in text:
        entities = sum(1 for _ in ENTITY_RE.finditer(text))
        if entities:
            flags.append(F["has_entity"])
    v[M["html_tags"]] = html_tags
    v[M["generic_tags"]] = generic_tags
    v[M["html_entities"]] = entities
    v[M["script_style_blocks"]] = script_style
    tag_ratio = tag_chars * inv
    v[M["html_tag_char_ratio"]] = tag_ratio
    if full:
        flags.append(F["markup_full_html"])
    elif html_tags or entities:
        flags.append(F["markup_light"])
    if generic_tags > html_tags:
        flags.append(F["markup_ambiguous_code"])
    if full or html_tags or entities or {F["has_xml_decl"], F["has_html_comment"]} & set(flags):
        flags.append(F["markup_any"])

    # Boilerplate / links (short lines only for phrase categories).
    bp_lines = 0
    categories: set[str] = set()
    short = "\n".join(s for s in stripped if len(s) <= BOILERPLATE_LINE_MAX_CHARS).lower()
    if short:
        for category, keywords, pattern in BOILERPLATE_PHRASES:
            if any(k in short for k in keywords):
                hits = sum(1 for _ in pattern.finditer(short))
                if hits:
                    bp_lines += hits
                    categories.add(category)
        for s in stripped:
            if len(s) <= MENU_LINE_MAX_CHARS:
                menu = BOILERPLATE_LINES.get(" ".join(s.lower().strip(MENU_STRIP).split()))
                if menu is not None:
                    bp_lines += 1
                    categories.add(menu)
    for category in sorted(categories):
        flags.append(_BP_FLAG[category])
    if categories:
        flags.append(F["boilerplate_any"])
    v[M["boilerplate_lines"]] = bp_lines
    urls = url_chars = 0
    if "://" in text or "www." in text or "WWW." in text:
        for match in URL_RE.finditer(text):
            urls += 1
            url_chars += match.end() - match.start()
    if urls:
        flags.append(F["has_url"])
    v[M["urls"]] = urls
    v[M["url_char_ratio"]] = url_chars * inv
    v[M["domain_mentions"]] = sum(1 for _ in DOMAIN_RE.finditer(text))

    # OCR / PDF layout.
    if nonempty:
        v[M["page_number_line_ratio"]] = sum(1 for _ in PAGE_NUMBER_RE.finditer(text)) / nonempty
        hyphen = sum(1 for _ in HYPHEN_BREAK_RE.finditer(text)) if "-\n" in text else 0
        v[M["hyphen_break_ratio"]] = min(1.0, hyphen / nonempty)
    newlines = text.count("\n")
    v[M["soft_break_ratio"]] = (
        sum(1 for _ in SOFT_BREAK_RE.finditer(text)) / newlines if newlines else 0.0
    )

    # Encoding damage (conservative sequences only).
    mojibake = 0
    if totals[NONASCII] and not MOJIBAKE_LEADS.isdisjoint(counts):
        mojibake = sum(1 for _ in MOJIBAKE_RE.finditer(text))
    v[M["mojibake_hits"]] = mojibake

    # Lexical / n-gram loops (bounded word prefix; heuristic).
    truncated = word_count > NGRAM_MAX_WORDS
    analysed = words[:NGRAM_MAX_WORDS] if truncated else words
    total = len(analysed)
    if total:
        distinct = len(set(analysed))
        v[M["distinct_words"]] = distinct
        v[M["type_token_ratio"]] = distinct / total
    else:
        v[M["distinct_words"]] = 0
    if total >= 5:
        positions = total - 4
        five = set(zip(*(analysed[k:] for k in range(5)), strict=False))
        v[M["ngram5_excess_ratio"]] = (positions - len(five)) / positions
    if total >= 10:
        positions = total - 9
        ten = Counter(zip(*(analysed[k:] for k in range(10)), strict=False))
        v[M["ngram10_excess_ratio"]] = (positions - len(ten)) / positions
        v[M["ngram10_top_share"]] = max(ten.values()) / positions
    if utf8_bytes >= COMPRESSION_MIN_BYTES:
        raw = text.encode("utf-8")
        if len(raw) > COMPRESSION_MAX_BYTES:
            raw = raw[:COMPRESSION_MAX_BYTES]
            truncated = True
        v[M["compression_ratio"]] = min(1.0, len(zlib.compress(raw, 1)) / len(raw))
    if truncated:
        flags.append(F["truncated_analysis"])

    latex = sum(1 for _ in LATEX_RE.finditer(text)) if "\\" in text else 0
    v[M["latex_commands"]] = latex

    doc_class = _classify(v, full, tag_ratio, code_lines, nonempty, latex, chars, word_count)
    return Analysis(v, flags, doc_class)


_RUN_PATTERNS: dict[str, re.Pattern[str]] = {}


def _run_pattern(ch: str) -> re.Pattern[str]:
    pattern = _RUN_PATTERNS.get(ch)
    if pattern is None:
        pattern = re.compile(re.escape(ch) + "{" + str(RUN_MIN) + ",}")
        _RUN_PATTERNS[ch] = pattern
    return pattern


def _runs(text: str, counts: dict[str, int], v: list[float | int | None]) -> None:
    """Same-character runs >= RUN_MIN: probe only code points that could form one."""
    best = {"space": 0, "punct": 0, "alnum": 0, "other": 0}
    covered = 0
    for ch, n in counts.items():
        if n < RUN_MIN or ch * RUN_MIN not in text:
            continue
        mask = _CLASS_CACHE.get(ch)
        if mask is None:
            mask = char_mask(ch)
        kind = _run_class(mask)
        for match in _run_pattern(ch).finditer(text):
            length = match.end() - match.start()
            covered += length
            if length > best[kind]:
                best[kind] = length
    v[M["max_char_run"]] = max(best.values())
    v[M["max_space_run"]] = best["space"]
    v[M["max_punct_run"]] = best["punct"]
    v[M["max_alnum_run"]] = best["alnum"]
    v[M["max_other_run"]] = best["other"]
    v[M["repeated_char_ratio"]] = covered / len(text) if text else None


def _classify(
    v: list[float | int | None],
    full: bool,
    tag_ratio: float,
    code_lines: int,
    nonempty: int,
    latex: int,
    chars: int,
    words: int,
) -> str:
    """Broad document-characteristic class for interpreting detector precision."""
    if full or tag_ratio >= 0.10:
        return "markup_like"
    if nonempty >= CODE_MIN_LINES and code_lines / nonempty >= 0.30:
        return "code_like"
    table = v[M["table_line_ratio"]] or 0.0
    math = v[M["math_symbol_ratio"]] or 0.0
    digits = v[M["digit_ratio"]] or 0.0
    if table >= 0.30 or math >= 0.03 or latex * 1000 / chars >= 5.0 or digits >= 0.30:
        return "math_table_like"
    if (v[M["alpha_ratio"]] or 0.0) >= 0.60 and (words >= 5 or chars >= 32):
        return "prose_like"
    return "other"
