"""Paired cluster-aware bootstrap with deterministic analysis seeds (A31).

Resampling unit is the cluster (BLiMP subdataset, or a frozen source group for
other tasks), never the bare item: items inside one cluster are dependent by
construction. Seeds are explicit; the same seed always yields the same
intervals. Between-training-seed spread is reported separately from
within-run item uncertainty, and a single seed honestly reports seed
variability as unmeasured. Mismatched IDs, partial coverage or missing
predictions invalidate the analysis instead of producing a number.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

BOOTSTRAP_VERSION = "1"


class BootstrapError(ValueError):
    """Raised when a paired analysis cannot be validly constructed."""


def pair_seed_evidence(
    baseline: Sequence[dict[str, Any]], candidate: Sequence[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair by recorded init/data seeds, never by argument order or run labels."""

    def indexed(evidence: Sequence[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
        result: dict[tuple[int, int], dict[str, Any]] = {}
        for item in evidence:
            seeds = item.get("training_seeds", {})
            if not isinstance(seeds, dict) or any(
                type(seeds.get(key)) is not int for key in ("init_seed", "data_seed")
            ):
                raise BootstrapError(
                    "multi-seed evidence requires recorded training_seeds (init_seed/data_seed)"
                )
            key = (seeds["init_seed"], seeds["data_seed"])
            if key in result:
                raise BootstrapError(f"duplicate training seed pair {key}")
            result[key] = item
        return result

    left, right = indexed(baseline), indexed(candidate)
    if left.keys() != right.keys():
        raise BootstrapError("baseline and candidate training seed pairs differ")
    return [(left[key], right[key]) for key in sorted(left)]


@dataclass(frozen=True)
class AlignedItem:
    """One item's paired outcomes plus its cluster membership."""

    item_id: str
    task: str
    cluster_id: str
    score_a: float
    score_b: float

    @property
    def paired_difference(self) -> float:
        return self.score_b - self.score_a


@dataclass(frozen=True)
class BootstrapInterval:
    """Point difference with a percentile interval from cluster resampling."""

    point: float
    ci_lo: float
    ci_hi: float
    ci_level: float
    n_bootstrap: int
    n_clusters: int
    n_items: int
    analysis_seed: int

    def excludes_zero(self) -> bool:
        return self.ci_lo > 0 or self.ci_hi < 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "excludes_zero": self.excludes_zero()}


@dataclass(frozen=True)
class SeedSpread:
    """Between-training-seed variability, kept apart from item uncertainty."""

    per_seed_differences: dict[str, float]
    mean: float
    minimum: float
    maximum: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def align_paired_items(
    arm_a: Mapping[str, float],
    arm_b: Mapping[str, float],
    tasks: Mapping[str, str],
    clusters: Mapping[str, str],
) -> list[AlignedItem]:
    """Align two arms on identical item IDs with complete task/cluster coverage."""
    ids_a = set(arm_a)
    ids_b = set(arm_b)
    if ids_a != ids_b:
        missing = sorted(ids_a ^ ids_b)
        raise BootstrapError(
            f"paired analysis requires identical item IDs; "
            f"{len(missing)} mismatched (e.g. {missing[:5]})"
        )
    if not ids_a:
        raise BootstrapError("paired analysis requires at least one item")
    items: list[AlignedItem] = []
    for item_id in sorted(ids_a):
        score_a, score_b = arm_a[item_id], arm_b[item_id]
        if score_a is None or score_b is None:
            raise BootstrapError(f"missing prediction for item '{item_id}'")
        if item_id not in tasks:
            raise BootstrapError(f"item '{item_id}' has no task assignment (partial coverage)")
        if item_id not in clusters:
            raise BootstrapError(f"item '{item_id}' has no cluster assignment")
        items.append(
            AlignedItem(
                item_id=item_id,
                task=str(tasks[item_id]),
                cluster_id=str(clusters[item_id]),
                score_a=float(score_a),
                score_b=float(score_b),
            )
        )
    return items


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        raise BootstrapError("cannot take a percentile of an empty sample")
    if not 0.0 <= fraction <= 1.0:
        raise BootstrapError(f"percentile fraction {fraction} out of [0, 1]")
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def cluster_bootstrap_difference(
    items: Sequence[AlignedItem],
    n_bootstrap: int = 1000,
    ci_level: float = 0.95,
    analysis_seed: int = 20260918,
) -> BootstrapInterval:
    """Paired mean difference with a cluster-resampled percentile interval."""
    if n_bootstrap < 100:
        raise BootstrapError(f"n_bootstrap={n_bootstrap} is below the 100 minimum")
    if not 0.0 < ci_level < 1.0:
        raise BootstrapError(f"ci_level={ci_level} must be in (0, 1)")
    items = list(items)
    if not items:
        raise BootstrapError("no items to resample")

    by_cluster: dict[str, list[AlignedItem]] = {}
    for item in items:
        by_cluster.setdefault(item.cluster_id, []).append(item)
    cluster_ids = sorted(by_cluster)

    point = sum(i.paired_difference for i in items) / len(items)
    rng = random.Random(analysis_seed)
    tail = (1.0 - ci_level) / 2.0
    replicates: list[float] = []
    for _ in range(n_bootstrap):
        drawn = [by_cluster[rng.choice(cluster_ids)] for _ in cluster_ids]
        flat = [item for cluster in drawn for item in cluster]
        replicates.append(sum(i.paired_difference for i in flat) / len(flat))
    replicates.sort()
    return BootstrapInterval(
        point=point,
        ci_lo=_percentile(replicates, tail),
        ci_hi=_percentile(replicates, 1.0 - tail),
        ci_level=ci_level,
        n_bootstrap=n_bootstrap,
        n_clusters=len(cluster_ids),
        n_items=len(items),
        analysis_seed=analysis_seed,
    )


def seed_spread(per_seed_differences: Mapping[str, float]) -> SeedSpread | None:
    """Between-seed spread, or None when a single seed leaves it unmeasured."""
    values = {seed: float(diff) for seed, diff in per_seed_differences.items()}
    if len(values) < 2:
        return None
    ordered = sorted(values.values())
    return SeedSpread(
        per_seed_differences=dict(sorted(values.items())),
        mean=sum(ordered) / len(ordered),
        minimum=ordered[0],
        maximum=ordered[-1],
    )


@dataclass
class SuiteBootstrapResult:
    """Paired bootstrap for a whole suite: per-task intervals plus the index."""

    task_intervals: dict[str, BootstrapInterval] = field(default_factory=dict)
    index_interval: BootstrapInterval | None = None
    seed_spread: SeedSpread | None = None
    seed_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_intervals": {k: v.to_dict() for k, v in sorted(self.task_intervals.items())},
            "index_interval": self.index_interval.to_dict() if self.index_interval else None,
            "seed_spread": self.seed_spread.to_dict() if self.seed_spread else None,
            "seed_note": self.seed_note,
        }


def suite_bootstrap(
    items: Sequence[AlignedItem],
    chances: Mapping[str, float],
    required_tasks: Sequence[str] = ("blimp", "arc_easy", "hellaswag", "piqa"),
    n_bootstrap: int = 1000,
    ci_level: float = 0.95,
    analysis_seed: int = 20260918,
    per_seed_differences: Mapping[str, float] | None = None,
) -> SuiteBootstrapResult:
    """Bootstrap every required task and the four-task suite index together.

    Each bootstrap replicate recomputes task accuracies (BLiMP macro-averaged
    over whole subdatasets first) and then the suite index, so the index
    interval honestly reflects item uncertainty. Missing tasks invalidate the
    index, exactly like the point calculation.
    """
    from xlm.evaluation.suites import TaskScore, compute_four_task_index

    items = list(items)
    present = {item.task for item in items}
    missing = sorted(set(required_tasks) - present)
    if missing:
        raise BootstrapError(
            f"paired suite analysis requires complete coverage; missing tasks: {missing}"
        )

    by_cluster: dict[str, list[AlignedItem]] = {}
    for item in items:
        by_cluster.setdefault(item.cluster_id, []).append(item)
    cluster_ids = sorted(by_cluster)
    counts = {task: sum(1 for i in items if i.task == task) for task in required_tasks}
    # Stratify resampling within tasks: a draw must never drop a whole task,
    # which would silently turn a paired suite analysis into a partial one.
    clusters_by_task: dict[str, list[str]] = {}
    for item in items:
        bucket = clusters_by_task.setdefault(item.task, [])
        if item.cluster_id not in bucket:
            bucket.append(item.cluster_id)
    task_cluster_ids = {task: sorted(bucket) for task, bucket in clusters_by_task.items()}

    def arm_accuracies(drawn: list[AlignedItem], arm: str) -> dict[str, float]:
        """Per-task accuracy for one arm; BLiMP macro-averaged over subclusters first."""
        per_task: dict[str, list[float]] = {}
        for item in drawn:
            score = item.score_b if arm == "candidate" else item.score_a
            per_task.setdefault(item.task, []).append(score)
        accuracies: dict[str, float] = {}
        for task, scores in per_task.items():
            if task == "blimp":
                subclusters: dict[str, list[float]] = {}
                for item in drawn:
                    if item.task == "blimp":
                        score = item.score_b if arm == "candidate" else item.score_a
                        subclusters.setdefault(item.cluster_id, []).append(score)
                accuracies[task] = sum(sum(v) / len(v) for v in subclusters.values()) / len(
                    subclusters
                )
            else:
                accuracies[task] = sum(scores) / len(scores)
        return accuracies

    def index_difference(drawn: list[AlignedItem]) -> float:
        """Suite-index(candidate) minus suite-index(baseline) on one sample."""
        indexes: dict[str, float] = {}
        for arm in ("baseline", "candidate"):
            accs = arm_accuracies(drawn, arm)
            scores: dict[str, TaskScore] = {}
            for task in required_tasks:
                scores[task] = TaskScore(
                    task=task,
                    metric_name="paired_accuracy",
                    value=accs[task],
                    chance=chances[task],
                    scored_items=counts.get(task, 0),
                    total_items=counts.get(task, 0),
                )
            result = compute_four_task_index(scores, required=required_tasks)
            if not result.complete or result.index is None:
                raise BootstrapError("suite index incomplete inside bootstrap replicate")
            indexes[arm] = result.index
        return indexes["candidate"] - indexes["baseline"]

    def task_differences(drawn: list[AlignedItem]) -> dict[str, float]:
        per_task: dict[str, list[float]] = {}
        for item in drawn:
            per_task.setdefault(item.task, []).append(item.paired_difference)
        values: dict[str, float] = {}
        for task, diffs in per_task.items():
            if task == "blimp":
                subclusters: dict[str, list[float]] = {}
                for item in drawn:
                    if item.task == "blimp":
                        subclusters.setdefault(item.cluster_id, []).append(item.paired_difference)
                values[task] = sum(sum(v) / len(v) for v in subclusters.values()) / len(subclusters)
            else:
                values[task] = sum(diffs) / len(diffs)
        return values

    point_values = task_differences(items)
    point_index = index_difference(items)

    rng = random.Random(analysis_seed)
    tail = (1.0 - ci_level) / 2.0
    task_replicates: dict[str, list[float]] = {task: [] for task in required_tasks}
    index_replicates: list[float] = []
    for _ in range(n_bootstrap):
        drawn = [
            item
            for task in required_tasks
            for _ in task_cluster_ids[task]
            for item in by_cluster[rng.choice(task_cluster_ids[task])]
        ]
        values = task_differences(drawn)
        for task in required_tasks:
            task_replicates[task].append(values[task])
        index_replicates.append(index_difference(drawn))

    task_intervals: dict[str, BootstrapInterval] = {}
    for task in required_tasks:
        ordered = sorted(task_replicates[task])
        task_intervals[task] = BootstrapInterval(
            point=point_values[task],
            ci_lo=_percentile(ordered, tail),
            ci_hi=_percentile(ordered, 1.0 - tail),
            ci_level=ci_level,
            n_bootstrap=n_bootstrap,
            n_clusters=len(cluster_ids),
            n_items=len([i for i in items if i.task == task]),
            analysis_seed=analysis_seed,
        )
    ordered_index = sorted(index_replicates)
    index_interval = BootstrapInterval(
        point=point_index,
        ci_lo=_percentile(ordered_index, tail),
        ci_hi=_percentile(ordered_index, 1.0 - tail),
        ci_level=ci_level,
        n_bootstrap=n_bootstrap,
        n_clusters=len(cluster_ids),
        n_items=len(items),
        analysis_seed=analysis_seed,
    )

    spread = seed_spread(dict(per_seed_differences or {}))
    note = (
        "between-training-seed spread over "
        f"{len(spread.per_seed_differences)} seeds; item intervals above are "
        "within-run uncertainty"
        if spread is not None
        else "single training seed per arm: between-seed variability is unmeasured"
    )
    return SuiteBootstrapResult(
        task_intervals=task_intervals,
        index_interval=index_interval,
        seed_spread=spread,
        seed_note=note,
    )
