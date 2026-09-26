"""Rescore a science-v1 evaluation event from its exact retained checkpoint (P35 M3).

M2 leaves an event FAILED (or interrupted) when its live boundary state is
gone. M3 may complete it only from a retained checkpoint of *exactly* that
state. Every identity is checked before anything is scored or published:

* the event was crossed (``due``) and is not already complete (unless a
  deliberate rerun is requested, which never changes the canonical receipt);
* the checkpoint is located by the event's model-state digest, committed count
  and step in the run's checkpoint ledger, and must still be retained; the
  latest or a nearby checkpoint is never substituted;
* the checkpoint's stored weights hash to the event's model-state digest (the
  same step and target count with different weights is refused);
* rebuilding the event's planned computation from the requested evaluator,
  device and scientific runtime reproduces the ``computation_identity``
  recorded at the crossing, so tokenizer, inventory, scoring policy, scorer
  source, device and runtime all equal what the event was planned with.

The rescore is a new immutable attempt (``started`` then ``outcome``) with its
own computation identity and an explicit ``lineage_identity`` equal to the
crossing's computation. Earlier attempts stay visible; the canonical receipt is
still the first complete attempt of the lineage (``first_complete_attempt_v1``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.evaluation.cadence import EventTier
from xlm.evaluation.receipts import (
    CANONICAL_RULE,
    RECEIPT_VERSION,
    RETRY_POLICY,
    AttemptOutcome,
    AttemptStore,
    EvaluationLedger,
    EventStatus,
    receipt_identity,
)

RESCORE_ROUTE = "retained_exact_checkpoint_rescore_v1"
RESCORABLE_TIERS = (EventTier.QUICK_LM, EventTier.FULL_LM)
UNRESOLVED = (EventStatus.DUE, EventStatus.FAILED, EventStatus.INTERRUPTED, EventStatus.PARTIAL)


class RescoreRefused(ValueError):
    """A rescore request does not bind the exact state and computation of its event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class RescoreInputs:
    """The LM evaluator to rebuild; it must reproduce the event's planned evaluator."""

    manifest_path: Path
    manifest_id: str
    rolling_stride: int
    forward_precision: str
    logprob_dtype: str
    tokenizer: Any = None
    tokenizer_path: Path | None = None

    @classmethod
    def from_evaluator(cls, evaluator: Any) -> RescoreInputs:
        """The inputs of a live :class:`LMValidationEvaluator` (in-training route)."""
        manifest = evaluator.inventory.manifest
        if manifest.source_path is None:
            raise RescoreRefused("evaluator_unlocated", "the inventory manifest has no path")
        return cls(
            manifest_path=Path(manifest.source_path),
            manifest_id=evaluator.inventory.manifest_id,
            rolling_stride=evaluator.policy.rolling_stride,
            forward_precision=evaluator.policy.forward_precision,
            logprob_dtype=evaluator.policy.logprob_dtype,
            tokenizer=evaluator.tokenizer,
        )


def planned_computation(
    tier: str, digest: str, evaluator_digest: str, device: str, runtime: Any
) -> dict[str, Any]:
    """The M2 in-memory computation of a crossing (``EvaluationController.computation``)."""
    return {
        "version": RECEIPT_VERSION,
        "tier": tier,
        "model_state": {"kind": "in_memory_replica_v1", "state_digest": digest},
        "evaluator": evaluator_digest,
        "device": device,
        "scientific_runtime": runtime,
    }


def locate_exact_checkpoint(checkpoint_ledger: Any, due: Mapping[str, Any], root: Path) -> Any:
    """The retained checkpoint record of exactly the event's state; never another one."""
    matches = [
        record
        for record in checkpoint_ledger.records.values()
        if record.model_state_digest == due["model_state_digest"]
        and record.actual_committed_targets == due["actual_committed_targets"]
        and record.step == due["step"]
    ]
    if not matches:
        raise RescoreRefused(
            "checkpoint_missing",
            f"no checkpoint of the event's state (C={due['actual_committed_targets']}, "
            f"step {due['step']}) was ever published; the event stays incomplete",
        )
    retained = [
        record
        for record in matches
        if record.status == "published" and (root / "checkpoints" / record.artifact_id).is_dir()
    ]
    if not retained:
        raise RescoreRefused(
            "checkpoint_not_retained",
            f"the event's checkpoint {[r.artifact_id for r in matches]} is no longer retained",
        )
    return min(retained, key=lambda record: record.attempt)


def _stored_state_digest(checkpoint_dir: Path) -> str:
    import torch

    from xlm.evaluation.state_digest import state_dict_digest

    state = torch.load(checkpoint_dir / "model.pt", map_location="cpu", weights_only=True)
    return state_dict_digest(state)


def rescore_event(
    *,
    ledger: EvaluationLedger,
    event_id: str,
    store: AttemptStore,
    run: Mapping[str, Any],
    checkpoint_dir: Path,
    inputs: RescoreInputs,
    device: str,
    runtime: Mapping[str, str] | None,
    deliberate_rerun: bool = False,
) -> dict[str, Any]:
    """Score one event from its exact checkpoint as a new attempt; return its summary."""
    from xlm.artifacts.store import ArtifactStore, compute_file_sha256
    from xlm.core.paths import ArtifactPaths
    from xlm.evaluation.lm_validation import score_checkpoint_validation

    if event_id not in ledger.records:
        raise RescoreRefused("unknown_event", f"'{event_id}' is not in this run's plan")
    record = ledger.records[event_id]
    if record.due is None:
        raise RescoreRefused("event_not_due", f"'{event_id}' was never crossed")
    for durable in store.history(event_id):
        ledger.adopt_attempt(record, durable)
    if record.status is EventStatus.COMPLETE and not deliberate_rerun:
        raise RescoreRefused("event_already_complete", f"'{event_id}' has a canonical receipt")
    tier = record.event.tier
    if tier not in RESCORABLE_TIERS:
        raise RescoreRefused(
            "tier_not_rescorable",
            f"{tier.value} events have no verified checkpoint scoring route; it stays incomplete",
        )
    due = record.due
    expected_evaluator = ledger.evaluator_digests[tier.value]
    planned = planned_computation(
        tier.value,
        due["model_state_digest"],
        expected_evaluator,
        device,
        dict(runtime) if runtime is not None else None,
    )
    if identity_digest(planned) != due["computation_identity"]:
        raise RescoreRefused(
            "computation_mismatch",
            "the event's planned evaluator, device or scientific runtime is not reproduced",
        )
    checkpoint_dir = checkpoint_dir.resolve()
    ArtifactStore(ArtifactPaths(root=checkpoint_dir.parent.parent)).verify_artifact(checkpoint_dir)
    meta = json.loads((checkpoint_dir / "checkpoint_meta.json").read_text(encoding="utf-8"))
    if meta.get("run_id") != run["run_id"] or meta.get("plan_id") != run["plan_id"]:
        raise RescoreRefused("run_mismatch", "the checkpoint belongs to another run or plan")
    if (
        meta.get("committed_valid_targets") != due["actual_committed_targets"]
        or meta.get("step") != due["step"]
    ):
        raise RescoreRefused(
            "checkpoint_counters_mismatch",
            f"checkpoint C={meta.get('committed_valid_targets')} step {meta.get('step')} is not "
            f"the event's C={due['actual_committed_targets']} step {due['step']}",
        )
    if _stored_state_digest(checkpoint_dir) != due["model_state_digest"]:
        raise RescoreRefused(
            "checkpoint_state_mismatch",
            "the checkpoint's stored weights are not the event's model state",
        )
    # The evaluator must reproduce the planned one exactly, before anything is published.
    from xlm.evaluation.lm_validation import (
        LMScoringPolicy,
        LMValidationEvaluator,
        load_pinned_inventory,
    )
    from xlm.tokenizers.loading import load_inference_tokenizer

    tokenizer = inputs.tokenizer or load_inference_tokenizer(
        checkpoint_dir, str(inputs.tokenizer_path) if inputs.tokenizer_path else None
    )
    try:
        candidate = LMValidationEvaluator(
            tier,
            load_pinned_inventory(
                inputs.manifest_path,
                manifest_id=inputs.manifest_id,
                tokenizer_fingerprint=tokenizer.fingerprint,
            ),
            tokenizer,
            LMScoringPolicy(
                context_length=int(meta["model_config"]["context_length"]),
                rolling_stride=inputs.rolling_stride,
                forward_precision=inputs.forward_precision,
                logprob_dtype=inputs.logprob_dtype,
            ),
        )
    except ValueError as exc:
        raise RescoreRefused("evaluator_mismatch", str(exc)) from exc
    if identity_digest(candidate.identity()) != expected_evaluator:
        raise RescoreRefused("evaluator_mismatch", _evaluator_diff(store, event_id, candidate))

    manifest_sha256 = compute_file_sha256(checkpoint_dir / "manifest.json")
    number = max((a["number"] for a in record.attempts), default=0) + 1
    computation = {
        "version": RECEIPT_VERSION,
        "tier": tier.value,
        "model_state": {
            "kind": "checkpoint_v1",
            "checkpoint_id": checkpoint_dir.name,
            "checkpoint_manifest_sha256": manifest_sha256,
            "state_digest": due["model_state_digest"],
        },
        "evaluator": expected_evaluator,
        "device": device,
        "scientific_runtime": dict(runtime) if runtime is not None else None,
        "route": RESCORE_ROUTE,
        "lineage": due["computation_identity"],
    }
    computation_id = identity_digest(computation)
    base = {
        "receipt_version": RECEIPT_VERSION,
        "run": dict(run),
        "event": record.event.to_dict(),
        "attempt": number,
        "actual_committed_targets": due["actual_committed_targets"],
        "step": due["step"],
        "model_state": computation["model_state"],
        "computation_identity": computation_id,
        "lineage_identity": due["computation_identity"],
        "route": RESCORE_ROUTE,
        "computation": computation,
        "evaluator": candidate.identity(),
        "canonical_rule": CANONICAL_RULE,
        "retry_policy": RETRY_POLICY,
        "deliberate_rerun": deliberate_rerun,
        "prior_attempts": [{"number": a["number"], "status": a["status"]} for a in record.attempts],
    }
    summary: dict[str, Any] = {
        "number": number,
        "started_artifact": None,
        "outcome_artifact": None,
        "status": EventStatus.RUNNING.value,
        "computation_identity": computation_id,
        "model_state_digest": due["model_state_digest"],
        "actual_committed_targets": due["actual_committed_targets"],
        "failure": None,
        "lineage_identity": due["computation_identity"],
        "route": RESCORE_ROUTE,
    }
    summary["started_artifact"] = store.publish(
        event_id,
        number,
        "started",
        {**base, "record": "started", "started_at": datetime.now(UTC).isoformat()},
    )
    ledger.adopt_attempt(record, summary)
    result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    try:
        result = score_checkpoint_validation(
            checkpoint_dir,
            manifest_path=inputs.manifest_path,
            manifest_id=inputs.manifest_id,
            tier=tier,
            rolling_stride=inputs.rolling_stride,
            forward_precision=inputs.forward_precision,
            logprob_dtype=inputs.logprob_dtype,
            device=device,
            tokenizer=tokenizer,
            expected_state_digest=due["model_state_digest"],
            expected_evaluator_digest=expected_evaluator,
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 - a failed rescore is a FAILED attempt, never a score
        failure = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    status = AttemptOutcome(result["status"]) if result is not None else AttemptOutcome.FAILED
    payload: dict[str, Any] = {
        **base,
        "record": "outcome",
        "status": status.value,
        "metrics": result["metrics"] if result is not None else None,
        "coverage": result["coverage"] if result is not None else None,
        "loaded_checkpoint": result["model_state"] if result is not None else None,
        "notes": [f"rescored from retained checkpoint {checkpoint_dir.name}"],
        "failure": failure,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    payload["receipt_identity"] = receipt_identity(payload)
    summary["outcome_artifact"] = store.publish(event_id, number, "outcome", payload)
    summary.update(status=status.value, failure=failure)
    ledger.adopt_attempt(record, summary)
    return summary


@dataclass
class RunEvaluationState:
    """A run's evaluation and checkpoint ledgers as recorded by one of its checkpoints."""

    ledger: EvaluationLedger
    checkpoints: Any
    store: AttemptStore
    run: dict[str, Any]
    runtime: dict[str, str] | None
    root: Path


def load_run_evaluation_state(checkpoint: Path) -> RunEvaluationState:
    """Read-only view of a finished run; attempts are reconciled from the artifact store."""
    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.training.milestones import CheckpointLedger

    checkpoint = checkpoint.resolve()
    root = checkpoint.parent.parent
    paths = ArtifactPaths(root=root)
    artifacts = ArtifactStore(paths)
    artifacts.verify_artifact(checkpoint)
    science = json.loads((checkpoint / "science.json").read_text(encoding="utf-8"))
    if science.get("evaluation") is None or science.get("checkpoints") is None:
        raise RescoreRefused(
            "no_cadence", "the checkpoint carries no evaluation and checkpoint cadence"
        )
    meta = json.loads((checkpoint / "checkpoint_meta.json").read_text(encoding="utf-8"))
    ledger = EvaluationLedger.from_dict(science["evaluation"])
    checkpoints = CheckpointLedger.from_dict(science["checkpoints"])
    execution = json.loads((checkpoint / "execution.json").read_text(encoding="utf-8"))
    envelope = execution.get("envelope")
    run_key = identity_digest(
        {"run_id": meta["run_id"], "plan_id": meta["plan_id"], "plan": ledger.plan.digest()}
    )
    run_ledger = RunLedger(paths.ledger / "ledger.sqlite")

    def record(artifact_id: str, path: Path) -> None:
        run_ledger.record_artifact(
            artifact_id=artifact_id,
            kind="evaluations",
            path=path,
            manifest_json=artifacts.load_manifest(path).model_dump_json(),
        )

    store = AttemptStore(
        artifacts,
        run_key,
        producer_code_hash=envelope["code_hash"]
        if envelope
        else identity_digest(dict(ledger.evaluator_digests)),
        dependency_hash=envelope["dependency_hash"]
        if envelope
        else identity_digest({"scope": "domain-api", "receipt": RECEIPT_VERSION}),
        record_artifact=record,
    )
    for event in ledger.ordered():
        for summary in store.history(event.event_id):
            ledger.adopt_attempt(event, summary)
    for cp in checkpoints.records.values():
        row = run_ledger.get_artifact(cp.artifact_id)
        if cp.status == "published" and row is not None and row["status"] == "retired":
            cp.status = "retired"
    run = {
        "run_id": meta["run_id"],
        "plan_id": meta["plan_id"],
        "run_key": run_key,
        "execution_hash": envelope["execution_hash"] if envelope else None,
        "scientific_policy": science["policy"],
        "evaluation_plan_digest": ledger.plan.digest(),
    }
    runtime = science["policy"].get("runtime")
    return RunEvaluationState(ledger, checkpoints, store, run, runtime, root)


def rescore_from_run_checkpoint(
    checkpoint: Path,
    event_id: str,
    *,
    inputs: RescoreInputs,
    device: str,
    deliberate_rerun: bool = False,
) -> dict[str, Any]:
    """Offline route: rescore one event of a finished run from its exact retained checkpoint.

    ``checkpoint`` is any checkpoint of the run whose ledgers are current enough
    to know the event's crossing (normally its final checkpoint). Nothing is
    written into that immutable checkpoint; the new attempt artifacts are the
    durable result, and :func:`load_run_evaluation_state` reconciles them.
    """
    state = load_run_evaluation_state(checkpoint)
    due = state.ledger.records[event_id].due if event_id in state.ledger.records else None
    if due is None:
        raise RescoreRefused("event_not_due", f"'{event_id}' was never crossed in this run")
    located = locate_exact_checkpoint(state.checkpoints, due, state.root)
    summary = rescore_event(
        ledger=state.ledger,
        event_id=event_id,
        store=state.store,
        run=state.run,
        checkpoint_dir=state.root / "checkpoints" / located.artifact_id,
        inputs=inputs,
        device=device,
        runtime=state.runtime,
        deliberate_rerun=deliberate_rerun,
    )
    return {
        "attempt": summary,
        "checkpoint": located.artifact_id,
        "canonical_attempt": state.ledger.records[event_id].canonical,
        "completeness": state.ledger.completeness().to_dict(),
    }


def _evaluator_diff(store: AttemptStore, event_id: str, candidate: Any) -> str:
    """Name which part of the evaluator differs, when an earlier attempt recorded it."""
    for summary in store.history(event_id):
        artifact = summary.get("started_artifact")
        started = store.read(artifact) if artifact else None
        if started is None or not isinstance(started.get("evaluator"), dict):
            continue
        planned = started["evaluator"]
        found = candidate.identity()
        fields = sorted(k for k in set(planned) | set(found) if planned.get(k) != found.get(k))
        return f"evaluator differs from the planned one in {fields}"
    return "evaluator identity differs from the planned one"
