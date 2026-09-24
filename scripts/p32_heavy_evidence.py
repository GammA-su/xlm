"""Package P32 heavy-crash evidence without altering any recorded test outcome."""

from __future__ import annotations

import gzip
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

from p32_gate import LEGS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-heavy-crash"
DEST = ROOT / "docs/implementation/evidence/P32-HEAVY-CRASH"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def summary(name: str) -> dict[str, Any]:
    data = read(OUT / f"{name}.json")
    collections = list(data["worker_collections"].values())
    selected = set(collections[0] if collections else data["selected"])
    assert selected
    assert all(set(nodes) == selected for nodes in collections)
    rows = data["reports"]
    failed = {row["nodeid"] for row in rows if row["outcome"] == "failed"}
    skipped = {row["nodeid"] for row in rows if row["outcome"] == "skipped"}
    passed = {
        row["nodeid"] for row in rows if row["phase"] == "call" and row["outcome"] == "passed"
    }
    passed -= failed | skipped
    missing = selected - passed - failed - skipped
    return {
        "exit": data["exit_code"],
        "passed": len(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "missing": sorted(missing),
        "selected": sorted(selected),
        "pytest_seconds": data["wall_seconds"],
    }


def main() -> None:
    if Path.cwd().resolve() != Path("G:/Project/xlm-p32-heavy-crash"):
        raise ValueError("Heavy-crash worktree only")
    DEST.mkdir(parents=True, exist_ok=True)
    names = [
        "before-serial",
        "before-group",
        *[f"stress-{i}" for i in range(1, 6)],
        "heavy",
        *[f"gate-{name}" for name in LEGS],
    ]
    outcomes = {name: summary(name) for name in names}
    gate = {name: outcomes[f"gate-{name}"] for name in LEGS}
    selected = [{node.rsplit("@p30b-", 1)[0] for node in leg["selected"]} for leg in gate.values()]
    assert sum(map(len, selected)) == len(set().union(*selected)), "overlapping gate selections"
    accepted = all(not (leg["exit"] or leg["failed"] or leg["missing"]) for leg in gate.values())
    result = {
        "starting_head": "fa4ff5009b0eb4a1c28b45576cc8e7dc9ab1b020",
        "recorded_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "outcomes": outcomes,
        "runs": {name: read(OUT / f"{name}.run.json") for name in names},
        "focused": summary("focused"),
        "native": read(OUT / "native-watchdog-results.json"),
        "control": read(OUT / "control-watchdog-results.json"),
        "replacement": read(OUT / "safe-watchdog-results.json"),
        "native_exception": read(OUT / "native-summary.json"),
        "original_dump_sha256": hashlib.sha256(
            (OUT / "original-python-36048.dmp").read_bytes()
        ).hexdigest(),
        "full_gate_passed": accepted,
        "total_passed": sum(leg["passed"] for leg in gate.values()),
        "total_skipped": sum(len(leg["skipped"]) for leg in gate.values()),
        "total_failed": sum(len(leg["failed"]) for leg in gate.values()),
        "selected_count": sum(map(len, selected)),
    }
    (DEST / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    table = [
        "# P32 heavy-crash validation",
        "",
        "| Run | Passed | Skipped | Failed | Exit | Runner s | Peak tree GiB |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in outcomes.items():
        run = result["runs"][name]
        table.append(
            f"| {name} | {row['passed']} | {len(row['skipped'])} | {len(row['failed'])} | "
            f"{row['exit']} | {run['wall_seconds']:.3f} | "
            f"{run['peak_tree_rss_bytes'] / 1024**3:.3f} |"
        )
    table += [
        "",
        f"Full gate accepted: **{accepted}**. {result['total_passed']} passed / "
        f"{result['total_skipped']} skipped / {result['total_failed']} failed; "
        f"{result['selected_count']} distinct selected nodes.",
        "",
        "Skips are unavailable capabilities, not passes. RSS is sampled process-tree RSS.",
    ]
    (DEST / "TABLES.md").write_text("\n".join(table) + "\n")
    hashes = {}
    for pattern in ("*.log", "*.json", "*.exit", "inspect_dump.py", "reproduce_watchdog.py"):
        for source in sorted(OUT.glob(pattern)):
            payload = source.read_bytes()
            (DEST / (source.name + ".gz")).write_bytes(gzip.compress(payload, mtime=0))
            hashes[source.name] = hashlib.sha256(payload).hexdigest()
    (DEST / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("full_gate_passed", "total_passed", "total_skipped", "total_failed")
            }
        )
    )


if __name__ == "__main__":
    main()
