"""Streaming, bounded, resumable Phase-A scan over a C05 input manifest (read-only).

The parent reads every manifest file once, sequentially, hashing every byte, and cuts
fixed-size blocks at line boundaries into chunk tasks. Workers (spawned processes, or
in-process for ``workers=1``) strictly parse, verify and measure rows and return
content-free statistics. Results are consumed strictly in task order.

Source identity (never mtime): a file's statistics are committed as one atomic unit
only after (1) the bytes that were measured hashed to the manifest SHA-256, size and
row count while being read, and (2) a SECOND full re-hash, started only after the
last measurement of that file returned, matches again (``verify_source``). Any
mutation persisting from before the scan, during it, or after it until the commit is
refused. Resume, ``report`` and review materialization re-hash every reused source
file the same way before trusting it.

Chunk size is fixed (bound into the audit identity), so worker count is operational
only: aggregate artifacts are byte-identical for 1/2/4/8/16 workers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import (
    FIRST_COMPLETED,
    BrokenExecutor,
    CancelledError,
    Future,
    ProcessPoolExecutor,
    wait,
)
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import numpy as np

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.supervisor import Checkable
from xlm.data.quality.aggregate import CLASS_INDEX, N_METRICS, Population
from xlm.data.quality.detectors import analyze
from xlm.data.quality.envelope import QUEUE_FACTOR
from xlm.data.quality.overlay import IDENTITY, KeptOverlay, doc_digest
from xlm.data.quality.policy import POLICY_VERSION, SPLIT_VALUES, policy_identity
from xlm.data.quality.progress import Telemetry
from xlm.data.quality.review import ChunkDocs, merge_samples, review_key, sample_chunk
from xlm.data.quality.strictjson import loads_strict_bytes

CHUNK_BYTES = 8 * 1024**2
VERIFY_BLOCK_BYTES = 8 * 1024**2
UNIT_KIND = "xlm_quality_audit_unit_v3"
BINDING_KIND = "xlm_quality_audit_binding_v2"
BINDING_FILE = "audit-binding.json"
UNITS_DIR = "units"
RECEIPT_FILE = "quality-audit-receipt.json"
MANIFEST_KINDS = {
    "c05_global_input_manifest": "production",
    "authored_c05_input": "authored",
    # Phase-C cleaned-corpus manifest candidates (``clean-production-manifest``); the
    # mode is the original manifest's. They still need an independent audit and C05.
    "xlm_cleaned_input_manifest": "production",
    "authored_cleaned_input": "authored",
}
FILE_KEYS = (
    "path",
    "source_key",
    "component",
    "view",
    "upstream_component",
    "documents_sha256",
    "file_bytes",
    "canonical_bytes",
    "documents",
)
WORKER_CHOICES = (1, 2, 4, 8, 12, 16)
MAX_FILES = 10_000
MAX_LINE_CEILING = 256 * 1024**2
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_UNIT_FILE_BYTES = 64 * 1024**2
MAX_UNIT_DECODED_BYTES = 512 * 1024**2
POLL_SECONDS = 0.05
RESULT_BACKLOG = 2  # finished-but-unconsumed results, in multiples of the queue bound
NAN = float("nan")
HEX = frozenset("0123456789abcdef")
CANONICAL_FIELDS = frozenset(f.name for f in dataclasses.fields(CanonicalDocument))
_STR_FIELDS = (
    "doc_id",
    "source_id",
    "source_revision",
    "source_file",
    "raw_hash",
    "clean_hash",
    "text",
    "language",
    "document_kind",
    "license_reference",
    "split",
)
_SPLITS = frozenset(SPLIT_VALUES)

T = TypeVar("T")
R = TypeVar("R")


class QualityError(ValueError):
    """Content-free refusal of the quality audit."""


class WorkerPoolError(QualityError, BrokenProcessPool):
    """Controlled fail-closed refusal: a worker process died with no supervisor reason.

    It is a :class:`QualityError` (the CLI refuses with a content-free message) and
    still a :class:`BrokenProcessPool` for callers that classify executor breakage.
    """


WORKER_FAILURE = "a worker process terminated abnormally; nothing published"
# Executor breakage after a worker was killed (by the supervisor or otherwise).
POOL_BREAKAGE: tuple[type[BaseException], ...] = (
    BrokenExecutor,
    CancelledError,
    EOFError,
    ConnectionError,
)
# ``submit`` on a broken or shut-down executor raises a RuntimeError subclass.
SUBMIT_BREAKAGE: tuple[type[BaseException], ...] = (*POOL_BREAKAGE, RuntimeError)


# -- manifest -------------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFile:
    ordinal: int
    path: str
    source_key: str
    component: str
    view: str
    upstream_component: str | None
    documents_sha256: str
    file_bytes: int
    canonical_bytes: int
    documents: int

    @property
    def allocation(self) -> str:
        return f"{self.component}|{self.view}|{self.upstream_component or '-'}"

    def record(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            **{key: getattr(self, key) for key in FILE_KEYS},
        }


@dataclass(frozen=True)
class InputManifest:
    path: Path
    digest: str
    file_sha256: str
    kind: str
    mode: str
    data_root: Path
    files: tuple[AuditFile, ...]

    @property
    def totals(self) -> dict[str, int]:
        return {
            "files": len(self.files),
            "documents": sum(f.documents for f in self.files),
            "file_bytes": sum(f.file_bytes for f in self.files),
            "canonical_bytes": sum(f.canonical_bytes for f in self.files),
        }


def read_bounded(path: Path, limit: int, what: str) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise QualityError(f"{what} exceeds its size bound")
    return raw


def load_manifest(path: Path, data_root: Path | None = None) -> InputManifest:
    """One bounded read of a C05 input manifest: strict JSON, self-digest, schema."""
    from xlm.data.exclusion.inputs import InputError, contained

    raw = read_bounded(path, MAX_MANIFEST_BYTES, "input manifest")
    try:
        body = canonical.loads_bytes_strict(raw)
    except ValueError:
        raise QualityError("input manifest refused: not strict canonical JSON") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("input manifest refused: self-digest mismatch")
    kind = body.get("kind")
    if kind not in MANIFEST_KINDS:
        raise QualityError("input manifest kind is not a C05 input manifest")
    root = Path(data_root) if data_root is not None else Path(str(body.get("data_root")))
    if not root.is_dir():
        raise QualityError("data root is not a directory")
    entries = body.get("files")
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
        raise QualityError("input manifest file list outside its bound")
    files: list[AuditFile] = []
    for ordinal, entry in enumerate(entries):
        if not isinstance(entry, dict) or any(key not in entry for key in FILE_KEYS):
            raise QualityError("input manifest file entry schema")
        for key in ("file_bytes", "canonical_bytes", "documents"):
            if type(entry[key]) is not int or entry[key] < 0:
                raise QualityError("input manifest file counts must be non-negative integers")
        sha = entry["documents_sha256"]
        if type(sha) is not str or len(sha) != 64 or not HEX.issuperset(sha):
            raise QualityError("input manifest file SHA-256 schema")
        upstream = entry["upstream_component"]
        if upstream is not None and type(upstream) is not str:
            raise QualityError("input manifest upstream component schema")
        for key in ("path", "source_key", "component", "view"):
            if type(entry[key]) is not str or not entry[key]:
                raise QualityError("input manifest file string schema")
        try:
            contained(root, entry["path"])
        except InputError:
            raise QualityError("input manifest path escapes the data root") from None
        files.append(
            AuditFile(
                ordinal=ordinal,
                path=entry["path"],
                source_key=entry["source_key"],
                component=entry["component"],
                view=entry["view"],
                upstream_component=upstream,
                documents_sha256=sha,
                file_bytes=entry["file_bytes"],
                canonical_bytes=entry["canonical_bytes"],
                documents=entry["documents"],
            )
        )
    if len({f.path for f in files}) != len(files):
        raise QualityError("input manifest repeats a file")
    return InputManifest(
        path=path,
        digest=str(body["digest"]),
        file_sha256=hashlib.sha256(raw).hexdigest(),
        kind=str(kind),
        mode=MANIFEST_KINDS[str(kind)],
        data_root=root,
        files=tuple(files),
    )


# -- source identity ------------------------------------------------------------------------


def verify_source(
    root: Path,
    item: AuditFile,
    *,
    check: Callable[[], None] | None = None,
    probes: Mapping[int, int] | None = None,
    progress: Callable[[int], None] | None = None,
) -> set[int]:
    """Full re-hash: SHA-256, byte size and row count must equal the frozen manifest.

    ``probes`` maps byte offsets to the 1-based row expected to start there; the
    offsets proven to start exactly that row are returned. ``progress`` (operational
    only) receives the size of every block read.
    """
    path = root / item.path
    digest = hashlib.sha256()
    size = newlines = 0
    previous = b"\n"
    targets = sorted(probes or {})
    verified: set[int] = set()
    pointer = 0
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(min(VERIFY_BLOCK_BYTES, item.file_bytes - size + 1)):
            if check is not None:
                check()
            end = size + len(block)
            while pointer < len(targets) and targets[pointer] < end:
                offset = targets[pointer]
                local = offset - size
                before = previous if local == 0 else block[local - 1 : local]
                row = newlines + block.count(b"\n", 0, local) + 1
                if before == b"\n" and probes is not None and probes[offset] == row:
                    verified.add(offset)
                pointer += 1
            digest.update(block)
            newlines += block.count(b"\n")
            size = end
            previous = block[-1:]
            if progress is not None:
                progress(len(block))
            if size > item.file_bytes:
                break
    rows = newlines + (1 if size and previous != b"\n" else 0)
    if (size, digest.hexdigest(), rows) != (item.file_bytes, item.documents_sha256, item.documents):
        raise QualityError("source file differs from the frozen manifest SHA-256/size/rows")
    return verified


# -- worker kernel ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkTask:
    ordinal: int
    path: str
    first_row: int
    base_offset: int
    data: bytes
    kept: bytes | None
    line_ceiling: int
    last: bool
    identity: bytes | None = None
    review_key: bytes = b"\0" * 32


@dataclass
class ChunkResult:
    ordinal: int
    last: bool
    rows: int
    canonical_bytes: int
    populations: dict[str, Population]
    review: dict[str, list[Any]]
    max_line_bytes: int = 0
    # Operational only (never in a unit, artifact or receipt): who measured the chunk,
    # when (monotonic, system-wide), for how long and with how much CPU.
    nbytes: int = 0
    pid: int = 0
    started: float = 0.0
    finished: float = 0.0
    cpu_seconds: float = 0.0


def worker_init() -> None:
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def parse_row(body: bytes) -> dict[str, Any]:
    """Strict canonical JSON (strict UTF-8, no duplicate keys, no NaN/Infinity) with the
    exact CanonicalDocument field set and types."""
    try:
        document = loads_strict_bytes(body)
    except ValueError:
        raise QualityError(
            "malformed canonical JSONL row (strict canonical JSON required)"
        ) from None
    if type(document) is not dict or document.keys() != CANONICAL_FIELDS:
        raise QualityError("canonical row schema")
    for name in _STR_FIELDS:
        if type(document[name]) is not str:
            raise QualityError("canonical row schema")
    for name in ("utf8_byte_count", "source_row"):
        if type(document[name]) is not int:
            raise QualityError("canonical row schema")
    confidence = document["language_confidence"]
    if type(confidence) not in (int, float):
        raise QualityError("canonical row schema")
    if type(document["source_metadata"]) is not dict or type(document["cluster_ids"]) is not dict:
        raise QualityError("canonical row schema")
    for name in ("parent_ids", "transform_log", "quality_reasons"):
        if type(document[name]) is not list:
            raise QualityError("canonical row schema")
    if document["split"] not in _SPLITS:
        raise QualityError("canonical row schema")
    return document


def process_chunk(task: ChunkTask) -> ChunkResult:
    """Parse, verify and measure every row of one chunk (pure; never writes), with the
    operational activity record of the process that did it."""
    started, cpu = time.monotonic(), time.process_time()
    result = measure_chunk(task)
    result.nbytes = len(task.data)
    result.pid = os.getpid()
    result.cpu_seconds = time.process_time() - cpu
    result.started, result.finished = started, time.monotonic()
    return result


def measure_chunk(task: ChunkTask) -> ChunkResult:
    """The scientific measurement of one chunk."""
    data = task.data
    kept = None if task.kept is None else np.frombuffer(task.kept, dtype=np.bool_)
    identity = None if task.identity is None else np.frombuffer(task.identity, dtype=IDENTITY)
    values: list[list[float]] = []
    sizes: list[int] = []
    lines: list[int] = []
    bits: list[int] = []
    classes: list[int] = []
    names: list[str] = []
    offsets: list[int] = []
    rows: list[int] = []
    id_digests: list[str] = []
    row_digests: list[str] = []
    kept_flags: list[bool | None] = []
    populations: dict[str, Population] = {}
    pos, row, end_of_data = 0, task.first_row, len(data)
    while pos < end_of_data:
        newline = data.find(b"\n", pos)
        stop = end_of_data if newline < 0 else newline + 1
        if stop - pos > task.line_ceiling:
            raise QualityError("canonical row exceeds the document ceiling")
        body = data[pos:newline] if newline >= 0 else data[pos:stop]
        row_digest = hashlib.sha256(body).digest()
        document = parse_row(body)
        text, declared, doc_id = document["text"], document["utf8_byte_count"], document["doc_id"]
        try:
            nbytes = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise QualityError("canonical text is not valid Unicode scalar values") from None
        if nbytes != declared:
            raise QualityError("canonical utf8_byte_count disagrees with its text")
        index = row - task.first_row
        if kept is None:
            membership: bool | None = None
            name = "all"
        else:
            if index >= kept.shape[0]:
                raise QualityError("C05 overlay row count disagrees with the chunk")
            membership = bool(kept[index])
            name = "c05_kept" if membership else "c05_removed"
            if membership:
                assert identity is not None
                _verify_kept(identity[index], doc_id, nbytes, row_digest, document)
        result = analyze(text, nbytes)
        population = populations.get(name)
        if population is None:
            population = populations[name] = Population()
        population.add_language(document, nbytes)
        values.append([NAN if x is None else float(x) for x in result.values])
        sizes.append(nbytes)
        lines.append(stop - pos)
        flag_bits = 0
        for flag in result.flags:
            flag_bits |= 1 << flag
        bits.append(flag_bits)
        classes.append(CLASS_INDEX[result.doc_class])
        names.append(name)
        offsets.append(task.base_offset + pos)
        rows.append(row)
        id_digests.append(hashlib.sha256(doc_id.encode("utf-8")).hexdigest())
        row_digests.append(row_digest.hex())
        kept_flags.append(membership)
        row += 1
        pos = stop
    count = len(rows)
    if kept is not None and kept.shape[0] != count:
        raise QualityError("C05 overlay row count disagrees with the chunk")
    matrix = np.array(values, dtype=np.float64).reshape(count, N_METRICS)
    size_array = np.array(sizes, dtype=np.int64)
    line_array = np.array(lines, dtype=np.int64)
    bit_array = np.array(bits, dtype=np.int64)
    class_array = np.array(classes, dtype=np.int64)
    name_array = np.array(names)
    for name, population in populations.items():
        members = np.flatnonzero(name_array == name)
        population.add_batch(
            matrix[members],
            size_array[members],
            int(line_array[members].sum()),
            bit_array[members],
            class_array[members],
        )
    review = sample_chunk(
        matrix,
        ChunkDocs(
            key=task.review_key,
            path=task.path,
            rows=rows,
            offsets=offsets,
            doc_id_digests=id_digests,
            row_digests=row_digests,
            kept=kept_flags,
            classes=class_array,
        ),
    )
    return ChunkResult(
        ordinal=task.ordinal,
        last=task.last,
        rows=count,
        canonical_bytes=int(size_array.sum()),
        populations=populations,
        review=review,
        max_line_bytes=int(line_array.max()) if count else 0,
    )


def _verify_kept(
    expected: Any, doc_id: str, nbytes: int, row_digest: bytes, document: dict[str, Any]
) -> None:
    """A kept C05 row must be exactly this canonical row: doc_id, bytes and content."""
    if expected["doc"].tobytes() != doc_digest(doc_id) or int(expected["bytes"]) != nbytes:
        raise QualityError("C05 kept-row identity differs from the canonical source row")
    content = expected["content"].tobytes()
    # Fast path: the stored row is already canonical bytes, so its SHA-256 is the C05
    # content digest; otherwise recompute canonical.digest of the parsed row.
    if len(content) != 32:
        raise QualityError("C05 kept-row content digest is not a full SHA-256")
    if row_digest != content and bytes.fromhex(canonical.digest(document)) != content:
        raise QualityError("C05 kept-row content differs from the canonical source row")


# -- ordered pool -----------------------------------------------------------------------------


class OrderedPool:
    """At most ``QUEUE_FACTOR x workers`` tasks in flight; results in task order.

    "In flight" means submitted and not yet finished by a worker: only those tasks
    hold chunk bytes. Results are consumed strictly in task order (so aggregation is
    deterministic), but a slow task at the head no longer stalls submission: up to
    ``RESULT_BACKLOG x queue`` finished, not-yet-consumed (small, content-free)
    results may wait behind it while the other workers keep working.

    ``workers=1`` runs in-process. Any abnormal exit terminates the worker processes
    instead of draining them. Executor breakage (``BrokenProcessPool``, a cancelled
    future, ``EOFError`` or a broken pipe) never escapes raw: a failure the supervisor
    already recorded (RSS ceiling, deadline, disk, cancellation; it records the reason
    BEFORE it terminates the workers) is re-raised as that reason, and breakage with no
    recorded reason is a controlled :class:`WorkerPoolError`. ``peak_in_flight`` is the
    measured maximum number of tasks in flight.
    """

    def __init__(
        self,
        workers: int,
        supervisor: Checkable | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        if workers not in WORKER_CHOICES:
            raise QualityError("workers must be 1, 2, 4, 8, 12 or 16")
        self.workers = workers
        self.supervisor = supervisor
        self.telemetry = telemetry
        self.executor: ProcessPoolExecutor | None = None
        self.peak_in_flight = 0
        self.limit = QUEUE_FACTOR * workers if workers > 1 else 1

    def __enter__(self) -> OrderedPool:
        if self.workers > 1:
            self.executor = ProcessPoolExecutor(
                max_workers=self.workers,
                mp_context=get_context("spawn"),
                initializer=worker_init,
            )
        return self

    def __exit__(self, kind: object, *_: object) -> None:
        if self.executor is None:
            return
        if kind is not None:
            self._abort()
        else:
            self.executor.shutdown(wait=True)
        self.executor = None

    def _abort(self) -> None:
        import psutil

        from xlm.data.exclusion.supervisor import terminate_processes

        executor = self.executor
        if executor is None:
            return
        processes = []
        for process in list((getattr(executor, "_processes", None) or {}).values()):
            try:
                processes.append(psutil.Process(process.pid))
            except (psutil.NoSuchProcess, ValueError, TypeError):
                continue
        terminate_processes(processes, 2.0)
        executor.shutdown(wait=False, cancel_futures=True)

    def _check(self) -> None:
        if self.supervisor is not None:
            self.supervisor.check()

    def _broken(self) -> NoReturn:
        """Resolve executor breakage to the recorded supervisor reason, else refuse."""
        self._check()
        raise WorkerPoolError(WORKER_FAILURE)

    def map(self, function: Callable[[T], R], tasks: Iterable[T]) -> Iterator[R]:
        telemetry = self.telemetry
        if self.executor is None:
            for task in tasks:
                self._check()
                self.peak_in_flight = max(self.peak_in_flight, 1)
                if telemetry is not None:
                    telemetry.submitted(1)
                result = function(task)
                self._check()  # a non-cooperative in-process task cannot outlive a failure
                if telemetry is not None:
                    telemetry.completed(0, 1)
                yield result
            return
        pending: deque[Future[R]] = deque()  # every unconsumed task, in task order
        running: set[Future[R]] = set()  # submitted, not yet finished (holds chunk bytes)
        iterator = iter(tasks)
        exhausted = False
        backlog = RESULT_BACKLOG * self.limit
        while True:
            while not exhausted and len(running) < self.limit and len(pending) < backlog:
                self._check()
                try:
                    task = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                future = self._submit(function, task)
                pending.append(future)
                running.add(future)
                self.peak_in_flight = max(self.peak_in_flight, len(running))
                if telemetry is not None:
                    telemetry.submitted(len(running))
            if not pending:
                return
            self._check()
            finished = {future for future in running if future.done()}
            if not finished and not pending[0].done():
                finished, _ = wait(running, timeout=POLL_SECONDS, return_when=FIRST_COMPLETED)
            if finished:
                running -= finished
                if telemetry is not None:
                    telemetry.completed(len(running), len(finished))
            while pending and pending[0].done():
                head = pending.popleft()
                if head in running:  # finished after the scan above
                    running.discard(head)
                    if telemetry is not None:
                        telemetry.completed(len(running), 1)
                yield self._result(head)

    def _submit(self, function: Callable[[T], R], task: T) -> Future[R]:
        assert self.executor is not None
        try:
            future = self.executor.submit(function, task)
        except SUBMIT_BREAKAGE:
            self._broken()
        return future

    def _result(self, future: Future[R]) -> R:
        self._check()
        try:
            return future.result(timeout=0)
        except POOL_BREAKAGE:
            self._broken()


# -- source reading -----------------------------------------------------------------------------


def file_tasks(
    root: Path,
    item: AuditFile,
    kept: np.ndarray | None,
    line_ceiling: int,
    *,
    identity: np.ndarray | None = None,
    key: bytes = b"\0" * 32,
) -> Iterator[ChunkTask]:
    """Fixed-size line-aligned chunks; the final task is created only after the file
    hash, size and row count equal the manifest (the bytes measured are the bytes
    hashed)."""
    path = root / item.path
    digest = hashlib.sha256()
    size = 0
    row = 1
    offset = 0
    pending = b""
    held: ChunkTask | None = None

    def make(data: bytes, last: bool) -> ChunkTask:
        nonlocal row, offset
        rows = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        bitmap = slab = None
        if row - 1 + rows > item.documents:
            raise QualityError("file holds more rows than the manifest declares")
        if kept is not None:
            bitmap = kept[row - 1 : row - 1 + rows].tobytes()
            if identity is not None:
                slab = identity[row - 1 : row - 1 + rows].tobytes()
        task = ChunkTask(
            item.ordinal, item.path, row, offset, data, bitmap, line_ceiling, last, slab, key
        )
        row += rows
        offset += len(data)
        return task

    with path.open("rb", buffering=0) as stream:
        while block := stream.read(min(CHUNK_BYTES, item.file_bytes - size + 1)):
            digest.update(block)
            size += len(block)
            if size > item.file_bytes:
                raise QualityError("source file is larger than the manifest declares")
            data = pending + block if pending else block
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
                if len(pending) > line_ceiling:
                    raise QualityError("canonical row exceeds the document ceiling")
                continue
            pending = data[cut:]
            if len(pending) > line_ceiling:
                raise QualityError("canonical row exceeds the document ceiling")
            if held is not None:
                yield held
            held = make(data[:cut], False)
    if size != item.file_bytes or digest.hexdigest() != item.documents_sha256:
        raise QualityError("source file bytes differ from the manifest SHA-256/size")
    if held is not None:
        if pending:
            yield held
            yield make(pending, True)
        else:
            yield dataclasses.replace(held, last=True)
    else:
        yield make(pending, True)


# -- units ----------------------------------------------------------------------------------


def unit_name(ordinal: int) -> str:
    return f"{UNITS_DIR}/f{ordinal:05d}.unit.zz"


def unit_path(output: Path, ordinal: int) -> Path:
    return output / UNITS_DIR / f"f{ordinal:05d}.unit.zz"


@dataclass
class FileAccumulator:
    item: AuditFile
    rows: int = 0
    canonical_bytes: int = 0
    populations: dict[str, Population] = field(default_factory=dict)
    review: dict[str, list[Any]] = field(default_factory=dict)
    max_line_bytes: int = 0

    def add(self, result: ChunkResult) -> None:
        self.rows += result.rows
        self.canonical_bytes += result.canonical_bytes
        self.max_line_bytes = max(self.max_line_bytes, result.max_line_bytes)
        for name, population in result.populations.items():
            mine = self.populations.get(name)
            if mine is None:
                self.populations[name] = population
            else:
                mine.merge(population)
        merge_samples(self.review, result.review)


class OutputBudget:
    """Every byte the job writes is charged BEFORE it is written."""

    def __init__(self, limit: int, used: int = 0) -> None:
        self.limit = limit
        self.used = used
        if self.used > limit:
            raise QualityError("output directory already exceeds --max-output-gib")

    def charge(self, size: int) -> None:
        if self.used + size > self.limit:
            raise QualityError("output byte ceiling (--max-output-gib) would be exceeded")
        self.used += size


def encode_unit(body: dict[str, Any]) -> bytes:
    body = dict(body)
    body["digest"] = canonical.digest(body)
    return zlib.compress(canonical.canonical_bytes(body), 6)


def decode_unit(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_UNIT_FILE_BYTES:
        raise QualityError("audit unit exceeds its size bound")
    try:
        inflater = zlib.decompressobj()
        expanded = inflater.decompress(raw, MAX_UNIT_DECODED_BYTES)
        if inflater.unconsumed_tail or not inflater.eof:
            raise QualityError("audit unit expands beyond its bound or is truncated")
        body = canonical.loads_bytes_strict(expanded)
    except (zlib.error, ValueError) as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("unreadable audit unit; use a new output directory") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("audit unit digest mismatch; use a new output directory")
    return body


def commit_unit(
    tree: Any,
    binding: str,
    acc: FileAccumulator,
    envelope: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    """``tree`` is the job's :class:`~xlm.data.quality.outputs.OutputTree`.

    ``facts`` are the producing run's MEASURED operational facts at commit time
    (supervised elapsed seconds, peak process-tree RSS, minimum observed free bytes);
    verification checks them, and ``max_line_bytes``, against ``producer_envelope``.
    """
    item = acc.item
    if acc.rows != item.documents:
        raise QualityError("source row count differs from the manifest")
    if acc.canonical_bytes != item.canonical_bytes:
        raise QualityError("source canonical byte total differs from the manifest")
    payload = encode_unit(
        {
            "kind": UNIT_KIND,
            "audit_binding": binding,
            "file": item.record(),
            "producer_envelope": dict(envelope),
            "producer_facts": dict(facts),
            "max_line_bytes": acc.max_line_bytes,
            "populations": {k: v.to_json() for k, v in sorted(acc.populations.items())},
            "review": {k: acc.review[k] for k in sorted(acc.review)},
        }
    )
    tree.write(unit_name(item.ordinal), payload)


def load_unit(
    output: Path, item: AuditFile, binding: str, root: Path | None = None
) -> dict[str, Any]:
    """Unit statistics bound to this audit and file record (source identity is
    established separately by :func:`verify_source`, never by mtime)."""
    path = unit_path(output, item.ordinal)
    body = decode_unit(read_bounded(path, MAX_UNIT_FILE_BYTES, "audit unit"))
    if body.get("kind") != UNIT_KIND or body.get("audit_binding") != binding:
        raise QualityError("audit unit belongs to a different audit binding")
    if body.get("file") != item.record():
        raise QualityError("audit unit file record differs from the manifest")
    check_unit_facts(body, item)
    return body


UNIT_FACT_KEYS = frozenset(
    {"elapsed_seconds", "peak_process_tree_rss_bytes", "min_observed_free_bytes"}
)


def check_unit_facts(body: Mapping[str, Any], item: AuditFile) -> None:
    """Exact types of a unit's measured facts (relations are checked against its
    producer envelope by verification)."""
    longest = body.get("max_line_bytes")
    facts = body.get("producer_facts")
    if (
        type(longest) is not int
        or not (0 < longest <= MAX_LINE_CEILING if item.documents else longest == 0)
        or not isinstance(facts, dict)
        or set(facts) != UNIT_FACT_KEYS
        or type(facts["elapsed_seconds"]) is not float
        or not 0 <= facts["elapsed_seconds"] < float("inf")
        or type(facts["peak_process_tree_rss_bytes"]) is not int
        or facts["peak_process_tree_rss_bytes"] <= 0
        or type(facts["min_observed_free_bytes"]) is not int
        or facts["min_observed_free_bytes"] < 0
    ):
        raise QualityError("audit unit measured facts are invalid")


# -- binding ----------------------------------------------------------------------------------


def implementation() -> dict[str, str]:
    from xlm.data.exclusion.identity import implementation_identity

    return implementation_identity()


def audit_binding(
    manifest: InputManifest,
    overlay: KeptOverlay | None,
    line_ceiling: int,
    identity: dict[str, str],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": BINDING_KIND,
        "input_manifest": {
            "digest": manifest.digest,
            "file_sha256": manifest.file_sha256,
            "kind": manifest.kind,
            "mode": manifest.mode,
            **manifest.totals,
        },
        "data_root": manifest.data_root.resolve().as_posix(),
        "detector_policy": {"version": POLICY_VERSION, "digest": policy_identity()},
        "implementation": {
            "code_identity": identity["code_identity"],
            "dependency_sha256": identity["dependency_sha256"],
        },
        "overlay": None if overlay is None else overlay.binding,
        "chunk_bytes": CHUNK_BYTES,
        "line_ceiling": line_ceiling,
        "review_key_sha256": hashlib.sha256(review_key(manifest.digest)).hexdigest(),
        "source_identity": "SHA-256 + size + rows, re-hashed on every reuse; never mtime",
    }
    body["digest"] = canonical.digest(body)
    return body
