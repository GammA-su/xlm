"""One bounded actual-50M receipt diagnostic; 196,608 targets, 300 s, 1 GiB."""

from __future__ import annotations

import gc
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psutil
import torch

from p35_eval_support import science_state
from test_trainer_mixture import BODIES, make_doc
from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.config.schemas import AdamWConfig, WarmupCosineScheduleConfig
from xlm.core.paths import ArtifactPaths
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling import update_payload as payload
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.models.transformer import create_transformer_baseline
from xlm.objectives.cross_entropy import CrossEntropyObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.cosine import WarmupCosineSchedule
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.checkpoint import CheckpointManager
from xlm.training.science import reseed_training_rng
from xlm.training.trainer import Trainer

GLOBAL = 65_536


def main(root: Path, output: Path) -> None:
    began = time.monotonic()
    root.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    torch.cuda.set_per_process_memory_fraction(0.5)
    readers = {}
    for source, body in BODIES.items():
        path = root / "data" / source
        TokenShardWriter(
            path, source, source, ByteTokenizer(), pool_hash="authored_cost"
        ).write_documents(
            [make_doc(f"{source}_{i}", body * 128, source) for i in range(16)],
            add_special_tokens=True,
        )
        readers[source] = TokenShardReader(path)
    results = []
    for label, enabled in (("warmup", False), ("off", False), ("on", True)):
        assert time.monotonic() - began < 300
        gc.collect()
        torch.cuda.empty_cache()
        config = json.loads(Path("recipes/models/50m.yaml").read_text())
        config["attention_backend"] = "sdpa"
        model = create_transformer_baseline(config, seed=101).to("cuda")
        count = sum(p.numel() for p in model.parameters())
        assert count == 49_883_648
        optimizer, manifest = create_adamw_optimizer(AdamWConfig(lr=1e-3), model=model)
        schedule = WarmupCosineSchedule(
            WarmupCosineScheduleConfig(
                warmup_valid_targets=10_000_000,
                horizon_valid_targets=1_000_000_000,
                min_lr_ratio=0.1,
            ),
            base_lr=1e-3,
        )
        sync = MixtureBatcher(
            MixtureRecipe(
                mixture_id="authored_cost",
                components=[MixtureComponent(source_id=s, weight=0.5) for s in sorted(readers)],
                exhaustion=ExhaustionPolicy(repeat=True, max_epochs=4),
            ),
            dict(readers),
            context_length=512,
            global_batch_valid_targets=GLOBAL,
            microbatch_sequences=8,
            pad_token_id=0,
            bos_token_id=1,
            eos_token_id=2,
            emit_tensors=True,
            max_open_shards=2,
        )
        producer = PrefetchingBatcher(
            ProducerSpec.from_batcher(sync), sync.get_state(), verify_content=True
        )
        paths = ArtifactPaths(root=root / label)
        manager = CheckpointManager(
            artifact_store=ArtifactStore(paths),
            run_ledger=RunLedger(paths.ledger / "ledger.sqlite"),
            paths=paths,
        )
        state = science_state(
            runtime={
                "attention_policy": "statistical_efficient_v1",
                "matmul_tf32": "disabled",
                "bf16_reduced_precision_reduction": "allowed",
            }
        )
        state.train_start_rng = reseed_training_rng(10001, "cuda")
        if enabled:
            state.update_payloads = payload.UpdatePayloadChain()
        trainer = Trainer(
            model,
            CrossEntropyObjective(),
            optimizer,
            manifest,
            schedule,
            producer,
            manager,
            label,
            "bounded_50m_diagnostic",
            device="cuda",
            precision="bf16_fp32_master",
            max_valid_targets=GLOBAL,
            max_train_seconds=120,
            science=state,
        )
        times: dict[str, list[float]] = {
            k: [] for k in ("canonical", "bind", "digest", "stage", "commit")
        }
        sync_count = [0]
        calls = []
        take = producer.next_step_microbatches

        def record_batches(take: Any = take, calls: Any = calls, **kwargs: Any) -> Any:
            batches = take(**kwargs)
            calls.extend(int(b.input_ids.shape[0]) for b in batches)
            return batches

        producer.next_step_microbatches = record_batches

        def timed(function: Any, name: str, times: Any = times) -> Any:
            def call(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    times[name].append(time.perf_counter() - start)

            return call

        original_sync = torch.cuda.Stream.synchronize

        def synchronize(
            stream: Any, sync_count: Any = sync_count, original_sync: Any = original_sync
        ) -> Any:
            sync_count[0] += 1
            return original_sync(stream)

        try:
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            with (
                patch.object(
                    payload, "canonical_update", timed(payload.canonical_update, "canonical")
                ),
                patch.object(payload, "bind_consumed", timed(payload.bind_consumed, "bind")),
                patch.object(
                    payload.CanonicalUpdate,
                    "digest",
                    timed(payload.CanonicalUpdate.digest, "digest"),
                ),
                patch.object(
                    payload.UpdatePayloadChain,
                    "stage",
                    timed(payload.UpdatePayloadChain.stage, "stage"),
                ),
                patch.object(
                    payload.UpdatePayloadChain,
                    "commit",
                    timed(payload.UpdatePayloadChain.commit, "commit"),
                ),
                patch.object(torch.cuda.Stream, "synchronize", synchronize),
            ):
                start = time.perf_counter()
                metrics = trainer.train_step()
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - start
            assert metrics is not None and trainer.committed_valid_targets == GLOBAL
            assert max(calls) == 8 and all(n <= 8 for n in calls)
            rss = psutil.Process().memory_info().rss
            tree_rss = rss + sum(
                p.memory_info().rss for p in psutil.Process().children(recursive=True)
            )
            results.append(
                {
                    "label": label,
                    "receipt": enabled,
                    "targets": GLOBAL,
                    "seconds": elapsed,
                    "targets_per_second": GLOBAL / elapsed,
                    "cpu_ms": {k: 1000 * sum(v) for k, v in times.items()},
                    "receipt_total_cpu_ms": 1000
                    * sum(sum(times[k]) for k in ("canonical", "digest", "stage", "commit")),
                    "stream_sync_count": sync_count[0],
                    "measurement_global_sync_count": 2,
                    "peak_allocated": torch.cuda.max_memory_allocated(),
                    "peak_reserved": torch.cuda.max_memory_reserved(),
                    "rss_bytes": rss,
                    "tree_rss_bytes": tree_rss,
                    "microbatch_rows": calls,
                    "unique_parameters": count,
                    "chain": state.update_payloads.to_dict() if enabled else None,
                }
            )
        finally:
            producer.close()
        del trainer, model, optimizer, producer, sync, state, manager
        assert sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) < 1024**3
    off, on = results[1:]
    report = {
        "scope": (
            "actual 50M architecture, authored tokens only; performance diagnostic, "
            "NOT training evidence"
        ),
        "design": (
            "one full warmup + one complete off and on update, fresh identical "
            "initialization per block; fixed order, no uncertainty estimate"
        ),
        "total_targets": 3 * GLOBAL,
        "wall_seconds": time.monotonic() - began,
        "output_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
        "targets_per_second_delta_percent": 100
        * (on["targets_per_second"] / off["targets_per_second"] - 1),
        "device": torch.cuda.get_device_name(),
        "torch": torch.__version__,
        "python": sys.version,
        "blocks": results,
        "limitations": (
            "one update per arm, cold producer and optimizer startup, shared desktop, "
            "no population overhead or quality inference; stream sync instrumentation "
            "is not a driver trace"
        ),
    }
    assert report["wall_seconds"] < 300
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if sys.argv[1] == "child":
        main(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        result = subprocess.run(
            [sys.executable, __file__, "child", *sys.argv[1:]], timeout=295, check=False
        )
        raise SystemExit(result.returncode)
