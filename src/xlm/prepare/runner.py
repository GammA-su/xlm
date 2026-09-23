"""Prepare execution: reuse verified stages, resume partial ones, never silently redo (P22).

Execution requires explicit authorization and records it. Verified stages with
unchanged inputs are reused (recorded, not rerun); incomplete stages resume;
`--force` rebuilds but says so in the state file. Subprocess stages run the
already-tested `xlm` commands; local copies are bounded and hashed. State is
written after every stage, so an interruption resumes instead of repeating
costly work.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from xlm.artifacts.manifest import comparable_resolved, ensure_plain_path, validate_component
from xlm.prepare.bounds import PrepareBounds
from xlm.prepare.config import PrepareConfig, PrepareStageSpec
from xlm.prepare.integrity import bounded_files
from xlm.prepare.planner import (
    build_variable_mapping,
    find_repo_root,
    hash_stage_inputs,
    hash_stage_outputs,
    plan_prepare,
    resolve_variables,
    stage_outputs_verified,
)

STATE_FILENAME = "prepare_state.json"
DEFAULT_STAGE_TIMEOUT_SECONDS = 1200.0


class PrepareRunError(RuntimeError):
    """Raised when preparation is refused or a stage fails."""


@dataclass
class StageRecord:
    """Durable per-stage outcome."""

    stage_id: str
    action: str  # reused | ran | resumed | forced | skipped | failed | check-only
    status: str  # succeeded | partial | failed | skipped | blocked
    inputs_hash: str = ""
    outputs_hash: str = ""
    attempts: int = 0
    note: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrepareResult:
    """Outcome of one prepare invocation."""

    config_id: str
    output_root: str
    authorized: bool
    stages: list[StageRecord] = field(default_factory=list)
    state_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "output_root": self.output_root,
            "authorized": self.authorized,
            "stages": [s.to_dict() for s in self.stages],
            "state_path": self.state_path,
        }


def state_file_path(output_root: Path | str) -> Path:
    return Path(output_root) / STATE_FILENAME


def load_prior_state(output_root: Path | str) -> dict[str, Any]:
    path = state_file_path(output_root)
    ensure_plain_path(path)
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > 8 * 1024**2:
            raise PrepareRunError("preparation state exceeds 8 MiB")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PrepareRunError(f"unreadable preparation state: {path}") from exc
    if not isinstance(data, dict):
        raise PrepareRunError(f"preparation state must be a mapping: {path}")
    return data


def _write_state(
    output_root: Path, records: dict[str, StageRecord], authorization: dict[str, Any]
) -> Path:
    path = state_file_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "authorization": authorization,
        "stages": {key: record.to_dict() for key, record in records.items()},
        "updated_at": datetime.now(UTC).isoformat(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def _clear_declared_outputs(
    spec: PrepareStageSpec,
    variables: dict[str, str],
    config_dir: Path,
    output_root: Path,
) -> None:
    """Remove a stage's declared outputs before it (re)runs.

    Immutable writers (token shards publish manifest-last and refuse to
    write into a directory that already holds payloads) cannot rebuild in
    place, so a legitimate rerun — a forced rebuild or a lineage-stale
    rerun after an upstream stage reran — must start from a clean slate.
    Reuse is untouched: this runs only for stages about to execute, never
    for reused, blocked, or check-only stages (which declare no outputs).

    Clearing is fail-closed: every declared path is validated before
    anything is removed. Paths must resolve inside the output root (a
    stage can never delete repo, home, or absolute-elsewhere files), must
    not be the prepare state or lock file, and symlinks are unlinked,
    never followed. A corrupt artifact at a declared path is therefore
    replaced only by that path owner's own rerun — writers still fail
    closed on anything unexpected found mid-run, and post-run
    verification still decides succeeded vs partial.
    """
    targets: list[Path] = []
    root = comparable_resolved(output_root)
    for raw in spec.outputs:
        resolved = resolve_variables(raw, variables, where=f"stage '{spec.stage_id}' outputs")
        path = Path(resolved)
        if not path.is_absolute():
            path = config_dir / path
        if comparable_resolved(path).parent == root and path.name in (
            STATE_FILENAME,
            ".prepare.lock",
        ):
            raise PrepareRunError(
                f"stage '{spec.stage_id}' must not declare prepare state as an output: {raw}"
            )
        if comparable_resolved(path) == root:
            raise PrepareRunError(
                f"stage '{spec.stage_id}' must not declare the whole output root "
                f"as an output: {raw}"
            )
        if root not in comparable_resolved(path).parents:
            raise PrepareRunError(
                f"stage '{spec.stage_id}' declares an output outside the output root, "
                f"refusing to clear: {raw}"
            )
        targets.append(path)
    for path in targets:
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            ensure_plain_path(path)
            shutil.rmtree(path)
        elif path.is_file():
            ensure_plain_path(path)
            path.unlink(missing_ok=True)


def _copy_bounded(
    sources: list[str],
    destination: Path,
    max_bytes: int,
    variables: dict[str, str],
    bounds: PrepareBounds,
) -> dict[str, Any]:
    """Copy fixture inputs with a byte cap and a content manifest."""
    total = 0
    files: list[dict[str, Any]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for raw in sources:
        resolved = resolve_variables(raw, variables, where="local_copy inputs")
        source = Path(resolved)
        ensure_plain_path(source)
        if not source.exists():
            raise PrepareRunError(f"local_copy input not found: {resolved}")
        entries = bounded_files(source)
        for entry in entries:
            ensure_plain_path(entry)
            if not entry.is_file():
                continue
            total += entry.stat().st_size
            if total > max_bytes:
                raise PrepareRunError(
                    f"local_copy exceeds {max_bytes:,} bytes at '{entry}'; "
                    "raise the cap explicitly instead of fetching blindly"
                )
            relative = entry.relative_to(source) if source.is_dir() else Path(entry.name)
            target = destination / relative
            ensure_plain_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            bounds.check()
            reserved_size = entry.stat().st_size
            input_reservation = bounds.capacity.reserve("input_bytes", reserved_size)
            reservation = bounds.capacity.reserve_disk_space(
                destination, reserved_size, is_temp=False
            )
            digest = hashlib.sha256()
            copied = 0
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            with entry.open("rb") as source_stream, temporary.open("xb") as output_stream:
                for chunk in iter(lambda: source_stream.read(64 * 1024), b""):
                    copied += len(chunk)
                    bounds.capacity.check_deadline()
                    if copied > reserved_size or copied > max_bytes:
                        raise PrepareRunError(f"local_copy input grew beyond its byte cap: {entry}")
                    digest.update(chunk)
                    output_stream.write(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            temporary.replace(target)
            bounds.capacity.settle("output", reservation, copied)
            bounds.capacity.settle("input_bytes", input_reservation, copied)
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": copied,
                    "sha256": digest.hexdigest(),
                }
            )
    manifest = {"files": files, "total_bytes": total}
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    reservation = bounds.capacity.reserve_disk_space(destination, len(payload), is_temp=False)
    target = destination / "copy_manifest.json"
    ensure_plain_path(target)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target)
    bounds.capacity.settle("output", reservation, len(payload))
    return manifest


def _run_prepare(
    config: PrepareConfig,
    config_path: Path | str,
    home: Path | str,
    repo_root: Path | str | None = None,
    *,
    authorize: bool = False,
    force: bool = False,
    only: list[str] | None = None,
    stage_timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS,
    bounds: PrepareBounds,
) -> PrepareResult:
    """Execute an authorized prepare run with reuse, resume and recorded force."""
    config_file = Path(config_path)
    home_path = Path(home)
    root = Path(repo_root) if repo_root is not None else find_repo_root(config_file.parent)
    if not authorize:
        raise PrepareRunError(
            "refusing to execute without explicit authorization: rerun with --authorize. "
            "Planning (--plan-only) is always allowed."
        )

    variables = build_variable_mapping(config, config_file, home_path, root)
    output_root = Path(variables["output_root"])
    prior = load_prior_state(output_root)
    prior_stages: dict[str, Any] = prior.get("stages", {}) if isinstance(prior, dict) else {}
    records: dict[str, StageRecord] = {}
    for key, payload in prior_stages.items():
        if isinstance(payload, dict):
            records[key] = StageRecord(
                stage_id=str(payload.get("stage_id", key)),
                action=str(payload.get("action", "")),
                status=str(payload.get("status", "")),
                inputs_hash=str(payload.get("inputs_hash", "")),
                outputs_hash=str(payload.get("outputs_hash", "")),
                attempts=int(payload.get("attempts", 0) or 0),
                note=str(payload.get("note", "")),
                updated_at=str(payload.get("updated_at", "")),
            )
    authorization = {
        "authorized": True,
        "forced": force,
        "only": list(only or []),
        "at": datetime.now(UTC).isoformat(),
    }

    plan = plan_prepare(config, config_file, home_path, root, prior)
    by_id = {stage.stage_id: stage for stage in config.stages}
    result = PrepareResult(config_id=config.id, output_root=str(output_root), authorized=True)

    def persist() -> None:
        result.state_path = str(_write_state(output_root, records, authorization))

    for stage_plan in plan.stages:
        spec = by_id[stage_plan.stage_id]
        if only is not None and stage_plan.stage_id not in only:
            continue
        if stage_plan.status == "blocked":
            record = StageRecord(
                stage_id=spec.stage_id,
                action="skipped",
                status="blocked",
                note="; ".join(stage_plan.reasons),
                updated_at=datetime.now(UTC).isoformat(),
            )
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
            raise PrepareRunError(
                f"stage '{spec.stage_id}' is blocked: " + "; ".join(stage_plan.reasons)
            )
        if stage_plan.status == "check-only":
            record = _execute_check_only(
                spec, stage_plan.command, root, stage_timeout_seconds, bounds
            )
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
            if record.status == "failed":
                raise PrepareRunError(f"stage '{spec.stage_id}' failed: {record.note}")
            continue

        previous = records.get(spec.stage_id)
        attempts = (previous.attempts if previous else 0) + 1
        inputs_hash = hash_stage_inputs(spec, variables, config_file.parent)

        if stage_plan.status == "reusable" and not force:
            record = StageRecord(
                stage_id=spec.stage_id,
                action="reused",
                status="succeeded",
                inputs_hash=inputs_hash,
                outputs_hash=previous.outputs_hash if previous else "",
                attempts=previous.attempts if previous else 0,
                note="outputs verified and inputs unchanged; not rerun",
                updated_at=datetime.now(UTC).isoformat(),
            )
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
            continue

        action = "forced" if force else ("resumed" if previous else "ran")
        # A stage about to (re)run owns its declared outputs: clear them so
        # immutable writers can rebuild. Reused stages never reach here.
        _clear_declared_outputs(spec, variables, config_file.parent, output_root)
        try:
            if spec.kind == "local_copy":
                destinations = [
                    resolve_variables(spec.copy_to, variables, where=f"stage '{spec.stage_id}'")
                ]
                for destination in destinations:
                    _copy_bounded(
                        [
                            resolve_variables(s, variables, where=f"stage '{spec.stage_id}'")
                            for s in spec.copy_from
                        ],
                        Path(destination),
                        spec.max_bytes,
                        variables,
                        bounds,
                    )
                bounds.check()
            else:
                _run_command(stage_plan.command, root, stage_timeout_seconds, bounds)
            bounds.validate_outputs(
                [Path(resolve_variables(p, variables, where="stage outputs")) for p in spec.outputs]
            )
        except (PrepareRunError, ValueError, RuntimeError, OSError) as exc:
            record = StageRecord(
                stage_id=spec.stage_id,
                action=action,
                status="failed",
                inputs_hash=inputs_hash,
                attempts=attempts,
                note=str(exc)[:500],
                updated_at=datetime.now(UTC).isoformat(),
            )
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
            raise PrepareRunError(f"stage '{spec.stage_id}' failed: {exc}") from exc

        # Post-run verification decides succeeded vs partial (resume next time).
        if stage_outputs_verified(spec, variables, config_file.parent):
            status, note = (
                "succeeded",
                ("forced rebuild recorded" if force else "outputs verified after run"),
            )
        else:
            status, note = "partial", "outputs incomplete after run; will resume"
        record = StageRecord(
            stage_id=spec.stage_id,
            action=action,
            status=status,
            inputs_hash=inputs_hash,
            outputs_hash=(
                hash_stage_outputs(spec, variables, config_file.parent)
                if status == "succeeded"
                else ""
            ),
            attempts=attempts,
            note=note,
            updated_at=datetime.now(UTC).isoformat(),
        )
        records[spec.stage_id] = record
        result.stages.append(record)
        persist()
        if status != "succeeded":
            raise PrepareRunError(f"stage '{spec.stage_id}' outputs incomplete after run")

    return result


def _execute_check_only(
    spec: Any, command: list[str], root: Path, timeout: float, bounds: PrepareBounds
) -> StageRecord:
    try:
        _run_command(command, root, timeout, bounds)
        note = "check passed"
        status = "succeeded"
    except PrepareRunError as exc:
        note = f"check reported: {str(exc)[:300]}"
        status = "failed"
    return StageRecord(
        stage_id=spec.stage_id,
        action="check-only",
        status=status,
        note=note,
        updated_at=datetime.now(UTC).isoformat(),
    )


def _run_command(command: list[str], root: Path, timeout: float, bounds: PrepareBounds) -> str:
    if not command:
        raise PrepareRunError("empty stage command")
    # Stage commands are `xlm` subcommand argv (e.g. ["data", "clean", ...]) and
    # always execute through this interpreter, never a mutable PATH lookup.
    argv = [
        sys.executable,
        "-m",
        "xlm.cli.main",
        *(command[1:] if command[0] == "xlm" else command),
    ]
    try:
        return bounds.run(argv, root, timeout)
    except (ValueError, RuntimeError, OSError) as exc:
        raise PrepareRunError(str(exc)) from exc


def run_prepare(
    config: PrepareConfig,
    config_path: Path | str,
    home: Path | str,
    repo_root: Path | str | None = None,
    *,
    authorize: bool = False,
    force: bool = False,
    only: list[str] | None = None,
    stage_timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS,
) -> PrepareResult:
    if not authorize:
        raise PrepareRunError(
            "refusing to execute without explicit authorization: rerun with --authorize"
        )
    validate_component(config.id)
    config = PrepareConfig.model_validate(config.model_dump())
    root = Path(repo_root) if repo_root else find_repo_root(Path(config_path).parent)
    variables = build_variable_mapping(config, Path(config_path), Path(home), root)
    output = Path(variables["output_root"])
    ensure_plain_path(output)
    output.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(output / ".prepare.lock"), timeout=1):
            bounds = PrepareBounds(config, output, Path(home), Path(config_path).parent)
            bounds.journal.set_status("IN_PROGRESS")
            try:
                result = _run_prepare(
                    config,
                    config_path,
                    home,
                    root,
                    authorize=authorize,
                    force=force,
                    only=only,
                    stage_timeout_seconds=stage_timeout_seconds,
                    bounds=bounds,
                )
            except BaseException as exc:
                bounds.journal.set_status("FAILED", str(exc)[:500])
                raise
            bounds.journal.set_status("COMPLETED")
            return result
    except (ValueError, RuntimeError, OSError) as exc:
        raise PrepareRunError(str(exc)) from exc
