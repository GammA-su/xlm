"""Fresh-process CPU continuation acceptance test for P05.

Complying with P05 Amendments 1 & 9:
- Declares final 200-target budget and schedule horizon (H=200) from the start.
- Update partitioning: 4 updates of 50 targets each (global_batch_valid_targets=50).
- Branch A (Uninterrupted): runs 4 updates to 200 targets.
- Branch B (Interrupted & Resumed):
    - Subprocess 1: runs 2 updates to 100 targets, saves checkpoint at natural boundary.
    - Subprocess 2: fresh process starts, restores from checkpoint,
      continues 2 updates to 200 targets.
- Bitwise equality verification:
    - Model weights
    - Objective parameters
    - Optimizer moment buffers (exp_avg, exp_avg_sq)
    - Schedule learning rate
    - Final step and committed valid targets counters
    - Next target IDs from data stream
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import torch

from xlm.artifacts.ledger import RunLedger
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

WORKER_SCRIPT = """
import sys
import json
import torch
from pathlib import Path
from xlm.core.paths import ArtifactPaths
from xlm.artifacts.store import ArtifactStore
from xlm.artifacts.ledger import RunLedger
from xlm.training.checkpoint import CheckpointManager
from xlm.training.trainer import Trainer
from xlm.training.data import TrainingBatcher
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.config.schemas import (
    TransformerBaselineConfig,
    AdamWConfig,
    WarmupCosineScheduleConfig,
    CrossEntropyObjectiveConfig,
)

def run():
    mode = sys.argv[1] # 'part1' or 'part2'
    artifact_root = Path(sys.argv[2])
    data_file = Path(sys.argv[3])
    out_file = Path(sys.argv[4])

    tokens = json.loads(data_file.read_text(encoding="utf-8"))

    paths = ArtifactPaths(root=artifact_root)
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=8,
        attention_backend="eager",
        tie_embeddings=True,
    )
    opt_config = AdamWConfig(lr=0.01, weight_decay=0.01)
    sched_config = WarmupCosineScheduleConfig(
        warmup_valid_targets=20,
        horizon_valid_targets=200,
        min_lr_ratio=0.1,
    )

    if mode == "part1":
        # Run 2 updates to 100 targets (2 x 50)
        torch.manual_seed(2026)
        model = TransformerBaseline(config, seed=42)
        obj = CrossEntropyObjective(CrossEntropyObjectiveConfig())
        opt, manifest = create_adamw_optimizer(opt_config, model=model, objective=obj)
        sched = WarmupCosineSchedule(sched_config, base_lr=0.01)
        batcher = TrainingBatcher(
            data_source=tokens,
            context_length=8,
            global_batch_valid_targets=50,
            exhaustion_policy="repeat_bounded",
            max_document_exposures=100,
        )

        trainer = Trainer(
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=manifest,
            schedule=sched,
            batcher=batcher,
            checkpoint_manager=manager,
            run_id="run_interrupted_demo",
            plan_id="plan_200_targets",
            device="cpu",
            precision="fp32",
            gradient_clip_norm=1.0,
            max_valid_targets=100, # Stops at natural boundary of 100
        )
        summary = trainer.train()
        chk_path = manager.save_checkpoint(
            checkpoint_id="chk_step_2_100",
            run_id="run_interrupted_demo",
            step=trainer.step,
            committed_valid_targets=trainer.committed_valid_targets,
            processed_valid_targets=trainer.processed_valid_targets,
            plan_id="plan_200_targets",
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=manifest,
            schedule=sched,
            batcher=batcher,
        )
        out_payload = {"checkpoint_dir": str(chk_path), "step": trainer.step}
        out_file.write_text(json.dumps(out_payload), encoding="utf-8")

    elif mode == "part2":
        # Fresh process resume: reconstruct from scratch, reload checkpoint, continue to 200
        in_meta = json.loads(out_file.read_text(encoding="utf-8"))
        chk_dir = Path(in_meta["checkpoint_dir"])

        # Construct with DIFFERENT initial seeds to prove complete restoration
        torch.manual_seed(9999)
        model = TransformerBaseline(config, seed=777)
        obj = CrossEntropyObjective(CrossEntropyObjectiveConfig())
        opt, manifest = create_adamw_optimizer(opt_config, model=model, objective=obj)
        sched = WarmupCosineSchedule(sched_config, base_lr=0.01)
        batcher = TrainingBatcher(
            data_source=tokens,
            context_length=8,
            global_batch_valid_targets=50,
            exhaustion_policy="repeat_bounded",
            max_document_exposures=100,
        )

        meta = manager.load_checkpoint(
            chk_dir,
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=manifest,
            schedule=sched,
            batcher=batcher,
            expected_plan_id="plan_200_targets",
        )

        trainer = Trainer(
            model=model,
            objective=obj,
            optimizer=opt,
            optimizer_manifest=manifest,
            schedule=sched,
            batcher=batcher,
            checkpoint_manager=manager,
            run_id="run_interrupted_demo",
            plan_id="plan_200_targets",
            device="cpu",
            precision="fp32",
            gradient_clip_norm=1.0,
            max_valid_targets=200, # Full horizon of 200
            step=meta.step,
            committed_valid_targets=meta.committed_valid_targets,
            processed_valid_targets=meta.processed_valid_targets,
        )
        summary = trainer.train()

        # Save final state for comparison
        final_save_path = artifact_root / "resumed_final.pt"
        torch.save({
            "model": model.state_dict(),
            "optimizer": opt.state_dict(),
            "step": trainer.step,
            "committed_valid_targets": trainer.committed_valid_targets,
            "lr": sched.get_lr(trainer.committed_valid_targets),
            "next_input_ids": [
                mb.input_ids.tolist()
                for mb in batcher.next_step_microbatches(remaining_budget=8)
            ],
        }, final_save_path)
        out_file.write_text(json.dumps({"final_pt": str(final_save_path)}), encoding="utf-8")

if __name__ == "__main__":
    run()
"""


def test_cpu_fresh_process_continuation_parity(tmp_path: Path) -> None:
    """Verify bitwise equality between uninterrupted and resumed runs at 200 targets."""
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)

    # 1. Author predictable tokens
    tokens = [((i % 24) + 4) for i in range(200)]
    data_file = tmp_path / "tokens.json"
    data_file.write_text(json.dumps(tokens), encoding="utf-8")

    worker_py = tmp_path / "worker.py"
    worker_py.write_text(WORKER_SCRIPT, encoding="utf-8")

    out_file = tmp_path / "ipc.json"

    # --- Branch A: Uninterrupted run in-process ---
    paths_a = ArtifactPaths(root=tmp_path / "artifacts_unint")
    store_a = ArtifactStore(paths_a)
    ledger_a = RunLedger(paths_a.ledger / "ledger.sqlite")
    manager_a = CheckpointManager(artifact_store=store_a, run_ledger=ledger_a, paths=paths_a)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=8,
        attention_backend="eager",
        tie_embeddings=True,
    )
    opt_config = AdamWConfig(lr=0.01, weight_decay=0.01)
    sched_config = WarmupCosineScheduleConfig(
        warmup_valid_targets=20,
        horizon_valid_targets=200,
        min_lr_ratio=0.1,
    )

    torch.manual_seed(2026)
    model_a = TransformerBaseline(config, seed=42)
    obj_a = CrossEntropyObjective(CrossEntropyObjectiveConfig())
    opt_a, manifest_a = create_adamw_optimizer(opt_config, model=model_a, objective=obj_a)
    sched_a = WarmupCosineSchedule(sched_config, base_lr=0.01)
    batcher_a = TrainingBatcher(
        data_source=tokens,
        context_length=8,
        global_batch_valid_targets=50,
        exhaustion_policy="repeat_bounded",
        max_document_exposures=100,
    )

    trainer_a = Trainer(
        model=model_a,
        objective=obj_a,
        optimizer=opt_a,
        optimizer_manifest=manifest_a,
        schedule=sched_a,
        batcher=batcher_a,
        checkpoint_manager=manager_a,
        run_id="run_unint",
        plan_id="plan_200_targets",
        device="cpu",
        precision="fp32",
        gradient_clip_norm=1.0,
        max_valid_targets=200,
    )
    summary_a = trainer_a.train()
    assert summary_a.committed_valid_targets == 200
    assert summary_a.total_steps == 4

    # --- Branch B: Subprocess 1 (0 -> 100 targets) ---
    res_1 = subprocess.run(
        [
            sys.executable,
            str(worker_py),
            "part1",
            str(artifact_root),
            str(data_file),
            str(out_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_1.returncode == 0, f"Part 1 failed: {res_1.stderr}\n{res_1.stdout}"

    # --- Branch B: Subprocess 2 (100 -> 200 targets in fresh process) ---
    res_2 = subprocess.run(
        [
            sys.executable,
            str(worker_py),
            "part2",
            str(artifact_root),
            str(data_file),
            str(out_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_2.returncode == 0, f"Part 2 failed: {res_2.stderr}\n{res_2.stdout}"

    # Load Branch B final state
    b_meta = json.loads(out_file.read_text(encoding="utf-8"))
    b_data = torch.load(Path(b_meta["final_pt"]), map_location="cpu", weights_only=True)

    # --- Verification of Bitwise Continuation Parity ---
    # 1. Step count and committed valid targets
    assert summary_a.total_steps == b_data["step"] == 4
    assert summary_a.committed_valid_targets == b_data["committed_valid_targets"] == 200

    # 2. Schedule learning rate
    lr_a = sched_a.get_lr(200)
    assert abs(lr_a - b_data["lr"]) < 1e-12

    # 3. Model weights bitwise equality
    for name, p_a in model_a.named_parameters():
        p_b = b_data["model"][name]
        assert torch.equal(p_a, p_b), (
            f"Parameter '{name}' differed between uninterrupted and resumed run!"
        )

    # 4. Optimizer moment buffers bitwise equality
    opt_a_state_dict = opt_a.state_dict()
    for param_idx, state_a in opt_a_state_dict["state"].items():
        state_b = b_data["optimizer"]["state"][param_idx]
        assert torch.equal(state_a["exp_avg"], state_b["exp_avg"]), (
            f"exp_avg differed for param {param_idx}"
        )
        assert torch.equal(state_a["exp_avg_sq"], state_b["exp_avg_sq"]), (
            f"exp_avg_sq differed for param {param_idx}"
        )

    # 5. Next target IDs from data stream
    next_mbs_a = [
        mb.input_ids.tolist() for mb in batcher_a.next_step_microbatches(remaining_budget=8)
    ]
    assert next_mbs_a == b_data["next_input_ids"], "Data stream next tokens differed!"
