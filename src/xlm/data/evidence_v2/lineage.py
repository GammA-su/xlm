"""v2.1 lineage verification and by-hash parent adoption.

Verifies the v2.1 protocol bytes, the v2.1 freeze (self-digest plus every
bound artifact), the v2.0 parent freeze, and the byte-identical selection
manifest — offline, from local bytes only. Parents are adopted by hash
with explicit roles; nothing is relabeled, rewritten, or overwritten.
The scientific hash namespace stays v2.0 for ranking, selection, review
IDs, and review order (guarded by test, not by string replacement).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class LineageError(ValueError):
    """Any freeze, parent, or identity mismatch: STOP."""


def file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_protocol_bytes(raw: bytes) -> None:
    """The normative v2.1 protocol document must match its frozen hash."""
    if file_sha256(raw) != frozen.V21_PROTOCOL_SHA256:
        raise LineageError("v2.1 protocol bytes do not match the frozen SHA-256")


def verify_freeze(freeze: Mapping[str, Any]) -> None:
    """v2.1 freeze self-digest, expected value, and artifact bindings."""
    if freeze.get("digest") != frozen.V21_FREEZE_DIGEST:
        raise LineageError("v2.1 freeze digest is not the expected canonical value")
    if canonical.self_digest(freeze) != freeze["digest"]:
        raise LineageError("v2.1 freeze self-digest recomputation mismatch")
    if freeze.get("protocol_version") != frozen.V21_PROTOCOL_VERSION:
        raise LineageError("freeze is not essential-web-evidence-v2.1")
    if freeze.get("parent_freeze_digest") != frozen.FREEZE_DIGEST:
        raise LineageError("v2.1 freeze does not bind the v2.0 parent freeze")
    if freeze.get("scientific_identity_namespace") != frozen.V21_SCIENTIFIC_NAMESPACE:
        raise LineageError("scientific namespace is not frozen v2.0")


def verify_freeze_artifact(freeze: Mapping[str, Any], relative_path: str, raw: bytes) -> None:
    """One freeze-bound artifact: bytes, hash, and descriptor digest."""
    wanted = None
    for artifact in freeze.get("artifacts", []):
        if artifact.get("relative_path") == relative_path:
            wanted = artifact
            break
    if wanted is None:
        raise LineageError(f"v2.1 freeze does not bind {relative_path}")
    if len(raw) != wanted["bytes"] or file_sha256(raw) != wanted["sha256"]:
        raise LineageError(f"v2.1 artifact drift: {relative_path}")
    descriptor = {k: v for k, v in wanted.items() if k != "descriptor_digest"}
    if canonical.digest(descriptor) != wanted["descriptor_digest"]:
        raise LineageError(f"v2.1 descriptor digest mismatch: {relative_path}")


def verify_parent(
    freeze: Mapping[str, Any],
    key: str,
    *,
    raw: bytes | None,
    digest: str | None,
    expected_role: str | None = None,
) -> dict[str, Any]:
    """Adopt one parent by hash: bytes, canonical digest, and role.

    Either the raw bytes (checked against the bound length/hash) or a
    pre-verified canonical digest must be supplied; the recorded role and
    original status are returned unchanged, never relabeled.
    """
    parents = freeze.get("parents", {})
    if key not in parents:
        raise LineageError(f"v2.1 freeze binds no parent {key!r}")
    bound = parents[key]
    if raw is not None:
        if len(raw) != bound["bytes"] or file_sha256(raw) != bound["sha256"]:
            raise LineageError(f"parent byte drift: {key}")
        body = canonical.loads_bytes_strict(raw)
        if not isinstance(body, dict) or canonical.self_digest(body) != body.get("digest"):
            raise LineageError(f"parent self-digest mismatch: {key}")
        if body["digest"] != bound["digest"]:
            raise LineageError(f"parent digest drift: {key}")
    elif digest is not None:
        if digest != bound["digest"]:
            raise LineageError(f"parent digest drift: {key}")
    else:
        raise LineageError(f"parent {key!r} needs raw bytes or a digest")
    if expected_role is not None and bound.get("role") != expected_role:
        raise LineageError(f"parent {key!r} role is not {expected_role}")
    return {
        "key": key,
        "role": bound.get("role"),
        "digest": bound["digest"],
        "sha256": bound["sha256"],
        "bytes": bound["bytes"],
        "original_protocol_version": bound.get("original_protocol_version"),
        "original_status": bound.get("original_status"),
    }


def verify_selection_identity(raw: bytes) -> dict[str, Any]:
    """The 118-locator manifest must be byte-identical: 23807 bytes."""
    if len(raw) != frozen.V21_SELECTION_BYTES:
        raise LineageError("selection manifest byte length drift")
    if file_sha256(raw) != frozen.V21_SELECTION_SHA256:
        raise LineageError("selection manifest byte drift")
    body = canonical.loads_bytes_strict(raw)
    if not isinstance(body, dict):
        raise LineageError("selection manifest is not an object")
    if canonical.self_digest(body) != body.get("digest") != frozen.V21_SELECTION_DIGEST:
        raise LineageError("selection manifest digest drift")
    if body.get("total_selected") != 118:
        raise LineageError("selection manifest is not the 118-locator freeze")
    if body.get("protocol_version") != frozen.PROTOCOL_VERSION:
        raise LineageError("selection manifest was rewritten with a new version")
    return {
        "bytes": len(raw),
        "sha256": frozen.V21_SELECTION_SHA256,
        "digest": frozen.V21_SELECTION_DIGEST,
        "total_selected": 118,
    }


def check_scientific_namespace() -> None:
    """The ranking/selection/review namespace stays v2.0, never v2.1."""
    if frozen.V21_SCIENTIFIC_NAMESPACE != "essential-web-evidence-v2.0":
        raise LineageError("scientific namespace drifted from v2.0")
    if frozen.V21_SCIENTIFIC_NAMESPACE != frozen.PROTOCOL_VERSION:
        raise LineageError("v2.0 namespace constant disagrees with v2.0 protocol")
    if frozen.V21_PROTOCOL_VERSION == frozen.V21_SCIENTIFIC_NAMESPACE:
        raise LineageError("receipt version must differ from the scientific namespace")


def parent_records(root: Path, freeze: Mapping[str, Any]) -> dict[str, Path]:
    """Local paths of repo-bound parents (G: parents are operator-owned)."""
    out: dict[str, Path] = {}
    for key, bound in freeze.get("parents", {}).items():
        path = str(bound.get("path", ""))
        if path.startswith("G:"):
            continue
        candidate = root / Path(path)
        out[key] = candidate
    return out
