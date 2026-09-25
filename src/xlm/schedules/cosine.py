"""Warmup + cosine decay learning rate schedule complying with Contract C09."""

from __future__ import annotations

import math
from typing import Any

from xlm.config.schemas import WarmupCosineScheduleConfig
from xlm.schedules.base import BaseSchedule


class WarmupCosineSchedule(BaseSchedule):
    """Warmup + cosine decay learning rate schedule complying with Contract C09.

    Complying with P04 Amendment 7:
    - Counter value represents committed valid targets.
    - Schedule queries are side-effect free.
    - Linear warmup over W targets, cosine decay over H - W targets.
    - Strict validation of all behavior-changing configuration on resume.
    """

    def __init__(
        self,
        config: WarmupCosineScheduleConfig,
        base_lr: float,
    ) -> None:
        if base_lr <= 0.0:
            raise ValueError(f"base_lr must be positive, got {base_lr}")
        self.config = config
        self.base_lr = float(base_lr)
        self.warmup_valid_targets = int(config.warmup_valid_targets)
        self.horizon_valid_targets = int(config.horizon_valid_targets)
        self.min_lr_ratio = float(config.min_lr_ratio)
        self.min_lr = self.base_lr * self.min_lr_ratio

        if self.warmup_valid_targets > self.horizon_valid_targets:
            raise ValueError(
                f"warmup_valid_targets ({self.warmup_valid_targets}) cannot exceed "
                f"horizon_valid_targets ({self.horizon_valid_targets})"
            )

    def get_lr(self, counter_value: int) -> float:
        """Calculate learning rate at counter_value without side effects.

        Documented boundary behavior (the math is policy-independent; the
        trainer's versioned ``lr_policy`` chooses which counter it passes):
        - ``legacy_base_then_postcommit_v1``: update 1 uses the optimizer base
          LR; after each update the trainer applies f(T_end), so later updates
          spanning [T_start, T_end) use f(T_start) (start boundary).
        - ``target_endpoint_before_update_v1``: an update spanning
          [C, C + N) with N actual valid targets uses f(C + N) (endpoint).
        """
        if counter_value < 0:
            raise ValueError(f"counter_value cannot be negative, got {counter_value}")

        # 1. Warmup phase: linear increase from 0 to base_lr
        if self.warmup_valid_targets > 0 and counter_value < self.warmup_valid_targets:
            return self.base_lr * (float(counter_value) / float(self.warmup_valid_targets))

        # If zero warmup and at 0: base_lr
        if self.warmup_valid_targets == 0 and counter_value == 0:
            return self.base_lr

        # 2. Cosine decay phase: from warmup_valid_targets to horizon_valid_targets
        if counter_value <= self.horizon_valid_targets:
            decay_span = self.horizon_valid_targets - self.warmup_valid_targets
            if decay_span == 0:
                return self.min_lr
            progress = float(counter_value - self.warmup_valid_targets) / float(decay_span)
            cosine_factor = 0.5 * (1.0 + math.cos(math.pi * progress))
            return self.min_lr + (self.base_lr - self.min_lr) * cosine_factor

        # 3. Beyond horizon: stays at min_lr
        return self.min_lr

    def state_dict(self) -> dict[str, Any]:
        """Serialize complete configuration and schedule metadata."""
        return {
            "type": self.config.type,
            "version": self.config.version,
            "counter": self.config.counter,
            "base_lr": self.base_lr,
            "horizon_valid_targets": self.horizon_valid_targets,
            "warmup_valid_targets": self.warmup_valid_targets,
            "min_lr_ratio": self.min_lr_ratio,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """Validate all behavioral fields on ordinary resume."""
        if state.get("type") != self.config.type:
            raise ValueError(
                f"Cannot change schedule type on resume: saved '{state.get('type')}' "
                f"!= current '{self.config.type}'"
            )
        if state.get("counter") != self.config.counter:
            raise ValueError(
                f"Cannot change schedule counter on resume: saved '{state.get('counter')}' "
                f"!= current '{self.config.counter}'"
            )
        if abs(float(state.get("base_lr", 0.0)) - self.base_lr) > 1e-9:
            raise ValueError(
                f"Cannot change schedule base_lr on resume: saved {state.get('base_lr')} "
                f"!= current {self.base_lr}"
            )
        if state.get("horizon_valid_targets") != self.horizon_valid_targets:
            raise ValueError(
                f"Cannot change schedule horizon on resume: saved "
                f"{state.get('horizon_valid_targets')} != current {self.horizon_valid_targets}"
            )
        if state.get("warmup_valid_targets") != self.warmup_valid_targets:
            raise ValueError(
                f"Cannot change schedule warmup on resume: saved "
                f"{state.get('warmup_valid_targets')} != current {self.warmup_valid_targets}"
            )
        if abs(float(state.get("min_lr_ratio", 0.0)) - self.min_lr_ratio) > 1e-9:
            raise ValueError(
                f"Cannot change schedule min_lr_ratio on resume: saved "
                f"{state.get('min_lr_ratio')} != current {self.min_lr_ratio}"
            )


def create_warmup_cosine_schedule(
    config: WarmupCosineScheduleConfig | dict[str, Any],
    base_lr: float,
) -> WarmupCosineSchedule:
    """Factory creating WarmupCosineSchedule."""
    if isinstance(config, dict):
        cfg = WarmupCosineScheduleConfig(**config)
    else:
        cfg = config
    return WarmupCosineSchedule(cfg, base_lr=base_lr)
