"""Intra-file row-group parallelism for local whole-file Parquet adaptation.

One verified local Parquet file is split into its row groups. Worker processes
open the same file independently (never a copy), each decodes and adapts one
row group, and returns compact ordered events plus the bytes it produced. The
coordinator consumes results strictly in row-group order, so nothing that is
written depends on worker completion order, and replays each row through the
same bounds and writer as the serial path. Workers never write output files.

Concurrency is opt-in and plan-bound (:class:`RowGroupParallel`): a per-file
worker count, a global slot budget shared with file-level concurrency, a
bounded reorder window and a sampled process-tree memory ceiling.
"""

from __future__ import annotations

import ast
import inspect
import multiprocessing
import os
import textwrap
import time
from array import array
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import psutil
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Coordinator poll interval while it waits for the head-of-line row group.
POLL_SECONDS = 0.25
#: Membership refresh of the sampled process tree (workers start on demand).
CHILD_REFRESH_SECONDS = 2.0
#: Adapters whose ``adapt`` is a pure function of one record and its locator.
#: Essential-Web adapters are excluded: that campaign is frozen on its own
#: pipeline. An adapter outside this set refuses intra-file parallelism.
ROW_INDEPENDENT_ADAPTERS = frozenset(
    {
        "nemotron_organic",
        "ultrax_ultrafineweb",
        "synth_en",
        "wiki_rewrite",
        "finewiki_en",
        "finepdfs_en",
        "ifm_general",
        "ifm_planning",
        "common_pile",
        "simple_stories",
        "txt360_web",
    }
)
DOCUMENT, REJECTION = 0, 1


class RowGroupError(RuntimeError):
    """Intra-file parallel processing cannot run or a worker failed outside a record."""


class ProcessingMemoryError(RowGroupError):
    """The sampled resident memory of one file's process tree exceeded its ceiling."""


class RowGroupParallel(BaseModel):
    """Plan-bound intra-file concurrency of one source view (absent means serial).

    ``workers`` row-group processes serve one file; at most ``workers +
    lookahead`` row groups are submitted and not yet merged. Across concurrent
    files ``process_workers * (workers + 1) <= processing_slots``: each file's
    coordinator holds a slot. ``memory_bytes``
    bounds the sampled resident set of one file's coordinator plus workers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: int = Field(default=1, ge=1, le=1)
    workers: int = Field(ge=2, le=16)
    lookahead: int = Field(ge=0, le=16)
    processing_slots: int = Field(ge=2, le=16)
    memory_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _fits_one_file(self) -> RowGroupParallel:
        if self.slots_per_file > self.processing_slots:
            raise ValueError("one file's coordinator and workers exceed the processing slots")
        return self

    @property
    def slots_per_file(self) -> int:
        # The coordinator replays, writes and hashes: it holds a slot too.
        return self.workers + 1

    def max_process_workers(self) -> int:
        return self.processing_slots // self.slots_per_file


def configured(limits: Mapping[str, Any]) -> RowGroupParallel | None:
    value = limits.get("row_group_parallel")
    return None if value is None else RowGroupParallel.model_validate(value)


def check_concurrency(process_workers: int, limits: Mapping[str, Any]) -> None:
    """File-level times intra-file workers must stay within the global slot budget.

    Inline processing (``process_workers=0``) still runs one file at a time.
    """
    config = configured(limits)
    if config is not None and max(1, process_workers) * config.slots_per_file > (
        config.processing_slots
    ):
        raise RowGroupError(
            f"{process_workers} file processes x ({config.workers} row-group workers + 1 "
            "coordinator) exceed "
            f"the {config.processing_slots} authorized processing slots"
        )


def check_adapter(adapter_id: str) -> None:
    if adapter_id not in ROW_INDEPENDENT_ADAPTERS:
        raise RowGroupError(f"adapter '{adapter_id}' is not declared row-independent")


def adapt_assigns_state(adapter: type) -> bool:
    """True when ``adapter.adapt`` assigns an attribute or declares global state."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(adapter, "adapt"))))  # noqa: B009
    for node in ast.walk(tree):
        if isinstance(node, ast.Global | ast.Nonlocal):
            return True
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            for part in ast.walk(target):
                if (
                    isinstance(part, ast.Attribute | ast.Subscript)
                    and isinstance(part.value, ast.Name)
                    and part.value.id in ("self", "cls")
                ):
                    return True
    return False


@dataclass(frozen=True)
class SourceStat:
    """What the coordinator saw; every worker must see the same file."""

    path: str
    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> SourceStat:
        stat = path.stat()
        return cls(str(path), stat.st_size, stat.st_mtime_ns)

    def check(self) -> None:
        stat = os.stat(self.path)
        if (stat.st_size, stat.st_mtime_ns) != (self.size, self.mtime_ns):
            raise RowGroupError("source file changed during intra-file processing")


@dataclass(frozen=True)
class GroupTask:
    """One row group of one verified local file and every bound its worker applies."""

    source: SourceStat
    group: int
    base: int
    rows: int
    row_range: tuple[int, int]
    logical: tuple[str, ...]
    source_file: str
    source_id: str
    revision: str
    adapter_id: str
    locator: dict[str, Any]
    etag: str
    max_record_bytes: int
    max_parser_bytes: int
    max_decoded_bytes: int


@dataclass
class GroupResult:
    """Ordered events of one row group; no file was written to produce it.

    ``batches`` holds ``(decoded bytes, rows completed)`` per decoded batch and
    ``kinds`` one :data:`DOCUMENT`/:data:`REJECTION` byte per completed row.
    ``error`` is the exception that stopped the group after those rows.
    """

    group: int
    batches: list[tuple[int, int]] = field(default_factory=list)
    kinds: bytearray = field(default_factory=bytearray)
    payloads: bytes = b""
    max_payload: int = 0
    documents: bytes = b""
    document_lengths: array[int] = field(default_factory=lambda: array("q"))
    document_text_bytes: array[int] = field(default_factory=lambda: array("q"))
    rejections: bytes = b""
    rejection_lengths: array[int] = field(default_factory=lambda: array("q"))
    rejection_codes: list[str] = field(default_factory=list)
    error: BaseException | None = None
    cpu_seconds: float = 0.0
    peak_rss_bytes: int = 0
    pid: int = 0

    def result_bytes(self) -> int:
        return len(self.payloads) + len(self.documents) + len(self.rejections)


def peak_rss() -> int:
    """This process's peak resident set where the platform reports one, else current."""
    info = psutil.Process().memory_info()
    return int(getattr(info, "peak_wset", 0) or info.rss)


def _init_worker() -> None:
    # One decode thread per worker: concurrency is the plan's worker count.
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)


@dataclass
class PoolStats:
    workers: int
    groups: int = 0
    peak_tree_rss_bytes: int = 0
    worker_peak_rss_bytes: dict[int, int] = field(default_factory=dict)
    worker_cpu_seconds: float = 0.0
    wait_seconds: float = 0.0
    peak_outstanding: int = 0
    peak_buffered_result_bytes: int = 0
    max_group_result_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "workers": self.workers,
            "groups": self.groups,
            "peak_tree_rss_bytes": self.peak_tree_rss_bytes,
            "max_worker_peak_rss_bytes": max(self.worker_peak_rss_bytes.values(), default=0),
            "sum_worker_peak_rss_bytes": sum(self.worker_peak_rss_bytes.values()),
            "worker_cpu_seconds": self.worker_cpu_seconds,
            "head_of_line_wait_seconds": self.wait_seconds,
            "peak_outstanding_groups": self.peak_outstanding,
            "peak_buffered_result_bytes": self.peak_buffered_result_bytes,
            "max_group_result_bytes": self.max_group_result_bytes,
        }


class RowGroupPool:
    """Ordered results of row-group tasks from a bounded spawn-context process pool.

    Use as a context manager: leaving it, normally or by an exception, cancels
    queued groups and waits for the at most ``workers + 1`` already handed to
    worker processes, so no worker outlives the file it served.
    """

    def __init__(
        self,
        config: RowGroupParallel,
        tasks: list[GroupTask],
        worker: Callable[[GroupTask], GroupResult],
        *,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.config, self.tasks, self.worker = config, tasks, worker
        self.poll_seconds = poll_seconds
        self.stats = PoolStats(workers=min(config.workers, len(tasks)))
        self._pool: ProcessPoolExecutor | None = None
        self._outstanding: deque[Future[GroupResult]] = deque()
        self._root = psutil.Process()
        # Enumerating children walks every process of the machine on Windows;
        # it is refreshed at most every CHILD_REFRESH_SECONDS, members each poll.
        self._members: list[psutil.Process] = []
        self._refreshed = float("-inf")

    def __enter__(self) -> RowGroupPool:
        self._pool = ProcessPoolExecutor(
            self.stats.workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_worker,
        )
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for future in self._outstanding:
            future.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
        self._outstanding.clear()

    def _tree_rss(self) -> int:
        now = time.monotonic()
        starting = len(self._members) < self.stats.workers
        if now - self._refreshed >= (POLL_SECONDS if starting else CHILD_REFRESH_SECONDS):
            try:
                self._members = self._root.children(recursive=True)
            except psutil.Error:
                self._members = []
            self._refreshed = now
        total = 0
        for member in [self._root, *self._members]:
            try:
                total += int(member.memory_info().rss)
            except psutil.Error:
                continue
        return total

    def sample(self) -> None:
        rss = self._tree_rss()
        self.stats.peak_tree_rss_bytes = max(self.stats.peak_tree_rss_bytes, rss)
        if rss > self.config.memory_bytes:
            raise ProcessingMemoryError(
                f"process tree resident memory {rss} exceeds its {self.config.memory_bytes} ceiling"
            )

    def _buffered(self) -> int:
        total = 0
        for future in self._outstanding:
            if future.done() and not future.cancelled() and future.exception() is None:
                total += future.result().result_bytes()
        return total

    def results(self) -> Iterator[GroupResult]:
        if self._pool is None:
            raise RowGroupError("row-group pool is not open")
        window = self.stats.workers + self.config.lookahead
        submitted = 0
        while submitted < len(self.tasks) or self._outstanding:
            while submitted < len(self.tasks) and len(self._outstanding) < window:
                self._outstanding.append(self._pool.submit(self.worker, self.tasks[submitted]))
                submitted += 1
            self.stats.peak_outstanding = max(self.stats.peak_outstanding, len(self._outstanding))
            head = self._outstanding[0]
            started = time.monotonic()
            while True:
                done, _ = wait([head], timeout=self.poll_seconds, return_when=FIRST_COMPLETED)
                self.sample()
                if done:
                    break
            self.stats.wait_seconds += time.monotonic() - started
            self.stats.peak_buffered_result_bytes = max(
                self.stats.peak_buffered_result_bytes, self._buffered()
            )
            self._outstanding.popleft()
            try:
                result = head.result()
            except BrokenProcessPool as exc:
                raise RowGroupError("a row-group worker process terminated abnormally") from exc
            expected = self.tasks[self.stats.groups].group
            if result.group != expected:
                raise RowGroupError(f"row group {result.group} arrived where {expected} belongs")
            self.stats.groups += 1
            self.stats.worker_cpu_seconds += result.cpu_seconds
            self.stats.max_group_result_bytes = max(
                self.stats.max_group_result_bytes, result.result_bytes()
            )
            if result.pid:
                self.stats.worker_peak_rss_bytes[result.pid] = max(
                    self.stats.worker_peak_rss_bytes.get(result.pid, 0), result.peak_rss_bytes
                )
            yield result
