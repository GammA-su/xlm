"""Validate and package existing closeout observations; never rerun a measurement."""

from __future__ import annotations

import gzip
import hashlib
import json
import statistics
import subprocess
from pathlib import Path
from typing import Any

import p32_heavy_evidence as tests
from p32_gate import LEGS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-perf-closeout"
DEST = ROOT / "docs/implementation/evidence/P32-PERF-CLOSEOUT"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    if Path.cwd().resolve() != Path("G:/Project/xlm-p32-perf-closeout"):
        raise ValueError("Closeout worktree only")
    tests.OUT = OUT
    outcomes = {
        name: tests.summary(name) for name in ("focused", "related", *[f"gate-{n}" for n in LEGS])
    }
    gate = [outcomes[f"gate-{n}"] for n in LEGS]
    selected = [{node.rsplit("@p30b-", 1)[0] for node in leg["selected"]} for leg in gate]
    assert sum(map(len, selected)) == len(set().union(*selected)), "overlapping gate selections"
    reference = read(ROOT / "docs/implementation/evidence/P32-HEAVY-CRASH/results.json")
    reference_nodes = {
        node.rsplit("@p30b-", 1)[0]
        for name in LEGS
        for node in reference["outcomes"][f"gate-{name}"]["selected"]
    }
    current_nodes = set().union(*selected)
    assert reference_nodes <= current_nodes, "certified acceptance node omitted"
    added_nodes = sorted(current_nodes - reference_nodes)
    assert len(added_nodes) == 13 and all(
        node.startswith("tests/test_p32_publication.py::") for node in added_nodes
    ), "unexpected acceptance selection change"
    assert all(not (r["exit"] or r["failed"] or r["missing"]) for r in outcomes.values())
    assert read(OUT / "exact-comparison.json") == {"equal": True, "cases": 8}
    assert read(OUT / "pipeline-comparison.json")["decisions_metrics_and_targets_equal"]
    crashes = {}
    for name, count in (("early", 44), ("mature", 44), ("selected", 20), ("parallel", 4)):
        rows = read(OUT / f"crashes-{name}/report.json")
        assert len(rows) == count and all(row["restart_exit"] == 0 for row in rows)
        assert not list((OUT / f"crashes-{name}").rglob("*.tmp"))
        crashes[name] = count
    measurements = {
        f"{mode}-{label}": read(OUT / f"{mode}-{label}/report.json")
        for mode in ("whole", "profile")
        for label in ("baseline", "current")
    }
    medians = {
        str(w): {
            label: statistics.median(
                r["MBps"] for r in measurements[f"whole-{label}"] if r["workers"] == w
            )
            for label in ("baseline", "current")
        }
        for w in (1, 8, 16)
    }
    result = {
        "environment": read(OUT / "environment.json"),
        "product_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "outcomes": outcomes,
        "runs": {path.stem: read(path) for path in OUT.glob("*.run.json")},
        "crashes": crashes,
        "measurements": measurements,
        "throughput_medians_MBps": medians,
        "pipeline": read(OUT / "pipeline-100k/report.json"),
        "pipeline_comparison": read(OUT / "pipeline-comparison.json"),
        "full_gate_passed": True,
        "total_passed": sum(r["passed"] for r in gate),
        "total_skipped": sum(len(r["skipped"]) for r in gate),
        "total_failed": 0,
        "selected_count": sum(map(len, selected)),
        "reference_nodes_preserved": len(reference_nodes),
        "added_nodes": added_nodes,
        "artifact_bytes_at_collection": sum(
            p.stat().st_size for p in OUT.rglob("*") if p.is_file()
        ),
    }
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    sources = [p for p in OUT.iterdir() if p.is_file() and p.suffix in (".json", ".log", ".exit")]
    sources += [
        OUT / f"{mode}-{label}/report.json"
        for mode in ("whole", "profile")
        for label in ("baseline", "current")
    ]
    sources += [OUT / f"crashes-{name}/report.json" for name in crashes]
    sources += [OUT / "pipeline-100k/report.json", OUT / "reference-100k/report.json"]
    hashes = {}
    for source in sorted(sources):
        relative = source.relative_to(OUT).as_posix()
        payload = source.read_bytes()
        (DEST / (relative.replace("/", "__") + ".gz")).write_bytes(gzip.compress(payload, mtime=0))
        hashes[relative] = hashlib.sha256(payload).hexdigest()
    (DEST / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    table = [
        "# P32 performance closeout observations",
        "",
        "All repetitions retained. MB/s uses decimal megabytes. Fsync seconds sum threads.",
        "",
        "| State | Rep | Workers | MB/s | Wall s | CPU s | Fsync s | Peak RSS MiB |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("baseline", "current"):
        for r in measurements[f"whole-{label}"]:
            table.append(
                f"| {label} | {r['repetition']} | {r['workers']} | {r['MBps']:.3f} | "
                f"{r['wall_seconds']:.3f} | {r['cpu_seconds']:.3f} | {r['fsync']['seconds']:.3f} | "
                f"{r['peak_rss_bytes'] / 1024**2:.1f} |"
            )
    table += [
        "",
        "Profile timings are inclusive sums across threads, cover fetcher construction/run,",
        "and overlap. Profiling overhead is substantial; use the unprofiled table for throughput.",
        "",
        "| State | Workers | Component | Calls | Seconds |",
        "|---|---:|---|---:|---:|",
    ]
    for label in ("baseline", "current"):
        for r in measurements[f"profile-{label}"]:
            for name, value in r["profile"].items():
                table.append(
                    f"| {label} | {r['workers']} | {name} | {value['calls']} | "
                    f"{value['seconds']:.6f} |"
                )
    table += [
        "",
        "| Gate | Passed | Skipped | Failed | Exit | Seconds |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, r in outcomes.items():
        table.append(
            f"| {name} | {r['passed']} | {len(r['skipped'])} | {len(r['failed'])} | "
            f"{r['exit']} | {r['pytest_seconds']:.3f} |"
        )
    (DEST / "TABLES.md").write_text("\n".join(table) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "full_gate_passed",
                    "total_passed",
                    "total_skipped",
                    "total_failed",
                    "selected_count",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
