"""Global gradient clipping for unique parameter sets complying with Contract C09."""

from __future__ import annotations

import math
from collections.abc import Iterable

import torch
import torch.nn as nn


def gradients_are_finite(parameters: Iterable[nn.Parameter]) -> bool:
    """Check every gradient, transferring one predicate per device on CUDA.

    Keep the scalar CPU path and support mixed-device auxiliary parameters.
    This does not replace clipping's distinct finite-norm (overflow) guard.
    """
    by_device: dict[torch.device, list[torch.Tensor]] = {}
    for parameter in parameters:
        grad = parameter.grad
        if grad is None:
            continue
        if grad.device.type != "cuda":
            if torch.isnan(grad).any() or torch.isinf(grad).any():
                return False
        else:
            by_device.setdefault(grad.device, []).append(torch.isfinite(grad).all())
    return all(bool(torch.stack(checks).all().item()) for checks in by_device.values())


def clip_global_gradient_norm(
    parameters: Iterable[nn.Parameter],
    max_norm: float,
    norm_type: float = 2.0,
) -> float:
    """Clip gradient norm over the deduplicated set of model and objective parameters.

    Complying with XLM Contract C09 and P04 Amendment 2:
    "Test any implemented clipping after global normalization, over the same unique
    model/objective parameter set. Do not clip each microbatch separately."
    """
    if max_norm <= 0.0:
        raise ValueError(f"max_norm must be positive, got {max_norm}")

    # Deduplicate by object identity in-process so tied weights are not counted twice
    unique_params: list[nn.Parameter] = []
    seen_ids: set[int] = set()

    for p in parameters:
        if id(p) not in seen_ids and p.grad is not None:
            seen_ids.add(id(p))
            unique_params.append(p)

    if not unique_params:
        return 0.0

    # Compute global norm
    grads: list[torch.Tensor] = [p.grad.detach() for p in unique_params if p.grad is not None]
    if not grads:
        return 0.0

    if all(grad.device == grads[0].device for grad in grads) and grads[0].is_cuda:
        # Use the same per-tensor reductions and ordered Python arithmetic as
        # the reference. Only their host transfers are consolidated, avoiding
        # one CUDA synchronization per parameter without changing norm rounding.
        norms = torch.stack([torch.linalg.vector_norm(g, ord=norm_type) for g in grads])
        param_norms = norms.tolist()
    else:
        param_norms = [torch.linalg.vector_norm(g, ord=norm_type).item() for g in grads]

    if norm_type == 2.0:
        total_norm_sq = 0.0
        for param_norm in param_norms:
            total_norm_sq += param_norm * param_norm
        total_norm = math.sqrt(total_norm_sq)
    else:
        # Generic Lp norm
        total_norm_p = 0.0
        for param_norm in param_norms:
            total_norm_p += param_norm**norm_type
        total_norm = total_norm_p ** (1.0 / norm_type)

    if math.isnan(total_norm) or math.isinf(total_norm):
        raise ValueError(f"Non-finite gradient norm encountered during clipping: {total_norm}")

    clip_coef = max_norm / (total_norm + 1e-6)
    if clip_coef < 1.0:
        for grad in grads:
            grad.mul_(clip_coef)

    return total_norm
