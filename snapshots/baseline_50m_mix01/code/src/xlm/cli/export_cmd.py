"""CLI for native model export with provenance (P20, A35)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Annotated

import typer


def export_command(
    checkpoint: Annotated[
        Path,
        typer.Argument(
            help="Path to checkpoint directory or artifact ID.",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Export bundle output directory."),
    ] = Path("data/exports/default"),
    export_id: Annotated[
        str, typer.Option("--export-id", help="Export bundle identifier.")
    ] = "export_default",
    tokenizer_path: Annotated[
        str | None,
        typer.Option("--tokenizer", help="Path to tokenizer directory or artifact ID."),
    ] = None,
    budget_targets: Annotated[
        int | None, typer.Option("--budget-targets", help="Training budget consumed.")
    ] = None,
    evidence_status: Annotated[
        str, typer.Option("--evidence-status", help="Evaluation evidence status.")
    ] = "none",
    data_manifest: Annotated[
        list[str] | None,
        typer.Option("--data-manifest", help="key=value manifest references (repeatable)."),
    ] = None,
    plugin_id: Annotated[
        str | None, typer.Option("--plugin-id", help="Plugin family that built the model.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the manifest as JSON.")] = False,
) -> None:
    """Export a checkpoint to a portable bundle: safe weights, hashes, provenance.

    Never bundles raw corpora (references only), credentials, run files, or
    optimizer state. Do not publish the result anywhere without authorization.
    """
    if importlib.util.find_spec("torch") is None:
        typer.echo(
            "Error: PyTorch is required for export. Run with uv run --extra cpu/cuda xlm export",
            err=True,
        )
        raise typer.Exit(code=1)

    from xlm.export.manifest import ExportError
    from xlm.export.writer import export_model
    from xlm.models.serialization import load_model_for_inference
    from xlm.tokenizers.base import BaseTokenizer
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer
    from xlm.tokenizers.byte import ByteTokenizer

    try:
        model = load_model_for_inference(checkpoint, device="cpu")
    except Exception as exc:
        typer.echo(f"Error: cannot load checkpoint: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    tok: BaseTokenizer
    if tokenizer_path is not None:
        t_dir = Path(tokenizer_path)
        if (t_dir / "tokenizer.json").is_file():
            tok = ByteLevelBPETokenizer.load(t_dir)
        else:
            tok = ByteTokenizer()
    else:
        parent_tok = checkpoint.parent / "tokenizer"
        if (parent_tok / "tokenizer.json").is_file():
            tok = ByteLevelBPETokenizer.load(parent_tok)
        else:
            tok = ByteTokenizer()

    refs: dict[str, str] = {}
    for item in data_manifest or []:
        if "=" not in item:
            typer.echo(f"Error: --data-manifest entries must be key=value, got '{item}'.", err=True)
            raise typer.Exit(code=1)
        key, value = item.split("=", 1)
        refs[key.strip()] = value.strip()

    try:
        manifest = export_model(
            model,
            tok,
            output_dir,
            export_id,
            plugin_id=plugin_id,
            data_manifest_refs=refs,
            budget_valid_targets=budget_targets,
            evidence_status=evidence_status,
        )
    except ExportError as exc:
        typer.echo(f"Error: export refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
        return
    typer.echo("============================================================")
    typer.echo(f"Export:          {manifest.export_id} -> {output_dir}")
    typer.echo(f"Architecture:    {manifest.architecture} v{manifest.architecture_version}")
    typer.echo(f"Parameters:      {manifest.parameters_deployed:,} deployed")
    typer.echo(f"Tokenizer:       {manifest.tokenizer_type} ({manifest.tokenizer_hash[:16]})")
    typer.echo(f"Weights:         model.safetensors ({manifest.model_hash[:16]})")
    typer.echo(
        f"Optimizer state: {'included' if manifest.includes_optimizer_state else 'excluded'}"
    )
    typer.echo("============================================================")
