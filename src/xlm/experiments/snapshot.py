"""Immutable code snapshots for queued runs (C12, A28).

A queued run executes captured code, not a mutable working tree. The snapshot
enumerates version-controlled files plus allowlisted untracked plugin/config
files, refuses secrets fail-closed, copies the tree into a bounded snapshot
directory, and hashes every file. The queue runner verifies and executes that
capture independently of subsequent edits to the live checkout.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from xlm.artifacts.manifest import canonical_json, ensure_plain_path, validate_file_set
from xlm.artifacts.store import compute_file_sha256

SNAPSHOT_VERSION = "2"

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
            ["git", "status", "--porcelain", "-uall", "--", *SNAPSHOT_INCLUDE],
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
        pending = [base]
        entries = 0
        while pending:
            directory = pending.pop()
            ensure_plain_path(directory)
            with os.scandir(directory) as children:
                for child in children:
                    entries += 1
                    if entries > 20_000:
                        raise SnapshotError("snapshot exceeds entry limit")
                    if child.name in ALWAYS_EXCLUDE_DIRS:
                        continue
                    path = Path(child.path)
                    ensure_plain_path(path)
                    if child.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif child.is_file(follow_symlinks=False):
                        found.append(path.relative_to(tree_root).as_posix())
                        if len(found) > 10_000:
                            raise SnapshotError("snapshot exceeds 10000-file capture limit")
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

    candidates = _iter_candidate_files(root)
    tracked, modified, untracked = _git_status(root)

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
    validate_file_set(["python-version.pin" if p == ".python-version" else p for p in selected])
    ensure_plain_path(out)
    for relative in selected:
        ensure_plain_path(root / relative)
        total_bytes += (root / relative).stat().st_size
    if total_bytes > max_snapshot_bytes:
        raise SnapshotError("snapshot refused: capture exceeds byte limit")
    out.parent.mkdir(parents=True, exist_ok=True)
    private = out.parent / f".capture-{uuid.uuid4().hex}"
    lock_path = out.parent / f".{out.name}.lock"
    ensure_plain_path(lock_path)
    with FileLock(str(lock_path), timeout=10):
        private.mkdir()
        try:
            code_dir = private / "code"
            used = 0
            for relative in sorted(selected):
                source = root / relative
                destination = code_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                hasher = hashlib.sha256()
                with source.open("rb") as reader, destination.open("xb") as writer:
                    while chunk := reader.read(65536):
                        used += len(chunk)
                        if used > max_snapshot_bytes:
                            raise SnapshotError("snapshot exceeds byte limit while copying")
                        writer.write(chunk)
                        hasher.update(chunk)
                file_hashes[relative] = hasher.hexdigest()
            snapshot = CodeSnapshot(
                snapshot_version=SNAPSHOT_VERSION,
                code_hash=hashlib.sha256(canonical_json(file_hashes)).hexdigest(),
                git_head=_git_head(root),
                tracked_modified=sorted(p for p in modified if p in file_hashes),
                included_files=file_hashes,
                total_bytes=used,
                created_at=datetime.now(UTC).isoformat(),
            )
            (private / "manifest.json").write_bytes(canonical_json(snapshot.to_dict()))
            if out.exists():
                existing = load_snapshot(out)
                errors = verify_snapshot(out / "code", existing, exact=True)
                if errors or existing.code_hash != snapshot.code_hash:
                    raise SnapshotError("immutable snapshot conflict; original is preserved")
                return existing
            private.rename(out)
            return snapshot
        finally:
            if private.exists():
                ensure_plain_path(private)
                shutil.rmtree(private)


def verify_snapshot(
    tree_root: Path | str, snapshot: CodeSnapshot, *, exact: bool = False
) -> list[str]:
    """Re-hash the selected tree and return diverged/missing files.

    An empty list means the tree still matches the capture. Any divergence must
    block a queued run; the caller decides the state, this function only reports.
    """
    root = Path(tree_root)
    diverged: list[str] = []
    ensure_plain_path(root)
    validate_file_set(
        ["python-version.pin" if p == ".python-version" else p for p in snapshot.included_files]
    )
    if snapshot.snapshot_version not in ("1", SNAPSHOT_VERSION):
        return ["unsupported snapshot version"]
    if hashlib.sha256(canonical_json(snapshot.included_files)).hexdigest() != snapshot.code_hash:
        return ["snapshot manifest identity mismatch"]
    total = 0
    for relative, expected in snapshot.included_files.items():
        source = root / relative
        ensure_plain_path(source)
        if not source.is_file():
            diverged.append(f"{relative}: missing from selected tree")
            continue
        total += source.stat().st_size
        if total > 256 * 1024**2:
            return ["snapshot verification exceeds byte limit"]
        actual = compute_file_sha256(source, max_bytes=256 * 1024**2)
        if actual != expected:
            diverged.append(f"{relative}: content changed after capture")
    if exact:
        if total != snapshot.total_bytes:
            diverged.append("snapshot total byte count mismatch")
        count = 0
        for path in root.rglob("*"):
            count += 1
            if count > 20_000:
                return ["snapshot tree exceeds entry limit"]
            ensure_plain_path(path)
            if path.is_file() and path.relative_to(root).as_posix() not in snapshot.included_files:
                diverged.append(f"{path.name}: unlisted snapshot file")
    return diverged


def load_snapshot(snapshot_dir: Path | str) -> CodeSnapshot:
    """Load a snapshot manifest from its directory."""
    manifest = Path(snapshot_dir) / "manifest.json"
    if not manifest.is_file():
        raise SnapshotError(f"no snapshot manifest at {manifest}")
    ensure_plain_path(manifest)
    if manifest.stat().st_size > 8 * 1024**2:
        raise SnapshotError("snapshot manifest exceeds byte limit")
    return CodeSnapshot.from_dict(json.loads(manifest.read_text(encoding="utf-8")))
