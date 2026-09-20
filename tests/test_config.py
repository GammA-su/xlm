"""Unit tests for configuration, composition, draft/executable modes, and hashing."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from xlm.cli.main import app
from xlm.config.composer import (
    ConfigComposer,
    compute_plan_hash,
    diff_configs,
    load_yaml_str,
)
from xlm.config.schemas import (
    ExecutableExperimentPlanConfig,
    ExperimentDraftConfig,
    MixtureConfig,
    ModelPresetConfig,
    TransformerBaselineConfig,
)

runner = CliRunner()


def test_duplicate_yaml_key_rejection() -> None:
    """Verify duplicate YAML keys raise an error immediately."""
    yaml_with_dupe = """
    model:
      hidden_size: 512
      hidden_size: 768
    """
    with pytest.raises(ValueError, match="Duplicate YAML key detected"):
        load_yaml_str(yaml_with_dupe)


def test_unknown_key_rejection() -> None:
    """Verify unknown fields like 'tokens_buget' raise ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        TransformerBaselineConfig.model_validate(
            {
                "architecture": "transformer_baseline",
                "num_layers": 10,
                "hidden_size": 512,
                "num_attention_heads": 8,
                "intermediate_size": 1472,
                "tokens_buget": 100000,  # Typo field
            }
        )
    assert "tokens_buget" in str(exc_info.value)


def test_non_finite_float_rejection() -> None:
    """Verify NaN and Infinity floats are forbidden."""
    with pytest.raises(ValidationError, match="Non-finite float value"):
        TransformerBaselineConfig.model_validate(
            {
                "architecture": "transformer_baseline",
                "num_layers": 10,
                "hidden_size": 512,
                "num_attention_heads": 8,
                "intermediate_size": 1472,
                "dropout": float("nan"),
            }
        )


def test_composition_extends_and_cycle_detection(tmp_path: Path) -> None:
    """Verify composition resolves extends and detects inheritance cycles."""
    file_a = tmp_path / "a.yaml"
    file_b = tmp_path / "b.yaml"

    # Cycle A extends B extends A
    file_a.write_text("extends: b.yaml\nval_a: 1\n", encoding="utf-8")
    file_b.write_text("extends: a.yaml\nval_b: 2\n", encoding="utf-8")

    composer = ConfigComposer(workspace_root=tmp_path)
    with pytest.raises(ValueError, match="Cyclic inheritance detected"):
        composer.compose(file_a)


def test_list_replacement_and_deep_merge(tmp_path: Path) -> None:
    """Verify mapping merges recursively while lists in child replace base lists."""
    base_file = tmp_path / "base.yaml"
    base_file.write_text(
        """
        optimizer:
          type: adamw
          betas: [0.9, 0.999]
          lr: 0.001
        layers: [1, 2, 3]
        """,
        encoding="utf-8",
    )

    child_file = tmp_path / "child.yaml"
    child_file.write_text(
        """
        extends: base.yaml
        optimizer:
          lr: 0.0005
        layers: [4]
        """,
        encoding="utf-8",
    )

    composer = ConfigComposer(workspace_root=tmp_path)
    res = composer.compose(child_file)

    # Optimizer dictionary deep merged: lr updated, betas preserved
    assert res["optimizer"]["lr"] == 0.0005
    assert res["optimizer"]["betas"] == [0.9, 0.999]
    # Layers list was replaced, NOT concatenated
    assert res["layers"] == [4]


def test_conflicting_component_type_change(tmp_path: Path) -> None:
    """Verify changing component type discards old component fields."""
    base_file = tmp_path / "base.yaml"
    base_file.write_text(
        """
        architecture:
          type: transformer
          num_heads: 8
          rope_theta: 10000.0
        """,
        encoding="utf-8",
    )

    child_file = tmp_path / "child.yaml"
    child_file.write_text(
        """
        extends: base.yaml
        architecture:
          type: rnn
          hidden_state_dim: 256
        """,
        encoding="utf-8",
    )

    composer = ConfigComposer(workspace_root=tmp_path)
    res = composer.compose(child_file)

    assert res["architecture"]["type"] == "rnn"
    assert res["architecture"]["hidden_state_dim"] == 256
    # Old transformer fields must NOT be silently retained
    assert "num_heads" not in res["architecture"]
    assert "rope_theta" not in res["architecture"]


def test_environment_variable_interpolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify approved env vars interpolate and unapproved/secret vars are rejected."""
    monkeypatch.setenv("XLM_HOME", "D:/test_xlm")
    monkeypatch.setenv("SECRET_TOKEN", "super_secret_123")

    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("root: ${XLM_HOME}/data\n", encoding="utf-8")

    composer = ConfigComposer(workspace_root=tmp_path)
    res = composer.compose(cfg_file)
    assert res["root"] == "D:/test_xlm/data"

    # Unapproved variable attempt
    bad_cfg = tmp_path / "bad.yaml"
    bad_cfg.write_text("token: ${SECRET_TOKEN}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not in the approved allowlist"):
        composer.compose(bad_cfg)


def test_cli_overrides(tmp_path: Path) -> None:
    """Verify typed CLI overrides apply cleanly."""
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        """
        training:
          budget:
            max_valid_targets: 100000
          compile: false
        """,
        encoding="utf-8",
    )
    composer = ConfigComposer(workspace_root=tmp_path)
    res = composer.compose(
        cfg_file,
        overrides=[
            "training.budget.max_valid_targets=200000",
            "training.compile=true",
        ],
    )
    assert res["training"]["budget"]["max_valid_targets"] == 200000
    assert res["training"]["compile"] is True


def test_draft_versus_executable_validation(tmp_path: Path) -> None:
    """Verify supplied unresolved experiment recipes validate as draft, fail executable."""
    repo_root = Path.cwd()
    exp_file = repo_root / "recipes" / "experiments" / "baseline_50m.yaml"
    assert exp_file.exists(), f"Recipe file missing: {exp_file}"

    composer = ConfigComposer(workspace_root=repo_root)
    resolved = composer.compose(exp_file)

    # Validates cleanly as draft
    draft = ExperimentDraftConfig.model_validate(resolved)
    assert draft.id == "baseline_50m_mix01"

    # Fails executable validation because required artifacts are null/placeholders
    with pytest.raises(ValidationError, match="requires explicit, immutable"):
        ExecutableExperimentPlanConfig.model_validate(resolved)


def test_supplied_preset_recipes_validity() -> None:
    """Verify supplied model and mixture presets validate under strict schemas."""
    repo_root = Path.cwd()
    m50 = repo_root / "recipes" / "models" / "50m.yaml"
    mix01 = repo_root / "recipes" / "mixtures" / "mix01.yaml"

    composer = ConfigComposer(workspace_root=repo_root)
    m50_data = composer.compose(m50)
    model_cfg = ModelPresetConfig.model_validate(m50_data)
    assert model_cfg.id == "50m"
    assert model_cfg.num_layers == 10

    mix_data = composer.compose(mix01)
    mix_cfg = MixtureConfig.model_validate(mix_data)
    assert mix_cfg.id == "mix01"
    assert mix_cfg.weights["nemotron_organic_high"] == 0.15


def test_canonical_hashing_and_granularity() -> None:
    """Verify canonical hashing is deterministic and tracks behavioral changes."""
    cfg1 = {
        "description": "First run",
        "notes": "Some notes",
        "id": "exp_1",
        "model": {"hidden_size": 512, "layers": 10},
        "training": {"lr": 0.001, "seed": 42},
    }
    cfg2 = {
        "description": "Second run with different notes",
        "notes": "Different cosmetic text",
        "id": "exp_2",
        "model": {"hidden_size": 512, "layers": 10},
        "training": {"lr": 0.001, "seed": 42},
    }
    cfg_diff_behavior = {
        "description": "First run",
        "model": {"hidden_size": 512, "layers": 10},
        "training": {"lr": 0.0005, "seed": 42},  # Changed lr
    }

    h1 = compute_plan_hash(cfg1)
    h2 = compute_plan_hash(cfg2)
    h3 = compute_plan_hash(cfg_diff_behavior)

    assert h1 == h2, "Cosmetic metadata differences must NOT alter behavioral plan hash"
    assert h1 != h3, "Hyperparameter change MUST alter behavioral plan hash"

    diffs = diff_configs(cfg1, cfg_diff_behavior)
    assert any("training.lr" in d for d in diffs)


def test_cli_config_commands(tmp_path: Path) -> None:
    """Test CLI commands: validate, resolve, diff, and schema."""
    repo_root = Path.cwd()
    m50_file = repo_root / "recipes" / "models" / "50m.yaml"

    # Validate
    res_val = runner.invoke(app, ["config", "validate", str(m50_file)])
    assert res_val.exit_code == 0
    assert "Validation successful" in res_val.output

    # Resolve
    res_res = runner.invoke(app, ["config", "resolve", str(m50_file)])
    assert res_res.exit_code == 0
    assert "hidden_size: 512" in res_res.output

    # Schema
    res_sch = runner.invoke(app, ["config", "schema", "--kind", "model_preset"])
    assert res_sch.exit_code == 0
    assert "TransformerBaselineConfig" in res_sch.output or "properties" in res_sch.output
