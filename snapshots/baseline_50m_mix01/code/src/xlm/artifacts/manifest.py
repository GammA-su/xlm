"""Manifest schema and integrity verification for XLM artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactFile(BaseModel):
    """File entry in an artifact manifest."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)  # Relative path within artifact directory
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ArtifactManifest(BaseModel):
    """Immutable manifest for published artifacts complying with Contract C01/C07."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: Literal["pending", "completed"] = "completed"
    created_at: str
    producer_code_hash: str = Field(min_length=8)
    dependency_hash: str = Field(min_length=8)
    resolved_config_hash: str = Field(min_length=8)
    input_artifact_ids: list[str] = Field(default_factory=list)
    files: list[ArtifactFile]
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_manifest_path(artifact_dir: Path, rel_path: str) -> Path:
    """Validate relative file path against path traversal attacks."""
    target = (artifact_dir / rel_path).resolve()
    try:
        target.relative_to(artifact_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Path traversal detected: '{rel_path}' is outside artifact directory '{artifact_dir}'") from exc
    return target
