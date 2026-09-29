"""Dry child artifact builders (repository evidence, not the execution root).

Fresh v3 dry child artifacts live under repository evidence
(docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/), never
in the future execution root. Every artifact carries its canonical
digest, file SHA-256 (added at publication), bytes, parent bindings,
and producer/code/environment identity. Authorization stays NONE and
executable stays false.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import envidentity, frozen_v3


class DryError(ValueError):
    """Any dry-plan construction violation: refuse."""


def code_hashes(paths: list[Path], root: Path) -> dict[str, str]:
    """Working-tree SHA-256 per file (checkout representation, not identity).

    Canonical code identity is Git blob bytes; see
    :func:`code_blob_hashes`. This helper records the checkout
    representation alongside it.
    """
    out: dict[str, str] = {}
    for path in paths:
        raw = path.read_bytes()
        out[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    return dict(sorted(out.items()))


def code_blob_hashes(paths: list[Path], root: Path) -> dict[str, str]:
    """Canonical code identity: Git blob hashes for committed files."""
    return envidentity.code_blob_hashes(root, paths)


def producer_identity(root: Path, code_paths: list[Path], *, command: str) -> dict[str, Any]:
    environment = envidentity.collect_environment_identity(root)
    return {
        "command": command,
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "epoch_id": frozen_v3.EPOCH_ID,
        "code_identity_method": "git-blob-bytes-authoritative",
        "code_blob_hashes": code_blob_hashes(code_paths, root),
        "code_hashes": code_hashes(code_paths, root),
        "environment": environment,
        "checkout_representation": envidentity.checkout_representation(root, code_paths),
        "checkout_path": root.as_posix(),
    }


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(body)
    sealed["digest"] = canonical.self_digest(body)
    return sealed


def publish(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Atomic publication; refuses to overwrite (dry plans are immutable)."""
    if path.exists():
        raise DryError(f"refusing to overwrite existing dry artifact: {path}")
    binding = canonical.write_canonical_json(path, manifest)
    return {
        "path": path.as_posix(),
        "bytes": binding["bytes"],
        "sha256": binding["sha256"],
        "canonical_digest": manifest["digest"],
    }


def base_body(
    *,
    kind: str,
    arm: str,
    producer: Mapping[str, Any],
    parents: Mapping[str, str],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "epoch_id": frozen_v3.EPOCH_ID,
        "epoch_state": frozen_v3.EPOCH_INITIAL_STATE,
        "arm": arm,
        "authorization": "NONE",
        "executable": False,
        "parents": dict(sorted(parents.items())),
        "producer": dict(producer),
        "payload": dict(payload),
    }
