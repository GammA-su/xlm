"""Rotary Position Embeddings (RoPE) complying with XLM Contract C08."""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    """Rotates half the hidden dimensions of the input."""
    d_half = x.shape[-1] // 2
    x1 = x[..., :d_half]
    x2 = x[..., d_half:]
    return torch.cat((-x2, x1), dim=-1)


class RotaryEmbedding(nn.Module):
    """Rotary Position Embedding for multi-head attention.

    Convention:
    - Frequencies: theta_i = base^(-2i / dim) for i in [0, dim // 2).
    - Half-rotation: (-x[..., dim//2:], x[..., :dim//2]).
    - Supports arbitrary explicit position_ids, including offsets and padded positions.
    """

    inv_freq: torch.Tensor
    cos_cached: torch.Tensor
    sin_cached: torch.Tensor

    def __init__(
        self,
        dim: int,
        max_position_embeddings: int = 2048,
        base: float = 10000.0,
        device: Any = None,
    ) -> None:
        super().__init__()
        if dim % 2 != 0:
            raise ValueError(f"RoPE dimension must be even, got {dim}")
        self.dim = dim
        self.max_position_embeddings = max_position_embeddings
        self.base = base

        # Precompute theta frequencies: shape (dim // 2,)
        pos_seq = torch.arange(0, self.dim, 2, dtype=torch.float32, device=device)
        inv_freq = 1.0 / (self.base ** (pos_seq / self.dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)

        # Build initial cache
        self._build_cache(max_position_embeddings, device=device)

    def _build_cache(self, seq_len: int, device: Any = None) -> None:
        target_device = device if device is not None else self.inv_freq.device
        t = torch.arange(seq_len, dtype=torch.float32, device=target_device)
        freqs = torch.outer(t, self.inv_freq)  # (seq_len, dim // 2)
        emb = torch.cat((freqs, freqs), dim=-1)  # (seq_len, dim)
        self.register_buffer("cos_cached", emb.cos(), persistent=False)
        self.register_buffer("sin_cached", emb.sin(), persistent=False)
        self.max_position_embeddings = seq_len

    def forward(
        self,
        x: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Get (cos, sin) tensors for specified position_ids.

        Args:
            x: Representative tensor for dtype/device extraction.
            position_ids: Explicit position IDs of shape (batch, seq_len).

        Returns:
            cos, sin of shape (batch, 1, seq_len, dim).
        """
        max_pos = int(position_ids.max().item()) if position_ids.numel() > 0 else 0
        if max_pos >= self.max_position_embeddings:
            # Dynamically extend cache if required
            new_len = max(max_pos + 1, self.max_position_embeddings * 2)
            self._build_cache(new_len, device=x.device)

        # position_ids shape: (batch, seq_len)
        # cos_cached shape: (max_len, dim)
        cos = self.cos_cached[position_ids].to(dtype=x.dtype)  # (batch, seq_len, dim)
        sin = self.sin_cached[position_ids].to(dtype=x.dtype)  # (batch, seq_len, dim)

        # Unsqueeze head dimension: (batch, 1, seq_len, dim)
        return cos.unsqueeze(1), sin.unsqueeze(1)


def apply_rotary_pos_emb(
    q: torch.Tensor,
    k: torch.Tensor,
    cos: torch.Tensor,
    sin: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Apply rotary position embeddings to query and key states.

    Args:
        q: Query tensor of shape (batch, heads, q_len, head_dim).
        k: Key tensor of shape (batch, heads, k_len, head_dim).
        cos: Cosine tensor of shape (batch, 1, seq_len, head_dim).
        sin: Sine tensor of shape (batch, 1, seq_len, head_dim).

    Returns:
        Rotated (q, k) tensors of same shape.
    """
    q_embed = (q * cos) + (rotate_half(q) * sin)
    k_embed = (k * cos) + (rotate_half(k) * sin)
    return q_embed, k_embed
