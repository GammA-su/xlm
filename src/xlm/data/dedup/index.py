"""Disk-backed partitioned index for exact hashes and LSH band keys.

Contract C05 forbids loading every hash and document into RAM. Keys are routed to a
fixed number of on-disk partitions by a stable hash of the key, appended as they
arrive, and read back one partition at a time. Peak memory is therefore set by the
largest single partition rather than by the corpus.

Partition assignment uses BLAKE2b rather than the built-in ``hash``, so a key lands
in the same partition in every process and on every platform.

Records use a versioned binary framing (magic + u16 lengths) rather than JSON:
same groupings come out, at a fraction of the encode/parse cost, and foreign or
truncated files fail closed on the magic instead of parsing as data.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

INDEX_MAGIC = b"PKI1\n"
_RECORD_HEADER = struct.Struct("<HH")
_PARTITION_SUFFIX = ".bin"
# Large user-space buffer: one durable transaction per flush, never per record.
_INDEX_BUFFER_BYTES = 1024 * 1024


def partition_for(key: str, partition_count: int) -> int:
    """Return the partition index for ``key``, stably across processes."""
    if partition_count < 1:
        raise ValueError(f"partition_count must be positive, got {partition_count}")
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % partition_count


class PartitionedKeyIndex:
    """Append-only, disk-backed multimap from string key to document IDs.

    The index is written once and then read partition by partition. It is not a
    random-access store: ``groups()`` is the only read path, deliberately, because a
    per-key lookup API would invite callers to build the RAM-resident dictionary this
    class exists to avoid.
    """

    def __init__(self, directory: Path, partition_count: int = 16) -> None:
        if partition_count < 1:
            raise ValueError(f"partition_count must be positive, got {partition_count}")
        self.directory = directory
        self.partition_count = partition_count
        self.directory.mkdir(parents=True, exist_ok=True)
        self._handles: dict[int, BinaryIO] = {}
        self._closed = False
        self.entries_written = 0

    def _path(self, index: int) -> Path:
        return self.directory / f"part_{index:04d}{_PARTITION_SUFFIX}"

    def _open_partition(self, index: int) -> BinaryIO:
        path = self._path(index)
        fresh = not path.is_file() or path.stat().st_size == 0
        handle = path.open("ab", buffering=_INDEX_BUFFER_BYTES)
        if fresh:
            handle.write(INDEX_MAGIC)
        return handle

    def add(self, key: str, doc_id: str) -> None:
        """Record that ``doc_id`` carries ``key``."""
        if self._closed:
            raise RuntimeError("cannot add to a closed PartitionedKeyIndex")
        index = partition_for(key, self.partition_count)
        handle = self._handles.get(index)
        if handle is None:
            handle = self._open_partition(index)
            self._handles[index] = handle
        key_bytes = key.encode("utf-8")
        id_bytes = doc_id.encode("utf-8")
        if len(key_bytes) > 0xFFFF or len(id_bytes) > 0xFFFF:
            raise ValueError("index key or document ID exceeds 64 KiB framing bound")
        handle.write(_RECORD_HEADER.pack(len(key_bytes), len(id_bytes)))
        handle.write(key_bytes)
        handle.write(id_bytes)
        self.entries_written += 1

    def flush(self) -> None:
        """Flush all open partition handles without closing the index."""
        for handle in self._handles.values():
            handle.flush()

    def close(self) -> None:
        """Close all partition handles."""
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._closed = True

    def __enter__(self) -> PartitionedKeyIndex:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def groups(self, min_size: int = 2) -> Iterator[tuple[str, list[str]]]:
        """Yield ``(key, sorted doc_ids)`` for keys carried by ``min_size`` or more docs.

        Only one partition is resident at a time. Document IDs are de-duplicated and
        sorted so that downstream clustering does not depend on insertion order.
        """
        self.flush()
        for index in range(self.partition_count):
            path = self._path(index)
            if not path.is_file():
                continue
            bucket: dict[str, set[str]] = {}
            with path.open("rb", buffering=_INDEX_BUFFER_BYTES) as handle:
                magic = handle.read(len(INDEX_MAGIC))
                if magic != INDEX_MAGIC:
                    raise ValueError(f"index partition {path} has a bad magic; refusing to read")
                while True:
                    header = handle.read(_RECORD_HEADER.size)
                    if not header:
                        break
                    if len(header) != _RECORD_HEADER.size:
                        raise ValueError(f"index partition {path} is truncated; refusing to read")
                    key_len, id_len = _RECORD_HEADER.unpack(header)
                    key_bytes = handle.read(key_len)
                    id_bytes = handle.read(id_len)
                    if len(key_bytes) != key_len or len(id_bytes) != id_len:
                        raise ValueError(f"index partition {path} is truncated; refusing to read")
                    bucket.setdefault(key_bytes.decode("utf-8"), set()).add(
                        id_bytes.decode("utf-8")
                    )
            for key in sorted(bucket):
                doc_ids = bucket[key]
                if len(doc_ids) >= min_size:
                    yield key, sorted(doc_ids)
            bucket.clear()

    def partition_sizes(self) -> list[int]:
        """Return the byte size of each partition file, for bounded-memory evidence."""
        self.flush()
        return [
            self._path(i).stat().st_size if self._path(i).is_file() else 0
            for i in range(self.partition_count)
        ]
