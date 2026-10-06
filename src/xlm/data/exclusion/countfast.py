"""Exact token counts, FAST: one authenticated membership stream, parallel verified counting.

Byte-identical to :func:`selection.count_tokens` (the reference oracle): the same
``counts.jsonl`` rows in the same order, the same signed ``counts.json``. Only the
work changes, and every reference check is kept:

1. **Membership** (``membership.jsonl``) is streamed once and authenticated exactly
   as the C06 fast fit does (:func:`fitfast.stream_membership`): SHA-256, size and row
   count equal the signed completion; rows are schema-checked, strictly ascending by
   doc id (unique), bound to a frozen plan file/row and its allocation, then
   reconciled against the completion's per-allocation accounting. No SQLite import
   and no per-record queries.
2. **Tokenizer**: the artifact files are read once into memory, bound to the same
   identity the reference records, and copied into private job scratch. Workers load
   only that copy and must reproduce the identity fingerprint. Before publication
   both the original directory and the copy must still equal the snapshot.
3. **Source**: every plan file is hashed completely, once, in one sequential pass
   (size, SHA-256 and row count equal the plan; growth refuses at once). A file whose
   kept text is small is verified and counted in that same pass. A large file's pass
   records the byte location of each kept row (no kept-index is trusted); its kept rows
   are then counted in bounded chunks by other workers, which re-read exactly those
   lines and refuse unless the file's size and modification time are unchanged.
4. **Every kept row** (train or not, exactly as the reference) is strict-JSON parsed,
   built as a ``CanonicalDocument`` and must have the membership doc id and the exact
   C05 content digest. A C05-train row whose canonical split is not ``train`` refuses.
   Train rows are counted with :meth:`BaseTokenizer.count_valid_targets` (the
   reference rule without offset construction; equality is tested).
5. Results are placed by membership position, never by completion order, so worker
   count and scheduling cannot change any byte. Export, signing and publication are
   the parent's alone: stage directory, fsync, re-read verification, one rename.

Interruption or failure terminates and reaps every worker and removes exactly the
files this job created; no complete output directory can exist unless publication
succeeded. Resume is not supported (a rerun starts over).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import psutil

from xlm.artifacts.manifest import ensure_plain_path
from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import signed, verify_signed
from xlm.data.exclusion.fitfast import (
    Membership,
    OwnedPaths,
    StreamedC05,
    open_streamed,
    reconcile,
    stream_membership,
)
from xlm.data.exclusion.fitscan import (
    FileRef,
    MembershipTables,
    OrderedPool,
    _cancel_check,
    init_worker,
    walk_rows,
)
from xlm.data.exclusion.inputs import contained
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import MiB, NullProgress, RunProgress
from xlm.data.exclusion.selection import (
    COUNT_RULE,
    MAX_TOKENIZER_FILE_BYTES,
    MAX_TOKENIZER_FILES,
    allocation_key,
    binding_of,
    load_tokenizer,
    tokenizer_identity,
)
from xlm.data.exclusion.supervisor import Deadline, Supervisor
from xlm.data.exclusion.tokenizer_fit import _Reporter as FitReporter

if TYPE_CHECKING:
    from xlm.tokenizers.base import BaseTokenizer

LABEL = "COUNT"
WORKER_CHOICES = (1, 2, 4, 8, 16)
# A file with more kept text than this is scanned first, then counted in chunks.
SPLIT_TEXT_BYTES = 24 * MiB
# Byte span re-read by one chunk task (a single longer line forms its own chunk).
CHUNK_SPAN_BYTES = 16 * MiB
# Text handed to one native batch call (bounds worker memory).
BATCH_TEXT_BYTES = 4 * MiB
EXPORT_BUFFER_BYTES = 8 * MiB
HASH_BLOCK = 8 * MiB


# -- worker side ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CountTables:
    """Sent once to every worker: membership tables and the private tokenizer copy."""

    membership: MembershipTables
    tokenizer: str
    fingerprint: str


_TOKENIZER: BaseTokenizer | None = None
_TOKENIZER_ERROR: str | None = None


def init_count_worker(tables: CountTables) -> None:
    """Pool initializer: single-threaded native tokenizer bound to its snapshot fingerprint."""
    global _TOKENIZER, _TOKENIZER_ERROR
    init_worker(tables.membership)  # Also pins TOKENIZERS_PARALLELISM/RAYON to one thread.
    _TOKENIZER, _TOKENIZER_ERROR = None, None
    try:
        tokenizer = load_tokenizer(Path(tables.tokenizer))
    except (OSError, ValueError, KeyError, TypeError):
        _TOKENIZER_ERROR = "count tokenizer snapshot cannot be loaded"
        return
    if tokenizer.fingerprint != tables.fingerprint:
        _TOKENIZER_ERROR = "count tokenizer differs from its verified identity"
        return
    _TOKENIZER = tokenizer


def _tokenizer() -> BaseTokenizer:
    if _TOKENIZER is None:
        raise C05Error(_TOKENIZER_ERROR or "count tokenizer was not initialized")
    return _TOKENIZER


@dataclass(frozen=True)
class KeptRows:
    """Membership expectations for consecutive kept rows of one file."""

    ids: list[bytes]
    content: bytes  # 32 bytes per row: the C05 content digest
    assigned: bytes  # one split code per row (0 = train)

    def __len__(self) -> int:
        return len(self.ids)


@dataclass(frozen=True)
class FileTask:
    """One plan file: full hash pass; ``count`` also verifies and counts its kept rows."""

    ordinal: int
    path: str
    sha256: str
    file_bytes: int
    documents: int
    line_ceiling: int
    rows: npt.NDArray[np.uint32]
    kept: KeptRows | None  # None: locate only (a chunked file)
    block_bytes: int = 0


@dataclass(frozen=True)
class ChunkTask:
    """Consecutive kept rows of a scanned file, re-read by byte location."""

    ordinal: int
    first: int
    path: str
    file_bytes: int
    mtime_ns: int
    offsets: npt.NDArray[np.uint64]
    lengths: npt.NDArray[np.uint32]
    kept: KeptRows


@dataclass
class CountResult:
    kind: str  # "file", "scan" or "chunk"
    ordinal: int
    first: int
    tokens: npt.NDArray[np.int64]  # -1 for kept rows that are not train
    offsets: npt.NDArray[np.uint64] | None = None
    lengths: npt.NDArray[np.uint32] | None = None
    mtime_ns: int = 0


class _Counter:
    """Batches verified train texts into bounded native count calls."""

    def __init__(self, tokens: npt.NDArray[np.int64]) -> None:
        self.tokens = tokens
        self.positions: list[int] = []
        self.texts: list[str] = []
        self.size = 0

    def add(self, position: int, text: str) -> None:
        self.positions.append(position)
        self.texts.append(text)
        self.size += len(text)
        if self.size >= BATCH_TEXT_BYTES:
            self.flush()

    def flush(self) -> None:
        if not self.texts:
            return
        _cancel_check()
        counts = _tokenizer().count_valid_targets(self.texts)
        if len(counts) != len(self.texts):
            raise C05Error("count tokenizer returned a different number of counts")
        self.tokens[self.positions] = counts
        self.positions, self.texts, self.size = [], [], 0


def _verified_text(line: bytes, doc_id: bytes, content: bytes, assigned: int) -> str | None:
    """The reference checks for one kept row; returns the text of a train row.

    Strict canonical JSON, a valid ``CanonicalDocument``, the membership doc id at this
    location and the exact C05 content digest (every kept row); a C05-train row's
    canonical split must be ``train``.
    """
    try:
        value = canonical.loads_bytes_strict(line)
    except canonical.CanonicalError as exc:
        raise C05Error("canonical record is not strict canonical JSON") from exc
    if type(value) is not dict:
        raise C05Error("canonical record must be an object")
    try:
        document = CanonicalDocument(**value)
    except (TypeError, ValueError) as exc:
        raise C05Error("canonical record is invalid") from exc
    if type(document.doc_id) is not str or document.doc_id.encode("utf-8") != doc_id:
        raise C05Error("source row differs from its C05 membership location")
    if hashlib.sha256(canonical.canonical_bytes(document.to_dict())).digest() != content:
        raise C05Error("record differs from C05 kept membership")
    if assigned != 0:
        return None
    if document.split != "train":
        raise C05Error("record split differs from C05 membership")
    if type(document.text) is not str:
        raise C05Error("canonical record text is invalid")
    return document.text


def _count_lines(
    lines: Iterator[tuple[int, bytes]], kept: KeptRows, tokens: npt.NDArray[np.int64]
) -> None:
    counter = _Counter(tokens)
    for position, line in lines:
        start = 32 * position
        text = _verified_text(
            line, kept.ids[position], kept.content[start : start + 32], kept.assigned[position]
        )
        if text is not None:
            counter.add(position, text)
    counter.flush()


def count_file(task: FileTask) -> CountResult:
    """Hash the whole file once; verify and count its kept rows, or only locate them."""
    path = Path(task.path)
    before = path.stat()
    count = len(task.rows)
    tokens = np.full(count, -1, dtype=np.int64)
    walk = walk_rows(
        path,
        task.sha256,
        task.file_bytes,
        task.documents,
        task.line_ceiling,
        task.rows.tolist(),
        task.block_bytes,
    )
    if task.kept is not None:
        if len(task.kept) != count:
            raise C05Error("count task rows are inconsistent")
        _count_lines(((p, line) for p, line, _ in walk), task.kept, tokens)
        return CountResult("file", task.ordinal, 0, tokens)
    offsets = np.zeros(count, dtype=np.uint64)
    lengths = np.zeros(count, dtype=np.uint32)
    for position, line, offset in walk:
        offsets[position] = offset
        lengths[position] = len(line)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise C05Error("input file changed while it was verified")
    return CountResult("scan", task.ordinal, 0, tokens, offsets, lengths, after.st_mtime_ns)


def count_chunk(task: ChunkTask) -> CountResult:
    """Re-read exactly the located kept lines of a verified, unchanged file and count them."""
    path = Path(task.path)

    def unchanged() -> None:
        now = path.stat()
        if (now.st_size, now.st_mtime_ns) != (task.file_bytes, task.mtime_ns):
            raise C05Error("input file changed after it was verified")

    unchanged()
    count = len(task.kept)
    if count == 0 or len(task.offsets) != count or len(task.lengths) != count:
        raise C05Error("count chunk is inconsistent")
    start = int(task.offsets[0])
    end = int(task.offsets[-1]) + int(task.lengths[-1])
    if end > task.file_bytes:
        raise C05Error("count chunk lies outside its file")
    with path.open("rb", buffering=0) as stream:
        stream.seek(start)
        data = stream.read(end - start)
    if len(data) != end - start:
        raise C05Error("input file changed after it was verified")
    offsets, lengths = task.offsets.tolist(), task.lengths.tolist()

    def lines() -> Iterator[tuple[int, bytes]]:
        for position in range(count):
            at = offsets[position] - start
            line = data[at : at + lengths[position]]
            final = offsets[position] + lengths[position] == task.file_bytes
            if (not line.endswith(b"\n") and not final) or b"\n" in line[:-1]:
                raise C05Error("count chunk line boundary differs from the verified pass")
            yield position, line

    tokens = np.full(count, -1, dtype=np.int64)
    _count_lines(lines(), task.kept, tokens)
    unchanged()
    return CountResult("chunk", task.ordinal, task.first, tokens)


def run_task(task: FileTask | ChunkTask) -> CountResult:
    return count_chunk(task) if isinstance(task, ChunkTask) else count_file(task)


# -- parent: tokenizer snapshot ---------------------------------------------------------------


@dataclass
class TokenizerSnapshot:
    files: dict[str, bytes]
    identity: dict[str, Any]
    copy: Path

    def digests(self) -> dict[str, str]:
        return {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}


def _read_tokenizer_files(directory: Path) -> dict[str, bytes]:
    entries = sorted(directory.iterdir())
    if len(entries) > MAX_TOKENIZER_FILES:
        raise C05Error("tokenizer artifact file ceiling")
    files: dict[str, bytes] = {}
    for entry in entries:
        if not entry.is_file() or entry.stat().st_size > MAX_TOKENIZER_FILE_BYTES:
            raise C05Error("tokenizer artifact entry is not a bounded regular file")
        with entry.open("rb") as stream:
            data = stream.read(MAX_TOKENIZER_FILE_BYTES + 1)
        if len(data) > MAX_TOKENIZER_FILE_BYTES:
            raise C05Error("tokenizer artifact entry is not a bounded regular file")
        files[entry.name] = data
    return files


def _directory_digests(directory: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256(data).hexdigest()
        for name, data in _read_tokenizer_files(directory).items()
    }


def snapshot_tokenizer(
    directory: Path, view: StreamedC05, scratch: Path, owned: OwnedPaths
) -> TokenizerSnapshot:
    """Reference identity of the tokenizer, bound to bytes read once, copied privately."""
    files = _read_tokenizer_files(directory)
    _, identity = tokenizer_identity(directory, view)
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    if canonical.digest(digests) != identity["files_digest"]:
        raise C05Error("tokenizer changed while its identity was read")
    copy = owned.directory(scratch / f"count-tokenizer-{uuid.uuid4().hex}")
    for name, data in files.items():
        target = owned.file(copy / name)
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(target, stat.S_IREAD)
    if _directory_digests(copy) != digests:
        raise C05Error("private tokenizer copy differs from its snapshot")
    return TokenizerSnapshot(files, identity, copy)


# -- parent: scheduling ---------------------------------------------------------------------


def count_tables(view: StreamedC05) -> tuple[MembershipTables, list[str]]:
    """Membership tables with no fit ranking: every allocation of the frozen plan."""
    keys = sorted(
        {allocation_key(i.component, i.view, i.upstream_component) for i in view.plan.files}
    )
    position = {key: n for n, key in enumerate(keys)}
    files: dict[str, FileRef] = {}
    for ordinal, item in enumerate(view.plan.files):
        key = allocation_key(item.component, item.view, item.upstream_component)
        files[item.path] = FileRef(
            ordinal=ordinal,
            allocation=position[key],
            component=item.component,
            view=item.view,
            upstream=item.upstream_component,
            source_id=item.source_id,
            documents=item.documents,
        )
    if len(files) != len(view.plan.files):
        raise C05Error("duplicate canonical file")
    tables = MembershipTables(
        files=files,
        allocation_keys=tuple(keys),
        seed=0,
        fit_document_cap=-1,  # No row is ranked: counting needs no fit order.
        line_ceiling=view.plan.resources.document_bytes,
        rank_tag="count-tokens-no-rank",
        contract=view.plan.output_contract,
    )
    return tables, keys


@dataclass
class _FileState:
    positions: npt.NDArray[np.int64]
    pending: int = 0
    done: bool = False


@dataclass
class _Telemetry:
    """Content-free resource sampling for progress lines (display only)."""

    supervisor: Supervisor
    output: Path
    scratch_bytes: int
    process: psutil.Process = field(default_factory=psutil.Process)
    last: tuple[float, float] | None = None

    def __call__(self) -> dict[str, int | float]:
        rss = cpu = 0.0
        try:
            tree = [self.process, *self.process.children(recursive=True)]
        except psutil.Error:
            tree = [self.process]
        for process in tree:
            try:
                rss += process.memory_info().rss
                times = process.cpu_times()
                cpu += times.user + times.system
            except psutil.Error:
                continue
        now = time.monotonic()
        values: dict[str, int | float] = {
            "rss": int(rss),
            "peak_rss": max(int(rss), self.supervisor.peak_rss),
            "ram_limit": int(self.supervisor.ram_ceiling or 0),
            "scratch": self.scratch_bytes,
        }
        if self.last is not None and now > self.last[0]:
            share = (cpu - self.last[1]) / (now - self.last[0]) / (psutil.cpu_count() or 1)
            values["cpu_percent"] = round(max(0.0, 100.0 * share), 1)
        self.last = (now, cpu)
        try:
            values["free"] = shutil.disk_usage(_existing(self.output)).free
        except OSError:
            pass
        return values


def _existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


class _Reporter(FitReporter):
    """Adapter for the shared membership stream: COUNT stage names, COUNT notes.

    Telemetry is attached separately (:class:`_Telemetry`); nothing else is inherited.
    """

    NAMES = {"MEMBERSHIP STREAM": "MEMBERSHIP VERIFY"}

    def __init__(self, progress: RunProgress | NullProgress) -> None:
        self.progress = progress

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        self.progress.stage(self.NAMES.get(name, name), total, unit)

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        self.progress.update(done, force=force, **fields)

    def note(self, text: str) -> None:
        """Fixed explanatory line; callers pass literals and numbers only."""
        if isinstance(self.progress, RunProgress):
            line = (
                json.dumps({"event": "note", "label": LABEL, "text": text}, sort_keys=True)
                if self.progress.fmt == "jsonl"
                else f"[{LABEL}] {text}"
            )
            print(line, file=self.progress.stream or sys.stderr, flush=True)


def _kept(m: Membership, positions: npt.NDArray[np.int64]) -> KeptRows:
    starts = m.id_offsets[positions].tolist()
    ends = m.id_offsets[positions + 1].tolist()
    return KeptRows(
        ids=[m.ids[s:e] for s, e in zip(starts, ends, strict=True)],
        content=np.ascontiguousarray(m.content[positions]).tobytes(),
        assigned=np.ascontiguousarray(m.split[positions]).tobytes(),
    )


def _chunks(
    offsets: npt.NDArray[np.uint64], lengths: npt.NDArray[np.uint32]
) -> Iterator[tuple[int, int]]:
    """Consecutive (start, end) row ranges whose byte span stays within the chunk bound."""
    ends = offsets.astype(np.int64) + lengths.astype(np.int64)
    count, start = len(offsets), 0
    while start < count:
        limit = int(offsets[start]) + CHUNK_SPAN_BYTES
        end = max(start + 1, int(np.searchsorted(ends, limit, side="right")))
        yield start, end
        start = end


def source_count(
    view: StreamedC05,
    m: Membership,
    pool: OrderedPool,
    progress: RunProgress | NullProgress,
    *,
    block_bytes: int = 0,
) -> npt.NDArray[np.int64]:
    """Count every kept train row; returns tokens by membership position (-1 = not train)."""
    plan = view.plan
    root = Path(plan.data_root)
    order = np.lexsort((m.row, m.file)).astype(np.int64)
    bounds = np.searchsorted(m.file[order], np.arange(len(plan.files) + 1, dtype=np.uint32))
    tokens = np.full(m.rows, -2, dtype=np.int64)  # -2: not yet counted
    train = m.split == 0
    total_docs = int(np.count_nonzero(train))
    total_text = int(m.nbytes[train].sum(dtype=np.uint64))
    total_bytes = sum(f.file_bytes for f in plan.files)
    states: list[_FileState] = []
    large: list[int] = []
    small: list[int] = []
    for ordinal in range(len(plan.files)):
        positions = order[bounds[ordinal] : bounds[ordinal + 1]]
        states.append(_FileState(positions))
        text = int(m.nbytes[positions].sum(dtype=np.uint64))
        (large if text > SPLIT_TEXT_BYTES else small).append(ordinal)
    # Longest first: large files are scanned early so their chunks balance the tail.
    large.sort(key=lambda n: -plan.files[n].file_bytes)
    small.sort(key=lambda n: -plan.files[n].file_bytes)
    files_queue = deque([*large, *small])

    def file_task(ordinal: int, *, locate_only: bool) -> FileTask:
        item = plan.files[ordinal]
        positions = states[ordinal].positions
        return FileTask(
            ordinal=ordinal,
            path=str(contained(root, item.path)),
            sha256=item.documents_sha256,
            file_bytes=item.file_bytes,
            documents=item.documents,
            line_ceiling=plan.resources.document_bytes,
            rows=np.ascontiguousarray(m.row[positions]),
            kept=None if locate_only else _kept(m, positions),
            block_bytes=block_bytes,
        )

    capacity = 2 * pool.workers
    scan_limit = max(1, pool.workers // 4)
    chunks: deque[ChunkTask] = deque()
    # Parent-side identity of every submitted task; a result must echo it exactly.
    in_flight: dict[Future[CountResult], tuple[str, int, int]] = {}
    scans = 0
    large_set = set(large)
    progress.stage("SOURCE COUNT", total_docs, "docs", work_total=total_text)
    begun = time.monotonic()
    counted_docs = counted_text = hashed = files_done = results = 0

    def accept(positions: npt.NDArray[np.int64], result: CountResult) -> None:
        nonlocal counted_docs, counted_text
        values = result.tokens
        if len(values) != len(positions):
            raise C05Error("count result size differs from its task")
        expected_train = train[positions]
        if not np.array_equal(values >= 0, expected_train) or np.any(values < -1):
            raise C05Error("count result disagrees with C05 train membership")
        if np.any(tokens[positions] != -2):
            raise C05Error("kept record counted twice")
        tokens[positions] = values
        counted_docs += int(np.count_nonzero(expected_train))
        counted_text += int(m.nbytes[positions[expected_train]].sum(dtype=np.uint64))

    while files_queue or chunks or in_flight:
        while len(in_flight) < capacity:
            if chunks:
                chunk = chunks.popleft()
                in_flight[pool.submit(run_task, chunk)] = ("chunk", chunk.ordinal, chunk.first)
            elif files_queue and (files_queue[0] not in large_set or scans < scan_limit):
                ordinal = files_queue.popleft()
                locate = ordinal in large_set
                scans += locate
                task = file_task(ordinal, locate_only=locate)
                in_flight[pool.submit(run_task, task)] = ("scan" if locate else "file", ordinal, 0)
            else:
                break
        for future in pool.completed(set(in_flight)):
            results += 1
            kind, ordinal, first = in_flight.pop(future)
            result = future.result()
            if (result.kind, result.ordinal, result.first) != (kind, ordinal, first):
                raise C05Error("count result differs from its task")
            state = states[ordinal]
            item = plan.files[ordinal]
            if kind in ("file", "scan"):
                hashed += item.file_bytes
            if kind == "file":
                accept(state.positions, result)
                state.done = True
                files_done += 1
            elif kind == "scan":
                scans -= 1
                if result.offsets is None or result.lengths is None:
                    raise C05Error("count scan returned no locations")
                if len(result.offsets) != len(state.positions):
                    raise C05Error("count scan located a different number of kept rows")
                kept = _kept(m, state.positions)
                for start, end in _chunks(result.offsets, result.lengths):
                    chunks.append(
                        ChunkTask(
                            ordinal=result.ordinal,
                            first=start,
                            path=str(contained(root, item.path)),
                            file_bytes=item.file_bytes,
                            mtime_ns=result.mtime_ns,
                            offsets=result.offsets[start:end],
                            lengths=result.lengths[start:end],
                            kept=KeptRows(
                                kept.ids[start:end],
                                kept.content[32 * start : 32 * end],
                                kept.assigned[start:end],
                            ),
                        )
                    )
                    state.pending += 1
                if state.pending == 0:
                    state.done = True
                    files_done += 1
            else:
                accept(state.positions[first : first + len(result.tokens)], result)
                state.pending -= 1
                if state.pending == 0:
                    state.done = True
                    files_done += 1
            elapsed = max(time.monotonic() - begun, 1e-9)
            progress.update(
                counted_docs,
                work=counted_text,
                files_committed=files_done,
                files_total=len(plan.files),
                bytes_done=hashed,
                bytes_total=total_bytes,
                gb_per_s=hashed / elapsed / 1e9,
                workers=pool.workers,
                busy=min(pool.workers, len(in_flight)),
                tasks=len(in_flight),
                capacity=capacity,
                results=results,
                queued=len(chunks),
            )
    if not all(s.done for s in states) or hashed != total_bytes:
        raise C05Error("exact counts do not cover every plan file")
    if np.any(tokens == -2) or counted_docs != total_docs:
        raise C05Error("exact counts do not cover every kept training record")
    return tokens


# -- parent: export, verification, publication ---------------------------------------------


def _row(fragment: bytes, doc_id: str, content: str, tokens: int) -> bytes:
    """Exactly ``canonical_bytes({allocation, content, doc_id, valid_targets}) + LF``.

    Keys in sorted order; the doc id is serialized with the same ``json`` encoder
    settings as :func:`canonical.canonical_bytes`; ``fragment`` is the canonical
    allocation list. Equality with the reference serializer is tested.
    """
    return b"".join(
        (
            b'{"allocation":',
            fragment,
            b',"content":"',
            content.encode("ascii"),
            b'","doc_id":',
            json.dumps(doc_id, ensure_ascii=False).encode("utf-8"),
            b',"valid_targets":',
            str(tokens).encode("ascii"),
            b"}\n",
        )
    )


def aggregate(
    m: Membership, tokens: npt.NDArray[np.int64], keys: list[str]
) -> tuple[dict[str, dict[str, int]], int]:
    """Per-allocation train documents and valid targets (the reference totals)."""
    train = m.split == 0
    if np.any(tokens[train] < 0) or np.any(tokens[~train] != -1):
        raise C05Error("exact counts do not cover every kept training record")
    documents = np.bincount(m.allocation[train], minlength=len(keys))
    targets = np.zeros(len(keys), dtype=np.int64)
    np.add.at(targets, m.allocation[train], tokens[train])
    allocations = {
        keys[n]: {"documents": int(documents[n]), "valid_targets": int(targets[n])}
        for n in range(len(keys))
        if documents[n]
    }
    return allocations, int(np.count_nonzero(train))


def export_counts(
    path: Path,
    m: Membership,
    tokens: npt.NDArray[np.int64],
    keys: list[str],
    ceiling: int,
    progress: RunProgress | NullProgress,
) -> tuple[str, int]:
    """Rows in ascending doc-id byte order (= the reference ``ORDER BY id``); one fsync."""
    train = np.flatnonzero(m.split == 0)
    progress.stage("EXPORT COUNTS", len(train), "rows")
    fragments = [canonical.canonical_bytes(canonical.loads_strict(k)) for k in keys]
    digest = hashlib.sha256()
    written = 0
    buffer: list[bytes] = []
    buffered = 0
    starts = m.id_offsets[train].tolist()
    ends = m.id_offsets[train + 1].tolist()
    allocation = m.allocation[train].tolist()
    values = tokens[train].tolist()
    content = m.content[train]
    with path.open("xb") as stream:
        for n in range(len(train)):
            row = _row(
                fragments[allocation[n]],
                m.ids[starts[n] : ends[n]].decode("utf-8"),
                content[n].tobytes().hex(),
                values[n],
            )
            written += len(row)
            if written > ceiling:
                raise C05Error("selection artifact output ceiling")
            buffer.append(row)
            buffered += len(row)
            if buffered >= EXPORT_BUFFER_BYTES:
                block = b"".join(buffer)
                stream.write(block)
                digest.update(block)
                buffer, buffered = [], 0
                progress.update(n + 1, written_bytes=written)
        block = b"".join(buffer)
        stream.write(block)
        digest.update(block)
        progress.update(len(train), written_bytes=written)
        progress.stage("FSYNC", None, "steps")
        stream.flush()
        os.fsync(stream.fileno())
    return digest.hexdigest(), written


def _file_sha(path: Path, progress: RunProgress | NullProgress, size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            total += len(block)
            progress.update(total, bytes_done=total, bytes_total=size)
    return digest.hexdigest(), total


# Fixed bytes of one counts row besides the allocation fragment, the doc id and the
# count digits: '{"allocation":' ',"content":"' 64 hex '","doc_id":' two id quotes
# ',"valid_targets":' '}' LF.
ROW_FIXED_BYTES = 14 + 12 + 64 + 11 + 2 + 17 + 2


def preflight_output(
    stage: Path,
    owned: OwnedPaths,
    *,
    names: tuple[str, ...] = ("counts.jsonl", "counts.json"),
    probe: bytes = b"count-tokens preflight\n",
) -> None:
    """Probe every late filesystem operation in seconds, before any counting.

    The output parent is created as the reference does (``stage.mkdir(parents=True)``).
    The owned staging directory then exists for the whole job; artifact names pass the
    same link/junction check as ``write_once``; a probe file is written, fsynced and
    hard-linked (``write_once`` publishes by hard link) and the staging directory is
    renamed and back (publication is one directory rename). Probes are removed.
    """
    stage.parent.mkdir(parents=True, exist_ok=True)
    owned.directory(stage)
    for name in names:
        ensure_plain_path(stage / name)
    probe_path = owned.file(stage / "preflight.probe")
    linked = owned.file(stage / "preflight.link")
    with probe_path.open("xb") as stream:
        stream.write(probe)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(probe_path, linked)
    linked.unlink()
    probe_path.unlink()
    moved = owned.directory(stage.with_name(stage.name + "-preflight"), create=False)
    os.rename(stage, moved)
    os.rename(moved, stage)


def export_lower_bound(m: Membership, keys: list[str]) -> int:
    """Smallest possible ``counts.jsonl`` size: one count digit, unescaped ids."""
    train = m.split == 0
    fragments = np.asarray(
        [len(canonical.canonical_bytes(canonical.loads_strict(k))) for k in keys], dtype=np.int64
    )
    id_bytes = np.diff(m.id_offsets.astype(np.int64))[train]
    rows = int(np.count_nonzero(train))
    return int(
        ROW_FIXED_BYTES * rows + fragments[m.allocation[train]].sum() + id_bytes.sum() + rows
    )


def preflight_export(m: Membership, keys: list[str], stage: Path, ceiling: int) -> None:
    """Refuse before counting only when the export is already guaranteed to fail."""
    needed = export_lower_bound(m, keys)
    if needed > ceiling:
        raise C05Error("exact counts would exceed the reviewed output ceiling")
    if shutil.disk_usage(stage).free < needed:
        raise C05Error("output volume lacks space for the exact counts")


def count_tokens_fast(
    proof: Path,
    tokenizer_dir: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    scratch: Path,
    workers: int,
    progress: RunProgress | NullProgress | None = None,
    consumes: list[Path | str] | None = None,
    inline: bool = False,
    block_bytes: int = 0,
    allow_authored: bool = True,
) -> dict[str, Any]:
    """Count every kept training record exactly (see module doc); returns the envelope."""
    if workers not in WORKER_CHOICES:
        raise C05Error("count workers must be 1, 2, 4, 8 or 16")
    progress = progress or NullProgress()
    progress.stage("PROOF VERIFY", None, "steps")
    view = open_streamed(
        proof,
        allow_authored=allow_authored,
        consumes=[tokenizer_dir, scratch, output, *(consumes or [])],
    )
    if view.trusted.get(issuer) != key:
        raise C05Error("allocation signer is not trusted")
    _check_roots(view, tokenizer_dir, scratch, output)
    if output.exists():
        raise C05Error("selection artifacts are write-once")
    owned = OwnedPaths()
    stage = output.with_name(output.name + f".partial-{uuid.uuid4().hex}")
    started = time.monotonic()
    current = ["OUTPUT PREFLIGHT"]

    def begin(name: str, total: int | None = None, unit: str = "steps") -> None:
        current[0] = name
        progress.stage(name, total, unit)

    try:
        begin("OUTPUT PREFLIGHT")
        preflight_output(stage, owned)
        scratch.mkdir(parents=True, exist_ok=True)
        begin("TOKENIZER VERIFY")
        snapshot = snapshot_tokenizer(tokenizer_dir, view, scratch, owned)
        membership, keys = count_tables(view)
        tables = CountTables(membership, str(snapshot.copy), str(snapshot.identity["fingerprint"]))
        supervisor = Supervisor(Deadline(None, started), view.plan.resources.ram_bytes)
        with supervisor:
            if isinstance(progress, RunProgress):
                progress.attach(
                    _Telemetry(supervisor, output, sum(len(d) for d in snapshot.files.values()))
                )
            with OrderedPool(
                workers, tables, supervisor, inline=inline, initializer=init_count_worker
            ) as pool:
                current[0] = "MEMBERSHIP VERIFY"
                m = stream_membership(view, membership, pool, _Reporter(progress), supervisor, {})
                reconcile(view, m, keys)
                preflight_export(m, keys, stage, view.plan.resources.output_bytes)
                current[0] = "SOURCE COUNT"
                tokens = source_count(view, m, pool, progress, block_bytes=block_bytes)
            supervisor.check()
            begin("AGGREGATE")
            allocations, counted = aggregate(m, tokens, keys)
            current[0] = "EXPORT COUNTS"
            counts_path = owned.file(stage / "counts.jsonl")
            sha, size = export_counts(
                counts_path, m, tokens, keys, view.plan.resources.output_bytes, progress
            )
            envelope = signed(
                {
                    "kind": "c05_exact_token_counts_v1",
                    **binding_of(view),
                    "tokenizer": snapshot.identity,
                    "rule": COUNT_RULE,
                    "documents": counted,
                    "allocations": dict(sorted(allocations.items())),
                    "counts_sha256": sha,
                    "counts_bytes": size,
                },
                issuer,
                key,
            )
            current[0] = "SIGN"
            write_once(owned.file(stage / "counts.json"), envelope)
            begin("VERIFY", size, "bytes")
            if _file_sha(counts_path, progress, size) != (sha, size):
                raise C05Error("exact count artifact changed")
            verify_signed(envelope, view.trusted)
            digests = snapshot.digests()
            if _directory_digests(tokenizer_dir) != digests:
                raise C05Error("tokenizer changed after counting started")
            if _directory_digests(snapshot.copy) != digests:
                raise C05Error("private tokenizer copy changed during counting")
            supervisor.check()
            begin("PUBLISH")
            if output.exists():
                raise C05Error("selection artifacts are write-once")
            os.rename(stage, output)  # The staged names are gone; cleanup skips them.
        progress.complete()
        return envelope
    except Exception as exc:
        # A fixed stage literal for the content-free CLI refusal (no paths or values).
        setattr(exc, "count_stage", current[0])  # noqa: B010
        raise
    finally:
        owned.cleanup()


def _check_roots(view: StreamedC05, tokenizer_dir: Path, scratch: Path, output: Path) -> None:
    """Scratch and output never overlap the corpus, C05 output, tokenizer or each other."""
    from xlm.data.exclusion.isolation import overlaps

    immutable = (
        ("C05 data root", view.plan.data_root),
        ("C05 completion", str(view.directory)),
        ("tokenizer", str(tokenizer_dir)),
    )
    for name, path in (("count scratch", scratch), ("count output", output)):
        for role, other in immutable:
            if overlaps(path, other):
                raise C05Error(f"{name} overlaps the {role}")
    if overlaps(scratch, output):
        raise C05Error("count scratch overlaps the count output")
