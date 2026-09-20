"""Real continuation test verifying serialization, reconstruction, and update parity.

Complying with XLM Contract C09 and P04 Amendments 1, 6, and 7:
"Use a real tiny continuation test: update, serialize, reconstruct fresh
model/objective/optimizer/schedule objects, reload, and compare the next update
with an uninterrupted branch."
"""

from __future__ import annotations

import copy

import torch

from xlm.config.schemas import (
    AdamWConfig,
    TransformerBaselineConfig,
    WarmupCosineScheduleConfig,
)
from xlm.core.contracts import TrainingBatch
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
from xlm.objectives.base import accumulate_microbatch_gradient
from xlm.optimizers.adamw import (
    create_adamw_optimizer,
    restore_optimizer_state,
    serialize_optimizer_state,
)
from xlm.schedules.cosine import WarmupCosineSchedule


def test_tiny_model_optimizer_schedule_continuation_parity() -> None:
    """Verify bitwise continuation parity between uninterrupted and reloaded training branches."""
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
    opt_config = AdamWConfig(lr=1e-3, weight_decay=0.1, decay_embeddings=True, decay_norms=False)
    sched_config = WarmupCosineScheduleConfig(
        warmup_valid_targets=20,
        horizon_valid_targets=100,
        min_lr_ratio=0.1,
    )

    # Deterministic batches
    torch.manual_seed(1001)
    input_ids_1 = torch.randint(0, 32, (2, 4))
    labels_1 = torch.randint(0, 32, (2, 4))
    mask_1 = torch.tensor([[1, 1, 1, 0], [0, 1, 1, 1]], dtype=torch.bool)
    n1 = int(mask_1.sum().item())
    batch_1 = TrainingBatch(input_ids=input_ids_1, labels=labels_1, loss_mask=mask_1)

    input_ids_2 = torch.randint(0, 32, (2, 4))
    labels_2 = torch.randint(0, 32, (2, 4))
    mask_2 = torch.tensor([[1, 1, 0, 1], [1, 1, 1, 0]], dtype=torch.bool)
    n2 = int(mask_2.sum().item())
    batch_2 = TrainingBatch(input_ids=input_ids_2, labels=labels_2, loss_mask=mask_2)

    # Initialize Branch A
    model_a = TransformerBaseline(config, seed=42)
    obj_a = AuxiliaryLearningObjective(init_val=1.0, target_val=3.0)
    opt_a, manifest_a = create_adamw_optimizer(opt_config, model=model_a, objective=obj_a)
    sched_a = WarmupCosineSchedule(sched_config, base_lr=1e-3)

    # Step 1 on Branch A
    counter_committed = 0
    _ = sched_a.apply_lr_to_optimizer(opt_a, counter_value=counter_committed)
    out_1 = model_a(batch_1.input_ids)
    res_1 = obj_a(out_1, batch_1)
    accumulate_microbatch_gradient(res_1, total_valid_targets=n1)
    opt_a.step()
    opt_a.zero_grad()
    counter_committed += n1

    # Take independent snapshot of state after Step 1
    saved_model_state = copy.deepcopy(model_a.state_dict())
    saved_obj_state = copy.deepcopy(obj_a.get_state())
    saved_opt_state = serialize_optimizer_state(opt_a, manifest_a)
    saved_sched_state = copy.deepcopy(sched_a.state_dict())
    saved_counter = counter_committed

    # Continue Branch A with Step 2 (uninterrupted)
    lr_2_a = sched_a.apply_lr_to_optimizer(opt_a, counter_value=counter_committed)
    out_2_a = model_a(batch_2.input_ids)
    res_2_a = obj_a(out_2_a, batch_2)
    accumulate_microbatch_gradient(res_2_a, total_valid_targets=n2)
    opt_a.step()
    opt_a.zero_grad()
    counter_committed += n2

    # --- Branch B: Reconstructed from scratch and reloaded from snapshot ---
    model_b = TransformerBaseline(config, seed=999)  # different init seed!
    obj_b = AuxiliaryLearningObjective(init_val=0.0, target_val=3.0)

    # Restore model and objective
    model_b.load_state_dict(saved_model_state)
    obj_b.load_state(saved_obj_state)

    # Construct fresh optimizer and restore
    opt_b, manifest_b = create_adamw_optimizer(opt_config, model=model_b, objective=obj_b)
    restore_optimizer_state(opt_b, saved_opt_state, manifest_b)

    # Construct fresh schedule and restore
    sched_b = WarmupCosineSchedule(sched_config, base_lr=1e-3)
    sched_b.load_state_dict(saved_sched_state)
    counter_b = saved_counter

    # Execute Step 2 on Branch B
    lr_2_b = sched_b.apply_lr_to_optimizer(opt_b, counter_value=counter_b)
    out_2_b = model_b(batch_2.input_ids)
    res_2_b = obj_b(out_2_b, batch_2)
    accumulate_microbatch_gradient(res_2_b, total_valid_targets=n2)
    opt_b.step()
    opt_b.zero_grad()
    counter_b += n2

    # --- Verification of Exact Equivalence ---
    # 1. Learning rates must match
    assert abs(lr_2_a - lr_2_b) < 1e-9

    # 2. Model parameters must be bitwise identical
    for (na, pa), (nb, pb) in zip(
        model_a.named_parameters(), model_b.named_parameters(), strict=True
    ):
        assert na == nb
        assert torch.equal(pa, pb), f"Parameter {na} differed after continuation step"

    # 3. Objective parameters must be bitwise identical
    assert torch.equal(obj_a.aux_param, obj_b.aux_param)

    # 4. Optimizer moment states must be bitwise identical
    for pa, pb in zip(model_a.parameters(), model_b.parameters(), strict=True):
        if pa in opt_a.state:
            assert torch.equal(opt_a.state[pa]["exp_avg"], opt_b.state[pb]["exp_avg"])
            assert torch.equal(opt_a.state[pa]["exp_avg_sq"], opt_b.state[pb]["exp_avg_sq"])

    # 5. Counter progress must match
    assert counter_committed == counter_b
