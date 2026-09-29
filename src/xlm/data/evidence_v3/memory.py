"""Process-tree memory supervision that runs DURING physical work.

- :class:`OwnedProcessRegistry` owns lifecycle records. ``register`` returns
  an opaque registration handle; the only way to drop a PID is ``reap``,
  which succeeds only when the registry ITSELF observes that the exact
  process (PID + creation time) no longer exists. There is no caller exit
  proof and no boolean release.
- :class:`TreeSampler` measures the main process, its recursive
  descendants and every registered PID (descendant or not) together with
  that PID's own recursive descendants, deduplicated by PID. Any access
  failure, or a registered PID that vanished without being reaped, is a
  measurement failure (fail closed).
- :class:`Supervisor` is a daemon monitor thread owned by the executor. It
  samples every ``interval_s`` while work is active and latches an abort
  reason on RSS > cap, on measurement failure, or when the current step
  deadline passes. Transport, body and parser loops call ``checkpoint()``
  which raises once an abort is latched.

Sampling limit (stated, not hidden): periodic RSS sampling cannot prove
that an infinitesimally short peak between two samples stayed under the
cap. Enforcement is therefore complemented by bounded buffers (the body
buffer can never exceed its reservation; parser input is bounded by the
4 MiB body cap and Thrift size limits).
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil


class MemoryGuardError(RuntimeError):
    """Memory cap breach or measurement failure: abort physical work."""


class StepDeadlineExceeded(RuntimeError):
    """The current step's absolute deadline passed (per-step, retryable)."""


@dataclass(frozen=True)
class Registration:
    """Opaque handle; holds no authority by itself."""

    token: str
    pid: int


class OwnedProcessRegistry:
    """Owns external/child process lifecycle for memory accounting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, tuple[int, float]] = {}

    def register(self, pid: int) -> Registration:
        if type(pid) is not int or pid <= 0:
            raise MemoryGuardError("pid must be a positive integer")
        try:
            created = psutil.Process(pid).create_time()
        except (psutil.Error, OSError) as exc:
            raise MemoryGuardError(f"cannot register unobservable pid {pid}: {exc}") from exc
        token = secrets.token_hex(16)
        with self._lock:
            self._records[token] = (pid, created)
        return Registration(token=token, pid=pid)

    def reap(self, registration: Registration) -> None:
        """Release only after the registry observes the process is gone."""
        with self._lock:
            record = self._records.get(registration.token)
        if record is None or record[0] != registration.pid:
            raise MemoryGuardError("unknown registration")
        pid, created = record
        try:
            proc = psutil.Process(pid)
            alive = proc.create_time() == created and proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
        except (psutil.Error, OSError) as exc:
            raise MemoryGuardError(f"cannot observe pid {pid}; keeping it accounted") from exc
        if alive:
            raise MemoryGuardError(f"pid {pid} is still running; cannot release its accounting")
        with self._lock:
            self._records.pop(registration.token, None)

    def owned(self) -> tuple[tuple[int, float], ...]:
        with self._lock:
            return tuple(sorted(self._records.values()))


RssReader = Callable[[], int]


class TreeSampler:
    """Fail-closed RSS of main process + descendants + registered PIDs."""

    def __init__(self, registry: OwnedProcessRegistry, *, main_pid: int | None = None) -> None:
        self._registry = registry
        self._main_pid = os.getpid() if main_pid is None else main_pid

    def __call__(self) -> int:
        try:
            main = psutil.Process(self._main_pid)
            members: dict[int, psutil.Process] = {main.pid: main}
            for child in main.children(recursive=True):
                members.setdefault(child.pid, child)
        except (psutil.Error, OSError) as exc:
            raise MemoryGuardError(f"cannot enumerate the process tree: {exc}") from exc
        required: set[int] = {self._main_pid}
        for pid, created in self._registry.owned():
            required.add(pid)
            if pid not in members:
                try:
                    proc = psutil.Process(pid)
                    if proc.create_time() != created:
                        raise MemoryGuardError(f"registered pid {pid} was reused; not reaped")
                except psutil.NoSuchProcess as exc:
                    raise MemoryGuardError(f"registered pid {pid} vanished without reap") from exc
                except (psutil.Error, OSError) as exc:
                    raise MemoryGuardError(f"registered pid {pid} is unobservable") from exc
                members[pid] = proc
            try:
                # An owned external process is measured with its own descendants
                # (e.g. a venv launcher and the interpreter it spawns).
                for child in members[pid].children(recursive=True):
                    members.setdefault(child.pid, child)
            except psutil.NoSuchProcess as exc:
                raise MemoryGuardError(f"registered pid {pid} vanished without reap") from exc
            except (psutil.Error, OSError) as exc:
                raise MemoryGuardError(f"cannot enumerate descendants of pid {pid}") from exc
        total = 0
        for pid, proc in members.items():
            try:
                total += int(proc.memory_info().rss)
            except psutil.NoSuchProcess as exc:
                if pid in required:
                    raise MemoryGuardError(f"required pid {pid} disappeared mid-sample") from exc
            except (psutil.Error, OSError) as exc:
                raise MemoryGuardError(f"cannot read RSS of pid {pid}: {exc}") from exc
        return total


class Supervisor:
    """Monitor thread enforcing the memory cap and the step deadline."""

    def __init__(
        self,
        *,
        reader: RssReader,
        cap_bytes: int,
        interval_s: float,
        monotonic_ns: Callable[[], int],
    ) -> None:
        if not 0 < interval_s <= 1.0:
            raise MemoryGuardError("sampling interval must be within (0, 1] s")
        self._reader = reader
        self._cap = cap_bytes
        self._interval = interval_s
        self._now = monotonic_ns
        self._abort: str | None = None
        self._deadline_ns: int | None = None
        self._peak = 0
        self._samples = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def peak_bytes(self) -> int:
        return self._peak

    @property
    def samples(self) -> int:
        return self._samples

    @property
    def abort_reason(self) -> str | None:
        return self._abort

    def _latch(self, reason: str) -> None:
        with self._lock:
            if self._abort is None:
                self._abort = reason

    def sample_once(self) -> None:
        try:
            rss = self._reader()
        except Exception as exc:  # any reader failure is a measurement failure
            self._latch(f"memory measurement failed: {exc}")
            return
        if type(rss) is not int or rss < 0:
            self._latch("memory reader returned a non-integer RSS")
            return
        self._samples += 1
        self._peak = max(self._peak, rss)
        if rss > self._cap:
            self._latch(f"process-tree RSS {rss} exceeds cap {self._cap}")

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.sample_once()

    def start(self) -> None:
        if self._thread is not None:
            raise MemoryGuardError("supervisor already started")
        self.sample_once()
        if self._abort is not None:
            raise MemoryGuardError(self._abort)
        self._thread = threading.Thread(
            target=self._run, name="evidence-v3-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def set_deadline(self, deadline_ns: int | None) -> None:
        self._deadline_ns = deadline_ns

    def checkpoint(self) -> None:
        """Raise on a latched memory abort, or when the step deadline passed."""
        if self._abort is not None:
            raise MemoryGuardError(self._abort)
        deadline = self._deadline_ns
        if deadline is not None and self._now() > deadline:
            raise StepDeadlineExceeded("step deadline exceeded")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def wait_interval() -> float:
    """Documented default sampling interval (seconds)."""
    return 0.1


def monotonic_ns() -> int:
    return time.monotonic_ns()
