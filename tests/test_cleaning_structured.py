"""Unit tests for structured rendering and non-substring answer span tracking.

Adheres to C02
and Amendment 1.
"""

from __future__ import annotations

from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.structured import (
    StructuredExampleRenderTransform,
    StructuredRenderConfig,
)
from xlm.data.cleaning.types import TransformAction
from xlm.data.normalization import compute_sha256


def create_sample_doc(
    doc_id: str,
    source_metadata: dict[str, Any],
    text: str = "",
) -> CanonicalDocument:
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_qa",
        source_revision="rev_1",
        source_file="test.jsonl",
        source_row=1,
        raw_hash=compute_sha256(b),
        clean_hash=compute_sha256(b),
        text=text,
        utf8_byte_count=len(b),
        language="en",
        language_confidence=1.0,
        document_kind="qa",
        source_metadata=source_metadata,
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_passage_qa_rendering_and_missing_context() -> None:
    """Verify passage QA requires passage, question, and answer."""
    transform = StructuredExampleRenderTransform(StructuredRenderConfig(schema_type="passage_qa"))

    # 1. Complete passage QA passes
    meta = {
        "passage": "Albert Einstein developed the general theory of relativity in 1915.",
        "question": "When was general relativity developed?",
        "answer": "1915",
    }
    doc_complete = create_sample_doc("doc_qa_1", meta)
    res = transform.apply(doc_complete)
    assert res.action == TransformAction.ACCEPT
    assert res.document is not None
    assert "Passage:\nAlbert Einstein" in res.document.text
    assert "Answer:\n1915" in res.document.text

    # 2. Missing passage fails with explicit incomplete_context reason
    meta_no_passage = {
        "question": "When was general relativity developed?",
        "answer": "1915",
    }
    doc_no_passage = create_sample_doc("doc_qa_no_pass", meta_no_passage)
    res_no_pass = transform.apply(doc_no_passage)
    assert res_no_pass.action == TransformAction.REJECT
    assert "incomplete_context:missing_passage" in res_no_pass.reasons


def test_direct_qa_does_not_require_passage() -> None:
    """Verify self-contained direct QA does not require a passage."""
    transform = StructuredExampleRenderTransform(StructuredRenderConfig(schema_type="direct_qa"))
    meta = {
        "question": "What is the square root of 64?",
        "answer": "8",
    }
    doc = create_sample_doc("doc_direct", meta)
    res = transform.apply(doc)
    assert res.action == TransformAction.ACCEPT
    assert res.document is not None
    assert "Passage:" not in res.document.text
    assert "Question:\nWhat is the square root of 64?" in res.document.text
    assert "Answer:\n8" in res.document.text


def test_non_substring_answer_span_tracking() -> None:
    """Verify answer spans point strictly to the answer block even when answer occurs in question or
    passage.

    Adheres strictly to Amendment 1:
    - Answer occurs in passage, question, and final answer.
    - Span must NOT match the earlier occurrences.
    """
    transform = StructuredExampleRenderTransform(StructuredRenderConfig(schema_type="passage_qa"))
    target_answer = "Paris"

    meta = {
        "passage": "Paris is the capital of France. Many historic treaties were signed in Paris.",
        "question": "Which French city is famously called Paris?",
        "answer": target_answer,
    }
    doc = create_sample_doc("doc_span", meta)
    res = transform.apply(doc)

    assert res.action == TransformAction.ACCEPT
    assert res.document is not None
    rendered_text = res.document.text

    spans = res.metrics.spans
    assert len(spans) == 1
    span = spans[0]

    # Verify byte span resolution
    rendered_bytes = rendered_text.encode("utf-8")
    extracted_answer_bytes = rendered_bytes[span.start_byte : span.end_byte]
    assert extracted_answer_bytes == target_answer.encode("utf-8")

    # Verify that start_byte points strictly to the final Answer block, NOT the first occurrence!
    first_occurrence_byte = rendered_bytes.find(b"Paris")
    assert first_occurrence_byte < span.start_byte
    # Confirm preceding text is "Answer:\n"
    preceding = rendered_bytes[: span.start_byte].decode("utf-8")
    assert preceding.endswith("Answer:\n")


def test_reasoning_policy_and_think_tag_parsing() -> None:
    """Verify reasoning inclusion/exclusion and embedded think tag parsing."""
    # 1. Include reasoning
    transform_include = StructuredExampleRenderTransform(
        StructuredRenderConfig(schema_type="direct_qa", include_reasoning=True)
    )
    meta_with_think = {
        "question": "Solve 2x + 4 = 10",
        "answer": "<think>Subtract 4 to get 2x = 6, then divide by 2.</think> x = 3",
    }
    doc = create_sample_doc("doc_think", meta_with_think)
    res_inc = transform_include.apply(doc)
    assert res_inc.action == TransformAction.ACCEPT
    assert res_inc.document is not None
    assert "Reasoning:\nSubtract 4 to get 2x = 6" in res_inc.document.text
    assert "Answer:\nx = 3" in res_inc.document.text
    assert "<think>" not in res_inc.document.text

    # Verify answer span points to x = 3
    span = res_inc.metrics.spans[0]
    extracted = res_inc.document.text.encode("utf-8")[span.start_byte : span.end_byte].decode(
        "utf-8"
    )
    assert extracted == "x = 3"

    # 2. Exclude reasoning
    transform_exclude = StructuredExampleRenderTransform(
        StructuredRenderConfig(schema_type="direct_qa", include_reasoning=False)
    )
    res_exc = transform_exclude.apply(doc)
    assert res_exc.action == TransformAction.ACCEPT
    assert res_exc.document is not None
    assert "Reasoning:" not in res_exc.document.text
    assert "Answer:\nx = 3" in res_exc.document.text

    # 3. Malformed think tag fails visibly
    meta_malformed = {
        "question": "Question",
        "answer": "<think>Unclosed think tag without closing tag",
    }
    doc_bad = create_sample_doc("doc_bad", meta_malformed)
    res_bad = transform_include.apply(doc_bad)
    assert res_bad.action == TransformAction.REJECT
    assert any("malformed_think_tag" in r for r in res_bad.reasons)


def test_messages_conversation_schema() -> None:
    """Verify conversational messages schema preserves roles and tracks assistant answers."""
    transform = StructuredExampleRenderTransform(StructuredRenderConfig(schema_type="messages"))
    meta = {
        "messages": [
            {"role": "system", "content": "You are a helpful research assistant."},
            {"role": "user", "content": "Explain causal language modeling."},
            {"role": "assistant", "content": "Causal language modeling predicts the next token."},
        ]
    }
    doc = create_sample_doc("doc_msg", meta)
    res = transform.apply(doc)

    assert res.action == TransformAction.ACCEPT
    assert res.document is not None
    text = res.document.text

    assert "<|im_start|>system\nYou are a helpful research assistant." in text
    assert "<|im_start|>user\nExplain causal language modeling." in text
    assert "<|im_start|>assistant\nCausal language modeling predicts the next token." in text

    # Assistant span tracked
    assert len(res.metrics.spans) == 1
    span = res.metrics.spans[0]
    extracted = text.encode("utf-8")[span.start_byte : span.end_byte].decode("utf-8")
    assert extracted == "Causal language modeling predicts the next token."
