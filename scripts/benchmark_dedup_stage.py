"""Isolated P28 dedup-stage benchmark on an existing cleaned canonical JSONL file.

Runs ``run_sharded_dedup`` with the pipeline harness parameters and reports wall,
process-tree CPU, phase telemetry and exact output digests (survivor IDs and the
published survivor bytes) so two source trees can be compared byte-for-byte on
identical input. Authored/local data only; never downloads anything. Whether
NumPy is importable is recorded, because the MinHash kernel depends on it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import psutil

from xlm.data.dedup import DedupConfig, run_sharded_dedup

MIB = 1024 * 1024


def numpy_importable() -> bool:
    try:
        import numpy  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def tree_cpu(process: psutil.Process) -> float:
    total = 0.0
    for proc in [process, *process.children(recursive=True)]:
        try:
            times = proc.cpu_times()
            total += times.user + times.system
        except psutil.Error:
            continue
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if args.work.exists():
        shutil.rmtree(args.work)
    args.work.mkdir(parents=True)
    me = psutil.Process()
    started = time.perf_counter()
    cpu0 = me.cpu_times()
    result, telemetry, _, _, _ = run_sharded_dedup(
        input_path=args.input,
        output_dir=args.work / "deduped",
        config=DedupConfig(),
        workers=args.workers,
        input_shard_bytes=4 * MIB,
        output_shard_bytes=None,
        work_dir=args.work / "dedup_work",
        max_input_bytes=1024 * MIB,
    )
    wall = time.perf_counter() - started
    cpu1 = me.cpu_times()
    survivors = hashlib.sha256("\n".join(result.survivor_doc_ids).encode()).hexdigest()
    output = hashlib.sha256((args.work / "deduped" / "documents.jsonl").read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "numpy_importable": numpy_importable(),
                "workers": args.workers,
                "wall_s": round(wall, 3),
                "parent_cpu_s": round(cpu1.user + cpu1.system - cpu0.user - cpu0.system, 3),
                "survivors": len(result.survivor_doc_ids),
                "survivor_ids_sha256": survivors,
                "survivor_bytes_sha256": output,
                "telemetry": {
                    key: round(value, 3) if isinstance(value, float) else value
                    for key, value in telemetry.to_dict().items()
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
