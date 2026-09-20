"""Meta-safe parameter accounting and formula verification complying with XLM Contract C08."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import torch.nn as nn


@dataclass(frozen=True)
class ParameterCounts:
    """Detailed parameter count breakdown complying with Contract C08 and Amendment 4."""

    total_instantiated: int
    unique_deployed: int
    active: int
    non_embedding: int
    training_only: int
    tied_parameters: int
    formula_estimate: int
    formula_matches: bool
    max_deployed_cap: int | None
    cap_respected: bool | None
    device: str
    tied_aliases: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_instantiated": self.total_instantiated,
            "unique_deployed": self.unique_deployed,
            "active": self.active,
            "non_embedding": self.non_embedding,
            "training_only": self.training_only,
            "tied_parameters": self.tied_parameters,
            "formula_estimate": self.formula_estimate,
            "formula_matches": self.formula_matches,
            "max_deployed_cap": self.max_deployed_cap,
            "cap_respected": self.cap_respected,
            "device": self.device,
            "tied_aliases": self.tied_aliases,
        }


def compute_parameter_formula(
    vocab_size: int,
    hidden_size: int,
    num_layers: int,
    intermediate_size: int,
) -> int:
    """Compute expected parameter count from Contract C08 formula:

    Formula: V*d + L*(4*d*d + 3*d*f + 2*d) + d
    where:
      - V: vocab_size
      - d: hidden_size
      - L: num_layers
      - f: intermediate_size (FFN intermediate dimension)
    """
    v = vocab_size
    d = hidden_size
    l_layers = num_layers
    f = intermediate_size

    embedding_and_tied_head = v * d
    per_layer = 4 * d * d + 3 * d * f + 2 * d
    final_norm = d

    return embedding_and_tied_head + l_layers * per_layer + final_norm


def count_model_parameters(model: nn.Module, config: Any) -> ParameterCounts:
    """Perform meta-safe parameter enumeration using Parameter object identity.

    Complying with Amendment 4:
    - Uses Parameter object identity to detect tied weights on both meta and real devices.
    - Does NOT use data_ptr() for deduplication on meta (where pointer is 0).
    - On non-meta devices, verifies that storage pointers match for tied parameters,
      and raises an explicit error if untracked distinct parameters share storage views.
    - Cross-checks against the theoretical C08 formula and declared caps.
    """
    seen_param_ids: dict[int, str] = {}
    unique_params: list[tuple[str, nn.Parameter]] = []
    tied_aliases: list[tuple[str, str]] = []
    tied_count = 0
    total_instantiated = 0

    device_type = "unknown"

    for name, param in model.named_parameters(remove_duplicate=False):
        numel = param.numel()
        total_instantiated += numel
        device_type = param.device.type

        p_id = id(param)
        if p_id in seen_param_ids:
            # Tied weight detected via object identity
            orig_name = seen_param_ids[p_id]
            tied_aliases.append((name, orig_name))
            tied_count += numel
        else:
            seen_param_ids[p_id] = name
            unique_params.append((name, param))

    # Cross-check storage pointers on non-meta devices
    if device_type != "meta":
        ptr_to_name: dict[int, str] = {}
        for name, param in unique_params:
            if param.numel() > 0:
                ptr = param.data_ptr()
                if ptr in ptr_to_name:
                    raise ValueError(
                        f"Unsupported shared-storage view detected between distinct parameters "
                        f"'{name}' and '{ptr_to_name[ptr]}'. Parameter accounting requires "
                        "explicit Parameter object identity tying."
                    )
                ptr_to_name[ptr] = name

    unique_deployed = sum(p.numel() for _, p in unique_params)

    # Calculate embedding parameters
    embedding_params = 0
    if hasattr(model, "embed_tokens") and isinstance(model.embed_tokens, nn.Embedding):
        embedding_params = model.embed_tokens.weight.numel()

    non_embedding = unique_deployed - embedding_params
    active = unique_deployed  # For dense transformer, all unique deployed parameters are active
    training_only = 0

    formula_estimate = compute_parameter_formula(
        vocab_size=getattr(config, "vocab_size", 32768),
        hidden_size=getattr(config, "hidden_size", 512),
        num_layers=getattr(config, "num_layers", 10),
        intermediate_size=getattr(config, "intermediate_size", 1472),
    )

    formula_matches = unique_deployed == formula_estimate

    max_cap = getattr(config, "max_deployed_parameters", None)
    cap_respected = (unique_deployed <= max_cap) if max_cap is not None else None

    return ParameterCounts(
        total_instantiated=total_instantiated,
        unique_deployed=unique_deployed,
        active=active,
        non_embedding=non_embedding,
        training_only=training_only,
        tied_parameters=tied_count,
        formula_estimate=formula_estimate,
        formula_matches=formula_matches,
        max_deployed_cap=max_cap,
        cap_respected=cap_respected,
        device=device_type,
        tied_aliases=tied_aliases,
    )
