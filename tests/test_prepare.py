"""Acceptance tests for P22 prepare: dry planning, reuse, resume, approvals.

Execution tests run the offline toy config against fixture data with an
isolated XLM_HOME. Nothing here touches the network or production budgets.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from xlm.prepare.config import PrepareConfig, load_prepare_config
from xlm.prepare.planner import (
    PlanError,
    find_repo_root,
    plan_prepare,
    resolve_variables,
)
from xlm.prepare.runner import (
    PrepareRunError,
    load_prior_state,
    run_prepare,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TOY_CONFIG = REPO_ROOT / "recipes" / "prepare" / "offline_toy.yaml"


def _config(tmp_path: Path, defines: dict[str, str] | None = None) -> tuple[PrepareConfig, Path]:
    merged = {"output_root": str(tmp_path / "out"), **(defines or {})}
    return load_prepare_config(TOY_CONFIG, merged), TOY_CONFIG


# ------------------------------------------------------------------ planning


def test_plan_only_reports_statuses_without_executing(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    home = tmp_path / "home"
    plan = plan_prepare(config, path, home, REPO_ROOT, None)
    assert plan.config_id == "offline_toy"
    by_id = {s.stage_id: s for s in plan.stages}
    assert by_id["admit-check"].status == "check-only"
    assert by_id["fetch"].status == "pending"
    assert by_id["mixture-plan"].status == "pending"
    assert plan.to_dict()["counts"]["blocked"] == 0
    assert not (tmp_path / "out").exists()


def test_missing_approval_blocks_with_named_reason(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    gated = config.model_copy(
        update={
            "stages": [
                s.model_copy(update={"approvals": ["pilot"]}) if s.stage_id == "fetch" else s
                for s in config.stages
            ]
        }
    )
    plan = plan_prepare(gated, path, tmp_path / "home", REPO_ROOT, None)
    fetch = next(s for s in plan.stages if s.stage_id == "fetch")
    assert fetch.status == "blocked"
    assert any("'pilot'" in r for r in fetch.reasons)


def test_unknown_variables_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="unknown variables"):
        resolve_variables("{mystery}/x", {"repo": "r"}, where="test")


def test_duplicate_and_unknown_stages_are_rejected(tmp_path: Path) -> None:
    from xlm.prepare.config import PrepareStageSpec

    base = PrepareStageSpec(stage_id="a", kind="run", command=["x"])
    with pytest.raises(ValueError, match="duplicate stage_id"):
        PrepareConfig(id="x", stages=[base, base])
    bad = PrepareStageSpec(stage_id="a", kind="run", command=["x"], invalidates=["ghost"])
    with pytest.raises(ValueError, match="unknown stages"):
        PrepareConfig(id="x", stages=[bad])
    with pytest.raises(ValueError, match="unknown kind"):
        PrepareStageSpec(stage_id="a", kind="teleport", command=["x"])


def test_stale_inputs_invalidate_downstream(tmp_path: Path) -> None:
    from xlm.prepare.config import PrepareConfig

    src = tmp_path / "src.txt"
    src.write_text("v1", encoding="utf-8")
    from xlm.prepare.config import PrepareStageSpec

    config = PrepareConfig(
        id="chain",
        output_root=str(tmp_path / "out"),
        stages=[
            PrepareStageSpec(
                stage_id="a",
                kind="local_copy",
                copy_from=[str(src)],
                copy_to="{output_root}/a",
                outputs=["{output_root}/a/src.txt"],
                watched_inputs=[str(src)],
                invalidates=["b"],
            ),
            PrepareStageSpec(
                stage_id="b",
                kind="local_copy",
                copy_from=["{output_root}/a/src.txt"],
                copy_to="{output_root}/b",
                outputs=["{output_root}/b/src.txt"],
                watched_inputs=[],
                invalidates=[],
            ),
        ],
    )
    home = tmp_path / "home"
    run_prepare(config, tmp_path / "cfg.yaml", home, REPO_ROOT, authorize=True)
    src.write_text("v2", encoding="utf-8")
    plan = plan_prepare(
        config, tmp_path / "cfg.yaml", home, REPO_ROOT, load_prior_state(tmp_path / "out")
    )
    by_id = {s.stage_id: s for s in plan.stages}
    assert by_id["a"].status == "stale"
    assert by_id["b"].status == "stale"
    assert "upstream" in by_id["b"].rerun_because


# ------------------------------------------------------------------ execution


def test_execution_requires_explicit_authorization(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    with pytest.raises(PrepareRunError, match="--authorize"):
        run_prepare(config, path, tmp_path / "home", REPO_ROOT, authorize=False)


def test_full_toy_run_reuses_on_repeat(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    home = tmp_path / "home"
    first = run_prepare(config, path, home, REPO_ROOT, authorize=True)
    ran = [s.stage_id for s in first.stages if s.action == "ran"]
    assert "fetch" in ran and "mixture-plan" in ran
    assert (tmp_path / "out" / "mixture_plan.json").is_file()

    raw_files = sorted((tmp_path / "out" / "raw").glob("*"))
    mtimes = {p.name: p.stat().st_mtime_ns for p in raw_files if p.is_file()}
    second = run_prepare(config, path, home, REPO_ROOT, authorize=True)
    assert all(s.action in ("reused", "check-only") for s in second.stages)
    for name, mtime in mtimes.items():
        assert (tmp_path / "out" / "raw" / name).stat().st_mtime_ns == mtime

    # Forced rebuilds say so explicitly.
    third = run_prepare(config, path, home, REPO_ROOT, authorize=True, force=True)
    assert any(s.action == "forced" for s in third.stages)


def test_partial_outputs_resume_without_redo(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    home = tmp_path / "home"
    run_prepare(config, path, home, REPO_ROOT, authorize=True)
    # Simulate an interrupted freeze: outputs gone, earlier stages intact.
    shutil.rmtree(tmp_path / "out" / "regime")
    (tmp_path / "out" / "shards").mkdir(parents=True, exist_ok=True)
    resumed = run_prepare(config, path, home, REPO_ROOT, authorize=True)
    by_id = {s.stage_id: s.action for s in resumed.stages}
    assert by_id["freeze"] == "resumed"
    assert by_id["fetch"] == "reused"
    assert by_id["clean"] == "reused"


def test_failed_stage_stops_with_reason_and_state(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    broken = config.model_copy(
        update={
            "stages": [
                s.model_copy(update={"command": ["data", "no-such-command"]})
                if s.stage_id == "clean"
                else s
                for s in config.stages
            ]
        }
    )
    with pytest.raises(PrepareRunError, match="stage 'clean' failed"):
        run_prepare(broken, path, tmp_path / "home", REPO_ROOT, authorize=True)
    state = load_prior_state(tmp_path / "out")
    assert state["stages"]["clean"]["status"] == "failed"
    assert state["stages"]["fetch"]["status"] == "succeeded"


def test_local_copy_enforces_its_byte_cap(tmp_path: Path) -> None:
    config, path = _config(tmp_path)
    tiny = config.model_copy(
        update={
            "stages": [
                s.model_copy(update={"max_bytes": 10}) if s.stage_id == "fetch" else s
                for s in config.stages
            ]
        }
    )
    with pytest.raises(PrepareRunError, match="exceeds 10 bytes"):
        run_prepare(tiny, path, tmp_path / "home", REPO_ROOT, authorize=True)


def test_find_repo_root_and_config_errors(tmp_path: Path) -> None:
    assert find_repo_root(REPO_ROOT) == REPO_ROOT
    with pytest.raises(FileNotFoundError, match="not found"):
        load_prepare_config(tmp_path / "absent.yaml")


# ------------------------------------------------------------------------ CLI


def _invoke(args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return CliRunner().invoke(app, args)


def test_cli_plan_only_lists_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _invoke(
        [
            "prepare",
            "--config",
            "recipes/prepare/offline_toy.yaml",
            "--plan-only",
            "--define",
            f"output_root={tmp_path}/out",
        ],
        monkeypatch,
        tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "offline_toy" in result.output
    assert "mixture-plan" in result.output


def test_cli_prepare_refuses_without_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(
        [
            "prepare",
            "--config",
            "recipes/prepare/offline_toy.yaml",
            "--define",
            f"output_root={tmp_path}/out",
        ],
        monkeypatch,
        tmp_path,
    )
    assert result.exit_code == 1
    assert "--authorize" in result.output


def test_cli_maintenance_dry_run_lists_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    scratch = home / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "temp.json").write_text("{}", encoding="utf-8")
    result = _invoke(["maintenance"], monkeypatch, tmp_path)
    assert result.exit_code == 0, result.output
    assert "temp.json" in result.output
    assert (scratch / "temp.json").is_file(), "dry run must not delete"

    applied = _invoke(["maintenance", "--apply"], monkeypatch, tmp_path)
    assert applied.exit_code == 0, applied.output
    assert not (scratch / "temp.json").exists()

    (scratch / "big.json").write_text("{}", encoding="utf-8")
    capped = _invoke(["maintenance", "--apply", "--max-bytes", "0"], monkeypatch, tmp_path)
    assert capped.exit_code == 1
    assert (scratch / "big.json").is_file(), "refused removal must delete nothing"
