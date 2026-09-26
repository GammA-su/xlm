"""Science-v1 evaluation cadence over absolute committed valid targets (P35 §K).

An evaluation event is planned at an absolute committed-target threshold and
becomes due at the first *committed* optimizer boundary whose count ``C`` is at
or above it. Training never changes an update to land on a threshold: the event
records both the planned threshold and the actual ``C`` it was evaluated at.

Thresholds are data. The three contract tables below are transcribed from §K
and are frozen to their run budgets; the only other source of thresholds is an
explicitly declared, non-research ``authored_fixture`` table for bounded smoke
tests. Nothing here executes configuration.

P35 M3 adds checkpoint events to the same planner (§L/§W): the same absolute
thresholds and the same first-crossing rule, with a fixed order at one boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from xlm.artifacts.manifest import identity_digest

CADENCE_VERSION = "xlm-eval-cadence-v1"
AUTHORED_FIXTURE = "authored_fixture"

_M = 1_000_000


class CadenceError(ValueError):
    """A cadence table or evaluation plan is unknown, inconsistent or unreachable."""


class EventTier(StrEnum):
    """Scientifically distinct evaluation event classes, in deterministic order."""

    QUICK_LM = "quick_lm"
    FULL_LM = "full_lm"
    SEARCH_BENCHMARK = "search_benchmark"
    ENDPOINT_CONFIRMATION = "endpoint_confirmation"


TIER_ORDER: tuple[EventTier, ...] = tuple(EventTier)


@dataclass(frozen=True)
class CadenceTable:
    """Absolute committed-target thresholds per tier for one run budget."""

    name: str
    budget_valid_targets: int | None
    thresholds: Mapping[EventTier, tuple[int, ...]]
    research: bool
    notes: tuple[str, ...] = ()


#: P35 §K, transcribed verbatim (decimal M). Endpoint confirmation appears only
#: in the 1B table and only for registered finalists.
CONTRACT_CADENCES: dict[str, CadenceTable] = {
    "pilot_32m": CadenceTable(
        name="pilot_32m",
        budget_valid_targets=32 * _M,
        thresholds={
            EventTier.QUICK_LM: (0, 1 * _M, 4 * _M, 8 * _M, 16 * _M, 32 * _M),
            EventTier.FULL_LM: (0, 32 * _M),
            EventTier.SEARCH_BENCHMARK: (0, 32 * _M),
            EventTier.ENDPOINT_CONFIRMATION: (),
        },
        research=True,
        notes=("§K 32M pilot: development benchmarks use a frozen small search subset, partial",),
    ),
    "screen_128m": CadenceTable(
        name="screen_128m",
        budget_valid_targets=128 * _M,
        thresholds={
            EventTier.QUICK_LM: tuple(t * _M for t in (0, 1, 4, 8, 16, 32, 64, 96, 128)),
            EventTier.FULL_LM: (0, 32 * _M, 128 * _M),
            EventTier.SEARCH_BENCHMARK: (128 * _M,),
            EventTier.ENDPOINT_CONFIRMATION: (),
        },
        research=True,
        notes=(
            "§K 128M screen: complete frozen search BLiMP at 128M; MC diagnostics for finalists",
        ),
    ),
    "full_1b": CadenceTable(
        name="full_1b",
        budget_valid_targets=1000 * _M,
        thresholds={
            EventTier.QUICK_LM: tuple(
                t * _M for t in (0, 1, 4, 8, 16, 32, 64, 128, 256, 512, 768, 1000)
            ),
            EventTier.FULL_LM: tuple(t * _M for t in (0, 128, 256, 512, 768, 1000)),
            EventTier.SEARCH_BENCHMARK: (256 * _M, 1000 * _M),
            EventTier.ENDPOINT_CONFIRMATION: (1000 * _M,),
        },
        research=True,
        notes=("§K 1B 50M: confirmation suite at 1000M only for registered finalists",),
    ),
}


@dataclass(frozen=True)
class PlannedEvent:
    """One planned evaluation: a tier at an absolute committed-target threshold."""

    tier: EventTier
    threshold: int
    is_endpoint: bool = False

    @property
    def event_id(self) -> str:
        return f"{self.tier.value}@{self.threshold}"

    @property
    def order_key(self) -> tuple[int, int]:
        return (self.threshold, TIER_ORDER.index(self.tier))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tier": self.tier.value,
            "planned_threshold": self.threshold,
            "is_endpoint": self.is_endpoint,
        }


@dataclass(frozen=True)
class EvaluationPlan:
    """The frozen, ordered set of evaluation events of one run."""

    cadence: str
    research: bool
    budget_valid_targets: int
    origin_committed_targets: int
    confirmation_registered: bool
    events: tuple[PlannedEvent, ...]
    excluded_before_origin: tuple[str, ...] = ()
    version: str = CADENCE_VERSION
    notes: tuple[str, ...] = field(default=(), compare=False)

    def identity(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "cadence": self.cadence,
            "research": self.research,
            "budget_valid_targets": self.budget_valid_targets,
            "origin_committed_targets": self.origin_committed_targets,
            "confirmation_registered": self.confirmation_registered,
            "events": [event.to_dict() for event in self.events],
            "excluded_before_origin": list(self.excluded_before_origin),
        }

    def digest(self) -> str:
        return identity_digest(self.identity())

    def event(self, event_id: str) -> PlannedEvent:
        for event in self.events:
            if event.event_id == event_id:
                return event
        raise CadenceError(f"event '{event_id}' is not in this plan")

    def due(self, committed: int, handled: Iterable[str] = ()) -> list[PlannedEvent]:
        """Events whose threshold the committed count has reached, in plan order.

        ``committed`` must be the count of an actual committed boundary. A
        single update that crosses several thresholds makes all of them due
        here, together, at that one ``C``.
        """
        return first_crossing_due(self.events, committed, handled)

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity(), "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EvaluationPlan:
        if payload.get("version") != CADENCE_VERSION:
            raise CadenceError(f"unsupported cadence version '{payload.get('version')}'")
        events = tuple(
            PlannedEvent(
                tier=EventTier(entry["tier"]),
                threshold=int(entry["planned_threshold"]),
                is_endpoint=bool(entry["is_endpoint"]),
            )
            for entry in payload["events"]
        )
        plan = cls(
            cadence=str(payload["cadence"]),
            research=bool(payload["research"]),
            budget_valid_targets=int(payload["budget_valid_targets"]),
            origin_committed_targets=int(payload["origin_committed_targets"]),
            confirmation_registered=bool(payload["confirmation_registered"]),
            events=events,
            excluded_before_origin=tuple(payload.get("excluded_before_origin", ())),
            notes=tuple(payload.get("notes", ())),
        )
        if [e.event_id for e in plan.events] != [
            e.event_id for e in sorted(plan.events, key=lambda e: e.order_key)
        ]:
            raise CadenceError("saved plan events are not in deterministic order")
        return plan


def first_crossing_due(
    events: Sequence[Any], committed: int, handled: Iterable[str] = ()
) -> list[Any]:
    """The one first-crossing rule shared by evaluation and checkpoint events.

    An event is due at the first committed boundary whose count reaches its
    absolute threshold; events already handled (crossed earlier, restored by a
    resume) are never due again. Plan order is preserved.
    """
    done = set(handled)
    return [e for e in events if e.threshold <= committed and e.event_id not in done]


def _checked_thresholds(label: str, values: Sequence[Any], budget: int) -> tuple[int, ...]:
    thresholds: list[int] = []
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool):
            raise CadenceError(f"{label} threshold {value!r} is not an integer target count")
        if value < 0:
            raise CadenceError(f"{label} threshold {value} is negative")
        if value > budget:
            raise CadenceError(
                f"{label} threshold {value} exceeds the run budget {budget}; it could "
                "never fire and would silently leave the run evaluation-incomplete"
            )
        thresholds.append(value)
    if thresholds != sorted(set(thresholds)):
        raise CadenceError(f"{label} thresholds must be strictly increasing and unique")
    return tuple(thresholds)


def build_plan(
    cadence: str,
    budget_valid_targets: int,
    *,
    confirmation_registered: bool,
    fixture_thresholds: Mapping[str, Sequence[int]] | None = None,
    origin_committed_targets: int = 0,
) -> EvaluationPlan:
    """Resolve a cadence name into the frozen plan of one run.

    Contract tables are bound to their budgets: a 128M screen table cannot be
    applied to a 100M run. An ``authored_fixture`` table must list its
    thresholds explicitly and is labelled non-research. ``origin`` is the
    committed count the run starts from (0 for a fresh run, the fork point for
    an explicit fork); thresholds below it belong to a parent run and are
    recorded as excluded, never silently dropped.
    """
    if budget_valid_targets <= 0:
        raise CadenceError("an evaluation plan requires a positive target budget")
    if not 0 <= origin_committed_targets <= budget_valid_targets:
        raise CadenceError("plan origin must lie within the run budget")
    if cadence == AUTHORED_FIXTURE:
        if fixture_thresholds is None:
            raise CadenceError("authored_fixture cadence requires explicit fixture thresholds")
        unknown = set(fixture_thresholds) - {tier.value for tier in EventTier}
        if unknown:
            raise CadenceError(f"unknown fixture tiers {sorted(unknown)}")
        table = CadenceTable(
            name=AUTHORED_FIXTURE,
            budget_valid_targets=None,
            thresholds={
                tier: _checked_thresholds(
                    tier.value, fixture_thresholds.get(tier.value, ()), budget_valid_targets
                )
                for tier in EventTier
            },
            research=False,
            notes=("authored fixture cadence: bounded smoke evidence, never a research schedule",),
        )
    else:
        if fixture_thresholds is not None:
            raise CadenceError("fixture thresholds are only accepted for authored_fixture")
        if cadence not in CONTRACT_CADENCES:
            raise CadenceError(
                f"unknown cadence '{cadence}'; contract tables are {sorted(CONTRACT_CADENCES)}"
            )
        table = CONTRACT_CADENCES[cadence]
        if table.budget_valid_targets != budget_valid_targets:
            raise CadenceError(
                f"cadence '{cadence}' is frozen for a {table.budget_valid_targets}-target budget, "
                f"not {budget_valid_targets}"
            )
    confirmation = table.thresholds.get(EventTier.ENDPOINT_CONFIRMATION, ())
    if confirmation_registered and not confirmation:
        raise CadenceError(f"cadence '{table.name}' has no endpoint confirmation event")
    if confirmation and any(t != budget_valid_targets for t in confirmation):
        raise CadenceError("endpoint confirmation may only be planned at the run budget")

    events: list[PlannedEvent] = []
    excluded: list[str] = []
    for tier in EventTier:
        if tier is EventTier.ENDPOINT_CONFIRMATION and not confirmation_registered:
            continue
        for threshold in table.thresholds.get(tier, ()):
            event = PlannedEvent(tier, threshold, is_endpoint=threshold == budget_valid_targets)
            if threshold < origin_committed_targets:
                excluded.append(event.event_id)
            else:
                events.append(event)
    if not events:
        raise CadenceError("the evaluation plan contains no event; declare no cadence instead")
    return EvaluationPlan(
        cadence=table.name,
        research=table.research,
        budget_valid_targets=budget_valid_targets,
        origin_committed_targets=origin_committed_targets,
        confirmation_registered=confirmation_registered,
        events=tuple(sorted(events, key=lambda e: e.order_key)),
        excluded_before_origin=tuple(excluded),
        notes=table.notes,
    )


def rebase_plan(plan: EvaluationPlan, origin_committed_targets: int) -> EvaluationPlan:
    """Re-origin an untouched plan for an explicit fork; the parent keeps its events."""
    thresholds: dict[str, list[int]] = {}
    for event in plan.events:
        thresholds.setdefault(event.tier.value, []).append(event.threshold)
    if plan.cadence == AUTHORED_FIXTURE:
        return build_plan(
            AUTHORED_FIXTURE,
            plan.budget_valid_targets,
            confirmation_registered=plan.confirmation_registered,
            fixture_thresholds=thresholds,
            origin_committed_targets=origin_committed_targets,
        )
    return build_plan(
        plan.cadence,
        plan.budget_valid_targets,
        confirmation_registered=plan.confirmation_registered,
        origin_committed_targets=origin_committed_targets,
    )


# --------------------------------------------------------------------- checkpoints
#
# Checkpoint events use the same absolute committed-target thresholds and the
# same first-crossing rule as evaluation events. At one committed boundary the
# order is fixed: evaluation crossings are recorded, then the boundary's single
# checkpoint is published (so it owes those evaluations), then evaluation
# attempts run. Checkpoints never shorten or split an optimizer update.

CHECKPOINT_CADENCE_VERSION = "xlm-checkpoint-cadence-v1"
BOUNDARY_PHASES: tuple[str, ...] = ("evaluation_crossing", "checkpoint", "evaluation_attempt")


class CheckpointRole(StrEnum):
    """Retention role of a checkpoint event."""

    #: Pinned scientific milestone: never retired by the rolling policy.
    MILESTONE = "milestone"
    #: Rolling recovery state: only the latest two are retained.
    RECOVERY = "recovery"


@dataclass(frozen=True)
class CheckpointCadenceTable:
    """Absolute checkpoint thresholds for one run budget (contract §L/§W)."""

    name: str
    budget_valid_targets: int | None
    milestones: tuple[int, ...]
    recovery: tuple[int, ...]
    research: bool
    notes: tuple[str, ...] = ()


def _recovery_multiples(every: int, budget: int, exclude: Sequence[int]) -> tuple[int, ...]:
    skip = set(exclude)
    return tuple(t for t in range(every, budget, every) if t not in skip)


_PILOT_MILESTONES = (0, 8 * _M, 16 * _M, 32 * _M)
_FULL_1B_MILESTONES = (0, 128 * _M, 256 * _M, 512 * _M, 1000 * _M)

#: Contract §L/§W, transcribed. The 32M pilot replaces the 64M recovery cadence
#: with its 8M/16M first-crossing boundaries and the exact 32M final state; the
#: initial (C=0) milestone is the "initialized weights/identity" checkpoint.
CONTRACT_CHECKPOINT_CADENCES: dict[str, CheckpointCadenceTable] = {
    "pilot_32m": CheckpointCadenceTable(
        name="pilot_32m",
        budget_valid_targets=32 * _M,
        milestones=_PILOT_MILESTONES,
        recovery=(),
        research=True,
        notes=(
            "§W: initialized weights/identity, 8M and 16M first-crossing boundaries, exact "
            "32M final; §L: the pilot overrides the 64M recovery cadence",
            "first crossing can exceed 8M/16M: planned and actual counts are both recorded",
        ),
    ),
    "full_1b": CheckpointCadenceTable(
        name="full_1b",
        budget_valid_targets=1000 * _M,
        milestones=_FULL_1B_MILESTONES,
        recovery=_recovery_multiples(64 * _M, 1000 * _M, _FULL_1B_MILESTONES),
        research=True,
        notes=(
            "§L: recovery every 64M targets at the next update boundary; immutable "
            "milestones initial/128/256/512/1000M; 768M is not planned (only if a named "
            "analysis needs it)",
        ),
    ),
}


@dataclass(frozen=True)
class PlannedCheckpoint:
    """One planned checkpoint at an absolute committed-target threshold."""

    threshold: int
    role: CheckpointRole
    is_endpoint: bool = False

    @property
    def event_id(self) -> str:
        return f"checkpoint@{self.threshold}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "planned_threshold": self.threshold,
            "role": self.role.value,
            "is_endpoint": self.is_endpoint,
        }


@dataclass(frozen=True)
class CheckpointPlan:
    """The frozen, ordered set of checkpoint events of one run."""

    cadence: str
    research: bool
    budget_valid_targets: int
    origin_committed_targets: int
    events: tuple[PlannedCheckpoint, ...]
    excluded_before_origin: tuple[str, ...] = ()
    version: str = CHECKPOINT_CADENCE_VERSION
    notes: tuple[str, ...] = field(default=(), compare=False)

    def identity(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "cadence": self.cadence,
            "research": self.research,
            "budget_valid_targets": self.budget_valid_targets,
            "origin_committed_targets": self.origin_committed_targets,
            "events": [event.to_dict() for event in self.events],
            "excluded_before_origin": list(self.excluded_before_origin),
        }

    def digest(self) -> str:
        return identity_digest(self.identity())

    def event(self, event_id: str) -> PlannedCheckpoint:
        for event in self.events:
            if event.event_id == event_id:
                return event
        raise CadenceError(f"checkpoint event '{event_id}' is not in this plan")

    def due(self, committed: int, handled: Iterable[str] = ()) -> list[PlannedCheckpoint]:
        """Checkpoint events first crossed at this committed boundary, in threshold order."""
        return first_crossing_due(self.events, committed, handled)

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity(), "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CheckpointPlan:
        if payload.get("version") != CHECKPOINT_CADENCE_VERSION:
            raise CadenceError(f"unsupported checkpoint cadence version '{payload.get('version')}'")
        events = tuple(
            PlannedCheckpoint(
                threshold=int(entry["planned_threshold"]),
                role=CheckpointRole(entry["role"]),
                is_endpoint=bool(entry["is_endpoint"]),
            )
            for entry in payload["events"]
        )
        if [e.threshold for e in events] != sorted({e.threshold for e in events}):
            raise CadenceError("saved checkpoint events are not strictly ordered")
        return cls(
            cadence=str(payload["cadence"]),
            research=bool(payload["research"]),
            budget_valid_targets=int(payload["budget_valid_targets"]),
            origin_committed_targets=int(payload["origin_committed_targets"]),
            events=events,
            excluded_before_origin=tuple(payload.get("excluded_before_origin", ())),
            notes=tuple(payload.get("notes", ())),
        )


def build_checkpoint_plan(
    cadence: str,
    budget_valid_targets: int,
    *,
    fixture_milestones: Sequence[int] | None = None,
    fixture_recovery: Sequence[int] | None = None,
    origin_committed_targets: int = 0,
) -> CheckpointPlan:
    """Resolve a checkpoint cadence into the frozen plan of one run.

    Contract tables are bound to their budgets. ``authored_fixture`` requires
    explicit milestone and recovery thresholds and is non-research. A threshold
    that is both a milestone and a recovery point is one milestone event. The
    endpoint (threshold equal to the budget) must be a milestone: the exact
    final state is always retained. Thresholds below a fork origin belong to
    the parent run and are recorded as excluded.
    """
    if budget_valid_targets <= 0:
        raise CadenceError("a checkpoint plan requires a positive target budget")
    if not 0 <= origin_committed_targets <= budget_valid_targets:
        raise CadenceError("checkpoint plan origin must lie within the run budget")
    if cadence == AUTHORED_FIXTURE:
        if fixture_milestones is None or fixture_recovery is None:
            raise CadenceError(
                "authored_fixture checkpoints require explicit milestone and recovery thresholds"
            )
        table = CheckpointCadenceTable(
            name=AUTHORED_FIXTURE,
            budget_valid_targets=None,
            milestones=_checked_thresholds(
                "checkpoint milestone", fixture_milestones, budget_valid_targets
            ),
            recovery=_checked_thresholds(
                "checkpoint recovery", fixture_recovery, budget_valid_targets
            ),
            research=False,
            notes=("authored fixture checkpoint cadence: bounded smoke evidence only",),
        )
    else:
        if fixture_milestones is not None or fixture_recovery is not None:
            raise CadenceError(
                "fixture checkpoint thresholds are only accepted for authored_fixture"
            )
        if cadence not in CONTRACT_CHECKPOINT_CADENCES:
            raise CadenceError(
                f"unknown checkpoint cadence '{cadence}'; contract tables are "
                f"{sorted(CONTRACT_CHECKPOINT_CADENCES)}"
            )
        table = CONTRACT_CHECKPOINT_CADENCES[cadence]
        if table.budget_valid_targets != budget_valid_targets:
            raise CadenceError(
                f"checkpoint cadence '{cadence}' is frozen for a "
                f"{table.budget_valid_targets}-target budget, not {budget_valid_targets}"
            )
    if budget_valid_targets not in table.milestones:
        raise CadenceError("the exact-budget endpoint must be a pinned checkpoint milestone")
    roles = {t: CheckpointRole.RECOVERY for t in table.recovery}
    roles.update({t: CheckpointRole.MILESTONE for t in table.milestones})
    events: list[PlannedCheckpoint] = []
    excluded: list[str] = []
    for threshold in sorted(roles):
        event = PlannedCheckpoint(
            threshold, roles[threshold], is_endpoint=threshold == budget_valid_targets
        )
        if threshold < origin_committed_targets:
            excluded.append(event.event_id)
        else:
            events.append(event)
    return CheckpointPlan(
        cadence=table.name,
        research=table.research,
        budget_valid_targets=budget_valid_targets,
        origin_committed_targets=origin_committed_targets,
        events=tuple(events),
        excluded_before_origin=tuple(excluded),
        notes=table.notes,
    )


def rebase_checkpoint_plan(plan: CheckpointPlan, origin_committed_targets: int) -> CheckpointPlan:
    """Re-origin an untouched checkpoint plan for an explicit fork."""
    if plan.cadence == AUTHORED_FIXTURE:
        return build_checkpoint_plan(
            AUTHORED_FIXTURE,
            plan.budget_valid_targets,
            fixture_milestones=[
                e.threshold for e in plan.events if e.role is CheckpointRole.MILESTONE
            ],
            fixture_recovery=[
                e.threshold for e in plan.events if e.role is CheckpointRole.RECOVERY
            ],
            origin_committed_targets=origin_committed_targets,
        )
    return build_checkpoint_plan(
        plan.cadence, plan.budget_valid_targets, origin_committed_targets=origin_committed_targets
    )


def projected_first_crossings(
    thresholds: Iterable[int], *, budget_valid_targets: int, global_batch_valid_targets: int
) -> dict[int, dict[str, int]]:
    """Where each threshold is first crossed if every update commits the full batch.

    Planning arithmetic only: the batcher fills every update to the exact global
    valid-target batch and the last update is masked to the exact budget, so the
    committed counts are ``min(k * B, budget)``. The trainer records the actual
    count it observes; this is the prediction a run is checked against.
    """
    if budget_valid_targets <= 0 or global_batch_valid_targets <= 0:
        raise CadenceError("projection requires a positive budget and global batch")
    batch = global_batch_valid_targets
    projection: dict[int, dict[str, int]] = {}
    for threshold in thresholds:
        if not 0 <= threshold <= budget_valid_targets:
            raise CadenceError(f"threshold {threshold} lies outside the budget")
        step = -(-threshold // batch)  # ceiling: the first boundary at or above
        committed = min(step * batch, budget_valid_targets)
        projection[threshold] = {
            "planned_threshold": threshold,
            "projected_committed_targets": committed,
            "projected_step": step,
            "overshoot_targets": committed - threshold,
        }
    return projection


def update_arithmetic(budget_valid_targets: int, global_batch_valid_targets: int) -> dict[str, int]:
    """Exact update count for a hard budget: full updates plus one masked final update."""
    if budget_valid_targets <= 0 or global_batch_valid_targets <= 0:
        raise CadenceError("update arithmetic requires a positive budget and global batch")
    full, final = divmod(budget_valid_targets, global_batch_valid_targets)
    return {
        "budget_valid_targets": budget_valid_targets,
        "global_batch_valid_targets": global_batch_valid_targets,
        "full_updates": full,
        "final_update_targets": final,
        "total_updates": full + (1 if final else 0),
    }
