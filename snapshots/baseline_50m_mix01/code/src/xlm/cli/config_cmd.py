"""CLI commands for validating, resolving, and diffing XLM configurations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from xlm.config.composer import ConfigComposer, diff_configs, load_yaml_file
from xlm.config.schemas import (
    ExecutableExperimentPlanConfig,
    ExperimentDraftConfig,
    MixtureConfig,
    ModelPresetConfig,
)

config_app = typer.Typer(
    name="config",
    help="Inspect, validate, resolve, and diff XLM configuration files.",
)


@config_app.command(name="validate")
def validate_command(
    path: Annotated[Path, typer.Argument(help="Path to YAML/JSON configuration file.")],
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            "-m",
            help="Validation mode: 'draft' or 'executable'.",
        ),
    ] = "draft",
) -> None:
    """Validate a configuration against strict schemas with precise error paths."""
    if not path.is_file():
        typer.echo(f"Error: File not found at '{path}'", err=True)
        raise typer.Exit(code=1)

    try:
        raw_data = load_yaml_file(path)
    except Exception as exc:
        typer.echo(f"Parsing error in '{path}': {exc}", err=True)
        raise typer.Exit(code=1) from exc

    kind = raw_data.get("kind", "experiment_draft")

    try:
        if kind == "model_preset":
            ModelPresetConfig.model_validate(raw_data)
        elif kind == "mixture_preset":
            MixtureConfig.model_validate(raw_data)
        elif kind == "comparison_recipe":
            from xlm.comparison.recipes import load_comparison_recipe

            load_comparison_recipe(path)
        elif kind == "campaign_draft":
            from xlm.experiments.campaigns import load_campaign_draft

            draft = load_campaign_draft(path)
            for key in ("id", "mixtures", "first_round"):
                if key not in draft:
                    raise ValueError(f"campaign draft missing required key '{key}'")
            if not draft["mixtures"]:
                raise ValueError("campaign draft names no mixtures")
        elif kind == "prepare_config":
            from xlm.prepare.config import load_prepare_config

            load_prepare_config(path)
        elif kind == "mix01_view_registry":
            from xlm.data.sources.mix01 import load_mix01_views

            load_mix01_views(path)
        elif mode == "executable":
            ExecutableExperimentPlanConfig.model_validate(raw_data)
        else:
            ExperimentDraftConfig.model_validate(raw_data)

        typer.echo(f"Validation successful: '{path}' complies with {kind} ({mode} mode)")
    except ValidationError as val_err:
        typer.echo(f"Validation failed for '{path}' ({mode} mode):", err=True)
        for error in val_err.errors():
            loc = ".".join(str(p) for p in error["loc"])
            typer.echo(f"  - [{loc}]: {error['msg']} (type={error['type']})", err=True)
        raise typer.Exit(code=1) from val_err
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Validation failed for '{path}' ({mode} mode): {exc}", err=True)
        raise typer.Exit(code=1) from exc


@config_app.command(name="resolve")
def resolve_command(
    path: Annotated[Path, typer.Argument(help="Path to configuration file to resolve.")],
    overrides: Annotated[
        list[str] | None,
        typer.Option(
            "--override",
            "-o",
            help="Typed override in key=value format (e.g. training.compile=true).",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Optional file path to save resolved configuration.",
        ),
    ] = None,
) -> None:
    """Resolve inheritance, model/mixture presets, and overrides into a flat configuration."""
    workspace_root = Path.cwd()
    composer = ConfigComposer(workspace_root=workspace_root)

    try:
        resolved = composer.compose(path, overrides=overrides)
    except Exception as exc:
        typer.echo(f"Resolution failed for '{path}': {exc}", err=True)
        raise typer.Exit(code=1) from exc

    output_str = yaml.dump(resolved, sort_keys=False)
    if output:
        output.write_text(output_str, encoding="utf-8")
        typer.echo(f"Resolved configuration written to {output}")
    else:
        sys.stdout.write(output_str + "\n")


@config_app.command(name="diff")
def diff_command(
    path1: Annotated[Path, typer.Argument(help="First configuration file.")],
    path2: Annotated[Path, typer.Argument(help="Second configuration file.")],
) -> None:
    """Compare two resolved configurations and display field-by-field differences."""
    workspace_root = Path.cwd()
    composer = ConfigComposer(workspace_root=workspace_root)

    try:
        c1 = composer.compose(path1)
        c2 = composer.compose(path2)
    except Exception as exc:
        typer.echo(f"Failed to load configurations for comparison: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    diffs = diff_configs(c1, c2)
    if not diffs:
        typer.echo("Configurations are identical.")
    else:
        typer.echo(f"Found {len(diffs)} differences:")
        for d in diffs:
            typer.echo(f"  {d}")


@config_app.command(name="schema")
def schema_command(
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            "-k",
            help="Schema to export: 'experiment_draft', 'experiment_plan', or preset name.",
        ),
    ] = "experiment_draft",
) -> None:
    """Export strict JSON Schema for XLM configuration models."""
    from pydantic import BaseModel  # noqa: PLC0415

    schemas: dict[str, type[BaseModel]] = {
        "experiment_draft": ExperimentDraftConfig,
        "experiment_plan": ExecutableExperimentPlanConfig,
        "model_preset": ModelPresetConfig,
        "mixture_preset": MixtureConfig,
    }
    if kind not in schemas:
        typer.echo(f"Unknown schema kind '{kind}'. Choices: {list(schemas.keys())}", err=True)
        raise typer.Exit(code=1)

    schema_dict = schemas[kind].model_json_schema()
    sys.stdout.write(json.dumps(schema_dict, indent=2) + "\n")
