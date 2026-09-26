"""Run evidence for science-v1 comparisons, derived from M1–M3 receipts (P35 M4, §C/§T).

A comparison row is only as good as the evidence it links. This module reads a
**verified frozen science-v1 checkpoint** from the existing ``ArtifactStore`` and
derives every scientific field from authoritative records:

- ``execution.json`` (frozen envelope and plan hash; identity recomputed and
  checked against the artifact manifest exactly as checkpoint loading does);
- ``checkpoint_meta.json``, ``data_state.json`` (committed counts, exposure-plan
  identity, chained per-target trace digest, per-source counters);
- ``science.json`` (M1 policy, LR receipts giving exact update boundaries and
  runtime receipts; M2 evaluation ledger; M3 checkpoint ledger);
- the M2 evaluation attempt artifacts, reconciled with the unchanged
  ``first_complete_attempt_v1`` rule and verified by the store.

Nothing here is taken from user-entered labels when a receipt exists. The only
non-receipt input is the unique parameter count, produced by a caller-supplied
counter (the product CLI uses the existing meta-device count); its source is
recorded. Legacy (non-science-v1) checkpoints are refused, not reinterpreted.

The M2 constants below are read, never redefined: the reader refuses a ledger
whose canonical rule or receipt version differs.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.artifacts.store import ArtifactStore, compute_file_sha256
from xlm.comparison.science_manifest import MEMBERSHIP_SENTINEL, ORDER_SENTINEL
from xlm.core.paths import ArtifactPaths

#: v2 (P35 M5) adds ``canonical_membership_id`` and reads the order identity from
#: receipts. v1 records (M4) stay verifiable and are read as pre-M5 evidence.
#: v3 (pilot readiness) adds the verified update payload receipt chain; v1/v2
#: records stay verifiable and read as "no receipt" (unknown, never equal).
EVIDENCE_VERSION = "xlm-science-run-evidence-v3"
LEGACY_EVIDENCE_VERSIONS = ("xlm-science-run-evidence-v1", "xlm-science-run-evidence-v2")
PAYLOAD_FIELDS = ("update_payload_receipt", "update_payload_chain_digest")
SCIENCE_VERSION = "xlm-science-v1"
#: M1/M2/M3 record versions this reader understands (read-only mirrors).
M1_SCIENCE_STATE_VERSION = 1
M1_LR_COLUMNS = ["step", "committed_before", "valid_targets", "schedule_counter", "lr_used"]
M2_LEDGER_VERSION = 1
M2_RECEIPT_VERSION = "xlm-eval-receipt-v1"
M2_CANONICAL_RULE = "first_complete_attempt_v1"
M2_MAX_ATTEMPT_NUMBER = 64
M3_CHECKPOINT_LEDGER_VERSION = 1
#: Without an M5 order manifest, within-source order is the prepared shard order,
#: and ``data_seed`` changes only source scheduling.
WITHIN_SOURCE_ORDER_POLICY = "shard_native_offset_order_v1"
#: The M5 policy, mirrored from ``xlm.data.ordering.ORDER_POLICY`` (AST-checked).
M5_ORDER_POLICY = "m5_within_source_document_permutation_v1"
MAX_JSON_BYTES = 64 * 1024**2
_SLUG = re.compile(r"[^A-Za-z0-9_.-]")

ParameterCounter = Callable[[Mapping[str, Any]], int]


class EvidenceError(ValueError):
    """Raised when a checkpoint cannot supply trustworthy science-v1 evidence."""


def _read(path: Path) -> Any:
    if not path.is_file():
        raise EvidenceError(f"required receipt missing: {path.name}")
    raw = path.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        raise EvidenceError(f"{path.name} exceeds its byte bound")
    return json.loads(raw)


def meta_parameter_counter(config: Mapping[str, Any]) -> int:
    """Unique deployed parameters by the existing meta-device count (needs torch)."""
    from xlm.training.components import inspect_model_shape

    return inspect_model_shape(dict(config))


def attempt_artifact_id(run_key: str, event_id: str, number: int, phase: str) -> str:
    """M2 ``AttemptStore.artifact_id``, reproduced for read-only lookup."""
    slug = _SLUG.sub("-", event_id.replace("@", "-t"))
    return f"ev_{run_key[:16]}_{slug}_a{number:03d}_{phase}"


def _attempt(store: ArtifactStore, root: Path, artifact_id: str) -> dict[str, Any] | None:
    path = root / "evaluations" / artifact_id
    if not path.is_dir():
        return None
    store.verify_artifact(path)
    record = _read(path / "attempt.json")
    if not isinstance(record, dict) or record.get("receipt_version") != M2_RECEIPT_VERSION:
        raise EvidenceError(f"attempt {artifact_id} is not a {M2_RECEIPT_VERSION} record")
    return record


def _finite_metrics(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_finite_metrics(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite_metrics(v) for v in value)
    return True


def _coverage_complete(coverage: Any) -> bool:
    if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        return False
    for scored, declared in (
        ("documents_scored", "documents_declared"),
        ("text_utf8_bytes_scored", "text_utf8_bytes_declared"),
    ):
        if scored in coverage and coverage.get(scored) != coverage.get(declared):
            return False
    return True


def reconcile_evaluations(
    store: ArtifactStore, root: Path, ledger: Mapping[str, Any], run_key: str
) -> dict[str, Any]:
    """Per-event status from durable attempts under ``first_complete_attempt_v1``.

    The canonical attempt is the first *complete* attempt of the lineage bound at
    the event's crossing (``lineage_identity`` for M3 rescores, else the attempt's
    own computation identity). A canonical receipt must re-verify its receipt
    identity, have finite metrics and complete coverage; otherwise the event is
    not complete and the run is evaluation-incomplete.
    """
    if ledger.get("version") != M2_LEDGER_VERSION:
        raise EvidenceError("unsupported evaluation ledger version")
    if ledger.get("canonical_rule") != M2_CANONICAL_RULE:
        raise EvidenceError("evaluation ledger uses a different canonical rule; refused")
    events: dict[str, Any] = {}
    for entry in ledger["events"]:
        event_id = str(entry["event_id"])
        due = entry.get("due")
        attempts: list[dict[str, Any]] = []
        canonical: dict[str, Any] | None = None
        canonical_id: str | None = None
        for number in range(1, M2_MAX_ATTEMPT_NUMBER + 1):
            started = _attempt(
                store, root, attempt_artifact_id(run_key, event_id, number, "started")
            )
            outcome_id = attempt_artifact_id(run_key, event_id, number, "outcome")
            outcome = _attempt(store, root, outcome_id)
            if started is None and outcome is None:
                break
            source = outcome if outcome is not None else started
            assert source is not None
            status = outcome["status"] if outcome is not None else "interrupted"
            lineage = source.get("lineage_identity", source["computation_identity"])
            in_lineage = due is not None and lineage == due["computation_identity"]
            attempts.append(
                {
                    "number": number,
                    "status": status,
                    "in_lineage": in_lineage,
                    "route": source.get("route"),
                    "outcome_artifact": outcome_id if outcome is not None else None,
                }
            )
            if canonical is None and in_lineage and status == "complete" and outcome is not None:
                canonical, canonical_id = outcome, outcome_id
        reasons: list[str] = []
        metrics: dict[str, Any] | None = None
        receipt: str | None = None
        coverage_ok = False
        if due is None:
            reasons.append("threshold not reached")
        elif canonical is None:
            reasons.append("no complete attempt in the crossing lineage")
        else:
            recomputed = identity_digest(
                {k: v for k, v in canonical.items() if k != "receipt_identity"}
            )
            if canonical.get("receipt_identity") != recomputed:
                reasons.append("canonical receipt identity does not verify")
            if canonical.get("run", {}).get("run_key") != run_key:
                reasons.append("canonical receipt belongs to another run")
            metrics = canonical.get("metrics")
            if not isinstance(metrics, dict) or not _finite_metrics(metrics):
                reasons.append("canonical metrics missing or non-finite")
                metrics = None
            coverage_ok = _coverage_complete(canonical.get("coverage"))
            if not coverage_ok:
                reasons.append("canonical coverage is not complete")
            receipt = canonical.get("receipt_identity")
        events[event_id] = {
            "tier": entry["tier"],
            "planned_threshold": entry["planned_threshold"],
            "is_endpoint": entry["is_endpoint"],
            "actual_committed_targets": due["actual_committed_targets"] if due else None,
            "complete": not reasons,
            "reasons": reasons,
            "canonical_attempt": canonical_id,
            "receipt_identity": receipt,
            "evaluator": identity_digest(canonical["evaluator"]) if canonical else None,
            "metrics": metrics,
            "coverage_complete": coverage_ok,
            "attempts": attempts,
        }
    return events


def _checkpoint_records(ledger: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if ledger.get("version") != M3_CHECKPOINT_LEDGER_VERSION:
        raise EvidenceError("unsupported checkpoint ledger version")
    records = ledger.get("records", [])
    for record in records:
        payload = {k: v for k, v in record.items() if k != "identity_digest"}
        if identity_digest(payload["identity"]) != record.get("identity_digest"):
            raise EvidenceError(f"checkpoint record {record.get('artifact_id')} identity mismatch")
    return list(records)


def _order_identity(config_order: Any, state_order: Any) -> tuple[str, str, str]:
    """``(order_manifest_id, canonical_membership_id, policy)`` from the receipts.

    The frozen envelope's pinned ``data.document_order`` and the committed data
    state's ``document_order`` must agree. Both absent is a pre-M5 (shard-native)
    run and yields the sentinels. Any disagreement is refused: an M5 receipt is
    never silently replaced by the pre-M5 sentinel.
    """
    if config_order is None and state_order is None:
        return ORDER_SENTINEL, MEMBERSHIP_SENTINEL, WITHIN_SOURCE_ORDER_POLICY
    if not isinstance(config_order, Mapping) or not isinstance(state_order, Mapping):
        raise EvidenceError(
            "document order identity is inconsistent between the execution envelope and "
            "the committed data state; refusing rather than substituting the pre-M5 sentinel"
        )
    order_id = state_order.get("order_manifest_id")
    membership_id = state_order.get("canonical_membership_id")
    if (
        state_order.get("within_source_order_policy") != M5_ORDER_POLICY
        or not isinstance(order_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", order_id)
        or not isinstance(membership_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", membership_id)
        or config_order.get("order_manifest_id") != order_id
        or config_order.get("canonical_membership_id") != membership_id
    ):
        raise EvidenceError(
            "document order receipt does not match the pinned execution order manifest"
        )
    return order_id, membership_id, M5_ORDER_POLICY


def _update_boundaries(rows: list[list[Any]], committed: int) -> tuple[str, int]:
    """Digest of every committed update's (committed_before, valid_targets)."""
    position = 0
    boundaries: list[list[int]] = []
    for row in rows:
        before, size = int(row[1]), int(row[2])
        if before != position or size <= 0:
            raise EvidenceError("LR receipts are not a contiguous committed-update sequence")
        boundaries.append([before, size])
        position += size
    if position != committed:
        raise EvidenceError(
            f"LR receipts account for {position} targets, checkpoint committed {committed}"
        )
    return identity_digest(boundaries), len(boundaries)


def _update_payload_receipt(
    science: Mapping[str, Any], lr_rows: list[list[Any]], declared: Any = None
) -> tuple[Any, Any]:
    """``(receipt version, chain head)`` from ``science.json``; ``(None, None)`` if absent.

    The chain is re-derived from its genesis and must match the LR receipts
    update by update (step, committed_before, valid targets). A present but
    inconsistent chain is refused, never replaced by "unknown". Its presence and
    version must be exactly what the frozen envelope declared
    (``training.update_payload_receipt``): an undeclared chain or a declared but
    missing one is ambiguous evidence and is refused.
    """
    from xlm.data.sampling.update_payload import PayloadReceiptError, verify_chain

    raw = science.get("update_payloads")
    if (raw is None) != (declared is None):
        raise EvidenceError(
            "update payload receipt presence disagrees with the execution envelope "
            f"declaration ({declared!r}); refusing ambiguous receipt evidence"
        )
    if raw is None:
        return None, None
    if not isinstance(raw, Mapping) or raw.get("version") != declared:
        raise EvidenceError("update payload receipt version differs from its declaration")
    try:
        head = verify_chain(raw)
    except (PayloadReceiptError, KeyError, TypeError, ValueError) as exc:
        raise EvidenceError(f"update payload chain does not verify: {exc}") from exc
    chain_rows = [[int(r[0]), int(r[1]), int(r[2])] for r in raw["rows"]]
    if chain_rows != [[int(r[0]), int(r[1]), int(r[2])] for r in lr_rows]:
        raise EvidenceError("update payload chain and LR receipts disagree on committed updates")
    return str(raw["version"]), head


def extract_run_evidence(
    checkpoint_dir: Path,
    *,
    parameter_counter: ParameterCounter | None = meta_parameter_counter,
    counter_label: str = "meta_device_unique_parameters_v1",
) -> dict[str, Any]:
    """Derive one run's science-v1 evidence from a verified frozen checkpoint."""
    checkpoint_dir = Path(checkpoint_dir)
    root = checkpoint_dir.parent.parent
    store = ArtifactStore(ArtifactPaths(root=root))
    try:
        manifest = store.verify_artifact(checkpoint_dir)
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"checkpoint artifact does not verify: {exc}") from exc
    if manifest.kind != "checkpoints":
        raise EvidenceError(f"artifact kind '{manifest.kind}' is not a checkpoint")
    execution = _read(checkpoint_dir / "execution.json")
    envelope = execution.get("envelope") if isinstance(execution, dict) else None
    if not isinstance(envelope, dict) or "plan_hash" not in execution:
        raise EvidenceError("checkpoint has no frozen execution envelope (legacy/unresolved)")
    payload = {k: v for k, v in envelope.items() if k != "execution_hash"}
    if envelope.get("execution_hash") != identity_digest(payload):
        raise EvidenceError("execution envelope identity mismatch")
    if manifest.metadata.get("execution_provenance_hash") != identity_digest(execution):
        raise EvidenceError("checkpoint manifest does not bind this execution provenance")
    if (
        manifest.producer_code_hash != envelope["code_hash"]
        or manifest.dependency_hash != envelope["dependency_hash"]
        or manifest.resolved_config_hash != execution["plan_hash"]
    ):
        raise EvidenceError("checkpoint manifest and execution envelope disagree")
    config = envelope["config"]
    training = config["training"]
    if training.get("science_version") != SCIENCE_VERSION:
        raise EvidenceError(
            "legacy (non-science-v1) execution: not eligible for P35 comparisons and never "
            "reinterpreted"
        )
    meta = _read(checkpoint_dir / "checkpoint_meta.json")
    science = _read(checkpoint_dir / "science.json")
    data_state = _read(checkpoint_dir / "data_state.json")
    if science.get("version") != M1_SCIENCE_STATE_VERSION:
        raise EvidenceError("unsupported science.json version")
    lr = science["lr_receipts"]
    if lr.get("columns") != M1_LR_COLUMNS:
        raise EvidenceError("unknown LR receipt layout")
    committed = int(meta["committed_valid_targets"])
    if int(data_state.get("committed_valid_targets", -1)) != committed:
        raise EvidenceError("data state and checkpoint metadata disagree on committed targets")
    boundaries_digest, updates = _update_boundaries(lr["rows"], committed)
    if updates != int(meta["step"]):
        raise EvidenceError("LR receipts and checkpoint step disagree")
    payload_receipt, payload_chain = _update_payload_receipt(
        science, lr["rows"], training.get("update_payload_receipt")
    )

    bindings = envelope["bindings"]
    components = bindings["components"]
    model = dict(config["model"])
    attention_backend = model.pop("attention_backend", None)
    data = config["data"]
    mixture = data.get("mixture") if isinstance(data, dict) else None
    if isinstance(mixture, Mapping):
        mixture_components: Any = sorted(
            ({"source_id": c["source_id"], "weight": c["weight"]} for c in mixture["components"]),
            key=lambda c: str(c["source_id"]),
        )
        exhaustion: Any = mixture.get("exhaustion")
        packing: Any = mixture.get("packing")
        scheduler: Any = "token_deficit_v1"
    else:
        mixture_components = "single_source_input"
        exhaustion = "single_source_input"
        packing = data.get("packing_policy") if isinstance(data, dict) else None
        scheduler = "single_source_input"
    order_id, membership_id, order_policy = _order_identity(
        data.get("document_order") if isinstance(data, dict) else None,
        data_state.get("document_order"),
    )
    counters = (data_state.get("scheduler") or {}).get("counters") or {}
    per_source = {
        source: {
            key: counters[source].get(key)
            for key in (
                "valid_targets",
                "repeated_targets",
                "content_targets",
                "eos_targets",
                "documents_visited",
                "canonical_bytes",
                "repeated_bytes",
                "epoch",
            )
        }
        for source in sorted(counters)
    }
    runtime_receipts = science.get("runtime_receipts") or []
    attention = [r for r in runtime_receipts if isinstance(r, Mapping)]
    device_names = sorted(
        {str(r.get("device_name", r.get("device"))) for r in attention if r.get("device")}
    )
    torch_versions = sorted({str(r["torch"]) for r in attention if r.get("torch")})
    observed_ops = sorted({str(op) for r in attention for op in r.get("observed_ops", [])})

    evaluation = science.get("evaluation")
    checkpoints = science.get("checkpoints")
    run_id, plan_id = str(meta["run_id"]), str(meta["plan_id"])
    events: dict[str, Any] = {}
    evaluation_plan_digest = None
    evaluator_identities = None
    if isinstance(evaluation, Mapping):
        evaluation_plan_digest = evaluation.get("plan_digest")
        evaluator_identities = evaluation.get("evaluator_digests")
        run_key = identity_digest(
            {"run_id": run_id, "plan_id": plan_id, "plan": evaluation_plan_digest}
        )
        events = reconcile_evaluations(store, root, evaluation, run_key)
    initial_digest = None
    final_digest = None
    checkpoint_plan_digest = None
    if isinstance(checkpoints, Mapping):
        checkpoint_plan_digest = checkpoints.get("plan_digest")
        for entry in _checkpoint_records(checkpoints):
            if entry["status"] in ("published", "retired") and "checkpoint@0" in entry["events"]:
                initial_digest = entry["identity"]["model_state_digest"]
            if entry["artifact_id"] == manifest.artifact_id:
                final_digest = entry["identity"]["model_state_digest"]

    parameter_count: int | None = None
    parameter_source = "not_counted"
    if parameter_counter is not None:
        try:
            parameter_count = int(parameter_counter(config))
            parameter_source = counter_label
        except Exception as exc:  # unknown stays unknown, never zero
            parameter_source = f"count_failed: {type(exc).__name__}"

    fields: dict[str, Any] = {
        "model_architecture": model,
        "model_component": components.get("model"),
        "model_parameter_count": parameter_count,
        "attention_backend": attention_backend,
        "initial_model_state_digest": initial_digest,
        "objective": {"config": config.get("objective"), "component": components.get("objective")},
        "init_seed": training.get("init_seed"),
        "training_seed": training.get("training_seed"),
        "data_seed": training.get("data_seed"),
        "order_manifest_id": order_id,
        "within_source_order_policy": order_policy,
        "canonical_membership_id": membership_id,
        "tokenizer_identity": bindings.get("tokenizer"),
        "vocab_size": config["model"].get("vocab_size"),
        "data_input_identity": bindings.get("data"),
        "mixture_components": mixture_components,
        "mixture_exhaustion_policy": exhaustion,
        "mixture_scheduler": scheduler,
        "packing_policy": packing,
        "context_length": training.get("context_length"),
        "exposure_plan_identity": data_state.get("exposure_identity"),
        "data_trace_digest": data_state.get("trace_digest"),
        "per_source_exposure": per_source or None,
        "global_batch_valid_targets": training.get("global_batch_valid_targets"),
        "microbatch_sequences": training.get("microbatch_sequences"),
        "budget_targets": training["budget"]["max_valid_targets"],
        "committed_targets": committed,
        "update_boundaries_digest": boundaries_digest,
        "update_payload_receipt": payload_receipt,
        "update_payload_chain_digest": payload_chain,
        "optimizer": {"config": config.get("optimizer"), "component": components.get("optimizer")},
        "gradient_clip_norm": training.get("gradient_clip_norm"),
        "schedule": {"config": training.get("schedule"), "component": components.get("schedule")},
        "lr_policy": training.get("lr_policy"),
        "science_version": training.get("science_version"),
        "precision": training.get("precision"),
        "runtime_policy": training.get("runtime"),
        "compile": training.get("compile"),
        "activation_checkpointing": training.get("activation_checkpointing"),
        "producer_prefetch": training.get("producer_prefetch"),
        "device": training.get("device"),
        "runtime_device_name": device_names or None,
        "runtime_torch": torch_versions or None,
        "code_hash": envelope.get("code_hash"),
        "dependency_hash": envelope.get("dependency_hash"),
        "environment_digest": identity_digest(envelope["environment"])
        if envelope.get("environment") is not None
        else None,
        "evaluation_plan_digest": evaluation_plan_digest,
        "evaluator_identities": evaluator_identities,
        "checkpoint_plan_digest": checkpoint_plan_digest,
        "run_id": run_id,
        "plan_hash": execution["plan_hash"],
        "execution_hash": envelope["execution_hash"],
        "checkpoint_id": manifest.artifact_id,
        "final_model_state_digest": final_digest,
        "observed_attention_ops": observed_ops or None,
    }
    record: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "source": {
            "kind": "frozen_science_checkpoint_v1",
            "checkpoint_id": manifest.artifact_id,
            "checkpoint_manifest_sha256": compute_file_sha256(checkpoint_dir / "manifest.json"),
            "parameter_count_source": parameter_source,
        },
        "replicate": {
            "init_seed": fields["init_seed"],
            "training_seed": fields["training_seed"],
            "data_seed": fields["data_seed"],
            "order_manifest_id": fields["order_manifest_id"],
        },
        "fields": fields,
        "endpoint": {
            "committed_targets": committed,
            "budget_targets": fields["budget_targets"],
            "step": int(meta["step"]),
            "updates": updates,
            "at_budget": committed == fields["budget_targets"],
        },
        "evaluations": events,
    }
    record["evidence_digest"] = identity_digest(record)
    return record


def verify_evidence_record(record: Mapping[str, Any]) -> None:
    """Refuse an evidence record whose digest or version does not verify."""
    version = record.get("evidence_version")
    if version != EVIDENCE_VERSION and version not in LEGACY_EVIDENCE_VERSIONS:
        raise EvidenceError(f"not a {EVIDENCE_VERSION} record")
    payload = {k: v for k, v in record.items() if k != "evidence_digest"}
    if record.get("evidence_digest") != identity_digest(payload):
        raise EvidenceError("evidence record digest does not verify (altered after extraction)")
    if version == "xlm-science-run-evidence-v1":
        fields = record.get("fields") or {}
        if fields.get("order_manifest_id") != ORDER_SENTINEL or "canonical_membership_id" in fields:
            raise EvidenceError("a v1 (pre-M5) evidence record cannot carry M5 order identity")
    if version in LEGACY_EVIDENCE_VERSIONS:
        fields = record.get("fields") or {}
        if any(name in fields for name in PAYLOAD_FIELDS):
            raise EvidenceError("a pre-readiness evidence record cannot carry payload receipts")


def evidence_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    """Scientific fields, read version-aware; the stored record is never rewritten.

    A v1 (M4, pre-M5) record binds no membership: it reads as the pre-M5
    membership sentinel, exactly as a pre-M5 checkpoint extracts today.
    """
    fields = dict(record["fields"])
    if record.get("evidence_version") == "xlm-science-run-evidence-v1":
        fields.setdefault("canonical_membership_id", MEMBERSHIP_SENTINEL)
    if record.get("evidence_version") in LEGACY_EVIDENCE_VERSIONS:
        # Pre-readiness runs bound no payload receipt: unknown, never equal.
        for name in PAYLOAD_FIELDS:
            fields.setdefault(name, None)
    return fields
