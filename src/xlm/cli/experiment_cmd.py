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
    bindings: Annotated[
        Path | None,
        typer.Option(
            "--bindings",
            help="Science pilot drafts only: operator bindings of local artifacts (JSON).",
        ),
    ] = None,
    review: Annotated[
        Path | None,
        typer.Option("--review", help="Science pilot drafts only: write the review JSON here."),
    ] = None,
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

    from xlm.config.composer import ConfigComposer

    try:
        pilot_draft = "science_pilot" in ConfigComposer(_workspace()).compose(draft)
    except Exception:  # noqa: BLE001 - the generic path reports its own resolution error
        pilot_draft = False
    if pilot_draft:
        _plan_science_pilot(
            draft, bindings, review, output, snapshot_dir, measured_profile, as_json
        )
        return
    if bindings is not None or review is not None:
        typer.echo("Error: --bindings/--review apply to science pilot drafts only.", err=True)
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


def _write_review(path: Path | None, review: dict[str, object]) -> None:
    if path is None:
        return
    from xlm.experiments.execution import write_json

    write_json(path, review)


def _echo_review(review: dict[str, object], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(review, indent=2, sort_keys=True, default=str))
        return
    typer.echo("============================================================")
    typer.echo(f"Science pilot:   {review.get('status')}  (contract {review.get('contract')})")
    plan = review.get("plan")
    if isinstance(plan, dict):
        typer.echo(f"Plan hash:       {plan.get('plan_hash')}")
    blockers = review.get("blockers") or []
    typer.echo(f"Blockers:        {len(blockers) if isinstance(blockers, list) else blockers}")
    if isinstance(blockers, list):
        for blocker in blockers[:40]:
            typer.echo(f"  - [{blocker['code']}] {blocker['detail']}")
    for warning in review.get("warnings") or []:  # type: ignore[attr-defined]
        typer.echo(f"Warning:         {warning}")
    commands = review.get("commands") or {}
    if isinstance(commands, dict):
        typer.echo(f"Launch:          {commands.get('launch')}")
    typer.echo("Nothing was executed; planning and validation never authorize or train.")
    typer.echo("============================================================")


def _plan_science_pilot(
    draft: Path,
    bindings: Path | None,
    review_path: Path | None,
    output: Path | None,
    snapshot_dir: Path | None,
    measured_profile: dict[str, object] | None,
    as_json: bool,
) -> None:
    from xlm.experiments.science_pilot import plan_science_pilot

    try:
        result = plan_science_pilot(
            draft,
            workspace_root=_workspace(),
            bindings_path=bindings,
            snapshot_dir=snapshot_dir,
            output_path=output,
            review_path=review_path,
            measured_profile=measured_profile,
        )
    except Exception as exc:
        typer.echo(f"Error: cannot plan science pilot: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _write_review(review_path, result.review)
    _echo_review(result.review, as_json)


@experiment_app.command("validate")
def experiment_validate_cmd(
    plan_file: Annotated[Path, typer.Argument(help="Frozen plan JSON from 'experiment plan'.")],
    ticket: Annotated[
        Path | None, typer.Option("--ticket", help="Operator ticket to check against the plan.")
    ] = None,
    review: Annotated[
        Path | None, typer.Option("--review", help="Write the validation review JSON here.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the review as JSON.")] = False,
) -> None:
    """Re-verify a frozen plan now. Nonzero exit unless RESOLVED or EXECUTABLE; never runs it."""
    from xlm.experiments.authorization import AuthorizationError, load_ticket
    from xlm.experiments.plans import ExecutablePlan
    from xlm.experiments.science_pilot import PilotStatus, validate_science_pilot_plan

    try:
        plan = ExecutablePlan.load(plan_file)
        ticket_obj = load_ticket(ticket) if ticket is not None else None
    except (AuthorizationError, OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(f"Error: cannot load plan/ticket: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if "science_pilot" not in plan.resolved_config:
        typer.echo(
            "Error: 'experiment validate' checks science pilot plans; other plans are "
            "validated by 'experiment submit'",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        result = validate_science_pilot_plan(plan, ticket=ticket_obj, plan_path=plan_file)
    except Exception as exc:
        typer.echo(f"Error: validation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _write_review(review, result.review)
    _echo_review(result.review, as_json)
    if result.status not in (PilotStatus.RESOLVED, PilotStatus.EXECUTABLE):
        raise typer.Exit(code=1)


def _echo_comparison(record: dict[str, object], paths: dict[str, Path]) -> None:
    from xlm.comparison.science_compare import summary_state

    typer.echo("============================================================")
    typer.echo(f"Comparison:      {record.get('manifest_hash')}")
    typer.echo(f"State:           {summary_state(record)}")
    candidates = record.get("candidates") or []
    assert isinstance(candidates, list)
    for candidate in candidates:
        decision = candidate.get("decision") or {}
        typer.echo(
            f"  {candidate['arm_id']}: eligible={candidate['eligible']} "
            f"decision={decision.get('result')} promotion={candidate['promotion']['state']}"
        )
        for reason in candidate["ineligible_reasons"][:20]:
            typer.echo(f"    ! {reason}")
    validation = record.get("manifest_validation") or {}
    assert isinstance(validation, dict)
    for problem in validation.get("problems", [])[:40]:
        typer.echo(f"  ! manifest {problem['field']}: {problem['problem']}")
    if record.get("synthetic_evidence"):
        typer.echo("Evidence:        SYNTHETIC (authored values, not results)")
    for kind, path in paths.items():
        typer.echo(f"{kind.upper():<16} {path}")
    typer.echo("Nothing was trained, scheduled or authorized.")
    typer.echo("============================================================")


@experiment_app.command("compare")
def experiment_compare_cmd(
    comparison: Annotated[
        Path, typer.Option("--comparison", help="Science-v1 comparison manifest JSON.")
    ],
    runs: Annotated[
        Path, typer.Option("--runs", help="Run roster JSON naming frozen checkpoints/attempts.")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Report directory.")],
    prerequisite: Annotated[
        list[Path] | None,
        typer.Option("--prerequisite", help="Prior-scale comparison.json (150M/300M entry)."),
    ] = None,
) -> None:
    """Validate a comparison manifest against immutable run evidence; never trains.

    Writes comparison.json (hashed record), report.md and summary.csv. Exit 1 when
    the manifest is invalid or any candidate is ineligible (the record is still written).
    """
    from xlm.comparison.science_compare import ComparisonError
    from xlm.comparison.science_io import run_comparison

    try:
        record, paths = run_comparison(
            comparison, runs, output, prerequisite_paths=list(prerequisite or [])
        )
    except (ComparisonError, OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(f"Error: comparison refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_comparison(record, paths)
    candidates = record.get("candidates") or []
    valid = bool((record.get("manifest_validation") or {}).get("valid"))
    if not valid or not all(c["eligible"] for c in candidates):
        raise typer.Exit(code=1)


@experiment_app.command("report")
def experiment_report_cmd(
    record: Annotated[
        Path, typer.Option("--record", help="comparison.json written by 'experiment compare'.")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Report directory.")],
) -> None:
    """Re-render an existing, hash-verified comparison record. Never changes the decision."""
    from xlm.comparison.science_compare import ComparisonError
    from xlm.comparison.science_io import render_record

    try:
        loaded, paths = render_record(record, output)
    except (ComparisonError, OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(f"Error: report refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_comparison(loaded, paths)


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
