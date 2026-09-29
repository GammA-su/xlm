"""Committed code identity and runtime environment identity (offline).

Representation rules (one width everywhere):

- Every code/config identity value is the lowercase **SHA-256 of the
  committed Git blob bytes** at an explicit commit, read with Git plumbing
  (``git cat-file blob <commit>:<path>``). Working-tree files are never
  hashed and called Git identity.
- The working-tree representation of each bound file is recorded
  separately as ``exact`` (byte-identical to the blob) or ``crlf`` (the
  blob with every LF expanded to CRLF and no other difference). Anything
  else is a mismatch and refuses.
- Commit identities are 40-hex Git commit IDs and are never mixed with
  file-content SHA-256 values.

The environment identity is compared field-for-field against the actual
running interpreter before any trusted authorization is produced. torch
is identified from installed metadata and ``torch/version.py`` text; it is
never imported (importing it would breach the 256 MiB process-tree cap).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class EnvIdentityError(ValueError):
    """Any code/environment identity failure: refuse."""


_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_REL = re.compile(r"\A[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*\Z")

CONFIG_PATHS: tuple[str, ...] = (".python-version", "pyproject.toml", "uv.lock")

# Exact environment identity schema (key order irrelevant; key set exact).
ENVIRONMENT_KEYS: tuple[str, ...] = (
    "python_version",
    "python_implementation",
    "python_executable_class",
    "uv_version",
    "python_version_file_sha256",
    "pyproject_sha256",
    "uv_lock_sha256",
    "pyarrow_version",
    "psutil_version",
    "torch_version",
    "torch_build",
    "os_system",
    "os_release",
    "os_version",
    "architecture",
)

# Non-package code executed by the Phase-P boundary (package modules are
# enumerated from the repository so an added module cannot go unbound).
EXTRA_CODE_PATHS: tuple[str, ...] = (
    "scripts/evidence_v3.py",
    "src/xlm/__init__.py",
    "src/xlm/data/__init__.py",
    "src/xlm/data/evidence_v2/__init__.py",
    "src/xlm/data/evidence_v2/canonical.py",
    "src/xlm/data/evidence_v2/frozen.py",
)
PACKAGE_DIR = "src/xlm/data/evidence_v3"


def is_commit(value: object) -> bool:
    return isinstance(value, str) and bool(_COMMIT.match(value))


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256.match(value))


def _check_rel(path: str) -> str:
    if not isinstance(path, str) or not _REL.match(path) or ".." in path.split("/"):
        raise EnvIdentityError(f"bound path must be a clean repository-relative path: {path!r}")
    return path


def _tool(name: str) -> str:
    if name == "uv":
        env = os.environ.get("UV")
        if env and Path(env).is_file():
            return env
    found = shutil.which(name)
    if found:
        return found
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd" / f"{name}.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / f"{name}.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / f"{name}.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise EnvIdentityError(f"required offline tool {name!r} is unavailable")


def _run(args: list[str], *, cwd: Path) -> bytes:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvIdentityError(f"{args[0]} failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        raise EnvIdentityError(f"{Path(args[0]).name} {args[1]} failed: {detail}")
    return completed.stdout


def committed_blob(repo: Path, commit: str, relpath: str) -> bytes:
    """Exact committed blob bytes for ``relpath`` at ``commit`` (Git plumbing)."""
    if not is_commit(commit):
        raise EnvIdentityError(f"commit must be a 40-hex Git commit ID: {commit!r}")
    _check_rel(relpath)
    git = _tool("git")
    kind = _run([git, "cat-file", "-t", f"{commit}:{relpath}"], cwd=repo).strip()
    if kind != b"blob":
        raise EnvIdentityError(f"{relpath} at {commit} is not a blob")
    return _run([git, "cat-file", "blob", f"{commit}:{relpath}"], cwd=repo)


def resolve_commit(repo: Path, ref: str) -> str:
    out = _run([_tool("git"), "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo)
    commit = out.decode("ascii").strip()
    if not is_commit(commit):
        raise EnvIdentityError(f"cannot resolve commit {ref!r}")
    return commit


def representation(worktree: bytes, blob: bytes) -> str:
    """Classify the checkout representation of a committed blob."""
    if worktree == blob:
        return "exact"
    if b"\r" not in blob and worktree == blob.replace(b"\n", b"\r\n"):
        return "crlf"
    raise EnvIdentityError("working-tree bytes differ from the committed blob")


def normalized_worktree_bytes(worktree: bytes) -> bytes:
    """Blob-equivalent bytes for an uncommitted file (CRLF -> LF only)."""
    if b"\r\n" in worktree:
        normalized = worktree.replace(b"\r\n", b"\n")
        if b"\r" in normalized:
            raise EnvIdentityError("mixed line endings cannot be normalized unambiguously")
        return normalized
    return worktree


def code_paths(repo: Path) -> tuple[str, ...]:
    """Every file whose committed identity the Phase-P boundary binds."""
    package = sorted(
        f"{PACKAGE_DIR}/{p.name}" for p in (repo / PACKAGE_DIR).glob("*.py") if p.is_file()
    )
    if not package:
        raise EnvIdentityError("evidence_v3 package has no modules")
    return tuple(sorted(set(package) | set(EXTRA_CODE_PATHS)))


def committed_identity(repo: Path, commit: str, paths: Iterable[str]) -> dict[str, str]:
    """SHA-256 of committed blob bytes, keyed by POSIX relative path."""
    return {
        path: hashlib.sha256(committed_blob(repo, commit, path)).hexdigest()
        for path in sorted(set(paths))
    }


def verify_worktree_against_commit(repo: Path, commit: str, paths: Iterable[str]) -> dict[str, str]:
    """Require every bound working-tree file to represent its committed blob."""
    out: dict[str, str] = {}
    for path in sorted(set(paths)):
        local = repo / Path(path)
        if not local.is_file() or local.is_symlink():
            raise EnvIdentityError(f"bound file missing from the working tree: {path}")
        try:
            out[path] = representation(local.read_bytes(), committed_blob(repo, commit, path))
        except EnvIdentityError as exc:
            raise EnvIdentityError(f"{path}: {exc} (commit {commit})") from exc
    return out


def worktree_identity(repo: Path, paths: Iterable[str]) -> dict[str, str]:
    """Blob-equivalent SHA-256 of uncommitted working-tree files (synthetic mode)."""
    out: dict[str, str] = {}
    for path in sorted(set(paths)):
        _check_rel(path)
        local = repo / Path(path)
        if not local.is_file() or local.is_symlink():
            raise EnvIdentityError(f"bound file missing from the working tree: {path}")
        out[path] = hashlib.sha256(normalized_worktree_bytes(local.read_bytes())).hexdigest()
    return out


def python_executable_class(repo: Path) -> str:
    """``project-venv`` only when the interpreter prefix is this repository's .venv."""
    prefix = Path(sys.prefix).resolve()
    if sys.prefix != sys.base_prefix and prefix == (repo / ".venv").resolve():
        return "project-venv"
    if sys.prefix != sys.base_prefix:
        return "foreign-venv"
    return "system-python"


def _torch_identity() -> tuple[str, str]:
    """Installed torch version/build without importing torch."""
    try:
        version = importlib.metadata.version("torch")
        files = importlib.metadata.files("torch") or []
    except importlib.metadata.PackageNotFoundError as exc:
        raise EnvIdentityError("torch is not installed") from exc
    version_file = next((f for f in files if str(f).replace("\\", "/") == "torch/version.py"), None)
    if version_file is None:
        raise EnvIdentityError("torch/version.py is not part of the installed distribution")
    text = Path(str(version_file.locate())).read_text(encoding="utf-8")
    match = re.search(r"^cuda\s*(?::[^=]*)?=\s*(None|'[^']*'|\"[^\"]*\")\s*$", text, re.M)
    if match is None:
        raise EnvIdentityError("cannot read the torch CUDA build from torch/version.py")
    cuda = match.group(1)
    build = "cpu" if cuda == "None" else f"cuda-{cuda.strip(chr(39) + chr(34))}"
    return version, build


def _dist_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise EnvIdentityError(f"{name} is not installed") from exc


def uv_version(repo: Path) -> str:
    return _run([_tool("uv"), "--version"], cwd=repo).decode("utf-8", "replace").strip()


def runtime_environment(repo: Path, *, config_identity: Mapping[str, str]) -> dict[str, Any]:
    """The actual running environment, in the exact authorization schema."""
    for path in CONFIG_PATHS:
        if not is_sha256(config_identity.get(path)):
            raise EnvIdentityError(f"config identity for {path} is missing")
    torch_version, torch_build = _torch_identity()
    release = platform.release()
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable_class": python_executable_class(repo),
        "uv_version": uv_version(repo),
        "python_version_file_sha256": config_identity[".python-version"],
        "pyproject_sha256": config_identity["pyproject.toml"],
        "uv_lock_sha256": config_identity["uv.lock"],
        "pyarrow_version": _dist_version("pyarrow"),
        "psutil_version": _dist_version("psutil"),
        "torch_version": torch_version,
        "torch_build": torch_build,
        "os_system": platform.system(),
        "os_release": release,
        "os_version": platform.version(),
        "architecture": platform.machine(),
    }


def check_environment_schema(env: object) -> dict[str, str]:
    if not isinstance(env, dict) or set(env) != set(ENVIRONMENT_KEYS):
        raise EnvIdentityError("environment_identity must have exactly the bound keys")
    for key in ENVIRONMENT_KEYS:
        value = env[key]
        if type(value) is not str or not value:
            raise EnvIdentityError(f"environment_identity.{key} must be a non-empty string")
    for key in ("python_version_file_sha256", "pyproject_sha256", "uv_lock_sha256"):
        if not is_sha256(env[key]):
            raise EnvIdentityError(f"environment_identity.{key} must be SHA-256 hex")
    return {k: str(env[k]) for k in ENVIRONMENT_KEYS}


def check_code_map_schema(code: object) -> dict[str, str]:
    if not isinstance(code, dict) or not code:
        raise EnvIdentityError("code_hashes must be a non-empty object")
    out: dict[str, str] = {}
    for path, value in code.items():
        _check_rel(path)
        if not is_sha256(value):
            raise EnvIdentityError(f"code hash for {path} must be 64-hex SHA-256")
        out[path] = value
    if list(out) != sorted(out):
        raise EnvIdentityError("code_hashes keys must be sorted")
    return out
