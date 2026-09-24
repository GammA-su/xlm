"""Consolidated CUDA transfers preserve clipping, guards and update arithmetic."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

import pytest
import torch
from torch import nn

from xlm.optimizers.clipping import clip_global_gradient_norm, gradients_are_finite


def reference_clip(
    parameters: Iterable[nn.Parameter], max_norm: float, norm_type: float = 2.0
) -> float:
    """Frozen pre-P33 scalar-transfer algorithm, including Python accumulation."""
    unique = {id(p): p for p in parameters if p.grad is not None}
    norms = [torch.linalg.vector_norm(p.grad, ord=norm_type).item() for p in unique.values()]
    total = 0.0
    for n in norms:
        total += n * n if norm_type == 2 else n**norm_type
    norm = math.sqrt(total) if norm_type == 2 else total ** (1 / norm_type)
    if not math.isfinite(norm):
        raise ValueError("Non-finite gradient norm")
    coefficient = max_norm / (norm + 1e-6)
    if coefficient < 1:
        for parameter in unique.values():
            assert parameter.grad is not None
            parameter.grad.mul_(coefficient)
    return norm


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda", marks=pytest.mark.cuda)])
@pytest.mark.parametrize("norm_type", [1.0, 2.0, 3.0])
def test_clipping_is_exact_with_ties_and_unused_parameters(device: str, norm_type: float) -> None:
    if device == "cuda" and not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    generator = torch.Generator().manual_seed(33)
    reference, candidate = [], []
    for size in (1, 513, 8192):
        grad = torch.randn(size, generator=generator, dtype=torch.float32).to(device)
        a, b = nn.Parameter(torch.zeros_like(grad)), nn.Parameter(torch.zeros_like(grad))
        a.grad, b.grad = grad.clone(), grad.clone()
        reference.append(a)
        candidate.append(b)
    unused = nn.Parameter(torch.ones(2, device=device))
    expected = reference_clip([*reference, reference[0], unused], 0.7, norm_type)
    actual = clip_global_gradient_norm([*candidate, candidate[0], unused], 0.7, norm_type)
    assert actual == expected
    for a, b in zip(reference, candidate, strict=True):
        torch.testing.assert_close(a.grad, b.grad, rtol=0, atol=0)
    assert unused.grad is None


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda", marks=pytest.mark.cuda)])
@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf"), -float("inf")])
def test_finite_guard_checks_late_parameters_and_empty_gradients(device: str, value: float) -> None:
    if device == "cuda" and not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    parameters = [nn.Parameter(torch.ones(8, device=device)) for _ in range(4)]
    for parameter in parameters[1:]:
        parameter.grad = torch.ones_like(parameter)
    last_grad = parameters[-1].grad
    assert last_grad is not None
    last_grad[-1] = value
    assert gradients_are_finite(parameters) == math.isfinite(value)
    assert gradients_are_finite([])


@pytest.mark.cuda
def test_finite_elements_with_overflowed_norm_still_fail_clipping() -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    parameter = nn.Parameter(torch.ones(2, device="cuda"))
    parameter.grad = torch.full_like(parameter, 3e38)
    assert gradients_are_finite([parameter])
    with pytest.raises(ValueError, match="Non-finite gradient norm"):
        clip_global_gradient_norm([parameter], 1.0)


@pytest.mark.cuda
def test_actual_50m_three_updates_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full preset, BF16, tied weights, accumulation; compare every updated weight."""
    if not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    from xlm.config.schemas import WarmupCosineScheduleConfig
    from xlm.core.paths import ArtifactPaths
    from xlm.models.transformer import create_transformer_baseline
    from xlm.objectives.cross_entropy import CrossEntropyObjective
    from xlm.optimizers.adamw import create_adamw_optimizer
    from xlm.schedules.cosine import WarmupCosineSchedule
    from xlm.training.checkpoint import CheckpointManager
    from xlm.training.data import TrainingBatcher
    from xlm.training.trainer import Trainer

    config = json.loads(Path("recipes/models/50m.yaml").read_text())
    config["attention_backend"] = "sdpa"
    tokens = torch.randint(4, 32768, (4096,), generator=torch.Generator().manual_seed(33)).tolist()
    trajectories = []
    for reference in (True, False):
        model = create_transformer_baseline(config, device="cuda", seed=101)
        objective = CrossEntropyObjective().cuda()
        optimizer, manifest = create_adamw_optimizer(None, model, objective)
        batcher = TrainingBatcher(
            tokens, context_length=512, global_batch_valid_targets=1024, microbatch_sequences=1
        )
        trainer = Trainer(
            model,
            objective,
            optimizer,
            manifest,
            WarmupCosineSchedule(
                WarmupCosineScheduleConfig(
                    warmup_valid_targets=10000000, horizon_valid_targets=1000000000
                ),
                base_lr=0.001,
            ),
            batcher,
            CheckpointManager(paths=ArtifactPaths(root=tmp_path / str(reference))),
            run_id="equivalence",
            plan_id="p33",
            device="cuda",
            precision="bf16_fp32_master",
            max_valid_targets=3072,
        )
        trainer.schedule.apply_lr_to_optimizer(optimizer, 0)
        metrics = []
        with monkeypatch.context() as patch:
            if reference:
                patch.setattr("xlm.training.trainer.clip_global_gradient_norm", reference_clip)
                patch.setattr(
                    "xlm.optimizers.clipping.gradients_are_finite",
                    lambda ps: all(
                        not (torch.isnan(p.grad).any() or torch.isinf(p.grad).any())
                        for p in ps
                        if p.grad is not None
                    ),
                )
            for _ in range(3):
                metric = trainer.train_step()
                assert metric is not None
                metrics.append(
                    (
                        metric.loss,
                        metric.grad_norm,
                        metric.valid_targets,
                        metric.learning_rate,
                        metric.committed_valid_targets,
                    )
                )
        trajectories.append(
            (
                metrics,
                {k: v.cpu().clone() for k, v in model.state_dict().items()},
                batcher.get_state(),
            )
        )
        del trainer, optimizer, model, objective
        torch.cuda.empty_cache()
    assert trajectories[0][0] == trajectories[1][0]
    assert trajectories[0][2] == trajectories[1][2]
    for name, expected in trajectories[0][1].items():
        torch.testing.assert_close(trajectories[1][1][name], expected, rtol=0, atol=0)
