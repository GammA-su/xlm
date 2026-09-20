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
    from xlm.config.schemas import (
        AdamWConfig,
        CrossEntropyObjectiveConfig,
        TransformerBaselineConfig,
        WarmupCosineScheduleConfig,
    )
    from xlm.core.paths import ArtifactPaths
    from xlm.models.transformer import create_transformer_baseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    # Load and compose plan
    if str(plan_or_recipe).endswith((".yaml", ".yml")):
        from xlm.config.composer import ConfigComposer

        composer = ConfigComposer(Path.cwd())
        plan_dict = composer.compose(plan_or_recipe)
    else:
        plan_dict = json.loads(plan_or_recipe.read_text(encoding="utf-8"))
    typer.echo(f"Loaded plan '{plan_dict.get('id', 'unknown')}'")

    if dry_run:
        typer.echo("Dry run requested. Plan successfully validated.")
        return

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    checkpoint_manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

    # Initialize model
    model_cfg = TransformerBaselineConfig.model_validate(plan_dict.get("model", {}))
    if attention_backend is not None:
        model_cfg = model_cfg.model_copy(update={"attention_backend": attention_backend})
    if model_cfg.attention_backend == "profile_required":
        typer.echo(
            "Error: model attention_backend is 'profile_required'. Resolve it explicitly "
            "with --attention-backend or by freezing an `xlm profile` result; "
            "execution never guesses a backend.",
            err=True,
        )
        raise typer.Exit(code=1)
    model = create_transformer_baseline(model_cfg, device=device)

    # Initialize objective
    obj_cfg = CrossEntropyObjectiveConfig.model_validate(plan_dict.get("objective", {}))
    objective = CrossEntropyObjective(obj_cfg)

    # Initialize optimizer
    opt_cfg = AdamWConfig.model_validate(plan_dict.get("optimizer", {}))
    optimizer, manifest = create_adamw_optimizer(opt_cfg, model=model, objective=objective)

    # Initialize schedule
    training_cfg = plan_dict.get("training", {})
    sched_cfg_dict = training_cfg.get("schedule", {})
    sched_cfg = WarmupCosineScheduleConfig.model_validate(sched_cfg_dict)
    schedule = WarmupCosineSchedule(sched_cfg, base_lr=opt_cfg.lr)

    # Initialize data batcher
    # For local testing/demo if data pool is not a shard, use synthetic tokens
    data_dict = plan_dict.get("data", {})
    pool_art = data_dict.get("pool_artifact")
    if pool_art and (paths.token_shards / pool_art).is_dir():
        data_source: Any = paths.token_shards / pool_art
    else:
        # Fallback synthetic pattern for local tests
        data_source = [((i % (model_cfg.vocab_size - 4)) + 4) for i in range(1000)]

    batcher = TrainingBatcher(
        data_source=data_source,
        context_length=training_cfg.get("context_length", model_cfg.context_length),
        global_batch_valid_targets=training_cfg.get("global_batch_valid_targets", 64),
        microbatch_sequences=training_cfg.get("microbatch_sequences"),
        exhaustion_policy="repeat_bounded",
        max_document_exposures=100,
    )

    budget_targets = max_targets or training_cfg.get("budget", {}).get("max_valid_targets", 100)
    budget_seconds = max_seconds or training_cfg.get("budget", {}).get("max_train_seconds")

    run_precision = precision or training_cfg.get("precision", "fp32")
    run_compile = (
        compile_model if compile_model is not None else bool(training_cfg.get("compile", False))
    )
    run_checkpointing = (
        activation_checkpointing
        if activation_checkpointing is not None
        else bool(training_cfg.get("activation_checkpointing", False))
    )
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
    from xlm.config.schemas import (
        AdamWConfig,
        CrossEntropyObjectiveConfig,
        TransformerBaselineConfig,
        WarmupCosineScheduleConfig,
    )
    from xlm.core.paths import ArtifactPaths
    from xlm.models.transformer import create_transformer_baseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    checkpoint_manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)

    meta_file = checkpoint_dir / "checkpoint_meta.json"
    if not meta_file.is_file():
        typer.echo(f"Error: Missing checkpoint_meta.json in {checkpoint_dir}", err=True)
        raise typer.Exit(code=1)

    meta_dict = json.loads(meta_file.read_text(encoding="utf-8"))
    chk_model_cfg = meta_dict["model_config"]
    model_cfg = TransformerBaselineConfig.model_validate(chk_model_cfg)
    model = create_transformer_baseline(model_cfg, device=device)

    objective = CrossEntropyObjective(CrossEntropyObjectiveConfig())

    # Read optimizer settings from saved manifest to ensure exact compatibility
    opt_pt = checkpoint_dir / "optimizer.pt"
    saved_weight_decay = 0.01
    saved_lr = 0.01
    if opt_pt.is_file():
        saved_opt_raw = torch.load(opt_pt, map_location="cpu", weights_only=False)
        saved_manifest_dict = saved_opt_raw.get("manifest", {})
        if "groups" in saved_manifest_dict and saved_manifest_dict["groups"]:
            saved_weight_decay = float(saved_manifest_dict["groups"][0].get("weight_decay", 0.01))
            saved_lr = float(saved_manifest_dict["groups"][0].get("lr", 0.01))

    optimizer, manifest = create_adamw_optimizer(
        AdamWConfig(lr=saved_lr, weight_decay=saved_weight_decay),
        model=model,
        objective=objective,
    )

    target_budget = budget or (meta_dict["committed_valid_targets"] + 50)
    sched_cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=20,
        horizon_valid_targets=target_budget,
        min_lr_ratio=0.1,
    )
    schedule = WarmupCosineSchedule(sched_cfg, base_lr=0.01)

    tokens = [((i % (model_cfg.vocab_size - 4)) + 4) for i in range(1000)]
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=model_cfg.context_length,
        global_batch_valid_targets=50,
        exhaustion_policy="repeat_bounded",
        max_document_exposures=100,
    )

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

    target_budget_preview = budget or (meta_dict["committed_valid_targets"] + 50)
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
        gradient_clip_norm=1.0,
        max_valid_targets=target_budget,
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
