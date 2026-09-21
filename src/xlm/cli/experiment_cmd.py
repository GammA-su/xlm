"""CLI for immutable experiment planning, bounded queue and campaigns (P16)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from xlm.core.paths import ArtifactPaths

if TYPE_CHECKING:
    from xlm.experiments.queue import ExperimentQueue

experiment_app = typer.Typer(
    name="experiment", help="Plan, authorize and submit immutable experiments."
)
queue_app = typer.Typer(name="queue", help="Run and inspect the bounded local queue.")
campaign_app = typer.Typer(name="campaign", help="Plan mixture-search campaigns.")


def _workspace() -> Path:
    return Path.cwd()


def _artifacts() -> ArtifactPaths:
    return ArtifactPaths.from_env()


def _queue(tree_root: Path | None = None) -> ExperimentQueue:
    from xlm.artifacts.ledger import RunLedger
    from xlm.experiments.queue import ExperimentQueue

    paths = ArtifactPaths.from_env()
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    return ExperimentQueue(ledger, paths, tree_root=tree_root or Path.cwd())


@experiment_app.command("plan")
def experiment_plan_cmd(
    draft: Annotated[Path, typer.Argument(help="Experiment draft YAML.")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write the resolved plan JSON.")
    ] = None,
    snapshot_dir: Annotated[
        Path | None, typer.Option("--snapshot-dir", help="Code snapshot output directory.")
    ] = None,
    profile_path: Annotated[
        Path | None,
        typer.Option(
            "--profile",
            help="Produced profile.json whose measured identity is validated against "
            "this plan and bound to the draft's resources.profile_artifact.",
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the plan as JSON.")] = False,
    smoke: Annotated[
        bool,
        typer.Option(
            "--smoke",
            help="Use C13 tiny-model smoke caps; no production/profile/evaluation readiness claim.",
        ),
    ] = False,
) -> None:
    """Resolve a draft into an immutable plan. Read-only apart from artifacts."""
    from xlm.experiments.plans import resolve_experiment_plan

    measured_profile = None
    if profile_path is not None:
        if profile_path.stat().st_size > 1024**2:
            typer.echo("Error: profile JSON exceeds 1 MiB.", err=True)
            raise typer.Exit(code=1)
        try:
            measured_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:
            typer.echo(f"Error: cannot read profile JSON: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        if not isinstance(measured_profile, dict):
            typer.echo("Error: profile JSON must be a mapping.", err=True)
            raise typer.Exit(code=1)

    try:
        plan = resolve_experiment_plan(
            draft,
            workspace_root=_workspace(),
            artifact_paths=_artifacts(),
            snapshot_dir=snapshot_dir,
            smoke=smoke,
            measured_profile=measured_profile,
        )
    except Exception as exc:
        typer.echo(f"Error: cannot resolve plan: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output is not None:
        plan.save(output)
    if as_json:
        typer.echo(json.dumps(plan.to_dict(), indent=2, sort_keys=True, default=str))
        return

    typer.echo("============================================================")
    typer.echo(f"Plan:            {plan.plan_id}")
    typer.echo(f"Hash:            {plan.plan_hash}")
    typer.echo(f"Track:           {plan.track} [{plan.horizon_kind}]")
    typer.echo(f"Budget:          {plan.budget_valid_targets:,} valid targets")
    typer.echo(
        f"Code:            {plan.code_snapshot.code_hash[:16]} "
        f"({plan.code_snapshot.total_bytes:,} bytes)"
    )
    typer.echo(f"Dependencies:    {plan.dependency_hash[:16]}")
    typer.echo(f"Cost basis:      {plan.cost_estimate.get('basis')}")
    typer.echo(f"Storage est:     {plan.storage_estimate_gib} GiB")
    if plan.blockers:
        typer.echo("Blockers:")
        for blocker in plan.blockers:
            typer.echo(f"  - [{blocker.code}] {blocker.detail}")
    else:
        typer.echo("Blockers:        none")
    if output is not None:
        typer.echo(f"Output:          {output}")
    typer.echo("============================================================")


@experiment_app.command("authorize")
def experiment_authorize_cmd(
    plan_hash: Annotated[str, typer.Option("--plan-hash", help="Plan hash to authorize.")],
    max_targets: Annotated[int, typer.Option("--max-targets", help="Target budget cap.")],
    approver: Annotated[str, typer.Option("--approver", help="Named approver.")],
    ticket_id: Annotated[str, typer.Option("--ticket-id", help="Ticket identifier.")],
    max_seconds: Annotated[float | None, typer.Option("--max-seconds")] = None,
    max_disk_gib: Annotated[float | None, typer.Option("--max-disk-gib")] = None,
    output: Annotated[Path, typer.Option("--output", "-o", help="Ticket output path.")] = Path(
        "ticket.json"
    ),
) -> None:
    """Issue an operator ticket bound to one plan hash and explicit limits."""
    from xlm.experiments.authorization import issue_ticket, save_ticket

    try:
        ticket = issue_ticket(
            plan_hash=plan_hash,
            max_valid_targets=max_targets,
            approver=approver,
            ticket_id=ticket_id,
            max_train_seconds=max_seconds,
            max_new_disk_gib=max_disk_gib,
        )
        save_ticket(ticket, output)
    except Exception as exc:
        typer.echo(f"Error: cannot issue ticket: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Ticket '{ticket_id}' issued for plan '{plan_hash[:12]}' -> {output}")


@experiment_app.command("submit")
def experiment_submit_cmd(
    plan_file: Annotated[Path, typer.Argument(help="Resolved plan JSON from 'experiment plan'.")],
    snapshot_dir: Annotated[
        Path, typer.Option("--snapshot-dir", help="Snapshot directory.")
    ] = Path("snapshots"),
    device: Annotated[str, typer.Option("--device", help="Execution device.")] = "cpu",
    ticket: Annotated[
        Path | None, typer.Option("--ticket", help="Authorization ticket file.")
    ] = None,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 0,
    allow_duplicate: Annotated[bool, typer.Option("--allow-duplicate")] = False,
) -> None:
    """Validate authorization and enqueue a plan. Identical plans deduplicate."""
    from xlm.experiments.authorization import (
        AuthorizationError,
        load_ticket,
        validate_against_ticket,
    )
    from xlm.experiments.plans import ExecutablePlan
    from xlm.experiments.queue import QueueError

    try:
        plan = ExecutablePlan.load(plan_file)
    except Exception as exc:
        typer.echo(f"Error: cannot load plan: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if plan.blockers:
        typer.echo(
            "Error: plan carries approval blockers: "
            + "; ".join(f"{b.code}: {b.detail}" for b in plan.blockers),
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        ticket_obj = load_ticket(ticket) if ticket is not None else None
        effective = validate_against_ticket(
            {
                "plan_hash": plan.plan_hash,
                "budget_valid_targets": plan.budget_valid_targets,
                "budget_max_seconds": plan.budget_max_seconds,
                "estimated_new_disk_gib": plan.storage_estimate_gib,
                "gpu_processes": plan.gpu_processes,
            },
            ticket_obj,
        )
    except AuthorizationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    queue = _queue()
    try:
        job_id, duplicate = queue.submit(
            plan,
            plan_file,
            snapshot_dir,
            device,
            authorization_token=(f"{effective.ticket_id}:{effective.plan_hash[:12]}"),
            max_retries=max_retries,
            allow_duplicate=allow_duplicate,
            authorization=effective,
        )
    except QueueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Job '{job_id}' enqueued for device '{device}'"
        + (" (duplicate of identical plan)" if duplicate else "")
    )


@queue_app.command("run")
def queue_run_cmd(
    device: Annotated[
        str | None, typer.Option("--device", help="Only run jobs for this device.")
    ] = None,
    once: Annotated[bool, typer.Option("--once", help="Execute at most one job.")] = False,
    max_jobs: Annotated[int, typer.Option("--max-jobs", help="Bound on jobs executed.")] = 100,
) -> None:
    """Execute authorized jobs sequentially until the queue drains or a lease blocks."""
    queue = _queue()
    recovered = queue.recover_stale_jobs()
    if recovered:
        typer.echo(f"Recovered stale jobs: {', '.join(recovered)}")
    executed = 0
    while executed < max_jobs:
        result = queue.run_next(device)
        if not result.get("ran"):
            typer.echo(f"Queue idle: {result.get('reason')}")
            break
        executed += 1
        typer.echo(f"Job '{result['job_id']}' finished: {result.get('state')}")
        if once:
            break
    typer.echo(f"Executed {executed} job(s).")


@queue_app.command("status")
def queue_status_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Emit status as JSON.")] = False,
) -> None:
    """List queued jobs with states, attempts and heartbeat ages."""
    queue = _queue()
    jobs = queue.list_jobs()
    if as_json:
        typer.echo(json.dumps([j.to_dict() for j in jobs], indent=2, sort_keys=True, default=str))
        return
    typer.echo("============================================================")
    if not jobs:
        typer.echo("Queue is empty.")
    for job in jobs:
        typer.echo(
            f"  {job.job_id:<28} {job.state:<14} device={job.device:<8} "
            f"attempts={job.attempts_made}/{job.max_retries}"
        )
        if job.last_reason:
            typer.echo(f"    reason: {job.last_reason.splitlines()[0][:100]}")
    typer.echo("============================================================")


@queue_app.command("cancel")
def queue_cancel_cmd(
    job_id: Annotated[str, typer.Argument(help="Job to cancel.")],
) -> None:
    """Cancel a queued or running job."""
    from xlm.experiments.queue import QueueError

    queue = _queue()
    try:
        job = queue.cancel(job_id)
    except QueueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Job '{job_id}' is {job.state}.")


@campaign_app.command("plan")
def campaign_plan_cmd(
    draft: Annotated[Path, typer.Argument(help="Campaign draft YAML.")],
    selections: Annotated[
        Path | None, typer.Option("--selections", help="Operator selection file JSON.")
    ] = None,
    throughput: Annotated[
        str | None,
        typer.Option("--throughput", help="Measured tok/s range 'lo,hi' for cost basis."),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the campaign plan as JSON.")
    ] = False,
) -> None:
    """Expand a campaign into complete trial plans. Planning only; nothing runs."""
    import json as _json

    from xlm.experiments.campaigns import expand_campaign

    selection_map: dict[str, list[str]] | None = None
    if selections is not None:
        selection_map = {
            str(k): [str(m) for m in v]
            for k, v in _json.loads(selections.read_text(encoding="utf-8")).items()
        }
    throughput_range: tuple[float, float] | None = None
    if throughput:
        lo, hi = (float(x) for x in throughput.split(","))
        throughput_range = (lo, hi)

    try:
        plan = expand_campaign(
            draft, _workspace(), selections=selection_map, throughput_range=throughput_range
        )
    except Exception as exc:
        typer.echo(f"Error: cannot expand campaign: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(plan.to_dict(), indent=2, sort_keys=True, default=str))
        return
    typer.echo("============================================================")
    typer.echo(f"Campaign:        {plan.campaign_id}")
    typer.echo(f"Trials:          {plan.total_trials}")
    typer.echo(f"Total targets:   {plan.total_valid_targets:,}")
    typer.echo(f"Tokens by size:  {json.dumps(plan.tokens_by_size, sort_keys=True)}")
    typer.echo(f"Cost basis:      {plan.cost_basis}")
    if plan.eta_seconds_range:
        typer.echo(
            f"ETA range:       {plan.eta_seconds_range[0]:,.0f}s .. "
            f"{plan.eta_seconds_range[1]:,.0f}s"
        )
    mixtures = sorted({t.mixture_preset for t in plan.trials})
    typer.echo(f"Mixtures ({len(mixtures)}):  {', '.join(mixtures)}")
    blocked = sum(1 for t in plan.trials if t.blocked_reason)
    typer.echo(f"Blocked trials:  {blocked}")
    if plan.blockers:
        for blocker in plan.blockers[:8]:
            typer.echo(f"  - {blocker[:120]}")
    typer.echo("Nothing was executed; trials are plans until authorized.")
    typer.echo("============================================================")
