"""Bounded repetition and n-gram loop detection filter adhering to C03 and Amendment 6."""

from __future__ import annotations

import collections
import re
import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
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


class RepetitionFilter(BaseTransform):
    """Detects and rejects documents containing excessive repeated lines, loops, or spans.

    Adheres strictly to Amendment 6:
    - Bounded n-gram and line analysis capping text evaluated to max_analysis_chars.
    - Tracks line repetition ratio, single-line frequency, character runs, and 5-gram loops.
    - Pure filter: does not mutate text (mutates_text = False).
    """

    transform_id = "repetition_filter"
    version = "1"
    mutates_text = False

    def __init__(self, config: RepetitionConfig | None = None) -> None:
        super().__init__(config or RepetitionConfig())
        self.cfg: RepetitionConfig = self.config  # type: ignore

    def apply(self, doc: CanonicalDocument) -> TransformResult:
        start_t = time.monotonic()
        text = doc.text
        if not text:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(),
                duration_ms=0.0,
            )

        # 1. Check single character runs (e.g. "aaaaaaaaaa...")
        char_run_pattern = re.compile(rf"(.)\1{{{self.cfg.max_char_run},}}")
        if char_run_pattern.search(text):
            duration = (time.monotonic() - start_t) * 1000.0
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"excessive_repetition:char_run_exceeded_{self.cfg.max_char_run}"],
                metrics=QualityMetrics(utf8_byte_count=doc.utf8_byte_count),
                duration_ms=duration,
            )

        # 2. Line-level repetition analysis
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        dup_line_ratio = 0.0
        if len(lines) > 2:
            counts = collections.Counter(lines)
            max_freq = max(counts.values()) if counts else 0
            if max_freq > self.cfg.max_line_frequency:
                duration = (time.monotonic() - start_t) * 1000.0
                return TransformResult(
                    action=TransformAction.REJECT,
                    document=None,
                    reasons=[f"excessive_repetition:line_frequency_{max_freq}"],
                    metrics=QualityMetrics(
                        utf8_byte_count=doc.utf8_byte_count,
                        duplicate_line_ratio=1.0,
                    ),
                    duration_ms=duration,
                )

            dup_chars = sum(len(line) * count for line, count in counts.items() if count > 1)
            total_chars = max(1, sum(len(line) * count for line, count in counts.items()))
            dup_line_ratio = dup_chars / total_chars

            if dup_line_ratio > self.cfg.max_duplicate_line_ratio:
                duration = (time.monotonic() - start_t) * 1000.0
                return TransformResult(
                    action=TransformAction.REJECT,
                    document=None,
                    reasons=[
                        f"excessive_repetition:duplicate_line_ratio_{dup_line_ratio:.2f}_exceeded_{self.cfg.max_duplicate_line_ratio:.2f}"
                    ],
                    metrics=QualityMetrics(
                        utf8_byte_count=doc.utf8_byte_count,
                        duplicate_line_ratio=dup_line_ratio,
                    ),
                    duration_ms=duration,
                )

        # 3. Bounded 5-gram repetition analysis
        sample_text = text[: self.cfg.max_analysis_chars]
        words = sample_text.split()
        ngram_repeat_ratio = 0.0
        if len(words) >= 10:
            n = 5
            ngrams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
            if ngrams:
                ngram_counts = collections.Counter(ngrams)
                repeated_ngrams = sum(c for c in ngram_counts.values() if c > 1)
                ngram_repeat_ratio = repeated_ngrams / len(ngrams)

                if ngram_repeat_ratio > self.cfg.max_ngram_repetition_ratio:
                    duration = (time.monotonic() - start_t) * 1000.0
                    return TransformResult(
                        action=TransformAction.REJECT,
                        document=None,
                        reasons=[
                            f"excessive_repetition:ngram_repeat_ratio_{ngram_repeat_ratio:.2f}_exceeded_{self.cfg.max_ngram_repetition_ratio:.2f}"
                        ],
                        metrics=QualityMetrics(
                            utf8_byte_count=doc.utf8_byte_count,
                            duplicate_line_ratio=dup_line_ratio,
                            max_ngram_repetition_ratio=ngram_repeat_ratio,
                        ),
                        duration_ms=duration,
                    )

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = QualityMetrics(
            utf8_byte_count=doc.utf8_byte_count,
            word_count=len(words),
            line_count=len(lines),
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
