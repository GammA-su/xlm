"""Bounded repetition and n-gram loop detection filter adhering to C03 and Amendment 6."""

from __future__ import annotations

import collections
import itertools
import re
import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult


class RepetitionConfig(StrictConfigModel):
    """Configuration for bounded repetition filtering."""

    max_duplicate_line_ratio: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Maximum allowed ratio of duplicate line characters to total characters.",
    )
    max_line_frequency: int = Field(
        default=10,
        gt=0,
        description="Maximum allowed occurrences of any identical non-empty line.",
    )
    max_char_run: int = Field(
        default=50,
        gt=0,
        description="Maximum allowed consecutive repetition of any single character.",
    )
    max_ngram_repetition_ratio: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Maximum allowed fraction of 5-grams that appear more than once.",
    )
    max_analysis_chars: int = Field(
        default=50_000,
        gt=0,
        description="Maximum prefix length analyzed for n-grams to bound compute.",
    )


def is_structural_separator_line(line: str) -> bool:
    """Return True for Markdown structural separator lines carrying no prose content.

    Recognizes only content-free separator syntax:

    - horizontal rules / dividers consisting solely of ``-``, ``*``, ``_`` or
      ``=`` (3+ markers, optional surrounding whitespace), e.g. ``---``,
      ``************``, ``___`` or a setext ``===`` underline;
    - Markdown table delimiter rows built only from ``|``, ``-``, ``:``,
      ``+``, ``=`` and whitespace and containing at least one ``-``/``=``
      span, e.g. ``|---|---|`` or
      ``|--------|-------|----------|----------------------------------|``.

    Any line containing letters, digits or other punctuation (headings, table
    headers and body rows, list items, prose) is NOT structural, so repeated
    prose, headings, navigation loops and OCR loops remain detectable.
    """
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    # Every accepted grammar uses only these markers. Ordinary prose can fail
    # before constructing a set of all characters in a potentially huge line.
    if stripped[0] not in "-*_=|:+":
        return False
    compact = stripped.replace(" ", "").replace("\t", "")
    if len(compact) < 3:
        return False
    chars = set(compact)
    if chars <= {"-"} or chars <= {"*"} or chars <= {"_"} or chars <= {"="}:
        return True
    if "|" in compact and chars <= {"|", "-", ":", "+", "="} and ("-" in compact or "=" in compact):
        return True
    return False


class RepetitionFilter(BaseTransform):
    """Detects and rejects documents containing excessive repeated lines, loops, or spans.

    Adheres strictly to Amendment 6:
    - Bounded n-gram and line analysis capping text evaluated to max_analysis_chars.
    - Tracks line repetition ratio, single-line frequency, character runs, and 5-gram loops.
    - Pure filter: does not mutate text (mutates_text = False).

    Structural Markdown separator lines (horizontal rules, table delimiter rows)
    are excluded from the single-line-frequency and duplicate-line-ratio
    statistics, and long same-character runs inside such lines are not treated
    as garbage runs. Thresholds are unchanged: repeated prose/content lines and
    same-character runs inside content lines are still rejected.
    """

    transform_id = "repetition_filter"
    version = "2"
    mutates_text = False

    def __init__(self, config: RepetitionConfig | None = None) -> None:
        super().__init__(config or RepetitionConfig())
        self.cfg: RepetitionConfig = self.config  # type: ignore
        # P27B-F: the character-run pattern depends only on configuration, so it
        # is compiled once per filter instance (hence once per worker process),
        # never per document. The pattern string is unchanged.
        self._char_run_re = re.compile(rf"(.)\1{{{self.cfg.max_char_run},}}")

    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        start_t = time.monotonic()
        text = doc.text
        if not text:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(),
                duration_ms=0.0,
            )

        # Inexpensive characterization first so every rejection path reports
        # measured line/word counts instead of misleading zeros.
        # P27B-G: the word split and the raw line split are shared with the
        # other stages observing this same text version when a feature cache
        # is supplied; the direct computation is otherwise unchanged.
        words = features.words(text) if features is not None else text.split()
        raw_lines = features.lines(text) if features is not None else text.splitlines()
        lines = [line.strip() for line in raw_lines if line.strip()]
        word_count = len(words)
        line_count = len(lines)
        content_lines = [line for line in lines if not is_structural_separator_line(line)]

        def base_metrics(
            duplicate_line_ratio: float = 0.0,
            max_ngram_repetition_ratio: float = 0.0,
        ) -> QualityMetrics:
            return QualityMetrics(
                utf8_byte_count=doc.utf8_byte_count,
                word_count=word_count,
                line_count=line_count,
                duplicate_line_ratio=duplicate_line_ratio,
                max_ngram_repetition_ratio=max_ngram_repetition_ratio,
            )

        # 1. Check single character runs (e.g. "aaaaaaaaaa...") in content
        # lines only. Long runs inside structural separator lines (Markdown
        # table delimiters, horizontal-rule dividers) are valid syntax, not
        # garbage. A run inside any other line still rejects.
        char_run_pattern = self._char_run_re
        for raw_line in raw_lines:
            if not raw_line.strip():
                continue
            if is_structural_separator_line(raw_line):
                continue
            if char_run_pattern.search(raw_line):
                duration = (time.monotonic() - start_t) * 1000.0
                return TransformResult(
                    action=TransformAction.REJECT,
                    document=None,
                    reasons=[f"excessive_repetition:char_run_exceeded_{self.cfg.max_char_run}"],
                    metrics=base_metrics(),
                    duration_ms=duration,
                )

        # 2. Line-level repetition analysis over content lines only, so that
        # repeated structural separators (e.g. one `---` per section) cannot
        # dominate the single-line-frequency statistic of an otherwise
        # coherent document.
        dup_line_ratio = 0.0
        if len(content_lines) > 2:
            counts = collections.Counter(content_lines)
            dup_chars = sum(len(line) * count for line, count in counts.items() if count > 1)
            total_chars = max(1, sum(len(line) * count for line, count in counts.items()))
            dup_line_ratio = dup_chars / total_chars

            max_freq = max(counts.values()) if counts else 0
            if max_freq > self.cfg.max_line_frequency:
                duration = (time.monotonic() - start_t) * 1000.0
                return TransformResult(
                    action=TransformAction.REJECT,
                    document=None,
                    reasons=[f"excessive_repetition:line_frequency_{max_freq}"],
                    metrics=base_metrics(duplicate_line_ratio=dup_line_ratio),
                    duration_ms=duration,
                )

            if dup_line_ratio > self.cfg.max_duplicate_line_ratio:
                duration = (time.monotonic() - start_t) * 1000.0
                return TransformResult(
                    action=TransformAction.REJECT,
                    document=None,
                    reasons=[
                        f"excessive_repetition:duplicate_line_ratio_{dup_line_ratio:.2f}_exceeded_{self.cfg.max_duplicate_line_ratio:.2f}"
                    ],
                    metrics=base_metrics(duplicate_line_ratio=dup_line_ratio),
                    duration_ms=duration,
                )

        # 3. Bounded 5-gram repetition analysis
        sample_words = (
            words
            if len(text) <= self.cfg.max_analysis_chars
            else text[: self.cfg.max_analysis_chars].split()
        )
        ngram_repeat_ratio = 0.0
        if len(sample_words) >= 10:
            n = 5
            # zip creates the same exact tuples in C, without a temporary list
            # slice or Python generator step for each window. Counter still
            # compares full tuples; hash collisions cannot change counts.
            ngrams = zip(*(itertools.islice(sample_words, i, None) for i in range(n)), strict=False)
            ngram_counts = collections.Counter(ngrams)
            if ngram_counts:
                repeated_ngrams = sum(c for c in ngram_counts.values() if c > 1)
                ngram_repeat_ratio = repeated_ngrams / sum(ngram_counts.values())

                if ngram_repeat_ratio > self.cfg.max_ngram_repetition_ratio:
                    duration = (time.monotonic() - start_t) * 1000.0
                    return TransformResult(
                        action=TransformAction.REJECT,
                        document=None,
                        reasons=[
                            f"excessive_repetition:ngram_repeat_ratio_{ngram_repeat_ratio:.2f}_exceeded_{self.cfg.max_ngram_repetition_ratio:.2f}"
                        ],
                        metrics=base_metrics(
                            duplicate_line_ratio=dup_line_ratio,
                            max_ngram_repetition_ratio=ngram_repeat_ratio,
                        ),
                        duration_ms=duration,
                    )

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = base_metrics(
            duplicate_line_ratio=dup_line_ratio,
            max_ngram_repetition_ratio=ngram_repeat_ratio,
        )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
