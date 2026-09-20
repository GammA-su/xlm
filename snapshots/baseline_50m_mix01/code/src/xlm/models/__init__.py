"""Model architectures, attention mechanisms, and parameter accounting."""

from __future__ import annotations

from xlm.models.attention import CausalSelfAttention
from xlm.models.base import BaseModel
from xlm.models.feedforward import SwiGLU
from xlm.models.masks import (
    build_causal_mask,
    build_causal_stream_mask,
    build_isolated_document_mask,
    prepare_attention_mask,
)
from xlm.models.parameter_counts import (
    ParameterCounts,
    compute_parameter_formula,
    count_model_parameters,
)
from xlm.models.rmsnorm import RMSNorm
from xlm.models.rope import RotaryEmbedding, apply_rotary_pos_emb
from xlm.models.serialization import (
    load_model_from_directory,
    publish_model_artifact,
    save_model_to_directory,
)
from xlm.models.transformer import (
    TransformerBaseline,
    TransformerBlock,
    check_tokenizer_model_compatibility,
    create_transformer_baseline,
)

__all__ = [
    "BaseModel",
    "CausalSelfAttention",
    "ParameterCounts",
    "RMSNorm",
    "RotaryEmbedding",
    "SwiGLU",
    "TransformerBaseline",
    "TransformerBlock",
    "apply_rotary_pos_emb",
    "build_causal_mask",
    "build_causal_stream_mask",
    "build_isolated_document_mask",
    "check_tokenizer_model_compatibility",
    "compute_parameter_formula",
    "count_model_parameters",
    "create_transformer_baseline",
    "load_model_from_directory",
    "prepare_attention_mask",
    "publish_model_artifact",
    "save_model_to_directory",
]
