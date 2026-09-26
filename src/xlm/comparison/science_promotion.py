"""P35 promotion rule ``xlm-p35-promotion-v1`` (§P, §R, §H; M4).

This is **not** the P17 ``promotion.evaluate_promotion`` (suite-point / compute
gate, two seeds): that function and its records stay historical and unchanged.
This rule consumes an eligible science-v1 comparison outcome and returns a
decision artifact. It never launches, authorizes or drafts a training job.

States:

- ``NOT_ELIGIBLE``: invalid manifest, ineligible pairs, or a frozen margin /
  cost threshold the decision needs is missing;
- ``INCOMPLETE``: a confirmation with no complete pair;
- ``SCREEN_ONLY``: screen/replication evidence; ranks hypotheses, never confirms;
- ``PROVISIONAL``: a partial confirmation (e.g. the first three of five pairs),
  a confirmation lacking order robustness (M5 absent), or a larger-scale
  confirmation whose pairs do not all support the direction;
- ``CONFIRMED_50M`` / ``CONFIRMED_150M`` / ``CONFIRMED_300M``;
- ``PROMOTE_TO_150M`` / ``PROMOTE_TO_300M``: confirmed *and* the §R entry
  requirements for the next scale are met (order robustness, declared ablation
  support and resource plan);
- ``REJECT``: a clear loss or a guardrail regression beyond its margin;
- ``AMBIGUOUS``: a complete confirmation whose interval crosses its boundary,
  a guardrail not shown within its margin, or an unshown efficiency benefit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from xlm.comparison.science_manifest import (
    ORDER_SENTINEL,
    PROMOTION_RULE_VERSION,
    SCALE_PREREQUISITE_STATE,
)
from xlm.comparison.science_stats import DecisionResult, QuestionType

M5_ORDER_EVIDENCE_KIND = "m5_independent_order_manifests_v1"
NEXT_SCALE = {"50m": "150m", "150m": "300m"}


class PromotionState(StrEnum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    INCOMPLETE = "INCOMPLETE"
    SCREEN_ONLY = "SCREEN_ONLY"
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED_50M = "CONFIRMED_50M"
    PROMOTE_TO_150M = "PROMOTE_TO_150M"
    CONFIRMED_150M = "CONFIRMED_150M"
    PROMOTE_TO_300M = "PROMOTE_TO_300M"
    CONFIRMED_300M = "CONFIRMED_300M"
    REJECT = "REJECT"
    AMBIGUOUS = "AMBIGUOUS"


CONFIRMED = {
    "50m": PromotionState.CONFIRMED_50M,
    "150m": PromotionState.CONFIRMED_150M,
    "300m": PromotionState.CONFIRMED_300M,
}
PROMOTE = {"50m": PromotionState.PROMOTE_TO_150M, "150m": PromotionState.PROMOTE_TO_300M}


@dataclass(frozen=True)
class GuardrailOutcome:
    """A guardrail's paired interval against its frozen regression margin."""

    name: str
    status: str  # "pass" | "fail" | "not_shown" | "incomplete"
    reason: str


@dataclass(frozen=True)
class CandidateEvidence:
    """What the promotion rule needs from one candidate's comparison outcome."""

    arm_id: str
    eligible: bool
    ineligible_reasons: tuple[str, ...]
    n_complete_pairs: int
    n_required_pairs: int
    decision: DecisionResult | None
    pair_improvements: tuple[float, ...]
    guardrails: tuple[GuardrailOutcome, ...]
    efficiency_shown: bool | None
    efficiency_reason: str
    order_manifest_ids: Mapping[str, str]
    #: Per complete tuple, the candidate run's canonical membership receipt (M5).
    canonical_membership_ids: Mapping[str, str] | None = None


@dataclass
class PromotionOutcome:
    state: PromotionState
    reasons: list[str] = field(default_factory=list)
    blockers: list[dict[str, str]] = field(default_factory=list)
    requirements: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "promotion_rule_version": PROMOTION_RULE_VERSION,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "blockers": [dict(b) for b in self.blockers],
            "requirements": dict(self.requirements),
            "executes_nothing": True,
        }


def order_robustness(
    manifest: Mapping[str, Any],
    order_ids: Mapping[str, str],
    membership_ids: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """``(status, reason)`` of §H independent document-order evidence (the M5 slot).

    Satisfied only by M5 evidence of kind ``m5_independent_order_manifests_v1``
    that **verifies** (P35 M5, ``science_order.declaration_problems``): >= 2
    distinct, independently ordered manifest headers over one canonical
    membership, allocated to the roster by the alternating policy (five 50M
    tuples: C0/C2/C4 -> A, C1/C3 -> B). Every paired tuple must actually have run
    under its allocated order (never the pre-M5 sentinel) on the declared
    membership, per its extracted receipts. Different ``data_seed`` values only
    change source-scheduler tie order; they are not independent within-source
    document orders and cannot satisfy this.
    """
    from xlm.comparison.science_order import declaration_problems, run_binding_problems

    slot = manifest["order_robustness"]
    if not slot["required"]:
        return "NOT_REQUIRED", "the manifest declares no order-robustness requirement"
    evidence = slot["m5_order_evidence"]
    if evidence is None:
        return (
            "BLOCKED",
            "independent within-source document-order evidence (M5) is NOT RUN: no ordered "
            "manifests exist; source-seed variation is not order robustness",
        )
    if not isinstance(evidence, Mapping) or evidence.get("kind") != M5_ORDER_EVIDENCE_KIND:
        kind = evidence.get("kind") if isinstance(evidence, Mapping) else None
        return (
            "BLOCKED",
            f"order evidence kind {kind!r} is not {M5_ORDER_EVIDENCE_KIND}; placeholders such "
            "as source-seed variation cannot satisfy it",
        )
    declared = evidence.get("order_manifest_ids")
    if not (isinstance(declared, list) and len(set(declared)) >= 2):
        return "BLOCKED", "M5 evidence must name at least two distinct order manifests"
    problems = declaration_problems(evidence, manifest.get("replicate_roster"))
    if problems:
        return "BLOCKED", "M5 order evidence does not verify: " + "; ".join(problems)
    used = set(order_ids.values())
    if ORDER_SENTINEL in used or not used <= set(declared) or len(used) < 2:
        return (
            "BLOCKED",
            "the paired runs do not carry at least two declared independent order manifests",
        )
    problems = run_binding_problems(evidence, order_ids, membership_ids)
    if problems:
        return "BLOCKED", "run-to-order binding fails: " + "; ".join(problems)
    return (
        "SATISFIED",
        f"paired tuples span {len(used)} independent within-source document orders over one "
        "canonical membership",
    )


def evaluate_promotion_v1(
    manifest: Mapping[str, Any],
    manifest_valid: bool,
    manifest_problems: Sequence[str],
    promotion_blockers: Sequence[str],
    candidate: CandidateEvidence,
    *,
    prerequisite_state: str | None,
) -> PromotionOutcome:
    """Apply ``xlm-p35-promotion-v1`` to one candidate. Returns a decision artifact."""
    scale = str(manifest.get("scale_stage"))
    stage = manifest.get("study_stage")
    outcome = PromotionOutcome(PromotionState.NOT_ELIGIBLE)
    if not manifest_valid:
        outcome.reasons.append("manifest invalid: " + "; ".join(manifest_problems))
        return outcome
    if not candidate.eligible:
        outcome.reasons.append("comparison ineligible: " + "; ".join(candidate.ineligible_reasons))
        return outcome
    if promotion_blockers:
        outcome.reasons.append("INELIGIBLE FOR PROMOTION: " + "; ".join(promotion_blockers))
        return outcome
    if stage in ("screen", "replication"):
        outcome.state = PromotionState.SCREEN_ONLY
        outcome.reasons.append(
            f"{stage} evidence ranks hypotheses; it supplies no confirmatory claim at any scale"
        )
        return outcome

    requirements: dict[str, str] = {}
    outcome.requirements = requirements
    needed = SCALE_PREREQUISITE_STATE.get(scale)
    if needed is not None:
        if prerequisite_state != needed:
            outcome.reasons.append(
                f"{scale} entry requires a verified prior-scale comparison at {needed}; "
                f"found {prerequisite_state!r}"
            )
            requirements["prior_scale"] = "MISSING"
            return outcome
        requirements["prior_scale"] = "SATISFIED"
    required = candidate.n_required_pairs
    if candidate.n_complete_pairs == 0:
        outcome.state = PromotionState.INCOMPLETE
        outcome.reasons.append(f"0 of {required} confirmation pairs are complete")
        return outcome
    if candidate.n_complete_pairs < required:
        outcome.state = PromotionState.PROVISIONAL
        outcome.reasons.append(
            f"{candidate.n_complete_pairs} of {required} fixed confirmation pairs complete: "
            "provisional progress only, never early success (§H/§V)"
        )
        requirements["sample_size"] = "INCOMPLETE"
        return outcome
    requirements["sample_size"] = "SATISFIED"

    decision = candidate.decision
    question = QuestionType(manifest["question"])
    fatal = [g for g in candidate.guardrails if g.status == "fail"]
    unshown = [g for g in candidate.guardrails if g.status in ("not_shown", "incomplete")]
    if decision is DecisionResult.CLEAR_LOSS or fatal:
        outcome.state = PromotionState.REJECT
        if decision is DecisionResult.CLEAR_LOSS:
            outcome.reasons.append("primary endpoint is a clear loss beyond the frozen margin")
        for g in fatal:
            outcome.reasons.append(f"guardrail '{g.name}' regression beyond its margin: {g.reason}")
        return outcome
    success = (
        decision is DecisionResult.CLEAR_WIN
        if question is QuestionType.SUPERIORITY
        else decision is DecisionResult.NON_INFERIOR
    )
    if not success:
        outcome.state = PromotionState.AMBIGUOUS
        outcome.reasons.append(
            f"primary decision {decision.value if decision else None}: no confirmation"
        )
        return outcome
    if unshown:
        outcome.state = PromotionState.AMBIGUOUS
        for g in unshown:
            outcome.reasons.append(f"guardrail '{g.name}' not shown within margin: {g.reason}")
        return outcome
    requirements["guardrails"] = "SATISFIED"
    if question is QuestionType.NONINFERIORITY:
        if not candidate.efficiency_shown:
            outcome.state = PromotionState.AMBIGUOUS
            outcome.reasons.append(
                "non-inferior quality without a shown cost benefit is not an efficiency win: "
                + candidate.efficiency_reason
            )
            return outcome
        requirements["efficiency"] = "SATISFIED"
    if scale in ("150m", "300m") and not all(x > 0 for x in candidate.pair_improvements):
        outcome.state = PromotionState.PROVISIONAL
        outcome.reasons.append(
            f"not every {scale} pair supports the direction; three seeds can be inconclusive (§R)"
        )
        requirements["direction_consistency"] = "NOT_SHOWN"
        return outcome
    if scale in ("150m", "300m"):
        requirements["direction_consistency"] = "SATISFIED"

    order_status, order_reason = order_robustness(
        manifest, candidate.order_manifest_ids, candidate.canonical_membership_ids
    )
    requirements["order_robustness"] = order_status
    if order_status == "BLOCKED":
        outcome.state = PromotionState.PROVISIONAL
        outcome.reasons.append(
            "primary rule satisfied but order robustness is required and BLOCKED: " + order_reason
        )
        outcome.blockers.append({"requirement": "order_robustness", "reason": order_reason})
        return outcome

    outcome.state = CONFIRMED[scale]
    outcome.reasons.append(f"{scale} confirmation satisfied the frozen §P rule")
    if order_status == "NOT_REQUIRED":
        outcome.reasons.append(
            "conditional on the fixed within-source document order (no robustness claim)"
        )
    promotion = manifest["scale_promotion"]
    if not promotion["intent"] or scale not in PROMOTE:
        requirements["next_scale"] = "NOT_REQUESTED" if scale in PROMOTE else "FINAL_SCALE"
        return outcome
    missing: list[dict[str, str]] = []
    if promotion["resource_plan_ref"] is None:
        missing.append(
            {"requirement": "resource_plan", "reason": "no scale-specific resource plan declared"}
        )
    if scale == "50m" and not promotion["ablation_refs"]:
        missing.append(
            {"requirement": "ablation_support", "reason": "no ablation/control evidence declared"}
        )
    if missing:
        requirements["next_scale"] = "BLOCKED"
        outcome.blockers.extend(missing)
        outcome.reasons.append(f"next-scale entry to {NEXT_SCALE[scale]} blocked")
        return outcome
    requirements["next_scale"] = "SATISFIED (declared references; not verified by M4)"
    outcome.state = PROMOTE[scale]
    outcome.reasons.append(
        f"eligible to register a fresh {NEXT_SCALE[scale]} study; nothing is launched"
    )
    return outcome
