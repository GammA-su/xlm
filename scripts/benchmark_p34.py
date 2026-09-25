"""Bounded offline P34 throughput diagnostics: synchronous, resident and prefetch.

Every invocation is one fresh CUDA process on the frozen P33 fixture. A run
refuses to start, or is recorded as INVALID_CONTENDED, when another XLM Python
process or unexplained device memory shares the GPU. No downloads or installs.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import types
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import torch

from xlm.config.schemas import WarmupCosineScheduleConfig
from xlm.core.paths import ArtifactPaths
from xlm.data.sampling.mixture import MixtureRecipe
from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
from xlm.data.tokens import TokenShardReader
from xlm.models.transformer import create_transformer_baseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import BatcherProtocol
from xlm.training.trainer import Trainer

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "artifacts/p34"
EVIDENCE = ROOT / "docs/implementation/evidence/p34"
FIXTURE_EVIDENCE = ROOT / "docs/implementation/evidence/p33/fixture.json"
GIB = 1024**3


def _p33() -> Any:
    spec = importlib.util.spec_from_file_location(
        "benchmark_p33", ROOT / "scripts/benchmark_p33.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P33 = _p33()
# The certified P33 base. Only this pinned repository file is executed, never
# configurable code, artifacts or corpus text.
P33_HEAD = "8fd05c11e1bdfd84e075000d59e14c315c986f36"


def p33_stream() -> Any:
    """The P33 MixtureBatcher (per-target trace dicts) for reference loaders."""
    code = subprocess.check_output(
        ["git", "show", f"{P33_HEAD}:src/xlm/data/sampling/stream.py"], cwd=ROOT, text=True
    )
    module = types.ModuleType("p33_reference_stream")
    sys.modules[module.__name__] = module
    exec(compile(code, "p33_reference_stream.py", "exec"), module.__dict__)
    return module


def p33_producer_factory(spec: ProducerSpec) -> Any:
    """Producer-side P33 loader: isolates hiding the loader from shortening it."""
    readers = {s: TokenShardReader(Path(d)) for s, d in spec.shards.items()}
    return p33_stream().MixtureBatcher(
        MixtureRecipe.model_validate(spec.recipe),
        readers,
        context_length=spec.context_length,
        global_batch_valid_targets=spec.global_batch_valid_targets,
        microbatch_sequences=spec.microbatch_sequences,
        emit_tensors=False,
        max_open_shards=spec.max_open_shards,
    )


def make_batcher(loader: str, microbatch: int, targets: int) -> Any:
    source = P33.batcher(microbatch, targets)
    if loader == "current":
        return source
    reader = source.readers["synthetic"]
    reference = p33_stream().MixtureBatcher(
        source.recipe,
        {"synthetic": reader},
        context_length=512,
        global_batch_valid_targets=targets,
        microbatch_sequences=microbatch,
        emit_tensors=True,
        max_open_shards=1,
    )
    source.close()
    return reference


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def foreign_xlm_processes(own: set[int]) -> list[dict[str, Any]]:
    """Other Python processes whose command line names an XLM checkout."""
    found = []
    for process in psutil.process_iter(["pid", "name"]):
        info = process.info
        if info["pid"] in own or not (info["name"] or "").lower().startswith("python"):
            continue
        try:
            command = " ".join(process.cmdline())
        except (psutil.Error, OSError):
            continue
        if "xlm" in command.lower():
            found.append({"pid": info["pid"], "command": command[:200]})
    return found


def own_pids(extra: int | None = None) -> set[int]:
    me = psutil.Process()
    pids = {me.pid} | {p.pid for p in me.parents()} | {c.pid for c in me.children(recursive=True)}
    if extra is not None:
        pids.add(extra)
    return pids


def device_memory_used_mib() -> int:
    return int(P33.smi().split(",")[1])


def parameter_digest(model: torch.nn.Module) -> str:
    digest = hashlib.sha256()
    for name, value in model.state_dict().items():
        digest.update(name.encode())
        digest.update(value.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


class Monitor:
    """1 Hz telemetry for trainer and producer; flags contention and hard caps."""

    def __init__(self, start: float, producer: int | None, baseline_mib: int) -> None:
        self.rows: list[dict[str, Any]] = []
        self.contention: list[dict[str, Any]] = []
        self.start = start
        self.producer = producer
        self.baseline_mib = baseline_mib
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        me = psutil.Process()
        child = psutil.Process(self.producer) if self.producer else None
        for process in (me, child):
            if process is not None:
                process.cpu_percent()
        psutil.cpu_percent()
        while not self.stop.is_set():
            rss = me.memory_info().rss
            child_rss = child.memory_info().rss if child is not None and child.is_running() else 0
            if time.monotonic() - self.start > 900 or rss + child_rss > 32 * GIB:
                os._exit(124)
            row: dict[str, Any] = {
                "elapsed": time.monotonic() - self.start,
                "rss": rss,
                "trainer_cpu_percent_one_core_100": me.cpu_percent(),
                "system_cpu_percent": psutil.cpu_percent(),
            }
            if child is not None and child.is_running():
                row["producer_rss"] = child_rss
                row["producer_cpu_percent_one_core_100"] = child.cpu_percent()
            try:
                row["smi"] = P33.smi()
                reserved_mib = torch.cuda.memory_reserved() / 2**20
                used = int(row["smi"].split(",")[1])
                # Our own context/allocator plus the pre-run desktop baseline, with slack.
                if used > self.baseline_mib + reserved_mib + 1536:
                    self.contention.append({"elapsed": row["elapsed"], "device_used_mib": used})
            except (OSError, subprocess.SubprocessError) as exc:
                row["telemetry_error"] = str(exc)
            if len(self.rows) % 5 == 0:
                foreign = foreign_xlm_processes(own_pids(self.producer))
                if foreign:
                    self.contention.append({"elapsed": row["elapsed"], "processes": foreign})
            self.rows.append(row)
            self.stop.wait(1.0)

    def __enter__(self) -> Monitor:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=15)


def summarize(rows: list[dict[str, Any]], interval: tuple[float, float]) -> dict[str, Any]:
    window = [r for r in rows if interval[0] <= r["elapsed"] <= interval[1] and "smi" in r]
    if not window:
        return {"samples": 0}
    smi = [[float(v) for v in r["smi"].split(",")[1:]] for r in window]
    names = ["memory_used_mib", "gpu_util", "mem_util", "power_w", "sm_mhz", "mem_mhz", "temp_c"]
    result: dict[str, Any] = {"samples": len(window)}
    for index, name in enumerate(names):
        result[f"mean_{name}"] = statistics.mean(row[index] for row in smi)
    for key in (
        "trainer_cpu_percent_one_core_100",
        "producer_cpu_percent_one_core_100",
        "system_cpu_percent",
        "rss",
        "producer_rss",
    ):
        values = [r[key] for r in window if key in r]
        if values:
            result[f"mean_{key}"] = statistics.mean(values)
            result[f"max_{key}"] = max(values)
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", "src", "scripts"], cwd=ROOT, text=True
    ).strip()
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    foreign = foreign_xlm_processes(own_pids())
    baseline_mib = device_memory_used_mib()
    if foreign or baseline_mib > args.max_baseline_mib:
        raise RuntimeError(f"GPU not exclusive: processes={foreign} baseline_mib={baseline_mib}")
    built = build(args)
    trainer, source, active, model = built.trainer, built.source, built.active, built.model
    initial_digest, fixture, setup_seconds = (
        built.initial_digest,
        built.fixture,
        built.setup_seconds,
    )
    producer = active.producer_pid if isinstance(active, PrefetchingBatcher) else None
    start = time.monotonic()
    monitor = Monitor(start, producer, baseline_mib)
    try:
        with monitor:
            for _ in range(args.warmup):
                if trainer.train_step() is None:
                    raise RuntimeError("Warmup failed to consume an update")
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            prefetch_before = active.stats() if isinstance(active, PrefetchingBatcher) else {}
            gpu_start, gpu_end = P33.cuda_event(), P33.cuda_event()
            interval_start = time.monotonic() - start
            gpu_start.record()
            begin = time.perf_counter()
            cpu_begin = time.process_time()
            metrics, update_walls = [], []
            for _ in range(args.steps):
                tick = time.perf_counter()
                metric = trainer.train_step()
                update_walls.append(time.perf_counter() - tick)
                if metric is None:
                    raise RuntimeError("Measured update missing")
                metrics.append(asdict(metric))
            gpu_end.record()
            torch.cuda.synchronize()
            wall = time.perf_counter() - begin
            trainer_cpu = time.process_time() - cpu_begin
            interval = (interval_start, time.monotonic() - start)
            prefetch_after = active.stats() if isinstance(active, PrefetchingBatcher) else {}
            sections = P33.measure_sections(trainer) if args.sections else None
            final_digest = parameter_digest(model)
            committed = active.get_state()
        targets = sum(m["valid_targets"] for m in metrics)
        result: dict[str, Any] = {
            "status": "INVALID_CONTENDED" if monitor.contention else "VERIFIED",
            "contention": monitor.contention,
            "args": vars(args),
            "head": head,
            "worktree_changes_src_scripts": dirty,
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "numpy": np.__version__,
            "gpu": torch.cuda.get_device_name(),
            "vram_bytes": torch.cuda.get_device_properties(0).total_memory,
            "baseline_device_used_mib": baseline_mib,
            "parameters": sum(p.numel() for p in model.parameters()),
            "initial_parameters_sha256": initial_digest,
            "final_parameters_sha256": final_digest,
            "committed_state_trace_digest": committed.get("trace_digest"),
            "committed_valid_targets": committed.get("committed_valid_targets"),
            "setup_seconds": setup_seconds,
            "wall_seconds": wall,
            "trainer_process_cpu_seconds": trainer_cpu,
            "cuda_elapsed_ms_including_launch_gaps": gpu_start.elapsed_time(gpu_end),
            "valid_targets": targets,
            "tokens_per_second": targets / wall,
            "steps_per_second": args.steps / wall,
            "update_wall_ms": {
                "median": statistics.median(update_walls) * 1000,
                "p90": sorted(update_walls)[int(0.9 * (len(update_walls) - 1))] * 1000,
                "max": max(update_walls) * 1000,
            },
            "max_allocated": torch.cuda.max_memory_allocated(),
            "max_reserved": torch.cuda.max_memory_reserved(),
            "rss": psutil.Process().memory_info().rss,
            "peak_rss": getattr(psutil.Process().memory_info(), "peak_wset", None),
            "prefetch_stats_measured": {
                k: prefetch_after.get(k, 0.0) - prefetch_before.get(k, 0.0)
                for k in (
                    "takes",
                    "blocked_takes",
                    "consumer_wait_seconds",
                    "produce_seconds",
                    "discarded_stale",
                    "resets",
                    "budget_mispredictions",
                )
            }
            if prefetch_after
            else None,
            "prefetch_stats_total": prefetch_after or None,
            "resident_preparation_seconds": getattr(active, "preparation_seconds", 0.0),
            "sections": sections,
            "telemetry_summary": summarize(monitor.rows, interval),
            "metrics": metrics,
            "fixture": fixture,
        }
        if args.save_checkpoints:
            result["checkpoints"] = [
                P33.checkpoint_pause(trainer, f"{args.name}_{index}")
                for index in range(args.save_checkpoints)
            ]
        result["telemetry"] = monitor.rows
        return result
    finally:
        if isinstance(active, PrefetchingBatcher):
            active.close()
        source.close()


@dataclasses.dataclass
class Built:
    trainer: Trainer
    source: Any
    active: Any
    model: torch.nn.Module
    initial_digest: str
    fixture: dict[str, Any]
    setup_seconds: float


def build(args: argparse.Namespace) -> Built:
    """The real model, optimizer, schedule, loader mode and Trainer for one case."""
    torch.cuda.set_per_process_memory_fraction(
        args.vram_cap_gib * GIB / torch.cuda.get_device_properties(0).total_memory
    )
    torch.set_num_threads(1)
    config = json.loads((ROOT / f"recipes/models/{args.model}.yaml").read_text())
    recipe = json.loads((ROOT / f"recipes/experiments/baseline_{args.model}.yaml").read_text())
    config["attention_backend"] = "sdpa"
    model = create_transformer_baseline(config, device="cuda", seed=101)
    initial_digest = parameter_digest(model)
    objective = CrossEntropyObjective().cuda()
    optimizer, manifest = create_adamw_optimizer(recipe["optimizer"], model, objective)
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(**recipe["training"]["schedule"]),
        base_lr=recipe["optimizer"]["lr"],
    )
    source = make_batcher(args.loader, args.microbatch, args.global_targets)
    fixture = source.readers["synthetic"].manifest.to_dict()
    frozen = json.loads(FIXTURE_EVIDENCE.read_text())["manifest"]
    if fixture["checksum_sha256"] != frozen["checksum_sha256"]:
        raise RuntimeError("fixture differs from the frozen P33 fixture")
    total_updates = args.warmup + args.steps
    max_targets = (total_updates + 3) * args.global_targets
    setup_begin = time.perf_counter()
    active: BatcherProtocol
    if args.mode == "resident":
        active = P33.ResidentBatcher(source, total_updates + 2)
    elif args.mode == "prefetch":
        with P33.batcher(args.microbatch, args.global_targets) as described:
            spec = ProducerSpec.from_batcher(described)
        if args.loader == "p33":
            spec = dataclasses.replace(spec, factory=p33_producer_factory)
        active = PrefetchingBatcher(spec, source.get_state(), depth=args.depth)
    else:
        active = source
    setup_seconds = time.perf_counter() - setup_begin
    manager = CheckpointManager(paths=ArtifactPaths(root=LOCAL / "checkpoints" / args.name))
    trainer = Trainer(
        model,
        objective,
        optimizer,
        manifest,
        schedule,
        active,
        manager,
        run_id=args.name,
        plan_id="p34-bounded-synthetic",
        device="cuda",
        precision="bf16_fp32_master",
        max_valid_targets=max_targets,
    )
    return Built(trainer, source, active, model, initial_digest, fixture, setup_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", choices=["50m", "150m", "300m"], default="50m")
    parser.add_argument("--mode", choices=["end_to_end", "resident", "prefetch"], required=True)
    parser.add_argument("--loader", choices=["current", "p33"], default="current")
    parser.add_argument("--microbatch", type=int, default=8)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--global-targets", type=int, default=65536)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--vram-cap-gib", type=float, default=14.5)
    parser.add_argument("--max-baseline-mib", type=int, default=11_000)
    parser.add_argument("--sections", action="store_true")
    parser.add_argument("--save-checkpoints", type=int, default=0)
    args = parser.parse_args()
    if not (
        1 <= args.microbatch <= 64
        and 1 <= args.global_targets <= 65536
        and 0 <= args.warmup <= 20
        and 1 <= args.steps <= 200
        and 1 <= args.depth <= 2
        and 0 < args.vram_cap_gib <= 23
        and 0 <= args.save_checkpoints <= 8
    ):
        parser.error("bound exceeded")
    if not args.name.replace("_", "").replace("-", "").isalnum():
        parser.error("name must be alphanumeric with hyphens/underscores")
    output = EVIDENCE / f"{args.name}.json"
    if output.exists():
        raise FileExistsError(output)
    LOCAL.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args)
    except Exception as exc:
        write_json(
            output,
            {
                "status": "OOM" if isinstance(exc, torch.OutOfMemoryError) else "FAILED",
                "args": vars(args),
                "error": repr(exc),
            },
        )
        raise
    write_json(output, result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "tokens_per_second",
                    "wall_seconds",
                    "max_allocated",
                    "final_parameters_sha256",
                )
            }
        )
    )
    if result["status"] != "VERIFIED":
        sys.exit(3)


if __name__ == "__main__":
    main()
