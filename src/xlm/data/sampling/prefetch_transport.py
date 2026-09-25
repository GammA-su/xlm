"""Bounded transport for the owned P34 child, not an untrusted pickle endpoint.

One reader stays alive for the entire connection lifetime. Thus even a tiny
control write cannot face an undrained child write on a Windows duplex pipe.
The bounded inbox stores wire bytes; the trainer alone decodes and owns state.
"""

from __future__ import annotations

import io
import pickle
import queue
import threading
from typing import Any

# The packer admits <= 2**20 positions: 77 MiB of compact arrays. Each of
# metadata, string table and end state is independently bounded to 8 MiB by
# canonical hashing. 128 MiB admits that domain plus pickle overhead.
MAX_FRAME_BYTES = 128 * 1024**2


def send_message(conn: Any, message: tuple[Any, ...]) -> None:
    """Bound serialization before publishing a frame (both endpoints)."""

    class LimitedBuffer(io.BytesIO):
        def write(self, data: Any) -> int:
            if self.tell() + memoryview(data).nbytes > MAX_FRAME_BYTES:
                raise OSError("prefetch frame exceeds byte limit")
            return super().write(data)

    with LimitedBuffer() as buffer:
        pickle.dump(message, buffer, protocol=5)
        conn.send_bytes(buffer.getbuffer())


def _read_frames(conn: Any, inbox: queue.Queue[Any], stopped: threading.Event) -> None:
    # No reference to the batcher/transport: their finalizers remain reachable.
    while not stopped.is_set():
        try:
            if not conn.poll(0.05):
                continue
            value: Any = conn.recv_bytes(MAX_FRAME_BYTES)
        except (EOFError, OSError, ValueError) as exc:
            value = exc
        while not stopped.is_set():
            try:
                inbox.put(value, timeout=0.05)
                break
            except queue.Full:
                continue
        if isinstance(value, BaseException):
            return


class BoundedConnection:
    """One receiver and serialized sends with deadlines; close never drains."""

    def __init__(self, conn: Any, capacity: int, timeout: float) -> None:
        self._raw = conn
        self.timeout_seconds = timeout
        self._inbox: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self._stopped = threading.Event()
        self._reader = threading.Thread(
            target=_read_frames,
            args=(conn, self._inbox, self._stopped),
            daemon=True,
            name="xlm-prefetch-reader",
        )
        self._reader.start()
        self._peek: Any = None
        self._writer: threading.Thread | None = None

    def poll(self, timeout: float = 0.0) -> bool:
        if self._peek is not None:
            return True
        if self._stopped.is_set():
            raise OSError("prefetch pipe is closed")
        try:
            self._peek = self._inbox.get(timeout=timeout)
        except queue.Empty:
            return False
        return True

    def recv(self) -> tuple[Any, ...]:
        if not self.poll(self.timeout_seconds):
            raise TimeoutError("prefetch receive exceeded deadline")
        value, self._peek = self._peek, None
        if isinstance(value, BaseException):
            raise OSError("prefetch pipe receive failed") from value
        try:
            message = pickle.loads(value)
        except (pickle.UnpicklingError, EOFError, ValueError, TypeError) as exc:
            raise OSError("malformed owned-child frame") from exc
        if not isinstance(message, tuple) or len(message) < 2:
            raise OSError("malformed owned-child message")
        return message

    def send(self, message: tuple[Any, ...]) -> None:
        if self._stopped.is_set():
            raise OSError("prefetch pipe is closed")
        finished = threading.Event()
        errors: list[BaseException] = []
        conn = self._raw

        def write() -> None:
            try:
                send_message(conn, message)
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        writer = threading.Thread(target=write, daemon=True, name="xlm-prefetch-writer")
        self._writer = writer
        writer.start()
        if not finished.wait(self.timeout_seconds):
            # The caller reaps the child; closing cancels Windows overlapped send.
            self.close()
            writer.join(timeout=1)
            raise TimeoutError("prefetch send exceeded deadline")
        writer.join(timeout=1)
        if errors:
            raise OSError("prefetch pipe send failed") from errors[0]

    def close(self) -> None:
        self._stopped.set()
        # Never close a Windows handle underneath an overlapped ReadFile: its
        # Overlapped destructor can otherwise still own an in-flight operation.
        # If a partial frame is stuck, the owner reaps the child and calls close
        # again; EOF completes that read before we release the handle.
        self._reader.join(timeout=0.1)
        if self._reader.is_alive():
            return
        try:
            self._raw.close()
        except (OSError, ValueError):
            pass
        if self._writer is not None:
            self._writer.join(timeout=1)
        self._peek = None
        while True:
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                break

    def join_reader(self) -> None:
        """After child termination, a partial-frame reader must also finish."""
        self._reader.join(timeout=1)
        self.close()
