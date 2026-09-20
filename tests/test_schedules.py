"""Tests for learning rate schedules and resume validation.

Complying with XLM Contract C09, Acceptance Requirement A07, and P04 Amendment 7.
"""

from __future__ import annotations

import pytest
import torch.nn as nn

from xlm.config.schemas import WarmupCosineScheduleConfig
from xlm.optimizers.adamw import create_adamw_optimizer
from xlm.schedules.anchors import get_lr_anchor_for_model
from xlm.schedules.constant import ConstantSchedule, ConstantScheduleConfig
from xlm.schedules.cosine import WarmupCosineSchedule


def test_warmup_cosine_exact_boundary_values() -> None:
    """Verify warmup cosine schedule at start, warmup, midpoint, end, and beyond."""
    base_lr = 1e-3
    warmup = 100
    horizon = 1000
    min_lr_ratio = 0.1
    min_lr = base_lr * min_lr_ratio  # 1e-4

    cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=warmup,
        horizon_valid_targets=horizon,
        min_lr_ratio=min_lr_ratio,
    )
    sched = WarmupCosineSchedule(cfg, base_lr=base_lr)

    # 1. Start: t=0 -> 0.0
    assert abs(sched.get_lr(0) - 0.0) < 1e-9

    # 2. Linear warmup intermediate: t=50 -> 0.5 * base_lr
    assert abs(sched.get_lr(50) - 0.5 * base_lr) < 1e-9

    # 3. Warmup endpoint: t=100 -> base_lr
    assert abs(sched.get_lr(100) - base_lr) < 1e-9

    # 4. Decay midpoint: t = 100 + (1000 - 100)/2 = 550
    # factor = 0.5 * (1 + cos(pi * 0.5)) = 0.5
    expected_mid_lr = min_lr + (base_lr - min_lr) * 0.5
    assert abs(sched.get_lr(550) - expected_mid_lr) < 1e-9

    # 5. Horizon endpoint: t=1000 -> min_lr
    assert abs(sched.get_lr(1000) - min_lr) < 1e-9

    # 6. Beyond horizon: t=1200 -> min_lr
    assert abs(sched.get_lr(1200) - min_lr) < 1e-9


def test_warmup_cosine_zero_warmup() -> None:
    """Verify schedule behavior when warmup is 0."""
    base_lr = 1e-3
    horizon = 500
    min_lr = 1e-4

    cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=0,
        horizon_valid_targets=horizon,
        min_lr_ratio=0.1,
    )
    sched = WarmupCosineSchedule(cfg, base_lr=base_lr)

    assert abs(sched.get_lr(0) - base_lr) < 1e-9
    assert abs(sched.get_lr(horizon) - min_lr) < 1e-9


def test_schedule_queries_are_side_effect_free() -> None:
    """Verify that schedule queries do not mutate schedule state."""
    cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=100,
        horizon_valid_targets=1000,
        min_lr_ratio=0.1,
    )
    sched = WarmupCosineSchedule(cfg, base_lr=1e-3)

    lr_first = sched.get_lr(250)
    # Query other points
    _ = sched.get_lr(800)
    _ = sched.get_lr(50)
    # Query original point again
    lr_second = sched.get_lr(250)

    assert abs(lr_first - lr_second) < 1e-9


def test_apply_lr_to_optimizer() -> None:
    """Verify applying scheduled learning rate to an optimizer's parameter groups."""
    m = nn.Linear(4, 2)
    opt, _ = create_adamw_optimizer(None, model=m)

    cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=100,
        horizon_valid_targets=1000,
        min_lr_ratio=0.1,
    )
    sched = WarmupCosineSchedule(cfg, base_lr=1e-3)

    applied_lr = sched.apply_lr_to_optimizer(opt, counter_value=100)
    assert abs(applied_lr - 1e-3) < 1e-9

    for g in opt.param_groups:
        assert abs(g["lr"] - 1e-3) < 1e-9


def test_schedule_resume_rejects_altered_configuration() -> None:
    """Verify that changing any behavioral configuration on resume raises ValueError.

    Complying with P04 Amendment 7:
    "On ordinary resume, validate the full behavior-changing schedule configuration,
    not only H: type/version, counter identity, warmup, base LR, minimum ratio,
    and any group multipliers."
    """
    cfg = WarmupCosineScheduleConfig(
        warmup_valid_targets=100,
        horizon_valid_targets=1000,
        min_lr_ratio=0.1,
    )
    sched = WarmupCosineSchedule(cfg, base_lr=1e-3)
    saved_state = sched.state_dict()

    # 1. Successful reload with identical configuration
    sched_fresh = WarmupCosineSchedule(cfg, base_lr=1e-3)
    sched_fresh.load_state_dict(saved_state)

    # 2. Altered horizon on resume
    cfg_bad_horizon = WarmupCosineScheduleConfig(
        warmup_valid_targets=100,
        horizon_valid_targets=2000,  # Changed!
        min_lr_ratio=0.1,
    )
    sched_bad_h = WarmupCosineSchedule(cfg_bad_horizon, base_lr=1e-3)
    with pytest.raises(ValueError, match="Cannot change schedule horizon on resume"):
        sched_bad_h.load_state_dict(saved_state)

    # 3. Altered base_lr on resume
    sched_bad_lr = WarmupCosineSchedule(cfg, base_lr=5e-4)  # Changed!
    with pytest.raises(ValueError, match="Cannot change schedule base_lr on resume"):
        sched_bad_lr.load_state_dict(saved_state)

    # 4. Altered warmup on resume
    cfg_bad_warmup = WarmupCosineScheduleConfig(
        warmup_valid_targets=200,  # Changed!
        horizon_valid_targets=1000,
        min_lr_ratio=0.1,
    )
    sched_bad_w = WarmupCosineSchedule(cfg_bad_warmup, base_lr=1e-3)
    with pytest.raises(ValueError, match="Cannot change schedule warmup on resume"):
        sched_bad_w.load_state_dict(saved_state)


def test_constant_schedule_fixture() -> None:
    """Verify constant schedule fixture maintains fixed LR across all steps."""
    cfg = ConstantScheduleConfig()
    sched = ConstantSchedule(cfg, base_lr=2e-3)

    assert abs(sched.get_lr(0) - 2e-3) < 1e-9
    assert abs(sched.get_lr(1000) - 2e-3) < 1e-9
    assert abs(sched.get_lr(50000) - 2e-3) < 1e-9

    state = sched.state_dict()
    sched_reloaded = ConstantSchedule(cfg, base_lr=2e-3)
    sched_reloaded.load_state_dict(state)
    assert abs(sched_reloaded.get_lr(100) - 2e-3) < 1e-9


def test_lr_anchors_by_model_size() -> None:
    """Verify initial LR anchors for reference models."""
    assert get_lr_anchor_for_model("50m") == 1e-3
    assert get_lr_anchor_for_model("150m") == 6e-4
    assert get_lr_anchor_for_model("300m") == 3e-4
    assert get_lr_anchor_for_model("tiny") == 1e-3

    with pytest.raises(KeyError, match="No LR anchor defined"):
        get_lr_anchor_for_model("gigantic_model")
