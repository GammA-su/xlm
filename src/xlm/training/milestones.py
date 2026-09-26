"""Science-v1 checkpoint events at absolute committed-target thresholds (P35 M3).

Checkpoint events share the M2 planner (:mod:`xlm.evaluation.cadence`): an
event is due at the first committed boundary whose count reaches its absolute
threshold, and training never shortens or splits an update to land on it. At
one boundary the order is fixed: evaluation crossings are recorded, the
boundary's single checkpoint is published (and therefore owes those
evaluations), then evaluation attempts run.

Each publication is a *record* bound to the run, the checkpoint events it
satisfies (planned threshold and actual committed count), the optimizer step,
the model-state digest, the committed data-cursor digest, the scientific
identity, the ArtifactStore id, its verified manifest hash and a creation
attempt number. Records live in ``science.json`` (committed training state);
immutable receipts published next to the checkpoint make the outcome of every
attempt durable even when the process dies before the next checkpoint.

Publication never replaces an artifact. If a replayed boundary finds an earlier
publication of the same event it is adopted only when run, counters, data
cursor and model-state digest are identical; otherwise the replay publishes
the next attempt number and the earlier one is recorded as superseded (lost
lineage). A failed publication is recorded as FAILED, receipted, and stops
training; nothing is retired before a replacement is published and verified.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import canonical_json, identity_digest, validate_component
from xlm.artifacts.store import compute_file_sha256
from xlm.evaluation.cadence import (
    CheckpointPlan,
    CheckpointRole,
    PlannedCheckpoint,
    build_checkpoint_plan,
    rebase_checkpoint_plan,
)
from xlm.evaluation.state_digest import model_state_digest, state_dict_digest

CHECKPOINT_LEDGER_VERSION = 1
CHECKPOINT_IDENTITY_VERSION = "xlm-checkpoint-identity-v1"
CHECKPOINT_RECEIPT_VERSION = "xlm-checkpoint-receipt-v1"
CHECKPOINT_RECEIPT_KIND = "checkpoint_events"
CHECKPOINT_KIND = "checkpoints"
RETENTION_POLICY = "latest_two_recovery_plus_pinned_v1"
RESCORE_POLICY = "retained_exact_checkpoint_v1"
MAX_PUBLICATION_ATTEMPTS = 16
MAX_RECORDS = 4096
MAX_HISTORY = 4096
MAX_RECEIPT_BYTES = 4 * 1024**2
MAX_FAILURE_MESSAGE = 2000


class CheckpointEventError(RuntimeError):
    """A checkpoint event cannot be recorded or published safely."""


class CheckpointPublicationError(CheckpointEventError):
    """A checkpoint was not published; the last good state is untouched."""


class CheckpointLedgerError(ValueError):
    """Saved checkpoint-event state is inconsistent with this run's plan."""


class PublicationStatus(StrEnum):
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    RETIRED = "retired"


def cursor_digest(batcher: Any) -> str:
    """Identity of the committed data cursor, stable across a JSON checkpoint round trip."""
    return identity_digest(json.dumps(batcher.get_state(), sort_keys=True, default=repr))


def _failure(exc: BaseException) -> dict[str, Any]:
    return {"type": type(exc).__name__, "message": str(exc)[:MAX_FAILURE_MESSAGE]}


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CheckpointRecord:
    """One checkpoint publication attempt and the committed state it binds."""

    artifact_id: str
    attempt: int
    slug: str
    events: list[str]
    planned_thresholds: list[int]
    role: str
    reason: str
    actual_committed_targets: int
    step: int
    identity: dict[str, Any]
    status: str
    manifest_sha256: str | None = None
    content_hash: str | None = None
    payload_bytes: int | None = None
    failure: dict[str, Any] | None = None
    retired: dict[str, Any] | None = None
    superseded: list[str] = field(default_factory=list)
    adopted_existing: bool = False
    receipt_errors: list[str] = field(default_factory=list)

    @property
    def model_state_digest(self) -> str:
        return str(self.identity["model_state_digest"])

    @property
    def identity_digest(self) -> str:
        return identity_digest(self.identity)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["identity_digest"] = self.identity_digest
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CheckpointRecord:
        data = {k: v for k, v in payload.items() if k != "identity_digest"}
        record = cls(**data)
        if record.status not in {s.value for s in PublicationStatus}:
            raise CheckpointLedgerError(f"unknown checkpoint record status '{record.status}'")
        if payload.get("identity_digest") != record.identity_digest:
            raise CheckpointLedgerError(f"checkpoint record {record.artifact_id} identity mismatch")
        return record


class CheckpointLedger:
    """Checkpoint events and publication records of one run, persisted in ``science.json``."""

    def __init__(self, plan: CheckpointPlan, protected: Mapping[str, str] | None = None) -> None:
        self.plan = plan
        #: Explicit parent/baseline references retention must never retire.
        self.protected: dict[str, str] = dict(sorted((protected or {}).items()))
        #: event id -> {"actual_committed_targets", "step", "record"}
        self.due: dict[str, dict[str, Any]] = {}
        self.records: dict[str, CheckpointRecord] = {}
        #: Durable attempts learned from receipts or lost lineages (inspectable history).
        self.history: list[dict[str, Any]] = []
        self.retention_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ queries

    def handled(self) -> list[str]:
        return list(self.due)

    def add_record(self, record: CheckpointRecord) -> None:
        if record.artifact_id in self.records:
            raise CheckpointLedgerError(f"checkpoint record {record.artifact_id} already exists")
        if len(self.records) >= MAX_RECORDS:
            raise CheckpointLedgerError("checkpoint record log exceeds its bound")
        self.records[record.artifact_id] = record

    def note(self, entry: Mapping[str, Any]) -> None:
        if len(self.history) >= MAX_HISTORY:
            raise CheckpointLedgerError("checkpoint history exceeds its bound")
        if dict(entry) not in self.history:
            self.history.append(dict(entry))

    def published(self) -> list[CheckpointRecord]:
        return [r for r in self.records.values() if r.status == PublicationStatus.PUBLISHED]

    def record_at(
        self, committed: int, step: int, digest: str, cursor: str
    ) -> CheckpointRecord | None:
        """The published record of exactly this committed state, if any (deduplication)."""
        for record in self.published():
            if (
                record.actual_committed_targets == committed
                and record.step == step
                and record.identity["model_state_digest"] == digest
                and record.identity["data_cursor_digest"] == cursor
            ):
                return record
        return None

    def records_for_state(self, digest: str) -> list[CheckpointRecord]:
        return [r for r in self.records.values() if r.identity["model_state_digest"] == digest]

    def last_good(self) -> CheckpointRecord | None:
        published = self.published()
        if not published:
            return None
        return max(published, key=lambda r: (r.actual_committed_targets, r.step, r.attempt))

    def event_status(self, event_id: str) -> str:
        entry = self.due.get(event_id)
        if entry is None:
            return "planned"
        return self.records[entry["record"]].status

    def summary(self) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        for event in self.plan.events:
            status = self.event_status(event.event_id)
            statuses[status] = statuses.get(status, 0) + 1
        last = self.last_good()
        return {
            "event_statuses": dict(sorted(statuses.items())),
            "published_records": len(self.published()),
            "last_good": last.artifact_id if last is not None else None,
        }

    # ----------------------------------------------------------- persistence

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": CHECKPOINT_LEDGER_VERSION,
            "plan": self.plan.to_dict(),
            "plan_digest": self.plan.digest(),
            "retention_policy": RETENTION_POLICY,
            "rescore_policy": RESCORE_POLICY,
            "protected_references": dict(self.protected),
            "events": [
                {
                    **event.to_dict(),
                    "status": self.event_status(event.event_id),
                    "due": dict(self.due[event.event_id]) if event.event_id in self.due else None,
                }
                for event in self.plan.events
            ],
            "records": [r.to_dict() for r in self.records.values()],
            "history": [dict(h) for h in self.history],
            "retention_log": [dict(entry) for entry in self.retention_log],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CheckpointLedger:
        if payload.get("version") != CHECKPOINT_LEDGER_VERSION:
            raise CheckpointLedgerError("unsupported checkpoint ledger version")
        if (
            payload.get("retention_policy") != RETENTION_POLICY
            or payload.get("rescore_policy") != RESCORE_POLICY
        ):
            raise CheckpointLedgerError("saved ledger uses a different retention/rescore policy")
        plan = CheckpointPlan.from_dict(payload["plan"])
        if plan.digest() != payload.get("plan_digest"):
            raise CheckpointLedgerError("saved checkpoint plan digest does not match its plan")
        ledger = cls(plan, payload.get("protected_references") or {})
        for entry in payload["records"]:
            ledger.add_record(CheckpointRecord.from_dict(entry))
        events = {entry["event_id"]: entry for entry in payload["events"]}
        if list(events) != [e.event_id for e in plan.events]:
            raise CheckpointLedgerError("saved checkpoint events differ from the saved plan")
        for event_id, entry in events.items():
            if entry["due"] is not None:
                if entry["due"]["record"] not in ledger.records:
                    raise CheckpointLedgerError(f"event '{event_id}' names an unknown record")
                ledger.due[event_id] = dict(entry["due"])
            if ledger.event_status(event_id) != entry["status"]:
                raise CheckpointLedgerError(f"saved status of '{event_id}' is inconsistent")
        ledger.history = [dict(h) for h in payload.get("history", [])]
        ledger.retention_log = [dict(e) for e in payload.get("retention_log", [])]
        return ledger


class CheckpointController:
    """Publishes the checkpoint events of one run at committed boundaries."""

    def __init__(self, plan: CheckpointPlan, *, protected_references: Sequence[str] = ()) -> None:
        for reference in protected_references:
            validate_component(reference)
        self.plan = plan
        self.protected_references = tuple(protected_references)
        self._science: Any = None
        self._run: dict[str, Any] = {}
        self._manager: Any = None
        self._reconciled: CheckpointLedger | None = None

    # ------------------------------------------------------------------ binding

    def attach(self, science: Any) -> None:
        if not science.policy.is_science:
            raise CheckpointEventError("a checkpoint cadence is science-v1 functionality")
        protected = {ref: "declared_reference" for ref in self.protected_references}
        if science.checkpoints is None:
            science.checkpoints = CheckpointLedger(self.plan, protected)
        elif science.checkpoints.plan.digest() != self.plan.digest():
            raise CheckpointEventError("attached checkpoint ledger belongs to another plan")
        self._science = science

    @property
    def ledger(self) -> CheckpointLedger:
        assert self._science is not None and self._science.checkpoints is not None
        return self._science.checkpoints  # type: ignore[no-any-return]

    def bind_trainer(self, trainer: Any) -> None:
        if self._science is not trainer.science:
            self.attach(trainer.science)
        self.plan = self.ledger.plan
        if self.plan.origin_committed_targets > trainer.committed_valid_targets:
            raise CheckpointEventError("checkpoint plan starts after the trainer's state")
        for reference in self.protected_references:
            self.ledger.protected.setdefault(reference, "declared_reference")
        if trainer.parent_checkpoint_id:
            self.ledger.protected.setdefault(str(trainer.parent_checkpoint_id), "fork_parent")
        manager = trainer.checkpoint_manager
        execution = manager.execution
        run_key = identity_digest(
            {
                "run_id": trainer.run_id,
                "plan_id": trainer.plan_id,
                "checkpoint_plan": self.plan.digest(),
            }
        )
        self._manager = manager
        self._run = {
            "run_id": trainer.run_id,
            "plan_id": trainer.plan_id,
            "run_key": run_key,
            "execution_hash": execution["envelope"]["execution_hash"] if execution else None,
            "producer_code_hash": execution["envelope"]["code_hash"]
            if execution
            else identity_digest({"scope": "domain-api", "receipt": CHECKPOINT_RECEIPT_VERSION}),
            "dependency_hash": execution["envelope"]["dependency_hash"]
            if execution
            else identity_digest({"scope": "domain-api"}),
        }
        self.settle()

    def checkpoint_dir(self, artifact_id: str) -> Path:
        assert self._manager is not None
        return Path(self._manager.store.paths.root) / CHECKPOINT_KIND / artifact_id

    # ------------------------------------------------------------- identities

    def event_identity(self, event: PlannedCheckpoint) -> str:
        return identity_digest(
            {
                "version": CHECKPOINT_IDENTITY_VERSION,
                "run_key": self._run["run_key"],
                "checkpoint_plan": self.plan.digest(),
                "event_id": event.event_id,
            }
        )

    def _identity(
        self,
        trainer: Any,
        events: Sequence[PlannedCheckpoint],
        *,
        artifact_id: str,
        attempt: int,
        digest: str,
        cursor: str,
    ) -> dict[str, Any]:
        science = trainer.science
        return {
            "version": CHECKPOINT_IDENTITY_VERSION,
            "run": {k: self._run[k] for k in ("run_id", "plan_id", "run_key", "execution_hash")},
            "checkpoint_events": [
                {
                    "event_id": event.event_id,
                    "event_identity": self.event_identity(event),
                    "planned_threshold": event.threshold,
                    "role": event.role.value,
                }
                for event in events
            ],
            "actual_committed_targets": trainer.committed_valid_targets,
            "processed_valid_targets": trainer.processed_valid_targets,
            "step": trainer.step,
            "model_state_digest": digest,
            "data_cursor_digest": cursor,
            "science_identity": identity_digest(
                {
                    "policy": science.policy.identity(),
                    "checkpoint_plan": self.plan.digest(),
                    "evaluation_plan": science.evaluation.plan.digest()
                    if science.evaluation is not None
                    else None,
                }
            ),
            "artifact_id": artifact_id,
            "creation_attempt": attempt,
        }

    # ------------------------------------------------------------- boundaries

    def at_boundary(self, trainer: Any) -> Path | None:
        """Publish one checkpoint for every event first crossed at this committed state."""
        self.settle()
        due = self.plan.due(trainer.committed_valid_targets, handled=self.ledger.handled())
        if not due:
            return None
        return self._publish(trainer, due, reason="planned")

    def save_terminal(self, trainer: Any, reason: str) -> Path:
        """A final/interrupted/cancelled/time-limit state; never a duplicate of this boundary."""
        trainer._require_committed_boundary()
        self.settle()
        digest = model_state_digest(trainer.model)
        cursor = cursor_digest(trainer.batcher)
        existing = self.ledger.record_at(
            trainer.committed_valid_targets, trainer.step, digest, cursor
        )
        if existing is not None and self.checkpoint_dir(existing.artifact_id).is_dir():
            return self.checkpoint_dir(existing.artifact_id)
        return self._publish(trainer, [], reason=reason)

    def _publish(self, trainer: Any, events: Sequence[PlannedCheckpoint], *, reason: str) -> Path:
        trainer._require_committed_boundary()
        committed, step = trainer.committed_valid_targets, trainer.step
        digest = model_state_digest(trainer.model)
        cursor = cursor_digest(trainer.batcher)
        milestone = any(e.role is CheckpointRole.MILESTONE for e in events)
        role = CheckpointRole.MILESTONE if milestone else CheckpointRole.RECOVERY
        slug = f"t{events[0].threshold}" if events else f"{reason}-c{committed}"
        attempt, superseded, adopted_dir = self._resolve_attempt(
            trainer, slug, committed=committed, step=step, digest=digest, cursor=cursor
        )
        artifact_id = self._artifact_id(trainer.run_id, slug, attempt)
        record = CheckpointRecord(
            artifact_id=artifact_id,
            attempt=attempt,
            slug=slug,
            events=[e.event_id for e in events],
            planned_thresholds=[e.threshold for e in events],
            role=role.value,
            reason=reason,
            actual_committed_targets=committed,
            step=step,
            identity=self._identity(
                trainer,
                events,
                artifact_id=artifact_id,
                attempt=attempt,
                digest=digest,
                cursor=cursor,
            ),  # fmt: skip
            status=PublicationStatus.PUBLISHING.value,
            superseded=superseded,
        )
        for lost in superseded:
            self.ledger.note({"superseded": lost, "by": artifact_id, "reason": "lost_lineage"})
        self.ledger.add_record(record)
        for event in events:
            self.ledger.due[event.event_id] = {
                "actual_committed_targets": committed,
                "step": step,
                "record": artifact_id,
            }
        if adopted_dir is not None:
            # An earlier process published exactly this state before it could
            # record it; the durable artifact is adopted, never republished.
            record.adopted_existing = True
            self._finish(record, adopted_dir)
            return adopted_dir
        try:
            path = trainer._save_checkpoint(artifact_id)
            # Authority is established by verifying the published bytes before
            # anything (retention, evaluation, resume) is allowed to depend on it.
            self._finish(record, Path(path))
        except BaseException as exc:
            record.status = PublicationStatus.FAILED.value
            record.failure = _failure(exc)
            self._receipt(record, "failed")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CheckpointPublicationError(
                f"checkpoint {artifact_id} for {record.events or reason} was not published: {exc}"
            ) from exc
        return Path(path)

    def _finish(self, record: CheckpointRecord, path: Path) -> None:
        assert self._manager is not None
        manifest = self._manager.store.verify_artifact(path)
        if manifest.artifact_id != record.artifact_id or manifest.kind != CHECKPOINT_KIND:
            raise CheckpointPublicationError(
                "published checkpoint identity differs from its record"
            )
        record.status = PublicationStatus.PUBLISHED.value
        record.manifest_sha256 = compute_file_sha256(path / "manifest.json")
        record.content_hash = manifest.content_hash
        record.payload_bytes = sum(entry.size_bytes for entry in manifest.files)
        if self._manager.ledger.get_artifact(record.artifact_id) is None:
            self._manager.ledger.record_artifact(
                artifact_id=record.artifact_id,
                kind=CHECKPOINT_KIND,
                path=path,
                manifest_json=manifest.model_dump_json(),
            )
        self._receipt(record, "published")

    @staticmethod
    def _artifact_id(run_id: str, slug: str, attempt: int) -> str:
        artifact_id = f"{run_id}_ckpt-{slug}-a{attempt:03d}"
        validate_component(artifact_id)
        return artifact_id

    def _matches(
        self, path: Path, trainer: Any, *, committed: int, step: int, digest: str, cursor: str
    ) -> bool:
        """Whether a durable checkpoint is exactly this run's current committed state."""
        import torch

        assert self._manager is not None
        try:
            self._manager.store.verify_artifact(path)
            meta = json.loads((path / "checkpoint_meta.json").read_text(encoding="utf-8"))
            if (
                meta.get("run_id") != trainer.run_id
                or meta.get("plan_id") != trainer.plan_id
                or meta.get("committed_valid_targets") != committed
                or meta.get("step") != step
            ):
                return False
            data_state = json.loads((path / "data_state.json").read_text(encoding="utf-8"))
            if identity_digest(json.dumps(data_state, sort_keys=True, default=repr)) != cursor:
                return False
            state = torch.load(path / "model.pt", map_location="cpu", weights_only=True)
            return state_dict_digest(state) == digest
        except (OSError, ValueError, KeyError, RuntimeError):
            return False

    def _resolve_attempt(
        self, trainer: Any, slug: str, *, committed: int, step: int, digest: str, cursor: str
    ) -> tuple[int, list[str], Path | None]:
        """Next free attempt number, earlier lost-lineage attempts, or an exact durable match."""
        used = {r.attempt for r in self.ledger.records.values() if r.slug == slug}
        used |= {int(h["attempt"]) for h in self.ledger.history if h.get("slug") == slug}
        superseded: list[str] = []
        for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
            artifact_id = self._artifact_id(trainer.run_id, slug, attempt)
            path = self.checkpoint_dir(artifact_id)
            receipts = [self._read_receipt(slug, attempt, p) for p in ("published", "failed")]
            if attempt not in used and not path.exists() and not any(receipts):
                return attempt, superseded, None
            if path.is_dir() and artifact_id not in self.ledger.records:
                if self._matches(
                    path, trainer, committed=committed, step=step, digest=digest, cursor=cursor
                ):
                    return attempt, superseded, path
                superseded.append(artifact_id)
        raise CheckpointPublicationError(
            f"checkpoint '{slug}' exceeds {MAX_PUBLICATION_ATTEMPTS} publication attempts"
        )

    # ---------------------------------------------------------------- resume

    def settle(self) -> None:
        """Finalize records a restored ledger still shows as publishing.

        A checkpoint's own ``science.json`` necessarily shows its record as
        publishing (it cannot contain its own manifest hash). After a resume the
        durable, verified artifact is the proof of publication. A publishing
        record without a verifiable artifact is a failed publication.
        """
        for record in list(self.ledger.records.values()):
            if record.status != PublicationStatus.PUBLISHING:
                continue
            path = self.checkpoint_dir(record.artifact_id)
            if path.is_dir():
                self._finish(record, path)
            else:
                record.status = PublicationStatus.FAILED.value
                record.failure = {"type": "MissingPublication", "message": "no durable artifact"}
        if self._reconciled is not self.ledger:
            # A resume may replace the ledger object after binding (the queue
            # restores into an already constructed trainer): reconcile each once.
            self._reconcile_receipts()
            self._reconciled = self.ledger

    # --------------------------------------------------------------- receipts

    def _receipt_id(self, slug: str, attempt: int, phase: str) -> str:
        receipt_id = f"ck_{self._run['run_key'][:16]}_{slug}_a{attempt:03d}_{phase}"
        validate_component(receipt_id)
        return receipt_id

    def _read_receipt(self, slug: str, attempt: int, phase: str) -> dict[str, Any] | None:
        assert self._manager is not None
        path = (
            Path(self._manager.store.paths.root)
            / CHECKPOINT_RECEIPT_KIND
            / self._receipt_id(slug, attempt, phase)
        )
        if not path.is_dir():
            return None
        self._manager.store.verify_artifact(path)
        raw = (path / "receipt.json").read_bytes()
        if len(raw) > MAX_RECEIPT_BYTES:
            raise CheckpointLedgerError("checkpoint receipt exceeds its byte bound")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or value.get("receipt_version") != CHECKPOINT_RECEIPT_VERSION
        ):
            raise CheckpointLedgerError("not a checkpoint receipt")
        return value

    def _receipt(self, record: CheckpointRecord, phase: str) -> None:
        """Best-effort immutable receipt; its absence never hides the in-memory outcome."""
        assert self._manager is not None
        try:
            receipt_id = self._receipt_id(record.slug, record.attempt, phase)
            if self._read_receipt(record.slug, record.attempt, phase) is not None:
                return  # an earlier process already made this outcome durable
            payload = {
                "receipt_version": CHECKPOINT_RECEIPT_VERSION,
                "phase": phase,
                "run": {
                    k: self._run[k] for k in ("run_id", "plan_id", "run_key", "execution_hash")
                },
                "checkpoint_plan_digest": self.plan.digest(),
                "record": record.to_dict(),
                "written_at": _now(),
            }
            destination = self._manager.store.publish_artifact(
                artifact_id=receipt_id,
                kind=CHECKPOINT_RECEIPT_KIND,
                files={"receipt.json": canonical_json(payload)},
                producer_code_hash=self._run["producer_code_hash"],
                dependency_hash=self._run["dependency_hash"],
                resolved_config_hash=record.identity_digest,
                metadata={
                    "receipt_version": CHECKPOINT_RECEIPT_VERSION,
                    "checkpoint_artifact_id": record.artifact_id,
                    "phase": phase,
                    "run_key": self._run["run_key"],
                },
            )
            self._manager.ledger.record_artifact(
                artifact_id=receipt_id,
                kind=CHECKPOINT_RECEIPT_KIND,
                path=Path(destination),
                manifest_json=self._manager.store.load_manifest(destination).model_dump_json(),
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never masking the primary outcome
            record.receipt_errors.append(f"{phase}: {type(exc).__name__}: {exc}"[:500])

    def _reconcile_receipts(self) -> None:
        """Make durable attempts of earlier processes inspectable in the ledger history."""
        slugs = {f"t{event.threshold}" for event in self.plan.events}
        slugs |= {r.slug for r in self.ledger.records.values()}
        for slug in sorted(slugs):
            for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
                found = [
                    (phase, receipt)
                    for phase in ("published", "failed")
                    if (receipt := self._read_receipt(slug, attempt, phase)) is not None
                ]
                if not found:
                    break
                for phase, receipt in found:
                    record = receipt["record"]
                    if record["artifact_id"] in self.ledger.records:
                        continue
                    self.ledger.note(
                        {
                            "slug": slug,
                            "attempt": attempt,
                            "artifact_id": record["artifact_id"],
                            "receipt_phase": phase,
                            "status": record["status"],
                            "actual_committed_targets": record["actual_committed_targets"],
                            "model_state_digest": record["identity"]["model_state_digest"],
                            "failure": record.get("failure"),
                        }
                    )


def build_checkpoint_controller(config: Mapping[str, Any], *, budget: int) -> CheckpointController:
    """Construct the controller from a validated ``training.checkpoint_cadence`` block."""
    plan = build_checkpoint_plan(
        str(config["cadence"]),
        budget,
        fixture_milestones=config.get("fixture_milestones"),
        fixture_recovery=config.get("fixture_recovery"),
    )
    if config.get("retention") != RETENTION_POLICY:
        raise CheckpointEventError(f"unsupported retention policy {config.get('retention')!r}")
    return CheckpointController(
        plan, protected_references=tuple(config.get("protected_references") or ())
    )


def rebase_checkpoint_ledger(ledger: CheckpointLedger, origin: int) -> CheckpointLedger:
    """A fork's fresh checkpoint ledger, re-originated at the fork's committed count."""
    if ledger.records or ledger.due:
        raise CheckpointLedgerError("a fork starts a fresh checkpoint ledger")
    return CheckpointLedger(rebase_checkpoint_plan(ledger.plan, origin), ledger.protected)
