"""Phase-C output layout: the input -> output path mapping and the cleaned-corpus tree.

Mapping (deterministic, collision-free, OS-independent). Every manifest path is a
relative POSIX path below the input data root. Its output path is the SAME relative
path below ``--output-root``, built from validated components and never by string
replacement of a drive letter or root prefix. A component refuses when it is empty,
``.`` or ``..``, holds a backslash, a colon, a Windows-forbidden character or a control
character, ends in a dot or space, is a reserved Windows device name, or ends with the
owned temporary suffix. Two inputs refuse when their outputs are equal after Unicode
NFC normalization and case folding (Windows and macOS compare names that way), or when
one output would be a directory of another. The mapping is therefore one input -> one
output on Windows and Linux alike, and every output stays below the output root.

:class:`CorpusTree` owns the output root. On a fresh run it must be absent or empty.
On a resume it may hold only the expected final files, their owned temporaries
(``<name>.xlmclean-tmp``) and the directories leading to them. Any other entry refuses
and is never deleted. Every path component is checked with ``lstat`` and must not be a
symbolic link, junction or other reparse point; a created directory must resolve
exactly where it was planned. No operational record (binding, unit, receipt or log)
lives in this tree.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.outputs import OutputError, fsync_directory, is_alias, overlaps
from xlm.data.quality.scan import AuditFile, InputManifest, QualityError

TEMP_SUFFIX = ".xlmclean-tmp"
MAX_RELATIVE_CHARS = 1024
FORBIDDEN = frozenset('<>:"\\|?*')
RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)
MAPPING_RULE = (
    "output relative path = the validated input relative POSIX path (same components); "
    "unique after NFC + casefold; no file is a directory of another"
)


class PathMappingError(QualityError):
    """Content-free refusal of the input -> output path mapping."""


def output_relpath(input_path: str) -> str:
    """The output path of one manifest path (refuses anything not portable)."""
    if type(input_path) is not str or not 0 < len(input_path) <= MAX_RELATIVE_CHARS:
        raise PathMappingError("input path is empty or too long")
    if input_path.startswith("/"):
        raise PathMappingError("input path is absolute")
    parts = input_path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathMappingError("input path has an empty, '.' or '..' component")
        if any(ch in FORBIDDEN or ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise PathMappingError("input path component holds a non-portable character")
        if part != part.rstrip(" ."):
            raise PathMappingError("input path component ends with a dot or space")
        if part.split(".")[0].upper() in RESERVED:
            raise PathMappingError("input path component is a reserved device name")
    if parts[-1].endswith(TEMP_SUFFIX):
        raise PathMappingError("input file name ends with the owned temporary suffix")
    return "/".join(parts)


def collision_key(relative: str) -> str:
    return unicodedata.normalize("NFC", relative).casefold()


@dataclass(frozen=True)
class MappedFile:
    ordinal: int
    input_path: str
    output_path: str
    component: str

    def record(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "component": self.component,
        }


def build_mapping(files: Sequence[AuditFile]) -> tuple[MappedFile, ...]:
    """One output per input; refuses collisions and file/directory conflicts."""
    entries: list[MappedFile] = []
    keys: set[str] = set()
    directories: set[str] = set()
    for item in files:
        output = output_relpath(item.path)
        key = collision_key(output)
        if key in keys:
            raise PathMappingError("two inputs map to one output path")
        keys.add(key)
        parts = key.split("/")
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))
        entries.append(MappedFile(item.ordinal, item.path, output, item.component))
    if keys & directories:
        raise PathMappingError("an output file path is also a directory of another output")
    return tuple(entries)


def mapping_digest(entries: Sequence[MappedFile]) -> str:
    return canonical.digest([e.record() for e in entries])


def check_roots(
    output_root: Path,
    state_output: Path,
    manifest: InputManifest,
    inputs: Iterable[Path],
) -> None:
    """Refuse every overlap between the new corpus, the state tree and the inputs.

    The cleaned corpus root may not equal, contain or lie inside the input data root,
    any corpus file directory or any input (manifest, policy, approved dry run). The
    state tree may live below the input data root (as the Phase-A/B outputs do) but may
    not contain it, overlap a corpus file directory, an input or the output root.
    """
    out = Path(output_root).resolve()
    state = Path(state_output).resolve()
    root = manifest.data_root.resolve()
    if overlaps(out, root):
        raise QualityError("output root overlaps the input data root")
    if overlaps(out, state):
        raise QualityError("state output overlaps the output root")
    if root.is_relative_to(state):
        raise QualityError("state output contains the input data root")
    for parent in sorted({(root / f.path).parent for f in manifest.files}):
        if overlaps(out, parent) or overlaps(state, parent):
            raise QualityError("output root or state output overlaps a corpus file directory")
    for item in inputs:
        if overlaps(out, Path(item)) or overlaps(state, Path(item)):
            raise QualityError("output root or state output overlaps an input")


class CorpusTree:
    """The cleaned-corpus output root: expected files only, alias-free, never deleted."""

    def __init__(self, root: Path, entries: Sequence[MappedFile], protected: Iterable[Path]):
        self.root = Path(os.path.abspath(root))
        self.expected = {e.output_path: e for e in entries}
        self.temps = {path + TEMP_SUFFIX: path for path in self.expected}
        self.directories = {
            "/".join(path.split("/")[:n])
            for path in self.expected
            for n in range(1, len(path.split("/")))
        }
        self.protected = tuple(Path(p) for p in protected)
        self.real: Path | None = None

    # -- validation ------------------------------------------------------------------

    def _check_protected(self, real: Path) -> None:
        for guarded in self.protected:
            if overlaps(real, guarded):
                raise OutputError("output root overlaps a protected input")

    def scan(self) -> tuple[set[str], set[str]]:
        """Existing final files and owned temporaries; anything else refuses."""
        finals: set[str] = set()
        temps: set[str] = set()
        pending = [""]
        while pending:
            relative = pending.pop()
            directory = self.root / relative if relative else self.root
            with os.scandir(directory) as listing:
                for entry in listing:
                    name = f"{relative}/{entry.name}" if relative else entry.name
                    path = Path(entry.path)
                    if is_alias(path):
                        raise OutputError("output tree holds a link/junction/reparse point")
                    if entry.is_dir(follow_symlinks=False):
                        if name not in self.directories:
                            raise OutputError("output root holds data the cleaner does not own")
                        pending.append(name)
                    elif entry.is_file(follow_symlinks=False):
                        if name in self.expected:
                            finals.add(name)
                        elif name in self.temps:
                            temps.add(name)
                        else:
                            raise OutputError("output root holds data the cleaner does not own")
                    else:
                        raise OutputError("output root holds a non-regular entry")
        return finals, temps

    def check_fresh(self) -> None:
        """Read-only: a fresh run needs an absent or empty, alias-free root."""
        if is_alias(self.root):
            raise OutputError("output root is a link/junction/reparse point")
        self._check_protected(self.root.resolve())
        if self.root.exists():
            if not self.root.is_dir():
                raise OutputError("output root exists and is not a directory")
            with os.scandir(self.root) as listing:
                if any(True for _ in listing):
                    raise OutputError("output root is not empty (unrelated pre-existing data)")
        elif not self.root.parent.is_dir():
            raise OutputError("output root parent directory does not exist")

    def open(self, *, create: bool, fresh: bool) -> tuple[set[str], set[str]]:
        """Validate (optionally create) the root; return existing finals and temps.

        ``fresh`` (no state binding yet): the root must be absent or empty.
        """
        if is_alias(self.root):
            raise OutputError("output root is a link/junction/reparse point")
        self._check_protected(self.root.resolve())
        if self.root.exists():
            if not self.root.is_dir():
                raise OutputError("output root exists and is not a directory")
            with os.scandir(self.root) as listing:
                occupied = any(True for _ in listing)
            if fresh and occupied:
                raise OutputError("output root is not empty (unrelated pre-existing data)")
        elif create:
            if not self.root.parent.is_dir():
                raise OutputError("output root parent directory does not exist")
            self.root.mkdir()
        else:
            raise OutputError("output root does not exist")
        self.real = self.root.resolve()
        self._check_protected(self.real)
        return self.scan()

    def _components(self, relative: str) -> list[str]:
        if relative not in self.expected and relative not in self.temps:
            raise OutputError("output target outside the mapped layout")
        return relative.split("/")

    def ensure_parent(self, relative: str) -> Path:
        """Create the parent directories of ``relative`` one validated level at a time."""
        if self.real is None:
            raise OutputError("output tree not opened")
        parts = self._components(relative)
        current = self.root
        for n, part in enumerate(parts[:-1]):
            current = current / part
            if is_alias(current):
                raise OutputError("output path component is a link/junction/reparse point")
            if not current.exists():
                current.mkdir()
            elif not current.is_dir():
                raise OutputError("output path component is not a directory")
            if current.resolve() != self.real.joinpath(*parts[: n + 1]):
                raise OutputError("output directory resolves outside its expected location")
        return current

    def path(self, relative: str) -> Path:
        """A validated file path (final or temporary), never an alias or a directory."""
        if self.real is None:
            raise OutputError("output tree not opened")
        parts = self._components(relative)
        current = self.root
        for part in parts[:-1]:
            current = current / part
            if is_alias(current):
                raise OutputError("output path component is a link/junction/reparse point")
        target = self.root.joinpath(*parts)
        if is_alias(target):
            raise OutputError("output file is a link/junction/reparse point")
        if target.exists() and not target.is_file():
            raise OutputError("output file is not a regular file")
        parent = target.parent
        if parent.exists() and parent.resolve() != self.real.joinpath(*parts[:-1]):
            raise OutputError("output file resolves outside its expected location")
        return target

    def final(self, output_path: str) -> Path:
        return self.path(output_path)

    def temp(self, output_path: str) -> Path:
        return self.path(output_path + TEMP_SUFFIX)

    def remove_temps(self, temps: Iterable[str]) -> int:
        """Delete owned temporaries only (exact names, regular files)."""
        removed = 0
        for name in sorted(temps):
            if name not in self.temps:
                raise OutputError("not an owned temporary output")
            path = self.path(name)
            if path.exists():
                path.unlink()
                fsync_directory(path.parent)
                removed += 1
        return removed

    def publish(self, output_path: str) -> Path:
        """Atomically rename the verified temporary into its final name (never overwrite)."""
        temp, final = self.temp(output_path), self.final(output_path)
        if final.exists() or not temp.is_file():
            raise OutputError("cleaned output cannot be published from its temporary")
        os.replace(temp, final)
        fsync_directory(final.parent)
        return final
