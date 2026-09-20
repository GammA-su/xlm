"""Structured example rendering and non-substring answer span tracking.

Adheres to C02 and Amendment 1.
"""

from __future__ import annotations

import re
import time
from typing import Any

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.types import QualityMetrics, TextSpan, TransformAction, TransformResult

THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


class StructuredRenderConfig(StrictConfigModel):
    """Configuration for structured example rendering."""

    schema_type: str = Field(
        default="auto",
        description="Structured schema: 'auto', 'passage_qa', 'direct_qa', or 'messages'.",
    )
    include_reasoning: bool = Field(
        default=True,
        description="Include explicit reasoning step when available in structured input.",
    )
    reasoning_policy: str = Field(
        default="include",
        description="Reasoning handling policy: 'include', 'exclude', or 'preserve'.",
    )


class StructuredExampleRenderTransform(BaseTransform):
    """Renders structured examples with exact half-open byte span accounting for answers.

    Adheres strictly to Amendment 1:
    - Answer spans are half-open UTF-8 byte spans [start, end) into the
      final canonical rendered text.
    - Exact offset tracking during template expansion; NEVER uses naive substring searching.
    - Schema-specific required fields: passage QA requires passage; direct QA does not.
    - Explicit reasoning policies: unsupported or malformed embedded reasoning fails visibly.
    - Records answer spans in metadata for optional answer masking, preserving full-sequence
    pretraining.
    """

    transform_id = "structured_render"
    version = "1"
    mutates_text = True

    def __init__(self, config: StructuredRenderConfig | None = None) -> None:
        super().__init__(config or StructuredRenderConfig())
        self.cfg: StructuredRenderConfig = self.config  # type: ignore

    def _render_passage_qa(
        self, doc: CanonicalDocument, meta: dict[str, Any]
    ) -> tuple[str, list[TextSpan], list[str]]:
        """Render passage-based QA with mandatory passage context."""
        passage = meta.get("passage") or meta.get("context")
        question = meta.get("question")
        answer = meta.get("answer")
        reasoning = meta.get("reasoning")

        if not passage or not str(passage).strip():
            return "", [], ["incomplete_context:missing_passage"]
        if not question or not str(question).strip():
            return "", [], ["incomplete_context:missing_question"]
        if not answer or not str(answer).strip():
            return "", [], ["incomplete_context:missing_answer"]

        passage_str = str(passage).strip()
        question_str = str(question).strip()
        answer_str = str(answer).strip()

        # Handle embedded think tags if present
        if "<think>" in answer_str:
            match = THINK_TAG_RE.search(answer_str)
            if not match:
                return "", [], ["unsupported_reasoning_format:malformed_think_tag"]
            extracted_reasoning = match.group(1).strip()
            answer_str = THINK_TAG_RE.sub("", answer_str).strip()
            if not reasoning:
                reasoning = extracted_reasoning

        prefix = f"Passage:\n{passage_str}\n\nQuestion:\n{question_str}\n\n"

        should_include_reasoning = (
            self.cfg.include_reasoning and self.cfg.reasoning_policy != "exclude"
        )
        if should_include_reasoning and reasoning and str(reasoning).strip():
            prefix += f"Reasoning:\n{str(reasoning).strip()}\n\n"

        prefix += "Answer:\n"

        prefix_bytes = prefix.encode("utf-8")
        answer_bytes = answer_str.encode("utf-8")

        start_byte = len(prefix_bytes)
        end_byte = start_byte + len(answer_bytes)

        rendered_text = prefix + answer_str
        span = TextSpan(start_byte=start_byte, end_byte=end_byte, label="answer")
        return rendered_text, [span], []

    def _render_direct_qa(
        self, doc: CanonicalDocument, meta: dict[str, Any]
    ) -> tuple[str, list[TextSpan], list[str]]:
        """Render self-contained direct QA (passage is not required)."""
        question = meta.get("question")
        answer = meta.get("answer")
        reasoning = meta.get("reasoning")

        if not question or not str(question).strip():
            return "", [], ["incomplete_context:missing_question"]
        if not answer or not str(answer).strip():
            return "", [], ["incomplete_context:missing_answer"]

        question_str = str(question).strip()
        answer_str = str(answer).strip()

        if "<think>" in answer_str:
            match = THINK_TAG_RE.search(answer_str)
            if not match:
                return "", [], ["unsupported_reasoning_format:malformed_think_tag"]
            extracted_reasoning = match.group(1).strip()
            answer_str = THINK_TAG_RE.sub("", answer_str).strip()
            if not reasoning:
                reasoning = extracted_reasoning

        prefix = f"Question:\n{question_str}\n\n"

        should_include_reasoning = (
            self.cfg.include_reasoning and self.cfg.reasoning_policy != "exclude"
        )
        if should_include_reasoning and reasoning and str(reasoning).strip():
            prefix += f"Reasoning:\n{str(reasoning).strip()}\n\n"

        prefix += "Answer:\n"

        prefix_bytes = prefix.encode("utf-8")
        answer_bytes = answer_str.encode("utf-8")

        start_byte = len(prefix_bytes)
        end_byte = start_byte + len(answer_bytes)

        rendered_text = prefix + answer_str
        span = TextSpan(start_byte=start_byte, end_byte=end_byte, label="answer")
        return rendered_text, [span], []

    def _render_messages(
        self, doc: CanonicalDocument, meta: dict[str, Any]
    ) -> tuple[str, list[TextSpan], list[str]]:
        """Render conversational message sequence preserving role order."""
        messages = meta.get("messages")
        if not isinstance(messages, list) or not messages:
            return "", [], ["incomplete_context:missing_messages"]

        pieces: list[str] = []
        spans: list[TextSpan] = []
        current_byte_offset = 0

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                return "", [], [f"incomplete_context:malformed_message_at_index_{i}"]

            role = str(msg["role"]).strip().lower()
            content = str(msg["content"]).strip()

            header = f"<|im_start|>{role}\n"
            footer = "<|im_end|>\n"
            newline_suffix = "\n" if not content.endswith("\n") else ""

            header_bytes = header.encode("utf-8")
            content_bytes = content.encode("utf-8")
            suffix_bytes = newline_suffix.encode("utf-8")
            footer_bytes = footer.encode("utf-8")

            if role == "assistant":
                start_byte = current_byte_offset + len(header_bytes)
                end_byte = start_byte + len(content_bytes)
                spans.append(TextSpan(start_byte=start_byte, end_byte=end_byte, label="answer"))

            pieces.append(header)
            pieces.append(content)
            pieces.append(newline_suffix)
            pieces.append(footer)

            current_byte_offset += (
                len(header_bytes) + len(content_bytes) + len(suffix_bytes) + len(footer_bytes)
            )

        rendered_text = "".join(pieces)
        return rendered_text, spans, []

    def apply(self, doc: CanonicalDocument) -> TransformResult:
        start_t = time.monotonic()
        meta = doc.source_metadata

        # Determine schema type
        schema = self.cfg.schema_type
        if schema == "auto":
            if "messages" in meta:
                schema = "messages"
            elif "passage" in meta or "context" in meta:
                schema = "passage_qa"
            elif "question" in meta and "answer" in meta:
                schema = "direct_qa"
            else:
                # Plain document without structured fields
                return TransformResult(
                    action=TransformAction.ACCEPT,
                    document=doc,
                    reasons=[],
                    metrics=QualityMetrics(utf8_byte_count=doc.utf8_byte_count),
                    duration_ms=(time.monotonic() - start_t) * 1000.0,
                )

        if schema == "passage_qa":
            rendered_text, spans, errors = self._render_passage_qa(doc, meta)
        elif schema == "direct_qa":
            rendered_text, spans, errors = self._render_direct_qa(doc, meta)
        elif schema == "messages":
            rendered_text, spans, errors = self._render_messages(doc, meta)
        else:
            raise ValueError(f"Unsupported structured schema type: '{schema}'")

        duration = (time.monotonic() - start_t) * 1000.0

        if errors:
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=errors,
                metrics=QualityMetrics(utf8_byte_count=doc.utf8_byte_count),
                duration_ms=duration,
            )

        new_doc = self.create_transformed_copy(
            doc=doc,
            new_text=rendered_text,
            stage_name=self.transform_id,
            metadata_updates={"rendered_schema": schema},
            spans=spans,
        )

        metrics = QualityMetrics(
            utf8_byte_count=new_doc.utf8_byte_count,
            word_count=len(rendered_text.split()),
            line_count=len(rendered_text.splitlines()),
            spans=spans,
        )

        return TransformResult(
            action=TransformAction.ACCEPT,
            document=new_doc,
            reasons=[],
            metrics=metrics,
            duration_ms=duration,
        )
