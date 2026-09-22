"""Reserved example-domain email placeholders: authored fixtures, offline only."""

from __future__ import annotations

from pathlib import Path

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.pii import (
    PiiConfig,
    PiiSecretFilter,
    is_reserved_example_email,
    redact_sensitive_text,
)
from xlm.data.cleaning.quarantine import QuarantineManager
from xlm.data.cleaning.types import TransformAction
from xlm.data.normalization import compute_sha256


def _doc(doc_id: str, text: str) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_filter",
        source_revision="rev_1",
        source_file="test.jsonl",
        source_row=1,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="mit",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def _filter() -> PiiSecretFilter:
    return PiiSecretFilter(PiiConfig(action="reject"))


def test_reserved_domains_are_placeholders() -> None:
    assert is_reserved_example_email("user@example.com")
    assert is_reserved_example_email("user@example.org")
    assert is_reserved_example_email("user@example.net")
    assert is_reserved_example_email("Contact@example.COM")
    assert is_reserved_example_email("a.b+tag@mail.example.org")
    assert is_reserved_example_email("user@example")
    assert is_reserved_example_email("user@deep.sub.example.net")


def test_lookalikes_stay_sensitive() -> None:
    assert not is_reserved_example_email("user@notreallyexample.com")
    assert not is_reserved_example_email("user@example.org.attacker.com")
    assert not is_reserved_example_email("user@my-example.com")
    assert not is_reserved_example_email("user@example.fr")
    assert not is_reserved_example_email("person@university.edu")
    assert not is_reserved_example_email("person@gmail.com")


def test_reserved_only_document_survives() -> None:
    text = "Contact: professor@example.org and school@example.com for details."
    result = _filter().apply(_doc("reserved-only", text))
    assert result.action == TransformAction.ACCEPT
    assert result.reasons == []
    assert result.metrics.reserved_email_placeholders_ignored == 2
    assert result.metrics.detected_secrets == []


def test_multiple_reserved_placeholders_survive() -> None:
    text = "Write to a@example.com, b@example.net, c@example.org, d@x.example.com."
    result = _filter().apply(_doc("multi", text))
    assert result.action == TransformAction.ACCEPT
    assert result.metrics.reserved_email_placeholders_ignored == 4


def test_real_addresses_still_rejected() -> None:
    for address in (
        "person@university.edu",
        "person@gmail.com",
        "person@example.fr",
        "user@notreallyexample.com",
        "user@example.org.attacker.com",
        "user@my-example.com",
    ):
        result = _filter().apply(_doc(f"real-{address}", f"Contact {address} today."))
        assert result.action == TransformAction.REJECT, address
        assert result.reasons == ["detected_secret:email_address"], address
        assert result.metrics.reserved_email_placeholders_ignored == 0, address


def test_uppercase_reserved_domain_survives() -> None:
    result = _filter().apply(_doc("upper", "Contact PROF@EXAMPLE.ORG urgently."))
    assert result.action == TransformAction.ACCEPT
    assert result.metrics.reserved_email_placeholders_ignored == 1


def test_reserved_plus_ssn_still_rejects_for_ssn() -> None:
    text = "Contact placeholder@example.com. SSN on file 123-45-6789 for payroll."
    result = _filter().apply(_doc("ssn-mix", text))
    assert result.action == TransformAction.REJECT
    assert result.reasons == ["detected_secret:ssn"]
    assert result.metrics.reserved_email_placeholders_ignored == 1


def test_reserved_plus_real_email_rejects_for_email() -> None:
    text = "Write placeholder@example.com or person@gmail.com for help."
    result = _filter().apply(_doc("mix", text))
    assert result.action == TransformAction.REJECT
    assert result.reasons == ["detected_secret:email_address"]
    assert result.metrics.reserved_email_placeholders_ignored == 1


def test_redact_keeps_reserved_placeholders() -> None:
    filt = PiiSecretFilter(PiiConfig(action="redact"))
    text = "Contact placeholder@example.com or person@gmail.com for help."
    result = filt.apply(_doc("redact", text))
    assert result.action == TransformAction.ACCEPT
    assert result.document is not None
    assert "placeholder@example.com" in result.document.text
    assert "person@gmail.com" not in result.document.text
    assert "[REDACTED_EMAIL_ADDRESS]" in result.document.text


def test_redact_sensitive_text_helper() -> None:
    assert redact_sensitive_text("mail placeholder@example.org now") == (
        "mail placeholder@example.org now"
    )
    assert "[REDACTED_EMAIL_ADDRESS]" in redact_sensitive_text("mail a@b.edu now")


def test_metrics_deterministic_across_runs() -> None:
    text = "Contact a@example.com and b@example.org, also person@gmail.com."
    first = _filter().apply(_doc("d1", text))
    second = _filter().apply(_doc("d2", text))
    assert first.metrics.reserved_email_placeholders_ignored == 2
    assert first.metrics.detected_secrets == ["email_address"]
    assert first.metrics.to_dict() == second.metrics.to_dict()
    assert first.reasons == second.reasons == ["detected_secret:email_address"]


def test_quarantine_omission_unchanged_for_real_pii(tmp_path: Path) -> None:
    doc = _doc("real", "Contact person@gmail.com today.")
    result = _filter().apply(doc)
    assert result.action == TransformAction.REJECT
    manager = QuarantineManager(tmp_path / "quarantine")
    manager.record_rejection(doc, result.reasons, "pii_secret_filter", result.metrics)
    content = (tmp_path / "quarantine" / "quarantine.jsonl").read_text(encoding="utf-8")
    assert "person@gmail.com" not in content
    assert '"is_secret_omitted": true' in content
    assert '"sanitized_preview": null' in content


def test_no_doc_ids_involved() -> None:
    first = _filter().apply(_doc("same-a", "Contact x@example.com."))
    second = _filter().apply(_doc("same-b", "Contact x@example.com."))
    assert first.action == second.action == TransformAction.ACCEPT
