"""Binding an order manifest to a training input, and resume identity (P35 M5).

A configuration selects an order with a data-only reference

``data.document_order: {manifest: <absolute path>, order_manifest_id: <sha256>,
canonical_membership_id: <sha256>}``

Both identities are pins: the manifest must verify to exactly that id, must
order exactly that membership, and the bound shards must recompute to that
membership. ``latest``, placeholders, wildcards and relative paths are refused.

The committed stream state of an ordered run carries ``document_order``
(order id, membership id, policy). Ordinary resume requires the identical value,
and :func:`check_order_resume` is evaluated before any model, optimizer or data
state is restored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.ordering.manifest import (
    ORDER_POLICY,
    OrderManifestError,
    check_against_membership,
    load_order_manifest,
)
from xlm.data.ordering.membership import MembershipError, build_membership
from xlm.data.tokens import TokenShardReader

REFERENCE_KEYS = frozenset({"manifest", "order_manifest_id", "canonical_membership_id"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UNPINNED = {"latest", "null", "none", "todo", "tbd", ""}


def _pinned(value: Any, where: str) -> str:
    if not isinstance(value, str) or value.strip().casefold() in _UNPINNED:
        raise OrderManifestError(f"{where} must be pinned, got {value!r}", code="order_unpinned")
    if not _HEX64.match(value):
        raise OrderManifestError(
            f"{where} must be a lowercase 64-hex identity, got {value!r}", code="order_unpinned"
        )
    return value


def validate_order_reference(reference: Any) -> dict[str, str]:
    """Shape and pins of a ``data.document_order`` reference (no file access)."""
    if not isinstance(reference, Mapping) or set(reference) != REFERENCE_KEYS:
        raise OrderManifestError(
            f"data.document_order must be exactly {sorted(REFERENCE_KEYS)}", code="order_unpinned"
        )
    path = reference["manifest"]
    if not isinstance(path, str) or path.strip().casefold() in _UNPINNED:
        raise OrderManifestError("document_order.manifest must be a path", code="order_unpinned")
    if any(ch in path for ch in "*?[]") or not Path(path).is_absolute():
        raise OrderManifestError(
            f"document_order.manifest must be an absolute local path, got {path!r}",
            code="order_unpinned",
        )
    return {
        "manifest": path,
        "order_manifest_id": _pinned(reference["order_manifest_id"], "order_manifest_id"),
        "canonical_membership_id": _pinned(
            reference["canonical_membership_id"], "canonical_membership_id"
        ),
    }


def resolve_document_order(
    reference: Any, readers: Mapping[str, TokenShardReader]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Verify a pinned order against the bound shards; returns (reference, manifest)."""
    pinned = validate_order_reference(reference)
    manifest = load_order_manifest(
        Path(pinned["manifest"]), expected_id=pinned["order_manifest_id"]
    )
    if manifest["canonical_membership_id"] != pinned["canonical_membership_id"]:
        raise OrderManifestError(
            "pinned canonical_membership_id differs from the order manifest's membership",
            code="order_membership_pin_mismatch",
        )
    if set(manifest["sources"]) != set(readers):
        raise OrderManifestError(
            "order manifest sources differ from the bound mixture sources",
            code="order_membership_mismatch",
        )
    try:
        membership = build_membership(readers)
    except MembershipError as exc:
        raise OrderManifestError(str(exc), code=exc.code) from exc
    check_against_membership(manifest, membership)
    pinned["manifest"] = str(Path(pinned["manifest"]).resolve())
    return pinned, manifest


def order_state(manifest: Mapping[str, Any] | None) -> dict[str, str] | None:
    """The order identity a committed stream state carries (``None``: shard native)."""
    if manifest is None:
        return None
    return {
        "order_manifest_id": str(manifest["order_manifest_id"]),
        "canonical_membership_id": str(manifest["canonical_membership_id"]),
        "within_source_order_policy": ORDER_POLICY,
    }


def state_order(state: Any) -> Any:
    """The ``document_order`` a serialized stream state carries (``None`` if absent)."""
    return state.get("document_order") if isinstance(state, Mapping) else None


def _describe(value: Any) -> str:
    if value is None:
        return "shard-native order (pre-M5)"
    return f"order {value.get('order_manifest_id')}" if isinstance(value, Mapping) else repr(value)


def check_order_resume(saved_state: Any, current_state: Any) -> None:
    """Refuse restoring a data state written under another document order.

    Ordinary resume and checkpoint forks both keep the data lineage (cursors are
    positions in the ordered stream), so both require the identical order. A
    different order is a new experiment with a fresh stream and a new identity.
    """
    saved, current = state_order(saved_state), state_order(current_state)
    if saved != current:
        raise OrderManifestError(
            f"refusing to restore: the checkpoint's data state was committed under "
            f"{_describe(saved)}, this run streams {_describe(current)}; a different document "
            "order is a new experiment, not a resume",
            code="order_resume_mismatch",
        )
