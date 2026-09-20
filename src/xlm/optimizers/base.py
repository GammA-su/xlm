"""Base parameter grouping, manifest validation, and memory estimation for optimizers.

Complying with XLM Contract C09 and P04 Amendments 5 and 6.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import torch.nn as nn


@dataclass(frozen=True)
class ParameterGroupInfo:
    """Metadata for a single optimizer parameter group."""

    group_index: int
    canonical_param_names: list[str]
    weight_decay: float
    lr: float
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParameterGroupManifest:
    """Immutable manifest of optimizer parameter grouping and parameter identities.

    Complying with P04 Amendment 6:
    "Persist and verify an ordered parameter-group manifest covering canonical names,
    aliases, shapes, relevant dtype policy, and group settings. Do not persist
    runtime id(p) as checkpoint identity."
    """

    groups: list[dict[str, Any]]
    param_shapes: dict[str, list[int]]
    param_dtypes: dict[str, str]
    aliases: dict[str, str]  # alias_name -> canonical_name
    frozen_parameters: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParameterGroupManifest:
        return cls(
            groups=data["groups"],
            param_shapes=data["param_shapes"],
            param_dtypes=data["param_dtypes"],
            aliases=data["aliases"],
            frozen_parameters=data.get("frozen_parameters", []),
        )

    def verify_against(self, other: ParameterGroupManifest) -> None:
        """Strictly verify semantic parameter identity against another manifest.

        Raises ValueError if group counts, parameter order, names, or shapes differ.
        """
        if len(self.groups) != len(other.groups):
            raise ValueError(
                f"Parameter group count mismatch: saved has {len(self.groups)} groups, "
                f"current has {len(other.groups)} groups"
            )

        for i, (g_self, g_other) in enumerate(zip(self.groups, other.groups, strict=True)):
            if g_self.get("canonical_param_names") != g_other.get("canonical_param_names"):
                raise ValueError(
                    f"Parameter names or order mismatch in group {i}:\n"
                    f"  Saved:   {g_self.get('canonical_param_names')}\n"
                    f"  Current: {g_other.get('canonical_param_names')}"
                )
            if (
                abs(
                    float(g_self.get("weight_decay", 0.0)) - float(g_other.get("weight_decay", 0.0))
                )
                > 1e-9
            ):
                raise ValueError(
                    f"Weight decay mismatch in group {i}: saved {g_self.get('weight_decay')} "
                    f"!= current {g_other.get('weight_decay')}"
                )

        if self.param_shapes != other.param_shapes:
            raise ValueError(
                f"Parameter shapes mismatch:\n"
                f"  Saved:   {self.param_shapes}\n"
                f"  Current: {other.param_shapes}"
            )


def build_parameter_groups(
    model: nn.Module,
    objective: nn.Module | None = None,
    *,
    weight_decay: float = 0.1,
    lr: float = 1e-3,
    decay_norms: bool = False,
    decay_embeddings: bool = True,
    decay_auxiliary: bool = False,
) -> tuple[list[dict[str, Any]], ParameterGroupManifest]:
    """Group model and objective parameters with explicit weight decay rules.

    Complying with XLM Contract C09 and P04 Amendments 1, 5, and 6:
    - Deduplicate by Parameter object identity in-process.
    - Tied weights are grouped exactly once.
    - Resolves norm/embedding/auxiliary decay policy across all aliases prior to assignment.
    - Norm layers and 1D biases are excluded from weight decay.
    - Objective-owned auxiliary parameters are included.
    - Returns parameter groups and an ordered semantic ParameterGroupManifest.
    """
    param_to_canonical: dict[int, str] = {}
    param_to_aliases: dict[int, list[str]] = {}
    canonical_to_param: dict[str, nn.Parameter] = {}
    param_shapes: dict[str, list[int]] = {}
    param_dtypes: dict[str, str] = {}
    frozen_parameters: list[str] = []
    aliases: dict[str, str] = {}

    # 1. Collect all model parameters
    for name, p in model.named_parameters(remove_duplicate=False):
        full_name = f"model.{name}" if not name.startswith("model.") else name
        if not p.requires_grad:
            frozen_parameters.append(full_name)
            continue

        p_id = id(p)
        if p_id in param_to_canonical:
            canonical_name = param_to_canonical[p_id]
            param_to_aliases[p_id].append(full_name)
            aliases[full_name] = canonical_name
        else:
            param_to_canonical[p_id] = full_name
            param_to_aliases[p_id] = [full_name]
            canonical_to_param[full_name] = p
            param_shapes[full_name] = list(p.shape)
            param_dtypes[full_name] = str(p.dtype).replace("torch.", "")

    # 2. Collect all objective parameters (if any)
    if objective is not None:
        for name, p in objective.named_parameters(remove_duplicate=False):
            full_name = f"objective.{name}" if not name.startswith("objective.") else name
            if not p.requires_grad:
                frozen_parameters.append(full_name)
                continue

            p_id = id(p)
            if p_id in param_to_canonical:
                canonical_name = param_to_canonical[p_id]
                param_to_aliases[p_id].append(full_name)
                aliases[full_name] = canonical_name
            else:
                param_to_canonical[p_id] = full_name
                param_to_aliases[p_id] = [full_name]
                canonical_to_param[full_name] = p
                param_shapes[full_name] = list(p.shape)
                param_dtypes[full_name] = str(p.dtype).replace("torch.", "")

    # 3. Resolve decay policy across all aliases for each unique parameter
    decay_params: list[nn.Parameter] = []
    decay_names: list[str] = []
    no_decay_params: list[nn.Parameter] = []
    no_decay_names: list[str] = []

    for p_id, canonical_name in param_to_canonical.items():
        all_names = param_to_aliases[p_id]
        p = canonical_to_param[canonical_name]

        # Norm rule: norm layers or 1D biases are excluded from weight decay
        is_norm = any("norm" in n.lower() or "rmsnorm" in n.lower() for n in all_names)
        is_embedding = any("embed_tokens" in n or "lm_head" in n for n in all_names)
        is_auxiliary = any(n.startswith("objective.") for n in all_names)
        is_1d_or_scalar = p.ndim <= 1

        if is_norm and not decay_norms:
            should_decay = False
        elif is_1d_or_scalar and not is_auxiliary:
            should_decay = False
        elif is_embedding:
            should_decay = decay_embeddings
        elif is_auxiliary:
            should_decay = decay_auxiliary
        else:
            should_decay = True

        if should_decay:
            decay_params.append(p)
            decay_names.append(canonical_name)
        else:
            no_decay_params.append(p)
            no_decay_names.append(canonical_name)

    # 4. Construct parameter groups
    groups: list[dict[str, Any]] = []
    group_manifests: list[dict[str, Any]] = []

    if decay_params:
        groups.append(
            {
                "params": decay_params,
                "weight_decay": float(weight_decay),
                "lr": float(lr),
            }
        )
        group_manifests.append(
            {
                "group_index": 0,
                "canonical_param_names": decay_names,
                "weight_decay": float(weight_decay),
                "lr": float(lr),
            }
        )

    if no_decay_params:
        idx = len(groups)
        groups.append(
            {
                "params": no_decay_params,
                "weight_decay": 0.0,
                "lr": float(lr),
            }
        )
        group_manifests.append(
            {
                "group_index": idx,
                "canonical_param_names": no_decay_names,
                "weight_decay": 0.0,
                "lr": float(lr),
            }
        )

    manifest = ParameterGroupManifest(
        groups=group_manifests,
        param_shapes=param_shapes,
        param_dtypes=param_dtypes,
        aliases=aliases,
        frozen_parameters=frozen_parameters,
    )

    # Verification: check no duplicate parameters across groups and no omissions
    total_grouped = sum(len(g["params"]) for g in groups)
    if total_grouped != len(canonical_to_param):
        raise ValueError(
            f"Parameter grouping error: {len(canonical_to_param)} unique parameters, "
            f"but {total_grouped} parameters assigned to groups."
        )

    return groups, manifest


def estimate_optimizer_memory(
    manifest: ParameterGroupManifest,
    *,
    optimizer_type: str = "adamw",
    moment_dtype: str = "float32",
) -> dict[str, Any]:
    """Estimate memory required for optimizer states based on unique parameter manifest.

    Complying with XLM Contract C09 and P04 Amendment 5:
    "Estimate state memory from unique optimizer-group parameters, including objective-owned
    parameters. Declare moment-dtype and implementation assumptions. Separate estimates,
    actual allocated state tensors, and unmeasured peak device memory."
    """
    total_unique_params = 0
    for shape in manifest.param_shapes.values():
        total_unique_params += math.prod(shape)

    bytes_per_elem = 4 if moment_dtype in ("float32", "fp32") else 2

    # For AdamW: 2 moment states per parameter (exp_avg, exp_avg_sq)
    if optimizer_type.lower() == "adamw":
        num_state_tensors_per_param = 2
    elif optimizer_type.lower() in ("sgd", "sgd_momentum"):
        num_state_tensors_per_param = 1
    else:
        num_state_tensors_per_param = 2

    estimated_state_bytes = total_unique_params * num_state_tensors_per_param * bytes_per_elem

    return {
        "optimizer_type": optimizer_type,
        "moment_dtype": moment_dtype,
        "num_unique_trainable_parameters": total_unique_params,
        "num_state_tensors_per_param": num_state_tensors_per_param,
        "bytes_per_element": bytes_per_elem,
        "estimated_state_memory_bytes": estimated_state_bytes,
        "estimated_state_memory_mib": estimated_state_bytes / (1024.0 * 1024.0),
        "is_formula_estimate": True,
        "notes": (
            "Formula-based estimate: does not include PyTorch overhead, grad buffers, "
            "activation memory, or master weights."
        ),
    }
