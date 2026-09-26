"""Science-v1 evaluation recoverability policy (P35 pilot readiness).

A required evaluation event must never be silently lost. For each planned
event this module states how its exact model state is retained, how a failed
attempt is retried, and what happens when retries are exhausted. It is pure:
the trainer and the pilot planner both call it, so the plan review and the
runtime make the same decision.

``xlm-evaluation-recoverability-v1`` has three independently declared parts:

* ``required_initial_evaluation_barrier_v1``: every required event at committed
  count C = 0 must have a canonical COMPLETE receipt before optimizer update 1
  may begin. A failed attempt is retried at C = 0 with the unchanged M2 attempt
  semantics (a new attempt on the same crossing lineage, at most
  ``MAX_ATTEMPTS_PER_EVENT`` per lineage, canonical ``first_complete_attempt_v1``).
  If the bound is exhausted the run stops at C = 0; no update happens.
* ``evaluation_recovery_checkpoints_v1``: a required evaluation threshold that
  no milestone or rolling recovery checkpoint covers gets an
  ``evaluation_recovery`` checkpoint event at the same threshold. First
  crossing makes it land on exactly the evaluation's boundary; no update is
  split. The checkpoint is retained only while an evaluation that needs
  exactly its state is unresolved.
* ``endpoint_live_retry_then_fail_stop_v1``: at the exact budget a failed
  required attempt is retried on the live endpoint state (same bound). After
  the at-budget exact-checkpoint rescore pass, any required event still not
  COMPLETE makes the run fail with ``EVALUATION_INCOMPLETE``; it is never
  reported as a success.

``disabled`` is an explicit, recorded choice for each part; the pilot planner
refuses any plan in which a required event is left without a route.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.evaluation.cadence import (
    CheckpointPlan,
    CheckpointRole,
    EvaluationPlan,
    EventTier,
    PlannedEvent,
    build_checkpoint_plan,
    build_plan,
)
from xlm.evaluation.receipts import MAX_ATTEMPTS_PER_EVENT, EvaluationLedger, EventStatus

RECOVERABILITY_VERSION = "xlm-evaluation-recoverability-v1"
REQUIRED_ALL_PLANNED = "all_planned_events_v1"
INITIAL_BARRIER = "required_initial_evaluation_barrier_v1"
RECOVERY_CHECKPOINTS = "evaluation_recovery_checkpoints_v1"
ENDPOINT_FAIL_STOP = "endpoint_live_retry_then_fail_stop_v1"
DISABLED = "disabled"
EVALUATION_INCOMPLETE = "EVALUATION_INCOMPLETE"
#: Tiers with a verified exact-checkpoint scoring route (``evaluation/rescore.py``).
CHECKPOINT_RESCORABLE = (EventTier.QUICK_LM, EventTier.FULL_LM)
TABLE_VERSION = "xlm-evaluation-recoverability-table-v1"


class RecoverabilityError(ValueError):
    """A recoverability policy is malformed or inconsistent with its run."""


@dataclass(frozen=True)
class RecoverabilityPolicy:
    """One run's declared recoverability policy (data only)."""

    required_events: str
    initial_barrier: str
    recovery_checkpoints: str
    endpoint: str
    version: str = RECOVERABILITY_VERSION

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> RecoverabilityPolicy:
        expected = {
            "version": (RECOVERABILITY_VERSION,),
            "required_events": (REQUIRED_ALL_PLANNED,),
            "initial_barrier": (INITIAL_BARRIER, DISABLED),
            "recovery_checkpoints": (RECOVERY_CHECKPOINTS, DISABLED),
            "endpoint": (ENDPOINT_FAIL_STOP, DISABLED),
        }
        if set(config) != set(expected):
            raise RecoverabilityError(
                f"recoverability fields {sorted(config)} differ from {sorted(expected)}"
            )
        for key, allowed in expected.items():
            if config[key] not in allowed:
                raise RecoverabilityError(f"recoverability {key} {config[key]!r} not in {allowed}")
        return cls(
            required_events=str(config["required_events"]),
            initial_barrier=str(config["initial_barrier"]),
            recovery_checkpoints=str(config["recovery_checkpoints"]),
            endpoint=str(config["endpoint"]),
        )

    @property
    def barrier(self) -> bool:
        return self.initial_barrier == INITIAL_BARRIER

    @property
    def recovery_checkpoints_enabled(self) -> bool:
        return self.recovery_checkpoints == RECOVERY_CHECKPOINTS

    @property
    def endpoint_fail_stop(self) -> bool:
        return self.endpoint == ENDPOINT_FAIL_STOP

    def identity(self) -> dict[str, str]:
        return {
            "version": self.version,
            "required_events": self.required_events,
            "initial_barrier": self.initial_barrier,
            "recovery_checkpoints": self.recovery_checkpoints,
            "endpoint": self.endpoint,
        }

    def digest(self) -> str:
        return identity_digest(self.identity())


def required_events(
    plan: EvaluationPlan, policy: RecoverabilityPolicy | None
) -> list[PlannedEvent]:
    """The required events; ``all_planned_events_v1`` makes every planned event required."""
    if policy is None or policy.required_events == REQUIRED_ALL_PLANNED:
        return list(plan.events)
    raise RecoverabilityError(f"unknown required-event scope {policy.required_events!r}")


def evaluation_recovery_thresholds(
    plan: EvaluationPlan,
    *,
    checkpoint_thresholds: Iterable[int],
    policy: RecoverabilityPolicy | None,
) -> tuple[int, ...]:
    """Required evaluation thresholds that no planned checkpoint event covers.

    Equal thresholds share the first-crossing boundary by construction, so a
    checkpoint at the same threshold always retains exactly the evaluation's
    state. C = 0 is always covered (the initial milestone is mandatory).
    """
    if policy is None or not policy.recovery_checkpoints_enabled:
        return ()
    covered = set(checkpoint_thresholds)
    return tuple(
        sorted({e.threshold for e in required_events(plan, policy) if e.threshold not in covered})
    )


def checkpoint_plan_with_recovery(
    cadence: Mapping[str, Any],
    budget: int,
    *,
    evaluation_plan: EvaluationPlan | None,
    policy: RecoverabilityPolicy | None,
) -> CheckpointPlan:
    """The run's checkpoint plan: the declared cadence plus evaluation-recovery events."""
    base = build_checkpoint_plan(
        str(cadence["cadence"]),
        budget,
        fixture_milestones=cadence.get("fixture_milestones"),
        fixture_recovery=cadence.get("fixture_recovery"),
    )
    if policy is None:
        return base
    if evaluation_plan is None:
        raise RecoverabilityError("a recoverability policy requires an evaluation cadence")
    extra = evaluation_recovery_thresholds(
        evaluation_plan,
        checkpoint_thresholds=[e.threshold for e in base.events],
        policy=policy,
    )
    return build_checkpoint_plan(
        str(cadence["cadence"]),
        budget,
        fixture_milestones=cadence.get("fixture_milestones"),
        fixture_recovery=cadence.get("fixture_recovery"),
        evaluation_recovery=extra,
        recoverability=policy.identity(),
    )


def run_plans_from_config(
    config: Mapping[str, Any],
) -> tuple[EvaluationPlan | None, CheckpointPlan | None, RecoverabilityPolicy | None]:
    """Evaluation plan, checkpoint plan and policy of a resolved configuration (no I/O)."""
    training = config.get("training") or {}
    budget = int(training["budget"]["max_valid_targets"])
    science = (config.get("evaluation") or {}).get("science")
    evaluation_plan = (
        build_plan(
            str(science["cadence"]),
            budget,
            confirmation_registered=bool(science["confirmation_registered"]),
            fixture_thresholds=science.get("fixture_thresholds"),
        )
        if science is not None
        else None
    )
    raw_policy = training.get("evaluation_recoverability")
    policy = RecoverabilityPolicy.from_config(raw_policy) if raw_policy is not None else None
    if policy is not None and (
        evaluation_plan is None or training.get("checkpoint_cadence") is None
    ):
        raise RecoverabilityError(
            "training.evaluation_recoverability requires evaluation.science and "
            "training.checkpoint_cadence"
        )
    cadence = training.get("checkpoint_cadence")
    checkpoint_plan = (
        checkpoint_plan_with_recovery(
            cadence, budget, evaluation_plan=evaluation_plan, policy=policy
        )
        if cadence is not None
        else None
    )
    return evaluation_plan, checkpoint_plan, policy


# ------------------------------------------------------------ runtime decisions


def _attempts_left(record: Any) -> bool:
    return len(record.lineage_attempts()) < MAX_ATTEMPTS_PER_EVENT


def barrier_blockers(ledger: EvaluationLedger, policy: RecoverabilityPolicy | None) -> list[str]:
    """Required C = 0 events without a canonical COMPLETE receipt (barrier closed if any).

    An event that was never recorded as due also blocks: skipping the
    evaluation boundary can never open the barrier.
    """
    if policy is None or not policy.barrier or ledger.plan.origin_committed_targets != 0:
        return []
    required = {e.event_id for e in required_events(ledger.plan, policy)}
    blockers: list[str] = []
    for record in ledger.ordered():
        if record.event.threshold != 0 or record.event_id not in required:
            continue
        if record.status is not EventStatus.COMPLETE:
            blockers.append(f"{record.event_id}: {record.status.value}")
    return blockers


def live_retry_candidates(
    ledger: EvaluationLedger, committed: int, policy: RecoverabilityPolicy | None
) -> list[str]:
    """Required events at this live boundary that the policy retries now.

    Only the C = 0 barrier and the exact-budget endpoint retry on the live
    state; every other boundary keeps the unchanged M2/M3 behavior (checkpoint
    rescore at the budget). The M2 per-lineage bound is never exceeded.
    """
    if policy is None:
        return []
    at_start = committed == 0 and policy.barrier
    at_end = committed == ledger.plan.budget_valid_targets and policy.endpoint_fail_stop
    if not (at_start or at_end):
        return []
    required = {e.event_id for e in required_events(ledger.plan, policy)}
    return [
        record.event_id
        for record in ledger.ordered()
        if record.event_id in required
        and record.due is not None
        and record.due["actual_committed_targets"] == committed
        and record.status is not EventStatus.COMPLETE
        and _attempts_left(record)
    ]


def required_incomplete(ledger: EvaluationLedger, policy: RecoverabilityPolicy | None) -> list[str]:
    """Required events that are not COMPLETE when the endpoint policy fail-stops."""
    if policy is None or not policy.endpoint_fail_stop:
        return []
    required = {e.event_id for e in required_events(ledger.plan, policy)}
    return [
        f"{record.event_id}: {record.status.value}"
        for record in ledger.ordered()
        if record.event_id in required and record.status is not EventStatus.COMPLETE
    ]


# -------------------------------------------------------------- plan review


class Retention(StrEnum):
    INITIAL_MILESTONE = "initial_milestone_checkpoint"
    MILESTONE = "milestone_checkpoint"
    ENDPOINT_MILESTONE = "endpoint_milestone_checkpoint"
    ROLLING_RECOVERY = "rolling_recovery_checkpoint_pinned_while_unresolved"
    EVALUATION_RECOVERY = "evaluation_recovery_checkpoint"
    NONE = "none"


def _retention(event: PlannedEvent, roles: Mapping[int, CheckpointRole], budget: int) -> Retention:
    role = roles.get(event.threshold)
    if role is None:
        return Retention.NONE
    if role is CheckpointRole.MILESTONE:
        if event.threshold == 0:
            return Retention.INITIAL_MILESTONE
        return Retention.ENDPOINT_MILESTONE if event.threshold == budget else Retention.MILESTONE
    if role is CheckpointRole.RECOVERY:
        return Retention.ROLLING_RECOVERY
    return Retention.EVALUATION_RECOVERY


def recoverability_table(
    evaluation_plan: EvaluationPlan,
    checkpoint_plan: CheckpointPlan | None,
    policy: RecoverabilityPolicy | None,
) -> list[dict[str, Any]]:
    """One row per planned evaluation event: retention, retry, recoverability, blocking.

    ``recoverability`` is ``NONE`` when a clean evaluation failure can leave the
    event permanently incomplete *without* stopping the run: the exact state is
    not retained, or no scorer route exists for it and training (or the job)
    moves on anyway.
    """
    budget = evaluation_plan.budget_valid_targets
    roles = (
        {e.threshold: e.role for e in checkpoint_plan.events} if checkpoint_plan is not None else {}
    )
    required = {e.event_id for e in required_events(evaluation_plan, policy)}
    barrier = policy is not None and policy.barrier
    endpoint = policy is not None and policy.endpoint_fail_stop
    rows: list[dict[str, Any]] = []
    for event in evaluation_plan.events:
        retention = _retention(event, roles, budget)
        rescorable = retention is not Retention.NONE and event.tier in CHECKPOINT_RESCORABLE
        retry: list[str] = []
        if event.threshold == 0 and barrier:
            retry.append("initial_barrier_live_retry_at_c0")
        if event.threshold == budget and endpoint:
            retry.append("endpoint_live_retry")
        if rescorable:
            retry.append(
                "offline_exact_checkpoint_rescore"
                if event.threshold == budget
                else "at_budget_exact_checkpoint_rescore"
            )
        if event.threshold == 0 and barrier:
            recoverability = "BARRIER"
            blocking = "no_update_until_complete; fail_stop_at_c0_after_attempt_bound"
            permanent = True
        elif rescorable:
            recoverability = "EXACT_CHECKPOINT"
            blocking = (
                "fail_stop_evaluation_incomplete_at_budget"
                if endpoint
                else "training_continues; incomplete_if_rescore_fails"
            )
            permanent = True
        elif event.threshold == budget and endpoint:
            recoverability = "LIVE_ENDPOINT_THEN_FAIL_STOP"
            blocking = "fail_stop_evaluation_incomplete_at_budget"
            permanent = False
        else:
            recoverability = "NONE"
            blocking = "training_continues; event_can_be_lost"
            permanent = False
        rows.append(
            {
                "event": event.event_id,
                "tier": event.tier.value,
                "planned_threshold": event.threshold,
                "required": event.event_id in required,
                "state_retention": retention.value,
                "retry_mechanism": retry or ["none"],
                "recoverability": recoverability,
                "permanently_recoverable": permanent,
                "blocking_policy": blocking,
            }
        )
    return rows


def unrecoverable_required(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Required events whose route is NONE; an executable plan must have none."""
    return [str(r["event"]) for r in rows if r["required"] and r["recoverability"] == "NONE"]


def table_digest(rows: Sequence[Mapping[str, Any]], policy: RecoverabilityPolicy | None) -> str:
    return identity_digest(
        {
            "version": TABLE_VERSION,
            "policy": policy.identity() if policy is not None else None,
            "rows": [dict(r) for r in rows],
        }
    )
