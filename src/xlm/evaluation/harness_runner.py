"""Bounded harness suite execution and evidence assembly (C11, A26).

Runs resolved task variants through the pinned harness with the XLM backend,
converts harness samples into per-item :class:`ItemEvidence`, computes task
scores and the four-task index, and stores identity-checked evidence. A suite
run that hits a limit or omits items records that fact; it never reports the
surviving subset as the task score.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.evaluation.evidence import (
    EVIDENCE_VERSION,
    EvaluationEvidence,
    EvidenceCache,
    ItemEvidence,
    TaskEvidence,
    items_from_harness_samples,
    task_evidence_from_items,
)
from xlm.evaluation.harness import (
    SUITE_USE_CACHE,
    build_harness_identity,
    build_task_manager,
    create_harness_model,
)
from xlm.evaluation.suites import (
    TaskVariant,
    compute_four_task_index,
)


class HarnessRunError(RuntimeError):
    """Raised when a harness run cannot produce usable evidence."""


def _metric_value(metrics: Mapping[str, Any], name: str) -> float | None:
    """Read a harness metric, accepting the ``name,filter`` key form."""
    if metrics.get(name) is not None:
        return float(metrics[name])
    for key, value in metrics.items():
        if key == name or key.startswith(f"{name},"):
            return float(value)
    return None


def _chance_for(task: str, variant: TaskVariant, items: list[ItemEvidence]) -> float:
    if variant.chance_reference == "mean_inverse_n_choices":
        if not items:
            raise HarnessRunError(f"cannot derive chance for '{task}' without items")
        values = [1.0 / len(i.choice_log_likelihoods) for i in items if i.choice_log_likelihoods]
        if not values:
            raise HarnessRunError(f"no choice structure for '{task}'")
        return sum(values) / len(values)
    if variant.chance_value is None:
        raise HarnessRunError(f"variant '{variant.variant_id}' has no chance reference")
    return float(variant.chance_value)


def run_harness_suite(
    model: Any,
    tokenizer: Any,
    variants: list[TaskVariant],
    *,
    checkpoint_hash: str,
    precision: str = "fp32",
    device: str = "cpu",
    limit: int | None = None,
    batch_size: int | str = "auto",
    include_path: Path | None = None,
    output_dir: Path | None = None,
    boundary_policy: Any = None,
    use_evidence_cache: bool = True,
    notes: list[str] | None = None,
    execution_provenance: dict[str, Any] | None = None,
) -> EvaluationEvidence:
    """Execute a suite through the harness and assemble identity-checked evidence."""
    from lm_eval import simple_evaluate  # noqa: PLC0415

    from xlm.artifacts.manifest import identity_digest
    from xlm.prepare.integrity import path_digest

    if any(v.tier.value == "final" or v.dataset_revision is not None for v in variants):
        raise HarnessRunError(
            "official/final evaluation is BLOCKED pending split-scoped bounded acquisition "
            "and isolated operator execution; local authored fixtures remain supported"
        )
    identity = build_harness_identity(
        checkpoint_hash=checkpoint_hash,
        tokenizer_hash=tokenizer.fingerprint,
        variants=variants,
        precision=precision,
        limit=limit,
        extra={
            "boundary_policy": str(boundary_policy or "joint_prefix_match_v1"),
            "context_length": str(getattr(getattr(model, "config", None), "context_length", None)),
            "local_task_content": path_digest(include_path) if include_path else "none",
            "execution_provenance": identity_digest(execution_provenance),
        },
    )
    identity_dict = identity.to_dict()
    if execution_provenance is not None:
        identity_dict["execution_provenance"] = execution_provenance

    cache = EvidenceCache((output_dir or Path("data/eval")) / "evidence")
    if use_evidence_cache:
        cached = cache.load_if_match(identity.fingerprint(), identity_dict)
        if cached is not None:
            return cached

    adapter = create_harness_model(
        model=model,
        tokenizer=tokenizer,
        device=device,
        precision=precision,
        boundary_policy=boundary_policy,
    )
    from xlm.evaluation.harness import materialize_pinned_tasks  # noqa: PLC0415

    base_dir = output_dir or Path("data/eval")
    source_manager = build_task_manager(include_path)
    task_names, task_to_variant = materialize_pinned_tasks(
        variants, base_dir / "tasks" / identity.fingerprint()[:12], source_manager
    )
    # The materialized wrappers are self-contained (official config copied
    # verbatim, local data paths absolutized), so a manager over that directory
    # is sufficient and cannot accidentally resolve a different upstream task.
    manager = build_task_manager(
        base_dir / "tasks" / identity.fingerprint()[:12], include_defaults=False
    )
    results = simple_evaluate(
        model=adapter,
        tasks=task_names,
        limit=limit,
        batch_size=batch_size,
        task_manager=manager,
        log_samples=True,
        # Suite runs never consult the harness response cache (SUITE_USE_CACHE=False):
        # stale third-party caches must not override XLM identity checks.
        use_cache=None if not SUITE_USE_CACHE else str(base_dir / "hf_cache"),
        bootstrap_iters=0,
    )
    if results is None:
        raise HarnessRunError("harness returned no results")

    raw_results: Mapping[str, Any] = results.get("results", {})
    samples_by_task: Mapping[str, Any] = results.get("samples", {})
    if not raw_results:
        raise HarnessRunError("harness results contain no task entries")

    tasks: dict[str, TaskEvidence] = {}
    blimp_items: list[ItemEvidence] = []
    blimp_subscores: dict[str, float] = {}
    blimp_chance: float = 0.5

    for task_name, metrics in raw_results.items():
        variant = task_to_variant.get(task_name)
        if variant is None:
            raise HarnessRunError(f"harness returned unrequested task '{task_name}'")

        samples = list(samples_by_task.get(task_name, []))
        items = items_from_harness_samples(task_name, samples)
        metric_name = variant.normalized_metric
        chance = _chance_for(task_name, variant, items)

        if variant.lm_eval_task == "blimp":
            blimp_items.extend(items)
            blimp_chance = chance
            value = metrics.get(metric_name)
            if value is None:
                raise HarnessRunError(f"task '{task_name}' lacks metric '{metric_name}'")
            blimp_subscores[task_name] = float(value)
            continue

        # Key by the logical task (arc_easy, hellaswag, ...) so the four-task
        # index can match components; the tier variant lives in variant_id.
        logical_task = variant.lm_eval_task
        task_evidence = task_evidence_from_items(
            task=logical_task,
            variant_id=variant.variant_id,
            metric_name=metric_name,
            chance=chance,
            items=items,
            total_items=len(items),
        )
        # Harness aggregates are authoritative; keep them alongside item-derived
        # ones. Metric keys carry the filter suffix (e.g. 'acc_norm,none').
        for key in ("acc", "acc_norm"):
            value = _metric_value(metrics, key)
            if value is None:
                continue
            if key == "acc":
                task_evidence.acc = value
            else:
                task_evidence.acc_norm = value
        tasks[logical_task] = task_evidence

    if blimp_subscores:
        blimp_task = TaskEvidence(
            task="blimp",
            variant_id="xlm_blimp",
            metric_name="acc",
            acc=sum(blimp_subscores.values()) / len(blimp_subscores),
            acc_norm=sum(blimp_subscores.values()) / len(blimp_subscores),
            chance=blimp_chance,
            scored_items=len(blimp_items),
            total_items=len(blimp_items),
            subdataset_scores=blimp_subscores,
            items=blimp_items,
        )
        tasks["blimp"] = blimp_task

    index = compute_four_task_index({name: ev.score() for name, ev in tasks.items()})
    run_notes = list(notes or [])
    if limit is not None:
        run_notes.append(
            f"bounded store run at limit={limit}: a smoke evaluation, not the official benchmark."
        )
        if math.isnan(index.index or 0.0):  # pragma: no cover - guard only
            run_notes.append("index not computable from a partial store run.")
    if not index.complete:
        run_notes.append(
            f"suite index withheld: missing {index.missing}; partial scores are not the index."
        )

    evidence = EvaluationEvidence(
        evidence_version=EVIDENCE_VERSION,
        identity_fingerprint=identity.fingerprint(),
        identity=identity_dict,
        tasks=tasks,
        index=index,
        limit=limit,
        notes=run_notes,
    )
    if output_dir is not None:
        cache.store(evidence)
        evidence.save(output_dir / f"evidence_{identity.fingerprint()[:16]}.json")
    return evidence
