"""Acceptance tests for P22: clean-env offline workflow and interface routing.

Runs the documented offline workflow (prepare -> train -> evaluate -> compare
-> export) from a clean temporary XLM_HOME with fixture-scale budgets, and
proves each experiment dimension flows through its intended interface.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], home: Path, cwd: Path, timeout: float = 900.0) -> Any:
    import os

    env = dict(os.environ)
    env["XLM_HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(cwd),
        timeout=timeout,
    )
    return completed


def _tiny_plan(path: Path, plan_id: str = "toy_flow") -> None:
    plan = {
        "id": plan_id,
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 260,
            "num_layers": 2,
            "hidden_size": 64,
            "num_attention_heads": 4,
            "intermediate_size": 128,
            "context_length": 64,
            "attention_backend": "eager",
        },
        "objective": {"type": "cross_entropy"},
        "optimizer": {"lr": 0.05, "weight_decay": 0.0},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "context_length": 64,
            "global_batch_valid_targets": 64,
            "schedule": {
                "type": "warmup_cosine",
                "counter": "committed_valid_targets",
                "horizon_valid_targets": 256,
                "warmup_valid_targets": 32,
                "min_lr_ratio": 0.1,
            },
            "budget": {"max_valid_targets": 256},
        },
        "data": {"synthetic_tokens": [i % 256 + 4 for i in range(1000)]},
    }
    path.write_text(json.dumps(plan), encoding="utf-8")


@pytest.mark.serial
def test_offline_workflow_from_clean_environment(tmp_path: Path) -> None:
    """prepare -> train -> evaluate -> compare -> export, all from scratch."""
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    out_root = tmp_path / "prepared"

    prepared = _run(
        [
            "prepare",
            "--config",
            str(REPO_ROOT / "recipes/prepare/offline_toy.yaml"),
            "--authorize",
            "--define",
            f"output_root={out_root}",
        ],
        home,
        cwd,
    )
    assert prepared.returncode == 0, prepared.stderr[-2000:]
    assert (out_root / "mixture_plan.json").is_file()

    plan_path = work / "plan.json"
    _tiny_plan(plan_path)
    # P23: train the shard actually prepared above; the old test trained an
    # unrelated implicit synthetic stream and could not prove pipeline wiring.
    training_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    tokenizer = ByteLevelBPETokenizer.load(out_root / "regime/tokenizer")
    training_plan["model"]["vocab_size"] = tokenizer.vocab_size
    training_plan["data"] = {
        "pool_artifact": str(out_root / "shards/fixture_cleaning"),
        "tokenizer_artifact": str(out_root / "regime/tokenizer"),
    }
    training_plan["training"]["budget"]["max_valid_targets"] = 257
    training_plan["training"]["schedule"]["horizon_valid_targets"] = 257
    training_plan["training"]["checkpoint_every_valid_targets"] = 128
    plan_path.write_text(json.dumps(training_plan), encoding="utf-8")
    trained = _run(["train", str(plan_path), "--device", "cpu"], home, cwd)
    assert trained.returncode == 0, trained.stderr[-2000:]
    assert "Training Run Complete" in trained.stdout

    checkpoints = sorted((home / "checkpoints").glob("run_toy_flow_final"))
    assert checkpoints, "final checkpoint missing"
    checkpoint = checkpoints[0]
    # Resume a committed intermediate checkpoint with its real shard and settings.
    resumed_home = tmp_path / "resumed_home"
    resumed = _run(
        ["resume", str(home / "checkpoints/run_toy_flow_step_2_ckpt"), "--budget", "257"],
        resumed_home,
        cwd,
    )
    assert resumed.returncode == 0, resumed.stderr[-2000:]
    resumed_checkpoint = resumed_home / "checkpoints/run_toy_flow_final"
    import torch

    reference_weights = torch.load(checkpoint / "model.pt", weights_only=True)
    resumed_weights = torch.load(resumed_checkpoint / "model.pt", weights_only=True)
    assert all(torch.equal(reference_weights[k], resumed_weights[k]) for k in reference_weights)
    assert (
        json.loads((resumed_checkpoint / "checkpoint_meta.json").read_text())[
            "committed_valid_targets"
        ]
        == 257
    )

    evaluated = _run(
        [
            "evaluate",
            str(checkpoint),
            "--suite",
            "synthetic_mc",
            "--device",
            "cpu",
            "--json",
            "--tokenizer",
            str(out_root / "regime/tokenizer"),
        ],
        home,
        cwd,
    )
    assert evaluated.returncode == 0, evaluated.stderr[-2000:]
    receipts = json.loads(evaluated.stdout)
    assert isinstance(receipts, list) and receipts

    base_evidence = _synthetic_evidence(work / "base.json", "fp_base", True)
    cand_evidence = _synthetic_evidence(work / "cand.json", "fp_cand", False)
    plan_a = work / "plan_a.json"
    plan_b = work / "plan_b.json"
    _comparison_plan(plan_a, "plan_a")
    _comparison_plan(plan_b, "plan_b")
    facts = work / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "total_params": 1000,
                "canonical_bytes": 500,
                "matched_bytes": None,
                "matched_compute": None,
                "measured_compute_seconds": 1.0,
            }
        ),
        encoding="utf-8",
    )
    clusters = work / "clusters.json"
    clusters.write_text(
        json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(6)}),
        encoding="utf-8",
    )
    compared = _run(
        [
            "compare",
            "--baseline",
            str(base_evidence),
            "--candidate",
            str(cand_evidence),
            "--plan-baseline",
            str(plan_a),
            "--plan-candidate",
            str(plan_b),
            "--track",
            "data-mixture",
            "--facts-baseline",
            str(facts),
            "--facts-candidate",
            str(facts),
            "--clusters",
            str(clusters),
            "--n-bootstrap",
            "100",
            "--output",
            str(work / "comparison.json"),
        ],
        home,
        cwd,
    )
    assert compared.returncode == 0, compared.stderr[-2000:] + compared.stdout[-2000:]

    exported = _run(
        [
            "export",
            str(checkpoint),
            "--output-dir",
            str(work / "bundle"),
            "--export-id",
            "toy_flow_export",
            "--tokenizer",
            str(out_root / "regime/tokenizer"),
        ],
        home,
        cwd,
    )
    assert exported.returncode == 0, exported.stderr[-2000:]
    assert (work / "bundle" / "model.safetensors").is_file()

    reloaded = _run(
        ["evaluate", str(work / "bundle"), "--suite", "synthetic_mc", "--json"], home, cwd
    )
    assert reloaded.returncode == 0, reloaded.stderr[-2000:]
    assert json.loads(reloaded.stdout)[0]["metrics"] == receipts[0]["metrics"]

    # A real paired analysis of this model's authored fixture predictions.
    # The CLI comparison above remains separate, authored comparison-format plumbing.
    from xlm.comparison.bootstrap import AlignedItem, cluster_bootstrap_difference
    from xlm.evaluation.fixtures import get_synthetic_multiple_choice_fixture
    from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
    from xlm.evaluation.scorer import BenchmarkFixtureScorer
    from xlm.models.serialization import load_model_for_inference
    from xlm.tokenizers.loading import checkpoint_weights_hash

    fixture_scorer = BenchmarkFixtureScorer(
        ConditionalLikelihoodScorer(load_model_for_inference(checkpoint), tokenizer),
        model_hash=checkpoint_weights_hash(checkpoint),
        use_cache=False,
    )
    _, predictions = fixture_scorer.evaluate_dataset(get_synthetic_multiple_choice_fixture())
    paired = [
        AlignedItem(
            p.item_id, "authored_mc", p.item_id, float(p.is_correct_norm), float(p.is_correct_norm)
        )
        for p in predictions
    ]
    control = cluster_bootstrap_difference(paired, n_bootstrap=100, analysis_seed=23)
    assert control.point == control.ci_lo == control.ci_hi == 0.0
    (work / "measured_control.json").write_text(json.dumps(control.to_dict()), encoding="utf-8")
    report = _run(
        [
            "report",
            "--run",
            "run_toy_flow",
            "--format",
            "html",
            "--output",
            str(work / "report.html"),
        ],
        home,
        cwd,
    )
    assert report.returncode == 0, report.stderr[-2000:]
    assert (work / "report.html").is_file()


def _synthetic_evidence(path: Path, fingerprint: str, candidate_wins: bool) -> Path:
    tasks: dict[str, Any] = {}
    for task, chance in (("blimp", 0.5), ("arc_easy", 0.25), ("hellaswag", 0.25), ("piqa", 0.5)):
        items = []
        for i in range(6):
            correct = (i % 2 == 0) if candidate_wins else (i % 2 == 1)
            items.append(
                {
                    "item_id": f"{task}_{i}",
                    "is_correct": correct,
                    "is_correct_normalized": correct,
                    "omitted_reason": None,
                }
            )
        tasks[task] = {
            "metric_name": "acc_norm" if task != "piqa" else "acc",
            "chance": chance,
            "items": items,
        }
    path.write_text(
        json.dumps(
            {
                "identity": {
                    "fingerprint": fingerprint,
                    "checkpoint_hash": fingerprint,
                    "tokenizer_hash": "tok",
                },
                "canonical_bytes": 500,
                "measured_compute_seconds": 1.0,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    return path


def _comparison_plan(path: Path, plan_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "plan_id": plan_id,
                "track": "baseline",
                "horizon_kind": "standalone",
                "resolved_config": {
                    "model": {"architecture": "transformer_baseline", "vocab_size": 260},
                    "training": {
                        "data_seed": 1,
                        "init_seed": 2,
                        "context_length": 64,
                        "precision": "fp32",
                        "budget": {"max_valid_targets": 64},
                    },
                    "data": {
                        "mixture_preset": "m",
                        "mixture_details": {"id": "m", "weights": {"a": 1.0}},
                        "packing_policy": "causal_stream",
                    },
                    "objective": {"type": "cross_entropy"},
                    "optimizer": {"type": "adamw", "tuning_allowance": 1},
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.serial
def test_prepare_restart_reuses_verified_acquisition(tmp_path: Path) -> None:
    home = tmp_path / "home"
    out_root = tmp_path / "out"
    args = [
        "prepare",
        "--config",
        str(REPO_ROOT / "recipes/prepare/offline_toy.yaml"),
        "--authorize",
        "--define",
        f"output_root={out_root}",
    ]
    first = _run(args, home, REPO_ROOT)
    assert first.returncode == 0, first.stderr[-2000:]
    raw_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (out_root / "raw").iterdir()
        if p.is_file()
    }
    second = _run(args, home, REPO_ROOT)
    assert second.returncode == 0, second.stderr[-2000:]
    assert "not rerun" in second.stdout
    for name, digest in raw_hashes.items():
        assert hashlib.sha256((out_root / "raw" / name).read_bytes()).hexdigest() == digest


def test_each_dimension_uses_its_intended_interface(tmp_path: Path) -> None:
    """Size/mixture/loss/tokenizer/budget changes route through named interfaces."""
    from xlm.config.composer import ConfigComposer, compute_plan_hash

    composer = ConfigComposer(REPO_ROOT)
    base = composer.compose(REPO_ROOT / "recipes/experiments/baseline_50m.yaml")

    def variant(mutator: Any) -> dict[str, Any]:
        import copy

        modified = copy.deepcopy(base)
        mutator(modified)
        return modified

    hashes = {
        "base": compute_plan_hash(base),
        "size": compute_plan_hash(variant(lambda c: c["model"].update({"preset": "150m"}))),
        "mixture": compute_plan_hash(
            variant(lambda c: c["data"].update({"mixture_preset": "m1_less_synth"}))
        ),
        "loss": compute_plan_hash(
            variant(lambda c: c["objective"].update({"label_smoothing": 0.1}))
        ),
        "tokenizer": compute_plan_hash(
            variant(lambda c: c["data"].update({"tokenizer_artifact": "tokenizer_bpe_999"}))
        ),
        "budget": compute_plan_hash(
            variant(lambda c: c["training"]["budget"].update({"max_valid_targets": 999}))
        ),
    }
    assert len(set(hashes.values())) == 6, hashes
    resolved = composer.compose(REPO_ROOT / "recipes/experiments/baseline_50m.yaml")
    assert resolved["model"]["num_layers"] == 10  # size came through the model preset
    assert resolved["data"]["mixture_details"]["id"] == "mix01"  # mixture preset interface


def test_large_campaigns_prepare_without_launching(tmp_path: Path) -> None:
    import os

    env = dict(os.environ)
    env["XLM_HOME"] = str(tmp_path / "home")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "campaign",
            "plan",
            "recipes/campaigns/staged_50m_150m_300m.yaml",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=300.0,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "Trials:" in completed.stdout
    assert "Nothing was executed" in completed.stdout
    assert list((tmp_path / "home").rglob("run_record.json")) == []
