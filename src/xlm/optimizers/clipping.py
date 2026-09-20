"""Global gradient clipping for unique parameter sets complying with Contract C09."""

from __future__ import annotations

import math
from collections.abc import Iterable

import torch
import torch.nn as nn


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

    if norm_type == 2.0:
        total_norm_sq = 0.0
        for grad in grads:
            param_norm = torch.linalg.vector_norm(grad, ord=2).item()
            total_norm_sq += param_norm * param_norm
        total_norm = math.sqrt(total_norm_sq)
    else:
        # Generic Lp norm
        total_norm_p = 0.0
        for grad in grads:
            param_norm = torch.linalg.vector_norm(grad, ord=norm_type).item()
            total_norm_p += param_norm**norm_type
        total_norm = total_norm_p ** (1.0 / norm_type)

    if math.isnan(total_norm) or math.isinf(total_norm):
        raise ValueError(f"Non-finite gradient norm encountered during clipping: {total_norm}")

    clip_coef = max_norm / (total_norm + 1e-6)
    if clip_coef < 1.0:
        for grad in grads:
            grad.mul_(clip_coef)

    return total_norm
