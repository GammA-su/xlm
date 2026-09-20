"""Bounded streaming identities for preparation receipts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from xlm.artifacts.manifest import bounded_children, ensure_plain_path


def bounded_files(path: Path, *, max_entries: int = 10000) -> Iterator[Path]:
    """Enumerate with a bounded frontier, including directories in the allowance."""
    pending = [(path, 0)]
    entries = 0
    while pending:
        item, depth = pending.pop()
        ensure_plain_path(item)
        entries += 1
        if entries + len(pending) > max_entries or depth > 32:
            raise ValueError("preparation tree exceeds entry/depth limit")
        if item.is_dir():
            children = list(bounded_children(item))
            if entries + len(pending) + len(children) > max_entries:
                raise ValueError("preparation tree exceeds entry limit")
            pending.extend((child, depth + 1) for child in reversed(sorted(children)))
        elif item.is_file():
            yield item


def path_digest(path: Path, *, max_bytes: int = 2 * 1024**3, max_entries: int = 10000) -> str:
    """Hash a complete tree with bounded discovery and actual streamed-byte accounting."""
    consumed = entries = 0

    def visit(item: Path, depth: int) -> str:
        nonlocal consumed, entries
        ensure_plain_path(item)
        entries += 1
        if entries > max_entries or depth > 32:
            raise ValueError("preparation identity tree exceeds entry/depth limit")
        digest = hashlib.sha256()
        if item.is_file():
            if item.stat().st_size + consumed > max_bytes:
                raise ValueError("preparation identity exceeds input byte limit")
            with item.open("rb") as stream:
                while chunk := stream.read(min(65536, max_bytes - consumed + 1)):
                    consumed += len(chunk)
                    if consumed > max_bytes:
                        raise ValueError("preparation identity input grew beyond byte limit")
                    digest.update(chunk)
        elif item.is_dir():
            for child in sorted(bounded_children(item)):
                if child.name == "__pycache__":
                    continue
                digest.update(child.name.encode("utf-8") + b"\0")
                digest.update(visit(child, depth + 1).encode("ascii"))
        else:
            raise FileNotFoundError(f"preparation input/output not found: {item}")
        return digest.hexdigest()

    return visit(path, 0)
