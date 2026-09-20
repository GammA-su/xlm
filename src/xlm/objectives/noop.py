"""No-op pass-through objective plugin used solely to verify research extensibility.

Complying with XLM Contract C09 and P04 Amendment 1:
"Keep a pure pass-through objective that matches baseline CE loss and model gradients.
Do not present either fixture as a novel research loss."
"""

from __future__ import annotations

from typing import Any

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import LMOutput, LossResult, TrainingBatch
from xlm.objectives.base import BaseObjective, ObjectiveCapabilities
from xlm.objectives.cross_entropy import CrossEntropyObjective


class NoOpObjectiveConfig(StrictConfigModel):
    """Configuration for no-op objective plugin."""

    type: str = "noop_objective"
    version: str = "1"


class NoOpObjective(BaseObjective):
    """Pure pass-through objective matching baseline next-token CE loss and model gradients."""

    def __init__(self, config: NoOpObjectiveConfig | None = None) -> None:
        super().__init__()
        self.config = config or NoOpObjectiveConfig()
        self._inner_ce = CrossEntropyObjective()
        self._capabilities = ObjectiveCapabilities(
            loss_protocol="token_additive",
            supports_microbatching=True,
            has_auxiliary_parameters=False,
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
        """Forward directly delegates to standard baseline CrossEntropyObjective."""
        return self._inner_ce.forward(model_output, batch, allow_empty=allow_empty)


def create_noop_objective(
    config: NoOpObjectiveConfig | dict[str, Any] | None = None,
) -> NoOpObjective:
    """Factory creating NoOpObjective."""
    if config is None:
        cfg = NoOpObjectiveConfig()
    elif isinstance(config, dict):
        cfg = NoOpObjectiveConfig(**config)
    else:
        cfg = config
    return NoOpObjective(cfg)
