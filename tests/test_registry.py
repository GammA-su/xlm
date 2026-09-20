"""Unit tests for typed component registries and plugin schema ownership."""

from typing import Literal

import pytest
from pydantic import Field, ValidationError

from xlm.config.schemas import StrictConfigModel
from xlm.core.registry import Registry, architectures, optimizers


class MockMambaConfig(StrictConfigModel):
    """Fixture plugin architecture schema."""

    architecture: Literal["mock_mamba"] = "mock_mamba"
    version: str = "1"
    d_model: int = Field(gt=0)
    d_state: int = Field(default=16, gt=0)
    expand: int = Field(default=2, gt=0)


class MockLionConfig(StrictConfigModel):
    """Fixture plugin optimizer schema."""

    type: Literal["mock_lion"] = "mock_lion"
    version: str = "1"
    lr: float = Field(gt=0.0)
    betas: tuple[float, float] = (0.9, 0.99)
    weight_decay: float = Field(default=0.01, ge=0.0)


def test_registry_registration_and_lookup() -> None:
    """Verify registry stores and retrieves component schemas."""
    test_reg: Registry[object] = Registry("test_kind")
    entry = test_reg.register(
        identifier="test_comp",
        version="1",
        config_schema=MockMambaConfig,
        capabilities={"linear_attention": True},
        serializer_version="1",
    )
    assert entry.identifier == "test_comp"
    assert test_reg.has("test_comp", "1")
    assert test_reg.get("test_comp", "1") == entry

    # Duplicate registration error
    with pytest.raises(ValueError, match="Duplicate registration"):
        test_reg.register(
            identifier="test_comp",
            version="1",
            config_schema=MockMambaConfig,
        )

    # Unknown lookup error
    with pytest.raises(KeyError, match="Unknown test_kind component"):
        test_reg.get("nonexistent", "1")


def test_configuration_only_plugins_without_generic_composer_edits() -> None:
    """Verify new architecture and optimizer plugins can be registered and validated."""
    # Register MockMamba in global architectures registry
    if not architectures.has("mock_mamba", "1"):
        architectures.register(
            identifier="mock_mamba",
            version="1",
            config_schema=MockMambaConfig,
            capabilities={"state_space": True, "supports_kv_cache": False},
        )

    # Register MockLion in global optimizers registry
    if not optimizers.has("mock_lion", "1"):
        optimizers.register(
            identifier="mock_lion",
            version="1",
            config_schema=MockLionConfig,
            capabilities={"supports_weight_decay": True},
        )

    # Validate valid mamba config
    valid_mamba = {"architecture": "mock_mamba", "version": "1", "d_model": 512, "d_state": 32}
    validated_arch = architectures.validate_config("mock_mamba", "1", valid_mamba)
    assert isinstance(validated_arch, MockMambaConfig)
    assert validated_arch.d_model == 512

    # Validate valid lion config
    valid_lion = {"type": "mock_lion", "version": "1", "lr": 1e-4, "betas": (0.9, 0.99)}
    validated_opt = optimizers.validate_config("mock_lion", "1", valid_lion)
    assert isinstance(validated_opt, MockLionConfig)
    assert validated_opt.lr == 1e-4

    # Mamba config with unknown key fails strict validation (no kwargs escape hatch)
    with pytest.raises(ValidationError):
        architectures.validate_config(
            "mock_mamba",
            "1",
            {"architecture": "mock_mamba", "d_model": 512, "unvalidated_kwarg": "leak"},
        )

    # Verify capabilities query
    entry = architectures.get("mock_mamba", "1")
    assert entry.capabilities["state_space"] is True
    assert entry.capabilities["supports_kv_cache"] is False
