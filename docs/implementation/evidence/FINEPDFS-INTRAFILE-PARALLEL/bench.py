"""OFFLINE intra-file row-group benchmark on the owned, verified FinePDFs shard.

No network: the parent and every child refuse socket connections, and the
children only read a local file. Each unit is a fresh child process calling the
production entry point ``process_source_unit`` with the authorized b3
benchmark limits (read only) plus, for parallel runs, a ``row_group_parallel``
configuration. ``--concurrent K`` starts K units of the same shard at once to
measure file-level x intra-file interaction. The parent samples each child's
process tree (resident memory, CPU time) and the machine CPU every 0.1 s.
Outputs go to private scratch and are deleted after their hashes are recorded;
corpus text is never printed or stored here.

    uv run --offline --locked --no-sync --extra cpu --extra eval python bench.py \
        --shard C:/XLM-scratch/finepdfs/bench-b2/f00000.parquet.part \
        --state C:/XLM-scratch/finepdfs/bench-b2/f00000.state.json \
        --b3 G:/XLM/plans/finepdfs/benchmarks/b3 \
        --scratch C:/XLM-scratch/finepdfs-intrafile \
        --out <evidence dir> --matrix 1,2,4,8 --repeats 3 [--concurrent 3]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

SHARD_SHA256 = "4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d"
SHARD_BYTES = 2_771_021_138
MEMORY_CEILING = 48 * 1024**3
OUTPUT_KEYS = (
    "rows",
    "documents",
    "rejected",
    "rejection_counts_by_code",
    "canonical_bytes",
    "documents_sha256",
    "documents_file_bytes",
    "rejections_sha256",
    "rejections_uncompressed_bytes",
    "rejections_file_sha256",
    "adaptation_summary_sha256",
    "selected_records_sha256",
    "selected_records_bytes",
    "max_selected_record_bytes",
    "decoded_bytes",
    "processing_output_bytes",
    "files",
)


def _no_network(*_: Any, **__: Any) -> None:
    raise OSError("network disabled in the offline intra-file benchmark")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def child(spec_path: Path) -> int:
    """Run one unit; the result (counts, hashes, timings) goes to a JSON file."""
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    from xlm.data.acquisition.source_growth import ProcessingGrowth, bounded_json
    from xlm.data.acquisition.source_local import process_source_unit

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    job = spec["job"]
    growth = ProcessingGrowth.model_validate(job["limits"]["processing_growth"]).for_source(
        int(job["identity_record"]["length"])
    )
    job["growth_reserved"] = growth.model_dump()
    bounded_json(job["identity_record"], growth.metadata_bytes)
    result = process_source_unit(job)
    staging = Path(result["staging_dir"])
    result["files"] = {
        p.name: {"bytes": p.stat().st_size, "sha256": file_sha256(p)}
        for p in sorted(staging.iterdir())
        if p.name != "progress.json"
    }
    Path(spec["result"]).write_text(json.dumps(result, indent=2, sort_keys=True), "utf-8")
    return 0


def sample_tree(process: psutil.Process) -> tuple[int, float]:
    rss = cpu = 0.0
    for member in [process, *process.children(recursive=True)]:
        try:
            rss += member.memory_info().rss
            times = member.cpu_times()
            cpu += times.user + times.system
        except psutil.Error:
            continue
    return int(rss), cpu


def lookahead_for(args: argparse.Namespace, workers: int) -> int:
    return workers if args.lookahead < 0 else int(args.lookahead)


def run_group(
    args: argparse.Namespace, label: str, workers: int, job: dict[str, Any], copies: int
) -> list[dict[str, Any]]:
    """``copies`` simultaneous units of the same shard (1 is a plain single run)."""
    env = {
        **os.environ,
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    bases = []
    for copy in range(copies):
        base = Path(args.scratch) / (label if copies == 1 else f"{label}-i{copy}")
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        limits = dict(job["limits"])
        if workers > 1:
            limits["row_group_parallel"] = {
                "version": 1,
                "workers": workers,
                "lookahead": lookahead_for(args, workers),
                "processing_slots": 16,
                "memory_bytes": MEMORY_CEILING,
            }
        spec = {
            "job": {**job, "limits": limits, "staging_dir": str(base / "staging")},
            "result": str(base / "result.json"),
        }
        (base / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        bases.append(base)
    psutil.cpu_percent(None)
    started = time.monotonic()
    procs = [
        subprocess.Popen([sys.executable, __file__, "--child", str(base / "spec.json")], env=env)
        for base in bases
    ]
    watched = [psutil.Process(proc.pid) for proc in procs]
    walls: list[float | None] = [None] * copies
    peak_rss = [0] * copies
    tree_cpu = [0.0] * copies
    group_peak_rss = 0
    machine: list[float] = []
    while any(wall is None for wall in walls):
        total = 0
        for index, (proc, member) in enumerate(zip(procs, watched, strict=True)):
            if walls[index] is not None:
                continue
            if proc.poll() is not None:
                walls[index] = time.monotonic() - started
                continue
            try:
                rss, cpu = sample_tree(member)
            except psutil.Error:
                rss, cpu = 0, tree_cpu[index]
            peak_rss[index] = max(peak_rss[index], rss)
            tree_cpu[index] = max(tree_cpu[index], cpu)
            total += rss
        group_peak_rss = max(group_peak_rss, total)
        machine.append(psutil.cpu_percent(None))
        time.sleep(0.1)
    group_wall = time.monotonic() - started
    runs = []
    for index, (proc, base) in enumerate(zip(procs, bases, strict=True)):
        if proc.returncode != 0:
            raise SystemExit(f"{base.name}: child failed with exit status {proc.returncode}")
        result = json.loads((base / "result.json").read_text(encoding="utf-8"))
        staging_bytes = sum(p.stat().st_size for p in (base / "staging").rglob("*") if p.is_file())
        shutil.rmtree(base, ignore_errors=True)
        wall = float(walls[index] or group_wall)
        seconds = float(result["process_seconds"])
        runs.append(
            {
                "label": base.name,
                "workers": workers,
                "lookahead": lookahead_for(args, workers) if workers > 1 else None,
                "concurrent_units": copies,
                "child_wall_seconds": wall,
                "group_wall_seconds": group_wall,
                "process_seconds": seconds,
                "rows_per_second": int(result["rows"]) / seconds,
                "process_cpu_seconds": result["process_cpu_seconds"],
                "sampled_tree_cpu_seconds": tree_cpu[index],
                "effective_cores": tree_cpu[index] / wall,
                "machine_cpu_percent_mean": sum(machine) / max(1, len(machine)),
                "peak_tree_rss_bytes_sampled_100ms": peak_rss[index],
                "group_peak_rss_bytes_sampled_100ms": group_peak_rss,
                "staging_bytes_at_end": staging_bytes,
                "row_group_workers": result["row_group_workers"],
                "row_group_pool": result["row_group_pool"],
                "output": {key: result[key] for key in OUTPUT_KEYS},
            }
        )
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("--shard")
    parser.add_argument("--state")
    parser.add_argument("--b3")
    parser.add_argument("--scratch")
    parser.add_argument("--out")
    parser.add_argument("--matrix", default="1,2,4,8")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--lookahead", type=int, default=-1, help="-1: equal to workers")
    parser.add_argument("--concurrent", type=int, default=1)
    parser.add_argument("--tag", default="matrix")
    args = parser.parse_args()
    if args.child:
        return child(Path(args.child))
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    from xlm.data.acquisition.plan import load_acquisition_plan
    from xlm.data.acquisition.source_run import PROCESS_LIMIT_KEYS

    shard, b3 = Path(args.shard), Path(args.b3)
    started = time.monotonic()
    sha = file_sha256(shard)  # also warms the OS file cache before every timed run
    if (sha, shard.stat().st_size) != (SHARD_SHA256, SHARD_BYTES):
        raise SystemExit("owned shard does not match its verified identity")
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    record = json.loads((b3 / "benchmark.json").read_text(encoding="utf-8"))
    plan = load_acquisition_plan(b3 / "acquisition.plan.json")
    if plan.plan_hash != record["acquisition_plan"]["plan_hash"]:
        raise SystemExit("b3 plan does not match its benchmark record")
    limits = {key: record["limits"][key] for key in PROCESS_LIMIT_KEYS}
    limits["processing_growth"] = record["limits"]["processing_growth"]
    pin = record["source"]
    identity = {
        "kind": "verified_source_parquet",
        "version": 1,
        "source_file": state["name"],
        "repository": pin["repository"],
        "revision": pin["revision"],
        "etag": state["etag"],
        "length": state["length"],
        "sha256": sha,
    }
    job = {
        "source_path": str(shard),
        "durable_path": None,
        "source_file": state["name"],
        "source_id": pin["source_id"],
        "view_id": pin["view_id"],
        "adapter_id": pin["adapter_id"],
        "repository": pin["repository"],
        "revision": pin["revision"],
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "selection_hash": plan.compute_selection_hash(),
        "limits": limits,
        "row_range": None,
        "progress_path": None,
        "identity_record": identity,
        "key": "f00000",
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    matrix = [int(value) for value in args.matrix.split(",")]
    with (out / f"runs-{args.tag}.jsonl").open("a", encoding="utf-8") as log:
        for repeat in range(args.repeats):
            for workers in matrix:
                label = f"{args.tag}-c{args.concurrent}-w{workers}-r{repeat}"
                for run in run_group(args, label, workers, job, args.concurrent):
                    run["repeat"] = repeat
                    log.write(json.dumps(run, sort_keys=True) + "\n")
                    log.flush()
                    print(
                        f"{run['label']}: {run['process_seconds']:.1f}s "
                        f"group={run['group_wall_seconds']:.1f}s "
                        f"cores={run['effective_cores']:.2f} "
                        f"rss={run['peak_tree_rss_bytes_sampled_100ms'] / 2**30:.2f}GiB "
                        f"docs={run['output']['documents_sha256'][:12]}",
                        flush=True,
                    )
    print(f"total {time.monotonic() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
