"""Unit tests for acquisition plan schemas, authorization validation, and sampling frames."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    AuthorizationRequiredError,
    PlanAuthorization,
    SamplingFrame,
    load_acquisition_plan,
    plan_requires_production_admission,
    save_acquisition_plan,
    validate_plan_authorization,
)
from xlm.data.sources.policy import DirectSourceDeniedError


def test_plan_behavioral_hash_invariance() -> None:
    """Verify that behavioral hash is deterministic and excludes cosmetic fields and timestamps."""
    plan1 = AcquisitionPlan(
        plan_id="plan_test_001",
        source_id="finewiki",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision="8bd13e72e6a002407649b3e898535f42ceb1aeb9",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train-00000.parquet"],
        output_artifact_id="raw_finewiki_default",
        is_pilot=True,
    )
    # Plan2 has different plan_id and authorization record but same behavioral fields
    plan2 = AcquisitionPlan(
        plan_id="plan_test_002_different_name",
        source_id="finewiki",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision="8bd13e72e6a002407649b3e898535f42ceb1aeb9",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train-00000.parquet"],
        output_artifact_id="raw_finewiki_default",
        is_pilot=True,
        authorization=PlanAuthorization(
            authorization_hash="some_hash",
            authorized_by="operator",
            authorized_at="2026-09-19T12:00:00Z",
        ),
    )

    assert plan1.compute_behavioral_hash() == plan2.compute_behavioral_hash()

    # Changing a behavioral field changes the hash
    plan3 = plan1.model_copy(update={"selected_files": ["data/train-00001.parquet"]})
    assert plan1.compute_behavioral_hash() != plan3.compute_behavioral_hash()


def test_plan_pilot_approval_enforcement() -> None:
    """Verify that being below pilot limits requires explicit operator pilot approval."""
    plan = AcquisitionPlan(
        plan_id="plan_test_pilot",
        source_id="finewiki",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision="8bd13e72e6a002407649b3e898535f42ceb1aeb9",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train-00000.parquet"],
        output_artifact_id="raw_finewiki_default",
        is_pilot=True,
    )

    # Lacks authorization / pilot approval -> must raise AuthorizationRequiredError
    with pytest.raises(
        AuthorizationRequiredError, match="requires explicit operator pilot approval"
    ):
        validate_plan_authorization(plan)

    # Adding explicit operator pilot approval passes
    approved_plan = plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="operator_cli",
                authorized_at="2026-09-19T12:00:00Z",
                is_pilot_approved=True,
            )
        }
    )
    validate_plan_authorization(approved_plan)


def test_plan_production_authorization_enforcement() -> None:
    """Verify that operations exceeding pilot limits require production admission & hash."""
    large_plan = AcquisitionPlan(
        plan_id="plan_test_prod",
        source_id="finewiki",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision="8bd13e72e6a002407649b3e898535f42ceb1aeb9",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train-00000.parquet"],
        output_artifact_id="raw_finewiki_default",
        is_pilot=False,  # Production
        limits=AcquisitionLimits(
            max_transferred_bytes=10 * 1024 * 1024 * 1024,  # 10 GiB > pilot
        ),
    )

    # 1. No admission reference or catalog approval
    with pytest.raises(AuthorizationRequiredError, match="requires prior operator admission"):
        validate_plan_authorization(large_plan, catalog_source_approved=False)

    # 2. Has catalog approval but no authorization record
    with pytest.raises(AuthorizationRequiredError, match="requires an explicit PlanAuthorization"):
        validate_plan_authorization(large_plan, catalog_source_approved=True)

    # 3. Has authorization record but hash does not match
    mismatched_auth = large_plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash="wrong_hash_value",
                authorized_by="compliance_officer",
                authorized_at="2026-09-19T12:00:00Z",
                scope="production",
            )
        }
    )
    with pytest.raises(AuthorizationRequiredError, match="Authorization hash mismatch"):
        validate_plan_authorization(mismatched_auth, catalog_source_approved=True)

    # 4. Correct matching authorization hash
    valid_auth = large_plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=large_plan.compute_behavioral_hash(),
                authorized_by="compliance_officer",
                authorized_at="2026-09-19T12:00:00Z",
                scope="production",
            )
        }
    )
    validate_plan_authorization(valid_auth, catalog_source_approved=True)


def test_plan_denied_fineweb_policy() -> None:
    """Verify that plans attempting to target denied FineWeb repositories are blocked."""
    plan = AcquisitionPlan(
        plan_id="plan_fineweb_attempt",
        source_id="denied_fineweb",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/fineweb",
        revision="abc123456",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train.parquet"],
        output_artifact_id="raw_denied",
        is_pilot=True,
    )
    with pytest.raises(DirectSourceDeniedError, match="explicitly denied"):
        validate_plan_authorization(plan)


def test_plan_serialization_roundtrip(tmp_path: Path) -> None:
    """Verify JSON serialization, hash embedding, and load validation."""
    plan = AcquisitionPlan(
        plan_id="plan_roundtrip",
        source_id="finewiki",
        view_id="default",
        provider="huggingface",
        repository="HuggingFaceFW/finewiki",
        revision="8bd13e72e6a002407649b3e898535f42ceb1aeb9",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["data/train-00000.parquet"],
        output_artifact_id="raw_finewiki",
        sampling_frame=SamplingFrame(
            selected_files=["data/train-00000.parquet"],
            coverage_notes="First shard covering English articles",
        ),
    )
    target_path = tmp_path / "plan.json"
    save_acquisition_plan(plan, target_path)

    loaded_plan = load_acquisition_plan(target_path)
    assert loaded_plan.plan_id == plan.plan_id
    assert loaded_plan.plan_hash == plan.compute_behavioral_hash()
    assert loaded_plan.sampling_frame.coverage_notes == "First shard covering English articles"
    assert "not a uniform random sample" in loaded_plan.sampling_frame.selection_bias_warning


# ------------------------------------------------- production admission scope


def _production_plan(**overrides: Any) -> AcquisitionPlan:
    fields: dict[str, Any] = {
        "plan_id": "plan_prod_scope",
        "source_id": "authored",
        "view_id": "default",
        "provider": "https",
        "repository": "https://127.0.0.1:9/files",
        "revision": "rev0000000000000000000000000000000000000001",
        "mode": AcquisitionMode.WHOLE_FILE,
        "selected_files": ["data.jsonl"],
        "output_artifact_id": "raw_authored_default",
        "is_pilot": False,
        "limits": AcquisitionLimits(max_transferred_bytes=4096, max_records=10),
    }
    fields.update(overrides)
    return AcquisitionPlan(**fields)


def _production_store(tmp_path: Path) -> Any:
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths

    return ArtifactStore(ArtifactPaths(root=tmp_path / "home"))


def _plant_production_state(
    tmp_path: Path,
    *,
    revision: str | None = None,
    approved: bool = True,
    fingerprint: str = "fp_production_1",
    decision_fingerprint: str | None = None,
) -> tuple[Any, Any, Any]:
    """Plant probe evidence plus an admission decision through the real services."""
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sources.admission import save_admission_decision, save_probe_evidence
    from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
    from xlm.data.sources.schema import FieldDescriptor, ViewSchema

    store = ArtifactStore(ArtifactPaths(root=tmp_path / "home"))
    revision = revision or "rev0000000000000000000000000000000000000001"
    schema = ViewSchema(
        view_id="default",
        fields={"text": FieldDescriptor(name="text", type_name="string")},
        raw_schema_type="arrow",
    )
    evidence = ProbeEvidenceRecord(
        source_id="authored",
        view_id="default",
        provider="https",
        repository="https://127.0.0.1:9/files",
        immutable_revision=revision,
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint=fingerprint,
        verified_schema=schema,
        declared_license="Apache-2.0",
    )
    save_probe_evidence(evidence, store, staging_dir=tmp_path / "staging-probe")
    decision_kwargs: dict[str, Any] = {
        "source_id": "authored",
        "view_id": "default",
        "provider": "https",
        "repository": "https://127.0.0.1:9/files",
        "immutable_revision": revision,
        "adapter_id": "JsonlAdapter",
        "probe_fingerprint": decision_fingerprint or fingerprint,
        "license_review": "approved",
        "benchmark_risk": "clean",
        "operator_approved": approved,
        "operator_notes": "closeout fixture review",
    }
    from xlm.data.sources.admission import AdmissionDecision

    save_admission_decision(
        AdmissionDecision(**decision_kwargs), store, staging_dir=tmp_path / "staging-decision"
    )
    return store, evidence, decision_kwargs


def _authorized_production_plan(**overrides: Any) -> AcquisitionPlan:
    plan = _production_plan(**overrides)
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="closeout-fixture",
                authorized_at="2026-09-21T00:00:00Z",
                scope="production",
            )
        }
    )


def test_production_scope_boundary_matches_validator(tmp_path: Path) -> None:
    pilot = _production_plan(is_pilot=True, limits=AcquisitionLimits(max_transferred_bytes=1024))
    assert plan_requires_production_admission(pilot) is False
    assert plan_requires_production_admission(_production_plan()) is True
    over_threshold = _production_plan(
        is_pilot=True, limits=AcquisitionLimits(max_transferred_bytes=300 * 1024**2)
    )
    assert plan_requires_production_admission(over_threshold) is True


def test_production_admission_resolves_for_matching_store_state(tmp_path: Path) -> None:
    from xlm.data.sources.admission import resolve_verified_production_admission

    store, _, _ = _plant_production_state(tmp_path)
    plan = _authorized_production_plan()
    resolve_verified_production_admission(plan, store)
    validate_plan_authorization(plan, catalog_source_approved=True)


def test_production_admission_missing_evidence_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import resolve_verified_production_admission

    store = _production_store(tmp_path)
    with pytest.raises(AuthorizationRequiredError, match="no probe evidence"):
        resolve_verified_production_admission(_authorized_production_plan(), store)


def test_production_admission_missing_decision_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import (
        resolve_verified_production_admission,
        save_probe_evidence,
    )
    from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
    from xlm.data.sources.schema import FieldDescriptor, ViewSchema

    store = _production_store(tmp_path)
    schema = ViewSchema(
        view_id="default",
        fields={"text": FieldDescriptor(name="text", type_name="string")},
        raw_schema_type="arrow",
    )
    save_probe_evidence(
        ProbeEvidenceRecord(
            source_id="authored",
            view_id="default",
            provider="https",
            repository="https://127.0.0.1:9/files",
            immutable_revision="rev0000000000000000000000000000000000000001",
            outcome=ProbeOutcome.ACCESSIBLE,
            evidence_type=EvidenceType.REAL_OBSERVED,
            probe_fingerprint="fp_production_1",
            verified_schema=schema,
            declared_license="Apache-2.0",
        ),
        store,
        staging_dir=tmp_path / "staging-probe",
    )
    with pytest.raises(AuthorizationRequiredError, match="no recorded operator admission"):
        resolve_verified_production_admission(_authorized_production_plan(), store)


def test_production_admission_rejected_decision_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import resolve_verified_production_admission

    store, _, _ = _plant_production_state(tmp_path, approved=False)
    with pytest.raises(AuthorizationRequiredError, match="not admitted"):
        resolve_verified_production_admission(_authorized_production_plan(), store)


def test_production_admission_stale_fingerprint_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import resolve_verified_production_admission

    store, _, _ = _plant_production_state(tmp_path, decision_fingerprint="fp_stale_unrelated")
    with pytest.raises(AuthorizationRequiredError, match="not admitted"):
        resolve_verified_production_admission(_authorized_production_plan(), store)


def test_production_admission_revision_mismatch_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import resolve_verified_production_admission

    store, _, _ = _plant_production_state(tmp_path)
    plan = _authorized_production_plan(revision="rev0000000000000000000000000000000000000002")
    with pytest.raises(AuthorizationRequiredError, match="does not match plan revision"):
        resolve_verified_production_admission(plan, store)


def test_production_admission_synthetic_evidence_is_refused(tmp_path: Path) -> None:
    from xlm.data.sources.admission import (
        AdmissionDecision,
        resolve_verified_production_admission,
        save_admission_decision,
        save_probe_evidence,
    )
    from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
    from xlm.data.sources.schema import FieldDescriptor, ViewSchema

    store = _production_store(tmp_path)
    schema = ViewSchema(
        view_id="default",
        fields={"text": FieldDescriptor(name="text", type_name="string")},
        raw_schema_type="arrow",
    )
    save_probe_evidence(
        ProbeEvidenceRecord(
            source_id="authored",
            view_id="default",
            provider="https",
            repository="https://127.0.0.1:9/files",
            immutable_revision="rev0000000000000000000000000000000000000001",
            outcome=ProbeOutcome.ACCESSIBLE,
            evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
            probe_fingerprint="fp_production_1",
            verified_schema=schema,
            declared_license="Apache-2.0",
        ),
        store,
        staging_dir=tmp_path / "staging-probe",
    )
    save_admission_decision(
        AdmissionDecision(
            source_id="authored",
            view_id="default",
            provider="https",
            repository="https://127.0.0.1:9/files",
            immutable_revision="rev0000000000000000000000000000000000000001",
            adapter_id="JsonlAdapter",
            probe_fingerprint="fp_production_1",
            license_review="approved",
            benchmark_risk="clean",
            operator_approved=True,
            operator_notes="synthetic must still be refused",
        ),
        store,
        staging_dir=tmp_path / "staging-decision",
    )
    with pytest.raises(AuthorizationRequiredError, match="not admitted"):
        resolve_verified_production_admission(_authorized_production_plan(), store)
