"""CLI command for model evaluation complying with C11, C12, and P15 tiers.

Two paths share one command:

* ``--suite synthetic_mc|synthetic_pair|all`` runs the native offline diagnostic
  fixtures (P06 behavior, unchanged).
* ``--suite search|confirmation|final`` runs the pinned lm-evaluation-harness
  adapter on explicit task variants. Search/confirmation can never resolve a
  final split; final refuses without an operator authorization matching a frozen
  request (executed on the operator side in P21).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Annotated, Any

import typer

NATIVE_SUITES = ("synthetic_mc", "synthetic_pair", "all")
TIER_SUITES = ("search", "confirmation", "final")
ALL_SUITES = NATIVE_SUITES + TIER_SUITES


def _run_native_fixtures(
    checkpoint: Path,
    tokenizer_path: str | None,
    suite: str,
    device: str,
    precision: str,
    boundary_policy: str,
    output_json: bool,
    execution: dict[str, Any] | None = None,
) -> None:
    from xlm.evaluation.fixtures import (
        get_synthetic_minimal_pair_fixture,
        get_synthetic_multiple_choice_fixture,
    )
    from xlm.evaluation.likelihood import (
        BoundaryPolicy,
        ConditionalLikelihoodScorer,
        WindowTruncationPolicy,
    )
    from xlm.evaluation.scorer import BenchmarkFixtureScorer
    from xlm.models.serialization import load_model_for_inference
    from xlm.tokenizers.loading import checkpoint_weights_hash

    model = load_model_for_inference(checkpoint, device=device)

    from xlm.tokenizers.loading import load_inference_tokenizer

    tok = load_inference_tokenizer(checkpoint, tokenizer_path)

    b_policy = (
        BoundaryPolicy.SEPARATE_V1
        if boundary_policy == "separate"
        else BoundaryPolicy.JOINT_PREFIX_MATCH_V1
    )

    scorer = ConditionalLikelihoodScorer(
        model=model,
        tokenizer=tok,
        device=device,
        precision=precision,
        boundary_policy=b_policy,
        window_policy=WindowTruncationPolicy.ROLLING,
    )
    fixture_scorer = BenchmarkFixtureScorer(
        scorer=scorer,
        model_hash=checkpoint_weights_hash(checkpoint),
        tokenizer_hash=tok.fingerprint,
        execution_fingerprint=execution["envelope"]["execution_hash"] if execution else None,
    )

    receipts = []
    if suite in ("synthetic_mc", "all"):
        mc_data = get_synthetic_multiple_choice_fixture()
        mc_receipt, _ = fixture_scorer.evaluate_dataset(mc_data)
        receipts.append(mc_receipt)
    if suite in ("synthetic_pair", "all"):
        pair_data = get_synthetic_minimal_pair_fixture()
        pair_receipt, _ = fixture_scorer.evaluate_dataset(pair_data)
        receipts.append(pair_receipt)

    payloads = [r.to_dict() for r in receipts]
    if execution is not None:
        from xlm.artifacts.manifest import identity_digest
        from xlm.experiments.evaluation_execution import receipt_provenance
        from xlm.experiments.execution import write_json

        for payload in payloads:
            payload["execution_provenance"] = receipt_provenance(execution)
            payload["execution_provenance_hash"] = identity_digest(payload["execution_provenance"])
            payload["execution_observations"] = execution["observations"]
        write_json(
            Path(execution["observations"]["work_dir"]) / "diagnostic_receipts.json",
            {"receipts": payloads},
        )
    if output_json:
        typer.echo(json.dumps(payloads, indent=2))
        return

    typer.echo("=" * 60)
    typer.echo(f"Evaluation Results for Checkpoint: {checkpoint.name}")
    typer.echo(f"Device: {device} | Precision: {precision} | Boundary Policy: {b_policy.value}")
    typer.echo("=" * 60)
    for r in receipts:
        typer.echo(f"\nTask: {r.task_source_and_scorer_version}")
        typer.echo(f"Items Scored: {r.scored_items_count}")
        typer.echo("Metrics:")
        for k, v in r.metrics.items():
            typer.echo(f"  - {k:<18}: {v:.4f}")
    typer.echo("\n" + "=" * 60)


def _blimp_subdataset_names(include_path: Path | None, explicit: str | None) -> list[str]:
    if explicit:
        return [name.strip() for name in explicit.split(",") if name.strip()]
    try:
        from xlm.evaluation.harness import build_task_manager

        manager = build_task_manager(include_path)
        matched = manager.match_tasks(["blimp"])
        names = [m for m in matched if m.startswith("blimp_")]
        if names:
            return sorted(names)
    except Exception:
        pass
    return []


def _verify_inputs_only(
    inputs: Path, suite: str, output_json: bool, acquisition_store_root: Path | None = None
) -> None:
    """Verify a declared evaluation-input manifest and run nothing else.

    Deliberately self-contained: no checkpoint, no tokenizer, no model
    allocation, no inference and no provider access. Only the harness version
    is read, because the manifest declares which harness it was built against.
    """
    from xlm.evaluation.harness import harness_version
    from xlm.evaluation.inputs import (
        EvaluationInputError,
        load_evaluation_inputs,
        verify_evaluation_inputs,
    )
    from xlm.evaluation.suites import FinalAuthorizationRequiredError, SuiteTier

    if suite not in TIER_SUITES:
        typer.echo(
            f"Error: --verify-inputs-only applies to the tier suites ({', '.join(TIER_SUITES)}).",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        manifest = load_evaluation_inputs(inputs)
        verified = verify_evaluation_inputs(
            manifest,
            tier=SuiteTier(suite),
            harness_version=harness_version(),
            acquisition_store_root=acquisition_store_root,
        )
    except (EvaluationInputError, FinalAuthorizationRequiredError) as exc:
        typer.echo(f"Error: evaluation inputs rejected: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    summary = verified.summary()
    if output_json:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo("=" * 60)
    typer.echo(f"Evaluation inputs VERIFIED: {inputs}")
    typer.echo(f"Manifest id  : {summary['manifest_id']}")
    typer.echo(f"Scope        : {summary['scope_kind']} '{summary['scope_label']}'")
    typer.echo(f"Exposure     : {summary['exposure_class']} | tier: {summary['tier']}")
    typer.echo(f"Tasks        : {', '.join(summary['tasks'])}")
    if summary["required_blimp_subdatasets"]:
        typer.echo(f"BLiMP required: {', '.join(summary['required_blimp_subdatasets'])}")
    for entry in summary["selections"]:
        typer.echo(
            f"  {entry['namespace']:<28} {entry['declared_items']:>5} items  "
            f"{entry['source']} [{entry['source_split']}]"
        )
    if not summary["acquisition_receipts"]:
        typer.echo(
            "NOTE: no acquisition receipt is referenced; provenance beyond this local "
            "artifact is not established by verification alone."
        )
    for receipt_id in summary["verified_acquisition_receipts"]:
        typer.echo(f"Acquisition receipt verified: {receipt_id}")
    typer.echo("Nothing was evaluated: --verify-inputs-only checks inputs only.")
    typer.echo("=" * 60)


def _run_tier_suite(
    checkpoint: Path,
    tokenizer_path: str | None,
    suite: str,
    device: str,
    precision: str,
    limit: int | None,
    tasks: str | None,
    include_path: Path | None,
    output_dir: Path,
    pins_path: Path,
    request_only: bool,
    final_authorization_file: Path | None,
    execution: dict[str, Any] | None = None,
    inputs: Path | None = None,
    acquisition_store_root: Path | None = None,
) -> None:
    from xlm.evaluation.harness import HarnessUnavailableError, harness_version
    from xlm.evaluation.suites import (
        FinalAuthorization,
        FinalAuthorizationRequiredError,
        SuiteTier,
        TaskVariant,
        build_final_request,
        load_dataset_pins,
        resolve_suite,
    )

    tier = SuiteTier(suite)
    if tier is SuiteTier.FINAL and not request_only:
        typer.echo(
            "Error: never resolve a final split in the developer CLI; "
            "use the separate operator final service.",
            err=True,
        )
        raise typer.Exit(code=1)
    if harness_version() is None and not request_only:
        try:
            from xlm.evaluation.harness import require_harness

            require_harness()
        except HarnessUnavailableError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    pins = load_dataset_pins(pins_path)
    from xlm.tokenizers.loading import checkpoint_weights_hash, load_inference_tokenizer

    tok = load_inference_tokenizer(checkpoint, tokenizer_path)
    tokenizer_hash = tok.fingerprint
    checkpoint_hash = checkpoint_weights_hash(checkpoint)

    if tier is SuiteTier.FINAL and request_only:
        request = build_final_request(
            checkpoint_hash=checkpoint_hash,
            tokenizer_hash=tokenizer_hash,
            pins=pins,
            limit=limit,
            precision=precision,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        request_path = output_dir / f"final_request_{request['request_hash'][:16]}.json"
        request_path.write_text(json.dumps(request, indent=2, sort_keys=True), encoding="utf-8")
        typer.echo(f"Final evaluation request written: {request_path}")
        typer.echo("Execution requires operator authorization (P21); nothing was run.")
        return

    final_auth: FinalAuthorization | None = None
    if final_authorization_file is not None:
        payload = json.loads(final_authorization_file.read_text(encoding="utf-8"))
        final_auth = FinalAuthorization(
            operator_authorized=bool(payload.get("operator_authorized", False)),
            request_hash=str(payload.get("request_hash", "")),
            authorized_by=str(payload.get("authorized_by", "")),
            ticket=str(payload.get("ticket", "")),
        )

    verified = None
    if inputs is not None:
        # Declared route (D04): verify the artifacts before a model is built,
        # a task is resolved or any cached result is consulted.
        from xlm.evaluation.inputs import (
            EvaluationInputError,
            load_evaluation_inputs,
            verify_evaluation_inputs,
        )

        try:
            verified = verify_evaluation_inputs(
                load_evaluation_inputs(inputs),
                tier=tier,
                harness_version=harness_version(),
                final_authorization=final_auth,
                acquisition_store_root=acquisition_store_root,
            )
        except (EvaluationInputError, FinalAuthorizationRequiredError) as exc:
            typer.echo(f"Error: evaluation inputs rejected: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if tasks or include_path:
            typer.echo(
                "Error: --inputs declares the evaluation population; --tasks and "
                "--include-path would silently change it.",
                err=True,
            )
            raise typer.Exit(code=1)

    variants: list[TaskVariant] = []
    if verified is None:
        try:
            variants = resolve_suite(
                tier,
                pins,
                # Only resolve official BLiMP subdatasets for default suite runs; a
                # --tasks override replaces the variants entirely.
                blimp_subdatasets=None if tasks else _blimp_subdataset_names(include_path, None),
                final_authorization=final_auth,
            )
        except FinalAuthorizationRequiredError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    if tasks:
        # Fixture/task override for bounded offline runs through the real harness.
        overridden: list[TaskVariant] = []
        for name in (n.strip() for n in tasks.split(",") if n.strip()):
            overridden.append(
                TaskVariant(
                    variant_id=f"xlm_{name}_{tier.value}",
                    lm_eval_task=name,
                    tier=tier,
                    split="train",
                    normalized_metric="acc_norm",
                    chance_reference="mean_inverse_n_choices",
                    dataset_revision=None,
                    notes=("task override via --tasks",),
                )
            )
        try:
            from xlm.evaluation.suites import assert_no_final_split_leak

            assert_no_final_split_leak(overridden, tier)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        variants = overridden

    import importlib.util as _ilu

    if _ilu.find_spec("torch") is None:
        typer.echo(
            "Error: PyTorch is required for evaluation. "
            "Run with uv run --extra cpu/cuda xlm evaluate",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.evaluation.harness_runner import run_harness_suite
    from xlm.experiments.evaluation_execution import receipt_provenance
    from xlm.models.serialization import load_model_for_inference

    model = load_model_for_inference(checkpoint, device=device)
    try:
        evidence = run_harness_suite(
            model=model,
            tokenizer=tok,
            variants=variants,
            checkpoint_hash=checkpoint_hash,
            precision=precision,
            device=device,
            limit=limit,
            include_path=include_path,
            output_dir=output_dir,
            execution_provenance=receipt_provenance(execution) if execution else None,
            verified_inputs=verified,
        )
    except Exception as exc:  # noqa: BLE001 - surface harness errors with context
        typer.echo(f"Error: harness evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    coverage = evidence.coverage
    typer.echo("=" * 60)
    typer.echo(f"Suite: {tier.value} | Checkpoint: {checkpoint_hash}")
    typer.echo(f"Identity: {evidence.identity_fingerprint[:24]} | limit: {limit}")
    typer.echo(f"Scope: {coverage.scope_statement()}")
    for name, task in sorted(evidence.tasks.items()):
        expected = "?" if task.expected_items is None else str(task.expected_items)
        typer.echo(
            f"  {name:<24} acc {task.acc:.4f}  acc_norm {task.acc_norm:.4f}  "
            f"scored {task.scored_items}/{expected} expected"
        )
    if evidence.index.complete and evidence.index.index is not None:
        scope = "Four-task index" if coverage.covers_full_suite else "Declared-scope index"
        typer.echo(f"{scope}: {evidence.index.index:.4f}  [{coverage.scope_label}]")
        if not coverage.research_eligible():
            typer.echo(
                "  NOT research evidence: authored synthetic fixture scope, "
                "not an official benchmark result."
            )
    else:
        typer.echo("Index WITHHELD - the declared scope was not covered:")
        for reason in evidence.index.withheld_reasons or ["coverage is unverified"]:
            typer.echo(f"  - {reason}")
    for note in evidence.notes:
        typer.echo(f"  NOTE: {note}")
    typer.echo(f"Evidence: {output_dir}")
    typer.echo("=" * 60)


def evaluate_command(
    checkpoint: Annotated[
        Path | None,
        typer.Argument(
            # Optional only so that --verify-inputs-only can check a manifest
            # without a model. Every evaluating mode still requires it, and
            # says so explicitly rather than failing obscurely later.
            help="Path to checkpoint directory or artifact ID "
            "(not required with --verify-inputs-only).",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
        ),
    ] = None,
    tokenizer_path: Annotated[
        str | None,
        typer.Option("--tokenizer", help="Path to tokenizer directory or artifact ID."),
    ] = None,
    suite: Annotated[
        str,
        typer.Option(
            "--suite",
            help=f"Suite: {', '.join(ALL_SUITES)}.",
        ),
    ] = "all",
    device: Annotated[
        str,
        typer.Option("--device", help="Device to evaluate on ('cpu' or 'cuda')."),
    ] = "cpu",
    precision: Annotated[
        str,
        typer.Option("--precision", help="Precision mode ('fp32', 'bf16', 'fp16')."),
    ] = "fp32",
    boundary_policy: Annotated[
        str,
        typer.Option(
            "--boundary-policy",
            help="Boundary tokenization policy ('joint' or 'separate').",
        ),
    ] = "joint",
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output raw receipt as JSON."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Bound the number of items per task (smoke runs)."),
    ] = None,
    tasks: Annotated[
        str | None,
        typer.Option("--tasks", help="Comma-separated explicit task override."),
    ] = None,
    include_path: Annotated[
        Path | None,
        typer.Option("--include-path", help="Extra harness task YAML directory."),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", help="Evidence / request output directory."),
    ] = Path("data/eval"),
    pins_path: Annotated[
        Path,
        typer.Option("--pins", help="Pinned official dataset revisions YAML."),
    ] = Path("manifests/eval_dataset_pins.yaml"),
    request_only: Annotated[
        bool,
        typer.Option("--request-only", help="Write a final-evaluation request; run nothing."),
    ] = False,
    final_authorization_file: Annotated[
        Path | None,
        typer.Option(
            "--final-authorization",
            help="Operator authorization file matching the frozen final request.",
        ),
    ] = None,
    inputs: Annotated[
        Path | None,
        typer.Option(
            "--inputs",
            help="Evaluation-input manifest declaring explicitly selected, verified "
            "local benchmark artifacts.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    acquisition_store_root: Annotated[
        Path | None,
        typer.Option(
            "--acquisition-store-root",
            help="D01 store root holding acquisition receipts referenced by "
            "--inputs (its raw_dataset directory). Required when the manifest "
            "declares acquisition receipts.",
        ),
    ] = None,
    verify_inputs_only: Annotated[
        bool,
        typer.Option(
            "--verify-inputs-only",
            help="Verify --inputs and exit. Loads no model and evaluates nothing.",
        ),
    ] = False,
) -> None:
    """Evaluate a trained checkpoint against native fixtures or tiered harness suites."""
    if verify_inputs_only:
        if inputs is None:
            typer.echo("Error: --verify-inputs-only requires --inputs.", err=True)
            raise typer.Exit(code=1)
        # Runs entirely in this process: no frozen worker, no checkpoint, no
        # model allocation, no inference.
        _verify_inputs_only(inputs, suite, output_json, acquisition_store_root)
        return
    if checkpoint is None:
        typer.echo(
            "Error: a checkpoint is required to evaluate. Only --verify-inputs-only "
            "runs without one.",
            err=True,
        )
        raise typer.Exit(code=1)

    options: dict[str, Any] = {
        "checkpoint": checkpoint,
        "tokenizer_path": tokenizer_path,
        "suite": suite,
        "device": device,
        "precision": precision,
        "boundary_policy": boundary_policy,
        "output_json": output_json,
        "limit": limit,
        "tasks": tasks,
        "include_path": include_path,
        "output_dir": output_dir,
        "pins_path": pins_path,
        "request_only": request_only,
        "final_authorization_file": final_authorization_file,
        "inputs": inputs,
        "acquisition_store_root": acquisition_store_root,
    }
    if request_only or suite == "final" or suite not in ALL_SUITES:
        _evaluate_in_process(**options)
        return
    from xlm.experiments.evaluation_execution import launch_evaluation

    launch_evaluation(options)


def _evaluate_in_process(
    checkpoint: Path,
    tokenizer_path: str | None = None,
    suite: str = "all",
    device: str = "cpu",
    precision: str = "fp32",
    boundary_policy: str = "joint",
    output_json: bool = False,
    limit: int | None = None,
    tasks: str | None = None,
    include_path: Path | None = None,
    output_dir: Path = Path("data/eval"),
    pins_path: Path = Path("manifests/eval_dataset_pins.yaml"),
    request_only: bool = False,
    final_authorization_file: Path | None = None,
    inputs: Path | None = None,
    acquisition_store_root: Path | None = None,
    *,
    execution: dict[str, Any] | None = None,
) -> None:
    if suite not in ALL_SUITES:
        choices = ", ".join(ALL_SUITES)
        typer.echo(f"Error: unknown suite '{suite}'. Choose from: {choices}.", err=True)
        raise typer.Exit(code=1)

    if suite in NATIVE_SUITES:
        if importlib.util.find_spec("torch") is None:
            typer.echo(
                "Error: PyTorch is required for evaluation. "
                "Run with uv run --extra cpu/cuda xlm evaluate",
                err=True,
            )
            raise typer.Exit(code=1)
        _run_native_fixtures(
            checkpoint,
            tokenizer_path,
            suite,
            device,
            precision,
            boundary_policy,
            output_json,
            execution=execution,
        )
        return

    _run_tier_suite(
        checkpoint=checkpoint,
        tokenizer_path=tokenizer_path,
        suite=suite,
        device=device,
        precision=precision,
        limit=limit,
        tasks=tasks,
        include_path=include_path,
        output_dir=output_dir,
        pins_path=pins_path,
        request_only=request_only,
        final_authorization_file=final_authorization_file,
        execution=execution,
        inputs=inputs,
        acquisition_store_root=acquisition_store_root,
    )
