"""Component registries with schema-driven validation and capability declarations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class RegistryEntry[T]:
    """Registered component entry with schema and capability metadata."""

    identifier: str
    version: str
    config_schema: type[BaseModel]
    capabilities: dict[str, Any]
    serializer_version: str
    factory: Callable[..., T] | None = None

    @property
    def key(self) -> str:
        """Composite key for registry lookup."""
        return f"{self.identifier}:{self.version}"


class Registry[T]:
    """Generic registry for extensible XLM components."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._entries: dict[str, RegistryEntry[T]] = {}

    def register(
        self,
        identifier: str,
        version: str,
        config_schema: type[BaseModel],
        capabilities: dict[str, Any] | None = None,
        serializer_version: str = "1",
        factory: Callable[..., T] | None = None,
    ) -> RegistryEntry[T]:
        """Register a new component entry."""
        if not issubclass(config_schema, BaseModel):
            raise TypeError(
                f"Component '{identifier}' schema must be a BaseModel subclass, got {config_schema}"
            )

        key = f"{identifier}:{version}"
        if key in self._entries:
            raise ValueError(
                f"Duplicate registration in {self.kind} registry: '{key}' is already registered"
            )

        entry = RegistryEntry(
            identifier=identifier,
            version=version,
            config_schema=config_schema,
            capabilities=capabilities or {},
            serializer_version=serializer_version,
            factory=factory,
        )
        self._entries[key] = entry
        return entry

    def get(self, identifier: str, version: str = "1") -> RegistryEntry[T]:
        """Retrieve a registered entry by identifier and version."""
        key = f"{identifier}:{version}"
        if key not in self._entries:
            available = list(self._entries.keys())
            raise KeyError(f"Unknown {self.kind} component '{key}'. Available entries: {available}")
        return self._entries[key]

    def has(self, identifier: str, version: str = "1") -> bool:
        """Check if an entry is registered."""
        return f"{identifier}:{version}" in self._entries

    def validate_config(self, identifier: str, version: str, data: dict[str, Any]) -> BaseModel:
        """Validate component data strictly using the component's registered schema."""
        entry = self.get(identifier, version)
        return entry.config_schema.model_validate(data)

    def list_entries(self) -> list[RegistryEntry[T]]:
        """Return all registered entries."""
        return list(self._entries.values())


# Global component registries
architectures: Registry[Any] = Registry("architecture")
objectives: Registry[Any] = Registry("objective")
optimizers: Registry[Any] = Registry("optimizer")
tokenizers: Registry[Any] = Registry("tokenizer")
source_adapters: Registry[Any] = Registry("source_adapter")
transforms: Registry[Any] = Registry("transform")
schedules: Registry[Any] = Registry("schedule")
