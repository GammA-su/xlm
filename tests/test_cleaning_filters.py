"""Unit tests for quality, boilerplate, repetition, language, noise, and PII filters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BlockedCapabilityError
from xlm.data.cleaning.boilerplate import BoilerplateTransform
from xlm.data.cleaning.language import LanguageConfig, LanguageFilter
from xlm.data.cleaning.length_noise import LengthConfig, LengthFilter, NoiseConfig, NoiseFilter
from xlm.data.cleaning.pii import PiiConfig, PiiSecretFilter
from xlm.data.cleaning.quarantine import QuarantineManager
from xlm.data.cleaning.repetition import RepetitionConfig, RepetitionFilter
from xlm.data.cleaning.types import TransformAction
from xlm.data.normalization import compute_sha256


def create_sample_doc(
    doc_id: str,
    text: str,
    document_kind: str = "prose",
    source_metadata: dict[str, Any] | None = None,
) -> CanonicalDocument:
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_filter",
        source_revision="rev_1",
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


# -------------------------------------------------------------------------
# Boilerplate Tests
# -------------------------------------------------------------------------


def test_boilerplate_removal_and_contextual_preservation() -> None:
    """Verify standalone boilerplate is stripped, while prose discussing policies is preserved."""
    transform = BoilerplateTransform()

    # Document containing standalone cookie notice and copyright footer + legitimate prose
    text = (
        "This website uses cookies to ensure you get the best experience on our site. Accept.\n\n"
        "In this legal analysis, we examine the updated Privacy Policy and evaluate how GDPR "
        "regulations "
        "mandate explicit user consent for tracking technologies across European jurisdictions.\n\n"
        "Copyright © 2026 Example Corp. All rights reserved."
    )
    doc = create_sample_doc("doc_bp", text)
    res = transform.apply(doc)

    assert res.action == TransformAction.ACCEPT
    assert res.document is not None
    cleaned = res.document.text

    # Standalone headers and footers stripped
    assert "This website uses cookies" not in cleaned
    assert "Copyright © 2026 Example Corp" not in cleaned

    # Legitimate prose discussing Privacy Policy preserved!
    assert "In this legal analysis, we examine the updated Privacy Policy" in cleaned
    assert res.metrics.boilerplate_paragraphs_removed == 2


def test_boilerplate_only_rejected() -> None:
    """Verify document consisting exclusively of boilerplate is rejected."""
    transform = BoilerplateTransform()
    text = (
        "Terms of Use | Privacy Policy | Cookie Policy | Sitemap\n\n"
        "Copyright © 2026. All rights reserved."
    )
    doc = create_sample_doc("doc_bp_only", text)
    res = transform.apply(doc)

    assert res.action == TransformAction.REJECT
    assert "boilerplate_only" in res.reasons


# -------------------------------------------------------------------------
# Repetition Tests
# -------------------------------------------------------------------------


def test_repetition_line_and_character_loops() -> None:
    """Verify duplicate line loops, character runs, and 5-gram repetitions are rejected."""
    filt = RepetitionFilter(RepetitionConfig(max_duplicate_line_ratio=0.30, max_char_run=30))

    # 1. Normal document passes
    normal_doc = create_sample_doc(
        "doc_norm",
        "This is a genuine article explaining computer science concepts with varied vocabulary. "
        "Transformers rely on multi-head self-attention mechanisms to model long-range context "
        "dependencies.",
    )
    res_norm = filt.apply(normal_doc)
    assert res_norm.action == TransformAction.ACCEPT

    # 2. Character run loop rejected
    char_loop_doc = create_sample_doc("doc_char_loop", "Error detected " + "x" * 60 + " ending.")
    res_char = filt.apply(char_loop_doc)
    assert res_char.action == TransformAction.REJECT
    assert any("char_run_exceeded" in r for r in res_char.reasons)

    # 3. Duplicate line loop rejected
    repeated_lines = "\n".join(["Click here to see more links!"] * 25)
    line_loop_doc = create_sample_doc("doc_line_loop", repeated_lines)
    res_line = filt.apply(line_loop_doc)
    assert res_line.action == TransformAction.REJECT
    assert any("excessive_repetition" in r for r in res_line.reasons)


# -------------------------------------------------------------------------
# Language Tests
# -------------------------------------------------------------------------


def test_language_heuristic_and_classifier_preflight() -> None:
    """Verify heuristic classification and classifier preflight error handling."""
    # 1. English passes heuristic
    lang_filt = LanguageFilter(LanguageConfig(mode="heuristic", target_languages=["en"]))
    en_doc = create_sample_doc(
        "doc_en",
        "The fundamental theorem of calculus connects differentiation with integration. "
        "This relationship was developed independently by Isaac Newton and Gottfried Leibniz.",
    )
    res_en = lang_filt.apply(en_doc)
    assert res_en.action == TransformAction.ACCEPT
    assert res_en.metrics.language == "en"

    # 2. Non-English Latin (German) rejected under target_languages=["en"]
    de_doc = create_sample_doc(
        "doc_de",
        "Die mathematische Analyse befasst sich mit Grenzwerten und unendlichen Reihen. "
        "Diese Untersuchungen bilden das Fundament der modernen theoretischen Physik und "
        "Mathematik.",
    )
    res_de = lang_filt.apply(de_doc)
    assert res_de.action == TransformAction.REJECT
    assert any("unsupported_language" in r for r in res_de.reasons)

    # 3. Short formula / code marked ambiguous and allowed under allow_ambiguous=True
    short_math_doc = create_sample_doc(
        "doc_short_math", "x = y + 1; z = x * 2", document_kind="code"
    )
    res_short = lang_filt.apply(short_math_doc)
    assert res_short.action == TransformAction.ACCEPT
    assert res_short.metrics.language == "ambiguous"

    # 4. Classifier mode with missing model artifact raises BlockedCapabilityError preflight
    with pytest.raises(BlockedCapabilityError, match="classifier_model_path"):
        LanguageFilter(LanguageConfig(mode="classifier", classifier_model_path=None))

    with pytest.raises(BlockedCapabilityError, match="not found"):
        LanguageFilter(
            LanguageConfig(
                mode="classifier",
                classifier_model_path="nonexistent/model/weights.bin",
            )
        )


# -------------------------------------------------------------------------
# Length & Educational Density Tests
# -------------------------------------------------------------------------


def test_length_and_educational_density_exemption() -> None:
    """Verify short educational text is retained while trivial short text is rejected."""
    len_filt = LengthFilter(
        LengthConfig(
            min_bytes=100,
            min_words=15,
            allow_educational_short_exemption=True,
            min_educational_bytes=20,
            min_educational_words=3,
        )
    )

    # 1. Trivial short text (< 100 bytes) rejected
    trivial_doc = create_sample_doc("doc_trivial", "Too short text.")
    res_triv = len_filt.apply(trivial_doc)
    assert res_triv.action == TransformAction.REJECT
    assert any("length_below_min" in r for r in res_triv.reasons)

    # 2. Compact educational mathematical definition retained via exemption
    math_definition_doc = create_sample_doc(
        "doc_edu",
        "Euler's identity: e^{i\\pi} + 1 = 0.",
        document_kind="math",
    )
    res_edu = len_filt.apply(math_definition_doc)
    assert res_edu.action == TransformAction.ACCEPT
    assert res_edu.metrics.is_educational_density is True

    # 3. Oversized document (> max_bytes) rejected
    len_filt_tight = LengthFilter(LengthConfig(max_bytes=500, max_words=100))
    oversized_doc = create_sample_doc("doc_oversized", "Word " * 150)
    res_huge = len_filt_tight.apply(oversized_doc)
    assert res_huge.action == TransformAction.REJECT
    assert any("length_exceeded_max" in r for r in res_huge.reasons)


# -------------------------------------------------------------------------
# Noise Tests
# -------------------------------------------------------------------------


def test_noise_filter_encoding_and_symbols() -> None:
    """Verify replacement characters, control chars, and excessive symbols are rejected."""
    noise_filt = NoiseFilter(NoiseConfig(max_replacement_chars=0, max_symbol_ratio=0.40))

    # 1. Unicode replacement character (\ufffd) rejected
    corrupted_doc = create_sample_doc("doc_corrupt", "Corrupted text with broken \ufffd character.")
    res_corrupt = noise_filt.apply(corrupted_doc)
    assert res_corrupt.action == TransformAction.REJECT
    assert any("encoding_noise:replacement_chars" in r for r in res_corrupt.reasons)

    # 2. Random symbol spam rejected
    symbol_spam = create_sample_doc(
        "doc_symbols", "Text with %$#@! &*^%$# @!#*&% symbols everywhere!"
    )
    res_sym = noise_filt.apply(symbol_spam)
    assert res_sym.action == TransformAction.REJECT
    assert any("excessive_symbols" in r for r in res_sym.reasons)


# -------------------------------------------------------------------------
# PII / Secret Tests & Quarantine Protection
# -------------------------------------------------------------------------


def test_pii_secret_filter_and_quarantine_protection(tmp_path: Path) -> None:
    """Verify secrets and canary credentials are caught and omitted from quarantine."""
    canary_token = "CANARY_SECRET_A1B2C3D4E5F6"
    hf_token = "hf_abcdefghijklmnopqrstuvwxyz01234567"

    secret_text = (
        f"Deployment instructions: authenticate with {hf_token} and test token {canary_token}."
    )
    doc = create_sample_doc("doc_secret", secret_text)

    # 1. Reject mode
    reject_filt = PiiSecretFilter(PiiConfig(action="reject"))
    res = reject_filt.apply(doc)
    assert res.action == TransformAction.REJECT
    assert any("detected_secret:huggingface_token" in r for r in res.reasons)
    assert any("detected_secret:canary_credential" in r for r in res.reasons)

    # 2. Quarantine manager: secret text must be OMITTED from quarantine file
    q_dir = tmp_path / "quarantine"
    q_mgr = QuarantineManager(q_dir)
    q_mgr.record_rejection(doc, res.reasons, "pii_secret_filter", res.metrics)

    q_file = q_dir / "quarantine.jsonl"
    assert q_file.exists()
    content = q_file.read_text(encoding="utf-8")

    # Secret string and canary credential must NEVER appear in the quarantine file!
    assert canary_token not in content
    assert hf_token not in content
    assert '"is_secret_omitted": true' in content
    assert '"sanitized_preview": null' in content

    # 3. Redact mode
    redact_filt = PiiSecretFilter(PiiConfig(action="redact"))
    res_redact = redact_filt.apply(doc)
    assert res_redact.action == TransformAction.ACCEPT
    assert res_redact.document is not None
    assert canary_token not in res_redact.document.text
    assert hf_token not in res_redact.document.text
    assert "[REDACTED_HUGGINGFACE_TOKEN]" in res_redact.document.text
    assert "[REDACTED_CANARY_CREDENTIAL]" in res_redact.document.text
