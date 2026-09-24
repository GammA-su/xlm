"""Collect existing review measurements without rerunning tests or benchmarks."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/opus-review"
DEST = ROOT / "docs/implementation/evidence/OPUS55-REVIEW"


def read(name: str) -> Any:
    return json.loads((OUT / name).read_text(encoding="utf-8-sig"))


def main() -> None:
    if ROOT.drive != "G:" or Path.cwd().resolve() != ROOT:
        raise ValueError("review worktree only")
    DEST.mkdir(parents=True, exist_ok=True)
    whole = {
        path.parent.name: read(str(path.relative_to(OUT)))
        for path in sorted(OUT.glob("whole-*/report.json"))
    }
    pipeline = read("pipeline-100k/report.json")
    data = {
        "product_head": "f981464",
        "fixture_only": True,
        "location": read("location.json"),
        "storage": read("storage.json"),
        "methodology": read("methodology.json"),
        "whole": whole,
        "selected": read("selected/report.json"),
        "selected_profile": read("selected-profile/report.json"),
        "cpu_green": read("cpu-green.json"),
        "cpu_corrected": read("cpu-corrected.json"),
        "crashes_original": read("crashes/report.json"),
        "crashes_corrected": read("crashes-corrected/report.json"),
        "crashes_mature": read("crashes-mature/report.json"),
        "selected_crashes": read("selected-crashes/report.json"),
        "orphan_journal_files": [
            {
                "path": str(path.relative_to(OUT)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted((OUT / "crashes-mature").rglob("*.tmp"))
        ],
        "exact_green": read("exact-green.json"),
        "exact_corrected": read("exact-corrected.json"),
        "pipeline_exact": read("pipeline-exact.json"),
        "pipeline_summary": {key: value for key, value in pipeline.items() if key != "stages"},
        "pipeline_stages": [
            {
                key: value
                for key, value in row.items()
                if key not in ("acquisition", "dedup", "report", "manifest")
            }
            for row in pipeline["stages"]
        ],
    }
    assert data["exact_green"] == data["exact_corrected"]
    hashes = {
        row["sha256"]
        for label in ("cpu_green", "cpu_corrected")
        for row in data[label]["serializer"]
    }
    assert len(hashes) == 1
    (DEST / "measurements.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf8")
    (DEST / "pipeline-report.json.gz").write_bytes(
        gzip.compress((OUT / "pipeline-100k/report.json").read_bytes(), mtime=0)
    )
    logs = [
        "focused",
        "tier-a",
        "review-final",
        "skip-reason",
        "regressions-before",
        "equivalence-before",
        "corrected",
        "crashes",
        "crashes-corrected",
        "crashes-mature",
        "selected-crashes",
        "pipeline",
        "pipeline-exact",
        "tooling-mypy",
        "final-static",
    ]
    manifest = {}
    for name in logs:
        source = OUT / f"{name}.log"
        if source.exists():
            raw = source.read_bytes()
            (DEST / f"{name}.log.gz").write_bytes(gzip.compress(raw, mtime=0))
            manifest[source.name] = hashlib.sha256(raw).hexdigest()
    exits = {path.stem: path.read_text(encoding="utf-8-sig").strip() for path in OUT.glob("*.exit")}
    (DEST / "log-manifest.json").write_text(
        json.dumps({"sha256": manifest, "exit_files": exits}, indent=2) + "\n", encoding="utf8"
    )
    tables = [
        "# Review measurement tables",
        "",
        "Decimal MB/s; binary MiB RSS. Single observations.",
        "CPU is seconds. Summed lock waits/fsync durations can exceed wall time.",
        "",
    ]
    for name, rows in whole.items():
        tables += [
            f"## {name}",
            "",
            "| Workers | MB/s | Wall s | CPU s | RSS MiB | Transactions | Journal writes | "
            "fsync calls / s | Journal wait / hold s | Capacity wait s | Budget wait s |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            tables.append(
                f"| {r['workers']} | {r['MBps']:.3f} | {r['wall_seconds']:.3f} | "
                f"{r['cpu_seconds']:.3f} | {r['peak_rss_bytes'] / 1024**2:.1f} | "
                f"{r['journal']['journal_transactions']} | "
                f"{r['journal']['journal_persisted_writes']} | "
                f"{r['fsync']['calls']} / {r['fsync']['seconds']:.3f} | "
                f"{r['journal_lock_wait_seconds']:.3f} / {r['journal_lock_hold_seconds']:.3f} | "
                f"{r['capacity_lock_wait_seconds']:.3f} | {r['budget_lock_wait_seconds']:.3f} |"
            )
        tables.append("")
    tables += [
        "## Selected Parquet",
        "",
        "| Workers | MB/s transferred | CPU s | RSS MiB | "
        "Journal transactions / writes | fsync calls / s |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for r in data["selected"]:
        tables.append(
            f"| {r['workers']} | {r['MBps']:.3f} | {r['cpu_seconds']:.3f} | "
            f"{r['peak_rss_bytes'] / 1024**2:.1f} | "
            f"{r['journal']['journal_transactions']} / "
            f"{r['journal']['journal_persisted_writes']} | "
            f"{r['fsync']['calls']} / {r['fsync']['seconds']:.3f} |"
        )
    tables.append("")
    (DEST / "TABLES.md").write_text("\n".join(tables), encoding="utf8")
    shutil.copyfile(OUT / "methodology.json", DEST / "methodology.json")
    print(DEST)


if __name__ == "__main__":
    main()
