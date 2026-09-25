"""Regenerate the P35 legacy fixture with the ORIGINAL pre-P35 code, never with HEAD.

The fixture proves that schema-v1 material produced before the P35 migration
still resolves, loads and continues under the historical LR/RNG semantics.
It must be produced by the P35 M1 starting commit, exported outside the tree:

    git archive 991dd397b5c682d3250b5f7437465192869b13f5 src | tar -x -C <old>
    PYTHONPATH=<old>/src python tests/fixtures/p35_legacy_checkpoint/generate.py <out>

then copy ``<out>/checkpoint`` and ``<out>/expected.json`` here. Run with one
CPU thread (OMP/MKL=1), as the test suite does, so CPU arithmetic is comparable.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import torch

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.manifest import identity_digest
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.paths import ArtifactPaths
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import TrainingBatcher
from xlm.training.trainer import Trainer

SOURCE_COMMIT = "991dd397b5c682d3250b5f7437465192869b13f5"
CHECKPOINT_ID = "p35_legacy_fixture"
TOKENS = [4 + (i * 7) % 27 for i in range(200)]

# A schema-v1 executable configuration with no science fields, as every
# pre-P35 recipe/plan was written. Its resolved identity must never change.
LEGACY_CONFIG: dict[str, Any] = {
    "model": {
        "architecture": "transformer_baseline",
        "vocab_size": 64,
        "num_layers": 2,
        "hidden_size": 32,
        "num_attention_heads": 2,
        "intermediate_size": 64,
        "context_length": 16,
        "attention_backend": "eager",
    },
    "data": {"synthetic_tokens": [4 + i % 60 for i in range(128)]},
    "objective": {"type": "cross_entropy"},
    "optimizer": {"type": "adamw", "lr": 0.01},
    "training": {
        "device": "cpu",
        "precision": "fp32",
        "init_seed": 7,
        "data_seed": 42,
        "context_length": 16,
        "global_batch_valid_targets": 8,
        "budget": {"max_valid_targets": 8, "max_train_seconds": 10},
        "schedule": {
            "type": "warmup_cosine",
            "warmup_valid_targets": 0,
            "horizon_valid_targets": 8,
        },
        "checkpoint_every_valid_targets": 8,
    },
}


def build(root: Path, **trainer_kwargs: Any) -> Trainer:
    """Legacy domain-API trainer; identical construction in old and new code."""
    paths = ArtifactPaths(root=root)
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = TransformerBaselineConfig(
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=7)
    optimizer, manifest = create_adamw_optimizer(AdamWConfig(lr=0.01), model=model)
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=32, horizon_valid_targets=64, min_lr_ratio=0.1
        ),
        base_lr=0.01,
    )
    return Trainer(
        model=model,
        objective=CrossEntropyObjective(CrossEntropyObjectiveConfig()),
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=TrainingBatcher(TOKENS, context_length=8, global_batch_valid_targets=16),
        checkpoint_manager=manager,
        run_id="p35_legacy_run",
        plan_id="p35_legacy_plan",
        max_valid_targets=64,
        **trainer_kwargs,
    )


def model_digest(model: torch.nn.Module) -> str:
    digest = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        digest.update(name.encode())
        digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def capture_step_lrs(trainer: Trainer) -> list[list[float]]:
    """Record the group LRs at the moment ``optimizer.step`` is invoked."""
    captured: list[list[float]] = []
    original = trainer.optimizer.step

    def step(*args: Any, **kwargs: Any) -> Any:
        captured.append([float(g["lr"]) for g in trainer.optimizer.param_groups])
        return original(*args, **kwargs)

    trainer.optimizer.step = step  # type: ignore[method-assign]
    return captured


def main(out: Path) -> None:
    from xlm.experiments.execution import resolve_execution_config

    torch.set_num_threads(1)
    trainer = build(out / "run")
    captured = capture_step_lrs(trainer)
    trainer.train_step()
    published = trainer._save_checkpoint(CHECKPOINT_ID)
    saved_group_lrs = [float(g["lr"]) for g in trainer.optimizer.param_groups]
    trainer.train_step()
    resolved, _ = resolve_execution_config(json.loads(json.dumps(LEGACY_CONFIG)))
    shutil.copytree(published, out / "checkpoint")
    (out / "expected.json").write_text(
        json.dumps(
            {
                "source_commit": SOURCE_COMMIT,
                "checkpoint_id": CHECKPOINT_ID,
                "update1_lr_used": captured[0],
                "saved_group_lrs": saved_group_lrs,
                "update2_lr_used": captured[1],
                "update2_model_sha256": model_digest(trainer.model),
                "legacy_resolved_training_keys": sorted(resolved["training"]),
                "legacy_resolved_config_digest": identity_digest(resolved),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
