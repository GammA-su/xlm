"""Nonnovel objective control: registers the existing pass-through objective."""

from __future__ import annotations

from typing import Any

from xlm.core.registry import Registry
from xlm.objectives.noop import NoOpObjectiveConfig, create_noop_objective


def register(registry: Registry[Any], capabilities: Any) -> Any:
    """Register the no-op objective control."""
    caps = capabilities.to_dict() if hasattr(capabilities, "to_dict") else dict(capabilities)
    return registry.register(
        "noop_objective",
        "1",
        NoOpObjectiveConfig,
        capabilities=caps,
        factory=create_noop_objective,
    )
