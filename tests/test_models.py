"""Tests for model architecture, parameter accounting, serialization, and CLI inspection.

Complying with XLM Contract C08 and P03 Amendments 4, 5, 6, and 7.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import torch
import torch.nn as nn

from xlm.config.composer import load_yaml_file
from xlm.config.schemas import ModelPresetConfig, TransformerBaselineConfig
from xlm.core.contracts import LMOutput
from xlm.models.parameter_counts import compute_parameter_formula
from xlm.models.serialization import (
    load_model_from_directory,
    save_model_to_directory,
)
from xlm.models.transformer import (
    TransformerBaseline,
    TransformerBlock,
    check_tokenizer_model_compatibility,
)

# -------------------------------------------------------------------------
# 1. Parameter Accounting & Meta-Device Verification (Amendment 4)
# -------------------------------------------------------------------------


def test_meta_parameter_counts_exact_matches() -> None:
    """Verify exact parameter formula match and preset counts on meta device.

    Expected V=32,768 counts from Contract C08:
    - 50m:   49,883,648
    - 150m: 149,942,016
    - 300m: 299,418,624
    - tiny:   2,179,392
    """
    expected_counts = {
        "50m": 49_883_648,
        "150m": 149_942_016,
        "300m": 299_418_624,
        "tiny": 2_179_392,
    }

    meta_device = torch.device("meta")

    for preset_id, expected_unique in expected_counts.items():
        recipe_path = Path("recipes/models") / f"{preset_id}.yaml"
        raw_dict = load_yaml_file(recipe_path)
        preset = ModelPresetConfig.model_validate(raw_dict)
        clean_dict = preset.model_dump(exclude={"schema_version", "kind", "id"})
        config = TransformerBaselineConfig.model_validate(clean_dict)

        # Theoretical formula calculation
        formula_val = compute_parameter_formula(
            vocab_size=config.vocab_size,
            hidden_size=config.hidden_size,
            num_layers=config.num_layers,
            intermediate_size=config.intermediate_size,
        )
        assert formula_val == expected_unique, f"Formula mismatch for {preset_id}"

        # Actual meta-device instantiation (zero CPU/GPU RAM allocation)
        model = TransformerBaseline(config, device=meta_device)
        counts = model.count_parameters()

        assert counts.unique_deployed == expected_unique, f"Meta count mismatch for {preset_id}"
        assert counts.formula_matches is True
        assert counts.cap_respected is True
        assert counts.device == "meta"
        assert len(counts.tied_aliases) == 1
        assert counts.tied_aliases[0] == ("lm_head.weight", "embed_tokens.weight")


def test_cpu_tiny_parameter_enumeration() -> None:
    """Verify parameter counting on actual CPU tiny instantiation."""
    recipe_path = Path("recipes/models/tiny.yaml")
    raw_dict = load_yaml_file(recipe_path)
    preset = ModelPresetConfig.model_validate(raw_dict)
    config = TransformerBaselineConfig.model_validate(
        preset.model_dump(exclude={"schema_version", "kind", "id"})
    )

    model = TransformerBaseline(config, device="cpu", seed=42)
    counts = model.count_parameters()

    assert counts.unique_deployed == 2_179_392
    assert counts.total_instantiated == 4_276_544
    assert counts.tied_parameters == 2_097_152  # 32768 * 64
    assert counts.non_embedding == 82_240
    assert counts.device == "cpu"
    assert counts.formula_matches is True


def test_untied_model_fixture_cross_check() -> None:
    """Cross-check parameter counting with a deliberately untied fixture model."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=1000,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=2,
        intermediate_size=128,
        tie_embeddings=False,  # Deliberately untied
        context_length=64,
    )
    model = TransformerBaseline(config, device="meta")
    counts = model.count_parameters()

    # When untied, lm_head is independent, so unique_deployed increases by V*d
    assert counts.tied_parameters == 0
    assert len(counts.tied_aliases) == 0
    assert counts.unique_deployed == counts.total_instantiated


# -------------------------------------------------------------------------
# 2. Configuration & Tokenizer Compatibility Validation (Amendment 5)
# -------------------------------------------------------------------------


def test_architecture_dimension_validation() -> None:
    """Verify validation of head divisibility, even rotary dim, and bounds."""
    # 1. Hidden size not divisible by num_heads
    with pytest.raises(ValueError, match="divisible by num_attention_heads"):
        TransformerBaselineConfig(
            architecture="transformer_baseline",
            num_layers=2,
            hidden_size=65,  # 65 not divisible by 2
            num_attention_heads=2,
            intermediate_size=128,
        )

    # 2. Odd head dimension (head_dim = 66 // 6 = 11, odd)
    with pytest.raises(ValueError, match="must be even for rotary"):
        TransformerBaselineConfig(
            architecture="transformer_baseline",
            num_layers=2,
            hidden_size=66,
            num_attention_heads=6,  # 66 // 6 = 11
            intermediate_size=128,
        )

    # 3. Invalid attention backend
    with pytest.raises(ValueError, match="Invalid attention_backend"):
        TransformerBaselineConfig(
            architecture="transformer_baseline",
            num_layers=2,
            hidden_size=64,
            num_attention_heads=2,
            intermediate_size=128,
            attention_backend="invalid_backend",
        )


def test_token_id_bounds_and_context_limits() -> None:
    """Verify out-of-bounds token rejection and context length enforcement."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=8,
        attention_backend="eager",
    )
    model = TransformerBaseline(config)

    # Negative token ID
    with pytest.raises(ValueError, match="Token ID out of bounds"):
        model(torch.tensor([[-1, 5, 10]]))

    # Token ID >= vocab_size
    with pytest.raises(ValueError, match="Token ID out of bounds"):
        model(torch.tensor([[5, 10, 100]]))

    # Exceeding context length
    with pytest.raises(ValueError, match="exceeds maximum configured context length"):
        model(torch.randint(0, 100, (1, 12)))


def test_tokenizer_model_compatibility_check() -> None:
    """Verify check_tokenizer_model_compatibility contract."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32768,
        num_layers=2,
        hidden_size=64,
        num_attention_heads=2,
        intermediate_size=128,
    )

    class MockTokenizer:
        def __init__(self, vocab_size: int, pad: int = 0, bos: int = 1, eos: int = 2, unk: int = 3):
            self.vocab_size = vocab_size
            self.pad_id = pad
            self.bos_id = bos
            self.eos_id = eos
            self.unk_id = unk

    # 1. Compatible tokenizer
    ok_tok = MockTokenizer(32768)
    compat, msg = check_tokenizer_model_compatibility(ok_tok, config)
    assert compat is True

    # 2. Incompatible vocab size (e.g. 260 byte tokenizer vs 32768 model)
    small_tok = MockTokenizer(260)
    with pytest.raises(ValueError, match="vocabulary size"):
        check_tokenizer_model_compatibility(small_tok, config)

    # 3. Mis-mapped special token
    bad_special_tok = MockTokenizer(32768, eos=99)
    with pytest.raises(ValueError, match="special token 'eos' ID mismatch"):
        check_tokenizer_model_compatibility(bad_special_tok, config)


# -------------------------------------------------------------------------
# 3. Initialization, Residual Scaling & Contracts (Amendment 6)
# -------------------------------------------------------------------------


def test_deterministic_initialization_and_residual_scaling() -> None:
    """Verify deterministic weight initialization and 1 / sqrt(2*L) residual scaling."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=500,
        num_layers=4,
        hidden_size=64,
        num_attention_heads=2,
        intermediate_size=128,
        attention_backend="eager",
        initialization_policy="baseline_v1",
    )

    m1 = TransformerBaseline(config, seed=12345)
    m2 = TransformerBaseline(config, seed=12345)

    # Determinism check: identical seeds produce bitwise equal weights
    for (n1, p1), (n2, p2) in zip(m1.named_parameters(), m2.named_parameters(), strict=True):
        assert n1 == n2
        assert torch.equal(p1, p2), f"Parameter {n1} differed with identical seed"

    # Residual scaling check: out_proj and down_proj should have std
    # ~ 0.02 / sqrt(2 * 4) = 0.02 / sqrt(8)
    expected_residual_std = 0.02 / math.sqrt(2.0 * 4)
    first_layer = cast(TransformerBlock, m1.layers[0])
    out_proj_std = first_layer.self_attn.out_proj.weight.std().item()
    down_proj_std = first_layer.mlp.down_proj.weight.std().item()
    q_proj_std = first_layer.self_attn.q_proj.weight.std().item()

    assert abs(out_proj_std - expected_residual_std) < 0.005
    assert abs(down_proj_std - expected_residual_std) < 0.005
    assert abs(q_proj_std - 0.02) < 0.005

    # Tied parameter initialized once: lm_head.weight is embed_tokens.weight
    assert m1.lm_head.weight is m1.embed_tokens.weight


def test_stateless_contract_and_hidden_states() -> None:
    """Verify stateless baseline contract and optional hidden_states request."""
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
    model = TransformerBaseline(config)

    # 1. Passing state must be rejected
    with pytest.raises(ValueError, match="Stateless baseline model"):
        model(torch.tensor([[1, 2, 3]]), state={"fake_cache": True})

    # 2. Hidden states request
    out = model(torch.tensor([[1, 2, 3]]), requested_outputs={"hidden_states": True})
    assert isinstance(out, LMOutput)
    assert "hidden_states" in out.auxiliary_outputs

    # hidden_states contains h_0 (post-embedding) + h_1 (layer 0) + h_2 (layer 1) = 3 tensors
    hs = out.auxiliary_outputs["hidden_states"]
    assert len(hs) == 3
    assert hs[0].shape == (1, 3, 32)
    assert hs[1].shape == (1, 3, 32)
    assert hs[2].shape == (1, 3, 32)


# -------------------------------------------------------------------------
# 4. Numerical Gradient Flow & Finite Parameter Norms (Amendment 8)
# -------------------------------------------------------------------------


def test_tiny_model_numerical_gradient_flow() -> None:
    """Verify that all trainable parameter groups receive finite non-zero gradients."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=120,
        num_layers=2,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        context_length=16,
        attention_backend="eager",
    )
    model = TransformerBaseline(config, seed=42)
    model.train()

    input_ids = torch.randint(0, 120, (2, 8))
    targets = torch.randint(0, 120, (2, 8))

    out = model(input_ids)
    loss = nn.functional.cross_entropy(out.logits.view(-1, 120), targets.view(-1))
    loss.backward()  # type: ignore[no-untyped-call]

    for name, param in model.named_parameters():
        assert param.grad is not None, f"Missing gradient for {name}"
        assert not torch.isnan(param.grad).any(), f"NaN gradient in {name}"
        assert not torch.isinf(param.grad).any(), f"Inf gradient in {name}"
        # Assert non-zero gradient norm across each parameter tensor
        grad_norm = param.grad.norm().item()
        assert grad_norm > 0.0, f"Zero gradient norm for parameter {name}"


# -------------------------------------------------------------------------
# 5. Serialization & Artifact Integration (Amendment 7)
# -------------------------------------------------------------------------


def test_model_serialization_and_reloading(tmp_path: Path) -> None:
    """Verify strict model serialization, tied weight restoration, and reloading."""
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
    model = TransformerBaseline(config, seed=888)
    model.eval()

    test_input = torch.randint(0, 100, (1, 8))
    with torch.no_grad():
        orig_logits = model(test_input).logits

    # Save model
    save_model_to_directory(model, tmp_path)
    assert (tmp_path / "config.json").is_file()
    assert (tmp_path / "model.pt").is_file()

    # Load model
    loaded_model = cast(TransformerBaseline, load_model_from_directory(tmp_path))
    loaded_model.eval()

    # Verify tied weights restoration
    assert loaded_model.lm_head.weight is loaded_model.embed_tokens.weight

    with torch.no_grad():
        loaded_logits = loaded_model(test_input).logits

    # Verify bitwise or identical outputs
    assert torch.allclose(orig_logits, loaded_logits, atol=1e-6)

    # Verify subsequent backward pass works on reloaded model
    loaded_model.train()
    out = loaded_model(test_input)
    loss = out.logits.sum()
    loss.backward()
    assert loaded_model.embed_tokens.weight.grad is not None


def test_serialization_rejects_conflicting_tied_weights(tmp_path: Path) -> None:
    """Verify that load_model_from_directory rejects conflicting weights for tied layers."""
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=100,
        num_layers=1,
        hidden_size=32,
        num_attention_heads=2,
        intermediate_size=64,
        tie_embeddings=True,
    )
    model = TransformerBaseline(config, seed=1)
    save_model_to_directory(model, tmp_path)

    # Manually corrupt lm_head.weight in model.pt
    state_dict = torch.load(tmp_path / "model.pt", weights_only=True)
    state_dict["lm_head.weight"] = state_dict["lm_head.weight"] + 1.0  # Conflict!
    torch.save(state_dict, tmp_path / "model.pt")

    with pytest.raises(ValueError, match="Conflicting weights detected"):
        load_model_from_directory(tmp_path)


# -------------------------------------------------------------------------
# 6. CLI Model Inspection Tests
# -------------------------------------------------------------------------


@pytest.mark.parametrize("preset_id", ["tiny", "50m", "150m", "300m"])
def test_cli_model_inspect_subprocess(preset_id: str) -> None:
    """Verify xlm model inspect executes successfully for all presets in a clean subprocess."""
    res = subprocess.run(
        [sys.executable, "-m", "xlm.cli.main", "model", "inspect", preset_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"CLI inspect failed for {preset_id}: {res.stderr}"
    assert '"formula_matches": true' in res.stdout
    assert '"cap_respected": true' in res.stdout
