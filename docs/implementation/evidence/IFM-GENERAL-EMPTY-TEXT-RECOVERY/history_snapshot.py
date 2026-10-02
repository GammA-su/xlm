"""OFFLINE, read-only: IFM General p01/p02 history, retained sources and sealed unit.

Reads the operator data root (default G:/XLM) and scratch root (default
C:/XLM-scratch); writes JSON to stdout. Every file is hashed in full, streaming
(large Parquet and JSONL files included). No network, no corpus text, no
writes under either root.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/history_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "G:/XLM")
SCRATCH = Path(sys.argv[2] if len(sys.argv) > 2 else "C:/XLM-scratch")
RUN_FILES = (
    "plan.json",
    "authorization.json",
    "acquisition.plan.json",
    "events.jsonl",
    "performance-00.json",
)
CHUNK = 8 * 1024 * 1024


def stat_of(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "present": False}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    stat = path.stat()
    return {
        "path": str(path),
        "present": True,
        "bytes": size,
        "sha256": digest.hexdigest(),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def tree(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for item in sorted(path.rglob("*")):
        relative = str(item.relative_to(path)).replace("\\", "/")
        if item.is_dir():
            entries.append({"path": relative, "dir": True})
        else:
            record = stat_of(item)
            entries.append(
                {
                    "path": relative,
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                    "mtime_utc": record["mtime_utc"],
                }
            )
    return entries


def main() -> None:
    general = DATA / "plans" / "ifm_general"
    planning = DATA / "plans" / "ifm_planning"
    report: dict[str, Any] = {
        "general_p01": {name: stat_of(general / "p01" / name) for name in RUN_FILES},
        "general_p02": {name: stat_of(general / "p02" / name) for name in RUN_FILES},
        "general_plan_dirs": sorted(p.name for p in general.iterdir() if p.is_dir()),
        "general_sufficiency": stat_of(general / "sufficiency.json"),
        "general_transport_policy": stat_of(general / "transport-policy.json"),
        "planning_p01": {name: stat_of(planning / "p01" / name) for name in RUN_FILES},
        "planning_p02": {name: stat_of(planning / "p02" / name) for name in RUN_FILES},
        "planning_plan_dirs": sorted(p.name for p in planning.iterdir() if p.is_dir()),
        "planning_transport_policy": stat_of(planning / "transport-policy.json"),
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
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
