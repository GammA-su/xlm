"""Bounded length, educational-density heuristic, and noise filtering transforms."""

from __future__ import annotations

import re
import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult

# Control characters excluding \t (0x09), \n (0x0a), \r (0x0d)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Common UTF-8 misencoding / Mojibake patterns
MOJIBAKE_PATTERNS = re.compile(r"(Ã©|Ã¨|Ã¼|Ã±|â€™|â€œ|â€\x9d|Ã¢|Ã®|Ã´)")

MATH_OR_CODE_TOKENS = re.compile(
    r"(?i)\b(def|class|import|return|let|const|fn|function|theorem|lemma|proof|definition|integral|sum|matrix|frac|sqrt)\b"
)


class LengthConfig(StrictConfigModel):
    """Configuration for bounded document length filtering."""

    min_bytes: int = Field(default=100, ge=0, description="Minimum UTF-8 bytes required.")
    max_bytes: int = Field(
        default=50 * 1024 * 1024,
        gt=0,
        description="Hard maximum UTF-8 bytes permitted per record.",
    )
    min_words: int = Field(default=15, ge=0, description="Minimum words required.")
    max_words: int = Field(default=1_000_000, gt=0, description="Maximum words permitted.")
    allow_educational_short_exemption: bool = Field(
        default=True,
        description=(
            "Allow high-density educational definitions, math or code to pass "
            "with lower minimum bounds."
        ),
    )
    min_educational_bytes: int = Field(
        default=25,
        ge=0,
        description="Relaxed minimum bytes for verified high-density educational text.",
    )
    min_educational_words: int = Field(
        default=3,
        ge=0,
        description="Relaxed minimum words for verified high-density educational text.",
    )


class LengthFilter(BaseTransform):
    """Enforces bounded record lengths with declared educational-density heuristics.

    Adheres strictly to Amendment 4 and 6:
    - Short formulas, code snippets, or definitions with high density are exempt from blanket
    pruning.
    - Educational exemption is a declared heuristic, not a guarantee of pedagogical value.
    - Hard maximum bounds are NEVER bypassed by exemptions.
    """

    transform_id = "length_filter"
    version = "1"
    mutates_text = False

    def __init__(self, config: LengthConfig | None = None) -> None:
        super().__init__(config or LengthConfig())
        self.cfg: LengthConfig = self.config  # type: ignore

    def _is_high_density_educational(self, doc: CanonicalDocument, words: list[str]) -> bool:
        """Heuristically detect short, compact mathematical, scientific, or code text."""
        if doc.document_kind in ("math", "code"):
            return True

        text = doc.text
        # Check for equations, definitions, or code syntax
        has_symbols = any(c in text for c in ("=", ":=", "\\sum", "\\int", "\\frac", "{", "}"))
        has_keywords = bool(MATH_OR_CODE_TOKENS.search(text))

        if has_symbols or has_keywords:
            alpha_num = sum(1 for c in text if c.isalnum())
            total = max(1, len(text.strip()))
            # High alphanumeric/token density
            return (alpha_num / total) > 0.60
        return False

    def apply(self, doc: CanonicalDocument) -> TransformResult:
        start_t = time.monotonic()
        byte_count = doc.utf8_byte_count
        words = doc.text.split()
        word_count = len(words)

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = QualityMetrics(
            utf8_byte_count=byte_count,
            word_count=word_count,
            line_count=len(doc.text.splitlines()),
        )

        # 1. Hard maximum bounds check (never bypassable)
        if byte_count > self.cfg.max_bytes:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"length_exceeded_max_bytes:{byte_count}>{self.cfg.max_bytes}"],
                metrics=metrics,
                duration_ms=duration,
            )

        if word_count > self.cfg.max_words:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"length_exceeded_max_words:{word_count}>{self.cfg.max_words}"],
                metrics=metrics,
                duration_ms=duration,
            )

        # 2. Minimum bounds check with educational density heuristic
        is_edu = False
        if self.cfg.allow_educational_short_exemption:
            is_edu = self._is_high_density_educational(doc, words)
            metrics.is_educational_density = is_edu

        effective_min_bytes = self.cfg.min_educational_bytes if is_edu else self.cfg.min_bytes
        effective_min_words = self.cfg.min_educational_words if is_edu else self.cfg.min_words

        if byte_count < effective_min_bytes:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"length_below_min_bytes:{byte_count}<{effective_min_bytes}"],
                metrics=metrics,
                duration_ms=duration,
            )

        if word_count < effective_min_words:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"length_below_min_words:{word_count}<{effective_min_words}"],
                metrics=metrics,
                duration_ms=duration,
            )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )


class NoiseConfig(StrictConfigModel):
    """Configuration for encoding noise and symbol density filtering."""

    max_replacement_chars: int = Field(
        default=0,
        ge=0,
        description="Maximum allowed Unicode replacement characters (\\ufffd).",
    )
    max_control_chars: int = Field(
        default=0,
        ge=0,
        description="Maximum allowed non-printable control characters.",
    )
    max_mojibake_occurrences: int = Field(
        default=1,
        ge=0,
        description="Maximum allowed common Mojibake encoding error patterns.",
    )
    max_symbol_ratio: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum ratio of symbol and punctuation characters to total non-whitespace characters."
        ),
    )
    code_symbol_ratio: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Relaxed symbol ratio for code/math documents.",
    )


class NoiseFilter(BaseTransform):
    """Detects and rejects encoding noise, Mojibake, and excessive symbol density.

    Adheres strictly to Amendment 3 and 4:
    - Rejects broken encoding without destructive or unvalidated in-place guessing.
    - Respects code and math kinds with appropriate symbol allowances.
    """

    transform_id = "noise_filter"
    version = "1"
    mutates_text = False

    def __init__(self, config: NoiseConfig | None = None) -> None:
        super().__init__(config or NoiseConfig())
        self.cfg: NoiseConfig = self.config  # type: ignore

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

        reasons: list[str] = []

        # 1. Check Unicode replacement character \ufffd
        replacement_count = text.count("\ufffd")
        if replacement_count > self.cfg.max_replacement_chars:
            reasons.append(f"encoding_noise:replacement_chars_{replacement_count}")

        # 2. Check non-printable control characters
        control_matches = CONTROL_CHAR_RE.findall(text)
        if len(control_matches) > self.cfg.max_control_chars:
            reasons.append(f"encoding_noise:control_chars_{len(control_matches)}")

        # 3. Check Mojibake patterns
        mojibake_matches = MOJIBAKE_PATTERNS.findall(text)
        if len(mojibake_matches) > self.cfg.max_mojibake_occurrences:
            reasons.append(f"encoding_noise:mojibake_{len(mojibake_matches)}")

        # 4. Symbol density calculation
        non_ws_chars = [c for c in text if not c.isspace()]
        total_non_ws = len(non_ws_chars)
        symbol_ratio = 0.0
        digit_ratio = 0.0

        if total_non_ws > 0:
            symbols = sum(1 for c in non_ws_chars if not c.isalnum())
            digits = sum(1 for c in non_ws_chars if c.isdigit())
            symbol_ratio = symbols / total_non_ws
            digit_ratio = digits / total_non_ws

            threshold = (
                self.cfg.code_symbol_ratio
                if doc.document_kind in ("code", "math")
                else self.cfg.max_symbol_ratio
            )
            if symbol_ratio > threshold:
                reasons.append(
                    f"excessive_symbols:symbol_ratio_{symbol_ratio:.2f}_exceeded_{threshold:.2f}"
                )

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = QualityMetrics(
            utf8_byte_count=doc.utf8_byte_count,
            word_count=len(text.split()),
            line_count=len(text.splitlines()),
            symbol_ratio=symbol_ratio,
            digit_ratio=digit_ratio,
        )

        if reasons:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=reasons,
                metrics=metrics,
                duration_ms=duration,
            )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
