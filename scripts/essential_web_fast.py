"""Fast Essential-Web campaign: whole-file transport, local processing and accounting.

The scientific definition is read from the historical bulk campaign and must
match it exactly; this tool changes only how bytes reach the adapters. A unit is
one whole source file: streamed to fast scratch, verified, copied into the
durable store as the immutable raw artifact, adapted locally into the three
views and sealed with a write-once receipt. Planning a batch is offline.

Network commands: ``run`` and ``benchmark`` only. Nothing here deletes a sealed
unit, a retained source file or a receipt.

Exit codes: 0 success/continue, 1 refusal, 3 first-pass targets reached,
4 batch already complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import essential_web_bulk as historical_tool
import essential_web_calibration_seal as sealer
import mix01_inventory
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from filelock import FileLock, Timeout

from xlm.data.acquisition.plan import (
    AcquisitionPlan,
    load_acquisition_plan,
    plan_requires_production_admission,
    validate_plan_authorization,
)
from xlm.data.acquisition.sampling import canonical_range_url
from xlm.data.acquisition.source_parquet import (
    ScratchBudget,
    TransferLimits,
    TransferMeter,
    TransferResult,
    identity_path,
    identity_record,
    load_durable_source,
)
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_local as local

REPO = Path(__file__).resolve().parents[1]
#: The checkout that holds the executing code, even when tests relocate REPO.
CODE_REPO = REPO
FAST_DIR = f"{historical_tool.EVIDENCE}/ESSENTIAL-WEB-FAST-TRANSPORT"
HISTORICAL_CAMPAIGN = f"{historical_tool.BULK_DIR}/bulk-campaign.json"
EXIT_REFUSED, EXIT_TARGET_REACHED, EXIT_COMPLETE = 1, 3, 4
MIN_FREE_BYTES = 64 * bulk.GIB
FOOTPRINT_CAP_BYTES = 400 * bulk.GIB
SCRATCH_CAP_BYTES = 64 * bulk.GIB
SCRATCH_MIN_FREE_BYTES = 32 * bulk.GIB
BOOKKEEPING_BYTES_PER_FILE = 32 * 1024
BENCHMARK_TIERS = (1, 4, 8)
LOCAL_BENCH_WORKERS = (1, 4, 8, 12, 16)

read_json = historical_tool.read_json
write_json = historical_tool.write_json
write_once = historical_tool.write_once
self_digest = historical_tool.self_digest
check_digest = historical_tool.check_digest


@dataclass(frozen=True)
class Campaign:
    """A verified fast campaign bound to one data root and one scratch root."""

    config: dict[str, Any]
    inventory: dict[str, Any]
    root: Path
    scratch_root: Path | None
    repo: Path

    @property
    def plans(self) -> Path:
        return self.root / str(self.config["roots"]["plans"])

    def batch_dir(self, batch: int) -> Path:
        return self.plans / f"b{batch:04d}"

    def members(self, batch: int) -> list[str]:
        return bulk.batch_members(self.inventory, int(self.config["batch"]["files"]), batch)

    def rank(self, batch: int, position: int) -> int:
        return batch * int(self.config["batch"]["files"]) + position

    def raw_path(self, name: str) -> Path:
        return self.root / str(self.config["roots"]["raw"]) / name

    def unit_dir(self, batch: int, rank: int) -> Path:
        return self.root / str(self.config["roots"]["canonical"]) / f"b{batch:04d}" / f"f{rank:05d}"

    def staging(self, batch: int) -> Path:
        return self.root / str(self.config["roots"]["canonical"]) / ".staging" / f"b{batch:04d}"

    def scratch(self, *parts: str) -> Path:
        if self.scratch_root is None:
            raise bulk.BulkError("set XLM_SCRATCH_ROOT or --scratch-root")
        return self.scratch_root.joinpath(str(self.config["roots"]["scratch"]), *parts)

    def process_limits(self) -> dict[str, Any]:
        limits = self.config["limits"]
        return {
            key: limits[key]
            for key in (
                "max_decompression_ratio",
                "max_parser_bytes",
                "max_rows_per_file",
                "max_record_bytes",
                "max_decoded_bytes_per_file",
                "max_ledger_bytes",
            )
        }

    def transfer_limits(self) -> TransferLimits:
        limits = self.config["limits"]
        return TransferLimits(
            max_file_bytes=int(limits["max_file_bytes"]),
            max_transfer_bytes=2 * int(limits["max_file_bytes"]),
            max_requests=int(limits["max_requests_per_file"]),
            max_retries=int(limits["max_retries"]),
            request_timeout_seconds=float(limits["request_timeout_seconds"]),
            deadline_seconds=float(limits["file_deadline_seconds"]),
        )


def check_roots(data_root: Path, scratch_root: Path | None) -> None:
    """Scratch is temporary and must never overlap the durable store."""
    if scratch_root is None:
        return
    durable, scratch = data_root.resolve(), scratch_root.resolve()
    if durable == scratch or scratch.is_relative_to(durable) or durable.is_relative_to(scratch):
        raise bulk.BulkError("scratch root and durable data root must be disjoint")


def load_campaign(
    path: Path, data_root: Path, scratch_root: Path | None = None, repo: Path | None = None
) -> Campaign:
    """Load the fast campaign; refuse any drift from the historical science or the code."""
    repo = REPO if repo is None else repo
    config = read_json(path)
    fast.check_campaign(config)
    historical_path = repo / config["supersedes"]["path"]
    historical = historical_tool.load_campaign(historical_path, data_root, repo)
    fast.check_same_science(config, historical.config)
    if fast.transport_code_identity(CODE_REPO) != config["transport_code_sha256"]:
        raise bulk.BulkError("transport or local-processing code changed since the freeze")
    check_roots(data_root, scratch_root)
    return Campaign(
        config=config,
        inventory=historical.inventory,
        root=data_root,
        scratch_root=scratch_root,
        repo=repo,
    )


def source_url(plan: AcquisitionPlan, name: str) -> str:
    return canonical_range_url(plan.provider, plan.repository, plan.revision, name)


def check_admission(plan: AcquisitionPlan) -> None:
    """The stored C04 production admission of this exact source, view and revision."""
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sources.admission import resolve_verified_production_admission

    if not plan_requires_production_admission(plan):
        raise bulk.BulkError("whole-file plan is unexpectedly inside the pilot scope")
    resolve_verified_production_admission(plan, ArtifactStore(ArtifactPaths.from_env()))


# ------------------------------------------------------------------ telemetry


class Sampler(threading.Thread):
    """One-second system samples: CPU, received bytes, written bytes and resident memory."""

    def __init__(self, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.interval, self._halt = interval, threading.Event()
        self.samples: list[tuple[float, float, int, int, int]] = []

    @staticmethod
    def _reading() -> tuple[int, int, int]:
        network = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        resident = 0
        try:
            process = psutil.Process()
            resident = process.memory_info().rss + sum(
                child.memory_info().rss for child in process.children(recursive=True)
            )
        except psutil.Error:
            pass
        return int(network.bytes_recv), 0 if disk is None else int(disk.write_bytes), resident

    def run(self) -> None:
        psutil.cpu_percent(None)
        while not self._halt.wait(self.interval):
            self.samples.append((time.monotonic(), psutil.cpu_percent(None), *self._reading()))

    def stop(self) -> dict[str, Any]:
        self._halt.set()
        self.join(timeout=5)
        if len(self.samples) < 2:
            return {"samples": len(self.samples)}
        first, last = self.samples[0], self.samples[-1]
        seconds = last[0] - first[0]
        steps = list(zip(self.samples, self.samples[1:], strict=False))
        return {
            "samples": len(self.samples),
            "seconds": seconds,
            "logical_cpus": psutil.cpu_count(),
            "physical_cpus": psutil.cpu_count(logical=False),
            "cpu_percent_mean": sum(sample[1] for sample in self.samples) / len(self.samples),
            "cpu_percent_max": max(sample[1] for sample in self.samples),
            "network_received_bytes": last[2] - first[2],
            "network_megabits_per_second_mean": (last[2] - first[2]) * 8 / 1e6 / seconds,
            "network_megabits_per_second_peak": max(
                (b[2] - a[2]) * 8 / 1e6 / max(b[0] - a[0], 1e-9) for a, b in steps
            ),
            "disk_written_bytes": last[3] - first[3],
            "disk_megabytes_per_second_mean": (last[3] - first[3]) / 1e6 / seconds,
            "disk_megabytes_per_second_peak": max(
                (b[3] - a[3]) / 1e6 / max(b[0] - a[0], 1e-9) for a, b in steps
            ),
            "peak_resident_bytes": max(sample[4] for sample in self.samples),
            "scope": "whole machine for CPU, network and disk; this process tree for memory",
        }


def rate(amount: float, seconds: float) -> float | None:
    """A rate, or None when the clock did not advance (tiny fixtures)."""
    return amount / seconds if seconds > 0 else None


def transfer_summary(transfers: list[TransferResult], wall: float) -> dict[str, Any]:
    """Aggregate transport metrics of one phase (response-body bytes; no TLS overhead)."""
    size = sum(t.identity.length for t in transfers)
    moved = sum(t.transferred_bytes for t in transfers)
    requests = sum(t.requests for t in transfers)
    rates = [t.identity.length / 1e6 / t.seconds for t in transfers if t.seconds > 0]
    return {
        "files": len(transfers),
        "file_bytes": size,
        "response_body_bytes": moved,
        "wall_seconds": wall,
        "megabytes_per_second": moved / 1e6 / wall if wall > 0 else None,
        "megabits_per_second": moved * 8 / 1e6 / wall if wall > 0 else None,
        "requests": requests,
        "requests_per_file": requests / len(transfers) if transfers else None,
        "requests_per_second": requests / wall if wall > 0 else None,
        "redirects": sum(t.redirects for t in transfers),
        "retries": sum(t.retries for t in transfers),
        "resumed_bytes": sum(t.resumed_bytes for t in transfers),
        "cache_hits": sum(1 for t in transfers if t.cache_hit),
        "per_file_megabytes_per_second_min_max": [min(rates), max(rates)] if rates else None,
        "sha256": {
            "independently_verified": sum(
                1 for t in transfers if t.identity.sha256_independently_verified
            ),
            "local_only": sum(1 for t in transfers if not t.identity.sha256_independently_verified),
        },
        "xet_hash_exposed": sum(1 for t in transfers if t.identity.xet_hash is not None),
    }


# ------------------------------------------------------------------- planning


def mint_plan(
    campaign: Campaign,
    files: list[str],
    limits: dict[str, Any],
    directory: Path,
    output: Path,
    authorization: str | None,
) -> AcquisitionPlan:
    """Create one whole-file plan through the real ``xlm data plan`` command."""
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app

    plan = campaign.config["plan"]
    limits_path = directory / f"{output.name.split('.')[0]}.limits.json"
    write_once(limits_path, limits)
    arguments = [
        "plan",
        "--source",
        plan["source_id"],
        "--view",
        plan["view_id"],
        "--catalog",
        str(campaign.repo / plan["catalog"]),
        "--files",
        ",".join(files),
        "--mode",
        plan["mode"],
        "--seed",
        str(plan["seed"]),
        "--limits",
        str(limits_path),
        "--output",
        str(output),
    ]
    if authorization is not None:
        arguments += ["--authorization-hash", authorization]
    result = CliRunner().invoke(app, arguments)
    if result.exit_code != 0:
        raise bulk.BulkError(f"xlm data plan refused the whole-file plan: {result.output}")
    minted = load_acquisition_plan(output)
    if (
        minted.revision != selector.SOURCE_REVISION
        or minted.mode.value != "whole_file"
        or minted.selected_files != files
        or not plan_requires_production_admission(minted)
    ):
        raise bulk.BulkError("whole-file plan is not the pinned production plan of these files")
    return minted


def batch_record(campaign: Campaign, batch: int) -> dict[str, Any]:
    record: dict[str, Any] = read_json(campaign.batch_dir(batch) / "batch.json")
    check_digest(record, "batch record")
    if record["campaign"] != campaign.config["digest"] or record["files"] != campaign.members(
        batch
    ):
        raise bulk.BulkError("batch record belongs to another campaign or membership")
    return record


def cmd_show(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    files = campaign.members(args.batch)
    print(f"campaign {campaign.config['digest']}")
    print(f"batch {args.batch}: {len(files)} files, membership {canonical.digest(files)}")
    for position, name in enumerate(files):
        print(f"  {campaign.rank(args.batch, position):5d}  {name}")
    print("planning is offline: no footer or layout read is needed before the run")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """OFFLINE: bind membership, revision and byte/disk ceilings; print the digest."""
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    directory = campaign.batch_dir(args.batch)
    files = campaign.members(args.batch)
    limits = fast.batch_limits(
        len(files),
        campaign.config["limits"],
        int(campaign.config["scratch"]["cap_bytes"]),
        int(campaign.config["concurrency"]["download_workers_max"]),
    ).model_dump()
    plan = mint_plan(campaign, files, limits, directory, directory / "batch.dry.plan.json", None)
    membership = canonical.digest(files)
    record = self_digest(
        {
            "campaign": campaign.config["digest"],
            "batch": args.batch,
            "files": files,
            "inventory_ranks": [campaign.rank(args.batch, i) for i in range(len(files))],
            "membership_digest": membership,
            "revision": plan.revision,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "selection_hash": plan.compute_selection_hash(),
            "limits": limits,
            "authorization_digest": fast.authorization_digest(
                campaign.config["digest"], args.batch, membership, plan.plan_hash, limits
            ),
        }
    )
    write_once(directory / "batch.json", record)
    print(f"batch {args.batch}: {len(files)} whole files, membership {membership}")
    print(f"per-file byte bound: {campaign.config['limits']['max_file_bytes']:,}")
    print(f"transfer ceiling: {limits['max_transferred_bytes']:,} bytes")
    print(f"durable output ceiling: {limits['max_output_disk_bytes']:,} bytes")
    print(f"scratch ceiling: {limits['max_temp_disk_bytes']:,} bytes")
    print(f"AUTHORIZATION DIGEST: {record['authorization_digest']}")
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    """Record the operator's authorization and mint the authorized plan."""
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    directory = campaign.batch_dir(args.batch)
    record = batch_record(campaign, args.batch)
    if args.digest != record["authorization_digest"]:
        raise bulk.BulkError("digest does not match this batch; nothing authorized")
    target = directory / "authorization.json"
    if not target.exists():
        write_json(
            target,
            {
                "authorization_digest": args.digest,
                "batch": args.batch,
                "operator": args.operator,
                "authorized_at": datetime.now(UTC).isoformat(),
            },
        )
    elif read_json(target)["authorization_digest"] != args.digest:
        raise bulk.BulkError("a different authorization is already recorded")
    plan = mint_plan(
        campaign,
        list(record["files"]),
        dict(record["limits"]),
        directory,
        directory / "batch.plan.json",
        record["plan_hash"],
    )
    if plan.plan_hash != record["plan_hash"]:
        raise bulk.BulkError("authorized plan differs from the reviewed dry plan")
    validate_plan_authorization(plan, catalog_source_approved=True)
    print(f"authorized batch {args.batch}: plan {plan.plan_hash}")
    return 0


# ----------------------------------------------------------------- accounting


def load_receipt(path: Path, campaign: Campaign) -> dict[str, Any]:
    receipt: dict[str, Any] = read_json(path)
    check_digest(receipt, f"unit receipt {path.parent.name}")
    if receipt["kind"] != fast.RECEIPT_KIND or receipt["campaign"] != campaign.config["digest"]:
        raise bulk.BulkError(f"receipt {path.parent.name} belongs to another campaign")
    return receipt


def campaign_state(campaign: Campaign) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cumulative yield and stop decision from the sealed unit receipts."""
    batches: dict[int, dict[str, Any]] = {}
    if campaign.plans.is_dir():
        for directory in sorted(campaign.plans.glob("b[0-9][0-9][0-9][0-9]")):
            if (directory / "batch.json").is_file():
                index = int(directory.name[1:])
                record = batch_record(campaign, index)
                batches[index] = {"files": record["files"], "plan_hash": record["plan_hash"]}
    canonical_root = campaign.root / str(campaign.config["roots"]["canonical"])
    receipts = [
        load_receipt(path, campaign)
        for path in sorted(canonical_root.glob(f"b*/f*/{local.RECEIPT_FILENAME}"))
    ]
    state = fast.cumulative(batches, receipts)
    return state, bulk.stop_decision(state, campaign.config["stop"]["targets"])


def benchmark_passed(campaign: Campaign) -> bool:
    path = campaign.plans / "benchmark" / "benchmark.json"
    if not path.is_file():
        return False
    report = read_json(path)
    return bool(
        report.get("verdict") == "PASS"
        and report.get("plan_digest") == campaign.config["benchmark"]["plan_digest"]
        and report.get("transport_code_sha256") == campaign.config["transport_code_sha256"]
    )


def evaluate_gate(
    campaign: Campaign, batch: int, top_up_reason: str, free_bytes: int | None
) -> dict[str, Any]:
    state, decision = campaign_state(campaign)
    if (campaign.batch_dir(batch) / "batch.json").is_file():
        cap = int(batch_record(campaign, batch)["limits"]["max_output_disk_bytes"])
    else:
        cap = int(campaign.config["batch"]["files"]) * int(
            campaign.config["limits"]["max_durable_bytes_per_file"]
        )
    campaign.root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(campaign.root).free if free_bytes is None else free_bytes
    old_ledger = campaign.root / "plans/ew-bulk/ledger"
    return fast.gate(
        campaign.config,
        state,
        decision,
        batch,
        free,
        cap,
        benchmark_passed=benchmark_passed(campaign),
        historical_ledger_entries=len(list(old_ledger.glob("*.json")))
        if old_ledger.is_dir()
        else 0,
        top_up_reason=top_up_reason,
    )


def cmd_gate(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    result = evaluate_gate(campaign, args.batch, args.top_up_reason, args.free_bytes)
    print(f"gate batch {args.batch}: {result['decision']} {'; '.join(result['reasons'])}")
    return {
        "RUN": 0,
        "STOP_TARGET_REACHED": EXIT_TARGET_REACHED,
        "COMPLETE": EXIT_COMPLETE,
    }.get(result["decision"], EXIT_REFUSED)


def cmd_account(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    return account(campaign)


def account(campaign: Campaign) -> int:
    state, decision = campaign_state(campaign)
    campaign.plans.mkdir(parents=True, exist_ok=True)
    report = historical_tool.repository_sufficiency(campaign, state)  # type: ignore[arg-type]
    for view in calibration.VIEWS:
        if report[view]["status"] != decision["views"][view]["status"]:
            raise bulk.BulkError(
                f"{view}: stop decision disagrees with mix01_inventory sufficiency"
            )
    write_json(campaign.plans / "cumulative.json", state)
    write_json(
        campaign.plans / "campaign-status.json",
        {"campaign": campaign.config["digest"], "decision": decision},
    )
    counted, sealed = state["counted"], state["sealed_including_incomplete_batch"]
    print(
        f"complete batches: {state['complete_batches']} rows: {counted['rows']:,} "
        f"sealed files: {sealed['files']} durable footprint: {sealed['footprint_bytes']:,}"
    )
    for view in calibration.VIEWS:
        entry = decision["views"][view]
        print(
            f"  {view}: {entry['estimated_tokens']:,.0f} estimated tokens, "
            f"{entry['acquired_canonical_bytes']:,} / {entry['required_canonical_bytes']:,} "
            f"canonical bytes -> {entry['status']}"
        )
    print(f"target reached: {decision['target_reached']}; next: {decision['next']}")
    return EXIT_TARGET_REACHED if decision["target_reached"] else 0


def cmd_status(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    if (campaign.batch_dir(args.batch) / "batch.json").is_file():
        record = batch_record(campaign, args.batch)
        authorized = (campaign.batch_dir(args.batch) / "batch.plan.json").is_file()
        print(f"batch {args.batch}: planned, authorized={authorized}")
        for position, name in enumerate(record["files"]):
            rank = campaign.rank(args.batch, position)
            sealed = (campaign.unit_dir(args.batch, rank) / local.RECEIPT_FILENAME).is_file()
            raw = identity_path(campaign.raw_path(name)).is_file()
            print(f"  f{rank:05d}: raw_retained={raw} sealed={sealed}  {name}")
    else:
        print(f"batch {args.batch}: not planned")
    return account(campaign)


# ------------------------------------------------------------------------ run


def unit_job(campaign: Campaign, plan: AcquisitionPlan, name: str, staging: Path) -> dict[str, Any]:
    return {
        "source_file": name,
        "staging_dir": str(staging),
        "views": list(campaign.config["views"]),
        "source_id": plan.source_id,
        "repository": plan.repository,
        "revision": plan.revision,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "selection_hash": plan.compute_selection_hash(),
        "limits": campaign.process_limits(),
        "row_range": None,
    }


def seal_unit(
    campaign: Campaign,
    plan: AcquisitionPlan,
    batch: int,
    rank: int,
    unit: local.Unit,
    transfer: TransferResult | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Cross-check one adapted unit, write its receipt and publish it atomically."""
    staging = Path(str(result["staging_dir"]))
    limit = int(campaign.config["limits"]["max_record_bytes"]) + 65536
    for view in calibration.VIEWS:
        documents = staging / view / "documents.jsonl"
        worker = result["views"][view]
        if mix01_inventory.scan_canonical(documents, require_text=True) != (
            worker["documents"],
            worker["canonical_bytes"],
            worker["documents_sha256"],
        ):
            raise bulk.BulkError(f"{unit.source_file}/{view}: canonical output does not reconcile")
        historical_tool.check_documents(documents, view, limit)
    durable = campaign.raw_path(unit.source_file)
    source = read_json(identity_path(durable))
    if durable.stat().st_size != int(source["length"]):
        raise bulk.BulkError(f"{unit.source_file}: retained source has the wrong size")
    if transfer is None:
        metrics: dict[str, Any] = {
            "transferred_bytes": 0,
            "requests": 0,
            "accounting": "source reused from the durable store; its transfer belongs to an "
            "earlier interrupted run whose scratch state no longer exists",
        }
    else:
        metrics = {
            key: value for key, value in asdict(transfer).items() if key not in ("identity", "path")
        }
    if plan.authorization is None:
        raise bulk.BulkError("executed plan carries no authorization")
    receipt = fast.unit_receipt(
        campaign_digest=campaign.config["digest"],
        batch=batch,
        rank=rank,
        plan={
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "selection_hash": plan.compute_selection_hash(),
            "authorization": plan.authorization.model_dump(),
        },
        source=source,
        raw_path=durable.relative_to(campaign.root).as_posix(),
        result=result,
        transfer=metrics,
        bookkeeping_bytes=sum(
            (staging / view / "adaptation_summary.json").stat().st_size
            for view in calibration.VIEWS
        )
        + identity_path(durable).stat().st_size,
        sealed_at=datetime.now(UTC).isoformat(),
    )
    write_json(staging / local.RECEIPT_FILENAME, receipt)
    final = campaign.unit_dir(batch, rank)
    if final.exists():
        raise bulk.BulkError(f"unit {final.name} already exists; refusing overwrite")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.rename(staging, final)
    try:
        staging.parent.rmdir()
    except OSError:
        pass
    # The scratch copy is released only now: the durable raw copy reproduced the
    # verified hash and the canonical unit with its receipt is published.
    unit.partial.unlink(missing_ok=True)
    unit.state.unlink(missing_ok=True)
    return receipt


def cmd_run(args: argparse.Namespace) -> int:
    """NETWORK: execute one authorized batch; resumes without redoing sealed units."""
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    gate = evaluate_gate(campaign, args.batch, args.top_up_reason, None)
    print(f"gate batch {args.batch}: {gate['decision']} {'; '.join(gate['reasons'])}")
    if gate["decision"] == "STOP_TARGET_REACHED":
        return EXIT_TARGET_REACHED
    if gate["decision"] == "COMPLETE":
        return EXIT_COMPLETE
    if gate["decision"] != "RUN":
        return EXIT_REFUSED
    record = batch_record(campaign, args.batch)
    directory = campaign.batch_dir(args.batch)
    if not (directory / "batch.plan.json").is_file():
        raise bulk.BulkError("batch is not authorized: run authorize with the printed digest")
    plan = load_acquisition_plan(directory / "batch.plan.json")
    if plan.plan_hash != record["plan_hash"] or plan.selected_files != record["files"]:
        raise bulk.BulkError("authorized plan is not the planned batch")
    check_admission(plan)
    validate_plan_authorization(plan, catalog_source_approved=True)
    concurrency = campaign.config["concurrency"]
    downloads = args.workers or int(concurrency["download_workers"])
    processes = (
        int(concurrency["process_workers"])
        if args.process_workers is None
        else args.process_workers
    )
    if downloads > plan.limits.max_workers or processes > int(concurrency["process_workers_max"]):
        raise bulk.BulkError("requested workers exceed the authorized ceiling")
    if args.top_up_reason.strip():
        write_once(
            directory / "top-up.json", {"batch": args.batch, "reason": args.top_up_reason.strip()}
        )
    scratch_dir = campaign.scratch(f"b{args.batch:04d}")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(campaign.plans / "run.lock"), timeout=1):
            return execute_batch(campaign, plan, record, scratch_dir, downloads, processes)
    except Timeout as exc:
        raise bulk.BulkError("another run of this campaign is active") from exc


def execute_batch(
    campaign: Campaign,
    plan: AcquisitionPlan,
    record: dict[str, Any],
    scratch_dir: Path,
    downloads: int,
    processes: int,
) -> int:
    batch = int(record["batch"])
    staging = campaign.staging(batch)
    # Private staging of an interrupted run is never evidence: no receipt names it.
    shutil.rmtree(staging, ignore_errors=True)
    units: list[local.Unit] = []
    ranks: dict[str, int] = {}
    charged = 0
    for position, name in enumerate(record["files"]):
        rank = campaign.rank(batch, position)
        key = f"f{rank:05d}"
        partial, state = scratch_dir / f"{key}.parquet.part", scratch_dir / f"{key}.state.json"
        final = campaign.unit_dir(batch, rank)
        if (final / local.RECEIPT_FILENAME).is_file():
            charged += int(
                load_receipt(final / local.RECEIPT_FILENAME, campaign)["transferred_bytes"]
            )
            partial.unlink(missing_ok=True)
            state.unlink(missing_ok=True)
            continue
        if final.exists():
            raise bulk.BulkError(f"unit {key} exists without a receipt; review before rerunning")
        ranks[key] = rank
        job = unit_job(campaign, plan, name, staging / key)
        durable = campaign.raw_path(name)
        if state.is_file():
            charged += int(read_json(state).get("charged_bytes", 0))
        retained = None if state.is_file() else load_durable_source(durable)
        if retained is not None:
            units.append(
                local.Unit(
                    key=key,
                    source_file=name,
                    url=None,
                    partial=partial,
                    state=state,
                    job={**job, "source_path": str(durable), "durable_path": None},
                    identity_record=retained,
                )
            )
        else:
            units.append(
                local.Unit(
                    key=key,
                    source_file=name,
                    url=source_url(plan, name),
                    partial=partial,
                    state=state,
                    job={**job, "source_path": str(partial), "durable_path": str(durable)},
                    expected_sha256=plan.expected_file_digests.get(name),
                )
            )
    limits = campaign.transfer_limits()
    scratch = ScratchBudget(
        campaign.scratch(),
        int(campaign.config["scratch"]["cap_bytes"]),
        int(campaign.config["scratch"]["min_free_bytes"]),
    )
    meter = TransferMeter(
        plan.limits.max_transferred_bytes, plan.limits.max_requests, bytes_used=charged
    )
    transfers: list[TransferResult] = []
    sealed: list[dict[str, Any]] = []

    def on_done(
        unit: local.Unit, transfer: TransferResult | None, result: dict[str, Any] | None
    ) -> None:
        if result is None:
            raise bulk.BulkError("campaign unit finished without local processing")
        if transfer is not None:
            transfers.append(transfer)
        receipt = seal_unit(campaign, plan, batch, ranks[unit.key], unit, transfer, result)
        sealed.append(receipt)
        print(
            f"sealed {unit.key} rows={receipt['rows']:,} "
            f"science/practical/prose="
            f"{'/'.join(str(receipt['views'][v]['documents']) for v in calibration.VIEWS)} "
            f"{unit.source_file}",
            flush=True,
        )

    print(
        f"batch {batch}: {len(units)} unit(s) to do, {len(record['files']) - len(units)} sealed; "
        f"download workers {downloads}, process workers {processes}",
        flush=True,
    )
    sampler = Sampler()
    sampler.start()
    stats: dict[str, Any] = {}
    try:
        if units:
            stats = local.run_pipeline(
                units,
                limits=limits,
                revision=plan.revision,
                download_workers=downloads,
                process_workers=processes,
                scratch=scratch,
                meter=meter,
                deadline_seconds=plan.limits.overall_deadline_seconds,
                identity_for=lambda unit, transfer: identity_record(
                    transfer.identity,
                    source_file=unit.source_file,
                    repository=plan.repository,
                    revision=plan.revision,
                ),
                on_done=on_done,
            )
    finally:
        system = sampler.stop()
        wall = float(stats.get("wall_seconds", system.get("seconds", 0.0)) or 0.0)
        rows = sum(int(receipt["rows"]) for receipt in sealed)
        report = {
            "campaign": campaign.config["digest"],
            "batch": batch,
            "completed": bool(stats),
            "download_workers": downloads,
            "process_workers": processes,
            "units_sealed_this_run": len(sealed),
            "rows": rows,
            "rows_per_second": rows / wall if wall > 0 else None,
            "pipeline": stats,
            "transfer": transfer_summary(transfers, wall) if transfers else None,
            "processing_cpu_seconds": sum(
                float(receipt["processing"]["cpu_seconds"]) for receipt in sealed
            ),
            "system": system,
            "finished_at": datetime.now(UTC).isoformat(),
        }
        index = len(list(campaign.batch_dir(batch).glob("performance-*.json")))
        write_json(campaign.batch_dir(batch) / f"performance-{index:02d}.json", report)
    shutil.rmtree(staging, ignore_errors=True)
    if report["transfer"] is not None:
        transfer = report["transfer"]
        print(
            f"transfer: {transfer['response_body_bytes']:,} bytes in {wall:.1f}s "
            f"({transfer['megabytes_per_second'] or 0:.1f} MB/s, {transfer['requests']} requests, "
            f"{transfer['retries']} retries)"
        )
    return account(campaign)


# ------------------------------------------------------------------ benchmark


def cmd_benchmark(args: argparse.Namespace) -> int:
    """NETWORK: small disjoint-file transport benchmark and real-byte parity check.

    Retains nothing but the metrics report and never writes a campaign receipt.
    """
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    bench = read_json(args.plan)
    check_digest(bench, "benchmark plan")
    if bench["digest"] != campaign.config["benchmark"]["plan_digest"]:
        raise bulk.BulkError("benchmark plan is not the one frozen in the campaign")
    if args.authorize != bench["digest"]:
        raise bulk.BulkError("benchmark needs --authorize with its frozen plan digest")
    batch_files = campaign.members(0)
    names = [name for tier in bench["tiers"] for name in tier["files"]]
    if names != batch_files[: len(names)]:
        raise bulk.BulkError("benchmark files are not the frozen prefix of batch 0")
    parity = bench["parity"]
    directory = campaign.plans / "benchmark"
    directory.mkdir(parents=True, exist_ok=True)
    limits = fast.batch_limits(
        len(names) + 1, campaign.config["limits"], int(campaign.config["scratch"]["cap_bytes"]), 16
    ).model_copy(
        update={
            "max_transferred_bytes": int(bench["limits"]["max_transferred_bytes"]),
            "max_requests": int(bench["limits"]["max_requests"]),
            "overall_deadline_seconds": float(bench["limits"]["deadline_seconds"]),
        }
    )
    limits_dump = limits.model_dump()
    all_files = [*names, parity["file"]]
    dry = mint_plan(
        campaign, all_files, limits_dump, directory, directory / "benchmark.dry.plan.json", None
    )
    plan = mint_plan(
        campaign,
        all_files,
        limits_dump,
        directory,
        directory / "benchmark.plan.json",
        dry.plan_hash,
    )
    check_admission(plan)
    validate_plan_authorization(plan, catalog_source_approved=True)
    work = campaign.scratch("benchmark", bench["digest"][:16])
    if work.exists():
        raise bulk.BulkError(f"benchmark scratch '{work}' already exists; remove it and rerun")
    work.mkdir(parents=True)
    scratch = ScratchBudget(
        campaign.scratch(),
        int(campaign.config["scratch"]["cap_bytes"]),
        int(campaign.config["scratch"]["min_free_bytes"]),
    )
    meter = TransferMeter(limits.max_transferred_bytes, limits.max_requests)
    transfer_limits = campaign.transfer_limits()
    reasons: list[str] = []
    phases: list[dict[str, Any]] = []
    downloaded: dict[str, TransferResult] = {}

    def unit_for(index: int, name: str, job: dict[str, Any] | None) -> local.Unit:
        return local.Unit(
            key=f"x{index:02d}",
            source_file=name,
            url=source_url(plan, name),
            partial=work / f"x{index:02d}.parquet.part",
            state=work / f"x{index:02d}.state.json",
            job=job,
        )

    def identity_for(unit: local.Unit, transfer: TransferResult) -> dict[str, Any]:
        return identity_record(
            transfer.identity,
            source_file=unit.source_file,
            repository=plan.repository,
            revision=plan.revision,
        )

    def keep(unit: local.Unit, transfer: TransferResult | None, _: Any) -> None:
        if transfer is not None:
            downloaded[unit.source_file] = transfer

    try:
        cursor = 0
        for tier in bench["tiers"]:
            units = [unit_for(cursor + i, name, None) for i, name in enumerate(tier["files"])]
            cursor += len(units)
            sampler = Sampler()
            sampler.start()
            try:
                stats = local.run_pipeline(
                    units,
                    limits=transfer_limits,
                    revision=plan.revision,
                    download_workers=int(tier["download_workers"]),
                    process_workers=0,
                    scratch=scratch,
                    meter=meter,
                    deadline_seconds=limits.overall_deadline_seconds,
                    identity_for=identity_for,
                    on_done=keep,
                    max_in_flight=len(names) + 1,
                )
            finally:
                system = sampler.stop()
            got = [downloaded[name] for name in tier["files"]]
            summary = transfer_summary(got, float(stats["wall_seconds"]))
            phases.append(
                {
                    "phase": "download",
                    "download_workers": tier["download_workers"],
                    "transfer": summary,
                    "system": system,
                }
            )
            print(
                f"workers={tier['download_workers']}: {summary['file_bytes']:,} bytes in "
                f"{summary['wall_seconds']:.1f}s = {summary['megabytes_per_second'] or 0:.1f} MB/s "
                f"({summary['megabits_per_second'] or 0:.0f} Mbit/s), "
                f"{summary['requests']} requests, {summary['retries']} retries",
                flush=True,
            )
        # Local processing of the downloaded files, exactly as a campaign unit
        # would be processed, but into scratch and without any durable copy.
        processes = int(campaign.config["concurrency"]["process_workers"])
        results: list[dict[str, Any]] = []
        jobs = []
        for index, name in enumerate(names):
            transfer = downloaded[name]
            jobs.append(
                {
                    **unit_job(campaign, plan, name, work / "out" / f"x{index:02d}"),
                    "source_path": str(transfer.path),
                    "durable_path": None,
                    "identity_record": identity_record(
                        transfer.identity,
                        source_file=name,
                        repository=plan.repository,
                        revision=plan.revision,
                    ),
                    "key": f"x{index:02d}",
                }
            )
        sampler = Sampler()
        sampler.start()
        started = time.monotonic()
        try:
            results = parallel_process(jobs, processes)
        finally:
            system = sampler.stop()
        wall = time.monotonic() - started
        rows = sum(int(result["rows"]) for result in results)
        for result in results:
            bulk.check_conservation(int(result["rows"]), result["views"])
        phases.append(
            {
                "phase": "local_processing",
                "process_workers": processes,
                "files": len(results),
                "rows": rows,
                "wall_seconds": wall,
                "rows_per_second": rate(rows, wall),
                "cpu_seconds": sum(float(r["process_cpu_seconds"]) for r in results),
                "rows_per_core_second": rate(
                    rows, sum(float(r["process_cpu_seconds"]) for r in results)
                ),
                "documents": {
                    view: sum(int(r["views"][view]["documents"]) for r in results)
                    for view in calibration.VIEWS
                },
                "canonical_bytes": {
                    view: sum(int(r["views"][view]["canonical_bytes"]) for r in results)
                    for view in calibration.VIEWS
                },
                "source_bytes": sum(downloaded[name].identity.length for name in names),
                "selected_records_bytes_not_stored": sum(
                    int(r["selected_records_bytes"]) for r in results
                ),
                "ledger_uncompressed_bytes": sum(
                    int(v["rejections_uncompressed_bytes"])
                    for r in results
                    for v in r["views"].values()
                ),
                "ledger_compressed_bytes": sum(
                    int(v["rejections_file_bytes"]) for r in results for v in r["views"].values()
                ),
                "documents_file_bytes": sum(
                    int(v["documents_file_bytes"]) for r in results for v in r["views"].values()
                ),
                "system": system,
                "note": "measured on real files; never counted as campaign progress",
            }
        )
        print(
            f"local processing: {rows:,} rows in {wall:.1f}s = {rate(rows, wall) or 0:,.0f} rows/s "
            f"with {processes} processes",
            flush=True,
        )
        # Durable-volume copy rate of one file (written to a temporary name, then removed).
        sample = downloaded[names[0]]
        probe = directory / f"copy-probe-{uuid.uuid4().hex}.tmp"
        started = time.monotonic()
        try:
            with sample.path.open("rb") as reader, probe.open("xb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            seconds = time.monotonic() - started
        finally:
            probe.unlink(missing_ok=True)
        phases.append(
            {
                "phase": "durable_copy",
                "bytes": sample.identity.length,
                "seconds": seconds,
                "megabytes_per_second": rate(sample.identity.length / 1e6, seconds),
            }
        )
        # Parity: one calibration file, whole, through the new path, against the seal.
        parity_unit = unit_for(len(names), parity["file"], None)
        holder: list[TransferResult] = []
        local.run_pipeline(
            [parity_unit],
            limits=transfer_limits,
            revision=plan.revision,
            download_workers=1,
            process_workers=0,
            scratch=scratch,
            meter=meter,
            deadline_seconds=limits.overall_deadline_seconds,
            identity_for=identity_for,
            on_done=lambda unit, transfer, _: holder.append(transfer) if transfer else None,
            max_in_flight=len(names) + 1,
        )
        parity_transfer = holder[0]
        downloaded[parity["file"]] = parity_transfer
        parity_result = local.adapt_source_file(
            parity_transfer.path,
            work / "out" / "parity",
            source_file=parity["file"],
            views=list(campaign.config["views"]),
            source_id=plan.source_id,
            repository=plan.repository,
            revision=plan.revision,
            plan_id=parity["plan_id"],
            plan_hash=parity["plan_hash"],
            selection_hash=parity["selection_hash"],
            identity=asdict(parity_transfer.identity),
            limits=campaign.process_limits(),
            row_range=(int(parity["row_range"][0]), int(parity["row_range"][1])),
        )
        checks = {
            "etag_equals_sealed_validator": parity_transfer.identity.etag == parity["etag"],
            "length_equals_sealed_validator": parity_transfer.identity.length == parity["length"],
            "selected_records_sha256_equals_sealed_raw": parity_result["selected_records_sha256"]
            == parity["raw_sha256"],
            "selected_records_bytes_equal": parity_result["selected_records_bytes"]
            == parity["raw_bytes"],
        }
        for view, sealed in parity["views"].items():
            got_view = parity_result["views"][view]
            checks[f"{view}_documents_identical"] = (
                got_view["documents_sha256"] == sealed["documents_sha256"]
            )
            checks[f"{view}_rejections_identical"] = (
                got_view["rejections_sha256"] == sealed["rejections_sha256"]
            )
        phases.append(
            {
                "phase": "parity",
                "file": parity["file"],
                "rows": parity_result["rows"],
                "checks": checks,
                "meaning": "the whole real upstream file, processed locally, reproduces the "
                "sealed certified calibration raw records and canonical outputs byte for byte",
            }
        )
        reasons += [f"parity: {name}" for name, ok in checks.items() if not ok]
        print(f"parity: {'IDENTICAL' if all(checks.values()) else 'DIFFERENT'}", flush=True)
    except Exception as exc:
        reasons.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Only this benchmark's own directory is removed; nothing is retained.
        shutil.rmtree(work, ignore_errors=True)
    if len(downloaded) != len(all_files):
        reasons.append("not every benchmark file was transferred")
    report = {
        "kind": "essential_web_fast_benchmark_report",
        "plan_digest": bench["digest"],
        "campaign": campaign.config["digest"],
        "transport_code_sha256": campaign.config["transport_code_sha256"],
        "verdict": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
        "phases": phases,
        "files": {
            name: {**asdict(t.identity), "seconds": t.seconds, "requests": t.requests}
            for name, t in downloaded.items()
        },
        "total_response_body_bytes": meter.bytes,
        "total_requests": meter.requests,
        "scratch_removed": not work.exists(),
        "counts_as_campaign_progress": False,
        "finished_at": datetime.now(UTC).isoformat(),
        "evidence_class": "REAL live transport measurement",
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    write_json(directory / f"benchmark-{stamp}.json", report)
    write_json(directory / "benchmark.json", report)
    print(f"benchmark {report['verdict']}: {directory / 'benchmark.json'}")
    for reason in reasons:
        print(f"  {reason}")
    return 0 if not reasons else EXIT_REFUSED


def parallel_process(jobs: list[dict[str, Any]], workers: int) -> list[dict[str, Any]]:
    """Process local files in ``workers`` processes (inline when zero)."""
    if workers == 0:
        return [local.process_unit(job) for job in jobs]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(local.process_unit, jobs))


def cmd_benchmark_accept(args: argparse.Namespace) -> int:
    """OFFLINE: re-apply the corrected identity rule to the saved live benchmark."""
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    bench = read_json(args.plan)
    check_digest(bench, "benchmark plan")
    if bench["digest"] != campaign.config["benchmark"]["plan_digest"]:
        raise bulk.BulkError("benchmark plan is not the one frozen in the campaign")
    directory = campaign.plans / "benchmark"
    source = args.report or directory / "benchmark.json"
    report = read_json(source)
    if "revalidation" in report:
        raise bulk.BulkError("report is already a re-accepted one; pass the original report")
    accepted = fast.reaccept_benchmark(
        report,
        sealer.file_sha256(source),
        bench,
        selector.SOURCE_REVISION,
        campaign.config["transport_code_sha256"],
    )
    accepted["campaign"] = campaign.config["digest"]
    accepted["revalidated_at"] = datetime.now(UTC).isoformat()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    write_json(directory / f"benchmark-accepted-{stamp}.json", accepted)
    write_json(directory / "benchmark.json", accepted)
    if args.evidence_dir is not None:
        write_json(args.evidence_dir / "live-benchmark-accepted.json", accepted)
    print(
        f"benchmark {accepted['verdict']} (offline re-acceptance): {directory / 'benchmark.json'}"
    )
    for reason in accepted["reasons"]:
        print(f"  {reason}")
    return 0 if accepted["verdict"] == "PASS" else EXIT_REFUSED


# ------------------------------------------------------- offline measurements


def calibration_tables(
    calibration_root: Path, footer_root: Path, seal: dict[str, Any]
) -> list[tuple[dict[str, Any], pa.Table]]:
    """Real calibration rows under the real upstream schema of each file.

    The container is rebuilt locally from the executed raw records (the
    calibration never transferred a whole file); unprojected columns are null.
    """
    raw = (footer_root / "m_phase_p_layout.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != historical_tool.FOOTER_LAYOUT_SHA256:
        raise bulk.BulkError("footer evidence differs from the reviewed Phase-P parent")
    layout = json.loads(raw)
    columns = columns_for(bulk.ADAPTER_ID, bulk.PLAN_VIEW)
    tables = []
    for index, unit in enumerate(seal["units"]):
        entry = layout["files"][index]
        footer = (
            footer_root / entry["payloads"][f"M-{index:02d}-footer"]["retained_file"]
        ).read_bytes()
        if entry["file"] != unit["file"]:
            raise bulk.BulkError("footer and calibration unit describe different files")
        schema = pq.ParquetFile(pa.BufferReader(b"PAR1" + footer)).schema_arrow.remove_metadata()
        source = calibration_root / unit["unit"] / "raw/selected_records.jsonl"
        if sealer.file_sha256(source) != unit["raw_sha256"]:
            raise bulk.BulkError(f"{unit['unit']}: raw records differ from the seal")
        rows = []
        with source.open("rb") as stream:
            for line in stream:
                record = json.loads(line)
                record.pop("_xlm_acquisition")
                rows.append(record)
        arrays = [
            pa.array([row[field.name] for row in rows], type=field.type)
            if field.name in columns
            else pa.nulls(len(rows), type=field.type)
            for field in schema
        ]
        tables.append((unit, pa.Table.from_arrays(arrays, schema=schema)))
    return tables


def offline_limits() -> dict[str, Any]:
    policy = read_json(REPO / HISTORICAL_CAMPAIGN)["limits"]
    physical = read_json(REPO / historical_tool.BULK_DIR / "physical-cost-model.json")
    limits = fast.limits_policy(policy, physical)
    return {
        key: limits[key]
        for key in (
            "max_decompression_ratio",
            "max_parser_bytes",
            "max_rows_per_file",
            "max_record_bytes",
            "max_decoded_bytes_per_file",
            "max_ledger_bytes",
        )
    }


def cmd_replay(args: argparse.Namespace) -> int:
    """OFFLINE: prove local-Parquet processing reproduces the sealed calibration."""
    seal = read_json(REPO / historical_tool.SEAL_DIR / "calibration-seal.json")
    if args.scratch_root is None:
        raise bulk.BulkError("set XLM_SCRATCH_ROOT or --scratch-root")
    work = args.scratch_root / "ew-fast" / f"replay-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    limits = offline_limits()
    units: list[dict[str, Any]] = []
    totals = {"rows": 0, "ledger_uncompressed": 0, "ledger_compressed": 0, "cpu": 0.0, "summary": 0}
    try:
        for index, (unit, table) in enumerate(
            calibration_tables(args.calibration_root, args.footer_root, seal)
        ):
            plan = load_acquisition_plan(args.calibration_root / f"{unit['unit']}.plan.json")
            identity = {
                "etag": unit["source_validator"]["etag"],
                "length": unit["source_validator"]["length"],
                "sha256": "not-transferred",
            }
            outcome: dict[str, Any] = {"unit": unit["unit"], "file": unit["file"]}
            for label, group_rows in (("one_row_group", table.num_rows), ("three_row_groups", 700)):
                path = work / f"u{index:02d}-{label}.parquet"
                pq.write_table(table, path, row_group_size=group_rows, compression="snappy")
                result = local.adapt_source_file(
                    path,
                    work / f"out{index:02d}-{label}",
                    source_file=unit["file"],
                    views=list(unit["views"]),
                    source_id=plan.source_id,
                    repository=plan.repository,
                    revision=plan.revision,
                    plan_id=plan.plan_id,
                    plan_hash=plan.plan_hash,
                    selection_hash=unit["selection_hash"],
                    identity=identity,
                    limits=limits,
                )
                views = {
                    view: (
                        result["views"][view]["documents_sha256"] == sealed["documents_sha256"]
                        and result["views"][view]["rejections_sha256"]
                        == sealed["rejections_sha256"]
                        and result["views"][view]["rejection_counts_by_code"]
                        == sealed["rejection_counts_by_code"]
                        and result["views"][view]["documents"] == sealed["accepted_records"]
                        and result["views"][view]["canonical_bytes"] == sealed["canonical_bytes"]
                    )
                    for view, sealed in unit["views"].items()
                }
                outcome[label] = {
                    "row_groups": result["row_groups"],
                    "canonical_and_ledger_identical_per_view": views,
                }
                if label == "one_row_group":
                    outcome["rows"] = result["rows"]
                    outcome["selected_records_sha256"] = result["selected_records_sha256"]
                    outcome["sealed_raw_sha256"] = unit["raw_sha256"]
                    outcome["selected_records_identical"] = (
                        result["selected_records_sha256"] == unit["raw_sha256"]
                        and result["selected_records_bytes"] == unit["raw_bytes"]
                    )
                    totals["rows"] += int(result["rows"])
                    totals["cpu"] += float(result["process_cpu_seconds"])
                    for view in result["views"].values():
                        totals["ledger_uncompressed"] += int(view["rejections_uncompressed_bytes"])
                        totals["ledger_compressed"] += int(view["rejections_file_bytes"])
                    totals["summary"] += sum(
                        (work / f"out{index:02d}-{label}" / view / "adaptation_summary.json")
                        .stat()
                        .st_size
                        for view in unit["views"]
                    )
            units.append(outcome)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    identical = all(
        unit["selected_records_identical"]
        and all(unit["one_row_group"]["canonical_and_ledger_identical_per_view"].values())
        and all(unit["three_row_groups"]["canonical_and_ledger_identical_per_view"].values())
        for unit in units
    )
    sealed_ledger = int(seal["totals"]["rejections_file_bytes"])
    write_json(
        args.output,
        {
            "kind": "essential_web_fast_local_replay",
            "evidence_class": "REAL calibration rows, offline; the Parquet container is rebuilt "
            "locally under the real upstream schema because the calibration never transferred a "
            "whole file. Parity on real upstream file bytes is the live benchmark's parity phase.",
            "seal_digest": seal["seal_digest"],
            "transport_code_sha256": fast.transport_code_identity(CODE_REPO),
            "units": units,
            "rows": totals["rows"],
            "all_identical": identical,
            "ledger": {
                "codec": local.LEDGER_CODEC,
                "level": local.LEDGER_LEVEL,
                "files": 3 * len(units),
                "uncompressed_bytes": totals["ledger_uncompressed"],
                "uncompressed_equals_sealed_ledger_bytes": totals["ledger_uncompressed"]
                == sealed_ledger,
                "compressed_bytes": totals["ledger_compressed"],
                "ratio": totals["ledger_uncompressed"] / totals["ledger_compressed"],
                "basis": "the 24 real calibration ledgers, each compressed on its own",
            },
            "summary_bytes_per_unit": totals["summary"] / len(units),
            "contains_document_text": False,
        },
    )
    print(f"replay {'IDENTICAL' if identical else 'DIFFERENT'}: {args.output}")
    return 0 if identical else EXIT_REFUSED


def cmd_bench_local(args: argparse.Namespace) -> int:
    """OFFLINE: local processing rate by process count, on calibration rows."""
    seal = read_json(REPO / historical_tool.SEAL_DIR / "calibration-seal.json")
    if args.scratch_root is None:
        raise bulk.BulkError("set XLM_SCRATCH_ROOT or --scratch-root")
    work = args.scratch_root / "ew-fast" / f"bench-local-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    limits = offline_limits()
    runs: dict[str, Any] = {}
    try:
        jobs = []
        for index, (unit, table) in enumerate(
            calibration_tables(args.calibration_root, args.footer_root, seal)
        ):
            plan = load_acquisition_plan(args.calibration_root / f"{unit['unit']}.plan.json")
            for copy in range(args.copies):
                path = work / f"u{index:02d}-{copy}.parquet"
                pq.write_table(
                    pa.concat_tables([table] * args.repeat),
                    path,
                    row_group_size=10_000,
                    compression="snappy",
                )
                jobs.append(
                    {
                        "source_path": str(path),
                        "durable_path": None,
                        "staging_dir": "",
                        "source_file": unit["file"],
                        "views": list(unit["views"]),
                        "source_id": plan.source_id,
                        "repository": plan.repository,
                        "revision": plan.revision,
                        "plan_id": plan.plan_id,
                        "plan_hash": plan.plan_hash,
                        "selection_hash": unit["selection_hash"],
                        "identity_record": {
                            "etag": unit["source_validator"]["etag"],
                            "length": path.stat().st_size,
                            "sha256": "not-transferred",
                        },
                        "limits": limits,
                        "row_range": None,
                    }
                )
        for workers in args.workers:
            out = work / f"out-{workers}"
            batch = [{**job, "staging_dir": str(out / f"j{i:02d}")} for i, job in enumerate(jobs)]
            sampler = Sampler()
            sampler.start()
            started = time.monotonic()
            try:
                results = parallel_process(batch, workers)
            finally:
                system = sampler.stop()
            wall = time.monotonic() - started
            rows = sum(int(result["rows"]) for result in results)
            cpu = sum(float(result["process_cpu_seconds"]) for result in results)
            runs[str(workers)] = {
                "process_workers": workers,
                "files": len(results),
                "rows": rows,
                "wall_seconds": wall,
                "rows_per_second": rate(rows, wall),
                "cpu_seconds": cpu,
                "rows_per_core_second": rate(rows, cpu),
                "cpu_percent_mean": system.get("cpu_percent_mean"),
                "peak_resident_bytes": system.get("peak_resident_bytes"),
            }
            shutil.rmtree(out, ignore_errors=True)
            print(
                f"workers={workers}: {rate(rows, wall) or 0:,.0f} rows/s ({wall:.1f}s)", flush=True
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_json(
        args.output,
        {
            "kind": "essential_web_fast_local_benchmark",
            "evidence_class": "measured on this machine, offline, on real calibration rows "
            f"repeated {args.repeat} times per file; repeats are for timing only and the rate "
            "on full upstream files is measured by the live benchmark",
            "machine": {
                "logical_cpus": psutil.cpu_count(),
                "physical_cpus": psutil.cpu_count(logical=False),
                "pyarrow": pa.__version__,
                "python": sys.version.split()[0],
            },
            "files": len(jobs),
            "rows_per_file": args.repeat * 2048,
            "includes_process_start": True,
            "runs": runs,
        },
    )
    print(f"local benchmark written: {args.output}")
    return 0


# ---------------------------------------------------------------------- model


#: Logical CPUs left to the download streams, hashing, sealing and the system.
RESERVED_LOGICAL_CPUS = 4


def choose_process_workers(
    runs: dict[str, Any], logical_cpus: int, minimum_gain: float = 0.10
) -> int:
    """Largest measured process count that still adds ``minimum_gain`` over the previous.

    Never more than the logical CPUs minus the reserve, so the CPU-bound
    processes do not starve the streams they are pipelined with.
    """
    ceiling = max(1, logical_cpus - RESERVED_LOGICAL_CPUS)
    ordered = sorted((int(key), float(value["rows_per_second"])) for key, value in runs.items())
    chosen, best = ordered[0]
    for workers, measured in ordered[1:]:
        if workers > ceiling or measured < best * (1 + minimum_gain):
            break
        chosen, best = workers, measured
    return chosen


def cmd_model(args: argparse.Namespace) -> int:
    """OFFLINE: build the transport, storage and throughput models; freeze the campaign."""
    out: Path = args.output_dir
    old_dir = REPO / historical_tool.BULK_DIR
    historical = read_json(REPO / HISTORICAL_CAMPAIGN)
    bulk.check_campaign(historical)
    physical = read_json(old_dir / "physical-cost-model.json")
    old_disk = read_json(old_dir / "disk-budget.json")
    old_batch = read_json(old_dir / "batch-policy.json")
    seal = read_json(REPO / historical_tool.SEAL_DIR / "calibration-seal.json")
    if seal["seal_digest"] != historical["calibration_seal_digest"]:
        raise bulk.BulkError("calibration seal differs from the historical campaign")
    totals = seal["totals"]
    replay = read_json(out / "local-replay.json")
    bench = read_json(out / "local-bench.json")
    code = fast.transport_code_identity(CODE_REPO)
    if not replay["all_identical"] or replay["transport_code_sha256"] != code:
        raise bulk.BulkError("local replay is missing, failed or was made with other code")
    process_workers = choose_process_workers(bench["runs"], int(bench["machine"]["logical_cpus"]))
    local_model = {
        "rows_per_core_second": bench["runs"]["1"]["rows_per_core_second"],
        "rows_per_second_by_process_workers": {
            key: value["rows_per_second"] for key, value in bench["runs"].items()
        },
        "basis": bench["evidence_class"],
    }
    comparison = fast.transport_comparison(
        physical, local_model, old_disk["bytes_per_input_row"]["raw_selected_records"]
    )
    write_json(out / "transport-comparison.json", comparison)
    if comparison["chosen_transport"] != "whole_file_stream":
        raise bulk.BulkError("whole-file transfer exceeds the preference threshold; not frozen")
    limits = fast.limits_policy(historical["limits"], physical)
    download_workers = 8
    in_flight = download_workers + 2 * process_workers
    scratch = {
        "root": "XLM_SCRATCH_ROOT (recipes/operator/storage.json 'scratch_root') or --scratch-root",
        "relative": "ew-fast",
        "cap_bytes": SCRATCH_CAP_BYTES,
        "min_free_bytes": SCRATCH_MIN_FREE_BYTES,
        "max_in_flight_files": in_flight,
        "flow": [
            "network -> <scratch>/ew-fast/bNNNN/fRRRRR.parquet.part (one sequential stream)",
            "verify: length, strong ETag, SHA-256, Parquet magic, footer layout",
            "exclusive hashed copy -> <data root>/acq-raw/ew-fast/source/<source path>",
            "local PyArrow decode and the frozen adapters, reading the scratch copy",
            "publish canonical unit + receipt on the durable volume (one rename)",
            "remove the scratch copy and its state",
        ],
        "cap_counts": "every file under <scratch>/ew-fast, including partial downloads, "
        "benchmark files, replay work and leftovers no running unit owns",
        "reservation": "a file reserves the per-file byte bound before its first byte and "
        "shrinks to its declared length; a file that does not fit waits",
        "cleanup": "a scratch copy is removed only after its durable copy reproduced the "
        "verified SHA-256 and its canonical unit with receipt is published",
        "restart": "a verified prefix resumes by Range/If-Range from the last fsynced "
        "checkpoint; a completed scratch file is rehashed and reused; a retained durable "
        "source is rehashed and processed without a new transfer",
        "never_deleted": "retained source files, receipts, canonical units, files of other "
        "owners under the scratch root",
        "disjoint_from_durable_root": True,
    }
    concurrency = {
        "unit": "files; never column or range requests",
        "download_workers": download_workers,
        "download_workers_max": 16,
        "download_stream": "one sequential HTTP stream per file",
        "process_workers": process_workers,
        "process_workers_max": 16,
        "process_threads": 1,
        "process_worker_rule": "largest measured process count that still adds 10% over the "
        f"previous measured count, never above logical CPUs minus {RESERVED_LOGICAL_CPUS} "
        "(kept for the download streams, hashing, sealing and the system)",
        "process_worker_basis": {
            key: value["rows_per_second"] for key, value in bench["runs"].items()
        },
        "machine": bench["machine"],
        "pipeline": "downloads continue while finished files are processed; at most "
        f"{in_flight} files hold scratch at once",
        "benchmark_tiers": list(BENCHMARK_TIERS),
        "sixteen_workers": "permitted by the resource contract (max 16); not a default and "
        "not benchmarked here",
        "output_independence": "worker counts are execution-only: they are normalized out of "
        "the selection hash and no output byte depends on completion order",
    }
    scenarios = {name: int(entry["input_rows"]) for name, entry in old_disk["scenarios"].items()}
    storage = fast.storage_model(
        totals,
        physical,
        replay["ledger"],
        scenarios,
        bookkeeping_bytes_per_file=BOOKKEEPING_BYTES_PER_FILE,
        scratch_cap_bytes=SCRATCH_CAP_BYTES,
        max_in_flight_files=in_flight,
        max_file_bytes=int(limits["max_file_bytes"]),
    )
    usage = {}
    for label, path in (("durable", args.data_root), ("scratch", args.scratch_root)):
        if path is not None and path.anchor and Path(path.anchor).exists():
            disk = shutil.disk_usage(path.anchor)
            usage[label] = {
                "volume": path.anchor,
                "free_bytes": disk.free,
                "total_bytes": disk.total,
            }
    storage["measured_volumes"] = usage
    storage["guards"] = {
        "durable_min_free_bytes": MIN_FREE_BYTES,
        "durable_footprint_cap_bytes": FOOTPRINT_CAP_BYTES,
        "scratch_cap_bytes": SCRATCH_CAP_BYTES,
        "scratch_min_free_bytes": SCRATCH_MIN_FREE_BYTES,
    }
    write_json(out / "storage-model.json", storage)
    required = scenarios["first_pass_target_central_4_bytes_per_token"]
    historical_files = int(historical["batch"]["files"])
    policy = fast.batch_policy(
        physical, local_model, totals, required, process_workers, historical_files
    )
    policy["historical_policy"] = {
        "chosen_files_per_batch": old_batch["chosen_files_per_batch"],
        "restart_unit": "one row-group slice of 32 files",
    }
    write_json(out / "batch-policy.json", policy)
    size = int(policy["chosen_files_per_batch"])
    if size != historical_files:
        raise bulk.BulkError("batch size would change the frozen batch membership; not frozen")
    throughput = fast.throughput_model(physical, local_model, size, process_workers)
    live = out / "live-benchmark.json"
    if live.is_file():
        throughput["live"] = fast.measured_batch_model(
            physical,
            read_json(live),
            size,
            int(policy["options"][str(size)]["batches_to_science_target"]),
        )
    write_json(out / "throughput-model.json", throughput)
    write_json(out / "scratch-policy.json", scratch)
    write_json(out / "concurrency-policy.json", concurrency)
    write_json(out / "raw-artifact-contract.json", fast.raw_contract())
    smallest = min(seal["units"], key=lambda unit: int(unit["source_validator"]["length"]))
    parity = {
        "file": smallest["file"],
        "crawl": smallest["crawl"],
        "etag": smallest["source_validator"]["etag"],
        "length": smallest["source_validator"]["length"],
        "row_range": smallest["row_range"],
        "plan_id": smallest["plan_id"],
        "plan_hash": smallest["plan_hash"],
        "selection_hash": smallest["selection_hash"],
        "raw_sha256": smallest["raw_sha256"],
        "raw_bytes": smallest["raw_bytes"],
        "views": {
            view: {
                "documents_sha256": sealed["documents_sha256"],
                "rejections_sha256": sealed["rejections_sha256"],
            }
            for view, sealed in smallest["views"].items()
        },
        "why_this_file": "smallest of the eight sealed calibration files; outside the "
        "campaign's execution ceiling, so it can never be campaign progress",
    }
    body = {
        "binding": historical["binding"],
        "transport_code_sha256": code,
        "limits": limits,
    }
    inventory = read_json(REPO / historical["inventory"]["path"])
    files = bulk.batch_members(inventory, size, 0)
    bench_plan = fast.benchmark_plan(body, files, BENCHMARK_TIERS, parity)
    known = sum(int(f["remote_length"]) for f in physical["files"]) / len(physical["files"])
    bench_plan_note = {
        "expected_transfer_bytes": round(bench_plan["files"] * known + parity["length"]),
        "expected_transfer_basis": "mean of the eight known file sizes; batch-0 file sizes are "
        "not known before the transfer",
    }
    write_json(out / "benchmark-plan.json", bench_plan)
    write_json(out / "benchmark-expectation.json", bench_plan_note)
    config = fast.campaign_config(
        historical=historical,
        historical_path=HISTORICAL_CAMPAIGN,
        transport_code_sha256=code,
        batch_files=size,
        limits=limits,
        scratch={
            key: scratch[key]
            for key in ("relative", "cap_bytes", "min_free_bytes", "max_in_flight_files")
        },
        concurrency={
            key: concurrency[key]
            for key in (
                "download_workers",
                "download_workers_max",
                "process_workers",
                "process_workers_max",
                "process_threads",
            )
        },
        min_free_bytes=MIN_FREE_BYTES,
        footprint_cap_bytes=FOOTPRINT_CAP_BYTES,
        benchmark_digest=bench_plan["digest"],
    )
    write_json(out / "campaign.json", config)
    print(
        f"campaign: {config['digest']} batch files: {size} process workers: {process_workers} "
        f"benchmark: {bench_plan['digest']}"
    )
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """OFFLINE determinism and readiness proofs over the real frozen inventory."""
    campaign = load_campaign(args.campaign, args.data_root, args.scratch_root)
    first, second = campaign.members(0), campaign.members(1)
    old = read_json(REPO / historical_tool.BULK_DIR / "dry-run.json")
    ceiling = int(campaign.config["ceiling"]["max_batches"])
    size = int(campaign.config["batch"]["files"])
    state, decision = campaign_state(campaign)
    gate = evaluate_gate(campaign, 0, "", None)
    bench = read_json(args.campaign.parent / "benchmark-plan.json")
    write_json(
        args.output,
        {
            "campaign_digest": campaign.config["digest"],
            "supersedes": campaign.config["supersedes"]["digest"],
            "inventory_digest": campaign.inventory["inventory_digest"],
            "batch_files": size,
            "batch_0": {"files": first, "membership_digest": canonical.digest(first)},
            "batch_1": {"files": second, "membership_digest": canonical.digest(second)},
            "membership_equals_historical_campaign": {
                "batch_0": canonical.digest(first) == old["batch_0"]["membership_digest"],
                "batch_1": canonical.digest(second) == old["batch_1"]["membership_digest"],
                "through_ceiling": canonical.digest(
                    [campaign.members(index) for index in range(ceiling)]
                )
                == old["membership_digest_through_ceiling"],
            },
            "files_through_ceiling_distinct": len(
                {name for index in range(ceiling) for name in campaign.members(index)}
            )
            == ceiling * size,
            "benchmark_files_are_batch_0_prefix": [
                name for tier in bench["tiers"] for name in tier["files"]
            ]
            == first[: bench["files"]],
            "parity_file_inventory_rank": [e["file"] for e in campaign.inventory["files"]].index(
                bench["parity"]["file"]
            ),
            "parity_file_beyond_ceiling": [e["file"] for e in campaign.inventory["files"]].index(
                bench["parity"]["file"]
            )
            >= ceiling * size,
            "sealed_units": state["sealed_including_incomplete_batch"]["files"],
            "campaign_rows_counted": state["counted"]["rows"],
            "stop_decision_before_any_batch": decision,
            "first_batch_gate": gate,
            "first_batch_gate_expected": "REFUSE until the live benchmark passes",
            "readiness": {
                "science_identical_to_historical_campaign": True,
                "selector_frozen": campaign.config["binding"]["selector"]
                == selector.selector_identity(),
                "source_revision_ok": campaign.config["binding"]["revision"]
                == selector.SOURCE_REVISION,
                "transport_code_matches_freeze": True,
                "membership_deterministic": True,
                "prepare_needs_network": False,
                "benchmark_passed": benchmark_passed(campaign),
                "bulk_fetch_run": state["sealed_including_incomplete_batch"]["files"] > 0,
                "historical_campaign_ledger_entries": len(
                    list((campaign.root / "plans/ew-bulk/ledger").glob("*.json"))
                )
                if (campaign.root / "plans/ew-bulk/ledger").is_dir()
                else 0,
                "c05_receipt_present": False,
            },
            "network": "none",
        },
    )
    print(f"dry run written: {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=REPO / FAST_DIR / "campaign.json")
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--scratch-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
        ("show", cmd_show, "Show one batch's deterministic membership (offline)."),
        ("plan", cmd_plan, "Bind membership and ceilings; print the digest (offline)."),
        ("status", cmd_status, "Batch unit states, cumulative yield and stop decision."),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--batch", type=int, required=True)
        command.set_defaults(func=func)
    authorize = sub.add_parser("authorize", help="Record operator authorization; mint the plan.")
    authorize.add_argument("--batch", type=int, required=True)
    authorize.add_argument("--digest", required=True)
    authorize.add_argument("--operator", required=True)
    authorize.set_defaults(func=cmd_authorize)
    gate = sub.add_parser("gate", help="May this batch run now?")
    gate.add_argument("--batch", type=int, required=True)
    gate.add_argument("--top-up-reason", default="")
    gate.add_argument("--free-bytes", type=int, default=None, help=argparse.SUPPRESS)
    gate.set_defaults(func=cmd_gate)
    run = sub.add_parser("run", help="NETWORK: execute one authorized batch (resumable).")
    run.add_argument("--batch", type=int, required=True)
    run.add_argument("--workers", type=int, default=None)
    run.add_argument("--process-workers", type=int, default=None)
    run.add_argument("--top-up-reason", default="")
    run.set_defaults(func=cmd_run)
    account_parser = sub.add_parser("account", help="Cumulative yield and the stop decision.")
    account_parser.set_defaults(func=cmd_account)
    benchmark = sub.add_parser("benchmark", help="NETWORK: small transport benchmark + parity.")
    benchmark.add_argument("--plan", type=Path, default=REPO / FAST_DIR / "benchmark-plan.json")
    benchmark.add_argument("--authorize", required=True)
    benchmark.set_defaults(func=cmd_benchmark)
    accept = sub.add_parser(
        "benchmark-accept", help="OFFLINE: re-accept the saved live benchmark report."
    )
    accept.add_argument("--plan", type=Path, default=REPO / FAST_DIR / "benchmark-plan.json")
    accept.add_argument("--report", type=Path, default=None)
    accept.add_argument("--evidence-dir", type=Path, default=None)
    accept.set_defaults(func=cmd_benchmark_accept)
    for name, func, text in (
        ("replay", cmd_replay, "OFFLINE: reproduce the sealed calibration from local Parquet."),
        ("bench-local", cmd_bench_local, "OFFLINE: local processing rate by process count."),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--calibration-root", type=Path, required=True)
        command.add_argument("--footer-root", type=Path, required=True)
        command.set_defaults(func=func)
    sub.choices["replay"].add_argument(
        "--output", type=Path, default=REPO / FAST_DIR / "local-replay.json"
    )
    sub.choices["bench-local"].add_argument(
        "--output", type=Path, default=REPO / FAST_DIR / "local-bench.json"
    )
    sub.choices["bench-local"].add_argument("--repeat", type=int, default=8)
    sub.choices["bench-local"].add_argument("--copies", type=int, default=2)
    sub.choices["bench-local"].add_argument(
        "--workers", type=int, nargs="+", default=list(LOCAL_BENCH_WORKERS)
    )
    model = sub.add_parser("model", help="OFFLINE: build the models and freeze the campaign.")
    model.add_argument("--output-dir", type=Path, default=REPO / FAST_DIR)
    model.set_defaults(func=cmd_model)
    dry = sub.add_parser("dry-run", help="OFFLINE determinism proofs on the real inventory.")
    dry.add_argument("--output", type=Path, default=REPO / FAST_DIR / "dry-run.json")
    dry.set_defaults(func=cmd_dry_run)
    return parser


NETWORK_COMMANDS = frozenset({"run", "benchmark"})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_root is None:
        configured = os.environ.get("XLM_DATA_ROOT", "")
        if not configured:
            print("essential_web_fast: refused: set XLM_DATA_ROOT or --data-root", file=sys.stderr)
            return EXIT_REFUSED
        args.data_root = Path(configured)
    if args.scratch_root is None and os.environ.get("XLM_SCRATCH_ROOT"):
        args.scratch_root = Path(os.environ["XLM_SCRATCH_ROOT"])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        return int(args.func(args))
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f"essential_web_fast: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
