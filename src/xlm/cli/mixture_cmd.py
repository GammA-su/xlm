"""CLI commands for mixture validation, planning, inspection and preview (C07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from xlm.data.exclusion.transport import open_gate
from xlm.data.sampling import (
    MATCHED_BASES,
    MixtureRecipe,
    SourceAvailability,
    compile_exposure_plan,
    compile_matched_plan,
    summarize_plan_blocks,
    validate_mixture,
)
from xlm.data.sources.mix01 import (
    PresetValidationError,
    diff_presets,
    load_mix01_views,
    load_mixture_preset,
    validate_preset_components,
    validate_preset_weights_exact,
)
from xlm.data.tokens import TokenShardReader

mixture_app = typer.Typer(
    name="mixture",
    help="Validate, plan, inspect and preview token mixtures over source shards.",
)


def _load_recipe(path: Path) -> MixtureRecipe:
    """Load a mixture recipe from YAML or JSON."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("mixture recipe file must be a mapping")
    return MixtureRecipe(**data)


def _discover_availability(shard_root: Path) -> dict[str, SourceAvailability]:
    """Read availability from every shard directory under ``shard_root``.

    Availability is measured from the shards themselves, never taken from the recipe:
    a recipe cannot assert how much text a source has.
    """
    availability: dict[str, SourceAvailability] = {}
    if not shard_root.is_dir():
        raise NotADirectoryError(f"shard root not found: {shard_root}")

    for candidate in sorted(shard_root.iterdir()):
        if not (candidate / "shard_manifest.json").is_file():
            continue
        reader = TokenShardReader(candidate)
        if reader.manifest.source_id in availability or len(availability) >= 64:
            raise ValueError("duplicate source shard or source ceiling exceeded")
        counters = reader.counters
        availability[reader.manifest.source_id] = SourceAvailability(
            source_id=reader.manifest.source_id,
            shard_id=reader.manifest.shard_id,
            valid_targets=int(
                counters.get("valid_targets", max(0, reader.manifest.num_tokens - 1))
            ),
            content_tokens=int(counters.get("content_tokens", reader.manifest.num_tokens)),
            eos_tokens=int(counters.get("eos_tokens", 0)),
            canonical_bytes=int(counters.get("canonical_bytes", 0)),
            num_documents=reader.manifest.num_documents,
            token_dtype=reader.manifest.token_dtype,
        )
    if not availability:
        raise FileNotFoundError(f"no token shards found under: {shard_root}")
    return availability


def _c05_shards(root: Path) -> dict[str, Path]:
    return {
        TokenShardReader(p).manifest.source_id: p
        for p in sorted(root.iterdir())
        if (p / "shard_manifest.json").is_file()
    }


def _load_inputs(
    recipe_path: Path, shard_root: Path
) -> tuple[MixtureRecipe, dict[str, SourceAvailability]]:
    try:
        return _load_recipe(recipe_path), _discover_availability(shard_root)
    except (ValueError, TypeError, KeyError, FileNotFoundError, NotADirectoryError) as e:
        typer.echo(f"Error loading mixture inputs: {e}", err=True)
        raise typer.Exit(code=1) from e


@mixture_app.command("validate")
def validate_cmd(
    recipe_path: Annotated[Path, typer.Option("--recipe", "-r", help="Mixture recipe YAML/JSON.")],
    shard_root: Annotated[Path, typer.Option("--shards", "-s", help="Directory of token shards.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the validation as JSON.")] = False,
) -> None:
    """Validate a mixture against the shards that actually exist."""
    recipe, availability = _load_inputs(recipe_path, shard_root)
    result = validate_mixture(recipe, availability)

    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        raise typer.Exit(code=0 if result.is_valid else 1)

    typer.echo("============================================================")
    typer.echo(f"Mixture:   {result.mixture_id}")
    typer.echo(f"Identity:  {result.mixture_identity}")
    typer.echo(f"Valid:     {result.is_valid}")
    for source_id, source in sorted(result.availability.items()):
        typer.echo(
            f"  {source_id:<20} weight {recipe.weight_of(source_id):.4f}  "
            f"{source.valid_targets:>12,} valid targets  {source.num_documents:>8,} docs"
        )
    for error in result.errors:
        typer.echo(f"  ERROR: {error}")
    for warning in result.warnings:
        typer.echo(f"  WARN:  {warning}")
    typer.echo("============================================================")

    if not result.is_valid:
        raise typer.Exit(code=1)


@mixture_app.command("plan")
def plan_cmd(
    recipe_path: Annotated[Path, typer.Option("--recipe", "-r", help="Mixture recipe YAML/JSON.")],
    shard_root: Annotated[Path, typer.Option("--shards", "-s", help="Directory of token shards.")],
    budget_targets: Annotated[
        int, typer.Option("--budget-targets", help="Valid next-token target budget.")
    ],
    output_path: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write the exposure plan.")
    ] = None,
    block_size: Annotated[
        int, typer.Option("--block-size", help="Tokens per exposure block.")
    ] = 8192,
    c05_proof: Annotated[Path | None, typer.Option("--c05-proof")] = None,
) -> None:
    """Compile a deterministic exposure plan for a token budget."""
    recipe, availability = _load_inputs(recipe_path, shard_root)
    validation = validate_mixture(recipe, availability)
    if not validation.is_valid:
        typer.echo("Error: mixture is invalid; cannot plan.", err=True)
        for error in validation.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)

    try:
        with open_gate(c05_proof, consumes=(shard_root,)) as gate:
            plan = compile_exposure_plan(
                recipe,
                validation,
                budget_targets,
                block_size,
                c05_gate=gate,
                c05_shards=_c05_shards(shard_root) if gate else None,
            )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(plan.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

    typer.echo("============================================================")
    typer.echo(f"Plan:            {plan.plan_id}")
    typer.echo(f"Mixture:         {plan.mixture_id} ({plan.mixture_identity})")
    typer.echo(f"Budget:          {plan.budget_targets:,} valid target tokens")
    typer.echo(f"Seeds:           data={plan.data_seed} model={plan.model_seed}")
    typer.echo(f"Frozen order:    {' -> '.join(plan.source_order)}")
    for source_id in plan.source_order:
        projection = plan.projections[source_id]
        typer.echo(
            f"  {source_id:<20} weight {projection.configured_weight:.4f}  "
            f"planned {projection.planned_targets:>12,}  "
            f"unique {projection.unique_targets_available:>12,}  "
            f"repeat {projection.repeated_targets:>10,}  "
            f"epochs {projection.epochs_required:.2f}"
        )
    typer.echo(f"Unique total:    {plan.total_unique_targets:,}")
    typer.echo(f"Repeated total:  {plan.total_repeated_targets:,}")
    typer.echo(f"Shard storage:   {plan.projected_storage_bytes:,} bytes")
    for warning in plan.warnings:
        typer.echo(f"  WARN: {warning}")
    for note in plan.notes:
        typer.echo(f"  NOTE: {note}")
    if output_path is not None:
        typer.echo(f"Output:          {output_path}")
    typer.echo("============================================================")


@mixture_app.command("matched-plan")
def matched_plan_cmd(
    recipe_path: Annotated[Path, typer.Option("--recipe", "-r", help="Mixture recipe YAML/JSON.")],
    shard_root: Annotated[Path, typer.Option("--shards", "-s", help="Directory of token shards.")],
    budget: Annotated[
        int,
        typer.Option(
            "--budget",
            help="Budget in the chosen basis: canonical UTF-8 bytes or document count.",
        ),
    ],
    basis: Annotated[
        str,
        typer.Option("--basis", help=f"Matched basis: {', '.join(MATCHED_BASES)}."),
    ] = "canonical_bytes",
    output_path: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write the matched plan.")
    ] = None,
    c05_proof: Annotated[Path | None, typer.Option("--c05-proof")] = None,
) -> None:
    """Compile a matched-canonical-byte or matched-document plan (C07).

    A matched plan fixes raw-text exposure so a tokenizer comparison changes the token
    budget rather than the amount of source text the model sees.
    """
    recipe, availability = _load_inputs(recipe_path, shard_root)
    validation = validate_mixture(recipe, availability)
    if not validation.is_valid:
        typer.echo("Error: mixture is invalid; cannot compile a matched plan.", err=True)
        for error in validation.errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(code=1)

    try:
        with open_gate(c05_proof, consumes=(shard_root,)) as gate:
            plan = compile_matched_plan(
                recipe,
                validation,
                budget,
                basis=basis,
                c05_gate=gate,
                c05_shards=_c05_shards(shard_root) if gate else None,
            )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(plan.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

    typer.echo("============================================================")
    typer.echo(f"Matched plan:    {plan.plan_id}")
    typer.echo(f"Mixture:         {plan.mixture_id} ({plan.mixture_identity})")
    typer.echo(f"Basis:           {plan.basis}")
    typer.echo(f"Budget:          {plan.budget:,} {plan.basis}")
    for source_id in sorted(plan.projections):
        projection = plan.projections[source_id]
        typer.echo(
            f"  {source_id:<20} weight {projection.configured_weight:.4f}  "
            f"bytes {projection.planned_bytes:>14,}  docs {projection.planned_documents:>10,}  "
            f"targets {projection.planned_targets:>14,}  "
            f"epochs {projection.epochs_required:.2f}"
        )
    typer.echo(f"Planned bytes:   {plan.total_planned_bytes:,}")
    typer.echo(f"Planned docs:    {plan.total_planned_documents:,}")
    typer.echo(f"Derived targets: {plan.total_planned_targets:,} (token budget for this tokenizer)")
    for warning in plan.warnings:
        typer.echo(f"  WARN: {warning}")
    for note in plan.notes:
        typer.echo(f"  NOTE: {note}")
    if output_path is not None:
        typer.echo(f"Output:          {output_path}")
    typer.echo("============================================================")


@mixture_app.command("preset-validate")
def preset_validate_cmd(
    preset_path: Annotated[Path, typer.Option("--preset", "-p", help="Mixture preset YAML.")],
    views_path: Annotated[
        Path, typer.Option("--views", "-v", help="Mix01 view registry YAML.")
    ] = Path("recipes/mixtures/mix01_views.yaml"),
    as_json: Annotated[bool, typer.Option("--json", help="Emit the verdict as JSON.")] = False,
) -> None:
    """Validate a mix01 mixture preset: exact weight sum, known views, no fallback (C04)."""
    try:
        preset = load_mixture_preset(preset_path)
        registry = load_mix01_views(views_path)
        total = validate_preset_weights_exact(preset)
        validate_preset_components(preset, registry)
    except (ValueError, TypeError, KeyError, FileNotFoundError) as e:
        typer.echo(f"Error: invalid preset: {e}", err=True)
        raise typer.Exit(code=1) from e

    verdict = {
        "preset_id": preset.id,
        "preset_identity": preset.identity(),
        "weight_sum_exact": str(total),
        "components": sorted(preset.weights),
        "valid": True,
    }
    if as_json:
        typer.echo(json.dumps(verdict, indent=2, sort_keys=True))
        return
    typer.echo("============================================================")
    typer.echo(f"Preset:          {preset.id} ({preset.identity()})")
    typer.echo(f"Weight sum:      exactly {total}")
    typer.echo(f"Components:      {len(preset.weights)} declared mix01 views")
    typer.echo("Valid:           True (static checks; run gating needs admission evidence)")
    typer.echo("============================================================")


@mixture_app.command("preset-diff")
def preset_diff_cmd(
    base_path: Annotated[Path, typer.Option("--base", "-b", help="Base preset YAML (M0).")],
    variant_path: Annotated[
        Path, typer.Option("--variant", help="Variant preset YAML (M1-M5 or no-IFM).")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the diff as JSON.")] = False,
) -> None:
    """Show the structured weight diff between two mixture treatments."""
    try:
        base = load_mixture_preset(base_path)
        variant = load_mixture_preset(variant_path)
        validate_preset_weights_exact(base)
        validate_preset_weights_exact(variant)
        diff = diff_presets(base, variant)
    except (ValueError, TypeError, KeyError, FileNotFoundError, PresetValidationError) as e:
        typer.echo(f"Error: cannot diff presets: {e}", err=True)
        raise typer.Exit(code=1) from e

    if as_json:
        typer.echo(json.dumps(diff.to_dict(), indent=2, sort_keys=True))
        return
    typer.echo("============================================================")
    typer.echo(f"Base:            {diff.base_id} ({diff.base_identity})")
    typer.echo(f"Variant:         {diff.variant_id} ({diff.variant_identity})")
    for component_id in sorted(set(diff.added) | set(diff.removed) | set(diff.changed)):
        delta = diff.weight_delta(component_id)
        typer.echo(f"  {component_id:<36} delta {delta:+.4f}")
    typer.echo(f"Unchanged:       {len(diff.unchanged)} component(s)")
    typer.echo("============================================================")


@mixture_app.command("inspect")
def inspect_cmd(
    plan_path: Annotated[Path, typer.Option("--plan", "-p", help="Exposure plan JSON to inspect.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit the plan as JSON.")] = False,
) -> None:
    """Inspect a compiled exposure plan."""
    if not plan_path.is_file():
        typer.echo(f"Error: plan '{plan_path}' not found.", err=True)
        raise typer.Exit(code=1)

    payload: dict[str, Any] = json.loads(plan_path.read_text(encoding="utf-8"))
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo("============================================================")
    typer.echo(f"Plan:            {payload['plan_id']}")
    typer.echo(f"Mixture:         {payload['mixture_id']}")
    typer.echo(f"Budget:          {payload['budget_targets']:,} valid target tokens")
    typer.echo(f"Requires repeat: {payload['requires_repetition']}")
    typer.echo(f"Frozen order:    {' -> '.join(payload['source_order'])}")
    for source_id in payload["source_order"]:
        projection = payload["projections"][source_id]
        typer.echo(
            f"  {source_id:<20} weight {projection['configured_weight']:.4f}  "
            f"planned {projection['planned_targets']:>12,}  "
            f"repeat {projection['repeated_targets']:>10,}"
        )
    for warning in payload.get("warnings", []):
        typer.echo(f"  WARN: {warning}")
    typer.echo("============================================================")


@mixture_app.command("preview")
def preview_cmd(
    recipe_path: Annotated[Path, typer.Option("--recipe", "-r", help="Mixture recipe YAML/JSON.")],
    shard_root: Annotated[Path, typer.Option("--shards", "-s", help="Directory of token shards.")],
    budget_targets: Annotated[
        int, typer.Option("--budget-targets", help="Valid next-token target budget.")
    ],
    max_blocks: Annotated[
        int, typer.Option("--max-blocks", help="Bound on previewed blocks.")
    ] = 500,
    block_size: Annotated[
        int, typer.Option("--block-size", help="Tokens per exposure block.")
    ] = 8192,
    c05_proof: Annotated[Path | None, typer.Option("--c05-proof")] = None,
) -> None:
    """Preview the observed source shares a plan would produce."""
    recipe, availability = _load_inputs(recipe_path, shard_root)
    validation = validate_mixture(recipe, availability)
    if not validation.is_valid:
        typer.echo("Error: mixture is invalid; cannot preview.", err=True)
        raise typer.Exit(code=1)

    with open_gate(c05_proof, consumes=(shard_root,)) as gate:
        plan = compile_exposure_plan(
            recipe,
            validation,
            budget_targets,
            block_size,
            c05_gate=gate,
            c05_shards=_c05_shards(shard_root) if gate else None,
        )
    summary = summarize_plan_blocks(plan, availability, max_blocks=max_blocks)

    typer.echo("============================================================")
    typer.echo(f"Plan:            {plan.plan_id}")
    typer.echo(
        f"Previewed:       {summary['blocks_previewed']:,} blocks, "
        f"{summary['targets_previewed']:,} target tokens"
        + (" (TRUNCATED)" if summary["is_truncated_preview"] else "")
    )
    for source_id in plan.source_order:
        observed = summary["observed_shares"][source_id]
        configured = summary["configured_weights"][source_id]
        typer.echo(
            f"  {source_id:<20} configured {configured:.4f}  observed {observed:.4f}  "
            f"drift {abs(observed - configured):+.5f}"
        )
    typer.echo(f"Max drift:       {summary['max_observed_drift']:.5f}")
    typer.echo(f"Drift bound:     {recipe.max_share_drift:.5f}")
    typer.echo(
        "Preview reflects the same deficit rule the runtime scheduler uses; it is a "
        "projection, not a measurement of a completed run."
    )
    typer.echo("============================================================")
