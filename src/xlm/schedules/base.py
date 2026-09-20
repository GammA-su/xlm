"""Base classes and interfaces for learning rate schedules.

Complying with XLM Contract C09 and P04 Amendment 7.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import torch.optim as optim


class BaseSchedule(ABC):
    """Abstract base class for deterministic, side-effect free learning rate schedules."""

    @abstractmethod
    def get_lr(self, counter_value: int) -> float:
        """Compute the learning rate at the given counter value (e.g. committed valid targets).

        This query must be strictly side-effect free and idempotent.
        """

    @abstractmethod
    def state_dict(self) -> dict[str, Any]:
        """Serialize complete schedule configuration and counter state."""

    @abstractmethod
    def load_state_dict(self, state: dict[str, Any]) -> None:
        """Restore schedule state with strict validation of full behavioral configuration."""

    def apply_lr_to_optimizer(
        self,
        optimizer: optim.Optimizer,
        counter_value: int,
    ) -> float:
        """Compute learning rate at counter_value and apply to all optimizer parameter groups.

        Returns the computed base learning rate.
        """
        lr = self.get_lr(counter_value)
        for group in optimizer.param_groups:
            # If group specifies a custom lr multiplier or scale, preserve it; otherwise apply lr
            multiplier = group.get("lr_multiplier", 1.0)
            group["lr"] = lr * multiplier
        return lr
