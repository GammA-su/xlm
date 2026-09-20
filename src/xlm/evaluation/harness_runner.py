"""Bounded harness suite execution, coverage accounting and evidence assembly (C11, A26).

Two input routes reach the same scoring code:

*declared* (D04)
    An evaluation-input manifest names explicitly selected, locally verified
    artifacts. The artifacts are verified before anything is built, then bound
    to the **pinned installed task definitions**, so the real prompts,
    preprocessing, choices, metrics and normalization apply. Nothing is fetched.

*undeclared*
    The legacy authored-fixture route through ``--include-path``. Per-task
    metrics are still produced, but with no declared population the full-suite
    index is withheld instead of inferred from whatever came back.

Coverage (D05) is decided by comparing the manifest's expected population -
fixed before execution - against the returned items, matched on declared
identities. A runtime ``--limit``, a failed item, a duplicate, an unexpected id,
a missing BLiMP subdataset or an interruption each keep the run incomplete, and
an incomplete run does not publish a suite index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.evaluation.coverage import (
    COVERAGE_POLICY_VERSION,
    ExpectedPopulation,
    ObservedItem,
    SuiteCoverage,
    expected_population,
    reconcile_coverage,
    undeclared_coverage,
)
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
    bind_verified_inputs_to_tasks,
    build_harness_identity,
    build_task_manager,
    create_harness_model,
)
from xlm.evaluation.inputs import VerifiedInputs
from xlm.evaluation.suites import (
    REQUIRED_TASKS_FOR_INDEX,
    TaskVariant,
    compute_four_task_index,
)


class HarnessRunError(RuntimeError):
    """Raised when a harness run cannot produce usable evidence."""


class MetricResolutionError(HarnessRunError):
    """Raised when a configured metric cannot be resolved in the harness result."""


def resolve_metric(metrics: Mapping[str, Any], name: str) -> float:
    """Resolve one configured metric against the installed harness result shape.

    The harness keys aggregate metrics as ``"<metric>,<filter>"`` - ``"acc,none"``
    for an unfiltered task. Three rules matter here:

    * an exact key wins over a suffixed one;
    * a valid ``0.0`` is a result, not a missing metric, so membership is tested
      rather than truthiness;
    * several filters for one metric are ambiguous and raise, rather than having
      an arbitrary one picked.

    :raises MetricResolutionError: when the metric is absent or ambiguous.
    """
    if name in metrics:
        return float(metrics[name])
    prefix = f"{name},"
    candidates = sorted(key for key in metrics if key.startswith(prefix))
    if not candidates:
        available = sorted(str(key) for key in metrics)
        raise MetricResolutionError(
            f"metric '{name}' is not present in the harness result; available keys: {available}"
        )
    if len(candidates) > 1:
        raise MetricResolutionError(
            f"metric '{name}' is ambiguous across filters {candidates}; the task variant "
            "must name the filter it scores."
        )
    return float(metrics[candidates[0]])


def _optional_metric(metrics: Mapping[str, Any], name: str) -> float | None:
    try:
        return resolve_metric(metrics, name)
    except MetricResolutionError:
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


def _chance_for_task(task: str, items: list[ItemEvidence]) -> float:
    """Chance reference for a declared-input run, from the frozen policy.

    ARC uses the mean inverse choice count over the items actually selected, as
    the policy requires when diagnostics use a subset; the others are fixed.
    """
    from xlm.evaluation.suites import FIXED_CHANCE_REFERENCES  # noqa: PLC0415

    fixed = FIXED_CHANCE_REFERENCES.get(task)
    if fixed is not None:
        return float(fixed)
    if task != "arc_easy":
        raise HarnessRunError(f"no chance reference is defined for task '{task}'")
    values = [1.0 / len(i.choice_log_likelihoods) for i in items if i.choice_log_likelihoods]
    if not values:
        raise HarnessRunError("cannot derive the ARC chance reference without items")
    return sum(values) / len(values)


#: Primary metric per task, frozen by the evaluation policy.
PRIMARY_METRIC = {
    "arc_easy": "acc_norm",
    "hellaswag": "acc_norm",
    "piqa": "acc",
    "blimp": "acc",
}


def _aggregate_identity(
    raw_fingerprint: str,
    verified: VerifiedInputs | None,
    coverage_scope: Mapping[str, Any],
) -> str:
    """Identity of everything the accuracies and the aggregate depend on."""
    from xlm.artifacts.manifest import identity_digest  # noqa: PLC0415

    return identity_digest(
        {
            "raw": raw_fingerprint,
            "coverage_policy": COVERAGE_POLICY_VERSION,
            "primary_metrics": PRIMARY_METRIC,
            "metric_normalization_policy": "character_length",
            "scope": dict(coverage_scope),
            "labels": (
                {v.namespace: v.label_digest for v in verified.selections}
                if verified is not None
                else None
            ),
            "manifest_id": verified.manifest_id if verified is not None else None,
            # Membership is a set, so it is sorted: reordering a manifest must
            # not look like a different population.
            "membership": (
                {v.namespace: sorted(v.selection.item_ids) for v in verified.selections}
                if verified is not None
                else None
            ),
        }
    )


def run_declared_input_suite(
    model: Any,
    tokenizer: Any,
    verified: VerifiedInputs,
    *,
    checkpoint_hash: str,
    precision: str = "fp32",
    device: str = "cpu",
    limit: int | None = None,
    batch_size: int | str = "auto",
    output_dir: Path | None = None,
    boundary_policy: Any = None,
    use_evidence_cache: bool = True,
    notes: list[str] | None = None,
    execution_provenance: dict[str, Any] | None = None,
    required_tasks: Sequence[str] = REQUIRED_TASKS_FOR_INDEX,
) -> EvaluationEvidence:
    """Evaluate explicitly selected, verified local inputs (D04 + D05).

    The expected population is frozen from the manifest before the harness is
    asked for anything, so nothing the run returns can redefine what it was
    supposed to cover.
    """
    from lm_eval import simple_evaluate  # noqa: PLC0415

    from xlm.artifacts.manifest import identity_digest  # noqa: PLC0415

    manifest = verified.manifest
    expected: ExpectedPopulation = expected_population(
        verified, runtime_limit=limit, required_tasks=required_tasks
    )

    source_manager = build_task_manager(None)
    specs, bound_map, definition_identities = bind_verified_inputs_to_tasks(
        verified, source_manager
    )

    identity = build_harness_identity(
        checkpoint_hash=checkpoint_hash,
        tokenizer_hash=tokenizer.fingerprint,
        variants=[],
        precision=precision,
        limit=limit,
        extra={
            "boundary_policy": str(boundary_policy or "joint_prefix_match_v1"),
            "context_length": str(getattr(getattr(model, "config", None), "context_length", None)),
            "input_route": "declared_manifest",
            # The manifest id deliberately does NOT appear here. It covers
            # labels, scope and file digests, none of which change the text the
            # model was shown; it belongs to the aggregate identity instead.
            "task_definitions": identity_digest(definition_identities),
            # Prompt-bearing content only. The gold labels live in the aggregate
            # identity instead, so a relabelling does not claim the model saw
            # different text.
            "prompt_content": identity_digest(
                {v.namespace: v.prompt_digest for v in verified.selections}
            ),
            "execution_provenance": identity_digest(execution_provenance),
        },
    )
    raw_fingerprint = identity.fingerprint()
    scope = {
        "manifest_id": verified.manifest_id,
        "scope_label": manifest.scope_label,
        "scope_kind": manifest.scope_kind,
        "exposure_class": manifest.exposure_class,
        "tier": str(manifest.tier),
        "required_tasks": sorted(expected.required_tasks),
        "runtime_limit": limit,
    }
    aggregate_fingerprint = _aggregate_identity(raw_fingerprint, verified, scope)

    identity_dict = identity.to_dict()
    identity_dict["evaluation_inputs"] = verified.summary()
    identity_dict["task_definitions"] = definition_identities
    if execution_provenance is not None:
        identity_dict["execution_provenance"] = execution_provenance

    cache = EvidenceCache((output_dir or Path("data/eval")) / "evidence")
    cache_notes: list[str] = []
    if use_evidence_cache:
        cached, reuse = cache.load_reusable(
            raw_fingerprint,
            identity_dict,
            raw_fingerprint=raw_fingerprint,
            aggregate_fingerprint=aggregate_fingerprint,
        )
        if reuse == "aggregate" and cached is not None:
            return cached
        if reuse == "raw_only":
            cache_notes.append(
                "cached aggregate invalidated: labels, membership, scope or coverage policy "
                "changed although the rendered prompts did not. Raw likelihood reuse is not "
                "implemented, so the suite was recomputed rather than reported from a "
                "result whose accuracies no longer apply."
            )

    adapter = create_harness_model(
        model=model,
        tokenizer=tokenizer,
        device=device,
        precision=precision,
        boundary_policy=boundary_policy,
    )

    try:
        results = simple_evaluate(
            model=adapter,
            tasks=specs,
            limit=limit,
            batch_size=batch_size,
            task_manager=source_manager,
            log_samples=True,
            # Suite runs never consult the harness response cache: a stale
            # third-party cache must not bypass XLM's membership checks.
            use_cache=None if not SUITE_USE_CACHE else str((output_dir or Path()) / "hf_cache"),
            bootstrap_iters=0,
        )
    except KeyboardInterrupt as exc:  # pragma: no cover - operator action
        raise HarnessRunError(
            "evaluation interrupted; no coverage claim is made for a partial run"
        ) from exc

    if results is None:
        raise HarnessRunError("harness returned no results")
    raw_results: Mapping[str, Any] = results.get("results", {})
    samples_by_task: Mapping[str, Any] = results.get("samples", {})
    if not raw_results:
        raise HarnessRunError("harness results contain no task entries")

    observed: dict[str, list[ObservedItem]] = {}
    items_by_task: dict[str, list[ItemEvidence]] = {}
    blimp_subscores: dict[str, float] = {}
    harness_metrics: dict[str, dict[str, float]] = {}

    for bound_name, metrics in raw_results.items():
        item = bound_map.get(bound_name)
        if item is None:
            raise HarnessRunError(f"harness returned unrequested task '{bound_name}'")
        selection = item.selection
        samples = list(samples_by_task.get(bound_name, []))
        rows = items_from_harness_samples(
            selection.task,
            samples,
            item_id_field=selection.item_id_field,
            namespace=selection.namespace,
            subdataset=selection.subdataset,
        )
        items_by_task.setdefault(selection.task, []).extend(rows)
        observed.setdefault(selection.task, []).extend(
            ObservedItem(
                namespace=selection.namespace,
                item_id=row.declared_item_id or row.item_id,
                subdataset=selection.subdataset,
                failed=row.omitted_reason is not None,
            )
            for row in rows
        )
        metric_name = PRIMARY_METRIC[selection.task]
        value = resolve_metric(metrics, metric_name)
        if selection.subdataset:
            blimp_subscores[selection.subdataset] = value
        else:
            harness_metrics[selection.task] = {
                key: found
                for key in ("acc", "acc_norm")
                if (found := _optional_metric(metrics, key)) is not None
            }

    # Declared selections that returned nothing at all never reach the loop
    # above, so their absence is caught here by reconciliation, not inferred.
    coverage = reconcile_coverage(expected, observed, notes=cache_notes)

    tasks: dict[str, TaskEvidence] = {}
    for task, rows in sorted(items_by_task.items()):
        chance = _chance_for_task(task, rows)
        task_coverage = coverage.tasks.get(task)
        expected_items = task_coverage.expected_items if task_coverage else None
        if task == "blimp":
            # Macro-average whole subdatasets before BLiMP takes one quarter of
            # the suite weight. Subdataset scores come from the harness metric
            # for each bound leaf, not from pooled items.
            macro = sum(blimp_subscores.values()) / len(blimp_subscores) if blimp_subscores else 0.0
            tasks[task] = TaskEvidence(
                task="blimp",
                variant_id=f"xlm_blimp_{manifest.tier}",
                metric_name="acc",
                acc=macro,
                acc_norm=macro,
                chance=chance,
                scored_items=len(rows),
                total_items=len(rows),
                expected_items=expected_items,
                subdataset_scores=dict(sorted(blimp_subscores.items())),
                items=rows,
            )
            continue
        evidence_row = task_evidence_from_items(
            task=task,
            variant_id=f"xlm_{task}_{manifest.tier}",
            metric_name=PRIMARY_METRIC[task],
            chance=chance,
            items=rows,
            total_items=len(rows),
            expected_items=expected_items,
        )
        for key, value in harness_metrics.get(task, {}).items():
            setattr(evidence_row, key, value)
        tasks[task] = evidence_row

    index = compute_four_task_index(
        {name: ev.score() for name, ev in tasks.items()},
        required=expected.required_tasks or tuple(required_tasks),
        coverage=coverage,
    )

    run_notes = list(notes or []) + list(cache_notes)
    run_notes.append(f"scope: {coverage.scope_statement()}")
    if manifest.is_authored_fixture:
        run_notes.append(
            "authored synthetic fixture evidence: it demonstrates the evaluation path and "
            "is never a benchmark result or research/promotion evidence."
        )
    if set(expected.required_tasks) != set(REQUIRED_TASKS_FOR_INDEX):
        run_notes.append(
            f"declared scope covers {sorted(expected.required_tasks)}, not the full four-task "
            "suite; this is not the XLM suite index."
        )
    if limit is not None:
        run_notes.append(
            f"runtime limit={limit} applied: a bounded smoke run, not the official benchmark."
        )
    run_notes.extend(f"index withheld: {reason}" for reason in index.withheld_reasons)

    evidence = EvaluationEvidence(
        evidence_version=EVIDENCE_VERSION,
        identity_fingerprint=raw_fingerprint,
        raw_identity_fingerprint=raw_fingerprint,
        aggregate_identity_fingerprint=aggregate_fingerprint,
        identity=identity_dict,
        tasks=tasks,
        index=index,
        limit=limit,
        coverage=coverage,
        notes=run_notes,
    )
    if output_dir is not None:
        cache.store(evidence)
        evidence.save(output_dir / f"evidence_{raw_fingerprint[:16]}.json")
    return evidence


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
    verified_inputs: VerifiedInputs | None = None,
) -> EvaluationEvidence:
    """Execute a suite through the harness and assemble identity-checked evidence.

    With ``verified_inputs`` this delegates to :func:`run_declared_input_suite`.
    Without them it runs the authored-fixture route, which still produces
    per-task metrics but withholds the suite index, because the expected
    population was never declared and must not be back-filled from the results.
    """
    from lm_eval import simple_evaluate  # noqa: PLC0415

    from xlm.artifacts.manifest import identity_digest  # noqa: PLC0415
    from xlm.prepare.integrity import path_digest  # noqa: PLC0415

    if verified_inputs is not None:
        return run_declared_input_suite(
            model=model,
            tokenizer=tokenizer,
            verified=verified_inputs,
            checkpoint_hash=checkpoint_hash,
            precision=precision,
            device=device,
            limit=limit,
            batch_size=batch_size,
            output_dir=output_dir,
            boundary_policy=boundary_policy,
            use_evidence_cache=use_evidence_cache,
            notes=notes,
            execution_provenance=execution_provenance,
        )

    if any(v.tier.value == "final" or v.dataset_revision is not None for v in variants):
        raise HarnessRunError(
            "official/final evaluation is BLOCKED on this route: a pinned remote revision "
            "cannot by itself enforce split membership, transfer caps or operator "
            "isolation. Supply explicitly selected, verified local inputs with --inputs, "
            "or use the operator-side final service."
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
            "input_route": "undeclared_fixture",
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
    observed: dict[str, list[ObservedItem]] = {}
    blimp_items: list[ItemEvidence] = []
    blimp_subscores: dict[str, float] = {}
    blimp_chance: float = 0.5

    for task_name, metrics in raw_results.items():
        variant = task_to_variant.get(task_name)
        if variant is None:
            raise HarnessRunError(f"harness returned unrequested task '{task_name}'")

        samples = list(samples_by_task.get(task_name, []))
        items = items_from_harness_samples(task_name, samples, namespace=task_name)
        metric_name = variant.normalized_metric
        chance = _chance_for(task_name, variant, items)
        observed.setdefault(variant.lm_eval_task, []).extend(
            ObservedItem(namespace=task_name, item_id=row.item_id) for row in items
        )

        if variant.lm_eval_task == "blimp":
            blimp_items.extend(items)
            blimp_chance = chance
            # Resolved against the installed result shape: the harness keys
            # aggregates as "acc,none", and a valid 0.0 is a score, not a miss.
            blimp_subscores[task_name] = resolve_metric(metrics, metric_name)
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
            value = _optional_metric(metrics, key)
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

    coverage: SuiteCoverage = undeclared_coverage(observed=observed, runtime_limit=limit)
    index = compute_four_task_index(
        {name: ev.score() for name, ev in tasks.items()}, coverage=coverage
    )
    run_notes = list(notes or [])
    run_notes.append(f"scope: {coverage.scope_statement()}")
    if limit is not None:
        run_notes.append(
            f"bounded store run at limit={limit}: a smoke evaluation, not the official benchmark."
        )
    run_notes.extend(f"index withheld: {reason}" for reason in index.withheld_reasons)

    evidence = EvaluationEvidence(
        evidence_version=EVIDENCE_VERSION,
        identity_fingerprint=identity.fingerprint(),
        raw_identity_fingerprint=identity.fingerprint(),
        aggregate_identity_fingerprint=_aggregate_identity(
            identity.fingerprint(), None, {"scope_kind": "undeclared", "runtime_limit": limit}
        ),
        identity=identity_dict,
        tasks=tasks,
        index=index,
        limit=limit,
        coverage=coverage,
        notes=run_notes,
    )
    if output_dir is not None:
        cache.store(evidence)
        evidence.save(output_dir / f"evidence_{identity.fingerprint()[:16]}.json")
    return evidence
