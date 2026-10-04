"""Streaming, bounded, resumable Phase-A scan over a C05 input manifest (read-only).

The parent reads every manifest file once, sequentially, hashing every byte, and cuts
fixed-size blocks at line boundaries into chunk tasks. Workers (spawned processes, or
in-process for ``workers=1``) parse, verify and measure rows and return content-free
statistics. Results are consumed strictly in task order. A file's statistics are
committed as one atomic unit only after its SHA-256, byte size, row count and
canonical-byte total equal the manifest; anything earlier is discarded on failure.

Chunk size is fixed (bound into the audit identity), so worker count is operational
only: aggregate artifacts are byte-identical for 1/2/4/8/16 workers.

Resume: a unit is reused only when the audit binding (manifest digest, detector
policy, implementation identity, overlay, chunking) is identical and the source
file's size and modification time still equal the values recorded at commit; any
other difference refuses. A partial run never writes the completion receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.supervisor import Checkable
from xlm.data.quality.aggregate import CLASS_INDEX, N_METRICS, Population
from xlm.data.quality.detectors import analyze
from xlm.data.quality.overlay import KeptOverlay
from xlm.data.quality.policy import POLICY_VERSION, policy_identity
from xlm.data.quality.review import ChunkDocs, merge_samples, sample_chunk

CHUNK_BYTES = 32 * 1024**2
UNIT_KIND = "xlm_quality_audit_unit_v1"
BINDING_KIND = "xlm_quality_audit_binding_v1"
BINDING_FILE = "audit-binding.json"
UNITS_DIR = "units"
RECEIPT_FILE = "quality-audit-receipt.json"
MANIFEST_KINDS = {"c05_global_input_manifest": "production", "authored_c05_input": "authored"}
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
WORKER_CHOICES = (1, 2, 4, 8, 16)
MAX_FILES = 10_000
MAX_LINE_CEILING = 256 * 1024**2
POLL_SECONDS = 0.05
NAN = float("nan")
HEX = frozenset("0123456789abcdef")

T = TypeVar("T")
R = TypeVar("R")


class QualityError(ValueError):
    """Content-free refusal of the quality audit."""


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


def load_manifest(path: Path, data_root: Path | None = None) -> InputManifest:
    """Read a C05 input manifest (digest-checked, 8 MiB bound) and validate its files."""
    from xlm.data.exclusion.inputs import InputError, contained, read_metadata

    raw = path.read_bytes()
    try:
        body = read_metadata(path)
    except (InputError, ValueError) as exc:
        raise QualityError(f"input manifest refused: {exc}") from None
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


@dataclass
class ChunkResult:
    ordinal: int
    last: bool
    rows: int
    canonical_bytes: int
    populations: dict[str, Population]
    review: dict[str, list[Any]]


def worker_init() -> None:
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def process_chunk(task: ChunkTask) -> ChunkResult:
    """Parse, verify and measure every row of one chunk (pure; never writes)."""
    data = task.data
    kept = None if task.kept is None else np.frombuffer(task.kept, dtype=np.bool_)
    values: list[list[float]] = []
    sizes: list[int] = []
    lines: list[int] = []
    bits: list[int] = []
    classes: list[int] = []
    names: list[str] = []
    offsets: list[int] = []
    rows: list[int] = []
    doc_ids: list[str] = []
    kept_flags: list[bool | None] = []
    populations: dict[str, Population] = {}
    pos, row, end_of_data = 0, task.first_row, len(data)
    while pos < end_of_data:
        newline = data.find(b"\n", pos)
        stop = end_of_data if newline < 0 else newline + 1
        if stop - pos > task.line_ceiling:
            raise QualityError("canonical row exceeds the document ceiling")
        try:
            document = json.loads(data[pos:stop])
        except ValueError:
            raise QualityError("malformed canonical JSONL row") from None
        if not isinstance(document, dict):
            raise QualityError("canonical row is not an object")
        text, declared, doc_id = (
            document.get("text"),
            document.get("utf8_byte_count"),
            document.get("doc_id"),
        )
        if type(text) is not str or type(declared) is not int or type(doc_id) is not str:
            raise QualityError("canonical row schema")
        try:
            nbytes = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise QualityError("canonical text is not valid Unicode scalar values") from None
        if nbytes != declared:
            raise QualityError("canonical utf8_byte_count disagrees with its text")
        result = analyze(text, nbytes)
        index = row - task.first_row
        if kept is None:
            membership: bool | None = None
            name = "all"
        else:
            if index >= kept.shape[0]:
                raise QualityError("C05 overlay row count disagrees with the chunk")
            membership = bool(kept[index])
            name = "c05_kept" if membership else "c05_removed"
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
        doc_ids.append(doc_id)
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
            path=task.path,
            rows=rows,
            offsets=offsets,
            doc_ids=doc_ids,
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
    )


# -- ordered pool -----------------------------------------------------------------------------


class OrderedPool:
    """At most ``2 x workers`` tasks outstanding; results in task order.

    ``workers=1`` runs in-process. Any abnormal exit terminates the worker processes
    instead of draining them.
    """

    def __init__(self, workers: int, supervisor: Checkable | None = None) -> None:
        if workers not in WORKER_CHOICES:
            raise QualityError("workers must be 1, 2, 4, 8 or 16")
        self.workers = workers
        self.supervisor = supervisor
        self.executor: ProcessPoolExecutor | None = None

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

    def map(self, function: Callable[[T], R], tasks: Iterable[T]) -> Iterator[R]:
        if self.executor is None:
            for task in tasks:
                self._check()
                yield function(task)
            return
        pending: deque[Future[R]] = deque()
        iterator = iter(tasks)
        limit = 2 * self.workers
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

    def _wait(self, future: Future[R]) -> R:
        while True:
            self._check()
            try:
                return future.result(timeout=POLL_SECONDS)
            except FutureTimeout:
                continue


# -- source reading -----------------------------------------------------------------------------


def file_tasks(
    root: Path, item: AuditFile, kept: np.ndarray | None, line_ceiling: int
) -> Iterator[ChunkTask]:
    """Fixed-size line-aligned chunks; the final task is created only after the file
    hash and size equal the manifest (so a commit implies a verified file)."""
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
        bitmap = None
        if kept is not None:
            if row - 1 + rows > kept.shape[0]:
                raise QualityError("file holds more rows than the manifest declares")
            bitmap = kept[row - 1 : row - 1 + rows].tobytes()
        task = ChunkTask(item.ordinal, item.path, row, offset, data, bitmap, line_ceiling, last)
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
            yield ChunkTask(**{**held.__dict__, "last": True})
    else:
        yield make(pending, True)


# -- units ----------------------------------------------------------------------------------


def unit_path(output: Path, ordinal: int) -> Path:
    return output / UNITS_DIR / f"f{ordinal:05d}.unit.zz"


@dataclass
class FileAccumulator:
    item: AuditFile
    rows: int = 0
    canonical_bytes: int = 0
    populations: dict[str, Population] = field(default_factory=dict)
    review: dict[str, list[Any]] = field(default_factory=dict)

    def add(self, result: ChunkResult) -> None:
        self.rows += result.rows
        self.canonical_bytes += result.canonical_bytes
        for name, population in result.populations.items():
            mine = self.populations.get(name)
            if mine is None:
                self.populations[name] = population
            else:
                mine.merge(population)
        merge_samples(self.review, result.review)


class OutputBudget:
    def __init__(self, limit: int, output: Path) -> None:
        self.limit = limit
        self.used = sum(p.stat().st_size for p in output.rglob("*") if p.is_file())
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
    try:
        body = canonical.loads_bytes_strict(zlib.decompress(raw))
    except (zlib.error, ValueError):
        raise QualityError("unreadable audit unit; use a new output directory") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("audit unit digest mismatch; use a new output directory")
    return body


def commit_unit(
    output: Path, binding: str, acc: FileAccumulator, root: Path, budget: OutputBudget
) -> None:
    item = acc.item
    if acc.rows != item.documents:
        raise QualityError("source row count differs from the manifest")
    if acc.canonical_bytes != item.canonical_bytes:
        raise QualityError("source canonical byte total differs from the manifest")
    stat = (root / item.path).stat()
    payload = encode_unit(
        {
            "kind": UNIT_KIND,
            "audit_binding": binding,
            "file": item.record(),
            "stat": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            "populations": {k: v.to_json() for k, v in sorted(acc.populations.items())},
            "review": {k: acc.review[k] for k in sorted(acc.review)},
        }
    )
    budget.charge(len(payload))
    canonical.write_atomic(unit_path(output, item.ordinal), payload)


def load_unit(output: Path, item: AuditFile, binding: str, root: Path) -> dict[str, Any]:
    body = decode_unit(unit_path(output, item.ordinal).read_bytes())
    if body.get("kind") != UNIT_KIND or body.get("audit_binding") != binding:
        raise QualityError("audit unit belongs to a different audit binding")
    if body.get("file") != item.record():
        raise QualityError("audit unit file record differs from the manifest")
    stat = (root / item.path).stat()
    if body["stat"] != {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}:
        raise QualityError("source file changed since its unit was committed; refusing")
    return body


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
    }
    body["digest"] = canonical.digest(body)
    return body


# -- progress ---------------------------------------------------------------------------------


class Progress:
    """Content-free stage lines on stderr (stdout stays the single final JSON)."""

    def __init__(self, interval: float | None, totals: dict[str, int]) -> None:
        self.interval = interval
        self.totals = totals
        self.started = time.monotonic()
        self.last = 0.0
        self.samples: deque[tuple[float, int]] = deque()

    def update(self, files: int, docs: int, read_bytes: int, rss: int, force: bool = False) -> None:
        if self.interval is None:
            return
        now = time.monotonic()
        if not force and now - self.last < self.interval:
            return
        self.last = now
        self.samples.append((now, read_bytes))
        while self.samples and now - self.samples[0][0] > 30:
            self.samples.popleft()
        elapsed = max(now - self.started, 1e-9)
        first_t, first_b = self.samples[0]
        rolling = (read_bytes - first_b) / max(now - first_t, 1e-9)
        total = self.totals["file_bytes"]
        eta = "--:--:--"
        if rolling > 0 and now - self.started >= 10:
            eta = time.strftime("%H:%M:%S", time.gmtime((total - read_bytes) / rolling))
        print(
            f"[QUALITY] SCAN | files {files:,}/{self.totals['files']:,} | docs {docs:,}/"
            f"{self.totals['documents']:,} | {read_bytes / 2**30:.2f}/{total / 2**30:.2f} GiB | "
            f"{rolling / 1e6:.1f} MB/s rolling | {read_bytes / elapsed / 1e6:.1f} MB/s avg | "
            f"RSS {rss / 2**30:.2f} GiB | elapsed "
            f"{time.strftime('%H:%M:%S', time.gmtime(elapsed))} | ETA {eta}",
            file=sys.stderr,
            flush=True,
        )
