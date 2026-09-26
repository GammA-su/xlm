"""Bounded science-v1 checkpoint retention over verified artifact ids (P35 M3).

``latest_two_recovery_plus_pinned_v1`` keeps:

* every pinned scientific milestone;
* the two most recent rolling recovery states;
* the last good (most recent verified) state, whatever its role;
* every explicitly protected reference (fork parent, declared baseline);
* every state an unresolved evaluation event still needs for exact rescoring
  (matched by model-state digest, never by step or filename).

Everything else owned by the run is retirable: older rolling recovery states,
and superseded lost-lineage publications. The decision is a pure, deterministic
function of verified records; it lists a reason for every kept and every
retired artifact so the dependency is explicit and inspectable. It never looks
at filenames or directory listings, and it never runs before the replacement
checkpoint is published and verified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

POLICY = "latest_two_recovery_plus_pinned_v1"
KEEP_RECOVERY = 2

MILESTONE = "milestone"
RECOVERY = "recovery"
SUPERSEDED = "superseded"


@dataclass(frozen=True)
class RetentionCandidate:
    """One verified, run-owned checkpoint that retention may reason about."""

    artifact_id: str
    role: str
    committed: int
    step: int
    attempt: int
    model_state_digest: str
    manifest_sha256: str

    def order_key(self) -> tuple[int, int, int]:
        return (self.committed, self.step, self.attempt)


@dataclass
class RetentionDecision:
    """Keep/retire verdicts with reasons; a pure function of its inputs."""

    policy: str
    keep: dict[str, list[str]] = field(default_factory=dict)
    retire: dict[str, str] = field(default_factory=dict)
    evaluation_dependencies: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "keep": {k: list(v) for k, v in sorted(self.keep.items())},
            "retire": dict(sorted(self.retire.items())),
            "evaluation_dependencies": {
                k: list(v) for k, v in sorted(self.evaluation_dependencies.items())
            },
        }


def decide_retention(
    candidates: Sequence[RetentionCandidate],
    *,
    protected: Mapping[str, str],
    evaluation_dependencies: Mapping[str, Sequence[str]],
    policy: str = POLICY,
) -> RetentionDecision:
    """Decide which verified candidates are kept and which may be retired.

    ``evaluation_dependencies`` maps a model-state digest to the unresolved
    evaluation events (due, failed, partial or interrupted) that may still
    need to rescore exactly that state.
    """
    if policy != POLICY:
        raise ValueError(f"unsupported retention policy '{policy}'")
    ids = [c.artifact_id for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("retention candidates must be unique verified artifact ids")
    decision = RetentionDecision(
        policy=policy,
        evaluation_dependencies={d: sorted(e) for d, e in evaluation_dependencies.items()},
    )

    def keep(candidate: RetentionCandidate, reason: str) -> None:
        decision.keep.setdefault(candidate.artifact_id, []).append(reason)

    live = [c for c in candidates if c.role in (MILESTONE, RECOVERY)]
    if live:
        keep(max(live, key=RetentionCandidate.order_key), "last_good_state")
    recovery = sorted(
        (c for c in candidates if c.role == RECOVERY),
        key=RetentionCandidate.order_key,
        reverse=True,
    )
    for rank, candidate in enumerate(recovery[:KEEP_RECOVERY], start=1):
        keep(candidate, f"latest_recovery_{rank}")
    for candidate in candidates:
        if candidate.role == MILESTONE:
            keep(candidate, "pinned_milestone")
        if candidate.artifact_id in protected:
            keep(candidate, f"reference:{protected[candidate.artifact_id]}")
        events = evaluation_dependencies.get(candidate.model_state_digest)
        if events:
            keep(candidate, "evaluation_dependency:" + ",".join(sorted(events)))
    for candidate in candidates:
        if candidate.artifact_id in decision.keep:
            continue
        if candidate.role == SUPERSEDED:
            decision.retire[candidate.artifact_id] = "superseded_lost_lineage"
        elif candidate.role == RECOVERY:
            decision.retire[candidate.artifact_id] = "rolling_recovery_beyond_latest_two"
        else:  # pragma: no cover - milestones are always kept above
            raise AssertionError(f"unexpected retention role {candidate.role}")
    return decision
