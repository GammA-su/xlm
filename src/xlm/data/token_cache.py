"""Bounded read-only maps for immutable C07 shards; no exported buffer lifetime."""

from __future__ import annotations

import copy
import hashlib
import json
import mmap
import os
import struct
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from xlm.core.contracts import TokenShardManifest
from xlm.data.tokens import TokenShardReader


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass
class _Mapping:
    handle: BinaryIO
    mapping: mmap.mmap
    paths: tuple[Path, ...]
    identities: tuple[tuple[int, int, int, int, int], ...]
    manifest: TokenShardManifest

    def close(self) -> None:
        self.mapping.close()
        self.handle.close()


def _close_all(entries: OrderedDict[Path, _Mapping]) -> None:
    while entries:
        _, entry = entries.popitem()
        entry.close()


class TokenMapCache:
    """An instance-owned LRU, verified on admission and checked for mutation per read.

    Immutable artifacts must not be edited while in use. Identity/size/timestamp
    changes fail closed, including replacement of the manifest or offsets. This
    is accidental-change detection, not a defense against a malicious writer
    restoring filesystem metadata. Close releases Windows handles immediately.
    """

    def __init__(self, max_open_shards: int = 8) -> None:
        if max_open_shards < 1:
            raise ValueError("max_open_shards must be positive")
        self.max_open_shards = max_open_shards
        self._entries: OrderedDict[Path, _Mapping] = OrderedDict()
        self._finalizer = weakref.finalize(self, _close_all, self._entries)
        self._closed = False

    def close(self) -> None:
        self._closed = True
        self._finalizer()

    def __enter__(self) -> TokenMapCache:
        if self._closed:
            raise ValueError("token map cache is closed")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def read(self, reader: TokenShardReader, start: int, count: int) -> list[int]:
        if self._closed:
            raise ValueError("token map cache is closed")
        total = reader.manifest.num_tokens
        if not 0 <= start <= total or count < 0:
            raise ValueError("invalid token slice")
        size = min(count, total - start)
        if size == 0:
            return []
        key = reader.directory
        manifest = reader.manifest
        entry = self._entries.get(key)
        if entry is not None:
            try:
                identities_now = tuple(map(_identity, entry.paths))
            except OSError as exc:
                self._entries.pop(key).close()
                raise ValueError("immutable token artifact disappeared while mapped") from exc
            if entry.manifest != manifest or identities_now != entry.identities:
                self._entries.pop(key).close()
                raise ValueError("immutable token artifact changed while mapped")
            self._entries.move_to_end(key)
        else:
            if len(self._entries) >= self.max_open_shards:
                _, oldest = self._entries.popitem(last=False)
                oldest.close()
            paths = tuple(
                key / name for name in ("tokens.bin", "offsets.jsonl", "shard_manifest.json")
            )
            identities = tuple(map(_identity, paths))
            if json.loads(paths[2].read_text(encoding="utf-8")) != reader.manifest.to_dict():
                raise ValueError("token artifact manifest changed since reader creation")
            reader.verify_integrity()
            handle = paths[0].open("rb")
            mapped = None
            try:
                # Verify the descriptor actually mapped, not just a prior path lookup.
                if (
                    hashlib.file_digest(handle, "sha256").hexdigest()
                    != reader.manifest.checksum_sha256
                ):
                    raise ValueError("token artifact changed during admission")
                if os.fstat(handle.fileno()).st_size != total * reader.token_bytes_size:
                    raise ValueError("token artifact length changed during admission")
                mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
                if tuple(map(_identity, paths)) != identities:
                    raise ValueError("token artifact changed during admission")
                entry = _Mapping(handle, mapped, paths, identities, copy.copy(manifest))
                self._entries[key] = entry
            except BaseException:
                if mapped is not None:
                    mapped.close()
                handle.close()
                raise
        code = "H" if reader.manifest.token_dtype == "uint16" else "I"
        return list(
            struct.unpack_from(f"<{size}{code}", entry.mapping, start * reader.token_bytes_size)
        )
