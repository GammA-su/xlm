"""Separate verified evaluator identity linked to preserved training provenance."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer

from xlm.artifacts.manifest import identity_digest
from xlm.artifacts.store import ArtifactStore, compute_file_sha256
from xlm.core.paths import ArtifactPaths
from xlm.experiments.environment import runtime_locations
from xlm.experiments.execution import bounded_asset_hash, make_envelope, read_json, write_json
from xlm.experiments.launcher import launch_worker
from xlm.experiments.snapshot import capture_snapshot


def resolve_evaluation_config(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from xlm.tokenizers.loading import checkpoint_weights_hash, load_inference_tokenizer

    checkpoint = Path(config["checkpoint"])
    manifest = None
    if (checkpoint / "manifest.json").exists() or (checkpoint / "_COMPLETED").exists():
        manifest = ArtifactStore(ArtifactPaths.from_env()).verify_artifact(checkpoint)
    training: dict[str, Any] = {"status": "legacy-unresolved"}
    context_path = checkpoint / "execution.json"
    if context_path.is_file() and manifest is not None:
        context = read_json(context_path)
        if "envelope" in context:
            if manifest.metadata.get("execution_provenance_hash") != identity_digest(context):
                raise ValueError("checkpoint training provenance is not bound to its manifest")
            envelope = context["envelope"]
            if (
                envelope["execution_hash"]
                != identity_digest({k: v for k, v in envelope.items() if k != "execution_hash"})
                or manifest.producer_code_hash != envelope["code_hash"]
                or manifest.dependency_hash != envelope["dependency_hash"]
                or manifest.resolved_config_hash != context["plan_hash"]
            ):
                raise ValueError("checkpoint manifest/training execution identity mismatch")
            training = {"envelope": context["envelope"], "plan_hash": context["plan_hash"]}
    tokenizer = load_inference_tokenizer(checkpoint, config.get("tokenizer_path"))
    assets = {
        key: bounded_asset_hash(Path(config[key]))
        # The evaluation-input manifest binds into the frozen execution
        # envelope exactly like the pins file and any include path.
        for key in ("pins_path", "include_path", "inputs")
        if config.get(key) and Path(config[key]).exists()
    }
    return config, {
        "training": training,
        "checkpoint_manifest_hash": (
            compute_file_sha256(checkpoint / "manifest.json") if manifest is not None else None
        ),
        "model_config_hash": compute_file_sha256(
            checkpoint
            / ("config.json" if (checkpoint / "config.json").is_file() else "model_config.json")
        ),
        "checkpoint_weights_hash": checkpoint_weights_hash(checkpoint),
        "tokenizer_hash": tokenizer.fingerprint,
        "evaluation_assets": assets,
    }


def launch_evaluation(options: dict[str, Any]) -> None:
    serialized = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in options.items()
    }
    if serialized.get("tokenizer_path"):
        serialized["tokenizer_path"] = str(Path(serialized["tokenizer_path"]).resolve())
    config = {
        key: value for key, value in serialized.items() if key not in ("output_dir", "output_json")
    }
    home = ArtifactPaths.from_env().root.resolve()
    attempt = home / "runs" / f"evaluate-{uuid.uuid4().hex}"
    snapshot = attempt / "snapshot"
    capture_snapshot(Path(__file__).resolve().parents[3], snapshot)
    extras = [options["device"]] + (
        [] if options["suite"] in ("all", "synthetic_mc", "synthetic_pair") else ["eval"]
    )
    envelope = make_envelope(config, snapshot, extras=extras, purpose="evaluation")
    result = launch_worker(
        {
            "action": "evaluate",
            "envelope": envelope,
            "plan_hash": envelope["execution_hash"],
            "snapshot_dir": str(snapshot),
            "runtime_locations": runtime_locations(),
            "artifact_home": str(home),
            "options": serialized,
        },
        attempt / "worker",
    )
    typer.echo((attempt / "worker/stdout.log").read_text(encoding="utf-8"), nl=False)
    write_json(attempt / "run_record.json", result)


def receipt_provenance(execution: dict[str, Any]) -> dict[str, Any]:
    evaluator = execution["envelope"]
    return {
        "version": 1,
        "training": evaluator["bindings"]["training"],
        "checkpoint_manifest_hash": evaluator["bindings"]["checkpoint_manifest_hash"],
        "evaluator": evaluator,
    }
