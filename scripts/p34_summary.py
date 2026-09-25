"""Summarize P34 benchmark evidence by case; invalid records are listed, never pooled."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

EVIDENCE = Path(__file__).resolve().parents[1] / "docs/implementation/evidence/p34"


def case_of(name: str) -> str:
    return name.removesuffix("_retry").rsplit("_r", 1)[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="final")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    cases: dict[str, list[dict[str, Any]]] = {}
    invalid = []
    for path in sorted(EVIDENCE.glob(f"{args.prefix}_*.json")):
        record = json.loads(path.read_text())
        if "args" not in record or not isinstance(record, dict):
            continue
        if record.get("status") != "VERIFIED" or path.stem.endswith("_invalid"):
            invalid.append({"file": path.name, "status": record.get("status")})
            continue
        cases.setdefault(case_of(record["args"]["name"]), []).append(record)
    summary: dict[str, Any] = {"invalid_or_failed": invalid, "cases": {}}
    for case, records in cases.items():
        rates = [r["tokens_per_second"] for r in records]
        tele = [r["telemetry_summary"] for r in records]
        prefetch = [r["prefetch_stats_measured"] for r in records if r["prefetch_stats_measured"]]
        entry = {
            "runs": [r["args"]["name"] for r in records],
            "targets_per_second": [round(v, 1) for v in rates],
            "median": statistics.median(rates),
            "min": min(rates),
            "max": max(rates),
            "update_wall_median_ms": [round(r["update_wall_ms"]["median"], 1) for r in records],
            "update_wall_p90_ms": [round(r["update_wall_ms"]["p90"], 1) for r in records],
            "final_parameter_digests": sorted({r["final_parameters_sha256"] for r in records}),
            "trace_digests": sorted({r["committed_state_trace_digest"] for r in records}),
            "max_allocated_gib": max(r["max_allocated"] for r in records) / 2**30,
            "max_reserved_gib": max(r["max_reserved"] for r in records) / 2**30,
            "max_trainer_rss_gib": max(t.get("max_rss", 0) for t in tele) / 2**30,
            "max_producer_rss_gib": max(t.get("max_producer_rss", 0) for t in tele) / 2**30,
            "trainer_cpu_percent_one_core": [
                round(t.get("mean_trainer_cpu_percent_one_core_100", 0), 1) for t in tele
            ],
            "producer_cpu_percent_one_core": [
                round(t.get("mean_producer_cpu_percent_one_core_100", 0), 1) for t in tele
            ],
            "system_cpu_percent": [round(t.get("mean_system_cpu_percent", 0), 1) for t in tele],
            "gpu_util_percent": [round(t["mean_gpu_util"], 1) for t in tele],
            "mem_util_percent": [round(t["mean_mem_util"], 1) for t in tele],
            "power_w": [round(t["mean_power_w"], 1) for t in tele],
            "device_memory_mib": [round(t["mean_memory_used_mib"]) for t in tele],
            "sm_mhz": [round(t["mean_sm_mhz"]) for t in tele],
            "telemetry_samples": [t["samples"] for t in tele],
            "setup_seconds": [round(r["setup_seconds"], 2) for r in records],
        }
        if prefetch:
            entry["blocked_takes"] = [p["blocked_takes"] for p in prefetch]
            entry["consumer_wait_ms_per_update"] = [
                round(p["consumer_wait_seconds"] * 1000 / p["takes"], 2) for p in prefetch
            ]
            entry["producer_ms_per_update"] = [
                round(p["produce_seconds"] * 1000 / p["takes"], 1) for p in prefetch
            ]
        sections = [r["sections"] for r in records if r.get("sections")]
        if sections:
            entry["sections"] = sections[0]
        summary["cases"][case] = entry
    text = json.dumps(summary, indent=2)
    if args.output:
        (EVIDENCE / args.output).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
