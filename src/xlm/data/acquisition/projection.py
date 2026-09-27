"""Logical Arrow projection -> physical Parquet leaf chunks (window-v2).

A Parquet row group stores one column chunk per LEAF of the schema; Arrow
exposes the logical top-level fields. A flat field owns exactly one leaf,
but a struct field owns every leaf beneath it (``metadata`` ->
``metadata.category``, ``metadata.models_used``). Safety accounting must
therefore be computed over the physical leaves a logical projection pulls
in, while decoding still asks Arrow for the logical fields so nested
values are reconstructed exactly as in a full decode.

Supported (window-v2): top-level fields whose type is a scalar or a struct
of scalars/structs (arbitrary struct depth). Refused with a reason: lists,
large/fixed-size lists, maps, unions and any leaf with a repetition level
(values per row are then not rows, so row-based scan/byte accounting would
be wrong). Pure functions only; no IO.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa


class ProjectionRefusal(ValueError):
    """A logical projection cannot be mapped safely onto physical leaves."""


@dataclass(frozen=True)
class LeafSpec:
    """One physical Parquet leaf column as described by the footer schema."""

    index: int
    path: str
    max_repetition_level: int


@dataclass(frozen=True)
class FieldLeaves:
    """A top-level logical field and the physical leaf indices backing it."""

    name: str
    leaf_indices: tuple[int, ...]
    unsupported: str | None = None


@dataclass(frozen=True)
class ResolvedProjection:
    """Requested logical fields, their leaves, and each leaf's owner field."""

    logical_fields: tuple[str, ...]
    leaf_indices: tuple[int, ...]
    leaf_owner: dict[int, str]


def _leaf_count(data_type: Any) -> int:
    if pa.types.is_struct(data_type):
        return sum(_leaf_count(data_type.field(i).type) for i in range(data_type.num_fields))
    if pa.types.is_map(data_type):
        return _leaf_count(data_type.key_type) + _leaf_count(data_type.item_type)
    if (
        pa.types.is_list(data_type)
        or pa.types.is_large_list(data_type)
        or pa.types.is_fixed_size_list(data_type)
        or (hasattr(pa.types, "is_list_view") and pa.types.is_list_view(data_type))
        or (hasattr(pa.types, "is_large_list_view") and pa.types.is_large_list_view(data_type))
    ):
        return _leaf_count(data_type.value_type)
    if pa.types.is_union(data_type):
        raise ProjectionRefusal("union types have no stable Parquet leaf mapping")
    return 1


def _unsupported_reason(data_type: Any) -> str | None:
    """Why a field type is outside window-v2 support, or None if supported."""
    if pa.types.is_struct(data_type):
        for i in range(data_type.num_fields):
            child = data_type.field(i)
            reason = _unsupported_reason(child.type)
            if reason is not None:
                return f"struct child '{child.name}': {reason}"
        return None
    if pa.types.is_map(data_type):
        return "map (repeated key/value leaves)"
    if pa.types.is_union(data_type):
        return "union"
    if (
        pa.types.is_list(data_type)
        or pa.types.is_large_list(data_type)
        or pa.types.is_fixed_size_list(data_type)
    ):
        return "list (repeated leaves)"
    if hasattr(pa.types, "is_list_view") and (
        pa.types.is_list_view(data_type) or pa.types.is_large_list_view(data_type)
    ):
        return "list view (repeated leaves)"
    return None


def map_fields_to_leaves(
    fields: Sequence[tuple[str, Any]], leaves: Sequence[LeafSpec]
) -> tuple[FieldLeaves, ...]:
    """Assign every physical leaf to its top-level logical field, fail-closed.

    Parquet stores leaves in depth-first schema order, so each top-level
    field owns the next ``leaf_count(type)`` leaves. Each assignment is
    cross-checked against the leaf path (``name`` or ``name.<child...>``);
    any count or path disagreement, or a duplicate top-level name, refuses.
    """
    names = [name for name, _ in fields]
    if len(set(names)) != len(names):
        raise ProjectionRefusal(f"ambiguous schema: duplicate top-level fields {sorted(names)}")
    if [leaf.index for leaf in leaves] != list(range(len(leaves))):
        raise ProjectionRefusal("physical leaves are not in dense schema order")
    mapped: list[FieldLeaves] = []
    cursor = 0
    for name, data_type in fields:
        count = _leaf_count(data_type)
        owned = leaves[cursor : cursor + count]
        if len(owned) != count:
            raise ProjectionRefusal(
                f"schema/leaf mismatch: field '{name}' needs {count} leaves, {len(owned)} remain"
            )
        for leaf in owned:
            if leaf.path != name and not leaf.path.startswith(name + "."):
                raise ProjectionRefusal(
                    f"schema/leaf conflict: leaf '{leaf.path}' does not belong to field '{name}'"
                )
        reason = _unsupported_reason(data_type)
        if reason is None and any(leaf.max_repetition_level > 0 for leaf in owned):
            reason = "repeated leaves"
        mapped.append(FieldLeaves(name, tuple(leaf.index for leaf in owned), reason))
        cursor += count
    if cursor != len(leaves):
        raise ProjectionRefusal(
            f"schema/leaf mismatch: {len(leaves) - cursor} physical leaves have no field"
        )
    return tuple(mapped)


def resolve_projection(
    mapping: Iterable[FieldLeaves], requested: Sequence[str]
) -> ResolvedProjection:
    """Resolve requested logical fields to deduplicated, sorted leaf indices."""
    by_name = {entry.name: entry for entry in mapping}
    logical: list[str] = []
    for name in requested:
        if name not in logical:
            logical.append(name)
    unknown = sorted(name for name in logical if name not in by_name)
    if unknown:
        raise ProjectionRefusal(f"projected fields not present: {unknown}")
    refused = {name: by_name[name].unsupported for name in logical if by_name[name].unsupported}
    if refused:
        detail = "; ".join(f"{name}: {reason}" for name, reason in sorted(refused.items()))
        raise ProjectionRefusal(f"unsupported nested projection for window-v2: {detail}")
    owner: dict[int, str] = {}
    for name in logical:
        for index in by_name[name].leaf_indices:
            if index in owner:
                raise ProjectionRefusal(f"physical leaf {index} claimed by two fields")
            owner[index] = name
    return ResolvedProjection(tuple(logical), tuple(sorted(owner)), owner)


def parquet_field_leaves(parquet: Any) -> tuple[FieldLeaves, ...]:
    """Field->leaf mapping of an open ``pyarrow.parquet.ParquetFile`` (footer only)."""
    schema = parquet.schema
    leaves = [
        LeafSpec(i, str(schema.column(i).path), int(schema.column(i).max_repetition_level))
        for i in range(len(schema))
    ]
    arrow = parquet.schema_arrow
    fields = [(arrow.field(i).name, arrow.field(i).type) for i in range(len(arrow))]
    return map_fields_to_leaves(fields, leaves)
