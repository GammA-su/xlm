"""Bounded offline CUDA diagnostics, separate from authorized research commands.

Every invocation is one fresh CUDA process. Fixture construction is a separate
command; measured runs verify and reuse it. No downloads or environment changes.
"""

from __future__ import annotations

import argparse
import copy
import cProfile
import hashlib
import importlib.util
import json
import os
import platform
import random
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import psutil
import torch

from xlm.config.schemas import WarmupCosineScheduleConfig
from xlm.core.contracts import CanonicalDocument, TrainingBatch
from xlm.core.paths import ArtifactPaths
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.models.transformer import create_transformer_baseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.checkpoint import CheckpointManager
from xlm.training.data import BatcherProtocol
from xlm.training.trainer import Trainer

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "artifacts/p33"
EVIDENCE = ROOT / "docs/implementation/evidence/p33"
REFERENCE_HEAD = "03e6c4278a8a64301a1c416e37bda7623907e361"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


class FixtureTokenizer(ByteTokenizer):
    """Authored numeric-word fixture codec, never a production tokenizer."""

    def __init__(self) -> None:
        super().__init__()
        self._vocab_size = 32768

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(b"p33:numeric-words:v1:32768").hexdigest()

    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        ids, offsets = [], []
        start = 0
        for word in text.splitlines(keepends=True):
            token = int(word.strip())
            if not 4 <= token < self.vocab_size:
                raise ValueError("Fixture token out of range")
            ids.append(token)
            offsets.append((start, start + len(word)))
            start += len(word)
        if add_special_tokens:
            ids = [1, *ids, 2]
            offsets = [(0, 0), *offsets, (len(text), len(text))]
        return ids, offsets


def fixture() -> None:
    directory = LOCAL / "fixture"
    if directory.exists():
        raise FileExistsError("Frozen fixture already exists; reuse it")
    rng = random.Random(20260918)
    documents = []
    for index in range(16):
        body = "".join(f"{rng.randrange(4, 32768)}\n" for _ in range(8192))
        digest = hashlib.sha256(body.encode()).hexdigest()
        documents.append(
            CanonicalDocument(
                doc_id=f"synthetic_{index}",
                source_id="synthetic",
                source_revision="p33-v1",
                source_file="authored",
                source_row=index,
                raw_hash=digest,
                clean_hash=digest,
                text=body,
                utf8_byte_count=len(body),
                language="und",
                language_confidence=1.0,
                document_kind="synthetic",
                source_metadata={},
                parent_ids=[],
                license_reference="authored-fixture",
                transform_log=[],
                quality_reasons=[],
                cluster_ids={},
                split="train",
            )
        )
    manifest = TokenShardWriter(
        directory,
        "p33_synthetic",
        "synthetic",
        FixtureTokenizer(),
        pool_hash="authored-p33-v1",
        max_output_bytes=16 * 1024**2,
    ).write_documents(documents, add_special_tokens=True)
    write_json(
        EVIDENCE / "fixture.json",
        {
            "manifest": manifest.to_dict(),
            "seed": 20260918,
            "documents": 16,
            "scope": "synthetic full-vocabulary token IDs; no production tokenizer or live text",
        },
    )


def batcher(microbatch: int, global_targets: int) -> MixtureBatcher:
    reader = TokenShardReader(LOCAL / "fixture")
    reader.verify_integrity()
    recipe = MixtureRecipe(
        mixture_id="p33_synthetic",
        data_seed=20260918,
        model_seed=101,
        components=[MixtureComponent(source_id="synthetic", weight=1.0)],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=256),
    )
    return MixtureBatcher(
        recipe,
        {"synthetic": reader},
        context_length=512,
        global_batch_valid_targets=global_targets,
        microbatch_sequences=microbatch,
        emit_tensors=True,
        max_open_shards=1,
    )


def move(batch: TrainingBatch, device: str, pin: bool = False) -> TrainingBatch:
    values = {}
    for name in ("input_ids", "labels", "loss_mask", "position_ids"):
        value = getattr(batch, name)
        values[name] = value.pin_memory() if pin else value.to(device)
    return replace(batch, **values)


class ResidentBatcher:
    """Preload the exact bounded advancing sequence, including partial updates."""

    def __init__(self, source: MixtureBatcher, updates: int) -> None:
        if not 1 <= updates <= 223:
            raise ValueError("Resident update count exceeds the diagnostic cap")
        self.initial_state = source.get_state()
        self.state = self.initial_state
        self.entries: list[tuple[list[TrainingBatch], dict[str, Any]]] = []
        self.index = 0
        self.pending = False
        begin = time.monotonic()
        for _ in range(updates):
            if time.monotonic() - begin > 180 or psutil.Process().memory_info().rss > 24 * 1024**3:
                raise RuntimeError("Resident fixture preparation exceeded time/RSS cap")
            batches = [move(mb, "cuda") for mb in source.next_step_microbatches()]
            for batch in batches:
                batch.metadata = dict(batch.metadata)
                batch.metadata["input_attention_mask"] = torch.as_tensor(
                    batch.metadata["input_attention_mask"], dtype=torch.bool, device="cuda"
                )
            source.commit()
            self.entries.append((batches, source.get_state()))
        source.load_state(self.initial_state)
        torch.cuda.synchronize()
        self.preparation_seconds = time.monotonic() - begin

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[TrainingBatch]:
        if self.pending or self.index >= len(self.entries):
            raise RuntimeError("Invalid consumption of the bounded resident fixture")
        batches = self.entries[self.index][0]
        count = sum(int(batch.metadata["valid_target_count"]) for batch in batches)
        if remaining_budget is not None and count > remaining_budget:
            raise RuntimeError("Resident update would exceed the exact remaining budget")
        self.pending = True
        return batches

    def commit(self) -> None:
        if not self.pending:
            raise RuntimeError("Cannot commit an unfetched resident update")
        self.state = self.entries[self.index][1]
        self.index += 1
        self.pending = False

    def rollback(self) -> None:
        self.pending = False

    def get_state(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)

    def load_state(self, state_dict: dict[str, Any]) -> None:
        raise RuntimeError("Compute-only fixture replay is not resumable training")


def smi() -> str:
    return subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=driver_version,memory.used,utilization.gpu,"
            "utilization.memory,power.draw,clocks.sm,clocks.mem,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=10,
    ).strip()


def cuda_event() -> Any:
    factory: Any = torch.cuda.Event
    return factory(enable_timing=True)


def measure_sections(trainer: Trainer) -> dict[str, Any]:
    """One separate instrumented update; event spans can include launch gaps."""
    import xlm.optimizers.clipping as clipping_module
    import xlm.training.trainer as trainer_module

    rows: list[tuple[str, float, Any, Any]] = []

    def measured(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            start, end = cuda_event(), cuda_event()
            start.record()
            begin = time.perf_counter()
            result = function(*args, **kwargs)
            elapsed = time.perf_counter() - begin
            end.record()
            rows.append((name, elapsed, start, end))
            return result

        return wrapped

    with ExitStack() as stack:
        for owner, attribute, name in (
            (trainer.batcher, "next_step_microbatches", "loader_cpu"),
            (trainer.exec_model, "forward", "forward"),
            (trainer.objective, "forward", "objective"),
            (trainer_module, "accumulate_microbatch_gradient", "backward"),
            (trainer_module, "clip_global_gradient_norm", "clipping"),
            (clipping_module, "gradients_are_finite", "finite_gradients"),
            (trainer.optimizer, "step", "optimizer"),
            (trainer.optimizer, "zero_grad", "zero_grad"),
        ):
            stack.enter_context(
                patch.object(owner, attribute, measured(name, getattr(owner, attribute)))
            )
        tensor_to = torch.Tensor.to

        def transfer(tensor: torch.Tensor, *values: Any, **kwargs: Any) -> Any:
            if tensor.device.type == "cpu" and values and str(values[0]).startswith("cuda"):
                return measured("H2D_tensor_to", tensor_to)(tensor, *values, **kwargs)
            return tensor_to(tensor, *values, **kwargs)

        stack.enter_context(patch.object(torch.Tensor, "to", transfer))
        begin = time.perf_counter()
        trainer.train_step()
        torch.cuda.synchronize()
        total_wall = time.perf_counter() - begin
    summary: dict[str, Any] = {"instrumented_update_wall_ms": total_wall * 1000}
    for name, wall, start, end in rows:
        result = summary.setdefault(name, {"cpu_wall_ms": 0.0, "cuda_span_ms": 0.0, "calls": 0})
        result["cpu_wall_ms"] += wall * 1000
        result["cuda_span_ms"] += start.elapsed_time(end)
        result["calls"] += 1
    return summary


def checkpoint_pause(trainer: Trainer, name: str) -> dict[str, Any]:
    """Inclusive phase timings: D2H is a subset of tensor serialization."""
    import xlm.artifacts.store as store_module
    import xlm.training.checkpoint as checkpoint_module

    timings: dict[str, float] = {}

    def timed(label: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            begin = time.perf_counter()
            result = function(*args, **kwargs)
            timings[label] = timings.get(label, 0.0) + time.perf_counter() - begin
            return result

        return wrapped

    manager = trainer.checkpoint_manager
    save_tensor = manager._save_tensor

    def save(state: Any, path: Path) -> None:
        timed("serialize_" + path.stem, save_tensor)(state, path)

    with ExitStack() as stack:
        for owner, attribute, label in (
            (torch.UntypedStorage, "cpu", "d2h_storage_copy"),
            (checkpoint_module, "serialize_optimizer_state", "optimizer_snapshot"),
            (store_module, "compute_file_sha256", "hashing"),
            (os, "fsync", "fsync"),
            (manager.store, "publish_artifact", "publication_including_hash_fsync"),
        ):
            stack.enter_context(
                patch.object(owner, attribute, timed(label, getattr(owner, attribute)))
            )
        stack.enter_context(patch.object(manager, "_save_tensor", save))
        torch.cuda.synchronize()
        begin = time.perf_counter()
        trainer._save_checkpoint(f"{name}_final")
        torch.cuda.synchronize()
        timings["total_pause"] = time.perf_counter() - begin
    return timings


def monitor(stop: threading.Event, rows: list[dict[str, Any]], start: float) -> None:
    while not stop.is_set():
        rss = psutil.Process().memory_info().rss
        if time.monotonic() - start > 900 or rss > 32 * 1024**3:
            os._exit(124)
        try:
            owned_bytes = owned_disk_bytes()
        except FileNotFoundError:
            owned_bytes = 0  # A temporary file may disappear during this sampled inventory.
        if owned_bytes > 8 * 1024**3:
            os._exit(125)
        try:
            rows.append({"elapsed": time.monotonic() - start, "smi": smi(), "rss": rss})
        except (OSError, subprocess.SubprocessError) as exc:
            rows.append({"telemetry_error": str(exc)})
        stop.wait(1.0)


def owned_disk_bytes() -> int:
    return sum(
        path.stat().st_size
        for root in (LOCAL, EVIDENCE)
        for path in root.rglob("*")
        if path.is_file()
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    head_at_start = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    harness_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    source_digests = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / "src/xlm").rglob("*.py")
    }
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    if args.vram_cap_gib:
        total_memory = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(args.vram_cap_gib * 1024**3 / total_memory)
    torch.set_num_threads(1)
    config = json.loads((ROOT / f"recipes/models/{args.model}.yaml").read_text())
    recipe = json.loads((ROOT / f"recipes/experiments/baseline_{args.model}.yaml").read_text())
    config["attention_backend"] = args.attention
    model = create_transformer_baseline(
        config, device="cuda", seed=101, activation_checkpointing=args.checkpointing
    )
    initial_digest = hashlib.sha256()
    for parameter in model.parameters():
        initial_digest.update(parameter.detach().cpu().numpy().tobytes())
    objective = CrossEntropyObjective().cuda()
    optimizer, manifest = create_adamw_optimizer(recipe["optimizer"], model, objective)
    for group in optimizer.param_groups:
        if args.optimizer != "default":
            group["foreach"] = args.optimizer == "foreach"
            group["fused"] = args.optimizer == "fused"
    schedule = WarmupCosineSchedule(
        WarmupCosineScheduleConfig(**recipe["training"]["schedule"]),
        base_lr=recipe["optimizer"]["lr"],
    )
    schedule.apply_lr_to_optimizer(optimizer, counter_value=0)
    source = batcher(args.microbatch, args.global_targets)
    if args.transfer != "pageable":
        fetch = source.next_step_microbatches

        def pinned_fetch(remaining_budget: int | None = None) -> list[TrainingBatch]:
            return [move(batch, "cpu", pin=True) for batch in fetch(remaining_budget)]

        source.next_step_microbatches = pinned_fetch  # type: ignore[method-assign]
    observed_counts: list[int] = []
    source_fetch = source.next_step_microbatches

    def observed_fetch(remaining_budget: int | None = None) -> list[TrainingBatch]:
        batches = source_fetch(remaining_budget)
        observed_counts.append(len(batches))
        return batches

    source.next_step_microbatches = observed_fetch  # type: ignore[method-assign]
    active: BatcherProtocol = (
        ResidentBatcher(source, args.warmup + args.steps + 2) if args.mode == "compute" else source
    )
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
        plan_id="p33-bounded-synthetic",
        device="cuda",
        precision=args.precision,
        max_valid_targets=(args.warmup + args.steps + 3) * args.global_targets,
        activation_checkpointing=args.checkpointing,
    )
    initial_logits: torch.Tensor | None = None
    if args.save_state:
        first = source.next_step_microbatches(args.global_targets)[0]
        source.rollback()
        observed_counts.pop()
        assert first.position_ids is not None
        with torch.no_grad(), trainer._get_autocast_context():
            initial_logits = (
                model(
                    first.input_ids.cuda(),
                    attention_mask=torch.tensor(
                        first.metadata["input_attention_mask"], device="cuda"
                    ),
                    position_ids=first.position_ids.cuda(),
                )
                .logits.detach()
                .float()
                .cpu()
            )
    rows: list[dict[str, Any]] = []
    stop = threading.Event()
    start = time.monotonic()
    thread = threading.Thread(target=monitor, args=(stop, rows, start), daemon=True)
    thread.start()
    try:
        for _ in range(args.warmup):
            if trainer.train_step() is None:
                raise RuntimeError("Warmup failed to consume an update")
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        gpu_start, gpu_end = cuda_event(), cuda_event()
        measurement_start = time.monotonic() - start
        gpu_start.record()
        begin = time.perf_counter()
        metrics = []
        for index in range(args.steps):
            metric = trainer.train_step()
            if metric is None:
                raise RuntimeError("Measured update missing")
            metrics.append(asdict(metric))
            if (index + 1) % 10 == 0:
                print(json.dumps({"name": args.name, "measured_updates": index + 1}), flush=True)
        gpu_end.record()
        torch.cuda.synchronize()
        wall = time.perf_counter() - begin
        measurement_end = time.monotonic() - start
        result = {
            "status": "VERIFIED",
            "args": vars(args),
            "head": head_at_start,
            "harness_sha256": harness_digest,
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch.__version__,
            "numpy": np.__version__,
            "initial_parameters_sha256": initial_digest.hexdigest(),
            "optimizer_recipe": recipe["optimizer"],
            "schedule_recipe": recipe["training"]["schedule"],
            "float32_matmul_precision": torch.get_float32_matmul_precision(),
            "allow_tf32": torch.backends.cuda.matmul.allow_tf32,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(),
            "vram_bytes": torch.cuda.get_device_properties(0).total_memory,
            "config": config,
            "parameters": sum(p.numel() for p in model.parameters()),
            "wall_seconds": wall,
            "cuda_elapsed_ms_including_launch_gaps": gpu_start.elapsed_time(gpu_end),
            "valid_targets": sum(m["valid_targets"] for m in metrics),
            "tokens_per_second": sum(m["valid_targets"] for m in metrics) / wall,
            "steps_per_second": args.steps / wall,
            "measured_microbatch_counts": (
                [len(entry[0]) for entry in active.entries[args.warmup : args.warmup + args.steps]]
                if isinstance(active, ResidentBatcher)
                else observed_counts[args.warmup : args.warmup + args.steps]
            ),
            "resident_preparation_seconds": active.preparation_seconds
            if isinstance(active, ResidentBatcher)
            else 0.0,
            "committed_cursor": active.get_state(),
            "max_allocated": torch.cuda.max_memory_allocated(),
            "max_reserved": torch.cuda.max_memory_reserved(),
            "rss": psutil.Process().memory_info().rss,
            "peak_rss": getattr(psutil.Process().memory_info(), "peak_wset", None),
            "metrics": metrics,
            "measurement_interval": [measurement_start, measurement_end],
            "fixture": source.readers["synthetic"].manifest.to_dict(),
            "triton_available": importlib.util.find_spec("triton") is not None,
            "source_sha256": source_digests,
        }
        if args.profile:
            result["sections"] = measure_sections(trainer)
            profile = cProfile.Profile()
            activities = [torch.profiler.ProfilerActivity.CPU, torch.profiler.ProfilerActivity.CUDA]
            with torch.profiler.profile(activities=activities, record_shapes=True) as prof:
                profile.enable()
                trainer.train_step()
                torch.cuda.synchronize()
                profile.disable()
            profile.dump_stats(str(LOCAL / f"{args.name}.pstats"))
            prof.export_chrome_trace(str(LOCAL / f"{args.name}.trace.json"))
            result["operators"] = [
                {
                    "key": event.key,
                    "count": event.count,
                    "cpu_us": event.cpu_time_total,
                    "self_cpu_us": event.self_cpu_time_total,
                    "device_us": event.device_time_total,
                }
                for event in prof.key_averages()
            ]
        if args.save_checkpoint:
            result["checkpoint_phases"] = checkpoint_pause(trainer, args.name)
            result["checkpoint_seconds"] = result["checkpoint_phases"]["total_pause"]
            result["checkpoint_bytes"] = sum(
                p.stat().st_size for p in manager.paths.root.rglob("*") if p.is_file()
            )
        if args.save_state:
            state_path = LOCAL / f"{args.name}.pt"
            temporary = state_path.with_suffix(".tmp")
            estimated_bytes = sum(
                value.numel() * value.element_size() for value in model.state_dict().values()
            )
            if initial_logits is not None:
                estimated_bytes += initial_logits.numel() * initial_logits.element_size()
            if owned_disk_bytes() + estimated_bytes + 8 * 1024**2 > 8 * 1024**3:
                raise RuntimeError("Diagnostic snapshot would exceed the aggregate disk cap")
            torch.save(
                {
                    "initial_logits": initial_logits,
                    "parameters": {
                        key: value.detach().cpu() for key, value in model.state_dict().items()
                    },
                },
                temporary,
            )
            temporary.replace(state_path)
            result["diagnostic_state"] = str(state_path.relative_to(ROOT))
        return result
    finally:
        stop.set()
        thread.join(timeout=12)
        source.close()
        if "result" in locals():
            result["telemetry"] = rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--name", default="diagnostic")
    parser.add_argument("--model", choices=["50m", "150m", "300m"], default="50m")
    parser.add_argument("--microbatch", type=int, default=8)
    parser.add_argument("--vram-cap-gib", type=float, default=0.0)
    parser.add_argument("--global-targets", type=int, default=65536)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--mode", choices=["compute", "end_to_end"], default="end_to_end")
    parser.add_argument(
        "--precision", choices=["bf16_fp32_master", "fp32"], default="bf16_fp32_master"
    )
    parser.add_argument("--attention", choices=["eager", "sdpa"], default="sdpa")
    parser.add_argument(
        "--optimizer", choices=["default", "scalar", "foreach", "fused"], default="default"
    )
    parser.add_argument("--checkpointing", action="store_true")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--save-checkpoint", action="store_true")
    parser.add_argument("--save-state", action="store_true")
    parser.add_argument("--reference-gradients", action="store_true")
    parser.add_argument("--reference-rmsnorm", action="store_true")
    parser.add_argument(
        "--transfer", choices=["pageable", "pinned", "nonblocking"], default="pageable"
    )
    args = parser.parse_args()
    if not (
        1 <= args.microbatch <= 128
        and 1 <= args.global_targets <= 65536
        and 0 <= args.warmup <= 20
        and 1 <= args.steps <= 200
        and 0 <= args.vram_cap_gib <= 23
    ):
        parser.error("Bound exceeded: microbatch<=128, targets<=65536, warmup<=20, steps<=200")
    if not args.name.replace("_", "").replace("-", "").isalnum():
        parser.error("Name must be alphanumeric with hyphens/underscores")
    if args.fixture:
        fixture()
        return
    output = EVIDENCE / f"{args.name}.json"
    if output.exists():
        raise FileExistsError(output)
    LOCAL.mkdir(parents=True, exist_ok=True)
    try:
        with ExitStack() as stack:
            if args.reference_rmsnorm:
                code = subprocess.check_output(
                    ["git", "show", REFERENCE_HEAD + ":src/xlm/models/rmsnorm.py"],
                    cwd=ROOT,
                    text=True,
                )
                reference_norm = types.ModuleType("p33_reference_rmsnorm")
                exec(compile(code, "p33_reference_rmsnorm.py", "exec"), reference_norm.__dict__)
                stack.enter_context(
                    patch("xlm.models.rmsnorm.RMSNorm.forward", reference_norm.RMSNorm.forward)
                )
            if args.transfer == "nonblocking":
                tensor_to = torch.Tensor.to

                def nonblocking_to(tensor: torch.Tensor, *values: Any, **kwargs: Any) -> Any:
                    if tensor.device.type == "cpu" and values and str(values[0]).startswith("cuda"):
                        kwargs["non_blocking"] = True
                    return tensor_to(tensor, *values, **kwargs)

                stack.enter_context(patch.object(torch.Tensor, "to", nonblocking_to))
            if args.reference_gradients:
                # Execute only this pinned, trusted repository implementation,
                # never a configurable module, external artifact or corpus text.
                code = subprocess.check_output(
                    ["git", "show", REFERENCE_HEAD + ":src/xlm/optimizers/clipping.py"],
                    cwd=ROOT,
                    text=True,
                )
                module = types.ModuleType("p33_reference_clipping")
                exec(compile(code, "p33_reference_clipping.py", "exec"), module.__dict__)
                stack.enter_context(
                    patch(
                        "xlm.training.trainer.clip_global_gradient_norm",
                        module.clip_global_gradient_norm,
                    )
                )
                stack.enter_context(
                    patch(
                        "xlm.optimizers.clipping.gradients_are_finite",
                        lambda ps: all(
                            not (torch.isnan(p.grad).any() or torch.isinf(p.grad).any())
                            for p in ps
                            if p.grad is not None
                        ),
                    )
                )
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
                    "max_reserved",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
