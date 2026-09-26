"""P35 pilot readiness: recoverability policy, C=0 barrier, evaluation-recovery checkpoints.

Pure tests (no torch): authored ledgers and plans only. The Trainer/queue
integration of the same decisions is in ``test_p35_readiness_runtime.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from xlm.artifacts.retention import (
    EVALUATION_RECOVERY,
    MILESTONE,
    RECOVERY,
    RetentionCandidate,
    decide_retention,
)
from xlm.evaluation.cadence import (
    CheckpointPlan,
    CheckpointRole,
    EvaluationPlan,
    build_checkpoint_plan,
    build_plan,
    projected_first_crossings,
    rebase_checkpoint_plan,
    update_arithmetic,
)
from xlm.evaluation.receipts import MAX_ATTEMPTS_PER_EVENT, EvaluationLedger, EventStatus
from xlm.evaluation.recoverability import (
    DISABLED,
    ENDPOINT_FAIL_STOP,
    INITIAL_BARRIER,
    RECOVERABILITY_VERSION,
    RECOVERY_CHECKPOINTS,
    REQUIRED_ALL_PLANNED,
    RecoverabilityError,
    RecoverabilityPolicy,
    barrier_blockers,
    checkpoint_plan_with_recovery,
    evaluation_recovery_thresholds,
    live_retry_candidates,
    recoverability_table,
    required_incomplete,
    run_plans_from_config,
    table_digest,
    unrecoverable_required,
)

M = 1_000_000
BUDGET = 32 * M
BATCH = 65_536
V1 = {
    "version": RECOVERABILITY_VERSION,
    "required_events": REQUIRED_ALL_PLANNED,
    "initial_barrier": INITIAL_BARRIER,
    "recovery_checkpoints": RECOVERY_CHECKPOINTS,
    "endpoint": ENDPOINT_FAIL_STOP,
}
PILOT_CADENCE = {"cadence": "pilot_32m", "fixture_milestones": None, "fixture_recovery": None}


def policy(**overrides: str) -> RecoverabilityPolicy:
    return RecoverabilityPolicy.from_config({**V1, **overrides})


def pilot_plans(
    pol: RecoverabilityPolicy | None,
) -> tuple[EvaluationPlan, CheckpointPlan]:
    evaluation = build_plan("pilot_32m", BUDGET, confirmation_registered=False)
    checkpoints = checkpoint_plan_with_recovery(
        PILOT_CADENCE, BUDGET, evaluation_plan=evaluation, policy=pol
    )
    return evaluation, checkpoints


# ------------------------------------------------------------------- policy


def test_policy_is_data_only_and_every_field_is_required() -> None:
    assert policy().identity() == V1
    for key in V1:
        broken = {k: v for k, v in V1.items() if k != key}
        with pytest.raises(RecoverabilityError):
            RecoverabilityPolicy.from_config(broken)
    with pytest.raises(RecoverabilityError):
        RecoverabilityPolicy.from_config({**V1, "initial_barrier": "maybe"})
    with pytest.raises(RecoverabilityError):
        RecoverabilityPolicy.from_config({**V1, "extra": 1})
    assert not policy(initial_barrier=DISABLED).barrier
    assert policy().digest() != policy(endpoint=DISABLED).digest()


def test_config_schema_requires_cadences_and_is_omitted_when_absent() -> None:
    from xlm.config.schemas import TrainingConfig
    from xlm.config.science import omit_absent_science_fields

    base = {
        "budget": {"max_valid_targets": 100},
        "schedule": {"type": "constant"},
        "device": "cpu",
    }
    dumped = omit_absent_science_fields(TrainingConfig.model_validate(base).model_dump(mode="json"))
    assert "evaluation_recoverability" not in dumped and "update_payload_receipt" not in dumped
    with pytest.raises(ValueError, match="checkpoint_cadence"):
        TrainingConfig.model_validate({**base, "evaluation_recoverability": V1})


# ----------------------------------------------- evaluation-recovery checkpoints


def test_pilot_recovery_checkpoints_are_exactly_1m_and_4m() -> None:
    evaluation, checkpoints = pilot_plans(policy())
    assert evaluation_recovery_thresholds(
        evaluation, checkpoint_thresholds=[0, 8 * M, 16 * M, 32 * M], policy=policy()
    ) == (1 * M, 4 * M)
    roles = {e.threshold: e.role for e in checkpoints.events}
    assert roles == {
        0: CheckpointRole.MILESTONE,
        1 * M: CheckpointRole.EVALUATION_RECOVERY,
        4 * M: CheckpointRole.EVALUATION_RECOVERY,
        8 * M: CheckpointRole.MILESTONE,
        16 * M: CheckpointRole.MILESTONE,
        32 * M: CheckpointRole.MILESTONE,
    }
    assert checkpoints.recoverability == V1


def test_recovery_checkpoints_never_split_an_update() -> None:
    """Same threshold, same first-crossing rule: the checkpoint lands on the eval boundary."""
    evaluation, checkpoints = pilot_plans(policy())
    thresholds = [e.threshold for e in checkpoints.events]
    project = projected_first_crossings(
        thresholds, budget_valid_targets=BUDGET, global_batch_valid_targets=BATCH
    )
    assert project[1 * M]["projected_committed_targets"] == 1_048_576  # update 16, overshoot
    assert project[1 * M]["overshoot_targets"] == 48_576
    assert project[4 * M]["projected_committed_targets"] == 4_063_232  # update 62
    eval_project = projected_first_crossings(
        [e.threshold for e in evaluation.events],
        budget_valid_targets=BUDGET,
        global_batch_valid_targets=BATCH,
    )
    for threshold in (1 * M, 4 * M):
        assert project[threshold] == eval_project[threshold]
        assert project[threshold]["projected_committed_targets"] % BATCH == 0
    # The update arithmetic is untouched: 488 full updates + one 18,432 final update.
    assert update_arithmetic(BUDGET, BATCH) == {
        "budget_valid_targets": BUDGET,
        "global_batch_valid_targets": BATCH,
        "full_updates": 488,
        "final_update_targets": 18_432,
        "total_updates": 489,
    }


def test_first_crossing_marks_the_recovery_event_due_with_the_actual_count() -> None:
    _, checkpoints = pilot_plans(policy())
    due = checkpoints.due(1_048_576, handled=["checkpoint@0"])
    assert [e.event_id for e in due] == ["checkpoint@1000000"]
    assert due[0].role is CheckpointRole.EVALUATION_RECOVERY
    assert checkpoints.due(983_040, handled=["checkpoint@0"]) == []  # update 15: not yet


def test_disabled_or_absent_policy_keeps_the_m3_plan_identity() -> None:
    m3 = build_checkpoint_plan("pilot_32m", BUDGET)
    _, none_policy = pilot_plans(None)
    assert none_policy.identity() == m3.identity()
    assert "recoverability" not in m3.identity()
    _, disabled = pilot_plans(policy(recovery_checkpoints=DISABLED))
    assert [e.threshold for e in disabled.events] == [e.threshold for e in m3.events]
    assert disabled.digest() != m3.digest()  # the declared policy is still bound


def test_changed_policy_changes_the_checkpoint_plan_digest() -> None:
    digests = {
        pilot_plans(policy(**change))[1].digest()
        for change in ({}, {"initial_barrier": DISABLED}, {"endpoint": DISABLED})
    }
    assert len(digests) == 3


def test_saved_plan_round_trips_and_fork_rebase_keeps_recovery_events() -> None:
    _, plan = pilot_plans(policy())
    again = CheckpointPlan.from_dict(plan.to_dict())
    assert again.digest() == plan.digest()
    fork = rebase_checkpoint_plan(plan, 2 * M)
    assert [e.event_id for e in fork.events][:1] == ["checkpoint@4000000"]
    assert fork.recoverability == V1
    assert "checkpoint@1000000" in fork.excluded_before_origin


def test_recovery_events_require_a_policy() -> None:
    from xlm.evaluation.cadence import CadenceError

    with pytest.raises(CadenceError, match="recoverability policy"):
        build_checkpoint_plan("pilot_32m", BUDGET, evaluation_recovery=[1 * M])


def test_run_plans_from_config_refuses_a_policy_without_cadences() -> None:
    config: dict[str, Any] = {
        "training": {"budget": {"max_valid_targets": BUDGET}, "evaluation_recoverability": V1},
        "evaluation": {},
    }
    with pytest.raises(RecoverabilityError, match="requires evaluation.science"):
        run_plans_from_config(config)


# --------------------------------------------------------- runtime decisions


def ledger_for(plan: EvaluationPlan) -> EvaluationLedger:
    return EvaluationLedger(plan, {t: "e" * 64 for t in {e.tier.value for e in plan.events}})


def cross(ledger: EvaluationLedger, event_id: str, committed: int) -> None:
    ledger.mark_due(
        ledger.plan.event(event_id),
        committed=committed,
        step=committed // BATCH,
        digest="d" * 64,
        computation_identity=f"comp-{event_id}",
    )


def attempt(ledger: EvaluationLedger, event_id: str, number: int, status: str) -> None:
    record = ledger.records[event_id]
    ledger.adopt_attempt(
        record,
        {
            "number": number,
            "status": status,
            "computation_identity": f"comp-{event_id}",
            "outcome_artifact": f"{event_id}-a{number}",
            "model_state_digest": "d" * 64,
            "actual_committed_targets": record.due["actual_committed_targets"],  # type: ignore[index]
            "failure": None,
        },
    )


def c0_ledger(statuses: dict[str, list[str]]) -> EvaluationLedger:
    ledger = ledger_for(build_plan("pilot_32m", BUDGET, confirmation_registered=False))
    for event_id in ("quick_lm@0", "full_lm@0", "search_benchmark@0"):
        cross(ledger, event_id, 0)
        for number, status in enumerate(statuses.get(event_id, ["complete"]), start=1):
            attempt(ledger, event_id, number, status)
    return ledger


def test_barrier_opens_only_when_every_required_c0_event_is_complete() -> None:
    assert barrier_blockers(c0_ledger({}), policy()) == []


@pytest.mark.parametrize("event_id", ["quick_lm@0", "full_lm@0", "search_benchmark@0"])
def test_any_failed_c0_event_closes_the_barrier(event_id: str) -> None:
    ledger = c0_ledger({event_id: ["failed"]})
    assert barrier_blockers(ledger, policy()) == [f"{event_id}: failed"]
    assert live_retry_candidates(ledger, 0, policy()) == [event_id]


@pytest.mark.parametrize("status", ["partial", "interrupted", "due"])
def test_partial_interrupted_or_unscored_c0_events_also_block(status: str) -> None:
    runs = [] if status == "due" else [status if status != "interrupted" else "interrupted"]
    ledger = c0_ledger({"search_benchmark@0": runs})
    assert barrier_blockers(ledger, policy()) == [f"search_benchmark@0: {status}"]


def test_a_retry_at_c0_that_completes_opens_the_barrier() -> None:
    ledger = c0_ledger({"search_benchmark@0": ["failed", "complete"]})
    assert barrier_blockers(ledger, policy()) == []
    record = ledger.records["search_benchmark@0"]
    assert [a["status"] for a in record.attempts] == ["failed", "complete"]  # history kept
    assert record.canonical == "search_benchmark@0-a2"  # first_complete_attempt_v1


def test_exhausted_retries_keep_the_barrier_closed_and_stop_retrying() -> None:
    ledger = c0_ledger({"full_lm@0": ["failed"] * MAX_ATTEMPTS_PER_EVENT})
    assert barrier_blockers(ledger, policy()) == ["full_lm@0: failed"]
    assert live_retry_candidates(ledger, 0, policy()) == []  # M2 lineage bound respected


def test_uncrossed_c0_events_block_so_skipping_the_boundary_cannot_open_it() -> None:
    ledger = ledger_for(build_plan("pilot_32m", BUDGET, confirmation_registered=False))
    assert len(barrier_blockers(ledger, policy())) == 3


def test_no_policy_or_disabled_barrier_keeps_legacy_behavior() -> None:
    ledger = c0_ledger({"search_benchmark@0": ["failed"]})
    assert barrier_blockers(ledger, None) == []
    assert barrier_blockers(ledger, policy(initial_barrier=DISABLED)) == []
    assert live_retry_candidates(ledger, 0, None) == []


def test_mid_run_boundaries_are_not_retried_live() -> None:
    ledger = c0_ledger({})
    cross(ledger, "quick_lm@1000000", 1_048_576)
    attempt(ledger, "quick_lm@1000000", 1, "failed")
    assert live_retry_candidates(ledger, 1_048_576, policy()) == []
    assert required_incomplete(ledger, policy()) == [
        f"{r.event_id}: {r.status.value}"
        for r in ledger.ordered()
        if r.status is not EventStatus.COMPLETE
    ]


def test_endpoint_retries_live_then_fail_stops() -> None:
    ledger = c0_ledger({})
    for record in ledger.ordered():
        if record.due is None:
            cross(ledger, record.event_id, min(-(-record.event.threshold // BATCH) * BATCH, BUDGET))
            if record.event.threshold < BUDGET:
                attempt(ledger, record.event_id, 1, "complete")
    attempt(ledger, "search_benchmark@32000000", 1, "failed")
    attempt(ledger, "quick_lm@32000000", 1, "complete")
    attempt(ledger, "full_lm@32000000", 1, "complete")
    assert live_retry_candidates(ledger, BUDGET, policy()) == ["search_benchmark@32000000"]
    assert required_incomplete(ledger, policy()) == ["search_benchmark@32000000: failed"]
    assert required_incomplete(ledger, policy(endpoint=DISABLED)) == []
    attempt(ledger, "search_benchmark@32000000", 2, "complete")
    assert required_incomplete(ledger, policy()) == []


# ---------------------------------------------------------------- plan table


def rows_by_event(pol: RecoverabilityPolicy | None) -> dict[str, dict[str, Any]]:
    evaluation, checkpoints = pilot_plans(pol)
    return {r["event"]: r for r in recoverability_table(evaluation, checkpoints, pol)}


def test_pilot_table_with_the_v1_policy_has_a_route_for_every_required_event() -> None:
    rows = rows_by_event(policy())
    assert unrecoverable_required(list(rows.values())) == []
    assert {r["recoverability"] for e, r in rows.items() if e.endswith("@0")} == {"BARRIER"}
    for event in ("quick_lm@1000000", "quick_lm@4000000"):
        assert rows[event]["state_retention"] == "evaluation_recovery_checkpoint"
        assert rows[event]["recoverability"] == "EXACT_CHECKPOINT"
    for event in ("quick_lm@8000000", "quick_lm@16000000"):
        assert rows[event]["state_retention"] == "milestone_checkpoint"
    endpoint = rows["search_benchmark@32000000"]
    assert endpoint["recoverability"] == "LIVE_ENDPOINT_THEN_FAIL_STOP"
    assert endpoint["retry_mechanism"] == ["endpoint_live_retry"]
    assert endpoint["blocking_policy"] == "fail_stop_evaluation_incomplete_at_budget"
    assert rows["quick_lm@32000000"]["retry_mechanism"] == [
        "endpoint_live_retry",
        "offline_exact_checkpoint_rescore",
    ]
    assert all(r["required"] for r in rows.values())


def test_pre_change_pilot_table_reproduces_the_audited_gaps() -> None:
    rows = rows_by_event(None)
    assert sorted(unrecoverable_required(list(rows.values()))) == sorted(
        [
            "search_benchmark@0",
            "quick_lm@1000000",
            "quick_lm@4000000",
            "search_benchmark@32000000",
        ]
    )
    assert rows["quick_lm@0"]["recoverability"] == "EXACT_CHECKPOINT"
    assert rows["quick_lm@1000000"]["state_retention"] == "none"


@pytest.mark.parametrize(
    ("change", "lost"),
    [
        ({"initial_barrier": DISABLED}, ["search_benchmark@0"]),
        ({"recovery_checkpoints": DISABLED}, ["quick_lm@1000000", "quick_lm@4000000"]),
        ({"endpoint": DISABLED}, ["search_benchmark@32000000"]),
    ],
)
def test_disabling_any_part_leaves_a_required_event_without_a_route(
    change: dict[str, str], lost: list[str]
) -> None:
    assert sorted(unrecoverable_required(list(rows_by_event(policy(**change)).values()))) == sorted(
        lost
    )


def test_table_digest_binds_policy_and_rows() -> None:
    pol = policy()
    rows = list(rows_by_event(pol).values())
    assert table_digest(rows, pol) == table_digest(list(rows), pol)
    other = policy(endpoint=DISABLED)
    assert table_digest(rows, pol) != table_digest(list(rows_by_event(other).values()), other)


# ------------------------------------------------------------------ retention


def candidate(name: str, role: str, committed: int, digest: str) -> RetentionCandidate:
    return RetentionCandidate(name, role, committed, committed // BATCH, 1, digest, "m" * 64)


PILOT_STATES = [
    candidate("t0", MILESTONE, 0, "s0"),
    candidate("t1m", EVALUATION_RECOVERY, 1_048_576, "s1"),
    candidate("t4m", EVALUATION_RECOVERY, 4_063_232, "s4"),
    candidate("t8m", MILESTONE, 8_060_928, "s8"),
]


def test_recovery_checkpoint_is_pinned_while_its_evaluation_is_unresolved() -> None:
    decision = decide_retention(
        PILOT_STATES, protected={}, evaluation_dependencies={"s1": ["quick_lm@1000000"]}
    )
    assert "t1m" in decision.keep
    assert decision.keep["t1m"] == ["evaluation_dependency:quick_lm@1000000"]
    assert decision.retire == {"t4m": "evaluation_recovery_released"}


def test_resolved_recovery_checkpoints_are_released_and_never_count_as_milestones() -> None:
    decision = decide_retention(PILOT_STATES, protected={}, evaluation_dependencies={})
    assert decision.retire == {
        "t1m": "evaluation_recovery_released",
        "t4m": "evaluation_recovery_released",
    }
    assert set(decision.keep) == {"t0", "t8m"}


def test_a_recovery_checkpoint_that_is_the_last_good_state_is_kept() -> None:
    decision = decide_retention(PILOT_STATES[:3], protected={}, evaluation_dependencies={})
    assert decision.keep["t4m"] == ["last_good_state"]
    assert decision.retire == {"t1m": "evaluation_recovery_released"}


def test_recovery_checkpoints_do_not_displace_the_latest_two_rolling_states() -> None:
    states = [
        candidate("r1", RECOVERY, 10, "a"),
        candidate("r2", RECOVERY, 20, "b"),
        candidate("e3", EVALUATION_RECOVERY, 30, "c"),
        candidate("m4", MILESTONE, 40, "d"),
    ]
    decision = decide_retention(states, protected={}, evaluation_dependencies={})
    assert decision.keep["r1"] == ["latest_recovery_2"]
    assert decision.keep["r2"] == ["latest_recovery_1"]
    assert decision.retire == {"e3": "evaluation_recovery_released"}
