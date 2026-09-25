"""P34 prefetching batcher under the real Trainer: exactness, failures and resume.

CPU training is deterministic here, so every recovery must reproduce the
uninterrupted synchronous run bit for bit: parameters, metrics and data state.
"""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from test_prefetch import GLOBAL, make_batcher, shards  # noqa: F401  (fixture)
from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
from xlm.data.tokens import TokenShardReader
from xlm.tokenizers.byte import ByteTokenizer

torch = pytest.importorskip("torch")

from xlm.artifacts.ledger import RunLedger  # noqa: E402
from xlm.artifacts.store import ArtifactStore  # noqa: E402
from xlm.config.schemas import (  # noqa: E402
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.paths import ArtifactPaths  # noqa: E402
from xlm.models.transformer import TransformerBaseline  # noqa: E402
from xlm.objectives.cross_entropy import CrossEntropyObjective  # noqa: E402
from xlm.optimizers.adamw import create_adamw_optimizer  # noqa: E402
from xlm.schedules.cosine import WarmupCosineSchedule  # noqa: E402
from xlm.training.checkpoint import CheckpointManager  # noqa: E402
from xlm.training.trainer import Trainer  # noqa: E402

UPDATES = 5
BUDGET = UPDATES * GLOBAL


def build(
    root: Path, batcher: Any, **counters: int
) -> tuple[Trainer, CheckpointManager, list[Path]]:
    paths = ArtifactPaths(root=root)
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=ByteTokenizer().vocab_size,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=32,
        attention_backend="eager",
        tie_embeddings=True,
    )
    model = TransformerBaseline(config, seed=1234)
    optimizer, manifest = create_adamw_optimizer(AdamWConfig(lr=1e-3), model=model)
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=GLOBAL, horizon_valid_targets=BUDGET, min_lr_ratio=0.1
        ),
        base_lr=1e-3,
    )
    trainer = Trainer(
        model=model,
        objective=CrossEntropyObjective(CrossEntropyObjectiveConfig()),
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=manager,
        run_id="prefetch_run",
        plan_id="prefetch_plan",
        max_valid_targets=BUDGET,
        checkpoint_every_valid_targets=GLOBAL,
        **counters,
    )
    saved: list[Path] = []
    original = trainer._save_checkpoint

    def save(checkpoint_id: str) -> Path:
        saved.append(original(checkpoint_id))
        return saved[-1]

    trainer._save_checkpoint = save  # type: ignore[method-assign]
    return trainer, manager, saved


def digest(model: torch.nn.Module) -> str:
    value = hashlib.sha256()
    for name, tensor in model.state_dict().items():
        value.update(name.encode() + tensor.detach().contiguous().numpy().tobytes())
    return value.hexdigest()


def prefetcher(shards: dict[str, TokenShardReader], state: dict[str, Any] | None = None) -> Any:  # noqa: F811
    source = make_batcher(shards)
    return PrefetchingBatcher(
        ProducerSpec.from_batcher(source),
        source.get_state() if state is None else state,
        verify_content=True,
    )


def run_to_end(trainer: Trainer, metrics: list[Any]) -> None:
    while (metric := trainer.train_step()) is not None:
        metrics.append(metric.to_dict() | {"step_time_seconds": 0.0})


def resume(root: Path, checkpoint: Path, shards: dict[str, TokenShardReader]) -> Trainer:  # noqa: F811
    batcher = prefetcher(shards)
    trainer, manager, _ = build(root, batcher)
    meta = manager.load_checkpoint(
        checkpoint,
        model=trainer.model,
        objective=trainer.objective,
        optimizer=trainer.optimizer,
        optimizer_manifest=trainer.optimizer_manifest,
        schedule=trainer.schedule,
        batcher=batcher,
    )
    trainer.step = meta.step
    trainer.committed_valid_targets = meta.committed_valid_targets
    trainer.processed_valid_targets = meta.processed_valid_targets
    trainer.next_checkpoint_target = meta.committed_valid_targets + GLOBAL
    return trainer


@pytest.fixture
def reference(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
) -> tuple[str, list[Any], dict[str, Any]]:
    batcher = make_batcher(shards)
    trainer, _, _ = build(tmp_path / "reference", batcher)
    metrics: list[Any] = []
    run_to_end(trainer, metrics)
    assert trainer.committed_valid_targets == BUDGET
    return digest(trainer.model), metrics, batcher.get_state()


def test_prefetched_training_is_bit_exact(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
) -> None:
    with prefetcher(shards) as batcher:
        trainer, _, _ = build(tmp_path / "prefetch", batcher)
        metrics: list[Any] = []
        run_to_end(trainer, metrics)
        assert (digest(trainer.model), metrics, batcher.get_state()) == reference


class Injected(RuntimeError):
    """A simulated consumer-side failure."""


def inject(stage: str, trainer: Trainer, at_update: int) -> ExitStack:
    """Fail once while executing update ``at_update`` (0-based) at ``stage``."""
    stack = ExitStack()
    fired = {"done": False}

    def armed() -> bool:
        return not fired["done"] and trainer.step == at_update

    def fire() -> None:
        fired["done"] = True
        raise Injected(stage)

    if stage == "before_h2d":
        to = torch.Tensor.to

        def failing_to(tensor: Any, *args: Any, **kwargs: Any) -> Any:
            if armed():
                fire()
            return to(tensor, *args, **kwargs)

        stack.enter_context(patch.object(torch.Tensor, "to", failing_to))
    elif stage == "device_failure_mid_accumulation":
        forward = trainer.exec_model.forward
        calls = {"n": 0}

        def failing_forward(*args: Any, **kwargs: Any) -> Any:
            if armed():
                calls["n"] += 1
                if calls["n"] == 2:  # after one microbatch already accumulated gradients
                    fire()
            return forward(*args, **kwargs)

        stack.enter_context(patch.object(trainer.exec_model, "forward", failing_forward))
    elif stage == "optimizer":
        step = trainer.optimizer.step

        def failing_step(*args: Any, **kwargs: Any) -> Any:
            if armed():
                fire()
            return step(*args, **kwargs)

        stack.enter_context(patch.object(trainer.optimizer, "step", failing_step))
    else:
        commit = trainer.batcher.commit

        def failing_commit() -> None:
            # trainer.step has already advanced when the batcher commits.
            if not fired["done"] and trainer.step == at_update + 1:
                if stage == "after_commit":
                    commit()
                fire()
            commit()

        stack.enter_context(patch.object(trainer.batcher, "commit", failing_commit))
    return stack


STAGES = [
    "before_h2d",
    "device_failure_mid_accumulation",
    "optimizer",
    "before_commit",
    "after_commit",
]


@pytest.mark.parametrize("stage", STAGES)
def test_consumer_failure_recovers_from_checkpoint_exactly(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
    stage: str,
) -> None:
    metrics: list[Any] = []
    with prefetcher(shards) as batcher:
        trainer, _, saved = build(tmp_path / "run", batcher)
        with inject(stage, trainer, at_update=2), pytest.raises(Injected):
            run_to_end(trainer, metrics)
        # Failures at/after the optimizer boundary leave the trainer in doubt;
        # earlier failures roll back cleanly and stay serviceable.
        assert trainer._update_in_doubt == (stage in ("optimizer", "before_commit", "after_commit"))
        # Crash: in-memory model/optimizer may be past the committed boundary.
        # Recovery trusts only the last published checkpoint.
        last = saved[-1]
        # After a post-commit failure the update is committed in memory, but its
        # checkpoint was never published; resume replays it from the prior one.
        expected_committed = 3 * GLOBAL if stage == "after_commit" else 2 * GLOBAL
        assert batcher.get_state()["committed_valid_targets"] == expected_committed
    assert len(metrics) == 2 and len(saved) == 2
    resumed = resume(tmp_path / "run", last, shards)
    try:
        run_to_end(resumed, metrics)
        assert (digest(resumed.model), metrics, resumed.batcher.get_state()) == reference
    finally:
        resumed.batcher.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("stage", ["before_h2d", "device_failure_mid_accumulation"])
def test_pre_optimizer_failure_retries_in_process_exactly(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
    stage: str,
) -> None:
    metrics: list[Any] = []
    with prefetcher(shards) as batcher:
        trainer, _, _ = build(tmp_path / "run", batcher)
        with inject(stage, trainer, at_update=2):
            with pytest.raises(Injected):
                run_to_end(trainer, metrics)
            assert not trainer._update_in_doubt
            batcher.rollback()  # parameters are untouched before optimizer.step
            run_to_end(trainer, metrics)
        assert (digest(trainer.model), metrics, batcher.get_state()) == reference
        # One rollback from the fail-closed train_step wrapper plus the explicit one.
        assert batcher.stats()["rollbacks"] == 2


def test_optimizer_failure_requires_durable_recovery(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
) -> None:
    from xlm.training.trainer import RecoveryRequiredError

    metrics: list[Any] = []
    with prefetcher(shards) as batcher:
        trainer, _, saved = build(tmp_path / "run", batcher)
        with inject("optimizer", trainer, at_update=2), pytest.raises(Injected):
            run_to_end(trainer, metrics)
        # Ambiguous partial mutation: the real step may have run before failing.
        # No more updates, no checkpoint, no in-process retry -- reload from durable.
        assert trainer._update_in_doubt
        assert batcher.get_state()["committed_valid_targets"] == 2 * GLOBAL
        with pytest.raises(RecoveryRequiredError):
            trainer.train_step()
        with pytest.raises(RecoveryRequiredError):
            trainer._save_checkpoint("invalid")
        last = saved[-1]
    assert len(metrics) == 2 and len(saved) == 2
    resumed = resume(tmp_path / "run", last, shards)
    try:
        run_to_end(resumed, metrics)
        assert (digest(resumed.model), metrics, resumed.batcher.get_state()) == reference
    finally:
        resumed.batcher.close()  # type: ignore[attr-defined]


def test_resume_regenerates_the_discarded_prefetched_update(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
) -> None:
    metrics: list[Any] = []
    with prefetcher(shards) as batcher:
        trainer, _, saved = build(tmp_path / "run", batcher)
        for _ in range(2):
            metrics.append(trainer.train_step().to_dict() | {"step_time_seconds": 0.0})  # type: ignore[union-attr]
        # Observe the speculative next update, then crash before it is committed.
        batcher.next_step_microbatches(BUDGET - trainer.committed_valid_targets)
        expected = batcher.pending_update
        assert expected is not None
    resumed = resume(tmp_path / "run", saved[-1], shards)
    try:
        assert resumed.batcher.get_state() == trainer.batcher.get_state()
        resumed.batcher.next_step_microbatches(BUDGET - resumed.committed_valid_targets)
        regenerated = resumed.batcher.pending_update  # type: ignore[attr-defined]
        assert regenerated.content_digest == expected.content_digest
        assert regenerated.end_state == expected.end_state
        resumed.batcher.rollback()
        run_to_end(resumed, metrics)
        assert (digest(resumed.model), metrics, resumed.batcher.get_state()) == reference
    finally:
        resumed.batcher.close()  # type: ignore[attr-defined]


def test_train_owns_producer_shutdown(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    reference: tuple[str, list[Any], dict[str, Any]],
) -> None:
    import psutil

    with prefetcher(shards) as batcher:
        trainer, _, _ = build(tmp_path / "run", batcher)
        pid = batcher.producer_pid
        assert pid is not None
        summary = trainer.train()
        assert summary.termination_reason == "completed"
        assert summary.committed_valid_targets == BUDGET
        assert digest(trainer.model) == reference[0]
        assert not psutil.pid_exists(pid)
