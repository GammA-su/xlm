"""Canonical NFC and newline normalization transform adhering to C02 and C03."""

from __future__ import annotations

import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult
from xlm.data.normalization import canonical_normalize


class CanonicalNormalizationConfig(StrictConfigModel):
    """Configuration for canonical NFC and newline normalization."""

    normalize_nfc: bool = Field(default=True, description="Apply Unicode NFC normalization.")
    normalize_newlines: bool = Field(default=True, description="Normalize \\r\\n and \\r to \\n.")


class CanonicalNormalizationTransform(BaseTransform):
    """Idempotently applies Unicode NFC and newline normalization.

    Reuses P02 normalization logic. Strictly preserves:
    - Case sensitivity (no lowercasing).
    - Code indentation and tabs/spaces.
    - Mathematical notation, angle brackets, LaTeX.
    - Paragraph separation (\\n\\n).
    - Emoji and combining characters.
    """

    transform_id = "canonical_normalization"
    version = "1"
    mutates_text = True

    def __init__(self, config: CanonicalNormalizationConfig | None = None) -> None:
        super().__init__(config or CanonicalNormalizationConfig())

    def apply(self, doc: CanonicalDocument) -> TransformResult:
        start_t = time.monotonic()

        # Idempotence comes from canonical_normalize itself being idempotent
        # (see tests/test_cleaning_normalization.py), not from a stage-visited flag.
        normalized_text = canonical_normalize(doc.text)
        new_doc = self.create_transformed_copy(
            doc=doc,
            new_text=normalized_text,
            stage_name=self.transform_id,
        )

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = QualityMetrics(
            utf8_byte_count=new_doc.utf8_byte_count,
            word_count=len(normalized_text.split()),
            line_count=len(normalized_text.splitlines()) if normalized_text else 0,
        )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=new_doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
