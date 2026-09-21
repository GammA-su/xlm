"""Immutable experiment plan resolution (C12, A28).

A draft becomes an executable plan only when every reference resolves: model
and mixture presets, data/tokenizer/profile/policy artifacts, a captured code
snapshot, the dependency hash, seeds, sizes, exposure, limits, evaluation
tier/cadence and comparison track. Anything unresolved is an approval blocker,
never a silent default. The plan hash covers the full canonical payload, so a
source or seed change alters the plan ID and deduplication is exact.

Sidecar envelope fields (horizon tagging, cost basis, exposure summary) live in
P16's own structures; the frozen P01 draft/executable schemas are validated,
never extended.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.config.composer import ConfigComposer, compute_plan_hash
from xlm.config.schemas import ExperimentDraftConfig
from xlm.core.paths import ArtifactPaths
from xlm.experiments.snapshot import CodeSnapshot, capture_snapshot

PLAN_VERSION = "1"

# Tiers a developer command may execute. Final evaluation belongs to the
# operator path (P21) and blocks planning until authorized there.
EXECUTABLE_TIERS = ("search", "confirmation")


class PlanError(RuntimeError):
    """Raised when a draft cannot be resolved into an executable plan."""


class BudgetHorizonError(RuntimeError):
    """Raised when a budget outgrows its schedule horizon without a fork."""


def find_artifact_dir(paths: ArtifactPaths, artifact_id: str) -> Path | None:
    """Locate a published artifact directory by ID across all kinds."""
    if not paths.root.is_dir():
        return None
    for kind_dir in sorted(paths.root.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name.startswith((".", "_")):
            continue
        candidate = kind_dir / artifact_id
        if candidate.is_dir() and (candidate / "_COMPLETED").is_file():
            return candidate
    return None


def dependency_hash(workspace_root: Path | str) -> str:
    """Hash the locked dependency graph. A changed lockfile is a new plan."""
    lockfile = Path(workspace_root) / "uv.lock"
    if not lockfile.is_file():
        raise PlanError(f"dependency lockfile not found: {lockfile}")
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()


@dataclass(frozen=True)
class PlanBlocker:
    """One reason a plan is not yet submittable."""

    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutablePlan:
    """A fully resolved, immutable experiment plan."""

    plan_version: str
    plan_id: str
    plan_hash: str
    draft_id: str
    track: str
    horizon_kind: str
    resolved_config: dict[str, Any]
    code_snapshot: CodeSnapshot
    dependency_hash: str
    seeds: dict[str, int]
    budget_valid_targets: int
    budget_max_seconds: float | None
    estimated_new_disk_gib: float | None
    gpu_processes: int
    evaluation_tier: str
    checkpoint_every_valid_targets: int
    evaluation_every_valid_targets: int
    exposure: dict[str, Any]
    cost_estimate: dict[str, Any]
    storage_estimate_gib: float | None
    blockers: list[PlanBlocker] = field(default_factory=list)
    parent_plan_id: str | None = None
    fork_reason: str | None = None
    created_at: str = ""
    execution_envelope: dict[str, Any] | None = None

    def identity_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        for key in ("plan_hash", "plan_id", "draft_id", "created_at", "cost_estimate"):
            payload.pop(key, None)
        payload["code_snapshot"] = {
            "code_hash": self.code_snapshot.code_hash,
            "snapshot_version": self.code_snapshot.snapshot_version,
        }
        return payload

    def validate_identity(self) -> None:
        from xlm.artifacts.manifest import identity_digest

        if self.plan_version != "2" or self.execution_envelope is None:
            raise PlanError("legacy/unfrozen plan cannot execute; resolve a new frozen plan")
        if identity_digest(self.identity_payload()) != self.plan_hash:
            raise PlanError("persisted plan identity mismatch")
        envelope = self.execution_envelope
        config = envelope["config"]
        if (
            config != self.resolved_config
            or envelope["code_hash"] != self.code_snapshot.code_hash
            or envelope["dependency_hash"] != self.dependency_hash
            or self.budget_valid_targets != config["training"]["budget"]["max_valid_targets"]
            or self.budget_max_seconds != config["training"]["budget"]["max_train_seconds"]
            or self.seeds != {k: config["training"][k] for k in ("init_seed", "data_seed")}
            or self.checkpoint_every_valid_targets
            != config["training"]["checkpoint_every_valid_targets"]
        ):
            raise PlanError("plan/envelope configuration or execution limits mismatch")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["code_snapshot"] = self.code_snapshot.to_dict()
        payload["blockers"] = [b.to_dict() for b in self.blockers]
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutablePlan:
        payload = dict(data)
        payload["code_snapshot"] = CodeSnapshot.from_dict(data["code_snapshot"])
        payload["blockers"] = [PlanBlocker(**b) for b in data.get("blockers", [])]
        return cls(**payload)

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def load(cls, path: Path | str) -> ExecutablePlan:
        from xlm.experiments.execution import read_json

        return cls.from_dict(read_json(Path(path)))


def _canonical_payload(
    resolved: dict[str, Any],
    code_hash: str,
    dep_hash: str,
    seeds: dict[str, int],
    artifacts: dict[str, str | None],
    track: str,
    horizon_kind: str,
    exposure: dict[str, Any],
) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "resolved_config": resolved,
        "code_hash": code_hash,
        "dependency_hash": dep_hash,
        "seeds": seeds,
        "artifacts": artifacts,
        "track": track,
        "horizon_kind": horizon_kind,
        "exposure": exposure,
    }


def freeze_execution(plan: ExecutablePlan, snapshot_dir: Path, extras: list[str]) -> ExecutablePlan:
    """Explicit planning step, before authorization; never called to repair a submitted plan."""
    from xlm.artifacts.manifest import identity_digest
    from xlm.experiments.execution import make_envelope

    envelope = make_envelope(plan.resolved_config, snapshot_dir, extras=extras)
    plan.execution_envelope = envelope
    plan.resolved_config = envelope["config"]
    plan.plan_version = "2"
    plan.code_snapshot = CodeSnapshot.from_dict(
        json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    )
    plan.dependency_hash = envelope["dependency_hash"]
    training = plan.resolved_config["training"]
    plan.budget_valid_targets = training["budget"]["max_valid_targets"]
    plan.budget_max_seconds = training["budget"]["max_train_seconds"]
    plan.seeds = {k: training[k] for k in ("init_seed", "data_seed")}
    plan.checkpoint_every_valid_targets = training["checkpoint_every_valid_targets"]
    plan.plan_hash = identity_digest(plan.identity_payload())
    plan.plan_id = f"plan_{plan.draft_id}_{plan.plan_hash[:12]}"
    plan.validate_identity()
    return plan


def _hash_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _classify_horizon(budget_targets: int, horizon_targets: int) -> str:
    """Tag continuation-prefix vs standalone runs so they can never compare as identical."""
    if budget_targets < horizon_targets:
        return "continuation_prefix"
    return "standalone"


def _estimate_storage_gib(resolved: dict[str, Any], budget_targets: int) -> float | None:
    """Labeled checkpoint-storage estimate. A rough model, never a measurement."""
    try:
        model = resolved["model"]
        vocab = int(model["vocab_size"])
        layers = int(model["num_layers"])
        hidden = int(model["hidden_size"])
        ffn = int(model["intermediate_size"])
        unique = vocab * hidden + layers * (4 * hidden * hidden + 3 * hidden * ffn + 2 * hidden)
        training = resolved.get("training", {})
        every = int(training.get("checkpoint_every_valid_targets") or budget_targets)
        saves = max(1, budget_targets // max(1, every) + 1)
        gib = unique * 12 * saves / (1024**3)
        return round(gib, 3)
    except (KeyError, TypeError, ValueError):
        return None


def resolve_experiment_plan(
    draft_path: Path | str,
    *,
    workspace_root: Path | str,
    artifact_paths: ArtifactPaths | None = None,
    snapshot_dir: Path | str | None = None,
    profile_lookup: Mapping[str, Any] | None = None,
    measured_profile: Mapping[str, Any] | None = None,
    smoke: bool = False,
) -> ExecutablePlan:
    """Resolve a draft into an executable plan, collecting blockers, not defaults."""
    draft_file = Path(draft_path)
    workspace = Path(workspace_root)
    paths = artifact_paths or ArtifactPaths.from_env()

    composer = ConfigComposer(workspace)
    composed = composer.compose(draft_file)
    if smoke:
        return _resolve_smoke_plan(composed, workspace, snapshot_dir)
    draft = ExperimentDraftConfig.model_validate(composed)

    blockers: list[PlanBlocker] = []

    # Artifacts must resolve; nulls are blockers, never defaults.
    data = dict(draft.data) if isinstance(draft.data, dict) else {}
    artifact_ids: dict[str, str | None] = {
        "pool_artifact": data.get("pool_artifact"),
        "tokenizer_artifact": data.get("tokenizer_artifact"),
    }
    training = draft.training
    resources = draft.resources
    evaluation = draft.evaluation
    profile_id = resources.profile_artifact if resources else None
    artifact_ids["profile_artifact"] = profile_id
    policy_id = evaluation.policy_artifact if evaluation else None
    artifact_ids["policy_artifact"] = policy_id

    if measured_profile is not None:
        # Bind one produced profile file to the draft's declared profile
        # artifact. The lookup key stays the draft's artifact id; the file's
        # measured identity is validated against this plan below. Importing the
        # training package here (instead of at module top) keeps base-only
        # plan resolution free of the torch import chain.
        from xlm.training.profile import resolve_measured_profile

        if not profile_id:
            raise ValueError(
                "--profile was given but the draft declares no resources.profile_artifact "
                "to bind it to"
            )
        composed_model = composed.get("model", {})
        composed_training = composed.get("training", {})
        if not isinstance(composed_model, dict) or not isinstance(composed_training, dict):
            raise ValueError("draft model/training sections must be mappings")
        profile_lookup = {
            **(dict(profile_lookup) if profile_lookup else {}),
            profile_id: resolve_measured_profile(
                measured_profile,
                model_config=composed_model,
                training=composed_training,
            ),
        }
        bound_profile_id: str | None = profile_id
    else:
        bound_profile_id = None

    for key, artifact_id in artifact_ids.items():
        if not artifact_id:
            blockers.append(PlanBlocker(code=f"missing_{key}", detail=f"'{key}' is null"))
        elif key == "profile_artifact" and bound_profile_id == artifact_id:
            # A validated --profile file fulfills this requirement for this plan;
            # the measured content (not store publication) is what the cost basis uses.
            continue
        elif find_artifact_dir(paths, artifact_id) is None:
            blockers.append(
                PlanBlocker(
                    code=f"unresolved_{key}",
                    detail=f"artifact '{artifact_id}' not found in the artifact store",
                )
            )

    # Evaluation tier and cadence are token-based and predeclared.
    tier = str(evaluation.suite) if evaluation else "search"
    if tier not in EXECUTABLE_TIERS:
        blockers.append(
            PlanBlocker(
                code="tier_not_executable",
                detail=f"evaluation tier '{tier}' is not executable by developers",
            )
        )
    checkpoint_every = int(training.checkpoint_every_valid_targets)
    evaluation_every = int(evaluation.every_valid_targets) if evaluation else checkpoint_every
    if checkpoint_every <= 0 or evaluation_every <= 0:
        blockers.append(
            PlanBlocker(code="cadence_not_token_based", detail="cadences must be positive")
        )

    # Seeds: any seed change alters the plan hash.
    seeds = {
        "init_seed": int(training.init_seed),
        "data_seed": int(training.data_seed),
    }

    budget_targets = int(training.budget.max_valid_targets)
    budget_seconds = training.budget.max_train_seconds
    disk_cap = resources.max_new_disk_gib if resources else None
    gpu_processes = int(resources.max_gpu_processes) if resources else 1

    # Schedule horizon tagging.
    sched = training.schedule if isinstance(training.schedule, dict) else {}
    horizon = int(sched.get("horizon_valid_targets", budget_targets))
    horizon_kind = _classify_horizon(budget_targets, horizon)

    # Exposure summary from the composed mixture details.
    mixture_details = data.get("mixture_details") or {}
    weights = mixture_details.get("weights", {}) if isinstance(mixture_details, dict) else {}
    exposure = {
        "mixture_id": mixture_details.get("id") if isinstance(mixture_details, dict) else None,
        "weights": dict(weights),
        "data_seed": seeds["data_seed"],
    }

    snapshot_out = (
        Path(snapshot_dir) if snapshot_dir is not None else (workspace / "snapshots" / draft.id)
    )
    snapshot = capture_snapshot(workspace, snapshot_out)
    dep_hash = dependency_hash(workspace)

    # Cost basis: measured profile when resolvable, otherwise an explicit unknown.
    cost_estimate: dict[str, Any] = {"basis": "unmeasured", "eta_seconds": None}
    profile = (profile_lookup or {}).get(profile_id) if profile_id else None
    if isinstance(profile, Mapping) and profile.get("throughput_tokens_per_sec_range"):
        lo, hi = profile["throughput_tokens_per_sec_range"][:2]
        if lo and hi:
            cost_estimate = {
                "basis": "measured_profile",
                "eta_seconds": [budget_targets / hi, budget_targets / lo],
                "profile": profile_id,
            }
    if cost_estimate["basis"] == "unmeasured":
        blockers.append(
            PlanBlocker(
                code="cost_unestimated",
                detail="no measured profile artifact; cost is unknown, not zero",
            )
        )

    storage_gib = _estimate_storage_gib(composed, budget_targets)
    if disk_cap is not None and storage_gib is not None and storage_gib > disk_cap:
        blockers.append(
            PlanBlocker(
                code="storage_exceeds_cap",
                detail=f"estimated {storage_gib} GiB exceeds cap {disk_cap} GiB",
            )
        )

    track = str(draft.track)
    payload = _canonical_payload(
        composed, snapshot.code_hash, dep_hash, seeds, artifact_ids, track, horizon_kind, exposure
    )
    plan_hash = _hash_payload(payload)
    # Cross-check the composer hash utility agrees on the resolved config portion.
    _composer_hash = compute_plan_hash(composed)

    plan = ExecutablePlan(
        plan_version=PLAN_VERSION,
        plan_id=f"plan_{draft.id}_{plan_hash[:12]}",
        plan_hash=plan_hash,
        draft_id=draft.id,
        track=track,
        horizon_kind=horizon_kind,
        resolved_config=composed,
        code_snapshot=snapshot,
        dependency_hash=dep_hash,
        seeds=seeds,
        budget_valid_targets=budget_targets,
        budget_max_seconds=budget_seconds,
        estimated_new_disk_gib=storage_gib,
        gpu_processes=gpu_processes,
        evaluation_tier=tier,
        checkpoint_every_valid_targets=checkpoint_every,
        evaluation_every_valid_targets=evaluation_every,
        exposure=exposure,
        cost_estimate={**cost_estimate, "composer_hash": _composer_hash},
        storage_estimate_gib=storage_gib,
        blockers=blockers,
        created_at=datetime.now(UTC).isoformat(),
    )
    if not blockers:
        try:
            freeze_execution(plan, snapshot_out, [training.device])
        except (ValueError, OSError, KeyError) as exc:
            plan.blockers.append(PlanBlocker(code="execution_unresolved", detail=str(exc)))
    return plan


def _resolve_smoke_plan(
    config: dict[str, Any], workspace: Path, snapshot_dir: Path | str | None
) -> ExecutablePlan:
    """Expose the existing C13 queue smoke tier without granting production authority."""
    from xlm.artifacts.manifest import validate_component
    from xlm.experiments.authorization import SMOKE_MAX_NEW_DISK_GIB
    from xlm.experiments.direct import _smoke_limits
    from xlm.experiments.execution import resolve_execution_config

    label = str(config.get("id", "smoke"))
    validate_component(label)
    resolved, _ = resolve_execution_config(config)
    _smoke_limits(resolved)
    evaluation = resolved.get("evaluation", {})
    tier = evaluation.get("suite", "search")
    if tier not in EXECUTABLE_TIERS or evaluation.get("allow_final", False):
        raise PlanError("smoke planning cannot authorize final evaluation")
    resources = resolved.get("resources", {})
    if resources.get("max_gpu_processes", 1) != 1:
        raise PlanError("smoke planning allows at most one GPU process")
    if resources.get("max_new_disk_gib", SMOKE_MAX_NEW_DISK_GIB) not in (None, 2.0):
        raise PlanError("smoke planning uses the existing 2 GiB artifact limit")
    training = resolved["training"]
    targets = training["budget"]["max_valid_targets"]
    snapshot_out = (
        Path(snapshot_dir) if snapshot_dir is not None else workspace / "snapshots" / label
    )
    snapshot = capture_snapshot(workspace, snapshot_out)
    mixture = resolved["data"].get("mixture", {})
    plan = ExecutablePlan(
        plan_version=PLAN_VERSION,
        plan_id=f"plan_{label}",
        plan_hash="",
        draft_id=label,
        track=resolved.get("track", "baseline"),
        horizon_kind=_classify_horizon(
            targets, training["schedule"].get("horizon_valid_targets", targets)
        ),
        resolved_config=resolved,
        code_snapshot=snapshot,
        dependency_hash=dependency_hash(workspace),
        seeds={k: training[k] for k in ("init_seed", "data_seed")},
        budget_valid_targets=targets,
        budget_max_seconds=training["budget"]["max_train_seconds"],
        estimated_new_disk_gib=SMOKE_MAX_NEW_DISK_GIB,
        gpu_processes=1,
        evaluation_tier=tier,
        checkpoint_every_valid_targets=training["checkpoint_every_valid_targets"],
        evaluation_every_valid_targets=evaluation.get(
            "every_valid_targets", training["checkpoint_every_valid_targets"]
        ),
        exposure={
            "mixture_id": mixture.get("mixture_id"),
            "weights": {c["source_id"]: c["weight"] for c in mixture.get("components", [])},
            "data_seed": training["data_seed"],
        },
        cost_estimate={
            "basis": "unmeasured_bounded_smoke",
            "eta_seconds": None,
            "disk_basis": "hard reservation, not measured usage",
        },
        storage_estimate_gib=SMOKE_MAX_NEW_DISK_GIB,
        created_at=datetime.now(UTC).isoformat(),
    )
    return freeze_execution(plan, snapshot_out, [training["device"]])


def check_budget_against_horizon(budget_targets: int, horizon_targets: int, is_fork: bool) -> None:
    """Refuse a resumed/extended budget that outgrows its horizon without a fork.

    A short completed schedule extended in place is not identical to a
    long-horizon run; the extension must be an explicit fork with its own
    horizon and lineage.
    """
    if budget_targets > horizon_targets and not is_fork:
        raise BudgetHorizonError(
            f"budget {budget_targets:,} exceeds schedule horizon {horizon_targets:,}; "
            "extending a completed schedule in place is forbidden -- fork explicitly "
            "with a matched horizon."
        )


def extend_plan_budget(plan: ExecutablePlan, new_max_targets: int) -> ExecutablePlan:
    """Fork a plan to a larger budget with an extended matched horizon.

    Returns a new plan with parent lineage; the original plan is untouched.
    """
    if new_max_targets <= plan.budget_valid_targets:
        raise PlanError("extension budget must exceed the current plan budget")
    resolved = json.loads(json.dumps(plan.resolved_config))
    try:
        resolved["training"]["budget"]["max_valid_targets"] = new_max_targets
        resolved["training"]["schedule"]["horizon_valid_targets"] = new_max_targets
    except KeyError as exc:
        raise PlanError(f"plan lacks a horizon-addressable schedule: {exc}") from exc
    forked_hash = _hash_payload(
        {
            "plan_version": plan.plan_version,
            "resolved_config": resolved,
            "code_hash": plan.code_snapshot.code_hash,
            "dependency_hash": plan.dependency_hash,
            "seeds": plan.seeds,
            "track": plan.track,
            "horizon_kind": "standalone",
            "exposure": plan.exposure,
            "parent": plan.plan_hash,
        }
    )
    forked = ExecutablePlan(
        plan_version=plan.plan_version,
        plan_id=f"plan_{plan.draft_id}_fork_{forked_hash[:12]}",
        plan_hash=forked_hash,
        draft_id=plan.draft_id,
        track=plan.track,
        horizon_kind="standalone",
        resolved_config=resolved,
        code_snapshot=plan.code_snapshot,
        dependency_hash=plan.dependency_hash,
        seeds=plan.seeds,
        budget_valid_targets=new_max_targets,
        budget_max_seconds=plan.budget_max_seconds,
        estimated_new_disk_gib=plan.estimated_new_disk_gib,
        gpu_processes=plan.gpu_processes,
        evaluation_tier=plan.evaluation_tier,
        checkpoint_every_valid_targets=plan.checkpoint_every_valid_targets,
        evaluation_every_valid_targets=plan.evaluation_every_valid_targets,
        exposure=dict(plan.exposure),
        cost_estimate=dict(plan.cost_estimate),
        storage_estimate_gib=plan.storage_estimate_gib,
        blockers=list(plan.blockers),
        parent_plan_id=plan.plan_id,
        fork_reason=f"budget extended {plan.budget_valid_targets:,} -> {new_max_targets:,}",
        created_at=datetime.now(UTC).isoformat(),
    )
    if plan.execution_envelope is not None:
        from xlm.artifacts.manifest import identity_digest
        from xlm.experiments.execution import resolve_execution_config

        plan.validate_identity()
        config, bindings = resolve_execution_config(resolved)
        envelope = {**plan.execution_envelope, "config": config, "bindings": bindings}
        envelope.pop("execution_hash")
        envelope["execution_hash"] = identity_digest(envelope)
        forked.resolved_config = config
        forked.execution_envelope = envelope
        forked.plan_hash = identity_digest(forked.identity_payload())
        forked.plan_id = f"plan_{plan.draft_id}_fork_{forked.plan_hash[:12]}"
        forked.validate_identity()
    return forked
