"""Test-only auxiliary learning objective fixture complying with P04 Amendment 1."""

from __future__ import annotations

import torch
import torch.nn as nn

from xlm.core.contracts import LMOutput, LossResult, TrainingBatch
from xlm.objectives.base import BaseObjective, ObjectiveCapabilities
from xlm.objectives.cross_entropy import CrossEntropyObjective


class AuxiliaryLearningObjective(BaseObjective):
    """Test-only objective with an auxiliary trainable parameter and non-zero gradient.

    Complying with P04 Amendment 1:
    - Genuinely non-zero gradient on an objective-owned auxiliary parameter.
    - Summed contribution follows token-additive normalization protocol under uneven microbatching.
    - Used solely for verification of auxiliary parameter optimization, serialization,
      and continuation without weight decay.
    """

    def __init__(
        self,
        init_val: float = 1.0,
        target_val: float = 3.0,
    ) -> None:
        super().__init__()
        self.aux_param = nn.Parameter(torch.tensor(float(init_val), dtype=torch.float32))
        self.target_val = float(target_val)
        self._inner_ce = CrossEntropyObjective()
        self._capabilities = ObjectiveCapabilities(
            loss_protocol="token_additive",
            supports_microbatching=True,
            has_auxiliary_parameters=True,
            requires_hidden_states=False,
        )

    @property
    def capabilities(self) -> ObjectiveCapabilities:
        return self._capabilities

    def forward(
        self,
        model_output: LMOutput,
        batch: TrainingBatch,
        *,
        allow_empty: bool = False,
    ) -> LossResult:
        base_result = self._inner_ce.forward(model_output, batch, allow_empty=allow_empty)
        if base_result.valid_target_denominator == 0:
            return base_result

        valid_targets = base_result.valid_target_denominator
        # Per-token quadratic penalty on aux_param
        aux_per_token = 0.5 * (self.aux_param - self.target_val) ** 2
        aux_unscaled_sum = aux_per_token * float(valid_targets)

        # Combined unscaled sum and combined standalone loss
        total_unscaled_loss_tensor = base_result.loss * float(valid_targets) + aux_unscaled_sum
        combined_loss = total_unscaled_loss_tensor / float(valid_targets)

        diagnostics = dict(base_result.diagnostics)
        diagnostics["aux_param_val"] = float(self.aux_param.item())
        diagnostics["aux_loss"] = float(aux_per_token.item())
        diagnostics["unscaled_loss_sum"] = float(total_unscaled_loss_tensor.item())

        return LossResult(
            loss_protocol="token_additive",
            loss=combined_loss,
            unscaled_loss_sum=float(total_unscaled_loss_tensor.item()),
            valid_target_denominator=valid_targets,
            diagnostics=diagnostics,
            auxiliary_loss=float(aux_per_token.item()),
        )
