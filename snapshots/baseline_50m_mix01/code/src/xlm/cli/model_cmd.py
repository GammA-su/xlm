"""CLI subcommands for model inspection and validation complying with XLM Contract C08."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from xlm.config.composer import load_yaml_file
from xlm.config.schemas import ModelPresetConfig, TransformerBaselineConfig

model_app = typer.Typer(
    name="model",
    help="Model inspection, validation, and architecture commands.",
    no_args_is_help=True,
)


def _resolve_model_config(preset_or_path: str) -> TransformerBaselineConfig:
    """Resolve a model configuration from a preset name or file path."""
    # Check if it is a file path
    path = Path(preset_or_path)
    if path.is_file():
        raw_dict = load_yaml_file(path)
        if raw_dict.get("kind") == "model_preset":
            preset = ModelPresetConfig.model_validate(raw_dict)
            dumped = preset.model_dump(exclude={"schema_version", "kind", "id"})
            return TransformerBaselineConfig.model_validate(dumped)
        return TransformerBaselineConfig.model_validate(raw_dict)

    # Check recipes/models/<preset_or_path>.yaml
    recipe_path = Path("recipes/models") / f"{preset_or_path}.yaml"
    if recipe_path.is_file():
        raw_dict = load_yaml_file(recipe_path)
        preset = ModelPresetConfig.model_validate(raw_dict)
        dumped = preset.model_dump(exclude={"schema_version", "kind", "id"})
        return TransformerBaselineConfig.model_validate(dumped)

    raise FileNotFoundError(
        f"Could not resolve model preset or file path: '{preset_or_path}'. "
        f"Searched direct path and '{recipe_path}'."
    )


@model_app.command("inspect")
def inspect_model(
    preset_or_path: str = typer.Argument(
        ...,
        help="Preset name (e.g. '50m', '150m', '300m', 'tiny') or path to YAML config.",
    ),
    device: str = typer.Option(
        "meta",
        "--device",
        "-d",
        help="Device for parameter instantiation ('meta' or 'cpu').",
    ),
    tokenizer_artifact: str | None = typer.Option(
        None,
        "--tokenizer-artifact",
        "-t",
        help="Optional tokenizer artifact ID or path for compatibility validation.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Output inspection results as structured JSON.",
    ),
) -> None:
    """Inspect model architecture specifications and meta-safe parameter counts."""
    # Lazy import of torch and model code
    try:
        import torch
    except ImportError as e:
        typer.echo(
            f"Error: PyTorch is required for model inspection. {e}\n"
            "Install with: uv sync --extra cpu (or --extra cuda)",
            err=True,
        )
        raise typer.Exit(1) from e

    from xlm.models.transformer import (
        TransformerBaseline,
        check_tokenizer_model_compatibility,
    )

    # 1. Resolve configuration
    try:
        config = _resolve_model_config(preset_or_path)
    except Exception as e:
        typer.echo(f"Error resolving model configuration: {e}", err=True)
        raise typer.Exit(1) from e

    # 2. Instantiate model on requested device
    target_device = torch.device(device)
    model = TransformerBaseline(config, device=target_device)
    param_counts = model.count_parameters()

    # 3. Check tokenizer compatibility if requested
    tokenizer_status: dict[str, Any] = {"status": "UNCHECKED", "details": "No tokenizer specified."}
    if tokenizer_artifact is not None:
        try:
            from xlm.core.paths import ArtifactPaths
            from xlm.tokenizers.bpe import ByteLevelBPETokenizer

            paths = ArtifactPaths.from_env()
            tok_path = Path(tokenizer_artifact)
            if not tok_path.is_dir():
                for kind in ("tokenizers", "raw", "clean", "shards"):
                    cand = paths.root / kind / tokenizer_artifact
                    if cand.is_dir():
                        tok_path = cand
                        break

            tokenizer = ByteLevelBPETokenizer.load(tok_path)
            compatible, reason = check_tokenizer_model_compatibility(tokenizer, config)
            tokenizer_status = {
                "status": "VERIFIED" if compatible else "INCOMPATIBLE",
                "details": reason,
                "tokenizer_vocab": tokenizer.vocab_size,
            }
        except Exception as e:
            tokenizer_status = {
                "status": "INCOMPATIBLE",
                "details": str(e),
            }

    result = {
        "model_id": preset_or_path,
        "architecture": config.architecture,
        "architecture_version": config.architecture_version,
        "device": device,
        "vocab_size": config.vocab_size,
        "context_length": config.context_length,
        "hidden_size": config.hidden_size,
        "num_layers": config.num_layers,
        "num_attention_heads": config.num_attention_heads,
        "head_dim": config.hidden_size // config.num_attention_heads,
        "intermediate_size": config.intermediate_size,
        "tie_embeddings": config.tie_embeddings,
        "normalization": config.normalization,
        "position_encoding": config.position_encoding,
        "ffn": config.ffn,
        "attention_backend": config.attention_backend,
        "initialization_policy": config.initialization_policy,
        "parameters": param_counts.to_dict(),
        "tokenizer_compatibility": tokenizer_status,
    }

    if output_json:
        typer.echo(json.dumps(result, indent=2))
        return

    # Formatted terminal output
    typer.echo("=" * 70)
    typer.echo(f"XLM Model Architecture Inspection: {preset_or_path}")
    typer.echo("=" * 70)
    typer.echo(f"  Architecture:           {config.architecture} (v{config.architecture_version})")
    typer.echo(f"  Instantiation Device:   {device}")
    typer.echo(f"  Vocabulary Size:        {config.vocab_size:,}")
    typer.echo(f"  Context Length:         {config.context_length:,}")
    typer.echo(f"  Layers (L):             {config.num_layers}")
    typer.echo(f"  Hidden Size (d):        {config.hidden_size}")
    head_dim = config.hidden_size // config.num_attention_heads
    typer.echo(f"  Attention Heads (H):    {config.num_attention_heads} (head_dim: {head_dim})")
    typer.echo(f"  FFN Intermediate (f):   {config.intermediate_size}")
    typer.echo(f"  Tied Embeddings:        {config.tie_embeddings}")
    typer.echo(f"  Attention Backend:      {config.attention_backend}")
    typer.echo(f"  Normalization:          {config.normalization}")
    typer.echo(f"  Position Encoding:      {config.position_encoding}")
    typer.echo("-" * 70)
    typer.echo("Parameter Accounting (Contract C08):")
    typer.echo(f"  Formula Expected:       {param_counts.formula_estimate:,}")
    typer.echo(f"  Total Instantiated:     {param_counts.total_instantiated:,}")
    typer.echo(f"  Unique Deployed:        {param_counts.unique_deployed:,}")
    typer.echo(f"  Active:                 {param_counts.active:,}")
    typer.echo(f"  Non-Embedding:          {param_counts.non_embedding:,}")
    typer.echo(f"  Tied Parameters:        {param_counts.tied_parameters:,}")
    typer.echo(
        f"  Formula Matches:        {'YES' if param_counts.formula_matches else 'NO (MISMATCH)'}"
    )
    if param_counts.max_deployed_cap is not None:
        status_cap = "PASSED" if param_counts.cap_respected else "EXCEEDED"
        typer.echo(f"  Max Deployed Cap:       {param_counts.max_deployed_cap:,} ({status_cap})")
    if param_counts.tied_aliases:
        alias_str = ", ".join(f"{a} <-> {b}" for a, b in param_counts.tied_aliases)
        typer.echo(f"  Tied Aliases:           {alias_str}")
    typer.echo("-" * 70)
    typer.echo(
        f"Tokenizer Compatibility:  {tokenizer_status['status']} ({tokenizer_status['details']})"
    )
    typer.echo("=" * 70)
