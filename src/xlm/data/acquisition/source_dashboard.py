"""Live, metadata-only progress for source acquisition runs; never used for decisions.

Generalizes the Essential-Web dashboard: one frame per second on a terminal,
one line per 30 s otherwise, plus an append-only JSONL event log. Only
code-authored metadata is rendered (counts, rates, file names escaped as
JSON); corpus text never reaches it. The rate, ETA and duration helpers are the
Essential-Web ones, imported unchanged.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from xlm.data.acquisition.source_parquet import ScratchBudget
from xlm.data.sources.essential_web_progress import Rate, duration, percentage, pipeline_eta

#: Restart classes, judged as the transport and the worker will judge them.
SEALED_SKIP = "sealed_skip"
LOCAL_PROCESSING_RETRY = "local_processing_retry"
LOCAL_COMPLETE_REUSE = "local_complete_reuse"
RESUMABLE_PARTIAL = "resumable_partial"
FRESH_DOWNLOAD = "fresh_download"
RESTART_CLASSES = (
    SEALED_SKIP,
    LOCAL_PROCESSING_RETRY,
    LOCAL_COMPLETE_REUSE,
    RESUMABLE_PARTIAL,
    FRESH_DOWNLOAD,
)
RECENT_WINDOW_SECONDS = 60.0


class ObservedScratch(ScratchBudget):
    """A scratch budget that records which streams exist and their declared lengths."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.streamed: set[str] = set()
        self.declared: dict[str, int] = {}

    def reserve(self, key: str, amount: int, path: Path) -> bool:
        reserved = super().reserve(key, amount, path)
        if reserved:
            self.streamed.add(key)
        return reserved

    def shrink(self, key: str, amount: int) -> None:
        super().shrink(key, amount)
        self.declared[key] = amount


@dataclass
class Snapshot:
    source: str
    plan: str
    mode: str
    total_units: int
    sealed_units: int
    expected_bytes: int | None = None
    downloaded_bytes: int = 0
    transfer_bytes: int = 0
    requests: int = 0
    retries: int = 0
    processed_rows: int = 0
    known_rows: int | None = None
    work_rows: int = 0
    documents: int = 0
    rejected: int = 0
    canonical_bytes: int = 0
    target_canonical_bytes: int | None = None
    prior_canonical_bytes: int = 0
    downloading: list[str] = field(default_factory=list)
    processing: list[str] = field(default_factory=list)
    process_workers: int = 0
    backlog: int = 0
    failed: int = 0
    cancelled: int = 0
    scratch_used: int = 0
    scratch_cap: int = 0
    scratch_free: int | None = None
    durable_used: int = 0
    durable_free: int | None = None


class RecentRate:
    """Bytes per second over the last ``window`` seconds (no smoothing)."""

    def __init__(self, window: float = RECENT_WINDOW_SECONDS) -> None:
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def update(self, now: float, amount: int) -> float | None:
        self.samples.append((now, amount))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()
        first_time, first_amount = self.samples[0]
        if now <= first_time:
            return None
        return max(0, amount - first_amount) / (now - first_time)


class Dashboard:
    """TTY frames each second, a line each 30 s otherwise, and append-only events."""

    def __init__(self, stream: TextIO, log: Path, *, now: float | None = None) -> None:
        self.stream, self.log = stream, log
        self.tty = bool(getattr(stream, "isatty", lambda: False)())
        self.started = time.monotonic() if now is None else now
        self.last_render = -math.inf
        self.lines = 0
        self.download_rate, self.process_rate = Rate(), Rate()
        self.recent = RecentRate()
        self.recent_value: float | None = None

    def clear(self) -> None:
        if self.tty and self.lines:
            self.stream.write(f"\x1b[{self.lines}A\x1b[J")
            self.lines = 0

    def event(self, kind: str, *, now: float | None = None, **metadata: Any) -> None:
        now = time.monotonic() if now is None else now
        record = {"elapsed": duration(now - self.started), "event": kind, **metadata}
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, sort_keys=True) + "\n")
        self.clear()
        self.stream.write(f"[{record['elapsed']}] {kind} " + json.dumps(metadata) + "\n")
        self.stream.flush()

    def update(self, snapshot: Snapshot, *, now: float | None = None, force: bool = False) -> None:
        now = time.monotonic() if now is None else now
        self.download_rate.update(now, snapshot.transfer_bytes)
        self.process_rate.update(now, snapshot.work_rows)
        self.recent_value = self.recent.update(now, snapshot.transfer_bytes)
        if not force and now - self.last_render < (1 if self.tty else 30):
            return
        self.last_render = now
        lines = self.render(snapshot, now)
        self.clear()
        if self.tty:
            self.stream.write("\n".join(lines) + "\n")
            self.lines = len(lines)
        else:
            self.stream.write(" | ".join(lines) + "\n")
        self.stream.flush()

    def render(self, s: Snapshot, now: float) -> list[str]:
        eta = pipeline_eta(
            None if s.expected_bytes is None else max(0, s.expected_bytes - s.downloaded_bytes),
            None if s.known_rows is None else max(0, s.known_rows - s.processed_rows),
            self.download_rate,
            self.process_rate,
            now,
            complete=s.sealed_units == s.total_units,
        )
        current = self.download_rate.value / 1e6
        recent = (self.recent_value or 0.0) / 1e6
        reached = s.prior_canonical_bytes + s.canonical_bytes
        target = s.target_canonical_bytes
        yield_ratio = s.canonical_bytes / s.transfer_bytes if s.transfer_bytes else None
        lines = [
            f"{s.source} {s.plan} {s.mode}",
            f"UNITS {s.sealed_units}/{s.total_units} {percentage(s.sealed_units, s.total_units)} "
            f"remaining={s.total_units - s.sealed_units}",
            f"DOWNLOAD {s.downloaded_bytes:,}/"
            f"{s.expected_bytes if s.expected_bytes is not None else '?'} B "
            f"{percentage(s.downloaded_bytes, s.expected_bytes)} current={current:.1f} MB/s "
            f"recent={recent:.1f} MB/s requests={s.requests} retries={s.retries}",
            f"PROCESS {s.processed_rows:,}/{s.known_rows if s.known_rows is not None else '?'} "
            f"rows {percentage(s.processed_rows, s.known_rows)} "
            f"{self.process_rate.value:.0f} rows/s workers={s.process_workers} "
            f"backlog={s.backlog}",
            f"YIELD documents={s.documents:,} rejected={s.rejected:,} canonical="
            f"{s.canonical_bytes:,} B ({s.canonical_bytes / 4 / 1e6:.1f}M est. tokens) "
            f"per transferred byte={yield_ratio if yield_ratio is None else round(yield_ratio, 3)}",
            f"TARGET {reached:,}/{target if target is not None else '?'} canonical B "
            f"{percentage(reached, target)} (estimated tokens = bytes/4, not exact)",
            f"TIME elapsed={duration(now - self.started)} ETA~{duration(eta)}",
            f"ERRORS failed units={s.failed} cancelled={s.cancelled} retries={s.retries}",
            f"DISK scratch={s.scratch_used:,}/{s.scratch_cap:,} B free="
            f"{s.scratch_free if s.scratch_free is not None else '?'} B durable this plan="
            f"{s.durable_used:,} B free={s.durable_free if s.durable_free is not None else '?'} B",
            "ACTIVE download=" + self.names(s.downloading) + " process=" + self.names(s.processing),
        ]
        return lines

    @staticmethod
    def names(values: list[str]) -> str:
        # JSON escaping keeps source paths from injecting terminal control sequences.
        names = [json.dumps(Path(name).name, ensure_ascii=True) for name in values[:2]]
        return ",".join(names) + (f" +{len(values) - 2}" if len(values) > 2 else "") or "-"


def fatal_lines(
    failures: list[dict[str, Any]],
    cancelled: list[str],
    sealed: int,
    total: int,
    sealed_this_run: int,
    restart: dict[str, Any],
) -> list[str]:
    """ROOT FAILURE apart from its consequences, preserved work and what a restart does."""
    root = failures[0] if failures else {"key": "pipeline", "exception": None}
    codes = " ".join(
        f"{name}={root[name]}" for name in ("errno", "winerror") if root.get(name) is not None
    )
    lines = ["ROOT FAILURE", f"  {root['key']} {root.get('exception') or ''} {codes}".rstrip()]
    if root.get("site"):
        lines.append(f"  at {root['site']}")
    if root.get("reason"):
        lines.append(f"  {root['reason']}")
    lines += [
        "CANCELLED BECAUSE OF ROOT FAILURE",
        f"  {len(cancelled)} in-flight units" + (f": {', '.join(cancelled)}" if cancelled else ""),
    ]
    others = [f"{f['key']} {f.get('exception')}" for f in failures[1:]]
    if others:
        lines += ["OTHER FAILURES", f"  {len(others)}: {', '.join(others)}"]
    counts = restart["counts"]
    lines += [
        "PRESERVED WORK",
        f"  {sealed} / {total} units sealed ({percentage(sealed, total)}); "
        f"{sealed_this_run} sealed in this run",
        f"  {restart['resumable_verified_bytes']:,} verified partial bytes kept on scratch",
        "RESTART CLASSIFICATION",
        *[f"  {name}: {counts[name]}" for name in RESTART_CLASSES],
        "  resume-check re-verifies every class before a run; sealed units are never redone",
    ]
    return lines


def free_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    return shutil.disk_usage(probe).free
