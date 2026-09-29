"""Windows-safe execution-root containment (the only physical write layer).

Every byte the Phase-P boundary writes under the execution root goes
through :class:`RootFS`. Accounting (reservations, caps, durable
inventory) is layered on top by the epoch store; this module guarantees
that a write cannot land outside the verified root:

- Relative paths use a strict grammar: lowercase ``[a-z0-9][a-z0-9._-]*``
  segments separated by ``/``; no ``..``, ``.``, drive letters, colons
  (alternate data streams / other volumes), backslashes, Windows device
  names, or trailing dots/spaces.
- The root directory is identified by (volume serial, file ID). Every
  operation re-verifies that identity, that no ancestor of the root and
  no existing component under it is a reparse point (junction, symlink,
  mount point — detected from ``st_file_attributes``/``st_reparse_tag``,
  not ``Path.is_symlink``), and that resolved parents stay on the root's
  volume inside the root. Checks run before and after each mutation.
- Renames use ``MoveFileExW(... MOVEFILE_WRITE_THROUGH)`` on Windows so a
  committed rename is durable; exclusive creation uses ``O_EXCL``.

Residual limit (documented): a concurrent local adversary with write
access to the root could race a component swap between check and use; the
post-mutation re-check detects an escape but cannot undo external bytes.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


class ContainmentError(RuntimeError):
    """Any path, reparse, volume or root-identity violation: STOP."""


_SEGMENT = re.compile(r"\A[a-z0-9][a-z0-9._\-]{0,63}\Z")
_DEVICE = re.compile(r"\A(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?\Z")
_MAX_DEPTH = 6

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def check_rel(rel: str) -> tuple[str, ...]:
    """Validate a root-relative path against the strict grammar."""
    if type(rel) is not str or not rel or len(rel) > 240:
        raise ContainmentError(f"relative path must be a bounded non-empty string: {rel!r}")
    if rel.startswith("/") or "\\" in rel or ":" in rel:
        raise ContainmentError(f"absolute, backslash or volume path refused: {rel!r}")
    parts = tuple(rel.split("/"))
    if len(parts) > _MAX_DEPTH:
        raise ContainmentError(f"path too deep: {rel!r}")
    for part in parts:
        if part in ("", ".", ".."):
            raise ContainmentError(f"empty/dot segment refused: {rel!r}")
        if not _SEGMENT.match(part) or part.endswith(".") or _DEVICE.match(part):
            raise ContainmentError(f"illegal path segment {part!r} in {rel!r}")
    return parts


def is_reparse(st: os.stat_result) -> bool:
    """True for junctions, symlinks, mount points and any other reparse point."""
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & _REPARSE:
        return True
    return bool(getattr(st, "st_reparse_tag", 0))


def check_ancestors(path: Path) -> None:
    """No existing ancestor of ``path`` (inclusive) may be a reparse point."""
    current = Path(os.path.abspath(path))
    chain = [current, *current.parents]
    for component in chain:
        try:
            st = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ContainmentError(f"cannot inspect ancestor {component}: {exc}") from exc
        if is_reparse(st):
            raise ContainmentError(f"reparse point/junction in the root ancestry: {component}")
        if component != current and not stat.S_ISDIR(st.st_mode):
            raise ContainmentError(f"ancestor is not a directory: {component}")


@dataclass(frozen=True)
class RootIdentity:
    """Physical identity of the root directory."""

    path: str  # absolute, as bound (forward slashes)
    volume_serial: int
    file_id: int

    def as_record(self) -> dict[str, object]:
        return {"path": self.path, "volume_serial": self.volume_serial, "file_id": self.file_id}


def observe_root(root: Path) -> RootIdentity:
    check_ancestors(root)
    st = os.lstat(root)
    if not stat.S_ISDIR(st.st_mode) or is_reparse(st):
        raise ContainmentError(f"execution root is not a plain directory: {root}")
    return RootIdentity(
        path=root_string(root), volume_serial=int(st.st_dev), file_id=int(st.st_ino)
    )


def root_string(root: Path) -> str:
    return Path(os.path.abspath(root)).as_posix()


def _move_durable(src: Path, dst: Path, *, replace: bool) -> None:
    if sys.platform == "win32":
        flags = 0x8  # MOVEFILE_WRITE_THROUGH
        if replace:
            flags |= 0x1  # MOVEFILE_REPLACE_EXISTING
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
        move = kernel32.MoveFileExW
        move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move.restype = ctypes.c_int
        if not move(str(src), str(dst), flags):
            err = ctypes.get_last_error()  # type: ignore[attr-defined,unused-ignore]
            raise ContainmentError(f"MoveFileExW {src.name} -> {dst.name} failed ({err})")
        return
    if replace:
        os.replace(src, dst)
    else:
        os.link(src, dst)
        os.unlink(src)
    fd = os.open(dst.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class RootFS:
    """Containment-checked physical operations under one verified root."""

    def __init__(self, identity: RootIdentity) -> None:
        self._identity = identity
        self._root = Path(identity.path)

    @property
    def identity(self) -> RootIdentity:
        return self._identity

    def verify_root(self) -> None:
        observed = observe_root(self._root)
        if (observed.volume_serial, observed.file_id) != (
            self._identity.volume_serial,
            self._identity.file_id,
        ):
            raise ContainmentError("execution root identity changed (different directory/volume)")

    def _resolve(self, rel: str, *, parent_must_exist: bool = True) -> Path:
        parts = check_rel(rel)
        self.verify_root()
        current = self._root
        for part in parts[:-1]:
            current = current / part
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                if parent_must_exist:
                    raise ContainmentError(f"parent directory missing for {rel!r}") from None
                continue
            if is_reparse(st) or not stat.S_ISDIR(st.st_mode):
                raise ContainmentError(f"component {current.name!r} of {rel!r} is not a plain dir")
            if st.st_dev != self._identity.volume_serial:
                raise ContainmentError(f"component of {rel!r} is on a different volume")
        target = self._root.joinpath(*parts)
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            pass
        else:
            if is_reparse(st):
                raise ContainmentError(f"target {rel!r} is a reparse point")
        if parent_must_exist:
            real_parent = os.path.normcase(os.path.realpath(target.parent))
            want = os.path.normcase(os.path.realpath(self._root.joinpath(*parts[:-1])))
            root_real = os.path.normcase(os.path.realpath(self._root))
            if real_parent != want or not (
                real_parent == root_real or real_parent.startswith(root_real + os.sep)
            ):
                raise ContainmentError(f"resolved parent of {rel!r} escapes the root")
        return target

    def mkdirs(self, rel: str) -> None:
        parts = check_rel(rel)
        for depth in range(1, len(parts) + 1):
            sub = "/".join(parts[:depth])
            path = self._resolve(sub)
            try:
                os.mkdir(path)
            except FileExistsError:
                pass
            st = os.lstat(path)
            if is_reparse(st) or not stat.S_ISDIR(st.st_mode):
                raise ContainmentError(f"{sub!r} is not a plain directory after mkdir")

    def exists(self, rel: str) -> bool:
        return os.path.lexists(self._resolve(rel, parent_must_exist=False))

    def read_bytes(self, rel: str, *, max_bytes: int) -> bytes:
        path = self._resolve(rel)
        with open(path, "rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ContainmentError(f"{rel!r} exceeds the {max_bytes}-byte read bound")
        return data

    def size(self, rel: str) -> int:
        st = os.lstat(self._resolve(rel))
        if not stat.S_ISREG(st.st_mode) or is_reparse(st):
            raise ContainmentError(f"{rel!r} is not a regular file")
        return int(st.st_size)

    def create_exclusive(self, rel: str, data: bytes) -> tuple[int, str]:
        """Create a new file (O_EXCL), write, fsync. Never overwrites."""
        path = self._resolve(rel)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ContainmentError(f"{rel!r} already exists") from exc
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._resolve(rel)
        if self.size(rel) != len(data):
            raise ContainmentError(f"{rel!r} size differs after write")
        return len(data), hashlib.sha256(data).hexdigest()

    def rename(self, src_rel: str, dst_rel: str, *, replace: bool) -> None:
        src = self._resolve(src_rel)
        dst = self._resolve(dst_rel)
        _move_durable(src, dst, replace=replace)
        self._resolve(dst_rel)

    def remove(self, rel: str) -> None:
        """Delete one regular file and verify it is gone."""
        path = self._resolve(rel)
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or is_reparse(st):
            raise ContainmentError(f"refusing to remove non-regular {rel!r}")
        os.unlink(path)
        if os.path.lexists(path):
            raise ContainmentError(f"{rel!r} still exists after removal")

    def open_append(self, rel: str) -> int:
        path = self._resolve(rel)
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
        return os.open(path, flags)

    def scan(self) -> dict[str, int]:
        """Physical inventory: every regular file under the root, by rel path.

        Any reparse point, non-regular entry or name outside the grammar is
        an unknown physical object and refuses.
        """
        self.verify_root()
        out: dict[str, int] = {}
        pending: list[tuple[Path, str]] = [(self._root, "")]
        while pending:
            directory, prefix = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    rel = f"{prefix}{entry.name}"
                    st = entry.stat(follow_symlinks=False)
                    if is_reparse(st):
                        raise ContainmentError(f"reparse point inside the root: {rel!r}")
                    try:
                        check_rel(rel)
                    except ContainmentError as exc:
                        raise ContainmentError(f"unknown physical object {rel!r}") from exc
                    if stat.S_ISDIR(st.st_mode):
                        pending.append((Path(entry.path), rel + "/"))
                    elif stat.S_ISREG(st.st_mode):
                        out[rel] = int(st.st_size)
                    else:
                        raise ContainmentError(f"non-regular object inside the root: {rel!r}")
        return dict(sorted(out.items()))
