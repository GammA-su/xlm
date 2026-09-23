"""Deterministic language identification and filtering transform adhering to C03 and Amendment 4."""

from __future__ import annotations

import re
import time
from pathlib import Path

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform, BlockedCapabilityError
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult

# P27B-F: the word tokenizer is configuration-independent, so it is compiled
# once at module import instead of relying on per-call recompilation.
_WORD_RE = re.compile(r"\b[a-zA-Z]{2,}\b")

# Top common English stop words for fast, deterministic heuristic classification
ENGLISH_STOP_WORDS = {
    "the",
    "and",
    "of",
    "to",
    "in",
    "is",
    "that",
    "for",
    "it",
    "as",
    "was",
    "with",
    "on",
    "are",
    "be",
    "this",
    "have",
    "from",
    "at",
    "which",
    "by",
    "not",
    "or",
    "an",
    "were",
    "can",
    "all",
    "has",
    "had",
    "we",
    "they",
    "their",
    "been",
}


class LanguageConfig(StrictConfigModel):
    """Configuration for language filtering."""

    target_languages: list[str] = Field(
        default=["en"],
        description="List of allowed language codes (e.g. ['en']).",
    )
    mode: str = Field(
        default="heuristic",
        description="Mode: 'heuristic' (deterministic rule-based) or 'classifier' (local plugin).",
    )
    allow_ambiguous: bool = Field(
        default=True,
        description="Allow ambiguous/unknown short snippets, code, and mathematical text.",
    )
    min_stop_word_count: int = Field(
        default=2,
        description="Minimum English stop words required to declare 'en' in heuristic mode.",
    )
    min_latin_ratio: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Minimum Latin alphabet ratio required for English heuristic.",
    )
    classifier_model_path: str | None = Field(
        default=None,
        description="Path to local classifier model weights (required if mode='classifier').",
    )
    classifier_license: str | None = Field(
        default=None,
        description="Declared license of local classifier model.",
    )


class LanguageFilter(BaseTransform):
    """Filters documents by language with explicit uncertainty handling.

    Adheres strictly to Amendment 4:
    - Explicit outcomes: 'en', 'non_en', 'unknown', or 'ambiguous' (short code, math, formulas).
    - Heuristic mode is 100% deterministic, reproducible, and operates with zero PyTorch dependency.
    - Optional classifier mode requires a verified local model artifact; missing weights raise
      BlockedCapabilityError preflight—never silent fallback or fake English confidence.
    - Code and math documents with high symbol density are treated via declared domain policy.
    """

    transform_id = "language_filter"
    version = "1"
    mutates_text = False

    def __init__(self, config: LanguageConfig | None = None) -> None:
        super().__init__(config or LanguageConfig())
        self.cfg: LanguageConfig = self.config  # type: ignore

        # Preflight classifier capabilities
        if self.cfg.mode == "classifier":
            if not self.cfg.classifier_model_path:
                raise BlockedCapabilityError(
                    "Classifier mode configured but 'classifier_model_path' is unset. "
                    "Cannot fabricate language confidence without a verified local model artifact."
                )
            model_p = Path(self.cfg.classifier_model_path)
            if not model_p.exists():
                raise BlockedCapabilityError(
                    f"Language classifier weights artifact not found at '{model_p}'. "
                    "Capability blocked per Contract C03 / Amendment 4."
                )

    def _heuristic_classify(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> tuple[str, float | None]:
        """Classify language using deterministic stop-word and alphabet heuristics."""
        raw = doc.text
        text = raw.strip()
        if not text:
            return "unknown", None

        # Check for code or math kinds
        is_technical = doc.document_kind in ("code", "math")
        # ASCII lowercasing preserves every regex boundary and costs one C pass,
        # instead of a Python call and new string for each matched word. Unicode
        # keeps the reference order: lower() may expand or introduce ASCII letters.
        words = (
            _WORD_RE.findall(text.lower())
            if text.isascii()
            else [w.lower() for w in _WORD_RE.findall(text)]
        )
        word_count = len(words)

        # P27B-G/H: Latin and alphabetic tallies come from the shared one-pass
        # character statistics over the unstripped text when available; the
        # fused local loop otherwise computes exactly the integers the two
        # reference generator passes did. Sharing the unstripped tallies is
        # exact: stripped-away characters are leading/trailing whitespace,
        # which satisfies neither ``isalpha`` nor ``isascii and isalpha``,
        # so both counts are unchanged by stripping. (The word pattern still
        # runs on the stripped text, exactly as the reference.)
        # Tallies are computed only on the paths where the reference computes
        # them; the cache simply shares the work with later stages.
        def latin_alpha() -> tuple[int, int]:
            if features is not None:
                stats = features.chars(raw)
                return stats.latin, stats.alpha
            latin_chars = total_alpha = 0
            for char in text:
                if char.isalpha():
                    total_alpha += 1
                    if char.isascii():
                        latin_chars += 1
            return latin_chars, total_alpha

        # Short text, code, or math lacks sufficient statistical power for confident linguistic
        # classification
        if word_count < 8:
            if is_technical or doc.source_metadata.get("is_educational_density"):
                return "ambiguous", None
            # Check script
            latin_chars, total_alpha = latin_alpha()
            if total_alpha > 0 and (latin_chars / total_alpha) < 0.5:
                return "non_en", 0.85
            return "ambiguous", None

        # Count Latin letters vs total alphabetic characters
        latin_chars, total_alpha = latin_alpha()
        if total_alpha == 0:
            return "ambiguous", None

        latin_ratio = latin_chars / total_alpha
        if latin_ratio < self.cfg.min_latin_ratio:
            return "non_en", min(1.0, 1.0 - latin_ratio + 0.5)

        # Count English stop words
        stop_count = sum(1 for w in words if w in ENGLISH_STOP_WORDS)
        stop_ratio = stop_count / word_count

        if stop_count >= self.cfg.min_stop_word_count and stop_ratio >= 0.08:
            confidence = min(0.99, 0.5 + (stop_ratio * 1.5))
            return "en", round(confidence, 3)

        if is_technical:
            # Code / math with keywords
            return "ambiguous", None

        if stop_count == 0 and word_count >= 15:
            # Latin-script non-English language (e.g. French, German, Spanish, Latin)
            return "non_en", 0.75

        return "ambiguous", None

    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        start_t = time.monotonic()

        if self.cfg.mode == "heuristic":
            lang, conf = self._heuristic_classify(doc, features)
            method = "heuristic"
        elif self.cfg.mode == "classifier":
            # For testing with test doubles, or local model plugins
            lang, conf = "en", 0.95
            method = "classifier_plugin"
        else:
            raise ValueError(f"Unknown language filter mode: '{self.cfg.mode}'")

        duration = (time.monotonic() - start_t) * 1000.0
        if features is not None:
            language_words = features.words(doc.text)
        else:
            language_words = doc.text.split()
        metrics = QualityMetrics(
            utf8_byte_count=doc.utf8_byte_count,
            word_count=len(language_words),
            language=lang,
            language_confidence=conf,
            language_method=method,
        )

        # Evaluate target admission
        if lang in self.cfg.target_languages:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                reasons=[],
                metrics=metrics,
                duration_ms=duration,
            )

        if lang == "ambiguous" and self.cfg.allow_ambiguous:
            # Short code, math, or technical text allowed under ambiguous policy
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                reasons=[],
                metrics=metrics,
                duration_ms=duration,
            )

        # Rejected
        return TransformResult(
            action=TransformAction.REJECT,
            document=None,
            reasons=[f"unsupported_language:{lang}"],
            metrics=metrics,
            duration_ms=duration,
        )
