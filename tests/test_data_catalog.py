"""Tests for dataset catalog parsing, uniqueness, denial policies, and license rules (C04, A13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.data.sources.catalog import (
    CandidateSourceEntry,
    DatasetCatalogDraft,
    load_catalog,
)
from xlm.data.sources.policy import (
    DENIED_DIRECT_SOURCES,
    DirectSourceDeniedError,
    SilentFallbackDeniedError,
    check_denial_policy,
    check_silent_fallback,
    evaluate_license_review,
    is_denied_source,
)


def test_twenty_source_catalog_parsing_and_integrity() -> None:
    """Verify that manifests/datasets.catalog.yaml loads and fulfills A12 requirements."""
    catalog_path = Path("manifests/datasets.catalog.yaml")
    assert catalog_path.is_file(), f"Catalog file missing: {catalog_path}"

    catalog = load_catalog(catalog_path)
    assert catalog.catalog_id == "xlm_recent_candidates_v1"
    assert catalog.status == "discovery_only"
    assert catalog.silent_fallback_allowed is False

    # Check that the shortlist candidate catalog has exactly 21 candidates
    # (20 historical + ultrax_ultrafineweb).
    assert len(catalog.sources) == 21

    # Ensure all source IDs and candidate numbers are strictly unique
    source_ids = [src.source_id for src in catalog.sources]
    candidate_numbers = [src.candidate_number for src in catalog.sources]
    assert len(set(source_ids)) == 21
    assert len(set(candidate_numbers)) == 21
    assert sorted(candidate_numbers) == list(range(1, 22))

    # All production approvals must initially remain False. The UltraX probe
    # revision is the single exception: it is pinned by the operator freeze
    # (exact 40-hex SHA, never main/latest) while approval stays pending.
    for src in catalog.sources:
        assert src.operator_approved is False, f"Source '{src.source_id}' was prematurely approved!"
        if src.source_id == "ultrax_ultrafineweb":
            assert src.revision == "a88527587389fd4ab352e9ad1273f4c0a234d8df"
        else:
            assert src.revision is None, f"Source '{src.source_id}' has premature revision."
        assert src.schema_fingerprint is None
        assert src.live_pilot_verified is False


def test_generic_catalog_does_not_hardcode_twenty() -> None:
    """Verify generic catalog engine handles arbitary candidate counts, not hardcoded to twenty."""
    single_source = CandidateSourceEntry(
        candidate_number=1,
        source_id="toy_source",
        provider="local",
        repository="fixtures/sources/sample_local",
    )
    draft = DatasetCatalogDraft(
        catalog_id="toy_catalog",
        sources=[single_source],
    )
    assert len(draft.sources) == 1
    assert draft.get_source("toy_source") == single_source
    assert draft.get_source_by_number(1) == single_source
    assert draft.get_source("nonexistent") is None


def test_fineweb_denial_policy_and_safeguards() -> None:
    """Verify A13: No FineWeb / FineWeb-Edu substitution or fallback is permitted."""
    assert "huggingfacefw/fineweb" in DENIED_DIRECT_SOURCES
    assert "huggingfacefw/fineweb-edu" in DENIED_DIRECT_SOURCES

    # Direct name variations
    assert is_denied_source("HuggingFaceFW/fineweb")
    assert is_denied_source("huggingfacefw/fineweb")
    assert is_denied_source("HuggingFaceFW/fineweb-edu")
    assert is_denied_source("https://huggingface.co/datasets/HuggingFaceFW/fineweb")
    assert is_denied_source("https://hf.co/datasets/HuggingFaceFW/fineweb-edu/")

    # FineWiki and FinePDFs-Edu must NOT be denied (they are valid shortlisted candidates)
    assert not is_denied_source("HuggingFaceFW/finewiki")
    assert not is_denied_source("HuggingFaceFW/finepdfs-edu")
    assert not is_denied_source("IFM/TxT360-v2")

    # check_denial_policy raises DirectSourceDeniedError
    with pytest.raises(DirectSourceDeniedError, match="explicitly denied"):
        check_denial_policy("HuggingFaceFW/fineweb")

    with pytest.raises(DirectSourceDeniedError, match="explicitly denied"):
        check_denial_policy("https://huggingface.co/datasets/HuggingFaceFW/fineweb-edu")

    # Non-denied sources pass without exception
    check_denial_policy("HuggingFaceFW/finewiki")
    check_denial_policy("HuggingFaceFW/finepdfs-edu")


def test_silent_fallback_prevention() -> None:
    """Verify that silent fallback or renormalization raises SilentFallbackDeniedError."""
    with pytest.raises(SilentFallbackDeniedError, match="silent substitution"):
        check_silent_fallback("txt360_v2", "HuggingFaceFW/fineweb")

    with pytest.raises(SilentFallbackDeniedError, match="silent substitution"):
        check_silent_fallback("ifm_behaviors", "essential_web")

    # Identical requested source does not raise
    check_silent_fallback("essential_web", "essential_web")


def test_license_evaluation_rules() -> None:
    """Verify license rules: unknown licenses cannot be auto-approved; pending review fails."""
    # Pending review
    is_ok, reason = evaluate_license_review("MIT", "pending")
    assert not is_ok
    assert "not 'approved'" in reason

    # Unknown license with approved status
    is_ok, reason = evaluate_license_review("unknown", "approved")
    assert not is_ok
    assert "cannot be auto-approved" in reason

    is_ok, reason = evaluate_license_review(None, "approved")
    assert not is_ok
    assert "cannot be auto-approved" in reason

    # Valid approved license
    is_ok, reason = evaluate_license_review("Apache-2.0", "approved")
    assert is_ok
    assert "approved under declared usage policy" in reason
