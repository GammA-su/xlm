"""CLI commands for inspecting, verifying, and indexing immutable artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.manifest import (
    INTERNAL_KINDS,
    bounded_children,
    ensure_plain_path,
    validate_component,
)
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths

artifact_app = typer.Typer(
    name="artifact",
    help="Inspect, verify, and index immutable XLM artifacts.",
)


def _resolve_artifact_dir(paths: ArtifactPaths, target: str | Path) -> Path:
    target_path = Path(target)
    # An explicit path remains supported; bare strings are always store IDs.
    explicit_path = target_path.is_absolute() or any(c in str(target) for c in ("/", "\\"))
    if explicit_path and target_path.is_dir():
        ensure_plain_path(target_path)
        return target_path.resolve()
    validate_component(str(target))

    # Search by ID across every artifact kind present in the store. Enumerating the
    # store rather than a hardcoded kind list keeps newly introduced kinds (such as
    # raw_dataset, probe_evidence or clean_dataset) reachable by ID.
    matches: list[Path] = []
    if paths.root.is_dir():
        ensure_plain_path(paths.root)
        for kind_dir in sorted(bounded_children(paths.root)):
            if (
                not kind_dir.is_dir()
                or kind_dir.name.casefold() in INTERNAL_KINDS
                or kind_dir.name.startswith(".")
            ):
                continue
            candidate = kind_dir / str(target)
            if candidate.is_dir():
                ensure_plain_path(candidate)
                matches.append(candidate.resolve())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous artifact ID {target!r}; use an explicit artifact path")

    raise FileNotFoundError(f"Artifact '{target}' not found in artifact root '{paths.root}'")


@artifact_app.command(name="inspect")
def inspect_command(
    target: Annotated[str, typer.Argument(help="Artifact directory path or artifact ID.")],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit manifest strictly as JSON.",
        ),
    ] = False,
) -> None:
    """Inspect metadata and file entries of an artifact without requiring PyTorch."""
    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)

    try:
        art_dir = _resolve_artifact_dir(paths, target)
        manifest = store.load_manifest(art_dir)
    except Exception as exc:
        typer.echo(f"Inspection error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        payload = manifest.model_dump()
        payload["identity_scope"] = manifest.identity_scope
        payload["unresolved_input_artifact_ids"] = manifest.unresolved_input_artifact_ids
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        typer.echo(f"Artifact ID:           {manifest.artifact_id}")
        typer.echo(f"Kind:                  {manifest.kind}")
        typer.echo(f"Status:                {manifest.status}")
        typer.echo(
            f"Identity scope:        {manifest.identity_scope} (declared, not authenticated)"
        )
        typer.echo(f"Created At:            {manifest.created_at}")
        typer.echo(f"Resolved Config Hash:  {manifest.resolved_config_hash}")
        typer.echo(f"Producer Code Hash:    {manifest.producer_code_hash}")
        typer.echo(f"Files ({len(manifest.files)}):")
        for f in manifest.files:
            typer.echo(f"  - {f.path} ({f.size_bytes} bytes, sha256={f.sha256[:16]}...)")


@artifact_app.command(name="verify")
def verify_command(
    target: Annotated[str, typer.Argument(help="Artifact directory path or artifact ID.")],
) -> None:
    """Verify artifact completeness, publication marker, and file checksums."""
    paths = ArtifactPaths.from_env()
    store = ArtifactStore(paths)

    try:
        art_dir = _resolve_artifact_dir(paths, target)
        manifest = store.verify_artifact(art_dir)
        typer.echo(
            f"Verification SUCCESS: Artifact '{manifest.artifact_id}' ({manifest.kind}) "
            f"is complete and verified against {len(manifest.files)} file checksums."
        )
        typer.echo(f"Identity scope: {manifest.identity_scope}; producer provenance is declared.")
        typer.echo(
            f"Unresolved input manifest identities: {len(manifest.unresolved_input_artifact_ids)}"
        )
        if manifest.schema_version == 1:
            typer.echo(
                "Legacy integrity only; not eligible for automatic request-equivalent reuse."
            )
    except Exception as exc:
        typer.echo(f"Verification FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@artifact_app.command(name="rebuild-ledger")
def rebuild_ledger_command(
    max_artifacts: Annotated[
        int | None,
        typer.Option("--max-artifacts", help="Stop after examining this many artifacts."),
    ] = None,
    max_verify_mib: Annotated[
        float | None,
        typer.Option("--max-verify-mib", help="Stop before verifying past this many MiB."),
    ] = None,
    deadline_seconds: Annotated[
        float | None,
        typer.Option("--deadline-seconds", help="Stop the scan after this many seconds."),
    ] = None,
    audit_references: Annotated[
        bool,
        typer.Option(
            "--audit-references/--no-audit-references",
            help="Also re-verify every ledger reference against the store, "
            "marking missing/corrupt ones unusable (never deleting).",
        ),
    ] = False,
) -> None:
    """Reconstruct SQLite catalog from disk manifests and durable run records.

    Only fully verified artifacts are recorded; corrupt, incomplete, and
    marker-less states are reported, never inserted. Repeating the command
    is a no-op for already-recorded identical artifacts.
    """
    paths = ArtifactPaths.from_env()
    paths.ensure_directories()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")

    try:
        results = ledger.rebuild_from_filesystem(
            paths,
            store,
            max_artifacts=max_artifacts,
            max_verify_bytes=None if max_verify_mib is None else int(max_verify_mib * 1024**2),
            deadline_seconds=deadline_seconds,
        )
    except ValueError as exc:
        typer.echo(f"Error: invalid rebuild bounds: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("Ledger rebuild complete:")
    typer.echo(f"  - Rebuilt artifacts: {results['rebuilt_artifacts']}")
    typer.echo(f"  - Already recorded: {results['already_recorded']}")
    typer.echo(f"  - Rebuilt runs:      {results['rebuilt_runs']}")
    typer.echo(f"  - Legacy checksum-only artifacts: {len(results['legacy_artifacts'])}")
    if results["incomplete_artifacts"]:
        typer.echo(f"  - Incomplete artifacts ({len(results['incomplete_artifacts'])}):")
        for c in results["incomplete_artifacts"]:
            typer.echo(f"      * {c}")
    if results["conflicts"]:
        typer.echo(f"  - Identity conflicts ({len(results['conflicts'])}):", err=True)
        for c in results["conflicts"]:
            typer.echo(f"      * {c}", err=True)
    if results["corrupt_artifacts"]:
        typer.echo(f"  - Corrupt artifacts ({len(results['corrupt_artifacts'])}):", err=True)
        for c in results["corrupt_artifacts"]:
            typer.echo(f"      * {c}", err=True)
    if results["truncated"]:
        typer.echo(f"  - Truncated: {results['truncated_reason']}", err=True)
    if audit_references:
        audit = ledger.audit_ledger_references(
            paths,
            store,
            max_artifacts=max_artifacts,
            max_verify_bytes=None if max_verify_mib is None else int(max_verify_mib * 1024**2),
            deadline_seconds=deadline_seconds,
        )
        typer.echo(
            f"  - Reference audit: {audit['ok']} ok, {audit['healed']} healed, "
            f"{audit['unusable']} unusable"
        )
        for verdict in audit["verdicts"]:
            if verdict["verdict"] != "ok":
                typer.echo(
                    f"      * {verdict['artifact_id']}: {verdict['verdict']} ({verdict['detail']})",
                    err=True,
                )
