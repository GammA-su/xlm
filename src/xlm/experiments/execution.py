"""Versioned resolved execution envelope shared by queue, worker and checkpoints."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from xlm.artifacts.manifest import canonical_json, identity_digest
from xlm.artifacts.store import compute_file_sha256
from xlm.config.science import runtime_policy_for
from xlm.core.paths import ArtifactPaths
from xlm.experiments.environment import installed_runtime, runtime_locations
from xlm.experiments.snapshot import CodeSnapshot, load_snapshot, verify_snapshot


def read_json(path: Path, limit: int = 8 * 1024**2) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("execution JSON exceeds byte limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("execution JSON must be an object")
    canonical_json(value)
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    from xlm.artifacts.manifest import ensure_plain_path

    encoded = canonical_json(value)
    if len(encoded) > 8 * 1024**2:
        raise ValueError("execution JSON exceeds byte limit")
    ensure_plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as output:
        output.write(encoded)
    temporary.replace(path)


def bounded_asset_hash(path: Path) -> str:
    """Preflight bounded local tokenizer/evaluator assets before streamed hashing."""
    from xlm.artifacts.manifest import ensure_plain_path
    from xlm.prepare.integrity import path_digest

    pending = [path]
    entries = total = 0
    while pending:
        item = pending.pop()
        ensure_plain_path(item)
        entries += 1
        if entries > 4096:
            raise ValueError("execution assets exceed entry limit")
        if item.is_dir():
            for child in item.iterdir():
                pending.append(child)
                if len(pending) > 4096:
                    raise ValueError("execution assets exceed entry limit")
        else:
            total += item.stat().st_size
            if total > 64 * 1024**2:
                raise ValueError("execution assets exceed byte limit")
    return path_digest(path)


def resolve_execution_config(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve registered defaults before identity. No new component execution paths."""
    from xlm.config import schemas  # noqa: F401 - register built-in factories
    from xlm.config.schemas import TrainingConfig
    from xlm.config.science import STATISTICAL_ATTENTION, omit_absent_science_fields
    from xlm.training.components import (
        component_catalog,
        selected_entry,
        validate_component_selection,
    )
    from xlm.training.inputs import (
        MixtureInput,
        normalize_training_data,
        resolve_training_input,
        resolve_training_tokenizer,
    )

    resolved = json.loads(canonical_json(config))
    unknown = set(resolved) - {
        "schema_version",
        "kind",
        "id",
        "status",
        "track",
        "model",
        "data",
        "objective",
        "optimizer",
        "training",
        "evaluation",
        "resources",
        "authorization",
        "code_hash",
        "dependency_hash",
        "created_at",
        "plugins",
        "science_pilot",
    }
    if unknown:
        raise ValueError(f"unsupported executable configuration fields: {sorted(unknown)}")
    components: dict[str, Any] = {}
    catalog = component_catalog(resolved.get("plugins"))
    for field, category in (
        ("model", "architecture"),
        ("objective", "objective"),
        ("optimizer", "optimizer"),
    ):
        raw = resolved.get(field, {})
        entry = selected_entry(catalog, category, raw)
        resolved[field] = entry.config_schema.model_validate(raw).model_dump(mode="json")
        components[field] = {"key": entry.key, "serializer": entry.serializer_version}
    training = omit_absent_science_fields(
        TrainingConfig.model_validate(resolved["training"]).model_dump(mode="json")
    )
    runtime = training.get("runtime")
    if runtime is not None:
        backend = resolved["model"].get("attention_backend")
        allowed = (
            ("sdpa",) if runtime["attention_policy"] == STATISTICAL_ATTENTION else ("sdpa", "eager")
        )
        if backend not in allowed:
            raise ValueError(
                f"attention_policy '{runtime['attention_policy']}' requires attention_backend "
                f"in {allowed}, got '{backend}'"
            )
    schedule = training["schedule"]
    entry = selected_entry(catalog, "schedule", schedule)
    training["schedule"] = entry.config_schema.model_validate(schedule).model_dump(mode="json")
    components["schedule"] = {"key": entry.key, "serializer": entry.serializer_version}
    if training["budget"]["max_train_seconds"] is None:
        training["budget"]["max_train_seconds"] = 600.0
    checkpoint_cadence = training.get("checkpoint_cadence")
    if checkpoint_cadence is not None:
        # P35 M3: an absolute checkpoint plan must bind to this budget before execution.
        from xlm.evaluation.cadence import build_checkpoint_plan

        build_checkpoint_plan(
            str(checkpoint_cadence["cadence"]),
            int(training["budget"]["max_valid_targets"]),
            fixture_milestones=checkpoint_cadence["fixture_milestones"],
            fixture_recovery=checkpoint_cadence["fixture_recovery"],
        )
    resolved["training"] = training
    evaluation = resolved.get("evaluation")
    if isinstance(evaluation, dict) and evaluation.get("science") is not None:
        # Science-v1 cadence (P35 M2): validated only when declared, so schema-v1
        # evaluation sections keep their historical bytes.
        from xlm.config.schemas import ScienceEvaluationConfig
        from xlm.training.evaluation import plan_from_config

        if training.get("science_version") is None:
            raise ValueError("evaluation.science requires training.science_version")
        science_evaluation = ScienceEvaluationConfig.model_validate(evaluation["science"])
        evaluation["science"] = science_evaluation.model_dump(mode="json")
        plan_from_config(evaluation["science"], int(training["budget"]["max_valid_targets"]))
    if training.get("evaluation_recoverability") is not None:
        # P35 pilot readiness: the recoverability policy needs the evaluation cadence
        # and derives evaluation-recovery checkpoints; resolve both plans now.
        from xlm.evaluation.recoverability import run_plans_from_config

        run_plans_from_config(resolved)
    if resolved.get("science_pilot") is not None:
        # P35 M3 pilot requirements are data-only and bound into the envelope/plan hash.
        from xlm.experiments.science_pilot import SciencePilotConfig

        if training.get("science_version") is None:
            raise ValueError("science_pilot requires training.science_version")
        resolved["science_pilot"] = SciencePilotConfig.model_validate(
            resolved["science_pilot"]
        ).model_dump(mode="json")
    validate_component_selection(resolved, catalog)
    data = resolved["data"]
    normalize_training_data(data, training)
    data_source, data_hash = resolve_training_input(data, ArtifactPaths.from_env())
    tokenizer, tokenizer_identity = resolve_training_tokenizer(data, catalog)
    readers = (
        list(data_source.readers.values())
        if isinstance(data_source, MixtureInput)
        else ([data_source] if hasattr(data_source, "manifest") else [])
    )
    if any(
        reader.manifest.tokenizer_hash != tokenizer_identity.get("fingerprint")
        for reader in readers
    ):
        raise ValueError("input shard/tokenizer identity mismatch")
    if tokenizer is not None and resolved["model"]["vocab_size"] != tokenizer.vocab_size:
        raise ValueError("model vocabulary differs from the selected tokenizer")
    if isinstance(data_source, MixtureInput) and data.get("exposure_plan") is not None:
        from xlm.data.sampling import MixtureBatcher, compile_exposure_plan, validate_mixture

        exposure = data["exposure_plan"]
        batcher = MixtureBatcher(data_source.recipe, data_source.readers)
        from xlm.data.exclusion.transport import open_gate

        with open_gate(Path(data["c05_proof"]) if data.get("c05_proof") else None) as gate:
            expected = compile_exposure_plan(
                data_source.recipe,
                validate_mixture(data_source.recipe, batcher.availability),
                training["budget"]["max_valid_targets"],
                block_size=int(exposure.get("block_size", 8192)),
                c05_gate=gate,
                c05_shards={s: r.directory for s, r in data_source.readers.items()}
                if gate
                else None,
            ).to_dict()
        if exposure != expected:
            raise ValueError(
                "exposure plan differs from recomputed token budget/source projections"
            )
    # Outer labels are observations, not executable semantics or trusted hashes.
    for key in ("id", "status", "authorization", "code_hash", "dependency_hash", "created_at"):
        resolved.pop(key, None)
    return resolved, {"data": data_hash, "tokenizer": tokenizer_identity, "components": components}


def make_envelope(
    config: dict[str, Any],
    snapshot_dir: Path,
    *,
    extras: list[str],
    policy: dict[str, Any] | None = None,
    purpose: str = "training",
) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_dir)
    errors = verify_snapshot(snapshot_dir / "code", snapshot, exact=True)
    if errors:
        raise ValueError(f"frozen snapshot integrity failed: {errors[:5]}")
    required = {
        "src/xlm/__init__.py",
        "src/xlm/experiments/worker.py",
        "src/xlm/training/trainer.py",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
    }
    if not required.issubset(snapshot.included_files):
        raise ValueError("snapshot lacks required executable closure")
    resolver = resolve_execution_config
    if purpose == "evaluation":
        from xlm.experiments.evaluation_execution import resolve_evaluation_config

        resolver = resolve_evaluation_config
    elif purpose != "training":
        raise ValueError("unsupported execution purpose")
    resolved, bindings = resolver(config)
    if resolved.get("training", {}).get("device", resolved.get("device")) not in extras:
        raise ValueError("execution device differs from selected accelerator extra")
    lock = snapshot_dir / "code/uv.lock"
    payload = {
        "version": 1,
        "purpose": purpose,
        "config": resolved,
        "bindings": bindings,
        "code_hash": snapshot.code_hash,
        "dependency_hash": compute_file_sha256(lock, max_bytes=8 * 1024**2),
        "environment": installed_runtime(lock, extras),
        "runtime_policy": policy or runtime_policy_for(resolved, purpose),
    }
    return {**payload, "execution_hash": identity_digest(payload)}


def validate_envelope(
    envelope: dict[str, Any],
    snapshot_dir: Path,
    *,
    check_environment: bool = True,
    site: Path | None = None,
    resolve_components: bool = True,
) -> CodeSnapshot:
    payload = {k: v for k, v in envelope.items() if k != "execution_hash"}
    if envelope.get("version") != 1 or envelope.get("execution_hash") != identity_digest(payload):
        raise ValueError("execution envelope identity mismatch")
    if envelope.get("runtime_policy") != runtime_policy_for(
        envelope.get("config", {}), str(envelope.get("purpose"))
    ):
        raise ValueError("unsupported frozen runtime policy")
    snapshot = load_snapshot(snapshot_dir)
    if snapshot.code_hash != envelope["code_hash"]:
        raise ValueError("execution snapshot identity mismatch")
    errors = verify_snapshot(snapshot_dir / "code", snapshot, exact=True)
    if errors:
        raise ValueError(f"frozen snapshot integrity failed: {errors[:5]}")
    if not resolve_components:
        # The live coordinator verifies the immutable envelope and code closure.
        # Only the captured worker may interpret that code's component schemas;
        # it always repeats full input/default/environment validation before work.
        if envelope.get("purpose") not in ("training", "evaluation"):
            raise ValueError("unknown execution purpose")
        resolved, bindings = envelope["config"], envelope["bindings"]
    elif envelope.get("purpose") == "evaluation":
        from xlm.experiments.evaluation_execution import resolve_evaluation_config

        resolved, bindings = resolve_evaluation_config(envelope["config"])
    elif envelope.get("purpose") == "training":
        resolved, bindings = resolve_execution_config(envelope["config"])
    else:
        raise ValueError("unknown execution purpose")
    if (
        resolved.get("training", {}).get("device", resolved.get("device"))
        not in envelope["environment"]["extras"]
    ):
        raise ValueError("execution device differs from selected accelerator extra")
    if (
        canonical_json(resolved) != canonical_json(envelope["config"])
        or bindings != envelope["bindings"]
    ):
        raise ValueError("execution defaults/component/input/tokenizer identity mismatch")
    lock = snapshot_dir / "code/uv.lock"
    if compute_file_sha256(lock) != envelope["dependency_hash"]:
        raise ValueError("execution dependency lock mismatch")
    if (
        check_environment
        and installed_runtime(lock, envelope["environment"]["extras"], site)
        != envelope["environment"]
    ):
        raise ValueError("actual installed environment differs from frozen identity")
    return snapshot


def observed_origins(snapshot_dir: Path, snapshot: CodeSnapshot) -> dict[str, str]:
    root = (snapshot_dir / "code").resolve()
    origins: dict[str, str] = {}
    for name, module in list(sys.modules.items()):
        if name != "xlm" and not name.startswith(("xlm.", "xlm_plugin_")):
            continue
        file = getattr(module, "__file__", None)
        if not file:
            namespace = [Path(p).resolve() for p in getattr(module, "__path__", [])]
            if len(namespace) != 1 or not namespace[0].is_relative_to(root):
                raise ValueError(f"unverifiable XLM namespace origin: {name}")
            prefix = namespace[0].relative_to(root).as_posix() + "/"
            if not any(relative.startswith(prefix) for relative in snapshot.included_files):
                raise ValueError(f"uncaptured XLM namespace: {name}")
            origins[name] = prefix
            continue
        path = Path(file).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"live package/import override refused: {name}: {path}")
        relative = path.relative_to(root).as_posix()
        if snapshot.included_files.get(relative) != compute_file_sha256(
            path, max_bytes=256 * 1024**2
        ):
            raise ValueError(f"executed module differs from selected snapshot: {name}")
        origins[name] = relative
    return origins


def execution_context(envelope: dict[str, Any], snapshot_dir: Path) -> dict[str, Any]:
    snapshot = load_snapshot(snapshot_dir)
    return {
        "envelope": envelope,
        "observations": {
            **runtime_locations(),
            "snapshot_dir": str(snapshot_dir.resolve()),
            "worker_pid": os.getpid(),
            "module_origins": observed_origins(snapshot_dir, snapshot),
        },
    }
