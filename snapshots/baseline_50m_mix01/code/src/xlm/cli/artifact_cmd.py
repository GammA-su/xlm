"""CLI commands for inspecting, verifying, and indexing immutable artifacts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths

# Store subdirectories that hold bookkeeping rather than published artifacts.
_INTERNAL_DIRS = frozenset({"ledger", "locks", "staging", "tmp"})

artifact_app = typer.Typer(
    name="artifact",
    help="Inspect, verify, and index immutable XLM artifacts.",
)


def _resolve_artifact_dir(paths: ArtifactPaths, target: str | Path) -> Path:
    target_path = Path(target)
    if target_path.is_dir():
        return target_path.resolve()

    # Search by ID across every artifact kind present in the store. Enumerating the
    # store rather than a hardcoded kind list keeps newly introduced kinds (such as
    # raw_dataset, probe_evidence or clean_dataset) reachable by ID.
    if paths.root.is_dir():
        for kind_dir in sorted(paths.root.iterdir()):
            if not kind_dir.is_dir() or kind_dir.name in _INTERNAL_DIRS:
                continue
            candidate = kind_dir / str(target)
            if candidate.is_dir():
                return candidate.resolve()

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
        sys.stdout.write(manifest.model_dump_json(indent=2) + "\n")
    else:
        typer.echo(f"Artifact ID:           {manifest.artifact_id}")
        typer.echo(f"Kind:                  {manifest.kind}")
        typer.echo(f"Status:                {manifest.status}")
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
    except Exception as exc:
        typer.echo(f"Verification FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@artifact_app.command(name="rebuild-ledger")
def rebuild_ledger_command() -> None:
    """Reconstruct SQLite catalog from disk manifests and durable run records."""
    paths = ArtifactPaths.from_env()
    paths.ensure_directories()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")

    results = ledger.rebuild_from_filesystem(paths, store)
    typer.echo("Ledger rebuild complete:")
    typer.echo(f"  - Rebuilt artifacts: {results['rebuilt_artifacts']}")
    typer.echo(f"  - Rebuilt runs:      {results['rebuilt_runs']}")
    if results["corrupt_artifacts"]:
        typer.echo(f"  - Corrupt artifacts ({len(results['corrupt_artifacts'])}):", err=True)
        for c in results["corrupt_artifacts"]:
            typer.echo(f"      * {c}", err=True)
