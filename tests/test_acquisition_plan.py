"""Unit tests for acquisition plan schemas, authorization validation, and sampling frames."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    AuthorizationRequiredError,
    PlanAuthorization,
    SamplingFrame,
    load_acquisition_plan,
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
