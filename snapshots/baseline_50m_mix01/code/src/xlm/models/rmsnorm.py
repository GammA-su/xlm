"""Bias-free Root Mean Square Layer Normalization (RMSNorm)."""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn


class RMSNorm(nn.Module):
    """Bias-free Root Mean Square Layer Normalization.

    Complying with XLM Contract C08:
    - Bias-free: only learnable scale parameter `weight`.
    - Numerically stable reductions: float16/bfloat16 computed in float32.
    - Preserves float64 precision for double-precision inputs/tests.
    """

    def __init__(self, hidden_size: int, eps: float = 1e-5, device: Any = None) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(hidden_size, device=device))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Apply RMSNorm to input tensor x."""
        orig_dtype = x.dtype
        # Low precision dtypes compute variance in float32 for stability; float64 is preserved.
        if orig_dtype in (torch.float16, torch.bfloat16):
            compute_dtype = torch.float32
        else:
            compute_dtype = orig_dtype

        x_compute = x.to(compute_dtype)
        variance = x_compute.pow(2).mean(-1, keepdim=True)
        rsqrt = torch.rsqrt(variance + self.eps)
        normalized = (x_compute * rsqrt).to(orig_dtype)
        return normalized * self.weight

    def extra_repr(self) -> str:
        return f"{self.hidden_size}, eps={self.eps}"
