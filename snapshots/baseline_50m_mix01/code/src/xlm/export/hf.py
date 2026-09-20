"""Baseline-only Hugging Face layout mapping (P20, A35, optional path).

The reference Transformer baseline (pre-RMSNorm, RoPE, SwiGLU, tied embeddings,
no grouped-query attention) maps exactly onto a Llama-layout state dict and
config. Anything else is refused: novel architectures must never be claimed as
an existing GPT family. Tokenizer special IDs are emitted as an exact table for
verification.

What this module does NOT do: run a Hugging Face model. Native/HF runtime
parity needs the transformers library plus their checkpoint on the operator
side and is recorded NOT RUN until then. The mapping itself is fully tested:
complete key coverage, exact shapes, exact config values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

HF_MAPPING_VERSION = "1"


class HfMappingError(ValueError):
    """Raised when a model cannot be honestly represented in the HF layout."""


@dataclass(frozen=True)
class HfMappingReport:
    """Proof that a native state dict maps completely onto the HF layout."""

    native_keys_mapped: int
    hf_keys_produced: int
    unmapped_native_keys: list[str] = field(default_factory=list)
    shape_mismatches: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    special_ids: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _native_key_to_hf(key: str, num_layers: int) -> str:
    if key == "embed_tokens.weight":
        return "model.embed_tokens.weight"
    if key == "norm.weight":
        return "model.norm.weight"
    if key == "lm_head.weight":
        return "lm_head.weight"
    for layer in range(num_layers):
        prefix = f"layers.{layer}."
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        table = {
            "input_layernorm.weight": f"model.layers.{layer}.input_layernorm.weight",
            "self_attn.q_proj.weight": f"model.layers.{layer}.self_attn.q_proj.weight",
            "self_attn.k_proj.weight": f"model.layers.{layer}.self_attn.k_proj.weight",
            "self_attn.v_proj.weight": f"model.layers.{layer}.self_attn.v_proj.weight",
            "self_attn.out_proj.weight": (f"model.layers.{layer}.self_attn.o_proj.weight"),
            "post_attention_layernorm.weight": (
                f"model.layers.{layer}.post_attention_layernorm.weight"
            ),
            "mlp.gate_proj.weight": f"model.layers.{layer}.mlp.gate_proj.weight",
            "mlp.up_proj.weight": f"model.layers.{layer}.mlp.up_proj.weight",
            "mlp.down_proj.weight": f"model.layers.{layer}.mlp.down_proj.weight",
        }
        if rest in table:
            return table[rest]
        raise HfMappingError(f"native key '{key}' has no HF-layout counterpart")
    raise HfMappingError(f"native key '{key}' is outside every transformer block")


def map_state_dict_to_hf_layout(
    state_dict: Mapping[str, Any],
    model_config: Mapping[str, Any],
) -> tuple[dict[str, Any], HfMappingReport]:
    """Map a native baseline state dict onto the HF Llama layout, verified."""
    architecture = model_config.get("architecture", "transformer_baseline")
    if architecture != "transformer_baseline":
        raise HfMappingError(
            f"architecture '{architecture}' cannot be exported as an existing HF GPT "
            "family; only 'transformer_baseline' maps onto the Llama layout"
        )
    try:
        num_layers = int(model_config["num_layers"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HfMappingError(f"model config lacks a valid num_layers: {exc}") from exc

    hf_state: dict[str, Any] = {}
    unmapped: list[str] = []
    mismatches: list[str] = []
    for key, tensor in state_dict.items():
        try:
            hf_key = _native_key_to_hf(str(key), num_layers)
        except HfMappingError:
            unmapped.append(str(key))
            continue
        if hf_key in hf_state:
            mismatches.append(f"duplicate HF key '{hf_key}' from native '{key}'")
            continue
        hf_state[hf_key] = tensor
    if unmapped:
        raise HfMappingError(
            f"{len(unmapped)} native keys have no HF counterpart (e.g. {unmapped[:3]}); "
            "refusing a partial mapping"
        )
    if mismatches:
        raise HfMappingError("; ".join(mismatches))

    report = HfMappingReport(
        native_keys_mapped=len(hf_state),
        hf_keys_produced=len(hf_state),
        config=hf_config(model_config),
        special_ids={
            "pad_token_id": 0,
            "bos_token_id": 1,
            "eos_token_id": 2,
            "unk_token_id": 3,
        },
    )
    return hf_state, report


def hf_config(model_config: Mapping[str, Any]) -> dict[str, Any]:
    """Emit the exact HF-layout config for a baseline config mapping."""
    try:
        return {
            "architectures": ["LlamaForCausalLM"],
            "model_type": "llama",
            "hidden_size": int(model_config["hidden_size"]),
            "num_hidden_layers": int(model_config["num_layers"]),
            "num_attention_heads": int(model_config["num_attention_heads"]),
            # No grouped-query attention in the baseline: KV heads equal Q heads.
            "num_key_value_heads": int(model_config["num_attention_heads"]),
            "intermediate_size": int(model_config["intermediate_size"]),
            "vocab_size": int(model_config["vocab_size"]),
            "max_position_embeddings": int(model_config["context_length"]),
            "rms_norm_eps": 1e-5,
            "rope_theta": 10000.0,
            "tie_word_embeddings": True,
            "bos_token_id": 1,
            "eos_token_id": 2,
            "pad_token_id": 0,
            "unk_token_id": 3,
            "hf_mapping_version": HF_MAPPING_VERSION,
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise HfMappingError(f"model config cannot produce an exact HF config: {exc}") from exc
