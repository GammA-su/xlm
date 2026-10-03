"""Bounded spawn-process job pools for C05 scanning, grouping and publication.

Children only compute. A ``scan`` child opens the verified compiled matcher
read-only (mmap, shared OS page cache) and prepares immutable facts for raw
batches; a ``group`` child reads published fact units and grouping arrays
read-only (mmap) to derive band keys, digests and output lines. No child writes a
file, state, attestation or artifact: the single parent owns all mutation.

Flow control is credit-based and deadlock-free: the parent keeps at most
``capacity`` jobs in flight (dispatched and not yet received), and both queues
hold more than ``capacity`` entries, so neither side ever blocks on a full pipe
while the other waits on it. Completion order is irrelevant: every result is keyed
(sequence/unit/range) and the parent integrates strictly by key.

``workers == 1`` runs the identical role handler in-process (the reference path).
"""

from __future__ import annotations

import ctypes
import importlib
import multiprocessing
import os
import pickle
import queue
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

import psutil

from xlm.data.exclusion.policy import C05Error, ProductionPolicy
from xlm.data.exclusion.scanprep import FileContext, PreparedBatch, Preparer

POLL_SECONDS: Final = 0.2
READY_SECONDS: Final = 600.0
STOP_SECONDS: Final = 10.0
JOBS_PER_WORKER: Final = 2
#: Fixed allowlist of child roles; never a path or name taken from plan data.
ROLES: Final = {
    "scan": ("xlm.data.exclusion.scanpool", "ScanRole"),
    "group": ("xlm.data.exclusion.grouping", "GroupRole"),
}


@dataclass(frozen=True)
class MatcherSpec:
    """Content-free binding a scan child re-verifies before its first lookup."""

    directory: str
    index_sha256: str
    index_bytes: int
    max_records: int | None
    max_logical_nodes: int | None


@dataclass(frozen=True)
class ScanInit:
    policy: dict[str, Any]
    matcher: MatcherSpec
    review: bool


@dataclass(frozen=True)
class Task:
    context: FileContext
    sequence: int
    first_row: int
    lines: tuple[bytes, ...]


class Role(Protocol):
    def __call__(self, job: Any) -> Any: ...

    def close(self) -> None: ...


class ScanRole:
    """Prepare facts for raw batches with a privately re-verified matcher."""

    def __init__(self, init: ScanInit, matcher: Any = None) -> None:
        self.owned = matcher is None
        if matcher is None:
            from xlm.data.exclusion.compact import CompactExactMatcher

            spec = init.matcher
            matcher = CompactExactMatcher(
                Path(spec.directory),
                index_sha256=spec.index_sha256,
                index_bytes=spec.index_bytes,
                max_records=spec.max_records,
                max_logical_nodes=spec.max_logical_nodes,
            )
        self.matcher = matcher
        self.preparer = Preparer(ProductionPolicy.model_validate(init.policy), matcher, init.review)

    def __call__(self, job: Task) -> PreparedBatch:
        return self.preparer.batch(job.context, job.sequence, job.first_row, job.lines)

    def close(self) -> None:
        if self.owned:
            self.matcher.close()


def _role(name: str, init: Any) -> Role:
    module, attribute = ROLES[name]
    factory: Callable[[Any], Role] = getattr(importlib.import_module(module), attribute)
    return factory(init)


def _child(number: int, tasks: Any, results: Any, busy: Any, role: str, init: Any) -> None:
    """Child entry point: reports only content-free reasons and exception types."""
    handler: Role | None = None
    try:
        handler = _role(role, init)
        results.put(("ready", number, os.getpid()))
        while (job := tasks.get()) is not None:
            busy[number] = 1
            result = handler(job)
            busy[number] = 0
            results.put(("result", result))
    except KeyboardInterrupt:
        return
    except BaseException as exc:  # noqa: BLE001 - any child failure fails the stage
        reason = str(exc)[:200] if isinstance(exc, C05Error) else ""
        results.put(("error", number, type(exc).__name__, reason))
    finally:
        busy[number] = 0
        if handler is not None:
            handler.close()


class InlinePool:
    """``workers == 1``: the same role handler, synchronously, in this process."""

    def __init__(self, handler: Role) -> None:
        self.handler = handler
        self.done: deque[Any] = deque()
        self.workers = 1
        self.capacity = 1
        self.pids: list[int] = []

    def start(self, check: Callable[[], None]) -> None:
        return None

    def submit(self, job: Any) -> None:
        self.done.append(self.handler(job))

    def get(self, check: Callable[[], None]) -> Any:
        if not self.done:
            raise C05Error("pool result requested with no job in flight")
        return self.done.popleft()

    def telemetry(self) -> dict[str, Any]:
        return {"workers": 1, "busy": 0, "tasks": 0, "results": len(self.done), "capacity": 1}

    def close(self) -> None:
        self.done.clear()
        self.handler.close()


class ProcessPool:
    """``workers`` spawned children behind bounded job/result queues."""

    def __init__(
        self, workers: int, role: str, init: Any, *, jobs_per_worker: int = JOBS_PER_WORKER
    ) -> None:
        if not 2 <= workers <= 16 or role not in ROLES:
            raise C05Error("worker pool configuration outside 2..16 or unknown role")
        self.workers = workers
        self.capacity = jobs_per_worker * workers
        context = multiprocessing.get_context("spawn")
        self.tasks: Any = context.Queue(maxsize=self.capacity + workers + 1)
        self.results: Any = context.Queue(maxsize=self.capacity + 2 * workers + 1)
        self.busy: Any = context.RawArray(ctypes.c_int, workers)
        self.children = [
            context.Process(
                target=_child,
                args=(number, self.tasks, self.results, self.busy, role, init),
                name=f"c05-{role}-{number}",
                daemon=True,
            )
            for number in range(workers)
        ]
        self.pids: list[int] = []

    def start(self, check: Callable[[], None]) -> None:
        for child in self.children:
            child.start()
        ready: set[int] = set()
        started = time.monotonic()
        while len(ready) < self.workers:
            check()
            if time.monotonic() - started > READY_SECONDS:
                raise C05Error("C05 workers did not become ready")
            event = self._event()
            if event is None:
                continue
            if event[0] != "ready":
                raise C05Error("unexpected C05 worker event before ready")
            ready.add(event[1])
            self.pids.append(event[2])

    def _event(self) -> tuple[Any, ...] | None:
        try:
            event: tuple[Any, ...] = self.results.get(timeout=POLL_SECONDS)
        except queue.Empty:
            if any(child.exitcode is not None for child in self.children):
                raise C05Error("C05 worker exited abnormally") from None
            return None
        except (EOFError, OSError, pickle.UnpicklingError) as exc:
            # A child killed mid-write leaves a broken result channel.
            raise C05Error("C05 worker result channel broken") from exc
        if event[0] == "error":
            raise C05Error(f"C05 worker failed: {event[2]} {event[3]}".rstrip())
        return event

    def submit(self, job: Any) -> None:
        self.tasks.put(job, timeout=STOP_SECONDS)

    def get(self, check: Callable[[], None]) -> Any:
        while True:
            check()
            event = self._event()
            if event is not None:
                return event[1]

    def telemetry(self) -> dict[str, Any]:
        try:
            tasks, results = self.tasks.qsize(), self.results.qsize()
        except NotImplementedError:  # pragma: no cover - platform without sem_getvalue
            tasks = results = -1
        return {
            "workers": self.workers,
            "busy": int(sum(self.busy)),
            "tasks": tasks,
            "results": results,
            "capacity": self.capacity,
        }

    def close(self) -> None:
        """Stop, join and reap every child; never leaves an orphan process."""
        for child in self.children:
            if child.is_alive():
                child.terminate()
        for child in self.children:
            if child.pid is not None:
                child.join(STOP_SECONDS)
                if child.is_alive():
                    child.kill()
                    child.join(STOP_SECONDS)
        for channel in (self.tasks, self.results):
            channel.cancel_join_thread()
            channel.close()


Pool = InlinePool | ProcessPool


def make_pool(workers: int, role: str, init: Any, inline: Callable[[], Role]) -> Pool:
    """``workers == 1`` builds the in-process handler; otherwise spawned children."""
    if workers == 1:
        return InlinePool(inline())
    return ProcessPool(workers, role, init)


class ProcessTree:
    """Parent plus every descendant's RSS (working set on Windows).

    Shared read-only mappings (the compiled matcher, fact units) are counted once
    per process that touches them, so the sum over-counts shared pages: the RAM
    gate is conservative. Descendants are rediscovered at most every ``refresh``
    seconds (the OS process table walk is slow on Windows).
    """

    def __init__(self, refresh: float = 2.0) -> None:
        self.parent = psutil.Process()
        self.refresh = refresh
        self.known: list[psutil.Process] = []
        self.scanned: float | None = None

    def rescan(self) -> None:
        self.scanned = None

    def sample(self) -> tuple[int, int]:
        """(total process-tree RSS, number of live descendants)."""
        now = time.monotonic()
        if self.scanned is None or now - self.scanned >= self.refresh:
            self.known = self.parent.children(recursive=True)
            self.scanned = now
        total = self.parent.memory_info().rss
        alive = 0
        for child in self.known:
            try:
                total += child.memory_info().rss
                alive += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total, alive


def run_jobs(
    pool: Pool,
    jobs: list[Any],
    consume: Callable[[Any], None],
    check: Callable[[], None],
) -> None:
    """Dispatch ``jobs`` with at most ``capacity`` in flight; consume every result.

    ``consume`` receives results in completion order; callers key them.
    """
    pending = 0
    position = 0
    while position < len(jobs) or pending:
        while position < len(jobs) and pending < pool.capacity:
            check()
            pool.submit(jobs[position])
            position += 1
            pending += 1
        consume(pool.get(check))
        pending -= 1


def ordered_jobs(
    pool: Pool,
    jobs: list[Any],
    key: Callable[[Any], int],
    consume: Callable[[Any], None],
    check: Callable[[], None],
) -> None:
    """Like :func:`run_jobs` but ``consume`` sees results in job order (bounded buffer)."""
    buffered: dict[int, Any] = {}
    expected = 0
    pending = 0
    position = 0
    while position < len(jobs) or pending:
        while position < len(jobs) and pending < pool.capacity:
            check()
            pool.submit(jobs[position])
            position += 1
            pending += 1
        result = pool.get(check)
        pending -= 1
        buffered[key(result)] = result
        while expected in buffered:
            consume(buffered.pop(expected))
            expected += 1
    if buffered:
        raise C05Error("ordered job results incomplete")
