"""Bounded parallel decode/render/pattern work for protected benchmark preparation.

Workers are spawn-safe child processes: CPU-bound render/tokenize/pattern code
holds the GIL, so threads would not scale. Children receive only paths, the
content-free material entry and the matcher policy (pickled over the spawn
pipe, never the command line), write no files, and return small batches over a
bounded in-memory queue to the single SQLite writer in the parent. Every
aggregate is order-independent, so scheduling cannot change artifacts.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from multiprocessing.synchronize import Barrier
from pathlib import Path
from typing import Any, TextIO

import psutil

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import MaterialFile
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, MatcherPolicyV4, matcher_policy
from xlm.data.exclusion.streaming import item_patterns

BATCH_ROWS = 32  # rows per result batch; patterns per row are policy-bounded
QUEUED_BATCHES_PER_WORKER = 2  # result-queue capacity is workers x this
POLL_SECONDS = 0.25  # writer re-checks ceilings at least this often while waiting
STOP_SECONDS = 10.0

# (item hash, variant count, raw candidate emissions, fallback used,
#  ((pattern identity, canonical tokens, provenance), ...) after exact per-item dedup)
RowResult = tuple[str, int, int, bool, tuple[tuple[str, str, tuple[str, ...]], ...]]


@dataclass(frozen=True)
class Task:
    number: int
    file: int
    path: str
    entry: dict[str, Any]
    row_group: int | None  # None: one streamed JSONL file
    first: int  # 1-based in-file count of the first row (provenance reference)
    rows: int  # exact rows of a Parquet row group; item ceiling of a JSONL file


class ProcessTree:
    """Parent plus all descendant RSS: the RAM ceiling is global to the build.

    Descendant discovery walks the OS process table (slow on Windows), so it is
    refreshed at most every ``refresh`` seconds; every known process (including
    venv-launcher grandchildren) is re-sampled on each call.
    """

    def __init__(self, refresh: float = 1.0) -> None:
        self.parent = psutil.Process()
        self.refresh = refresh
        self.known: list[psutil.Process] = []
        self.scanned: float | None = None

    def watch(self, pid: int) -> None:
        """A worker announced itself before any decoding: rescan on the next sample."""
        if pid not in {child.pid for child in self.known}:
            self.scanned = None

    def rss(self) -> int:
        now = time.monotonic()
        if self.scanned is None or now - self.scanned >= self.refresh:
            self.known = self.parent.children(recursive=True)
            self.scanned = now
        total = self.parent.memory_info().rss
        for child in self.known:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total


def plan_tasks(paths: Sequence[Path], files: Sequence[MaterialFile], max_record: int) -> list[Task]:
    """One task per Parquet row group (validated from the footer) or JSONL file."""
    import pyarrow.parquet as pq

    tasks: list[Task] = []
    for number, (path, entry) in enumerate(zip(paths, files, strict=True)):
        dumped = entry.model_dump(mode="json")
        if entry.format == "jsonl":
            tasks.append(Task(len(tasks), number, str(path), dumped, None, 1, entry.items))
            continue
        with pq.ParquetFile(path) as parquet:
            if parquet.metadata.num_rows != entry.items:
                raise C05Error("benchmark parquet item count mismatch")
            first = 1
            for group in range(parquet.metadata.num_row_groups):
                meta = parquet.metadata.row_group(group)
                # Refuse large row groups rather than silently widening memory bounds.
                if meta.total_byte_size > max_record:
                    raise C05Error("benchmark parquet decompressed row-group ceiling")
                tasks.append(
                    Task(len(tasks), number, str(path), dumped, group, first, meta.num_rows)
                )
                first += meta.num_rows
    return tasks


def _rows(task: Task, max_record: int, check: Callable[[], None]) -> Iterator[dict[str, Any]]:
    path = Path(task.path)
    if task.row_group is None:
        count = 0
        with path.open("rb") as stream:
            while raw := stream.readline(max_record + 1):
                check()
                count += 1
                if count > task.rows:
                    raise C05Error("benchmark material item ceiling")
                if len(raw) > max_record:
                    raise C05Error("benchmark material record ceiling")
                row = canonical.loads_bytes_strict(raw)
                if not isinstance(row, dict):
                    raise C05Error("benchmark row must be an object")
                yield row
        return
    import pyarrow.parquet as pq

    with pq.ParquetFile(path) as parquet:
        meta = parquet.metadata.row_group(task.row_group)
        if meta.num_rows != task.rows or meta.total_byte_size > max_record:
            raise C05Error("benchmark parquet row group changed during preparation")
        # The decompressed row group is bounded by max_record (validated above).
        decoded = parquet.read_row_group(task.row_group, use_threads=False).to_pylist()
    if len(decoded) != task.rows:
        raise C05Error("benchmark parquet item count mismatch")
    for row in decoded:
        check()
        if len(canonical.canonical_bytes(row)) > max_record:
            raise C05Error("benchmark material record ceiling")
        yield row


def task_batches(
    task: Task,
    policy: MatcherPolicy | MatcherPolicyV4,
    max_record: int,
    check: Callable[[], None],
) -> Iterator[list[RowResult]]:
    """Render, hash and derive patterns for one task in batches of BATCH_ROWS rows."""
    batch: list[RowResult] = []
    for offset, row in enumerate(_rows(task, max_record, check)):
        reference = canonical.digest([task.entry, task.first + offset])
        rendered, unique, raw, fallback = item_patterns(task.entry["task"], row, reference, policy)
        found = tuple(
            (
                canonical.digest(p.tokens),
                canonical.canonical_bytes(p.tokens).decode(),
                p.provenance,
            )
            for p in unique
        )
        # The item hash keeps its historical v3 definition (task + rendered variants).
        item = canonical.digest([task.entry["task"], rendered])
        batch.append((item, len(rendered), raw, fallback, found))
        if len(batch) >= BATCH_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def _no_check() -> None:
    return None


def _worker(
    tasks: Any,
    results: Any,
    policy_json: dict[str, Any],
    max_record: int,
    barrier: Barrier | None,
) -> None:
    """Child entry point; reports only an exception type and a fixed C05 reason."""
    try:
        policy = matcher_policy(policy_json)
        results.put(("ready", os.getpid()))
        if barrier is not None:
            barrier.wait(60)
        while (task := tasks.get()) is not None:
            rows = 0
            for batch in task_batches(task, policy, max_record, _no_check):
                rows += len(batch)
                results.put(("batch", task.number, batch))
            results.put(("done", task.number, rows, os.getpid()))
    except KeyboardInterrupt:
        return
    except BaseException as exc:  # noqa: BLE001 - every child failure fails the build
        # C05Error reasons are fixed authored strings; other messages may hold text.
        reason = str(exc)[:200] if isinstance(exc, C05Error) else ""
        results.put(("error", type(exc).__name__, reason))


Event = tuple[Any, ...]


def run_tasks(
    tasks: Sequence[Task],
    *,
    workers: int,
    policy: MatcherPolicy | MatcherPolicyV4,
    max_record: int,
    check: Callable[[], None],
    barrier: bool = False,
) -> Generator[Event, None, None]:
    """Yield ("batch", task, rows), ("done", task, rows, pid) and ("ready", pid).

    ``workers == 1`` runs in-process with per-row ceiling checks: no children.
    Otherwise ``min(workers, len(tasks))`` spawned children feed a result queue of
    capacity ``QUEUED_BATCHES_PER_WORKER * n``; the caller (single writer) checks
    ceilings per batch and this loop re-checks at least every POLL_SECONDS.
    Any failure, ceiling refusal or interrupt terminates every child.
    """
    if workers == 1:
        for task in tasks:
            check()
            rows = 0
            for batch in task_batches(task, policy, max_record, check):
                rows += len(batch)
                yield ("batch", task.number, batch)
            yield ("done", task.number, rows, os.getpid())
        return
    count = min(workers, len(tasks))
    context = multiprocessing.get_context("spawn")
    task_queue = context.Queue()
    result_queue = context.Queue(maxsize=QUEUED_BATCHES_PER_WORKER * count)
    gate = context.Barrier(count) if barrier else None
    children = [
        context.Process(
            target=_worker,
            args=(task_queue, result_queue, policy.model_dump(mode="json"), max_record, gate),
            name=f"c05-prepare-{number}",
            daemon=True,
        )
        for number in range(count)
    ]
    try:
        for child in children:
            child.start()
        for task in tasks:
            task_queue.put(task)
        for _ in children:
            task_queue.put(None)
        pending = len(tasks)
        while pending:
            check()
            try:
                event = result_queue.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if any(child.exitcode not in (None, 0) for child in children) or all(
                    child.exitcode is not None for child in children
                ):
                    raise C05Error("protected preparation worker exited abnormally") from None
                continue
            if event[0] == "error":
                raise C05Error(f"protected preparation worker failed: {event[1]} {event[2]}")
            pending -= event[0] == "done"
            yield event
        for child in children:
            child.join(STOP_SECONDS)
            if child.exitcode != 0:
                raise C05Error("protected preparation worker exited abnormally")
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
        for child in children:
            if child.pid is not None:
                child.join(STOP_SECONDS)
                if child.is_alive():
                    child.kill()
                    child.join(STOP_SECONDS)
        for channel in (task_queue, result_queue):
            channel.cancel_join_thread()
            channel.close()


class Progress:
    """Content-free, rate-limited operational progress on stderr; never artifact state.

    Emits only counts, rates and durations: no text, tokens, hashes or provenance.
    """

    def __init__(
        self,
        *,
        files: int,
        rows: int,
        workers: int,
        interval: float = 1.0,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < interval <= 3600:
            raise C05Error("progress interval must be in (0, 3600] seconds")
        self.files, self.rows, self.workers = files, rows, workers
        self.interval, self.stream, self.clock = interval, stream, clock
        self.started = clock()
        self.last: float | None = None
        self.lines = 0

    def update(
        self,
        phase: str,
        *,
        files: int = 0,
        rows: int = 0,
        patterns: int = 0,
        active: int = 0,
        fallback: int = 0,
        force: bool = False,
    ) -> None:
        now = self.clock()
        if not force and self.last is not None and now - self.last < self.interval:
            return
        self.last = now
        elapsed = now - self.started
        rate = rows / elapsed if elapsed > 0 else 0.0
        eta = (self.rows - rows) / rate if rate > 0 else None
        percent = 100.0 * rows / self.rows if self.rows else 100.0
        line = (
            f"[C05 prepare] {phase} | files {files}/{self.files} | "
            f"rows {rows:,}/{self.rows:,} ({percent:.1f}%) | patterns {patterns:,} | "
            f"fallback items {fallback:,} | "
            f"workers {active}/{self.workers} | {rate:,.0f} rows/s | "
            f"elapsed {_clock(elapsed)} | ETA {_clock(eta) if eta is not None else '--:--:--'}"
        )
        print(line, file=self.stream or sys.stderr, flush=True)
        self.lines += 1


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
