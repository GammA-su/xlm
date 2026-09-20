"""D08 before-evidence: order-dependent headlines (A) and lost draw multiplicity (B).

Authored synthetic evidence only. Exercises the real comparison CLI and the
real suite_bootstrap against independently computed expectations. Exit 0 when
both defects reproduce as described; any deviation fails loudly.
"""

import argparse
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

CHANCES = {"blimp": 0.5, "arc_easy": 0.25, "hellaswag": 0.25, "piqa": 0.5}
TASKS = ("blimp", "arc_easy", "hellaswag", "piqa")


def write_arm_evidence(
    path: Path, fingerprint: str, init_seed: int, data_seed: int, correct: dict[str, list[bool]]
) -> None:
    tasks: dict[str, object] = {}
    for task_name, flags in correct.items():
        metric = "acc_norm" if task_name in ("arc_easy", "hellaswag") else "acc"
        tasks[task_name] = {
            "metric_name": metric,
            "chance": CHANCES[task_name],
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
                "identity": {"fingerprint": fingerprint, "checkpoint_hash": fingerprint,
                             "tokenizer_hash": "tok_fp_1"},
                "training_seeds": {"init_seed": init_seed, "data_seed": data_seed},
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )


def write_plan(path: Path, plan_id: str = "plan_x") -> None:
    plan = {
        "plan_id": plan_id, "track": "baseline", "horizon_kind": "standalone",
        "resolved_config": {
            "model": {"architecture": "transformer_baseline", "vocab_size": 64},
            "training": {"data_seed": 1, "init_seed": 2, "context_length": 16,
                         "precision": "fp32", "budget": {"max_valid_targets": 100},
                         "schedule": {"type": "warmup_cosine", "horizon_valid_targets": 100,
                                      "warmup_valid_targets": 10, "min_lr_ratio": 0.1}},
            "data": {"mixture_preset": "mix01",
                     "mixture_details": {"id": "mix01", "weights": {"a": 1.0}},
                     "packing_policy": "causal_stream"},
            "objective": {"type": "cross_entropy"},
            "optimizer": {"type": "adamw", "tuning_allowance": 1},
        },
    }
    path.write_text(json.dumps(plan), encoding="utf-8")


def write_facts(path: Path) -> None:
    path.write_text(json.dumps({"total_params": 1000, "canonical_bytes": 500,
                                "matched_bytes": None, "matched_compute": None,
                                "measured_compute_seconds": 5.0}), encoding="utf-8")


def invoke(args: list[str]) -> object:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    return CliRunner().invoke(app, args)


def reproduce_order_dependence(work: Path) -> dict[str, object]:
    """Two seeds with opposite per-seed winners; headline must not flip on reorder."""
    from xlm.comparison.bootstrap import pair_seed_evidence

    neutral = {t: [True, True, True, True] for t in TASKS}
    # Seed 1: candidate sweeps arc_easy, baseline blank -> candidate ahead.
    base1 = {**neutral, "arc_easy": [False] * 4}
    cand1 = {**neutral, "arc_easy": [True] * 4}
    # Seed 2: mirror image -> baseline ahead.
    base2 = {**neutral, "arc_easy": [True] * 4}
    cand2 = {**neutral, "arc_easy": [False] * 4}
    files = {}
    for name, fp, init, data, correct in [
        ("b1", "fp_b1", 1, 100, base1), ("b2", "fp_b2", 2, 200, base2),
        ("c1", "fp_c1", 1, 100, cand1), ("c2", "fp_c2", 2, 200, cand2),
    ]:
        files[name] = work / f"{name}.json"
        write_arm_evidence(files[name], fp, init, data, correct)
    plan_a, plan_b, facts = work / "plan_a.json", work / "plan_b.json", work / "facts.json"
    write_plan(plan_a)
    write_plan(plan_b)
    write_facts(facts)
    clusters = work / "clusters.json"
    clusters.write_text(json.dumps({f"blimp:blimp_{i}": f"blimp_sub_{i % 2}" for i in range(4)}))

    # Positive control: recorded-seed pairing itself is order-independent.
    first = pair_seed_evidence(
        [json.loads(files["b1"].read_text()), json.loads(files["b2"].read_text())],
        [json.loads(files["c1"].read_text()), json.loads(files["c2"].read_text())],
    )
    swapped = pair_seed_evidence(
        [json.loads(files["b2"].read_text()), json.loads(files["b1"].read_text())],
        [json.loads(files["c2"].read_text()), json.loads(files["c1"].read_text())],
    )
    assert [(a["identity"]["fingerprint"], b["identity"]["fingerprint"]) for a, b in first] == [
        ("fp_b1", "fp_c1"), ("fp_b2", "fp_c2")]
    assert [(a["identity"]["fingerprint"], b["identity"]["fingerprint"]) for a, b in swapped] == [
        ("fp_b1", "fp_c1"), ("fp_b2", "fp_c2")]
    print("positive control: pair_seed_evidence is order-independent", flush=True)

    def compare(b_order: list[str], c_order: list[str], tag: str) -> dict[str, object]:
        out = work / f"comparison_{tag}.json"
        args = ["compare"]
        for name in b_order:
            args += ["--baseline", str(files[name])]
        for name in c_order:
            args += ["--candidate", str(files[name])]
        args += ["--plan-baseline", str(plan_a), "--plan-candidate", str(plan_b),
                 "--track", "architecture", "--facts-baseline", str(facts),
                 "--facts-candidate", str(facts), "--clusters", str(clusters),
                 "--n-bootstrap", "100", "--output", str(out)]
        result = invoke(args)
        assert result.exit_code == 0, result.output
        return json.loads(out.read_text(encoding="utf-8"))

    order_ab = compare(["b1", "b2"], ["c1", "c2"], "ab")
    order_ba = compare(["b2", "b1"], ["c2", "c1"], "ba")
    d_ab = order_ab["index_difference"]
    d_ba = order_ba["index_difference"]
    print(f"headline index_difference order1={d_ab:+.4f} order2={d_ba:+.4f}", flush=True)
    print(f"baseline_run order1={order_ab['baseline_run']} order2={order_ba['baseline_run']}", flush=True)
    print(f"seeds order1={order_ab['seeds']} order2={order_ba['seeds']}", flush=True)
    assert d_ab is not None and d_ba is not None
    assert d_ab > 0 > d_ba, "expected the headline to flip sign on reorder (defect A)"
    assert (d_ab, order_ab["baseline_run"]) != (d_ba, order_ba["baseline_run"])
    print("D08-A REPRODUCED: headline/identity depend on argument order", flush=True)
    (work / "order_ab.json").write_text(json.dumps(order_ab, indent=2, sort_keys=True))
    (work / "order_ba.json").write_text(json.dumps(order_ba, indent=2, sort_keys=True))
    return {"order_ab": d_ab, "order_ba": d_ba}


def hand_mean(draw: list[float]) -> float:
    """Independent arithmetic: mean over the drawn multisequence (1/3 for [0,0,1])."""
    assert draw, "empty draw"
    return sum(draw) / len(draw)


def expected_blimp_ci(
    diffs: dict[str, float], n_bootstrap: int, ci_level: float, analysis_seed: int
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Independently recompute the BLiMP-task CI under both aggregation rules.

    Returns (buggy_ci, correct_ci): buggy collapses repeated draws by cluster id
    (macro over distinct clusters); correct retains occurrences (macro over the
    drawn multisequence, so draw [A,A,C] with A=0/C=1 scores 1/3). The RNG
    stream mirrors suite_bootstrap exactly: stratified per-task draws in
    required_tasks order, including the constant single-cluster slots whose
    values never vary but whose draws advance the stream.
    """
    from xlm.comparison.bootstrap import _percentile

    cluster_ids = sorted(diffs)
    buggy_reps, correct_reps = [], []
    rng = random.Random(analysis_seed)
    for _ in range(n_bootstrap):
        drawn = [rng.choice(cluster_ids) for _ in cluster_ids]
        for _ in ("arc_easy", "hellaswag", "piqa"):
            rng.choice([0])
        distinct = sorted(set(drawn))
        buggy_reps.append(sum(diffs[c] for c in distinct) / len(distinct))
        correct_reps.append(hand_mean([diffs[c] for c in drawn]))
    buggy_reps.sort()
    correct_reps.sort()
    tail = (1.0 - ci_level) / 2.0
    buggy = (_percentile(buggy_reps, tail), _percentile(buggy_reps, 1.0 - tail))
    correct = (_percentile(correct_reps, tail), _percentile(correct_reps, 1.0 - tail))
    return buggy, correct


def reproduce_multiplicity() -> dict[str, object]:
    from xlm.comparison.bootstrap import AlignedItem, cluster_bootstrap_difference, suite_bootstrap

    assert hand_mean([0.0, 0.0, 1.0]) == 1 / 3, "hand-checkable draw mean must be 1/3, not 1/2"
    # Three BLiMP clusters: repeats (e.g. draw [A,A,C]) separate occurrence
    # weighting (1/3-type values) from distinct-cluster collapse (1/2-type).
    items = [
        AlignedItem(item_id="blimp_A", task="blimp", cluster_id="A", score_a=0.0, score_b=0.0),
        AlignedItem(item_id="blimp_B", task="blimp", cluster_id="B", score_a=0.0, score_b=0.0),
        AlignedItem(item_id="blimp_C", task="blimp", cluster_id="C", score_a=0.0, score_b=1.0),
        AlignedItem(item_id="arc_easy_0", task="arc_easy", cluster_id="arc_easy",
                    score_a=1.0, score_b=1.0),
        AlignedItem(item_id="hellaswag_0", task="hellaswag", cluster_id="hellaswag",
                    score_a=1.0, score_b=1.0),
        AlignedItem(item_id="piqa_0", task="piqa", cluster_id="piqa", score_a=1.0, score_b=1.0),
    ]
    chances = dict(CHANCES)
    # ci_level=0.8 separates the models robustly (buggy hi 0.5 vs correct hi
    # 2/3 across every probed seed); 0.95 coincides at the extremes by construction.
    result = suite_bootstrap(items, chances, n_bootstrap=1000, ci_level=0.8, analysis_seed=7)
    actual = (result.task_intervals["blimp"].ci_lo, result.task_intervals["blimp"].ci_hi)
    buggy, correct = expected_blimp_ci({"A": 0.0, "B": 0.0, "C": 1.0}, 1000, 0.8, 7)
    print(f"blimp CI actual={actual} buggy-expected={buggy} correct-expected={correct}", flush=True)
    assert actual == buggy, "actual replicates must match the collapse-by-cluster model (defect B)"
    assert actual != correct, "actual replicates must differ from the multiplicity model"
    # Positive control: the single-task path already preserves multiplicity.
    control = cluster_bootstrap_difference(
        [items[0], items[1], items[2]], n_bootstrap=1000, ci_level=0.95, analysis_seed=7
    )
    assert control.point == 1 / 3, control.point
    again = cluster_bootstrap_difference(
        [items[0], items[1], items[2]], n_bootstrap=1000, ci_level=0.95, analysis_seed=7
    )
    assert (again.ci_lo, again.ci_hi) == (control.ci_lo, control.ci_hi)
    print(f"positive control: single-task CI=({control.ci_lo:.4f},{control.ci_hi:.4f}) deterministic", flush=True)
    print("D08-B REPRODUCED: suite BLiMP aggregation drops repeated draws", flush=True)
    return {"actual": list(actual), "buggy": [list(buggy)], "correct": [list(correct)]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    options = parser.parse_args()
    work = Path(options.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    summary = {
        "order_dependence": reproduce_order_dependence(work),
        "multiplicity": reproduce_multiplicity(),
    }
    (work / "repro_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    print("D08 BEFORE-EVIDENCE COMPLETE", flush=True)


if __name__ == "__main__":
    main()