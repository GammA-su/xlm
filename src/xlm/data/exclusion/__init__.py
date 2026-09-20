"""Benchmark contamination matching and protected final exclusion receipts."""

from __future__ import annotations

from xlm.data.exclusion.benchmark import (
    EXCLUSION_POLICY_VERSION,
    BenchmarkExample,
    BenchmarkExclusionMatcher,
    ExclusionConfig,
    ExclusionHit,
    ExclusionReport,
)
from xlm.data.exclusion.receipt import (
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

__all__ = [
    "EXCLUSION_POLICY_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "BenchmarkExample",
    "BenchmarkExclusionMatcher",
    "ExclusionConfig",
    "ExclusionHit",
    "ExclusionReport",
    "FinalExclusionReceipt",
    "ProtectedAccessError",
    "ReceiptValidationError",
    "assert_no_protected_access",
    "compute_corpus_input_digest",
    "compute_membership_digest",
    "issue_development_receipt",
    "sign_receipt",
    "verify_receipt",
]
