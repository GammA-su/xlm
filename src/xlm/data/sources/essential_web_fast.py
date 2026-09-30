"""Fast Essential-Web campaign: whole-file transport models, freeze and accounting.

Offline, deterministic logic only. The scientific definition is the one of the
historical bulk campaign and is read from it, never restated: same source
revision, inventory order, batch membership, B-normal selector, adapters, stop
targets and C05 obligation. Only the physical unit changes, from a projected
row-group slice read over HTTP ranges to one whole verified source Parquet file
processed locally.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources.admission import essential_contamination_mitigation
from xlm.data.sources.essential_web_bulk import GIB, MIB, SCIENCE, BulkError

CAMPAIGN_KIND = "essential_web_fast_campaign"
CAMPAIGN_VERSION = 1
RAW_CONTRACT_ID = "essential-web-raw-artifact-v2"
RAW_REPRESENTATION = "verified_source_parquet"
RECEIPT_KIND = "essential_web_fast_unit_receipt"
RECEIPT_VERSION = 1
#: Operational heuristic, not a scientific rule: below this byte ratio the
#: request-count reduction of whole-file transport is worth the extra bytes.
WHOLE_FILE_PREFERENCE_RATIO = 1.35
#: One resolve request plus its allowlisted redirect target.
WHOLE_FILE_REQUESTS = 2
BATCH_OPTIONS = bulk.BATCH_OPTIONS
#: Batch-size rule inputs (operational choices).
MAX_BATCH_SECONDS = 900
MAX_STOP_OVERSHOOT = 0.05
NETWORK_FLOOR_BYTES_PER_SECOND = 25_000_000
NETWORK_SCENARIOS_BYTES_PER_SECOND = (12_500_000, 25_000_000, 125_000_000, 500_000_000)
#: Files whose line endings may differ between checkouts are hashed normalized.
TRANSPORT_CODE_FILES = (
    "src/xlm/data/acquisition/source_parquet.py",
    "src/xlm/data/sources/essential_web_local.py",
    "src/xlm/data/adapters/columns.py",
)


def transport_code_identity(repo: Path) -> dict[str, str]:
    """SHA-256 of the code that turns a source file into records (LF-normalized)."""
    return {
        name: hashlib.sha256((repo / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in TRANSPORT_CODE_FILES
    }


# --------------------------------------------------------------------- models


def transport_comparison(
    physical: Mapping[str, Any], local: Mapping[str, Any], raw_bytes_per_row: float
) -> dict[str, Any]:
    """Projected range reader (A) against whole-file streaming (B), per real footer.

    ``physical`` is the historical full-file model built from the eight real
    footers; ``local`` holds the measured local processing rate. No file is
    read here and nothing is extrapolated beyond those eight layouts.
    """
    seconds_per_request = float(physical["timing"]["seconds_per_request"])
    files: list[dict[str, Any]] = []
    for entry in physical["files"]:
        projected = int(entry["full_file_transfer_bytes"])
        whole = int(entry["remote_length"])
        files.append(
            {
                "file": entry["file"],
                "rows": entry["rows"],
                "row_groups": entry["row_groups"],
                "projected_range_reader": {
                    "transfer_bytes": projected,
                    "http_requests": entry["requests"],
                    "request_latency_seconds": int(entry["requests"]) * seconds_per_request,
                },
                "whole_file_stream": {
                    "transfer_bytes": whole,
                    "http_requests": WHOLE_FILE_REQUESTS,
                    "request_latency_seconds": WHOLE_FILE_REQUESTS * seconds_per_request,
                    "scratch_bytes": whole,
                },
                "whole_over_projected": whole / projected,
            }
        )
    projected_total = sum(int(f["projected_range_reader"]["transfer_bytes"]) for f in files)
    whole_total = sum(int(f["whole_file_stream"]["transfer_bytes"]) for f in files)
    requests_total = sum(int(f["projected_range_reader"]["http_requests"]) for f in files)
    rows = sum(int(f["rows"]) for f in files)
    ratio = whole_total / projected_total
    ratios = [float(f["whole_over_projected"]) for f in files]
    return {
        "evidence": "the eight real retained footers (sizes only) and the sealed calibration "
        "timing; no file was transferred for this comparison",
        "known_layout_files": len(files),
        "files": files,
        "projected_range_reader": {
            "transfer_bytes": projected_total,
            "transfer_bytes_per_row": projected_total / rows,
            "http_requests": requests_total,
            "http_requests_per_file": requests_total / len(files),
            "latency_sensitivity": "one round trip per 4 MiB range and four metadata requests "
            "per row group; wall time is dominated by request count, not by bytes",
            "request_latency_seconds_per_file": requests_total * seconds_per_request / len(files),
            "local_read_cost": "none; decode happens inside the fetch",
            "temporary_disk": "selected-record JSONL staging, twice the slice output",
            "raw_jsonl_bytes_per_row": raw_bytes_per_row,
        },
        "whole_file_stream": {
            "transfer_bytes": whole_total,
            "transfer_bytes_per_row": whole_total / rows,
            "http_requests": WHOLE_FILE_REQUESTS * len(files),
            "http_requests_per_file": WHOLE_FILE_REQUESTS,
            "latency_sensitivity": "two round trips per file; wall time follows bytes and the "
            "endpoint's per-stream rate",
            "request_latency_seconds_per_file": WHOLE_FILE_REQUESTS * seconds_per_request,
            "local_read_cost": {
                "rows_per_core_second": local["rows_per_core_second"],
                "core_seconds_per_file": rows / len(files) / float(local["rows_per_core_second"]),
                "basis": local["basis"],
            },
            "temporary_disk": "the compressed source file on fast scratch until its canonical "
            "outputs are published",
            "scratch_bytes_per_file": whole_total / len(files),
        },
        "whole_over_projected": ratio,
        "whole_file_overhead_fraction": ratio - 1,
        "whole_over_projected_min_max_file": [min(ratios), max(ratios)],
        "request_reduction_factor": requests_total / (WHOLE_FILE_REQUESTS * len(files)),
        "preference_threshold": WHOLE_FILE_PREFERENCE_RATIO,
        "threshold_kind": "operational heuristic, not a scientific rule",
        "chosen_transport": "whole_file_stream"
        if ratio <= WHOLE_FILE_PREFERENCE_RATIO
        else "projected_range_reader",
        "seconds_per_request_measured": seconds_per_request,
    }


def storage_model(
    totals: Mapping[str, Any],
    physical: Mapping[str, Any],
    ledger: Mapping[str, Any],
    scenarios: Mapping[str, int],
    *,
    bookkeeping_bytes_per_file: int,
    scratch_cap_bytes: int,
    max_in_flight_files: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    """Durable and scratch bytes of the historical and the fast representation.

    Linear extrapolation of measured bytes per input row. The ledger ratio is
    the one measured on the real calibration ledgers at the frozen codec level.
    """
    rows = int(totals["input_rows"])
    known_rows = int(physical["full_file"]["rows"])
    source_per_row = sum(int(f["remote_length"]) for f in physical["files"]) / known_rows
    rows_per_file = float(physical["full_file"]["mean_rows_per_file"])
    mean_file = source_per_row * rows_per_file
    current = {
        "raw_selected_records": int(totals["raw_bytes"]) / rows,
        "rejection_ledgers": int(totals["rejections_file_bytes"]) / rows,
        "canonical_documents": int(totals["documents_file_bytes"]) / rows,
    }
    fast = {
        "retained_source_parquet": source_per_row,
        "compressed_rejection_ledgers": int(ledger["compressed_bytes"]) / rows,
        "canonical_documents": current["canonical_documents"],
    }
    cases: dict[str, Any] = {}
    for name, need in scenarios.items():
        files = math.ceil(need / rows_per_file)
        old = {key: math.ceil(need * value) for key, value in current.items()}
        new = {key: math.ceil(need * value) for key, value in fast.items()}
        new["manifests_and_receipts"] = files * bookkeeping_bytes_per_file
        steady = sum(new.values())
        scratch_expected = math.ceil(max_in_flight_files * mean_file)
        cases[name] = {
            "input_rows": need,
            "estimated_files": files,
            "current": {**old, "steady_state_bytes": sum(old.values())},
            "fast": {
                **new,
                "durable_steady_state_bytes": steady,
                "durable_peak_bytes": steady,
                "scratch_peak_bytes_expected": scratch_expected,
                "scratch_peak_bytes_bound": min(
                    scratch_cap_bytes, max_in_flight_files * max_file_bytes
                ),
                "total_campaign_footprint_bytes": steady + scratch_expected,
            },
            "durable_saving_fraction": 1 - steady / sum(old.values()),
        }
    return {
        "basis": "measured bytes per input row, linear; source Parquet from the eight real "
        "footers, ledgers and documents from the sealed calibration outputs",
        "bytes_per_input_row": {"current": current, "fast": fast},
        "ledger_compression": dict(ledger),
        "durable_peak_note": "the durable peak equals the steady state: a raw copy and a "
        "canonical unit are written under temporary names and then linked or renamed, so no "
        "second durable copy of finished data exists",
        "scratch": {
            "cap_bytes": scratch_cap_bytes,
            "max_in_flight_files": max_in_flight_files,
            "mean_file_bytes": mean_file,
            "max_file_bytes": max_file_bytes,
        },
        "scenarios": cases,
        "raw_retention": "one authoritative raw representation: the immutable source Parquet; "
        "selected-record JSONL is never written to disk",
    }


def throughput_model(
    physical: Mapping[str, Any],
    local: Mapping[str, Any],
    batch_files: int,
    process_workers: int,
) -> dict[str, Any]:
    """Historical measured-coefficient model against the parametric fast model.

    The remote endpoint's rate is unknown until the live benchmark, so the fast
    model is a function of it. Nothing here is a throughput promise.
    """
    full, timing = physical["full_file"], physical["timing"]
    rows = round(batch_files * float(full["mean_rows_per_file"]))
    old = bulk.extrapolate(physical, rows)
    old_requests = int(old["requests"])
    old_bytes = int(old["full_file_transfer_bytes"])
    old_parts = {
        "request_latency_seconds": old_requests * float(timing["seconds_per_request"]),
        "body_seconds": old_bytes * float(timing["seconds_per_transfer_byte"]),
        "row_seconds": rows * float(timing["seconds_per_row"]),
    }
    whole = sum(int(f["remote_length"]) for f in physical["files"]) / int(full["rows"]) * rows
    parallel = local["rows_per_second_by_process_workers"].get(str(process_workers))
    cpu_seconds = None if parallel is None else rows / float(parallel)
    scenarios = []
    for rate in NETWORK_SCENARIOS_BYTES_PER_SECOND:
        network = whole / rate
        scenarios.append(
            {
                "aggregate_network_bytes_per_second": rate,
                "aggregate_network_megabits_per_second": rate * 8 / 1e6,
                "network_seconds": network,
                "batch_seconds_pipelined": None
                if cpu_seconds is None
                else max(network, cpu_seconds),
                "limited_by": None
                if cpu_seconds is None
                else ("network" if network > cpu_seconds else "local processing"),
            }
        )
    return {
        "batch_files": batch_files,
        "estimated_rows": rows,
        "historical_range_reader": {
            "transfer_bytes": old_bytes,
            "http_requests": old_requests,
            "seconds_one_worker": old["estimated_seconds_one_worker"],
            "components": old_parts,
            "request_latency_share": old_parts["request_latency_seconds"]
            / float(old["estimated_seconds_one_worker"]),
            "basis": physical["timing"]["basis"],
        },
        "fast_whole_file": {
            "transfer_bytes": math.ceil(whole),
            "http_requests": WHOLE_FILE_REQUESTS * batch_files,
            "process_workers": process_workers,
            "local_rows_per_second": parallel,
            "local_processing_seconds": cpu_seconds,
            "local_basis": local["basis"],
            "network_scenarios": scenarios,
            "network_rate": "UNKNOWN until the live benchmark; the scenarios are not claims",
            "durable_copy": "sequential copy to the durable volume, measured by the benchmark",
        },
    }


def batch_policy(
    physical: Mapping[str, Any],
    local: Mapping[str, Any],
    totals: Mapping[str, Any],
    required_rows: int,
    process_workers: int,
    historical_files: int,
) -> dict[str, Any]:
    """Re-assess 8/16/32/64 files per batch for file-level restart units."""
    options: dict[str, Any] = {}
    chosen = BATCH_OPTIONS[0]
    tokens_per_row = int(totals["retained"][SCIENCE]["canonical_bytes"]) / (
        calibration.BYTES_PER_TOKEN["central"] * int(totals["input_rows"])
    )
    target = required_rows * tokens_per_row
    for size in BATCH_OPTIONS:
        model = throughput_model(physical, local, size, process_workers)["fast_whole_file"]
        floor = next(
            entry
            for entry in model["network_scenarios"]
            if entry["aggregate_network_bytes_per_second"] == NETWORK_FLOOR_BYTES_PER_SECOND
        )
        rows = round(size * float(physical["full_file"]["mean_rows_per_file"]))
        overshoot = rows * tokens_per_row / target
        seconds = floor["batch_seconds_pipelined"]
        fits = (
            seconds is not None
            and seconds <= MAX_BATCH_SECONDS
            and overshoot <= (MAX_STOP_OVERSHOOT)
        )
        if fits:
            chosen = size
        options[str(size)] = {
            "files": size,
            "estimated_rows": rows,
            "estimated_transfer_bytes": model["transfer_bytes"],
            "local_processing_seconds": model["local_processing_seconds"],
            "batch_seconds_at_network_floor": seconds,
            "stop_overshoot_fraction_of_science_target": overshoot,
            "batches_to_science_target": math.ceil(required_rows / rows),
            "within_limits": fits,
        }
    return {
        "unit": "whole files in frozen inventory order; the restart unit is ONE FILE",
        "rule": f"largest assessed size whose batch stays within {MAX_BATCH_SECONDS} s at a "
        f"{NETWORK_FLOOR_BYTES_PER_SECOND} B/s aggregate network floor and the measured local "
        f"rate, and whose stop overshoot stays within {MAX_STOP_OVERSHOOT:.0%} of the science "
        "target",
        "network_floor_kind": "planning assumption, not a measured endpoint rate",
        "process_workers": process_workers,
        "options": options,
        "chosen_files_per_batch": chosen,
        "historical_files_per_batch": historical_files,
        "membership_unchanged": chosen == historical_files,
        "uncertainty": "rows and bytes are estimates from eight footers; the network rate is "
        "unmeasured; the local rate is measured on calibration rows",
    }


# ------------------------------------------------------------------- campaign


def limits_policy(historical: Mapping[str, Any], physical: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen per-file and per-batch bounds. Every value is a refusal threshold."""
    largest = max(int(f["remote_length"]) for f in physical["files"])
    most_rows = int(physical["full_file"]["min_max_rows_per_file"][1])
    max_file = 512 * MIB
    if largest * 1.5 > max_file:
        raise BulkError("per-file byte bound no longer covers the observed files with margin")
    return {
        "max_file_bytes": max_file,
        "observed_max_file_bytes": largest,
        #: One whole restart from zero per file on average, never more.
        "transfer_factor": 1.5,
        "max_requests_per_file": 16,
        "max_retries": int(historical["max_retries"]),
        "request_timeout_seconds": 30.0,
        "file_deadline_seconds": 1800.0,
        "batch_deadline_seconds": 14400.0,
        "deadline_basis": "a stalled stream fails after the request timeout and resumes; the "
        "file deadline bounds one file at about 0.3 MiB/s and the batch deadline one run",
        "max_rows_per_file": 250_000,
        "observed_max_rows_per_file": most_rows,
        "max_decoded_bytes_per_file": 2 * GIB,
        "max_ledger_bytes": 512 * MIB,
        "max_durable_bytes_per_file": GIB,
        "max_record_bytes": int(historical["max_record_bytes"]),
        "max_parser_bytes": int(historical["max_parser_bytes"]),
        "max_decompression_ratio": float(historical["max_decompression_ratio"]),
        "require_etag_sha256": True,
    }


def batch_limits(
    files: int, policy: Mapping[str, Any], scratch_cap_bytes: int, workers: int
) -> AcquisitionLimits:
    """The authorized bounds of one whole-file batch plan."""
    if files < 1 or not 1 <= workers <= 16:
        raise BulkError("batch limits need files and 1..16 workers")
    return AcquisitionLimits(
        max_transferred_bytes=bulk.ceil_mib(
            files * int(policy["max_file_bytes"]) * float(policy["transfer_factor"])
        ),
        max_decompressed_bytes=files * int(policy["max_decoded_bytes_per_file"]),
        max_records=files * int(policy["max_rows_per_file"]),
        max_scanned_records=files * int(policy["max_rows_per_file"]),
        max_temp_disk_bytes=scratch_cap_bytes,
        max_output_disk_bytes=files * int(policy["max_durable_bytes_per_file"]),
        max_requests=files * int(policy["max_requests_per_file"]),
        max_retries=int(policy["max_retries"]),
        per_request_timeout_seconds=float(policy["request_timeout_seconds"]),
        overall_deadline_seconds=float(policy["batch_deadline_seconds"]),
        max_decompression_ratio=float(policy["max_decompression_ratio"]),
        max_workers=workers,
        max_record_bytes=int(policy["max_record_bytes"]),
        max_parser_bytes=int(policy["max_parser_bytes"]),
    )


def raw_contract() -> dict[str, Any]:
    """The versioned raw-artifact amendment this campaign executes under. Data only."""
    return {
        "contract_id": RAW_CONTRACT_ID,
        "amends": "C04 bounded acquisition, raw artifact of source 'essential_web' only",
        "representation": RAW_REPRESENTATION,
        "existing_basis": "AcquisitionMode.WHOLE_FILE already makes whole immutable originals "
        "a valid raw acquisition ('Whole original identity retained'); C04 prefers 'selected "
        "immutable shards' over repository snapshots, and a batch is 32 selected shards",
        "what_is_new": [
            "the whole-file executor: one sequential stream per file with a resumable verified "
            "prefix, instead of the fetcher whose per-row inspection refuses these row groups",
            "adaptation reads the verified source Parquet directly; the selected-record stream "
            "is produced in memory and never written to disk",
            "the immutable rejection ledger is stored zstd-compressed and identified by the "
            "SHA-256 of its uncompressed version-1 JSONL bytes",
        ],
        "authoritative_raw": {
            "file": "the unmodified upstream Parquet file",
            "identity": [
                "repository",
                "immutable revision in the request URL",
                "source path",
                "strong ETag",
                "remote length",
                "locally computed SHA-256 of every byte",
            ],
            "stronger_than_before": "the historical raw artifact hashed a derived projection "
            "of selected rows and stated that it could not verify a full-file digest; this one "
            "hashes the complete original and, where the strong ETag is a 64-hex digest, "
            "requires it to equal that hash",
            "immutability": "linked exclusively under its final name after its copy reproduced "
            "the verified SHA-256; an existing file is never replaced",
        },
        "derived_lineage_kept_per_file": [
            "deterministic campaign membership (inventory rank and batch)",
            "row accounting: every row of the file has exactly one outcome per view",
            "SHA-256 and size of the selected-record stream the certified reader would write",
            "per view: documents hash, canonical bytes, rejection counts by code, ledger hash",
            "acquisition receipt with transfer, request and retry counts",
        ],
        "reproducibility": "canonical outputs are reproducible offline from the retained "
        "source Parquet, the pinned code identity and the receipt; no network is needed",
        "not_permitted": [
            "deleting or replacing a retained source file, a ledger or a receipt",
            "keeping a second raw representation",
            "any selector, mixture, quota or stop-target change",
        ],
        "scope": "this campaign and source only; the historical selected_records contract and "
        "every other source are unchanged",
    }


def campaign_config(
    *,
    historical: Mapping[str, Any],
    historical_path: str,
    transport_code_sha256: Mapping[str, str],
    batch_files: int,
    limits: Mapping[str, Any],
    scratch: Mapping[str, Any],
    concurrency: Mapping[str, Any],
    min_free_bytes: int,
    footprint_cap_bytes: int,
    benchmark_digest: str,
) -> dict[str, Any]:
    """The frozen fast campaign. Scientific fields are copied from the historical one."""
    bulk.check_campaign(historical)
    if batch_files < 1:
        raise BulkError("campaign needs a positive batch size")
    config: dict[str, Any] = {
        "kind": CAMPAIGN_KIND,
        "version": CAMPAIGN_VERSION,
        "supersedes": {
            "kind": historical["kind"],
            "path": historical_path,
            "digest": historical["digest"],
            "status": "historical; preserved unmodified; it executed no bulk fetch",
        },
        "binding": dict(historical["binding"]),
        "adapter_code_sha256": dict(historical["adapter_code_sha256"]),
        "transport_code_sha256": dict(transport_code_sha256),
        "calibration_seal_digest": historical["calibration_seal_digest"],
        "calibration_freeze_digest": historical["calibration_freeze_digest"],
        "inventory": dict(historical["inventory"]),
        "plan": {
            "source_id": historical["plan"]["source_id"],
            "view_id": historical["plan"]["view_id"],
            "catalog": historical["plan"]["catalog"],
            "catalog_sha256": historical["plan"]["catalog_sha256"],
            "seed": historical["plan"]["seed"],
            "mode": "whole_file",
            "projection": historical["plan"]["adapter_spec"],
        },
        "views": list(historical["views"]),
        "adapt": dict(historical["adapt"]),
        "raw_contract": raw_contract(),
        "batch": {
            "files": batch_files,
            "membership": historical["batch"]["membership"],
            "unit": "one whole source file: download, verify, retain, adapt, receipt",
        },
        "limits": dict(limits),
        "scratch": dict(scratch),
        "concurrency": dict(concurrency),
        "roots": {
            "plans": "plans/ew-fast",
            "raw": "acq-raw/ew-fast/source",
            "canonical": "canonical/ew-fast",
            "scratch": "ew-fast",
        },
        "excluded_progress": "benchmark, probe and calibration transfers are never counted; "
        "progress is defined only by sealed unit receipts of this campaign",
        "disk": {"min_free_bytes": min_free_bytes, "footprint_cap_bytes": footprint_cap_bytes},
        "ceiling": dict(historical["ceiling"]),
        "quotas": dict(historical["quotas"]),
        "stop": dict(historical["stop"]),
        "benchmark": {
            "plan_digest": benchmark_digest,
            "required_before_first_batch": True,
            "why": "source-identity behaviour and parity on real upstream bytes cannot be "
            "proved offline",
        },
        "c05": {
            "obligation": essential_contamination_mitigation().model_dump(),
            "runs": historical["c05"]["runs"],
            "status": "NOT RUN",
            "required_before": list(historical["c05"]["required_before"]),
        },
    }
    config["digest"] = canonical.digest(config)
    return config


def check_campaign(config: Mapping[str, Any]) -> None:
    """Refuse an altered or foreign fast campaign definition."""
    body = {key: value for key, value in config.items() if key != "digest"}
    if (
        config.get("kind") != CAMPAIGN_KIND
        or config.get("version") != CAMPAIGN_VERSION
        or canonical.digest(body) != config.get("digest")
    ):
        raise BulkError("campaign definition is altered or not a fast Essential-Web campaign")


def check_same_science(config: Mapping[str, Any], historical: Mapping[str, Any]) -> None:
    """The fast campaign may differ from the historical one only physically."""
    bulk.check_campaign(historical)
    same = (
        "binding",
        "adapter_code_sha256",
        "calibration_seal_digest",
        "calibration_freeze_digest",
        "inventory",
        "views",
        "adapt",
        "ceiling",
        "quotas",
        "stop",
    )
    changed = [key for key in same if config[key] != historical[key]]
    plan = ("source_id", "view_id", "catalog", "catalog_sha256", "seed")
    changed += [f"plan.{key}" for key in plan if config["plan"][key] != historical["plan"][key]]
    if config["supersedes"]["digest"] != historical["digest"]:
        changed.append("supersedes")
    if config["c05"]["obligation"] != historical["c05"]["obligation"]:
        changed.append("c05")
    if changed:
        raise BulkError(f"fast campaign changes scientific fields: {sorted(changed)}")


def authorization_digest(
    campaign_digest: str, batch: int, membership_digest: str, plan_hash: str, limits: Any
) -> str:
    """What the operator authorizes: this campaign, batch, membership, plan and limits."""
    return canonical.digest(
        {
            "campaign": campaign_digest,
            "batch": batch,
            "membership": membership_digest,
            "plan_hash": plan_hash,
            "limits": limits,
        }
    )


def benchmark_plan(
    config_body: Mapping[str, Any],
    files: Sequence[str],
    tiers: Sequence[int],
    parity: Mapping[str, Any],
) -> dict[str, Any]:
    """A small disjoint-file transport benchmark plus one real-byte parity file."""
    limits = config_body["limits"]
    groups, cursor = [], 0
    for workers in tiers:
        members = list(files[cursor : cursor + workers])
        if workers < 1 or len(members) != workers:
            raise BulkError("benchmark tiers need one distinct batch file per worker")
        groups.append({"download_workers": workers, "files": members})
        cursor += workers
    count = cursor + 1
    plan: dict[str, Any] = {
        "kind": "essential_web_fast_benchmark",
        "version": 1,
        "binding": dict(config_body["binding"]),
        "transport_code_sha256": dict(config_body["transport_code_sha256"]),
        "tiers": groups,
        "files": cursor,
        "parity": dict(parity),
        "limits": {
            "max_file_bytes": limits["max_file_bytes"],
            "max_transferred_bytes": count * int(limits["max_file_bytes"]),
            "max_requests": count * int(limits["max_requests_per_file"]),
            "max_retries": limits["max_retries"],
            "request_timeout_seconds": limits["request_timeout_seconds"],
            "file_deadline_seconds": limits["file_deadline_seconds"],
            "deadline_seconds": 3600.0,
        },
        "retention": "nothing is retained: every benchmark file and output lives under the "
        "scratch root and is removed at the end; only the metrics report is kept",
        "counts_as_campaign_progress": False,
    }
    plan["digest"] = canonical.digest(plan)
    return plan


# ----------------------------------------------------------------- accounting

_ADDITIVE = (
    "rows",
    "malformed_rows",
    "transferred_bytes",
    "requests",
    "raw_bytes",
    "rejections_file_bytes",
    "documents_file_bytes",
    "footprint_bytes",
)


def unit_receipt(
    *,
    campaign_digest: str,
    batch: int,
    rank: int,
    plan: Mapping[str, Any],
    source: Mapping[str, Any],
    raw_path: str,
    result: Mapping[str, Any],
    transfer: Mapping[str, Any],
    bookkeeping_bytes: int,
    sealed_at: str,
) -> dict[str, Any]:
    """The immutable receipt of one file unit; row conservation is checked here."""
    rows = int(result["rows"])
    if rows != int(result["file_rows"]) or result["row_range"] != [0, rows]:
        raise BulkError(f"{result['source_file']}: a campaign unit must cover the whole file")
    if result["source_file"] != source["source_file"]:
        raise BulkError("adapted file differs from the verified source")
    views = {view: dict(result["views"][view]) for view in calibration.VIEWS}
    malformed = bulk.check_conservation(rows, views)
    documents = sum(int(view["documents_file_bytes"]) for view in views.values())
    ledgers = sum(int(view["rejections_file_bytes"]) for view in views.values())
    receipt: dict[str, Any] = {
        "kind": RECEIPT_KIND,
        "version": RECEIPT_VERSION,
        "campaign": campaign_digest,
        "batch": batch,
        "inventory_rank": rank,
        "file": source["source_file"],
        "plan": dict(plan),
        "source": dict(source),
        "raw": {
            "representation": RAW_REPRESENTATION,
            "contract_id": RAW_CONTRACT_ID,
            "path": raw_path,
            "bytes": source["length"],
            "sha256": source["sha256"],
        },
        "rows": rows,
        "row_groups": result["row_groups"],
        "selected_records": {
            "sha256": result["selected_records_sha256"],
            "bytes": result["selected_records_bytes"],
            "max_record_bytes": result["max_selected_record_bytes"],
            "stored": False,
            "meaning": "hash of the selected-record stream the certified reader writes for "
            "these rows under this plan; reproducible from the retained source file",
        },
        "views": views,
        "malformed_rows": malformed,
        "transfer": dict(transfer),
        "transferred_bytes": int(transfer["transferred_bytes"]),
        "requests": int(transfer["requests"]),
        "processing": {
            "seconds": result["process_seconds"],
            "cpu_seconds": result["process_cpu_seconds"],
            "promote_seconds": result["promote_seconds"],
            "decoded_bytes": result["decoded_bytes"],
        },
        "raw_bytes": int(source["length"]),
        "documents_file_bytes": documents,
        "rejections_file_bytes": ledgers,
        "footprint_bytes": int(source["length"]) + documents + ledgers + bookkeeping_bytes,
        "sealed_at": sealed_at,
    }
    receipt["digest"] = canonical.digest(receipt)
    return receipt


def cumulative(
    batches: Mapping[int, Mapping[str, Any]], receipts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Deterministic cumulative yield over sealed file units; refuses any duplicate file.

    ``batches`` maps a planned batch index to ``{"files", "plan_hash"}``. Only
    complete batches count toward the stop decision.
    """
    owner: dict[str, int] = {}
    for index, batch in batches.items():
        for name in batch["files"]:
            if owner.setdefault(name, index) != index:
                raise BulkError(f"{name} belongs to two batches")
    seen: dict[str, Mapping[str, Any]] = {}
    for receipt in receipts:
        name, index = str(receipt["file"]), int(receipt["batch"])
        if name in seen:
            raise BulkError(f"{name} is sealed twice")
        if owner.get(name) != index:
            raise BulkError(f"{name} belongs to no planned batch {index}")
        if receipt["plan"]["plan_hash"] != batches[index]["plan_hash"]:
            raise BulkError(f"{name} was not executed under its planned identity")
        if receipt["source"]["sha256"] != receipt["raw"]["sha256"]:
            raise BulkError(f"{name}: receipt does not bind its raw file")
        seen[name] = receipt
    complete = sorted(
        index for index, batch in batches.items() if all(name in seen for name in batch["files"])
    )
    if complete != list(range(len(complete))):
        raise BulkError("complete batches are not a contiguous prefix from batch 0")
    if any(int(receipt["batch"]) > len(complete) for receipt in seen.values()):
        raise BulkError("a later batch was started before an earlier batch completed")

    def total(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {key: sum(int(e[key]) for e in selected) for key in _ADDITIVE}
        result["files"] = len(selected)
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
        result["malformed_rate"] = result["malformed_rows"] / result["rows"] if selected else None
        return result

    ordered = [seen[name] for name in sorted(seen, key=lambda n: int(seen[n]["inventory_rank"]))]
    return {
        "complete_batches": len(complete),
        "complete_files": sum(len(batches[index]["files"]) for index in complete),
        "counted": total([r for r in ordered if int(r["batch"]) < len(complete)]),
        "sealed_including_incomplete_batch": total(ordered),
        "stop_basis": "complete batches only",
        "token_method": calibration.TOKEN_METHOD,
    }


def gate(
    config: Mapping[str, Any],
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
    batch: int,
    free_bytes: int,
    batch_cap_bytes: int,
    *,
    benchmark_passed: bool,
    historical_ledger_entries: int,
    top_up_reason: str = "",
) -> dict[str, Any]:
    """The historical gate plus the fast-campaign preconditions."""
    result = bulk.gate(config, state, decision, batch, free_bytes, batch_cap_bytes, top_up_reason)
    reasons = list(result["reasons"]) if result["decision"] == "REFUSE" else []
    if historical_ledger_entries:
        reasons.append("the superseded campaign has sealed slices; the two must not be mixed")
    if config["benchmark"]["required_before_first_batch"] and not benchmark_passed:
        reasons.append("the transport benchmark and real-byte parity check have not passed")
    if reasons:
        return {"decision": "REFUSE", "reasons": reasons}
    return result
