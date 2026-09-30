"""Measured capacity models and the restartable Essential-Web bulk campaign.

Offline, deterministic logic only: no network, no source text, no selector,
quota or mixture change. A campaign scans whole inventory files in the frozen
inventory order. One batch is a fixed number of files; one slice is one
row-group index across the batch, read by the certified window reader as a
single full-row-group window per file. Every number derived from the prefix
calibration is an extrapolation and is labelled as such.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

from xlm.data.acquisition.plan import AcquisitionLimits, ParquetWindowDecode
from xlm.data.acquisition.projection import ProjectionRefusal, resolve_projection
from xlm.data.acquisition.sampling import WINDOW_METADATA_REQUESTS, FileLayout
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources.admission import essential_contamination_mitigation

MIB = 1024**2
GIB = 1024**3
CAMPAIGN_KIND = "essential_web_bulk_campaign"
CAMPAIGN_VERSION = 1
ADAPTER_ID = "essential_web_bnormal"
#: One shared raw fetch feeds the three adapters, exactly as calibrated.
PLAN_VIEW = "essential_science"
WINDOW_POLICY_VERSION = 2
WINDOW_BUFFER_BYTES = 4 * MIB
WINDOW_BATCH_ROWS = 256
#: Opening a file costs the 4-byte header, the 8-byte tail and the reader's
#: 64 KiB footer read-ahead in addition to the footer itself.
FOOTER_OPEN_BYTES = 65536 + 12
BATCH_OPTIONS = (8, 16, 32, 64)
#: A private selection restarts from zero with spent budgets intact, so each
#: budget covers the fetch plus two complete restarts.
RESTART_FACTOR = 3
#: Restart-unit ceilings used to pick the batch size (operational choices).
MAX_SLICE_SECONDS = 1800
MAX_SLICE_TRANSFER_BYTES = GIB
SCIENCE = "essential_science"


class BulkError(ValueError):
    """Any campaign identity, accounting or resource deviation: refuse."""


def ceil_mib(value: float) -> int:
    return max(1, math.ceil(value / MIB)) * MIB


# --------------------------------------------------------------------- layout


def layout_record(
    layout: FileLayout, projection: Sequence[str], max_decompression_ratio: float
) -> dict[str, Any]:
    """Footer-only summary of the projected chunks of every row group of one file."""
    if layout.schema_refusal is not None:
        raise BulkError(f"{layout.name}: schema cannot be mapped: {layout.schema_refusal}")
    try:
        resolved = resolve_projection(layout.field_leaves, tuple(projection))
    except ProjectionRefusal as exc:
        raise BulkError(f"{layout.name}: projection refused: {exc}") from exc
    groups: list[dict[str, Any]] = []
    expected_start = 0
    for group in layout.groups:
        if group.num_rows < 1 or group.start_row != expected_start:
            raise BulkError(f"{layout.name}: row group {group.index} does not tile the file")
        if tuple(column.path for column in group.columns) != layout.leaf_paths:
            raise BulkError(f"{layout.name}: row group {group.index} disagrees with the schema")
        chunks = [group.columns[index] for index in resolved.leaf_indices]
        window = ParquetWindowDecode(
            policy_version=WINDOW_POLICY_VERSION,
            stream_buffer_bytes=WINDOW_BUFFER_BYTES,
            max_window_scan_rows=group.num_rows,
            batch_rows=min(WINDOW_BATCH_ROWS, group.num_rows),
        )
        groups.append(
            {
                "index": group.index,
                "start_row": group.start_row,
                "rows": group.num_rows,
                "projected_leaves": len(chunks),
                "projected_compressed_bytes": sum(chunk.compressed for chunk in chunks),
                "projected_uncompressed_bytes": sum(chunk.uncompressed for chunk in chunks),
                "text_compressed_bytes": sum(
                    chunk.compressed for chunk in chunks if chunk.path == "text"
                ),
                "buffered_reads": sum(
                    max(1, -(-chunk.compressed // WINDOW_BUFFER_BYTES)) for chunk in chunks
                ),
                "refusal": window.ratio_refusal(
                    ((chunk.path, chunk.compressed, chunk.uncompressed) for chunk in chunks),
                    max_decompression_ratio,
                ),
            }
        )
        expected_start += group.num_rows
    if not groups or expected_start != layout.num_rows:
        raise BulkError(f"{layout.name}: row groups do not cover the declared rows")
    return {"file": layout.name, "rows": layout.num_rows, "groups": groups}


# ------------------------------------------------------------- capacity models


def science_capacity(
    totals: Mapping[str, Any], quotas: Mapping[str, Any], dispersion: Mapping[str, Any]
) -> dict[str, Any]:
    """Rows, documents, bytes, transfer and time by LINEAR calibration extrapolation.

    This is the naive prefix-linear model: it multiplies the prefix-window cost
    per row and is not an acquisition budget. The 3/4/5 cases change only the
    assumed bytes per token; the first-pass target stays frozen.
    """
    rows = int(totals["input_rows"])
    science = totals["retained"][SCIENCE]
    target = int(quotas["first_pass_headroom_quotas"][SCIENCE])
    final = int(quotas["final_quotas"][SCIENCE])
    transfer = int(totals["physical_response_body_bytes"])
    elapsed = Fraction(str(totals["elapsed_wall_seconds_including_restart_downtime"]))
    cases: dict[str, Any] = {}
    for name, size in calibration.BYTES_PER_TOKEN.items():
        need = calibration.rows_required(target, int(science["canonical_bytes"]), rows, size)
        cases[name] = {
            "assumed_bytes_per_token": size,
            "required_input_rows": need,
            "estimated_science_documents": float(Fraction(need * int(science["documents"]), rows)),
            "estimated_canonical_science_bytes": target * size,
            "naive_linear_transfer_bytes": math.ceil(Fraction(need * transfer, rows)),
            "naive_linear_elapsed_seconds": float(elapsed * need / rows),
        }
    central = calibration.BYTES_PER_TOKEN["central"]
    return {
        "model": "simple linear calibration extrapolation; NOT a guaranteed acquisition budget",
        "final_exact_token_quota": final,
        "first_pass_estimated_token_target": target,
        "quota_headroom_ratio": target / final,
        "measured_estimated_tokens_per_input_row_central": float(
            Fraction(int(science["canonical_bytes"]), central * rows)
        ),
        "cases": cases,
        "stop_uses": "canonical bytes at 4 bytes/token; the 3/5 cases bound what the exact "
        "tokenizer count may later show and are handled by deterministic top-up",
        "exact_token_break_even_bytes_per_token": target * central / final,
        "rows_for_final_quota_if_bytes_per_token_is_high": calibration.rows_required(
            final, int(science["canonical_bytes"]), rows, calibration.BYTES_PER_TOKEN["high"]
        ),
        "leave_one_crawl_out_rows_range": dispersion["science_rows_required_leave_one_out_range"],
        "quota_changed": False,
    }


def physical_cost(
    footers: Sequence[Mapping[str, Any]],
    crawl_units: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Any],
) -> dict[str, Any]:
    """Full-file projected cost from the real footers, against the prefix-linear cost.

    ``footers`` are :func:`layout_record` results for the footer-certified
    files plus ``remote_length`` and ``footer_bytes``. Only these files have
    known layouts, so the model is a bounded estimate for the rest.
    """
    if len(footers) != len(crawl_units) or not footers:
        raise BulkError("physical model needs one footer per calibration unit")
    files: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for footer, unit in zip(footers, crawl_units, strict=True):
        if footer["file"] != unit["file"]:
            raise BulkError("footer and calibration unit describe different files")
        groups = footer["groups"]
        projected = sum(int(group["projected_compressed_bytes"]) for group in groups)
        overhead = len(groups) * (int(footer["footer_bytes"]) + FOOTER_OPEN_BYTES)
        files.append(
            {
                "file": footer["file"],
                "rows": footer["rows"],
                "row_groups": len(groups),
                "remote_length": footer["remote_length"],
                "projected_compressed_bytes": projected,
                "projected_uncompressed_bytes": sum(
                    int(group["projected_uncompressed_bytes"]) for group in groups
                ),
                "footer_rereads_bytes": overhead,
                "full_file_transfer_bytes": projected + overhead,
                "buffered_reads": sum(int(group["buffered_reads"]) for group in groups),
                "requests": sum(
                    int(group["buffered_reads"]) + WINDOW_METADATA_REQUESTS for group in groups
                ),
            }
        )
        first = groups[0]
        other = int(first["projected_compressed_bytes"]) - int(first["text_compressed_bytes"])
        fixed = other + int(footer["footer_bytes"]) + FOOTER_OPEN_BYTES
        validation.append(
            {
                "file": footer["file"],
                "measured_prefix_transfer_bytes": unit["transferred_bytes"],
                "non_text_chunks_plus_footer_bytes": fixed,
                "implied_text_buffers": (int(unit["transferred_bytes"]) - fixed)
                / WINDOW_BUFFER_BYTES,
            }
        )
    rows = sum(int(file["rows"]) for file in files)
    transfer = sum(int(file["full_file_transfer_bytes"]) for file in files)
    requests = sum(int(file["requests"]) for file in files)
    per_row = [int(f["full_file_transfer_bytes"]) / int(f["rows"]) for f in files]
    cal_rows = int(totals["input_rows"])
    cal_requests = sum(int(u["performance"]["logical_requests"]) for u in crawl_units)
    open_seconds = sum(float(u["performance"]["open_seconds"]) for u in crawl_units)
    body_seconds = sum(float(u["performance"]["body_seconds"]) for u in crawl_units)
    wall_seconds = sum(float(u["performance"]["wall_seconds"]) for u in crawl_units)
    cal_transfer = int(totals["physical_response_body_bytes"])
    timing = {
        "seconds_per_request": open_seconds / cal_requests,
        "seconds_per_transfer_byte": body_seconds / cal_transfer,
        "seconds_per_row": max(0.0, wall_seconds - open_seconds - body_seconds) / cal_rows,
        "basis": "sum over the eight prefix windows, one worker: request open latency, body "
        "time and the remaining wall time; an ESTIMATE, not measured on full row groups",
    }
    return {
        "reader": "window-v2: projected leaves only, one full row group per file per plan, "
        "4 MiB buffered ranges; the footer is re-read for every row group",
        "known_layout_files": len(files),
        "files": files,
        "prefix_model_validation": validation,
        "prefix_linear": {
            "transfer_bytes_per_row": int(totals["physical_response_body_bytes"]) / cal_rows,
            "why_too_high": "each 2,048-row prefix already reads every non-text chunk of its "
            "10,000-row group in full plus whole 4 MiB text buffers",
        },
        "full_file": {
            "rows": rows,
            "transfer_bytes": transfer,
            "transfer_bytes_per_row": transfer / rows,
            "transfer_bytes_per_row_min_max_file": [min(per_row), max(per_row)],
            "requests_per_row": requests / rows,
            "mean_rows_per_file": rows / len(files),
            "min_max_rows_per_file": [
                min(int(f["rows"]) for f in files),
                max(int(f["rows"]) for f in files),
            ],
            "mean_row_groups_per_file": sum(int(f["row_groups"]) for f in files) / len(files),
            "max_row_groups_per_file": max(int(f["row_groups"]) for f in files),
            "max_group_rows": max(int(g["rows"]) for f in footers for g in f["groups"]),
            "max_group_projected_compressed_bytes": max(
                int(g["projected_compressed_bytes"]) for f in footers for g in f["groups"]
            ),
            "max_group_buffered_reads": max(
                int(g["buffered_reads"]) for f in footers for g in f["groups"]
            ),
            "decoded_bytes_per_projected_uncompressed_byte": int(totals["decompressed_bytes"])
            / sum(
                int(footer["groups"][0]["projected_uncompressed_bytes"])
                * int(unit["input_rows"])
                / int(footer["groups"][0]["rows"])
                for footer, unit in zip(footers, crawl_units, strict=True)
            ),
            "excludes": "retries and restarts; bounded separately by the slice limits",
        },
        "timing": timing,
        "limits_of_knowledge": "layouts are known for the footer-certified files only; every "
        "other inventory file is measured by its own footer before it is planned",
    }


def extrapolate(physical: Mapping[str, Any], rows: int) -> dict[str, Any]:
    """Full-file model cost of scanning ``rows`` rows (estimate; one worker)."""
    full, timing = physical["full_file"], physical["timing"]
    transfer = rows * float(full["transfer_bytes_per_row"])
    requests = rows * float(full["requests_per_row"])
    seconds = (
        requests * float(timing["seconds_per_request"])
        + transfer * float(timing["seconds_per_transfer_byte"])
        + rows * float(timing["seconds_per_row"])
    )
    return {
        "rows": rows,
        "estimated_files": rows / float(full["mean_rows_per_file"]),
        "full_file_transfer_bytes": math.ceil(transfer),
        "full_file_transfer_bytes_min_max": [
            math.ceil(rows * float(bound)) for bound in full["transfer_bytes_per_row_min_max_file"]
        ],
        "prefix_linear_transfer_bytes": math.ceil(
            rows * float(physical["prefix_linear"]["transfer_bytes_per_row"])
        ),
        "requests": math.ceil(requests),
        "estimated_seconds_one_worker": seconds,
    }


def inventory_capacity(
    inventory: Mapping[str, Any], physical: Mapping[str, Any], required_rows: Mapping[str, int]
) -> dict[str, Any]:
    """What the frozen inventory can plausibly supply; unknown rows stay unknown."""
    files = int(inventory["file_count"])
    known = [entry for entry in inventory["files"] if entry["size_bytes"] is not None]
    low, high = physical["full_file"]["min_max_rows_per_file"]
    mean = float(physical["full_file"]["mean_rows_per_file"])
    crawls: dict[str, int] = {}
    for entry in inventory["files"]:
        crawl = str(entry["file"]).split("/")[1]
        crawls[crawl] = crawls.get(crawl, 0) + 1
    cases = {
        name: {
            "required_input_rows": rows,
            "files_at_mean_observed_rows": math.ceil(rows / mean),
            "files_at_smallest_observed_file": math.ceil(rows / low),
            "inventory_share_at_smallest_observed_file": math.ceil(rows / low) / files,
            "break_even_mean_rows_per_file": rows / files,
        }
        for name, rows in required_rows.items()
    }
    return {
        "inventory_digest": inventory["inventory_digest"],
        "inventory_files": files,
        "crawl_strata": dict(sorted(crawls.items())),
        "files_with_known_size_and_rows": len(known),
        "files_with_unknown_rows": files - len(known),
        "observed_rows_per_file": {"min": low, "max": high, "mean": mean},
        "cases": cases,
        "total_capacity_known": False,
        "assessment": "plausibly sufficient by a wide margin; NOT proven",
        "policy": "progressive: scan the next inventory batch, measure cumulative canonical "
        "bytes, continue until the first-pass targets are met, stop cleanly, top up later "
        "from the next inventory batch if the exact tokenizer count is deficient",
        "row_counts_fabricated": False,
    }


def batch_policy(
    physical: Mapping[str, Any], totals: Mapping[str, Any], required_rows: int
) -> dict[str, Any]:
    """Assess 8/16/32/64 files per batch from measured costs and pick one."""
    full, timing = physical["full_file"], physical["timing"]
    rows_per_file = float(full["mean_rows_per_file"])
    cal_rows = int(totals["input_rows"])
    raw_per_row = int(totals["raw_bytes"]) / cal_rows
    tokens_per_row = int(totals["retained"][SCIENCE]["canonical_bytes"]) / (
        calibration.BYTES_PER_TOKEN["central"] * cal_rows
    )
    group_rows = int(full["max_group_rows"])
    group_bytes = int(full["max_group_projected_compressed_bytes"])
    group_requests = int(full["max_group_buffered_reads"]) + WINDOW_METADATA_REQUESTS
    group_seconds = (
        group_requests * float(timing["seconds_per_request"])
        + group_bytes * float(timing["seconds_per_transfer_byte"])
        + group_rows * float(timing["seconds_per_row"])
    )
    options: dict[str, Any] = {}
    chosen = BATCH_OPTIONS[0]
    for size in BATCH_OPTIONS:
        batch = extrapolate(physical, round(size * rows_per_file))
        fits = (
            size * group_seconds <= MAX_SLICE_SECONDS
            and size * group_bytes <= MAX_SLICE_TRANSFER_BYTES
        )
        if fits:
            chosen = size
        options[str(size)] = {
            "files": size,
            "estimated_rows": batch["rows"],
            "estimated_transfer_bytes": batch["full_file_transfer_bytes"],
            "estimated_raw_jsonl_bytes": math.ceil(batch["rows"] * raw_per_row),
            "estimated_science_tokens": batch["rows"] * tokens_per_row,
            "estimated_seconds_one_worker": batch["estimated_seconds_one_worker"],
            "slices": int(full["max_row_groups_per_file"]),
            "slice_transfer_bytes": size * group_bytes,
            "slice_raw_jsonl_bytes": math.ceil(size * group_rows * raw_per_row),
            "slice_seconds_one_worker": size * group_seconds,
            "batches_to_science_target": math.ceil(required_rows / batch["rows"]),
            "within_restart_unit_limits": fits,
        }
    return {
        "unit": "whole files in frozen inventory order; one slice per row-group index",
        "rule": "largest assessed size whose slice (the restart unit) stays within "
        f"{MAX_SLICE_SECONDS} s and {MAX_SLICE_TRANSFER_BYTES} transfer bytes at the measured "
        "one-worker rates",
        "contract_limit_files_per_plan": 256,
        "options": options,
        "chosen_files_per_batch": chosen,
        "uncertainty": "rows, bytes and time are estimates from eight footers and eight "
        "prefix windows; each batch is planned from its own real footers",
    }


def disk_budget(
    totals: Mapping[str, Any],
    scenarios: Mapping[str, int],
    slice_rows: int,
    free_bytes: int,
    total_bytes: int,
    min_free_bytes: int,
    footprint_cap_bytes: int,
) -> dict[str, Any]:
    """Steady-state and peak campaign disk by linear extrapolation of measured file sizes."""
    rows = int(totals["input_rows"])
    per_row = {
        "raw_selected_records": int(totals["raw_bytes"]) / rows,
        "rejection_ledgers": int(totals["rejections_file_bytes"]) / rows,
        "canonical_documents": int(totals["documents_file_bytes"]) / rows,
    }
    steady_per_row = sum(per_row.values())
    # Staging: the merged selection plus (parallel mode) the per-file chunks.
    transient = 2 * slice_rows * per_row["raw_selected_records"]
    cases = {}
    for name, need in scenarios.items():
        steady = need * steady_per_row
        cases[name] = {
            "input_rows": need,
            "raw_selected_records_bytes": math.ceil(need * per_row["raw_selected_records"]),
            "rejection_ledgers_bytes": math.ceil(need * per_row["rejection_ledgers"]),
            "canonical_documents_bytes": math.ceil(need * per_row["canonical_documents"]),
            "steady_state_bytes": math.ceil(steady),
            "peak_bytes": math.ceil(steady + transient),
            "fits_footprint_cap": steady + transient <= footprint_cap_bytes,
            "fits_free_space_keeping_reserve": steady + transient <= free_bytes - min_free_bytes,
            "steady_state_bytes_if_raw_also_published_to_store": math.ceil(
                steady + need * per_row["raw_selected_records"]
            ),
        }
    return {
        "basis": "measured file bytes per input row of the calibration outputs, linear",
        "bytes_per_input_row": per_row,
        "slice_transient_bytes": math.ceil(transient),
        "measured_volume": {"free_bytes": free_bytes, "total_bytes": total_bytes},
        "min_free_bytes": min_free_bytes,
        "footprint_cap_bytes": footprint_cap_bytes,
        "rows_at_footprint_cap": math.floor((footprint_cap_bytes - transient) / steady_per_row),
        "scenarios": cases,
        "raw_publication": "verification does not publish a second copy of the raw records "
        "into the artifact store: two copies would not fit this volume",
        "raw_retention": "retained; no deletion is implemented",
    }


# ------------------------------------------------------------------- campaign


def limits_policy(physical: Mapping[str, Any], totals: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen inputs of the per-slice resource limits (all derived from measurements)."""
    rows = int(totals["input_rows"])
    timing = physical["timing"]
    return {
        "restart_factor": RESTART_FACTOR,
        "metadata_bytes_per_file": MIB,
        "metadata_requests_per_file": 2 * WINDOW_METADATA_REQUESTS,
        "request_margin": 1.25,
        "decode_expansion": 1.5,
        "raw_bytes_per_row_bound": 3 * math.ceil(int(totals["raw_bytes"]) / rows),
        "planning_group_rows": int(physical["full_file"]["max_group_rows"]),
        "seconds_per_request": float(timing["seconds_per_request"]),
        "seconds_per_transfer_byte": float(timing["seconds_per_transfer_byte"]),
        "seconds_per_row": float(timing["seconds_per_row"]),
        "deadline_factor": 8,
        "min_deadline_seconds": 86400,
        "per_request_timeout_seconds": 15.0,
        "max_retries": 5,
        "max_record_bytes": 8 * MIB,
        "max_parser_bytes": 32 * MIB,
        "max_decompression_ratio": 15.0,
        "observed_max_raw_record_bytes": int(totals["max_raw_record_bytes"]),
        "max_record_bytes_basis": "the largest of 16,384 calibration records already needs "
        "about half of the 1 MiB default; one larger row would fail its whole slice, so the "
        "production bound is raised, below the unchanged 32 MiB parser bound",
    }


def slice_limits(
    rows: int,
    files: int,
    compressed: int,
    uncompressed: int,
    reads: int,
    policy: Mapping[str, Any],
    workers: int,
) -> AcquisitionLimits:
    """Bounded limits for one slice: the fetch plus ``restart_factor - 1`` restarts."""
    if min(rows, files, compressed, uncompressed, reads) < 1 or not 1 <= workers <= 16:
        raise BulkError("slice limits need positive work and 1..16 workers")
    factor = int(policy["restart_factor"])
    requests = reads + files * int(policy["metadata_requests_per_file"])
    output = ceil_mib(rows * int(policy["raw_bytes_per_row_bound"]))
    estimate = (
        requests * float(policy["seconds_per_request"])
        + compressed * float(policy["seconds_per_transfer_byte"])
        + rows * float(policy["seconds_per_row"])
    )
    return AcquisitionLimits(
        max_transferred_bytes=ceil_mib(
            factor * (compressed + files * int(policy["metadata_bytes_per_file"]))
        ),
        max_decompressed_bytes=max(
            512 * MIB, ceil_mib(factor * float(policy["decode_expansion"]) * uncompressed)
        ),
        max_records=rows,
        max_scanned_records=factor * rows,
        max_temp_disk_bytes=2 * output,
        max_output_disk_bytes=output,
        max_requests=math.ceil(factor * float(policy["request_margin"]) * requests),
        max_retries=int(policy["max_retries"]),
        per_request_timeout_seconds=float(policy["per_request_timeout_seconds"]),
        overall_deadline_seconds=float(
            max(
                int(policy["min_deadline_seconds"]),
                600 * math.ceil(float(policy["deadline_factor"]) * estimate / 600),
            )
        ),
        max_decompression_ratio=float(policy["max_decompression_ratio"]),
        max_workers=workers,
        max_record_bytes=int(policy["max_record_bytes"]),
        max_parser_bytes=int(policy["max_parser_bytes"]),
    )


def campaign_config(
    *,
    binding: Mapping[str, Any],
    adapter_code_sha256: Mapping[str, str],
    seal_digest: str,
    freeze_digest: str,
    inventory: Mapping[str, Any],
    inventory_path: str,
    catalog_path: str,
    catalog_sha256: str,
    quotas: Mapping[str, Any],
    quotas_path: str,
    quotas_sha256: str,
    required_canonical_bytes: Mapping[str, int],
    batch_files: int,
    max_batches: int,
    limits: Mapping[str, Any],
    min_free_bytes: int,
    footprint_cap_bytes: int,
) -> dict[str, Any]:
    """The frozen, self-digested campaign definition. Data only."""
    if int(inventory["file_count"]) != len(inventory["files"]) or batch_files < 1:
        raise BulkError("campaign needs a consistent inventory and a positive batch size")
    config: dict[str, Any] = {
        "kind": CAMPAIGN_KIND,
        "version": CAMPAIGN_VERSION,
        "binding": dict(binding),
        "adapter_code_sha256": dict(adapter_code_sha256),
        "calibration_seal_digest": seal_digest,
        "calibration_freeze_digest": freeze_digest,
        "inventory": {
            "path": inventory_path,
            "digest": inventory["inventory_digest"],
            "file_count": inventory["file_count"],
            "seed": inventory["seed"],
            "order": inventory["selection"],
        },
        "plan": {
            "source_id": "essential_web",
            "view_id": PLAN_VIEW,
            "adapter_spec": f"{ADAPTER_ID}:{PLAN_VIEW}",
            "catalog": catalog_path,
            "catalog_sha256": catalog_sha256,
            "seed": inventory["seed"],
            "mode": "selected_records",
            "window": {
                "policy_version": WINDOW_POLICY_VERSION,
                "stream_buffer_bytes": WINDOW_BUFFER_BYTES,
                "batch_rows": WINDOW_BATCH_ROWS,
            },
        },
        "views": list(calibration.VIEWS),
        "adapt": {"adapter_id": ADAPTER_ID, "on_reject": "record", "malformed_policy": "frozen"},
        "batch": {
            "files": batch_files,
            "membership": "inventory files [index*files, (index+1)*files) in frozen order",
            "slice": "one row-group index across the batch; one full-group window per file",
        },
        "limits": dict(limits),
        "roots": {
            "plans": "plans/ew-bulk",
            "raw": "acq-raw/ew-bulk",
            "scratch": "acq-scratch/ew-bulk",
            "canonical": "canonical/ew-bulk",
        },
        "excluded_progress": "probe and calibration outputs (calib/essential-web-production) "
        "are never counted; their files are re-acquired in inventory order like any other",
        "disk": {"min_free_bytes": min_free_bytes, "footprint_cap_bytes": footprint_cap_bytes},
        "ceiling": {
            "max_batches": max_batches,
            "meaning": "execution contingency, not a quota: beyond it a new reviewed campaign "
            "version is required",
        },
        "quotas": {
            "path": quotas_path,
            "sha256": quotas_sha256,
            "quota_id": quotas["quota_id"],
            "changed": False,
        },
        "stop": {
            "mechanism": "scripts/mix01_inventory.py estimate + sufficiency",
            "assumed_bytes_per_token": calibration.BYTES_PER_TOKEN["central"],
            "evaluated": "at batch boundaries, over complete batches only",
            "targets": {
                view: {
                    "final_exact_token_quota": int(quotas["final_quotas"][view]),
                    "first_pass_estimated_token_target": int(
                        quotas["first_pass_headroom_quotas"][view]
                    ),
                    "required_canonical_bytes": int(required_canonical_bytes[view]),
                }
                for view in calibration.VIEWS
            },
            "exactness": "estimated tokens only; exact sufficiency needs the frozen tokenizer",
        },
        "raw_retention": "raw selected records are retained; no deletion is implemented",
        "c05": {
            "obligation": essential_contamination_mitigation().model_dump(),
            "runs": "on the frozen canonical pool, before tokenizer fitting and gradient "
            "training; again on the final pool after any top-up",
            "status": "NOT RUN",
            "required_before": ["tokenizer_fit", "training", "official_benchmark_claims"],
        },
    }
    config["digest"] = canonical.digest(config)
    return config


def check_campaign(config: Mapping[str, Any]) -> None:
    """Refuse an altered or foreign campaign definition."""
    body = {key: value for key, value in config.items() if key != "digest"}
    if (
        config.get("kind") != CAMPAIGN_KIND
        or config.get("version") != CAMPAIGN_VERSION
        or canonical.digest(body) != config.get("digest")
    ):
        raise BulkError("campaign definition is altered or not an Essential-Web bulk campaign")


def batch_members(inventory: Mapping[str, Any], size: int, index: int) -> list[str]:
    """Files of batch ``index``: a pure slice of the frozen inventory order."""
    names = [str(entry["file"]) for entry in inventory["files"]]
    if size < 1 or index < 0 or index * size >= len(names):
        raise BulkError(f"batch {index} is outside the frozen inventory")
    return names[index * size : (index + 1) * size]


def plan_slices(
    files: Sequence[str],
    layouts: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
    workers: int,
) -> list[dict[str, Any]]:
    """One slice per row-group index; every row of every file exactly once."""
    if len(set(files)) != len(files) or set(layouts) != set(files):
        raise BulkError("layouts must cover exactly the distinct batch files")
    slices: list[dict[str, Any]] = []
    for index in range(max(len(layouts[name]["groups"]) for name in files)):
        members = [name for name in files if len(layouts[name]["groups"]) > index]
        groups = {name: layouts[name]["groups"][index] for name in members}
        refused = {name: g["refusal"] for name, g in groups.items() if g["refusal"] is not None}
        if refused:
            raise BulkError(f"row group {index} is refused by the window reader: {refused}")
        rows = sum(int(group["rows"]) for group in groups.values())
        compressed = sum(int(group["projected_compressed_bytes"]) for group in groups.values())
        uncompressed = sum(int(group["projected_uncompressed_bytes"]) for group in groups.values())
        reads = sum(int(group["buffered_reads"]) for group in groups.values())
        limits = slice_limits(rows, len(members), compressed, uncompressed, reads, policy, workers)
        slices.append(
            {
                "slice": index,
                "files": members,
                "row_ranges": {
                    name: [int(group["start_row"]), int(group["start_row"]) + int(group["rows"])]
                    for name, group in groups.items()
                },
                "rows": rows,
                "window_scan_rows": max(int(group["rows"]) for group in groups.values()),
                "modeled": {
                    "projected_compressed_bytes": compressed,
                    "projected_uncompressed_bytes": uncompressed,
                    "buffered_reads": reads,
                },
                "limits": limits.model_dump(),
            }
        )
    covered = {name: 0 for name in files}
    for entry in slices:
        for name, (start, stop) in entry["row_ranges"].items():
            if start != covered[name]:
                raise BulkError(f"{name}: slices do not tile the file")
            covered[name] = stop
    if any(covered[name] != int(layouts[name]["rows"]) for name in files):
        raise BulkError("slices do not cover every row of the batch")
    return slices


def authorization_digest(
    campaign_digest: str, batch: int, slices: Sequence[Mapping[str, Any]]
) -> str:
    """What the operator authorizes: this campaign, batch and these exact plan hashes."""
    return canonical.digest(
        {
            "campaign": campaign_digest,
            "batch": batch,
            "slices": [
                {key: entry[key] for key in ("slice", "attempt", "plan_hash", "limits")}
                for entry in slices
            ],
        }
    )


# ----------------------------------------------------------------- accounting

_ADDITIVE = (
    "rows",
    "malformed_rows",
    "transferred_bytes",
    "decompressed_bytes",
    "requests",
    "raw_bytes",
    "rejections_file_bytes",
    "documents_file_bytes",
)


def check_conservation(rows: int, views: Mapping[str, Mapping[str, Any]]) -> int:
    """Each row has exactly one outcome and at most one admitting view; returns malformed."""
    malformed: set[int] = set()
    for view in calibration.VIEWS:
        entry = views[view]
        codes = entry["rejection_counts_by_code"]
        others = sum(int(views[other]["documents"]) for other in calibration.VIEWS if other != view)
        if int(codes.get("EssentialWebSelectorOtherComponentError", 0)) != others:
            raise BulkError(f"{view}: rows admitted by another view do not reconcile")
        if int(entry["documents"]) + sum(int(count) for count in codes.values()) != rows:
            raise BulkError(f"{view}: adaptation does not conserve the slice rows")
        malformed.add(int(codes.get("EssentialWebMalformedRowError", 0)))
    if len(malformed) != 1:
        raise BulkError("views disagree on malformed rows")
    return malformed.pop()


def cumulative(
    batches: Mapping[int, Mapping[str, Any]], entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Deterministic cumulative yield over sealed slices; refuses any duplicate source row.

    ``batches`` maps a batch index to its planned record; ``entries`` are sealed
    slice ledger entries. Only complete batches count toward the stop decision.
    """
    seen: dict[tuple[int, int], Mapping[str, Any]] = {}
    ranges: dict[str, list[tuple[int, int]]] = {}
    owner: dict[str, int] = {}
    for index, batch in batches.items():
        for name in batch["files"]:
            if owner.setdefault(name, index) != index:
                raise BulkError(f"{name} belongs to two batches")
    for entry in entries:
        key = (int(entry["batch"]), int(entry["slice"]))
        if key in seen:
            raise BulkError(f"slice {key} is sealed twice")
        planned_batch = batches.get(key[0])
        if planned_batch is None or not 0 <= key[1] < len(planned_batch["slices"]):
            raise BulkError(f"slice {key} belongs to no planned batch")
        planned = planned_batch["slices"][key[1]]
        if (
            entry["plan_hash"] != planned["plan_hash"]
            or entry["attempt"] != planned["attempt"]
            or entry["row_ranges"] != planned["row_ranges"]
            or int(entry["rows"]) != int(planned["rows"])
        ):
            raise BulkError(f"slice {key} was not executed under its planned identity")
        for name, (start, stop) in entry["row_ranges"].items():
            if owner.get(name) != key[0]:
                raise BulkError(f"{name} is outside batch {key[0]}")
            for other_start, other_stop in ranges.setdefault(name, []):
                if start < other_stop and other_start < stop:
                    raise BulkError(f"{name}: source rows [{start},{stop}) acquired twice")
            ranges[name].append((start, stop))
        seen[key] = entry
    complete = sorted(
        index
        for index, batch in batches.items()
        if all((index, int(planned["slice"])) in seen for planned in batch["slices"])
    )
    if complete != list(range(len(complete))):
        raise BulkError("complete batches are not a contiguous prefix from batch 0")
    started = sorted({key[0] for key in seen})
    if any(index > len(complete) for index in started):
        raise BulkError("a later batch was started before an earlier batch completed")

    def total(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {key: sum(int(e[key]) for e in selected) for key in _ADDITIVE}
        result["elapsed_seconds"] = sum(float(e["elapsed_seconds"]) for e in selected)
        result["slices"] = len(selected)
        result["views"] = {
            view: {
                "documents": sum(int(e["views"][view]["documents"]) for e in selected),
                "canonical_bytes": sum(int(e["views"][view]["canonical_bytes"]) for e in selected),
            }
            for view in calibration.VIEWS
        }
        for view in calibration.VIEWS:
            result["views"][view]["estimated_tokens"] = (
                result["views"][view]["canonical_bytes"] / calibration.BYTES_PER_TOKEN["central"]
            )
        result["footprint_bytes"] = (
            result["raw_bytes"] + result["rejections_file_bytes"] + result["documents_file_bytes"]
        )
        result["malformed_rate"] = result["malformed_rows"] / result["rows"] if selected else None
        return result

    counted = [seen[key] for key in sorted(seen) if key[0] < len(complete)]
    return {
        "complete_batches": len(complete),
        "complete_files": sum(len(batches[index]["files"]) for index in complete),
        "counted": total(counted),
        "sealed_including_incomplete_batch": total([seen[key] for key in sorted(seen)]),
        "stop_basis": "complete batches only",
        "token_method": calibration.TOKEN_METHOD,
    }


def stop_decision(
    state: Mapping[str, Any], targets: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """The cumulative stop condition over complete batches (estimated tokens only)."""
    views: dict[str, Any] = {}
    for view in calibration.VIEWS:
        have = int(state["counted"]["views"][view]["canonical_bytes"])
        need = int(targets[view]["required_canonical_bytes"])
        views[view] = {
            "status": "SUFFICIENT" if have >= need else "TOP_UP",
            "acquired_canonical_bytes": have,
            "required_canonical_bytes": need,
            "deficit_canonical_bytes": max(0, need - have),
            "estimated_tokens": have / calibration.BYTES_PER_TOKEN["central"],
        }
    reached = all(entry["status"] == "SUFFICIENT" for entry in views.values())
    return {
        "target_reached": reached,
        "views": views,
        "complete_batches": state["complete_batches"],
        "next": "freeze canonical pool -> C05 exclusion receipt -> tokenizer -> exact count -> "
        "deterministic top-up of deficient components"
        if reached
        else "run the next inventory batch",
        "exact_token_sufficiency_known": False,
        "c05_receipt_required_before_training": True,
        "training_permitted": False,
    }


def gate(
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
    batch: int,
    free_bytes: int,
    slice_cap_bytes: int,
    top_up_reason: str = "",
) -> dict[str, Any]:
    """May batch ``batch`` run now? RUN, STOP_TARGET_REACHED or REFUSE, with reasons."""
    reasons: list[str] = []
    complete = int(state["complete_batches"])
    if batch > complete:
        reasons.append(f"batch {batch} cannot run before batch {complete} is complete")
    if batch >= int(config["ceiling"]["max_batches"]):
        reasons.append("batch is beyond the campaign execution ceiling")
    disk = config["disk"]
    if free_bytes - slice_cap_bytes < int(disk["min_free_bytes"]):
        reasons.append("free space would fall below the reserve during this slice")
    footprint = int(state["sealed_including_incomplete_batch"]["footprint_bytes"])
    if footprint + slice_cap_bytes > int(disk["footprint_cap_bytes"]):
        reasons.append("campaign footprint cap would be exceeded")
    if reasons:
        return {"decision": "REFUSE", "reasons": reasons}
    if batch < complete:
        return {"decision": "COMPLETE", "reasons": [f"batch {batch} is already complete"]}
    if decision["target_reached"] and not top_up_reason.strip():
        return {
            "decision": "STOP_TARGET_REACHED",
            "reasons": ["first-pass targets are met; a top-up needs an explicit reason"],
        }
    return {"decision": "RUN", "reasons": [top_up_reason.strip()] if top_up_reason.strip() else []}
