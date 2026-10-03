"""Steady-state source-worker scaling for the C06 fast path (authored, bounded).

Repeats the same generated plan files ``--repeat`` times per run so one-time Windows
process spawn is amortized; files are served from the OS cache, so this measures CPU
throughput of hashing + strict kept-row parsing, not SATA I/O.

    python -m scripts.c06_fast_scaling --root <new dir> --output <json>
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from scripts.c06_fast_benchmark import Peak, make_sources, source_tasks

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.fitscan import MembershipTables, OrderedPool, scan_source_file
from xlm.data.exclusion.tokenizer_fit import RANK_TAG


def run(entries: list[dict[str, Any]], workers: int, repeat: int, parse: bool) -> dict[str, Any]:
    tables = MembershipTables({}, (), 0, 0, 64 * 1024**2, RANK_TAG)
    size = sum(e["bytes"] for e in entries) * repeat
    tasks = [t for _ in range(repeat) for t in source_tasks(entries, parse)]
    with OrderedPool(workers, tables) as pool:
        for _ in pool.map(scan_source_file, tasks[:workers]):  # Spawn every worker first.
            pass
        started = time.perf_counter()
        with Peak() as peak:
            for _ in pool.map(scan_source_file, tasks):
                pass
        wall = time.perf_counter() - started
    return {
        "workers": workers,
        "parse": parse,
        "gb": round(size / 1e9, 2),
        "seconds": round(wall, 2),
        "gb_per_s": round(size / 1e9 / wall, 3),
        "peak_tree_rss_mib": round(peak.peak / 2**20),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    try:
        entries = make_sources(args.root, 24, 9_000)
        for parse in (False, True):
            for workers in (1, 2, 4, 8, 16) if parse else (1, 8):
                results.append(run(entries, workers, args.repeat, parse))
                print(json.dumps(results[-1]), flush=True)
    finally:
        shutil.rmtree(args.root, ignore_errors=True)
    canonical.write_canonical_json(args.output, {"scaling": results})


if __name__ == "__main__":
    main()
