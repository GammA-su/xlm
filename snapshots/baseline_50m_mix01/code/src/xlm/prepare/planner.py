"""Dry planning: approvals, staleness and downstream invalidation (P22).

Planning is read-only. It reports every stage as ready, blocked (with the
missing approval or input named) or stale (with the changed input named), and
propagates staleness downstream through declared invalidations so the dry plan
explains the exact rerun set before anything executes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.prepare.config import PrepareConfig, PrepareStageSpec

KNOWN_VARIABLES = ("repo", "home", "output_root", "config_dir")


class PlanError(RuntimeError):
    """Raised when a prepare plan cannot be evaluated safely."""


def resolve_variables(text: str, mapping: dict[str, str], *, where: str = "config") -> str:
    """Substitute {repo}/{home}/{output_root}/{config_dir}; refuse unknowns."""
    result = text
    for key, value in mapping.items():
        result = result.replace("{" + key + "}", value)
    import re as _re

    leftover = sorted(set(_re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", result)))
    if leftover:
        raise PlanError(
            f"unknown variables {leftover} in {where}; known: {list(KNOWN_VARIABLES)}. "
            "Declare paths explicitly instead of guessing."
        )
    return result


def build_variable_mapping(
    config: PrepareConfig, config_path: Path, home: Path, repo_root: Path
) -> dict[str, str]:
    """Resolve the variable mapping, expanding output_root against the rest."""
    base = {
        "repo": str(repo_root),
        "home": str(home),
        "config_dir": str(config_path.parent),
    }
    output_root = resolve_variables(config.output_root, base, where="output_root")
    return {**base, "output_root": output_root}


def find_repo_root(start: Path) -> Path:
    """Locate the repository root by the xlm package layout, without guessing."""
    candidate = start.resolve()
    for _ in range(8):
        if (candidate / "src" / "xlm" / "__init__.py").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    raise PlanError(f"cannot locate the repository root from '{start}'")


def hash_stage_inputs(stage: PrepareStageSpec, variables: dict[str, str], config_dir: Path) -> str:
    """Hash a stage's command plus watched input files to detect staleness."""
    digest = hashlib.sha256()
    digest.update(json.dumps(stage.command, sort_keys=True).encode("utf-8"))
    digest.update(stage.kind.encode("utf-8"))
    for raw in stage.watched_inputs:
        resolved = resolve_variables(raw, variables, where=f"stage '{stage.stage_id}' inputs")
        path = Path(resolved)
        if not path.is_absolute():
            path = config_dir / path
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        else:
            digest.update(f"missing:{resolved}".encode())
    return digest.hexdigest()


@dataclass
class StagePlan:
    """Dry-plan verdict for one stage."""

    stage_id: str
    kind: str
    status: str  # 'ready' | 'blocked' | 'stale' | 'check-only'
    reasons: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    rerun_because: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparePlan:
    """The full dry plan: per-stage verdicts plus totals."""

    config_id: str
    output_root: str
    stages: list[StagePlan]
    approvals: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "output_root": self.output_root,
            "approvals": self.approvals,
            "stages": [s.to_dict() for s in self.stages],
            "counts": {
                status: sum(1 for s in self.stages if s.status == status)
                for status in ("blocked", "stale", "pending", "reusable", "check-only")
            },
        }


def plan_prepare(
    config: PrepareConfig,
    config_path: Path,
    home: Path,
    repo_root: Path,
    prior_state: dict[str, Any] | None = None,
) -> PreparePlan:
    """Evaluate every stage without executing anything."""
    variables = build_variable_mapping(config, config_path, home, repo_root)
    prior_stages: dict[str, Any] = {}
    if isinstance(prior_state, dict) and isinstance(prior_state.get("stages"), dict):
        prior_stages = prior_state["stages"]

    # Statuses: blocked (approval/input errors), stale (inputs changed),
    # pending (will run: outputs missing or never verified), reusable (verified
    # outputs, unchanged inputs), check-only (read-only, always runs).
    plans: list[StagePlan] = []
    for stage in config.stages:
        reasons: list[str] = []
        for approval in stage.approvals:
            if not config.approvals.get(approval, False):
                reasons.append(
                    f"approval '{approval}' is missing; stage '{stage.stage_id}' is blocked"
                )
        try:
            command = [
                resolve_variables(token, variables, where=f"stage '{stage.stage_id}'")
                for token in stage.command
            ]
            inputs_hash = hash_stage_inputs(stage, variables, config_path.parent)
        except PlanError as exc:
            reasons.append(str(exc))
            command, inputs_hash = list(stage.command), "unhashable"

        status = "pending"
        rerun_because = ""
        if reasons:
            status = "blocked"
        elif stage.check_only:
            status = "check-only"
        else:
            previous = prior_stages.get(stage.stage_id, {})
            outputs_ok = stage_outputs_verified(stage, variables, config_path.parent)
            if not outputs_ok:
                if previous.get("status") in ("succeeded", "partial"):
                    rerun_because = "previous outputs incomplete; resuming this stage"
                else:
                    rerun_because = "outputs missing; will run"
            elif previous.get("inputs_hash") != inputs_hash:
                status = "stale"
                rerun_because = "inputs changed since the last verified run"
            else:
                status = "reusable"
                rerun_because = "outputs verified and inputs unchanged; will reuse"
        plans.append(
            StagePlan(
                stage_id=stage.stage_id,
                kind=stage.kind,
                status=status,
                reasons=reasons,
                command=command,
                rerun_because=rerun_because,
            )
        )

    # A stage reruns when it is stale or pending. Verified downstream stages of
    # a rerunning stage become stale through declared invalidations, so lineage
    # staleness is exact. A blocked upstream additionally blocks downstream
    # stages that have nothing verified to reuse.
    by_id = {plan.stage_id: plan for plan in plans}
    specs = {stage.stage_id: stage for stage in config.stages}
    rerunning = {p.stage_id for p in plans if p.status in ("stale", "pending")}
    blocked = {p.stage_id for p in plans if p.status == "blocked"}
    changed = True
    while changed:
        changed = False
        for plan in plans:
            if plan.stage_id in rerunning or plan.stage_id in blocked:
                continue
            upstream_rerunning = sorted(
                s for s in specs if plan.stage_id in specs[s].invalidates and s in rerunning
            )
            upstream_blocked = sorted(
                s for s in specs if plan.stage_id in specs[s].invalidates and s in blocked
            )
            if plan.status == "reusable" and upstream_rerunning:
                plan.status = "stale"
                plan.rerun_because = (
                    f"upstream stage(s) {upstream_rerunning} will rerun; "
                    "downstream lineage is stale"
                )
                rerunning.add(plan.stage_id)
                changed = True
            elif plan.status == "pending" and upstream_blocked and not upstream_rerunning:
                plan.status = "blocked"
                plan.reasons.append(
                    f"upstream stage(s) {upstream_blocked} blocked with nothing to reuse"
                )
                blocked.add(plan.stage_id)
                changed = True
    _ = by_id

    return PreparePlan(
        config_id=config.id,
        output_root=variables["output_root"],
        stages=plans,
        approvals=dict(config.approvals),
    )


def stage_outputs_verified(
    stage: PrepareStageSpec, variables: dict[str, str], config_dir: Path
) -> bool:
    if not stage.outputs:
        return False
    for raw in stage.outputs:
        resolved = resolve_variables(raw, variables, where=f"stage '{stage.stage_id}' outputs")
        path = Path(resolved)
        if not path.is_absolute():
            path = config_dir / path
        if not path.exists():
            return False
    return True
