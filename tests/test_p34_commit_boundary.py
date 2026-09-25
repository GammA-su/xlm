from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_prefetch import GLOBAL, make_batcher, prefetcher, shards  # noqa: F401
from test_prefetch_training import build
from xlm.data.tokens import TokenShardReader
from xlm.training.trainer import RecoveryRequiredError


@pytest.mark.parametrize("stage", ["schedule", "counter", "commit_return", "checkpoint"])
def test_exact_optimizer_boundary_failures(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    with prefetcher(shards) as pre:
        trainer, _, _ = build(tmp_path / stage, pre)
        initial = pre.get_state()

        def fail(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(stage)

        if stage == "schedule":
            monkeypatch.setattr(trainer.schedule, "apply_lr_to_optimizer", fail)
        elif stage == "counter":

            class FailedCounter(int):
                def __add__(self, other: Any) -> Any:
                    fail()

            trainer.processed_valid_targets = FailedCounter(0)
        elif stage == "commit_return":
            commit = pre.commit

            def promote_then_fail() -> None:
                commit()
                fail()

            monkeypatch.setattr(pre, "commit", promote_then_fail)
        else:
            monkeypatch.setattr(trainer.checkpoint_manager, "save_checkpoint", fail)
        with pytest.raises(RuntimeError, match=stage):
            trainer.train_step()
        assert trainer._update_in_doubt is (stage != "checkpoint")
        if stage in ("schedule", "counter"):
            assert pre.get_state() == initial
        else:
            assert pre.get_state()["committed_valid_targets"] == GLOBAL
        if stage != "checkpoint":
            pre.load_state(initial)
            pre.close()
            with pytest.raises(RecoveryRequiredError):
                trainer.train_step()
            with pytest.raises(RecoveryRequiredError):
                trainer._save_checkpoint("invalid")
            trainer.max_valid_targets = trainer.committed_valid_targets
            trainer._stop_requested = True
            with pytest.raises(RecoveryRequiredError):
                trainer.train()


def test_checkpoint_manager_cannot_bypass_poisoned_trainer(
    shards: dict[str, TokenShardReader],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with make_batcher(shards) as batcher:
        trainer, manager, _ = build(tmp_path, batcher)

        def mutate_and_fail() -> None:
            trainer.optimizer.param_groups[0]["lr"] = 0.75
            raise RuntimeError("optimizer interrupted")

        monkeypatch.setattr(trainer.optimizer, "step", mutate_and_fail)
        with pytest.raises(RuntimeError, match="optimizer interrupted"):
            trainer.train_step()
        with pytest.raises(RecoveryRequiredError):
            manager.save_checkpoint(
                "invalid",
                trainer.run_id,
                trainer.step,
                trainer.committed_valid_targets,
                trainer.processed_valid_targets,
                trainer.plan_id,
                trainer.model,
                trainer.objective,
                trainer.optimizer,
                trainer.optimizer_manifest,
                trainer.schedule,
                trainer.batcher,
            )
