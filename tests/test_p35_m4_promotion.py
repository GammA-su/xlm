"""P35 M4: promotion-v1 states, sample sizes, M5 dependency, curves, item vs seed (SYNTHETIC)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from p35_m4_support import (
    C_TUPLES,
    E_TUPLES,
    h,
    make_evidence,
    microbatch_manifest,
    microbatch_runs,
    mixture_pair_runs,
    run,
    superiority_manifest,
)
from p35_m5_support import synthetic_orders
from xlm.comparison.curves import CurveError, fixed_linear_target_area
from xlm.comparison.science_compare import compare_science
from xlm.comparison.science_order import build_order_declaration
from xlm.comparison.science_promotion import (
    CandidateEvidence,
    GuardrailOutcome,
    PromotionState,
    evaluate_promotion_v1,
    order_robustness,
)
from xlm.comparison.science_stats import DecisionResult

CTRL = {"C0": 3.00, "C1": 3.10, "C2": 3.20, "C3": 3.05, "C4": 3.15}
WIN = {"C0": 2.98, "C1": 3.07, "C2": 3.19, "C3": 3.02, "C4": 3.12}  # CI [0.0129, 0.0351]
STRONG = {"C0": 2.90, "C1": 2.99, "C2": 3.095, "C3": 2.95, "C4": 3.05}
LOSS = {"C0": 3.03, "C1": 3.14, "C2": 3.22, "C3": 3.085, "C4": 3.18}  # CI [-0.040, -0.022]
ORDER_A, ORDER_B = h("order-manifest-A"), h("order-manifest-B")


def _only(record: dict[str, Any]) -> dict[str, Any]:
    (candidate,) = record["candidates"]
    return candidate


def _state(record: dict[str, Any]) -> str:
    return str(_only(record)["promotion"]["state"])


def _compare(
    manifest: dict[str, Any], control: dict[str, float], candidate: dict[str, float], **kwargs: Any
) -> dict[str, Any]:
    overrides = kwargs.pop("overrides", None)
    return compare_science(
        manifest,
        mixture_pair_runs(manifest, control, candidate, candidate_overrides=overrides),
        **kwargs,
    )


def _with_m5(manifest: dict[str, Any], *, promote: bool) -> dict[str, Any]:
    """M5 evidence slot from two authored independent order manifests (§H).

    Since P35 M5 the slot is consumed and verified, not trusted: the declaration
    is built by ``build_order_declaration`` over SYNTHETIC order headers, and
    the roster carries the allocated order per tuple (C0/C2/C4 A, C1/C3 B).
    """
    doc = copy.deepcopy(manifest)
    declaration = build_order_declaration(
        synthetic_orders(), [e["tuple_id"] for e in doc["replicate_roster"]]
    )
    for entry in doc["replicate_roster"]:
        entry["order_manifest_id"] = declaration["allocation"][entry["tuple_id"]]
    doc["order_robustness"] = {"required": True, "m5_order_evidence": declaration}
    doc["scale_promotion"] = {
        "intent": promote,
        "prerequisite": doc["scale_promotion"]["prerequisite"],
        "resource_plan_ref": "SYNTHETIC resource plan RP-1" if promote else None,
        "ablation_refs": ["SYNTHETIC ablation AB-1"] if promote else [],
    }
    return doc


# ------------------------------------------------------------------ sample size


def test_one_pair_screen_is_screen_only_without_seed_ci() -> None:
    manifest = superiority_manifest(stage="screen", tuples=E_TUPLES[:1])
    record = _compare(manifest, {"E0": 3.4}, {"E0": 3.2})
    candidate = _only(record)
    assert candidate["statistics"]["ci_raw_delta"] is None
    assert candidate["decision"]["result"] == "NO_SEED_INTERVAL"
    assert candidate["promotion"]["state"] == "SCREEN_ONLY"


def test_second_seed_replication_is_still_exploratory() -> None:
    manifest = superiority_manifest(stage="replication", tuples=E_TUPLES[:2])
    record = _compare(manifest, {"E0": 3.4, "E1": 3.5}, {"E0": 3.2, "E1": 3.31})
    candidate = _only(record)
    assert candidate["statistics"]["df"] == 1
    assert candidate["statistics"]["t_critical"] == pytest.approx(12.706204736174707, rel=1e-10)
    assert "exploratory" in candidate["decision"]["scope"]
    assert candidate["promotion"]["state"] == "SCREEN_ONLY"


def test_five_pair_50m_confirmation_confirms() -> None:
    record = _compare(superiority_manifest(), CTRL, WIN)
    candidate = _only(record)
    assert candidate["decision"]["result"] == "CLEAR_WIN"
    assert candidate["promotion"]["state"] == "CONFIRMED_50M"
    assert any("fixed within-source document order" in r for r in candidate["promotion"]["reasons"])


def test_first_three_of_five_is_provisional_even_with_a_strong_signal() -> None:
    first_three = {k: STRONG[k] for k in ("C0", "C1", "C2")}
    record = _compare(superiority_manifest(), {k: CTRL[k] for k in first_three}, first_three)
    candidate = _only(record)
    assert candidate["pairing"]["n_complete"] == 3
    assert candidate["decision"]["result"] == "INCOMPLETE"
    assert candidate["decision"]["provisional_view"]["result"] == "CLEAR_WIN"
    assert candidate["promotion"]["state"] == "PROVISIONAL"
    assert "never early success" in candidate["promotion"]["reasons"][0]


def test_confirmation_without_any_complete_pair_is_incomplete() -> None:
    manifest = superiority_manifest()
    runs = [run("m1-C0", "m1", None, status="failed", failure="SYNTHETIC crash")]
    record = compare_science(manifest, runs)
    assert _state(record) == "INCOMPLETE"


# -------------------------------------------------------------------- decisions


def test_clear_loss_rejects_and_ambiguous_is_ambiguous() -> None:
    assert _state(_compare(superiority_manifest(), CTRL, LOSS)) == "REJECT"
    ambiguous = _compare(superiority_manifest(margin=0.02), CTRL, WIN)
    assert _only(ambiguous)["decision"]["result"] == "AMBIGUOUS"
    assert _state(ambiguous) == "AMBIGUOUS"


def test_missing_margin_blocks_promotion() -> None:
    record = _compare(superiority_manifest(margin=None), CTRL, WIN)
    candidate = _only(record)
    assert candidate["decision"]["result"] == "NO_MARGIN"
    assert candidate["promotion"]["state"] == "NOT_ELIGIBLE"
    assert "INELIGIBLE FOR PROMOTION" in candidate["promotion"]["reasons"][0]


def test_guardrail_regression_rejects_and_unshown_guardrail_is_ambiguous() -> None:
    manifest = superiority_manifest()
    runs = mixture_pair_runs(manifest, CTRL, WIN)
    # Candidate web-domain CE worse by 0.2 on every pair (guardrail margin 0.05).
    patched = []
    for entry in runs:
        evidence = copy.deepcopy(entry.evidence)
        assert evidence is not None
        if entry.arm_id == "m1":
            metrics = evidence["evaluations"]["full_lm@1000000000"]["metrics"]
            base = metrics["domains"]["web"]["text_ce_nats_per_token"]
            metrics["domains"]["web"]["text_ce_nats_per_token"] = base + 0.2 + 0.001 * len(patched)
            evidence.pop("evidence_digest")
            from xlm.artifacts.manifest import identity_digest

            evidence["evidence_digest"] = identity_digest(evidence)
        patched.append(run(entry.label, entry.arm_id, evidence))
    record = compare_science(manifest, patched)
    candidate = _only(record)
    assert candidate["decision"]["result"] == "CLEAR_WIN"
    assert candidate["guardrails"][0]["status"] == "fail"
    assert candidate["promotion"]["state"] == "REJECT"


def test_guardrail_statuses_via_rule() -> None:
    base = dict(
        arm_id="m1",
        eligible=True,
        ineligible_reasons=(),
        n_complete_pairs=5,
        n_required_pairs=5,
        decision=DecisionResult.CLEAR_WIN,
        pair_improvements=(0.02,) * 5,
        efficiency_shown=None,
        efficiency_reason="",
        order_manifest_ids={},
    )
    manifest = superiority_manifest()
    unshown = CandidateEvidence(
        guardrails=(GuardrailOutcome("web", "not_shown", "crosses"),),
        **base,  # type: ignore[arg-type]
    )
    outcome = evaluate_promotion_v1(manifest, True, [], [], unshown, prerequisite_state=None)
    assert outcome.state is PromotionState.AMBIGUOUS


# ------------------------------------------------------------------ non-inferiority


def _microbatch_confirmation(
    b16_shift: float, b16_speed: float, spread: float = 0.0
) -> dict[str, Any]:
    manifest = microbatch_manifest(stage="confirmation")
    manifest["candidate_arms"] = manifest["candidate_arms"][:1]
    manifest["multiplicity"]["family_size"] = 1
    b8 = dict(CTRL)
    b16 = {k: v + b16_shift + spread * ((i % 3) - 1) for i, (k, v) in enumerate(CTRL.items())}
    values = {"b8": b8, "b16": b16}
    return compare_science(
        manifest,
        microbatch_runs(manifest, values, speeds={"b8": 45_000.0, "b16": 52_000.0 * b16_speed}),
    )


def test_noninferior_with_measured_cost_benefit_confirms() -> None:
    record = _microbatch_confirmation(0.002, 1.0, spread=0.001)
    candidate = _only(record)
    assert candidate["decision"]["result"] == "NON_INFERIOR"
    assert candidate["promotion"]["state"] == "CONFIRMED_50M"
    assert candidate["efficiency"]["measurement_source"].startswith("operator-declared")


def test_noninferior_without_cost_benefit_is_ambiguous() -> None:
    record = _microbatch_confirmation(0.002, 45_000.0 / 52_000.0, spread=0.001)
    candidate = _only(record)
    assert candidate["decision"]["result"] == "NON_INFERIOR"
    assert candidate["efficiency"]["shown"] is False
    assert candidate["promotion"]["state"] == "AMBIGUOUS"


def test_nonsignificant_quality_difference_is_not_noninferiority() -> None:
    record = _microbatch_confirmation(0.0, 1.0, spread=0.06)
    candidate = _only(record)
    low, high = candidate["statistics"]["ci_improvement"]
    assert low < 0 < high
    assert candidate["decision"]["result"] == "AMBIGUOUS"
    assert candidate["promotion"]["state"] == "AMBIGUOUS"


# ------------------------------------------------------------- M5 order evidence


def test_missing_m5_order_evidence_blocks_robust_promotion() -> None:
    manifest = superiority_manifest()
    manifest["order_robustness"]["required"] = True
    manifest["scale_promotion"].update(
        intent=True, resource_plan_ref="SYNTHETIC RP", ablation_refs=["SYNTHETIC AB"]
    )
    candidate = _only(_compare(manifest, CTRL, WIN))
    assert candidate["decision"]["result"] == "CLEAR_WIN"
    assert candidate["promotion"]["state"] == "PROVISIONAL"
    assert candidate["promotion"]["requirements"]["order_robustness"] == "BLOCKED"
    assert "NOT RUN" in candidate["promotion"]["blockers"][0]["reason"]


def test_source_seed_placeholder_cannot_satisfy_order_robustness() -> None:
    manifest = superiority_manifest()
    manifest["order_robustness"] = {
        "required": True,
        "m5_order_evidence": {"kind": "source_seed_variation", "data_seeds": [20261001, 20261002]},
    }
    candidate = _only(_compare(manifest, CTRL, WIN))
    assert candidate["promotion"]["state"] == "PROVISIONAL"
    assert "cannot satisfy" in candidate["promotion"]["blockers"][0]["reason"]
    # The M5 kind with runs that still carry the pre-M5 sentinel is also refused.
    slot = {
        "required": True,
        "m5_order_evidence": {
            "kind": "m5_independent_order_manifests_v1",
            "order_manifest_ids": [ORDER_A, ORDER_B],
        },
    }
    status, _ = order_robustness(
        {"order_robustness": slot}, {"C0": "shard_native_no_order_manifest"}
    )
    assert status == "BLOCKED"


# --------------------------------------------------------- 50M -> 150M -> 300M


def _promote_50m() -> dict[str, Any]:
    manifest = _with_m5(superiority_manifest(), promote=True)
    record = _compare(manifest, CTRL, WIN)
    assert _state(record) == "PROMOTE_TO_150M", _only(record)["promotion"]
    return record


def _scale_manifest(scale: str, prerequisite: dict[str, Any], promote: bool) -> dict[str, Any]:
    manifest = superiority_manifest(scale=scale)
    manifest["scale_promotion"]["prerequisite"] = {
        "manifest_hash": prerequisite["manifest_hash"],
        "required_state": {"150m": "PROMOTE_TO_150M", "300m": "PROMOTE_TO_300M"}[scale],
    }
    return _with_m5(manifest, promote=promote)


SCALE_CTRL = {"C0": 2.80, "C1": 2.85, "C2": 2.90}
SCALE_WIN = {"C0": 2.76, "C1": 2.81, "C2": 2.862}


def test_150m_requires_prior_50m_promotion_and_three_pairs() -> None:
    fifty = _promote_50m()
    manifest = _scale_manifest("150m", fifty, promote=True)
    without = _compare(manifest, SCALE_CTRL, SCALE_WIN)
    assert _state(without) == "NOT_ELIGIBLE"
    record = _compare(manifest, SCALE_CTRL, SCALE_WIN, prerequisites=[fifty])
    candidate = _only(record)
    assert candidate["pairing"]["n_required"] == 3
    assert candidate["decision"]["result"] == "CLEAR_WIN"
    assert candidate["promotion"]["state"] == "PROMOTE_TO_300M"
    confirmed_only = _compare(
        _scale_manifest("150m", fifty, promote=False), SCALE_CTRL, SCALE_WIN, prerequisites=[fifty]
    )
    assert _state(confirmed_only) == "CONFIRMED_150M"
    two = {k: SCALE_WIN[k] for k in ("C0", "C1")}
    assert _state(_compare(manifest, SCALE_CTRL, two, prerequisites=[fifty])) == "PROVISIONAL"


def test_one_50m_screen_cannot_unlock_150m() -> None:
    screen = _compare(
        superiority_manifest(stage="screen", tuples=E_TUPLES[:1]), {"E0": 3.4}, {"E0": 3.0}
    )
    manifest = _scale_manifest("150m", screen, promote=False)
    assert (
        _state(_compare(manifest, SCALE_CTRL, SCALE_WIN, prerequisites=[screen])) == "NOT_ELIGIBLE"
    )


def test_300m_requires_the_150m_confirmation() -> None:
    fifty = _promote_50m()
    hundred_fifty = _compare(
        _scale_manifest("150m", fifty, promote=True), SCALE_CTRL, SCALE_WIN, prerequisites=[fifty]
    )
    manifest = _scale_manifest("300m", hundred_fifty, promote=False)
    record = _compare(manifest, SCALE_CTRL, SCALE_WIN, prerequisites=[hundred_fifty])
    assert _state(record) == "CONFIRMED_300M"
    assert _only(record)["promotion"]["requirements"]["next_scale"] == "FINAL_SCALE"
    assert (
        _state(_compare(manifest, SCALE_CTRL, SCALE_WIN, prerequisites=[fifty])) == "NOT_ELIGIBLE"
    )


def test_larger_scale_sign_reversal_is_provisional() -> None:
    evidence = CandidateEvidence(
        arm_id="m1",
        eligible=True,
        ineligible_reasons=(),
        n_complete_pairs=3,
        n_required_pairs=3,
        decision=DecisionResult.CLEAR_WIN,
        pair_improvements=(0.05, 0.06, -0.001),
        guardrails=(),
        efficiency_shown=None,
        efficiency_reason="",
        order_manifest_ids={},
    )
    manifest = superiority_manifest(scale="150m")
    outcome = evaluate_promotion_v1(
        manifest, True, [], [], evidence, prerequisite_state="PROMOTE_TO_150M"
    )
    assert outcome.state is PromotionState.PROVISIONAL


# ----------------------------------------------------------------------- curves


def test_fixed_linear_target_area_hand_computed() -> None:
    points = [
        (16_000_000, 16_000_000, 4.0),
        (32_000_000, 32_000_000, 3.0),
        (128_000_000, 128_000_000, 2.0),
    ]
    expected = (0.5 * 7.0 * 16e6 + 0.5 * 5.0 * 96e6) / 112e6
    assert fixed_linear_target_area(points, 16_000_000, 128_000_000) == pytest.approx(expected)


def test_curve_missing_endpoint_or_different_range_is_refused() -> None:
    points = [(16_000_000, 16_000_000, 4.0), (32_000_000, 32_000_000, 3.0)]
    with pytest.raises(CurveError, match="endpoint"):
        fixed_linear_target_area(points, 16_000_000, 128_000_000)
    with pytest.raises(CurveError, match="start point"):
        fixed_linear_target_area(
            points[1:] + [(128_000_000, 128_000_000, 2.0)], 16_000_000, 128_000_000
        )
    with pytest.raises(CurveError, match="outside"):
        fixed_linear_target_area(
            points + [(256_000_000, 256_000_000, 1.0)], 16_000_000, 128_000_000
        )


def test_curve_area_uses_the_same_target_grid_and_never_wall_time() -> None:
    manifest = microbatch_manifest()
    values = {
        "b8": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
        "b16": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
        "b32": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
    }
    record = compare_science(manifest, microbatch_runs(manifest, values))
    curve = next(c for c in record["candidates"] if c["arm_id"] == "b16")["curve"]
    assert curve["complete"] and curve["x_axis"].startswith("actual committed targets")
    assert curve["pairs"][0]["grid"][0] == [16_000_000, 16_056_320]
    assert curve["statistics"]["mean_raw_delta"] == pytest.approx(0.0)


def test_curve_with_a_missing_point_is_incomplete() -> None:
    manifest = microbatch_manifest()
    values = {
        "b8": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
        "b16": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
        "b32": {"E0": 3.4, "E1": 3.45, "E2": 3.38},
    }
    runs = microbatch_runs(manifest, values)
    entry = next(r for r in runs if r.label == "b16-E1")
    evidence = copy.deepcopy(entry.evidence)
    assert evidence is not None
    evidence["evaluations"]["quick_lm@128000000"].update(complete=False, metrics=None)
    from xlm.artifacts.manifest import identity_digest

    evidence.pop("evidence_digest")
    evidence["evidence_digest"] = identity_digest(evidence)
    runs = [
        r if r.label != "b16-E1" else run(r.label, r.arm_id, evidence, measurements=r.measurements)
        for r in runs
    ]
    record = compare_science(manifest, runs)
    curve = next(c for c in record["candidates"] if c["arm_id"] == "b16")["curve"]
    assert not curve["complete"] and curve["statistics"] is None
    assert any(p["status"] == "INCOMPLETE" for p in curve["pairs"])


# ------------------------------------------------------- item vs seed uncertainty


def test_narrow_item_bootstrap_cannot_override_a_wide_seed_interval() -> None:
    manifest = superiority_manifest(margin=0.02)
    item = {"blimp": {"point": 0.03, "ci_lo": 0.029, "ci_hi": 0.031, "source": "SYNTHETIC"}}
    with_items = _only(_compare(manifest, CTRL, WIN, item_uncertainty={"m1": item}))
    without = _only(_compare(manifest, CTRL, WIN))
    assert with_items["decision"] == without["decision"]
    assert with_items["decision"]["result"] == "AMBIGUOUS"
    assert with_items["item_level_uncertainty"]["used_for_decision"] is False
    assert (
        with_items["statistics"]["uncertainty_source"] == "between_independent_training_seed_pairs"
    )


def test_contract_roster_is_used_for_confirmation() -> None:
    manifest = superiority_manifest()
    assert [e["tuple_id"] for e in manifest["replicate_roster"]] == [t[0] for t in C_TUPLES]


def test_promotion_record_executes_nothing() -> None:
    record = _compare(superiority_manifest(), CTRL, WIN)
    assert record["executes_nothing"] and _only(record)["promotion"]["executes_nothing"]


def test_evidence_fixture_helper_is_synthetic() -> None:
    evidence = make_evidence("x", (1, 2, 3), primary=1.0)
    assert evidence["source"]["synthetic"] is True
