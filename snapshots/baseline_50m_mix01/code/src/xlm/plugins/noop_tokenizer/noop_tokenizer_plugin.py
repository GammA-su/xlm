"""Nonnovel tokenizer control: byte tokenization through the plugin seam."""

from __future__ import annotations

from typing import Any

from xlm.config.schemas import StrictConfigModel
from xlm.core.registry import Registry
from xlm.tokenizers.byte import ByteTokenizer


class NoOpTokenizerConfig(StrictConfigModel):
    """Configuration for the no-op tokenizer control."""

    type: str = "noop_tokenizer"
    version: str = "1"


class NoOpByteTokenizer(ByteTokenizer):
    """Byte tokenization, plugin packaging. Intentionally behavior-free."""


def create_noop_tokenizer(
    config: NoOpTokenizerConfig | dict[str, Any] | None = None,
) -> NoOpByteTokenizer:
    """Factory creating the no-op tokenizer."""
    return NoOpByteTokenizer()


def register(registry: Registry[Any], capabilities: Any) -> Any:
    """Register the no-op tokenizer control."""
    caps = capabilities.to_dict() if hasattr(capabilities, "to_dict") else dict(capabilities)
    return registry.register(
        "noop_tokenizer",
        "1",
        NoOpTokenizerConfig,
        capabilities=caps,
        factory=create_noop_tokenizer,
    )
