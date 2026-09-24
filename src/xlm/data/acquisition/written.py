"""Ephemeral evidence from an XLM writer; never deserialize this from a journal.

The caller captures this after its last successful flush/fsync, using the SHA
computed while writing the actual bytes. Publication requires the writer closed.
The plan execution lock excludes cooperating writers throughout that lifetime.
This is not a defence against an administrator changing bytes behind that lock.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import BinaryIO, Protocol


class PayloadDigest(Protocol):
    def hexdigest(self) -> str: ...


@dataclass(frozen=True)
class WrittenPayload:
    stream: BinaryIO
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str

    @classmethod
    def capture(cls, stream: BinaryIO, size: int, digest: PayloadDigest) -> WrittenPayload:
        """Capture the open, fully flushed writer, not a subsequent path lookup."""
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_ino <= 0 or info.st_size != size:
            raise ValueError("written payload size/identity mismatch")
        return cls(stream, info.st_dev, info.st_ino, size, info.st_mtime_ns, digest.hexdigest())
