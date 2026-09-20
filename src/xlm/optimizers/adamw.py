"""AdamW optimizer factory, state serialization, and manifest validation.

Complying with XLM Contract C09 and P04 Amendments 5 and 6.
"""

from __future__ import annotations

import copy
from typing import Any

import torch
import torch.nn as nn

from xlm.config.schemas import AdamWConfig
from xlm.optimizers.base import (
    ParameterGroupManifest,
    build_parameter_groups,
)


class UnsupportedOptimizerError(ValueError):
    """Raised when an unsupported optimizer configuration or closure is requested."""


def create_adamw_optimizer(
    config: AdamWConfig | dict[str, Any] | None,
    model: nn.Module,
    objective: nn.Module | None = None,
    *,
    decay_auxiliary: bool = False,
) -> tuple[torch.optim.AdamW, ParameterGroupManifest]:
    """Create a configured AdamW optimizer with unique parameter grouping and manifest.

    Complying with Contract C09:
    - Betas (0.9, 0.95), eps 1e-8, weight_decay 0.1, norm gains excluded.
    - Tied embedding decay policy explicitly applied.
    - Objective-owned parameters included without double-counting.
    - Returns optimizer instance and semantic ParameterGroupManifest.
    """
    if config is None:
        cfg = AdamWConfig(lr=1e-3)
    elif isinstance(config, dict):
        cfg = AdamWConfig(**config)
    else:
        cfg = config

    groups, manifest = build_parameter_groups(
        model=model,
        objective=objective,
        weight_decay=cfg.weight_decay,
        lr=cfg.lr,
        decay_norms=cfg.decay_norms,
        decay_embeddings=cfg.decay_embeddings,
        decay_auxiliary=decay_auxiliary,
    )

    optimizer = torch.optim.AdamW(
        groups,
        lr=cfg.lr,
        betas=cfg.betas,
        eps=cfg.eps,
        weight_decay=cfg.weight_decay,
    )

    return optimizer, manifest


def serialize_optimizer_state(
    optimizer: torch.optim.Optimizer,
    manifest: ParameterGroupManifest,
) -> dict[str, Any]:
    """Serialize optimizer state dict together with semantic parameter group manifest.

    Complying with P04 Amendment 6:
    "Ensure saved test state is an independent snapshot rather than an in-memory
    reference that later updates can modify."
    """
    state_dict = copy.deepcopy(optimizer.state_dict())
    return {
        "manifest": manifest.to_dict(),
        "state_dict": state_dict,
    }


def restore_optimizer_state(
    optimizer: torch.optim.Optimizer,
    serialized_state: dict[str, Any],
    current_manifest: ParameterGroupManifest,
) -> None:
    """Restore optimizer state with strict verification of semantic parameter identity.

    Complying with P04 Amendment 6:
    "Persist and verify an ordered parameter-group manifest covering canonical names,
    aliases, shapes, relevant dtype policy, and group settings. Do not persist
    runtime id(p) as checkpoint identity. Do not assume torch optimizer.load_state_dict
    verifies semantic parameter identity."
    """
    if "manifest" not in serialized_state or "state_dict" not in serialized_state:
        raise ValueError("Serialized optimizer state missing required 'manifest' or 'state_dict'")

    saved_manifest = ParameterGroupManifest.from_dict(serialized_state["manifest"])
    # Strictly verify semantic parameter identity, names, shapes, and group order
    current_manifest.verify_against(saved_manifest)

    optimizer.load_state_dict(serialized_state["state_dict"])
