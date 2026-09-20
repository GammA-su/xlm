"""Freeze and launch existing bounded train/resume CLI implementations."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import typer

from xlm.artifacts.manifest import identity_digest, validate_component
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.experiments.environment import runtime_locations
from xlm.experiments.execution import (
    make_envelope,
    read_json,
    resolve_execution_config,
    validate_envelope,
    write_json,
)
from xlm.experiments.launcher import launch_worker
from xlm.experiments.snapshot import capture_snapshot


def _smoke_limits(config: dict[str, Any]) -> None:
    training = config["training"]
    budget = training["budget"]
    if not 0 < budget["max_valid_targets"] <= 200_000 or not 0 < budget["max_train_seconds"] <= 600:
        raise ValueError(
            "direct training exceeds C13 smoke caps; an authorized campaign is required"
        )
    from xlm.training.components import inspect_model_shape

    model = config["model"]
    if inspect_model_shape(config) >= 5_000_000:
        raise ValueError("direct smoke model must have fewer than 5 million parameters")
    horizon = training["schedule"].get("horizon_valid_targets", budget["max_valid_targets"])
    if budget["max_valid_targets"] > horizon:
        raise ValueError("training budget exceeds frozen horizon; explicit fork required")
    if model.get("attention_backend") == "profile_required":
        raise ValueError("attention_backend is profile_required; resolve it explicitly")


def launch_train(plan_path: Path, options: dict[str, Any]) -> None:
    if plan_path.suffix in (".yaml", ".yml"):
        from xlm.config.composer import ConfigComposer

        config = ConfigComposer(Path.cwd()).compose(plan_path)
    else:
        config = read_json(plan_path)
    label = str(config.get("id", "unnamed"))
    validate_component(label)
    training = config["training"]
    training["device"] = options["device"]
    for option, field in (
        ("precision", "precision"),
        ("compile_model", "compile"),
        ("activation_checkpointing", "activation_checkpointing"),
    ):
        if options.get(option) is not None:
            training[field] = options[option]
    for option, field in (
        ("max_targets", "max_valid_targets"),
        ("max_seconds", "max_train_seconds"),
    ):
        if options.get(option) is not None:
            training["budget"][field] = options[option]
    if options.get("attention_backend") is not None:
        config["model"]["attention_backend"] = options["attention_backend"]
    resolved, _ = resolve_execution_config(config)
    _smoke_limits(resolved)
    if options["dry_run"]:
        typer.echo("Dry run requested. Plan successfully validated (bounded local execution).")
        return
    home = ArtifactPaths.from_env().root.resolve()
    attempt = home / "runs" / f"direct-{uuid.uuid4().hex}"
    snapshot = attempt / "snapshot"
    capture_snapshot(Path(__file__).resolve().parents[3], snapshot)
    envelope = make_envelope(resolved, snapshot, extras=[training["device"]])
    frozen_plan = attempt / "train.json"
    write_json(frozen_plan, {**envelope["config"], "id": label})
    result = launch_worker(
        {
            "action": "train",
            "envelope": envelope,
            "plan_hash": envelope["execution_hash"],
            "snapshot_dir": str(snapshot),
            "runtime_locations": runtime_locations(),
            "artifact_home": str(home),
            "plan_path": str(frozen_plan),
            "options": options,
        },
        attempt / "worker",
    )
    typer.echo((attempt / "worker/stdout.log").read_text(encoding="utf-8"), nl=False)
    write_json(attempt / "run_record.json", result)


def launch_resume(checkpoint: Path, options: dict[str, Any]) -> None:
    home = ArtifactPaths.from_env().root.resolve()
    store = ArtifactStore(ArtifactPaths(root=home))
    manifest = store.verify_artifact(checkpoint)
    context_path = checkpoint / "execution.json"
    if not context_path.is_file():
        raise ValueError("legacy checkpoint has no frozen execution evidence; inspection only")
    context = read_json(context_path)
    if "envelope" not in context:
        raise ValueError("legacy/unresolved checkpoint cannot qualify for frozen continuation")
    if manifest.metadata.get("execution_provenance_hash") != identity_digest(context):
        raise ValueError("checkpoint execution provenance mismatch")
    snapshot = Path(context["observations"]["snapshot_dir"])
    envelope = context["envelope"]
    if (
        manifest.producer_code_hash != envelope["code_hash"]
        or manifest.dependency_hash != envelope["dependency_hash"]
        or manifest.resolved_config_hash != context["plan_hash"]
    ):
        raise ValueError("checkpoint manifest/execution identity mismatch")
    validate_envelope(envelope, snapshot, check_environment=False, resolve_components=False)
    original = envelope["config"]
    target = options.get("budget")
    if target is None:
        target = original["training"]["budget"]["max_valid_targets"]
    changed = (
        target != original["training"]["budget"]["max_valid_targets"]
        or options["device"] != original["training"]["device"]
    )
    if changed and not options["fork"]:
        raise ValueError("changed frozen budget/device requires explicit fork/new plan")
    if options["fork"]:
        config = json.loads(json.dumps(original))
        config["training"]["budget"]["max_valid_targets"] = target
        config["training"]["device"] = options["device"]
        if "horizon_valid_targets" in config["training"]["schedule"]:
            config["training"]["schedule"]["horizon_valid_targets"] = max(
                target, config["training"]["schedule"]["horizon_valid_targets"]
            )
        _smoke_limits(config)
        envelope = make_envelope(config, snapshot, extras=[options["device"]])
    attempt = home / "runs" / f"resume-{uuid.uuid4().hex}"
    result = launch_worker(
        {
            "action": "resume",
            "envelope": envelope,
            "plan_hash": envelope["execution_hash"] if options["fork"] else context["plan_hash"],
            "snapshot_dir": str(snapshot),
            "runtime_locations": runtime_locations(),
            "artifact_home": str(home),
            "checkpoint": str(checkpoint.resolve()),
            "options": options,
        },
        attempt / "worker",
    )
    typer.echo((attempt / "worker/stdout.log").read_text(encoding="utf-8"), nl=False)
    write_json(attempt / "run_record.json", result)
