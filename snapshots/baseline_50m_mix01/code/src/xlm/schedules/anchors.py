"""Per-size learning rate anchors complying with Contract C09."""

from __future__ import annotations

# Contract C09 uncalibrated initial LR anchors by model size
# "Initial LR anchors are 1e-3 / 6e-4 / 3e-4 by size; they need calibration."
MODEL_SIZE_LR_ANCHORS: dict[str, float] = {
    "tiny": 1e-3,
    "50m": 1e-3,
    "150m": 6e-4,
    "300m": 3e-4,
}


def get_lr_anchor_for_model(model_size: str) -> float:
    """Return the uncalibrated starting learning rate anchor for a reference model size.

    Complying with XLM Contract C09 and P04 Amendment 7:
    "Keep per-size LR anchors as uncalibrated configuration defaults, not model-size
    branches inside generic scheduling or training code."
    """
    normalized = model_size.strip().lower()
    if normalized not in MODEL_SIZE_LR_ANCHORS:
        raise KeyError(
            f"No LR anchor defined for model size '{model_size}'. "
            f"Supported sizes: {list(MODEL_SIZE_LR_ANCHORS.keys())}"
        )
    return MODEL_SIZE_LR_ANCHORS[normalized]
