"""Streaming file identities for preparation receipts (no existence-only reuse)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def path_digest(path: Path) -> str:
    """Hash a file or complete directory including names; reject symbolic links."""
    if path.is_symlink():
        raise ValueError(f"symbolic link is not a preparation input/output: {path}")
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    elif path.is_dir():
        for entry in sorted(path.iterdir()):
            if entry.name == "__pycache__":
                continue
            digest.update(entry.name.encode("utf-8") + b"\0")
            digest.update(path_digest(entry).encode("ascii"))
    else:
        raise FileNotFoundError(f"preparation input/output not found: {path}")
    return digest.hexdigest()
