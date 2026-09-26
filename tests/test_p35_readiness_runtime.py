"""P35 pilot readiness, Trainer integration (needs torch; NEEDS LOCAL CERTIFICATION in cloud).

Tiny CPU science-v1 trainers over authored tokens; recording/callback
evaluators and the real native LM evaluator over authored inventories. Nothing
here measures quality or runs the pilot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

torch = pytest.importorskip("torch")

from p35_eval_support import (  # noqa: E402
    CallbackEvaluator,
    RecordingEvaluator,
    build_trainer,
    fixture_plan,
    reload,
    run_all,
)
from xlm.evaluation.cadence import (  # noqa: E402
    AUTHORED_FIXTURE,
    CheckpointRole,
    EventTier,
)
from xlm.evaluation.receipts import MAX_ATTEMPTS_PER_EVENT  # noqa: E402
from xlm.evaluation.recoverability import (  # noqa: E402
    DISABLED,
    ENDPOINT_FAIL_STOP,
    INITIAL_BARRIER,
    RECOVERABILITY_VERSION,
    RECOVERY_CHECKPOINTS,
    REQUIRED_ALL_PLANNED,
    RecoverabilityPolicy,
    checkpoint_plan_with_recovery,
)
from xlm.training.trainer import (  # noqa: E402
    InitialEvaluationBarrierError,
    RequiredEvaluationIncompleteError,
)

QUICK, FULL, SEARCH = EventTier.QUICK_LM, EventTier.FULL_LM, EventTier.SEARCH_BENCHMARK
V1 = {
    "version": RECOVERABILITY_VERSION,
    "required_events": REQUIRED_ALL_PLANNED,
    "initial_barrier": INITIAL_BARRIER,
    "recovery_checkpoints": RECOVERY_CHECKPOINTS,
    "endpoint": ENDPOINT_FAIL_STOP,
}
BUDGET = 48


class Failing:
    """Raise on selected calls (1-based) of one tier's evaluator, then succeed."""

    def __init__(self, fail_calls: set[int], exc: type[BaseException] = RuntimeError) -> None:
        self.fail_calls = fail_calls
        self.exc = exc
        self.calls = 0

    def __call__(self, model: Any) -> None:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise self.exc(f"authored evaluator failure on call {self.calls}")


def trainer_with_policy(
    root: Path,
    *,
    evaluators: dict[EventTier, Any],
    thresholds: dict[str, list[int]],
    milestones: list[int],
    policy: dict[str, str] | None = V1,
    run_id: str = "rd_run",
) -> Any:
    plan = fixture_plan(BUDGET, **thresholds)
    pol = RecoverabilityPolicy.from_config(policy) if policy is not None else None
    checkpoints = checkpoint_plan_with_recovery(
        {"cadence": AUTHORED_FIXTURE, "fixture_milestones": milestones, "fixture_recovery": []},
        BUDGET,
        evaluation_plan=plan,
        policy=pol,
    )
    trainer = build_trainer(
        root,
        budget=BUDGET,
        evaluators=evaluators,
        plan=plan,
        checkpoint_plan=checkpoints,
        run_id=run_id,
    )
    trainer.evaluation.recoverability = pol
    return trainer


def attempts(trainer: Any, event_id: str) -> list[tuple[int, str]]:
    return [(a["number"], a["status"]) for a in trainer.evaluation._store.history(event_id)]


# ------------------------------------------------------------- C=0 barrier


def c0_trainer(root: Path, failing_tier: EventTier | None, fail_calls: set[int]) -> Any:
    evaluators: dict[EventTier, Any] = {}
    for tier in (QUICK, FULL, SEARCH):
        if tier is failing_tier:
            evaluators[tier] = CallbackEvaluator(tier, Failing(fail_calls), label=tier.value)
        else:
            evaluators[tier] = RecordingEvaluator(tier, label=tier.value)
    return trainer_with_policy(
        root,
        evaluators=evaluators,
        thresholds={
            "quick_lm": [0, BUDGET],
            "full_lm": [0, BUDGET],
            "search_benchmark": [0, BUDGET],
        },
        milestones=[0, BUDGET],
    )


def test_complete_c0_events_allow_update_1(tmp_path: Path) -> None:
    trainer = c0_trainer(tmp_path, None, set())
    trainer.train()
    assert trainer.committed_valid_targets == BUDGET
    assert trainer.evaluation.completeness().complete


@pytest.mark.parametrize("tier", [QUICK, FULL, SEARCH])
def test_failed_c0_event_blocks_update_1_after_bounded_retries(
    tmp_path: Path, tier: EventTier
) -> None:
    trainer = c0_trainer(tmp_path, tier, {1, 2, 3, 4})
    with pytest.raises(InitialEvaluationBarrierError, match=f"{tier.value}@0"):
        trainer.train()
    assert trainer.step == 0 and trainer.committed_valid_targets == 0
    assert trainer.science.lr_receipts == []  # no optimizer update happened
    assert attempts(trainer, f"{tier.value}@0") == [
        (n, "failed") for n in range(1, MAX_ATTEMPTS_PER_EVENT + 1)
    ]
    # The initial state is durable and still owes the event.
    records = trainer.checkpoints.ledger.published()
    assert [r.planned_thresholds for r in records] == [[0]]
    with pytest.raises(InitialEvaluationBarrierError):
        trainer.train_step()  # retrying the loop cannot sneak an update in


def test_retry_at_c0_that_succeeds_lets_training_proceed(tmp_path: Path) -> None:
    trainer = c0_trainer(tmp_path, SEARCH, {1})
    trainer.train()
    assert attempts(trainer, "search_benchmark@0") == [(1, "failed"), (2, "complete")]
    record = trainer.evaluation.ledger.records["search_benchmark@0"]
    assert (
        record.canonical
        == trainer.evaluation._store.history("search_benchmark@0")[1]["outcome_artifact"]
    )
    assert trainer.committed_valid_targets == BUDGET


def test_without_a_policy_a_failed_c0_event_keeps_legacy_behavior(tmp_path: Path) -> None:
    evaluators = {
        QUICK: RecordingEvaluator(QUICK),
        SEARCH: CallbackEvaluator(SEARCH, Failing({1, 2, 3, 4}), label="s"),
    }
    trainer = trainer_with_policy(
        tmp_path,
        evaluators=evaluators,
        thresholds={"quick_lm": [0, BUDGET], "search_benchmark": [0]},
        milestones=[0, BUDGET],
        policy=None,
    )
    run_all(trainer)
    assert trainer.committed_valid_targets == BUDGET  # M2: training advanced
    assert attempts(trainer, "search_benchmark@0") == [(1, "failed")]  # no immediate retry
    assert not trainer.evaluation.completeness().complete


# ---------------------------------------------------- 1M/4M-style recovery


def recovery_trainer(root: Path, quick: Any, run_id: str = "rd_run") -> Any:
    return trainer_with_policy(
        root,
        evaluators={QUICK: quick},
        thresholds={"quick_lm": [0, 8, 24, BUDGET]},
        milestones=[0, BUDGET],
        run_id=run_id,
    )


def test_recovery_checkpoint_lands_on_the_natural_first_crossing(tmp_path: Path) -> None:
    trainer = recovery_trainer(tmp_path, RecordingEvaluator(QUICK))
    trainer.train()
    rows = trainer.science.lr_receipts
    assert [r[2] for r in rows] == [16, 16, 16]  # never split to land on 8 or 24
    records = {tuple(r.planned_thresholds): r for r in trainer.checkpoints.ledger.records.values()}
    t8 = records[(8,)]
    assert t8.role == CheckpointRole.EVALUATION_RECOVERY.value
    assert (t8.actual_committed_targets, t8.step) == (16, 1)
    due = trainer.evaluation.ledger.records["quick_lm@8"].due
    assert due["actual_committed_targets"] == 16
    assert due["model_state_digest"] == t8.model_state_digest  # exact state
    # Both evaluations completed, so neither recovery state is retained afterwards.
    assert records[(8,)].status == "retired" and records[(24,)].status == "retired"
    assert records[(8,)].retired["reason"] == "evaluation_recovery_released"
    assert records[(BUDGET,)].status == "published"


def test_failed_evaluation_keeps_its_recovery_checkpoint_until_exact_rescore(
    tmp_path: Path,
) -> None:
    # quick_lm@0 is call 1, quick_lm@8 call 2 (fails), quick_lm@24 call 3.
    quick = CallbackEvaluator(QUICK, Failing({2}), label="q")
    trainer = recovery_trainer(tmp_path, quick)
    run_all(trainer)  # stops at the budget before the rescore pass
    records = {tuple(r.planned_thresholds): r for r in trainer.checkpoints.ledger.records.values()}
    assert records[(8,)].status == "published"  # pinned by the unresolved event
    keeps = [
        entry["decision"]["keep"].get(records[(8,)].artifact_id)
        for entry in trainer.checkpoints.ledger.retention_log
    ]
    assert ["evaluation_dependency:quick_lm@8"] in keeps
    assert records[(24,)].status == "retired"  # resolved: released normally
    # The recording evaluator is not LM-rescorable; exact routing is checked instead.
    from xlm.evaluation.rescore import locate_exact_checkpoint

    root = Path(trainer.checkpoint_manager.store.paths.root)
    due = trainer.evaluation.ledger.records["quick_lm@8"].due
    located = locate_exact_checkpoint(trainer.checkpoints.ledger, due, root)
    assert located.artifact_id == records[(8,)].artifact_id  # never the latest one


def test_crash_after_recovery_checkpoint_before_scoring_resumes_exactly(tmp_path: Path) -> None:
    quick = CallbackEvaluator(QUICK, Failing({2}, KeyboardInterrupt), label="q")
    first = recovery_trainer(tmp_path / "run", quick)
    with pytest.raises(KeyboardInterrupt):
        run_all(first)
    root = Path(first.checkpoint_manager.store.paths.root) / "checkpoints"
    t8 = [p for p in root.iterdir() if "-t8-" in p.name]
    assert [p.name for p in t8] == ["rd_run_ckpt-t8-a001"]
    second = recovery_trainer(tmp_path / "run", RecordingEvaluator(QUICK, label="q"))
    reload(second, t8[0])
    assert second.committed_valid_targets == 16
    run_all(second)
    assert attempts(second, "quick_lm@8") == [(1, "interrupted"), (2, "complete")]
    assert [p.name for p in root.iterdir() if "-t8-" in p.name] == []  # released afterwards
    names = [r.artifact_id for r in second.checkpoints.ledger.records.values()]
    assert names.count("rd_run_ckpt-t8-a001") == 1  # never republished after resume


# ----------------------------------------------------------------- endpoint


def endpoint_trainer(root: Path, fail_calls: set[int]) -> Any:
    return trainer_with_policy(
        root,
        evaluators={
            QUICK: RecordingEvaluator(QUICK),
            SEARCH: CallbackEvaluator(SEARCH, Failing(fail_calls), label="s"),
        },
        thresholds={"quick_lm": [0, BUDGET], "search_benchmark": [0, BUDGET]},
        milestones=[0, BUDGET],
    )


def test_endpoint_failure_is_retried_on_the_live_endpoint_state(tmp_path: Path) -> None:
    trainer = endpoint_trainer(tmp_path, {2})  # call 1 is search@0, call 2 the endpoint
    trainer.train()
    event = f"search_benchmark@{BUDGET}"
    assert attempts(trainer, event) == [(1, "failed"), (2, "complete")]
    history = trainer.evaluation._store.history(event)
    assert {h["model_state_digest"] for h in history} == {
        trainer.evaluation.ledger.records[event].due["model_state_digest"]
    }
    assert trainer.evaluation.completeness().complete


def test_exhausted_endpoint_retries_fail_stop_evaluation_incomplete(tmp_path: Path) -> None:
    trainer = endpoint_trainer(tmp_path, {2, 3, 4, 5})
    with pytest.raises(RequiredEvaluationIncompleteError, match="EVALUATION_INCOMPLETE"):
        trainer.train()
    event = f"search_benchmark@{BUDGET}"
    assert attempts(trainer, event) == [(n, "failed") for n in (1, 2, 3)]
    assert trainer.committed_valid_targets == BUDGET
    endpoint = [r for r in trainer.checkpoints.ledger.published() if BUDGET in r.planned_thresholds]
    assert len(endpoint) == 1  # the exact endpoint state stays durable, nothing substituted
    assert trainer.termination_reason == "failed"


def test_disabled_endpoint_policy_keeps_m3_success_semantics(tmp_path: Path) -> None:
    trainer = trainer_with_policy(
        tmp_path,
        evaluators={
            QUICK: RecordingEvaluator(QUICK),
            SEARCH: CallbackEvaluator(SEARCH, Failing({2, 3, 4}), label="s"),
        },
        thresholds={"quick_lm": [0, BUDGET], "search_benchmark": [0, BUDGET]},
        milestones=[0, BUDGET],
        policy={**V1, "endpoint": DISABLED},
    )
    summary = trainer.train()
    assert summary.termination_reason == "completed"
    assert not trainer.evaluation.completeness().complete
    assert attempts(trainer, f"search_benchmark@{BUDGET}") == [(1, "failed")]


# ------------------------------------------------- update payload receipts


def payload_trainer(
    root: Path, *, group: int | None = None, milestones: list[int] | None = None
) -> Any:
    from p35_eval_support import default_tokens
    from xlm.data.sampling.update_payload import UpdatePayloadChain
    from xlm.evaluation.cadence import build_checkpoint_plan
    from xlm.training.data import TrainingBatcher

    trainer = build_trainer(
        root,
        budget=BUDGET,
        checkpoint_plan=build_checkpoint_plan(
            AUTHORED_FIXTURE,
            BUDGET,
            fixture_milestones=milestones or [0, 16, BUDGET],
            fixture_recovery=[],
        ),  # fmt: skip
        run_id="pay_run",
    )
    if group is not None:
        trainer.batcher = TrainingBatcher(
            default_tokens(BUDGET),
            context_length=8,
            global_batch_valid_targets=16,
            microbatch_sequences=group,
        )
    trainer.science.update_payloads = UpdatePayloadChain()
    return trainer


def test_committed_updates_and_only_those_enter_the_chain(tmp_path: Path) -> None:
    trainer = payload_trainer(tmp_path)
    run_all(trainer)
    chain = trainer.science.update_payloads
    assert [r[:3] for r in chain.rows] == [r[:3] for r in trainer.science.lr_receipts]
    saved = json.loads(
        (Path(trainer.checkpoints.checkpoint_dir(trainer.checkpoints.ledger.last_good().artifact_id))
         / "science.json").read_text()
    )  # fmt: skip
    assert saved["update_payloads"]["head"] == chain.head


def test_a_failed_optimizer_update_never_becomes_payload_evidence(tmp_path: Path) -> None:
    trainer = payload_trainer(tmp_path)
    assert trainer.train_step() is not None
    original = trainer.optimizer.step

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("authored optimizer failure")

    trainer.optimizer.step = boom
    with pytest.raises(RuntimeError, match="authored optimizer failure"):
        trainer.train_step()
    trainer.optimizer.step = original
    chain = trainer.science.update_payloads
    assert len(chain.rows) == 1 and chain._staged is None


def test_resume_restores_the_chain_and_matches_the_uninterrupted_run(tmp_path: Path) -> None:
    uninterrupted = payload_trainer(tmp_path / "a")
    run_all(uninterrupted)
    first = payload_trainer(tmp_path / "b")
    while first.committed_valid_targets < 16:
        first.train_step()
    root = Path(first.checkpoint_manager.store.paths.root) / "checkpoints"
    [t16] = [p for p in root.iterdir() if "-t16-" in p.name]
    resumed = payload_trainer(tmp_path / "b")
    reload(resumed, t16)
    assert len(resumed.science.update_payloads.rows) == 1
    run_all(resumed)
    assert (
        resumed.science.update_payloads.to_dict() == uninterrupted.science.update_payloads.to_dict()
    )


def test_resume_without_the_receipt_declaration_is_refused(tmp_path: Path) -> None:
    from xlm.training.checkpoint import IncompatibleCheckpointError

    first = payload_trainer(tmp_path)
    while first.committed_valid_targets < 16:
        first.train_step()
    root = Path(first.checkpoint_manager.store.paths.root) / "checkpoints"
    [t16] = [p for p in root.iterdir() if "-t16-" in p.name]
    other = payload_trainer(tmp_path)
    other.science.update_payloads = None
    before = {k: v.clone() for k, v in other.model.state_dict().items()}
    with pytest.raises(IncompatibleCheckpointError, match="update payload receipt presence"):
        reload(other, t16)
    assert all(torch.equal(before[k], v) for k, v in other.model.state_dict().items())


def test_microbatch_grouping_does_not_change_the_trainer_chain(tmp_path: Path) -> None:
    heads = set()
    weights = []
    for group in (1, 2):
        trainer = payload_trainer(tmp_path / f"g{group}", group=group)
        run_all(trainer)
        heads.add(trainer.science.update_payloads.head)
        weights.append({k: v.clone() for k, v in trainer.model.state_dict().items()})
    assert len(heads) == 1
