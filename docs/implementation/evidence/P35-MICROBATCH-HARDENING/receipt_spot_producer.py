"""Bounded authored RTX 4090 receipt diagnostic on the process_depth1 producer path.

Never a throughput qualification: tiny authored model, authored shards. Same
ABBA design as the Astra readiness spot (receipt disabled/enabled/enabled/
disabled, four warmup updates excluded per block), but through the real spawned
P34 producer, where hardening added consumed-tensor binding and the producer
seal re-check. Bounds: <= 200,000 targets, <= 300 s, <= 1 GiB output.

Usage: python receipt_spot_producer.py <scratch_dir> <output.json>
"""

from __future__ import annotations

import gc
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil
import torch

from p35_eval_support import build_trainer
from test_trainer_mixture import BODIES, make_doc
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling import update_payload as payload
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer

CONTEXT = 64
GLOBAL = 2048
WARMUP = 4
MEASURED = 16
BLOCKS = (False, True, True, False)
MAX_TARGETS = 200_000
MAX_SECONDS = 300
MAX_BYTES = 1024**3


def write_shards(root: Path) -> dict[str, TokenShardReader]:
    readers = {}
    for source_id, body in BODIES.items():
        directory = root / "shards" / source_id
        TokenShardWriter(
            directory, f"shard_{source_id}", source_id, ByteTokenizer(), pool_hash="pool_spot"
        ).write_documents(
            [make_doc(f"{source_id}_{i}", body * (4 + i % 5), source_id) for i in range(64)],
            add_special_tokens=True,
        )
        readers[source_id] = TokenShardReader(directory)
    return readers


def producer(readers: dict[str, TokenShardReader]) -> PrefetchingBatcher:
    tokenizer = ByteTokenizer()
    source = MixtureBatcher(
        MixtureRecipe(
            mixture_id="spot_mix",
            components=[
                MixtureComponent(source_id="prose_src", weight=0.6),
                MixtureComponent(source_id="code_src", weight=0.4),
            ],
            exhaustion=ExhaustionPolicy(repeat=True, max_epochs=64),
        ),
        dict(readers),
        context_length=CONTEXT,
        global_batch_valid_targets=GLOBAL,
        microbatch_sequences=8,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        emit_tensors=True,
        max_open_shards=2,
    )
    return PrefetchingBatcher(
        ProducerSpec.from_batcher(source), source.get_state(), verify_content=True
    )


def main(root: Path, output: Path) -> None:
    began = time.monotonic()
    assert GLOBAL * (WARMUP + MEASURED) * len(BLOCKS) <= MAX_TARGETS
    torch.set_num_threads(1)
    readers = write_shards(root)
    results = []
    total_targets = 0
    receipt_times: list[float] = []
    binding_times: list[float] = []
    original_canonical = payload.canonical_update
    original_bind = payload.bind_consumed
    original_commit = payload.UpdatePayloadChain.commit
    original_stage = payload.UpdatePayloadChain.stage
    original_sync = torch.cuda.Stream.synchronize
    sync_calls = [0]

    def synchronize(stream: Any) -> Any:
        sync_calls[0] += 1
        return original_sync(stream)

    def timed(function: Any, sink: list[float]) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                sink.append(time.perf_counter() - start)

        return call

    for index, enabled in enumerate(BLOCKS):
        assert time.monotonic() - began < MAX_SECONDS
        gc.collect()
        torch.cuda.empty_cache()
        trainer = build_trainer(
            root / f"block_{index}",
            budget=GLOBAL * (WARMUP + MEASURED),
            global_batch=GLOBAL,
            context=CONTEXT,
            device="cuda",
        )
        trainer.batcher = producer(readers)
        if enabled:
            trainer.science.update_payloads = payload.UpdatePayloadChain()
        parameter_count = sum(p.numel() for p in trainer.model.parameters())
        try:
            with (
                # canonical_update includes bind_consumed; binding is also timed alone.
                patch.object(payload, "canonical_update", timed(original_canonical, receipt_times)),
                patch.object(payload, "bind_consumed", timed(original_bind, binding_times)),
                patch.object(
                    payload.UpdatePayloadChain, "stage", timed(original_stage, receipt_times)
                ),
                patch.object(
                    payload.UpdatePayloadChain, "commit", timed(original_commit, receipt_times)
                ),
                patch.object(torch.cuda.Stream, "synchronize", synchronize),
            ):
                for _ in range(WARMUP):
                    assert trainer.train_step() is not None
                    total_targets += GLOBAL
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                receipt_times.clear()
                binding_times.clear()
                sync_calls[0] = 0
                started = time.perf_counter()
                for _ in range(MEASURED):
                    assert time.monotonic() - began < MAX_SECONDS
                    assert trainer.train_step() is not None
                    total_targets += GLOBAL
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
        finally:
            trainer.batcher.close()
        results.append(
            {
                "receipt_enabled": enabled,
                "measured_updates": MEASURED,
                "warmup_updates_excluded": WARMUP,
                "measured_targets": GLOBAL * MEASURED,
                "elapsed_seconds": elapsed,
                "targets_per_second": GLOBAL * MEASURED / elapsed,
                "receipt_cpu_ms_per_update": 1000 * sum(receipt_times) / MEASURED,
                "binding_cpu_ms_per_update": 1000 * sum(binding_times) / MEASURED,
                "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
                "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
                "process_rss_bytes": psutil.Process().memory_info().rss,
                "unique_deployed_parameters": parameter_count,
                "chain_head": trainer.science.update_payloads.head if enabled else None,
                "explicit_stream_synchronizations_per_update": sync_calls[0] / MEASURED,
            }
        )
        del trainer
    disabled = [r for r in results if not r["receipt_enabled"]]
    enabled_rows = [r for r in results if r["receipt_enabled"]]
    off = statistics.mean(r["targets_per_second"] for r in disabled)
    on = statistics.mean(r["targets_per_second"] for r in enabled_rows)
    assert len({r["chain_head"] for r in enabled_rows}) == 1
    disk = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    assert disk < MAX_BYTES and total_targets <= MAX_TARGETS
    report = {
        "scope": (
            "authored tiny CUDA Trainer, B8, fp32 eager strict, real spawned process_depth1 "
            "producer with content verification; NOT pilot-size throughput"
        ),
        "design": "ABBA; identical seed/data/global updates; four warmups excluded per block",
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "python": sys.version,
        "blocks": results,
        "total_targets_including_warmup": total_targets,
        "wall_seconds": time.monotonic() - began,
        "output_bytes_before_report": disk,
        "targets_per_second_delta_percent": 100 * (on / off - 1),
        "peak_gpu_memory_delta_bytes": max(r["peak_cuda_allocated_bytes"] for r in enabled_rows)
        - max(r["peak_cuda_allocated_bytes"] for r in disabled),
        "new_cuda_sync": (
            "Stream.synchronize calls instrumented in both arms; the receipt and binding read "
            "CPU arrays only. This is not a driver-level synchronization profile."
        ),
        "limitation": (
            "Small model and authored shards do not estimate 50M/B8 production overhead; "
            "block spread may exceed the arm difference."
        ),
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
