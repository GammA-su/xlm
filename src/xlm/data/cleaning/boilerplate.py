"""Context-aware, paragraph-level boilerplate filtering transform.

Adheres to C03 and Amendment 3.
"""

from __future__ import annotations

import re
import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult

# Standard standalone boilerplate patterns
STANDALONE_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)^\s*(copyright\s+(©|\(c\)|\d{4})|all rights reserved|©\s*\d{4}).*$",
        re.MULTILINE,
    ),
    re.compile(
        r"(?i)^\s*(terms\s+(of\s+service|of\s+use)|privacy\s+policy|cookie\s+policy)(\s*[|•·/]\s*[\w\s]+)+\s*$",
        re.MULTILINE,
    ),
    re.compile(
        r"(?i)^\s*this (site|website) uses cookies to (ensure|give|provide)"
        r".*?(accept|agree|continue|close)\.?\s*$",
    ),
    re.compile(
        r"(?i)^\s*(subscribe to our (free\s+)?newsletter"
        r"|sign up for (our\s+)?updates"
        r"|enter your email to subscribe)\.?\s*$",
    ),
    re.compile(
        r"(?i)^\s*(follow us on|share this (article|post) on):?\s+"
        r"(facebook|twitter|x|linkedin|reddit|instagram|youtube)\.?\s*$",
    ),
    re.compile(
        r"(?i)^\s*(click here to (unsubscribe|manage preferences)"
        r"|to opt out of these emails)\.?\s*$",
    ),
]

# Words signaling legitimate analytical prose discussing policies
PROSE_INDICATORS = re.compile(
    r"(?i)\b(analyz\w+|discuss\w+|examin\w+|review\w+|argu\w+|stat\w+|requir\w+|mandat\w+|section|clause|court|law|regulation|study|research)\b"
)


class BoilerplateConfig(StrictConfigModel):
    """Configuration for paragraph-aware boilerplate removal."""

    enabled: bool = Field(default=True, description="Enable boilerplate filtering.")
    max_boilerplate_paragraph_len: int = Field(
        default=300,
        description="Maximum length of a paragraph eligible for boilerplate matching.",
    )


class BoilerplateTransform(BaseTransform):
    """Identifies and removes standalone boilerplate paragraphs without destroying prose context.

    Adheres strictly to Amendment 3:
    - Contextual: preserves prose discussing or quoting phrases like 'Privacy Policy'.
    - Paragraph-aware: strips on paragraph boundaries (\\n\\n) without
      line-joining or whitespace collapse.
    - Records removal counts in metrics and transform_log.
    - Rejects document if completely reduced to boilerplate (boilerplate_only).
    """

    transform_id = "boilerplate_removal"
    version = "1"
    mutates_text = True

    def __init__(self, config: BoilerplateConfig | None = None) -> None:
        super().__init__(config or BoilerplateConfig())
        self.cfg: BoilerplateConfig = self.config  # type: ignore

    def _is_boilerplate_paragraph(self, para: str) -> bool:
        """Evaluate whether an isolated paragraph is pure boilerplate."""
        stripped = para.strip()
        if not stripped:
            return False

        # If paragraph is long, it is likely narrative or technical text
        if len(stripped) > self.cfg.max_boilerplate_paragraph_len:
            return False

        # If paragraph contains analytical prose discussing policies, preserve it
        if PROSE_INDICATORS.search(stripped):
            return False

        for pattern in STANDALONE_BOILERPLATE_PATTERNS:
            if pattern.search(stripped):
                return True
        return False

    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        start_t = time.monotonic()
        if not self.cfg.enabled:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(),
                duration_ms=0.0,
            )

        # Split on paragraph boundaries (\n\n)
        paragraphs = re.split(r"\n{2,}", doc.text)
        surviving_paragraphs: list[str] = []
        removed_count = 0

        for p in paragraphs:
            if self._is_boilerplate_paragraph(p):
                removed_count += 1
            else:
                surviving_paragraphs.append(p)

        new_text = "\n\n".join(surviving_paragraphs).strip()

        duration = (time.monotonic() - start_t) * 1000.0

        if not new_text:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=["boilerplate_only"],
                metrics=QualityMetrics(boilerplate_paragraphs_removed=removed_count),
                duration_ms=duration,
            )

        if removed_count == 0:
            # No changes
            if features is not None:
                no_change_words = features.words(doc.text)
                no_change_lines = features.lines(doc.text)
            else:
                no_change_words = doc.text.split()
                no_change_lines = doc.text.splitlines()
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                reasons=[],
                metrics=QualityMetrics(
                    utf8_byte_count=doc.utf8_byte_count,
                    word_count=len(no_change_words),
                    line_count=len(no_change_lines),
                    boilerplate_paragraphs_removed=0,
                ),
                duration_ms=duration,
            )

        new_doc = self.create_transformed_copy(
            doc=doc,
            new_text=new_text,
            stage_name=self.transform_id,
            metadata_updates={"boilerplate_removed_count": removed_count},
        )

        if features is not None:
            kept_words = features.words(new_text)
            kept_lines = features.lines(new_text)
        else:
            kept_words = new_text.split()
            kept_lines = new_text.splitlines()
        metrics = QualityMetrics(
            utf8_byte_count=new_doc.utf8_byte_count,
            word_count=len(kept_words),
            line_count=len(kept_lines),
            boilerplate_paragraphs_removed=removed_count,
        )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=new_doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
