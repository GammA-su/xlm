"""Read-only Batch-0/1 state and immutability audit; no source rows are decoded."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import essential_web_fast as driver

from xlm.data.acquisition.plan import load_acquisition_plan, validate_plan_authorization
from xlm.data.acquisition.source_parquet import file_sha256, identity_path
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_recovery as recovery


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    campaign = driver.load_campaign(
        driver.REPO / driver.FAST_DIR / "campaign.json", Path("G:/XLM"), Path("C:/XLM-scratch")
    )
    snapshots: dict[str, Any] = {}
    batches: dict[str, Any] = {}
    for batch in (0, 1):
        record = driver.batch_record(campaign, batch)
        plan = load_acquisition_plan(campaign.batch_dir(batch) / "batch.plan.json")
        auth = driver.read_json(campaign.batch_dir(batch) / "authorization.json")
        if (
            plan.plan_hash != record["plan_hash"]
            or plan.selected_files != record["files"]
            or auth["authorization_digest"] != record["authorization_digest"]
            or record["authorization_digest"]
            != fast.authorization_digest(
                campaign.config["digest"],
                batch,
                record["membership_digest"],
                plan.plan_hash,
                record["limits"],
            )
        ):
            raise RuntimeError("batch authorization identity mismatch")
        validate_plan_authorization(plan, catalog_source_approved=True)
        state = recovery.resume_state(campaign, record)
        raw = [campaign.raw_path(n) for n in record["files"] if campaign.raw_path(n).is_file()]
        canonical_dir = campaign.root / campaign.config["roots"]["canonical"] / f"b{batch:04d}"
        scratch = campaign.scratch(f"b{batch:04d}")
        staging = campaign.staging(batch)
        files = set(campaign.batch_dir(batch).rglob("*")) | set(canonical_dir.rglob("*"))
        files |= set(scratch.rglob("*")) | set(staging.rglob("*"))
        files |= set(raw) | {identity_path(p) for p in raw}
        for path in sorted(files):
            if path.is_file():
                digest, size = file_sha256(path)
                snapshots[str(path)] = {
                    "sha256": digest,
                    "bytes": size,
                    "mtime_ns": path.stat().st_mtime_ns,
                }
        batches[str(batch)] = {
            "total": state["total"],
            "sealed": state["sealed"],
            "rows": sum(r["rows"] for r in state["receipts"]),
            "retained_raw_files": len(raw),
            "scratch_exists": scratch.exists(),
            "scratch_files": sum(p.is_file() for p in scratch.rglob("*")),
            "staging_files": sum(p.is_file() for p in staging.rglob("*")),
            "canonical_files": sum(p.is_file() for p in canonical_dir.rglob("*")),
            "performance_records": len(list(campaign.batch_dir(batch).glob("performance-*.json"))),
            "events_present": (campaign.batch_dir(batch) / "events.jsonl").exists(),
            "plan_hash": plan.plan_hash,
            "authorization_digest": auth["authorization_digest"],
            "authorization_valid": True,
            "membership": record["membership_digest"],
        }
    cumulative, _ = driver.campaign_state(campaign)
    result = {
        "campaign": campaign.config["digest"],
        "batches": batches,
        "cumulative": cumulative["counted"],
        "snapshots": snapshots,
        "seconds": time.monotonic() - started,
    }
    if args.compare is not None:
        before = driver.read_json(args.compare)
        if before["snapshots"] != snapshots:
            raise RuntimeError("operator artifacts changed since the initial audit")
        result["all_operator_artifacts_unchanged"] = True
    driver.write_json(args.output, result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("snapshots", "cumulative")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
