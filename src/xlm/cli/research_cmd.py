"""CLI for the research idea workflow and plugin scaffolding (P18, A33)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

research_app = typer.Typer(name="research", help="Research ideas and plugin scaffolds.")
idea_app = typer.Typer(name="idea", help="Create and validate research idea cards.")
research_app.add_typer(idea_app, name="idea")


@idea_app.command("new")
def idea_new_cmd(
    idea_id: Annotated[str, typer.Option("--id", help="Idea identifier.")],
    title: Annotated[str, typer.Option("--title", help="Idea title.")] = "",
    category: Annotated[
        str, typer.Option("--category", help="architecture|objective|optimizer|tokenizer.")
    ] = "",
    output: Annotated[Path, typer.Option("--output", "-o", help="Card output path.")] = Path(
        "idea.yaml"
    ),
) -> None:
    """Create a blank proposed idea card. Validation fails until it is filled."""
    from xlm.research.ideas import blank_card, save_card, validate_card

    try:
        card = blank_card(idea_id, title=title, category=category)
    except Exception as exc:
        typer.echo(f"Error: cannot create idea card: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    save_card(card, output)
    problems = validate_card(card)
    typer.echo(
        f"Idea card '{idea_id}' written to {output} "
        f"({len(problems)} open fields; fill them, then validate)."
    )
    typer.echo(
        "Follow CHANGE_EXPERIMENT_TEMPLATE.md for the idea-patch-smoke-"
        "matched-experiment-comparison chain."
    )


@idea_app.command("validate")
def idea_validate_cmd(
    card: Annotated[Path, typer.Argument(help="Idea card YAML to validate.")],
) -> None:
    """Validate an idea card. Missing definitions and novelty claims fail."""
    from xlm.research.ideas import validate_card_file

    problems = validate_card_file(card)
    if problems:
        typer.echo(f"Idea card '{card}' is INVALID:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)
    typer.echo(f"Idea card '{card}' is valid.")


@research_app.command("scaffold")
def scaffold_cmd(
    category: Annotated[str, typer.Option("--category", help="Plugin category.")],
    name: Annotated[str, typer.Option("--name", help="Plugin name.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", "-o")] = Path("scaffolds"),
) -> None:
    """Generate a disabled-by-default plugin scaffold for one category."""
    from xlm.research.scaffold import ScaffoldError, write_scaffold

    try:
        written = write_scaffold(name, category, output_dir)
    except ScaffoldError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Scaffold '{name}' ({category}) written, DISABLED by default:")
    for path in written:
        typer.echo(f"  {path}")
    typer.echo("It fails explicitly until implemented; it is not a working mechanism.")


@research_app.command("check-plugin")
def check_plugin_cmd(
    plugin_dir: Annotated[Path, typer.Argument(help="Plugin directory to verify.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the verdict as JSON.")] = False,
) -> None:
    """Verify manifest, file scope, capabilities and registration of one plugin.

    Registration is verified against isolated registries: the check proves the
    seam works without polluting (or colliding with) the global registries.
    """
    from xlm.core.registry import Registry
    from xlm.research.loader import PluginError, load_manifest, load_plugin

    try:
        manifest = load_manifest(plugin_dir)
        isolated: dict[str, Registry[Any]] = {
            kind: Registry(kind)
            for kind in (
                "architecture",
                "objective",
                "optimizer",
                "tokenizer",
                "source_adapter",
                "transform",
                "schedule",
            )
        }
        result = load_plugin(plugin_dir, isolated)
    except PluginError as exc:
        typer.echo(f"Plugin check FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    verdict = {
        "plugin_id": result.plugin_id,
        "version": result.version,
        "category": result.category,
        "registered_key": result.registered_key,
        "files": result.files,
        "novelty": result.novelty,
        "manifest_enabled": manifest.enabled,
    }
    if as_json:
        typer.echo(json.dumps(verdict, indent=2, sort_keys=True))
        return
    typer.echo("============================================================")
    typer.echo(f"Plugin:          {result.plugin_id} v{result.version} ({result.category})")
    typer.echo(f"Registered:      {result.registered_key}")
    typer.echo(f"Files verified:  {len(result.files)}")
    typer.echo(f"Novelty label:   {result.novelty or '(none)'}")
    typer.echo("============================================================")
