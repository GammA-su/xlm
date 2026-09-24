"""Bounded control-file inventory and obsolete atomic-replacement retirement.

Every caller holds the journal FileLock, which also protects diagnostic writes.
An uncommitted replacement is never authoritative and is never promoted.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from pathlib import Path

from xlm.artifacts.manifest import bounded_children, ensure_plain_path

MAX_ORPHANS = 64
MAX_ORPHAN_BYTES = 16 * 1024**2


def regular_size(path: Path, maximum: int) -> int:
    ensure_plain_path(path)
    if not path.exists():
        return 0
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError("invalid or oversized owned control file")
    return info.st_size


def retire_replacements(target: Path, maximum: int, validate: Callable[[bytes], None]) -> None:
    """Delete only validated obsolete siblings, after validating the whole bounded set."""
    ensure_plain_path(target.parent)
    if not target.parent.exists():
        return
    prefix = target.name + "."
    pattern = re.compile(re.escape(prefix) + r"[0-9a-f]{32}\.tmp\Z")
    owned: list[Path] = []
    total = 0
    for path in bounded_children(target.parent):
        if not path.name.startswith(prefix):
            continue  # Another namespace is neither adopted nor removed.
        if not pattern.fullmatch(path.name):
            raise ValueError("unexpected atomic replacement name")
        size = regular_size(path, maximum)
        info = path.stat()
        if info.st_nlink != 1:
            raise ValueError("atomic replacement must have exactly one private link")
        total += size
        if len(owned) >= MAX_ORPHANS or total > MAX_ORPHAN_BYTES:
            raise ValueError("atomic replacement cleanup bound exceeded")
        validate(path.read_bytes())
        owned.append(path)
    for path in owned:
        # The same FileLock excludes every journal/control writer. No temp file
        # from a live conforming writer can be present while this lock is held.
        path.unlink()


def validate_diagnostic(payload: bytes, plan_id: str) -> None:
    value = json.loads(payload)
    if (
        not isinstance(value, dict)
        or value.get("plan_id") != plan_id
        or value.get("perf_version") != 1
    ):
        raise ValueError("foreign diagnostic replacement")
