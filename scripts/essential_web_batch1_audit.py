"""Read-only Batch-1 failure audit: unit states, restart classes and an immutability snapshot.

No source row is decoded and no document text is read into the report: sealed
units are verified by hash, retained sources are rehashed, scratch checkpoints
are verified against their fsynced prefix hash, and staging is listed by name,
size and modification time only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import essential_web_fast as driver

from xlm.data.acquisition.plan import load_acquisition_plan, validate_plan_authorization
from xlm.data.acquisition.source_parquet import (
    READ_BYTES,
    file_sha256,
    identity_path,
    load_durable_source,
)
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_recovery as recovery


def prefix_sha256(path: Path, length: int) -> str:
    digest, left = hashlib.sha256(), length
    with path.open("rb") as stream:
        while left:
            chunk = stream.read(min(READ_BYTES, left))
            if not chunk:
                break
            digest.update(chunk)
            left -= len(chunk)
    return digest.hexdigest()


def stamp(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {
        "bytes": info.st_size,
        "mtime": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
    }


def scratch_unit(campaign: Any, plan: Any, batch: int, name: str, key: str) -> dict[str, Any]:
    """Verified scratch state of one non-sealed unit, exactly as a restart would judge it."""
    directory = campaign.scratch(f"b{batch:04d}")
    partial, state_path = directory / f"{key}.parquet.part", directory / f"{key}.state.json"
    if not state_path.is_file():
        return {
            "state": None,
            "partial_bytes": partial.stat().st_size if partial.is_file() else None,
        }
    state = driver.read_json(state_path)
    size = partial.stat().st_size if partial.is_file() else 0
    verified = int(state.get("verified_bytes", 0))
    result: dict[str, Any] = {
        "state": {
            k: state.get(k)
            for k in ("complete", "length", "verified_bytes", "charged_bytes", "requests", "etag")
        },
        "identity_matches_plan": state.get("name") == name
        and state.get("url") == driver.source_url(plan, name),
        "partial_bytes": size,
        "unverified_tail_bytes": max(0, size - verified),
    }
    if state.get("complete"):
        sha, length = file_sha256(partial)
        result["complete_rehash_ok"] = (sha, length) == (state.get("sha256"), state.get("length"))
    elif verified:
        result["prefix_rehash_ok"] = size >= verified and prefix_sha256(
            partial, verified
        ) == state.get("prefix_sha256")
    return result


def classify(durable: dict[str, Any] | None, scratch: dict[str, Any]) -> str:
    state = scratch["state"]
    if durable is not None:
        return "READY_FROM_DURABLE_RAW"
    if state is not None and state["complete"] and scratch.get("complete_rehash_ok"):
        return "READY_FROM_SCRATCH_COMPLETE"
    if state is not None and state["verified_bytes"] and scratch.get("prefix_rehash_ok"):
        return "RESUMABLE_PARTIAL"
    if state is not None or scratch["partial_bytes"] is not None:
        return "MUST_DOWNLOAD"
    return "UNTOUCHED"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--batch", type=int, default=1)
    args = parser.parse_args()
    started = time.monotonic()
    campaign = driver.load_campaign(
        driver.REPO / driver.FAST_DIR / "campaign.json", Path("G:/XLM"), Path("C:/XLM-scratch")
    )
    record = driver.batch_record(campaign, args.batch)
    directory = campaign.batch_dir(args.batch)
    plan = load_acquisition_plan(directory / "batch.plan.json")
    auth = driver.read_json(directory / "authorization.json")
    if (
        plan.plan_hash != record["plan_hash"]
        or plan.selected_files != record["files"]
        or auth["authorization_digest"] != record["authorization_digest"]
        or record["authorization_digest"]
        != fast.authorization_digest(
            campaign.config["digest"],
            args.batch,
            record["membership_digest"],
            plan.plan_hash,
            record["limits"],
        )
    ):
        raise RuntimeError("batch authorization identity mismatch")
    validate_plan_authorization(plan, catalog_source_approved=True)
    resume = recovery.resume_state(campaign, record)
    sealed = {f"f{int(r['inventory_rank']):05d}": r for r in resume["receipts"]}
    units: dict[str, Any] = {}
    for position, name in enumerate(record["files"]):
        rank = campaign.rank(args.batch, position)
        key = f"f{rank:05d}"
        if key in sealed:
            receipt = sealed[key]
            units[key] = {
                "file": name,
                "class": "SEALED",
                "rows": receipt["rows"],
                "receipt_digest": receipt["digest"],
                "raw_sha256": receipt["raw"]["sha256"],
            }
            continue
        durable = load_durable_source(campaign.raw_path(name))
        scratch = scratch_unit(campaign, plan, args.batch, name, key)
        units[key] = {
            "file": name,
            "class": classify(durable, scratch),
            "durable_raw": None
            if durable is None
            else {"sha256": durable["sha256"], "length": durable["length"], "rehash_ok": True},
            "scratch": scratch,
            "canonical_unit_exists": campaign.unit_dir(args.batch, rank).exists(),
        }
    staging = campaign.staging(args.batch)
    listing = {
        path.relative_to(staging).as_posix(): stamp(path)
        for path in sorted(staging.rglob("*"))
        if path.is_file()
    }
    progress = {
        path.name: json.loads(path.read_bytes()) for path in sorted(staging.glob("*.progress.json"))
    }
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    snapshots: dict[str, Any] = {}
    for batch in (0, args.batch):
        batch_record = driver.batch_record(campaign, batch)
        canonical_dir = campaign.root / campaign.config["roots"]["canonical"] / f"b{batch:04d}"
        raw = [campaign.raw_path(n) for n in batch_record["files"]]
        files = set(campaign.batch_dir(batch).rglob("*")) | set(canonical_dir.rglob("*"))
        files |= set(campaign.scratch(f"b{batch:04d}").rglob("*"))
        files |= set(campaign.staging(batch).rglob("*"))
        files |= {p for p in raw if p.is_file()} | {identity_path(p) for p in raw}
        for path in sorted(files):
            if path.is_file():
                digest, size = file_sha256(path)
                snapshots[str(path)] = {
                    "sha256": digest,
                    "bytes": size,
                    "mtime_ns": path.stat().st_mtime_ns,
                }
    classes: dict[str, int] = {}
    for unit in units.values():
        classes[unit["class"]] = classes.get(unit["class"], 0) + 1
    result = {
        "campaign": campaign.config["digest"],
        "batch": args.batch,
        "membership": record["membership_digest"],
        "plan_hash": plan.plan_hash,
        "authorization_digest": auth["authorization_digest"],
        "authorization_valid": True,
        "total": resume["total"],
        "sealed": resume["sealed"],
        "sealed_rows": sum(r["rows"] for r in resume["receipts"]),
        "completion_percent": resume["completion_percent"],
        "classes": dict(sorted(classes.items())),
        "units": units,
        "staging_listing": listing,
        "staging_progress_snapshots": progress,
        "events": events,
        "performance_records": sorted(p.name for p in directory.glob("performance-*.json")),
        "snapshots": snapshots,
        "seconds": time.monotonic() - started,
    }
    if args.compare is not None:
        before = driver.read_json(args.compare)
        changed = sorted(
            path
            for path in set(before["snapshots"]) | set(snapshots)
            if before["snapshots"].get(path) != snapshots.get(path)
        )
        result["changed_since_compare"] = changed
        result["all_operator_artifacts_unchanged"] = not changed
    driver.write_json(args.output, result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("snapshots", "units", "events", "staging_listing")
            },
            indent=2,
        )
    )
    for key, unit in units.items():
        print(f"{key} {unit['class']:28} {unit['file']}")


if __name__ == "__main__":
    main()
