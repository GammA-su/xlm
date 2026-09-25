"""P35 M2 CUDA semantic check: evaluation between updates cannot change CUDA training.

Bounded: a ~20k-parameter model, four 16-target updates per run, two runs, in a
fresh process (strict deterministic mode needs ``CUBLAS_WORKSPACE_CONFIG`` at
process start). Not a throughput, quality or capacity measurement.
"""

# ruff: noqa: E501  (long lines live inside the embedded subprocess script)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import torch

needs_cuda = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA hardware required")

SCRIPT = r"""
import json, math, sys
from pathlib import Path
import torch
from p35_eval_support import (
    RecordingEvaluator, build_trainer, dev_inventories, fixture_plan, run_all,
)
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.lm_validation import LMScoringPolicy, LMValidationEvaluator, load_pinned_inventory
from xlm.tokenizers.byte import ByteTokenizer

root = Path(sys.argv[1])
runtime = {
    "attention_policy": "strict_deterministic_v1",
    "matmul_tf32": "disabled",
    "bf16_reduced_precision_reduction": "allowed",
}
tokenizer = ByteTokenizer()
path, manifest_id = dev_inventories(root / "inputs", tokenizer)["full"]
full = LMValidationEvaluator(
    EventTier.FULL_LM,
    load_pinned_inventory(path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint),
    tokenizer,
    LMScoringPolicy(context_length=8, rolling_stride=4, forward_precision="bf16_autocast",
                    logprob_dtype="fp32"),
)
quick = RecordingEvaluator(EventTier.QUICK_LM, consume_rng=True)

def run(name, evaluate):
    trainer = build_trainer(
        root / name, device="cuda", runtime=runtime, dropout=0.1, budget=64,
        evaluators={EventTier.QUICK_LM: quick, EventTier.FULL_LM: full} if evaluate else None,
        plan=fixture_plan(64, quick_lm=[0, 16, 32, 48, 64], full_lm=[16, 64]) if evaluate else None,
    )
    run_all(trainer)
    return trainer, [s.clone() for s in torch.cuda.get_rng_state_all()], torch.get_rng_state()

plain, plain_cuda, plain_cpu = run("plain", False)
evaluated, eval_cuda, eval_cpu = run("eval", True)
w0, w1 = plain.model.state_dict(), evaluated.model.state_dict()
found = evaluated.evaluation.canonical_receipts()
full_receipt = found["full_lm@64"]
print(json.dumps({
    "device": torch.cuda.get_device_name(),
    "weights_equal": all(torch.equal(w0[k], w1[k]) for k in w0),
    "cuda_rng_equal": all(torch.equal(a, b) for a, b in zip(plain_cuda, eval_cuda)),
    "cpu_rng_equal": torch.equal(plain_cpu, eval_cpu),
    "lr_receipts_equal": plain.science.lr_receipts == evaluated.science.lr_receipts,
    "valid_targets": [row[2] for row in evaluated.science.lr_receipts],
    "statuses": {k: v["status"] for k, v in found.items()},
    "restored_rng": "rng" in found["quick_lm@16"]["guard"]["restored"],
    "guard_changed": sorted({c for v in found.values() for c in v["guard"]["changed"]}),
    "full_forward_precision": full_receipt["evaluator"]["scoring_policy"]["forward_precision"],
    "full_primary_finite": math.isfinite(full_receipt["metrics"]["equal_domain_text_ce_nats_per_token"]),
    "calls": len(quick.calls),
}))
"""


@pytest.mark.cuda
@needs_cuda
def test_cuda_evaluation_between_updates_changes_nothing(tmp_path: Path) -> None:
    env = dict(os.environ, CUBLAS_WORKSPACE_CONFIG=":4096:8")
    completed = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["weights_equal"] and result["lr_receipts_equal"]
    assert result["cuda_rng_equal"] and result["cpu_rng_equal"]
    assert result["valid_targets"] == [16, 16, 16, 16]
    assert set(result["statuses"].values()) == {"complete"} and len(result["statuses"]) == 7
    assert result["restored_rng"] and result["guard_changed"] == []
    assert result["full_forward_precision"] == "bf16_autocast" and result["full_primary_finite"]
    assert result["calls"] == 5
