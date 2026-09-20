"""XLM learning rate schedules package complying with Contract C09."""

from xlm.schedules.anchors import MODEL_SIZE_LR_ANCHORS, get_lr_anchor_for_model
from xlm.schedules.base import BaseSchedule
from xlm.schedules.constant import (
    ConstantSchedule,
    ConstantScheduleConfig,
    create_constant_schedule,
)
from xlm.schedules.cosine import (
    WarmupCosineSchedule,
    create_warmup_cosine_schedule,
)

__all__ = [
    "MODEL_SIZE_LR_ANCHORS",
    "BaseSchedule",
    "ConstantSchedule",
    "ConstantScheduleConfig",
    "WarmupCosineSchedule",
    "create_constant_schedule",
    "create_warmup_cosine_schedule",
    "get_lr_anchor_for_model",
]
