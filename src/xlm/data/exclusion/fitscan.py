"""Worker kernels for the C06 fast path: membership chunks and one-pass source files.

Every function here is a pure, picklable task over bytes or one plan file, so it can
run in-process (``workers=1``) or in a spawned worker; results are consumed by the
parent strictly in submission order (:class:`OrderedPool`). Nothing here publishes.

Source semantics are exactly ``selection.iter_plan_documents``: LF-delimited rows
(a final row may lack its LF), a row longer than the plan's ``document_bytes``
(LF included) refuses, every byte is hashed, and the SHA-256, byte size and row
count must equal the frozen plan entry.
"""

from __future__ import annotations

import hashlib
import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import TypeVar

import numpy as np
import numpy.typing as npt
import psutil

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.supervisor import Checkable, terminate_processes

SPLIT_CODES = {"train": 0, "diagnostic_val": 1, "audit": 2}
SPLIT_NAMES = ("train", "diagnostic_val", "audit")
NOT_PARSED = 255
MEMBERSHIP_KEYS = frozenset(
    {
        "doc_id",
        "source_id",
        "component",
        "view",
        "file",
        "row",
        "content",
        "bytes",
        "duplicate_group",
        "lineage_group",
        "decision",
        "split",
        "quick",
        "upstream_component",
    }
)
SOURCE_BLOCK_BYTES = 8 * 1024**2
POLL_SECONDS = 0.05
HEX = frozenset("0123456789abcdef")

T = TypeVar("T")
R = TypeVar("R")


# -- membership -----------------------------------------------------------------------


@dataclass(frozen=True)
class FileRef:
    ordinal: int
    allocation: int
    component: str
    view: str
    upstream: str | None
    source_id: str
    documents: int


@dataclass(frozen=True)
class MembershipTables:
    """Plan-derived lookup tables every membership worker needs (sent once)."""

    files: dict[str, FileRef]
    allocation_keys: tuple[str, ...]
    seed: int
    fit_document_cap: int
    line_ceiling: int
    rank_tag: str


@dataclass
class MembershipChunk:
    """Column arrays for one chunk of membership rows, in file order."""

    rows: int
    ids: bytes
    id_lengths: npt.NDArray[np.uint32]
    file: npt.NDArray[np.uint32]
    row: npt.NDArray[np.uint32]
    nbytes: npt.NDArray[np.uint64]
    content: npt.NDArray[np.uint8]
    split: npt.NDArray[np.uint8]
    allocation: npt.NDArray[np.uint16]
    rank: npt.NDArray[np.uint8]
    first_id: bytes
    last_id: bytes


_TABLES: MembershipTables | None = None
# Set only while an inline (in-process) pool runs; spawned workers are killed instead.
_CANCEL: Callable[[], None] | None = None


def _cancel_check() -> None:
    if _CANCEL is not None:
        _CANCEL()


def init_worker(tables: MembershipTables) -> None:
    """Pool initializer: tables for membership chunks; no Rayon pools in scan workers."""
    global _TABLES
    _TABLES = tables
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"


def rank_digest(tag: str, seed: int, allocation: str, doc_id: str, content: str) -> bytes:
    """Byte-identical to ``tokenizer_fit._rank`` (the frozen fit order)."""
    return hashlib.sha256(
        canonical.canonical_bytes([tag, seed, allocation, doc_id, content])
    ).digest()


def parse_membership_chunk(chunk: bytes) -> MembershipChunk:
    """Strictly parse and validate complete membership lines (LF-terminated or final)."""
    tables = _TABLES
    if tables is None:
        raise C05Error("membership worker tables missing")
    ids: list[bytes] = []
    lengths: list[int] = []
    files: list[int] = []
    rows: list[int] = []
    sizes: list[int] = []
    contents: list[bytes] = []
    splits: list[int] = []
    allocations: list[int] = []
    ranks: list[bytes] = []
    previous: bytes | None = None
    zero = bytes(32)
    start = 0
    end_of_chunk = len(chunk)
    while start < end_of_chunk:
        end = chunk.find(b"\n", start)
        stop = end_of_chunk if end < 0 else end + 1
        if stop - start > tables.line_ceiling:
            raise C05Error("membership record ceiling")
        row = canonical.loads_bytes_strict(chunk[start:stop])
        start = stop
        if len(ids) % 4096 == 4095:
            _cancel_check()
        if type(row) is not dict or row.keys() != MEMBERSHIP_KEYS:
            raise C05Error("membership record schema")
        doc_id, content, size = row["doc_id"], row["content"], row["bytes"]
        number, split = row["row"], row["split"]
        if type(doc_id) is not str or not doc_id:
            raise C05Error("membership document id type")
        if row["decision"] != "kept" or split not in SPLIT_CODES:
            raise C05Error("membership decision or split value")
        if type(content) is not str or len(content) != 64 or not HEX.issuperset(content):
            raise C05Error("membership content digest")
        if type(size) is not int or size < 0 or type(number) is not int or number < 1:
            raise C05Error("membership size or row value")
        if type(row["quick"]) is not bool or not all(
            type(row[name]) is str for name in ("duplicate_group", "lineage_group")
        ):
            raise C05Error("membership group field type")
        ref = tables.files.get(row["file"]) if type(row["file"]) is str else None
        if (
            ref is None
            or number > ref.documents
            or (row["component"], row["view"], row["upstream_component"], row["source_id"])
            != (ref.component, ref.view, ref.upstream, ref.source_id)
        ):
            raise C05Error("membership record outside its frozen plan file allocation")
        raw_id = doc_id.encode("utf-8")
        # C05 publishes in exact UTF-8 byte order of doc_id: strictly ascending
        # rows prove there is no repeated ID without retaining a seen-set.
        if previous is not None and raw_id <= previous:
            raise C05Error("membership is not in strictly ascending document id order")
        previous = raw_id
        ids.append(raw_id)
        lengths.append(len(raw_id))
        files.append(ref.ordinal)
        rows.append(number)
        sizes.append(size)
        contents.append(bytes.fromhex(content))
        code = SPLIT_CODES[split]
        splits.append(code)
        allocations.append(ref.allocation)
        if code == 0 and size <= tables.fit_document_cap:
            ranks.append(
                rank_digest(
                    tables.rank_tag,
                    tables.seed,
                    tables.allocation_keys[ref.allocation],
                    doc_id,
                    content,
                )
            )
        else:
            ranks.append(zero)
    count = len(ids)
    return MembershipChunk(
        rows=count,
        ids=b"".join(ids),
        id_lengths=np.asarray(lengths, dtype=np.uint32),
        file=np.asarray(files, dtype=np.uint32),
        row=np.asarray(rows, dtype=np.uint32),
        nbytes=np.asarray(sizes, dtype=np.uint64),
        content=np.frombuffer(b"".join(contents), dtype=np.uint8).reshape(count, 32),
        split=np.asarray(splits, dtype=np.uint8),
        allocation=np.asarray(allocations, dtype=np.uint16),
        rank=np.frombuffer(b"".join(ranks), dtype=np.uint8).reshape(count, 32),
        first_id=ids[0] if ids else b"",
        last_id=ids[-1] if ids else b"",
    )


# -- one source pass --------------------------------------------------------------------


@dataclass(frozen=True)
class SourceTask:
    """One frozen plan file and the kept rows it must account for (ascending rows)."""

    ordinal: int
    path: str
    sha256: str
    file_bytes: int
    documents: int
    line_ceiling: int
    rows: npt.NDArray[np.uint32]
    ids: list[bytes]
    nbytes: npt.NDArray[np.uint64]
    assigned: npt.NDArray[np.uint8]
    selected: npt.NDArray[np.bool_]
    selected_content: list[str]
    parse: bool = True
    block_bytes: int = 0


@dataclass
class SourceResult:
    ordinal: int
    sha256: str
    rows: int
    file_bytes: int
    offsets: npt.NDArray[np.uint64]
    lengths: npt.NDArray[np.uint32]
    original: npt.NDArray[np.uint8]
    selected: list[tuple[CanonicalDocument, str]] = field(default_factory=list)
    parsed: int = 0


def scan_source_file(task: SourceTask) -> SourceResult:
    """Hash every byte once; locate kept rows; strict-parse them; fully decode selected."""
    path = Path(task.path)
    if path.stat().st_size != task.file_bytes:
        raise C05Error("input file size changed since C05")
    wanted = task.rows.tolist()
    expect_bytes = task.nbytes.tolist()
    assigned = task.assigned.tolist()
    selected = task.selected.tolist()
    count = len(wanted)
    offsets = np.zeros(count, dtype=np.uint64)
    lengths = np.zeros(count, dtype=np.uint32)
    original = np.full(count, NOT_PARSED, dtype=np.uint8)
    result = SourceResult(task.ordinal, "", 0, 0, offsets, lengths, original)
    content_of = iter(task.selected_content)
    position = 0
    target = wanted[0] if count else 0
    ceiling = task.line_ceiling
    digest = hashlib.sha256()
    total = row = base = 0
    pending = b""

    def handle(line: bytes, offset: int) -> None:
        nonlocal position, target
        offsets[position] = offset
        lengths[position] = len(line)
        if task.parse:
            original[position] = _check_kept_line(
                line,
                task.ids[position],
                expect_bytes[position],
                assigned[position],
                next(content_of) if selected[position] else None,
                result,
            )
        position += 1
        target = wanted[position] if position < count else 0

    with path.open("rb", buffering=0) as stream:
        # Never request more than one byte past the frozen size: growth refuses at once.
        size = task.block_bytes or SOURCE_BLOCK_BYTES
        while block := stream.read(min(size, task.file_bytes - total + 1)):
            _cancel_check()
            digest.update(block)
            total += len(block)
            if total > task.file_bytes:
                raise C05Error("input content changed since C05 (read exceeds frozen bytes)")
            data = pending + block if pending else block
            start = 0
            while (end := data.find(b"\n", start)) >= 0:
                row += 1
                if row > task.documents:
                    raise C05Error("input content changed since C05 (rows exceed frozen count)")
                if end + 1 - start > ceiling:
                    raise C05Error("canonical record ceiling")
                if row == target:
                    handle(data[start : end + 1], base + start)
                start = end + 1
            pending = data[start:]
            base += start
            if len(pending) > ceiling:
                raise C05Error("canonical record ceiling")
    if pending:
        row += 1
        if row > task.documents:
            raise C05Error("input content changed since C05 (rows exceed frozen count)")
        if row == target:
            handle(pending, base)
    if (digest.hexdigest(), row, total) != (task.sha256, task.documents, task.file_bytes):
        raise C05Error("input content changed since C05")
    if position != count:
        raise C05Error("C05 membership row is outside its source file")
    result.sha256, result.rows, result.file_bytes = digest.hexdigest(), row, total
    return result


def _check_kept_line(
    line: bytes,
    doc_id: bytes,
    size: int,
    assigned: int,
    content: str | None,
    result: SourceResult,
) -> int:
    """Strict canonical parse of one kept row; returns its original split code.

    Unselected rows: the strict canonical JSON parser only (no dataclass, asdict,
    re-serialization or content digest). Selected rows: full CanonicalDocument and
    the exact C05 content digest.
    """
    try:
        value = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise C05Error("canonical record is not strict canonical JSON") from exc
    result.parsed += 1
    if type(value) is not dict:
        raise C05Error("canonical record must be an object")
    found = value.get("doc_id")
    if type(found) is not str or found.encode("utf-8") != doc_id:
        raise C05Error("source row differs from its C05 membership location")
    split = value.get("split")
    code = SPLIT_CODES.get(split) if type(split) is str else None
    if code is None:
        raise C05Error("invalid canonical split")
    if assigned == 0 and code != 0:
        # bb886bd: a C05-train record whose original split is not train refuses.
        raise C05Error("record split differs from C05 membership")
    text, declared = value.get("text"), value.get("utf8_byte_count")
    if type(text) is not str or type(declared) is not int or declared != size:
        raise C05Error("canonical text byte count differs from C05 membership")
    if len(text.encode("utf-8")) != size:
        raise C05Error("canonical text byte count differs from C05 membership")
    if content is not None:
        try:
            doc = CanonicalDocument(**value)
        except (TypeError, ValueError) as exc:
            raise C05Error("selected canonical record is invalid") from exc
        digest = canonical.digest(doc.to_dict())
        if digest != content:
            raise C05Error("selected fit record differs from its C05 binding")
        result.selected.append((doc, digest))
    return code


# -- ordered bounded process pool -------------------------------------------------------


class OrderedPool:
    """Ordered results with at most ``capacity`` tasks outstanding.

    Worker count is operational only: results are always yielded in task order.
    Every wait polls ``supervisor.check()``; any abnormal exit (deadline, RAM, error,
    interrupt, early consumer exit) terminates and reaps the worker processes rather
    than waiting for running tasks. ``inline=True`` runs tasks in-process with
    cooperative cancellation checks (tests and diagnostics; never the CLI).
    """

    def __init__(
        self,
        workers: int,
        tables: MembershipTables,
        supervisor: Checkable | None = None,
        *,
        inline: bool = False,
        grace: float = 2.0,
    ) -> None:
        if workers not in (1, 2, 4, 8, 16):
            raise C05Error("fit workers must be 1, 2, 4, 8 or 16")
        self.workers = workers
        self.tables = tables
        self.supervisor = supervisor
        self.inline = inline
        self.grace = grace
        self.executor: ProcessPoolExecutor | None = None
        self.aborted = False

    def __enter__(self) -> OrderedPool:
        if self.inline:
            init_worker(self.tables)
        else:
            self.executor = ProcessPoolExecutor(
                max_workers=self.workers,
                mp_context=get_context("spawn"),
                initializer=init_worker,
                initargs=(self.tables,),
            )
        return self

    def __exit__(self, kind: object, *_: object) -> None:
        if self.executor is None:
            return
        if kind is not None or self.aborted:
            self.abort()
        else:
            self.executor.shutdown(wait=True)
        self.executor = None

    def abort(self) -> None:
        """Terminate, wait, kill and reap every worker process; never drain tasks."""
        executor = self.executor
        if executor is None or self.aborted:
            self.aborted = True
            return
        self.aborted = True
        processes: list[psutil.Process] = []
        for process in list((getattr(executor, "_processes", None) or {}).values()):
            try:
                processes.append(psutil.Process(process.pid))
            except (psutil.NoSuchProcess, ValueError, TypeError):
                continue
        trees = list(processes)
        for process in processes:
            try:
                trees += process.children(recursive=True)
            except psutil.Error:
                continue
        terminate_processes(trees, self.grace)
        executor.shutdown(wait=False, cancel_futures=True)

    def _check(self) -> None:
        if self.supervisor is not None:
            self.supervisor.check()

    def map(
        self, function: Callable[[T], R], tasks: Iterable[T], capacity: int | None = None
    ) -> Iterator[R]:
        global _CANCEL
        if self.executor is None:
            _CANCEL = self._check
            try:
                for task in tasks:
                    self._check()
                    yield function(task)
            finally:
                _CANCEL = None
            return
        limit = capacity or 2 * self.workers
        pending: deque[Future[R]] = deque()
        iterator = iter(tasks)
        finished = False
        try:
            for task in iterator:
                self._check()
                pending.append(self.executor.submit(function, task))
                if len(pending) >= limit:
                    break
            while pending:
                result = self._wait(pending[0])
                pending.popleft()
                for task in iterator:
                    pending.append(self.executor.submit(function, task))
                    break
                yield result
            finished = True
        finally:
            if not finished:
                for future in pending:
                    future.cancel()
                self.abort()

    def _wait(self, future: Future[R]) -> R:
        while True:
            self._check()
            try:
                return future.result(timeout=POLL_SECONDS)
            except FutureTimeout:
                continue
            except BrokenProcessPool as exc:
                self._check()  # A supervisor kill reports its own reason first.
                raise C05Error("C06 worker process terminated unexpectedly") from exc
