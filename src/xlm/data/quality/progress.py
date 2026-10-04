"""Operational live progress for the quality audit (never a scientific output).

:class:`Telemetry` holds content-free counters that the audit updates as it works;
:class:`Reporter` is one background thread that, every interval, formats one plain
text line from those counters plus a process-tree measurement (CPU seconds of the
parent and of every child, child count, RSS) and writes it to stderr and optionally
appends it to a progress log (flushed per line). Nothing here reads corpus text,
metadata or paths; lines hold phase names and numbers only. Progress is not part of
any digest, unit, artifact or receipt and is never a completion signal.

Rates use monotonic time: ``now`` is the rate over the last ~``WINDOW_SECONDS`` and
``ewma`` an exponentially weighted rate (time constant ``EWMA_SECONDS``) that drives
the ETA, so a single slow or fast interval does not make the ETA jump.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

PREFIX = "[quality-audit]"
WINDOW_SECONDS = 15.0
EWMA_SECONDS = 30.0
ACTIVE_CORE_FRACTION = 0.5  # a child using >= half a core over the interval is "active"
MIN_INTERVAL_SECONDS = 0.2
MAX_INTERVAL_SECONDS = 3600.0
PHASES = (
    "prepare",
    "overlay",
    "resume-verify",
    "scan",
    "verify-drain",
    "aggregate",
    "write",
    "publish",
    "complete",
)


class ProgressError(ValueError):
    """Content-free refusal of a progress option."""


def check_interval(seconds: float) -> float:
    if not (math.isfinite(seconds) and MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS):
        raise ProgressError("--progress-interval-seconds outside [0.2, 3600]")
    return float(seconds)


@dataclass
class _Phase:
    name: str
    started: float
    done: int = 0
    total: int = 0
    unit: str = ""


class Telemetry:
    """Content-free counters shared by the audit thread(s) and the reporter.

    Writers increment under ``lock``; the reporter reads a consistent snapshot.
    """

    def __init__(self, workers: int, started: float) -> None:
        self.lock = threading.Lock()
        self.started = started
        self.totals = {"files": 0, "documents": 0, "file_bytes": 0, "canonical_bytes": 0}
        self.workers = workers
        self.phase = _Phase("prepare", started)
        self.durations: list[tuple[str, float]] = []
        self.files_done = 0
        self.docs_done = 0
        self.bytes_done = 0  # resumed files + measured chunks (partial files included)
        self.bytes_scanned = 0  # measured by THIS run (the rate base)
        self.files_resumed = 0
        self.files_scanned = 0
        self.chunks_submitted = 0
        self.chunks_completed = 0
        self.inflight = 0
        self.peak_inflight = 0
        self.verify_pending = 0
        self.worker_busy_seconds = 0.0
        self.worker_cpu_seconds = 0.0
        self.worker_pids: set[int] = set()
        self.output_bytes = 0

    def set_totals(self, totals: dict[str, int]) -> None:
        with self.lock:
            self.totals = dict(totals)

    def set_phase(self, name: str, total: int = 0, unit: str = "") -> None:
        if name not in PHASES:
            raise ProgressError("unknown progress phase")
        now = time.monotonic()
        with self.lock:
            self.durations.append((self.phase.name, now - self.phase.started))
            self.phase = _Phase(name, now, 0, total, unit)

    def phase_seconds(self) -> dict[str, float]:
        """Measured wall seconds per finished phase (repeated phases are summed)."""
        with self.lock:
            out: dict[str, float] = {}
            for name, seconds in self.durations:
                out[name] = round(out.get(name, 0.0) + seconds, 3)
            return out

    def set_output_bytes(self, value: int) -> None:
        with self.lock:
            self.output_bytes = value

    def advance(self, amount: int) -> None:
        """Phase-local progress (overlay bytes, re-hashed bytes, units aggregated)."""
        with self.lock:
            self.phase.done += amount

    def resumed(self, files: int, documents: int, nbytes: int) -> None:
        with self.lock:
            self.files_resumed += files
            self.files_done += files
            self.docs_done += documents
            self.bytes_done += nbytes

    def submitted(self, inflight: int) -> None:
        with self.lock:
            self.chunks_submitted += 1
            self.inflight = inflight
            self.peak_inflight = max(self.peak_inflight, inflight)

    def completed(self, inflight: int, newly: int) -> None:
        with self.lock:
            self.chunks_completed += newly
            self.inflight = inflight

    def measured(self, rows: int, nbytes: int, busy: float, cpu: float, pid: int) -> None:
        with self.lock:
            self.docs_done += rows
            self.bytes_done += nbytes
            self.bytes_scanned += nbytes
            self.worker_busy_seconds += busy
            self.worker_cpu_seconds += cpu
            self.worker_pids.add(pid)

    def committed(self, output_bytes: int, verify_pending: int) -> None:
        with self.lock:
            self.files_done += 1
            self.files_scanned += 1
            self.output_bytes = output_bytes
            self.verify_pending = verify_pending

    def verifying(self, pending: int) -> None:
        with self.lock:
            self.verify_pending = pending

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            phase = self.phase
            return {
                "totals": dict(self.totals),
                "phase": phase.name,
                "phase_started": phase.started,
                "phase_done": phase.done,
                "phase_total": phase.total,
                "phase_unit": phase.unit,
                "files_done": self.files_done,
                "docs_done": self.docs_done,
                "bytes_done": self.bytes_done,
                "bytes_scanned": self.bytes_scanned,
                "files_resumed": self.files_resumed,
                "files_scanned": self.files_scanned,
                "chunks_submitted": self.chunks_submitted,
                "chunks_completed": self.chunks_completed,
                "inflight": self.inflight,
                "verify_pending": self.verify_pending,
                "worker_busy_seconds": self.worker_busy_seconds,
                "worker_cpu_seconds": self.worker_cpu_seconds,
                "worker_processes": len(self.worker_pids),
                "output_bytes": self.output_bytes,
            }


class RateMeter:
    """Short-window and EWMA rates of a monotonically growing counter.

    The EWMA starts at the first interval in which the counter moved (startup time
    before any progress does not drag it down) from that interval's rate, then
    blends each new interval's rate with weight ``1 - exp(-dt / tau)``.
    """

    def __init__(self, window: float = WINDOW_SECONDS, tau: float = EWMA_SECONDS) -> None:
        self.window = window
        self.tau = tau
        self.samples: deque[tuple[float, float]] = deque()
        self.ewma: float | None = None

    def update(self, now: float, value: float) -> tuple[float | None, float | None]:
        """Record ``value`` at ``now``; return (window rate, EWMA rate)."""
        if self.samples:
            last_t, last_v = self.samples[-1]
            dt = now - last_t
            if dt > 0:
                instant = (value - last_v) / dt
                if self.ewma is None:
                    if value > last_v:
                        self.ewma = instant
                else:
                    self.ewma += (1.0 - math.exp(-dt / self.tau)) * (instant - self.ewma)
        self.samples.append((now, value))
        while len(self.samples) > 2 and now - self.samples[1][0] >= self.window:
            self.samples.popleft()
        first_t, first_v = self.samples[0]
        window = (value - first_v) / (now - first_t) if now > first_t else None
        return window, self.ewma


def duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1e6:.2f}M"
    if value >= 10_000:
        return f"{value / 1e3:.1f}k"
    return str(value)


def _rate(value: float | None) -> str:
    return "--" if value is None else f"{value / 1e6:.1f}"


@dataclass
class TreeSample:
    """One process-tree measurement: CPU seconds per process, child count, RSS."""

    at: float
    parent_cpu: float
    children_cpu: dict[int, float]
    rss: int


def sample_tree() -> TreeSample | None:
    import psutil

    try:
        me = psutil.Process()
        times = me.cpu_times()
        rss = int(me.memory_info().rss)
        children: dict[int, float] = {}
        for child in me.children(recursive=True):
            try:
                ct = child.cpu_times()
                children[child.pid] = float(ct.user + ct.system)
                rss += int(child.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return TreeSample(time.monotonic(), float(times.user + times.system), children, rss)
    except (psutil.Error, OSError):  # progress is operational: never fail the audit
        return None


def tree_usage(before: TreeSample | None, after: TreeSample | None) -> dict[str, Any]:
    """Cores used by parent and children between two samples, active children."""
    if before is None or after is None or after.at <= before.at:
        return {"parent_cores": None, "child_cores": None, "active": None, "children": None}
    dt = after.at - before.at
    child_cores = 0.0
    active = 0
    for pid, cpu in after.children_cpu.items():
        used = (cpu - before.children_cpu.get(pid, 0.0)) / dt
        child_cores += max(used, 0.0)
        active += used >= ACTIVE_CORE_FRACTION
    return {
        "parent_cores": max(after.parent_cpu - before.parent_cpu, 0.0) / dt,
        "child_cores": child_cores,
        "active": active,
        "children": len(after.children_cpu),
    }


def format_line(
    snap: dict[str, Any],
    totals: dict[str, int],
    workers: int,
    elapsed: float,
    rates: tuple[float | None, float | None],
    usage: dict[str, Any],
    rss: int | None,
    prefix: str = PREFIX,
) -> str:
    """One content-free progress line."""
    phase = snap["phase"]
    total_bytes = totals["file_bytes"]
    parts = [f"{prefix} {phase}"]
    if phase in ("scan", "verify-drain") or snap["bytes_done"]:
        percent = 100.0 * snap["bytes_done"] / total_bytes if total_bytes else 100.0
        parts[0] += f" {percent:.1f}%"
        parts += [
            f"{snap['files_done']}/{totals['files']} files",
            f"{_count(snap['docs_done'])}/{_count(totals['documents'])} docs",
            f"{snap['bytes_done'] / 1e9:.1f}/{total_bytes / 1e9:.1f} GB",
        ]
    if phase not in ("scan", "verify-drain") and snap["phase_total"]:
        done, total = snap["phase_done"], snap["phase_total"]
        unit = snap["phase_unit"]
        if unit == "bytes":
            parts.append(f"{phase} {done / 1e9:.2f}/{total / 1e9:.2f} GB")
        else:
            parts.append(f"{phase} {done}/{total} {unit}")
        phase_elapsed = time.monotonic() - snap["phase_started"]
        if done and total > done and phase_elapsed > 0:
            parts.append(f"phase ETA {duration((total - done) * phase_elapsed / done)}")
    window, ewma = rates
    if phase in ("scan", "verify-drain"):
        remaining = total_bytes - snap["bytes_done"]
        eta = remaining / ewma if ewma and ewma > 0 else None
        parts += [
            f"{_rate(window)} MB/s now",
            f"{_rate(ewma)} MB/s ewma",
            f"ETA {duration(eta) if remaining > 0 else '0s'}",
        ]
    parts.append(f"elapsed {duration(elapsed)}")
    if phase in ("scan", "verify-drain"):
        active = usage.get("active")
        parts += [
            f"workers {'--' if active is None else active}/{workers} active",
            f"inflight {snap['inflight']}",
            f"chunks {snap['chunks_completed']}/{snap['chunks_submitted']}",
            f"resumed {snap['files_resumed']} new {snap['files_scanned']}",
            f"verify {snap['verify_pending']}",
        ]
    cores = usage.get("child_cores")
    if cores is not None:
        parts.append(f"CPU {usage['parent_cores']:.1f}+{cores:.1f} cores")
    if rss is not None:
        parts.append(f"RSS {rss / 2**30:.2f} GiB")
    parts.append(f"out {snap['output_bytes'] / 2**30:.2f} GiB")
    return " | ".join(parts)


class Reporter:
    """Background line writer: stderr (optional) and an append-only log (optional)."""

    def __init__(
        self,
        telemetry: Telemetry,
        interval: float,
        *,
        stderr: bool,
        log: Path | None,
        prefix: str = PREFIX,
    ) -> None:
        self.prefix = prefix
        self.telemetry = telemetry
        self.interval = check_interval(interval)
        self.stderr = stderr
        self.log_path = log
        self.log: IO[str] | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.meter = RateMeter()
        self.previous: TreeSample | None = None
        self.lines = 0

    def __enter__(self) -> Reporter:
        if self.log_path is not None:
            self.log = self.log_path.open("a", encoding="utf-8", newline="\n")
            self._write(
                f"{self.prefix} start pid {os.getpid()} | workers {self.telemetry.workers} | "
                f"interval {self.interval:g}s"
            )
        if self.stderr or self.log is not None:
            self.previous = sample_tree()
            self.thread = threading.Thread(target=self._run, name="quality-progress", daemon=True)
            self.thread.start()
        return self

    def __exit__(self, kind: object, *_: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10.0)
        try:
            if self.stderr or self.log is not None:
                self.emit(final="ok" if kind is None else "stopped")
        finally:
            if self.log is not None:
                self.log.close()
                self.log = None

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            self.emit()

    def emit(self, final: str | None = None) -> None:
        try:
            snap = self.telemetry.snapshot()
            now = time.monotonic()
            rates = self.meter.update(now, float(snap["bytes_scanned"]))
            current = sample_tree()
            usage = tree_usage(self.previous, current)
            if current is not None:
                self.previous = current
            line = format_line(
                snap,
                snap["totals"],
                self.telemetry.workers,
                now - self.telemetry.started,
                rates,
                usage,
                None if current is None else current.rss,
                self.prefix,
            )
            if final is not None:
                line += f" | {final}"
            self._write(line)
        except (OSError, ValueError, ArithmeticError):  # progress never fails the audit
            return

    def _write(self, line: str) -> None:
        self.lines += 1
        if self.stderr:
            print(line, file=sys.stderr, flush=True)
        if self.log is not None:
            self.log.write(line + "\n")
            self.log.flush()
