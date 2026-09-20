"""Deterministic weight initialization policies complying with XLM Contract C08."""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn


def init_weights(
    model: nn.Module,
    num_layers: int,
    policy: str = "baseline_v1",
    seed: int | None = None,
    device: Any = None,
) -> None:
    """Initialize model parameters according to named initialization policy.

    Args:
        model: PyTorch model to initialize.
        num_layers: Total number of transformer layers L (for residual scaling).
        policy: Named initialization policy ('baseline_v1').
        seed: Optional integer seed for deterministic initialization.
        device: Target device for generator.
    """
    if policy != "baseline_v1":
        raise ValueError(f"Unsupported initialization policy: '{policy}'. Supported: 'baseline_v1'")

    generator: torch.Generator | None = None
    if seed is not None:
        # Avoid creating CPU generator for meta-device tensors
        gen_device = "cpu" if device is None or str(device) == "meta" else device
        generator = torch.Generator(device=gen_device).manual_seed(seed)

    base_std = 0.02
    residual_std = base_std / math.sqrt(2.0 * num_layers)

    # Track already-initialized parameter IDs so tied parameters are initialized once
    initialized_param_ids: set[int] = set()

    for name, module in model.named_modules():
        # Embedding layer
        if isinstance(module, nn.Embedding):
            param = module.weight
            if id(param) not in initialized_param_ids:
                if generator is not None:
                    param.data.normal_(mean=0.0, std=base_std, generator=generator)
                else:
                    nn.init.normal_(param, mean=0.0, std=base_std)
                initialized_param_ids.add(id(param))

        # Linear projections
        elif isinstance(module, nn.Linear):
            param = module.weight
            if id(param) in initialized_param_ids:
                continue

            # Residual projections scaled by 1 / sqrt(2 * L)
            if name.endswith("out_proj") or name.endswith("down_proj"):
                std = residual_std
            else:
                std = base_std

            if generator is not None:
                param.data.normal_(mean=0.0, std=std, generator=generator)
            else:
                nn.init.normal_(param, mean=0.0, std=std)

            initialized_param_ids.add(id(param))

            if module.bias is not None:
                nn.init.zeros_(module.bias)

        # RMSNorm layers
        elif module.__class__.__name__ == "RMSNorm":
            param = module.weight
            if id(param) not in initialized_param_ids:
                nn.init.ones_(param)
                initialized_param_ids.add(id(param))
