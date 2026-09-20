"""Disk-backed partitioned index for exact hashes and LSH band keys.

Contract C05 forbids loading every hash and document into RAM. Keys are routed to a
fixed number of on-disk partitions by a stable hash of the key, appended as they
arrive, and read back one partition at a time. Peak memory is therefore set by the
largest single partition rather than by the corpus.

Partition assignment uses BLAKE2b rather than the built-in ``hash``, so a key lands
in the same partition in every process and on every platform.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType


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
        self._handles: dict[int, object] = {}
        self._closed = False
        self.entries_written = 0

    def _path(self, index: int) -> Path:
        return self.directory / f"part_{index:04d}.jsonl"

    def add(self, key: str, doc_id: str) -> None:
        """Record that ``doc_id`` carries ``key``."""
        if self._closed:
            raise RuntimeError("cannot add to a closed PartitionedKeyIndex")
        index = partition_for(key, self.partition_count)
        handle = self._handles.get(index)
        if handle is None:
            handle = self._path(index).open("a", encoding="utf-8")
            self._handles[index] = handle
        line = json.dumps([key, doc_id], ensure_ascii=False)
        handle.write(line + "\n")  # type: ignore[attr-defined]
        self.entries_written += 1

    def flush(self) -> None:
        """Flush all open partition handles without closing the index."""
        for handle in self._handles.values():
            handle.flush()  # type: ignore[attr-defined]

    def close(self) -> None:
        """Close all partition handles."""
        for handle in self._handles.values():
            handle.close()  # type: ignore[attr-defined]
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
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    key, doc_id = json.loads(stripped)
                    bucket.setdefault(key, set()).add(doc_id)
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
