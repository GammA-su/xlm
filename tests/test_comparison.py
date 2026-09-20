"""Acceptance tests for P17: track eligibility, paired bootstrap, curves, promotion.

All fixtures are authored synthetic data. No benchmark labels, no live runs, no
hardware requirements.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from xlm.comparison.bootstrap import (
    BootstrapError,
    align_paired_items,
    cluster_bootstrap_difference,
    seed_spread,
    suite_bootstrap,
)
from xlm.comparison.curves import (
    CurveError,
    CurvePoint,
    LearningCurve,
    compare_compute_to_target,
    compute_to_target,
    crossing_at_target,
    curves_from_mapping,
)
from xlm.comparison.promotion import (
    PromotionError,
    PromotionEvidence,
    PromotionGates,
    data_architecture_factorial,
    decision_fingerprint,
    evaluate_promotion,
    next_size_draft,
    two_idea_ablation_matrix,
)
from xlm.comparison.tracks import (
    REQUIRED_FIELDS,
    ComparisonRun,
    check_track_eligibility,
)
from xlm.evaluation.suites import REQUIRED_TASKS_FOR_INDEX


def _fields(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "architecture_id": "transformer_baseline",
        "model_config": {
            "num_layers": 10,
            "hidden_size": 512,
            "num_attention_heads": 8,
            "intermediate_size": 1472,
            "vocab_size": 32768,
            "context_length": 512,
        },
        "total_params": 49883648,
        "tokenizer_hash": "tok_fp_1",
        "vocab_size": 32768,
        "mixture_id": "mix01",
        "mixture_weights": {"a": 0.5, "b": 0.5},
        "canonical_bytes": 1000,
        "packing": "causal_stream",
        "data_seed": 20260918,
        "objective_id": "cross_entropy",
        "objective_extras": None,
        "optimizer_id": "adamw",
        "tuning_allowance": 3,
        "init_seeds": 101,
        "context_length": 512,
        "budget_targets": 1000,
        "measured_compute_seconds": 10.0,
        "horizon_kind": "standalone",
        "precision": "fp32",
        "matched_bytes": None,
        "matched_compute": None,
    }
    base.update(overrides)
    return base


def _run(run_id: str, **overrides: Any) -> ComparisonRun:
    return ComparisonRun(run_id=run_id, fields=_fields(**overrides))


# ------------------------------------------------------------ track eligibility


def test_identical_runs_are_eligible_on_every_causal_track() -> None:
    base, cand = _run("a"), _run("b")
    for track in ("architecture", "objective", "optimizer", "data-mixture"):
        result = check_track_eligibility(base, cand, track)
        assert result.eligible, (track, result.reasons)
        assert all(d.verdict == "same" for d in result.diffs)


def test_changed_tokenizer_fails_the_architecture_track() -> None:
    base = _run("a")
    cand = _run("b", tokenizer_hash="tok_fp_2", vocab_size=30000)
    result = check_track_eligibility(base, cand, "architecture")
    assert not result.eligible
    assert any("tokenizer_hash" in r for r in result.reasons)
    assert any("vocab_size" in r for r in result.reasons)


def test_byte_matched_tokenizer_track_can_pass() -> None:
    base = _run("a")
    cand = _run(
        "b",
        tokenizer_hash="tok_fp_2",
        vocab_size=30000,
        total_params=50000000,
        matched_bytes={"matched": True, "bytes": 1000},
        matched_compute={"matched": True},
    )
    result = check_track_eligibility(base, cand, "tokenizer")
    assert result.eligible, result.reasons


def test_tokenizer_track_without_matched_evidence_is_ineligible() -> None:
    base = _run("a")
    cand = _run("b", tokenizer_hash="tok_fp_2")
    result = check_track_eligibility(base, cand, "tokenizer")
    assert not result.eligible
    assert any("matched-canonical-byte" in r for r in result.reasons)


def test_unknown_fields_fail_closed() -> None:
    fields = _fields()
    del fields["total_params"]
    base = ComparisonRun(run_id="a", fields=fields)
    result = check_track_eligibility(base, _run("b"), "architecture")
    assert not result.eligible
    assert any("total_params" in r and "unknown" in r for r in result.reasons)


def test_mismatched_horizons_are_ineligible() -> None:
    base = _run("a", horizon_kind="continuation_prefix")
    cand = _run("b", horizon_kind="standalone")
    result = check_track_eligibility(base, cand, "data-mixture")
    assert not result.eligible
    assert any("horizon_kind" in r for r in result.reasons)


def test_unconstrained_track_labels_differences_without_a_causal_badge() -> None:
    base = _run("a")
    cand = _run(
        "b",
        tokenizer_hash="tok2",
        mixture_id="m2",
        objective_id="other",
        optimizer_id="sgd",
    )
    result = check_track_eligibility(base, cand, "unconstrained-system")
    assert result.eligible
    assert any(d.verdict == "allowed" for d in result.diffs)


def test_unknown_track_is_refused() -> None:
    result = check_track_eligibility(_run("a"), _run("b"), "vibes")
    assert not result.eligible


def test_run_facts_assemble_from_evidence_and_plan() -> None:
    evidence = {
        "identity": {"checkpoint_hash": "c1", "tokenizer_hash": "tok9"},
        "unique_parameters": 123,
        "canonical_bytes": 456,
        "matched_bytes": {"matched": True},
        "matched_compute": {"matched": True},
        "measured_compute_seconds": 7.5,
    }
    plan = {
        "track": "baseline",
        "horizon_kind": "standalone",
        "resolved_config": {
            "model": {"architecture": "transformer_baseline", "vocab_size": 100},
            "training": {
                "data_seed": 5,
                "init_seed": 6,
                "context_length": 64,
                "precision": "fp32",
                "budget": {"max_valid_targets": 500},
            },
            "data": {
                "mixture_preset": "mix01",
                "mixture_details": {"id": "mix01", "weights": {"a": 1.0}},
                "packing_policy": "causal_stream",
            },
            "objective": {"type": "cross_entropy"},
            "optimizer": {"type": "adamw", "tuning_allowance": 2},
        },
    }
    run = ComparisonRun.from_records("run_x", evidence, plan)
    for name in REQUIRED_FIELDS:
        assert run.get(name) != "unknown", name
    assert run.get("tokenizer_hash") == "tok9"
    assert run.get("total_params") == 123


# ------------------------------------------------------------------ bootstrap


def _paired(
    n: int, clusters: int, wins_b: int, seed_offset: int = 0
) -> tuple[dict[str, float], dict[str, float], dict[str, str], dict[str, str]]:
    """Two arms over n items in `clusters` clusters; B wins the first `wins_b`."""
    arm_a: dict[str, float] = {}
    arm_b: dict[str, float] = {}
    tasks: dict[str, str] = {}
    cluster_map: dict[str, str] = {}
    for i in range(n):
        item_id = f"item_{i + seed_offset}"
        arm_a[item_id] = float(i % 2)
        arm_b[item_id] = 1.0 if i < wins_b else float(i % 2)
        tasks[item_id] = "arc_easy"
        cluster_map[item_id] = f"cluster_{i % clusters}"
    return arm_a, arm_b, tasks, cluster_map


def test_identical_predictions_give_zero_paired_difference() -> None:
    arm_a, arm_b, tasks, clusters = _paired(20, 4, 0)
    items = align_paired_items(arm_a, arm_b, tasks, clusters)
    interval = cluster_bootstrap_difference(items, n_bootstrap=200, analysis_seed=1)
    assert interval.point == 0.0
    assert interval.ci_lo == 0.0 and interval.ci_hi == 0.0
    assert not interval.excludes_zero()
    assert interval.n_clusters == 4 and interval.n_items == 20


def test_known_winner_and_swapped_arms_behave_correctly() -> None:
    # Every cluster leans the same way, so every resample stays positive.
    arm_a = {f"item_{i}": 0.0 for i in range(20)}
    arm_b = {f"item_{i}": 1.0 if i % 5 < 4 else 0.0 for i in range(20)}
    tasks = {f"item_{i}": "arc_easy" for i in range(20)}
    clusters = {f"item_{i}": f"cluster_{i // 5}" for i in range(20)}
    items = align_paired_items(arm_a, arm_b, tasks, clusters)
    forward = cluster_bootstrap_difference(items, n_bootstrap=200, analysis_seed=1)
    assert forward.point == pytest.approx(0.8)
    assert forward.excludes_zero()

    swapped = align_paired_items(arm_b, arm_a, tasks, clusters)
    backward = cluster_bootstrap_difference(swapped, n_bootstrap=200, analysis_seed=1)
    assert backward.point == pytest.approx(-forward.point)
    assert backward.ci_lo == pytest.approx(-forward.ci_hi)
    assert backward.ci_hi == pytest.approx(-forward.ci_lo)


def test_bootstrap_is_deterministic_for_a_fixed_seed() -> None:
    arm_a, arm_b, tasks, clusters = _paired(30, 5, 18)
    items = align_paired_items(arm_a, arm_b, tasks, clusters)
    first = cluster_bootstrap_difference(items, n_bootstrap=300, analysis_seed=99)
    second = cluster_bootstrap_difference(items, n_bootstrap=300, analysis_seed=99)
    assert first.to_dict() == second.to_dict()
    third = cluster_bootstrap_difference(items, n_bootstrap=300, analysis_seed=100)
    assert third.ci_lo <= third.point <= third.ci_hi
    assert third.n_bootstrap == 300 and third.analysis_seed == 100


def test_cluster_resampling_counts_clusters_not_items() -> None:
    arm_a = {f"i{i}": 0.0 for i in range(10)}
    arm_b = {f"i{i}": 1.0 for i in range(10)}
    tasks = {f"i{i}": "arc_easy" for i in range(10)}
    one_cluster = {f"i{i}": "only" for i in range(10)}
    items = align_paired_items(arm_a, arm_b, tasks, one_cluster)
    interval = cluster_bootstrap_difference(items, n_bootstrap=200, analysis_seed=1)
    assert interval.n_clusters == 1
    assert interval.point == 1.0
    assert interval.ci_lo == interval.ci_hi == 1.0


def test_mismatched_ids_and_missing_predictions_invalidate() -> None:
    arm_a = {"i0": 1.0, "i1": 0.0}
    arm_b = {"i0": 1.0, "i2": 0.0}
    with pytest.raises(BootstrapError, match="identical item IDs"):
        align_paired_items(
            arm_a, arm_b, {"i0": "t", "i1": "t", "i2": "t"}, {"i0": "c", "i1": "c", "i2": "c"}
        )
    with pytest.raises(BootstrapError, match="missing prediction"):
        align_paired_items({"i0": None}, {"i0": 1.0}, {"i0": "t"}, {"i0": "c"})  # type: ignore[dict-item]
    with pytest.raises(BootstrapError, match="no task assignment"):
        align_paired_items({"i0": 1.0}, {"i0": 1.0}, {}, {"i0": "c"})


def test_seed_spread_separates_seed_from_item_uncertainty() -> None:
    assert seed_spread({"s1": 1.0}) is None
    spread = seed_spread({"s1": 1.0, "s2": 3.0})
    assert spread is not None
    assert spread.mean == pytest.approx(2.0)
    assert (spread.minimum, spread.maximum) == (1.0, 3.0)


def _suite_items() -> tuple[list[Any], dict[str, float]]:
    """Aligned items across all four required tasks with a known B win."""
    from xlm.comparison.bootstrap import AlignedItem

    items: list[Any] = []
    for task in REQUIRED_TASKS_FOR_INDEX:
        for i in range(8):
            correct_b = 1.0 if (i % 4 != 0) else 0.0
            items.append(
                AlignedItem(
                    item_id=f"{task}_{i}",
                    task=task,
                    cluster_id=f"{task}_cluster_{i % 2}",
                    score_a=float(i % 2),
                    score_b=correct_b,
                )
            )
    chances = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
    return items, chances


def test_suite_bootstrap_computes_index_intervals() -> None:
    items, chances = _suite_items()
    result = suite_bootstrap(items, chances, n_bootstrap=200, analysis_seed=7)
    assert result.index_interval is not None
    assert result.index_interval.point > 0
    assert set(result.task_intervals) == set(REQUIRED_TASKS_FOR_INDEX)
    assert result.seed_spread is None
    assert "unmeasured" in result.seed_note


def test_suite_bootstrap_rejects_partial_coverage() -> None:
    items, chances = _suite_items()
    partial = [i for i in items if i.task != "piqa"]
    with pytest.raises(BootstrapError, match="missing tasks.*piqa"):
        suite_bootstrap(partial, chances, n_bootstrap=200, analysis_seed=7)


def test_suite_bootstrap_macro_averages_blimp_subclusters() -> None:
    from xlm.comparison.bootstrap import AlignedItem

    items: list[Any] = []
    # BLiMP cluster_0 always wrong for B, cluster_1 always right: macro = 0.5.
    for i in range(4):
        items.append(AlignedItem(f"blimp_{i}", "blimp", f"blimp_sub_{i % 2}", 0.0, float(i % 2)))
    for task in ("arc_easy", "hellaswag", "piqa"):
        for i in range(4):
            items.append(AlignedItem(f"{task}_{i}", task, f"{task}_c", 0.0, 1.0))
    chances = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
    result = suite_bootstrap(items, chances, n_bootstrap=200, analysis_seed=7)
    assert result.index_interval is not None
    # Hand-computed: blimp macro acc diff 0.5, others 1.0; chance-adjusted below.
    assert result.task_intervals["blimp"].point == pytest.approx(0.5)


# --------------------------------------------------------------------- curves


def _curve(higher_is_better: bool = True) -> LearningCurve:
    from xlm.comparison.curves import CurvePoint

    return LearningCurve(
        run_id="run_a",
        higher_is_better=higher_is_better,
        points=(
            CurvePoint(100, 0.20),
            CurvePoint(200, 0.40),
            CurvePoint(400, 0.60),
        ),
    )


def test_crossing_interpolates_between_measured_points() -> None:
    assert crossing_at_target(_curve(), 0.50) == pytest.approx(300.0)
    assert crossing_at_target(_curve(), 0.20) == pytest.approx(100.0)
    lower = LearningCurve(
        run_id="run_b",
        higher_is_better=False,
        points=(CurvePoint(100, 0.60), CurvePoint(200, 0.40)),
    )
    assert crossing_at_target(lower, 0.50) == pytest.approx(150.0)


def test_unreached_and_out_of_range_targets_are_not_invented() -> None:
    assert crossing_at_target(_curve(), 0.95) is None
    assert crossing_at_target(_curve(), 5.0) is None, "no extrapolation past the last point"


def test_teacher_and_auxiliary_costs_are_labeled() -> None:
    curve = LearningCurve(
        run_id="run_a",
        higher_is_better=True,
        points=(
            CurvePoint(100, 0.5, compute_seconds=10.0),
            CurvePoint(200, 0.7, compute_seconds=20.0),
        ),
        teacher_compute_seconds=100.0,
        auxiliary_compute_seconds=5.0,
    )
    assert curve.total_compute_seconds() == pytest.approx(125.0)
    target = compute_to_target(curve, 0.6)
    assert target.reached and target.compute_seconds_to_target == pytest.approx(15.0)
    assert target.basis == "measured_interpolation"


def test_compute_comparison_requires_both_arms_to_reach() -> None:
    reached = compute_to_target(_curve(), 0.5, tokens_per_second=10.0)
    missed = compute_to_target(_curve(), 0.95, tokens_per_second=10.0)
    comparison = compare_compute_to_target(reached, missed)
    assert not comparison.comparable
    assert comparison.compute_saving_fraction is None
    assert any("never reached" in r for r in comparison.reasons)

    other = compute_to_target(_curve(), 0.4, tokens_per_second=20.0)
    both = compare_compute_to_target(reached, other)
    assert both.comparable
    assert both.compute_saving_fraction is not None


def test_malformed_curve_mappings_are_rejected() -> None:
    with pytest.raises(CurveError, match="malformed"):
        curves_from_mapping({"run_id": "x"})
    with pytest.raises(CurveError, match="at least two"):
        curves_from_mapping({"run_id": "x", "higher_is_better": True, "points": []})


# ------------------------------------------------------------------ promotion


def _passing_evidence() -> PromotionEvidence:
    from xlm.comparison.promotion import PromotionEvidence

    return PromotionEvidence(
        baseline_run_id="run_a",
        candidate_run_id="run_b",
        track="architecture",
        suite_index_delta=1.5,
        index_ci_lo=0.5,
        index_ci_hi=2.5,
        task_deltas={"blimp": 2.0, "arc_easy": 1.0, "hellaswag": 1.2, "piqa": 0.8},
        compute_saving_fraction=None,
        n_seeds_baseline=2,
        n_seeds_candidate=2,
        comparisons_examined=3,
        tuning_trials=4,
    )


def test_promotion_passes_on_material_evidence() -> None:

    decision = evaluate_promotion(_passing_evidence(), PromotionGates(), True, [])
    assert decision.promote is True
    assert decision.gate_version == "1"
    assert any("all promotion gates passed" in r for r in decision.reasons)
    assert "3 comparison(s)" in decision.multiplicity_note
    assert decision_fingerprint(decision)


def test_promotion_refuses_ineligible_missing_or_weak_evidence() -> None:

    ineligible = evaluate_promotion(_passing_evidence(), PromotionGates(), False, ["bad track"])
    assert ineligible.promote is False

    empty = PromotionEvidence(
        baseline_run_id="a",
        candidate_run_id="b",
        track="architecture",
        suite_index_delta=None,
        index_ci_lo=None,
        index_ci_hi=None,
        task_deltas={},
        compute_saving_fraction=None,
        n_seeds_baseline=2,
        n_seeds_candidate=2,
    )
    assert evaluate_promotion(empty, PromotionGates(), True, []).promote is False

    weak_ci = replace(_passing_evidence(), index_ci_lo=-0.5, index_ci_hi=2.5)
    assert evaluate_promotion(weak_ci, PromotionGates(), True, []).promote is False

    one_seed = replace(_passing_evidence(), n_seeds_candidate=1)
    assert evaluate_promotion(one_seed, PromotionGates(), True, []).promote is False


def test_material_regression_warns_without_silently_passing() -> None:

    regressed = replace(_passing_evidence(), task_deltas={"blimp": -1.2, "arc_easy": 3.0})
    decision = evaluate_promotion(regressed, PromotionGates(), True, [])
    assert decision.promote is True
    assert any("material regression on 'blimp'" in w for w in decision.warnings)


def test_gate_thresholds_are_inputs_not_rewritten_by_scores() -> None:

    strict = PromotionGates(min_suite_delta=100.0)
    assert evaluate_promotion(_passing_evidence(), strict, True, []).promote is False
    assert strict.gate_version == "1"
    assert evaluate_promotion(_passing_evidence(), PromotionGates(), True, []).promote is True


def test_promotion_emits_a_valid_from_scratch_draft(tmp_path: Path) -> None:
    from xlm.config.schemas import ExperimentDraftConfig

    decision = evaluate_promotion(_passing_evidence(), PromotionGates(), True, [])
    baseline_plan = {
        "track": "baseline",
        "resolved_config": {
            "model": {"preset": "50m"},
            "data": {"mixture_preset": "mix01"},
            "objective": {"type": "cross_entropy"},
            "optimizer": {"type": "adamw", "lr": 0.001},
            "training": {
                "device": "cuda",
                "budget": {"max_valid_targets": 1000},
                "schedule": {
                    "type": "warmup_cosine",
                    "horizon_valid_targets": 1000,
                    "warmup_valid_targets": 100,
                    "min_lr_ratio": 0.1,
                },
                "init_seed": 101,
            },
        },
    }
    draft = next_size_draft(
        "150m",
        baseline_plan,
        decision,
        "run_b",
        tmp_path / "promoted.json",
        throughput_range=(1000.0, 2000.0),
    )
    validated = ExperimentDraftConfig.model_validate(draft)
    assert validated.model == {"preset": "150m"}
    assert validated.authorization.state == "not_authorized"
    assert validated.evaluation.allow_final is False
    lineage = json.loads((tmp_path / "promoted.lineage.json").read_text(encoding="utf-8"))
    assert lineage["from_scratch"] is True
    assert lineage["resized_weights"] is False
    assert lineage["projected_eta_seconds"] == [0.5, 1.0]
    with pytest.raises(PromotionError, match="reference 150m/300m"):
        next_size_draft("50m", baseline_plan, decision, "run_b", tmp_path / "x.json")


def test_promotion_needs_a_passing_decision_for_drafts(tmp_path: Path) -> None:

    failing = evaluate_promotion(_passing_evidence(), PromotionGates(min_suite_delta=99), True, [])
    with pytest.raises(PromotionError, match="passing decision"):
        next_size_draft("150m", {"resolved_config": {}}, failing, "run_b", tmp_path / "x.json")


def _write_arm_evidence(
    path: Path, fingerprint: str, correct: dict[str, list[bool]], chances: dict[str, float]
) -> None:
    tasks: dict[str, Any] = {}
    for task_name, flags in correct.items():
        metric = "acc_norm" if task_name in ("arc_easy", "hellaswag") else "acc"
        tasks[task_name] = {
            "metric_name": metric,
            "chance": chances[task_name],
            "items": [
                {
                    "item_id": f"{task_name}_{i}",
                    "is_correct": bool(flag),
                    "is_correct_normalized": bool(flag),
                    "omitted_reason": None,
                }
                for i, flag in enumerate(flags)
            ],
        }
    path.write_text(
        json.dumps(
            {
                "identity": {
                    "fingerprint": fingerprint,
                    "checkpoint_hash": fingerprint,
                    "tokenizer_hash": "tok_fp_1",
                },
                "training_seeds": {
                    "init_seed": 2 if fingerprint.endswith("2") else 1,
                    "data_seed": 20260918,
                },
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )


def _write_plan(path: Path, plan_id: str = "plan_x") -> None:
    plan = {
        "plan_id": plan_id,
        "track": "baseline",
        "horizon_kind": "standalone",
        "resolved_config": {
            "model": {"architecture": "transformer_baseline", "vocab_size": 64},
            "training": {
                "data_seed": 1,
                "init_seed": 2,
                "context_length": 16,
                "precision": "fp32",
                "budget": {"max_valid_targets": 100},
                "schedule": {
                    "type": "warmup_cosine",
                    "horizon_valid_targets": 100,
                    "warmup_valid_targets": 10,
                    "min_lr_ratio": 0.1,
                },
            },
            "data": {
                "mixture_preset": "mix01",
                "mixture_details": {"id": "mix01", "weights": {"a": 1.0}},
                "packing_policy": "causal_stream",
            },
            "objective": {"type": "cross_entropy"},
            "optimizer": {"type": "adamw", "tuning_allowance": 1},
        },
    }
    path.write_text(json.dumps(plan), encoding="utf-8")


def _write_facts(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "total_params": 1000,
                "canonical_bytes": 500,
                "matched_bytes": None,
                "matched_compute": None,
                "measured_compute_seconds": 5.0,
            }
        ),
        encoding="utf-8",
    )


def _invoke(args: list[str]) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    return CliRunner().invoke(app, args)


CHANCES = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
BASELINE_CORRECT = {task: [i % 2 == 0 for i in range(8)] for task in REQUIRED_TASKS_FOR_INDEX}
CANDIDATE_CORRECT = {task: [i % 4 != 3 for i in range(8)] for task in REQUIRED_TASKS_FOR_INDEX}


def test_cli_compare_reports_eligible_paired_difference(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    _write_arm_evidence(base, "fp_base", BASELINE_CORRECT, CHANCES)
    _write_arm_evidence(cand, "fp_cand", CANDIDATE_CORRECT, CHANCES)
    plan_a = tmp_path / "plan_a.json"
    plan_b = tmp_path / "plan_b.json"
    _write_plan(plan_a)
    _write_plan(plan_b)
    facts = tmp_path / "facts.json"
    _write_facts(facts)
    clusters = tmp_path / "clusters.json"
    clusters.write_text(
        json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(8)}),
        encoding="utf-8",
    )
    out = tmp_path / "comparison.json"
    result = _invoke(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--plan-baseline",
            str(plan_a),
            "--plan-candidate",
            str(plan_b),
            "--track",
            "architecture",
            "--facts-baseline",
            str(facts),
            "--facts-candidate",
            str(facts),
            "--clusters",
            str(clusters),
            "--n-bootstrap",
            "200",
            "--output",
            str(out),
        ]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["eligibility"]["eligible"] is True
    assert payload["index_difference"] is not None and payload["index_difference"] > 0
    assert payload["suite"]["seed_note"] == (
        "single training seed per arm: between-seed variability is unmeasured"
    )


def test_cli_compare_refuses_ineligible_tracks_before_any_winner(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    _write_arm_evidence(base, "fp_base", BASELINE_CORRECT, CHANCES)
    _write_arm_evidence(cand, "fp_cand", CANDIDATE_CORRECT, CHANCES)
    plan_a = tmp_path / "plan_a.json"
    plan_b = tmp_path / "plan_b.json"
    _write_plan(plan_a)
    _write_plan(plan_b, plan_id="plan_y")
    facts = tmp_path / "facts.json"
    _write_facts(facts)
    tampered_facts = tmp_path / "facts_tok.json"
    tampered_facts.write_text(
        json.dumps(
            {**json.loads(facts.read_text(encoding="utf-8")), "tokenizer_hash": "different"}
        ),
        encoding="utf-8",
    )
    out = tmp_path / "comparison.json"
    result = _invoke(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--plan-baseline",
            str(plan_a),
            "--plan-candidate",
            str(plan_b),
            "--track",
            "architecture",
            "--facts-baseline",
            str(facts),
            "--facts-candidate",
            str(tampered_facts),
            "--n-bootstrap",
            "200",
            "--output",
            str(out),
        ]
    )
    assert result.exit_code == 1
    assert "tokenizer_hash" in result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["eligibility"]["eligible"] is False
    assert payload["suite"] is None and payload["index_difference"] is None


def test_cli_promote_emits_a_valid_draft_on_pass(tmp_path: Path) -> None:
    from xlm.config.schemas import ExperimentDraftConfig

    base1 = tmp_path / "base1.json"
    base2 = tmp_path / "base2.json"
    cand1 = tmp_path / "cand1.json"
    cand2 = tmp_path / "cand2.json"
    _write_arm_evidence(base1, "fp_b1", BASELINE_CORRECT, CHANCES)
    _write_arm_evidence(base2, "fp_b2", BASELINE_CORRECT, CHANCES)
    _write_arm_evidence(cand1, "fp_c1", CANDIDATE_CORRECT, CHANCES)
    _write_arm_evidence(cand2, "fp_c2", CANDIDATE_CORRECT, CHANCES)
    plan_a = tmp_path / "plan_a.json"
    plan_b = tmp_path / "plan_b.json"
    _write_plan(plan_a)
    _write_plan(plan_b)
    facts = tmp_path / "facts.json"
    _write_facts(facts)
    clusters = tmp_path / "clusters.json"
    clusters.write_text(
        json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(8)}),
        encoding="utf-8",
    )
    comparison = tmp_path / "comparison.json"
    compared = _invoke(
        [
            "compare",
            "--baseline",
            str(base1),
            "--baseline",
            str(base2),
            "--candidate",
            str(cand1),
            "--candidate",
            str(cand2),
            "--plan-baseline",
            str(plan_a),
            "--plan-candidate",
            str(plan_b),
            "--track",
            "architecture",
            "--facts-baseline",
            str(facts),
            "--facts-candidate",
            str(facts),
            "--clusters",
            str(clusters),
            "--n-bootstrap",
            "200",
            "--output",
            str(comparison),
        ]
    )
    assert compared.exit_code == 0, compared.output

    draft_out = tmp_path / "promoted.json"
    decision_out = tmp_path / "decision.json"
    promoted = _invoke(
        [
            "promote",
            "--comparison",
            str(comparison),
            "--plan",
            str(plan_a),
            "--to-size",
            "150m",
            "--output-draft",
            str(draft_out),
            "--output-decision",
            str(decision_out),
        ]
    )
    assert promoted.exit_code == 0, promoted.output
    validated = ExperimentDraftConfig.model_validate(
        json.loads(draft_out.read_text(encoding="utf-8"))
    )
    assert validated.model == {"preset": "150m"}
    assert validated.authorization.state == "not_authorized"
    decision = json.loads(decision_out.read_text(encoding="utf-8"))
    assert decision["fingerprint"]


def test_cli_promote_refuses_without_passing_gates(tmp_path: Path) -> None:
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    _write_arm_evidence(base, "fp_base", BASELINE_CORRECT, CHANCES)
    _write_arm_evidence(cand, "fp_cand", CANDIDATE_CORRECT, CHANCES)
    plan_a = tmp_path / "plan_a.json"
    _write_plan(plan_a)
    facts = tmp_path / "facts.json"
    _write_facts(facts)
    clusters = tmp_path / "clusters.json"
    clusters.write_text(
        json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(8)}),
        encoding="utf-8",
    )
    comparison = tmp_path / "comparison.json"
    compared = _invoke(
        [
            "compare",
            "--baseline",
            str(base),
            "--candidate",
            str(cand),
            "--plan-baseline",
            str(plan_a),
            "--plan-candidate",
            str(plan_a),
            "--track",
            "architecture",
            "--facts-baseline",
            str(facts),
            "--facts-candidate",
            str(facts),
            "--clusters",
            str(clusters),
            "--n-bootstrap",
            "200",
            "--output",
            str(comparison),
        ]
    )
    assert compared.exit_code == 0, compared.output

    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps({"min_suite_delta": 1000.0}), encoding="utf-8")
    refused = _invoke(
        [
            "promote",
            "--comparison",
            str(comparison),
            "--plan",
            str(plan_a),
            "--to-size",
            "150m",
            "--gates",
            str(gates),
            "--output-draft",
            str(tmp_path / "promoted.json"),
            "--output-decision",
            str(tmp_path / "decision.json"),
        ]
    )
    assert refused.exit_code == 1
    assert not (tmp_path / "promoted.json").exists()
    assert (tmp_path / "decision.json").is_file()


def test_factorial_and_ablation_matrices_emit_plans_only(tmp_path: Path) -> None:
    from xlm.config.schemas import ExperimentDraftConfig

    drafts = data_architecture_factorial(
        ["mix01", "m1_less_synth"], ["50m", "150m"], 1000, 1000, tmp_path / "factorial"
    )
    assert len(drafts) == 4
    for draft in drafts:
        ExperimentDraftConfig.model_validate(draft)
    assert not any((tmp_path / "factorial").glob("*.ckpt"))

    base = {
        "schema_version": 1,
        "kind": "experiment_draft",
        "id": "base",
        "status": "draft_requires_artifacts_and_approval",
        "track": "baseline",
        "model": {"preset": "50m"},
        "data": {"mixture_preset": "mix01"},
        "objective": {"type": "cross_entropy"},
        "optimizer": {"type": "adamw", "lr": 0.001},
        "training": {
            "device": "cpu",
            "budget": {"max_valid_targets": 1000},
            "schedule": {"type": "constant"},
            "init_seed": 1,
            "data_seed": 2,
            "checkpoint_every_valid_targets": 100,
        },
        "evaluation": {"suite": "search", "every_valid_targets": 100, "allow_final": False},
        "resources": {"max_gpu_processes": 1},
        "authorization": {"state": "not_authorized", "plan_hash": None},
    }
    cells = two_idea_ablation_matrix("idea_a", "idea_b", base, tmp_path / "ablation")
    assert len(cells) == 4
    for cell in cells:
        ExperimentDraftConfig.model_validate(cell)
    matrix = json.loads(
        (tmp_path / "ablation" / "ablation_matrix.json").read_text(encoding="utf-8")
    )
    assert sorted(c["cell"] for c in matrix) == ["a_only", "b_only", "both", "neither"]
