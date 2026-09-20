"""Attention mask construction and validation complying with XLM Contracts C07 & C08."""

from __future__ import annotations

from typing import Any

import torch


def build_causal_mask(seq_len: int, device: Any = None) -> torch.Tensor:
    """Build lower-triangular boolean causal mask of shape (seq_len, seq_len).

    True indicates allowed attention (j <= i); False indicates masked future token.
    """
    return torch.tril(torch.ones(seq_len, seq_len, dtype=torch.bool, device=device))


def build_causal_stream_mask(
    seq_len: int,
    pad_mask: torch.Tensor | None = None,
    device: Any = None,
) -> torch.Tensor:
    """Build causal stream mask with optional padding exclusion.

    Args:
        seq_len: Sequence length.
        pad_mask: Optional boolean or integer tensor of shape (batch, seq_len)
                  where True/1 indicates a valid token and False/0 indicates padding.
        device: Target tensor device.

    Returns:
        Boolean mask of shape (batch, 1, seq_len, seq_len) or (1, 1, seq_len, seq_len).
    """
    causal = build_causal_mask(seq_len, device=device)  # (seq_len, seq_len)
    if pad_mask is None:
        return causal.unsqueeze(0).unsqueeze(0)

    # pad_mask shape: (batch, seq_len)
    # Key padding: query can only attend to key j if pad_mask[b, j] is True.
    # Query padding: if query i is padding, its entire row in pad_mask is False.
    if pad_mask.dtype != torch.bool:
        bool_pad = pad_mask != 0
    else:
        bool_pad = pad_mask

    # (batch, 1, 1, seq_len) for keys & (batch, 1, seq_len, 1) for queries
    key_valid = bool_pad.unsqueeze(1).unsqueeze(2)  # (batch, 1, 1, seq_len)
    query_valid = bool_pad.unsqueeze(1).unsqueeze(3)  # (batch, 1, seq_len, 1)

    # Combined: causal & key_valid & query_valid
    combined = causal.unsqueeze(0).unsqueeze(0) & key_valid & query_valid
    return combined


def build_isolated_document_mask(
    segment_ids: torch.Tensor,
    pad_mask: torch.Tensor | None = None,
    device: Any = None,
) -> torch.Tensor:
    """Build isolated-document causal mask complying with Contract C07.

    Tokens within document A cannot attend to tokens in document B, even if B
    precedes A in the packed sequence. Causality is strictly enforced.

    Args:
        segment_ids: Document/segment assignment IDs of shape (batch, seq_len).
        pad_mask: Optional padding mask of shape (batch, seq_len).
        device: Target tensor device.

    Returns:
        Boolean mask of shape (batch, 1, seq_len, seq_len).
    """
    batch_size, seq_len = segment_ids.shape
    dev = device or segment_ids.device

    causal = (
        build_causal_mask(seq_len, device=dev).unsqueeze(0).unsqueeze(0)
    )  # (1, 1, seq_len, seq_len)

    # Within-segment condition: segment_ids[b, i] == segment_ids[b, j]
    seg_q = segment_ids.unsqueeze(2)  # (batch, seq_len, 1)
    seg_k = segment_ids.unsqueeze(1)  # (batch, 1, seq_len)
    same_segment = (seg_q == seg_k).unsqueeze(1)  # (batch, 1, seq_len, seq_len)

    combined = causal & same_segment

    if pad_mask is not None:
        bool_pad = pad_mask != 0 if pad_mask.dtype != torch.bool else pad_mask
        key_valid = bool_pad.unsqueeze(1).unsqueeze(2)  # (batch, 1, 1, seq_len)
        query_valid = bool_pad.unsqueeze(1).unsqueeze(3)  # (batch, 1, seq_len, 1)
        combined = combined & key_valid & query_valid

    return combined


def prepare_attention_mask(
    attention_mask: torch.Tensor | None,
    batch_size: int,
    seq_len: int,
    device: Any = None,
) -> torch.Tensor | None:
    """Prepare and enforce causality on an optional user-supplied attention mask.

    Guarantees that passing an explicit attention_mask never silently disables
    causality (Amendment 3).
    """
    causal = build_causal_mask(seq_len, device=device)  # (seq_len, seq_len)

    if attention_mask is None:
        # None signals to use the causal fast-path (e.g. is_causal=True in SDPA)
        return None

    if attention_mask.dim() == 2:
        # 2D (batch, seq_len) padding mask
        bool_pad = attention_mask != 0 if attention_mask.dtype != torch.bool else attention_mask
        key_valid = bool_pad.unsqueeze(1).unsqueeze(2)  # (batch, 1, 1, seq_len)
        query_valid = bool_pad.unsqueeze(1).unsqueeze(3)  # (batch, 1, seq_len, 1)
        return causal.unsqueeze(0).unsqueeze(0) & key_valid & query_valid

    elif attention_mask.dim() == 3:
        # 3D (batch, seq_len, seq_len)
        if attention_mask.dtype == torch.bool:
            return (attention_mask & causal.unsqueeze(0)).unsqueeze(1)
        else:
            # Additive float: <= -1e4 is masked out
            bool_mask = attention_mask > -1e4
            return (bool_mask & causal.unsqueeze(0)).unsqueeze(1)

    elif attention_mask.dim() == 4:
        # 4D (batch, heads, seq_len, seq_len) or (batch, 1, seq_len, seq_len)
        if attention_mask.dtype == torch.bool:
            return attention_mask & causal.unsqueeze(0).unsqueeze(0)
        else:
            bool_mask = attention_mask > -1e4
            return bool_mask & causal.unsqueeze(0).unsqueeze(0)

    else:
        raise ValueError(
            f"Unsupported attention_mask dimension: {attention_mask.dim()}. "
            "Expected 2D (batch, seq), 3D (batch, seq, seq), or 4D (batch, heads, seq, seq)"
        )
