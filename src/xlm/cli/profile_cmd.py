"""CLI for bounded execution profiling and resource plans (C10, P14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml


def profile_command(
    config: Annotated[Path, typer.Option("--config", "-c", help="Model preset YAML.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", "-o", help="Directory for profile artifacts.")
    ] = Path("data/profiles/default"),
    device: Annotated[str, typer.Option("--device", help="'cuda' or 'cpu'.")] = "cuda",
    precision: Annotated[
        str, typer.Option("--precision", help="fp32 | bf16_fp32_master | fp16.")
    ] = "bf16_fp32_master",
    attention_backend: Annotated[
        str | None,
        typer.Option("--attention-backend", help="Resolve 'eager' or 'sdpa' explicitly."),
    ] = None,
    global_batch: Annotated[
        int, typer.Option("--global-batch", help="Global valid-target batch to preserve.")
    ] = 65536,
    microbatches: Annotated[
        str, typer.Option("--microbatches", help="Comma-separated microbatch sizes.")
    ] = "1,2,4,8",
    dry_steps: Annotated[int, typer.Option("--dry-steps", help="Timed steps per size.")] = 2,
    budget_targets: Annotated[
        int, typer.Option("--budget-targets", help="Token budget for the ETA range.")
    ] = 1_000_000_000,
    compile_model: Annotated[
        bool, typer.Option("--compile/--no-compile", help="Verify with torch.compile.")
    ] = False,
    activation_checkpointing: Annotated[
        bool,
        typer.Option(
            "--activation-checkpointing/--no-activation-checkpointing",
            help="Verify with activation checkpointing.",
        ),
    ] = False,
    worker_timeout: Annotated[
        float, typer.Option("--worker-timeout", help="Seconds per calibration worker.")
    ] = 600.0,
) -> None:
    """Calibrate microbatch sizes in isolated workers and freeze a resource plan."""
    from xlm.config.schemas import ModelPresetConfig
    from xlm.models.backends import validate_precision
    from xlm.training.profile import (
        PreflightError,
        ProfileRequest,
        plan_resources,
        run_calibration,
        write_profile_artifacts,
    )

    try:
        raw = config.read_text(encoding="utf-8")
        data = json.loads(raw) if config.suffix == ".json" else yaml.safe_load(raw)
        preset = ModelPresetConfig.model_validate(data)
    except Exception as e:
        typer.echo(f"Error loading model preset: {e}", err=True)
        raise typer.Exit(code=1) from e

    backend = attention_backend or preset.attention_backend
    try:
        validate_precision(precision, device)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    model_config = preset.model_dump(exclude={"schema_version", "kind", "id"})
    request = ProfileRequest(
        model_id=preset.id,
        model_config=model_config,
        device=device,
        precision=precision,
        attention_backend=backend,
        global_batch_valid_targets=global_batch,
        context_length=preset.context_length,
        microbatch_candidates=[int(x) for x in microbatches.split(",") if x.strip()],
        dry_steps=dry_steps,
        compile=compile_model,
        activation_checkpointing=activation_checkpointing,
        worker_timeout_seconds=worker_timeout,
    )

    try:
        result = run_calibration(request, output_dir)
    except PreflightError as e:
        typer.echo(f"Preflight refused: {e}", err=True)
        raise typer.Exit(code=1) from e

    if result.selected_microbatch_sequences is None:
        typer.echo("Error: no feasible microbatch size; refusing to guess.", err=True)
        for size in result.sizes:
            typer.echo(f"  size {size.microbatch_sequences}: {size.failure_reason}", err=True)
        raise typer.Exit(code=1)

    from xlm.models.transformer import create_transformer_baseline

    # Exact parameter counts come from the meta-device model: no memory touched.
    probe_model = create_transformer_baseline(model_config, device="meta")
    unique = probe_model.count_parameters().unique_deployed

    plan = plan_resources(
        result,
        budget_valid_targets=budget_targets,
        unique_params=unique,
        architecture=model_config.get("architecture", ""),
    )
    result.resource_plan = plan
    paths = write_profile_artifacts(result, output_dir)

    selected = next(
        s
        for s in result.sizes
        if s.feasible and s.microbatch_sequences == result.selected_microbatch_sequences
    )
    typer.echo("============================================================")
    typer.echo(f"Profile:           {result.model_id} on {device} ({precision})")
    typer.echo(f"Backend:           {backend}")
    typer.echo(
        f"Selected:          microbatch {selected.microbatch_sequences} sequences, "
        f"accumulation x{result.accumulation_steps}"
    )
    typer.echo(
        f"Measured:          {selected.tokens_per_sec:,.0f} tokens/sec, "
        f"peak reserved {selected.peak_reserved_gib:.2f} GiB"
    )
    typer.echo(
        f"ETA range:         {plan.eta_seconds_range[0]:,.0f}s .. "
        f"{plan.eta_seconds_range[1]:,.0f}s for {budget_targets:,} targets"
    )
    typer.echo(
        f"Checkpoint:        {plan.checkpoint_gib:.2f} GiB, "
        f"{plan.checkpoint_save_seconds:.1f}s per save (train overhead separated)"
    )
    typer.echo(f"Profile:           {paths['profile']}")
    typer.echo(f"Freeze:            {paths['freeze']}")
    typer.echo("============================================================")
