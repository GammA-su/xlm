"""Derived CHILD artifact helpers (repository evidence, never the execution root).

Artifact identity is independent of where the builder writes: descriptors
embed the normative repository-relative path of the child tree, never the
builder's output directory or checkout location. Producer identity binds
the implementation commit and the SHA-256 of each committed Git blob, the
verified working-tree representation, and the actual runtime environment.
Authorization stays NONE and executable stays false.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import envidentity, frozen_v3, plan


class DryError(ValueError):
    """Any child-artifact construction violation: refuse."""


CODE_IDENTITY_METHOD = "sha256-of-committed-git-blob-at-implementation-commit"


def producer_identity(repo: Path, *, command: str, implementation_commit: str) -> dict[str, Any]:
    """Committed-code + runtime identity; refuses if the tree differs from the commit."""
    try:
        paths = envidentity.code_paths(repo)
        code = envidentity.committed_identity(repo, implementation_commit, paths)
        representation = envidentity.verify_worktree_against_commit(
            repo, implementation_commit, [*paths, *envidentity.CONFIG_PATHS]
        )
        config = envidentity.committed_identity(
            repo, implementation_commit, envidentity.CONFIG_PATHS
        )
        environment = envidentity.runtime_environment(repo, config_identity=config)
    except envidentity.EnvIdentityError as exc:
        raise DryError(f"producer identity unavailable: {exc}") from exc
    return {
        "command": command,
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "epoch_id": frozen_v3.EPOCH_ID,
        "implementation_commit": implementation_commit,
        "code_identity_method": CODE_IDENTITY_METHOD,
        "code_hashes": code,
        "checkout_representation": representation,
        "environment": environment,
    }


def envelope(
    *,
    kind: str,
    arm: str,
    producer: Mapping[str, Any],
    parents: Mapping[str, str],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
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
    body["digest"] = canonical.self_digest(body)
    return body


def publish(out_dir: Path, name: str, artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Write canonical bytes; refuse overwrite; describe by repository path."""
    target = out_dir / name
    if target.exists():
        raise DryError(f"refusing to overwrite existing child artifact: {name}")
    binding = canonical.write_canonical_json(target, artifact)
    return {
        "path": f"{plan.CHILD_DIR}/{name}",
        "bytes": binding["bytes"],
        "sha256": binding["sha256"],
        "canonical_digest": artifact["digest"],
    }
