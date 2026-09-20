"""Immutable code snapshots for queued runs (C12, A28).

A queued run executes captured code, not a mutable working tree. The snapshot
enumerates version-controlled files plus allowlisted untracked plugin/config
files, refuses secrets fail-closed, copies the tree into a bounded snapshot
directory, and hashes every file. The queue runner re-verifies the live tree
against the manifest before launch; a changed tree blocks the run instead of
silently altering what executes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SNAPSHOT_VERSION = "1"

# Only these tree-relative paths enter a snapshot. Everything else (data,
# environments, caches, the snapshot store itself) is out of scope by
# construction, so unrelated user files can never enter a queued run.
SNAPSHOT_INCLUDE = (
    "src/",
    "recipes/",
    "manifests/",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
)

# Untracked files are captured only under these allowlisted prefixes: plugin
# code and experiment/campaign/mixture recipes. Anything else untracked is
# ignored, never silently absorbed.
UNTRACKED_ALLOWLIST = (
    "src/xlm/plugins/",
    "recipes/experiments/",
    "recipes/campaigns/",
    "recipes/mixtures/",
)

# Basename patterns that must never enter a snapshot. A match fails the whole
# snapshot loudly; there is no partial capture that drops the secret quietly.
SECRET_PATTERNS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*api*token*",
    "*auth*token*",
    "*access*token*",
    "*secret*",
    "*credential*",
    "*password*",
    ".env",
    ".env.*",
)

ALWAYS_EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "data",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "snapshots",
    ".staging",
}


class SnapshotError(RuntimeError):
    """Raised when a snapshot cannot be captured or verified safely."""


@dataclass(frozen=True)
class CodeSnapshot:
    """Manifest of one immutable code capture."""

    snapshot_version: str
    code_hash: str
    git_head: str | None
    tracked_modified: list[str]
    included_files: dict[str, str]
    total_bytes: int
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeSnapshot:
        return cls(
            snapshot_version=str(data["snapshot_version"]),
            code_hash=str(data["code_hash"]),
            git_head=data.get("git_head"),
            tracked_modified=list(data.get("tracked_modified", [])),
            included_files=dict(data.get("included_files", {})),
            total_bytes=int(data.get("total_bytes", 0)),
            created_at=str(data.get("created_at", "")),
        )


def _is_included(relative: str) -> bool:
    for prefix in SNAPSHOT_INCLUDE:
        if prefix.endswith("/"):
            if relative == prefix[:-1] or relative.startswith(prefix):
                return True
        elif relative == prefix:
            return True
    return False


def _is_secret(relative: str) -> str | None:
    name = relative.rsplit("/", 1)[-1].lower()
    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatchcase(name, pattern.lower()):
            return pattern
    return None


def _git_head(tree_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tree_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_status(tree_root: Path) -> tuple[set[str], set[str], set[str]]:
    """Return (tracked files, modified tracked files, untracked files), best effort."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(tree_root),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set(), set(), set()
    if completed.returncode != 0:
        return set(), set(), set()
    tracked: set[str] = set()
    modified: set[str] = set()
    untracked: set[str] = set()
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip().strip('"')
        if status.strip() == "??":
            untracked.add(path)
        else:
            tracked.add(path)
            if status.strip():
                modified.add(path)
    return tracked, modified, untracked


def _iter_candidate_files(tree_root: Path) -> list[str]:
    """All files under the include prefixes, minus always-excluded directories."""
    found: list[str] = []
    for prefix in SNAPSHOT_INCLUDE:
        base = tree_root / prefix.rstrip("/")
        if base.is_file():
            found.append(prefix)
            continue
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(tree_root).as_posix()
            if any(part in ALWAYS_EXCLUDE_DIRS for part in path.relative_to(tree_root).parts):
                continue
            found.append(relative)
    return found


def capture_snapshot(
    tree_root: Path | str,
    snapshot_dir: Path | str,
    max_snapshot_bytes: int = 256 * 1024 * 1024,
) -> CodeSnapshot:
    """Capture an immutable, hashed copy of the executable tree subset."""
    root = Path(tree_root)
    out = Path(snapshot_dir)
    if not root.is_dir():
        raise SnapshotError(f"snapshot tree root is not a directory: {root}")

    tracked, modified, untracked = _git_status(root)
    candidates = _iter_candidate_files(root)

    # The capture set is the include prefixes, whatever git thinks of them, so
    # a queued run always executes the full tree subset it was planned against.
    # Untracked files *outside* the includes are pulled in only under the
    # allowlist (plugin code and recipes); everything else untracked is ignored.
    selected = list(candidates)
    for path in sorted(untracked):
        if path in selected:
            continue
        if any(path == rule.rstrip("/") or path.startswith(rule) for rule in UNTRACKED_ALLOWLIST):
            if (root / path).is_file():
                selected.append(path)

    secret_hits: list[str] = []
    for relative in selected:
        pattern = _is_secret(relative)
        if pattern is not None:
            secret_hits.append(f"{relative} (matches {pattern})")
    if secret_hits:
        raise SnapshotError(
            "snapshot refused: secret-like files would enter the capture: "
            + "; ".join(sorted(secret_hits))
        )

    file_hashes: dict[str, str] = {}
    total_bytes = 0
    code_dir = out / "code"
    if code_dir.exists():
        shutil.rmtree(code_dir)
    code_dir.mkdir(parents=True, exist_ok=True)

    for relative in sorted(selected):
        source = root / relative
        if not source.is_file():
            continue
        data = source.read_bytes()
        total_bytes += len(data)
        if total_bytes > max_snapshot_bytes:
            raise SnapshotError(
                f"snapshot refused: capture exceeds {max_snapshot_bytes:,} bytes at '{relative}'."
            )
        file_hashes[relative] = hashlib.sha256(data).hexdigest()
        destination = code_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    ordered = {path: file_hashes[path] for path in sorted(file_hashes)}
    code_hash = hashlib.sha256(
        json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    snapshot = CodeSnapshot(
        snapshot_version=SNAPSHOT_VERSION,
        code_hash=code_hash,
        git_head=_git_head(root),
        tracked_modified=sorted(p for p in modified if p in ordered),
        included_files=ordered,
        total_bytes=total_bytes,
        created_at=datetime.now(UTC).isoformat(),
    )
    (out / "manifest.json").write_text(
        json.dumps(snapshot.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return snapshot


def verify_snapshot(tree_root: Path | str, snapshot: CodeSnapshot) -> list[str]:
    """Re-hash the live tree and return the list of diverged/missing files.

    An empty list means the tree still matches the capture. Any divergence must
    block a queued run; the caller decides the state, this function only reports.
    """
    root = Path(tree_root)
    diverged: list[str] = []
    for relative, expected in snapshot.included_files.items():
        source = root / relative
        if not source.is_file():
            diverged.append(f"{relative}: missing from working tree")
            continue
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != expected:
            diverged.append(f"{relative}: content changed after capture")
    return diverged


def load_snapshot(snapshot_dir: Path | str) -> CodeSnapshot:
    """Load a snapshot manifest from its directory."""
    manifest = Path(snapshot_dir) / "manifest.json"
    if not manifest.is_file():
        raise SnapshotError(f"no snapshot manifest at {manifest}")
    return CodeSnapshot.from_dict(json.loads(manifest.read_text(encoding="utf-8")))
