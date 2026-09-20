"""Acquisition receipt schema capturing evidence-based checksums, resources, and approval scope."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AcquiredFileInfo(BaseModel):
    """Integrity and lineage evidence for a single acquired file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relative_path: str
    size_bytes: int
    locally_computed_sha256: str
    provider_declared_checksum: str | None = None
    independent_expected_sha256: str | None = None
    independent_checksum_verified: bool = False
    record_count: int | None = None


class AcquisitionReceipt(BaseModel):
    """Formal audit receipt documenting a completed data acquisition run adhering to C04."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    receipt_id: str
    plan_id: str
    plan_hash: str
    source_id: str
    view_id: str
    provider: str
    repository: str
    revision: str
    mode: str
    eligibility: str = "pilot_only"  # 'pilot_only' | 'training_eligible'
    files: list[AcquiredFileInfo] = Field(default_factory=list)
    resource_metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    is_verified: bool = False
    verification_notes: list[str] = Field(default_factory=list)
