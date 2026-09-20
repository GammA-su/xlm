"""Prepare configuration: explicit stages, approvals and budgets (P22).

A prepare config declares every stage's exact command, the approvals each
stage needs, the outputs that prove it, and the downstream stages it
invalidates. Nothing is inferred: an undeclared approval blocks, an
undeclared output cannot prove reuse, and unknown variables fail closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from xlm.config.composer import load_yaml_file
from xlm.config.schemas import StrictConfigModel

PREPARE_CONFIG_VERSION = "1"


class PrepareBudgets(StrictConfigModel):
    """Aggregate allowances persist across stages, retries, force and restarts."""

    fetch_max_bytes: int = Field(default=64 * 1024**2, ge=1)
    max_subprocess_output_bytes: int = Field(default=16 * 1024**2, ge=1)
    max_temp_disk_bytes: int = Field(default=64 * 1024**2, ge=1)
    max_output_disk_bytes: int = Field(default=2 * 1024**3, ge=1)
    max_decompressed_bytes: int = Field(default=512 * 1024**2, ge=1)
    max_record_bytes: int = Field(default=1024**2, ge=1)
    max_records: int = Field(default=100000, ge=1)
    overall_deadline_seconds: float = Field(default=1800, gt=0)
    max_attempts: int = Field(default=256, ge=1, le=4096)
    max_network_requests: int = Field(default=100, ge=1)


class PrepareStageSpec(StrictConfigModel):
    """One pipeline stage: a local copy or an explicit subprocess command."""

    stage_id: str = Field(min_length=1)
    kind: str = Field(default="run", description="'run' a command or 'local_copy' inputs")
    command: list[str] = Field(
        default_factory=list,
        description="Argv for kind=run; supports {repo}, {home}, {output_root} variables.",
    )
    copy_from: list[str] = Field(
        default_factory=list, description="Source paths for kind=local_copy."
    )
    copy_to: str = Field(default="", description="Destination directory for kind=local_copy.")
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    approvals: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    watched_inputs: list[str] = Field(
        default_factory=list, description="Files hashed to detect stale stages."
    )
    invalidates: list[str] = Field(
        default_factory=list, description="Downstream stages rerun when this one does."
    )
    check_only: bool = Field(
        default=False, description="Read-only stages always run and prove nothing."
    )

    @model_validator(mode="after")
    def validate_kind_shape(self) -> PrepareStageSpec:
        if self.kind not in ("run", "local_copy"):
            raise ValueError(f"stage '{self.stage_id}' has unknown kind '{self.kind}'")
        if self.kind == "run" and not self.command:
            raise ValueError(f"stage '{self.stage_id}' of kind run needs a command")
        if self.kind == "local_copy" and (not self.copy_from or not self.copy_to):
            raise ValueError(
                f"stage '{self.stage_id}' of kind local_copy needs copy_from and copy_to"
            )
        if self.check_only and self.outputs:
            raise ValueError(
                f"stage '{self.stage_id}' is check-only yet declares outputs; "
                "check-only stages prove nothing and always run"
            )
        return self


class PrepareConfig(StrictConfigModel):
    """A validated data-preparation configuration."""

    schema_version: int = Field(default=1)
    kind: str = Field(default="prepare_config")
    id: str = Field(min_length=1)
    output_root: str = Field(
        default="{home}/prepare",
        description="Stage outputs root; supports {repo}, {home}, {config_dir}.",
    )
    approvals: dict[str, bool] = Field(default_factory=dict)
    budgets: dict[str, Any] = Field(default_factory=dict)
    stages: list[PrepareStageSpec] = Field(min_length=1)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value != "prepare_config":
            raise ValueError(f"kind must be 'prepare_config', got '{value}'")
        return value

    @model_validator(mode="after")
    def validate_stage_graph(self) -> PrepareConfig:
        object.__setattr__(
            self, "budgets", PrepareBudgets.model_validate(self.budgets).model_dump()
        )
        ids = [stage.stage_id for stage in self.stages]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate stage_id values: {duplicates}")
        known = set(ids)
        for stage in self.stages:
            unknown = sorted(set(stage.invalidates) - known)
            if unknown:
                raise ValueError(f"stage '{stage.stage_id}' invalidates unknown stages: {unknown}")
            if any(ids.index(target) <= ids.index(stage.stage_id) for target in stage.invalidates):
                raise ValueError(
                    "invalidation edges must point to later stages; cycles are forbidden"
                )
        return self


def load_prepare_config(path: Path | str, defines: dict[str, str] | None = None) -> PrepareConfig:
    """Load and strictly validate a prepare config with --define overrides."""
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"prepare config not found: {config_path}")
    data = load_yaml_file(config_path)
    if not isinstance(data, dict):
        raise ValueError(f"prepare config at {config_path} must be a mapping")
    for key, value in (defines or {}).items():
        data[key] = value
    return PrepareConfig.model_validate(data)
