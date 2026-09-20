"""CLI for staged data preparation with dry planning (P22)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from xlm.prepare.config import load_prepare_config
from xlm.prepare.planner import PlanError, find_repo_root, plan_prepare
from xlm.prepare.runner import PrepareRunError, load_prior_state, run_prepare


def _parse_defines(defines: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in defines or []:
        if "=" not in item:
            typer.echo(f"Error: --define entries must be KEY=VALUE, got '{item}'.", err=True)
            raise typer.Exit(code=1)
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def prepare_command(
    config: Annotated[Path, typer.Option("--config", "-c", help="Prepare config YAML.")] = Path(
        "recipes/prepare/offline_toy.yaml"
    ),
    plan_only: Annotated[
        bool, typer.Option("--plan-only", help="Evaluate stages without executing.")
    ] = False,
    authorize: Annotated[
        bool, typer.Option("--authorize", help="Explicitly authorize execution.")
    ] = False,
    define: Annotated[
        list[str] | None,
        typer.Option("--define", "-d", help="Config override as KEY=VALUE (repeatable)."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rebuild verified stages (recorded, never silent).")
    ] = False,
    only: Annotated[
        str | None,
        typer.Option("--only", help="Comma-separated stage subset to consider."),
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-stage timeout in seconds.")
    ] = 1200.0,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the plan/result as JSON.")] = False,
) -> None:
    """Plan or execute staged data preparation with reuse and resume."""
    import json as _json

    from xlm.core.paths import ArtifactPaths

    try:
        cfg = load_prepare_config(config, _parse_defines(define))
    except Exception as exc:
        typer.echo(f"Error: cannot load prepare config: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        repo_root = find_repo_root(config.resolve().parent)
    except PlanError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    home = ArtifactPaths.from_env().root

    if plan_only:
        try:
            from xlm.prepare.planner import build_variable_mapping

            variables = build_variable_mapping(cfg, config.resolve(), home, repo_root)
            prior = load_prior_state(variables["output_root"])
            plan = plan_prepare(cfg, config.resolve(), home, repo_root, prior)
        except PlanError as exc:
            typer.echo(f"Error: cannot evaluate plan: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if as_json:
            typer.echo(_json.dumps(plan.to_dict(), indent=2, sort_keys=True))
            return
        typer.echo("============================================================")
        typer.echo(f"Prepare plan:    {plan.config_id} (dry run, nothing executed)")
        typer.echo(f"Output root:    {plan.output_root}")
        for stage in plan.stages:
            typer.echo(f"  [{stage.status.upper():<10}] {stage.stage_id:<18} {stage.rerun_because}")
            for reason in stage.reasons:
                typer.echo(f"      - {reason}")
        typer.echo(f"Counts:          {plan.to_dict()['counts']}")
        typer.echo("============================================================")
        return

    only_list = [s.strip() for s in only.split(",") if s.strip()] if only else None
    try:
        result = run_prepare(
            cfg,
            config.resolve(),
            home,
            repo_root,
            authorize=authorize,
            force=force,
            only=only_list,
            stage_timeout_seconds=timeout,
        )
    except PrepareRunError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(_json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return
    typer.echo("============================================================")
    typer.echo(f"Prepare run:     {result.config_id} (authorized, state: {result.state_path})")
    for record in result.stages:
        typer.echo(
            f"  [{record.status.upper():<10}] {record.stage_id:<18} {record.action} - {record.note}"
        )
    typer.echo("============================================================")


def maintenance_cleanup_command(
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--apply", help="List only, or actually remove.")
    ] = True,
    max_bytes: Annotated[
        int,
        typer.Option("--max-bytes", help="Refuse to remove more than this."),
    ] = 10 * 1024**3,
) -> None:
    """List (default) or remove unreachable scratch data under XLM_HOME.

    Reachability is conservative: only staging areas, worker scratch and files
    with no ledger or manifest reference are candidates, and --apply refuses
    above --max-bytes. Anything ambiguous is listed for manual review, never
    deleted.
    """
    from xlm.core.paths import ArtifactPaths

    root = ArtifactPaths.from_env().root
    if not root.is_dir():
        typer.echo(f"Error: artifact root '{root}' does not exist.", err=True)
        raise typer.Exit(code=1)

    removable: list[dict[str, object]] = []
    review: list[str] = []
    total = 0
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file():
            continue
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = relative.split("/")
        if parts[0] in (".staging", "staging", "workers", "scratch", "tmp"):
            size = candidate.stat().st_size
            removable.append({"path": relative, "bytes": size, "reason": "scratch area"})
            total += size
        elif relative.endswith(".tmp") or ".tmp." in relative:
            size = candidate.stat().st_size
            removable.append({"path": relative, "bytes": size, "reason": "temporary file"})
            total += size
        elif parts[0] in ("ledger",):
            review.append(relative)

    if dry_run:
        typer.echo(f"Dry run: {len(removable)} removable file(s), {total:,} bytes.")
        for entry in removable[:20]:
            typer.echo(f"  - {entry['path']} ({entry['bytes']} bytes, {entry['reason']})")
        if len(removable) > 20:
            typer.echo(f"  ... and {len(removable) - 20} more")
        typer.echo("Nothing removed. Re-run with --apply to remove (bounded).")
        return
    if total > max_bytes:
        typer.echo(
            f"Error: {total:,} bytes exceed --max-bytes {max_bytes:,}; refusing.",
            err=True,
        )
        raise typer.Exit(code=1)
    removed = 0
    for entry in removable:
        target = root / str(entry["path"])
        try:
            target.unlink()
            removed += 1
        except OSError as exc:
            typer.echo(f"Warning: could not remove {entry['path']}: {exc}", err=True)
    typer.echo(f"Removed {removed} file(s), {total:,} bytes.")
