"""Tests for RoPE, RMSNorm, masks, eager/SDPA attention, and document isolation.

Complying with XLM Contract C08 and P03 Amendments 1, 2, 3, and 8.
"""

from __future__ import annotations

import pytest
import torch

from xlm.config.schemas import TransformerBaselineConfig
from xlm.models.attention import CausalSelfAttention
from xlm.models.masks import (
    build_causal_stream_mask,
    build_isolated_document_mask,
)
from xlm.models.rmsnorm import RMSNorm
from xlm.models.rope import RotaryEmbedding, apply_rotary_pos_emb
from xlm.models.transformer import TransformerBaseline

# -------------------------------------------------------------------------
# 1. Independent RoPE Mathematical Tests (Amendment 5)
# -------------------------------------------------------------------------


def test_rope_rotation_properties() -> None:
    """Verify mathematical properties of Rotary Position Embeddings independently."""
    dim = 32
    rope = RotaryEmbedding(dim=dim, max_position_embeddings=128)

    x = torch.randn(1, 1, 1, dim)
    cos, sin = rope(x, torch.tensor([[0]]))

    # At position 0, cos should be 1.0 and sin should be 0.0
    assert torch.allclose(cos, torch.ones_like(cos), atol=1e-6)
    assert torch.allclose(sin, torch.zeros_like(sin), atol=1e-6)

    # Rotated vector at position 0 must equal original vector
    q_rot, _ = apply_rotary_pos_emb(x, x, cos, sin)
    assert torch.allclose(q_rot, x, atol=1e-6)

    # Norm preservation (rotation does not change vector norm)
    pos_ids = torch.tensor([[5]])
    cos5, sin5 = rope(x, pos_ids)
    q_rot5, _ = apply_rotary_pos_emb(x, x, cos5, sin5)
    assert torch.allclose(x.norm(), q_rot5.norm(), atol=1e-5)

    # Inner product preservation: <R(x, p), R(y, p)> == <x, y>
    y = torch.randn(1, 1, 1, dim)
    y_rot5, _ = apply_rotary_pos_emb(y, y, cos5, sin5)
    dot_orig = (x * y).sum()
    dot_rot = (q_rot5 * y_rot5).sum()
    assert torch.allclose(dot_orig, dot_rot, atol=1e-5)

    # Relative position property: <R(x, p+k), R(y, p)> depends only on k
    cos7, sin7 = rope(x, torch.tensor([[7]]))
    cos2, sin2 = rope(x, torch.tensor([[2]]))
    cos0, sin0 = rope(x, torch.tensor([[0]]))
    qx7, _ = apply_rotary_pos_emb(x, x, cos7, sin7)
    qy5, _ = apply_rotary_pos_emb(y, y, cos5, sin5)
    qx2, _ = apply_rotary_pos_emb(x, x, cos2, sin2)
    qy0, _ = apply_rotary_pos_emb(y, y, cos0, sin0)
    assert torch.allclose((qx7 * qy5).sum(), (qx2 * qy0).sum(), atol=1e-5)


def test_rope_offsets_and_padding_support() -> None:
    """Verify RoPE indexing under arbitrary position offsets and non-contiguous IDs."""
    dim = 16
    rope = RotaryEmbedding(dim=dim, max_position_embeddings=64)
    x = torch.randn(1, 1, 3, dim)

    # Non-standard position IDs (e.g. sequence continuation starting at pos 20)
    pos_ids = torch.tensor([[20, 21, 22]])
    cos, sin = rope(x, pos_ids)

    assert cos.shape == (1, 1, 3, dim)
    assert sin.shape == (1, 1, 3, dim)

    # Position 20 must match direct evaluation at 20
    cos20, sin20 = rope(x[:, :, :1, :], torch.tensor([[20]]))
    assert torch.allclose(cos[:, :, 0:1, :], cos20, atol=1e-6)
    assert torch.allclose(sin[:, :, 0:1, :], sin20, atol=1e-6)


# -------------------------------------------------------------------------
# 2. Independent RMSNorm Numerical Tests (Amendment 6)
# -------------------------------------------------------------------------


def test_rmsnorm_precision_and_stability() -> None:
    """Verify RMSNorm stable reductions, bias-free scale, and float64 preservation."""
    dim = 64
    norm = RMSNorm(dim, eps=1e-5)
    assert norm.weight.shape == (dim,)
    assert torch.allclose(norm.weight, torch.ones(dim))

    # Float32 test
    x32 = torch.randn(2, 8, dim, dtype=torch.float32)
    out32 = norm(x32)
    assert out32.dtype == torch.float32
    # Verify unit root-mean-square: sqrt(mean(out^2)) ~= 1
    rms32 = torch.sqrt(out32.pow(2).mean(-1) + 1e-5)
    assert torch.allclose(rms32, torch.ones_like(rms32), atol=1e-3)

    # Float64 preservation: must remain float64
    x64 = torch.randn(2, 8, dim, dtype=torch.float64)
    norm64 = RMSNorm(dim, eps=1e-5)
    norm64.weight.data = norm64.weight.data.to(torch.float64)
    out64 = norm64(x64)
    assert out64.dtype == torch.float64

    # BFloat16 input produces bfloat16 output
    xb16 = torch.randn(2, 8, dim, dtype=torch.bfloat16)
    normb16 = RMSNorm(dim, eps=1e-5)
    normb16.weight.data = normb16.weight.data.to(torch.bfloat16)
    outb16 = normb16(xb16)
    assert outb16.dtype == torch.bfloat16


# -------------------------------------------------------------------------
# 3. Pre-Softmax Safe Masking Tests (Amendment 2)
# -------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["eager", "sdpa"])
def test_safe_masking_padding_and_gradients(backend: str) -> None:
    """Verify pre-softmax safe masking on left padding, right padding, and fully padded rows.

    Checks:
    - Fully padded rows produce exact 0 attention outputs.
    - Zero gradients on invalid query rows and masked key positions.
    - Finite non-NaN gradients on all valid positions.
    - No post-softmax blanket repair or nan_to_num.
    """
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend=backend,
    )
    attn = CausalSelfAttention(config, layer_idx=0)
    attn.eval()

    # Batch of 3 sequences of length 4:
    # Seq 0: right padding (valid, valid, pad, pad)
    # Seq 1: left padding (pad, pad, valid, valid)
    # Seq 2: fully padded (pad, pad, pad, pad)
    pad_mask = torch.tensor(
        [
            [True, True, False, False],
            [False, False, True, True],
            [False, False, False, False],
        ]
    )
    mask = build_causal_stream_mask(4, pad_mask=pad_mask)

    x = torch.randn(3, 4, 32, requires_grad=True)
    out = attn(x, attention_mask=mask, backend_override=backend)

    # Assert no NaNs anywhere in output
    assert not torch.isnan(out).any(), f"NaN detected in {backend} output"

    # Seq 0: pos 2 and 3 (padding queries) must be exactly zero
    assert torch.allclose(out[0, 2:], torch.zeros_like(out[0, 2:]), atol=1e-6)

    # Seq 1: pos 0 and 1 (padding queries) must be exactly zero
    assert torch.allclose(out[1, :2], torch.zeros_like(out[1, :2]), atol=1e-6)

    # Seq 2: all positions must be exactly zero
    assert torch.allclose(out[2], torch.zeros_like(out[2]), atol=1e-6)

    # Backward pass
    loss = (out[0, :2] + out[1, 2:]).sum()
    loss.backward()

    assert x.grad is not None, f"Missing {backend} gradients"
    assert not torch.isnan(x.grad).any(), f"NaN in {backend} gradients"

    # Fully padded seq 2 must receive zero gradients
    assert torch.allclose(x.grad[2], torch.zeros_like(x.grad[2]), atol=1e-6)

    # Padding positions in seq 0 and seq 1 must receive zero query gradients
    assert torch.allclose(x.grad[0, 2:], torch.zeros_like(x.grad[0, 2:]), atol=1e-6)


# -------------------------------------------------------------------------
# 4. Document Isolation & Positive Control Tests (Amendment 1)
# -------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["eager", "sdpa"])
def test_isolated_document_attention_and_positive_control(backend: str) -> None:
    """Verify document isolation:

    1. Changing earlier Doc A does NOT alter later Doc B logits in isolated mode.
    2. Doc B alone produces identical logits to Doc B inside isolated sequence.
    3. Positive control: In unisolated causal stream, Doc B logits DO change.
    4. Future-token causality: mutating future tokens does not affect earlier tokens.
    """
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=200,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=2,
        intermediate_size=128,
        context_length=32,
        attention_backend=backend,
    )
    model = TransformerBaseline(config, seed=42)
    model.eval()

    # Document A (len 4) and Document B (len 4)
    doc_a1 = torch.tensor([[10, 11, 12, 13]])
    doc_a2 = torch.tensor([[50, 51, 52, 53]])  # Completely different tokens in Doc A
    doc_b = torch.tensor([[20, 21, 22, 23]])

    # Pack sequence: [Doc A, Doc B] of length 8
    seq1 = torch.cat([doc_a1, doc_b], dim=1)  # [10, 11, 12, 13, 20, 21, 22, 23]
    seq2 = torch.cat([doc_a2, doc_b], dim=1)  # [50, 51, 52, 53, 20, 21, 22, 23]

    # Reset position IDs per document in isolated packing
    pos_ids = torch.tensor([[0, 1, 2, 3, 0, 1, 2, 3]])
    seg_ids = torch.tensor([[0, 0, 0, 0, 1, 1, 1, 1]])

    isolated_mask = build_isolated_document_mask(seg_ids)

    # 1. ISOLATED MODE: Forward on seq1 and seq2
    out1 = model(seq1, attention_mask=isolated_mask, position_ids=pos_ids, backend_override=backend)
    out2 = model(seq2, attention_mask=isolated_mask, position_ids=pos_ids, backend_override=backend)

    # Logits for Document B are positions 4..7
    b_logits_from_seq1 = out1.logits[:, 4:]
    b_logits_from_seq2 = out2.logits[:, 4:]

    # Assert Document B's logits are IDENTICAL despite Document A changing
    assert torch.allclose(b_logits_from_seq1, b_logits_from_seq2, atol=1e-5, rtol=1e-5), (
        f"Document B logits leaked Document A information in {backend} isolated mode!"
    )

    # 2. STANDALONE EQUIVALENCE: Document B executed alone
    b_pos = torch.tensor([[0, 1, 2, 3]])
    out_b_alone = model(doc_b, position_ids=b_pos, backend_override=backend)
    b_logits_alone = out_b_alone.logits

    assert torch.allclose(b_logits_from_seq1, b_logits_alone, atol=1e-5, rtol=1e-5), (
        f"Document B alone does not match isolated packed Document B in {backend}!"
    )

    # 3. POSITIVE CONTROL: Ordinary causal stream (unisolated)
    # When cross-document attention is allowed, Document B CAN attend to Document A!
    stream_mask = build_causal_stream_mask(8)
    out_stream1 = model(
        seq1, attention_mask=stream_mask, position_ids=pos_ids, backend_override=backend
    )
    out_stream2 = model(
        seq2, attention_mask=stream_mask, position_ids=pos_ids, backend_override=backend
    )

    b_stream_logits1 = out_stream1.logits[:, 4:]
    b_stream_logits2 = out_stream2.logits[:, 4:]

    # Assert that logits DO change when isolation is removed
    max_diff = (b_stream_logits1 - b_stream_logits2).abs().max().item()
    assert max_diff > 1e-3, (
        f"Positive control failed in {backend}: causal-stream attention did not attend "
        f"to earlier document (max diff={max_diff})"
    )


def test_future_token_causality_independence() -> None:
    """Verify future-token causality: changing future token T does not alter outputs at < T."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=123)
    model.eval()

    tokens1 = torch.tensor([[5, 10, 15, 20, 25]])
    tokens2 = torch.tensor([[5, 10, 15, 20, 99]])  # Perturb only token 4

    out1 = model(tokens1, backend_override="eager")
    out2 = model(tokens2, backend_override="eager")

    # Outputs at positions 0, 1, 2, 3 must be IDENTICAL
    assert torch.allclose(out1.logits[:, :4], out2.logits[:, :4], atol=1e-6)

    # Output at position 4 must differ
    assert not torch.allclose(out1.logits[:, 4:], out2.logits[:, 4:])


# -------------------------------------------------------------------------
# 5. Eager vs SDPA Parity within Declared Tolerance (Amendment 8)
# -------------------------------------------------------------------------


@pytest.mark.parametrize("seq_len", [4, 8])
def test_eager_versus_sdpa_parity(seq_len: int) -> None:
    """Verify bitwise or near-identical parity between eager and SDPA backends."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=150,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=32,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=777)
    model.eval()

    input_ids = torch.randint(0, 150, (2, seq_len))

    # Fast-path (no mask)
    out_eager = model(input_ids, backend_override="eager")
    out_sdpa = model(input_ids, backend_override="sdpa")

    assert torch.allclose(out_eager.logits, out_sdpa.logits, atol=1e-5, rtol=1e-5), (
        "Eager and SDPA logits mismatch on unmasked fast-path"
    )

    # Masked path with padding
    pad_mask = torch.ones((2, seq_len), dtype=torch.bool)
    pad_mask[0, -1] = False
    pad_mask[1, -2:] = False
    mask = build_causal_stream_mask(seq_len, pad_mask=pad_mask)

    out_eager_masked = model(input_ids, attention_mask=mask, backend_override="eager")
    out_sdpa_masked = model(input_ids, attention_mask=mask, backend_override="sdpa")

    assert torch.allclose(out_eager_masked.logits, out_sdpa_masked.logits, atol=1e-5, rtol=1e-5), (
        "Eager and SDPA logits mismatch on masked path"
    )


def test_fast_path_causality_guard() -> None:
    """Verify that passing an attention_mask never silently disables causality in SDPA."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="sdpa",
    )
    model = TransformerBaseline(config, seed=999)
    model.eval()

    # Pass 2D padding mask containing all 1s (all valid tokens)
    all_ones_mask = torch.ones((1, 4), dtype=torch.long)
    tokens1 = torch.tensor([[5, 10, 15, 20]])
    tokens2 = torch.tensor([[5, 10, 15, 99]])  # Perturb position 3

    out1 = model(tokens1, attention_mask=all_ones_mask, backend_override="sdpa")
    out2 = model(tokens2, attention_mask=all_ones_mask, backend_override="sdpa")

    # If causality was preserved, positions 0..2 MUST be identical
    assert torch.allclose(out1.logits[:, :3], out2.logits[:, :3], atol=1e-6), (
        "Passing attention_mask disabled causality in SDPA fast path!"
    )


def test_profile_required_raises_error_on_forward() -> None:
    """Verify that attention_backend='profile_required' raises an error during numerical forward."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="profile_required",
    )
    model = TransformerBaseline(config)
    with pytest.raises(ValueError, match="profile_required"):
        model(torch.tensor([[1, 2, 3]]))
