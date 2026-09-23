"""Subprocess CLI acceptance tests for xlm train, xlm resume, xlm run inspect, and xlm demo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.serial
def test_cli_demo_subprocess() -> None:
    """Verify xlm demo runs cleanly via subprocess to 200 targets."""
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "demo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Demo failed:\n{result.stderr}\n{result.stdout}"
    assert "Demo Successfully Completed: 200 Valid Targets Reached!" in result.stdout
    assert "Part 1 completed: 100 targets" in result.stdout
    assert "Part 2 completed: 200 targets" in result.stdout


def test_cli_run_inspect_subprocess(tmp_path: Path) -> None:
    """Verify xlm run inspect displays metadata cleanly without torch."""
    chk_meta = {
        "checkpoint_id": "test_chk_001",
        "run_id": "test_run_001",
        "step": 5,
        "committed_valid_targets": 250,
        "processed_valid_targets": 250,
        "plan_id": "test_plan_001",
        "model_config": {"architecture": "transformer_baseline", "vocab_size": 32},
        "created_at": "2026-09-19T08:00:00Z",
    }
    meta_path = tmp_path / "checkpoint_meta.json"
    meta_path.write_text(json.dumps(chk_meta), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "run", "inspect", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Inspect failed:\n{result.stderr}\n{result.stdout}"
    assert "test_chk_001" in result.stdout
    assert "committed_valid_targets: 250" in result.stdout

    # Test --json output
    result_json = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "run", "inspect", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result_json.returncode == 0
    parsed = json.loads(result_json.stdout.strip())
    assert parsed["checkpoint_id"] == "test_chk_001"
    assert parsed["step"] == 5


@pytest.mark.serial
def test_cli_train_and_resume_subprocess(tmp_path: Path) -> None:
    """Verify xlm train and xlm resume subprocess workflow."""
    plan_data = {
        "schema_version": 1,
        "kind": "experiment_plan",
        "id": "cli_plan_test_01",
        "status": "planned",
        "track": "baseline",
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 260,
            "num_layers": 1,
            "hidden_size": 16,
            "num_attention_heads": 2,
            "intermediate_size": 32,
            "context_length": 8,
            "attention_backend": "eager",
            "tie_embeddings": True,
        },
        "data": {
            "synthetic_tokens": [i % 28 + 4 for i in range(1000)],
            "tokenizer_artifact": "byte",
        },
        "objective": {
            "type": "cross_entropy",
            "version": "1",
        },
        "optimizer": {
            "type": "adamw",
            "version": "1",
            "lr": 0.01,
            "weight_decay": 0.0,
        },
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "context_length": 8,
            "global_batch_valid_targets": 16,
            "gradient_clip_norm": 1.0,
            "budget": {
                "max_valid_targets": 32,
            },
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 8,
                "horizon_valid_targets": 32,
            },
        },
        "evaluation": {
            "suite": "search",
        },
        "resources": {},
        "authorization": {
            "state": "authorized",
        },
        "code_hash": "code_hash_12345678",
        "dependency_hash": "dep_hash_12345678",
    }

    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

    env = dict(os.environ)
    env["XLM_HOME"] = str(tmp_path / "xlm_home")

    # 1. Train
    train_res = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "train", str(plan_file), "--device", "cpu"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert train_res.returncode == 0, f"Train failed:\n{train_res.stderr}\n{train_res.stdout}"
    assert "Training Run Complete" in train_res.stdout
    assert "Committed Targets:  32" in train_res.stdout

    # 2. Resume with --fork and extended budget
    chk_dir = tmp_path / "xlm_home" / "checkpoints" / "run_cli_plan_test_01_final"
    assert chk_dir.is_dir()
    original = {p.name: p.read_bytes() for p in chk_dir.iterdir() if p.is_file()}
    complete = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "resume", str(chk_dir), "--device", "cpu"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert complete.returncode == 0, complete.stderr
    assert "already committed" in complete.stdout
    assert original == {p.name: p.read_bytes() for p in chk_dir.iterdir() if p.is_file()}
    changed_without_fork = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "resume", str(chk_dir), "--budget", "48"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert changed_without_fork.returncode != 0
    assert "explicit fork" in changed_without_fork.stderr

    resume_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "resume",
            str(chk_dir),
            "--fork",
            "--budget",
            "48",
            "--device",
            "cpu",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert resume_res.returncode == 0, f"Resume failed:\n{resume_res.stderr}\n{resume_res.stdout}"
    assert "Fork Run Complete" in resume_res.stdout
    assert "Committed Targets:  48" in resume_res.stdout
    fork = tmp_path / "xlm_home/checkpoints" / f"fork_from_{chk_dir.name}_final"
    parent_execution = json.loads((chk_dir / "execution.json").read_text())
    fork_execution = json.loads((fork / "execution.json").read_text())
    assert parent_execution["plan_hash"] != fork_execution["plan_hash"]
    assert (
        json.loads((fork / "checkpoint_meta.json").read_text())["parent_checkpoint_id"]
        == chk_dir.name
    )
