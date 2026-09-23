"""Bounded P29C cleaner telemetry on the frozen authored P29B input; offline only."""

from __future__ import annotations

import argparse
import cProfile
import functools
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from benchmark_pipeline import Measurements

from xlm.data.cleaning import sharded
from xlm.data.cleaning.quarantine import QuarantinePolicy

_WORKER = sharded.clean_unit_worker


def measured_worker(
    spec: dict[str, Any], *, evidence: str, profile: bool, dispatched: float
) -> dict[str, Any]:
    """Telemetry outside identities; one sidecar per process task, no source text."""
    start, cpu = time.perf_counter(), time.process_time()
    profiler = cProfile.Profile()
    if profile:
        profiler.enable()
    result = _WORKER(spec)
    if profile:
        profiler.disable()
    end = time.perf_counter()
    first = spec["units"][0]["index"]
    prefix = Path(evidence) / f"worker-{first:05d}"
    row = {
        "pid": os.getpid(),
        "start": start,
        "end": end,
        "dispatch_to_start_seconds": start - dispatched,
        "wall_seconds": end - start,
        "cpu_seconds": time.process_time() - cpu,
        "units": [{"index": u["index"], "timing": u["timing"]} for u in result["units"]],
    }
    prefix.with_suffix(".json").write_text(json.dumps(row, indent=2), encoding="utf-8")
    if profile:
        profiler.dump_stats(str(prefix.with_suffix(".pstats")))
    return result


def timed_call(function: Any, rows: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
    start, cpu = time.perf_counter(), time.process_time()
    try:
        return function(*args, **kwargs)
    finally:
        rows.append(
            {
                "function": function.__name__,
                "wall_seconds": time.perf_counter() - start,
                "cpu_seconds": time.process_time() - cpu,
            }
        )


def digest_files(paths: list[Path], *, quarantine: bool = False) -> str:
    digest = hashlib.sha256()
    for path in paths:
        with path.open("rb") as stream:
            if quarantine:
                for line in stream:
                    record = json.loads(line)
                    record.pop("recorded_at")
                    digest.update(json.dumps(record, sort_keys=True).encode())
            else:
                for block in iter(lambda: stream.read(1024**2), b""):
                    digest.update(block)
    return digest.hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    root: Path = args.output
    root.mkdir(parents=True, exist_ok=False)
    sidecars = root / "workers"
    sidecars.mkdir()
    measurement = Measurements(root, 900, args.workers, max_artifact_bytes=2 * 1024**3)
    parent_rows: list[dict[str, Any]] = []
    originals = {
        name: getattr(sharded, name)
        for name in (
            "plan_clean_units",
            "verify_manifest",
            "merge_unit_results",
            "_stream_concat",
            "assemble_output_shards",
        )
    }
    from contextlib import ExitStack

    with ExitStack() as stack:
        for name, function in originals.items():
            stack.enter_context(
                patch.object(sharded, name, functools.partial(timed_call, function, parent_rows))
            )
        stack.enter_context(
            patch.object(
                sharded,
                "clean_unit_worker",
                functools.partial(
                    measured_worker,
                    evidence=str(sidecars),
                    profile=args.profile,
                    dispatched=time.perf_counter(),
                ),
            )
        )
        with measurement.stage("cleaning"):
            summary, _, assembled, throughput, manifest = sharded.run_sharded_clean(
                input_path=args.input,
                output_dir=root / "cleaned",
                preset="prose",
                workers=args.workers,
                input_shard_bytes=args.shard_mib * 1024**2,
                output_shard_bytes=(args.output_shard_mib * 1024**2 or None),
                quarantine_dir=root / "quarantine",
                quarantine_shard_bytes=None,
                quarantine_policy=QuarantinePolicy(
                    max_records=args.documents, store_previews=False
                ),
                max_docs=args.documents,
                max_input_bytes=512 * 1024**2,
                scheduling=args.scheduling,
            )
    workers = [json.loads(p.read_text()) for p in sorted(sidecars.glob("*.json"))]
    metrics = summary.to_dict()
    metrics.pop("elapsed_seconds")
    for stage in metrics["stage_metrics"]:
        stage.pop("duration_ms")
    report = {
        "fixture_only": True,
        "scheduling": args.scheduling,
        "workers": args.workers,
        "profiled": args.profile,
        "shard_mib": args.shard_mib,
        "output_shard_mib": args.output_shard_mib,
        "measurement": measurement.rows[0],
        "throughput": throughput,
        "worker_telemetry": workers,
        "parent_phases": parent_rows,
        "scientific_summary": metrics,
        "accepted_sha256": digest_files([root / "cleaned" / p for p in assembled.accepted_files]),
        "quarantine_logical_sha256": digest_files(
            [root / "quarantine" / p for p in assembled.quarantine_files], quarantine=True
        ),
        "manifest": manifest.to_dict() if manifest else None,
        "limits": {"seconds": 900, "rss_bytes": 3 * 1024**3, "disk_bytes": 2 * 1024**3},
        "notes": [
            "parent phase timers can nest; do not add nested phase times",
            "worker CPU excludes interpreter bootstrap; wall minus CPU is not measured OS wait",
            "dispatch-to-start includes planning, spawn, imports and queueing",
            "RSS sampled at 100 ms, disk and process discovery at 1 s",
        ],
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"stage_seconds": throughput["stage_seconds"], "parent_phases": parent_rows}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--documents", type=int, choices=(10000, 100000, 250000), default=100000)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4, 6, 8), default=1)
    parser.add_argument("--shard-mib", type=int, choices=(1, 4, 16), default=4)
    parser.add_argument("--output-shard-mib", type=int, choices=(0, 1, 4), default=0)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--scheduling", choices=("static", "dynamic"), default="static")
    run(parser.parse_args())
