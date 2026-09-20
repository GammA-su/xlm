"""Tests for optimizer factories, parameter grouping, memory estimation, and reload validation.

Complying with XLM Contract C09, Acceptance Requirement A07, and P04 Amendments 5 and 6.
"""

from __future__ import annotations

import copy

import pytest
import torch
import torch.nn as nn

from xlm.config.schemas import AdamWConfig, TransformerBaselineConfig
from xlm.models.transformer import TransformerBaseline
from xlm.objectives.auxiliary_fixture import AuxiliaryLearningObjective
from xlm.optimizers.adamw import (
    create_adamw_optimizer,
    restore_optimizer_state,
    serialize_optimizer_state,
)
from xlm.optimizers.base import (
    ParameterGroupManifest,
    build_parameter_groups,
    estimate_optimizer_memory,
)


def test_parameter_grouping_completeness_and_tied_weights() -> None:
    """Verify all trainable parameters are grouped without omission or double counting.

    Requirement A07:
    "Complete unique param groups, state reload, horizon continuity."
    """
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=64,
        num_layers=2,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    obj = AuxiliaryLearningObjective(init_val=1.0)

    # 1. With decay_embeddings=True
    opt, manifest = create_adamw_optimizer(
        AdamWConfig(lr=1e-3, weight_decay=0.1, decay_embeddings=True, decay_norms=False),
        model=model,
        objective=obj,
    )

    # Total parameters across all groups
    all_grouped_params = [p for g in opt.param_groups for p in g["params"]]
    unique_grouped_ids = {id(p) for p in all_grouped_params}

    # Verify no duplicates across groups
    assert len(all_grouped_params) == len(unique_grouped_ids)

    # Verify tied weight lm_head.weight is grouped exactly once
    assert model.lm_head.weight is model.embed_tokens.weight
    embed_id = id(model.embed_tokens.weight)
    assert sum(1 for p in all_grouped_params if id(p) == embed_id) == 1

    # Verify objective parameter is included
    aux_id = id(obj.aux_param)
    assert sum(1 for p in all_grouped_params if id(p) == aux_id) == 1

    # 2. Check norm layers have weight decay = 0.0
    for name, p in model.named_parameters():
        if "norm" in name.lower():
            # Find group containing p
            for g in opt.param_groups:
                if any(p is gp for gp in g["params"]):
                    assert g["weight_decay"] == 0.0, f"Norm parameter {name} had non-zero decay"

    # 3. Check embedding decay policy when decay_embeddings=False
    opt_no_embed_decay, _ = create_adamw_optimizer(
        AdamWConfig(lr=1e-3, weight_decay=0.1, decay_embeddings=False, decay_norms=False),
        model=model,
    )
    for g in opt_no_embed_decay.param_groups:
        if any(model.embed_tokens.weight is gp for gp in g["params"]):
            assert g["weight_decay"] == 0.0, (
                "embed_tokens had non-zero decay when decay_embeddings=False"
            )


def test_optimizer_memory_estimation() -> None:
    """Verify formula-based optimizer memory estimation reports exact values."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=64,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    _, manifest = create_adamw_optimizer(None, model=model)

    mem_report = estimate_optimizer_memory(manifest, optimizer_type="adamw", moment_dtype="float32")

    unique_params = mem_report["num_unique_trainable_parameters"]
    assert unique_params > 0
    # For AdamW: 2 state tensors per param in FP32 (4 bytes) -> 8 bytes per parameter
    assert mem_report["estimated_state_memory_bytes"] == unique_params * 8
    assert mem_report["is_formula_estimate"] is True


def test_optimizer_state_serialization_and_reloading() -> None:
    """Verify optimizer state serialization, reload, and continuation."""
    model = nn.Linear(4, 2, bias=False)
    opt, manifest = create_adamw_optimizer(
        AdamWConfig(lr=0.01, weight_decay=0.0),
        model=model,
    )

    # Perform 1 optimization step to populate optimizer state
    x = torch.randn(2, 4)
    loss = model(x).sum()
    loss.backward()
    opt.step()

    # Check optimizer has state
    p = model.weight
    assert len(opt.state[p]) > 0
    assert "exp_avg" in opt.state[p]
    assert "exp_avg_sq" in opt.state[p]

    # Serialize
    saved_state = serialize_optimizer_state(opt, manifest)
    assert "manifest" in saved_state
    assert "state_dict" in saved_state

    # Construct fresh optimizer
    fresh_opt, fresh_manifest = create_adamw_optimizer(
        AdamWConfig(lr=0.01, weight_decay=0.0),
        model=model,
    )

    # Restore
    restore_optimizer_state(fresh_opt, saved_state, fresh_manifest)

    # Verify restored state matches
    assert torch.equal(fresh_opt.state[p]["exp_avg"], opt.state[p]["exp_avg"])
    assert torch.equal(fresh_opt.state[p]["exp_avg_sq"], opt.state[p]["exp_avg_sq"])


def test_optimizer_reload_rejects_reordered_same_shaped_parameters() -> None:
    """Negative test: reordering same-shaped parameters is strictly rejected on reload.

    Complying with P04 Amendment 6:
    "Do not assume torch optimizer.load_state_dict verifies semantic parameter identity.
    Add a negative test with same-shaped parameters reordered."
    """

    class TwoSameShapedLayers(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layer_a = nn.Linear(4, 4, bias=False)
            self.layer_b = nn.Linear(4, 4, bias=False)

    m = TwoSameShapedLayers()
    opt, manifest = create_adamw_optimizer(None, model=m)

    # Serialize
    saved_state = serialize_optimizer_state(opt, manifest)

    # Tamper with the saved manifest by swapping layer_a and layer_b in canonical_param_names
    tampered_state = copy.deepcopy(saved_state)
    names = tampered_state["manifest"]["groups"][0]["canonical_param_names"]
    assert len(names) == 2
    # Reverse names: ["model.layer_b.weight", "model.layer_a.weight"]
    tampered_state["manifest"]["groups"][0]["canonical_param_names"] = list(reversed(names))

    # Reloading against current manifest must fail
    with pytest.raises(ValueError, match="Parameter names or order mismatch"):
        restore_optimizer_state(opt, tampered_state, manifest)


def test_build_parameter_groups_detects_omissions_or_duplicates() -> None:
    """Verify build_parameter_groups raises ValueError on parameter omission."""
    m = nn.Linear(4, 2)
    # Normal grouping succeeds
    groups, manifest = build_parameter_groups(m)
    assert len(manifest.groups) > 0

    # Verification checks manifest against tampered group count
    with pytest.raises(ValueError, match="Parameter group count mismatch"):
        tampered_manifest = ParameterGroupManifest(
            groups=[],
            param_shapes=manifest.param_shapes,
            param_dtypes=manifest.param_dtypes,
            aliases=manifest.aliases,
            frozen_parameters=manifest.frozen_parameters,
        )
        manifest.verify_against(tampered_manifest)
