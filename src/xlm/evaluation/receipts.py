"""Science-v1 evaluation event state, attempt receipts and completeness.

Two layers, one identity:

* The **ledger** is committed training state. It lives in the checkpoint's
  ``science.json`` and records, per planned event, whether its threshold was
  crossed, at which committed count and model state, and which attempts exist.
  It is only changed at committed boundaries.
* **Attempts** are immutable artifacts in the run's existing artifact store:
  one ``started`` record published before scoring and one ``outcome`` record
  after it. They are never overwritten. A started attempt without an outcome
  was interrupted, and stays visible as such.

The canonical receipt of an event is its *first* complete attempt bound to the
model state recorded at the crossing (``first_complete_attempt_v1``). A later
deliberate rerun is kept as history but never replaces it, so rerunning cannot
select a preferred number. A missing, failed, partial or interrupted event is
never a score: it makes the run evaluation-incomplete.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import canonical_json, identity_digest
from xlm.evaluation.cadence import EvaluationPlan, PlannedEvent

RECEIPT_VERSION = "xlm-eval-receipt-v1"
LEDGER_VERSION = 1
CANONICAL_RULE = "first_complete_attempt_v1"
RETRY_POLICY = "retry_on_restart_v1"
#: Attempts per event on the current model-state lineage, including the first.
MAX_ATTEMPTS_PER_EVENT = 3
#: Upper bound on attempt numbers probed per event across all lineages.
MAX_ATTEMPT_NUMBER = 64
ATTEMPT_KIND = "evaluations"
MAX_RECORD_BYTES = 64 * 1024**2


class EvaluationLedgerError(ValueError):
    """Saved evaluation state is inconsistent with this run's plan."""


class EventStatus(StrEnum):
    PLANNED = "planned"
    DUE = "due"
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class AttemptOutcome(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class EventRecord:
    """Committed state of one planned event."""

    event: PlannedEvent
    due: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    canonical: str | None = None

    @property
    def event_id(self) -> str:
        return self.event.event_id

    def lineage_attempts(self) -> list[dict[str, Any]]:
        """Attempts of exactly the computation bound at this event's crossing.

        Attempts from a lost lineage (a replay that reached a numerically
        different state) or a different evaluator are history, never canonical.
        """
        if self.due is None:
            return []
        identity = self.due["computation_identity"]
        return [a for a in self.attempts if a["computation_identity"] == identity]

    @property
    def status(self) -> EventStatus:
        if self.canonical is not None:
            return EventStatus.COMPLETE
        if self.due is None:
            return EventStatus.PLANNED
        current = self.lineage_attempts()
        if not current:
            return EventStatus.DUE
        return EventStatus(current[-1]["status"])

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.event.to_dict(),
            "status": self.status.value,
            "due": dict(self.due) if self.due is not None else None,
            "attempts": [dict(a) for a in self.attempts],
            "canonical_attempt": self.canonical,
        }


@dataclass(frozen=True)
class Completeness:
    """Whether every planned event of a run has a canonical complete receipt."""

    complete: bool
    counts: dict[str, int]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"complete": self.complete, "counts": self.counts, "reasons": list(self.reasons)}


class EvaluationLedger:
    """Per-run evaluation event state, persisted in ``science.json``.

    ``evaluator_digests`` binds each tier to the identity of the evaluator the
    run was planned with; a resumed run with a different evaluator is refused.
    """

    def __init__(self, plan: EvaluationPlan, evaluator_digests: Mapping[str, str]) -> None:
        planned = sorted({e.tier.value for e in plan.events})
        if sorted(evaluator_digests) != planned:
            raise EvaluationLedgerError(
                f"evaluators {sorted(evaluator_digests)} do not match planned tiers {planned}"
            )
        self.plan = plan
        self.evaluator_digests = dict(sorted(evaluator_digests.items()))
        self.records: dict[str, EventRecord] = {e.event_id: EventRecord(e) for e in plan.events}

    def ordered(self) -> list[EventRecord]:
        return [self.records[e.event_id] for e in self.plan.events]

    def handled(self) -> list[str]:
        return [r.event_id for r in self.ordered() if r.due is not None]

    @property
    def untouched(self) -> bool:
        return all(r.due is None and not r.attempts for r in self.records.values())

    def mark_due(
        self,
        event: PlannedEvent,
        *,
        committed: int,
        step: int,
        digest: str,
        computation_identity: str,
    ) -> None:
        record = self.records[event.event_id]
        if record.due is not None:
            raise EvaluationLedgerError(f"event '{event.event_id}' already crossed")
        if committed < event.threshold:
            raise EvaluationLedgerError(f"event '{event.event_id}' is not due at {committed}")
        record.due = {
            "actual_committed_targets": committed,
            "step": step,
            "model_state_digest": digest,
            "computation_identity": computation_identity,
        }

    def adopt_attempt(self, record: EventRecord, summary: Mapping[str, Any]) -> None:
        """Add or update one attempt summary, then apply the canonical rule."""
        for existing in record.attempts:
            if existing["number"] == summary["number"]:
                existing.update(summary)
                break
        else:
            record.attempts.append(dict(summary))
            record.attempts.sort(key=lambda a: a["number"])
        if record.canonical is None and record.due is not None:
            for attempt in record.lineage_attempts():
                if attempt["status"] == AttemptOutcome.COMPLETE.value:
                    record.canonical = attempt["outcome_artifact"]
                    break

    def completeness(self) -> Completeness:
        counts: dict[str, int] = {}
        reasons: list[str] = []
        for record in self.ordered():
            status = record.status
            counts[status.value] = counts.get(status.value, 0) + 1
            if status is not EventStatus.COMPLETE:
                where = (
                    f"at C={record.due['actual_committed_targets']}"
                    if record.due is not None
                    else "threshold not reached"
                )
                reasons.append(f"{record.event_id}: {status.value} ({where})")
        return Completeness(not reasons, dict(sorted(counts.items())), tuple(reasons))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": LEDGER_VERSION,
            "plan": self.plan.to_dict(),
            "plan_digest": self.plan.digest(),
            "evaluator_digests": dict(self.evaluator_digests),
            "canonical_rule": CANONICAL_RULE,
            "retry_policy": RETRY_POLICY,
            "max_attempts_per_event": MAX_ATTEMPTS_PER_EVENT,
            "events": [r.to_dict() for r in self.ordered()],
            "completeness": self.completeness().to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EvaluationLedger:
        if payload.get("version") != LEDGER_VERSION:
            raise EvaluationLedgerError("unsupported evaluation ledger version")
        if (
            payload.get("canonical_rule") != CANONICAL_RULE
            or payload.get("retry_policy") != RETRY_POLICY
        ):
            raise EvaluationLedgerError("saved ledger uses a different canonical/retry rule")
        plan = EvaluationPlan.from_dict(payload["plan"])
        if plan.digest() != payload.get("plan_digest"):
            raise EvaluationLedgerError("saved evaluation plan digest does not match its plan")
        ledger = cls(plan, payload["evaluator_digests"])
        saved = {entry["event_id"]: entry for entry in payload["events"]}
        if list(saved) != [e.event_id for e in plan.events]:
            raise EvaluationLedgerError("saved events differ from the saved plan")
        for event_id, entry in saved.items():
            record = ledger.records[event_id]
            record.due = dict(entry["due"]) if entry["due"] is not None else None
            record.attempts = [dict(a) for a in entry["attempts"]]
            record.canonical = entry["canonical_attempt"]
            if record.status.value != entry["status"]:
                raise EvaluationLedgerError(f"saved status of '{event_id}' is inconsistent")
        return ledger


_SLUG = re.compile(r"[^A-Za-z0-9_.-]")


class AttemptStore:
    """Append-only attempt records published through the existing artifact store."""

    def __init__(
        self,
        store: Any,
        run_key: str,
        *,
        producer_code_hash: str,
        dependency_hash: str,
        record_artifact: Callable[[str, Path], None] | None = None,
    ) -> None:
        self.store = store
        self.run_key = run_key
        self.producer_code_hash = producer_code_hash
        self.dependency_hash = dependency_hash
        self.record_artifact = record_artifact

    def artifact_id(self, event_id: str, number: int, phase: str) -> str:
        if phase not in ("started", "outcome"):
            raise ValueError(f"unknown attempt phase '{phase}'")
        slug = _SLUG.sub("-", event_id.replace("@", "-t"))
        return f"ev_{self.run_key[:16]}_{slug}_a{number:03d}_{phase}"

    def path(self, artifact_id: str) -> Path:
        return Path(self.store.paths.root) / ATTEMPT_KIND / artifact_id

    def publish(
        self,
        event_id: str,
        number: int,
        phase: str,
        record: Mapping[str, Any],
        extra_files: Mapping[str, bytes] | None = None,
    ) -> str:
        artifact_id = self.artifact_id(event_id, number, phase)
        files: dict[str, bytes] = {"attempt.json": canonical_json(record)}
        files.update(extra_files or {})
        destination = self.store.publish_artifact(
            artifact_id=artifact_id,
            kind=ATTEMPT_KIND,
            files=files,
            producer_code_hash=self.producer_code_hash,
            dependency_hash=self.dependency_hash,
            resolved_config_hash=str(record["computation_identity"]),
            metadata={
                "receipt_version": RECEIPT_VERSION,
                "event_id": event_id,
                "attempt": number,
                "phase": phase,
                "run_key": self.run_key,
            },
        )
        if self.record_artifact is not None:
            self.record_artifact(artifact_id, Path(destination))
        return artifact_id

    def read(self, artifact_id: str) -> dict[str, Any] | None:
        path = self.path(artifact_id)
        if not path.is_dir():
            return None
        self.store.verify_artifact(path)
        raw = (path / "attempt.json").read_bytes()
        if len(raw) > MAX_RECORD_BYTES:
            raise EvaluationLedgerError(f"attempt record {artifact_id} exceeds its byte bound")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("receipt_version") != RECEIPT_VERSION:
            raise EvaluationLedgerError(f"attempt record {artifact_id} is not a {RECEIPT_VERSION}")
        return value

    def history(self, event_id: str) -> list[dict[str, Any]]:
        """Every attempt ever published for this event of this run, in number order."""
        summaries: list[dict[str, Any]] = []
        for number in range(1, MAX_ATTEMPT_NUMBER + 1):
            started = self.read(self.artifact_id(event_id, number, "started"))
            outcome = self.read(self.artifact_id(event_id, number, "outcome"))
            if started is None and outcome is None:
                break
            source = outcome if outcome is not None else started
            assert source is not None
            summaries.append(
                {
                    "number": number,
                    "started_artifact": self.artifact_id(event_id, number, "started")
                    if started is not None
                    else None,
                    "outcome_artifact": self.artifact_id(event_id, number, "outcome")
                    if outcome is not None
                    else None,
                    "status": (
                        outcome["status"] if outcome is not None else EventStatus.INTERRUPTED.value
                    ),
                    "computation_identity": source["computation_identity"],
                    "model_state_digest": source["model_state"]["state_digest"],
                    "actual_committed_targets": source["actual_committed_targets"],
                    "failure": outcome.get("failure") if outcome is not None else None,
                }
            )
        else:
            raise EvaluationLedgerError(f"event '{event_id}' exceeds {MAX_ATTEMPT_NUMBER} attempts")
        return summaries


def receipt_identity(record: Mapping[str, Any]) -> str:
    """Identity of one outcome receipt: everything except its own identity field."""
    return identity_digest({k: v for k, v in record.items() if k != "receipt_identity"})
