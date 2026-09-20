"""Constant learning rate fixture schedule complying with Contract C09."""

from __future__ import annotations

from typing import Any

from xlm.config.schemas import StrictConfigModel
from xlm.schedules.base import BaseSchedule


class ConstantScheduleConfig(StrictConfigModel):
    """Configuration for constant schedule fixture."""

    type: str = "constant"
    version: str = "1"
    counter: str = "committed_valid_targets"


class ConstantSchedule(BaseSchedule):
    """Constant learning rate fixture schedule."""

    def __init__(
        self,
        config: ConstantScheduleConfig | None = None,
        base_lr: float = 1e-3,
    ) -> None:
        if base_lr <= 0.0:
            raise ValueError(f"base_lr must be positive, got {base_lr}")
        self.config = config or ConstantScheduleConfig()
        self.base_lr = float(base_lr)

    def get_lr(self, counter_value: int) -> float:
        if counter_value < 0:
            raise ValueError(f"counter_value cannot be negative, got {counter_value}")
        return self.base_lr

    def state_dict(self) -> dict[str, Any]:
        return {
            "type": self.config.type,
            "version": self.config.version,
            "counter": self.config.counter,
            "base_lr": self.base_lr,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        if state.get("type") != self.config.type:
            raise ValueError(
                f"Cannot change schedule type on resume: saved '{state.get('type')}' "
                f"!= current '{self.config.type}'"
            )
        if abs(float(state.get("base_lr", 0.0)) - self.base_lr) > 1e-9:
            raise ValueError(
                f"Cannot change schedule base_lr on resume: saved {state.get('base_lr')} "
                f"!= current {self.base_lr}"
            )


def create_constant_schedule(
    config: ConstantScheduleConfig | dict[str, Any] | None = None,
    base_lr: float = 1e-3,
) -> ConstantSchedule:
    """Factory creating ConstantSchedule."""
    if config is None:
        cfg = ConstantScheduleConfig()
    elif isinstance(config, dict):
        cfg = ConstantScheduleConfig(**config)
    else:
        cfg = config
    return ConstantSchedule(cfg, base_lr=base_lr)
