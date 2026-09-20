"""CLI for fair comparisons and promotion gates (P17, A30-A32)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: cannot read {label} '{path}': {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(data, dict):
        typer.echo(f"Error: {label} '{path}' must be a JSON mapping.", err=True)
        raise typer.Exit(code=1)
    return data


def _outcomes_by_task(
    evidence: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """Per-task item correctness under each task's own metric, keyed task:item."""
    from xlm.comparison.bootstrap import BootstrapError

    arms: dict[str, dict[str, float]] = {}
    metrics: dict[str, str] = {}
    for task_name, task in evidence.get("tasks", {}).items():
        metric = task.get("metric_name", "acc_norm")
        metrics[task_name] = metric
        per_item: dict[str, float] = {}
        for item in task.get("items", []):
            if item.get("omitted_reason") is not None:
                continue
            key = f"{task_name}:{item.get('item_id')}"
            if key in per_item:
                raise BootstrapError(
                    f"task '{task_name}' repeats item '{item.get('item_id')}' "
                    "inside one evidence file; duplicate input records are refused"
                )
            per_item[key] = float(
                item.get("is_correct_normalized" if metric == "acc_norm" else "is_correct")
            )
        if not per_item:
            raise BootstrapError(f"task '{task_name}' contributes no scored predictions")
        arms[task_name] = per_item
    return arms, metrics


def _load_clusters(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = _load_json(path, "cluster map")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        typer.echo("Error: cluster map must be {item_key: cluster_id} strings.", err=True)
        raise typer.Exit(code=1)
    return dict(data)


def compare_command(
    baseline: Annotated[
        list[Path], typer.Option("--baseline", help="Baseline evidence JSON (repeat per seed).")
    ],
    candidate: Annotated[
        list[Path], typer.Option("--candidate", help="Candidate evidence JSON (repeat per seed).")
    ],
    plan_baseline: Annotated[Path, typer.Option("--plan-baseline", help="Baseline P16 plan JSON.")],
    plan_candidate: Annotated[
        Path, typer.Option("--plan-candidate", help="Candidate P16 plan JSON.")
    ],
    track: Annotated[str, typer.Option("--track", help="Comparison track.")],
    facts_baseline: Annotated[Path | None, typer.Option("--facts-baseline")] = None,
    facts_candidate: Annotated[Path | None, typer.Option("--facts-candidate")] = None,
    clusters: Annotated[
        Path | None, typer.Option("--clusters", help="item_key -> cluster JSON.")
    ] = None,
    n_bootstrap: Annotated[int, typer.Option("--n-bootstrap")] = 1000,
    analysis_seed: Annotated[int, typer.Option("--analysis-seed")] = 20260918,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("comparison.json"),
) -> None:
    """Check track eligibility, then run the paired cluster bootstrap.

    Recorded (init_seed, data_seed) pairs are matched before anything else. The
    headline analysis uses the numerically smallest pair
    (``numeric_lexicographic_min_v1``); every required pair must be eligible and
    cover identical item populations. A pooled multi-seed analysis would be a
    different analysis requiring its own policy, not part of this command.
    """
    from xlm.comparison.bootstrap import (
        BOOTSTRAP_VERSION,
        PRIMARY_SELECTION_POLICY,
        BootstrapError,
        align_paired_items,
        pair_seed_evidence,
        seed_spread,
        suite_bootstrap,
    )
    from xlm.comparison.tracks import ComparisonRun, check_track_eligibility
    from xlm.evaluation.suites import REQUIRED_TASKS_FOR_INDEX

    if not baseline or not candidate:
        typer.echo("Error: at least one --baseline and one --candidate evidence file.", err=True)
        raise typer.Exit(code=1)

    base_evidence = [_load_json(p, "baseline evidence") for p in baseline]
    cand_evidence = [_load_json(p, "candidate evidence") for p in candidate]
    base_plan = _load_json(plan_baseline, "baseline plan")
    cand_plan = _load_json(plan_candidate, "candidate plan")

    def facts(evidence: dict[str, Any], extra: Path | None) -> dict[str, Any]:
        merged = {
            "unique_parameters": None,
            "canonical_bytes": None,
            "matched_bytes": None,
            "matched_compute": None,
            "measured_compute_seconds": None,
        }
        if extra is not None:
            merged.update(_load_json(extra, "run facts"))
        return merged

    def seed_key(evidence: dict[str, Any]) -> tuple[int, int] | None:
        seeds = evidence.get("training_seeds", {})
        if (
            isinstance(seeds, dict)
            and type(seeds.get("init_seed")) is int
            and type(seeds.get("data_seed")) is int
        ):
            return (seeds["init_seed"], seeds["data_seed"])
        return None

    def run_fingerprint(evidence: dict[str, Any], key: tuple[int, int] | None) -> str:
        fingerprint = evidence.get("identity", {}).get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            return fingerprint
        if key is None:
            return "unidentified-single-evidence-file"
        return f"unidentified-seed-init{key[0]}-data{key[1]}"

    def keyed_items(
        evidence: dict[str, Any],
    ) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
        arms, metrics = _outcomes_by_task(evidence)
        flat: dict[str, float] = {}
        tasks: dict[str, str] = {}
        for task_name, per_item in arms.items():
            for key, value in per_item.items():
                flat[key] = value
                tasks[key] = task_name
        return flat, tasks, metrics

    try:
        # Pair recorded seeds FIRST, before any eligibility check, primary-pair
        # selection, or headline calculation. Duplicates, missing partners, and
        # absent seed records are refused here, independent of argument order.
        # A single baseline/candidate file each without recorded seeds keeps the
        # legacy single-pair path (no order dependence is possible with one
        # pair); anything larger without complete seed records is refused.
        seed_recorded = [seed_key(e) for e in (*base_evidence, *cand_evidence)]
        if all(key is not None for key in seed_recorded):
            pairs = pair_seed_evidence(base_evidence, cand_evidence)
            pair_keys: list[tuple[int, int] | None] = [seed_key(b) for b, _ in pairs]
        elif len(base_evidence) == 1 and len(cand_evidence) == 1:
            pairs = [(base_evidence[0], cand_evidence[0])]
            pair_keys = [None]
        else:
            # Raises the recorded-seed contract error for the caller to report.
            pair_seed_evidence(base_evidence, cand_evidence)
            raise BootstrapError("unreachable: seed pairing unexpectedly succeeded")
        # pair_seed_evidence returns pairs sorted by recorded seeds; the primary
        # pair is the numeric lexicographic minimum (numeric, not string order).
        # A lone unrecorded pair is trivially primary: no order exists to depend on.
        known_keys = [key for key in pair_keys if key is not None]
        if len(known_keys) == len(pair_keys):
            primary_index = min(range(len(pairs)), key=lambda i: known_keys[i])
        else:
            assert len(pairs) == 1 and pair_keys == [None]
            primary_index = 0
        primary_key = pair_keys[primary_index]
        primary_base, primary_cand = pairs[primary_index]

        # Every required pair must be eligible; one ineligible pair refuses the
        # whole comparison instead of being silently discarded.
        eligibility_results = []
        for (base_seed_ev, cand_seed_ev), key in zip(pairs, pair_keys, strict=True):
            tag = (
                f"init_seed={key[0]} data_seed={key[1]}"
                if key is not None
                else "single evidence pair"
            )
            base_run = ComparisonRun.from_records(
                run_fingerprint(base_seed_ev, key),
                base_seed_ev,
                base_plan,
                extra=facts(base_seed_ev, facts_baseline),
            )
            cand_run = ComparisonRun.from_records(
                run_fingerprint(cand_seed_ev, key),
                cand_seed_ev,
                cand_plan,
                extra=facts(cand_seed_ev, facts_candidate),
            )
            verdict = check_track_eligibility(base_run, cand_run, track)
            if len(pairs) > 1:
                verdict.reasons = [f"seed pair ({tag}): {reason}" for reason in verdict.reasons]
            eligibility_results.append(verdict)
        eligibility = eligibility_results[primary_index]
        eligibility.eligible = all(v.eligible for v in eligibility_results)
        if len(pairs) > 1:
            eligibility.reasons = [reason for v in eligibility_results for reason in v.reasons]
        base_run_fingerprint = run_fingerprint(primary_base, primary_key)
        cand_run_fingerprint = run_fingerprint(primary_cand, primary_key)
        if not eligibility.eligible:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(
                    {
                        "comparison_version": 1,
                        "bootstrap_version": BOOTSTRAP_VERSION,
                        "primary_selection_policy": PRIMARY_SELECTION_POLICY,
                        "primary_seed_pair": list(primary_key) if primary_key is not None else None,
                        "seed_pairs": [
                            {
                                "init_seed": key[0] if key is not None else None,
                                "data_seed": key[1] if key is not None else None,
                                "baseline": run_fingerprint(b, key),
                                "candidate": run_fingerprint(c, key),
                            }
                            for (b, c), key in zip(pairs, pair_keys, strict=True)
                        ],
                        "baseline_run": base_run_fingerprint,
                        "candidate_run": cand_run_fingerprint,
                        "track": track,
                        "eligibility": eligibility.to_dict(),
                        "suite": None,
                        "index_difference": None,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            typer.echo(f"Comparison ineligible for track '{track}':")
            for reason in eligibility.reasons:
                typer.echo(f"  - {reason}")
            raise typer.Exit(code=1)

        cluster_map = _load_clusters(clusters)

        # All required pairs must cover identical item populations; seed
        # summaries over shifting populations would be meaningless.
        pair_populations = []
        pair_keyed = []
        for base_seed_ev, cand_seed_ev in pairs:
            seed_base, _, _ = keyed_items(base_seed_ev)
            seed_cand, _, _ = keyed_items(cand_seed_ev)
            if set(seed_base) != set(seed_cand):
                raise BootstrapError("baseline and candidate evidence cover different items")
            pair_populations.append(sorted(seed_base))
            pair_keyed.append((seed_base, seed_cand))
        if any(population != pair_populations[0] for population in pair_populations[1:]):
            raise BootstrapError(
                "seed pairs cover different item populations; paired multi-seed "
                "analysis requires identical populations"
            )
        primary_flat, primary_tasks, _ = keyed_items(primary_base)
        cand_flat, cand_tasks, _ = keyed_items(primary_cand)
        merged_tasks = dict(primary_tasks)
        if any(t == "blimp" for t in merged_tasks.values()) and not cluster_map:
            raise BootstrapError(
                "BLiMP evidence requires an explicit --clusters map; per-item "
                "resampling would pretend subdataset structure away"
            )
        clusters_full = {key: cluster_map.get(key, key) for key in primary_flat}
        items = align_paired_items(primary_flat, cand_flat, merged_tasks, clusters_full)

        # Chance references must agree per task across both arms and every
        # required pair. Tasks keep their own values; conflicts are refused,
        # never resolved by file order, averaging, or defaults.
        chances: dict[str, float] = {}
        for task_name in {v for v in merged_tasks.values()}:
            recorded: list[float] = []
            for evidence in (*base_evidence, *cand_evidence):
                task_block = evidence.get("tasks", {}).get(task_name, {})
                if isinstance(task_block.get("chance"), (int, float)):
                    value = float(task_block["chance"])
                    if value not in recorded:
                        recorded.append(value)
            if not recorded:
                raise BootstrapError(f"no chance reference recorded for task '{task_name}'")
            if len(recorded) > 1:
                raise BootstrapError(
                    f"conflicting chance references for task '{task_name}': "
                    f"{recorded}; recorded references must agree"
                )
            chances[task_name] = recorded[0]

        suite = suite_bootstrap(
            items,
            chances,
            required_tasks=REQUIRED_TASKS_FOR_INDEX,
            n_bootstrap=n_bootstrap,
            analysis_seed=analysis_seed,
        )
        suite.primary_seed_pair = primary_key
        suite.primary_selection_policy = PRIMARY_SELECTION_POLICY
        per_seed: dict[str, float] = {}
        if len(pairs) > 1:
            for position, key in enumerate(pair_keys):
                assert key is not None, "multi-pair flow requires recorded seeds"
                seed_base, seed_cand = pair_keyed[position]
                seed_items = align_paired_items(seed_base, seed_cand, merged_tasks, clusters_full)
                seed_suite = suite_bootstrap(
                    seed_items,
                    chances,
                    required_tasks=REQUIRED_TASKS_FOR_INDEX,
                    n_bootstrap=max(100, n_bootstrap // 10),
                    analysis_seed=analysis_seed + position,
                )
                per_seed[f"init_{key[0]}_data_{key[1]}"] = (
                    seed_suite.index_interval.point if seed_suite.index_interval else 0.0
                )
            spread = seed_spread(per_seed)
            suite.seed_spread = spread
            assert primary_key is not None, "multi-pair flow requires recorded seeds"
            suite.seed_note = (
                f"between-training-seed spread over {len(per_seed)} seed pairs; "
                f"primary pair (init_seed={primary_key[0]}, data_seed={primary_key[1]}) "
                "item intervals above are within-run uncertainty for that pair, "
                "not uncertainty across training seeds"
            )

        index_point = suite.index_interval.point if suite.index_interval else None
        payload = {
            "comparison_version": 1,
            "bootstrap_version": BOOTSTRAP_VERSION,
            "primary_selection_policy": PRIMARY_SELECTION_POLICY,
            "primary_seed_pair": list(primary_key) if primary_key is not None else None,
            "seed_pairs": [
                {
                    "init_seed": key[0] if key is not None else None,
                    "data_seed": key[1] if key is not None else None,
                    "baseline": run_fingerprint(b, key),
                    "candidate": run_fingerprint(c, key),
                }
                for (b, c), key in zip(pairs, pair_keys, strict=True)
            ],
            "baseline_run": base_run_fingerprint,
            "candidate_run": cand_run_fingerprint,
            "track": track,
            "eligibility": eligibility.to_dict(),
            "seeds": {
                "baseline": [
                    run_fingerprint(b, key) for (b, _), key in zip(pairs, pair_keys, strict=True)
                ],
                "candidate": [
                    run_fingerprint(c, key) for (_, c), key in zip(pairs, pair_keys, strict=True)
                ],
            },
            "suite": suite.to_dict(),
            "index_difference": index_point,
        }
    except BootstrapError as exc:
        typer.echo(f"Error: invalid paired analysis: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    typer.echo("============================================================")
    typer.echo(f"Track:           {track} (eligible: {eligibility.eligible})")
    for reason in eligibility.reasons:
        typer.echo(f"  ! {reason}")
    if index_point is not None and suite.index_interval is not None:
        interval = suite.index_interval
        typer.echo(
            f"Index delta:     {index_point:+.3f} [{interval.ci_lo:+.3f}, {interval.ci_hi:+.3f}]"
        )
    typer.echo(f"Seed note:       {suite.seed_note}")
    typer.echo(f"Output:          {output}")
    typer.echo("============================================================")


def promote_command(
    comparison: Annotated[Path, typer.Option("--comparison", help="xlm compare output JSON.")],
    baseline_plan: Annotated[Path, typer.Option("--plan", help="Baseline P16 plan JSON.")],
    to_size: Annotated[str, typer.Option("--to-size", help="Reference next size: 150m|300m.")],
    compute_saving: Annotated[float | None, typer.Option("--compute-saving")] = None,
    comparisons_examined: Annotated[int, typer.Option("--comparisons-examined")] = 1,
    tuning_trials: Annotated[int, typer.Option("--tuning-trials")] = 0,
    scale_exceptions: Annotated[str | None, typer.Option("--scale-exceptions")] = None,
    throughput: Annotated[str | None, typer.Option("--throughput", help="'lo,hi' tok/s.")] = None,
    gates: Annotated[Path | None, typer.Option("--gates", help="Gate rules JSON override.")] = None,
    output_draft: Annotated[Path, typer.Option("--output-draft", "-o")] = Path(
        "promoted_draft.json"
    ),
    output_decision: Annotated[Path, typer.Option("--output-decision")] = Path(
        "promotion_decision.json"
    ),
) -> None:
    """Apply frozen gates; on pass, emit a from-scratch draft. Never launches."""
    from xlm.comparison.bootstrap import BOOTSTRAP_VERSION, PRIMARY_SELECTION_POLICY
    from xlm.comparison.promotion import (
        PromotionError,
        PromotionEvidence,
        PromotionGates,
        decision_fingerprint,
        evaluate_promotion,
        next_size_draft,
    )

    compared = _load_json(comparison, "comparison output")
    analysis_version = compared.get("bootstrap_version")
    analysis_policy = compared.get("primary_selection_policy")
    if analysis_version != BOOTSTRAP_VERSION or analysis_policy != PRIMARY_SELECTION_POLICY:
        # Saved results from the pre-D08 analysis stay available for historical
        # inspection, but they must never silently satisfy current requirements:
        # their headlines could depend on argument order and their BLiMP
        # intervals drop repeated draws. Re-run `xlm compare` to refresh.
        typer.echo(
            "Error: comparison output predates the corrected D08 analysis "
            f"(bootstrap_version={analysis_version!r}, "
            f"primary_selection_policy={analysis_policy!r}); re-run `xlm compare` "
            "before promotion.",
            err=True,
        )
        raise typer.Exit(code=1)
    plan = _load_json(baseline_plan, "baseline plan")
    gate_rules = PromotionGates()
    if gates is not None:
        gate_payload = _load_json(gates, "gate rules")
        try:
            gate_rules = PromotionGates(
                **{k: v for k, v in gate_payload.items() if k != "gate_version"}
            )
        except TypeError as exc:
            typer.echo(f"Error: malformed gate rules: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    suite = compared.get("suite", {})
    index_block = suite.get("index_interval") or {}
    task_intervals = suite.get("task_intervals", {})
    eligibility = compared.get("eligibility", {})

    task_deltas = {name: float(block.get("point", 0.0)) for name, block in task_intervals.items()}
    seeds = compared.get("seeds", {})
    evidence = PromotionEvidence(
        baseline_run_id=str(compared.get("baseline_run", "")),
        candidate_run_id=str(compared.get("candidate_run", "")),
        track=str(compared.get("track", "")),
        suite_index_delta=index_block.get("point"),
        index_ci_lo=index_block.get("ci_lo"),
        index_ci_hi=index_block.get("ci_hi"),
        task_deltas=task_deltas,
        compute_saving_fraction=compute_saving,
        n_seeds_baseline=len(seeds.get("baseline", [])),
        n_seeds_candidate=len(seeds.get("candidate", [])),
        comparisons_examined=comparisons_examined,
        tuning_trials=tuning_trials,
        scale_hypothesis_exceptions=tuple(
            s.strip() for s in (scale_exceptions or "").split(";") if s.strip()
        ),
    )
    decision = evaluate_promotion(
        evidence,
        gate_rules,
        bool(eligibility.get("eligible", False)),
        eligibility.get("reasons", []),
    )
    output_decision.parent.mkdir(parents=True, exist_ok=True)
    decision_payload = {
        **decision.to_dict(),
        "fingerprint": decision_fingerprint(decision),
        "evidence": evidence.to_dict(),
        "gates": gate_rules.to_dict(),
    }
    output_decision.write_text(
        json.dumps(decision_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    if not decision.promote:
        typer.echo("Promotion refused:")
        for reason in decision.reasons:
            typer.echo(f"  - {reason}")
        raise typer.Exit(code=1)

    throughput_range = None
    if throughput:
        lo, hi = (float(x) for x in throughput.split(","))
        throughput_range = (lo, hi)
    try:
        draft = next_size_draft(
            to_size,
            plan,
            decision,
            evidence.candidate_run_id,
            output_draft,
            throughput_range=throughput_range,
        )
    except PromotionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Promotion passed; draft '{draft['id']}' written to {output_draft}.")
    typer.echo("No job launched, no authorization granted, no final suite scheduled.")
    for warning in decision.warnings:
        typer.echo(f"  warning: {warning}")
