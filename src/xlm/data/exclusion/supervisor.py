"""One supervisor for the C06 fast fit: absolute deadline, process-tree RAM, disk, projection.

The supervisor is independent of progress rendering. A monitor thread samples every
``interval`` seconds; on the first failure (deadline, RSS ceiling, free-space reserve,
unavailable inspection, or a hopeless projected total) it records one content-free
reason, sets the cancellation flag and terminates every descendant process
(terminate -> bounded grace -> kill -> reap). Work runs only in descendants or in
cooperative main-thread loops that call :meth:`Supervisor.check`; final publication
is the parent's alone and requires a passing check with time to spare.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import psutil

from xlm.data.exclusion.policy import C05Error

DEADLINE_REASON = "operator deadline exceeded; nothing published"
RSS_REASON = "C06 process-tree RSS exceeded its reviewed ceiling"
DISK_REASON = "free-space reserve violated on a C06 volume"
INSPECTION_REASON = "process-tree inspection unavailable; refusing"
PROJECTION_REASON = "projected total exceeds the operator deadline; aborting early"


class Checkable(Protocol):
    def check(self) -> None: ...


class Deadline:
    """Absolute monotonic deadline; ``None`` means unbounded (internal/test use only)."""

    def __init__(self, seconds: float | None, started: float) -> None:
        if seconds is not None and (
            isinstance(seconds, bool) or not math.isfinite(seconds) or seconds <= 0
        ):
            raise C05Error("deadline must be a finite positive number of seconds")
        self.seconds, self.started = seconds, started
        self.at = None if seconds is None else started + seconds

    def remaining(self) -> float | None:
        return None if self.at is None else self.at - time.monotonic()

    def expired(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    def check(self) -> None:
        if self.expired():
            raise C05Error(DEADLINE_REASON)


def tree_rss(process: psutil.Process | None = None) -> int:
    """Conservative aggregate RSS of a process and every descendant (shared pages counted)."""
    root = process or psutil.Process()
    total = int(root.memory_info().rss)
    for child in root.children(recursive=True):
        try:
            total += int(child.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return total


def terminate_processes(processes: list[psutil.Process], grace: float) -> list[int]:
    """Terminate, wait ``grace``, kill survivors, reap; return PIDs still alive."""
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(processes, timeout=grace)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(alive, timeout=grace)
    return [process.pid for process in alive]


def terminate_descendants(grace: float) -> list[int]:
    try:
        children = psutil.Process().children(recursive=True)
    except psutil.Error:
        return []
    return terminate_processes(children, grace)


@dataclass
class _Stage:
    done: float = 0.0
    total: float = 0.0
    started: float | None = None
    ended: float | None = None


@dataclass
class Projection:
    """Projected TOTAL completion time; operational only, never a scientific identity.

    Before a stage has measured progress it is charged at a conservative planning
    rate. BPE and finalization always keep their reviewed reserves until they finish.
    """

    deadline_seconds: float | None
    membership_planning_rate: float
    source_planning_rate: float
    selection_reserve: float
    bpe_reserve: float
    finalization_reserve: float
    early_abort_ratio: float
    stages: dict[str, _Stage] = field(default_factory=dict)
    bpe_started: float | None = None
    bpe_ended: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def plan(self, name: str, total: float) -> None:
        with self.lock:
            self.stages.setdefault(name, _Stage()).total = float(total)

    def progress(self, name: str, done: float, total: float | None = None) -> None:
        now = time.monotonic()
        with self.lock:
            stage = self.stages.setdefault(name, _Stage())
            if stage.started is None:
                stage.started = now
            stage.done = float(done)
            if total is not None:
                stage.total = float(total)

    def finish(self, name: str) -> None:
        with self.lock:
            stage = self.stages.setdefault(name, _Stage())
            stage.ended = time.monotonic()
            stage.done = stage.total

    def bpe(self, *, started: bool) -> None:
        with self.lock:
            if started:
                self.bpe_started = time.monotonic()
            else:
                self.bpe_ended = time.monotonic()

    def _remaining(self, name: str, planning_rate: float, now: float) -> tuple[float, bool]:
        stage = self.stages.get(name, _Stage())
        if stage.ended is not None:
            return 0.0, True
        left = max(0.0, stage.total - stage.done)
        if stage.started is not None and stage.done > 0:
            elapsed = now - stage.started
            measured = elapsed >= 10 and stage.done >= 0.02 * max(stage.total, 1.0)
            if elapsed > 0:
                rate = stage.done / elapsed
                if measured:
                    return left / max(rate, 1e-9), True
        return left / planning_rate, False

    def evaluate(self, elapsed: float) -> dict[str, float | bool]:
        """Projected total seconds and whether it rests on measured source progress."""
        now = time.monotonic()
        with self.lock:
            membership, _ = self._remaining("membership", self.membership_planning_rate, now)
            source, source_measured = self._remaining("source", self.source_planning_rate, now)
            selection = (
                0.0 if self.stages.get("source", _Stage()).started else self.selection_reserve
            )
            if self.bpe_ended is not None:
                bpe = 0.0
            elif self.bpe_started is not None:
                bpe = max(0.0, self.bpe_reserve - (now - self.bpe_started))
            else:
                bpe = self.bpe_reserve
        projected = elapsed + membership + selection + source + bpe + self.finalization_reserve
        return {
            "projected_total_s": projected,
            "membership_remaining_s": membership,
            "source_remaining_s": source,
            "bpe_reserve_s": bpe,
            "finalization_reserve_s": self.finalization_reserve,
            "measured": source_measured,
        }


class Supervisor:
    """Owns the deadline, RAM ceiling, volume reserves, projection and descendant teardown."""

    def __init__(
        self,
        deadline: Deadline,
        ram_ceiling: int | None,
        *,
        interval: float = 0.25,
        grace: float = 2.0,
        volumes: Mapping[str, int] | None = None,
        projection: Projection | None = None,
        warn: Callable[[str], None] | None = None,
        warn_interval: float = 30.0,
    ) -> None:
        if not 0 < interval <= 5 or not 0 < grace <= 30:
            raise C05Error("supervisor interval/grace outside reviewed bounds")
        if ram_ceiling is not None and ram_ceiling < 1:
            raise C05Error("RAM ceiling must be positive")
        self.deadline = deadline
        self.ram_ceiling = ram_ceiling
        self.interval, self.grace = interval, grace
        self.volumes = dict(volumes or {})
        self.projection = projection
        self.warn = warn or _stderr
        self.warn_interval = warn_interval
        self.process = psutil.Process()
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.stopped = threading.Event()
        self.failure: str | None = None
        self.failed_at: float | None = None
        self.peak_rss = 0
        self.min_free: dict[str, int] = {}  # minimum observed free bytes per watched volume
        self.samples = 0
        self.unreaped: list[int] = []
        self.last_warning: float | None = None
        self.thread = threading.Thread(target=self._run, name="c06-supervisor", daemon=True)

    # -- lifecycle -------------------------------------------------------------------

    def __enter__(self) -> Supervisor:
        self.sample()  # Fail closed immediately if inspection is unavailable.
        self.thread.start()
        return self

    def __exit__(self, kind: object, *_: object) -> None:
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join()
        if kind is not None:
            self.unreaped += terminate_descendants(self.grace)

    def _run(self) -> None:
        while not self.stopped.wait(self.interval):
            self.sample()

    # -- checks ----------------------------------------------------------------------

    def remaining(self) -> float | None:
        return self.deadline.remaining()

    def check(self) -> None:
        if self.failure is not None:
            raise C05Error(self.failure)
        if self.deadline.expired():
            self.fail(DEADLINE_REASON)
            raise C05Error(DEADLINE_REASON)

    def fail(self, reason: str) -> None:
        with self.lock:
            first = self.failure is None
            if first:
                self.failure = reason
                self.failed_at = time.monotonic()
                self.cancelled.set()
        if first:
            self.unreaped += terminate_descendants(self.grace)

    def sample(self) -> None:
        if self.failure is not None:
            return
        if self.deadline.expired():
            self.fail(DEADLINE_REASON)
            return
        try:
            rss = tree_rss(self.process)
        except psutil.Error:
            self.fail(INSPECTION_REASON)
            return
        self.samples += 1
        self.peak_rss = max(self.peak_rss, rss)
        if self.ram_ceiling is not None and rss > self.ram_ceiling:
            self.fail(RSS_REASON)
            return
        for path, reserve in self.volumes.items():
            try:
                free = shutil.disk_usage(path).free
            except OSError:
                self.fail(DISK_REASON)
                return
            self.min_free[path] = min(free, self.min_free.get(path, free))
            if free < reserve:
                self.fail(DISK_REASON)
                return
        self._project()

    def _project(self) -> None:
        projection = self.projection
        if projection is None or projection.deadline_seconds is None:
            return
        elapsed = time.monotonic() - self.deadline.started
        view = projection.evaluate(elapsed)
        total = float(view["projected_total_s"])
        if total <= projection.deadline_seconds:
            return
        now = time.monotonic()
        if self.last_warning is None or now - self.last_warning >= self.warn_interval:
            self.last_warning = now
            self.warn(
                f"SLO WARNING | projected total {total:,.0f} s exceeds the "
                f"{projection.deadline_seconds:,.0f} s deadline "
                f"(elapsed {elapsed:,.0f} s; remaining: membership "
                f"{float(view['membership_remaining_s']):,.0f} s, source "
                f"{float(view['source_remaining_s']):,.0f} s, BPE reserve "
                f"{float(view['bpe_reserve_s']):,.0f} s, finalization "
                f"{float(view['finalization_reserve_s']):,.0f} s)"
            )
        if view["measured"] and total > projection.deadline_seconds * projection.early_abort_ratio:
            self.fail(PROJECTION_REASON)

    def overshoot(self) -> float:
        at = self.deadline.at
        return 0.0 if at is None else max(0.0, time.monotonic() - at)


def _stderr(message: str) -> None:
    print(f"[C06] {message}", file=sys.stderr, flush=True)


def device_of(path: Path) -> int:
    """Volume identity of the nearest existing ancestor (Windows: volume serial)."""
    current = path
    while not current.exists():
        if current.parent == current:
            raise C05Error("no existing ancestor for a C06 path")
        current = current.parent
    return os.stat(current).st_dev


def existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists():
        current = current.parent
    return current
