"""Acceptance tests for the operator-side final exclusion receipt interface.

EVALUATION_POLICY: the coding workspace must not read protected final data, a final
exclusion receipt must bind corpus inputs to a policy identity and output membership,
and a malformed, unsigned or untrusted receipt must be rejected under protected
policy. Everything here runs against synthetic fixtures only.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from xlm.data.exclusion import (
    RECEIPT_SCHEMA_VERSION,
    FinalExclusionReceipt,
    ProtectedAccessError,
    ReceiptValidationError,
    assert_no_protected_access,
    compute_corpus_input_digest,
    compute_membership_digest,
    issue_development_receipt,
    sign_receipt,
    verify_receipt,
)
from xlm.data.exclusion.receipt import BenchmarkClaimBinding, verify_benchmark_claim

TRUSTED_ISSUER = "operator_holdout_service"
TRUSTED_KEY = b"synthetic-operator-key-for-tests-only"
CORPUS = [f"doc_{i}" for i in range(10)]
DROPPED = ["doc_3", "doc_7"]


def protected_receipt(**overrides: object) -> FinalExclusionReceipt:
    """Build an unsigned protected-mode receipt over synthetic inputs."""
    kept = [d for d in CORPUS if d not in DROPPED]
    base = {
        "receipt_id": "rcpt_synthetic_1",
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "issued_at": "2026-09-19T00:00:00+00:00",
        "issuer_id": TRUSTED_ISSUER,
        "mode": "protected",
        "corpus_input_digest": compute_corpus_input_digest(CORPUS),
        "exclusion_policy_identity": "policy_abc123",
        "exclusion_index_identity": "index_def456",
        "output_membership_digest": compute_membership_digest(kept),
        "documents_considered": len(CORPUS),
        "documents_dropped": len(DROPPED),
        "dropped_doc_ids": list(DROPPED),
        "aggregate_counts": {"full_example": 1, "informative_span": 1},
        "notes": [],
        "signature": None,
    }
    base.update(overrides)
    return FinalExclusionReceipt(**base)  # type: ignore[arg-type]


def test_signed_receipt_from_a_trusted_issuer_verifies() -> None:
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def claim_binding(receipt: FinalExclusionReceipt) -> BenchmarkClaimBinding:
    return BenchmarkClaimBinding(
        checkpoint_hash="authored-checkpoint",
        suite_fingerprint="authored-suite",
        corpus_input_digest=receipt.corpus_input_digest,
        output_membership_digest=receipt.output_membership_digest,
        exclusion_policy_identity=receipt.exclusion_policy_identity,
        exclusion_index_identity=receipt.exclusion_index_identity,
    )


def test_acquisition_admission_never_substitutes_for_c05() -> None:
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    binding = claim_binding(signed)
    trusted = {TRUSTED_ISSUER: TRUSTED_KEY}
    with pytest.raises(ReceiptValidationError, match="bound C05 receipt"):
        verify_benchmark_claim(None, binding, trusted)
    with pytest.raises(ReceiptValidationError, match="bound C05 receipt"):
        verify_benchmark_claim(signed, None, trusted)
    verify_benchmark_claim(signed, binding, trusted)
    with pytest.raises(ReceiptValidationError, match="development"):
        verify_benchmark_claim(
            sign_receipt(protected_receipt(mode="development"), TRUSTED_KEY), binding, trusted
        )


@pytest.mark.parametrize(
    "field",
    [
        "corpus_input_digest",
        "output_membership_digest",
        "exclusion_policy_identity",
        "exclusion_index_identity",
    ],
)
def test_c05_claim_requires_exact_frozen_pool(field: str) -> None:
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    binding = claim_binding(signed)
    for value in ("foreign", "none_declared", ""):
        with pytest.raises(ReceiptValidationError):
            verify_benchmark_claim(
                signed, replace(binding, **{field: value}), {TRUSTED_ISSUER: TRUSTED_KEY}
            )


def test_unsigned_receipt_is_rejected_under_protected_policy() -> None:
    with pytest.raises(ReceiptValidationError, match="unsigned"):
        verify_receipt(protected_receipt(), {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_untrusted_issuer_is_rejected() -> None:
    """A valid signature from an unknown issuer is still untrusted."""
    rogue_key = b"rogue-key"
    signed = sign_receipt(protected_receipt(issuer_id="rogue_service"), rogue_key)
    with pytest.raises(ReceiptValidationError, match="not in the trusted issuer set"):
        verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_tampered_receipt_fails_signature_verification() -> None:
    """Altering a signed receipt must invalidate it."""
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    tampered = FinalExclusionReceipt(**signed.to_dict())
    tampered.dropped_doc_ids = ["doc_1", "doc_2"]

    with pytest.raises(ReceiptValidationError, match="signature does not verify"):
        verify_receipt(tampered, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_wrong_key_fails_signature_verification() -> None:
    signed = sign_receipt(protected_receipt(), b"a-different-key")
    with pytest.raises(ReceiptValidationError, match="signature does not verify"):
        verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"receipt_id": ""}, "malformed"),
        ({"issuer_id": ""}, "malformed"),
        ({"corpus_input_digest": ""}, "malformed"),
        ({"output_membership_digest": ""}, "malformed"),
        ({"schema_version": "99"}, "unsupported receipt schema version"),
        ({"documents_dropped": 5}, "inconsistent"),
        ({"documents_considered": 1}, "inconsistent"),
    ],
)
def test_malformed_receipts_are_rejected(overrides: dict[str, object], message: str) -> None:
    """Structural defects are caught before any signature check."""
    receipt = protected_receipt(**overrides)
    signed = sign_receipt(receipt, TRUSTED_KEY)
    with pytest.raises(ReceiptValidationError, match=message):
        verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_receipt_carrying_protected_content_is_rejected() -> None:
    """A receipt that smuggles labels or snippets is invalid, not merely untidy."""
    receipt = protected_receipt(aggregate_counts={"gold_labels": 4, "full_example": 1})
    signed = sign_receipt(receipt, TRUSTED_KEY)
    with pytest.raises(ReceiptValidationError, match="forbidden protected content"):
        verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_receipt_must_be_bound_to_the_submitted_corpus_batch() -> None:
    """A receipt for a different corpus batch must not be accepted for this one."""
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    other_digest = compute_corpus_input_digest(["different", "corpus"])

    verify_receipt(
        signed,
        {TRUSTED_ISSUER: TRUSTED_KEY},
        policy="protected",
        expected_corpus_digest=compute_corpus_input_digest(CORPUS),
    )
    with pytest.raises(ReceiptValidationError, match="not bound to this corpus batch"):
        verify_receipt(
            signed,
            {TRUSTED_ISSUER: TRUSTED_KEY},
            policy="protected",
            expected_corpus_digest=other_digest,
        )


def test_development_receipt_cannot_satisfy_a_protected_policy_check() -> None:
    """A locally issued receipt must never be mistaken for a sealed-final claim."""
    receipt = issue_development_receipt(
        corpus_doc_ids=CORPUS,
        dropped_doc_ids=DROPPED,
        exclusion_policy_identity="policy_abc123",
        exclusion_index_identity="index_def456",
    )
    assert receipt.mode == "development"
    assert any("not a sealed-final" in note.lower() for note in receipt.notes)

    # It is structurally valid under development policy...
    verify_receipt(receipt, {}, policy="development")

    # ...and is refused under protected policy even if someone signs it.
    with pytest.raises(ReceiptValidationError, match="development"):
        verify_receipt(
            sign_receipt(receipt, TRUSTED_KEY),
            {receipt.issuer_id: TRUSTED_KEY},
            policy="protected",
        )


def test_development_receipt_binds_inputs_and_output_membership() -> None:
    """Even a development receipt must bind corpus input to output membership."""
    receipt = issue_development_receipt(
        corpus_doc_ids=CORPUS,
        dropped_doc_ids=DROPPED,
        exclusion_policy_identity="policy_abc123",
        exclusion_index_identity="index_def456",
    )
    kept = [d for d in CORPUS if d not in DROPPED]

    assert receipt.corpus_input_digest == compute_corpus_input_digest(CORPUS)
    assert receipt.output_membership_digest == compute_membership_digest(kept)
    assert receipt.documents_considered == len(CORPUS)
    assert receipt.documents_dropped == len(DROPPED)

    # A different keep-set must produce a different membership digest.
    assert receipt.output_membership_digest != compute_membership_digest(CORPUS)


def test_receipt_contains_no_benchmark_text_or_example_identifiers() -> None:
    """The receipt is opaque: keep/drop decisions and counts only."""
    receipt = issue_development_receipt(
        corpus_doc_ids=CORPUS,
        dropped_doc_ids=DROPPED,
        exclusion_policy_identity="policy_abc123",
        exclusion_index_identity="index_def456",
        aggregate_counts={"full_example": 1, "informative_span": 1},
    )
    serialized = json.dumps(receipt.to_dict())
    for forbidden in ("gold", "label", "snippet", "matched_text", "answer"):
        assert forbidden not in serialized.lower(), f"receipt leaked '{forbidden}'"


def test_receipt_round_trips_through_disk(tmp_path: Path) -> None:
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    path = signed.save(tmp_path / "receipt.json")
    reloaded = FinalExclusionReceipt.load(path)

    assert reloaded.to_dict() == signed.to_dict()
    verify_receipt(reloaded, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="protected")


def test_receipt_with_unexpected_fields_is_rejected_on_load(tmp_path: Path) -> None:
    """An extra field may be smuggled protected content; refuse it rather than ignore it."""
    payload = protected_receipt().to_dict()
    payload["matched_snippets"] = ["some benchmark text"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReceiptValidationError, match="unexpected fields"):
        FinalExclusionReceipt.load(path)


def test_unknown_policy_name_is_rejected() -> None:
    signed = sign_receipt(protected_receipt(), TRUSTED_KEY)
    with pytest.raises(ReceiptValidationError, match="unknown receipt policy"):
        verify_receipt(signed, {TRUSTED_ISSUER: TRUSTED_KEY}, policy="sealed_ish")


def test_protected_paths_are_refused_in_the_coding_workspace(tmp_path: Path) -> None:
    """The workspace must refuse to read under a declared protected root."""
    protected_root = tmp_path / "holdout"
    (protected_root / "final").mkdir(parents=True)
    protected_file = protected_root / "final" / "labels.jsonl"
    protected_file.write_text("{}", encoding="utf-8")

    ordinary = tmp_path / "workspace" / "corpus.jsonl"
    ordinary.parent.mkdir(parents=True)
    ordinary.write_text("{}", encoding="utf-8")

    with pytest.raises(ProtectedAccessError, match="protected root"):
        assert_no_protected_access(protected_file, [protected_root])

    # An ordinary workspace path is unaffected.
    assert_no_protected_access(ordinary, [protected_root])


def test_protected_access_guard_is_documented_as_not_os_isolation() -> None:
    """The guard must not be presented as an isolation guarantee (EVALUATION_POLICY)."""
    doc = assert_no_protected_access.__doc__ or ""
    assert "not an OS security boundary" in doc
