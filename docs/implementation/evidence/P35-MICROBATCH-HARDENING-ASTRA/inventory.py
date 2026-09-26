"""Read-only metadata inventory; meta tensors only, no extra training targets."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import torch

from xlm.models.transformer import create_transformer_baseline
from xlm.objectives.cross_entropy import CrossEntropyObjective


def main(output: Path) -> None:
    config = json.loads(Path("recipes/models/50m.yaml").read_text())
    config["attention_backend"] = "sdpa"
    with torch.device("meta"):
        model = create_transformer_baseline(config, seed=101)
        objective = CrossEntropyObjective()
    parameters = list(model.parameters())
    assert all(p.device.type == "meta" for p in parameters)
    count = sum(p.numel() for p in parameters)
    evidence = output.parent
    scratch = Path(".hardening-scratch")
    report = {
        "scope": "meta shape inspection and file sizes; zero additional training targets",
        "unique_model_parameters": count,
        "trainable_model_parameters": sum(p.numel() for p in parameters if p.requires_grad),
        "unique_parameter_tensors": len(parameters),
        "trainable_objective_parameters": sum(p.numel() for p in objective.parameters()),
        "objective_state_entries": len(objective.state_dict()),
        "fp32_master_parameter_bytes": 4 * count,
        "adamw_expected_two_moment_fp32_elements": 2 * count,
        "adamw_expected_two_moment_bytes": 8 * count,
        "adamw_expected_step_scalars": len(parameters),
        "optimizer_qualification": (
            "Analytic stock AdamW state accounting, not a captured optimizer inventory; "
            "no optimizer checkpoint was saved in the 50M measurement. GPU peaks in "
            "cost-50m.json include the actually allocated training state."
        ),
        "evidence_bytes_before_this_inventory": sum(
            p.stat().st_size for p in evidence.rglob("*") if p.is_file()
        ),
        "scratch_bytes_at_inventory": sum(
            p.stat().st_size for p in scratch.rglob("*") if p.is_file()
        ),
        "dependencies": {
            name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
            for name in ("pyproject.toml", "uv.lock", ".python-version")
        },
        "nvidia_smi": subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip(),
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
