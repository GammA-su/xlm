"""Nonnovel architecture control: the baseline Transformer through the plugin seam.

No new mechanism. Instantiation goes through the plugin registry instead of the
direct factory, which is exactly what the seam exists to support.
"""

from __future__ import annotations

from typing import Any

from xlm.config.schemas import TransformerBaselineConfig
from xlm.core.registry import Registry
from xlm.models.transformer import TransformerBaseline


class NoOpTransformerBaseline(TransformerBaseline):
    """Baseline computation, plugin packaging. Intentionally behavior-free."""


def create_noop_architecture(
    config: TransformerBaselineConfig | dict[str, Any] | None = None,
    device: Any = None,
    seed: int | None = None,
) -> NoOpTransformerBaseline:
    """Factory creating the no-op architecture from config."""
    if config is None:
        raise ValueError("noop_architecture requires an explicit config")
    if isinstance(config, dict):
        config = TransformerBaselineConfig.model_validate(config)
    return NoOpTransformerBaseline(config, device=device, seed=seed)


def register(registry: Registry[Any], capabilities: Any) -> Any:
    """Register the no-op architecture control."""
    caps = capabilities.to_dict() if hasattr(capabilities, "to_dict") else dict(capabilities)
    return registry.register(
        "noop_architecture",
        "1",
        TransformerBaselineConfig,
        capabilities=caps,
        factory=create_noop_architecture,
    )
