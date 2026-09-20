"""CLI for protected final evaluation and release auditing (P21).

`final request` builds a frozen bounded request on the dev side. `final
execute` is operator-only: without operator credentials and a ready sealed
root it refuses. `final verify-receipt` checks a receipt against sealed
records. `release audit` gates a release directory on evidence, never on
claims.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

final_app = typer.Typer(name="final", help="Protected final-evaluation workflow.")
release_app = typer.Typer(name="release", help="Release auditing.")


@final_app.command("request")
def final_request_cmd(
    checkpoint_hash: Annotated[str, typer.Option("--checkpoint-hash")],
    suite_fingerprint: Annotated[str, typer.Option("--suite-fingerprint")],
    task_variants: Annotated[
        str, typer.Option("--task-variants", help="Comma-separated variant IDs.")
    ],
    max_items: Annotated[int, typer.Option("--max-items")] = 100,
    max_requests: Annotated[int, typer.Option("--max-requests")] = 1,
    requester: Annotated[str, typer.Option("--requester")] = "developer",
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("final_request.json"),
) -> None:
    """Freeze a bounded final-evaluation request. Builds, never executes."""
    from xlm.operator.final import FinalEvaluationError, build_final_request

    variants = [v.strip() for v in task_variants.split(",") if v.strip()]
    try:
        request = build_final_request(
            checkpoint_hash=checkpoint_hash,
            suite_fingerprint=suite_fingerprint,
            task_variants=variants,
            max_items=max_items,
            max_requests=max_requests,
            requester=requester,
        )
    except FinalEvaluationError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    typer.echo(f"Final request '{request.request_id}' frozen: {output}")
    typer.echo(f"Request hash: {request.request_hash}")
    typer.echo("Execution requires the operator path; nothing was scored.")


@final_app.command("execute")
def final_execute_cmd(
    request_path: Annotated[Path, typer.Option("--request")],
    authorization: Annotated[
        Path, typer.Option("--authorization", help="Operator authorization JSON.")
    ],
    sealed_root: Annotated[Path, typer.Option("--sealed-root")],
    scorer_bundle: Annotated[Path, typer.Option("--scorer-bundle")],
    reviewed_bundles: Annotated[Path, typer.Option("--reviewed-bundles")],
    quota: Annotated[Path, typer.Option("--quota")] = Path("quota.json"),
    credentials: Annotated[
        str | None,
        typer.Option(
            "--credentials", help="Operator credential file (or $XLM_OPERATOR_CREDENTIALS)."
        ),
    ] = None,
    model_code_hash: Annotated[str, typer.Option("--model-code-hash")] = "unknown",
    context_policy: Annotated[str, typer.Option("--context-policy")] = "rolling",
    precision: Annotated[str, typer.Option("--precision")] = "fp32",
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("final_receipt.json"),
) -> None:
    """Execute a protected request. Operator credentials and sealed root required."""
    import os

    from xlm.operator.final import (
        FinalEvaluationError,
        FinalEvaluationRequest,
        OperatorAuthorization,
        check_operator_environment,
        execute_final_request,
    )
    from xlm.operator.sealed import check_sealed_readiness, require_ready

    credential_file = credentials or os.environ.get("XLM_OPERATOR_CREDENTIALS")
    try:
        operator_secret = check_operator_environment(credential_file)
        _ = operator_secret
        require_ready(check_sealed_readiness(sealed_root, [Path.cwd(), Path.home() / ".xlm"]))
    except (FinalEvaluationError, Exception) as exc:
        typer.echo(f"Error: operator path refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        request = FinalEvaluationRequest.from_dict(
            json.loads(request_path.read_text(encoding="utf-8"))
        )
        auth_payload = json.loads(authorization.read_text(encoding="utf-8"))
        auth = OperatorAuthorization(
            operator_id=str(auth_payload.get("operator_id", "")),
            request_hash=str(auth_payload.get("request_hash", "")),
            ticket=str(auth_payload.get("ticket", "")),
            revoked=bool(auth_payload.get("revoked", False)),
        )
    except (OSError, json.JSONDecodeError, FinalEvaluationError, KeyError) as exc:
        typer.echo(f"Error: bad request or authorization: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    def reviewed_only_supplier(req: Any) -> tuple[dict[str, Any], int]:
        """Placeholder supplier contract: aggregates only, declared count.

        A real operator run injects the reviewed scorer here. This default
        refuses to invent scores: without an injected supplier it raises.
        """
        raise FinalEvaluationError("no reviewed aggregate supplier injected for this execution")

    try:
        receipt = execute_final_request(
            request,
            auth,
            sealed_root,
            scorer_bundle,
            reviewed_bundles,
            quota,
            reviewed_only_supplier,
            auth.operator_id,
            model_code_hash,
            context_policy,
            precision,
        )
    except FinalEvaluationError as exc:
        typer.echo(f"Error: execution refused: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    typer.echo(f"Receipt '{receipt.receipt_id}' written: {output} (aggregates only)")


@final_app.command("verify-receipt")
def final_verify_receipt_cmd(
    receipt_path: Annotated[Path, typer.Argument(help="Receipt JSON to verify.")],
    sealed_root: Annotated[Path, typer.Option("--sealed-root")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify a receipt against sealed records without exposing protected data."""
    from xlm.operator.final import verify_receipt

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: cannot read receipt: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(receipt, dict):
        typer.echo("Error: receipt must be a mapping.", err=True)
        raise typer.Exit(code=1)
    verdict = verify_receipt(receipt, sealed_root)
    if as_json:
        typer.echo(json.dumps(verdict, indent=2, sort_keys=True))
    else:
        typer.echo(
            f"Receipt '{verdict['receipt_id']}': {'VALID' if verdict['valid'] else 'INVALID'}"
        )
        for finding in verdict["findings"]:
            typer.echo(f"  - {finding}")
    if not verdict["valid"]:
        raise typer.Exit(code=1)


@release_app.command("audit")
def release_audit_cmd(
    release_dir: Annotated[Path, typer.Argument(help="Release directory to audit.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Audit a release directory. Unknown rights stay BLOCKED, never certified."""
    from xlm.operator.release import ReleaseAuditError, audit_release

    try:
        audit = audit_release(release_dir)
    except ReleaseAuditError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps(audit.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(f"Release audit: {audit.verdict}")
        for finding in audit.findings:
            typer.echo(f"  [{finding.status.upper():<7}] {finding.check}: {finding.detail}")
    if audit.verdict != "RELEASE":
        raise typer.Exit(code=1)
