"""Unit tests for canonical normalization and Unicode/indentation preservation.

Adheres to C02 and C03.
"""

from __future__ import annotations

from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.normalization import (
    CanonicalNormalizationTransform,
)
from xlm.data.normalization import compute_sha256


def create_sample_doc(
    doc_id: str,
    text: str,
    document_kind: str = "prose",
    source_metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    """Helper to construct a valid CanonicalDocument."""
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_source",
        source_revision="rev_123",
        source_file="test.jsonl",
        source_row=1,
        raw_hash=compute_sha256(b),
        clean_hash=compute_sha256(b),
        text=text,
        utf8_byte_count=len(b),
        language="en",
        language_confidence=1.0,
        document_kind=document_kind,
        source_metadata=source_metadata or {},
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_normalization_idempotence() -> None:
    """Verify clean(clean(x)) == clean(x) and no text drift."""
    transform = CanonicalNormalizationTransform()
    raw_text = "Line 1\r\nLine 2\rLine 3\n\nParagraph 2."
    doc = create_sample_doc("doc_idem", raw_text)

    # Pass 1
    res1 = transform.apply(doc)
    doc1 = res1.document
    assert doc1 is not None
    assert "\r" not in doc1.text
    assert doc1.text == "Line 1\nLine 2\nLine 3\n\nParagraph 2."

    # Pass 2 (reapplication)
    res2 = transform.apply(doc1)
    doc2 = res2.document
    assert doc2 is not None
    assert doc2.text == doc1.text
    assert doc2.clean_hash == doc1.clean_hash
    assert doc2.utf8_byte_count == doc1.utf8_byte_count


def test_unicode_preservation() -> None:
    """Verify Unicode accents, non-Latin scripts, emoji, and math symbols are preserved."""
    transform = CanonicalNormalizationTransform()
    text = (
        "Scholars: François Müller, José García, 李白, Søren Kierkegaard. "
        "Emoji: 🚀 🤖 👨‍👩‍👧‍👦. Math: ∀x ∈ ℝ, ∑_{i=1}^n i = \\frac{n(n+1)}{2} and ∫_0^1 x dx."
    )
    doc = create_sample_doc("doc_unicode", text)

    res = transform.apply(doc)
    assert res.document is not None
    # Text must preserve all exact characters in NFC form
    assert "François Müller" in res.document.text
    assert "李白" in res.document.text
    assert "🚀 🤖 👨‍👩‍👧‍👦" in res.document.text
    assert "∀x ∈ ℝ" in res.document.text
    assert "\\frac{n(n+1)}{2}" in res.document.text


def test_code_indentation_preservation() -> None:
    """Verify code indentation with leading spaces and tabs is never collapsed or stripped."""
    transform = CanonicalNormalizationTransform()
    code_text = (
        "def compute_frequencies(items: list[str]) -> dict[str, int]:\n"
        "    counts = {}\n"
        "\tfor item in items:\n"
        "        if item not in counts:\n"
        "            counts[item] = 0\n"
        "        counts[item] += 1\n"
        "    return counts\n"
    )
    doc = create_sample_doc("doc_code", code_text, document_kind="code")

    res = transform.apply(doc)
    assert res.document is not None
    # Verbatim indentation preserved
    assert res.document.text == code_text
    assert "    counts = {}" in res.document.text
    assert "\tfor item in items:" in res.document.text
    assert "            counts[item] = 0" in res.document.text


def test_immutability_and_hash_update() -> None:
    """Verify input document is never mutated in place and clean_hash is updated."""
    transform = CanonicalNormalizationTransform()
    original_text = "Heading\r\nBody text with carriage return.\r"
    doc = create_sample_doc("doc_immut", original_text)

    original_hash = doc.clean_hash
    original_bytes = doc.utf8_byte_count

    res = transform.apply(doc)
    new_doc = res.document
    assert new_doc is not None

    # Input doc unchanged
    assert doc.text == original_text
    assert doc.clean_hash == original_hash
    assert doc.utf8_byte_count == original_bytes

    # New doc updated
    assert new_doc.text != original_text
    assert new_doc.clean_hash == compute_sha256(new_doc.text)
    assert new_doc.utf8_byte_count == len(new_doc.text.encode("utf-8"))
    assert len(new_doc.transform_log) == 1
    assert new_doc.transform_log[0]["stage"] == "canonical_normalization"
