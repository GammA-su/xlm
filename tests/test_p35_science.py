"""P35 M1: versioned first-update LR, independent training RNG, scientific runtime identity.

Authored synthetic fixtures only. CPU tests drive the real Trainer and
CheckpointManager; ``cuda``-marked tests are bounded semantic checks on tiny
models, never training studies. LRs are captured at the moment
``optimizer.step`` is invoked, which is what AdamW reads.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import random
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import torch
import yaml
from pydantic import ValidationError

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.manifest import identity_digest
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import (
    AdamWConfig,
    CrossEntropyObjectiveConfig,
    ExecutableExperimentPlanConfig,
    ExperimentDraftConfig,
    TrainingConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.config.science import (
    ENDPOINT_LR_POLICY,
    LEGACY_LR_POLICY,
    LEGACY_RUNTIME_POLICY,
    SCIENCE_RUNTIME_POLICY,
    SCIENCE_V1,
    TRAINING_RNG_POLICY,
    ScientificPolicy,
    runtime_policy_for,
    seed_fields,
)
from xlm.core.contracts import TrainingBatch
from xlm.core.paths import ArtifactPaths
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import CheckpointManager, IncompatibleCheckpointError
from xlm.training.data import TrainingBatcher
from xlm.training.science import (
    ScientificRuntime,
    ScientificRuntimeError,
    ScientificState,
    rng_state_digest,
)
from xlm.training.trainer import NonFiniteGradientError, RecoveryRequiredError, Trainer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "p35_legacy_checkpoint"
CPU_RUNTIME = {
    "attention_policy": "strict_deterministic_v1",
    "matmul_tf32": "disabled",
    "bf16_reduced_precision_reduction": "allowed",
}
CUDA_STATISTICAL = {**CPU_RUNTIME, "attention_policy": "statistical_efficient_v1"}
# Actual 50M draft: base LR 1e-3, warmup 10M, horizon 1B, 65,536 targets/update.
REFERENCE: dict[str, Any] = {
    "context": 64,
    "global_batch": 65_536,
    "warmup": 10_000_000,
    "horizon": 1_000_000_000,
    "base_lr": 0.001,
}
needs_cuda = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA hardware required")


def legacy_fixture() -> ModuleType:
    """The pre-migration generator: reused for its legacy builders and helpers."""
    spec = importlib.util.spec_from_file_location("p35_legacy_generate", FIXTURE / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEGACY = legacy_fixture()
capture_step_lrs: Callable[[Trainer], list[list[float]]] = LEGACY.capture_step_lrs
model_digest: Callable[[torch.nn.Module], str] = LEGACY.model_digest


def science(seed: int = 10001, runtime: dict[str, str] | None = None) -> ScientificState:
    return ScientificState(
        ScientificPolicy.from_training(
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "training_seed": seed,
                "runtime": runtime or CPU_RUNTIME,
            }
        )
    )


def make_trainer(
    root: Path,
    *,
    science_state: ScientificState | None = None,
    tokens: list[int] | None = None,
    context: int = 8,
    global_batch: int = 16,
    budget: int = 64,
    warmup: int = 32,
    horizon: int = 64,
    base_lr: float = 1e-3,
    device: str = "cpu",
    backend: str = "eager",
    precision: str = "fp32",
    dropout: float = 0.0,
    group_setup: Callable[[Any, Any], None] | None = None,
) -> Trainer:
    paths = ArtifactPaths(root=root)
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    config = TransformerBaselineConfig(
        vocab_size=64,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=context,
        attention_backend=backend,
        dropout=dropout,
    )
    model = TransformerBaseline(config, seed=7).to(device)
    optimizer, manifest = create_adamw_optimizer(
        AdamWConfig(lr=base_lr, weight_decay=0.0), model=model
    )
    if group_setup is not None:
        group_setup(optimizer, manifest)
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(
            warmup_valid_targets=warmup, horizon_valid_targets=horizon, min_lr_ratio=0.1
        ),
        base_lr=base_lr,
    )
    if tokens is None:
        tokens = [4 + (i * 7) % 60 for i in range(budget + 2 * (budget // context) + 64)]
    return Trainer(
        model=model,
        objective=CrossEntropyObjective(CrossEntropyObjectiveConfig()),
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=TrainingBatcher(
            tokens, context_length=context, global_batch_valid_targets=global_batch
        ),
        checkpoint_manager=manager,
        run_id="p35_run",
        plan_id="p35_plan",
        device=device,
        precision=precision,
        max_valid_targets=budget,
        science=science_state,
    )


def reload(trainer: Trainer, checkpoint: Path, **kwargs: Any) -> Any:
    meta = trainer.checkpoint_manager.load_checkpoint(
        checkpoint,
        model=trainer.model,
        objective=trainer.objective,
        optimizer=trainer.optimizer,
        optimizer_manifest=trainer.optimizer_manifest,
        schedule=trainer.schedule,
        batcher=trainer.batcher,
        device=trainer.device,
        **kwargs,
    )
    trainer.step = meta.step
    trainer.committed_valid_targets = meta.committed_valid_targets
    trainer.processed_valid_targets = meta.processed_valid_targets
    return meta


def reference_tokens(targets: int) -> list[int]:
    return [4 + (i * 7) % 60 for i in range(targets + targets // 64 + 64)]


def largest_update(trainer: Trainer, before: dict[str, torch.Tensor]) -> float:
    return max(
        float((p.detach().double() - before[n].double()).abs().max())
        for n, p in trainer.model.named_parameters()
    )


def snapshot(trainer: Trainer) -> dict[str, torch.Tensor]:
    return {n: p.detach().clone() for n, p in trainer.model.named_parameters()}


# --------------------------------------------------------------------------
# LR policy at optimizer.step
# --------------------------------------------------------------------------


def test_legacy_step_sees_base_lr_then_postcommit_schedule(tmp_path: Path) -> None:
    trainer = make_trainer(tmp_path, tokens=reference_tokens(131_072), budget=131_072, **REFERENCE)
    captured = capture_step_lrs(trainer)
    before = snapshot(trainer)
    first = trainer.train_step()
    # AdamW's first step moves each coordinate by ~lr (m_hat/sqrt(v_hat) = sign(g)).
    assert largest_update(trainer, before) == pytest.approx(0.001, rel=2e-2)
    second = trainer.train_step()
    assert first is not None and second is not None
    assert captured == [[0.001, 0.001], [trainer.schedule.get_lr(65_536)] * 2]
    assert captured[1][0] == pytest.approx(0.0000065536, rel=1e-12)
    assert first.lr_used == (0.001, 0.001) and first.lr_policy == LEGACY_LR_POLICY
    # Historical meaning preserved: `learning_rate` is the next, post-commit LR.
    assert first.learning_rate == first.lr_next == pytest.approx(0.0000065536, rel=1e-12)
    assert first.lr_schedule_counter is None
    assert second.learning_rate == pytest.approx(0.0000131072, rel=1e-12)
    assert trainer.science.lr_receipts == []
    assert not (trainer._save_checkpoint("legacy") / "science.json").exists()


@pytest.mark.parametrize(
    "device", ["cpu", pytest.param("cuda", marks=[pytest.mark.cuda, needs_cuda])]
)
def test_endpoint_step_sees_target_endpoint_lr(tmp_path: Path, device: str) -> None:
    trainer = make_trainer(
        tmp_path,
        science_state=science(runtime=CPU_RUNTIME if device == "cpu" else CUDA_STATISTICAL),
        tokens=reference_tokens(131_072),
        budget=131_072,
        device=device,
        backend="eager" if device == "cpu" else "sdpa",
        precision="fp32" if device == "cpu" else "bf16_fp32_master",
        **REFERENCE,
    )
    captured = capture_step_lrs(trainer)
    before = snapshot(trainer)
    first = trainer.train_step()
    if device == "cpu":
        assert largest_update(trainer, before) == pytest.approx(0.0000065536, rel=2e-2)
    second = trainer.train_step()
    assert first is not None and second is not None
    lr1, lr2 = trainer.schedule.get_lr(65_536), trainer.schedule.get_lr(131_072)
    assert captured == [[lr1] * 2, [lr2] * 2]
    assert lr1 == pytest.approx(0.0000065536, rel=1e-12)
    assert lr2 == pytest.approx(0.0000131072, rel=1e-12)
    assert (first.lr_used, first.lr_schedule_counter) == ((lr1,) * 2, 65_536)
    assert (second.lr_used, second.lr_schedule_counter) == ((lr2,) * 2, 131_072)
    assert first.learning_rate == first.lr_used[0] and first.lr_next is None
    assert first.lr_policy == ENDPOINT_LR_POLICY
    assert trainer.science.lr_receipts == [
        [1, 0, 65_536, 65_536, [lr1] * 2],
        [2, 65_536, 65_536, 131_072, [lr2] * 2],
    ]


def test_endpoint_uses_actual_partial_valid_targets(tmp_path: Path) -> None:
    budget = 65_536 + 1_000
    trainer = make_trainer(
        tmp_path,
        science_state=science(),
        tokens=reference_tokens(budget),
        budget=budget,
        **REFERENCE,
    )
    captured = capture_step_lrs(trainer)
    trainer.train_step()
    final = trainer.train_step()
    assert final is not None and final.valid_targets == 1_000
    assert captured[1] == [trainer.schedule.get_lr(66_536)] * 2
    assert captured[1][0] == pytest.approx(0.001 * 66_536 / 10_000_000, rel=1e-12)
    assert trainer.science.lr_receipts[1][:4] == [2, 65_536, 1_000, 66_536]
    assert trainer.train_step() is None and trainer.committed_valid_targets == budget


@pytest.mark.parametrize(
    "warmup,horizon,budget,expected",
    [
        # Warmup crossing: 16 in warmup, 32 and 48 past W=24 in the cosine region.
        (24, 64, 48, [1e-3 * 16 / 24, None, None]),
        # Exact warmup endpoint: f(W) is the full base LR.
        (32, 64, 48, [5e-4, 1e-3, None]),
        # Exact horizon: f(H) is exactly the minimum LR.
        (16, 48, 48, [1e-3, None, 1e-4]),
        # No warmup: the first update already decays by cosine.
        (0, 64, 32, [1e-4 + 9e-4 * 0.5 * (1 + math.cos(math.pi * 16 / 64)), None]),
        # Final partial update: 16, 16 and the remaining 8 targets.
        (32, 64, 40, [5e-4, 1e-3, None]),
    ],
    ids=["warmup_crossing", "warmup_endpoint", "horizon", "no_warmup", "final_partial"],
)
def test_endpoint_boundaries_use_existing_schedule_function(
    tmp_path: Path, warmup: int, horizon: int, budget: int, expected: list[float | None]
) -> None:
    trainer = make_trainer(
        tmp_path, science_state=science(), budget=budget, warmup=warmup, horizon=horizon
    )
    captured = capture_step_lrs(trainer)
    while trainer.train_step() is not None:
        pass
    endpoints = [row[3] for row in trainer.science.lr_receipts]
    assert endpoints == [min(16 * k, budget) for k in range(1, len(endpoints) + 1)]
    assert endpoints[-1] == budget
    for index, endpoint in enumerate(endpoints):
        assert captured[index] == [trainer.schedule.get_lr(endpoint)] * 2
        if index < len(expected) and expected[index] is not None:
            assert captured[index][0] == pytest.approx(expected[index], rel=1e-12)


class ZeroValidBatcher:
    """One update whose every target is masked; counts protocol calls."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[TrainingBatch]:
        ids = torch.full((2, 8), 5, dtype=torch.long)
        return [
            TrainingBatch(
                input_ids=ids,
                labels=ids.clone(),
                loss_mask=torch.zeros(2, 8, dtype=torch.bool),
                metadata={"valid_target_count": 0},
            )
        ]

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def get_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, state: dict[str, Any]) -> None:
        raise AssertionError("not used")


@pytest.mark.parametrize("scientific", [False, True], ids=["legacy", "science_v1"])
def test_zero_valid_update_does_not_step_schedule_or_count(
    tmp_path: Path, scientific: bool
) -> None:
    trainer = make_trainer(tmp_path, science_state=science() if scientific else None)
    batcher = ZeroValidBatcher()
    trainer.batcher = batcher
    captured = capture_step_lrs(trainer)
    lrs = [g["lr"] for g in trainer.optimizer.param_groups]
    assert trainer.train_step() is None
    assert captured == [] and not trainer.optimizer.state
    assert [g["lr"] for g in trainer.optimizer.param_groups] == lrs
    assert trainer.step == trainer.committed_valid_targets == trainer.processed_valid_targets == 0
    assert trainer.science.lr_receipts == [] and not trainer._update_in_doubt
    assert (batcher.commits, batcher.rollbacks) == (1, 0)


def test_endpoint_preserves_group_multipliers_and_refuses_ambiguous_groups(
    tmp_path: Path,
) -> None:
    def halve_second(optimizer: Any, manifest: Any) -> None:
        optimizer.param_groups[1]["lr_multiplier"] = 0.5

    trainer = make_trainer(tmp_path / "a", science_state=science(), group_setup=halve_second)
    captured = capture_step_lrs(trainer)
    metrics = trainer.train_step()
    rate = trainer.schedule.get_lr(16)
    assert captured == [[rate, 0.5 * rate]]
    assert metrics is not None and metrics.lr_used == (rate, 0.5 * rate)
    assert trainer.science.lr_receipts[0][4] == [rate, 0.5 * rate]

    def distinct_without_multiplier(optimizer: Any, manifest: Any) -> None:
        # A plugin optimizer that built a distinct group rate but no multiplier.
        optimizer.param_groups[1]["lr"] = manifest.groups[1]["lr"] = 5e-4

    with pytest.raises(ValueError, match="endpoint rate is ambiguous"):
        make_trainer(
            tmp_path / "b", science_state=science(), group_setup=distinct_without_multiplier
        )
    # Legacy semantics are historical and unchanged for the same optimizer.
    legacy = make_trainer(tmp_path / "c", group_setup=distinct_without_multiplier)
    assert capture_step_lrs(legacy) == [] and legacy.train_step() is not None


# --------------------------------------------------------------------------
# Failure and in-doubt semantics
# --------------------------------------------------------------------------


class Injected(RuntimeError):
    pass


@pytest.mark.parametrize("stage", ["pre_step", "optimizer", "schedule", "counter", "data_commit"])
def test_failed_update_never_publishes_committed_lr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    trainer = make_trainer(tmp_path / "run", science_state=science(seed=3))
    trainer.train_step()
    good = trainer._save_checkpoint("good")
    receipts = copy.deepcopy(trainer.science.lr_receipts)
    lrs = [g["lr"] for g in trainer.optimizer.param_groups]
    cursor = trainer.batcher.get_state()
    counters = (trainer.step, trainer.committed_valid_targets, trainer.processed_valid_targets)
    expected: type[BaseException] = Injected
    handle = None
    if stage == "pre_step":
        expected = NonFiniteGradientError
        parameter = list(trainer.model.parameters())[-1]
        handle = parameter.register_hook(  # type: ignore[no-untyped-call]
            lambda grad: grad * float("nan")
        )
    elif stage == "optimizer":
        original = trainer.optimizer.step

        def mutate_then_fail(*args: Any, **kwargs: Any) -> Any:
            original(*args, **kwargs)
            raise Injected("optimizer")

        monkeypatch.setattr(trainer.optimizer, "step", mutate_then_fail)
    elif stage == "schedule":
        calls = {"n": 0}
        get_lr = trainer.schedule.get_lr

        def fail_after_mutation(counter: int) -> float:
            calls["n"] += 1
            if calls["n"] == 2:  # the post-barrier schedule promotion
                raise Injected("schedule")
            return get_lr(counter)

        monkeypatch.setattr(trainer.schedule, "get_lr", fail_after_mutation)
    elif stage == "counter":

        class FailedCounter(int):
            def __add__(self, other: Any) -> Any:
                raise Injected("counter")

        trainer.processed_valid_targets = FailedCounter(trainer.processed_valid_targets)
    else:

        def failed_commit() -> None:
            raise Injected("data_commit")

        monkeypatch.setattr(trainer.batcher, "commit", failed_commit)
    with pytest.raises(expected):
        trainer.train_step()
    if handle is not None:
        handle.remove()
    assert trainer.science.lr_receipts == receipts
    if stage == "pre_step":
        # Safe rollback: nothing mutated, the same update retries in process.
        assert not trainer._update_in_doubt
        assert [g["lr"] for g in trainer.optimizer.param_groups] == lrs
        assert trainer.batcher.get_state() == cursor
        assert (trainer.step, trainer.committed_valid_targets) == counters[:2]
        captured = capture_step_lrs(trainer)
        assert trainer.train_step() is not None
        assert captured == [[trainer.schedule.get_lr(32)] * 2]
        return
    # Mutation may have happened: no retry, no checkpoint, fresh durable reload.
    assert trainer._update_in_doubt
    checkpoints = sorted(p.name for p in trainer.checkpoint_manager.paths.checkpoints.iterdir())
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()
    with pytest.raises(RecoveryRequiredError):
        trainer._save_checkpoint("ambiguous")
    with pytest.raises(RecoveryRequiredError):
        trainer.checkpoint_manager.save_checkpoint(
            "ambiguous", "p35_run", 0, 0, 0, "p35_plan", trainer.model, None, None, None, None, None
        )
    assert sorted(p.name for p in trainer.checkpoint_manager.paths.checkpoints.iterdir()) == (
        checkpoints
    )
    recovered = make_trainer(tmp_path / "recovered", science_state=science(seed=3))
    reload(recovered, good, science=recovered.science)
    assert recovered.science.lr_receipts == receipts
    captured = capture_step_lrs(recovered)
    assert recovered.train_step() is not None
    assert captured == [[recovered.schedule.get_lr(32)] * 2]
    assert [row[3] for row in recovered.science.lr_receipts] == [16, 32]


@pytest.mark.cuda
@needs_cuda
def test_cuda_barrier_failure_publishes_no_lr_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = make_trainer(
        tmp_path,
        science_state=science(runtime=CUDA_STATISTICAL),
        device="cuda",
        backend="sdpa",
        precision="bf16_fp32_master",
    )
    assert trainer.train_step() is not None
    receipts = copy.deepcopy(trainer.science.lr_receipts)
    before = trainer.batcher.get_state()

    class FailedStream:
        def synchronize(self) -> None:
            raise RuntimeError("authored asynchronous optimizer failure")

    with monkeypatch.context() as patch:
        patch.setattr(torch.cuda, "current_stream", lambda device: FailedStream())
        with pytest.raises(RuntimeError, match="asynchronous optimizer"):
            trainer.train_step()
    torch.cuda.synchronize()
    assert trainer._update_in_doubt and trainer.committed_valid_targets == 16
    assert trainer.batcher.get_state() == before
    assert trainer.science.lr_receipts == receipts
    with pytest.raises(RecoveryRequiredError):
        trainer._save_checkpoint("ambiguous")


# --------------------------------------------------------------------------
# Resume, round trip and historical compatibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint_after", [1, 2])
def test_endpoint_resume_continues_exactly(tmp_path: Path, checkpoint_after: int) -> None:
    settings: dict[str, Any] = {"budget": 40, "warmup": 24, "horizon": 64}
    reference = make_trainer(tmp_path / "ref", science_state=science(seed=5), **settings)
    reference_lrs = capture_step_lrs(reference)
    while reference.train_step() is not None:
        pass
    interrupted = make_trainer(tmp_path / "run", science_state=science(seed=5), **settings)
    for _ in range(checkpoint_after):
        interrupted.train_step()
    checkpoint = interrupted._save_checkpoint("partway")
    del interrupted
    resumed = make_trainer(tmp_path / "resumed", science_state=science(seed=5), **settings)
    reload(resumed, checkpoint, science=resumed.science)
    resumed_lrs = capture_step_lrs(resumed)
    while resumed.train_step() is not None:
        pass
    assert resumed_lrs == reference_lrs[checkpoint_after:]
    assert resumed.science.lr_receipts == reference.science.lr_receipts
    assert [row[2] for row in resumed.science.lr_receipts] == [16, 16, 8]
    assert model_digest(resumed.model) == model_digest(reference.model)
    assert resumed.batcher.get_state() == reference.batcher.get_state()
    for key, state in reference.optimizer.state_dict()["state"].items():
        for name, value in state.items():
            assert torch.equal(resumed.optimizer.state_dict()["state"][key][name], value)


def test_science_state_round_trips_and_policy_change_refuses_resume(tmp_path: Path) -> None:
    trainer = make_trainer(tmp_path / "run", science_state=science(seed=1))
    trainer.train_step()
    trainer.train_step()
    checkpoint = trainer._save_checkpoint("science")
    saved = json.loads((checkpoint / "science.json").read_text(encoding="utf-8"))
    assert saved["policy"] == trainer.science.policy.identity()
    assert saved["lr_receipts"]["rows"] == trainer.science.lr_receipts
    assert saved["train_start_rng"] == trainer.science.train_start_rng
    changed = {
        "legacy": None,
        "training_seed": science(seed=2),
        "tf32": science(seed=1, runtime={**CPU_RUNTIME, "matmul_tf32": "enabled"}),
    }
    for name, state in changed.items():
        other = make_trainer(tmp_path / name, science_state=state)
        before = model_digest(other.model)
        with pytest.raises(IncompatibleCheckpointError, match="scientific policy changed"):
            reload(other, checkpoint, science=state)
        assert model_digest(other.model) == before  # refused before any restore
    forked = make_trainer(tmp_path / "fork", science_state=science(seed=2))
    reload(forked, checkpoint, science=forked.science, is_fork=True)
    assert forked.science.lr_receipts == []  # a different policy does not adopt history
    same = make_trainer(tmp_path / "same", science_state=science(seed=1))
    reload(same, checkpoint, science=same.science)
    assert same.science.lr_receipts == trainer.science.lr_receipts
    assert len(same.science.runtime_receipts) == 2  # both attempts' observations


def test_schema_v1_material_resolves_legacy_with_historical_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.experiments.execution import resolve_execution_config

    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    resolved, _ = resolve_execution_config(copy.deepcopy(LEGACY.LEGACY_CONFIG))
    # Digest recorded by the unmodified 991dd39 code: identity is not rewritten.
    assert identity_digest(resolved) == expected["legacy_resolved_config_digest"]
    assert sorted(resolved["training"]) == expected["legacy_resolved_training_keys"]
    assert ScientificPolicy.from_training(resolved["training"]) == ScientificPolicy.legacy()
    assert runtime_policy_for(resolved, "training") == LEGACY_RUNTIME_POLICY
    assert seed_fields(resolved["training"]) == {"init_seed": 7, "data_seed": 42}
    assert resolve_execution_config(copy.deepcopy(resolved))[0] == resolved
    # No checked-in historical recipe moved to science-v1.
    for path in sorted((ROOT / "recipes" / "experiments").glob("*.yaml")):
        draft = yaml.safe_load(path.read_text(encoding="utf-8"))
        policy = ScientificPolicy.from_training(draft["training"])
        assert policy.is_science == path.name.startswith("draft_science_v1_"), path.name


def test_pre_migration_checkpoint_continues_with_historical_semantics(tmp_path: Path) -> None:
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    checkpoint = tmp_path / "legacy"
    shutil.copytree(FIXTURE / "checkpoint", checkpoint)
    assert not (checkpoint / "science.json").exists()
    trainer = LEGACY.build(tmp_path / "run")
    reload(trainer, checkpoint, expected_plan_id="p35_legacy_plan")
    assert [g["lr"] for g in trainer.optimizer.param_groups] == expected["saved_group_lrs"]
    captured = capture_step_lrs(trainer)
    metrics = trainer.train_step()
    assert metrics is not None and metrics.lr_policy == LEGACY_LR_POLICY
    assert captured == [expected["update2_lr_used"]]
    # Bit-identical to the original code continuing the same checkpoint.
    assert model_digest(trainer.model) == expected["update2_model_sha256"]
    science_run = LEGACY.build(tmp_path / "science", science=science(runtime=CPU_RUNTIME))
    with pytest.raises(IncompatibleCheckpointError, match="scientific policy changed"):
        reload(science_run, checkpoint, science=science_run.science)
    reload(science_run, checkpoint, science=science_run.science, is_fork=True)


# --------------------------------------------------------------------------
# Independent training RNG
# --------------------------------------------------------------------------


def rng_config(
    *, init_seed: int = 7, training_seed: int | None = 10001, dropout: float = 0.2
) -> dict[str, Any]:
    config: dict[str, Any] = copy.deepcopy(LEGACY.LEGACY_CONFIG)
    config["model"]["dropout"] = dropout
    training = config["training"]
    training["init_seed"] = init_seed
    training["budget"]["max_valid_targets"] = 32
    training["schedule"]["horizon_valid_targets"] = 32
    if training_seed is not None:
        training.update(
            science_version=SCIENCE_V1,
            lr_policy=ENDPOINT_LR_POLICY,
            training_seed=training_seed,
            runtime=CPU_RUNTIME,
        )
    return config


def build_run(config: dict[str, Any], root: Path, *, fresh: bool = True) -> Trainer:
    from xlm.experiments.execution import resolve_execution_config
    from xlm.training.components import construct_training_components

    resolved, _ = resolve_execution_config(copy.deepcopy(config))
    components = construct_training_components(resolved, device="cpu", fresh_training_rng=fresh)
    paths = ArtifactPaths(root=root)
    manager = CheckpointManager(
        artifact_store=ArtifactStore(paths),
        run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
        paths=paths,
    )
    return Trainer(
        model=components.model,
        objective=components.objective,
        optimizer=components.optimizer,
        optimizer_manifest=components.optimizer_manifest,
        schedule=components.schedule,
        batcher=components.batcher,
        checkpoint_manager=manager,
        run_id="rng_run",
        plan_id="rng_plan",
        max_valid_targets=resolved["training"]["budget"]["max_valid_targets"],
        science=components.science,
    )


@pytest.fixture
def xlm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return tmp_path


def weights(trainer: Trainer) -> dict[str, torch.Tensor]:
    return {k: v.clone() for k, v in trainer.model.state_dict().items()}


def same_weights(left: dict[str, torch.Tensor], right: dict[str, torch.Tensor]) -> bool:
    return left.keys() == right.keys() and all(torch.equal(left[k], right[k]) for k in left)


def test_training_seed_varies_training_but_not_initialization(xlm_home: Path) -> None:
    runs = {
        seed: build_run(rng_config(training_seed=seed), xlm_home / str(seed)) for seed in (1, 2)
    }
    initial = {seed: weights(run) for seed, run in runs.items()}
    assert same_weights(initial[1], initial[2])
    receipts = {seed: run.science.train_start_rng for seed, run in runs.items()}
    assert receipts[1] is not None and receipts[1]["policy"] == TRAINING_RNG_POLICY
    assert receipts[1]["state_digest"] != receipts[2]["state_digest"]  # type: ignore[index]
    trained = {}
    for seed in (1, 2):
        # Rebuild so each run trains from its own post-construction reseed.
        run = build_run(rng_config(training_seed=seed), xlm_home / f"train{seed}")
        run.train_step()
        trained[seed] = weights(run)
    assert not same_weights(trained[1], trained[2])  # dropout masks differ


def test_init_seed_varies_weights_but_not_training_rng(xlm_home: Path) -> None:
    left = build_run(rng_config(init_seed=101), xlm_home / "a")
    right = build_run(rng_config(init_seed=211), xlm_home / "b")
    assert not same_weights(weights(left), weights(right))
    assert left.science.train_start_rng == right.science.train_start_rng


def test_constructor_rng_consumption_changes_legacy_but_not_science_training_rng(
    xlm_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xlm.objectives.cross_entropy as cross_entropy

    plain = cross_entropy.create_cross_entropy_objective

    def consuming(config: Any) -> Any:
        random.random()
        np.random.rand(3)
        torch.rand(5)
        return plain(config)

    observed: dict[tuple[str, bool], Any] = {}
    for consumes in (False, True):
        if consumes:
            monkeypatch.setattr(cross_entropy, "create_cross_entropy_objective", consuming)
        legacy = build_run(rng_config(training_seed=None), xlm_home / f"legacy{consumes}")
        observed["legacy", consumes] = rng_state_digest("cpu")
        run = build_run(rng_config(), xlm_home / f"science{consumes}")
        observed["science", consumes] = run.science.train_start_rng
        run.train_step()
        observed["trained", consumes] = weights(run)
        assert legacy.science.train_start_rng is None
    assert observed["legacy", False] != observed["legacy", True]
    assert observed["science", False] == observed["science", True]
    assert same_weights(observed["trained", False], observed["trained", True])


def test_legacy_rng_remains_coupled_to_init_seed_and_construction(
    xlm_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xlm.training.science as runtime
    from xlm.models.transformer import create_transformer_baseline

    monkeypatch.setattr(
        runtime, "reseed_training_rng", lambda *a, **k: pytest.fail("legacy run was reseeded")
    )
    config = rng_config(training_seed=None, dropout=0.0)
    build_run(config, xlm_home / "legacy")
    after_components = rng_state_digest("cpu")
    random.seed(7)
    torch.manual_seed(7)
    np.random.seed(7)
    create_transformer_baseline(TransformerBaselineConfig(**config["model"]))
    CrossEntropyObjective(CrossEntropyObjectiveConfig())
    assert rng_state_digest("cpu") == after_components


def test_resume_restores_training_rng_without_reseeding(
    xlm_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xlm.training.science as runtime

    reference = build_run(rng_config(), xlm_home / "reference")
    while reference.train_step() is not None:
        pass
    run = build_run(rng_config(), xlm_home / "run")
    run.train_step()
    checkpoint = run._save_checkpoint("rng")
    saved = (random.getstate(), np.random.get_state(), torch.get_rng_state().clone())
    start = run.science.train_start_rng
    del run
    # Destroy the process-level stream the checkpoint must replace.
    random.seed(999)
    np.random.seed(999)
    torch.manual_seed(999)
    calls: list[Any] = []
    monkeypatch.setattr(runtime, "reseed_training_rng", lambda *a, **k: calls.append(a))
    resumed = build_run(rng_config(), xlm_home / "resumed", fresh=False)
    reload(resumed, checkpoint, science=resumed.science)
    assert random.getstate() == saved[0]
    restored_numpy = np.random.get_state()
    assert restored_numpy[0] == saved[1][0] and np.array_equal(restored_numpy[1], saved[1][1])
    assert restored_numpy[2:] == saved[1][2:]
    assert torch.equal(torch.get_rng_state(), saved[2])
    while resumed.train_step() is not None:
        pass
    assert calls == []
    assert resumed.science.train_start_rng == start
    assert same_weights(weights(resumed), weights(reference))
    assert resumed.science.lr_receipts == reference.science.lr_receipts


@pytest.mark.cuda
@needs_cuda
def test_cuda_training_rng_resumes_rather_than_restarts(tmp_path: Path) -> None:
    from xlm.training.science import reseed_training_rng

    def cuda_run(root: Path) -> Trainer:
        return make_trainer(
            root,
            science_state=science(runtime=CUDA_STATISTICAL),
            device="cuda",
            backend="sdpa",
            precision="bf16_fp32_master",
            dropout=0.2,
            budget=48,
        )

    reference = cuda_run(tmp_path / "ref")
    reseed_training_rng(10001, "cuda")
    reference.train_step()
    reference_cuda = torch.cuda.get_rng_state().clone()
    checkpoint = reference._save_checkpoint("cuda_rng")
    continued = reference.train_step()
    torch.cuda.manual_seed_all(999)
    torch.manual_seed(999)
    resumed = cuda_run(tmp_path / "resumed")
    reload(resumed, checkpoint, science=resumed.science)
    assert torch.equal(torch.cuda.get_rng_state(), reference_cuda)
    # The same dropout stream continues: the next update's forward loss matches
    # exactly (weights restored bitwise; forward attention is deterministic).
    resumed_metrics = resumed.train_step()
    assert continued is not None and resumed_metrics is not None
    assert resumed_metrics.loss == continued.loss


# --------------------------------------------------------------------------
# Scientific runtime identity and scoped runtime
# --------------------------------------------------------------------------


def science_training(**changes: Any) -> dict[str, Any]:
    config = rng_config()
    config["model"]["attention_backend"] = "sdpa"
    config["training"].update(device="cuda", precision="bf16_fp32_master", runtime=CUDA_STATISTICAL)
    for key, value in changes.items():
        if key == "runtime":
            config["training"]["runtime"] = {**CUDA_STATISTICAL, **value}
        else:
            config["training"][key] = value
    return config


def test_scientific_policies_change_the_resolved_behavior_identity(xlm_home: Path) -> None:
    from xlm.experiments.execution import resolve_execution_config

    variants = {
        "science_v1": science_training(),
        "training_seed": science_training(training_seed=10002),
        "strict_attention": science_training(
            runtime={"attention_policy": "strict_deterministic_v1"}
        ),
        "tf32": science_training(runtime={"matmul_tf32": "enabled"}),
        "bf16_reduction": science_training(
            runtime={"bf16_reduced_precision_reduction": "disallowed"}
        ),
        "precision": science_training(precision="fp32"),
        "producer": science_training(producer_prefetch="process_depth1"),
        "init_seed": science_training(init_seed=211),
    }
    legacy = rng_config(training_seed=None)
    legacy["model"]["attention_backend"] = "sdpa"
    legacy["training"].update(device="cuda", precision="bf16_fp32_master")
    variants["legacy_lr_policy"] = legacy
    digests = {}
    for name, config in variants.items():
        resolved, _ = resolve_execution_config(config)
        assert resolve_execution_config(copy.deepcopy(resolved))[0] == resolved  # resume path
        digests[name] = identity_digest(resolved)
        expected_policy = (
            LEGACY_RUNTIME_POLICY if name == "legacy_lr_policy" else SCIENCE_RUNTIME_POLICY
        )
        assert runtime_policy_for(resolved, "training") == expected_policy
    assert len(set(digests.values())) == len(digests), digests
    science_seeds = seed_fields(resolve_execution_config(variants["science_v1"])[0]["training"])
    assert science_seeds == {"init_seed": 7, "data_seed": 42, "training_seed": 10001}


def test_science_envelope_binds_its_runtime_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_frozen_execution import authored_tree
    from xlm.experiments.execution import make_envelope, validate_envelope
    from xlm.experiments.snapshot import capture_snapshot

    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    tree = authored_tree(tmp_path / "tree")
    snapshot = tmp_path / "snapshot"
    capture_snapshot(tree, snapshot, max_snapshot_bytes=16 * 1024**2)
    envelope = make_envelope(science_training(), snapshot, extras=["cuda"])
    assert envelope["runtime_policy"] == SCIENCE_RUNTIME_POLICY
    assert envelope["config"]["training"]["lr_policy"] == ENDPOINT_LR_POLICY
    validate_envelope(envelope, snapshot, check_environment=False)
    forged = {**envelope, "runtime_policy": dict(LEGACY_RUNTIME_POLICY)}
    forged["execution_hash"] = identity_digest(
        {k: v for k, v in forged.items() if k != "execution_hash"}
    )
    with pytest.raises(ValueError, match="unsupported frozen runtime policy"):
        validate_envelope(forged, snapshot, check_environment=False)


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"training_seed": 5}, "require science_version"),
        ({"science_version": SCIENCE_V1, "training_seed": 5, "runtime": CPU_RUNTIME}, "lr_policy"),
        (
            {
                "science_version": SCIENCE_V1,
                "lr_policy": LEGACY_LR_POLICY,
                "training_seed": 5,
                "runtime": CPU_RUNTIME,
            },
            "lr_policy",
        ),
        (
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "runtime": CPU_RUNTIME,
            },
            "training_seed",
        ),
        (
            {"science_version": SCIENCE_V1, "lr_policy": ENDPOINT_LR_POLICY, "training_seed": 5},
            "runtime",
        ),
        ({"lr_policy": "first_step_whatever_v9"}, "lr_policy"),
        ({"science_version": "xlm-science-v2"}, "science_version"),
        (
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "training_seed": 5,
                "runtime": {"attention_policy": "strict_deterministic_v1"},
            },
            "matmul_tf32",
        ),
        (
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "training_seed": 5,
                "runtime": {**CPU_RUNTIME, "attention_policy": "fastest_available"},
            },
            "attention_policy",
        ),
        (
            {
                "science_version": SCIENCE_V1,
                "lr_policy": ENDPOINT_LR_POLICY,
                "training_seed": 5,
                "runtime": CUDA_STATISTICAL,
                "device": "cpu",
            },
            "requires a CUDA device",
        ),
    ],
)
def test_unknown_or_incomplete_scientific_policy_fails_early(
    patch: dict[str, Any], message: str
) -> None:
    training = copy.deepcopy(LEGACY.LEGACY_CONFIG["training"])
    training.update(patch)
    with pytest.raises(ValidationError, match=message):
        TrainingConfig.model_validate(training)


def test_statistical_policy_requires_the_efficient_sdpa_route(xlm_home: Path) -> None:
    from xlm.experiments.execution import resolve_execution_config

    config = science_training()
    config["model"]["attention_backend"] = "eager"
    with pytest.raises(ValueError, match="requires attention_backend"):
        resolve_execution_config(config)


def test_runtime_scope_sets_and_restores_process_flags() -> None:
    matmul = torch.backends.cuda.matmul
    prior = ScientificRuntime.observed_flags()
    runtime = ScientificRuntime(
        {
            "attention_policy": "strict_deterministic_v1",
            "matmul_tf32": "enabled",
            "bf16_reduced_precision_reduction": "disallowed",
        },
        "cpu",
    )
    with pytest.raises(Injected), runtime.scope():
        assert torch.are_deterministic_algorithms_enabled()
        assert not torch.is_deterministic_algorithms_warn_only_enabled()
        assert matmul.allow_tf32 and not matmul.allow_bf16_reduced_precision_reduction
        raise Injected("inside scope")
    assert ScientificRuntime.observed_flags() == prior
    with ScientificRuntime(None, "cpu").scope():
        assert ScientificRuntime.observed_flags() == prior


def test_resolved_runtime_mismatch_fails_instead_of_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ScientificRuntime(CPU_RUNTIME, "cpu")
    real = ScientificRuntime.observed_flags

    def drifted() -> dict[str, Any]:
        return {**real(), "matmul_allow_tf32": True}

    monkeypatch.setattr(ScientificRuntime, "observed_flags", staticmethod(drifted))
    before = real()
    with pytest.raises(ScientificRuntimeError, match="differ from requested"), runtime.scope():
        pass
    assert real() == before


def test_cpu_strict_sdpa_receipt_records_the_observed_operator(tmp_path: Path) -> None:
    trainer = make_trainer(tmp_path, science_state=science(), backend="sdpa")
    receipt = trainer.execution_report()["runtime_receipt"]
    assert receipt["sdpa_restriction"] == ["MATH"]
    assert "aten::_scaled_dot_product_attention_math" in receipt["observed_ops"]
    assert receipt["flags"]["deterministic_algorithms"] is True
    assert "not a whole-model determinism certificate" in receipt["scope"]
    assert trainer.execution_report()["scientific_policy"]["lr_policy"] == ENDPOINT_LR_POLICY
    assert trainer.train_step() is not None
    assert not torch.are_deterministic_algorithms_enabled()  # restored after the update


@pytest.mark.cuda
@needs_cuda
def test_cuda_statistical_policy_observes_efficient_attention(tmp_path: Path) -> None:
    trainer = make_trainer(
        tmp_path,
        science_state=science(runtime=CUDA_STATISTICAL),
        device="cuda",
        backend="sdpa",
        precision="bf16_fp32_master",
    )
    receipt = trainer.science.runtime_receipts[-1]
    assert receipt["sdpa_restriction"] == ["EFFICIENT_ATTENTION"]
    assert {"aten::_efficient_attention_forward", "aten::_efficient_attention_backward"} <= set(
        receipt["observed_ops"]
    )
    assert receipt["flags"]["deterministic_algorithms"] is False
    assert receipt["flags"]["matmul_allow_tf32"] is False
    assert receipt["probe"]["dtype"] == "bfloat16"
    assert trainer.train_step() is not None


@pytest.mark.cuda
@needs_cuda
def test_cuda_strict_policy_requires_deterministic_cublas_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUBLAS_WORKSPACE_CONFIG", raising=False)
    with pytest.raises(ScientificRuntimeError, match="CUBLAS_WORKSPACE_CONFIG"):
        make_trainer(
            tmp_path,
            science_state=science(
                runtime={**CUDA_STATISTICAL, "attention_policy": "strict_deterministic_v1"}
            ),
            device="cuda",
            backend="sdpa",
            precision="bf16_fp32_master",
        )


def strict_cuda_probe(root: Path) -> dict[str, Any]:
    """Run in a fresh process whose cuBLAS workspace is configured at start."""
    strict = {**CUDA_STATISTICAL, "attention_policy": "strict_deterministic_v1"}
    digests = []
    for attempt in range(2):
        trainer = make_trainer(
            root / str(attempt),
            science_state=science(runtime=strict),
            device="cuda",
            backend="sdpa",
            precision="bf16_fp32_master",
            dropout=0.1,
        )
        from xlm.training.science import reseed_training_rng

        reseed_training_rng(10001, "cuda")
        while trainer.train_step() is not None:
            pass
        digests.append(model_digest(trainer.model))
    values = torch.arange(8, dtype=torch.float32, device="cuda")
    with trainer.runtime.scope():
        try:
            torch.histc(values, bins=4)
            refused = None
        except RuntimeError as exc:
            refused = str(exc)
    after = torch.are_deterministic_algorithms_enabled()
    torch.histc(values, bins=4)
    return {
        "digests": digests,
        "refused": refused,
        "deterministic_after_scope": after,
        "receipt": trainer.science.runtime_receipts[-1],
    }


@pytest.mark.cuda
@needs_cuda
def test_cuda_strict_policy_enforces_and_scopes_determinism(tmp_path: Path) -> None:
    script = (
        "import json, sys; from pathlib import Path; import test_p35_science as t; "
        "print(json.dumps(t.strict_cuda_probe(Path(sys.argv[1]))))"
    )
    env = {**os.environ, "CUBLAS_WORKSPACE_CONFIG": ":4096:8"}
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["digests"][0] == result["digests"][1]  # two strict runs, identical weights
    assert result["refused"] is not None and "deterministic" in result["refused"]
    assert result["deterministic_after_scope"] is False
    receipt = result["receipt"]
    assert receipt["flags"]["deterministic_algorithms"] is True
    assert receipt["cublas_workspace_config"] == ":4096:8"
    assert {"aten::_efficient_attention_forward", "aten::_efficient_attention_backward"} <= set(
        receipt["observed_ops"]
    )


# --------------------------------------------------------------------------
# Draft science-v1 recipe
# --------------------------------------------------------------------------


def test_science_v1_draft_is_explicit_and_not_executable() -> None:
    path = ROOT / "recipes" / "experiments" / "draft_science_v1_50m.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    draft = ExperimentDraftConfig.model_validate(data)
    assert draft.status == "draft_science_v1_not_executable"
    training = data["training"]
    assert training["lr_policy"] == ENDPOINT_LR_POLICY and training["training_seed"] == 10001
    assert training["runtime"]["attention_policy"] == "statistical_efficient_v1"
    assert ScientificPolicy.from_training(training).is_science
    with pytest.raises(ValidationError):
        ExecutableExperimentPlanConfig.model_validate(data)
