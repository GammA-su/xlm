"""Bounded checkpoint-tail study on the artifact volume (P34).

Saves real 50M model/optimizer checkpoints repeatedly with the existing
synchronous path, recording the P33 phase breakdown and the physical-disk
write volume of each save, then probes raw sequential write+fsync throughput
in 64 MiB chunks. Everything written is removed at the end. No product change.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_p34 as bench  # noqa: E402

from xlm.core.paths import ArtifactPaths  # noqa: E402
from xlm.training.checkpoint import CheckpointManager  # noqa: E402

GIB = 1024**3
CHUNK = 64 * 1024**2


def disk_for(path: Path) -> str:
    """The PhysicalDriveN counter key that holds ``path`` (Windows)."""
    letter = path.resolve().drive.rstrip(":")
    number = subprocess.check_output(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Partition -DriveLetter {letter}).DiskNumber",
        ],
        text=True,
        timeout=30,
    ).strip()
    return f"PhysicalDrive{number}"


def disk_written(key: str) -> int:
    return int(psutil.disk_io_counters(perdisk=True)[key].write_bytes)


def raw_probe(directory: Path, total: int, key: str) -> dict[str, Any]:
    """Sequential write of ``total`` bytes with an fsync per 64 MiB chunk."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "probe.bin"
    block = os.urandom(CHUNK)
    seconds = []
    before = disk_written(key)
    begin = time.perf_counter()
    with path.open("xb", buffering=0) as output:
        for _ in range(total // CHUNK):
            tick = time.perf_counter()
            output.write(block)
            os.fsync(output.fileno())
            seconds.append(time.perf_counter() - tick)
    wall = time.perf_counter() - begin
    written = disk_written(key) - before
    path.unlink()
    rates = [CHUNK / s / 2**20 for s in seconds]
    return {
        "bytes": total,
        "wall_seconds": wall,
        "mib_per_second": total / wall / 2**20,
        "chunk_mib_per_second": {
            "min": min(rates),
            "median": sorted(rates)[len(rates) // 2],
            "max": max(rates),
        },
        "chunk_seconds": seconds,
        "physical_disk_written_bytes": written,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--back-to-back", type=int, default=4)
    parser.add_argument("--spaced", type=int, default=3)
    parser.add_argument("--gap-seconds", type=float, default=60.0)
    parser.add_argument("--probe-gib", type=float, default=1.0)
    parser.add_argument("--probes", type=int, default=2)
    args = parser.parse_args()
    if not (
        0 <= args.back_to_back <= 6
        and 0 <= args.spaced <= 4
        and args.probe_gib <= 2
        and args.probes <= 3
        and 0 <= args.gap_seconds <= 300
    ):
        parser.error("bound exceeded")
    output = bench.EVIDENCE / f"{args.name}.json"
    if output.exists():
        raise FileExistsError(output)
    foreign = bench.foreign_xlm_processes(bench.own_pids())
    if foreign:
        raise RuntimeError(f"GPU/storage not exclusive: {foreign}")
    root = bench.LOCAL / "checkpoint_study" / args.name
    key = disk_for(bench.LOCAL)
    namespace = argparse.Namespace(
        name=args.name,
        model="50m",
        mode="end_to_end",
        loader="current",
        microbatch=8,
        depth=1,
        global_targets=65536,
        warmup=1,
        steps=2,
        vram_cap_gib=14.5,
        max_baseline_mib=11_000,
        sections=False,
        save_checkpoints=0,
    )
    records: list[dict[str, Any]] = []
    try:
        trainer = bench.build(namespace).trainer
        for _ in range(3):  # populate real AdamW state
            assert trainer.train_step() is not None
        torch.cuda.synchronize()
        schedule = ["back_to_back"] * args.back_to_back + ["spaced"] * args.spaced
        for index, regime in enumerate(schedule):
            if regime == "spaced":
                time.sleep(args.gap_seconds)
            trainer.checkpoint_manager = CheckpointManager(
                paths=ArtifactPaths(root=root / f"save_{index}")
            )
            before = disk_written(key)
            phases = bench.P33.checkpoint_pause(trainer, f"{args.name}_{index}")
            written = disk_written(key) - before
            payload = sum(
                p.stat().st_size for p in (root / f"save_{index}").rglob("*") if p.is_file()
            )
            records.append(
                {
                    "index": index,
                    "regime": regime,
                    "phases": phases,
                    "physical_disk_written_bytes": written,
                    "published_bytes": payload,
                    "foreign_processes": bench.foreign_xlm_processes(bench.own_pids()),
                }
            )
            print(
                json.dumps(
                    {
                        "index": index,
                        "regime": regime,
                        "pause_s": round(phases["total_pause"], 3),
                        "disk_written_mib": round(written / 2**20),
                    }
                ),
                flush=True,
            )
            shutil.rmtree(root / f"save_{index}")
        probes = [
            raw_probe(root / "probe", int(args.probe_gib * GIB), key) for _ in range(args.probes)
        ]
        result = {
            "status": "INVALID_CONTENDED"
            if any(r["foreign_processes"] for r in records)
            else "VERIFIED",
            "args": vars(args),
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=bench.ROOT, text=True
            ).strip(),
            "volume": str(bench.LOCAL.resolve().drive),
            "physical_disk": key,
            "temp_dir": os.environ.get("TEMP"),
            "checkpoints": records,
            "raw_write_probes": probes,
        }
        bench.write_json(output, result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "pauses": [round(r["phases"]["total_pause"], 2) for r in records],
                }
            )
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
