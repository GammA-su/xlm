"""Multi-head causal self-attention with RoPE, eager and SDPA backends."""

from __future__ import annotations

import math
from typing import Any, cast

import torch
import torch.nn as nn
import torch.nn.functional as F

from xlm.config.schemas import TransformerBaselineConfig
from xlm.models.masks import build_causal_mask
from xlm.models.rope import RotaryEmbedding, apply_rotary_pos_emb


class CausalSelfAttention(nn.Module):
    """Bias-free multi-head causal self-attention complying with XLM Contract C08.

    Features:
    - RoPE applied to query and key projections.
    - Dual execution backends: 'eager' and 'sdpa'.
    - Safe pre-softmax masking without invalid all-masked evaluations.
    - Fast causal path when no mask is provided; causality strictly preserved when mask is provided.
    - Dropout is strictly 0.0 by default, and disabled during eval mode.
    """

    def __init__(
        self,
        config: TransformerBaselineConfig,
        layer_idx: int = 0,
        device: Any = None,
    ) -> None:
        super().__init__()
        self.hidden_size = config.hidden_size
        self.num_heads = config.num_attention_heads
        self.head_dim = self.hidden_size // self.num_heads
        self.layer_idx = layer_idx
        self.attention_backend = config.attention_backend
        self.dropout_rate = config.dropout

        if self.head_dim * self.num_heads != self.hidden_size:
            raise ValueError(
                f"hidden_size ({self.hidden_size}) must be divisible by "
                f"num_heads ({self.num_heads})"
            )
        if self.head_dim % 2 != 0:
            raise ValueError(f"head_dim ({self.head_dim}) must be even for RoPE")

        self.q_proj = nn.Linear(self.hidden_size, self.hidden_size, bias=False, device=device)
        self.k_proj = nn.Linear(self.hidden_size, self.hidden_size, bias=False, device=device)
        self.v_proj = nn.Linear(self.hidden_size, self.hidden_size, bias=False, device=device)
        self.out_proj = nn.Linear(self.hidden_size, self.hidden_size, bias=False, device=device)

        self.rotary_emb = RotaryEmbedding(
            dim=self.head_dim,
            max_position_embeddings=config.context_length,
            device=device,
        )

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        backend_override: str | None = None,
    ) -> torch.Tensor:
        """Forward pass of CausalSelfAttention.

        Args:
            hidden_states: Input tensor of shape (batch, seq_len, hidden_size).
            attention_mask: Prepared boolean mask of shape (batch, 1/heads, seq_len, seq_len)
                            or None for standard causal fast-path.
            position_ids: Explicit position IDs of shape (batch, seq_len).
            backend_override: Optional backend override ('eager' or 'sdpa').

        Returns:
            Output tensor of shape (batch, seq_len, hidden_size).
        """
        backend = backend_override or self.attention_backend
        if backend == "profile_required":
            raise ValueError(
                "attention_backend 'profile_required' must be resolved to 'eager' or 'sdpa' "
                "before numerical execution."
            )
        if backend not in ("eager", "sdpa"):
            raise ValueError(f"Unsupported attention_backend: '{backend}'")

        batch_size, seq_len, _ = hidden_states.shape

        # Linear projections
        q = self.q_proj(hidden_states)
        k = self.k_proj(hidden_states)
        v = self.v_proj(hidden_states)

        # Reshape to (batch, num_heads, seq_len, head_dim)
        q = q.view(batch_size, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        k = k.view(batch_size, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        v = v.view(batch_size, seq_len, self.num_heads, self.head_dim).transpose(1, 2)

        # Default position IDs if not provided
        if position_ids is None:
            position_ids = torch.arange(seq_len, dtype=torch.long, device=hidden_states.device)
            position_ids = position_ids.unsqueeze(0).expand(batch_size, -1)

        # Apply RoPE
        cos, sin = self.rotary_emb(v, position_ids)
        q, k = apply_rotary_pos_emb(q, k, cos, sin)

        dropout_p = self.dropout_rate if self.training else 0.0

        if backend == "sdpa":
            if attention_mask is None:
                # Fast causal path
                attn_output = F.scaled_dot_product_attention(
                    q,
                    k,
                    v,
                    attn_mask=None,
                    dropout_p=dropout_p,
                    is_causal=True,
                )
            else:
                # Supplied mask already includes causality; pass with is_causal=False
                attn_output = F.scaled_dot_product_attention(
                    q,
                    k,
                    v,
                    attn_mask=attention_mask,
                    dropout_p=dropout_p,
                    is_causal=False,
                )
        else:
            # Eager manual reference attention path
            attn_output = self._eager_attention(q, k, v, attention_mask, dropout_p)

        # Transpose back and project output: (batch, seq_len, hidden_size)
        attn_output = (
            attn_output.transpose(1, 2).contiguous().view(batch_size, seq_len, self.hidden_size)
        )
        return cast(torch.Tensor, self.out_proj(attn_output))

    def _eager_attention(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        attention_mask: torch.Tensor | None,
        dropout_p: float,
    ) -> torch.Tensor:
        """Reference eager attention path with safe pre-softmax masking."""
        seq_len = q.shape[-2]
        scores = torch.matmul(q, k.transpose(-1, -2)) / math.sqrt(self.head_dim)

        if attention_mask is None:
            # Construct standard causal lower-triangular boolean mask
            mask = build_causal_mask(seq_len, device=scores.device).unsqueeze(0).unsqueeze(0)
        else:
            mask = attention_mask

        # Amendment 2: Pre-softmax safe masking strategy
        # Identify rows that have at least one valid key to attend to
        valid_rows = mask.any(dim=-1, keepdim=True)  # (batch, heads, q_len, 1)

        # safe_mask guarantees every query row has at least one True entry
        safe_mask = mask | (~valid_rows)

        # For masked positions in safe_mask, assign -inf (or -1e9 for half precision)
        neg_inf = -1e9 if scores.dtype in (torch.float16, torch.bfloat16) else float("-inf")
        masked_scores = torch.where(safe_mask, scores, scores.new_tensor(neg_inf))

        # Stable softmax computation
        if scores.dtype == torch.float64:
            attn_weights = F.softmax(masked_scores, dim=-1)
        else:
            attn_weights = F.softmax(masked_scores, dim=-1, dtype=torch.float32).to(scores.dtype)

        # Zero out invalid query rows so they make zero contribution and receive zero gradients
        attn_weights = torch.where(valid_rows, attn_weights, torch.zeros_like(attn_weights))

        if dropout_p > 0.0:
            attn_weights = F.dropout(attn_weights, p=dropout_p, training=self.training)

        return torch.matmul(attn_weights, v)
