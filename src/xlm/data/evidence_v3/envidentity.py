"""Code / environment byte identity for v3 producer bindings.
Astra found three producer files differed between checkout and Git blobs
only by line endings (``.python-version``, ``pyproject.toml``, ``uv.lock``).
Representation is therefore explicit here:

- Canonical identity uses **Git blob bytes** (``git hash-object``) for every
  committed source/config file. Blob bytes are what the commit binds; they
  are unaffected by checkout line-ending conversion.
- The checkout/runtime representation (working-tree SHA-256 plus
  ``core.autocrlf`` setting) is recorded separately under
  ``checkout_representation`` when relevant. A mismatch between blob and
  working-tree hashes is reported, never silently normalized.

Environment identity binds more than version-file/lock identity: Python
version, executable path class, uv version, the three blob hashes, library
versions, CPU/CUDA build state, OS/build, and architecture. It does not
claim complete reproducibility beyond what is actually bound.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


class EnvIdentityError(ValueError):
    """Any environment-identity collection failure: refuse or mark unknown."""


def _resolve_tool(name: str) -> str:
    """Resolve a tool offline: PATH first, then well-known install locations."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / f"{name}.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / f"{name}.exe",
        Path(os.environ.get("USERPROFILE", "")) / ".cargo" / "bin" / f"{name}.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise EnvIdentityError(f"tool {name} not found on PATH or well-known locations")


def git_blob_sha(path: Path, *, root: Path) -> str:
    """Git blob SHA-1 for a committed file (offline, no network)."""
    try:
        out = subprocess.check_output(
            [_resolve_tool("git"), "hash-object", str(path)],
            cwd=str(root),
            stderr=subprocess.STDOUT,
            timeout=30,
        )
    except (subprocess.CalledProcessError, EnvIdentityError, subprocess.TimeoutExpired) as exc:
        raise EnvIdentityError(f"git hash-object unavailable for {path}: {exc}") from exc
    return out.decode("ascii").strip()


def working_tree_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_blob_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    """Blob identities keyed by POSIX relative path, sorted."""
    out: dict[str, str] = {}
    for path in paths:
        out[path.relative_to(root).as_posix()] = git_blob_sha(path, root=root)
    return dict(sorted(out.items()))


def python_executable_class() -> str:
    exe = Path(sys.executable).as_posix().lower()
    if ".venv" in exe or "venv" in exe:
        return "project-venv"
    if "conda" in exe:
        return "conda-env"
    return "system-python"


def uv_version() -> str:
    try:
        out = subprocess.check_output(
            [_resolve_tool("uv"), "--version"], stderr=subprocess.STDOUT, timeout=30
        )
    except (subprocess.CalledProcessError, EnvIdentityError, subprocess.TimeoutExpired) as exc:
        raise EnvIdentityError(f"uv --version unavailable: {exc}") from exc
    return out.decode("utf-8", "replace").strip()


def collect_environment_identity(root: Path) -> dict[str, Any]:
    """Collect the full v3 environment identity mapping."""
    try:
        import psutil
        import pyarrow
        import torch
    except ImportError as exc:
        raise EnvIdentityError(f"environment package unavailable: {exc}") from exc
    lock_blob = git_blob_sha(root / "uv.lock", root=root)
    pyproject_blob = git_blob_sha(root / "pyproject.toml", root=root)
    version_blob = git_blob_sha(root / ".python-version", root=root)
    cuda_build = "cuda" if torch.cuda.is_available() else "cpu"
    return {
        "python_version": platform.python_version(),
        "python_executable_class": python_executable_class(),
        "uv_version": uv_version(),
        "uv_lock_blob_sha": lock_blob,
        "pyproject_blob_sha": pyproject_blob,
        "python_version_blob_sha": version_blob,
        "pyarrow_version": pyarrow.__version__,
        "psutil_version": psutil.__version__,
        "torch_version": torch.__version__,
        "cpu_cuda_build": f"{torch.__version__}+{cuda_build}",
        "os_build": platform.platform(),
        "architecture": platform.machine(),
    }


def checkout_representation(root: Path, paths: list[Path]) -> dict[str, Any]:
    """Working-tree representation recorded alongside blob identity."""
    try:
        out = subprocess.check_output(
            [_resolve_tool("git"), "config", "core.autocrlf"],
            cwd=str(root),
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        autocrlf = out.decode("ascii").strip() or "unset"
    except (subprocess.CalledProcessError, EnvIdentityError, subprocess.TimeoutExpired):
        autocrlf = "unknown"
    files: dict[str, str] = {}
    for path in paths:
        files[path.relative_to(root).as_posix()] = working_tree_sha256(path)
    return {"core_autocrlf": autocrlf, "working_tree_sha256": dict(sorted(files.items()))}
