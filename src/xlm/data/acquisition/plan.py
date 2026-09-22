"""Acquisition plan schemas and authorization validation complying with C01 and C04."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xlm.artifacts.manifest import ensure_plain_path, validate_component, validate_file_set
from xlm.data.sources.policy import check_denial_policy


class AcquisitionMode(StrEnum):
    """Mode of acquisition: whole original files vs selected record extraction."""

    WHOLE_FILE = "whole_file"
    SELECTED_RECORDS = "selected_records"


class AcquisitionLimits(BaseModel):
    """Strict resource bounds for network, memory, decompression, and disk capacities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_transferred_bytes: int = 256 * 1024 * 1024  # 256 MiB default pilot limit
    max_decompressed_bytes: int = 512 * 1024 * 1024  # 512 MiB
    max_records: int = 25_000  # 25,000 records default pilot limit
    max_temp_disk_bytes: int = 1024 * 1024 * 1024  # 1 GiB scratch disk limit
    max_output_disk_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB local output limit
    max_requests: int = 100
    max_retries: int = 5
    per_request_timeout_seconds: float = 15.0
    overall_deadline_seconds: float = 600.0  # 10 minute execution deadline
    max_decompression_ratio: float = 15.0  # Zip-bomb protection limit
    max_workers: int = 2
    max_record_bytes: int = 1024 * 1024
    max_parser_bytes: int = 32 * 1024 * 1024
    max_scanned_records: int = 100000

    @model_validator(mode="after")
    def bounded_limits(self) -> AcquisitionLimits:
        for name, value in self.model_dump().items():
            if not math.isfinite(value) or value < (0 if name == "max_retries" else 1):
                raise ValueError(f"{name} must be positive (retries may be zero)")
        if self.max_workers > 16 or self.max_retries > 20:
            raise ValueError("at most 16 workers and 20 retries are supported")
        if self.max_record_bytes > self.max_parser_bytes:
            raise ValueError("record byte bound exceeds parser byte bound")
        return self


class SamplingFrame(BaseModel):
    """Explicit disclosure of sampling frame, file selection, and non-uniformity bias."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_remote_files: int | None = None
    selected_files: list[str] = Field(default_factory=list)
    selection_seed: int | None = None
    sampling_method: str = "explicit_file_list"
    coverage_notes: str = ""
    selection_bias_warning: str = (
        "Warning: Selected subset is not a uniform random sample of the full corpus. "
        "Domain, temporal, and shard clustering effects apply."
    )


class PlanAuthorization(BaseModel):
    """Audit record authorizing acquisition execution for pilot or production scopes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_hash: str
    authorized_by: str
    authorized_at: str
    scope: str = "pilot"  # 'pilot' | 'production'
    is_pilot_approved: bool = False
    bound_limits_hash: str = ""


class AcquisitionPlan(BaseModel):
    """Immutable acquisition plan adhering to Contracts C01 and C04."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 2
    plan_id: str
    source_id: str
    view_id: str = "default"
    provider: str  # 'huggingface' | 'https' | 'local'
    repository: str
    revision: str  # Immutable commit SHA, git tag, or local file hash
    mode: AcquisitionMode = AcquisitionMode.WHOLE_FILE
    selected_files: list[str] = Field(min_length=1)
    row_ranges: dict[str, tuple[int, int]] | None = None
    sampling_frame: SamplingFrame = Field(default_factory=SamplingFrame)
    expected_bytes: int | None = None
    expected_file_digests: dict[str, str] = Field(
        default_factory=dict,
        description="Optional independent expected SHA-256 digests mapped by relative file path.",
    )
    limits: AcquisitionLimits = Field(default_factory=AcquisitionLimits)
    output_artifact_id: str
    admitted_source_reference: str | None = None
    is_pilot: bool = True
    attempt: int = Field(
        default=1,
        ge=1,
        le=999,
        description=(
            "Operator-declared fresh-attempt counter. Attempt 1 is the original "
            "execution identity; a renewed attempt after an expired deadline uses "
            "a higher attempt with identical source/view/revision/selection/limits, "
            "yielding a distinct plan identity and journal while the old attempt's "
            "accounting is preserved untouched."
        ),
    )
    authorization: PlanAuthorization | None = None
    plan_hash: str = ""

    @model_validator(mode="after")
    def validate_paths_and_selection(self) -> AcquisitionPlan:
        for value in (self.plan_id, self.source_id, self.view_id, self.output_artifact_id):
            validate_component(value)
        validate_file_set(self.selected_files)
        if "acquisition_receipt.json" in self.selected_files:
            raise ValueError("source filename collides with publication receipt")
        if len(self.selected_files) > 256:
            raise ValueError("acquisition supports at most 256 selected files")
        if set(self.expected_file_digests) - set(self.selected_files):
            raise ValueError("expected digests refer to unselected files")
        for digest in self.expected_file_digests.values():
            if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError("expected digest must be SHA-256 hex")
        if self.mode == AcquisitionMode.SELECTED_RECORDS:
            if not self.row_ranges or set(self.row_ranges) != set(self.selected_files):
                raise ValueError("selected records require an explicit row range for every file")
            for start, stop in self.row_ranges.values():
                if not 0 <= start < stop:
                    raise ValueError("row ranges must be nonempty zero-based half-open intervals")
            if (
                sum(stop - start for start, stop in self.row_ranges.values())
                > self.limits.max_records
            ):
                raise ValueError("selected row ranges exceed record limit")
        elif self.row_ranges:
            raise ValueError("whole-file mode cannot declare selected row ranges")
        return self

    def compute_behavioral_hash(self) -> str:
        """Compute SHA-256 digest over behavioral fields.

        Strictly excludes self-referential plan_id, plan_hash, authorization records,
        and cosmetic timestamps to ensure reproducible plan identity.
        """
        behavioral_dict: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "view_id": self.view_id,
            "provider": self.provider,
            "repository": self.repository,
            "revision": self.revision,
            "mode": self.mode.value,
            "selected_files": sorted(self.selected_files),
            "row_ranges": self.row_ranges,
            "sampling_frame": self.sampling_frame.model_dump(),
            "expected_bytes": self.expected_bytes,
            "expected_file_digests": dict(sorted(self.expected_file_digests.items())),
            "limits": self.limits.model_dump(),
            "output_artifact_id": self.output_artifact_id,
            "admitted_source_reference": self.admitted_source_reference,
            "is_pilot": self.is_pilot,
        }
        # Attempt 1 is the legacy identity element: plans written before the
        # attempt counter existed hash exactly as before, so their recorded
        # plan_hash values keep verifying. Higher attempts bind a distinct
        # execution identity (and authorization) to identical behavior.
        if self.attempt != 1:
            behavioral_dict["attempt"] = self.attempt
        canonical_json = json.dumps(behavioral_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def compute_selection_hash(self) -> str:
        """Stable selected-record identity independent of file-worker concurrency.

        Normalizes ``max_workers`` to 1 so ``max_workers=1/2/4`` produce
        byte-identical ``selected_records.jsonl`` for identical source data,
        files, row ranges, revision, and remaining limits. All other
        behavioral fields (including ``attempt`` and remaining limits) still
        bind identity. Journals, receipts, and authorizations keep using
        :meth:`compute_behavioral_hash`; only the per-record
        ``selection_hash`` locator uses this.
        """
        normalized = self.model_copy(
            update={"limits": self.limits.model_copy(update={"max_workers": 1})}
        )
        return normalized.compute_behavioral_hash()

    def with_computed_hash(self) -> AcquisitionPlan:
        """Return a copy of the plan with the canonical behavioral plan_hash populated."""
        b_hash = self.compute_behavioral_hash()
        return self.model_copy(update={"plan_hash": b_hash})


class AuthorizationRequiredError(RuntimeError):
    """Raised when an acquisition plan lacks required operator pilot approval or authorization."""


class SourceDriftDetectedError(RuntimeError):
    """Raised when an upstream source entity has drifted from its approved plan identity."""


def plan_requires_production_admission(plan: AcquisitionPlan) -> bool:
    """True when a plan leaves the pilot path (over C13 thresholds or non-pilot).

    Single source of truth for the pilot/production boundary; the validator
    and the fetch command both use it so they cannot disagree.
    """
    return (
        plan.limits.max_transferred_bytes > 256 * 1024 * 1024
        or plan.limits.max_records > 25_000
        or plan.limits.max_output_disk_bytes > 2 * 1024 * 1024 * 1024
        or not plan.is_pilot
    )


def validate_plan_authorization(
    plan: AcquisitionPlan,
    catalog_source_approved: bool = False,
) -> None:
    """Strictly validate execution rights for pilot or production plans.

    Enforces:
    1. Direct-source denial check (Contract C04).
    2. Immutably pinned revision (no 'latest', 'master', or empty string).
    3. Pilot approval check: being below pilot thresholds does not grant execution rights;
       explicit operator pilot approval is mandatory.
    4. Production authorization: operations exceeding pilot thresholds require production
       admission plus a valid authorization hash binding full limits.
    """
    check_denial_policy(plan.repository)
    # Revalidate model_copy inputs before any filesystem or transport side effect.
    AcquisitionPlan.model_validate(plan.model_dump())
    if plan.schema_version != 2:
        raise ValueError(
            "legacy acquisition plans require a newly reviewed v2 plan; originals are preserved"
        )

    if not plan.revision or plan.revision.lower() in ("latest", "master", "main", "head", "todo"):
        raise ValueError(
            f"Plan '{plan.plan_id}' must specify an immutable revision SHA; got '{plan.revision}'."
        )

    # Check limits against pilot boundaries (C13: 256 MiB, 25k records, 2 GiB output)
    if plan_requires_production_admission(plan):
        # Production execution path
        if not catalog_source_approved:
            raise AuthorizationRequiredError(
                f"Production acquisition for '{plan.source_id}:{plan.view_id}' requires "
                "prior operator admission verified by the caller; "
                "an unresolved admission reference is insufficient."
            )
        if not plan.authorization:
            raise AuthorizationRequiredError(
                f"Production acquisition for plan '{plan.plan_id}' requires an explicit "
                "PlanAuthorization matching the plan's behavioral hash and full resolved limits."
            )
        b_hash = plan.compute_behavioral_hash()
        if plan.authorization.authorization_hash != b_hash:
            raise AuthorizationRequiredError(
                f"Authorization hash mismatch for plan '{plan.plan_id}': "
                f"expected '{b_hash}', got '{plan.authorization.authorization_hash}'."
            )
    else:
        # Pilot execution path: requires explicit pilot approval
        if not plan.authorization or not plan.authorization.is_pilot_approved:
            raise AuthorizationRequiredError(
                f"Pilot execution for plan '{plan.plan_id}' requires explicit operator pilot "
                "approval (pass --pilot-approved or supply PlanAuthorization with "
                "is_pilot_approved=True)."
            )
        if plan.authorization.authorization_hash != plan.compute_behavioral_hash():
            raise AuthorizationRequiredError("Authorization hash mismatch for pilot plan")


def save_acquisition_plan(plan: AcquisitionPlan, path: Path) -> Path:
    """Save acquisition plan to JSON or YAML file."""
    ensure_plain_path(path)
    plan_with_hash = plan.with_computed_hash()
    if path.exists():
        existing = load_acquisition_plan(path)
        if existing.compute_behavioral_hash() != plan_with_hash.compute_behavioral_hash():
            raise ValueError("acquisition plan conflict; use a new reviewed plan/output path")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(plan_with_hash.model_dump(), stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def load_acquisition_plan(path: Path) -> AcquisitionPlan:
    """Load acquisition plan from file and verify schema integrity."""
    if not path.is_file():
        raise FileNotFoundError(f"Acquisition plan file not found: {path}")
    ensure_plain_path(path)
    if path.stat().st_size > 1024**2:
        raise ValueError("acquisition plan exceeds 1 MiB")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    plan = AcquisitionPlan.model_validate(data)
    expected_hash = plan.compute_behavioral_hash()
    if plan.plan_hash and plan.plan_hash != expected_hash:
        raise ValueError(
            f"Plan hash mismatch for '{plan.plan_id}': "
            f"recorded {plan.plan_hash} != computed {expected_hash}"
        )
    return plan
