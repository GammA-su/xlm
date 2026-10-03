"""Content-free live C05 progress on stderr; never artifact, state or identity input.

Only fixed stage names, unit names and numeric counters are printed: no document
text, tokens, identifiers, paths, signatures, benchmark rows or provenance. The
values are type-checked so a caller cannot pass a string through a counter.

ETA math: a stage's rate is a rolling rate over the last ``window`` seconds of
samples (taken at most once per second). An ETA is shown only when the stage has
an exact denominator and the window holds at least ``min_span`` seconds of
samples; otherwise ``--:--:--``. Every stage transition resets the estimator and
forces a line, so startup averages never leak into a later stage.
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Final, Literal, TextIO

from xlm.data.exclusion.policy import C05Error

GiB: Final = 1024**3
MiB: Final = 1024**2
Format = Literal["text", "jsonl"]


def clock(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return "--:--:--"
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


class RollingRate:
    """Rate over a bounded recent window of (time, count) samples."""

    def __init__(self, window: float = 30.0, min_span: float = 5.0) -> None:
        if not 0 < min_span <= window:
            raise C05Error("progress rate window must contain its minimum span")
        self.window, self.min_span = window, min_span
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, now: float, count: float) -> None:
        if self.samples and now - self.samples[-1][0] < 1.0:
            self.samples[-1] = (self.samples[-1][0], count)
        else:
            self.samples.append((now, count))
        while len(self.samples) > 2 and now - self.samples[1][0] >= self.window:
            self.samples.popleft()

    def rate(self) -> float | None:
        if len(self.samples) < 2:
            return None
        (start, first), (end, last) = self.samples[0], self.samples[-1]
        span = end - start
        if span < self.min_span:
            return None
        return max(0.0, (last - first) / span)


class NullProgress:
    """``--no-progress``: every call is a no-op."""

    lines = 0

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        return None

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        return None

    def advance(self, amount: int = 1, **fields: Any) -> None:
        return None

    def finish(self, **fields: Any) -> None:
        return None

    def complete(self, **fields: Any) -> None:
        return None

    def attach(self, telemetry: Callable[[], Mapping[str, int | float]]) -> None:
        return None


class RunProgress:
    """Rate-limited stage progress with rolling-rate ETA and sampled telemetry."""

    def __init__(
        self,
        *,
        interval: float = 1.0,
        stream: TextIO | None = None,
        clock_fn: Callable[[], float] = time.monotonic,
        fmt: Format = "text",
        telemetry: Callable[[], Mapping[str, int | float]] | None = None,
        telemetry_interval: float = 5.0,
        window: float = 30.0,
        min_span: float = 10.0,
        label: str = "C05",
    ) -> None:
        if not 0 < interval <= 3600:
            raise C05Error("progress interval must be in (0, 3600] seconds")
        if fmt not in ("text", "jsonl"):
            raise C05Error("progress format must be text or jsonl")
        self.interval, self.stream, self.clock, self.fmt = interval, stream, clock_fn, fmt
        self.telemetry_source = telemetry
        self.telemetry_interval = telemetry_interval
        self.window, self.min_span = window, min_span
        self.label = label
        self.started = clock_fn()
        self.name: str | None = None
        self.total: int | None = None
        self.unit = "docs"
        self.done = 0
        self.fields: dict[str, int | float] = {}
        self.stage_started = self.started
        self.rate = RollingRate(window, min_span)
        self.last_line: float | None = None
        self.last_telemetry: float | None = None
        self.telemetry: dict[str, int | float] = {}
        self.lines = 0

    def attach(self, telemetry: Callable[[], Mapping[str, int | float]]) -> None:
        """Content-free resource telemetry, sampled every ``telemetry_interval``."""
        self.telemetry_source = telemetry
        self.last_telemetry = None

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        if self.name is not None:
            self.finish()
        now = self.clock()
        self.name, self.total, self.unit = name, total, unit
        self.done = 0
        self.fields = {}
        self.stage_started = now
        self.rate = RollingRate(self.window, self.min_span)
        self.rate.add(now, 0)
        self._emit(now, event="stage")

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        if done is not None:
            self.done = int(done)
        for key, value in fields.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise C05Error("progress fields must be numeric")
            self.fields[key] = value
        now = self.clock()
        self.rate.add(now, self.done)
        if force or self.last_line is None or now - self.last_line >= self.interval:
            self._emit(now, event="progress")

    def advance(self, amount: int = 1, **fields: Any) -> None:
        self.update(self.done + amount, **fields)

    def finish(self, **fields: Any) -> None:
        if self.name is None:
            return
        self.update(force=False, **fields)
        self._emit(self.clock(), event="finish")

    def complete(self, **fields: Any) -> None:
        self.finish(**fields)
        self.name, self.total = "COMPLETE", None
        self.done, self.fields = 0, {}
        self._emit(self.clock(), event="complete")
        self.name = None

    # -- formatting ---------------------------------------------------------------

    def _sample_telemetry(self, now: float) -> None:
        if self.telemetry_source is None:
            return
        if self.last_telemetry is None or now - self.last_telemetry >= self.telemetry_interval:
            self.last_telemetry = now
            values = dict(self.telemetry_source())
            for value in values.values():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise C05Error("progress telemetry must be numeric")
            self.telemetry = values

    def snapshot(self, now: float) -> dict[str, Any]:
        elapsed = now - self.stage_started
        rolling = self.rate.rate()
        lifetime = self.done / elapsed if elapsed > 0 else None
        remaining = None if self.total is None else max(0, self.total - self.done)
        eta = None
        if remaining == 0:
            eta = 0.0
        elif remaining is not None and rolling:
            eta = remaining / rolling
        return {
            "stage": self.name,
            "unit": self.unit,
            "done": self.done,
            "total": self.total,
            "percent": None if not self.total else 100.0 * self.done / self.total,
            "rolling_rate": rolling,
            "lifetime_rate": lifetime,
            "stage_seconds": elapsed,
            "run_seconds": now - self.started,
            "eta_seconds": eta,
            "fields": dict(self.fields),
            "telemetry": dict(self.telemetry),
        }

    def _emit(self, now: float, *, event: str) -> None:
        self._sample_telemetry(now)
        self.last_line = now
        snap = self.snapshot(now)
        if self.fmt == "jsonl":
            line = json.dumps({"event": event, **snap}, sort_keys=True)
        else:
            line = render(snap, event, self.label)
        print(line, file=self.stream or sys.stderr, flush=True)
        self.lines += 1


_FIELD_LABELS: Final = {
    "committed": "committed {:,} docs",
    "files_committed": "files {:,}",
    "comparisons": "comparisons {:,}",
    "oversized": "oversized {:,}",
    "capped": "cap-docs {:,}",
    "kept": "kept {:,}",
    "excluded": "excluded {:,}",
    "duplicates": "duplicates {:,}",
    "runs": "runs {:,}",
    "passes": "passes {:,}",
    "edges": "edges {:,}",
    "families": "families {:,}",
    "candidates": "candidates {:,}",
}


def render(snap: Mapping[str, Any], event: str, label: str = "C05") -> str:
    """One compact text line (numbers and fixed labels only)."""
    total = snap["total"]
    unit = snap["unit"]
    head = f"[{label}] {snap['stage']}"
    if event == "stage":
        head += " | started"
    elif event == "finish":
        head += " | done"
    parts = [head]
    if total is not None:
        parts.append(f"{snap['done']:,}/{total:,} {unit} ({snap['percent'] or 0.0:.2f}%)")
    elif snap["done"]:
        parts.append(f"{snap['done']:,} {unit}")
    fields = dict(snap["fields"])
    if "files_total" in fields:
        parts.append(f"files {fields.pop('files_committed', 0):,}/{fields.pop('files_total'):,}")
    if "bytes_total" in fields:
        done_bytes = fields.pop("bytes_done", 0)
        parts.append(f"{done_bytes / GiB:,.2f}/{fields.pop('bytes_total') / GiB:,.2f} GiB input")
    for key in list(fields):
        if key in _FIELD_LABELS:
            parts.append(_FIELD_LABELS[key].format(int(fields.pop(key))))
    rolling, lifetime = snap["rolling_rate"], snap["lifetime_rate"]
    if rolling is not None:
        parts.append(f"{rolling:,.0f} {unit}/s rolling")
    if lifetime is not None and snap["done"]:
        parts.append(f"{lifetime:,.0f} {unit}/s avg")
    if "mib_per_s" in fields:
        parts.append(f"{fields.pop('mib_per_s'):,.1f} MiB/s")
    if "workers" in fields:
        parts.append(f"workers {int(fields.pop('busy', 0))}/{int(fields.pop('workers'))} busy")
    if "tasks" in fields:
        capacity = int(fields.pop("capacity", 0))
        parts.append(
            f"tasks {int(fields.pop('tasks'))}/{capacity} results {int(fields.pop('results', 0))}"
        )
    if "written_bytes" in fields:
        parts.append(f"written {fields.pop('written_bytes') / MiB:,.1f} MiB")
    for key, value in sorted(fields.items()):
        parts.append(f"{key} {value:,}" if isinstance(value, int) else f"{key} {value:,.2f}")
    telemetry = snap["telemetry"]
    if "rss" in telemetry:
        parts.append(
            f"RSS {telemetry['rss'] / GiB:.1f}/{telemetry.get('ram_limit', 0) / GiB:.1f} GiB"
            f" (peak {telemetry.get('peak_rss', telemetry['rss']) / GiB:.1f})"
        )
    if "index" in telemetry:
        parts.append(
            f"index {telemetry['index'] / GiB:.2f}/{telemetry.get('index_limit', 0) / GiB:.1f} GiB"
        )
    if "scratch" in telemetry:
        parts.append(f"scratch {telemetry['scratch'] / GiB:.2f} GiB")
    if "free" in telemetry:
        parts.append(f"free {telemetry['free'] / GiB:.1f} GiB")
    parts.append(f"elapsed {clock(snap['stage_seconds'])} (run {clock(snap['run_seconds'])})")
    parts.append(f"ETA {clock(snap['eta_seconds'])}")
    return " | ".join(parts)


Progress = RunProgress | NullProgress
