"""Single-size calibration worker for `xlm profile`, run in a separate process.

The parent spawns one worker per microbatch candidate so an OOM kills only the
worker: the production/persistent process never observes a poisoned CUDA
context. The worker exercises the real stack (model, objective, optimizer,
trainer, checkpoint) on deterministic synthetic tokens and writes one JSON
result file. Usage: ``python -m xlm.training.profile_worker REQ.json RES.json``.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def _synthetic_tokens(vocab_size: int, count: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.randrange(4, vocab_size) for _ in range(count)]


def run_calibration(request: dict[str, Any]) -> dict[str, Any]:
    """Build the real stack and measure one microbatch size. Never returns OOM."""
    import torch

    from xlm.models.backends import resolve_attention_backend
    from xlm.models.transformer import create_transformer_baseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.constant import ConstantSchedule, ConstantScheduleConfig
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    device = str(request["device"])
    model_id = str(request.get("model_id", "unknown"))
    precision = str(request["precision"])
    microbatch_sequences = int(request["microbatch_sequences"])
    context_length = int(request["context_length"])
    dry_steps = int(request["dry_steps"])
    seed = int(request["seed"])
    model_config = dict(request["model_config"])

    backend_report = resolve_attention_backend(
        str(request["attention_backend"]), device, arbitrary_mask=False
    )
    model_config["attention_backend"] = backend_report.selected

    timings: dict[str, float] = {}
    build_start = time.monotonic()
    model = create_transformer_baseline(
        model_config,
        device=device,
        seed=seed,
        activation_checkpointing=bool(request.get("activation_checkpointing", False)),
    )
    objective = CrossEntropyObjective()
    optimizer, manifest = create_adamw_optimizer(
        {"lr": 1e-3, "weight_decay": 0.1}, model=model, objective=objective
    )
    schedule = ConstantSchedule(ConstantScheduleConfig())
    timings["build_seconds"] = time.monotonic() - build_start

    tokens = _synthetic_tokens(int(model_config["vocab_size"]), 200_000, seed)
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=microbatch_sequences * context_length,
        microbatch_sequences=microbatch_sequences,
        exhaustion_policy="repeat_bounded",
        max_document_exposures=10_000,
    )

    compile_report: dict[str, Any] = {"compiled": False, "mode": None}
    compile_seconds = 0.0
    trainer = Trainer(
        model=model,
        objective=objective,
        optimizer=optimizer,
        optimizer_manifest=manifest,
        schedule=schedule,
        batcher=batcher,
        checkpoint_manager=CheckpointManager(),
        run_id=f"profile_{model_id}_{microbatch_sequences}",
        plan_id="profile_plan",
        device=device,
        precision=precision,
        gradient_clip_norm=1.0,
        max_valid_targets=10**12,
        activation_checkpointing=False,  # already applied at construction above
        compile_model=bool(request.get("compile", False)),
        compile_mode=str(request.get("compile_mode", "default")),
    )
    compile_report = dict(trainer.compile_report)

    # Loader-wait sample: time raw batcher fetches outside any compute.
    fetch_times: list[float] = []
    for _ in range(5):
        fetch_start = time.monotonic()
        fetched = batcher.next_step_microbatches(
            remaining_budget=microbatch_sequences * context_length
        )
        fetch_times.append(time.monotonic() - fetch_start)
        if not fetched:
            break
        batcher.rollback()
    loader_wait_seconds = sum(fetch_times) / max(1, len(fetch_times))

    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()

    # Warmup (untimed) then measured steps with device synchronization.
    trainer.train_step()
    if device == "cuda":
        torch.cuda.synchronize()
    measured_targets = 0
    step_start = time.monotonic()
    for _ in range(dry_steps):
        metrics = trainer.train_step()
        if metrics is None:
            raise RuntimeError("synthetic batcher exhausted during dry steps")
        measured_targets += metrics.valid_targets
    if device == "cuda":
        torch.cuda.synchronize()
    step_seconds = time.monotonic() - step_start
    timings["compile_seconds"] = compile_seconds

    peak_allocated_gib = 0.0
    peak_reserved_gib = 0.0
    if device == "cuda":
        peak_allocated_gib = torch.cuda.max_memory_allocated() / (1024**3)
        peak_reserved_gib = torch.cuda.max_memory_reserved() / (1024**3)

    import psutil

    rss_gib = psutil.Process().memory_info().rss / (1024**3)

    # Exact optimizer tensor bytes after a real update (serialized form excluded).
    optimizer_state_bytes = 0
    for state in optimizer.state.values():
        for value in state.values():
            if isinstance(value, torch.Tensor):
                optimizer_state_bytes += value.numel() * value.element_size()

    # Real checkpoint: time the save and measure the artifact on disk.
    ckpt_start = time.monotonic()
    ckpt_dir = trainer._save_checkpoint(f"profile_{model_id}_ckpt_{microbatch_sequences}")
    checkpoint_save_seconds = time.monotonic() - ckpt_start
    checkpoint_bytes = sum(p.stat().st_size for p in Path(ckpt_dir).rglob("*") if p.is_file())

    tokens_per_sec = measured_targets / step_seconds if step_seconds > 0 else 0.0
    return {
        "feasible": True,
        "microbatch_sequences": microbatch_sequences,
        "measured_valid_targets_per_step": measured_targets // max(1, dry_steps),
        "tokens_per_sec": tokens_per_sec,
        "step_seconds": step_seconds / max(1, dry_steps),
        "peak_allocated_gib": peak_allocated_gib,
        "peak_reserved_gib": peak_reserved_gib,
        "cpu_rss_gib": rss_gib,
        "loader_wait_seconds": loader_wait_seconds,
        "optimizer_state_bytes": optimizer_state_bytes,
        "checkpoint_bytes": checkpoint_bytes,
        "checkpoint_save_seconds": checkpoint_save_seconds,
        "build_seconds": timings["build_seconds"],
        "compile": compile_report,
        "backend": backend_report.to_dict(),
        "execution": trainer.execution_report(),
    }


def main(argv: list[str]) -> int:
    request_path = Path(argv[1])
    result_path = Path(argv[2])
    if len(argv) > 3 and argv[3]:
        os.environ["XLM_HOME"] = argv[3]
    request = json.loads(request_path.read_text(encoding="utf-8"))
    try:
        result = run_calibration(request)
    except Exception as exc:
        oom = "OutOfMemoryError" in type(exc).__name__ or "out of memory" in str(exc).lower()
        result = {
            "feasible": False,
            "microbatch_sequences": request.get("microbatch_sequences"),
            "failure_reason": ("cuda_oom" if oom else "worker_error") + f": {exc}",
            "traceback": traceback.format_exc(limit=5),
        }
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
