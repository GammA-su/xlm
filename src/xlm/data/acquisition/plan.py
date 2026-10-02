"""Acquisition plan schemas and authorization validation complying with C01 and C04."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xlm.artifacts.manifest import ensure_plain_path, validate_component, validate_file_set
from xlm.data.acquisition.source_growth import ProcessingGrowth
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
        # The record bound is checked against the parser bound where the parser
        # bound also caps the buffered bytes a record is decoded from (selected
        # records, see AcquisitionPlan); a whole local file has no such buffer.
        return self


#: Pilot ceilings (C13) for physical work. The legacy gate binds only
#: transfer/records/output; window-decode plans additionally bind these.
PILOT_MAX_TRANSFERRED_BYTES = 256 * 1024 * 1024
PILOT_MAX_RECORDS = 25_000
PILOT_MAX_OUTPUT_DISK_BYTES = 2 * 1024 * 1024 * 1024
PILOT_MAX_DECOMPRESSED_BYTES = 512 * 1024 * 1024
PILOT_MAX_SCANNED_RECORDS = 100_000
PILOT_MAX_REQUESTS = 100
PILOT_MAX_WINDOW_BUFFER_BYTES = 8 * 1024 * 1024
PILOT_MAX_RATIO_EXEMPT_BYTES = 16 * 1024 * 1024

PARQUET_WINDOW_POLICY_VERSION = 1
#: 1 = flat projections only (frozen); 2 = adds scalar/struct nested projections.
SUPPORTED_WINDOW_POLICY_VERSIONS = (1, 2)


class ParquetWindowDecode(BaseModel):
    """Streaming sub-row-group decode for one bounded window per Parquet file.

    Physical semantics (policy version 1): the selected row range of every
    file lies inside ONE row group. Only projected column chunks are read,
    sequentially through a buffered stream of ``stream_buffer_bytes`` per
    column (each HTTP range <= that size, never a whole chunk), and decoding
    stops at the first batch reaching the window stop. Rows from the row
    group start up to the window stop are decoded and charged as scanned;
    there is no page skipping (PyArrow exposes no page-index row seeking).

    Policy version 1 is frozen: flat projections only (one physical leaf per
    projected field; nested fields refused). Version 2 keeps every v1 bound
    and adds nested STRUCT projections: a logical field is accounted over
    ALL physical leaves beneath it (``xlm.data.acquisition.projection``) and
    decoded as the logical field so structs reconstruct exactly; repeated
    leaves (lists/maps) stay refused. The version is part of the model dump,
    so it binds the behavioral hash, authorization and the window start.

    Bound into the behavioral hash only when set, so legacy plans keep
    their identity. Excluded from the selection hash: records and locators
    are byte-identical to the whole-group projected decode of the same rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: int = PARQUET_WINDOW_POLICY_VERSION
    stream_buffer_bytes: int = Field(ge=64 * 1024)
    max_window_scan_rows: int = Field(ge=1)
    batch_rows: int = Field(default=256, ge=1, le=65536)
    ratio_exempt_bytes: int = Field(
        default=16 * 1024 * 1024,
        ge=0,
        description=(
            "A projected column chunk whose TOTAL uncompressed size is at most this "
            "is exempt from the per-column ratio rule: its absolute expansion is "
            "bounded by this size (low-entropy ids/labels compress far beyond the "
            "ratio yet cannot amplify). Larger chunks and the projected aggregate "
            "stay ratio-bound; decoded bytes stay charged against the hard limit."
        ),
    )

    @model_validator(mode="after")
    def supported_version(self) -> ParquetWindowDecode:
        if self.policy_version not in SUPPORTED_WINDOW_POLICY_VERSIONS:
            raise ValueError(f"unsupported parquet window policy_version {self.policy_version}")
        return self

    def ratio_refusal(
        self, columns: Iterable[tuple[str, int, int]], max_ratio: float
    ) -> str | None:
        """Decompression-ratio refusal over PROJECTED ``(path, compressed, uncompressed)``.

        Single rule shared by sampling eligibility and fetch-time checks so
        they cannot disagree. Unprojected chunks are never read and never
        passed here.
        """
        total_compressed = total_uncompressed = 0
        for path, compressed, uncompressed in columns:
            total_compressed += compressed
            total_uncompressed += uncompressed
            if uncompressed > self.ratio_exempt_bytes and uncompressed > max_ratio * max(
                1, compressed
            ):
                return (
                    f"projected column '{path}' exceeds decompression ratio bound: "
                    f"uncompressed={uncompressed} against max_decompression_ratio="
                    f"{max_ratio} * compressed={compressed} "
                    f"(ratio_exempt_bytes={self.ratio_exempt_bytes})"
                )
        if total_uncompressed > self.ratio_exempt_bytes and total_uncompressed > max_ratio * max(
            1, total_compressed
        ):
            return (
                f"projected columns exceed aggregate decompression ratio bound: "
                f"uncompressed={total_uncompressed} against max_decompression_ratio="
                f"{max_ratio} * compressed={total_compressed}"
            )
        return None

    def expected_scan_rows(self, group_rows: int, stop_in_group: int) -> int:
        """Rows decoded for a window ending at ``stop_in_group`` (whole batches)."""
        if not 0 < stop_in_group <= group_rows:
            raise ValueError("window stop must lie inside its row group")
        batches = -(-stop_in_group // self.batch_rows)
        return min(group_rows, batches * self.batch_rows)


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
    selected_file_limit: int = Field(default=256, ge=1, le=384)
    row_ranges: dict[str, tuple[int, int]] | None = None
    projected_fields: list[str] | None = Field(
        default=None,
        description=(
            "Optional explicit Parquet column projection for selected_records. "
            "None preserves legacy all-columns behavior with identical identity."
        ),
    )
    range_coalesce_bytes: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional explicit gap threshold for coalescing adjacent Parquet "
            "column-chunk ranges. None preserves legacy exact-range behavior."
        ),
    )
    parquet_window: ParquetWindowDecode | None = Field(
        default=None,
        description=(
            "Optional streaming sub-row-group window decode for selected_records. "
            "None preserves legacy whole-row-group decode with identical identity."
        ),
    )
    sampling_frame: SamplingFrame = Field(default_factory=SamplingFrame)
    expected_bytes: int | None = None
    source_processing_growth: ProcessingGrowth | None = None
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
        if self.is_pilot and self.selected_file_limit > 256:
            raise ValueError("pilot acquisition supports at most 256 selected files")
        if len(self.selected_files) > self.selected_file_limit:
            raise ValueError(
                f"acquisition supports at most {self.selected_file_limit} selected files"
            )
        if set(self.expected_file_digests) - set(self.selected_files):
            raise ValueError("expected digests refer to unselected files")
        for digest in self.expected_file_digests.values():
            if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError("expected digest must be SHA-256 hex")
        if self.mode == AcquisitionMode.SELECTED_RECORDS:
            if self.limits.max_record_bytes > self.limits.max_parser_bytes:
                raise ValueError("record byte bound exceeds parser byte bound")
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
            if self.projected_fields is not None and (
                not self.projected_fields
                or any(not isinstance(name, str) or not name for name in self.projected_fields)
            ):
                raise ValueError("projected fields must be a nonempty list of names")
        elif self.row_ranges:
            raise ValueError("whole-file mode cannot declare selected row ranges")
        if self.mode != AcquisitionMode.SELECTED_RECORDS and (
            self.projected_fields is not None or self.range_coalesce_bytes is not None
        ):
            raise ValueError("whole-file mode cannot declare projection/coalescing options")
        if self.parquet_window is not None:
            self._validate_parquet_window(self.parquet_window)
        return self

    def _validate_parquet_window(self, window: ParquetWindowDecode) -> None:
        if self.mode != AcquisitionMode.SELECTED_RECORDS:
            raise ValueError("parquet window decode requires selected_records mode")
        if self.projected_fields is None:
            raise ValueError("parquet window decode requires an explicit column projection")
        if self.range_coalesce_bytes is not None:
            raise ValueError("parquet window decode streams chunks; coalescing is not applicable")
        if any(not name.endswith(".parquet") for name in self.selected_files):
            raise ValueError("parquet window decode requires Parquet files only")
        if window.stream_buffer_bytes > self.limits.max_parser_bytes:
            raise ValueError("window stream buffer exceeds the per-range byte bound")
        if window.max_window_scan_rows > self.limits.max_scanned_records:
            raise ValueError("window scan rows exceed the cumulative scanned-record bound")
        if window.batch_rows > window.max_window_scan_rows:
            raise ValueError("window batch rows exceed the window scan bound")
        if window.ratio_exempt_bytes > self.limits.max_decompressed_bytes:
            raise ValueError("window ratio exemption exceeds the decompressed byte bound")
        for start, stop in (self.row_ranges or {}).values():
            if stop - start > window.max_window_scan_rows:
                raise ValueError("selected window exceeds the window scan bound")

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
        if self.source_processing_growth is not None:
            behavioral_dict["source_processing_growth"] = self.source_processing_growth.model_dump()
        if self.selected_file_limit != 256:
            behavioral_dict["selected_file_limit"] = self.selected_file_limit
        # Attempt 1 is the legacy identity element: plans written before the
        # attempt counter existed hash exactly as before, so their recorded
        # plan_hash values keep verifying. Higher attempts bind a distinct
        # execution identity (and authorization) to identical behavior.
        if self.attempt != 1:
            behavioral_dict["attempt"] = self.attempt
        # Projection/coalescing options are bound only when explicitly set:
        # None preserves the legacy all-columns exact-range identity so old
        # plans keep verifying and never silently reuse a new artifact.
        if self.projected_fields is not None:
            behavioral_dict["projected_fields"] = sorted(self.projected_fields)
        if self.range_coalesce_bytes is not None:
            behavioral_dict["range_coalesce_bytes"] = self.range_coalesce_bytes
        # Physical decode policy changes transfer/scan behavior, so it binds
        # execution identity (and authorization) when set; None is legacy.
        if self.parquet_window is not None:
            behavioral_dict["parquet_window"] = self.parquet_window.model_dump()
        canonical_json = json.dumps(behavioral_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def compute_selection_hash(self) -> str:
        """Logical upstream selection identity for ``selected_records.jsonl`` bytes.

        Contract (deterministic-output semantics only):
        Included (logical selection):
        - ``schema_version``, ``source_id``, ``view_id``, ``provider``,
          ``repository``, immutable ``revision``, ``mode``
        - ``selected_files`` in plan order (output concatenation order),
          ``row_ranges``, ``sampling_frame`` (seed/method), ``expected_bytes``,
          ``expected_file_digests``, ``admitted_source_reference``, ``is_pilot``
        - resource ``limits`` with ``max_workers`` normalized to 1 (concurrency
          must not change bytes; other bounds stay bound and fail-closed)
        - ``projected_fields`` when explicitly set (projection changes which
          source fields reach published records, so it binds selection
          identity; ``None`` preserves the legacy all-columns identity)
        Excluded (execution identity, never changes bytes):
        - ``attempt`` number, ``plan_id``/``plan_hash``, ``authorization``
          (hash/timestamp), ``output_artifact_id`` (derived output naming),
          ``range_coalesce_bytes`` (transport framing only; records identical),
          ``parquet_window`` (physical decode policy; records identical),
          runtime deadlines, observational telemetry.
        Execution identity stays in :meth:`compute_behavioral_hash`,
        journals, receipts, and authorizations; only the per-record
        ``selection_hash`` locator uses this. Attempt renewal therefore keeps
        an independently auditable execution identity while producing
        byte-identical selection bytes for identical logical selection.
        """

        limits = self.limits.model_copy(update={"max_workers": 1}).model_dump()
        logical_dict: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "view_id": self.view_id,
            "provider": self.provider,
            "repository": self.repository,
            "revision": self.revision,
            "mode": self.mode.value,
            "selected_files": list(self.selected_files),
            "row_ranges": self.row_ranges,
            "sampling_frame": self.sampling_frame.model_dump(),
            "expected_bytes": self.expected_bytes,
            "expected_file_digests": dict(sorted(self.expected_file_digests.items())),
            "limits": limits,
            "admitted_source_reference": self.admitted_source_reference,
            "is_pilot": self.is_pilot,
        }
        if self.projected_fields is not None:
            logical_dict["projected_fields"] = sorted(self.projected_fields)
        canonical_json = json.dumps(logical_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def compute_legacy_worker_normalized_hash(self) -> str:
        """Intermediate post-fix hash (workers→1, attempt still bound).

        Preserved only to verify acquisitions already written with the
        first concurrency fix (e.g. FinePDF attempts 13/14) where ``attempt``
        was still included. New acquisitions use :meth:`compute_selection_hash`.
        """

        normalized = self.model_copy(
            update={"limits": self.limits.model_copy(update={"max_workers": 1})}
        )
        return normalized.compute_behavioral_hash()

    def accepted_selection_hashes(self) -> set[str]:
        """Locator selection hashes honored by verification and adaptation.

        The logical hash (new fetcher output), the intermediate
        worker-normalized hash (first concurrency fix, e.g. FinePDF attempts
        13/14), and the behavioral hash (hand-built selections in existing
        tests). Foreign selections match none and stay refused.
        """

        return {
            self.compute_selection_hash(),
            self.compute_legacy_worker_normalized_hash(),
            self.compute_behavioral_hash(),
        }

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

    Window-decode plans also bind physical work (decompression, scanned
    records, requests, window scan rows, stream buffer) to pilot ceilings,
    so a small retained count can never carry an oversized physical scan
    onto the pilot path. Legacy plans keep the original three checks.
    """
    limits = plan.limits
    if (
        limits.max_transferred_bytes > PILOT_MAX_TRANSFERRED_BYTES
        or limits.max_records > PILOT_MAX_RECORDS
        or limits.max_output_disk_bytes > PILOT_MAX_OUTPUT_DISK_BYTES
        or not plan.is_pilot
    ):
        return True
    window = plan.parquet_window
    return window is not None and (
        limits.max_decompressed_bytes > PILOT_MAX_DECOMPRESSED_BYTES
        or limits.max_scanned_records > PILOT_MAX_SCANNED_RECORDS
        or limits.max_requests > PILOT_MAX_REQUESTS
        or window.max_window_scan_rows > PILOT_MAX_SCANNED_RECORDS
        or window.stream_buffer_bytes > PILOT_MAX_WINDOW_BUFFER_BYTES
        or window.ratio_exempt_bytes > PILOT_MAX_RATIO_EXEMPT_BYTES
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
