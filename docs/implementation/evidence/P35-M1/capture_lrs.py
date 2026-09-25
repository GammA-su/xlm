"""Record the LRs optimizer.step actually read for the P35 M1 report.

Bounded authored run: a 1-layer, 16-wide toy transformer on synthetic tokens,
two or three updates per case. This is not training and produces no model.
Run from the repository root with src/ and tests/ on PYTHONPATH:

    python docs/implementation/evidence/P35-M1/capture_lrs.py <output.json>
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import torch

from test_p35_science import (
    CPU_RUNTIME,
    CUDA_STATISTICAL,
    REFERENCE,
    capture_step_lrs,
    make_trainer,
    reference_tokens,
    science,
)


def run(root: Path, *, scientific: bool, budget: int, device: str = "cpu") -> dict[str, Any]:
    cuda = device == "cuda"
    trainer = make_trainer(
        root,
        science_state=(
            science(runtime=CUDA_STATISTICAL if cuda else CPU_RUNTIME) if scientific else None
        ),
        tokens=reference_tokens(budget),
        budget=budget,
        device=device,
        backend="sdpa" if cuda else "eager",
        precision="bf16_fp32_master" if cuda else "fp32",
        **REFERENCE,
    )
    captured = capture_step_lrs(trainer)
    metrics = []
    while (step := trainer.train_step()) is not None:
        metrics.append(
            {
                "step": step.step,
                "valid_targets": step.valid_targets,
                "committed_valid_targets": step.committed_valid_targets,
                "learning_rate": step.learning_rate,
                "lr_used": list(step.lr_used),
                "lr_schedule_counter": step.lr_schedule_counter,
                "lr_next": step.lr_next,
            }
        )
    return {
        "policy": trainer.science.policy.lr_policy,
        "device": device,
        "captured_at_optimizer_step": captured,
        "metrics": metrics,
        "lr_receipts": trainer.science.lr_receipts,
        "runtime_receipt": trainer.science.runtime_receipts[-1]
        if trainer.science.runtime_receipts
        else None,
    }


def main(output: Path) -> None:
    torch.set_num_threads(1)
    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cases["legacy_cpu"] = run(root / "a", scientific=False, budget=131_072)
        cases["endpoint_cpu"] = run(root / "b", scientific=True, budget=131_072)
        cases["endpoint_partial_cpu"] = run(root / "c", scientific=True, budget=66_536)
        if torch.cuda.is_available():
            cases["endpoint_cuda_statistical"] = run(
                root / "d", scientific=True, budget=131_072, device="cuda"
            )
    output.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, case in cases.items():
        print(name, [row[0] for row in case["captured_at_optimizer_step"]])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
