"""Package observed P32 results; refuse to certify missing or failed gate legs."""

from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-recovery"
DEST = ROOT / "docs/implementation/evidence/P32-RECOVERY"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def summarize(path: Path) -> dict[str, Any]:
    evidence = read(path)
    reports = evidence["reports"]
    passed = {r["nodeid"] for r in reports if r["phase"] == "call" and r["outcome"] == "passed"}
    skipped = {r["nodeid"] for r in reports if r["outcome"] == "skipped"}
    failed = {r["nodeid"] for r in reports if r["outcome"] == "failed"}
    call_passed = len(passed)
    passed -= skipped | failed  # A passed body followed by a worker crash is not a pass.
    worker_lists = list(evidence["worker_collections"].values())
    selected = set(worker_lists[0] if worker_lists else evidence["selected"])
    missing = selected - passed - skipped - failed
    assert not missing, (path.name, "missing test outcomes", missing)
    return {
        "exit": evidence["exit_code"],
        "passed": len(passed),
        "call_passed": call_passed,
        "skipped": sorted(skipped),
        "failed": sorted(failed),
        "missing": sorted(missing),
        "selected": sorted(selected),
        "wall_seconds": evidence["wall_seconds"],
        "peak_tree_rss_bytes": evidence["peak_tree_rss_bytes"],
        "sampled_cpu_seconds_lower_bound": evidence["sampled_cpu_seconds_lower_bound"],
        "argv": evidence["argv"],
    }


def main() -> None:
    if ROOT != Path("G:/Project/xlm-p32-recovery") or Path.cwd().resolve() != ROOT:
        raise ValueError("P32 worktree only")
    DEST.mkdir(parents=True, exist_ok=True)
    matrices = {}
    for name, expected in (("early", 44), ("mature", 44), ("selected", 20), ("parallel", 4)):
        directory = OUT / f"crashes-{name}"
        rows = read(directory / "report.json")
        assert len(rows) == expected
        assert all(
            row["death_exit"] == 73 and row["restart_exit"] == 0 and row["valid_output_exists"]
            for row in rows
        )
        assert not list(directory.rglob("*.tmp"))
        matrices[name] = rows
    exact_before, exact_after = read(OUT / "exact-baseline.json"), read(OUT / "exact-current.json")
    assert exact_before == exact_after and len(exact_after) == 8
    legs = {
        name: summarize(OUT / "gate" / f"{name}.json")
        for name in ("tier-a", "core", "exclusive", "heavy", "scale", "optional")
    }
    failed_gate = any(row["exit"] or row["failed"] or row["missing"] for row in legs.values())
    selected_sets = [set(row["selected"]) for row in legs.values()]
    assert sum(map(len, selected_sets)) == len(set().union(*selected_sets)), "gate overlap"
    report = {
        "starting_head": "26c1238bb015494fa8646efe6ec3277bc3add0f2",
        "recorded_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "product_commits": ["3864646", "522cffa", "7321bd6"],
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pyarrow", "tokenizers", "torch", "pytest", "psutil")
            },
        },
        "focused": summarize(OUT / "focused.json"),
        "focused_resource_limitation": "psutil mock stopped sampler; incomplete measurements",
        "gates": legs,
        "full_gate_passed": not failed_gate,
        "total_passed": sum(row["passed"] for row in legs.values()),
        "total_skipped": sum(len(row["skipped"]) for row in legs.values()),
        "matrices": matrices,
        "exact_baseline": exact_before,
        "exact_current": exact_after,
        "whole_baseline": read(OUT / "whole-baseline/report.json"),
        "whole_current": read(OUT / "whole-current/report.json"),
        "location": read(OUT / "location.json"),
        "gate_runs": {name: read(OUT / "gate" / f"{name}.run.json") for name in legs},
    }
    (DEST / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    tables = [
        "# P32 observed results",
        "",
        "## Six-leg offline gate",
        "",
        "Skips are unavailable capabilities, not passes. RSS is sampled and sums processes.",
        "Pass counts exclude any node with a later failure, including a teardown worker crash.",
        "",
        "| Leg | Pass | Skip | Fail | Runner seconds | Peak tree GiB |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, leg in legs.items():
        run = report["gate_runs"][name]
        tables.append(
            f"| {name} | {leg['passed']} | {len(leg['skipped'])} | {len(leg['failed'])} | "
            f"{run['wall_seconds']:.3f} | {run['peak_tree_rss_bytes'] / 1024**3:.3f} |"
        )
    tables += [
        "",
        f"Total: {report['total_passed']} passed / {report['total_skipped']} skipped.",
        f"Aggregate accepted: {not failed_gate}. Failed nodes: "
        f"{sum(len(leg['failed']) for leg in legs.values())}.",
        "",
        "## Recovery matrices",
        "",
        "| Matrix | Successful restarts | Cases |",
        "|---|---:|---:|",
    ]
    for name, rows in matrices.items():
        tables.append(f"| {name} | {sum(row['restart_exit'] == 0 for row in rows)} | {len(rows)} |")
    tables += [
        "",
        "## Fixed 1 GiB acquisition fixture",
        "",
        "Decimal MB/s; binary MiB RSS; summed fsync/lock seconds can exceed wall time.",
        "",
        "| Version | Workers | MB/s | Wall s | CPU s | RSS MiB | Tx / writes | fsync calls / s | "
        "Journal wait / hold s | Capacity wait s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("baseline", "current"):
        for row in report[f"whole_{label}"]:
            tables.append(
                f"| {label} | {row['workers']} | {row['MBps']:.3f} | "
                f"{row['wall_seconds']:.3f} | {row['cpu_seconds']:.3f} | "
                f"{row['peak_rss_bytes'] / 1024**2:.1f} | "
                f"{row['journal']['journal_transactions']} / "
                f"{row['journal']['journal_persisted_writes']} | "
                f"{row['fsync']['calls']} / {row['fsync']['seconds']:.3f} | "
                f"{row['journal_lock_wait_seconds']:.3f} / "
                f"{row['journal_lock_hold_seconds']:.3f} | "
                f"{row['capacity_lock_wait_seconds']:.3f} |"
            )
    (DEST / "TABLES.md").write_text("\n".join(tables) + "\n", encoding="utf8")
    manifest = {}
    for directory in (OUT, OUT / "gate", OUT / "gate-before-bootstrap"):
        for pattern in ("*.log", "*.exit", "*.json", "INTERRUPTED.txt", "reproduce_bootstrap.py"):
            for source in sorted(directory.glob(pattern)):
                relative = str(source.relative_to(OUT)).replace("\\", "-").replace("/", "-")
                payload = source.read_bytes()
                (DEST / (relative + ".gz")).write_bytes(gzip.compress(payload, mtime=0))
                manifest[relative] = hashlib.sha256(payload).hexdigest()
    (DEST / "sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps(
            {
                "full_gate_passed": not failed_gate,
                "passed": report["total_passed"],
                "skipped": report["total_skipped"],
            }
        )
    )


if __name__ == "__main__":
    main()
