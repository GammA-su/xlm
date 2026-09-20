"""CLI commands for training, checkpoint resumption, and run inspection.

Complying with XLM Contracts C08, C09, C10 and P05 Amendments:
- `xlm train`: executes a resolved training plan.
- `xlm resume`: resumes an uninterrupted plan or creates an explicit fork.
- `xlm run inspect`: inspects checkpoint or run metadata WITHOUT importing heavy dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

run_app = typer.Typer(name="run", help="Inspect and manage experiment runs.", no_args_is_help=True)


@run_app.command(name="inspect")
def run_inspect_command(
    target: Annotated[
        Path,
        typer.Argument(
            help="Path to checkpoint directory, run directory, or manifest file.",
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output inspection data as raw JSON.",
        ),
    ] = False,
) -> None:
    """Inspect checkpoint or run metadata without importing heavy runtime dependencies."""
    target_path = target.resolve()

    # Determine what kind of metadata exists
    meta_dict: dict[str, Any] = {}

    if target_path.is_file() and target_path.name in (
        "checkpoint_meta.json",
        "manifest.json",
        "run_record.json",
    ):
        meta_dict = json.loads(target_path.read_text(encoding="utf-8"))
    elif target_path.is_dir():
        chk_meta = target_path / "checkpoint_meta.json"
        manifest_file = target_path / "manifest.json"
        run_record = target_path / "run_record.json"

        if chk_meta.is_file():
            meta_dict = json.loads(chk_meta.read_text(encoding="utf-8"))
        elif manifest_file.is_file():
            meta_dict = json.loads(manifest_file.read_text(encoding="utf-8"))
        elif run_record.is_file():
            meta_dict = json.loads(run_record.read_text(encoding="utf-8"))
        else:
            typer.echo(f"Error: No metadata file found in {target_path}", err=True)
            raise typer.Exit(code=1)
    else:
        typer.echo(f"Error: Invalid inspection target {target_path}", err=True)
        raise typer.Exit(code=1)

    if target_path.is_dir() and (target_path / "checkpoint_meta.json").is_file():
        context = target_path / "execution.json"
        meta_dict["execution_evidence"] = "unresolved-legacy"
        if context.is_file():
            from xlm.experiments.execution import read_json

            recorded = read_json(context)
            meta_dict["execution_evidence"] = (
                "recorded-frozen-envelope; inspection is not execution verification"
                if "envelope" in recorded
                else recorded.get("status", "unresolved")
            )
    if json_output:
        typer.echo(json.dumps(meta_dict, indent=2, sort_keys=True))
        return

    # Rich human-readable formatting
    typer.echo("=" * 60)
    typer.echo(f"XLM Inspection: {target_path.name}")
    typer.echo("=" * 60)
    for k, v in meta_dict.items():
        if isinstance(v, dict):
            typer.echo(f"{k}:")
            for sub_k, sub_v in v.items():
                typer.echo(f"  {sub_k}: {sub_v}")
        else:
            typer.echo(f"{k}: {v}")
    typer.echo("=" * 60)


def train_command(
    plan_or_recipe: Annotated[
        Path,
        typer.Argument(
            help="Path to YAML/JSON experiment plan or recipe.",
        ),
    ],
    device: Annotated[
        str, typer.Option("--device", help="Device to train on ('cpu' or 'cuda').")
    ] = "cpu",
    max_targets: Annotated[
        int | None, typer.Option("--max-targets", help="Override max target budget.")
    ] = None,
    max_seconds: Annotated[
        float | None, typer.Option("--max-seconds", help="Override max runtime seconds.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate plan without executing training.")
    ] = False,
    precision: Annotated[
        str | None,
        typer.Option("--precision", help="Override plan precision (fp32, bf16_fp32_master, fp16)."),
    ] = None,
    attention_backend: Annotated[
        str | None,
        typer.Option("--attention-backend", help="Resolve model attention backend explicitly."),
    ] = None,
    compile_model: Annotated[
        bool | None,
        typer.Option("--compile/--no-compile", help="Opt into torch.compile explicitly."),
    ] = None,
    activation_checkpointing: Annotated[
        bool | None,
        typer.Option(
            "--activation-checkpointing/--no-activation-checkpointing",
            help="Opt into activation checkpointing explicitly.",
        ),
    ] = None,
) -> None:
    """Execute a causal language model training run."""
    from xlm.experiments.direct import launch_train

    try:
        launch_train(
            plan_or_recipe,
            {
                "device": device,
                "max_targets": max_targets,
                "max_seconds": max_seconds,
                "dry_run": dry_run,
                "precision": precision,
                "attention_backend": attention_backend,
                "compile_model": compile_model,
                "activation_checkpointing": activation_checkpointing,
            },
        )
    except (ValueError, OSError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _train_in_process(
    plan_or_recipe: Path,
    device: str = "cpu",
    max_targets: int | None = None,
    max_seconds: float | None = None,
    dry_run: bool = False,
    precision: str | None = None,
    attention_backend: str | None = None,
    compile_model: bool | None = None,
    activation_checkpointing: bool | None = None,
    *,
    execution: dict[str, Any],
) -> None:
    try:
        import torch  # noqa: F401
    except ImportError as err:
        typer.echo(
            "Error: PyTorch is required for training. Run with uv run --extra cpu/cuda xlm train",
            err=True,
        )
        raise typer.Exit(code=1) from err

    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.components import construct_training_components
    from xlm.training.trainer import Trainer

    # Load and compose plan
    if str(plan_or_recipe).endswith((".yaml", ".yml")):
        from xlm.config.composer import ConfigComposer

        composer = ConfigComposer(Path.cwd())
        plan_dict = composer.compose(plan_or_recipe)
    else:
        plan_dict = json.loads(plan_or_recipe.read_text(encoding="utf-8"))
    typer.echo(f"Loaded plan '{plan_dict.get('id', 'unknown')}'")

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    checkpoint_manager = CheckpointManager(
        artifact_store=store, run_ledger=ledger, paths=paths, execution=execution
    )
    from xlm.experiments.execution import resolve_execution_config

    normalized, _ = resolve_execution_config(plan_dict)
    if normalized != execution["envelope"]["config"]:
        raise ValueError("worker training configuration differs from frozen envelope")

    from xlm.experiments.direct import _smoke_limits

    _smoke_limits(normalized)
    label = plan_dict.get("id", "unnamed")
    plan_dict = {**normalized, "id": label}
    training_cfg = plan_dict["training"]
    components = construct_training_components(normalized, device=device)
    model, objective = components.model, components.objective
    optimizer, manifest = components.optimizer, components.optimizer_manifest
    schedule, batcher = components.schedule, components.batcher
    model_cfg = model.config
    data_identity = components.data_identity
    budget_targets = training_cfg["budget"]["max_valid_targets"]
    budget_seconds = training_cfg["budget"]["max_train_seconds"]

    run_precision = precision or training_cfg.get("precision", "fp32")
    run_compile = (
        compile_model if compile_model is not None else bool(training_cfg.get("compile", False))
    )
    run_checkpointing = (
        activation_checkpointing
        if activation_checkpointing is not None
        else bool(training_cfg.get("activation_checkpointing", False))
    )
    plan_dict["model"] = model_cfg.model_dump()
    training_cfg.update(
        {
            "device": device,
            "precision": run_precision,
            "compile": run_compile,
            "activation_checkpointing": run_checkpointing,
        }
    )
    training_cfg["budget"] = {
        "max_valid_targets": budget_targets,
        "max_train_seconds": budget_seconds,
    }
    checkpoint_manager.runtime_config = {
        "version": 2,
        "plan": plan_dict,
        "data_identity": data_identity,
    }
    try:
        trainer = Trainer(
            model=model,
            objective=objective,
            optimizer=optimizer,
            optimizer_manifest=manifest,
            schedule=schedule,
            batcher=batcher,
            checkpoint_manager=checkpoint_manager,
            run_id=f"run_{plan_dict.get('id', 'unnamed')}",
            plan_id=plan_dict.get("id", "plan_unnamed"),
            device=device,
            precision=run_precision,
            gradient_clip_norm=training_cfg.get("gradient_clip_norm", 1.0),
            max_valid_targets=budget_targets,
            max_train_seconds=budget_seconds,
            checkpoint_every_valid_targets=training_cfg.get("checkpoint_every_valid_targets"),
            activation_checkpointing=run_checkpointing,
            compile_model=run_compile,
        )
    except Exception as e:
        typer.echo(f"Error: invalid execution configuration: {e}", err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"Execution: {trainer.execution_report()}")

    typer.echo(f"Starting training run: budget={budget_targets} targets, device={device}")
    summary = trainer.train()

    typer.echo("=" * 60)
    typer.echo("Training Run Complete")
    typer.echo("=" * 60)
    typer.echo(f"Status:             {summary.termination_reason}")
    typer.echo(f"Total Steps:        {summary.total_steps}")
    typer.echo(f"Committed Targets:  {summary.committed_valid_targets}")
    typer.echo(
        f"Final Loss:         {summary.final_loss:.4f}"
        if summary.final_loss is not None
        else "Final Loss: N/A"
    )
    typer.echo(f"Training Time:      {summary.total_training_time_seconds:.2f}s")
    typer.echo(f"Setup Time:         {summary.setup_time_seconds:.2f}s")
    typer.echo("=" * 60)


def resume_command(
    checkpoint_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to checkpoint artifact directory.",
        ),
    ],
    fork: Annotated[
        bool,
        typer.Option(
            "--fork",
            help="Create a new forked run branching from this checkpoint.",
        ),
    ] = False,
    budget: Annotated[
        int | None,
        typer.Option(
            "--budget",
            help="New cumulative target budget for resume/fork.",
        ),
    ] = None,
    device: Annotated[
        str, typer.Option("--device", help="Device to resume on ('cpu' or 'cuda').")
    ] = "cpu",
) -> None:
    """Resume training from a saved checkpoint, or branch via an explicit fork."""
    from xlm.experiments.direct import launch_resume

    try:
        launch_resume(checkpoint_dir, {"fork": fork, "budget": budget, "device": device})
    except (ValueError, OSError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _resume_in_process(
    checkpoint_dir: Path,
    fork: bool = False,
    budget: int | None = None,
    device: str = "cpu",
    *,
    execution: dict[str, Any],
) -> None:
    try:
        import torch  # noqa: F401
    except ImportError as err:
        typer.echo(
            "Error: PyTorch is required for resumption. "
            "Run with uv run --extra cpu/cuda xlm resume",
            err=True,
        )
        raise typer.Exit(code=1) from err

    from xlm.artifacts.ledger import RunLedger
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.components import construct_training_components
    from xlm.training.trainer import Trainer

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    checkpoint_manager = CheckpointManager(
        artifact_store=store, run_ledger=ledger, paths=paths, execution=execution
    )

    meta_file = checkpoint_dir / "checkpoint_meta.json"
    if not meta_file.is_file():
        typer.echo(f"Error: Missing checkpoint_meta.json in {checkpoint_dir}", err=True)
        raise typer.Exit(code=1)

    store.verify_artifact(checkpoint_dir)
    runtime_path = checkpoint_dir / "runtime.json"
    if not runtime_path.is_file():
        typer.echo(
            "Error: legacy checkpoint has no runtime.json; CLI resume cannot reconstruct "
            "its data/config safely. Use the domain API with the original frozen components.",
            err=True,
        )
        raise typer.Exit(code=1)
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    original = runtime["plan"]
    resolved = execution["envelope"]["config"]
    training_cfg = resolved["training"]
    components = construct_training_components(resolved, device=device)
    if components.data_identity != runtime["data_identity"]:
        raise ValueError("resume data identity changed; refusing a different token stream")
    model, objective = components.model, components.objective
    optimizer, manifest = components.optimizer, components.optimizer_manifest
    schedule, batcher = components.schedule, components.batcher
    meta_dict = json.loads(meta_file.read_text(encoding="utf-8"))
    target_budget = training_cfg["budget"]["max_valid_targets"]
    if not meta_dict["committed_valid_targets"] <= target_budget <= 200_000 or (
        fork and meta_dict["committed_valid_targets"] == target_budget
    ):
        raise ValueError("resume requires a larger target count within the 200000-target smoke cap")
    runtime["plan"] = {**resolved, "id": original.get("id", "unnamed")}

    # Restore the recorded precision: an FP16 run resumes with its scaler, and a
    # checkpoint carrying scaler state without one is refused (or forked).
    resume_precision = str(meta_dict.get("precision", "fp32"))
    resume_scaler = None
    if resume_precision in ("fp16", "fp16_scaler") and device == "cuda" and not fork:
        resume_scaler = torch.amp.GradScaler("cuda")

    # Horizon rule: a resumed budget that outgrows the saved schedule horizon
    # requires an explicit fork; extending a completed schedule in place is
    # never identical to a long-horizon run.
    from xlm.experiments.plans import BudgetHorizonError, check_budget_against_horizon

    target_budget_preview = target_budget
    saved_schedule = checkpoint_dir / "schedule.json"
    saved_horizon: int | None = None
    if saved_schedule.is_file():
        try:
            saved_horizon = (
                int(
                    json.loads(saved_schedule.read_text(encoding="utf-8")).get(
                        "horizon_valid_targets", 0
                    )
                    or 0
                )
                or None
            )
        except (ValueError, TypeError):
            saved_horizon = None
    if saved_horizon is not None:
        try:
            check_budget_against_horizon(target_budget_preview, saved_horizon, fork)
        except BudgetHorizonError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    # Load checkpoint
    expected_plan = None if fork else meta_dict["plan_id"]
    meta = checkpoint_manager.load_checkpoint(
        checkpoint_dir,
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        expected_plan_id=expected_plan,
        device=device,
        is_fork=fork,
        scaler=resume_scaler,
    )
    if not fork and meta.committed_valid_targets == target_budget:
        typer.echo(
            "Resume complete: frozen target budget is already committed; checkpoint preserved."
        )
        return

    parent_chk: str | None
    parent_plan: str | None
    if fork:
        run_id = f"fork_from_{meta.checkpoint_id}"
        plan_id = f"plan_fork_from_{meta.plan_id}"
        parent_chk = meta.checkpoint_id
        parent_plan = meta.plan_id
        typer.echo(f"Creating forked run '{run_id}' linked to parent checkpoint '{parent_chk}'")
    else:
        run_id = meta.run_id
        plan_id = meta.plan_id
        parent_chk = meta.parent_checkpoint_id
        parent_plan = meta.parent_plan_id
        typer.echo(
            f"Resuming run '{run_id}' from step {meta.step} "
            f"({meta.committed_valid_targets} targets)"
        )

    trainer = Trainer(
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=checkpoint_manager,
        run_id=run_id,
        plan_id=plan_id,
        device=device,
        precision=resume_precision,
        gradient_clip_norm=training_cfg.get("gradient_clip_norm", 1.0),
        max_valid_targets=target_budget,
        max_train_seconds=training_cfg["budget"].get("max_train_seconds", 600.0),
        checkpoint_every_valid_targets=training_cfg.get("checkpoint_every_valid_targets"),
        activation_checkpointing=training_cfg.get("activation_checkpointing", False),
        compile_model=training_cfg.get("compile", False),
        step=meta.step,
        committed_valid_targets=meta.committed_valid_targets,
        processed_valid_targets=meta.processed_valid_targets,
        parent_checkpoint_id=parent_chk,
        parent_plan_id=parent_plan,
    )
    if resume_scaler is not None:
        # The checkpoint's scaler state was restored into this object; the
        # trainer must continue from it rather than from a fresh scaler.
        trainer.scaler = resume_scaler

    training_cfg["budget"]["max_valid_targets"] = target_budget
    training_cfg["device"] = device
    checkpoint_manager.runtime_config = runtime

    summary = trainer.train()

    typer.echo("=" * 60)
    typer.echo(f"{'Fork' if fork else 'Resumed'} Run Complete")
    typer.echo("=" * 60)
    typer.echo(f"Status:             {summary.termination_reason}")
    typer.echo(f"Total Steps:        {summary.total_steps}")
    typer.echo(f"Committed Targets:  {summary.committed_valid_targets}")
    typer.echo(
        f"Final Loss:         {summary.final_loss:.4f}"
        if summary.final_loss is not None
        else "Final Loss: N/A"
    )
    typer.echo("=" * 60)
