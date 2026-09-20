"""Storage capacity management, headroom guards, and atomic file writers complying with C04."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from pathlib import Path

from xlm.data.sources.transport import BudgetExhaustedError


class DiskCeilingExceededError(RuntimeError):
    """Raised when temporary scratch, output disk, or host disk headroom ceiling is breached."""


class StorageCapacityManager:
    """Thread-safe coordinator for network reservations, disk capacities, and headroom."""

    def __init__(
        self,
        max_transferred_bytes: int,
        max_decompressed_bytes: int,
        max_temp_disk_bytes: int,
        max_output_disk_bytes: int,
        min_free_headroom_bytes: int = 50 * 1024 * 1024,  # 50 MiB minimum physical disk headroom
    ) -> None:
        self.max_transferred_bytes = max_transferred_bytes
        self.max_decompressed_bytes = max_decompressed_bytes
        self.max_temp_disk_bytes = max_temp_disk_bytes
        self.max_output_disk_bytes = max_output_disk_bytes
        self.min_free_headroom_bytes = min_free_headroom_bytes

        self.transferred_bytes = 0
        self.decompressed_bytes = 0
        self.temp_disk_bytes = 0
        self.output_disk_bytes = 0
        self.cache_hits = 0

        self._lock = threading.Lock()

    def reserve_transfer(self, estimated_bytes: int) -> None:
        """Reserve network byte allowance before initiating a chunk or request."""
        with self._lock:
            if self.transferred_bytes + estimated_bytes > self.max_transferred_bytes:
                raise BudgetExhaustedError(
                    f"Transferred bytes limit breached: current {self.transferred_bytes:,} + "
                    f"reserved {estimated_bytes:,} > max {self.max_transferred_bytes:,} bytes."
                )

    def record_transfer(self, actual_bytes: int) -> None:
        """Record actual transferred bytes measured at the transport socket."""
        with self._lock:
            self.transferred_bytes += actual_bytes
            if self.transferred_bytes > self.max_transferred_bytes:
                raise BudgetExhaustedError(
                    f"Transferred bytes limit exceeded: {self.transferred_bytes:,} > "
                    f"max {self.max_transferred_bytes:,} bytes."
                )

    def record_decompressed(self, bytes_count: int) -> None:
        """Record uncompressed bytes and enforce ceiling."""
        with self._lock:
            self.decompressed_bytes += bytes_count
            if self.decompressed_bytes > self.max_decompressed_bytes:
                raise BudgetExhaustedError(
                    f"Decompressed bytes limit exceeded: {self.decompressed_bytes:,} > "
                    f"max {self.max_decompressed_bytes:,} bytes."
                )

    def reserve_disk_space(
        self, target_dir: Path, estimated_bytes: int, is_temp: bool = True
    ) -> None:
        """Verify host physical headroom and configured logical ceiling before writing."""
        with self._lock:
            # Check host filesystem physical free space
            try:
                usage = shutil.disk_usage(target_dir)
                if usage.free - estimated_bytes < self.min_free_headroom_bytes:
                    raise DiskCeilingExceededError(
                        f"Physical host disk exhaustion risk: free space ({usage.free:,}) "
                        f"would fall below required headroom ({self.min_free_headroom_bytes:,})."
                    )
            except OSError:
                pass  # Fall through if disk_usage is unsupported on mock/virtual mount

            # Check configured logical capacity
            if is_temp:
                if self.temp_disk_bytes + estimated_bytes > self.max_temp_disk_bytes:
                    raise DiskCeilingExceededError(
                        f"Temporary disk ceiling exceeded: current {self.temp_disk_bytes:,} + "
                        f"new {estimated_bytes:,} > max {self.max_temp_disk_bytes:,} bytes."
                    )
                self.temp_disk_bytes += estimated_bytes
            else:
                if self.output_disk_bytes + estimated_bytes > self.max_output_disk_bytes:
                    raise DiskCeilingExceededError(
                        f"Output disk ceiling exceeded: current {self.output_disk_bytes:,} + "
                        f"new {estimated_bytes:,} > max {self.max_output_disk_bytes:,} bytes."
                    )
                self.output_disk_bytes += estimated_bytes

    def release_temp_disk(self, byte_count: int) -> None:
        """Release temporary disk space after atomic rename or cleanup."""
        with self._lock:
            self.temp_disk_bytes = max(0, self.temp_disk_bytes - byte_count)

    def record_cache_hit(self) -> None:
        """Increment observable cache hit counter."""
        with self._lock:
            self.cache_hits += 1

    def snapshot(self) -> dict[str, int]:
        """Return point-in-time snapshot of resource metrics."""
        with self._lock:
            return {
                "transferred_bytes": self.transferred_bytes,
                "decompressed_bytes": self.decompressed_bytes,
                "temp_disk_bytes": self.temp_disk_bytes,
                "output_disk_bytes": self.output_disk_bytes,
                "cache_hits": self.cache_hits,
            }


class AtomicFileWriter:
    """Manages partial file streaming with fsync, durable prefix hashing, and atomic rename."""

    @staticmethod
    def hash_durable_prefix(path: Path, length: int) -> str:
        """Compute SHA-256 over exactly `length` bytes of a local file in bounded 64 KiB chunks."""
        hasher = hashlib.sha256()
        bytes_read = 0
        with path.open("rb") as f:
            while bytes_read < length:
                to_read = min(65536, length - bytes_read)
                chunk = f.read(to_read)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)
        if bytes_read != length:
            raise ValueError(
                f"Cannot hash prefix of '{path}': requested {length} bytes but got {bytes_read}."
            )
        return hasher.hexdigest()

    @staticmethod
    def truncate_to_length(path: Path, length: int) -> None:
        """Truncate local file to verified prefix length, discarding unverified trailing data."""
        with path.open("r+b") as f:
            f.truncate(length)
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def atomic_complete(partial_path: Path, final_path: Path) -> Path:
        """Ensure file is synced to storage media and atomically renamed to final destination."""
        final_path.parent.mkdir(parents=True, exist_ok=True)
        # Flush and fsync partial file before rename
        with partial_path.open("r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(partial_path, final_path)
        return final_path
