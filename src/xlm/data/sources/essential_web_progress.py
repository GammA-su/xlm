"""Bounded, metadata-only live progress; never used for campaign decisions."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO


def percentage(done: int | float, total: int | float | None) -> str:
    return "?" if total is None or total <= 0 else f"{100 * done / total:.1f}%"


@dataclass
class Rate:
    """Time-weighted 15-second EWMA, including zero-work (stall) samples."""

    last_time: float | None = None
    last_value: int = 0
    value: float = 0.0
    samples: int = 0
    last_work: float | None = None

    def update(self, now: float, amount: int) -> None:
        if self.last_time is not None and now > self.last_time:
            delta = max(0, amount - self.last_value)
            dt = now - self.last_time
            rate = delta / dt
            alpha = 1 - math.exp(-dt / 15)
            self.value = rate if self.samples == 0 else alpha * rate + (1 - alpha) * self.value
            self.samples += 1
            if delta:
                self.last_work = now
        self.last_time, self.last_value = now, amount

    def usable(self, now: float) -> bool:
        return (
            self.samples >= 2
            and self.value > 0
            and self.last_work is not None
            and now - self.last_work < 15
        )


def pipeline_eta(
    remaining_bytes: int | None,
    remaining_rows: int | None,
    download: Rate,
    processing: Rate,
    now: float,
    *,
    complete: bool = False,
) -> float | None:
    """Critical stage, not a sum: rows include backlog and future downloads."""
    if complete:
        return 0.0
    estimates = []
    for amount, rate in ((remaining_bytes, download), (remaining_rows, processing)):
        if amount is None:
            return None
        if amount > 0:
            if not rate.usable(now):
                return None
            estimates.append(amount / rate.value)
    return max(estimates, default=0.0)


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculating..."
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


@dataclass
class Snapshot:
    batch: int
    campaign: str
    mode: str
    total: int
    sealed: int
    downloaded: int = 0
    expected_bytes: int | None = None
    transfer_bytes: int = 0
    processed: int = 0
    known_rows: int | None = None
    work_rows: int = 0
    downloading: list[str] = field(default_factory=list)
    processing: list[str] = field(default_factory=list)
    process_workers: int = 0
    backlog: int = 0
    retries: int = 0
    malformed: int = 0
    failed: int = 0
    cancelled: int = 0
    scratch: int = 0
    scratch_cap: int = 0
    durable: int = 0
    free: int | None = None
    views: dict[str, dict[str, Any]] = field(default_factory=dict)
    targets: dict[str, int] = field(default_factory=dict)


class Dashboard:
    """One-second TTY frames; 30-second durable fallback and append-only events."""

    def __init__(self, stream: TextIO, log: Path, *, now: float | None = None) -> None:
        self.stream, self.log = stream, log
        self.tty = stream.isatty()
        self.started = time.monotonic() if now is None else now
        self.last_render = -math.inf
        self.lines = 0
        self.download_rate, self.process_rate = Rate(), Rate()

    def clear(self) -> None:
        if self.tty and self.lines:
            self.stream.write(f"\x1b[{self.lines}A\x1b[J")
            self.lines = 0

    def event(self, kind: str, *, now: float | None = None, **metadata: Any) -> None:
        now = time.monotonic() if now is None else now
        # Only caller-authored metadata is accepted; never exception/source text.
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
            None if s.expected_bytes is None else max(0, s.expected_bytes - s.downloaded),
            None if s.known_rows is None else max(0, s.known_rows - s.processed),
            self.download_rate,
            self.process_rate,
            now,
            complete=s.sealed == s.total,
        )
        rate = self.download_rate.value / 1e6
        lines = [
            f"Essential-Web Batch {s.batch} {s.mode} {s.campaign[:12]}",
            f"FILES {s.sealed}/{s.total} {percentage(s.sealed, s.total)} "
            f"remaining={s.total - s.sealed}",
            f"DOWNLOAD {s.downloaded:,}/"
            f"{s.expected_bytes if s.expected_bytes is not None else '?'} B "
            f"{percentage(s.downloaded, s.expected_bytes)} {rate:.1f} MB/s {rate * 8:.1f} Mbit/s "
            f"workers={len(s.downloading)} retries={s.retries}",
            f"PROCESS {s.processed:,}/{s.known_rows if s.known_rows is not None else '?'} rows "
            f"{percentage(s.processed, s.known_rows)} {self.process_rate.value:.0f} rows/s "
            f"workers={s.process_workers} backlog={s.backlog}",
            f"TIME elapsed={duration(now - self.started)} ETA~{duration(eta)}",
        ]
        for view, target in s.targets.items():
            value = s.views.get(view, {})
            tokens = float(value.get("canonical_bytes", 0)) / 4
            lines.append(
                f"{view.removeprefix('essential_').upper():9} "
                f"docs={value.get('documents', 0):,} estimated tokens="
                f"{tokens / 1e6:.2f}M/{target / 1e6:.0f}M {percentage(tokens, target)}"
            )
        lines.extend(
            [
                f"ERRORS malformed={s.malformed} retries={s.retries} failed units={s.failed} "
                f"cancelled={s.cancelled}",
                f"DISK scratch={s.scratch:,}/{s.scratch_cap:,} B durable batch={s.durable:,} B "
                f"free={s.free if s.free is not None else '?'} B",
                "ACTIVE download="
                + self.names(s.downloading)
                + " process="
                + self.names(s.processing),
            ]
        )
        return lines

    @staticmethod
    def names(values: list[str]) -> str:
        # JSON escaping prevents source paths from injecting terminal control sequences.
        names = [json.dumps(Path(name).name, ensure_ascii=True) for name in values[:2]]
        return ",".join(names) + (f" +{len(values) - 2}" if len(values) > 2 else "") or "-"
