"""Subprocess acceptance tests for xlm evaluate, xlm generate, and extended xlm demo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.serial
def test_cli_demo_complete_vertical_slice_subprocess() -> None:
    """Verify extended xlm demo runs cleanly through vertical slice."""
    result = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "demo"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Demo failed:\n{result.stderr}\n{result.stdout}"
    assert "Complete Offline Vertical Slice Successfully Completed (P00-P06)!" in result.stdout
    assert "Part 1 completed: 100 targets" in result.stdout
    assert "Part 2 completed: 200 targets" in result.stdout
    assert "Part 3: Evaluating offline diagnostic fixtures" in result.stdout
    assert "Part 4: Autoregressive text generation" in result.stdout
    assert "Part 5: Emitting structured vertical slice summary report" in result.stdout
    assert "Offline MC Accuracy" in result.stdout
    assert "Document Text BPB" in result.stdout


@pytest.mark.serial
def test_cli_evaluate_and_generate_subprocess(tmp_path: Path) -> None:
    """Verify xlm evaluate and xlm generate commands run cleanly on a saved checkpoint."""

    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.models.serialization import save_model_to_directory
    from xlm.models.transformer import TransformerBaseline

    # 1. Author a small model artifact
    cfg = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=260,
        context_length=128,
        hidden_size=64,
        intermediate_size=128,
        num_layers=2,
        num_attention_heads=4,
        attention_backend="eager",
    )
    model = TransformerBaseline(cfg, seed=42)
    model_dir = tmp_path / "model_ckpt"
    save_model_to_directory(model, model_dir)

    env = dict(os.environ)
    env["XLM_HOME"] = str(tmp_path / "xlm_home")

    # 2. Test xlm evaluate
    eval_res = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "evaluate", str(model_dir), "--device", "cpu"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert eval_res.returncode == 0, f"Evaluate failed:\n{eval_res.stderr}\n{eval_res.stdout}"
    assert "Evaluation Results for Checkpoint: model_ckpt" in eval_res.stdout
    assert "synthetic_offline_multiple_choice_v1" in eval_res.stdout

    # Test xlm evaluate with --json
    eval_json_res = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "evaluate", str(model_dir), "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert eval_json_res.returncode == 0
    parsed = json.loads(eval_json_res.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 2

    # 3. Test xlm generate (greedy and sampled)
    gen_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "generate",
            str(model_dir),
            "--prompt",
            "The quick brown fox",
            "--max-new-tokens",
            "12",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert gen_res.returncode == 0, f"Generate failed:\n{gen_res.stderr}\n{gen_res.stdout}"
    assert "XLM Text Generation Result" in gen_res.stdout
    assert "[Prompt]:" in gen_res.stdout
    assert "[Completion]:" in gen_res.stdout

    # Test xlm generate with --json
    gen_json_res = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "generate",
            str(model_dir),
            "--prompt",
            "Test",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert gen_json_res.returncode == 0
    gen_data = json.loads(gen_json_res.stdout)
    assert "generated_token_ids" in gen_data
    assert "finish_reason" in gen_data
