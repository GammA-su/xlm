"""P35 M2: evaluation cadence integrated with the real Trainer, checkpoints and resume.

Authored synthetic fixtures only; tiny models on CPU. Evaluators are either
real native LM validation over authored inventories or recording/failing/
mutating fixtures. None of this is a capability or quality measurement.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from p35_eval_support import (
    CPU_RUNTIME,
    FULL_DOCS,
    CallbackEvaluator,
    RecordingEvaluator,
    build_trainer,
    dev_inventories,
    events_by_id,
    fixture_plan,
    reload,
    run_all,
    science_json,
    write_inventory,
)
from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.science import ENDPOINT_LR_POLICY, SCIENCE_V1
from xlm.core.paths import ArtifactPaths
from xlm.evaluation.cadence import EventTier, build_plan
from xlm.evaluation.likelihood import ConditionalLikelihoodScorer
from xlm.evaluation.lm_validation import (
    PRIMARY_METRIC,
    LMScoringPolicy,
    LMValidationEvaluator,
    load_pinned_inventory,
)
from xlm.evaluation.outcome import EvaluationOutcome
from xlm.evaluation.receipts import AttemptOutcome
from xlm.evaluation.state_digest import model_state_digest, value_digest
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.checkpoint import CheckpointManager, IncompatibleCheckpointError
from xlm.training.evaluation import TrainingStateGuard
from xlm.training.science import rng_state_digest
from xlm.training.trainer import RecoveryRequiredError, Trainer, TrainerError

QUICK, FULL, SEARCH = EventTier.QUICK_LM, EventTier.FULL_LM, EventTier.SEARCH_BENCHMARK
ROOT = Path(__file__).resolve().parents[1]


def lm_evaluators(root: Path, *, context: int = 8, stride: int = 4) -> dict[EventTier, Any]:
    tokenizer = ByteTokenizer()
    inventories = dev_inventories(root, tokenizer)
    policy = LMScoringPolicy(context_length=context, rolling_stride=stride)
    return {
        tier: LMValidationEvaluator(
            tier,
            load_pinned_inventory(
                path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
            ),
            tokenizer,
            policy,
        )
        for tier, (path, manifest_id) in (
            (QUICK, inventories["quick"]),
            (FULL, inventories["full"]),
        )
    }


def receipts(trainer: Trainer) -> dict[str, Any]:
    assert trainer.evaluation is not None
    return trainer.evaluation.canonical_receipts()


def attempt_history(trainer: Trainer, event_id: str) -> list[dict[str, Any]]:
    assert trainer.evaluation is not None and trainer.evaluation._store is not None
    return trainer.evaluation._store.history(event_id)


def weights(trainer: Trainer) -> dict[str, torch.Tensor]:
    return {k: v.detach().clone() for k, v in trainer.model.state_dict().items()}


# ---------------------------------------------------------------------- cadence


def test_1m_threshold_fires_at_the_first_natural_boundary_1048576(tmp_path: Path) -> None:
    """Fifteen 65,536-target updates reach 983,040; the sixteenth commits 1,048,576."""
    recorders = {tier: RecordingEvaluator(tier, label=tier.value) for tier in (QUICK, FULL, SEARCH)}
    plan = build_plan("pilot_32m", 32_000_000, confirmation_registered=False)
    trainer = build_trainer(
        tmp_path,
        evaluators=recorders,
        plan=plan,
        budget=32_000_000,
        global_batch=65_536,
        context=64,
        tokens=[4 + (i * 7) % 250 for i in range(1_200_000)],
    )
    for _ in range(15):
        trainer.train_step()
    assert trainer.committed_valid_targets == 983_040
    assert [c["event_id"] for c in recorders[QUICK].calls] == ["quick_lm@0"]
    trainer.train_step()
    assert trainer.committed_valid_targets == 1_048_576
    assert [(c["event_id"], c["committed"]) for c in recorders[QUICK].calls] == [
        ("quick_lm@0", 0),
        ("quick_lm@1000000", 1_048_576),
    ]
    # The update was never shortened to land on the threshold.
    assert [row[2] for row in trainer.science.lr_receipts] == [65_536] * 16
    receipt = receipts(trainer)["quick_lm@1000000"]
    assert receipt["event"]["planned_threshold"] == 1_000_000
    assert receipt["actual_committed_targets"] == 1_048_576 and receipt["step"] == 16
    assert receipt["status"] == "complete"
    incomplete = trainer.evaluation.completeness()  # type: ignore[union-attr]
    assert not incomplete.complete
    assert "quick_lm@4000000: planned (threshold not reached)" in incomplete.reasons


def test_one_update_crossing_several_events_shares_scoring_but_not_receipts(
    tmp_path: Path,
) -> None:
    evaluators: dict[EventTier, Any] = lm_evaluators(tmp_path / "inputs")
    evaluators[SEARCH] = RecordingEvaluator(SEARCH)
    plan = fixture_plan(32, quick_lm=[8, 16], full_lm=[16], search_benchmark=[12])
    trainer = build_trainer(tmp_path, evaluators=evaluators, plan=plan, budget=32)
    trainer.train_step()  # one update: C 0 -> 16 crosses 8, 12 and 16
    ledger = trainer.evaluation.ledger  # type: ignore[union-attr]
    assert [r.event_id for r in ledger.ordered() if r.due] == [
        "quick_lm@8",
        "search_benchmark@12",
        "quick_lm@16",
        "full_lm@16",
    ]
    found = receipts(trainer)
    for event_id in ("quick_lm@8", "search_benchmark@12", "quick_lm@16", "full_lm@16"):
        assert found[event_id]["actual_committed_targets"] == 16
        assert found[event_id]["status"] == "complete"
    assert found["quick_lm@8"]["event"]["planned_threshold"] == 8
    assert found["quick_lm@16"]["event"]["planned_threshold"] == 16
    # Identical computations are scored once and receipted twice, labelled.
    assert found["quick_lm@16"]["shared_computation_with"] == "quick_lm@8"
    assert found["quick_lm@16"]["metrics"] == found["quick_lm@8"]["metrics"]
    assert found["quick_lm@16"]["receipt_identity"] != found["quick_lm@8"]["receipt_identity"]
    # Full and quick are different tiers: separate receipts, shared document scores.
    assert found["full_lm@16"]["shared_computation_with"] is None
    assert found["full_lm@16"]["coverage"]["documents_reused_within_boundary"] == 2
    assert (
        found["full_lm@16"]["metrics"][PRIMARY_METRIC]
        != found["quick_lm@8"]["metrics"][PRIMARY_METRIC]
    )
    assert len(evaluators[SEARCH].calls) == 1


def test_endpoint_fires_once_after_the_final_partial_update(tmp_path: Path) -> None:
    recorder = RecordingEvaluator(QUICK)
    plan = fixture_plan(40, quick_lm=[40])
    trainer = build_trainer(tmp_path, evaluators={QUICK: recorder}, plan=plan, budget=40)
    assert plan.events[0].is_endpoint
    summary = trainer.train()
    assert summary.termination_reason == "completed"
    assert [row[2] for row in trainer.science.lr_receipts] == [16, 16, 8]
    assert [(c["event_id"], c["committed"]) for c in recorder.calls] == [("quick_lm@40", 40)]
    assert trainer.train_step() is None and len(recorder.calls) == 1
    final = tmp_path / "checkpoints" / "m2_run_final"
    ledger = science_json(final)["evaluation"]
    assert events_by_id(ledger)["quick_lm@40"]["status"] == "complete"
    assert ledger["completeness"]["complete"] is True
    # A fresh process resuming the completed run owes nothing and scores nothing.
    again = RecordingEvaluator(QUICK)
    resumed = build_trainer(tmp_path, evaluators={QUICK: again}, plan=plan, budget=40)
    reload(resumed, final)
    resumed._evaluate_boundary()
    assert again.calls == []


@dataclasses.dataclass
class ZeroValidOnce:
    """Delegating batcher whose first update carries no valid targets."""

    inner: Any
    used: bool = False

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[Any]:
        batches = self.inner.next_step_microbatches(remaining_budget=remaining_budget)
        if self.used:
            return batches
        self.used = True
        return [dataclasses.replace(b, loss_mask=torch.zeros_like(b.loss_mask)) for b in batches]

    def commit(self) -> None:
        self.inner.commit()

    def rollback(self) -> None:
        self.inner.rollback()

    def get_state(self) -> dict[str, Any]:
        return self.inner.get_state()  # type: ignore[no-any-return]

    def load_state(self, state: dict[str, Any]) -> None:
        self.inner.load_state(state)


def test_zero_valid_work_never_triggers_a_committed_target_event(tmp_path: Path) -> None:
    recorder = RecordingEvaluator(QUICK)
    trainer = build_trainer(
        tmp_path, evaluators={QUICK: recorder}, plan=fixture_plan(64, quick_lm=[0, 1])
    )
    trainer.batcher = ZeroValidOnce(trainer.batcher)
    assert trainer.train_step() is None  # the initial state is evaluated, nothing committed
    assert trainer.committed_valid_targets == 0 and trainer.science.lr_receipts == []
    assert [c["event_id"] for c in recorder.calls] == ["quick_lm@0"]
    trainer.train_step()
    assert [(c["event_id"], c["committed"]) for c in recorder.calls] == [
        ("quick_lm@0", 0),
        ("quick_lm@1", 16),
    ]


def test_cadence_never_changes_the_training_trajectory(tmp_path: Path) -> None:
    """Same stochastic run with and without evaluation between every update."""
    baseline = build_trainer(tmp_path / "plain", budget=64, dropout=0.1)
    run_all(baseline)
    baseline_rng = rng_state_digest("cpu")
    evaluators: dict[EventTier, Any] = lm_evaluators(tmp_path / "inputs")
    evaluators[QUICK] = RecordingEvaluator(QUICK, consume_rng=True)
    plan = fixture_plan(64, quick_lm=[0, 5, 17, 33, 40, 64], full_lm=[16, 48])
    evaluated = build_trainer(
        tmp_path / "eval", evaluators=evaluators, plan=plan, budget=64, dropout=0.1
    )
    run_all(evaluated)
    # Identical update boundaries, targets, LRs, weights, optimizer, cursor and RNG.
    assert evaluated.science.lr_receipts == baseline.science.lr_receipts
    assert [row[2] for row in evaluated.science.lr_receipts] == [16, 16, 16, 16]
    assert math.isclose(evaluated.science.lr_receipts[0][4][0], evaluated.schedule.get_lr(16))
    assert weights(evaluated).keys() == weights(baseline).keys()
    assert all(torch.equal(weights(evaluated)[k], weights(baseline)[k]) for k in weights(baseline))
    assert value_digest(evaluated.optimizer.state_dict()) == value_digest(
        baseline.optimizer.state_dict()
    )
    assert evaluated.batcher.get_state() == baseline.batcher.get_state()
    assert rng_state_digest("cpu") == baseline_rng  # training RNG stream unchanged
    found = receipts(evaluated)
    assert {e: r["actual_committed_targets"] for e, r in found.items()} == {
        "quick_lm@0": 0,
        "quick_lm@5": 16,
        "full_lm@16": 16,
        "quick_lm@17": 32,
        "quick_lm@33": 48,
        "quick_lm@40": 48,
        "full_lm@48": 48,
        "quick_lm@64": 64,
    }
    assert all(r["guard"]["changed"] == [] for r in found.values())
    assert "rng" in found["quick_lm@5"]["guard"]["restored"]


def test_stochastic_next_update_is_identical_with_and_without_evaluation(tmp_path: Path) -> None:
    """Evaluate between update N and N+1 only; update N+1 must not notice."""
    runs = {}
    for label, evaluate in (("plain", False), ("eval", True)):
        recorder = RecordingEvaluator(QUICK, consume_rng=True)
        trainer = build_trainer(
            tmp_path / label,
            evaluators={QUICK: recorder} if evaluate else None,
            plan=fixture_plan(64, quick_lm=[16]) if evaluate else None,
            dropout=0.2,
        )
        trainer.train_step()
        state = (random.getstate(), np.random.get_state()[1].copy(), torch.get_rng_state())
        trainer.train_step()
        runs[label] = (trainer, state, weights(trainer))
    assert runs["plain"][1][0] == runs["eval"][1][0]
    assert np.array_equal(runs["plain"][1][1], runs["eval"][1][1])
    assert torch.equal(runs["plain"][1][2], runs["eval"][1][2])
    plain, evaluated = runs["plain"][2], runs["eval"][2]
    assert all(torch.equal(plain[k], evaluated[k]) for k in plain)


# --------------------------------------------------------------- state guard


def test_guard_restores_rng_and_exact_per_module_modes(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path)
    modules = list(trainer.model.modules())
    modules[1].eval()  # heterogeneous: one submodule already in eval mode
    before = [m.training for m in modules]
    rng = rng_state_digest("cpu")
    guard = TrainingStateGuard(trainer)
    guard.capture()
    trainer.model.eval()  # an evaluator flipping modes on the live model
    torch.set_grad_enabled(False)
    random.random()
    np.random.rand(2)
    torch.rand(2)
    report = guard.restore_and_verify()
    assert report["restored"] == ["rng", "module_modes", "grad_enabled"]
    assert report["changed"] == []
    assert [m.training for m in modules] == before
    assert torch.is_grad_enabled() and rng_state_digest("cpu") == rng


def test_guard_reports_unreadable_runtime_flags_as_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """torch refuses to report matmul precision after mixed APIs; that is a change."""
    from xlm.training.science import ScientificRuntime

    trainer = build_trainer(tmp_path)
    guard = TrainingStateGuard(trainer)
    guard.capture()

    def refuse() -> dict[str, Any]:
        raise RuntimeError("mix of the legacy and new APIs")

    monkeypatch.setattr(ScientificRuntime, "observed_flags", staticmethod(refuse))
    assert guard.restore_and_verify()["changed"] == ["runtime_flags"]


def _first_rope(model: Any) -> Any:
    return next(m for m in model.modules() if hasattr(m, "cos_cached"))


LIVE_MUTATIONS: dict[str, Any] = {
    "model_tensors": lambda t: next(t.model.parameters()).data.add_(1.0),
    "rope_buffer": lambda t: _first_rope(t.model).cos_cached.add_(1.0),
    "model_attributes": lambda t: setattr(_first_rope(t.model), "max_position_embeddings", 99),
    "gradients": lambda t: setattr(
        next(t.model.parameters()), "grad", torch.ones_like(next(t.model.parameters()))
    ),
    "optimizer": lambda t: next(iter(t.optimizer.state.values()))["exp_avg"].add_(1.0),
    "schedule": lambda t: setattr(t.schedule, "base_lr", 0.5),
    "counters": lambda t: setattr(t, "processed_valid_targets", t.processed_valid_targets + 1),
    "data_cursor": lambda t: setattr(t.batcher.committed_cursor, "stream_token_offset", 1),
    "science_receipts": lambda t: t.science.lr_receipts.append([0, 0, 0, 0, [0.0]]),
    "runtime_flags": lambda t: torch.set_num_threads(torch.get_num_threads() + 1),
}
EXPECTED_COMPONENT = {
    "rope_buffer": "model_tensors",
    "model_attributes": "model_attributes",
}


@pytest.mark.parametrize("mutation", sorted(LIVE_MUTATIONS))
def test_mutating_evaluator_is_caught_and_training_refuses_to_continue(
    tmp_path: Path, mutation: str
) -> None:
    holder: dict[str, Trainer] = {}
    evaluator = CallbackEvaluator(QUICK, lambda _model: LIVE_MUTATIONS[mutation](holder["t"]))
    trainer = build_trainer(
        tmp_path, evaluators={QUICK: evaluator}, plan=fixture_plan(64, quick_lm=[16])
    )
    holder["t"] = trainer
    threads = torch.get_num_threads()
    try:
        with pytest.raises(RecoveryRequiredError, match="changed live training state"):
            trainer.train_step()
    finally:
        torch.set_num_threads(threads)
    history = attempt_history(trainer, "quick_lm@16")
    assert [h["status"] for h in history] == ["failed"]
    failure = history[0]["failure"]
    assert failure["type"] == "EvaluationStateMutationError"
    assert failure["live_state_compromised"] is True
    assert EXPECTED_COMPONENT.get(mutation, mutation) in failure["components"]
    outcome = trainer.evaluation._store.read(history[0]["outcome_artifact"])  # type: ignore[union-attr]
    assert outcome is not None and outcome["metrics"] is None and outcome["status"] == "failed"
    # A compromised in-memory run can neither checkpoint nor train further.
    with pytest.raises(RecoveryRequiredError):
        trainer._save_checkpoint("must_not_exist")
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()
    assert not (tmp_path / "checkpoints" / "must_not_exist").exists()


def test_replica_mutation_fails_the_score_but_not_training(tmp_path: Path) -> None:
    def mutate_replica(model: Any) -> None:
        with torch.no_grad():
            next(model.parameters()).add_(1.0)

    trainer = build_trainer(
        tmp_path,
        evaluators={QUICK: CallbackEvaluator(QUICK, mutate_replica)},
        plan=fixture_plan(64, quick_lm=[16]),
    )
    trainer.train_step()
    failure = attempt_history(trainer, "quick_lm@16")[0]["failure"]
    assert failure["components"] == ["replica"] and failure["live_state_compromised"] is False
    assert trainer.train_step() is not None  # training state was never touched
    trainer._save_checkpoint("still_allowed")


def test_long_document_scoring_mutates_a_live_model_but_never_the_trained_one(
    tmp_path: Path,
) -> None:
    """The audit hazard: rolling windows rebuild RoPE caches on the scored module."""
    tokenizer = ByteTokenizer()
    path, manifest_id = write_inventory(
        tmp_path / "long", {"d": [("x", "a long validation document " * 3)]}, tokenizer
    )
    evaluator = LMValidationEvaluator(
        FULL,
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        ),
        tokenizer,
        LMScoringPolicy(context_length=8, rolling_stride=4),
    )
    trainer = build_trainer(
        tmp_path, evaluators={FULL: evaluator}, plan=fixture_plan(64, full_lm=[16])
    )
    rope = _first_rope(trainer.model)
    shape = tuple(rope.cos_cached.shape)
    trainer.train_step()
    assert receipts(trainer)["full_lm@16"]["status"] == "complete"
    assert tuple(rope.cos_cached.shape) == shape and rope.max_position_embeddings == 8
    live = copy.deepcopy(trainer.model)
    ConditionalLikelihoodScorer(live, tokenizer, max_context_length=8, stride=4).score_document(
        "a long validation document " * 3
    )
    assert _first_rope(live).cos_cached.shape[0] > shape[0]  # would have mutated training


# ------------------------------------------------------------------- failures


def _raise(exc: BaseException) -> Any:
    def action(_model: Any) -> None:
        raise exc

    return action


def test_evaluator_exception_is_a_failed_attempt_never_a_score(tmp_path: Path) -> None:
    trainer = build_trainer(
        tmp_path,
        evaluators={QUICK: CallbackEvaluator(QUICK, _raise(RuntimeError("scorer exploded")))},
        plan=fixture_plan(64, quick_lm=[16]),
    )
    trainer.train_step()
    history = attempt_history(trainer, "quick_lm@16")
    assert [h["status"] for h in history] == ["failed"]
    assert history[0]["failure"] == {"type": "RuntimeError", "message": "scorer exploded"}
    assert receipts(trainer)["quick_lm@16"] is None
    completeness = trainer.evaluation.completeness()  # type: ignore[union-attr]
    assert not completeness.complete
    assert "quick_lm@16: failed (at C=16)" in completeness.reasons
    # Training authority is intact: training and checkpointing continue.
    assert trainer.train_step() is not None
    checkpoint = trainer._save_checkpoint("after_failure")
    assert events_by_id(science_json(checkpoint)["evaluation"])["quick_lm@16"]["status"] == "failed"


def test_invalid_metric_is_refused(tmp_path: Path) -> None:
    def nan_outcome(_model: Any) -> EvaluationOutcome:
        return EvaluationOutcome(AttemptOutcome.COMPLETE, {"ce": float("nan")}, {})

    trainer = build_trainer(
        tmp_path,
        evaluators={QUICK: CallbackEvaluator(QUICK, nan_outcome)},
        plan=fixture_plan(64, quick_lm=[16]),
    )
    trainer.train_step()
    failure = attempt_history(trainer, "quick_lm@16")[0]["failure"]
    assert failure["type"] == "InvalidMetricError" and "not finite" in failure["message"]


class Flaky(CallbackEvaluator):
    """Same identity every process; fails with ``exc`` while ``failures`` remain."""

    def __init__(self, failures: int, exc: BaseException) -> None:
        self.remaining = failures

        def action(_model: Any) -> None:
            if self.remaining > 0:
                self.remaining -= 1
                raise exc

        super().__init__(QUICK, action, label="flaky")


def test_interruption_and_retry_preserve_every_attempt(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16])
    first = build_trainer(
        tmp_path,
        evaluators={QUICK: Flaky(1, KeyboardInterrupt("operator stop mid-suite"))},
        plan=plan,
        checkpoint_every=16,
    )
    with pytest.raises(KeyboardInterrupt):
        first.train_step()
    checkpoint = tmp_path / "checkpoints" / "m2_run_step_1_ckpt"
    assert events_by_id(science_json(checkpoint)["evaluation"])["quick_lm@16"]["status"] == "due"
    assert [h["status"] for h in attempt_history(first, "quick_lm@16")] == ["interrupted"]
    # Second process: a transient failure. Third process: success.
    second = build_trainer(
        tmp_path,
        evaluators={QUICK: Flaky(1, RuntimeError("transient"))},
        plan=plan,
        checkpoint_every=16,
    )
    reload(second, checkpoint)
    second._evaluate_boundary()
    third = build_trainer(
        tmp_path, evaluators={QUICK: Flaky(0, RuntimeError())}, plan=plan, checkpoint_every=16
    )
    reload(third, checkpoint)
    third.train_step()
    history = attempt_history(third, "quick_lm@16")
    assert [(h["number"], h["status"]) for h in history] == [
        (1, "interrupted"),
        (2, "failed"),
        (3, "complete"),
    ]
    record = third.evaluation.ledger.records["quick_lm@16"]  # type: ignore[union-attr]
    assert record.canonical == history[2]["outcome_artifact"]
    assert [a["number"] for a in record.attempts] == [1, 2, 3]


def test_retries_are_bounded(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16])
    for _ in range(4):
        trainer = build_trainer(
            tmp_path,
            evaluators={QUICK: Flaky(10, RuntimeError("always"))},
            plan=plan,
            checkpoint_every=16,
        )
        checkpoint = tmp_path / "checkpoints" / "m2_run_step_1_ckpt"
        if checkpoint.exists():
            reload(trainer, checkpoint)
            trainer._evaluate_boundary()
        else:
            trainer.train_step()
    assert [h["status"] for h in attempt_history(trainer, "quick_lm@16")] == ["failed"] * 3


def test_deliberate_rerun_keeps_the_first_complete_receipt_canonical(tmp_path: Path) -> None:
    trainer = build_trainer(
        tmp_path,
        evaluators={QUICK: RecordingEvaluator(QUICK)},
        plan=fixture_plan(64, quick_lm=[16]),
    )
    trainer.train_step()
    first = trainer.evaluation.ledger.records["quick_lm@16"].canonical  # type: ignore[union-attr]
    trainer.evaluation.rerun(trainer, "quick_lm@16")  # type: ignore[union-attr]
    history = attempt_history(trainer, "quick_lm@16")
    assert [h["status"] for h in history] == ["complete", "complete"]
    record = trainer.evaluation.ledger.records["quick_lm@16"]  # type: ignore[union-attr]
    assert record.canonical == first == history[0]["outcome_artifact"]
    trainer.train_step()
    with pytest.raises(ValueError, match="own committed state"):
        trainer.evaluation.rerun(trainer, "quick_lm@16")  # type: ignore[union-attr]


# --------------------------------------------------------------------- resume


def test_due_event_survives_a_crash_before_scoring(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16])
    trainer = build_trainer(
        tmp_path, evaluators={QUICK: RecordingEvaluator(QUICK)}, plan=plan, checkpoint_every=16
    )

    original = trainer.evaluation.run_pending  # type: ignore[union-attr]

    def crash(current: Trainer) -> None:
        if current.committed_valid_targets == 16:
            raise KeyboardInterrupt("process died after the checkpoint, before scoring")
        original(current)

    trainer.evaluation.run_pending = crash  # type: ignore[method-assign,union-attr]
    with pytest.raises(KeyboardInterrupt):
        run_all(trainer)
    checkpoint = tmp_path / "checkpoints" / "m2_run_step_1_ckpt"
    saved = events_by_id(science_json(checkpoint)["evaluation"])["quick_lm@16"]
    assert saved["status"] == "due" and saved["attempts"] == []
    assert saved["due"]["actual_committed_targets"] == 16
    recorder = RecordingEvaluator(QUICK)
    resumed = build_trainer(tmp_path, evaluators={QUICK: recorder}, plan=plan, checkpoint_every=16)
    reload(resumed, checkpoint)
    resumed.train_step()  # the owed event is scored before the next update
    assert [(c["event_id"], c["committed"]) for c in recorder.calls] == [("quick_lm@16", 16)]
    assert recorder.calls[0]["digest"] == saved["due"]["model_state_digest"]
    assert resumed.committed_valid_targets == 32


def test_completed_attempt_is_adopted_on_resume_without_rescoring(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16])
    trainer = build_trainer(
        tmp_path, evaluators={QUICK: RecordingEvaluator(QUICK)}, plan=plan, checkpoint_every=16
    )
    trainer.train_step()  # checkpoint at C=16 (event due), then a complete attempt
    checkpoint = tmp_path / "checkpoints" / "m2_run_step_1_ckpt"
    assert events_by_id(science_json(checkpoint)["evaluation"])["quick_lm@16"]["status"] == "due"
    recorder = RecordingEvaluator(QUICK)
    resumed = build_trainer(tmp_path, evaluators={QUICK: recorder}, plan=plan, checkpoint_every=16)
    reload(resumed, checkpoint)
    resumed.train_step()
    assert recorder.calls == []
    assert [h["status"] for h in attempt_history(resumed, "quick_lm@16")] == ["complete"]
    assert receipts(resumed)["quick_lm@16"]["attempt"] == 1


def test_checkpoint_resume_across_cadence_runs_each_event_once(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16, 48])
    first = RecordingEvaluator(QUICK)
    trainer = build_trainer(tmp_path, evaluators={QUICK: first}, plan=plan, checkpoint_every=32)
    trainer.train_step()
    trainer.train_step()  # C=32: checkpoint after quick@16 completed at C=16
    assert [c["event_id"] for c in first.calls] == ["quick_lm@16"]
    checkpoint = tmp_path / "checkpoints" / "m2_run_step_2_ckpt"
    second = RecordingEvaluator(QUICK)
    resumed = build_trainer(tmp_path, evaluators={QUICK: second}, plan=plan, checkpoint_every=32)
    reload(resumed, checkpoint)
    resumed.train()
    assert [(c["event_id"], c["committed"]) for c in second.calls] == [("quick_lm@48", 48)]
    final = science_json(tmp_path / "checkpoints" / "m2_run_final")["evaluation"]
    assert final["completeness"]["complete"] is True
    assert [e["status"] for e in final["events"]] == ["complete", "complete"]


def test_a_different_model_state_is_never_a_cache_hit(tmp_path: Path) -> None:
    """Same run, event and step, different weights: the old receipt is not reused."""
    plan = fixture_plan(64, quick_lm=[0])
    lost = build_trainer(
        tmp_path, evaluators={QUICK: RecordingEvaluator(QUICK)}, plan=plan, init_seed=7
    )
    lost._evaluate_boundary()
    recorder = RecordingEvaluator(QUICK)
    replay = build_trainer(tmp_path, evaluators={QUICK: recorder}, plan=plan, init_seed=8)
    replay._evaluate_boundary()
    assert len(recorder.calls) == 1  # rescored: different computation identity
    history = attempt_history(replay, "quick_lm@0")
    assert [h["status"] for h in history] == ["complete", "complete"]
    assert history[0]["computation_identity"] != history[1]["computation_identity"]
    record = replay.evaluation.ledger.records["quick_lm@0"]  # type: ignore[union-attr]
    assert record.canonical == history[1]["outcome_artifact"]
    assert [a["number"] for a in record.lineage_attempts()] == [2]


def test_changed_plan_or_evaluator_refuses_ordinary_resume(tmp_path: Path) -> None:
    plan = fixture_plan(64, quick_lm=[16])
    trainer = build_trainer(
        tmp_path, evaluators={QUICK: RecordingEvaluator(QUICK)}, plan=plan, checkpoint_every=16
    )
    trainer.train_step()
    checkpoint = tmp_path / "checkpoints" / "m2_run_step_1_ckpt"
    cases = [
        ({QUICK: RecordingEvaluator(QUICK, label="other")}, plan, "evaluation inputs changed"),
        ({QUICK: RecordingEvaluator(QUICK)}, fixture_plan(64, quick_lm=[16, 32]), "plan changed"),
        (None, None, "presence differs"),
    ]
    for evaluators, other_plan, message in cases:
        resumed = build_trainer(tmp_path, evaluators=evaluators, plan=other_plan)
        with pytest.raises(IncompatibleCheckpointError, match=message):
            reload(resumed, checkpoint)
    forked = build_trainer(
        tmp_path,
        evaluators={QUICK: RecordingEvaluator(QUICK)},
        plan=fixture_plan(64, quick_lm=[0, 16, 48]),
        run_id="m2_fork",
    )
    reload(forked, checkpoint, is_fork=True)
    ledger = forked.science.evaluation
    assert ledger is not None and ledger.plan.origin_committed_targets == 16
    assert [e.event_id for e in ledger.plan.events] == ["quick_lm@16", "quick_lm@48"]
    assert ledger.plan.excluded_before_origin == ("quick_lm@0",)


def test_legacy_training_cannot_take_an_evaluation_cadence(tmp_path: Path) -> None:
    with pytest.raises(TrainerError, match="xlm-science-v1"):
        build_trainer(
            tmp_path,
            science=False,
            evaluators={QUICK: RecordingEvaluator(QUICK)},
            plan=fixture_plan(64, quick_lm=[16]),
        )
    legacy = build_trainer(tmp_path / "legacy", science=False)
    assert legacy.evaluation is None
    run_all(legacy)
    checkpoint = legacy._save_checkpoint("legacy")
    assert not (checkpoint / "science.json").exists()


def test_science_json_without_cadence_is_unchanged_from_m1(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path)
    trainer.train_step()
    payload = science_json(trainer._save_checkpoint("m1_shape"))
    assert sorted(payload) == [
        "lr_receipts",
        "policy",
        "runtime_receipts",
        "train_start_rng",
        "version",
    ]


# ---------------------------------------------------------- configured route


@pytest.fixture
def xlm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return tmp_path


def science_config(inputs: dict[str, tuple[Path, str]], **evaluation: Any) -> dict[str, Any]:
    science = {
        "version": "xlm-eval-cadence-v1",
        "cadence": "authored_fixture",
        "fixture_thresholds": {"quick_lm": [0, 8], "full_lm": [16]},
        "confirmation_registered": False,
        "quick_lm": {"manifest": str(inputs["quick"][0]), "manifest_id": inputs["quick"][1]},
        "full_lm": {"manifest": str(inputs["full"][0]), "manifest_id": inputs["full"][1]},
        "search_benchmark": None,
        "endpoint_confirmation": None,
        "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
    }
    science.update(evaluation)
    return {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 260,
            "num_layers": 1,
            "hidden_size": 16,
            "num_attention_heads": 2,
            "intermediate_size": 32,
            "context_length": 8,
            "attention_backend": "eager",
        },
        "data": {
            "synthetic_tokens": [4 + (i * 7) % 250 for i in range(160)],
            "tokenizer_artifact": "byte",
        },
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.01},
        "training": {
            "device": "cpu",
            "precision": "fp32",
            "init_seed": 7,
            "data_seed": 42,
            "context_length": 8,
            "global_batch_valid_targets": 8,
            "budget": {"max_valid_targets": 16, "max_train_seconds": 60},
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 0,
                "horizon_valid_targets": 16,
            },
            "checkpoint_every_valid_targets": 8,
            "science_version": SCIENCE_V1,
            "lr_policy": ENDPOINT_LR_POLICY,
            "training_seed": 10001,
            "runtime": CPU_RUNTIME,
        },
        "evaluation": {"suite": "search", "every_valid_targets": 8, "science": science},
    }


def configured_trainer(
    config: dict[str, Any], root: Path, *, fresh: bool = True, checkpoints: bool = True
) -> Trainer:
    from xlm.experiments.execution import resolve_execution_config
    from xlm.training.components import construct_training_components

    resolved, _ = resolve_execution_config(copy.deepcopy(config))
    components = construct_training_components(resolved, device="cpu", fresh_training_rng=fresh)
    paths = ArtifactPaths(root=root)
    return Trainer(
        model=components.model,
        objective=components.objective,
        optimizer=components.optimizer,
        optimizer_manifest=components.optimizer_manifest,
        schedule=components.schedule,
        batcher=components.batcher,
        checkpoint_manager=CheckpointManager(
            artifact_store=ArtifactStore(paths),
            run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
            paths=paths,
        ),
        run_id="configured",
        plan_id="configured_plan",
        max_valid_targets=resolved["training"]["budget"]["max_valid_targets"],
        checkpoint_every_valid_targets=(
            resolved["training"]["checkpoint_every_valid_targets"] if checkpoints else None
        ),
        science=components.science,
        evaluation=components.evaluation,
    )


def test_configured_cadence_actually_invokes_the_evaluator(xlm_home: Path) -> None:
    inputs = dev_inventories(xlm_home / "inputs", ByteTokenizer())
    config = science_config(inputs)
    trainer = configured_trainer(config, xlm_home / "run")
    assert trainer.evaluation is not None
    summary = trainer.train()
    assert summary.termination_reason == "completed"
    found = receipts(trainer)
    assert {e: (r["status"], r["actual_committed_targets"]) for e, r in found.items()} == {
        "quick_lm@0": ("complete", 0),
        "quick_lm@8": ("complete", 8),
        "full_lm@16": ("complete", 16),
    }
    full = found["full_lm@16"]
    assert full["evaluator"]["inputs"]["manifest_id"] == inputs["full"][1]
    assert full["evaluator"]["scoring_policy"]["logprob_dtype"] == "fp64"
    assert full["metrics"]["domains"].keys() == FULL_DOCS.keys()
    assert math.isfinite(full["metrics"][PRIMARY_METRIC])
    ledger = science_json(xlm_home / "run" / "checkpoints" / "configured_final")["evaluation"]
    assert ledger["completeness"]["complete"] is True
    # Resume from the mid checkpoint (C=8, quick_lm@8 recorded as due): the
    # replayed updates reach the identical state, so every durable receipt is
    # adopted exactly and nothing is scored twice.
    resumed = configured_trainer(config, xlm_home / "run", fresh=False, checkpoints=False)
    reload(resumed, xlm_home / "run" / "checkpoints" / "configured_step_1_ckpt")
    run_all(resumed)
    for event_id in ("quick_lm@0", "quick_lm@8", "full_lm@16"):
        assert [h["number"] for h in attempt_history(resumed, event_id)] == [1]
    assert resumed.evaluation.completeness().complete  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["training"].pop("science_version"), "requires training.science_version"),
        (
            lambda c: c["evaluation"]["science"].update(
                cadence="pilot_32m", fixture_thresholds=None
            ),
            "frozen for a 32000000-target budget",
        ),
        (lambda c: c["evaluation"]["science"].update(full_lm=None), "name no evaluation inputs"),
        (
            lambda c: c["evaluation"]["science"]["fixture_thresholds"].pop("full_lm"),
            "have no planned event",
        ),
    ],
)
def test_configured_cadence_fails_closed(xlm_home: Path, mutate: Any, message: str) -> None:
    from xlm.experiments.execution import resolve_execution_config

    config = science_config(dev_inventories(xlm_home / "inputs", ByteTokenizer()))
    if "science_version" in message:
        for key in ("lr_policy", "training_seed", "runtime"):
            config["training"].pop(key)
    mutate(config)
    with pytest.raises(ValueError, match=message):
        resolve_execution_config(config)


def test_checkpoint_scoring_in_a_separate_process_binds_the_loaded_weights(
    tmp_path: Path,
) -> None:
    tokenizer = ByteTokenizer()
    tokenizer_dir = tmp_path / "tokenizer"
    tokenizer.save(tokenizer_dir)
    evaluators = lm_evaluators(tmp_path / "inputs")
    trainer = build_trainer(
        tmp_path / "run",
        evaluators={FULL: evaluators[FULL]},
        plan=fixture_plan(64, full_lm=[16]),
        checkpoint_every=16,
    )
    trainer.train_step()
    checkpoint = tmp_path / "run" / "checkpoints" / "m2_run_step_1_ckpt"
    in_memory = receipts(trainer)["full_lm@16"]
    manifest = evaluators[FULL].inventory.manifest
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from xlm.evaluation.cadence import EventTier\n"
        "from xlm.evaluation.lm_validation import score_checkpoint_validation\n"
        "print(json.dumps(score_checkpoint_validation(Path(sys.argv[1]), "
        "manifest_path=Path(sys.argv[2]), manifest_id=sys.argv[3], tier=EventTier.FULL_LM, "
        "rolling_stride=4, tokenizer_path=sys.argv[4])))\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(checkpoint),
            str(manifest.source_path),
            manifest.manifest_id(),
            str(tokenizer_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    weights_file = hashlib.sha256((checkpoint / "model.pt").read_bytes()).hexdigest()
    assert result["model_state"]["weights_sha256"] == weights_file
    assert result["model_state"]["state_digest"] == in_memory["model_state"]["state_digest"]
    assert result["model_state"]["state_digest"] == model_state_digest(trainer.model)
    assert result["metrics"] == in_memory["metrics"]
