"""OFFLINE, read-only: IFM p01 history hashes and selected-file inventory facts.

Reads the operator data root (default G:/XLM) and scratch root (default
C:/XLM-scratch); writes JSON to stdout. No network, no corpus text, no writes
under either root.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-PRODUCTION-BOUND-RECOVERY/history_and_inventory.py
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "G:/XLM")
SCRATCH = Path(sys.argv[2] if len(sys.argv) > 2 else "C:/XLM-scratch")
P01_FILES = (
    "plan.json",
    "authorization.json",
    "acquisition.plan.json",
    "events.jsonl",
    "performance-00.json",
)


def stat_of(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "present": False}
    data = path.read_bytes()
    return {
        "path": str(path),
        "present": True,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
    }


def tree(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def view_facts(key: str) -> dict[str, Any]:
    inventory = json.loads((DATA / "inventories" / f"{key}.inventory.json").read_bytes())
    plan = json.loads((DATA / "plans" / key / "p01" / "plan.json").read_bytes())
    sizes = [int(e["size_bytes"]) for e in inventory["files"]]
    by_name = {e["file"]: int(e["size_bytes"]) for e in inventory["files"]}
    ordered = sorted(sizes)
    calibration_bytes = int(plan["inputs"]["layout"]["file_bytes_measured"])
    max_file = int(plan["limits"]["max_file_bytes"])
    selected = []
    for entry in plan["selection"]["files"]:
        size = by_name[entry["file"]]
        selected.append(
            {
                "rank": entry["rank"],
                "file": entry["file"],
                "declared_bytes": size,
                "fits_p01_max_file_bytes": size <= max_file,
                "x_calibration_file": round(size / calibration_bytes, 4),
                "x_inventory_median": round(size / statistics.median(sizes), 4),
                "x_inventory_mean": round(size / statistics.mean(sizes), 4),
                "x_inventory_max": round(size / max(sizes), 4),
                "size_percentile_rank": round(
                    100.0 * sum(1 for s in sizes if s <= size) / len(sizes), 2
                ),
            }
        )
    calib_pct = round(100.0 * sum(1 for s in sizes if s <= calibration_bytes) / len(sizes), 2)
    return {
        "inventory": {
            "path": str(DATA / "inventories" / f"{key}.inventory.json"),
            "sha256": hashlib.sha256(
                (DATA / "inventories" / f"{key}.inventory.json").read_bytes()
            ).hexdigest(),
            "digest": inventory["inventory_digest"],
            "file_count": len(sizes),
            "unknown_sizes": sum(1 for e in inventory["files"] if e["size_bytes"] is None),
            "total_bytes": sum(sizes),
            "min_bytes": ordered[0],
            "p10_bytes": ordered[len(ordered) // 10],
            "median_bytes": statistics.median(sizes),
            "mean_bytes": round(statistics.mean(sizes), 1),
            "p90_bytes": ordered[(9 * len(ordered)) // 10],
            "max_bytes": ordered[-1],
            "files_above_p01_max_file_bytes": sum(1 for s in sizes if s > max_file),
        },
        "calibration_file_bytes": calibration_bytes,
        "calibration_file_size_percentile_rank": calib_pct,
        "calibration_x_median": round(calibration_bytes / statistics.median(sizes), 4),
        "p01_plan_digest": plan["digest"],
        "p01_max_file_bytes": max_file,
        "p01_selected": selected,
    }


def main() -> None:
    general = DATA / "plans" / "ifm_general"
    report: dict[str, Any] = {
        "general_p01": {name: stat_of(general / "p01" / name) for name in P01_FILES},
        "general_sufficiency": stat_of(general / "sufficiency.json"),
        "general_transport_policy": stat_of(general / "transport-policy.json"),
        "planning_p01": {
            name: stat_of(DATA / "plans" / "ifm_planning" / "p01" / name) for name in P01_FILES
        },
        "planning_transport_policy": stat_of(
            DATA / "plans" / "ifm_planning" / "transport-policy.json"
        ),
        "general_state": {
            "canonical_tree": tree(DATA / "canonical" / "ifm_general"),
            "raw_tree": tree(DATA / "acq-raw" / "ifm_general"),
            "scratch_tree": tree(SCRATCH / "ifm_general"),
        },
        "planning_state": {
            "canonical_tree": tree(DATA / "canonical" / "ifm_planning"),
            "raw_tree": tree(DATA / "acq-raw" / "ifm_planning"),
            "scratch_tree": tree(SCRATCH / "ifm_planning"),
        },
        "general": view_facts("ifm_general"),
        "planning": view_facts("ifm_planning"),
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
