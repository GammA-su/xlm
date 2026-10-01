"""Summarize the intra-file benchmark logs into ``summary.json`` (offline, read-only inputs).

The identity gate compares every run's complete output record (counts,
rejection codes, documents/ledger/summary/selected-record SHA-256 and the
SHA-256 of every output file) with the first serial run.

    uv run --offline --locked --no-sync --extra cpu --extra eval python summarize.py
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load(name: str) -> list[dict[str, Any]]:
    path = HERE / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def spread(values: list[float]) -> dict[str, float]:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def main() -> int:
    matrix = load("runs-matrix.jsonl")
    concurrent = load("runs-concurrent.jsonl")
    reference = next(r for r in matrix if r["workers"] == 1 and r["repeat"] == 0)["output"]
    identity = [
        {
            "label": run["label"],
            "workers": run["workers"],
            "identical": run["output"] == reference,
            "documents_sha256": run["output"]["documents_sha256"],
        }
        for run in matrix + concurrent
    ]
    serial = statistics.median(r["process_seconds"] for r in matrix if r["workers"] == 1)
    table = {}
    for workers in sorted({r["workers"] for r in matrix}):
        runs = [r for r in matrix if r["workers"] == workers]
        seconds = [r["process_seconds"] for r in runs]
        pools = [r["row_group_pool"] for r in runs if r["row_group_pool"]]
        table[str(workers)] = {
            "lookahead": runs[0]["lookahead"],
            "process_seconds": spread(seconds),
            "rows_per_second_median": reference["rows"] / statistics.median(seconds),
            "speedup_vs_serial_median": serial / statistics.median(seconds),
            "parallel_efficiency": serial / statistics.median(seconds) / workers,
            "effective_cores": spread([r["effective_cores"] for r in runs]),
            "machine_cpu_percent_mean": spread([r["machine_cpu_percent_mean"] for r in runs]),
            "process_cpu_seconds": spread([r["process_cpu_seconds"] for r in runs]),
            "coordinator_cpu_seconds": None
            if not pools
            else spread(
                [r["process_cpu_seconds"] - r["row_group_pool"]["worker_cpu_seconds"] for r in runs]
            ),
            "peak_tree_rss_bytes_sampled_100ms": spread(
                [r["peak_tree_rss_bytes_sampled_100ms"] for r in runs]
            ),
            "pool_peak_tree_rss_bytes": None
            if not pools
            else max(p["peak_tree_rss_bytes"] for p in pools),
            "max_worker_peak_rss_bytes": None
            if not pools
            else max(p["max_worker_peak_rss_bytes"] for p in pools),
            "peak_buffered_result_bytes": None
            if not pools
            else max(p["peak_buffered_result_bytes"] for p in pools),
            "max_group_result_bytes": None
            if not pools
            else max(p["max_group_result_bytes"] for p in pools),
            "head_of_line_wait_seconds": None
            if not pools
            else spread([p["head_of_line_wait_seconds"] for p in pools]),
            "staging_bytes_at_end": sorted({r["staging_bytes_at_end"] for r in runs}),
        }
    groups: dict[str, dict[str, Any]] = {}
    for run in concurrent:
        key = f"{run['concurrent_units']}x{run['workers']}-r{run['repeat']}"
        entry = groups.setdefault(
            key,
            {
                "units": run["concurrent_units"],
                "workers_per_unit": run["workers"],
                "group_wall_seconds": run["group_wall_seconds"],
                "group_peak_rss_bytes_sampled_100ms": run["group_peak_rss_bytes_sampled_100ms"],
                "machine_cpu_percent_mean": run["machine_cpu_percent_mean"],
                "unit_process_seconds": [],
            },
        )
        entry["unit_process_seconds"].append(run["process_seconds"])
    for entry in groups.values():
        entry["aggregate_rows_per_second"] = (
            entry["units"] * reference["rows"] / entry["group_wall_seconds"]
        )
    summary = {
        "kind": "finepdfs_intrafile_parallel_benchmark",
        "source": {
            "file": "data/eng_Latn/train/000_00083.parquet",
            "local_copy": "C:/XLM-scratch/finepdfs/bench-b2/f00000.parquet.part",
            "sha256": "4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d",
            "bytes": 2771021138,
            "row_groups": 221,
        },
        "method": (
            "each unit a fresh child process calling process_source_unit with the authorized "
            "b3 limits; warm OS file cache (the shard is re-hashed in full before the runs); "
            "matrix runs interleaved 1,2,4,8 per repeat; lookahead equal to workers; no network"
        ),
        "serial_reference": reference,
        "identity_gate": {
            "runs": len(identity),
            "all_identical": all(item["identical"] for item in identity),
            "runs_detail": identity,
        },
        "single_file": table,
        "concurrent_files": groups,
    }
    (HERE / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: summary[k] for k in ("identity_gate",)}["identity_gate"]["all_identical"]))
    for workers, row in table.items():
        print(
            workers,
            round(row["process_seconds"]["median"], 1),
            round(row["process_seconds"]["min"], 1),
            round(row["process_seconds"]["max"], 1),
            "speedup",
            round(row["speedup_vs_serial_median"], 2),
            "cores",
            round(row["effective_cores"]["median"], 2),
            "rss",
            row["peak_tree_rss_bytes_sampled_100ms"]["max"],
            "pool",
            row["pool_peak_tree_rss_bytes"],
            "coord",
            row["coordinator_cpu_seconds"],
        )
    for key, entry in groups.items():
        print(key, json.dumps(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
