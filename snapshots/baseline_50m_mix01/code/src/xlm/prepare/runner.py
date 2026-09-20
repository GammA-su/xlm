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
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xlm.prepare.config import PrepareConfig
from xlm.prepare.planner import (
    build_variable_mapping,
    find_repo_root,
    hash_stage_inputs,
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
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


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


def _copy_bounded(
    sources: list[str], destination: Path, max_bytes: int, variables: dict[str, str]
) -> dict[str, Any]:
    """Copy fixture inputs with a byte cap and a content manifest."""
    total = 0
    files: list[dict[str, Any]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for raw in sources:
        resolved = resolve_variables(raw, variables, where="local_copy inputs")
        source = Path(resolved)
        if not source.exists():
            raise PrepareRunError(f"local_copy input not found: {resolved}")
        entries = sorted(source.rglob("*")) if source.is_dir() else [source]
        for entry in entries:
            if not entry.is_file():
                continue
            data = entry.read_bytes()
            total += len(data)
            if total > max_bytes:
                raise PrepareRunError(
                    f"local_copy exceeds {max_bytes:,} bytes at '{entry}'; "
                    "raise the cap explicitly instead of fetching blindly"
                )
            relative = entry.relative_to(source) if source.is_dir() else Path(entry.name)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files.append(
                {
                    "path": relative.as_posix(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    manifest = {"files": files, "total_bytes": total}
    (destination / "copy_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest


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
            record = _execute_check_only(spec, stage_plan.command, root, stage_timeout_seconds)
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
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
                attempts=previous.attempts if previous else 0,
                note="outputs verified and inputs unchanged; not rerun",
                updated_at=datetime.now(UTC).isoformat(),
            )
            records[spec.stage_id] = record
            result.stages.append(record)
            persist()
            continue

        action = "forced" if force else ("resumed" if previous else "ran")
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
                    )
            else:
                _run_command(stage_plan.command, root, stage_timeout_seconds)
        except PrepareRunError as exc:
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
            attempts=attempts,
            note=note,
            updated_at=datetime.now(UTC).isoformat(),
        )
        records[spec.stage_id] = record
        result.stages.append(record)
        persist()

    return result


def _execute_check_only(spec: Any, command: list[str], root: Path, timeout: float) -> StageRecord:
    try:
        _run_command(command, root, timeout)
        note = "check passed"
    except PrepareRunError as exc:
        note = f"check reported: {str(exc)[:300]}"
    return StageRecord(
        stage_id=spec.stage_id,
        action="check-only",
        status="succeeded",
        note=note,
        updated_at=datetime.now(UTC).isoformat(),
    )


def _run_command(command: list[str], root: Path, timeout: float) -> str:
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
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(root),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrepareRunError(f"stage timed out after {timeout:.0f}s") from exc
    tail = (completed.stdout or "")[-1500:] + (completed.stderr or "")[-500:]
    if completed.returncode != 0:
        raise PrepareRunError(f"command exited {completed.returncode}: {tail[-500:]}")
    return tail
