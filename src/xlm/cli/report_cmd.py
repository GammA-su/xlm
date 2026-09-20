"""CLI for offline reports, run listing and the optional dashboard (P19, A34)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

runs_app = typer.Typer(name="runs", help="List recorded experiment runs.")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: cannot read {label} '{path}': {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(data, dict):
        typer.echo(f"Error: {label} '{path}' must be a JSON mapping.", err=True)
        raise typer.Exit(code=1)
    return data


def _write_output(text: str, output: Path | None, suffix: str) -> Path | None:
    if output is None:
        typer.echo(text)
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    target = output if output.suffix else output.with_suffix(suffix)
    target.write_text(text, encoding="utf-8")
    typer.echo(f"Report written to {target}")
    return target


def report_command(
    run: Annotated[
        str | None, typer.Option("--run", help="Run ID from the runs directory.")
    ] = None,
    run_record: Annotated[Path | None, typer.Option("--run-record")] = None,
    campaign: Annotated[Path | None, typer.Option("--campaign", help="Campaign plan JSON.")] = None,
    data_inputs: Annotated[
        Path | None, typer.Option("--data-inputs", help="Data fragments JSON.")
    ] = None,
    evidence_dir: Annotated[Path | None, typer.Option("--evidence-dir")] = None,
    comparison_dir: Annotated[Path | None, typer.Option("--comparison-dir")] = None,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    format: Annotated[str, typer.Option("--format", help="json|csv|md|html.")] = "md",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Render an offline report from authoritative run, campaign or dataset data."""
    from xlm.core.paths import ArtifactPaths
    from xlm.reports import collect as collect_mod
    from xlm.reports import render as render_mod

    chosen = [x for x in (run, run_record, campaign, data_inputs) if x is not None]
    if len(chosen) != 1:
        typer.echo(
            "Error: pass exactly one of --run, --run-record, --campaign, --data-inputs.", err=True
        )
        raise typer.Exit(code=1)
    if format not in ("json", "csv", "md", "html"):
        typer.echo(f"Error: unknown format '{format}'.", err=True)
        raise typer.Exit(code=1)

    home_runs = runs_dir or (ArtifactPaths.from_env().runs)
    kind = "run"
    if run is not None:
        from xlm.artifacts.ledger import RunLedger

        paths = ArtifactPaths.from_env()
        ledger = RunLedger(paths.ledger / "ledger.sqlite")
        report = collect_mod.collect_run_report(
            run, home_runs, ledger, evidence_dir, comparison_dir
        )
        payload = report.to_dict()
    elif run_record is not None:
        record = _read_json(run_record, "run record")
        home = record.get("_runs_dir")
        report = collect_mod.collect_run_report(
            record.get("run_id", run_record.parent.name),
            Path(home) if home else run_record.parent.parent,
            None,
            evidence_dir,
            comparison_dir,
        )
        payload = report.to_dict()
    elif campaign is not None:
        kind = "campaign"
        plan = _read_json(campaign, "campaign plan")
        payload = collect_mod.collect_campaign_report(plan).to_dict()
    else:
        kind = "data"
        fragments = _read_json(data_inputs, "data inputs")  # type: ignore[arg-type]
        payload = collect_mod.collect_data_report(fragments).to_dict()

    if format == "json":
        text = render_mod.to_json(payload)
        suffix = ".json"
    elif format == "csv":
        if kind == "run":
            rows = render_mod.run_metrics_rows(payload)
            text = render_mod.metrics_to_csv(
                rows, ["run_id", "section", "metric", "value", "unit", "provenance"]
            )
        elif kind == "campaign":
            rows = [
                {
                    "campaign": payload.get("campaign_id"),
                    "trial": t.get("trial_id"),
                    "model": t.get("model_preset"),
                    "budget": t.get("budget_valid_targets"),
                    "blocked": bool(t.get("blocked_reason")),
                }
                for t in payload.get("trials", [])
                if isinstance(t, dict)
            ]
            text = render_mod.metrics_to_csv(
                rows, ["campaign", "trial", "model", "budget", "blocked"]
            )
        else:
            rows = [
                {"section": section, "detail": json.dumps(value, sort_keys=True, default=str)[:200]}
                for section, value in payload.items()
                if section not in ("report_version", "missing", "previews")
            ]
            text = render_mod.metrics_to_csv(rows, ["section", "detail"])
        suffix = ".csv"
    elif format == "md":
        if kind == "run":
            text = render_mod.to_markdown_run(payload)
        elif kind == "campaign":
            text = render_mod.to_markdown_campaign(payload)
        else:
            text = render_mod.to_markdown_data(payload)
        suffix = ".md"
    else:
        if kind == "run":
            text = render_mod.to_html_run(payload)
        elif kind == "campaign":
            text = render_mod.to_html_campaign(payload)
        else:
            text = render_mod.to_html_data(payload)
        suffix = ".html"
    _write_output(text, output, suffix)


@runs_app.command("list")
def runs_list_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Emit runs as JSON.")] = False,
) -> None:
    """List recorded runs with their ledger states."""
    from xlm.artifacts.ledger import RunLedger
    from xlm.core.paths import ArtifactPaths
    from xlm.experiments.queue import ExperimentQueue

    paths = ArtifactPaths.from_env()
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    queue = ExperimentQueue(ledger, paths)
    jobs = queue.list_jobs()
    ledger_runs = [ledger.get_run(job.job_id) for job in jobs]
    rows = []
    for job, row in zip(jobs, ledger_runs, strict=True):
        rows.append(
            {
                "job_id": job.job_id,
                "experiment": job.experiment_id,
                "queue_state": job.state,
                "ledger_state": (row or {}).get("status"),
                "device": job.device,
                "attempts": f"{job.attempts_made}/{job.max_retries}",
            }
        )
    if as_json:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True, default=str))
        return
    if not rows:
        typer.echo("No runs recorded.")
        return
    typer.echo("job | experiment | queue state | ledger | device | attempts")
    for row in rows:
        typer.echo(
            f"{row['job_id']} | {row['experiment']} | {row['queue_state']} | "
            f"{row['ledger_state']} | {row['device']} | {row['attempts']}"
        )


def dashboard_command(
    port: Annotated[int, typer.Option("--port", help="Port to serve (0 selects one).")] = 8765,
    host: Annotated[
        str, typer.Option("--host", help="Bind host (loopback by default).")
    ] = "127.0.0.1",
    deployment_config: Annotated[
        Path | None, typer.Option("--deployment-config", help="Authenticated deployment config.")
    ] = None,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    evidence_dir: Annotated[Path | None, typer.Option("--evidence-dir")] = None,
    comparison_dir: Annotated[Path | None, typer.Option("--comparison-dir")] = None,
    campaigns_dir: Annotated[Path | None, typer.Option("--campaigns-dir")] = None,
    data_inputs: Annotated[Path | None, typer.Option("--data-inputs")] = None,
) -> None:
    """Serve the read-only local dashboard. Loopback only unless explicitly deployed."""
    from xlm.core.paths import ArtifactPaths
    from xlm.dashboard.server import DashboardConfigError, DashboardServer, build_data_provider
    from xlm.reports import collect as collect_mod

    paths = ArtifactPaths.from_env()
    home_runs = runs_dir or paths.runs
    runs: dict[str, Any] = {}
    if home_runs.is_dir():
        for child in sorted(home_runs.iterdir()):
            if not child.is_dir():
                continue
            try:
                runs[child.name] = collect_mod.collect_run_report(
                    child.name, home_runs, None, evidence_dir, comparison_dir
                ).to_dict()
            except Exception as exc:  # noqa: BLE001 - one bad run must not kill the board
                runs[child.name] = {
                    "run_id": child.name,
                    "status": "unreadable",
                    "missing": [str(exc)],
                }
    campaigns: dict[str, Any] = {}
    if campaigns_dir is not None and campaigns_dir.is_dir():
        for plan_file in sorted(campaigns_dir.glob("*.json")):
            try:
                plan = json.loads(plan_file.read_text(encoding="utf-8"))
                campaigns[plan_file.stem] = collect_mod.collect_campaign_report(plan).to_dict()
            except (OSError, json.JSONDecodeError):
                continue
    data_report = None
    if data_inputs is not None:
        try:
            fragments = json.loads(data_inputs.read_text(encoding="utf-8"))
            data_report = collect_mod.collect_data_report(fragments).to_dict()
        except (OSError, json.JSONDecodeError) as exc:
            typer.echo(f"Error: cannot read data inputs: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    try:
        server = DashboardServer(
            build_data_provider(runs, campaigns, data_report),
            host=host,
            port=port,
            deployment_config=deployment_config,
        )
    except DashboardConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    server.start()
    typer.echo(f"Dashboard serving read-only at {server.url} (Ctrl+C to stop).")
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
