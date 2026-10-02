"""C04 component review basis; never a repository or row-level license declaration."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical

CONTRACT = "c04-common-pile-component-license-v1"
REPOSITORY = "common-pile/comma_v0.1_training_dataset"
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
COMPONENTS = [
    "libretexts",
    "news",
    "oercommons",
    "pressbooks",
    "project_gutenberg",
    "public_domain_review",
]
MATRIX = Path(__file__).resolve().parents[4] / (
    "docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/license-matrix.json"
)


class LicenseBasisError(ValueError):
    """The proposed component license basis does not match its evidence."""


def build_basis(allowlist: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(allowlist)
    if body.pop("digest", None) != canonical.digest(body):
        raise LicenseBasisError("component license basis: allowlist digest does not verify")
    if (
        allowlist["source_id"],
        allowlist["repository"],
        allowlist["revision"],
        allowlist["included"],
    ) != ("common_pile", REPOSITORY, REVISION, COMPONENTS):
        raise LicenseBasisError(
            "component license basis requires the exact pinned Balanced components"
        )
    matrix_sha = hashlib.sha256(MATRIX.read_bytes()).hexdigest()
    if allowlist["evidence_matrix"]["sha256"] != matrix_sha:
        raise LicenseBasisError("component license evidence matrix changed; new review required")
    return {
        "contract": CONTRACT,
        "license_basis": "component_allowlist_review",
        "declared_repository_license": None,
        "allowlist_digest": allowlist["digest"],
        "included_components": COMPONENTS,
        "component_evidence_sha256": matrix_sha,
        "allowlist": dict(allowlist),
    }


def valid_basis(value: Any, source: str, repository: str, revision: str | None) -> bool:
    if source != "common_pile" or repository != REPOSITORY or revision != REVISION:
        return False
    if not isinstance(value, dict):
        return False
    try:
        return value == build_basis(value["allowlist"])
    except (ValueError, KeyError, TypeError, OSError):
        return False
