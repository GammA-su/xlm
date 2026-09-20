"""Bias-free SwiGLU feed-forward network complying with XLM Contract C08."""

from __future__ import annotations

from typing import Any, cast

import torch
import torch.nn as nn
import torch.nn.functional as F


class SwiGLU(nn.Module):
    """Bias-free SwiGLU feed-forward network.

    Computation: down_proj(SiLU(gate_proj(x)) * up_proj(x))
    """

    def __init__(
        self,
        hidden_size: int,
        intermediate_size: int,
        device: Any = None,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size

        self.gate_proj = nn.Linear(hidden_size, intermediate_size, bias=False, device=device)
        self.up_proj = nn.Linear(hidden_size, intermediate_size, bias=False, device=device)
        self.down_proj = nn.Linear(intermediate_size, hidden_size, bias=False, device=device)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass of SwiGLU."""
        return cast(torch.Tensor, self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x)))
