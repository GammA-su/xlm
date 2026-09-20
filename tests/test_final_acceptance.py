"""Independent P23 regressions: failures must never become successful evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.prepare.config import PrepareConfig, PrepareStageSpec, load_prepare_config
from xlm.prepare.planner import plan_prepare
from xlm.prepare.runner import PrepareRunError, load_prior_state, run_prepare

REPO = Path(__file__).resolve().parents[1]


def copy_config(tmp_path: Path) -> PrepareConfig:
    source = tmp_path / "source"
    source.mkdir()
    (source / "record.txt").write_text("original", encoding="utf-8")
    return PrepareConfig(
        id="audit",
        output_root=str(tmp_path / "out"),
        stages=[
            PrepareStageSpec(
                stage_id="copy",
                kind="local_copy",
                copy_from=[str(source)],
                copy_to="{output_root}/copy",
                outputs=["{output_root}/copy"],
            )
        ],
    )


def test_prepare_rejects_corrupted_existing_output(tmp_path: Path) -> None:
    config = copy_config(tmp_path)
    path = tmp_path / "config.yaml"
    run_prepare(config, path, tmp_path / "home", REPO, authorize=True)
    (tmp_path / "out/copy/record.txt").write_text("tampered", encoding="utf-8")
    plan = plan_prepare(config, path, tmp_path / "home", REPO, load_prior_state(tmp_path / "out"))
    assert plan.stages[0].status != "reusable"


def test_prepare_hashes_copy_inputs_and_policy(tmp_path: Path) -> None:
    config = copy_config(tmp_path)
    path = tmp_path / "config.yaml"
    run_prepare(config, path, tmp_path / "home", REPO, authorize=True)
    (tmp_path / "source/record.txt").write_text("changed!", encoding="utf-8")
    plan = plan_prepare(config, path, tmp_path / "home", REPO, load_prior_state(tmp_path / "out"))
    assert plan.stages[0].status == "stale"
    config.stages[0].max_bytes = 1
    with pytest.raises(PrepareRunError, match="exceeds"):
        run_prepare(config, path, tmp_path / "home", REPO, authorize=True)


def test_failed_check_stops_preparation_and_records_failure(tmp_path: Path) -> None:
    config = copy_config(tmp_path)
    config.stages.insert(
        0, PrepareStageSpec(stage_id="gate", command=["not-a-command"], check_only=True)
    )
    with pytest.raises(PrepareRunError, match="gate"):
        run_prepare(config, tmp_path / "config.yaml", tmp_path / "home", REPO, authorize=True)
    assert load_prior_state(tmp_path / "out")["stages"]["gate"]["status"] == "failed"
    assert not (tmp_path / "out/copy").exists()


def test_prepare_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("id: first\nid: second\nstages: [{stage_id: a, command: [doctor]}]\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_prepare_config(path)


def test_blocked_upstream_blocks_pending_downstream(tmp_path: Path) -> None:
    config = PrepareConfig(
        id="blocked",
        stages=[
            PrepareStageSpec(
                stage_id="gate", command=["doctor"], approvals=["pilot"], invalidates=["next"]
            ),
            PrepareStageSpec(
                stage_id="next", command=["doctor"], outputs=[str(tmp_path / "missing")]
            ),
        ],
    )
    plan = plan_prepare(config, tmp_path / "config.yaml", tmp_path / "home", REPO)
    assert [stage.status for stage in plan.stages] == ["blocked", "blocked"]


def test_request_budget_counts_attempts_across_restart(tmp_path: Path) -> None:
    from xlm.data.acquisition.progress import ProgressJournal
    from xlm.data.sources.transport import BudgetExhaustedError

    path = tmp_path / "progress.json"
    journal = ProgressJournal(path, "fixture", "authored_hash")
    journal.record_request(1)
    restored = ProgressJournal(path, "fixture", "authored_hash")
    assert restored.state.requests_made == 1
    with pytest.raises(BudgetExhaustedError, match="request limit"):
        restored.record_request(1)


def test_seed_pairs_follow_recorded_ids_not_argument_order() -> None:
    from xlm.comparison.bootstrap import BootstrapError, pair_seed_evidence

    first = {"training_seeds": {"init_seed": 1, "data_seed": 4}}
    second = {"training_seeds": {"init_seed": 2, "data_seed": 4}}
    assert pair_seed_evidence([first, second], [second, first]) == [
        (first, first),
        (second, second),
    ]
    with pytest.raises(BootstrapError, match="duplicate"):
        pair_seed_evidence([first, first], [first, second])
    with pytest.raises(BootstrapError, match="recorded"):
        pair_seed_evidence([{}], [{}])
    with pytest.raises(BootstrapError, match="differ"):
        pair_seed_evidence([first], [second])


def test_remote_harness_task_is_refused_before_dataset_loading(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from xlm.evaluation.harness import HarnessUnavailableError, materialize_pinned_tasks
    from xlm.evaluation.suites import SuiteTier, TaskVariant

    task = tmp_path / "official.yaml"
    task.write_text("task: arc_easy\ndataset_path: allenai/ai2_arc\ntest_split: test\n")
    manager = SimpleNamespace(task_index={"arc_easy": SimpleNamespace(yaml_path=task)})
    variant = TaskVariant(
        variant_id="arc_search",
        lm_eval_task="arc_easy",
        tier=SuiteTier.SEARCH,
        split="train",
        normalized_metric="acc_norm",
        chance_reference="mean_inverse_n_choices",
    )
    with pytest.raises(HarnessUnavailableError, match="BLOCKED"):
        materialize_pinned_tasks([variant], tmp_path / "out", manager)


def test_missing_explicit_tokenizer_never_uses_byte_fixture(tmp_path: Path) -> None:
    from xlm.tokenizers.loading import load_inference_tokenizer

    with pytest.raises(FileNotFoundError, match="no fallback"):
        load_inference_tokenizer(tmp_path, str(tmp_path / "missing"))


def tiny_plan() -> dict[str, Any]:
    return {
        "id": "audit_cli",
        "model": {
            "architecture": "transformer_baseline",
            "num_layers": 1,
            "hidden_size": 16,
            "num_attention_heads": 2,
            "intermediate_size": 32,
            "vocab_size": 64,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "objective": {"type": "cross_entropy", "label_smoothing": 0.1},
        "optimizer": {"type": "adamw", "lr": 0.01, "weight_decay": 0.03, "betas": [0.8, 0.9]},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "init_seed": 45,
            "context_length": 8,
            "global_batch_valid_targets": 8,
            "checkpoint_every_valid_targets": 16,
            "budget": {"max_valid_targets": 33},
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 5,
                "horizon_valid_targets": 33,
            },
        },
        "data": {"synthetic_tokens": [i % 60 + 4 for i in range(100)]},
    }


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_missing_data_is_refused_even_in_dry_run(tmp_path: Path, dry_run: bool) -> None:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    plan = tiny_plan()
    plan["data"] = {"pool_artifact": "nonexistent_audit_shard"}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["train", str(path), *(["--dry-run"] if dry_run else [])],
        env={"XLM_HOME": str(tmp_path / "home")},
    )
    assert result.exit_code != 0
    assert "Shard manifest not found" in result.output
    assert not list((tmp_path / "home").rglob("model.pt"))


def test_cli_resume_reconstructs_real_settings_and_exact_nonmultiple_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch
    from typer.testing import CliRunner

    from xlm.cli.main import app

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(tiny_plan()), encoding="utf-8")
    runner = CliRunner()
    reference_home = tmp_path / "reference"
    resumed_home = tmp_path / "resumed"
    # Authored captured implementation, exercised in real fresh workers. Both runs
    # use identical A bytes; only the operational interruption trigger differs.
    from test_frozen_execution import authored_tree
    from xlm.experiments import direct
    from xlm.experiments.snapshot import capture_snapshot

    tree = authored_tree(
        tmp_path / "authored",
        "if self.committed_valid_targets == 16 and "
        '__import__("os").environ["XLM_HOME"].endswith("interrupted"):\n'
        "            self._stop_requested = True\n"
        '            self.termination_reason = "interrupted"\n'
        "            return None",
    )

    def capture_authored(root: Path, target: Path) -> Any:
        return capture_snapshot(tree, target)

    monkeypatch.setattr(direct, "capture_snapshot", capture_authored)
    result = runner.invoke(app, ["train", str(plan_path)], env={"XLM_HOME": str(reference_home)})
    assert result.exit_code == 0, (result.output, result.exception)
    interrupted_home = tmp_path / "interrupted"
    result = runner.invoke(app, ["train", str(plan_path)], env={"XLM_HOME": str(interrupted_home)})
    assert result.exit_code == 0, result.output
    assert "interrupted" in result.output
    checkpoint = interrupted_home / "checkpoints/run_audit_cli_interrupted"
    result = runner.invoke(
        app, ["resume", str(checkpoint), "--budget", "33"], env={"XLM_HOME": str(resumed_home)}
    )
    assert result.exit_code == 0, result.output
    left = reference_home / "checkpoints/run_audit_cli_final"
    right = resumed_home / "checkpoints/run_audit_cli_final"
    assert json.loads((right / "checkpoint_meta.json").read_text())["committed_valid_targets"] == 33
    a = torch.load(left / "model.pt", weights_only=True)
    b = torch.load(right / "model.pt", weights_only=True)
    assert all(torch.equal(a[key], b[key]) for key in a)
    ea = json.loads((left / "execution.json").read_text())
    eb = json.loads((right / "execution.json").read_text())
    assert ea["envelope"] == eb["envelope"]
    assert ea["plan_hash"] == eb["plan_hash"]
    assert ea["observations"]["worker_pid"] != eb["observations"]["worker_pid"]
    assert json.loads((left / "data_state.json").read_text()) == json.loads(
        (right / "data_state.json").read_text()
    )
    assert json.loads((left / "schedule.json").read_text()) == json.loads(
        (right / "schedule.json").read_text()
    )

    def same_state(a: Any, b: Any) -> bool:
        if isinstance(a, torch.Tensor):
            return bool(torch.equal(a, b))
        if isinstance(a, dict):
            return a.keys() == b.keys() and all(same_state(a[k], b[k]) for k in a)
        if isinstance(a, (list, tuple)):
            return len(a) == len(b) and all(same_state(x, y) for x, y in zip(a, b, strict=True))
        return bool(a == b)

    for name in ("optimizer.pt", "objective.pt", "rng_state.pt"):
        assert same_state(
            torch.load(left / name, weights_only=True), torch.load(right / name, weights_only=True)
        ), name
    runtime = json.loads((right / "runtime.json").read_text())
    assert runtime["plan"]["optimizer"]["betas"] == [0.8, 0.9]
    assert runtime["plan"]["objective"]["label_smoothing"] == 0.1
