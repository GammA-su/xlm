"""Acceptance tests for P22 recipe coverage: every recipe validates by kind.

Drafts validate as drafts and fail executable mode; comparison recipes check
their tracks; campaigns expand. No recipe masquerades as executable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from xlm.comparison.recipes import load_comparison_recipe
from xlm.config.schemas import ExecutableExperimentPlanConfig, ExperimentDraftConfig
from xlm.experiments.campaigns import expand_campaign

REPO_ROOT = Path(__file__).resolve().parents[1]
RECIPES_ROOT = REPO_ROOT / "recipes"


def _recipe_files() -> list[Path]:
    return sorted(
        p for p in RECIPES_ROOT.rglob("*.yaml") if p.is_file() and p.name != "mix01_views.yaml"
    )


def test_every_recipe_file_parses_without_duplicate_keys() -> None:
    from xlm.config.composer import load_yaml_file

    files = _recipe_files()
    assert len(files) >= 20, f"expected the full recipe set, found {len(files)}"
    for path in files:
        data = load_yaml_file(path)  # duplicate keys raise here
        assert isinstance(data, dict), path


def test_model_and_mixture_presets_validate() -> None:
    from xlm.config.schemas import MixtureConfig, ModelPresetConfig

    for name in ("50m", "150m", "300m", "tiny"):
        ModelPresetConfig.model_validate(
            yaml.safe_load((RECIPES_ROOT / "models" / f"{name}.yaml").read_text(encoding="utf-8"))
        )
    for name in (
        "mix01",
        "mix01_no_ifm",
        "m1_less_synth",
        "m2_more_synth",
        "m3_more_pdfs",
        "m4_txt360_web",
        "m5_more_practical",
    ):
        MixtureConfig.model_validate(
            yaml.safe_load((RECIPES_ROOT / "mixtures" / f"{name}.yaml").read_text(encoding="utf-8"))
        )


def test_experiment_drafts_validate_but_are_not_executable() -> None:
    drafts = sorted((RECIPES_ROOT / "experiments").glob("*.yaml"))
    assert {p.name for p in drafts} >= {
        "baseline_50m.yaml",
        "baseline_150m.yaml",
        "baseline_300m.yaml",
        "tiny_demo.yaml",
    }
    for path in drafts:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        ExperimentDraftConfig.model_validate(data)
        with pytest.raises(ValidationError):
            ExecutableExperimentPlanConfig.model_validate(data)


def test_comparison_recipes_validate_and_check_tracks() -> None:
    recipes = sorted((RECIPES_ROOT / "comparisons").glob("*.yaml"))
    assert len(recipes) == 5
    tracks = set()
    for path in recipes:
        recipe = load_comparison_recipe(path)
        tracks.add(recipe.track)
        assert recipe.status == "draft_requires_evidence"
        assert recipe.baseline_run is None and recipe.candidate_run is None
    assert tracks == {"architecture", "objective", "optimizer", "tokenizer"}


def test_comparison_recipe_rejects_unknown_tracks(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump({"kind": "comparison_recipe", "id": "bad", "track": "vibes"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown track"):
        load_comparison_recipe(bad)


def test_campaign_drafts_expand() -> None:
    staged = expand_campaign(
        RECIPES_ROOT / "campaigns" / "staged_50m_150m_300m.yaml", workspace_root=REPO_ROOT
    )
    assert staged.total_trials == 6 * (1 + 2 + 2 + 3 + 2) == 60
    expected_50m = 6 * 128_000_000 + 12 * 512_000_000
    expected_150m = 12 * 1_000_000_000 + 18 * 3_000_000_000
    expected_300m = 12 * 6_000_000_000
    assert staged.tokens_by_size == {
        "50m": expected_50m,
        "150m": expected_150m,
        "300m": expected_300m,
    }
    assert staged.total_valid_targets == expected_50m + expected_150m + expected_300m
    confirm = expand_campaign(
        RECIPES_ROOT / "campaigns" / "confirm_multiseed.yaml", workspace_root=REPO_ROOT
    )
    assert confirm.total_trials == 2 * 2
    factorial = expand_campaign(
        RECIPES_ROOT / "campaigns" / "factorial_demo.yaml", workspace_root=REPO_ROOT
    )
    assert factorial.total_trials == 2 * 2


def test_readme_example_commands_are_implemented(tmp_path: Path) -> None:
    """The recipes README examples must match the real CLI surface."""
    import os
    import subprocess
    import sys

    readme = (REPO_ROOT / "recipes" / "README.md").read_text(encoding="utf-8")
    assert "xlm config validate recipes/experiments/baseline_50m.yaml --mode draft" in readme
    assert "xlm experiment plan recipes/experiments/baseline_50m.yaml" in readme
    assert "--device cuda --max-targets 128000000" in readme

    validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "config",
            "validate",
            "recipes/experiments/baseline_50m.yaml",
            "--mode",
            "draft",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        timeout=120.0,
    )
    assert validate.returncode == 0, validate.stderr[-1000:]
    planned = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "experiment",
            "plan",
            "recipes/experiments/baseline_50m.yaml",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        timeout=120.0,
        env={**os.environ, "XLM_HOME": str(tmp_path / "home")},
    )
    assert planned.returncode == 0, planned.stderr[-1000:]
    assert "Blockers:" in planned.stdout
    train_help = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "train", "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        timeout=120.0,
    )
    assert "--device" in train_help.stdout and "--max-targets" in train_help.stdout


def test_prepare_configs_validate() -> None:
    from xlm.prepare.config import load_prepare_config

    config = load_prepare_config(RECIPES_ROOT / "prepare" / "offline_toy.yaml")
    assert config.id == "offline_toy"
    assert [s.stage_id for s in config.stages][:3] == ["admit-check", "fetch", "clean"]
    assert config.stages[-1].stage_id == "mixture-plan"


def test_mix01_views_registry_still_validates() -> None:
    from xlm.data.sources.mix01 import load_mix01_views

    registry = load_mix01_views(RECIPES_ROOT / "mixtures" / "mix01_views.yaml")
    assert len(registry.views) == 13
