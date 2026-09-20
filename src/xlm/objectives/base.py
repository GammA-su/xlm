"""Base classes, protocols, and interfaces for XLM objectives complying with Contract C09."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import torch.nn as nn

from xlm.core.contracts import LMOutput, LossResult, TrainingBatch


class InvalidBatchError(ValueError):
    """Raised when an objective receives an invalid or wholly all-masked batch."""


class UnsupportedBatchingError(RuntimeError):
    """Raised when unsupported microbatching is attempted on an objective."""


@dataclass(frozen=True)
class ObjectiveCapabilities:
    """Declared capabilities and constraints of an objective plugin."""

    loss_protocol: Literal["token_additive", "custom_batch"] = "token_additive"
    supports_microbatching: bool = True
    has_auxiliary_parameters: bool = False
    requires_hidden_states: bool = False


class BaseObjective(nn.Module, ABC):
    """Abstract base class for all XLM objective plugins."""

    def __init__(self) -> None:
        super().__init__()

    @property
    @abstractmethod
    def capabilities(self) -> ObjectiveCapabilities:
        """Return declared capabilities of this objective."""

    @abstractmethod
    def forward(self, model_output: LMOutput, batch: TrainingBatch) -> LossResult:
        """Evaluate objective on model output and training batch."""

    def get_state(self) -> dict[str, Any]:
        """Serialize objective-owned state dict."""
        return self.state_dict()

    def load_state(self, state: dict[str, Any]) -> None:
        """Load objective-owned state dict."""
        self.load_state_dict(state)


def accumulate_microbatch_gradient(
    loss_result: LossResult,
    total_valid_targets: int,
) -> None:
    """Accumulate gradient for a microbatch scaled by the global valid target count.

    Complying with XLM Contract C09 and Acceptance Requirement A06.

    If the microbatch contains zero valid targets, it is skipped without
    erasing prior accumulated gradients.
    """
    if loss_result.valid_target_denominator == 0:
        # Zero-valid-target microbatch: skipped contribution
        return

    if total_valid_targets <= 0:
        raise InvalidBatchError(
            f"Cannot accumulate gradients with total_valid_targets={total_valid_targets}"
        )

    if loss_result.loss_protocol != "token_additive":
        raise UnsupportedBatchingError(
            f"Objective loss_protocol '{loss_result.loss_protocol}' does not support "
            "standard token-additive microbatch accumulation"
        )

    # Scale standalone loss (which is unscaled_loss_sum / microbatch_valid_targets)
    # by (microbatch_valid_targets / total_valid_targets) so that the effective
    # differentiated quantity is (unscaled_loss_sum / total_valid_targets).
    scale = float(loss_result.valid_target_denominator) / float(total_valid_targets)
    scaled_loss = loss_result.loss * scale
    scaled_loss.backward()
