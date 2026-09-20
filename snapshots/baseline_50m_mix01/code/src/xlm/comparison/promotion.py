"""Promotion gates, next-size drafts and experiment matrices (C12, A32).

Gate rules are versioned and frozen in advance: the suggested materiality
thresholds (+1 suite point, or at least 10% lower measured compute to a
declared target) are inputs, and passing different scores never rewrites them.
Promotion needs evidence, emits a from-scratch next-size draft with comparison
lineage and a projected resource plan, and launches nothing: no resized
weights, no silent budget increase, no authorization, no final suite.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GATE_RULES_VERSION = "1"
PROMOTION_VERSION = "1"


class PromotionError(RuntimeError):
    """Raised when promotion is requested without the required evidence or gates."""


@dataclass(frozen=True)
class PromotionGates:
    """Frozen materiality rules. Policy choices, not proof of novelty."""

    gate_version: str = GATE_RULES_VERSION
    min_suite_delta: float = 1.0
    min_compute_saving: float = 0.10
    min_seeds: int = 2
    require_ci_excludes_zero: bool = True
    max_task_regression: float = 0.5
    ci_condition: str = "search_stage_decision_support_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionEvidence:
    """The evidence a promotion decision consumes."""

    baseline_run_id: str
    candidate_run_id: str
    track: str
    suite_index_delta: float | None
    index_ci_lo: float | None
    index_ci_hi: float | None
    task_deltas: dict[str, float]
    compute_saving_fraction: float | None
    n_seeds_baseline: int
    n_seeds_candidate: int
    comparisons_examined: int = 1
    tuning_trials: int = 0
    scale_hypothesis_exceptions: tuple[str, ...] = ()
    target: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scale_hypothesis_exceptions"] = list(self.scale_hypothesis_exceptions)
        return payload


@dataclass
class PromotionDecision:
    """Whether the gates pass, with every reason and warning recorded."""

    promote: bool
    gate_version: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    multiplicity_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_promotion(
    evidence: PromotionEvidence,
    gates: PromotionGates,
    eligible: bool,
    eligibility_reasons: Sequence[str] = (),
) -> PromotionDecision:
    """Apply the frozen gates to promotion evidence.

    Search-stage CIs are decision support, not confirmatory proof after adaptive
    selection: the multiplicity note records how many comparisons and tuning
    trials fed the decision, and a formal significance claim is never produced
    from an adaptive sweep.
    """
    decision = PromotionDecision(promote=False, gate_version=gates.gate_version)
    if not eligible:
        decision.reasons.append(
            "comparison is ineligible for its track: " + "; ".join(eligibility_reasons)
        )
        return decision
    if evidence.suite_index_delta is None and evidence.compute_saving_fraction is None:
        decision.reasons.append(
            "promotion requires evidence: neither a suite delta nor a compute saving was supplied"
        )
        return decision

    suite_pass = (
        evidence.suite_index_delta is not None
        and evidence.suite_index_delta >= gates.min_suite_delta
    )
    compute_pass = (
        evidence.compute_saving_fraction is not None
        and evidence.compute_saving_fraction >= gates.min_compute_saving
    )
    if evidence.suite_index_delta is not None:
        decision.reasons.append(
            f"suite delta {evidence.suite_index_delta:+.3f} vs gate "
            f"{gates.min_suite_delta:+.3f}: {'pass' if suite_pass else 'fail'}"
        )
    if evidence.compute_saving_fraction is not None:
        decision.reasons.append(
            f"compute saving {evidence.compute_saving_fraction:+.3%} vs gate "
            f"{gates.min_compute_saving:.0%}: {'pass' if compute_pass else 'fail'}"
        )
    if not (suite_pass or compute_pass):
        decision.reasons.append("no materiality gate passed")
        return decision

    seeds_ok = (
        evidence.n_seeds_baseline >= gates.min_seeds
        and evidence.n_seeds_candidate >= gates.min_seeds
    )
    if not seeds_ok:
        decision.reasons.append(
            f"seed requirement unmet: baseline has {evidence.n_seeds_baseline}, candidate "
            f"has {evidence.n_seeds_candidate}, gate needs {gates.min_seeds} each"
        )
        return decision

    if gates.require_ci_excludes_zero and evidence.suite_index_delta is not None:
        if evidence.index_ci_lo is None or evidence.index_ci_hi is None:
            decision.reasons.append("gate requires a suite-index interval and none was supplied")
            return decision
        if not (evidence.index_ci_lo > 0):
            decision.reasons.append(
                f"suite-index interval [{evidence.index_ci_lo:+.3f}, "
                f"{evidence.index_ci_hi:+.3f}] does not exclude zero"
            )
            return decision

    for task, delta in sorted(evidence.task_deltas.items()):
        if delta < -gates.max_task_regression:
            decision.warnings.append(
                f"material regression on '{task}': {delta:+.3f} beyond "
                f"{gates.max_task_regression:.3f}"
            )
    decision.multiplicity_note = (
        f"decision support only: {evidence.comparisons_examined} comparison(s) and "
        f"{evidence.tuning_trials} tuning trial(s) examined; "
        f"{gates.ci_condition}; no formal significance claim from adaptive selection"
    )
    if evidence.scale_hypothesis_exceptions:
        decision.warnings.append(
            "scale-hypothesis exceptions recorded: "
            + "; ".join(evidence.scale_hypothesis_exceptions)
        )
    decision.promote = True
    decision.reasons.append("all promotion gates passed")
    return decision


def next_size_draft(
    size: str,
    baseline_plan: Mapping[str, Any],
    decision: PromotionDecision,
    candidate_run_id: str,
    output_path: Path | str,
    *,
    throughput_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Emit a from-scratch next-size experiment draft with comparison lineage.

    The draft validates against the frozen ``ExperimentDraftConfig`` schema, so
    comparison lineage lives in a sidecar file rather than as extra draft keys.
    The draft initializes from scratch (fresh init seed, no resized weights),
    keeps the candidate's data/objective/optimizer/schedule shape, projects
    resources, and authorizes nothing. Supported sizes are the reference
    150m/300m shapes; anything else is refused rather than reshaped.
    """
    if size not in ("150m", "300m"):
        raise PromotionError(f"promotion supports the reference 150m/300m shapes, not '{size}'")
    if not decision.promote:
        raise PromotionError(
            "promotion draft requires a passing decision; " + "; ".join(decision.reasons)
        )
    resolved = baseline_plan.get("resolved_config", {})
    training = dict(resolved.get("training", {}))
    budget = dict(training.get("budget", {}))

    draft_id = f"promoted_{size}_from_{candidate_run_id}"
    draft = {
        "schema_version": 1,
        "kind": "experiment_draft",
        "id": draft_id,
        "status": "draft_requires_artifacts_and_approval",
        "track": baseline_plan.get("track", "baseline"),
        "model": {"preset": size},
        "data": dict(resolved.get("data", {})),
        "objective": dict(resolved.get("objective", {})),
        "optimizer": dict(resolved.get("optimizer", {})),
        "training": {
            **training,
            "budget": budget,
            "init_seed": int(training.get("init_seed", 101)) + 1000,
        },
        "evaluation": {
            "suite": "search",
            "policy_artifact": None,
            "every_valid_targets": 16000000,
            "checkpoint_selection": "last_at_declared_budget",
            "allow_final": False,
        },
        "resources": {"profile_artifact": None, "max_new_disk_gib": None, "max_gpu_processes": 1},
        "authorization": {"state": "not_authorized", "plan_hash": None},
    }
    lineage: dict[str, Any] = {
        "promotion_version": PROMOTION_VERSION,
        "gate_version": decision.gate_version,
        "draft_id": draft_id,
        "candidate_run_id": candidate_run_id,
        "from_scratch": True,
        "resized_weights": False,
        "reasons": decision.reasons,
        "warnings": decision.warnings,
    }
    if throughput_range is not None:
        lo, hi = throughput_range
        budget_targets = int(budget.get("max_valid_targets", 0))
        if budget_targets > 0 and lo > 0 and hi > 0:
            lineage["projected_eta_seconds"] = [budget_targets / hi, budget_targets / lo]
            lineage["cost_basis"] = "declared_throughput_estimate"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lineage_path = out.with_name(out.stem + ".lineage.json")
    lineage_path.write_text(
        json.dumps(lineage, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return draft


def data_architecture_factorial(
    mixture_presets: Sequence[str],
    model_presets: Sequence[str],
    budget_targets: int,
    schedule_horizon_targets: int,
    output_dir: Path | str,
) -> list[dict[str, Any]]:
    """Emit a data x architecture factorial matrix of draft dicts (plans only)."""
    if not mixture_presets or not model_presets:
        raise PromotionError("factorial matrix needs at least one mixture and one model")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    drafts: list[dict[str, Any]] = []
    for mixture in mixture_presets:
        for model_preset in model_presets:
            draft_id = f"factorial_{model_preset}_{mixture}"
            draft = {
                "schema_version": 1,
                "kind": "experiment_draft",
                "id": draft_id,
                "status": "draft_requires_artifacts_and_approval",
                "track": "baseline",
                "model": {"preset": model_preset},
                "data": {"mixture_preset": mixture},
                "objective": {"type": "cross_entropy", "version": "1"},
                "optimizer": {"type": "adamw", "lr": 0.001},
                "training": {
                    "device": "cuda",
                    "precision": "bf16_fp32_master",
                    "context_length": 512,
                    "global_batch_valid_targets": 65536,
                    "budget": {"max_valid_targets": budget_targets},
                    "schedule": {
                        "type": "warmup_cosine",
                        "horizon_valid_targets": schedule_horizon_targets,
                    },
                    "init_seed": 101,
                    "data_seed": 20260918,
                    "checkpoint_every_valid_targets": 16000000,
                },
                "evaluation": {
                    "suite": "search",
                    "every_valid_targets": 16000000,
                    "allow_final": False,
                },
                "resources": {"max_gpu_processes": 1},
                "authorization": {"state": "not_authorized", "plan_hash": None},
            }
            (out / f"{draft_id}.json").write_text(
                json.dumps(draft, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
            drafts.append(draft)
    return drafts


def two_idea_ablation_matrix(
    idea_a: str,
    idea_b: str,
    base_draft: Mapping[str, Any],
    output_dir: Path | str,
) -> list[dict[str, Any]]:
    """Emit the four cells of a two-idea ablation (neither/A/B/both), plans only."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    for label, ideas in (
        ("neither", []),
        ("a_only", [idea_a]),
        ("b_only", [idea_b]),
        ("both", [idea_a, idea_b]),
    ):
        draft = json.loads(json.dumps(dict(base_draft), default=str))
        draft["id"] = f"{draft.get('id', 'ablation')}_{label}"
        draft["status"] = "draft_requires_artifacts_and_approval"
        draft["authorization"] = {"state": "not_authorized", "plan_hash": None}
        (out / f"{draft['id']}.json").write_text(
            json.dumps(draft, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        cells.append(draft)
        matrix.append(
            {
                "cell": label,
                "idea_a": idea_a,
                "idea_b": idea_b,
                "ideas": ideas,
                "draft_id": draft["id"],
            }
        )
    (out / "ablation_matrix.json").write_text(
        json.dumps(matrix, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return cells


def decision_fingerprint(decision: PromotionDecision) -> str:
    """Stable fingerprint of a promotion decision for ledger records."""
    encoded = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]
