"""Deterministic production plans for non-Essential Mix-01 sources (offline).

A plan is a write-once, self-digested record derived only from frozen
artifacts: the Mix-01 quota and headroom estimate, the source's calibration
layout, its frozen deterministic inventory, its frozen transport policy, the
current store admission and the sealed outcome of earlier plans. The operator
never chooses file counts, prefixes, row ranges or byte ceilings by hand.

Selection is always the next contiguous run of the frozen inventory order,
starting at the cursor the previous plan left: plan 1 starts at rank 0, a
top-up plan starts where the last one stopped. A spent plan is never edited;
ordering never restarts; inventory positions reserved for bounded benchmarks
are never planned. Each plan names its predecessor's digest, so lineage is a
hash chain.

Its digest is what the operator reviews and authorizes; nothing runs on a
digest that was not explicitly authorized.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    SamplingFrame,
    plan_requires_production_admission,
)
from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.acquisition.transport_policy import (
    LOCAL_MODES,
    SUBJECT_KEYS,
    PolicyError,
    SourceLayout,
    TransportMode,
    check_frozen,
    check_subject,
)
from xlm.data.evidence_v2 import canonical

PLAN_KIND = "mix01_source_production_plan"
PLAN_VERSION = 1
PLANNER_RULES = "mix01-source-planner-v1"
MIB = 1024**2
GIB = 1024**3
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: Planner rules (versioned with PLANNER_RULES). Only one file of each source
#: has a measured size, so per-file bounds carry a tolerance; a larger file is
#: refused before any byte of it is transferred (fail closed, re-plan).
FILE_BYTES_TOLERANCE = 2.0
ROWS_TOLERANCE = 2.0
TRANSFER_FACTOR = 1.5
DECODED_BYTES_PER_FILE_FACTOR = 4
MAX_REQUESTS_PER_FILE = 16
MAX_RETRIES = 5
REQUEST_TIMEOUT_SECONDS = 30.0
#: A stalled file fails at about 0.3 MiB/s; a plan run is bounded to four hours.
MIN_FILE_RATE_BYTES_PER_SECOND = 0.3 * MIB
MIN_FILE_DEADLINE_SECONDS = 1800.0
PLAN_DEADLINE_SECONDS = 14400.0
MAX_RECORD_BYTES = 8 * MIB
MAX_PARSER_BYTES = 32 * MIB
#: Source-specific record bounds that replace the generic one, each with its
#: evidence. They enter the policy and the ``AcquisitionLimits`` and so the plan
#: digest and hash: a changed bound is a new plan that needs a new authorization.
#: A row above the bound still fails its unit closed (``RecordLimitError``).
SOURCE_RECORD_BYTES: dict[tuple[str, str], tuple[int, str]] = {
    ("finepdfs_edu", "eng_Latn"): (
        MAX_PARSER_BYTES,
        "finepdfs-record-v1: whole-file scan of data/eng_Latn/train/000_00083.parquet "
        "(220,407 rows, sha256 4eeb58bc...a38d) found a 24,828,818-byte largest "
        "projected row and 3 rows above the generic 8 MiB; the bound is the existing "
        "32 MiB parser ceiling, about 1.35x that maximum",
    ),
}
MAX_LEDGER_BYTES = 512 * MIB
MAX_DECOMPRESSION_RATIO = 15.0
#: Canonical JSONL carries metadata beyond the UTF-8 text; ledgers are small.
CANONICAL_FILE_OVERHEAD = 2.0
SCRATCH_CAP_BYTES = 64 * GIB
SCRATCH_MIN_FREE_BYTES = 32 * GIB
#: Inventory positions held back for bounded benchmarks (the last ranks).
BENCHMARK_RESERVED_POSITIONS = 2
#: Default concurrency per mode; operator options may lower, never exceed, the maxima.
CONCURRENCY = {
    TransportMode.WHOLE_FILE_LOCAL: {"download": 8, "process": 12},
    TransportMode.ROW_GROUP_LOCAL: {"download": 8, "process": 12},
    TransportMode.SMALL_SOURCE_DIRECT: {"download": 4, "process": 4},
}
CONCURRENCY_MAX = {"download": 16, "process": 16}
BYTES_PER_ESTIMATED_TOKEN = 4
SIZING_KIND = "mix01_source_sizing_measurement"
#: Whole files measured end to end supersede a small calibration for exactly
#: rows per file, canonical bytes per row (rejections included) and file
#: bytes. Calibration keeps the schema/adapter evidence and the range model.
SIZING_CONTRACT = "mix01-whole-file-sizing-v1"
TOKEN_METHOD = "canonical UTF-8 bytes / 4 (estimate, never exact XLM tokens)"


class PlanError(ValueError):
    """A planning input is missing, stale or inconsistent; nothing is written."""


# ----------------------------------------------------------------- inventory


def inventory_digest(inventory: Mapping[str, Any]) -> str:
    """Recompute the frozen inventory digest exactly as ``mix01_inventory.py freeze`` does."""
    entries = inventory["files"]
    text = (
        f"v{inventory['inventory_version']}|{inventory['source_id']}|{inventory['repository']}"
        f"|{inventory['revision']}|{inventory['seed']}|{len(entries)}\n"
        + "".join(
            f"{e['order_key']} {e['size_bytes'] if e['size_bytes'] is not None else -1}"
            f" {e['file']}\n"
            for e in entries
        )
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_inventory(
    inventory: Mapping[str, Any], source_id: str, repository: str, revision: str
) -> list[str]:
    """Verify order keys, ordering and digest; return the files in frozen order."""
    if inventory.get("inventory_version") != 1:
        raise PlanError("inventory is not a version-1 frozen inventory")
    if (inventory.get("source_id"), inventory.get("repository"), inventory.get("revision")) != (
        source_id,
        repository,
        revision,
    ):
        raise PlanError("inventory belongs to another source, repository or revision")
    seed = inventory.get("seed")
    entries = inventory.get("files")
    if not isinstance(seed, int) or not isinstance(entries, list) or not entries:
        raise PlanError("inventory has no seed or no files")
    keys: list[tuple[str, str]] = []
    for entry in entries:
        name = entry["file"]
        key = hashlib.sha256(f"{seed}|{repository}|{revision}|{name}".encode()).hexdigest()
        if entry["order_key"] != key:
            raise PlanError(f"inventory order key of '{name}' does not verify")
        keys.append((key, name))
    if keys != sorted(keys) or len({name for _, name in keys}) != len(keys):
        raise PlanError("inventory is not in frozen hash order or repeats a file")
    if inventory.get("file_count") != len(entries) or inventory.get(
        "inventory_digest"
    ) != inventory_digest(inventory):
        raise PlanError("inventory digest does not verify")
    return [name for _, name in keys]


# -------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Requirement:
    """The first-pass canonical requirement of one component, from frozen files."""

    component_id: str
    first_pass_tokens: int
    required_canonical_bytes: int
    safety_margin: float
    quotas_sha256: str
    estimate_sha256: str


def requirement_from(
    component_id: str,
    quotas: Mapping[str, Any],
    estimate: Mapping[str, Any],
    *,
    quotas_sha256: str,
    estimate_sha256: str,
) -> Requirement:
    """Cross-check the quota table and the headroom estimate; never renormalize."""
    targets = quotas.get("first_pass_headroom_quotas") or {}
    if component_id not in targets:
        raise PlanError(f"component '{component_id}' has no first-pass quota")
    tokens = int(targets[component_id])
    entry = (estimate.get("sources") or {}).get(component_id) or {}
    if entry.get("status") != "ESTIMATED":
        raise PlanError(f"headroom estimate for '{component_id}' is not ESTIMATED")
    assumptions = estimate.get("assumptions") or {}
    per_token = float(assumptions.get("bytes_per_token_base", 0))
    required = float(entry.get("required_canonical_bytes_base", 0))
    if (
        int(entry.get("first_pass_usable_token_target", -1)) != tokens
        or per_token != BYTES_PER_ESTIMATED_TOKEN
        or required != tokens * per_token
    ):
        raise PlanError("headroom estimate disagrees with the frozen quota table")
    safety = float(assumptions.get("safety_margin", 0))
    if not 1 <= safety <= 2:
        raise PlanError("estimate safety margin must lie in [1, 2]")
    return Requirement(
        component_id=component_id,
        first_pass_tokens=tokens,
        required_canonical_bytes=int(required),
        safety_margin=safety,
        quotas_sha256=quotas_sha256,
        estimate_sha256=estimate_sha256,
    )


@dataclass(frozen=True)
class Predecessor:
    """A completed earlier plan of the same source and its sealed outcome."""

    plan: Mapping[str, Any]
    sealed_canonical_bytes: int
    sealed_files: int
    accounting_digest: str


def _with_digest(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("digest", None)
    body["digest"] = canonical.digest(body)
    return body


def _verifies(record: Mapping[str, Any]) -> bool:
    body = dict(record)
    return bool(body.pop("digest", None) == canonical.digest(body))


# -------------------------------------------------------------------- sizing


def sizing_from_receipt(
    receipt: Mapping[str, Any], pin: Mapping[str, str], *, receipt_sha256: str
) -> dict[str, Any]:
    """Whole-file production sizing from one completed benchmark receipt of this view.

    Only what a whole-file run actually measures is derived: rows per file,
    canonical bytes per row (acceptance and rejections included) and file
    bytes. Tokens stay an explicit ``canonical bytes / 4`` estimate.
    """
    if not _verifies(receipt):
        raise PlanError("performance receipt digest does not verify")
    if receipt.get("kind") != "mix01_source_performance_receipt" or "benchmark" not in receipt:
        raise PlanError("sizing needs a Mix-01 source benchmark performance receipt")
    source = receipt.get("source") or {}
    if any(source.get(key) != pin[key] for key in SUBJECT_KEYS):
        raise PlanError(
            "performance receipt belongs to another source view, repository or revision"
        )
    if (receipt.get("outcome") or {}).get("status") != "completed":
        raise PlanError("sizing needs a completed benchmark run")
    if receipt.get("transport_mode") != TransportMode.WHOLE_FILE_LOCAL.value:
        raise PlanError("sizing needs whole files processed end to end")
    files = sorted(str(entry["file"]) for entry in receipt["benchmark"]["files"])
    transfer, processing = receipt["transfer"], receipt["processing"]
    count = len(files)
    rows, documents, rejected = (
        int(processing["rows"]),
        int(processing["documents"]),
        int(processing["rejected"]),
    )
    canonical_bytes, file_bytes = int(processing["canonical_bytes"]), int(transfer["file_bytes"])
    if (
        count < 1
        or len(set(files)) != count
        or int(transfer["files"]) != count
        or int(processing["units"]) != count
        or int(transfer["sha256_independently_verified"]) != count
        or min(rows, canonical_bytes, file_bytes) < 1
        or documents + rejected != rows
    ):
        raise PlanError("performance receipt does not account whole, verified files")
    return _with_digest(
        {
            "kind": SIZING_KIND,
            "contract": SIZING_CONTRACT,
            "subject": {key: pin[key] for key in SUBJECT_KEYS},
            "receipt": {
                "digest": receipt["digest"],
                "sha256": receipt_sha256,
                "benchmark_digest": receipt["benchmark"]["digest"],
                "plan_hash": receipt["plan"]["plan_hash"],
            },
            "files": files,
            "measured": {
                "files": count,
                "rows": rows,
                "documents": documents,
                "rejected": rejected,
                "file_bytes": file_bytes,
                "canonical_bytes": canonical_bytes,
            },
            "derived": {
                "rows_per_file": rows // count,
                "file_bytes_per_file": math.ceil(file_bytes / count),
                "canonical_bytes_per_row": canonical_bytes / rows,
                "accepted_fraction": documents / rows,
                "canonical_bytes_per_raw_byte": canonical_bytes / file_bytes,
                "estimated_tokens": canonical_bytes // BYTES_PER_ESTIMATED_TOKEN,
            },
            "token_method": f"{TOKEN_METHOD}; exact tokens only after tokenizer freeze",
            "supersedes": "calibration rows per file, canonical bytes per row and file bytes",
        }
    )


def check_sizing(sizing: Mapping[str, Any], pin: Mapping[str, str]) -> None:
    if not _verifies(sizing):
        raise PlanError("sizing measurement digest does not verify")
    if sizing.get("kind") != SIZING_KIND or sizing.get("contract") != SIZING_CONTRACT:
        raise PlanError(f"not a {SIZING_CONTRACT} sizing measurement")
    if dict(sizing["subject"]) != {key: pin[key] for key in SUBJECT_KEYS}:
        raise PlanError("sizing measurement belongs to another source view, repository or revision")


def sized_layout(layout: SourceLayout, sizing: Mapping[str, Any]) -> SourceLayout:
    """Measured whole-file facts take precedence over calibration estimates for sizing."""
    if sizing["subject"]["source_id"] != layout.source_id:
        raise PlanError("sizing measurement belongs to another source")
    derived = sizing["derived"]
    return dataclasses.replace(
        layout,
        file_bytes=int(derived["file_bytes_per_file"]),
        canonical_bytes_per_row=float(derived["canonical_bytes_per_row"]),
        rows_per_file_measured=int(derived["rows_per_file"]),
        evidence={**layout.evidence, "sizing": str(sizing["digest"])},
    )


def check_plan(record: Mapping[str, Any]) -> None:
    body = dict(record)
    if body.pop("digest", None) != canonical.digest(body):
        raise PlanError("production plan digest does not verify")
    if record.get("kind") != PLAN_KIND or record.get("version") != PLAN_VERSION:
        raise PlanError("not a version-1 Mix-01 source production plan")


def record_bound(source_id: str, view_id: str) -> tuple[int, str | None]:
    """The record byte bound of one source view and its basis (``None``: generic)."""
    bound, basis = SOURCE_RECORD_BYTES.get((source_id, view_id), (MAX_RECORD_BYTES, None))
    if not 0 < bound <= MAX_PARSER_BYTES:
        raise PlanError(f"record bound of {source_id}:{view_id} exceeds the parser ceiling")
    return bound, basis


def plan_limits(
    files: int, layout: SourceLayout, mode: TransportMode, pin: Mapping[str, str]
) -> tuple[dict[str, Any], AcquisitionLimits]:
    """Every resource ceiling of one plan, derived by the versioned planner rules."""
    concurrency = CONCURRENCY[mode]
    record_bytes, record_basis = record_bound(pin["source_id"], pin["view_id"])
    max_file = math.ceil(layout.file_bytes * FILE_BYTES_TOLERANCE / MIB) * MIB
    max_rows = math.ceil(layout.rows_per_file * ROWS_TOLERANCE)
    canonical_per_file = math.ceil(
        layout.rows_per_file * layout.canonical_bytes_per_row * ROWS_TOLERANCE
    )
    durable_per_file = max_file + math.ceil(canonical_per_file * CANONICAL_FILE_OVERHEAD)
    file_deadline = max(MIN_FILE_DEADLINE_SECONDS, max_file / MIN_FILE_RATE_BYTES_PER_SECOND)
    in_flight = min(files, concurrency["download"] + 2 * concurrency["process"])
    output_bytes = durable_per_file - max_file
    growth = ProcessingGrowth(
        output_bytes=output_bytes,
        source_max_bytes=max_file,
        metadata_bytes=min(MIB, max(1, output_bytes // 16)),
    )
    unit_peak = max_file + growth.processing_peak + growth.state_peak
    if unit_peak > SCRATCH_CAP_BYTES:
        raise PlanError("one source/processing envelope exceeds the scratch policy ceiling")
    scratch_cap = min(SCRATCH_CAP_BYTES, in_flight * unit_peak)
    policy: dict[str, Any] = {
        "rules": PLANNER_RULES,
        "max_file_bytes": max_file,
        "max_rows_per_file": max_rows,
        "max_decoded_bytes_per_file": max_file * DECODED_BYTES_PER_FILE_FACTOR,
        "max_record_bytes": record_bytes,
        "max_parser_bytes": MAX_PARSER_BYTES,
        "max_ledger_bytes": MAX_LEDGER_BYTES,
        "max_decompression_ratio": MAX_DECOMPRESSION_RATIO,
        "max_requests_per_file": MAX_REQUESTS_PER_FILE,
        "max_retries": MAX_RETRIES,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "file_deadline_seconds": file_deadline,
        "plan_deadline_seconds": PLAN_DEADLINE_SECONDS,
        "max_durable_bytes_per_file": durable_per_file,
        "max_canonical_bytes_per_file": canonical_per_file,
        "scratch_cap_bytes": scratch_cap,
        "processing_growth": growth.model_dump(),
        "scratch_min_free_bytes": SCRATCH_MIN_FREE_BYTES,
        "max_in_flight_files": in_flight,
        "download_workers": min(files, concurrency["download"]),
        "process_workers": min(files, concurrency["process"]),
        "download_workers_max": CONCURRENCY_MAX["download"],
        "process_workers_max": CONCURRENCY_MAX["process"],
    }
    if record_basis is not None:
        # Only an overridden source carries this key: generic plans keep their digests.
        policy["max_record_bytes_basis"] = record_basis
    limits = AcquisitionLimits(
        max_transferred_bytes=math.ceil(files * layout.file_bytes * TRANSFER_FACTOR / MIB) * MIB,
        max_decompressed_bytes=files * policy["max_decoded_bytes_per_file"],
        max_records=files * max_rows,
        max_scanned_records=files * max_rows,
        max_temp_disk_bytes=scratch_cap,
        max_output_disk_bytes=files
        * (2 * max_file + growth.processing_peak + 2 * growth.metadata_bytes)
        + growth.run_peak,
        max_requests=files * MAX_REQUESTS_PER_FILE,
        max_retries=MAX_RETRIES,
        per_request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        overall_deadline_seconds=PLAN_DEADLINE_SECONDS,
        max_decompression_ratio=MAX_DECOMPRESSION_RATIO,
        max_workers=CONCURRENCY_MAX["download"],
        max_record_bytes=record_bytes,
        max_parser_bytes=MAX_PARSER_BYTES,
    )
    return policy, limits


def coverage_note(sequence: int, start: int, stop: int) -> str:
    return f"{PLANNER_RULES} plan {sequence}: frozen inventory ranks [{start}, {stop})"


def minted_from_record(record: Mapping[str, Any]) -> AcquisitionPlan:
    """Rebuild the plan's ``AcquisitionPlan`` from the record alone; it must match its hash."""
    check_plan(record)
    selection = record["selection"]
    minted = acquisition_plan(
        record["source"],
        [entry["file"] for entry in selection["files"]],
        AcquisitionLimits.model_validate(record["acquisition_plan"]["limits"]),
        int(record["inventory"]["seed"]),
        coverage_note(int(record["sequence"]), selection["start_rank"], selection["stop_rank"]),
        processing_growth=record["limits"].get("processing_growth"),
    )
    if minted.plan_hash != record["acquisition_plan"]["plan_hash"]:
        raise PlanError("plan record does not reproduce its acquisition plan hash")
    return minted


def acquisition_plan(
    pin: Mapping[str, str],
    files: Sequence[str],
    limits: AcquisitionLimits,
    seed: int,
    coverage: str,
    attempt: int = 1,
    processing_growth: Mapping[str, Any] | None = None,
) -> AcquisitionPlan:
    """The production ``AcquisitionPlan`` of these whole files.

    Always production scope, whatever its size: a source plan never takes the
    pilot path, so it always needs the stored admission and an authorization
    bound to its behavioral hash.
    """
    base = AcquisitionPlan(
        plan_id=f"plan_{pin['source_id']}_{pin['view_id']}_{pin['provider']}",
        source_id=pin["source_id"],
        view_id=pin["view_id"],
        provider=pin["provider"],
        repository=pin["repository"],
        revision=pin["revision"],
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=list(files),
        sampling_frame=SamplingFrame(
            selected_files=list(files), selection_seed=seed, coverage_notes=coverage
        ),
        limits=limits,
        output_artifact_id=f"raw_{pin['source_id']}_{pin['view_id']}",
        attempt=attempt,
        is_pilot=False,
        source_processing_growth=None
        if processing_growth is None
        else ProcessingGrowth.model_validate(processing_growth),
    )
    if not plan_requires_production_admission(base):
        raise PlanError("a source plan must take the production admission path")
    suffix = base.compute_behavioral_hash()[:20]
    named = base.model_copy(
        update={
            "plan_id": f"{base.plan_id}_{suffix}",
            "output_artifact_id": f"{base.output_artifact_id}_{suffix}",
        }
    )
    return named.with_computed_hash()


def build_plan(
    *,
    source_key: str,
    pin: Mapping[str, str],
    requirement: Requirement,
    inventory: Mapping[str, Any],
    inventory_sha256: str,
    layout: SourceLayout,
    calibration: Mapping[str, str],
    policy: Mapping[str, Any],
    admission: Mapping[str, str],
    predecessor: Predecessor | None = None,
    benchmark_reserved: int = BENCHMARK_RESERVED_POSITIONS,
) -> dict[str, Any]:
    """The next deterministic plan of this source; refuses rather than guesses."""
    if not SHA_RE.fullmatch(pin["revision"]):
        raise PlanError("source revision is not an exact 40-hex commit")
    mode = check_frozen(policy, pin["source_id"])
    if mode not in LOCAL_MODES:
        raise PlanError(
            f"frozen transport mode '{mode.value}' runs through xlm data plan/fetch, "
            "not this planner"
        )
    try:
        check_subject(policy, pin)
    except PolicyError as exc:
        raise PlanError(str(exc)) from exc
    # A policy that binds a whole-file measurement sizes the plan from it, never
    # from the calibration estimate; policies without one keep their exact plans.
    sizing = policy.get("sizing")
    if sizing is not None:
        check_sizing(sizing, pin)
        layout = sized_layout(layout, sizing)
    ordered = check_inventory(inventory, pin["source_id"], pin["repository"], pin["revision"])
    if not 0 <= benchmark_reserved < len(ordered):
        raise PlanError("benchmark reservation leaves no plannable inventory")
    eligible = len(ordered) - benchmark_reserved
    sequence, start, acquired, previous_digest = 1, 0, 0, None
    if predecessor is not None:
        check_plan(predecessor.plan)
        prior = predecessor.plan
        if (prior["source"], prior["inventory"]["digest"]) != (
            dict(pin),
            inventory["inventory_digest"],
        ):
            raise PlanError("predecessor belongs to another source or inventory")
        if predecessor.sealed_files != len(prior["selection"]["files"]):
            raise PlanError("predecessor is not completely sealed; resume it instead of topping up")
        sequence = int(prior["sequence"]) + 1
        start = int(prior["selection"]["next_cursor"])
        acquired = (
            int(prior["acquired_before"]["canonical_bytes"]) + predecessor.sealed_canonical_bytes
        )
        previous_digest = prior["digest"]
    deficit = requirement.required_canonical_bytes - acquired
    if deficit <= 0:
        raise PlanError("sealed canonical bytes already meet the first-pass requirement")
    per_file = layout.rows_per_file * layout.canonical_bytes_per_row
    wanted = math.ceil(deficit * requirement.safety_margin / per_file)
    if start + wanted > eligible:
        raise PlanError(
            f"the frozen inventory has {eligible - start} plannable files left; "
            f"{wanted} are needed (benchmark-reserved positions are never planned)"
        )
    stop = start + wanted
    files = ordered[start:stop]
    policy_limits, limits = plan_limits(len(files), layout, mode, pin)
    minted = acquisition_plan(
        pin,
        files,
        limits,
        int(inventory["seed"]),
        coverage_note(sequence, start, stop),
        processing_growth=policy_limits["processing_growth"],
    )
    expected_rows = len(files) * layout.rows_per_file
    expected_canonical = math.floor(len(files) * per_file)
    record: dict[str, Any] = {
        "kind": PLAN_KIND,
        "version": PLAN_VERSION,
        "rules": PLANNER_RULES,
        "source_key": source_key,
        "sequence": sequence,
        "source": dict(pin),
        "lineage": {
            "previous_plan_digest": previous_digest,
            "previous_accounting_digest": None
            if predecessor is None
            else predecessor.accounting_digest,
            "rule": "plans form a hash chain; each starts at its predecessor's next_cursor",
        },
        "inputs": {
            "quota_component": requirement.component_id,
            "quotas_sha256": requirement.quotas_sha256,
            "estimate_sha256": requirement.estimate_sha256,
            "calibration": dict(calibration),
            "layout": {
                "file_bytes_measured": layout.file_bytes,
                "rows_per_file_estimate": layout.rows_per_file,
                "whole_bytes_per_row": layout.whole_bytes_per_row,
                "canonical_bytes_per_row": layout.canonical_bytes_per_row,
            },
            "transport_policy": {
                "digest": policy["digest"],
                "basis": policy["basis"],
                "selected_mode": mode.value,
            },
            "admission": dict(admission),
        },
        "inventory": {
            "digest": inventory["inventory_digest"],
            "file_sha256": inventory_sha256,
            "seed": inventory["seed"],
            "file_count": len(ordered),
            "order": inventory["selection"],
            "benchmark_reserved_ranks": list(range(eligible, len(ordered))),
        },
        "requirement": {
            "first_pass_tokens": requirement.first_pass_tokens,
            "required_canonical_bytes": requirement.required_canonical_bytes,
            "safety_margin": requirement.safety_margin,
            "token_method": "canonical UTF-8 bytes / 4 (estimate, never exact XLM tokens)",
        },
        "acquired_before": {"canonical_bytes": acquired},
        "transport_mode": mode.value,
        "selection": {
            "rule": "the next contiguous ranks of the frozen inventory order",
            "start_rank": start,
            "stop_rank": stop,
            "files": [{"rank": start + i, "file": name} for i, name in enumerate(files)],
            "row_ranges": "whole files (every row of every planned file)",
            "next_cursor": stop,
        },
        "expected": {
            "files": len(files),
            "rows": expected_rows,
            "canonical_bytes": expected_canonical,
            "estimated_tokens": expected_canonical // BYTES_PER_ESTIMATED_TOKEN,
            "transfer_bytes": len(files) * layout.file_bytes,
            "requests": 2 * len(files),
            "basis": "one measured file and calibration yield; exact counts come from receipts",
        },
        "limits": policy_limits,
        "acquisition_plan": {
            "plan_id": minted.plan_id,
            "plan_hash": minted.plan_hash,
            "selection_hash": minted.compute_selection_hash(),
            "limits": minted.limits.model_dump(),
            "is_pilot": minted.is_pilot,
        },
        "authorization": "STOP: the operator reviews this digest and authorizes it explicitly",
        "live_run": False,
    }
    if sizing is not None:
        record["inputs"]["sizing"] = {
            "contract": sizing["contract"],
            "digest": sizing["digest"],
            "receipt_digest": sizing["receipt"]["digest"],
            "rule": "measured rows per file, canonical bytes per row and file bytes "
            "supersede the calibration estimate",
        }
        record["expected"]["basis"] = (
            f"whole-file measurement (receipt {sizing['receipt']['digest']}) of "
            f"{sizing['measured']['files']} file(s); other files differ in size and "
            "yield; exact counts come from receipts"
        )
    return _with_digest(record)


def check_against_inputs(record: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> None:
    """A stored plan must be exactly what the same frozen inputs plan today."""
    check_plan(record)
    if record["digest"] != rebuilt["digest"]:
        raise PlanError("stored plan differs from the plan its frozen inputs produce")
