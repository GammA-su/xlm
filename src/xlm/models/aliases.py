"""Declared tied-tensor aliases for single-copy serialization (C08, D07).

A *tie* is two state-dict names referring to one intended tensor — the reference
Transformer ties its token embedding to its output head. C08 requires that such
storage is "saved and counted once", which means a serialized bundle must carry
one payload for the tied tensor and enough metadata to rebuild the alias.

Ties are determined from **declared, validated model relationships**, never from
value or hash equality: two independently trainable parameters that happen to
hold identical numbers must stay independent. Detection therefore starts from
``nn.Parameter`` object identity, exactly as parameter counting already does,
and then adds the stricter checks serialization needs.

Only a *complete same-tensor alias* is supported: both names must denote the
whole of one contiguous tensor. Slices, offsets, transposes and other partial or
reinterpreting views share storage without being the same tensor; they are
refused rather than silently flattened into an ordinary tie. A general
arbitrary-view serialization engine is deliberately out of scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import torch
from torch import nn

#: Version of the alias metadata contract written into export manifests. Bump
#: this when the meaning of an alias entry changes, never for cosmetic edits.
ALIAS_SCHEMA_VERSION = "1"


class AliasError(RuntimeError):
    """Raised when tensor aliasing cannot be represented safely."""


class UnsupportedAliasError(AliasError):
    """Raised for storage sharing that is not a complete same-tensor alias."""


@dataclass(frozen=True)
class TensorAlias:
    """One alias name and the canonical name holding its payload."""

    alias: str
    target: str

    def __post_init__(self) -> None:
        if not self.alias or not self.target:
            raise AliasError("alias and target names must both be non-empty")
        if self.alias == self.target:
            raise AliasError(f"alias '{self.alias}' cannot target itself")

    def to_dict(self) -> dict[str, str]:
        return {"alias": self.alias, "target": self.target}


def _describe(tensor: torch.Tensor) -> dict[str, Any]:
    return {
        "shape": tuple(tensor.shape),
        "dtype": str(tensor.dtype),
        "storage_offset": int(tensor.storage_offset()),
        "contiguous": bool(tensor.is_contiguous()),
    }


def _storage_nbytes(tensor: torch.Tensor) -> int:
    return int(tensor.untyped_storage().nbytes())


def assert_complete_alias(
    alias_name: str, alias: torch.Tensor, target_name: str, target: torch.Tensor
) -> None:
    """Refuse anything that is not the whole of one contiguous shared tensor.

    Equal ``data_ptr`` alone is not enough: a transpose, a narrow, or a
    zero-offset slice of a larger buffer can share the same first element while
    denoting different data. Requiring identical shape, dtype, storage offset,
    total storage size and contiguity makes "these two names are the same
    tensor" a checked statement rather than an assumption.
    """
    if alias.shape != target.shape:
        raise UnsupportedAliasError(
            f"alias '{alias_name}' has shape {tuple(alias.shape)} but target "
            f"'{target_name}' has {tuple(target.shape)}; not a complete alias"
        )
    if alias.dtype != target.dtype:
        raise UnsupportedAliasError(
            f"alias '{alias_name}' has dtype {alias.dtype} but target "
            f"'{target_name}' has {target.dtype}; not a complete alias"
        )
    if alias.device.type != "meta":
        if alias.data_ptr() != target.data_ptr():
            raise UnsupportedAliasError(
                f"alias '{alias_name}' and target '{target_name}' do not share storage"
            )
        if alias.storage_offset() != target.storage_offset():
            raise UnsupportedAliasError(
                f"alias '{alias_name}' is a shared-storage view at offset "
                f"{alias.storage_offset()} of '{target_name}'; offset views are not "
                "supported ties"
            )
        if _storage_nbytes(alias) != _storage_nbytes(target):
            raise UnsupportedAliasError(
                f"alias '{alias_name}' and '{target_name}' span different storages; "
                "partial or reinterpreting views are not supported ties"
            )
        if not (alias.is_contiguous() and target.is_contiguous()):
            raise UnsupportedAliasError(
                f"alias '{alias_name}'/'{target_name}' is non-contiguous "
                f"({_describe(alias)} vs {_describe(target)}); only whole contiguous "
                "tensors can be stored once and rebuilt"
            )


def _named_state_tensors(model: nn.Module) -> Iterator[tuple[str, torch.Tensor, str]]:
    """Yield (name, tensor, role) for everything that reaches a state dict.

    Parameters and *persistent* buffers are the two roles a bundle stores.
    Non-persistent buffers (the RoPE caches, for example) are recomputed at
    construction and never serialized, so they are not enumerated here.
    """
    for name, param in model.named_parameters(remove_duplicate=False):
        yield name, param, "parameter"
    persistent = set(model.state_dict().keys())
    for name, buffer in model.named_buffers(remove_duplicate=False):
        if name in persistent and buffer is not None:
            yield name, buffer, "buffer"


def resolve_tied_aliases(model: nn.Module) -> tuple[TensorAlias, ...]:
    """Return the model's declared tied aliases, validated for serialization.

    The first name encountered in ``named_parameters`` order owns the payload,
    which makes the canonical name deterministic for a given architecture.

    :raises UnsupportedAliasError: for shared storage that is not a complete
        same-tensor alias, including a buffer sharing storage with a parameter.
    """
    owner_by_id: dict[int, tuple[str, torch.Tensor, str]] = {}
    aliases: list[TensorAlias] = []

    for name, tensor, role in _named_state_tensors(model):
        key = id(tensor)
        if key in owner_by_id:
            target_name, target, target_role = owner_by_id[key]
            if role != target_role:
                raise UnsupportedAliasError(
                    f"'{name}' ({role}) and '{target_name}' ({target_role}) are the same "
                    "object but have different roles; stored state and deployed "
                    "parameters are not interchangeable"
                )
            assert_complete_alias(name, tensor, target_name, target)
            aliases.append(TensorAlias(alias=name, target=target_name))
        else:
            owner_by_id[key] = (name, tensor, role)

    _refuse_undeclared_sharing(owner_by_id)
    return tuple(aliases)


def _refuse_undeclared_sharing(owner_by_id: dict[int, tuple[str, torch.Tensor, str]]) -> None:
    """Two *distinct* objects sharing storage are not a tie we can represent.

    Object identity is what declares a tie. Distinct objects over one storage
    are a view relationship, so refuse them here instead of letting the writer
    quietly clone one of them and call the result an ordinary tensor.
    """
    by_pointer: dict[int, str] = {}
    for name, tensor, _role in owner_by_id.values():
        if tensor.device.type == "meta" or tensor.numel() == 0:
            continue
        pointer = tensor.data_ptr()
        if pointer in by_pointer:
            raise UnsupportedAliasError(
                f"distinct tensors '{name}' and '{by_pointer[pointer]}' share storage "
                "without sharing object identity; XLM stores complete tied tensors "
                "once and refuses arbitrary shared-storage views"
            )
        by_pointer[pointer] = name


def alias_map(aliases: tuple[TensorAlias, ...]) -> dict[str, str]:
    """Flatten aliases into the serialized ``alias -> canonical`` mapping."""
    mapping: dict[str, str] = {}
    for entry in aliases:
        if entry.alias in mapping:
            raise AliasError(f"duplicate alias entry for '{entry.alias}'")
        mapping[entry.alias] = entry.target
    return mapping


def validate_alias_map(mapping: dict[str, str], payload_names: set[str]) -> None:
    """Check a deserialized alias map before it is used to rebuild a model.

    Refuses dangling targets, aliases that also carry their own payload, and
    chains or cycles. Aliases point at a stored payload directly: one hop, no
    alias-of-an-alias, so the mapping cannot be self-referential.
    """
    for alias, target in sorted(mapping.items()):
        if not alias or not target:
            raise AliasError("alias map contains an empty name")
        if alias == target:
            raise AliasError(f"alias '{alias}' targets itself")
        if target not in payload_names:
            raise AliasError(f"alias '{alias}' targets '{target}', which carries no stored payload")
        if alias in payload_names:
            raise AliasError(
                f"alias '{alias}' also carries its own payload; a single-copy bundle "
                "must store the tied tensor exactly once"
            )
        if target in mapping:
            raise AliasError(
                f"alias '{alias}' targets '{target}', which is itself an alias; "
                "alias chains are not supported"
            )


def resolve_attribute_owner(model: nn.Module, qualified_name: str) -> tuple[nn.Module, str]:
    """Resolve a dotted state name to its owning submodule and attribute.

    Generic on purpose: no architecture-specific tensor names appear in the
    writer or the loader.
    """
    parts = qualified_name.split(".")
    owner: nn.Module = model
    for part in parts[:-1]:
        child = getattr(owner, part, None)
        if not isinstance(child, nn.Module):
            raise AliasError(f"cannot resolve '{qualified_name}': '{part}' is not a submodule")
        owner = child
    return owner, parts[-1]


def restore_alias_identity(model: nn.Module, mapping: dict[str, str]) -> None:
    """Re-point each alias at its target's live object, restoring the tie.

    After ``load_state_dict`` every name owns a distinct object again, so the
    declared aliases are re-established here. Parameters are reassigned as
    ``nn.Parameter`` attributes; persistent buffers are reassigned in place.
    """
    for alias, target in sorted(mapping.items()):
        target_owner, target_attr = resolve_attribute_owner(model, target)
        alias_owner, alias_attr = resolve_attribute_owner(model, alias)
        source = getattr(target_owner, target_attr, None)
        if source is None:
            raise AliasError(f"alias target '{target}' is absent from the built model")
        current = getattr(alias_owner, alias_attr, None)
        if current is None:
            raise AliasError(f"alias '{alias}' is absent from the built model")
        if isinstance(source, nn.Parameter) != isinstance(current, nn.Parameter):
            raise AliasError(f"alias '{alias}' and target '{target}' disagree on parameter role")
        assert_complete_alias(alias, current.detach(), target, source.detach())
        setattr(alias_owner, alias_attr, source)


def verify_alias_identity(model: nn.Module, mapping: dict[str, str]) -> None:
    """Assert the aliases really share one object after load and conversion.

    Device placement and dtype conversion can replace parameter objects, which
    would silently untie the model. This is checked rather than assumed.
    """
    for alias, target in sorted(mapping.items()):
        alias_owner, alias_attr = resolve_attribute_owner(model, alias)
        target_owner, target_attr = resolve_attribute_owner(model, target)
        if getattr(alias_owner, alias_attr) is not getattr(target_owner, target_attr):
            raise AliasError(
                f"alias '{alias}' is no longer the same object as '{target}'; "
                "the tie did not survive loading"
            )
