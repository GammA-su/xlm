"""Tests for admission gates, audit, P01 persistence, and live verification seam (A12, A13, A15)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    AdmissionStatus,
    CatalogAuditor,
    load_admission_decision,
    load_probe_evidence,
    save_admission_decision,
    save_probe_evidence,
)
from xlm.data.sources.catalog import CandidateSourceEntry, load_catalog
from xlm.data.sources.policy import (
    LEGAL_DISCLAIMER,
    BenchmarkContaminationRisk,
    LicenseReviewStatus,
)
from xlm.data.sources.prober import (
    EvidenceType,
    ProbeEvidenceRecord,
    ProbeOutcome,
    SourceProber,
)
from xlm.data.sources.schema import FieldDescriptor, ViewSchema
from xlm.data.sources.transport import (
    HuggingFaceTransport,
    TransportBudget,
)


def _make_sample_view_schema(view_id: str = "test_view") -> ViewSchema:
    return ViewSchema(
        view_id=view_id,
        fields={
            "text": FieldDescriptor(name="text", type_name="string"),
            "doc_id": FieldDescriptor(name="doc_id", type_name="string"),
        },
        raw_schema_type="arrow",
    )


def test_admission_gate_positive_path() -> None:
    """Verify complete positive admission path when all criteria are satisfied."""
    schema = _make_sample_view_schema("v1")
    evidence = ProbeEvidenceRecord(
        source_id="test_candidate",
        view_id="v1",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision="a1b2c3d4e5f67890",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="fp_123456",
        verified_schema=schema,
        declared_license="Apache-2.0",
    )
    decision = AdmissionDecision(
        source_id="test_candidate",
        view_id="v1",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision="a1b2c3d4e5f67890",
        adapter_id="text_plain",
        probe_fingerprint="fp_123456",
        license_review=LicenseReviewStatus.APPROVED,
        benchmark_risk=BenchmarkContaminationRisk.CLEAN,
        operator_approved=True,
        operator_notes="Audited and approved for experimental research track.",
    )

    result = AdmissionGate.evaluate(evidence, decision)
    assert result.admitted is True
    assert result.status == AdmissionStatus.ADMITTED
    assert "All admission criteria verified" in result.reasons[0]


def test_admission_gate_synthetic_evidence_rejection() -> None:
    """Verify that synthetic fixture evidence is strictly rejected by production admission gates."""
    schema = _make_sample_view_schema("v1")
    evidence = ProbeEvidenceRecord(
        source_id="synthetic_candidate",
        view_id="v1",
        provider="local",
        repository="fixtures/sources/sample_nested",
        immutable_revision="sha_syn_123",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,  # Marked synthetic!
        probe_fingerprint="fp_syn",
        verified_schema=schema,
        declared_license="CC0-1.0",
    )
    decision = AdmissionDecision(
        source_id="synthetic_candidate",
        view_id="v1",
        provider="local",
        repository="fixtures/sources/sample_nested",
        immutable_revision="sha_syn_123",
        adapter_id="text_plain",
        probe_fingerprint="fp_syn",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,
    )

    result = AdmissionGate.evaluate(evidence, decision)
    assert result.admitted is False
    assert result.status == AdmissionStatus.UNADMITTED
    assert any("Synthetic fixture evidence is strictly prohibited" in r for r in result.reasons)


def test_admission_gate_operator_approval_cannot_override_denial_or_missing_revision() -> None:
    """Verify operator approval cannot override missing evidence or direct-source denial."""
    # 1. Denied repository
    denied_evidence = ProbeEvidenceRecord(
        source_id="fineweb_attempt",
        provider="huggingface",
        repository="HuggingFaceFW/fineweb",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
    )
    denied_decision = AdmissionDecision(
        source_id="fineweb_attempt",
        provider="huggingface",
        repository="HuggingFaceFW/fineweb",
        immutable_revision="abc",
        adapter_id="test",
        probe_fingerprint="fp",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,  # Operator erroneously set to True
    )
    res_denied = AdmissionGate.evaluate(denied_evidence, denied_decision)
    assert res_denied.admitted is False
    assert res_denied.status == AdmissionStatus.BLOCKED

    # 2. Missing revision
    missing_rev_evidence = ProbeEvidenceRecord(
        source_id="no_rev_source",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision=None,  # Missing!
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        verified_schema=_make_sample_view_schema("v1"),
        declared_license="MIT",
    )
    missing_rev_decision = AdmissionDecision(
        source_id="no_rev_source",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision="",
        adapter_id="test",
        probe_fingerprint="fp",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,
    )
    res_rev = AdmissionGate.evaluate(missing_rev_evidence, missing_rev_decision)
    assert res_rev.admitted is False
    assert any("Immutable commit revision" in r for r in res_rev.reasons)


def test_admission_gate_stale_fingerprint_invalidation() -> None:
    """Verify that when schema or snapshot changes, previous decision fingerprint is invalidated."""
    schema = _make_sample_view_schema("v1")
    evidence = ProbeEvidenceRecord(
        source_id="modified_source",
        view_id="v1",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision="new_sha_999",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="new_fingerprint_abc",
        verified_schema=schema,
        declared_license="MIT",
    )
    # Stale decision recorded with older fingerprint
    stale_decision = AdmissionDecision(
        source_id="modified_source",
        view_id="v1",
        provider="huggingface",
        repository="valid/repo",
        immutable_revision="old_sha_111",
        adapter_id="text_plain",
        probe_fingerprint="old_fingerprint_xyz",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,
    )

    result = AdmissionGate.evaluate(evidence, stale_decision)
    assert result.admitted is False
    assert result.status == AdmissionStatus.PENDING_REVIEW
    assert any("fingerprint does not match" in r for r in result.reasons)


def test_catalog_auditor_all_twenty_candidates_unadmitted(isolated_xlm_home: Path) -> None:
    """Verify that catalog audit on all candidates produces 0 admitted sources and disclaimer."""
    catalog = load_catalog("manifests/datasets.catalog.yaml")
    store = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    auditor = CatalogAuditor(catalog=catalog, artifact_store=store)

    report = auditor.audit_all()
    assert report["counts"]["total_candidates"] == 21
    assert report["counts"]["admitted"] == 0
    assert report["counts"]["blocked"] == 0
    assert report["counts"]["unadmitted"] == 21
    assert report["legal_disclaimer"] == LEGAL_DISCLAIMER
    assert catalog.get_source("ultrax_ultrafineweb") is not None


def test_p01_artifact_persistence(isolated_xlm_home: Path) -> None:
    """Verify persisting and loading probe evidence and admission decisions using ArtifactStore."""
    store = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))

    evidence = ProbeEvidenceRecord(
        source_id="art_test",
        view_id="default",
        provider="huggingface",
        repository="test/repo",
        immutable_revision="sha_persist_123",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
        probe_fingerprint="fp_persist",
        observed_files_count=12,
    )
    save_probe_evidence(evidence, store)

    loaded_evidence = load_probe_evidence("art_test", "default", store)
    assert loaded_evidence is not None
    assert loaded_evidence.source_id == "art_test"
    assert loaded_evidence.immutable_revision == "sha_persist_123"
    assert loaded_evidence.observed_files_count == 12

    decision = AdmissionDecision(
        source_id="art_test",
        view_id="default",
        provider="huggingface",
        repository="test/repo",
        immutable_revision="sha_persist_123",
        adapter_id="text_plain",
        probe_fingerprint="fp_persist",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,
    )
    save_admission_decision(decision, store)

    loaded_decision = load_admission_decision("art_test", "default", store)
    assert loaded_decision is not None
    assert loaded_decision.source_id == "art_test"
    assert loaded_decision.operator_approved is True


def test_base_environment_zero_torch_isolation() -> None:
    """Verify that importing xlm.data.sources in a fresh process does not import PyTorch."""
    import subprocess

    code = (
        "import sys; "
        "import xlm.data.sources; "
        "assert 'torch' not in sys.modules, f'torch was imported: {sys.modules.get(\"torch\")}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Import isolation failed: {res.stderr}"


def test_admission_gate_view_mismatch() -> None:
    """Verify approval for one view does not authorize a different view."""
    schema = _make_sample_view_schema("view_organic")
    evidence = ProbeEvidenceRecord(
        source_id="nemotron_source",
        view_id="view_synthetic",  # Evidence is for synthetic view
        provider="huggingface",
        repository="nvidia/Nemotron-CC-v2.1",
        immutable_revision="sha_nem_123",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="fp_synthetic",
        verified_schema=schema,
        declared_license="Apache-2.0",
    )
    decision = AdmissionDecision(
        source_id="nemotron_source",
        view_id="view_organic",  # Decision was only for organic view!
        provider="huggingface",
        repository="nvidia/Nemotron-CC-v2.1",
        immutable_revision="sha_nem_123",
        adapter_id="text_plain",
        probe_fingerprint="fp_organic",
        license_review=LicenseReviewStatus.APPROVED,
        operator_approved=True,
    )

    result = AdmissionGate.evaluate(evidence, decision)
    assert result.admitted is False
    assert result.status == AdmissionStatus.PENDING_REVIEW
    assert any("fingerprint does not match" in r for r in result.reasons)


def test_admission_gate_benchmark_risk_block() -> None:
    """Verify benchmark contamination risk (disabled_pending_audit) blocks admission."""
    schema = _make_sample_view_schema("v1")
    evidence = ProbeEvidenceRecord(
        source_id="blend_source",
        view_id="v1",
        provider="huggingface",
        repository="org/blend-repo",
        immutable_revision="sha_blend_123",
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="fp_blend",
        verified_schema=schema,
        declared_license="Apache-2.0",
    )
    decision = AdmissionDecision(
        source_id="blend_source",
        view_id="v1",
        provider="huggingface",
        repository="org/blend-repo",
        immutable_revision="sha_blend_123",
        adapter_id="text_plain",
        probe_fingerprint="fp_blend",
        license_review=LicenseReviewStatus.APPROVED,
        benchmark_risk=BenchmarkContaminationRisk.DISABLED_PENDING_AUDIT,  # Blocked!
        operator_approved=True,
    )

    result = AdmissionGate.evaluate(evidence, decision)
    assert result.admitted is False
    assert any("Benchmark risk status" in r for r in result.reasons)


@pytest.mark.network
def test_live_public_metadata_probe_seam() -> None:
    """Verify A15: Bounded live public discovery probe on public repository.

    Ensures that real provider response resolves commit SHA and metadata,
    and explicitly verifies that successful live probe does NOT auto-admit the candidate.
    """
    candidate = CandidateSourceEntry(
        candidate_number=14,
        source_id="finewiki",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
    )
    budget = TransportBudget(max_bytes=2 * 1024 * 1024, max_requests=5)
    transport = HuggingFaceTransport(budget)
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=budget,
        view_id="default",
        evidence_type=EvidenceType.REAL_OBSERVED,
    )

    evidence = prober.probe()
    assert evidence.outcome in (ProbeOutcome.ACCESSIBLE, ProbeOutcome.PARTIAL)
    assert evidence.immutable_revision is not None
    assert len(evidence.immutable_revision) >= 8, "Expected real Git commit SHA"
    assert evidence.evidence_type == EvidenceType.REAL_OBSERVED

    # Crucial A15 gate verification:
    # Successful live probe metadata does NOT auto-admit into production!
    gate_res = AdmissionGate.evaluate(evidence, decision=None)
    assert gate_res.admitted is False
    assert gate_res.status in (AdmissionStatus.UNADMITTED, AdmissionStatus.PENDING_REVIEW)
    assert len(gate_res.reasons) > 0
