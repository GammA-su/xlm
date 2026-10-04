"""Job-owned output tree: every write target is validated, nothing unknown is deleted.

The audit owns exactly these names under ``--output``: the binding (ownership
marker), the receipt, the fixed artifact names, their ``.tmp`` atomic-write staging
siblings, ``units/`` and inside it ``fNNNNN.unit.zz`` (+ ``.tmp``). Any other entry
refuses (it is never deleted). Before any write, every path component from the
output root down to the target is checked with ``lstat`` and must not be a symbolic
link, junction or any other reparse point; the resolved parent must equal the
expected location and must not overlap a protected input. Cleanup removes only
regular files whose names are exact job-owned staging names.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable
from pathlib import Path

from xlm.data.quality.scan import QualityError

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
UNIT_RE = re.compile(r"f\d{5}\.unit\.zz")
UNIT_STAGING_RE = re.compile(r"f\d{5}\.unit\.zz\.tmp")


class OutputError(QualityError):
    """Content-free refusal concerning the output tree."""


def is_alias(path: Path) -> bool:
    """True for a symbolic link, a Windows junction or any reparse point."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a.is_relative_to(b) or b.is_relative_to(a)


class OutputTree:
    """Validated writes and owned-only cleanup below one output root."""

    def __init__(
        self,
        root: Path,
        *,
        marker: str,
        owned: Iterable[str],
        protected: Iterable[Path],
        charge: Callable[[int], None],
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self.marker = marker
        self.owned = frozenset(owned) | {marker}
        self.staging = frozenset(name + ".tmp" for name in self.owned)
        self.protected = tuple(Path(p) for p in protected)
        self.charge = charge
        self.real: Path | None = None

    # -- validation ------------------------------------------------------------------

    def open(self, *, create: bool) -> None:
        """Validate (and optionally create) the root and ``units/``; refuse foreign content."""
        if is_alias(self.root):
            raise OutputError("output directory is a link/junction/reparse point")
        # Resolved BEFORE anything is created, so no directory is ever made through
        # an alias into a protected location.
        planned = self.root.resolve()
        for guarded in self.protected:
            target = guarded.resolve()
            if planned.is_relative_to(target) or target.is_relative_to(planned):
                raise OutputError("output directory overlaps a protected audit input")
        if self.root.exists():
            if not self.root.is_dir():
                raise OutputError("output path exists and is not a directory")
            entries = {e.name for e in os.scandir(self.root)}
            if entries and self.marker not in entries:
                raise OutputError(
                    "output directory is not empty and is not owned by a quality audit"
                )
            unknown = entries - self.owned - self.staging - {"units"}
            if unknown:
                raise OutputError("output directory holds files the audit does not own")
        elif create:
            self.root.mkdir(parents=True)
        else:
            raise OutputError("output directory does not exist")
        self.real = self.root.resolve()
        for guarded in self.protected:
            target = guarded.resolve()
            if self.real.is_relative_to(target) or target.is_relative_to(self.real):
                raise OutputError("output directory overlaps a protected audit input")
        units = self.root / "units"
        if is_alias(units):
            raise OutputError("output units directory is a link/junction/reparse point")
        if units.exists():
            if not units.is_dir():
                raise OutputError("output units entry is not a directory")
            for entry in os.scandir(units):
                if not (UNIT_RE.fullmatch(entry.name) or UNIT_STAGING_RE.fullmatch(entry.name)):
                    raise OutputError("output units directory holds files the audit does not own")
                if is_alias(Path(entry.path)) or not entry.is_file(follow_symlinks=False):
                    raise OutputError("output unit is not a regular file")
        elif create:
            units.mkdir()
        self._check_dir(units if units.exists() else self.root)

    def _check_dir(self, directory: Path) -> None:
        if self.real is None:
            raise OutputError("output tree not opened")
        current = self.root
        relative = directory.relative_to(self.root) if directory != self.root else Path()
        for part in ("", *relative.parts):
            current = current / part if part else current
            if is_alias(current):
                raise OutputError("output path component is a link/junction/reparse point")
        # The root was proven disjoint from every protected input in ``open``; a
        # descendant that resolves exactly inside the root therefore cannot overlap one.
        if directory.resolve() != self.real / relative:
            raise OutputError("output path resolves outside its expected location")

    def target(self, relative: str) -> Path:
        """A validated write target (and its atomic-write staging sibling)."""
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) > 2:
            raise OutputError("output target outside the owned layout")
        name = rel.name
        if len(rel.parts) == 2:
            if rel.parts[0] != "units" or not UNIT_RE.fullmatch(name):
                raise OutputError("output target outside the owned layout")
        elif name not in self.owned:
            raise OutputError("output target outside the owned layout")
        path = self.root / rel
        self._check_dir(path.parent)
        for candidate in (path, path.with_name(name + ".tmp")):
            if is_alias(candidate):
                raise OutputError("output file is a link/junction/reparse point")
            if candidate.exists() and not candidate.is_file():
                raise OutputError("output file is not a regular file")
        return path

    def write(self, relative: str, payload: bytes) -> Path:
        """Precharged, validated atomic write (``canonical.write_atomic``)."""
        from xlm.data.evidence_v2 import canonical

        path = self.target(relative)
        self.charge(len(payload))
        canonical.write_atomic(path, payload)
        self._check_dir(path.parent)
        return path

    def remove_owned_staging(self) -> int:
        """Delete only regular files with exact job-owned staging names."""
        removed = 0
        for directory, pattern in ((self.root, None), (self.root / "units", UNIT_STAGING_RE)):
            if not directory.exists():
                continue
            self._check_dir(directory)
            for entry in os.scandir(directory):
                owned = (
                    pattern.fullmatch(entry.name)
                    if pattern is not None
                    else entry.name in self.staging
                )
                if not owned:
                    continue
                path = Path(entry.path)
                if is_alias(path) or not entry.is_file(follow_symlinks=False):
                    raise OutputError("owned staging name is not a regular file")
                path.unlink()
                removed += 1
        return removed

    def used_bytes(self) -> int:
        total = 0
        for directory in (self.root, self.root / "units"):
            if directory.exists():
                for entry in os.scandir(directory):
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
        return total
