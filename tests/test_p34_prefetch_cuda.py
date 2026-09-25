"""The prefetching batcher leaves actual-50M B8 CUDA training bit exact."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.cuda
def test_actual_50m_prefetched_updates_match_synchronous_loader(tmp_path: Path) -> None:
    """Frozen P33 fixture, 65,536 targets per update, release LR; compare every bit."""
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    from xlm.config.schemas import WarmupCosineScheduleConfig
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
    from xlm.models.transformer import create_transformer_baseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.trainer import Trainer

    spec = importlib.util.spec_from_file_location(
        "p34_cuda_harness", ROOT / "scripts/benchmark_p33.py"
    )
    assert spec is not None and spec.loader is not None
    harness: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    harness.LOCAL = tmp_path / "local"
    harness.EVIDENCE = tmp_path / "evidence"
    harness.fixture()
    frozen = json.loads((ROOT / "docs/implementation/evidence/p33/fixture.json").read_text())
    assert (
        json.loads((harness.EVIDENCE / "fixture.json").read_text())["manifest"]["checksum_sha256"]
        == frozen["manifest"]["checksum_sha256"]
    )
    config = json.loads((ROOT / "recipes/models/50m.yaml").read_text())
    config["attention_backend"] = "sdpa"
    recipe = json.loads((ROOT / "recipes/experiments/baseline_50m.yaml").read_text())
    trajectories = []
    for prefetch in (False, True):
        source = harness.batcher(8, 65536)
        batcher: Any = (
            PrefetchingBatcher(
                ProducerSpec.from_batcher(source), source.get_state(), verify_content=True
            )
            if prefetch
            else source
        )
        model = create_transformer_baseline(config, device="cuda", seed=101)
        objective = CrossEntropyObjective().cuda()
        optimizer, manifest = create_adamw_optimizer(recipe["optimizer"], model, objective)
        trainer = Trainer(
            model,
            objective,
            optimizer,
            manifest,
            WarmupCosineSchedule(
                WarmupCosineScheduleConfig(**recipe["training"]["schedule"]),
                base_lr=recipe["optimizer"]["lr"],
            ),
            batcher,
            CheckpointManager(paths=ArtifactPaths(root=tmp_path / str(prefetch))),
            run_id="p34-equivalence",
            plan_id="p34",
            device="cuda",
            precision="bf16_fp32_master",
            max_valid_targets=3 * 65536,
        )
        digests: list[str] = []
        rates: list[float] = []

        def record(
            opt: Any,
            args: Any,
            kwargs: Any,
            digests: list[str] = digests,
            rates: list[float] = rates,
        ) -> None:
            rates.append(float(opt.param_groups[0]["lr"]))
            digest = hashlib.sha256()
            for group in opt.param_groups:
                for parameter in group["params"]:
                    if parameter.grad is not None:
                        digest.update(parameter.grad.detach().cpu().numpy().tobytes())
            digests.append(digest.hexdigest())

        optimizer.register_step_pre_hook(record)
        try:
            metrics = []
            for _ in range(3):
                metric = trainer.train_step()
                assert metric is not None
                metrics.append(metric.to_dict() | {"step_time_seconds": 0.0})
            state = batcher.get_state()
        finally:
            if prefetch:
                batcher.close()
            source.close()
        trajectories.append(
            (
                metrics,
                digests,
                rates,
                state,
                {k: v.cpu().clone() for k, v in model.state_dict().items()},
            )
        )
        assert rates[0] == recipe["optimizer"]["lr"]
        del trainer, optimizer, model, objective
        torch.cuda.empty_cache()
    (m0, g0, r0, s0, p0), (m1, g1, r1, s1, p1) = trajectories
    assert m0 == m1 and g0 == g1 and r0 == r1 and s0 == s1
    assert s1["committed_valid_targets"] == 3 * 65536
    for name, expected in p0.items():
        assert torch.equal(p1[name], expected), name
