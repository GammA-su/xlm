"""Essential-Web bulk campaign: measured models, batch planning and accounting.

Offline except ``layout``, which reads Parquet FOOTERS only through the
existing bounded range discovery. Fetching stays with ``xlm data fetch`` and
adaptation with ``xlm data adapt``; this tool plans batches from the frozen
inventory, verifies and seals each slice, and keeps the cumulative yield and
stop decision. It never deletes anything and never counts calibration rows.

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
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import essential_web_calibration_seal as sealer
import mix01_inventory
import pyarrow as pa
import yaml

from xlm.data.acquisition.plan import (
    AcquisitionPlan,
    AuthorizationRequiredError,
    load_acquisition_plan,
    plan_requires_production_admission,
    validate_plan_authorization,
)
from xlm.data.acquisition.progress import AcquisitionState, ProgressJournal
from xlm.data.acquisition.sampling import SamplingRefusal, discover_layout_local
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources import essential_web_readiness as ready

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = "docs/implementation/evidence"
SEAL_DIR = f"{EVIDENCE}/ESSENTIAL-WEB-PRODUCTION-CALIBRATION"
READY_DIR = f"{EVIDENCE}/ESSENTIAL-WEB-PRODUCTION-READINESS"
BULK_DIR = f"{EVIDENCE}/ESSENTIAL-WEB-BULK-ACQUISITION"
QUOTAS = "recipes/mixtures/mix01_quotas_6b.yaml"
FOOTER_LAYOUT_SHA256 = "3bcafccb81737694d731dad9cda4949b5e221c51ca9cde4f69102b087267f5e4"
EXIT_REFUSED, EXIT_TARGET_REACHED, EXIT_COMPLETE = 1, 3, 4
MIN_FREE_BYTES = 64 * bulk.GIB
FOOTPRINT_CAP_BYTES = 850 * bulk.GIB
MAX_JSON_BYTES = 64 * bulk.MIB


def read_json(path: Path) -> Any:
    return mix01_inventory._read_bounded_json(path, MAX_JSON_BYTES, "campaign input")


def write_json(path: Path, payload: Any) -> None:
    mix01_inventory._atomic_write_json(path, payload)


def write_once(path: Path, payload: Any) -> bool:
    """Write an immutable record; an identical record is reused, a different one refused."""
    if path.exists():
        if read_json(path) != json.loads(json.dumps(payload)):
            raise bulk.BulkError(f"'{path}' already holds a different immutable record")
        return False
    write_json(path, payload)
    return True


def self_digest(payload: dict[str, Any]) -> dict[str, Any]:
    payload["digest"] = canonical.digest({k: v for k, v in payload.items() if k != "digest"})
    return payload


def check_digest(payload: dict[str, Any], what: str) -> None:
    body = {key: value for key, value in payload.items() if key != "digest"}
    if canonical.digest(body) != payload.get("digest"):
        raise bulk.BulkError(f"{what} does not match its own digest")


def projection() -> tuple[str, ...]:
    return tuple(columns_for(bulk.ADAPTER_ID, bulk.PLAN_VIEW))


@dataclass(frozen=True)
class Campaign:
    """A verified campaign definition bound to one operator data root."""

    config: dict[str, Any]
    inventory: dict[str, Any]
    root: Path
    repo: Path

    @property
    def plans(self) -> Path:
        return self.root / str(self.config["roots"]["plans"])

    @property
    def ledger(self) -> Path:
        return self.plans / "ledger"

    def batch_dir(self, batch: int) -> Path:
        return self.plans / f"b{batch:04d}"

    def members(self, batch: int) -> list[str]:
        return bulk.batch_members(self.inventory, int(self.config["batch"]["files"]), batch)

    def unit(self, kind: str, batch: int, name: str) -> Path:
        return self.root / str(self.config["roots"][kind]) / f"b{batch:04d}" / name

    def canonical(self, view: str, batch: int, name: str) -> Path:
        return self.root / str(self.config["roots"]["canonical"]) / view / f"b{batch:04d}" / name


def slice_name(index: int, attempt: int) -> str:
    return f"s{index:02d}" if attempt == 1 else f"s{index:02d}-a{attempt}"


def load_campaign(path: Path, data_root: Path, repo: Path | None = None) -> Campaign:
    """Load the frozen campaign and refuse any drift in its bound inputs."""
    repo = REPO if repo is None else repo
    config = read_json(path)
    bulk.check_campaign(config)
    ready.check_binding(config["binding"])
    if sealer.code_identity() != config["adapter_code_sha256"]:
        raise bulk.BulkError("adapter or selector code changed since the campaign was frozen")
    inventory = read_json(repo / config["inventory"]["path"])
    refrozen = mix01_inventory.freeze_inventory(
        "essential_web",
        ready.REPOSITORY,
        selector.SOURCE_REVISION,
        int(config["inventory"]["seed"]),
        [entry["file"] for entry in inventory["files"]],
        {e["file"]: e["size_bytes"] for e in inventory["files"] if e["size_bytes"] is not None},
    )
    if refrozen != inventory or inventory["inventory_digest"] != config["inventory"]["digest"]:
        raise bulk.BulkError("inventory differs from the frozen campaign inventory")
    for key, name in (("quotas", "sha256"), ("plan", "catalog_sha256")):
        target = repo / config[key]["path" if key == "quotas" else "catalog"]
        if sealer.file_sha256(target) != config[key][name]:
            raise bulk.BulkError(f"{target.name} differs from the frozen campaign input")
    return Campaign(config=config, inventory=inventory, root=data_root, repo=repo)


# ------------------------------------------------------------------ modelling


def real_footers(footer_root: Path, physical_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All row groups of the footer-certified files, from the retained real footers."""
    raw = (footer_root / "m_phase_p_layout.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != FOOTER_LAYOUT_SHA256:
        raise bulk.BulkError("footer evidence differs from the reviewed Phase-P parent")
    layout = json.loads(raw)
    if layout["source"]["revision"] != selector.SOURCE_REVISION or layout["synthetic"]:
        raise bulk.BulkError("footer evidence is not the pinned real source")
    footers = []
    for index, (file, known) in enumerate(zip(layout["files"], physical_inputs, strict=True)):
        descriptor = file["payloads"][f"M-{index:02d}-footer"]
        data = (footer_root / descriptor["retained_file"]).read_bytes()
        if (
            hashlib.sha256(data).hexdigest() != descriptor["sha256"]
            or descriptor["sha256"] != known["footer_sha256"]
            or file["file"] != known["file"]
        ):
            raise bulk.BulkError("frozen footer integrity mismatch")
        record = bulk.layout_record(
            discover_layout_local(
                pa.BufferReader(b"PAR1" + data),
                name=file["file"],
                max_parser_bytes=32 * bulk.MIB,
                max_decompression_ratio=15.0,
            ),
            projection(),
            15.0,
        )
        if (
            record["rows"] != known["file_rows"]
            or len(record["groups"]) != known["file_row_groups"]
        ):
            raise bulk.BulkError("footer rows differ from the frozen physical inputs")
        record.update(
            crawl=known["crawl"],
            remote_length=known["remote_length"],
            footer_bytes=len(data),
            footer_sha256=descriptor["sha256"],
        )
        footers.append(record)
    return footers


def headroom_estimate(totals: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Run the existing ``mix01_inventory estimate`` on the sealed calibration."""
    entries = {
        view: {
            "records_sampled": totals["input_rows"],
            "accepted_records": totals["retained"][view]["documents"],
            "rejected_records": totals["input_rows"] - totals["retained"][view]["documents"],
            "transferred_bytes": totals["physical_response_body_bytes"],
            "canonical_bytes": totals["retained"][view]["canonical_bytes"],
            "extra_survival": 1.0,
        }
        for view in calibration.VIEWS
    }
    if Path.cwd().resolve() != REPO or not output_dir.resolve().is_relative_to(REPO):
        raise bulk.BulkError("run 'model' from the repository root with an in-repository output")
    output_dir = output_dir.resolve().relative_to(REPO)
    calibration_file = output_dir / "calibration-entries.json"
    write_json(
        calibration_file,
        {
            "sources": entries,
            "note": "sealed calibration totals; every component bears the full shared "
            "prefix-window transfer, so transfer sizing here is the naive prefix-linear one",
        },
    )
    parser = mix01_inventory.build_parser()
    args = parser.parse_args(
        [
            "estimate",
            "--quotas",
            QUOTAS,
            "--calibration",
            calibration_file.as_posix(),
            "--output",
            (output_dir / "headroom-estimate.json").as_posix(),
        ]
    )
    if args.func(args) != 0:
        raise bulk.BulkError("mix01_inventory estimate refused the sealed calibration")
    estimate: dict[str, Any] = read_json(output_dir / "headroom-estimate.json")
    return estimate


def cmd_model(args: argparse.Namespace) -> int:
    seal = read_json(args.seal_dir / "calibration-seal.json")
    body = {key: value for key, value in seal.items() if key != "seal_digest"}
    if canonical.digest(body) != seal["seal_digest"]:
        raise bulk.BulkError("calibration seal does not match its own digest")
    crawls = read_json(args.seal_dir / "crawl-yields.json")
    if crawls["seal_digest"] != seal["seal_digest"]:
        raise bulk.BulkError("crawl yields belong to another seal")
    totals = seal["totals"]
    quotas_path = REPO / QUOTAS
    quotas = yaml.safe_load(quotas_path.read_bytes())
    inventory_path = f"{READY_DIR}/production.inventory.json"
    inventory = read_json(REPO / inventory_path)
    footers = real_footers(args.footer_root, read_json(args.readiness_dir / "physical-inputs.json"))
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    write_json(
        out / "footer-layouts.json",
        {
            "evidence": "real retained Phase-P footers of the eight footer-certified files; "
            "sizes and row counts only, no row was decoded",
            "projection": list(projection()),
            "files": footers,
        },
    )
    capacity = bulk.science_capacity(totals, quotas, crawls)
    physical = bulk.physical_cost(footers, crawls["per_crawl"], totals)
    low, high = crawls["science_rows_required_leave_one_out_range"]
    rows = {name: int(case["required_input_rows"]) for name, case in capacity["cases"].items()}
    scenarios = {
        "first_pass_target_central_4_bytes_per_token": rows["central"],
        "leave_one_crawl_out_maximum": int(high),
        "final_quota_if_exact_bytes_per_token_is_5": int(
            capacity["rows_for_final_quota_if_bytes_per_token_is_high"]
        ),
    }
    capacity["physical_full_file"] = {
        name: bulk.extrapolate(physical, need) for name, need in {**rows, **scenarios}.items()
    }
    capacity["leave_one_crawl_out_rows_range"] = [int(low), int(high)]
    capacity["seal_digest"] = seal["seal_digest"]
    physical["seal_digest"] = seal["seal_digest"]
    supply = bulk.inventory_capacity(inventory, physical, {**rows, **scenarios})
    strata = supply["crawl_strata"]
    weighted = sum(
        strata[unit["crawl"]] * unit[bulk.SCIENCE]["estimated_tokens_per_input_row"]
        for unit in crawls["per_crawl"]
    ) / sum(strata.values())
    capacity["inventory_file_share_weighted"] = {
        "estimated_tokens_per_input_row": weighted,
        "required_input_rows": int(-(-capacity["first_pass_estimated_token_target"] // weighted)),
        "assumption": "crawls weighted by their inventory file counts instead of equally; "
        "assumes equal rows per file in every crawl, which is not verified",
    }
    write_json(out / "science-capacity-model.json", capacity)
    write_json(out / "physical-cost-model.json", physical)
    write_json(out / "inventory-capacity.json", supply)
    policy = bulk.batch_policy(physical, totals, rows["central"])
    size = int(policy["chosen_files_per_batch"])
    full = physical["full_file"]
    max_batches = -(-int(high) // (size * int(full["min_max_rows_per_file"][0])))
    policy["execution_contingency"] = {
        "quota_headroom": "10% first-pass headroom inside the frozen 660M/660M/330M targets",
        "scan_contingency": "separate execution ceiling; the stop condition and the targets "
        "are unchanged and nothing is acquired beyond the measured stop",
        "ceiling_rows": int(high),
        "ceiling_basis": "largest leave-one-crawl-out science row requirement, in files of "
        "the smallest observed size",
        "max_batches": max_batches,
        "expected_batches_central": policy["options"][str(size)]["batches_to_science_target"],
    }
    write_json(out / "batch-policy.json", policy)
    usage = shutil.disk_usage(args.data_root)
    disk = bulk.disk_budget(
        totals,
        scenarios,
        size * int(full["max_group_rows"]),
        usage.free,
        usage.total,
        MIN_FREE_BYTES,
        FOOTPRINT_CAP_BYTES,
    )
    disk["measured_at"] = str(args.data_root)
    write_json(out / "disk-budget.json", disk)
    estimate = headroom_estimate(totals, out)
    required = {
        view: int(estimate["sources"][view]["required_canonical_bytes_base"])
        for view in calibration.VIEWS
    }
    catalog = f"{READY_DIR}/production-catalog.json"
    config = bulk.campaign_config(
        binding=seal["binding"],
        adapter_code_sha256=seal["adapter"]["code_sha256"],
        seal_digest=seal["seal_digest"],
        freeze_digest=seal["calibration_freeze_digest"],
        inventory=inventory,
        inventory_path=inventory_path,
        catalog_path=catalog,
        catalog_sha256=sealer.file_sha256(REPO / catalog),
        quotas=quotas,
        quotas_path=QUOTAS,
        quotas_sha256=sealer.file_sha256(quotas_path),
        required_canonical_bytes=required,
        batch_files=size,
        max_batches=max_batches,
        limits=bulk.limits_policy(physical, totals),
        min_free_bytes=MIN_FREE_BYTES,
        footprint_cap_bytes=FOOTPRINT_CAP_BYTES,
    )
    write_json(out / "bulk-campaign.json", config)
    write_json(
        out / "stop-policy.json",
        {
            "condition": "continue deterministic inventory batches until, over complete "
            "batches, cumulative canonical bytes of every Essential view reach its "
            "required_canonical_bytes (first-pass estimated tokens x 4 bytes/token)",
            "mechanism": config["stop"]["mechanism"],
            "targets": config["stop"]["targets"],
            "evaluated": config["stop"]["evaluated"],
            "overshoot_bound": "at most one batch past the first complete batch that meets it",
            "afterwards": [
                "freeze the canonical pool",
                "C05 exclusion receipt over the frozen pool",
                "train and freeze the tokenizer",
                "exact token count",
                "top up deficient components from the next inventory batches, then refreeze "
                "and rescreen",
            ],
            "exactness": config["stop"]["exactness"],
            "campaign_digest": config["digest"],
        },
    )
    print(f"campaign: {config['digest']} batch files: {size} max batches: {max_batches}")
    return 0


# ------------------------------------------------------------------- planning


def plan_arguments(campaign: Campaign, files: list[str], scan_rows: int) -> list[str]:
    plan = campaign.config["plan"]
    window = plan["window"]
    return [
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
        "--adapter-spec",
        plan["adapter_spec"],
        "--seed",
        str(plan["seed"]),
        "--parquet-window-scan-rows",
        str(scan_rows),
        "--parquet-window-buffer-bytes",
        str(window["stream_buffer_bytes"]),
        "--parquet-window-batch-rows",
        str(min(int(window["batch_rows"]), scan_rows)),
        "--parquet-window-policy-version",
        str(window["policy_version"]),
    ]


def mint_plan(
    campaign: Campaign,
    entry: dict[str, Any],
    directory: Path,
    output: Path,
    authorization: str | None,
) -> AcquisitionPlan:
    """Create one slice plan through the real ``xlm data plan`` command."""
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app

    name = slice_name(int(entry["slice"]), int(entry.get("attempt", 1)))
    rows_path, limits_path = directory / f"{name}.rows.json", directory / f"{name}.limits.json"
    write_once(rows_path, entry["row_ranges"])
    write_once(limits_path, entry["limits"])
    arguments = plan_arguments(campaign, list(entry["files"]), int(entry["window_scan_rows"])) + [
        "--row-ranges",
        str(rows_path),
        "--limits",
        str(limits_path),
        "--attempt",
        str(entry.get("attempt", 1)),
        "--output",
        str(output),
    ]
    if authorization is not None:
        arguments += ["--authorization-hash", authorization]
    result = CliRunner().invoke(app, arguments)
    if result.exit_code != 0:
        raise bulk.BulkError(f"xlm data plan refused slice {name}: {result.output}")
    plan = load_acquisition_plan(output)
    if plan.revision != selector.SOURCE_REVISION or not plan_requires_production_admission(plan):
        raise bulk.BulkError("slice plan is not a pinned production plan")
    return plan


def batch_record(campaign: Campaign, batch: int) -> dict[str, Any]:
    record: dict[str, Any] = read_json(campaign.batch_dir(batch) / "batch.json")
    check_digest(record, "batch record")
    if record["campaign"] != campaign.config["digest"] or record["files"] != campaign.members(
        batch
    ):
        raise bulk.BulkError("batch record belongs to another campaign or membership")
    return record


def current_slices(campaign: Campaign, batch: int) -> list[dict[str, Any]]:
    """Planned slices with the newest renewed attempt, paths and authorization state."""
    directory = campaign.batch_dir(batch)
    record = batch_record(campaign, batch)
    slices = []
    for planned in record["slices"]:
        entry = dict(planned)
        entry["authorization_file"] = "authorization.json"
        entry["authorization_digest"] = record["authorization_digest"]
        renewals = directory.glob(f"renew-s{int(planned['slice']):02d}-a*.json")
        for renewal_path in sorted(renewals, key=lambda p: int(p.stem.rsplit("-a", 1)[1])):
            renewal = read_json(renewal_path)
            check_digest(renewal, "renewal record")
            if renewal["attempt"] == entry["attempt"] + 1:
                entry.update(
                    attempt=renewal["attempt"],
                    plan_hash=renewal["plan_hash"],
                    plan_id=renewal["plan_id"],
                    authorization_digest=renewal["authorization_digest"],
                    authorization_file=f"authorization-{renewal_path.stem.removeprefix('renew-')}"
                    ".json",
                )
        name = slice_name(int(entry["slice"]), int(entry["attempt"]))
        entry.update(
            name=name,
            plan_path=directory / f"{name}.plan.json",
            dry_plan_path=directory / f"{name}.dry.plan.json",
            raw_dir=campaign.unit("raw", batch, name),
            scratch_dir=campaign.unit("scratch", batch, name),
            canonical={view: campaign.canonical(view, batch, name) for view in calibration.VIEWS},
            ledger_path=campaign.ledger / f"b{batch:04d}.s{int(entry['slice']):02d}.json",
        )
        entry["journal_path"] = (
            entry["scratch_dir"] / "journals" / f"{entry['plan_id']}.progress.json"
        )
        slices.append(entry)
    return slices


def cmd_show(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root)
    files = campaign.members(args.batch)
    print(f"campaign {campaign.config['digest']}")
    print(f"batch {args.batch}: {len(files)} files, membership {canonical.digest(files)}")
    for position, name in enumerate(files):
        print(f"  {args.batch * int(campaign.config['batch']['files']) + position:5d}  {name}")
    print("rows, bytes and time are unknown until this batch's footers are read (Layout)")
    return 0


def cmd_layout(args: argparse.Namespace) -> int:
    """NETWORK: read the footers of one batch; never record payloads."""
    from xlm.cli.data_cmd import _discover_remote_layouts

    campaign = load_campaign(args.campaign, args.data_root)
    files = campaign.members(args.batch)
    target = campaign.batch_dir(args.batch) / "layout.json"
    if target.exists():
        check_digest(read_json(target), "layout record")
        print(f"layout reused: {target}")
        return 0
    limits = campaign.config["limits"]
    layouts = _discover_remote_layouts(
        provider="huggingface",
        repository=ready.REPOSITORY,
        revision=selector.SOURCE_REVISION,
        files=files,
        max_parser_bytes=int(limits["max_parser_bytes"]),
        max_decompression_ratio=float(limits["max_decompression_ratio"]),
        metadata_bytes=len(files) * int(limits["metadata_bytes_per_file"]),
        metadata_requests=len(files) * 2 * int(limits["metadata_requests_per_file"]),
    )
    record = self_digest(
        {
            "campaign": campaign.config["digest"],
            "batch": args.batch,
            "files": files,
            "revision": selector.SOURCE_REVISION,
            "layouts": {
                name: bulk.layout_record(
                    layouts[name], projection(), float(limits["max_decompression_ratio"])
                )
                for name in files
            },
        }
    )
    write_once(target, record)
    rows = sum(int(entry["rows"]) for entry in record["layouts"].values())
    print(f"layout written: {target} rows: {rows}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root)
    directory = campaign.batch_dir(args.batch)
    files = campaign.members(args.batch)
    layout = read_json(directory / "layout.json")
    check_digest(layout, "layout record")
    if layout["campaign"] != campaign.config["digest"] or layout["files"] != files:
        raise bulk.BulkError("layout belongs to another campaign or membership")
    slices = bulk.plan_slices(files, layout["layouts"], campaign.config["limits"], args.workers)
    for entry in slices:
        entry["attempt"] = 1
        name = slice_name(int(entry["slice"]), 1)
        plan = mint_plan(campaign, entry, directory, directory / f"{name}.dry.plan.json", None)
        entry.update(plan_hash=plan.plan_hash, plan_id=plan.plan_id)
    record = self_digest(
        {
            "campaign": campaign.config["digest"],
            "batch": args.batch,
            "files": files,
            "membership_digest": canonical.digest(files),
            "layout_digest": layout["digest"],
            "workers": args.workers,
            "rows": sum(int(entry["rows"]) for entry in slices),
            "slices": slices,
            "authorization_digest": bulk.authorization_digest(
                campaign.config["digest"], args.batch, slices
            ),
        }
    )
    write_once(directory / "batch.json", record)
    modeled = sum(int(e["modeled"]["projected_compressed_bytes"]) for e in slices)
    print(f"batch {args.batch}: {len(slices)} slices, {record['rows']} rows")
    print(f"modeled projected transfer (no retries): {modeled} bytes")
    print(f"transfer budget: {sum(int(e['limits']['max_transferred_bytes']) for e in slices)}")
    print(f"AUTHORIZATION DIGEST: {record['authorization_digest']}")
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    """Record the operator's authorization and mint the authorized plans."""
    campaign = load_campaign(args.campaign, args.data_root)
    directory = campaign.batch_dir(args.batch)
    pending = [
        entry
        for entry in current_slices(campaign, args.batch)
        if entry["authorization_digest"] == args.digest
    ]
    if not pending:
        raise bulk.BulkError("digest matches no planned slice of this batch; nothing authorized")
    names = {entry["authorization_file"] for entry in pending}
    if len(names) != 1:
        raise bulk.BulkError("ambiguous authorization scope")
    target = directory / names.pop()
    if not target.exists():
        write_json(
            target,
            {
                "authorization_digest": args.digest,
                "batch": args.batch,
                "slices": [entry["name"] for entry in pending],
                "operator": args.operator,
                "authorized_at": datetime.now(UTC).isoformat(),
            },
        )
    elif read_json(target)["authorization_digest"] != args.digest:
        raise bulk.BulkError("a different authorization is already recorded")
    for entry in pending:
        plan = mint_plan(campaign, entry, directory, entry["plan_path"], entry["plan_hash"])
        if plan.plan_hash != entry["plan_hash"]:
            raise bulk.BulkError("authorized plan differs from the reviewed dry plan")
        validate_plan_authorization(plan, catalog_source_approved=True)
    print(f"authorized {len(pending)} slice plan(s) for batch {args.batch}")
    return 0


def journal_state(entry: dict[str, Any]) -> AcquisitionState | None:
    path: Path = entry["journal_path"]
    if not path.is_file():
        return None
    state = AcquisitionState.model_validate(read_json(path))
    if state.plan_hash != entry["plan_hash"]:
        raise bulk.BulkError("journal belongs to another plan")
    return state


def slice_status(entry: dict[str, Any]) -> dict[str, Any]:
    state = journal_state(entry)
    raw = entry["raw_dir"] / "selected_records.jsonl"
    adapted = {}
    for view, directory in entry["canonical"].items():
        summary_path = directory / "adaptation_summary.json"
        adapted[view] = (
            summary_path.is_file() and read_json(summary_path)["plan_hash"] == entry["plan_hash"]
        )
    return {
        "slice": entry["slice"],
        "attempt": entry["attempt"],
        "name": entry["name"],
        "rows": entry["rows"],
        "plan_hash": entry["plan_hash"],
        "authorized": entry["plan_path"].is_file(),
        "fetched": state is not None and state.status == "COMPLETED" and raw.is_file(),
        "journal_status": None if state is None else state.status,
        "adapted": adapted,
        "sealed": entry["ledger_path"].is_file(),
        "plan": str(entry["plan_path"]),
        "raw_dir": str(entry["raw_dir"]),
        "scratch_dir": str(entry["scratch_dir"]),
        "raw_file": str(raw),
        "canonical": {view: str(path) for view, path in entry["canonical"].items()},
        "max_input_bytes": entry["limits"]["max_output_disk_bytes"],
    }


def cmd_state(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root)
    statuses = [slice_status(entry) for entry in current_slices(campaign, args.batch)]
    write_json(
        campaign.batch_dir(args.batch) / "state.json",
        {"batch": args.batch, "views": list(calibration.VIEWS), "slices": statuses},
    )
    for status in statuses:
        done = ",".join(view for view, ok in status["adapted"].items() if ok) or "-"
        print(
            f"{status['name']}: authorized={status['authorized']} fetched={status['fetched']} "
            f"adapted={done} sealed={status['sealed']}"
        )
    return 0


# ----------------------------------------------------------------- accounting


def check_documents(path: Path, view: str, limit: int) -> None:
    """Every retained document carries its own component and the frozen selector."""
    with path.open("rb") as stream:
        while raw := stream.readline(limit + 1):
            if len(raw) > limit:
                raise bulk.BulkError("canonical document exceeds the bounded line size")
            metadata = json.loads(raw)["source_metadata"]
            if (
                metadata["mix01_component"] != view
                or metadata["essential_web_selector_policy_digest"] != selector.POLICY_DIGEST
            ):
                raise bulk.BulkError("retained document has the wrong component or selector")


def cmd_seal_slice(args: argparse.Namespace) -> int:
    """Verify one fetched and adapted slice and write its immutable ledger entry."""
    campaign = load_campaign(args.campaign, args.data_root)
    entry = next(e for e in current_slices(campaign, args.batch) if e["slice"] == args.slice)
    plan = load_acquisition_plan(entry["plan_path"])
    if (
        plan.plan_hash != entry["plan_hash"]
        or plan.authorization is None
        or plan.authorization.authorization_hash != plan.plan_hash
        or {name: list(span) for name, span in (plan.row_ranges or {}).items()}
        != entry["row_ranges"]
    ):
        raise bulk.BulkError("executed plan is not the authorized planned slice")
    if not entry["journal_path"].is_file():
        raise bulk.BulkError("slice has no fetch journal")
    journal = ProgressJournal(entry["journal_path"], plan.plan_id, plan.plan_hash)
    receipt = AcquisitionVerifier(plan, output_dir=entry["raw_dir"], journal=journal).verify()
    state = journal.state
    validators = {name: state.source_validators.get(name) for name in entry["files"]}
    if any(
        not value or not value.get("etag") or not value.get("length")
        for value in validators.values()
    ):
        raise bulk.BulkError("fetch journal lacks a source validator for a slice file")
    for other_path in sorted(campaign.ledger.glob(f"b{args.batch:04d}.s*.json")):
        other = read_json(other_path)
        for name, value in other["source_validators"].items():
            if name in validators and validators[name] != value:
                raise bulk.BulkError(f"source drift: {name} changed between slices")
    raw = entry["raw_dir"] / "selected_records.jsonl"
    limit = int(entry["limits"]["max_record_bytes"]) + 65536
    views: dict[str, Any] = {}
    for view, directory in entry["canonical"].items():
        measured = mix01_inventory.measure_unit(
            plan_path=entry["plan_path"],
            scratch_dir=entry["scratch_dir"],
            canonical_dir=directory,
            source="essential_web",
            view=bulk.PLAN_VIEW,
            revision=selector.SOURCE_REVISION,
            allow_empty=True,
        )
        summary = read_json(directory / "adaptation_summary.json")
        rejections = directory / "adaptation_rejections.jsonl"
        if summary["adapter_id"] != bulk.ADAPTER_ID or summary["on_reject"] != "record":
            raise bulk.BulkError(f"{view}: canonical output has the wrong adapter or policy")
        check_documents(directory / "documents.jsonl", view, limit)
        views[view] = {
            "documents": measured["accepted_records"],
            "canonical_bytes": measured["canonical_bytes"],
            "documents_sha256": measured["documents_sha256"],
            "documents_file_bytes": (directory / "documents.jsonl").stat().st_size,
            "adaptation_summary_sha256": sealer.file_sha256(directory / "adaptation_summary.json"),
            "rejections_sha256": summary["rejections"]["sha256"],
            "rejections_file_bytes": rejections.stat().st_size,
            "rejection_counts_by_code": dict(sorted(summary["rejection_counts_by_code"].items())),
        }
    malformed = bulk.check_conservation(int(entry["rows"]), views)
    record = self_digest(
        {
            "campaign": campaign.config["digest"],
            "batch": args.batch,
            "slice": entry["slice"],
            "attempt": entry["attempt"],
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "selection_hash": plan.compute_selection_hash(),
            "authorization": plan.authorization.model_dump(),
            "files": entry["files"],
            "row_ranges": entry["row_ranges"],
            "rows": receipt.files[0].record_count,
            "source_validators": validators,
            "raw_sha256": receipt.files[0].locally_computed_sha256,
            "raw_bytes": raw.stat().st_size,
            "receipt": receipt.model_dump(),
            "transferred_bytes": state.transferred_bytes,
            "decompressed_bytes": state.decompressed_bytes,
            "requests": state.requests_made,
            "records_scanned": state.accounting.consumed.get("records_scanned", 0),
            "elapsed_seconds": (
                datetime.fromisoformat(state.updated_at) - datetime.fromisoformat(state.started_at)
            ).total_seconds(),
            "views": views,
            "malformed_rows": malformed,
            "documents_file_bytes": sum(v["documents_file_bytes"] for v in views.values()),
            "rejections_file_bytes": sum(v["rejections_file_bytes"] for v in views.values()),
        }
    )
    if record["rows"] != entry["rows"]:
        raise bulk.BulkError("fetched rows differ from the planned slice rows")
    created = write_once(entry["ledger_path"], record)
    print(f"slice {entry['name']} {'sealed' if created else 'already sealed'}: {record['digest']}")
    return 0


def campaign_state(campaign: Campaign) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cumulative yield and stop decision from the immutable ledger."""
    batches: dict[int, dict[str, Any]] = {}
    if campaign.plans.is_dir():
        for directory in sorted(campaign.plans.glob("b[0-9][0-9][0-9][0-9]")):
            if (directory / "batch.json").is_file():
                index = int(directory.name[1:])
                batches[index] = {
                    "files": campaign.members(index),
                    "slices": [
                        {
                            key: e[key]
                            for key in ("slice", "attempt", "plan_hash", "row_ranges", "rows")
                        }
                        for e in current_slices(campaign, index)
                    ],
                }
    entries = []
    if campaign.ledger.is_dir():
        for path in sorted(campaign.ledger.glob("b*.s*.json")):
            entry = read_json(path)
            check_digest(entry, f"ledger entry {path.name}")
            if entry["campaign"] != campaign.config["digest"]:
                raise bulk.BulkError(f"ledger entry {path.name} belongs to another campaign")
            entries.append(entry)
    state = bulk.cumulative(batches, entries)
    return state, bulk.stop_decision(state, campaign.config["stop"]["targets"])


def repository_sufficiency(campaign: Campaign, state: dict[str, Any]) -> dict[str, Any]:
    """The repository's own sufficiency verdict on the cumulative canonical bytes."""
    acquired = campaign.plans / "acquired.json"
    estimate = campaign.plans / "stop-estimate.json"
    write_json(
        estimate,
        {
            "note": "required canonical bytes of the frozen campaign (mix01_inventory estimate)",
            "sources": {
                view: {"required_canonical_bytes_base": target["required_canonical_bytes"]}
                for view, target in campaign.config["stop"]["targets"].items()
            },
        },
    )
    write_json(
        acquired,
        {
            "sources": {
                view: {"canonical_bytes": state["counted"]["views"][view]["canonical_bytes"]}
                for view in calibration.VIEWS
            }
        },
    )
    output = campaign.plans / "sufficiency.json"
    parser = mix01_inventory.build_parser()
    args = parser.parse_args(
        [
            "sufficiency",
            "--estimate",
            str(estimate),
            "--acquired",
            str(acquired),
            "--output",
            str(output),
        ]
    )
    if args.func(args) != 0:
        raise bulk.BulkError("mix01_inventory sufficiency refused the cumulative state")
    report: dict[str, Any] = read_json(output)["sources"]
    return report


def cmd_account(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root)
    state, decision = campaign_state(campaign)
    campaign.plans.mkdir(parents=True, exist_ok=True)
    report = repository_sufficiency(campaign, state)
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
    counted = state["counted"]
    print(f"complete batches: {state['complete_batches']} rows: {counted['rows']}")
    for view in calibration.VIEWS:
        entry = decision["views"][view]
        print(
            f"  {view}: {entry['estimated_tokens']:,.0f} estimated tokens, "
            f"{entry['acquired_canonical_bytes']:,} / {entry['required_canonical_bytes']:,} "
            f"canonical bytes -> {entry['status']}"
        )
    print(f"target reached: {decision['target_reached']}; next: {decision['next']}")
    return EXIT_TARGET_REACHED if decision["target_reached"] else 0


def planning_cap(campaign: Campaign) -> int:
    """Output plus staging cap of a full slice before the batch footers are known."""
    limits = campaign.config["limits"]
    return 3 * bulk.ceil_mib(
        int(campaign.config["batch"]["files"])
        * int(limits["planning_group_rows"])
        * int(limits["raw_bytes_per_row_bound"])
    )


def cmd_gate(args: argparse.Namespace) -> int:
    campaign = load_campaign(args.campaign, args.data_root)
    state, decision = campaign_state(campaign)
    if (campaign.batch_dir(args.batch) / "batch.json").is_file():
        slices = current_slices(campaign, args.batch)
        chosen = [e for e in slices if args.slice is None or e["slice"] == args.slice]
        cap = max(
            int(e["limits"]["max_output_disk_bytes"]) + int(e["limits"]["max_temp_disk_bytes"])
            for e in chosen
        )
    else:
        cap = planning_cap(campaign)
    campaign.root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(campaign.root).free if args.free_bytes is None else args.free_bytes
    result = bulk.gate(campaign.config, state, decision, args.batch, free, cap, args.top_up_reason)
    if result["decision"] == "RUN" and args.top_up_reason.strip():
        write_once(
            campaign.batch_dir(args.batch) / "top-up.json",
            {"batch": args.batch, "reason": args.top_up_reason.strip()},
        )
    print(f"gate batch {args.batch}: {result['decision']} {'; '.join(result['reasons'])}")
    return {
        "RUN": 0,
        "STOP_TARGET_REACHED": EXIT_TARGET_REACHED,
        "COMPLETE": EXIT_COMPLETE,
    }.get(result["decision"], EXIT_REFUSED)


def cmd_renew(args: argparse.Namespace) -> int:
    """A fresh attempt for a slice whose deadline expired before completion."""
    campaign = load_campaign(args.campaign, args.data_root)
    directory = campaign.batch_dir(args.batch)
    entry = next(e for e in current_slices(campaign, args.batch) if e["slice"] == args.slice)
    state = journal_state(entry)
    deadline = None if state is None else state.accounting.deadline_at
    now = time.time() if args.now is None else args.now
    if state is None or state.status == "COMPLETED" or deadline is None or now <= deadline:
        raise bulk.BulkError("renewal is only for an unfinished slice whose deadline expired")
    renewed = {
        key: entry[key]
        for key in ("slice", "files", "row_ranges", "rows", "window_scan_rows", "limits")
    }
    renewed["attempt"] = int(entry["attempt"]) + 1
    name = slice_name(int(renewed["slice"]), int(renewed["attempt"]))
    plan = mint_plan(campaign, renewed, directory, directory / f"{name}.dry.plan.json", None)
    renewed.update(plan_hash=plan.plan_hash, plan_id=plan.plan_id)
    record = self_digest(
        {
            "batch": args.batch,
            "slice": renewed["slice"],
            "attempt": renewed["attempt"],
            "plan_hash": plan.plan_hash,
            "plan_id": plan.plan_id,
            "replaces_plan_hash": entry["plan_hash"],
            "reason": "overall deadline expired before completion; prior attempt preserved",
            "expired_deadline_at": deadline,
            "authorization_digest": bulk.authorization_digest(
                campaign.config["digest"], args.batch, [renewed]
            ),
        }
    )
    write_once(directory / f"renew-{name}.json", record)
    print(f"renewed {name}; AUTHORIZATION DIGEST: {record['authorization_digest']}")
    return 0


# -------------------------------------------------------------------- dry run


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Offline determinism proofs over the real frozen inventory."""
    campaign = load_campaign(args.campaign, args.data_root)
    size = int(campaign.config["batch"]["files"])
    first, second = campaign.members(0), campaign.members(1)
    again = load_campaign(args.campaign, args.data_root)
    ceiling = int(campaign.config["ceiling"]["max_batches"])
    order = [entry["file"] for entry in campaign.inventory["files"]]
    seal = read_json(REPO / SEAL_DIR / "calibration-seal.json")
    calibrated = {
        unit["file"]: {
            "inventory_rank": order.index(unit["file"]),
            "batch": order.index(unit["file"]) // size,
            "calibration_rows": unit["row_range"],
        }
        for unit in seal["units"]
    }
    state, decision = campaign_state(campaign)
    within = [name for name, entry in calibrated.items() if entry["batch"] < ceiling]
    write_json(
        args.output,
        {
            "campaign_digest": campaign.config["digest"],
            "inventory_digest": campaign.inventory["inventory_digest"],
            "batch_files": size,
            "batch_0": {"files": first, "membership_digest": canonical.digest(first)},
            "batch_1": {"files": second, "membership_digest": canonical.digest(second)},
            "batch_0_and_1_disjoint": not set(first) & set(second),
            "restart_reproduces_membership": (first, second)
            == (again.members(0), again.members(1)),
            "membership_digest_through_ceiling": canonical.digest(
                [campaign.members(index) for index in range(ceiling)]
            ),
            "files_through_ceiling_distinct": len(
                {name for index in range(ceiling) for name in campaign.members(index)}
            )
            == ceiling * size,
            "calibration_files": calibrated,
            "calibration_files_within_ceiling": within,
            "calibration_policy": campaign.config["excluded_progress"],
            "calibration_measurement_present": (
                campaign.root / "calib/essential-web-production/calibration/measurement.json"
            ).is_file(),
            "campaign_ledger_entries": state["sealed_including_incomplete_batch"]["slices"],
            "campaign_rows_counted": state["counted"]["rows"],
            "stop_decision_before_any_batch": decision,
            "readiness": {
                "selector_frozen": seal["selector"] == selector.selector_identity(),
                "source_revision_ok": seal["source_revision"] == selector.SOURCE_REVISION,
                "production_admission_recorded": all(
                    seal["admission"][view]["admitted"] for view in calibration.VIEWS
                ),
                "calibration_sealed": seal["seal_digest"]
                == campaign.config["calibration_seal_digest"],
                "current_adapter_reproduces_calibration": all(
                    view["reproduced_by_current_adapter"]
                    for unit in seal["units"]
                    for view in unit["views"].values()
                ),
                "inventory_ready": True,
                "campaign_frozen": True,
                "membership_deterministic": (first, second) == (again.members(0), again.members(1)),
                "first_batch_gate": bulk.gate(
                    campaign.config,
                    state,
                    decision,
                    0,
                    shutil.disk_usage(campaign.root).free,
                    planning_cap(campaign),
                )["decision"],
                "batch_0_footers_read": (campaign.batch_dir(0) / "layout.json").is_file(),
                "bulk_fetch_run": state["sealed_including_incomplete_batch"]["slices"] > 0,
                "c05_receipt_present": False,
                "raw_reclamation_permitted_by_contract": False,
            },
            "network": "none",
        },
    )
    print(f"dry run written: {args.output}")
    return 0


def cmd_dry_slices(args: argparse.Namespace) -> int:
    """Real-footer slice plans for the footer-certified files. Not a campaign batch."""
    campaign = load_campaign(args.campaign, args.data_root)
    footers = read_json(REPO / BULK_DIR / "footer-layouts.json")["files"]
    order = [entry["file"] for entry in campaign.inventory["files"]]
    files = sorted((footer["file"] for footer in footers), key=order.index)
    layouts = {footer["file"]: footer for footer in footers}
    slices = bulk.plan_slices(files, layouts, campaign.config["limits"], 1)
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    summary = []
    for entry in slices:
        entry["attempt"] = 1
        name = slice_name(int(entry["slice"]), 1)
        plan = mint_plan(campaign, entry, out, out / f"{name}.dry.plan.json", None)
        try:
            validate_plan_authorization(plan, catalog_source_approved=True)
            refused = False
        except AuthorizationRequiredError:
            refused = True
        summary.append(
            {
                "slice": entry["slice"],
                "files": len(entry["files"]),
                "rows": entry["rows"],
                "plan_hash": plan.plan_hash,
                "production_scope": plan_requires_production_admission(plan),
                "fetch_refused_without_authorization": refused,
                "modeled": entry["modeled"],
                "limits": entry["limits"],
            }
        )
    write_json(
        out / "dry-slices.json",
        {
            "status": "DRY DEMONSTRATION on the eight footer-certified files; NOT a campaign "
            "batch, NOT authorized, NOT executed",
            "campaign_digest": campaign.config["digest"],
            "files": files,
            "rows": sum(int(entry["rows"]) for entry in slices),
            "slices": summary,
        },
    )
    print(f"dry slices written: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=REPO / BULK_DIR / "bulk-campaign.json")
    parser.add_argument("--data-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    model = sub.add_parser("model", help="Build the measured models and freeze the campaign.")
    model.add_argument("--seal-dir", type=Path, default=REPO / SEAL_DIR)
    model.add_argument("--readiness-dir", type=Path, default=REPO / READY_DIR)
    model.add_argument("--footer-root", type=Path, required=True)
    model.add_argument("--output-dir", type=Path, default=REPO / BULK_DIR)
    model.set_defaults(func=cmd_model)
    for name, func, text in (
        ("show", cmd_show, "Show one batch's deterministic membership (offline)."),
        ("layout", cmd_layout, "NETWORK: read the batch footers only."),
        ("state", cmd_state, "Write the batch state file for the operator driver."),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--batch", type=int, required=True)
        command.set_defaults(func=func)
    plan = sub.add_parser("plan", help="Plan the batch slices from its footers (offline).")
    plan.add_argument("--batch", type=int, required=True)
    plan.add_argument("--workers", type=int, default=1)
    plan.set_defaults(func=cmd_plan)
    authorize = sub.add_parser("authorize", help="Record operator authorization; mint plans.")
    authorize.add_argument("--batch", type=int, required=True)
    authorize.add_argument("--digest", required=True)
    authorize.add_argument("--operator", required=True)
    authorize.set_defaults(func=cmd_authorize)
    gate = sub.add_parser("gate", help="May this batch (or slice) run now?")
    gate.add_argument("--batch", type=int, required=True)
    gate.add_argument("--slice", type=int, default=None)
    gate.add_argument("--top-up-reason", default="")
    gate.add_argument("--free-bytes", type=int, default=None, help=argparse.SUPPRESS)
    gate.set_defaults(func=cmd_gate)
    for name, func, text in (
        ("seal-slice", cmd_seal_slice, "Verify one slice and write its ledger entry."),
        ("renew", cmd_renew, "Fresh attempt for a slice with an expired deadline."),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--batch", type=int, required=True)
        command.add_argument("--slice", type=int, required=True)
        command.set_defaults(func=func)
    sub.choices["renew"].add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    account = sub.add_parser("account", help="Cumulative yield and the stop decision.")
    account.set_defaults(func=cmd_account)
    dry = sub.add_parser("dry-run", help="Offline determinism proofs on the real inventory.")
    dry.add_argument("--output", type=Path, default=REPO / BULK_DIR / "dry-run.json")
    dry.set_defaults(func=cmd_dry_run)
    demo = sub.add_parser("dry-slices", help="Real-footer slice plans (not a campaign batch).")
    demo.add_argument("--output-dir", type=Path, default=REPO / BULK_DIR / "dry-slices")
    demo.set_defaults(func=cmd_dry_slices)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_root is None:
        configured = os.environ.get("XLM_DATA_ROOT", "")
        if not configured:
            print("essential_web_bulk: refused: set XLM_DATA_ROOT or --data-root", file=sys.stderr)
            return EXIT_REFUSED
        args.data_root = Path(configured)
    if args.command != "layout":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        return int(args.func(args))
    except (ValueError, OSError, KeyError, StopIteration, SamplingRefusal, RuntimeError) as exc:
        print(f"essential_web_bulk: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
