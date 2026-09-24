"""Unit and integration tests for XLM Trainer engine."""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.contracts import RunStatus
from xlm.core.paths import ArtifactPaths
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import TrainingBatcher
from xlm.training.trainer import NonFiniteGradientError, Trainer


def _setup_test_components(
    tmp_path: Path,
    vocab_size: int = 32,
    context_length: int = 8,
    global_batch_targets: int = 16,
    max_valid_targets: int = 64,
    tokens: list[int] | None = None,
    lr: float = 0.01,
    weight_decay: float = 0.0,
    seed: int = 42,
    exhaustion_policy: str = "repeat_bounded",
    max_document_exposures: int = 100,
) -> tuple[Trainer, TransformerBaseline, TrainingBatcher, CheckpointManager]:
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=vocab_size,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=context_length,
        attention_backend="eager",
        tie_embeddings=True,
    )
    model = TransformerBaseline(config, seed=seed)
    obj = CrossEntropyObjective(CrossEntropyObjectiveConfig())
    opt_config = AdamWConfig(lr=lr, weight_decay=weight_decay)
    opt, manifest = create_adamw_optimizer(opt_config, model=model)
    sched_config = WarmupCosineScheduleConfig(
        warmup_valid_targets=max_valid_targets // 4,
        horizon_valid_targets=max_valid_targets,
        min_lr_ratio=0.1,
    )
    sched = WarmupCosineSchedule(sched_config, base_lr=lr)

    if tokens is None:
        # Repeating pattern of tokens < vocab_size
        tokens = [((i % (vocab_size - 4)) + 4) for i in range(100)]

    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=global_batch_targets,
        exhaustion_policy=exhaustion_policy,
        max_document_exposures=max_document_exposures,
    )

    trainer = Trainer(
        model=model,
        objective=obj,
        optimizer=opt,
        optimizer_manifest=manifest,
        schedule=sched,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id="test_run_01",
        plan_id="test_plan_01_id",
        device="cpu",
        precision="fp32",
        gradient_clip_norm=1.0,
        max_valid_targets=max_valid_targets,
    )
    return trainer, model, batcher, manager


def test_trainer_overfitting_criterion(tmp_path: Path) -> None:
    """Verify trainer overfits on fixed repeated data: loss drops by >50% or reaches <0.5."""
    # A short, predictable sequence repeated
    vocab_size = 16
    pattern = [4, 5, 6, 7, 8, 9, 10, 11] * 20

    trainer, model, _, _ = _setup_test_components(
        tmp_path,
        vocab_size=vocab_size,
        context_length=8,
        global_batch_targets=16,
        max_valid_targets=320,  # 20 steps
        tokens=pattern,
        lr=0.05,
        seed=101,
    )

    summary = trainer.train()
    assert summary.termination_reason == "completed"
    assert len(summary.metrics_history) > 0

    initial_loss = summary.metrics_history[0].loss
    final_loss = summary.final_loss
    assert final_loss is not None

    # Criterion: drops by >50% OR reaches <0.5
    loss_dropped_50_pct = final_loss < (initial_loss * 0.5)
    loss_below_threshold = final_loss < 0.5
    assert loss_dropped_50_pct or loss_below_threshold, (
        f"Overfitting criterion failed: initial {initial_loss:.4f} -> final {final_loss:.4f}"
    )


def test_trainer_exact_token_budget_completion(tmp_path: Path) -> None:
    """Verify trainer completes exactly at declared token budget (e.g. 17 targets)."""
    tokens = [((i % 20) + 4) for i in range(100)]
    budget = 17

    trainer, _, _, _ = _setup_test_components(
        tmp_path,
        vocab_size=32,
        context_length=8,
        global_batch_targets=8,
        max_valid_targets=budget,
        tokens=tokens,
    )

    summary = trainer.train()
    assert summary.termination_reason == "completed"
    assert summary.committed_valid_targets == budget
    # 8 + 8 + 1 -> exactly 3 steps
    assert summary.total_steps == 3
    assert summary.metrics_history[-1].valid_targets == 1


def test_trainer_non_finite_gradient_failure_guard(tmp_path: Path) -> None:
    """Verify NonFiniteGradientError is raised on non-finite values and run marked FAILED."""
    trainer, model, _, manager = _setup_test_components(
        tmp_path,
        vocab_size=32,
        context_length=8,
        global_batch_targets=8,
        max_valid_targets=16,
    )

    # Corrupt model weights with NaN
    with torch.no_grad():
        for p in model.parameters():
            p.fill_(float("nan"))
            break

    with pytest.raises(NonFiniteGradientError):
        trainer.train()

    assert trainer.termination_reason == "failed"
    run_rec = manager.ledger.get_run(trainer.run_id)
    assert run_rec is not None
    assert run_rec["status"] == RunStatus.FAILED.value


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_nonfinite_backward_preserves_optimizer_and_committed_cursor(
    tmp_path: Path, invalid: float
) -> None:
    """A finite forward with a corrupt late gradient must not commit an update."""
    trainer, model, batcher, _ = _setup_test_components(tmp_path)
    before = {name: value.clone() for name, value in model.state_dict().items()}
    cursor = batcher.get_state()
    learning_rates = [group["lr"] for group in trainer.optimizer.param_groups]
    handle = list(model.parameters())[-1].register_hook(  # type: ignore[no-untyped-call]
        lambda grad: grad * invalid
    )
    try:
        with pytest.raises(NonFiniteGradientError, match="Non-finite gradient encountered"):
            trainer.train_step()
    finally:
        handle.remove()
    assert batcher.get_state() == cursor
    assert trainer.step == trainer.committed_valid_targets == trainer.processed_valid_targets == 0
    assert not trainer.optimizer.state
    assert [group["lr"] for group in trainer.optimizer.param_groups] == learning_rates
    for name, value in model.state_dict().items():
        torch.testing.assert_close(value, before[name], atol=0, rtol=0)


def test_trainer_cooperative_interruption(tmp_path: Path) -> None:
    """Verify cooperative interruption saves checkpoint at optimizer boundary and updates ledger."""
    trainer, _, _, manager = _setup_test_components(
        tmp_path,
        vocab_size=32,
        context_length=8,
        global_batch_targets=8,
        max_valid_targets=80,
    )

    # Request stop after 1 step
    def step_callback() -> None:
        trainer._stop_requested = True

    # Execute 1 step manually then trigger stop
    trainer.train_step()
    step_callback()

    summary = trainer.train()
    assert summary.termination_reason == "interrupted"

    # Verify interrupted checkpoint exists
    chk_dir = manager.paths.checkpoints / f"{trainer.run_id}_interrupted"
    assert chk_dir.is_dir()
    assert (chk_dir / "_COMPLETED").is_file()

    # Verify run status in ledger
    run_rec = manager.ledger.get_run(trainer.run_id)
    assert run_rec is not None
    assert run_rec["status"] == RunStatus.INTERRUPTED.value
