"""Exact byte digests of model and training state for evaluation identity and guards.

A digest covers dtype, shape and raw bytes of every tensor, so it distinguishes
``-0.0`` from ``0.0``, NaN payloads, and dtype changes that compare equal as
numbers. Tensors are copied to the CPU only to be hashed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from xlm.artifacts.manifest import canonical_json

MODEL_STATE_DIGEST_VERSION = "xlm-model-state-v1"


def _update_tensor(digest: Any, tensor: Any) -> None:
    import torch

    flat = tensor.detach().reshape(-1).contiguous().cpu()
    digest.update(f"{tensor.dtype}|{tuple(tensor.shape)}|".encode())
    digest.update(flat.view(torch.uint8).numpy().tobytes())


def _update_value(digest: Any, value: Any) -> None:
    import torch

    if isinstance(value, torch.Tensor):
        digest.update(b"T")
        _update_tensor(digest, value)
    elif isinstance(value, Mapping):
        digest.update(b"{")
        for key in sorted(value, key=repr):
            digest.update(repr(key).encode() + b":")
            _update_value(digest, value[key])
        digest.update(b"}")
    elif isinstance(value, (list, tuple)):
        digest.update(b"[" if isinstance(value, list) else b"(")
        for item in value:
            _update_value(digest, item)
        digest.update(b"]")
    elif isinstance(value, float):
        digest.update(b"f" + repr(value).encode())  # repr keeps NaN/inf distinguishable
    elif value is None or isinstance(value, (bool, int, str)):
        digest.update(canonical_json(value))
    else:
        raise TypeError(f"cannot digest state value of type {type(value).__name__}")


def value_digest(value: Any) -> str:
    """Digest of nested tensors/containers/scalars (optimizer or objective state)."""
    digest = hashlib.sha256(b"xlm-state-value-v1\0")
    _update_value(digest, value)
    return digest.hexdigest()


def model_state_digest(model: Any) -> str:
    """Identity of a model's persistent state (``state_dict``), device independent."""
    return state_dict_digest(model.state_dict())


def state_dict_digest(state: Mapping[str, Any]) -> str:
    """The same identity computed from a stored ``state_dict`` (e.g. a checkpoint's model.pt)."""
    digest = hashlib.sha256(MODEL_STATE_DIGEST_VERSION.encode() + b"\0")
    for name, tensor in sorted(state.items()):
        digest.update(name.encode() + b"\0")
        _update_tensor(digest, tensor)
    return digest.hexdigest()


def module_tensor_digest(model: Any) -> str:
    """All parameters and buffers, persistent or not, plus trainability flags."""
    digest = hashlib.sha256(b"xlm-module-tensors-v1\0")
    for name, parameter in model.named_parameters(remove_duplicate=False):
        digest.update(f"P:{name}:{parameter.requires_grad}\0".encode())
        _update_tensor(digest, parameter)
    for name, buffer in model.named_buffers(remove_duplicate=False):
        digest.update(f"B:{name}\0".encode())
        _update_tensor(digest, buffer)
    return digest.hexdigest()


def gradient_digest(model: Any) -> str:
    digest = hashlib.sha256(b"xlm-gradients-v1\0")
    for name, parameter in model.named_parameters(remove_duplicate=False):
        digest.update(name.encode() + b"\0")
        if parameter.grad is None:
            digest.update(b"none")
        else:
            _update_tensor(digest, parameter.grad)
    return digest.hexdigest()
