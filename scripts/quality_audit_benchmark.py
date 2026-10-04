"""Bounded AUTHORED throughput benchmark for the Phase-A quality audit.

Generates a deterministic synthetic corpus (no real text) shaped like the Mix-01
canonical files (about 6.9 KB per JSONL row, 5.4 KB of text), runs the real audit at
the requested worker counts and reports measured throughput plus a PROJECTION for the
real input manifest totals. A projection is arithmetic on an authored measurement,
not a measurement of the real corpus or of the operator's disks.

    uv run --offline --locked --no-sync python scripts/quality_audit_benchmark.py \
        --root <scratch-dir> --output <result.json> --target-mib 256 --workers 1 4 8
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import time
from pathlib import Path

from xlm.data.quality.bench import generate
from xlm.data.quality.runner import Limits, run_audit

REAL_DOCUMENTS = 15_097_174
REAL_FILE_BYTES = 104_506_534_003
REAL_CANONICAL_BYTES = 81_859_652_239
MAX_TARGET_MIB = 2048


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-mib", type=int, default=256)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    args = parser.parse_args()
    if not 0 < args.target_mib <= MAX_TARGET_MIB:
        raise SystemExit("--target-mib outside (0, 2048]")
    if args.root.exists():
        raise SystemExit("--root must be a new directory")
    started = time.monotonic()
    manifest = generate(args.root, args.target_mib * 1024**2)
    generation = time.monotonic() - started
    body = json.loads(manifest.read_bytes())
    totals = {
        "documents": sum(f["documents"] for f in body["files"]),
        "file_bytes": sum(f["file_bytes"] for f in body["files"]),
        "canonical_bytes": sum(f["canonical_bytes"] for f in body["files"]),
    }
    runs = []
    digests = set()
    for workers in args.workers:
        output = args.root / f"audit-w{workers}"
        limits = Limits(
            workers=workers,
            max_rss_bytes=16 * 1024**3,
            free_reserve_bytes=1024**3,
            max_output_bytes=2 * 1024**3,
            line_ceiling=64 * 1024**2,
            deadline_seconds=6 * 3600,
        )
        result = run_audit(manifest, output, limits=limits, progress_interval=None)
        scan = result["scan"]
        digests.add(result["result_digest"])
        seconds = scan["scan_seconds"]
        runs.append(
            {
                "workers": workers,
                "scan_seconds": seconds,
                "wall_seconds": result["wall_seconds"],
                "file_mb_per_s": scan["file_mb_per_s"],
                "documents_per_s": scan["documents_per_s"],
                "peak_process_tree_rss_gib": round(scan["peak_process_tree_rss_bytes"] / 2**30, 3),
                "PROJECTION_real_scan_hours_by_bytes": round(
                    REAL_FILE_BYTES / (scan["file_mb_per_s"] * 1e6) / 3600, 2
                ),
                "PROJECTION_real_scan_hours_by_documents": round(
                    REAL_DOCUMENTS / scan["documents_per_s"] / 3600, 2
                ),
            }
        )
        shutil.rmtree(output / "units")
    report = {
        "kind": "xlm_quality_audit_benchmark_v1",
        "label": "AUTHORED SYNTHETIC CORPUS MEASUREMENT + PROJECTION (not a real-corpus run)",
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
        },
        "corpus": {
            **totals,
            "files": len(body["files"]),
            "generation_seconds": round(generation, 1),
        },
        "mean_jsonl_row_bytes": round(totals["file_bytes"] / totals["documents"], 1),
        "real_target": {
            "documents": REAL_DOCUMENTS,
            "file_bytes": REAL_FILE_BYTES,
            "canonical_bytes": REAL_CANONICAL_BYTES,
            "mean_jsonl_row_bytes": round(REAL_FILE_BYTES / REAL_DOCUMENTS, 1),
        },
        "worker_independent_result_digest": sorted(digests),
        "runs": runs,
        "projection_caveats": [
            "authored text mix, not the real detector-cost mix",
            "corpus generated on this machine; the real corpus is read from the operator's G: SSD",
            "projection excludes overlay membership streaming and final aggregation",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(report, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
