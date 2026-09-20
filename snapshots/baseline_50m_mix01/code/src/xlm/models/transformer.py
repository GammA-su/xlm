"""Reference causal Transformer architecture complying with XLM Contract C08."""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn

from xlm.config.schemas import TransformerBaselineConfig
from xlm.core.contracts import InferenceInput, LMOutput, ModelCapabilities
from xlm.models.attention import CausalSelfAttention
from xlm.models.base import BaseModel
from xlm.models.feedforward import SwiGLU
from xlm.models.initialization import init_weights
from xlm.models.masks import prepare_attention_mask
from xlm.models.parameter_counts import ParameterCounts, count_model_parameters
from xlm.models.rmsnorm import RMSNorm


class TransformerBlock(nn.Module):
    """Transformer decoder block with pre-RMSNorm, multi-head attention, and SwiGLU."""

    input_layernorm: RMSNorm
    self_attn: CausalSelfAttention
    post_attention_layernorm: RMSNorm
    mlp: SwiGLU

    def __init__(
        self,
        config: TransformerBaselineConfig,
        layer_idx: int,
        device: Any = None,
    ) -> None:
        super().__init__()
        self.layer_idx = layer_idx
        self.input_layernorm = RMSNorm(config.hidden_size, device=device)
        self.self_attn = CausalSelfAttention(config, layer_idx=layer_idx, device=device)
        self.post_attention_layernorm = RMSNorm(config.hidden_size, device=device)
        self.mlp = SwiGLU(config.hidden_size, config.intermediate_size, device=device)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        backend_override: str | None = None,
    ) -> torch.Tensor:
        # Self-attention with pre-RMSNorm
        normed_attn_input = self.input_layernorm(hidden_states)
        attn_out = self.self_attn(
            normed_attn_input,
            attention_mask=attention_mask,
            position_ids=position_ids,
            backend_override=backend_override,
        )
        hidden_states = hidden_states + attn_out

        # MLP with pre-RMSNorm
        normed_mlp_input = self.post_attention_layernorm(hidden_states)
        mlp_out = self.mlp(normed_mlp_input)
        hidden_states = hidden_states + mlp_out

        return hidden_states


class TransformerBaseline(BaseModel):
    """Bias-free causal Transformer baseline complying with Contract C08.

    Architecture specifications:
    - Pre-RMSNorm
    - RoPE rotary position embeddings with explicit position IDs
    - Multi-head causal self-attention (eager & SDPA backends)
    - SwiGLU feed-forward network
    - Tied token embedding and output head weights
    - Stateless execution (kv-cache explicitly disabled initially)
    """

    embed_tokens: nn.Embedding
    layers: nn.ModuleList
    norm: RMSNorm
    lm_head: nn.Linear

    def __init__(
        self,
        config: TransformerBaselineConfig,
        device: Any = None,
        seed: int | None = None,
        activation_checkpointing: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.device_type = str(device) if device is not None else "cpu"
        # Activation checkpointing is an explicit opt-in execution mode, never
        # implied: recompute trades compute for memory with identical numerics.
        self.activation_checkpointing = activation_checkpointing

        # Token embeddings
        self.embed_tokens = nn.Embedding(
            config.vocab_size,
            config.hidden_size,
            device=device,
        )

        # Transformer blocks
        self.layers = nn.ModuleList(
            [TransformerBlock(config, layer_idx=i, device=device) for i in range(config.num_layers)]
        )

        # Final RMSNorm
        self.norm = RMSNorm(config.hidden_size, device=device)

        # Output LM head
        self.lm_head = nn.Linear(
            config.hidden_size,
            config.vocab_size,
            bias=False,
            device=device,
        )

        # Weight tying: lm_head shares weights with embed_tokens
        if config.tie_embeddings:
            self.lm_head.weight = self.embed_tokens.weight

        # Weight initialization (skip on meta device)
        if str(device) != "meta":
            init_weights(
                self,
                num_layers=config.num_layers,
                policy=config.initialization_policy,
                seed=seed,
                device=device,
            )

    def forward(
        self,
        input_ids: torch.Tensor | InferenceInput,
        attention_mask: torch.Tensor | None = None,
        position_ids: torch.Tensor | None = None,
        state: Any | None = None,
        requested_outputs: dict[str, bool] | None = None,
        backend_override: str | None = None,
    ) -> LMOutput:
        """Forward pass complying with Contract C08.

        Args:
            input_ids: Token ID tensor of shape (batch, seq_len) or InferenceInput.
            attention_mask: Optional attention mask (2D, 3D, or 4D).
            position_ids: Optional explicit position IDs of shape (batch, seq_len).
            state: Recurrent/cache state. Must be None for stateless baseline.
            requested_outputs: Optional output requests, e.g. {'hidden_states': True}.
            backend_override: Optional backend switch ('eager' or 'sdpa').

        Returns:
            LMOutput(logits=logits, auxiliary_outputs=..., state=None).
        """
        if state is not None:
            raise ValueError(
                "Stateless baseline model: 'state' argument is not supported. "
                "State caching must pass parity tests before being enabled."
            )

        ids: torch.Tensor
        if isinstance(input_ids, InferenceInput):
            ids = input_ids.input_ids
            if attention_mask is None:
                attention_mask = input_ids.attention_mask
            if position_ids is None:
                position_ids = input_ids.position_ids
        else:
            ids = input_ids

        batch_size, seq_len = ids.shape

        # Token ID bounds check: never truncate or silently clamp
        if ids.numel() > 0:
            min_id = int(ids.min().item())
            max_id = int(ids.max().item())
            if min_id < 0 or max_id >= self.config.vocab_size:
                raise ValueError(
                    f"Token ID out of bounds: range [{min_id}, {max_id}] is outside "
                    f"valid vocabulary [0, {self.config.vocab_size - 1}]."
                )

        # Context length check
        if seq_len > self.config.context_length:
            raise ValueError(
                f"Sequence length {seq_len} exceeds maximum configured context length "
                f"{self.config.context_length}."
            )

        # Prepare attention mask, enforcing causality
        prepared_mask = prepare_attention_mask(
            attention_mask,
            batch_size=batch_size,
            seq_len=seq_len,
            device=ids.device,
        )

        # Embed input tokens
        hidden_states = self.embed_tokens(ids)

        all_hidden_states: list[torch.Tensor] = []
        return_hidden_states = bool(
            requested_outputs and requested_outputs.get("hidden_states", False)
        )
        if return_hidden_states:
            all_hidden_states.append(hidden_states)

        # Pass through layers
        for layer in self.layers:
            if self.activation_checkpointing:
                if return_hidden_states:
                    raise ValueError(
                        "Activation checkpointing cannot record per-layer hidden states; "
                        "request hidden_states with checkpointing disabled."
                    )
                hidden_states = torch.utils.checkpoint.checkpoint(
                    layer,
                    hidden_states,
                    prepared_mask,
                    position_ids,
                    backend_override,
                    use_reentrant=False,
                )
            else:
                hidden_states = layer(
                    hidden_states,
                    attention_mask=prepared_mask,
                    position_ids=position_ids,
                    backend_override=backend_override,
                )
            if return_hidden_states:
                all_hidden_states.append(hidden_states)

        # Final RMSNorm
        hidden_states = self.norm(hidden_states)

        # Compute logits
        logits = self.lm_head(hidden_states)

        auxiliary_outputs: dict[str, Any] = {}
        if return_hidden_states:
            auxiliary_outputs["hidden_states"] = tuple(all_hidden_states)

        return LMOutput(
            logits=logits,
            auxiliary_outputs=auxiliary_outputs,
            state=None,
        )

    def get_capabilities(self) -> ModelCapabilities:
        """Return declared capabilities."""
        return ModelCapabilities(
            supports_kv_cache=False,
            supports_cross_document_attention=True,
            supports_bidirectional=False,
            max_context_length=self.config.context_length,
            custom_state=False,
        )

    def count_parameters(self) -> ParameterCounts:
        """Enumerate parameters using meta-safe counting."""
        return count_model_parameters(self, self.config)


def check_tokenizer_model_compatibility(
    tokenizer: Any,
    model_config: TransformerBaselineConfig,
) -> tuple[bool, str]:
    """Validate that a tokenizer's vocabulary and special tokens match the model configuration.

    Complying with Amendment 5:
    - Verifies exact vocab_size match.
    - Verifies reserved special token mapping (pad=0, bos=1, eos=2, unk=3).
    - Rejects undersized, oversized, or mis-mapped tokenizers.
    """
    tok_vocab = getattr(tokenizer, "vocab_size", None)
    if tok_vocab is None:
        raise ValueError("Tokenizer does not declare 'vocab_size' attribute.")

    if tok_vocab != model_config.vocab_size:
        raise ValueError(
            f"Tokenizer vocabulary size ({tok_vocab}) does not match model "
            f"vocab_size ({model_config.vocab_size}). Truncation or expansion is forbidden."
        )

    # Check special token IDs
    pad_id = getattr(tokenizer, "pad_id", None)
    bos_id = getattr(tokenizer, "bos_id", None)
    eos_id = getattr(tokenizer, "eos_id", None)
    unk_id = getattr(tokenizer, "unk_id", None)

    expected_specials = {"pad": 0, "bos": 1, "eos": 2, "unk": 3}
    actual_specials = {"pad": pad_id, "bos": bos_id, "eos": eos_id, "unk": unk_id}

    for name, expected in expected_specials.items():
        actual = actual_specials[name]
        if actual != expected:
            raise ValueError(
                f"Tokenizer special token '{name}' ID mismatch: expected {expected}, got {actual}."
            )

    return True, "Tokenizer and model configuration are strictly compatible."


def create_transformer_baseline(
    config: Any,
    device: Any = None,
    seed: int | None = None,
    activation_checkpointing: bool = False,
) -> TransformerBaseline:
    """Factory loader for transformer_baseline architecture."""
    if isinstance(config, dict):
        if config.get("kind") == "model_preset":
            clean_dict = {
                k: v for k, v in config.items() if k not in ("schema_version", "kind", "id")
            }
            validated_config = TransformerBaselineConfig.model_validate(clean_dict)
        else:
            validated_config = TransformerBaselineConfig.model_validate(config)
    elif isinstance(config, TransformerBaselineConfig):
        validated_config = config
    else:
        clean_dict = config.model_dump(exclude={"schema_version", "kind", "id"})
        validated_config = TransformerBaselineConfig.model_validate(clean_dict)

    return TransformerBaseline(
        validated_config,
        device=device,
        seed=seed,
        activation_checkpointing=activation_checkpointing,
    )
