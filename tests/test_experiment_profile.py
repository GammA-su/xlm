"""Measured-profile binding: produced profiles validate against training plans.

All fixtures here are authored synthetic declarations in the real profile
schema, labeled as such. They prove the binding and refusal logic — never a
measured production profile, which only `xlm profile` on real hardware
produces and which no test here performs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCOPE = "authored-test-only, not a measured production profile"


def _model_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "architecture": "transformer_baseline",
        "vocab_size": 512,
        "num_layers": 2,
        "hidden_size": 64,
        "num_attention_heads": 4,
        "intermediate_size": 128,
        "context_length": 128,
        "attention_backend": "eager",
    }
    config.update(overrides)
    return config


def _training_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "device": "cpu",
        "precision": "fp32",
        "context_length": 128,
        "global_batch_valid_targets": 1024,
    }
    config.update(overrides)
    return config


def _profile_fixture(
    model_config: dict[str, Any] | None = None,
    training: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Authored profile-shaped fixture (see module docstring for scope)."""
    model = model_config if model_config is not None else _model_config()
    train = training if training is not None else _training_config()
    payload: dict[str, Any] = {
        "profile_version": "1",
        "model_id": "fixture-model",
        "request": {
            "model_id": "fixture-model",
            "model_config": dict(model),
            "device": train["device"],
            "precision": train["precision"],
            "attention_backend": model["attention_backend"],
            "global_batch_valid_targets": train["global_batch_valid_targets"],
            "context_length": train["context_length"],
        },
        "selected_microbatch_sequences": 4,
        "resource_plan": {
            "throughput_tokens_per_sec_range": [100.0, 200.0],
            "eta_seconds_range": [5.0, 10.0],
            "budget_valid_targets": 1024,
        },
        "fixture_scope": FIXTURE_SCOPE,
    }
    payload.update(overrides)
    return payload


def _resolve(fixture: dict[str, Any], model: dict[str, Any], training: dict[str, Any]) -> Any:
    from xlm.training.profile import resolve_measured_profile

    return resolve_measured_profile(fixture, model_config=model, training=training)


def test_matching_profile_resolves_to_its_resource_plan() -> None:
    resource = _resolve(_profile_fixture(), _model_config(), _training_config())
    assert resource["throughput_tokens_per_sec_range"] == [100.0, 200.0]


@pytest.mark.parametrize(
    "dimension",
    ["architecture", "vocab_size", "num_layers", "hidden_size", "context_length"],
)
def test_model_dimension_mismatch_is_refused(dimension: str) -> None:
    model, training = _model_config(), _training_config()
    altered = dict(model)
    altered[dimension] = "other" if isinstance(model[dimension], str) else model[dimension] + 1
    with pytest.raises(ValueError, match="model mismatch"):
        _resolve(_profile_fixture(model, training), altered, training)


@pytest.mark.parametrize(
    "field",
    ["device", "precision", "global_batch_valid_targets", "context_length"],
)
def test_training_field_mismatch_is_refused(field: str) -> None:
    model, training = _model_config(), _training_config()
    altered = dict(training)
    altered[field] = "other" if isinstance(training[field], str) else training[field] + 1
    with pytest.raises(ValueError, match=field):
        _resolve(_profile_fixture(model, training), model, altered)


def test_unresolved_attention_backend_is_refused() -> None:
    model, training = _model_config(), _training_config()
    unresolved = dict(model, attention_backend="profile_required")
    with pytest.raises(ValueError, match="profile_required"):
        _resolve(_profile_fixture(model, training), unresolved, training)


def test_backend_mismatch_is_refused() -> None:
    model, training = _model_config(), _training_config()
    sdpa = dict(model, attention_backend="sdpa")
    with pytest.raises(ValueError, match="attention_backend"):
        _resolve(_profile_fixture(model, training), sdpa, training)


def test_missing_throughput_range_is_refused() -> None:
    model, training = _model_config(), _training_config()
    fixture = _profile_fixture(model, training)
    fixture["resource_plan"] = {"eta_seconds_range": [5.0, 10.0]}
    with pytest.raises(ValueError, match="no measured throughput"):
        _resolve(fixture, model, training)


def test_infeasible_selection_is_refused() -> None:
    model, training = _model_config(), _training_config()
    fixture = _profile_fixture(model, training, selected_microbatch_sequences=None)
    with pytest.raises(ValueError, match="no feasible microbatch"):
        _resolve(fixture, model, training)


def test_unversioned_payload_is_refused() -> None:
    model, training = _model_config(), _training_config()
    fixture = _profile_fixture(model, training)
    del fixture["profile_version"]
    with pytest.raises(ValueError, match="not a produced profile result"):
        _resolve(fixture, model, training)


def _draft_with_profile(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Draft mirrored from the real recipe, with explicit backend and profile id."""
    draft = yaml.safe_load(
        (REPO_ROOT / "recipes/experiments/baseline_50m.yaml").read_text(encoding="utf-8")
    )
    from xlm.config.composer import ConfigComposer

    composed = ConfigComposer(REPO_ROOT).compose(
        REPO_ROOT / "recipes/experiments/baseline_50m.yaml"
    )
    model = dict(composed["model"])
    training = dict(composed["training"])
    draft["model"]["attention_backend"] = "eager"
    model["attention_backend"] = "eager"
    draft.setdefault("resources", {})["profile_artifact"] = "prof_fixture"
    draft_path = tmp_path / "draft_profile.yaml"
    draft_path.write_text(yaml.safe_dump(draft), encoding="utf-8")
    return draft_path, model, training


def test_plan_with_matching_profile_is_measured(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.plans import resolve_experiment_plan

    draft_path, model, training = _draft_with_profile(tmp_path)
    fixture = _profile_fixture(model, training)
    plan = resolve_experiment_plan(
        draft_path,
        workspace_root=REPO_ROOT,
        artifact_paths=ArtifactPaths(root=tmp_path / "store"),
        snapshot_dir=tmp_path / "snap",
        measured_profile=fixture,
    )
    assert plan.cost_estimate["basis"] == "measured_profile"
    assert plan.cost_estimate["profile"] == "prof_fixture"
    assert "cost_unestimated" not in {b.code for b in plan.blockers}
    assert "unresolved_profile_artifact" not in {b.code for b in plan.blockers}


def test_plan_with_mismatched_profile_is_refused(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.plans import resolve_experiment_plan

    draft_path, model, training = _draft_with_profile(tmp_path)
    other = dict(training, device="cpu")
    with pytest.raises(ValueError, match="device"):
        resolve_experiment_plan(
            draft_path,
            workspace_root=REPO_ROOT,
            artifact_paths=ArtifactPaths(root=tmp_path / "store"),
            snapshot_dir=tmp_path / "snap",
            measured_profile=_profile_fixture(model, other),
        )


def test_plan_with_profile_but_no_declared_artifact_is_refused(tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.plans import resolve_experiment_plan

    draft_path, model, training = _draft_with_profile(tmp_path)
    payload = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    del payload["resources"]["profile_artifact"]
    draft_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="no resources.profile_artifact"):
        resolve_experiment_plan(
            draft_path,
            workspace_root=REPO_ROOT,
            artifact_paths=ArtifactPaths(root=tmp_path / "store"),
            snapshot_dir=tmp_path / "snap",
            measured_profile=_profile_fixture(model, training),
        )


def test_cli_plan_with_matching_profile_reports_measured(tmp_path: Path) -> None:
    draft_path, model, training = _draft_with_profile(tmp_path)
    fixture_path = tmp_path / "profile.json"
    fixture_path.write_text(json.dumps(_profile_fixture(model, training)), encoding="utf-8")
    env = dict(os.environ)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "experiment",
            "plan",
            str(draft_path),
            "--snapshot-dir",
            str(tmp_path / "snap"),
            "--profile",
            str(fixture_path),
            "--output",
            str(tmp_path / "plan.json"),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=str(REPO_ROOT),
        env={**env, "XLM_HOME": str(tmp_path / "home")},
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    saved = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert saved["cost_estimate"]["basis"] == "measured_profile"
    assert all(b["code"] != "cost_unestimated" for b in saved.get("blockers", []))


def test_cli_plan_with_mismatched_profile_is_refused(tmp_path: Path) -> None:
    draft_path, model, training = _draft_with_profile(tmp_path)
    other = dict(training, device="cpu")
    fixture_path = tmp_path / "profile.json"
    fixture_path.write_text(json.dumps(_profile_fixture(model, other)), encoding="utf-8")
    env = dict(os.environ)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "experiment",
            "plan",
            str(draft_path),
            "--snapshot-dir",
            str(tmp_path / "snap"),
            "--profile",
            str(fixture_path),
            "--output",
            str(tmp_path / "plan.json"),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=str(REPO_ROOT),
        env={**env, "XLM_HOME": str(tmp_path / "home")},
        timeout=300,
    )
    assert result.returncode == 1
    assert "device" in result.stderr
