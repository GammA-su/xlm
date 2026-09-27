"""Provenance-bound receipts and aggregate integrity (protocol section 12).

Every manifest binds protocol/freeze/policy digests, source revision,
code hashes, Python version, ``uv.lock``, command, exit status, resource
caps, and parent artifact digests. Publication is atomic; a failed run
leaves no apparently-complete artifact, and missing artifacts fail the
aggregate check (a skip is never a pass).
"""

from __future__ import annotations

import hashlib
import platform
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class ReceiptError(ValueError):
    """Any provenance gap, hash drift, or incomplete aggregate: refuse."""


def code_hashes(paths: Sequence[Path], root: Path) -> dict[str, str]:
    """SHA-256 per source file, keyed by POSIX relative path."""
    out: dict[str, str] = {}
    for path in paths:
        raw = path.read_bytes()
        out[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    return dict(sorted(out.items()))


def environment_binding(root: Path) -> dict[str, str]:
    """Python version, .python-version content, and uv.lock hash."""
    requested = (root / ".python-version").read_text(encoding="utf-8").strip()
    lock = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    return {
        "python_version": platform.python_version(),
        "python_version_file": requested,
        "uv_lock_sha256": lock,
    }


def provenance(
    *,
    root: Path,
    code_paths: Sequence[Path],
    command: str,
    exit_status: int,
    caps: Mapping[str, Any],
    parents: Mapping[str, str],
    stage: str,
) -> dict[str, Any]:
    """Provenance block shared by every evidence-v2 manifest."""
    return {
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "repository": frozen.REPOSITORY,
        "revision": frozen.REVISION,
        "code_hashes": code_hashes(code_paths, root),
        "environment": environment_binding(root),
        "command": command,
        "exit_status": exit_status,
        "caps": dict(caps),
        "parents": dict(sorted(parents.items())),
        "stage": stage,
    }


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the canonical self-digest to a manifest body."""
    sealed = dict(body)
    sealed["digest"] = canonical.self_digest(body)
    return sealed


def publish_manifest(path: Path, manifest: Mapping[str, Any]) -> dict[str, int | str]:
    """Atomic canonical publication; refuses to overwrite an existing file."""
    if path.exists():
        raise ReceiptError(f"refusing to overwrite existing manifest: {path}")
    return canonical.write_canonical_json(path, manifest)


def check_aggregate(
    artifacts: Mapping[str, Mapping[str, Any]],
    expected: Sequence[str],
    *,
    what: str,
) -> None:
    """Every expected artifact present with bytes+hash; missing fails."""
    missing = [name for name in expected if name not in artifacts]
    if missing:
        raise ReceiptError(f"{what} is missing artifacts: {missing}")
    for name in expected:
        entry = artifacts[name]
        if "bytes" not in entry or "sha256" not in entry:
            raise ReceiptError(f"{what}/{name} lacks byte binding")
