"""Nonnovel optimizer control: baseline AdamW updates through the plugin seam."""

from __future__ import annotations

from typing import Any

import torch

from xlm.config.schemas import StrictConfigModel
from xlm.core.registry import Registry


class NoOpOptimizerConfig(StrictConfigModel):
    """Configuration for the no-op optimizer control."""

    type: str = "noop_optimizer"
    version: str = "1"
    lr: float = 0.001
    betas: tuple[float, float] = (0.9, 0.95)
    eps: float = 1e-8
    weight_decay: float = 0.1


class NoOpAdamW(torch.optim.AdamW):
    """Baseline AdamW mathematics, plugin packaging. Intentionally behavior-free."""

    def __init__(
        self,
        params: Any,
        lr: float = 0.001,
        betas: tuple[float, float] = (0.9, 0.95),
        eps: float = 1e-8,
        weight_decay: float = 0.1,
    ) -> None:
        super().__init__(params, lr=lr, betas=betas, eps=eps, weight_decay=weight_decay)


def create_noop_optimizer(
    config: NoOpOptimizerConfig | dict[str, Any] | None = None,
    model: Any = None,
) -> NoOpAdamW:
    """Factory creating the no-op optimizer over a model's parameters."""
    cfg = config or NoOpOptimizerConfig()
    if isinstance(cfg, dict):
        cfg = NoOpOptimizerConfig(**cfg)
    if model is None:
        raise ValueError("noop_optimizer requires a model to optimize")
    return NoOpAdamW(
        model.parameters(),
        lr=cfg.lr,
        betas=cfg.betas,
        eps=cfg.eps,
        weight_decay=cfg.weight_decay,
    )


def register(registry: Registry[Any], capabilities: Any) -> Any:
    """Register the no-op optimizer control."""
    caps = capabilities.to_dict() if hasattr(capabilities, "to_dict") else dict(capabilities)
    return registry.register(
        "noop_optimizer",
        "1",
        NoOpOptimizerConfig,
        capabilities=caps,
        factory=create_noop_optimizer,
    )
