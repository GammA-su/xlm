"""Science-v1 evaluation events at committed trainer boundaries (P35 Milestone 2).

The trainer calls :meth:`EvaluationController.record_crossings` right after a
successful update commits (before any periodic checkpoint, so a checkpoint
taken at that boundary already knows which events are due) and
:meth:`EvaluationController.run_pending` after the update returns. Evaluation
therefore runs synchronously between updates in the training process: never
concurrently with a GPU update, never on a speculative state, and never by
changing an update's size.

Scoring never touches the live model. Each event scores a deep copy whose
``state_dict`` digest must equal the digest recorded at the crossing (scoring
long documents rebuilds RoPE caches on the scored module). A guard captures
and restores Python/NumPy/torch CPU/CUDA RNG, per-module train/eval flags and
grad mode, and verifies that parameters, all buffers, gradients, optimizer,
schedule, objective, scaler, counters, the committed data cursor, science
receipts (including a declared update payload receipt chain and its staging)
and process runtime flags are unchanged. A change to live state is
not repaired: the attempt fails and the trainer refuses to continue or
checkpoint until it is resumed from a durable checkpoint.
"""

from __future__ import annotations

import copy
import json
import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.evaluation.cadence import EvaluationPlan, EventTier, PlannedEvent
from xlm.evaluation.outcome import (
    EvaluationContext,
    EvaluationOutcome,
    EvaluationStateMutationError,
    Evaluator,
)
from xlm.evaluation.receipts import (
    CANONICAL_RULE,
    MAX_ATTEMPTS_PER_EVENT,
    RECEIPT_VERSION,
    RETRY_POLICY,
    AttemptOutcome,
    AttemptStore,
    Completeness,
    EvaluationLedger,
    EventRecord,
    EventStatus,
    receipt_identity,
)
from xlm.evaluation.state_digest import (
    gradient_digest,
    model_state_digest,
    module_tensor_digest,
    value_digest,
)

MAX_FAILURE_MESSAGE = 2000


class EvaluationConfigurationError(ValueError):
    """Evaluators do not match the planned tiers or the run's scientific policy."""


def _module_attributes(module: Any) -> dict[str, Any]:
    """Plain scalar attributes per submodule (e.g. RoPE cache length)."""
    attributes: dict[str, Any] = {}
    for name, sub in module.named_modules():
        values = {
            key: value
            for key, value in vars(sub).items()
            if not key.startswith("_")
            and key != "training"
            and (value is None or isinstance(value, (bool, int, float, str)))
        }
        attributes[name or "<root>"] = {k: repr(v) for k, v in sorted(values.items())}
    return attributes


def _runtime_flags() -> dict[str, Any]:
    """Process-global numeric settings, including ones the runtime scope does not own."""
    import torch

    from xlm.training.science import ScientificRuntime

    flags: dict[str, Any] = {}
    try:
        flags.update(ScientificRuntime.observed_flags())
    except RuntimeError as exc:
        # torch refuses to report matmul precision once legacy and new APIs mix;
        # that state is itself a change, never a reason to skip the check.
        flags["observed_flags_error"] = str(exc)[:200]
    matmul = torch.backends.cuda.matmul
    flags.update(
        grad_enabled=torch.is_grad_enabled(),
        num_threads=torch.get_num_threads(),
        default_dtype=str(torch.get_default_dtype()),
        cudnn_enabled=torch.backends.cudnn.enabled,
        cudnn_allow_tf32=torch.backends.cudnn.allow_tf32,
        fp16_reduced_precision_reduction=matmul.allow_fp16_reduced_precision_reduction,
    )
    return flags


def training_state_fingerprint(trainer: Any) -> dict[str, str]:
    """Digests of every piece of training state evaluation must not change.

    With a declared update payload receipt, its declaration, committed rows,
    head and staged receipt are one more guarded component
    (``update_payload_receipt``); runs without it keep the historical component
    set, and a receipt attached by an evaluator shows as a science-receipt change.
    """
    objective = trainer.objective
    batcher_state = json.dumps(trainer.batcher.get_state(), sort_keys=True, default=repr)
    payloads = trainer.science.update_payloads
    science_receipts: dict[str, Any] = {
        "train_start_rng": trainer.science.train_start_rng,
        "lr_receipts": trainer.science.lr_receipts,
        "runtime_receipts": len(trainer.science.runtime_receipts),
    }
    if payloads is not None:
        science_receipts["update_payload_receipt_declared"] = True
    fingerprint = {
        "model_tensors": module_tensor_digest(trainer.model),
        "model_attributes": identity_digest(_module_attributes(trainer.model)),
        "module_modes": identity_digest(
            [m.training for m in trainer.model.modules()]
            + ([m.training for m in objective.modules()] if objective is not None else [])
        ),
        "gradients": gradient_digest(trainer.model)
        + (gradient_digest(objective) if objective is not None else ""),
        "optimizer": value_digest(trainer.optimizer.state_dict()),
        "schedule": value_digest(trainer.schedule.state_dict()),
        "objective": value_digest(
            {
                "state": objective.get_state() if objective is not None else None,
                "tensors": module_tensor_digest(objective) if objective is not None else None,
            }
        ),
        "scaler": value_digest(trainer.scaler.state_dict() if trainer.scaler is not None else None),
        "counters": identity_digest(
            {
                "step": trainer.step,
                "committed_valid_targets": trainer.committed_valid_targets,
                "processed_valid_targets": trainer.processed_valid_targets,
                "next_checkpoint_target": trainer.next_checkpoint_target,
                "update_in_doubt": trainer._update_in_doubt,
                "consecutive_scaler_skips": trainer._consecutive_scaler_skips,
            }
        ),
        "data_cursor": identity_digest(batcher_state),
        "science_receipts": identity_digest(science_receipts),
        "runtime_flags": identity_digest(_runtime_flags()),
    }
    if payloads is not None:
        fingerprint["update_payload_receipt"] = identity_digest(payloads.guard_state())
    return fingerprint


def _capture_rng() -> dict[str, Any]:
    import numpy as np
    import torch

    state: dict[str, Any] = {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch_cpu": torch.get_rng_state(),
    }
    # Mirrors checkpoint RNG capture: CUDA generators exist whenever CUDA does,
    # and an evaluator (the harness seeds torch) can reseed them.
    if torch.cuda.is_available():
        state["torch_cuda"] = torch.cuda.get_rng_state_all()
    return state


def _rng_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    import numpy as np
    import torch

    if left.keys() != right.keys() or left["python"] != right["python"]:
        return False
    ln, rn = left["numpy"], right["numpy"]
    if ln[0] != rn[0] or not np.array_equal(ln[1], rn[1]) or tuple(ln[2:]) != tuple(rn[2:]):
        return False
    if not torch.equal(left["torch_cpu"], right["torch_cpu"]):
        return False
    if "torch_cuda" in left:
        return len(left["torch_cuda"]) == len(right["torch_cuda"]) and all(
            torch.equal(a, b) for a, b in zip(left["torch_cuda"], right["torch_cuda"], strict=True)
        )
    return True


def _restore_rng(state: Mapping[str, Any]) -> None:
    import numpy as np
    import torch

    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch_cpu"])
    if "torch_cuda" in state:
        torch.cuda.set_rng_state_all(state["torch_cuda"])


class TrainingStateGuard:
    """Restore RNG, module modes and grad mode; verify all other training state."""

    def __init__(self, trainer: Any) -> None:
        self.trainer = trainer
        self._rng: dict[str, Any] = {}
        self._modes: list[tuple[Any, bool]] = []
        self._grad_enabled = True
        self._before: dict[str, str] = {}

    def capture(self) -> None:
        import torch

        trainer = self.trainer
        self._rng = _capture_rng()
        modules = list(trainer.model.modules())
        if trainer.objective is not None:
            modules += list(trainer.objective.modules())
        self._modes = [(module, bool(module.training)) for module in modules]
        self._grad_enabled = torch.is_grad_enabled()
        self._before = training_state_fingerprint(trainer)

    def restore_and_verify(self) -> dict[str, Any]:
        import torch

        restored: list[str] = []
        if not _rng_equal(self._rng, _capture_rng()):
            restored.append("rng")
        _restore_rng(self._rng)
        flipped = [module for module, flag in self._modes if module.training != flag]
        for module, flag in self._modes:
            module.training = flag  # per module, never a recursive .train()
        if flipped:
            restored.append("module_modes")
        if torch.is_grad_enabled() != self._grad_enabled:
            restored.append("grad_enabled")
        torch.set_grad_enabled(self._grad_enabled)
        after = training_state_fingerprint(self.trainer)
        changed = sorted(key for key, value in self._before.items() if after.get(key) != value)
        if not _rng_equal(self._rng, _capture_rng()):
            changed.append("rng_restore")
        return {"restored": restored, "changed": changed, "verified": sorted(self._before)}


def make_replica(model: Any, expected_digest: str) -> Any:
    """An isolated copy of the committed model, proven identical by digest."""
    replica = copy.deepcopy(model)
    for parameter in replica.parameters():
        parameter.requires_grad_(False)
    replica.eval()
    if model_state_digest(replica) != expected_digest:
        raise EvaluationStateMutationError(
            "the evaluation replica differs from the committed model state",
            components=("replica",),
            live=False,
        )
    return replica


def _failure(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc)[:MAX_FAILURE_MESSAGE],
    }
    if isinstance(exc, EvaluationStateMutationError):
        payload["components"] = list(exc.components)
        payload["live_state_compromised"] = exc.live
    return payload


class EvaluationController:
    """Owns the planned events, their evaluators and attempt publication for one run."""

    def __init__(self, plan: EvaluationPlan, evaluators: Mapping[EventTier, Evaluator]) -> None:
        planned = {event.tier for event in plan.events}
        if set(evaluators) != planned:
            raise EvaluationConfigurationError(
                f"evaluators for {sorted(t.value for t in evaluators)} do not match the planned "
                f"tiers {sorted(t.value for t in planned)}; a planned event needs an evaluator "
                "and an evaluator needs a planned event"
            )
        for tier, evaluator in evaluators.items():
            if evaluator.tier is not tier:
                raise EvaluationConfigurationError(
                    f"evaluator for {tier.value} reports tier {evaluator.tier}"
                )
        self.plan = plan
        self.evaluators = dict(evaluators)
        self.evaluator_digests = {
            tier.value: identity_digest(evaluator.identity())
            for tier, evaluator in sorted(evaluators.items(), key=lambda kv: kv[0].value)
        }
        self._science: Any = None
        self._store: AttemptStore | None = None
        self._run: dict[str, Any] = {}
        self._attempted: set[str] = set()
        #: P35 pilot readiness: ``RecoverabilityPolicy`` or ``None`` (M2/M3 behavior).
        self.recoverability: Any = None

    # ------------------------------------------------------------------ binding

    def attach(self, science: Any) -> None:
        """Give a science-v1 run its (fresh) evaluation ledger."""
        if not science.policy.is_science:
            raise EvaluationConfigurationError("evaluation cadence is science-v1 functionality")
        if science.evaluation is None:
            science.evaluation = EvaluationLedger(self.plan, self.evaluator_digests)
        elif (
            science.evaluation.plan.digest() != self.plan.digest()
            or science.evaluation.evaluator_digests != self.evaluator_digests
        ):
            raise EvaluationConfigurationError("attached ledger belongs to another plan")
        self._science = science

    @property
    def ledger(self) -> EvaluationLedger:
        assert self._science is not None and self._science.evaluation is not None
        return self._science.evaluation  # type: ignore[no-any-return]

    def bind_trainer(self, trainer: Any) -> None:
        if self._science is not trainer.science:
            self.attach(trainer.science)
        # An explicit fork was re-originated by the checkpoint loader; ordinary
        # resume adopted the saved ledger of this exact plan.
        self.plan = self.ledger.plan
        if self.plan.origin_committed_targets > trainer.committed_valid_targets:
            raise EvaluationConfigurationError("evaluation plan starts after the trainer's state")
        manager = trainer.checkpoint_manager
        execution = manager.execution
        if execution is not None:
            producer = execution["envelope"]["code_hash"]
            dependency = execution["envelope"]["dependency_hash"]
        else:
            producer = identity_digest(
                {tier: digest for tier, digest in self.evaluator_digests.items()}
            )
            dependency = identity_digest({"scope": "domain-api", "receipt": RECEIPT_VERSION})
        run_key = identity_digest(
            {"run_id": trainer.run_id, "plan_id": trainer.plan_id, "plan": self.plan.digest()}
        )
        self._run = {
            "run_id": trainer.run_id,
            "plan_id": trainer.plan_id,
            "run_key": run_key,
            "execution_hash": execution["envelope"]["execution_hash"] if execution else None,
            "scientific_policy": trainer.science.policy.identity(),
            "evaluation_plan_digest": self.plan.digest(),
        }

        def record(artifact_id: str, path: Any) -> None:
            manager.ledger.record_artifact(
                artifact_id=artifact_id,
                kind="evaluations",
                path=path,
                manifest_json=manager.store.load_manifest(path).model_dump_json(),
            )

        self._store = AttemptStore(
            manager.store,
            run_key,
            producer_code_hash=producer,
            dependency_hash=dependency,
            record_artifact=record,
        )

    # ------------------------------------------------------------- boundaries

    def computation(self, trainer: Any, event: PlannedEvent, digest: str) -> dict[str, Any]:
        """What one scoring computes; shared by events at one state with one evaluator."""
        from xlm.evaluation.rescore import planned_computation

        return planned_computation(
            event.tier.value,
            digest,
            self.evaluator_digests[event.tier.value],
            trainer.device,
            trainer.science.policy.runtime,
        )

    def record_crossings(self, trainer: Any) -> list[str]:
        """Mark every event whose threshold the committed count reached; no scoring."""
        ledger = self.ledger
        due = ledger.plan.due(trainer.committed_valid_targets, handled=ledger.handled())
        if not due:
            return []
        digest = model_state_digest(trainer.model)
        for event in due:
            ledger.mark_due(
                event,
                committed=trainer.committed_valid_targets,
                step=trainer.step,
                digest=digest,
                computation_identity=identity_digest(self.computation(trainer, event, digest)),
            )
        return [event.event_id for event in due]

    def _reconcile(self, record: EventRecord) -> None:
        """Adopt every durable attempt of this event, from any process or lineage."""
        assert self._store is not None
        for summary in self._store.history(record.event_id):
            self.ledger.adopt_attempt(record, summary)

    def run_pending(self, trainer: Any) -> None:
        """Evaluate due events of the current committed state, in plan order."""
        committed = trainer.committed_valid_targets
        pending = [
            record
            for record in self.ledger.ordered()
            if record.due is not None
            and record.due["actual_committed_targets"] == committed
            and record.event_id not in self._attempted
            and record.status is not EventStatus.COMPLETE
        ]
        if not pending:
            return
        shared: dict[str, tuple[EvaluationOutcome, str]] = {}
        documents: dict[tuple[str, str], Any] = {}
        for record in pending:
            self._attempted.add(record.event_id)
            self._reconcile(record)
            if record.status is EventStatus.COMPLETE:
                continue  # an exact durable result already exists: never rescored
            if len(record.lineage_attempts()) >= MAX_ATTEMPTS_PER_EVENT:
                continue
            self._run_event(trainer, record, shared, documents)

    def settle_required(self, trainer: Any) -> list[str]:
        """Pilot readiness: retry required events at the live C = 0 / endpoint boundary.

        Each retry is an ordinary M2 attempt at the same committed state: the
        next attempt number on the crossing's lineage, scored from a
        digest-verified replica, bounded by ``MAX_ATTEMPTS_PER_EVENT`` and
        canonicalized by ``first_complete_attempt_v1``. Nothing here changes
        attempt semantics; the policy only decides that the retry happens now
        instead of after a restart. Returns the event ids that were retried.
        """
        from xlm.evaluation.recoverability import live_retry_candidates

        policy = self.recoverability
        if policy is None:
            return []
        committed = trainer.committed_valid_targets
        if not (
            (committed == 0 and policy.barrier)
            or (committed == self.ledger.plan.budget_valid_targets and policy.endpoint_fail_stop)
        ):
            return []  # every other boundary keeps the unchanged M2/M3 behavior
        retried: list[str] = []
        for _ in range(MAX_ATTEMPTS_PER_EVENT):
            for record in self.ledger.ordered():
                if record.due is not None and record.due["actual_committed_targets"] == committed:
                    self._reconcile(record)
            candidates = live_retry_candidates(self.ledger, committed, self.recoverability)
            if not candidates:
                break
            for event_id in candidates:
                self._attempted.discard(event_id)
            retried.extend(candidates)
            self.run_pending(trainer)
        return retried

    def barrier_blockers(self) -> list[str]:
        """Required C = 0 events without a canonical COMPLETE receipt (policy only)."""
        from xlm.evaluation.recoverability import barrier_blockers

        return barrier_blockers(self.ledger, self.recoverability)

    def required_incomplete(self) -> list[str]:
        """Required events still incomplete when the endpoint policy fail-stops."""
        from xlm.evaluation.recoverability import required_incomplete

        return required_incomplete(self.ledger, self.recoverability)

    def rerun(self, trainer: Any, event_id: str) -> None:
        """Deliberately score an event again at its own committed state.

        The new attempt is preserved next to the earlier ones and never replaces
        an existing canonical receipt (``first_complete_attempt_v1``).
        """
        record = self.ledger.records[event_id]
        if record.due is None or (
            record.due["actual_committed_targets"] != trainer.committed_valid_targets
        ):
            raise EvaluationConfigurationError("a rerun needs the event's own committed state")
        trainer._require_committed_boundary()
        self._reconcile(record)
        self._run_event(trainer, record, {}, {})

    def recover_from_retained(self, trainer: Any, checkpoints: Any) -> list[dict[str, Any]]:
        """Rescore unresolved events of past boundaries from their exact retained checkpoints.

        Only events whose live state is gone (crossed at an earlier committed
        count) are considered, only their own checkpoint (by model-state
        digest) is used, and the M2 per-lineage attempt bound applies. Each
        refusal is reported; nothing falls back to another checkpoint. The
        separate model load consumes RNG, so the training-state guard restores
        and verifies everything exactly as around ordinary scoring.
        """
        from pathlib import Path

        from xlm.evaluation.rescore import (
            RESCORABLE_TIERS,
            UNRESOLVED,
            RescoreInputs,
            RescoreRefused,
            locate_exact_checkpoint,
            rescore_event,
        )
        from xlm.training.trainer import RecoveryRequiredError

        trainer._require_committed_boundary()
        assert self._store is not None
        root = Path(trainer.checkpoint_manager.store.paths.root)
        checkpoints.settle()
        report: list[dict[str, Any]] = []
        for record in self.ledger.ordered():
            if record.due is None:
                continue
            if record.due["actual_committed_targets"] == trainer.committed_valid_targets:
                continue  # the live state still exists: run_pending owns it
            self._reconcile(record)
            if record.status not in UNRESOLVED:
                continue
            entry: dict[str, Any] = {
                "event_id": record.event_id,
                "actual_committed_targets": record.due["actual_committed_targets"],
                "status_before": record.status.value,
            }
            if len(record.lineage_attempts()) >= MAX_ATTEMPTS_PER_EVENT:
                report.append({**entry, "action": "skipped", "code": "attempt_bound_reached"})
                continue
            try:
                if record.event.tier not in RESCORABLE_TIERS:
                    raise RescoreRefused(
                        "tier_not_rescorable",
                        f"{record.event.tier.value} has no verified checkpoint scoring route",
                    )
                located = locate_exact_checkpoint(checkpoints.ledger, record.due, root)
                inputs = RescoreInputs.from_evaluator(self.evaluators[record.event.tier])
                guard = TrainingStateGuard(trainer)
                guard.capture()
                try:
                    summary = rescore_event(
                        ledger=self.ledger,
                        event_id=record.event_id,
                        store=self._store,
                        run=self._run,
                        checkpoint_dir=root / "checkpoints" / located.artifact_id,
                        inputs=inputs,
                        device=trainer.device,
                        runtime=trainer.science.policy.runtime,
                    )
                finally:
                    guard_report = guard.restore_and_verify()
                if guard_report["changed"]:
                    trainer._evaluation_compromised = True
                    raise RecoveryRequiredError(
                        "checkpoint rescoring changed live training state: "
                        f"{guard_report['changed']}"
                    )
                report.append(
                    {
                        **entry,
                        "action": "rescored",
                        "checkpoint": located.artifact_id,
                        "attempt": summary["number"],
                        "status": summary["status"],
                    }
                )
            except RescoreRefused as exc:
                report.append({**entry, "action": "refused", "code": exc.code, "reason": str(exc)})
        return report

    def _run_event(
        self,
        trainer: Any,
        record: EventRecord,
        shared: dict[str, tuple[EvaluationOutcome, str]],
        documents: dict[tuple[str, str], Any],
    ) -> None:
        from xlm.training.trainer import RecoveryRequiredError

        assert self._store is not None and record.due is not None
        event, due = record.event, record.due
        digest = due["model_state_digest"]
        live_digest = model_state_digest(trainer.model)
        computation = self.computation(trainer, event, digest)
        computation_id = identity_digest(computation)
        if live_digest != digest or computation_id != due["computation_identity"]:
            trainer._evaluation_compromised = True
            raise RecoveryRequiredError(
                f"committed state differs from the crossing state of '{record.event_id}'; "
                "resume from a durable checkpoint"
            )
        evaluator = self.evaluators[event.tier]
        number = max((a["number"] for a in record.attempts), default=0) + 1
        base = {
            "receipt_version": RECEIPT_VERSION,
            "run": dict(self._run),
            "event": event.to_dict(),
            "attempt": number,
            "actual_committed_targets": due["actual_committed_targets"],
            "step": due["step"],
            "model_state": computation["model_state"],
            "computation_identity": computation_id,
            "computation": computation,
            "evaluator": evaluator.identity(),
            "canonical_rule": CANONICAL_RULE,
            "retry_policy": RETRY_POLICY,
        }
        summary: dict[str, Any] = {
            "number": number,
            "started_artifact": None,
            "outcome_artifact": None,
            "status": EventStatus.RUNNING.value,
            "computation_identity": computation_id,
            "model_state_digest": digest,
            "actual_committed_targets": due["actual_committed_targets"],
            "failure": None,
        }
        try:
            summary["started_artifact"] = self._store.publish(
                record.event_id,
                number,
                "started",
                {**base, "record": "started", "started_at": datetime.now(UTC).isoformat()},
            )
        except Exception as exc:  # receipt store unavailable: nothing is scored
            summary.update(status=AttemptOutcome.FAILED.value, failure=_failure(exc))
            self.ledger.adopt_attempt(record, summary)
            return
        self.ledger.adopt_attempt(record, summary)

        guard = TrainingStateGuard(trainer)
        guard.capture()
        outcome: EvaluationOutcome | None = None
        failure: BaseException | None = None
        shared_with: str | None = None
        started = time.monotonic()
        try:
            with trainer.runtime.scope():
                cached = shared.get(computation_id)
                if cached is not None:
                    outcome, shared_with = cached
                else:
                    replica = make_replica(trainer.model, digest)
                    try:
                        outcome = evaluator.evaluate(
                            replica,
                            device=trainer.device,
                            context=EvaluationContext(
                                event_id=record.event_id,
                                actual_committed_targets=due["actual_committed_targets"],
                                model_state_digest=digest,
                                provenance={"run": dict(self._run), "event": event.to_dict()},
                                document_cache=documents,
                            ),
                        )
                        if model_state_digest(replica) != digest:
                            raise EvaluationStateMutationError(
                                "the evaluated replica changed during scoring",
                                components=("replica",),
                                live=False,
                            )
                    finally:
                        del replica
                    shared[computation_id] = (outcome, record.event_id)
        except (KeyboardInterrupt, SystemExit):
            # Interrupted mid-suite: the started record stays without an
            # outcome, which reads back as INTERRUPTED, never as a score.
            guard.restore_and_verify()
            raise
        except Exception as exc:
            failure = exc
        evaluation_seconds = time.monotonic() - started
        guard_started = time.monotonic()
        report = guard.restore_and_verify()
        report["seconds"] = time.monotonic() - guard_started
        compromise: EvaluationStateMutationError | None = None
        if report["changed"]:
            compromise = EvaluationStateMutationError(
                f"evaluation changed live training state: {report['changed']}",
                components=tuple(report["changed"]),
                live=True,
            )
            failure = compromise
        status = AttemptOutcome.FAILED if failure is not None or outcome is None else outcome.status
        succeeded = status is not AttemptOutcome.FAILED and outcome is not None
        record_payload: dict[str, Any] = {
            **base,
            "record": "outcome",
            "status": status.value,
            "metrics": outcome.metrics if succeeded and outcome is not None else None,
            "coverage": outcome.coverage if succeeded and outcome is not None else None,
            "notes": list(outcome.notes) if succeeded and outcome is not None else [],
            "failure": _failure(failure) if failure is not None else None,
            "guard": report,
            "timing": {"evaluation_seconds": evaluation_seconds},
            "shared_computation_with": shared_with,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        record_payload["receipt_identity"] = receipt_identity(record_payload)
        try:
            summary["outcome_artifact"] = self._store.publish(
                record.event_id,
                number,
                "outcome",
                record_payload,
                extra_files=outcome.extra_files if succeeded and outcome is not None else None,
            )
            summary.update(status=status.value, failure=record_payload["failure"])
        except Exception as exc:
            summary.update(status=AttemptOutcome.FAILED.value, failure=_failure(exc))
        self.ledger.adopt_attempt(record, summary)
        if compromise is not None:
            trainer._evaluation_compromised = True
            raise RecoveryRequiredError(
                "evaluation changed live training state; resume from a durable checkpoint"
            ) from compromise

    # ------------------------------------------------------------- reporting

    def completeness(self) -> Completeness:
        return self.ledger.completeness()

    def canonical_receipts(self) -> dict[str, dict[str, Any] | None]:
        """Canonical outcome receipt per event, read back from the artifact store."""
        assert self._store is not None
        receipts: dict[str, dict[str, Any] | None] = {}
        for record in self.ledger.ordered():
            receipts[record.event_id] = (
                self._store.read(record.canonical) if record.canonical is not None else None
            )
        return receipts


# ----------------------------------------------------------------- configuration

_TIER_KEYS = {tier: tier.value for tier in EventTier}


def plan_from_config(science_evaluation: Mapping[str, Any], budget: int) -> EvaluationPlan:
    """Validate a resolved ``evaluation.science`` block against the run budget (no I/O)."""
    from xlm.evaluation.cadence import build_plan

    plan = build_plan(
        str(science_evaluation["cadence"]),
        budget,
        confirmation_registered=bool(science_evaluation["confirmation_registered"]),
        fixture_thresholds=science_evaluation["fixture_thresholds"],
    )
    planned = {event.tier.value for event in plan.events}
    configured = {key for key in _TIER_KEYS.values() if science_evaluation.get(key) is not None}
    if planned - configured:
        raise EvaluationConfigurationError(
            f"planned tiers {sorted(planned - configured)} name no evaluation inputs"
        )
    if configured - planned:
        raise EvaluationConfigurationError(
            f"evaluation inputs for {sorted(configured - planned)} have no planned event"
        )
    return plan


def build_evaluation_controller(
    science_evaluation: Mapping[str, Any],
    *,
    budget: int,
    tokenizer: Any,
    context_length: int,
    device: str,
) -> EvaluationController:
    """Construct pinned, verified evaluators for every planned tier."""
    from pathlib import Path

    from xlm.evaluation.lm_validation import (
        LMScoringPolicy,
        LMValidationEvaluator,
        ValidationManifestError,
        load_pinned_inventory,
    )
    from xlm.evaluation.search_tier import (
        BenchmarkInputSpec,
        BenchmarkTierEvaluator,
        EndpointConfirmationEvaluator,
    )
    from xlm.evaluation.suites import SuiteTier

    if tokenizer is None:
        raise EvaluationConfigurationError("evaluation requires the run's frozen tokenizer")
    plan = plan_from_config(science_evaluation, budget)
    scoring = science_evaluation["scoring"]
    if scoring["forward_precision"] == "bf16_autocast" and device != "cuda":
        raise EvaluationConfigurationError("bf16_autocast evaluation requires a CUDA device")
    policy = LMScoringPolicy(
        context_length=context_length,
        rolling_stride=int(scoring["rolling_stride"]),
        forward_precision=str(scoring["forward_precision"]),
        logprob_dtype=str(scoring["logprob_dtype"]),
    )
    planned = {event.tier for event in plan.events}

    def inventory(ref: Mapping[str, Any]) -> Any:
        return load_pinned_inventory(
            ref["manifest"],
            manifest_id=str(ref["manifest_id"]),
            tokenizer_fingerprint=tokenizer.fingerprint,
        )

    def benchmark(ref: Mapping[str, Any], tier: SuiteTier) -> BenchmarkTierEvaluator:
        spec = BenchmarkInputSpec(
            inputs_path=Path(ref["inputs"]),
            manifest_id=str(ref["manifest_id"]),
            tier=tier,
            blimp_universe=tuple(ref["blimp_universe"]),
        )
        return BenchmarkTierEvaluator(spec, tokenizer, policy)

    evaluators: dict[EventTier, Evaluator] = {}
    development: list[Any] = []
    for tier in (EventTier.QUICK_LM, EventTier.FULL_LM):
        if tier in planned:
            inv = inventory(science_evaluation[tier.value])
            evaluators[tier] = LMValidationEvaluator(tier, inv, tokenizer, policy)
            development.append(inv)
    if EventTier.QUICK_LM in planned and EventTier.FULL_LM in planned:
        quick, full = development
        if quick.manifest.nested_in != full.manifest_id or not (
            quick.manifest.document_ids() <= full.manifest.document_ids()
        ):
            raise ValidationManifestError("the quick subset must be nested in the full inventory")
    if EventTier.SEARCH_BENCHMARK in planned:
        evaluators[EventTier.SEARCH_BENCHMARK] = benchmark(
            science_evaluation["search_benchmark"], SuiteTier.SEARCH
        )
    if EventTier.ENDPOINT_CONFIRMATION in planned:
        ref = science_evaluation["endpoint_confirmation"]
        confirmation = inventory(ref["lm"])
        for inv in development:
            if confirmation.manifest.document_ids() & inv.manifest.document_ids():
                raise ValidationManifestError(
                    "the confirmation LM inventory overlaps a development inventory"
                )
        evaluators[EventTier.ENDPOINT_CONFIRMATION] = EndpointConfirmationEvaluator(
            LMValidationEvaluator(EventTier.ENDPOINT_CONFIRMATION, confirmation, tokenizer, policy),
            benchmark(ref["benchmark"], SuiteTier.CONFIRMATION)
            if ref["benchmark"] is not None
            else None,
        )
    return EvaluationController(plan, evaluators)
