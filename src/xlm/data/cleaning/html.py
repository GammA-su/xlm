"""Conservative, bounded HTML extraction transform adhering to C02, C03, and Amendment 3."""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult

# Regex matching unmistakable HTML structural patterns or markup tags
REAL_HTML_INDICATORS = re.compile(
    r"(?i)(<!doctype\s+html|<html[\s>]|<body[\s>]|<div[\s>]|</div\s*>|<p[\s>]|</p\s*>|<a\s+href=|<span[\s>]|</span\s*>|<br\s*/?>|<li[\s>]|</li\s*>|<table[\s>]|<script[\s>]|<style[\s>])"
)

BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "blockquote",
    "section",
    "article",
    "header",
    "footer",
    "tr",
}


class _BoundedTextExtractingParser(HTMLParser):
    """Safe, bounded HTML parser that extracts text while preserving formatting and code."""

    def __init__(self, drop_tags: set[str], max_chars: int = 10_000_000) -> None:
        super().__init__(convert_charrefs=True)
        self.drop_tags = drop_tags
        self.max_chars = max_chars
        self.pieces: list[str] = []
        self.ignore_depth = 0
        self.in_pre = 0
        self.total_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.drop_tags:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return

        if tag_lower == "pre":
            self.in_pre += 1
            self.pieces.append("\n\n")
        elif tag_lower == "br":
            self.pieces.append("\n")
        elif tag_lower in BLOCK_TAGS:
            self.pieces.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.drop_tags:
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if self.ignore_depth > 0:
            return

        if tag_lower == "pre":
            if self.in_pre > 0:
                self.in_pre -= 1
            self.pieces.append("\n\n")
        elif tag_lower in BLOCK_TAGS:
            self.pieces.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.ignore_depth > 0:
            return
        self.total_chars += len(data)
        if self.total_chars > self.max_chars:
            raise ValueError(f"HTML text extraction exceeded bound of {self.max_chars} chars.")
        self.pieces.append(data)


class HtmlExtractionConfig(StrictConfigModel):
    """Configuration for conservative HTML extraction."""

    enabled: bool = Field(default=True, description="Enable HTML extraction.")
    require_declared_content_type: bool = Field(
        default=False,
        description="If True, only extracts if doc metadata declares content_type='html'.",
    )
    drop_tags: list[str] = Field(
        default=["script", "style", "head", "noscript", "svg"],
        description="Tags whose inner content should be completely dropped.",
    )
    max_html_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum input HTML size to prevent denial of service.",
    )


class HtmlExtractionTransform(BaseTransform):
    """Extracts text from HTML documents while preserving code and mathematical notation.

    Adheres strictly to Amendment 3:
    - Conservative: does not extract if plain-text or code document.
    - Angle-bracket safety: Math inequalities (0 < x < 1, a < b and c > d) are never stripped.
    - Drops script and style content without executing or fetching resources.
    - Preserves pre/code blocks and paragraph separation.
    - Idempotent: does not double-decode entities or re-parse extracted text.
    """

    transform_id = "html_extraction"
    version = "1"
    mutates_text = True

    def __init__(self, config: HtmlExtractionConfig | None = None) -> None:
        super().__init__(config or HtmlExtractionConfig())
        self.cfg: HtmlExtractionConfig = self.config  # type: ignore

    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        start_t = time.monotonic()
        if not self.cfg.enabled:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(has_html_markup=False),
                duration_ms=0.0,
            )

        # Check if already processed by HTML extraction
        if any(entry.get("stage") == self.transform_id for entry in doc.transform_log):
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(has_html_markup=False),
                duration_ms=0.0,
            )

        content_type = str(doc.source_metadata.get("content_type", "")).lower()
        is_declared_html = content_type in ("html", "text/html")
        is_code = doc.document_kind == "code"

        # Code documents explicitly bypass HTML extraction to protect <tag> code examples
        if is_code:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(has_html_markup=False),
                duration_ms=(time.monotonic() - start_t) * 1000.0,
            )

        has_markup = bool(REAL_HTML_INDICATORS.search(doc.text))

        if self.cfg.require_declared_content_type and not is_declared_html:
            # Extraction requires explicit declaration
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(has_html_markup=has_markup),
                duration_ms=(time.monotonic() - start_t) * 1000.0,
            )

        # If not declared HTML and no genuine HTML markup patterns found, preserve as plain text
        if not is_declared_html and not has_markup:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(has_html_markup=False),
                duration_ms=(time.monotonic() - start_t) * 1000.0,
            )

        # Bounded HTML extraction
        if doc.utf8_byte_count > self.cfg.max_html_bytes:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"html_size_exceeded:{doc.utf8_byte_count}"],
                metrics=QualityMetrics(has_html_markup=True),
                duration_ms=(time.monotonic() - start_t) * 1000.0,
            )

        parser = _BoundedTextExtractingParser(
            drop_tags=set(t.lower() for t in self.cfg.drop_tags),
            max_chars=self.cfg.max_html_bytes,
        )
        try:
            parser.feed(doc.text)
            parser.close()
        except Exception as e:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=[f"html_parse_error:{type(e).__name__}"],
                metrics=QualityMetrics(has_html_markup=True),
                duration_ms=(time.monotonic() - start_t) * 1000.0,
            )

        extracted_raw = "".join(parser.pieces)

        # Clean multiple blank lines without aggressive whitespace collapsing
        lines = extracted_raw.split("\n")
        cleaned_lines: list[str] = []
        blank_run = 0
        for line in lines:
            if not line.strip():
                blank_run += 1
                if blank_run <= 2:  # Preserve paragraph breaks (up to 2 blank lines)
                    cleaned_lines.append("")
            else:
                blank_run = 0
                cleaned_lines.append(line.rstrip())

        cleaned_text = "\n".join(cleaned_lines).strip()

        new_doc = self.create_transformed_copy(
            doc=doc,
            new_text=cleaned_text,
            stage_name=self.transform_id,
            metadata_updates={"html_extracted": True},
        )

        duration = (time.monotonic() - start_t) * 1000.0
        if features is not None:
            extracted_words = features.words(cleaned_text)
        else:
            extracted_words = cleaned_text.split()
        metrics = QualityMetrics(
            utf8_byte_count=new_doc.utf8_byte_count,
            word_count=len(extracted_words),
            line_count=len(cleaned_lines),
            has_html_markup=True,
        )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=new_doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
