"""Tests for objective plugins, loss normalization, and masking.

Complying with XLM Contract C09, Acceptance Requirement A06, and P04 Amendments 1, 2, 3, 4.
"""

from __future__ import annotations

import math

import pytest
import torch
import torch.nn as nn
import torch.nn.functional as F

from xlm.config.schemas import TransformerBaselineConfig
from xlm.core.contracts import LMOutput, LossResult, TrainingBatch
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
from xlm.objectives.base import (
    BaseObjective,
    InvalidBatchError,
    ObjectiveCapabilities,
    UnsupportedBatchingError,
    accumulate_microbatch_gradient,
)
from xlm.objectives.cross_entropy import (
    CrossEntropyObjective,
    compute_independent_diagnostic_ce,
)
from xlm.objectives.noop import NoOpObjective
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.optimizers.clipping import clip_global_gradient_norm

# -------------------------------------------------------------------------
# 1. Hand-sized PyTorch calculation cross-check & Float64 preservation
# -------------------------------------------------------------------------


def test_cross_entropy_hand_sized_reference_calculation() -> None:
    """Verify CE agrees with a direct hand-sized calculation in float64."""
    # Fixed synthetic logits and labels in float64
    logits = torch.tensor(
        [
            [[2.0, 1.0, 0.1, -1.0], [0.5, 2.5, 0.0, 1.0], [1.0, 1.0, 1.0, 1.0]],
            [[-0.5, 0.0, 1.5, -2.0], [3.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 2.0]],
        ],
        dtype=torch.float64,
    )
    labels = torch.tensor([[1, 0, 3], [2, 0, 1]], dtype=torch.long)
    # Mask out (batch 0, token 2) and (batch 1, token 1)
    loss_mask = torch.tensor([[1, 1, 0], [1, 0, 1]], dtype=torch.bool)

    # Hand calculation using direct PyTorch operations
    valid_indices = [(0, 0, 1), (0, 1, 0), (1, 0, 2), (1, 2, 1)]
    expected_nll_sum = 0.0
    for b, s, target in valid_indices:
        token_logits = logits[b, s]
        log_probs = F.log_softmax(token_logits, dim=-1)
        expected_nll_sum += -log_probs[target].item()

    expected_denominator = len(valid_indices)  # 4
    expected_mean_loss = expected_nll_sum / expected_denominator

    objective = CrossEntropyObjective()
    model_output = LMOutput(logits=logits)
    batch = TrainingBatch(input_ids=torch.zeros_like(labels), labels=labels, loss_mask=loss_mask)

    res = objective(model_output, batch)

    assert res.valid_target_denominator == expected_denominator
    assert abs(res.unscaled_loss_sum - expected_nll_sum) < 1e-9
    assert abs(res.loss.item() - expected_mean_loss) < 1e-9
    assert res.loss.dtype == torch.float64
    assert abs(res.diagnostics["ce_loss"] - expected_mean_loss) < 1e-9
    assert abs(res.diagnostics["perplexity"] - math.exp(expected_mean_loss)) < 1e-9

    # Check independent diagnostic function agrees
    diag_sum, diag_count = compute_independent_diagnostic_ce(logits, labels, loss_mask)
    assert diag_count == expected_denominator
    assert abs(diag_sum - expected_nll_sum) < 1e-9


# -------------------------------------------------------------------------
# 2. Masking exclusions, valid EOS retention, and Sentinel safety (Amendment 3)
# -------------------------------------------------------------------------


def test_masking_excludes_padding_bos_and_retains_eos() -> None:
    """Verify padding, BOS, and ignored targets are excluded, while valid EOS is retained."""
    logits = torch.randn(1, 4, 10, dtype=torch.float32)
    # Positions: 0: BOS (ignored), 1: token A (valid), 2: token B (valid), 3: EOS (valid)
    labels = torch.tensor([[1, 5, 7, 2]], dtype=torch.long)  # 2 is EOS ID
    loss_mask = torch.tensor([[0, 1, 1, 1]], dtype=torch.bool)  # BOS masked out, EOS kept

    objective = CrossEntropyObjective()
    model_output = LMOutput(logits=logits)
    batch = TrainingBatch(input_ids=labels, labels=labels, loss_mask=loss_mask)

    res = objective(model_output, batch)
    assert res.valid_target_denominator == 3  # positions 1, 2, 3

    # Now mask out EOS as well (e.g. padding after EOS)
    loss_mask_with_pad = torch.tensor([[0, 1, 1, 0]], dtype=torch.bool)
    batch_pad = TrainingBatch(input_ids=labels, labels=labels, loss_mask=loss_mask_with_pad)
    res_pad = objective(model_output, batch_pad)
    assert res_pad.valid_target_denominator == 2


def test_ignored_label_sentinels_handled_safely_before_indexing() -> None:
    """Verify that ignored labels with sentinel -100 or out-of-bounds IDs are safe."""
    logits = torch.randn(2, 4, 32, dtype=torch.float32)
    # Masked positions contain sentinel -100 and out-of-bounds 9999
    labels = torch.tensor([[-100, 5, 12, 9999], [8, -100, -100, 15]], dtype=torch.long)
    loss_mask = torch.tensor([[0, 1, 1, 0], [1, 0, 0, 1]], dtype=torch.bool)

    objective = CrossEntropyObjective()
    model_output = LMOutput(logits=logits)
    batch = TrainingBatch(input_ids=torch.zeros_like(labels), labels=labels, loss_mask=loss_mask)

    # Must execute safely without index / out-of-bounds error
    res = objective(model_output, batch)
    assert res.valid_target_denominator == 4
    assert not math.isnan(res.unscaled_loss_sum)
    assert not math.isinf(res.unscaled_loss_sum)

    # Independent diagnostic CE must also handle sentinels safely
    diag_sum, diag_count = compute_independent_diagnostic_ce(logits, labels, loss_mask)
    assert diag_count == 4
    assert abs(diag_sum - res.unscaled_loss_sum) < 1e-6


def test_out_of_bounds_label_on_valid_position_raises_error() -> None:
    """Verify that an out-of-bounds target on a VALID (unmasked) position raises an error."""
    logits = torch.randn(1, 2, 10, dtype=torch.float32)
    labels = torch.tensor([[5, 100]], dtype=torch.long)  # 100 >= vocab_size 10
    loss_mask = torch.tensor([[1, 1]], dtype=torch.bool)

    objective = CrossEntropyObjective()
    model_output = LMOutput(logits=logits)
    batch = TrainingBatch(input_ids=labels, labels=labels, loss_mask=loss_mask)

    with pytest.raises(InvalidBatchError, match="out of vocabulary bounds"):
        objective(model_output, batch)


# -------------------------------------------------------------------------
# 3. All-masked batch & empty microbatch handling (Amendment 4)
# -------------------------------------------------------------------------


def test_all_masked_batch_raises_explicit_invalid_error() -> None:
    """Verify that an all-masked batch raises InvalidBatchError when allow_empty=False."""
    logits = torch.randn(2, 4, 16, dtype=torch.float32)
    labels = torch.tensor([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=torch.long)
    loss_mask = torch.zeros((2, 4), dtype=torch.bool)

    objective = CrossEntropyObjective()
    model_output = LMOutput(logits=logits)
    batch = TrainingBatch(input_ids=labels, labels=labels, loss_mask=loss_mask)

    with pytest.raises(InvalidBatchError, match="zero valid targets"):
        objective(model_output, batch, allow_empty=False)

    # When allow_empty=True, returns clean zero result without NaN
    res = objective(model_output, batch, allow_empty=True)
    assert res.valid_target_denominator == 0
    assert res.unscaled_loss_sum == 0.0
    assert res.loss.item() == 0.0
    assert not math.isnan(res.loss.item())


def test_empty_microbatch_in_accumulation_does_not_erase_gradients() -> None:
    """Verify zero-valid-target microbatch does not erase previously accumulated gradients."""
    model = nn.Linear(8, 4, bias=False)
    x = torch.randn(1, 8, requires_grad=False)
    target = torch.tensor([2], dtype=torch.long)

    # First microbatch has valid target
    out1 = model(x)
    loss1 = F.cross_entropy(out1, target)
    loss1.backward()  # type: ignore[no-untyped-call]

    assert model.weight.grad is not None
    initial_grad = model.weight.grad.clone()
    assert initial_grad.norm().item() > 0.0

    # Second microbatch has 0 valid targets
    empty_result = LossResult(
        loss_protocol="token_additive",
        loss=torch.tensor(0.0),
        unscaled_loss_sum=0.0,
        valid_target_denominator=0,
    )
    accumulate_microbatch_gradient(empty_result, total_valid_targets=1)

    # Gradients must be preserved exactly
    assert model.weight.grad is not None
    assert torch.equal(model.weight.grad, initial_grad)


# -------------------------------------------------------------------------
# 4. Requirement A06 Numerical Gradient Parity (Amendment 2)
# -------------------------------------------------------------------------


def test_a06_numerical_gradient_parity_full_vs_microbatches() -> None:
    """Verify that token-additive accumulated loss equals unsplit batch gradients and updates.

    Acceptance Requirement A06:
    "Compare full-batch backward against sequential microbatch backward from identical
    model/objective initialization, using unequal valid target counts and masks.
    Compare gradients and one resulting optimizer update, not just reported loss values."
    """
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=2,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )

    # Initialize two identical models from the same seed
    m_full = TransformerBaseline(config, seed=42)
    m_micro = TransformerBaseline(config, seed=42)

    # Verify identical starting weights
    for p1, p2 in zip(m_full.parameters(), m_micro.parameters(), strict=True):
        assert torch.equal(p1, p2)

    # Construct two microbatches with unequal valid target counts
    # Microbatch 1: 2 sequences, valid mask has 5 valid tokens
    input_ids_1 = torch.randint(0, 32, (2, 4))
    labels_1 = torch.randint(0, 32, (2, 4))
    mask_1 = torch.tensor([[1, 1, 0, 1], [0, 1, 1, 0]], dtype=torch.bool)
    n1 = int(mask_1.sum().item())  # 5

    # Microbatch 2: 2 sequences, valid mask has 7 valid tokens
    input_ids_2 = torch.randint(0, 32, (2, 4))
    labels_2 = torch.randint(0, 32, (2, 4))
    mask_2 = torch.tensor([[1, 1, 1, 1], [1, 0, 1, 1]], dtype=torch.bool)
    n2 = int(mask_2.sum().item())  # 7

    total_valid = n1 + n2  # 12 valid tokens

    # Combined full batch
    input_ids_full = torch.cat([input_ids_1, input_ids_2], dim=0)
    labels_full = torch.cat([labels_1, labels_2], dim=0)
    mask_full = torch.cat([mask_1, mask_2], dim=0)

    objective = CrossEntropyObjective()

    # --- Branch 1: Full-batch evaluation and backward ---
    out_full = m_full(input_ids_full)
    batch_full = TrainingBatch(input_ids=input_ids_full, labels=labels_full, loss_mask=mask_full)
    res_full = objective(out_full, batch_full)
    res_full.loss.backward()

    # --- Branch 2: Sequential microbatch backward ---
    m_micro.zero_grad()

    # Microbatch 1
    out_mb1 = m_micro(input_ids_1)
    batch_mb1 = TrainingBatch(input_ids=input_ids_1, labels=labels_1, loss_mask=mask_1)
    res_mb1 = objective(out_mb1, batch_mb1)
    accumulate_microbatch_gradient(res_mb1, total_valid_targets=total_valid)

    # Microbatch 2
    out_mb2 = m_micro(input_ids_2)
    batch_mb2 = TrainingBatch(input_ids=input_ids_2, labels=labels_2, loss_mask=mask_2)
    res_mb2 = objective(out_mb2, batch_mb2)
    accumulate_microbatch_gradient(res_mb2, total_valid_targets=total_valid)

    # Verify unscaled loss sums match
    assert (
        abs(res_full.unscaled_loss_sum - (res_mb1.unscaled_loss_sum + res_mb2.unscaled_loss_sum))
        < 1e-5
    )

    # 1. Compare gradients across all parameters: must be numerically identical
    for (n_f, p_f), (_n_m, p_m) in zip(
        m_full.named_parameters(), m_micro.named_parameters(), strict=True
    ):
        assert p_f.grad is not None and p_m.grad is not None
        assert torch.allclose(p_f.grad, p_m.grad, atol=1e-5, rtol=1e-5), (
            f"Gradient mismatch for {n_f}: max diff = {(p_f.grad - p_m.grad).abs().max().item()}"
        )

    # 2. Test global gradient clipping on both branches
    norm_full = clip_global_gradient_norm(m_full.parameters(), max_norm=0.5)
    norm_micro = clip_global_gradient_norm(m_micro.parameters(), max_norm=0.5)
    assert abs(norm_full - norm_micro) < 1e-5

    # 3. Take 1 optimizer step on both branches and compare resulting updated weights
    opt_full, _ = create_adamw_optimizer(None, m_full)
    opt_micro, _ = create_adamw_optimizer(None, m_micro)

    opt_full.step()
    opt_micro.step()

    for (n_f, p_f), (_n_m, p_m) in zip(
        m_full.named_parameters(), m_micro.named_parameters(), strict=True
    ):
        assert torch.allclose(p_f, p_m, atol=1e-6, rtol=1e-6), (
            f"Updated weight mismatch for {n_f}: max diff = {(p_f - p_m).abs().max().item()}"
        )


# -------------------------------------------------------------------------
# 5. Pass-through NoOp Objective Parity (Amendment 1)
# -------------------------------------------------------------------------


def test_noop_objective_matches_baseline_gradients_and_loss() -> None:
    """Verify that pure pass-through NoOpObjective matches baseline CE loss and gradients."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )

    m1 = TransformerBaseline(config, seed=123)
    m2 = TransformerBaseline(config, seed=123)

    input_ids = torch.randint(0, 32, (2, 4))
    labels = torch.randint(0, 32, (2, 4))
    mask = torch.tensor([[1, 1, 1, 0], [0, 1, 1, 1]], dtype=torch.bool)
    batch = TrainingBatch(input_ids=input_ids, labels=labels, loss_mask=mask)

    ce_obj = CrossEntropyObjective()
    noop_obj = NoOpObjective()

    out1 = m1(input_ids)
    out2 = m2(input_ids)

    res1 = ce_obj(out1, batch)
    res2 = noop_obj(out2, batch)

    assert abs(res1.unscaled_loss_sum - res2.unscaled_loss_sum) < 1e-6
    assert abs(res1.loss.item() - res2.loss.item()) < 1e-6

    res1.loss.backward()
    res2.loss.backward()

    for (n1, p1), (_n2, p2) in zip(m1.named_parameters(), m2.named_parameters(), strict=True):
        assert p1.grad is not None and p2.grad is not None
        assert torch.equal(p1.grad, p2.grad), f"No-op gradient differed for {n1}"


# -------------------------------------------------------------------------
# 6. Auxiliary Learning Objective (Amendment 1)
# -------------------------------------------------------------------------


def test_auxiliary_learning_objective_gradient_and_update_without_decay() -> None:
    """Verify auxiliary parameter has genuine non-zero gradient and updates without weight decay."""
    obj = AuxiliaryLearningObjective(init_val=1.0, target_val=5.0)

    # Initial auxiliary value: 1.0, target: 5.0
    # Penalty: 0.5 * (aux - target)^2 = 0.5 * (1 - 5)^2 = 8.0
    # d/d(aux) = (aux - target) = 1.0 - 5.0 = -4.0
    logits = torch.randn(1, 2, 10, dtype=torch.float32)
    labels = torch.tensor([[1, 2]], dtype=torch.long)
    mask = torch.tensor([[1, 1]], dtype=torch.bool)
    batch = TrainingBatch(input_ids=labels, labels=labels, loss_mask=mask)
    model_output = LMOutput(logits=logits)

    res = obj(model_output, batch)
    assert abs(res.auxiliary_loss - 8.0) < 1e-5

    # Backward pass
    res.loss.backward()
    assert obj.aux_param.grad is not None
    # Gradient on aux_param should be exactly (1.0 - 5.0) = -4.0
    assert abs(obj.aux_param.grad.item() - (-4.0)) < 1e-4

    # Optimizer step with weight decay disabled on aux_param
    dummy_model = nn.Linear(2, 2)
    opt, manifest = create_adamw_optimizer(
        None,
        model=dummy_model,
        objective=obj,
        decay_auxiliary=False,
    )

    # Verify aux_param is in non-decayed group
    found_aux = False
    for g in opt.param_groups:
        for p in g["params"]:
            if p is obj.aux_param:
                assert g["weight_decay"] == 0.0, "Weight decay must be 0 for auxiliary parameter"
                found_aux = True
    assert found_aux, "aux_param was not found in optimizer groups"

    initial_val = obj.aux_param.item()
    opt.step()
    updated_val = obj.aux_param.item()

    # Since grad is -4.0, update step should move parameter positive (towards target 5.0)
    assert updated_val > initial_val, (
        f"Auxiliary parameter failed to move towards target: "
        f"initial={initial_val}, updated={updated_val}"
    )


# -------------------------------------------------------------------------
# 7. Unsupported Microbatching for Global-Batch Objective (Amendment 4)
# -------------------------------------------------------------------------


class DummyGlobalBatchObjective(BaseObjective):
    """Test fixture objective requiring global-batch normalization and rejecting microbatching."""

    def __init__(self) -> None:
        super().__init__()
        self._capabilities = ObjectiveCapabilities(
            loss_protocol="custom_batch",
            supports_microbatching=False,
        )

    @property
    def capabilities(self) -> ObjectiveCapabilities:
        return self._capabilities

    def forward(self, model_output: LMOutput, batch: TrainingBatch) -> LossResult:
        return LossResult(
            loss_protocol="custom_batch",
            loss=torch.tensor(1.0),
            unscaled_loss_sum=1.0,
            valid_target_denominator=1,
        )


def test_global_batch_objective_rejects_microbatching() -> None:
    """Verify that a global-batch objective rejects microbatching attempts."""
    obj = DummyGlobalBatchObjective()
    res = obj(
        LMOutput(logits=torch.randn(1, 2, 4)),
        TrainingBatch(torch.zeros(1, 2), torch.zeros(1, 2), torch.ones(1, 2)),
    )

    with pytest.raises(UnsupportedBatchingError, match="does not support standard token-additive"):
        accumulate_microbatch_gradient(res, total_valid_targets=10)
