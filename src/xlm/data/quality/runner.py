"""Phase-A audit orchestration under ONE whole-command supervisor.

``Guard`` wraps the shared process-tree supervisor from command dispatch until the
end: argument checks, proof/overlay validation, resume validation and source
re-hashing, scanning, aggregation, artifact writing and receipt staging. It
enforces, independently of progress output, an absolute monotonic deadline, the
whole process-tree RSS ceiling and the free-space reserve; every write is precharged
against the output ceiling.

The COMPLETE receipt (the sole completion signal) is published by a two-phase
protocol (:func:`_publish_receipt`): it is built in memory and staged (written,
flushed, fsynced) while the monitor still runs; the monitor is then stopped and
joined; ``Guard.final`` reconciles every failure ever recorded and re-measures the
deadline, process-tree RSS, free space, output bytes and supervisor state; only then
is the staged receipt atomically renamed into place. A synchronous post-publication
gate follows and withdraws the receipt (directory fsynced) if it fails. Success is
returned to the caller only after that gate passes.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.envelope import QUEUE_FACTOR
from xlm.data.quality.outputs import OutputTree, overlaps
from xlm.data.quality.overlay import KeptOverlay, load_overlay
from xlm.data.quality.policy import REVIEW_PER_STRATUM
from xlm.data.quality.progress import Reporter, Telemetry
from xlm.data.quality.receipt import build_receipt, load_receipt
from xlm.data.quality.report import ARTIFACTS, build_artifacts
from xlm.data.quality.review import (
    MAX_EXCERPT_CHARS,
    MAX_MATERIALIZE_DOCUMENTS,
    ReviewError,
    ReviewWriter,
    check_destination,
    check_review_row,
    excerpt,
    iter_review_rows,
    review_key,
)
from xlm.data.quality.scan import (
    BINDING_FILE,
    CHUNK_BYTES,
    MAX_LINE_CEILING,
    MAX_MANIFEST_BYTES,
    POLL_SECONDS,
    RECEIPT_FILE,
    WORKER_CHOICES,
    AuditFile,
    FileAccumulator,
    InputManifest,
    OrderedPool,
    OutputBudget,
    QualityError,
    audit_binding,
    commit_unit,
    file_tasks,
    implementation,
    load_manifest,
    load_unit,
    parse_row,
    process_chunk,
    read_bounded,
    unit_path,
    verify_source,
)

MAX_RSS_BYTES = 16 * 1024**3
MAX_DEADLINE_SECONDS = 7 * 86400
SUPERVISOR_INTERVAL = 0.25
PUBLICATION_MARGIN = 0.5
VERIFY_THREADS = 2
MAX_PENDING_COMMITS = 4


@dataclass(frozen=True)
class Limits:
    workers: int
    max_rss_bytes: int
    free_reserve_bytes: int
    max_output_bytes: int
    line_ceiling: int
    deadline_seconds: float

    def check(self) -> None:
        if self.workers not in WORKER_CHOICES:
            raise QualityError("workers must be 1, 2, 4, 8, 12 or 16")
        if not 0 < self.max_rss_bytes <= MAX_RSS_BYTES:
            raise QualityError("--max-rss-gib must be in (0, 16]")
        if not 0 < self.line_ceiling <= MAX_LINE_CEILING:
            raise QualityError("--max-document-mib outside its bound")
        if self.max_output_bytes <= 0 or self.free_reserve_bytes < 0:
            raise QualityError("output limits must be positive")
        deadline = self.deadline_seconds
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, int | float)
            or not math.isfinite(deadline)
            or not 0 < deadline <= MAX_DEADLINE_SECONDS
        ):
            raise QualityError("--deadline-hours outside (0, 168]")

    def envelope(self) -> dict[str, Any]:
        """The effective operational envelope (strictly validated), recorded in units and
        the receipt."""
        from xlm.data.quality.envelope import EnvelopeError, validate_envelope

        body = self._envelope_body()
        try:
            validate_envelope(body)
        except EnvelopeError:
            raise QualityError("operational envelope is invalid") from None
        return body

    def _envelope_body(self) -> dict[str, Any]:
        return {
            "workers": self.workers,
            "queue_tasks": QUEUE_FACTOR * self.workers if self.workers > 1 else 1,
            "max_rss_bytes": self.max_rss_bytes,
            "free_reserve_bytes": self.free_reserve_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_document_bytes": self.line_ceiling,
            "deadline_seconds": float(self.deadline_seconds),
            "chunk_bytes": CHUNK_BYTES,
            "verify_threads": VERIFY_THREADS,
            "max_pending_commits": MAX_PENDING_COMMITS,
            "supervisor_interval_seconds": SUPERVISOR_INTERVAL,
            "publication_margin_seconds": PUBLICATION_MARGIN,
            "review_per_stratum": REVIEW_PER_STRATUM,
        }


# -- whole-command supervisor ---------------------------------------------------------------


def _existing(path: Path) -> Path:
    current = Path(path).absolute()
    while not current.exists():
        if current.parent == current:
            raise QualityError("no existing ancestor for a watched path")
        current = current.parent
    return current


def _tree_rss() -> int:
    from xlm.data.exclusion.supervisor import tree_rss

    return int(tree_rss())


def _free_bytes(path: str) -> int:
    return int(shutil.disk_usage(path).free)


class Guard:
    """One supervisor from dispatch to publication (deadline, tree RSS, free space)."""

    def __init__(
        self,
        *,
        deadline_seconds: float,
        started: float,
        max_rss_bytes: int,
        watch: Sequence[Path],
        reserve_bytes: int,
    ) -> None:
        from xlm.data.exclusion.supervisor import Deadline, Supervisor

        self.started = started
        self.max_rss_bytes = max_rss_bytes
        self.volumes = {str(_existing(p)): reserve_bytes for p in watch}
        self.stopped = False
        self.supervisor = Supervisor(
            Deadline(deadline_seconds, started),
            max_rss_bytes,
            interval=SUPERVISOR_INTERVAL,
            volumes=dict(self.volumes),
            warn=lambda _message: None,
        )

    def __enter__(self) -> Guard:
        self.supervisor.__enter__()
        try:
            self.check()
        except BaseException:
            self.supervisor.__exit__(QualityError, None, None)
            raise
        return self

    def __exit__(self, kind: Any, value: Any, traceback: Any) -> None:
        try:
            self.supervisor.__exit__(kind, value, traceback)
        finally:
            self.stopped = True

    def final(self, margin: float, output_check: Callable[[], None] | None = None) -> None:
        """THE single authoritative final-success gate, only after shutdown.

        Reconciles every failure the monitor recorded (before or during shutdown), then
        takes FRESH measurements: deadline margin, whole process-tree RSS, free space on
        every watched volume and (optionally) the output byte accounting. Any failure is
        recorded and refuses; nothing may be published unless this returns.
        """
        from xlm.data.exclusion.supervisor import DEADLINE_REASON, DISK_REASON, RSS_REASON

        if not self.stopped:
            raise QualityError("final gate requires a stopped supervisor")
        self.check()  # recorded failures and the deadline
        remaining = self.supervisor.remaining()
        if remaining is not None and remaining < margin:
            self.supervisor.fail(DEADLINE_REASON)
        rss = _tree_rss()
        self.supervisor.peak_rss = max(self.supervisor.peak_rss, rss)
        if rss > self.max_rss_bytes:
            self.supervisor.fail(RSS_REASON)
        for path, reserve in self.volumes.items():
            free = _free_bytes(path)
            minimum = self.supervisor.min_free
            minimum[path] = min(free, minimum.get(path, free))
            if free < reserve:
                self.supervisor.fail(DISK_REASON)
        self.check()
        if output_check is not None:
            output_check()
        self.check()

    def failed(self) -> bool:
        return self.supervisor.failure is not None

    def check(self) -> None:
        from xlm.data.exclusion.policy import C05Error
        from xlm.data.exclusion.supervisor import DEADLINE_REASON, DISK_REASON, RSS_REASON

        try:
            self.supervisor.check()
        except C05Error as exc:
            reasons = {
                RSS_REASON: "process-tree RSS exceeded --max-rss-gib; nothing published",
                DISK_REASON: "free space fell below --free-reserve-gib; nothing published",
                DEADLINE_REASON: "deadline exceeded; nothing published (units kept for resume)",
            }
            raise QualityError(
                reasons.get(str(exc), "supervisor refused; nothing published")
            ) from None

    @property
    def peak_rss(self) -> int:
        return int(self.supervisor.peak_rss)

    def elapsed(self) -> float:
        """Supervised elapsed seconds since dispatch (the deadline's own clock)."""
        return round(time.monotonic() - self.started, 3)

    def free_space(self) -> list[dict[str, int]]:
        """Measured minimum free bytes per watched volume (path-free), with its reserve."""
        observed = self.supervisor.min_free
        if set(observed) != set(self.volumes):
            raise QualityError("free space was not measured on every watched volume")
        return [
            {"reserve_bytes": reserve, "min_observed_free_bytes": int(observed[path])}
            for path, reserve in sorted(self.volumes.items())
        ]

    def unit_facts(self) -> dict[str, Any]:
        """Measured facts recorded in each committed unit."""
        return {
            "elapsed_seconds": float(self.elapsed()),
            "peak_process_tree_rss_bytes": self.peak_rss,
            "min_observed_free_bytes": min(
                (v["min_observed_free_bytes"] for v in self.free_space()), default=0
            ),
        }


# -- inputs and outputs -----------------------------------------------------------------------


def _proof_inputs(proof: Path) -> list[Path]:
    """Every public C05 path named by a proof specification (all are protected inputs)."""
    from xlm.data.exclusion.transport import ProofSpec

    try:
        spec = ProofSpec.model_validate(
            canonical.loads_bytes_strict(read_bounded(proof, MAX_MANIFEST_BYTES, "C05 proof"))
        )
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("C05 proof specification is not valid") from None
    return [
        proof,
        Path(spec.plan),
        Path(spec.manifest),
        Path(spec.completion),
        Path(spec.trust),
        Path(spec.scratch),
    ]


def _check_output(output: Path, manifest: InputManifest, inputs: Sequence[Path]) -> None:
    """The output may live under the data root (as C05/C06 outputs do), but never in,
    above or below a directory that holds an audited corpus file or any audit input."""
    out = output.resolve()
    root = manifest.data_root.resolve()
    if root.is_relative_to(out):
        raise QualityError("output directory contains the corpus data root")
    for parent in sorted({(root / f.path).parent for f in manifest.files}):
        if overlaps(out, parent):
            raise QualityError("output directory overlaps a corpus file directory")
    for item in inputs:
        if overlaps(out, Path(item)):
            raise QualityError("output directory overlaps an audit input (manifest/proof/C05)")


@dataclass
class Prepared:
    manifest: InputManifest
    overlay: KeptOverlay | None
    binding: dict[str, Any]
    identity: dict[str, str]
    protected: list[Path]


def prepare(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None,
    proof: Path | None,
    allow_authored_proof: bool,
    line_ceiling: int,
    check: Callable[[], None] | None = None,
    telemetry: Telemetry | None = None,
) -> Prepared:
    manifest = load_manifest(manifest_path, data_root)
    if telemetry is not None:
        telemetry.set_totals(manifest.totals)
    inputs: list[Path] = [manifest_path]
    if proof is not None:
        inputs += _proof_inputs(proof)
    _check_output(output, manifest, inputs)
    overlay = None
    if proof is not None:
        overlay = load_overlay(
            proof,
            manifest_digest=manifest.digest,
            documents={f.path: f.documents for f in manifest.files},
            allow_authored=allow_authored_proof,
            consumes=[manifest.data_root, output],
            check=check,
            telemetry=telemetry,
        )
        inputs += [Path(p) for p in overlay.forbidden_roots]
        if telemetry is not None:
            telemetry.set_phase("prepare")
    identity = implementation()
    binding = audit_binding(manifest, overlay, line_ceiling, identity)
    protected = [*inputs, *{(manifest.data_root / f.path).parent for f in manifest.files}]
    return Prepared(manifest, overlay, binding, identity, protected)


def _read_binding(output: Path) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(
            read_bounded(output / BINDING_FILE, MAX_MANIFEST_BYTES, "audit binding")
        )
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("audit binding is not strict canonical JSON") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("audit binding self-digest mismatch")
    return body


def _tree(output: Path, protected: Sequence[Path], budget: OutputBudget | None) -> OutputTree:
    def charge(size: int) -> None:
        if budget is None:
            raise QualityError("read-only verification never writes")
        budget.charge(size)

    return OutputTree(
        output,
        marker=BINDING_FILE,
        owned=(RECEIPT_FILE, *ARTIFACTS),
        protected=protected,
        charge=charge,
        staged=(RECEIPT_FILE,),
    )


# -- source verification ----------------------------------------------------------------------


def verify_sources(
    root: Path,
    files: Iterable[AuditFile],
    *,
    threads: int,
    check: Callable[[], None] | None,
    progress: Callable[[int], None] | None = None,
) -> None:
    """Re-hash every file against its frozen SHA-256/size/rows (bounded threads)."""
    items = list(files)
    if not items:
        return
    stop = threading.Event()

    def cooperative() -> None:
        if stop.is_set():
            raise QualityError("source verification stopped")
        if check is not None:
            check()

    with ThreadPoolExecutor(max_workers=max(1, min(threads, len(items)))) as pool:
        futures = [
            pool.submit(verify_source, root, item, check=cooperative, progress=progress)
            for item in items
        ]
        try:
            for future in futures:
                while True:
                    if check is not None:
                        check()
                    try:
                        future.result(timeout=POLL_SECONDS)
                        break
                    except FutureTimeout:
                        continue
        finally:
            stop.set()  # siblings stop at their next block instead of finishing their file
            for future in futures:
                future.cancel()


def stream_units(
    output: Path,
    manifest: InputManifest,
    digest: str,
    identities: list[dict[str, Any]],
    *,
    check: Callable[[], None] | None = None,
    rehash: bool = True,
    threads: int = VERIFY_THREADS,
    envelopes: list[dict[str, Any]] | None = None,
    facts: list[dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Verified units one at a time, in manifest order (never all in memory).

    With ``rehash`` every source file is first re-hashed against the frozen manifest
    (SHA-256, size, rows), so a stale unit can never describe changed source bytes.
    ``identities`` collects each file's content-free source identity for the receipt.
    Every unit's measured facts are checked against its own producer envelope;
    ``facts`` collects them for the receipt's cross-checks.
    """
    if rehash:
        verify_sources(manifest.data_root, manifest.files, threads=threads, check=check)
    for item in manifest.files:
        if check is not None:
            check()
        unit = load_unit(output, item, digest)
        identities.append(
            {
                "path": item.path,
                "documents_sha256": item.documents_sha256,
                "file_bytes": item.file_bytes,
                "documents": item.documents,
            }
        )
        if envelopes is not None and unit["producer_envelope"] not in envelopes:
            envelopes.append(unit["producer_envelope"])
        check_unit_against_producer(unit)
        if facts is not None:
            facts.append(
                {
                    "max_line_bytes": unit["max_line_bytes"],
                    **unit["producer_facts"],
                }
            )
        yield unit


def check_unit_against_producer(unit: Mapping[str, Any]) -> None:
    """A unit's measured facts must lie inside the envelope that produced it."""
    from xlm.data.quality.receipt import check_envelope

    envelope = unit["producer_envelope"]
    check_envelope(envelope)
    facts = unit["producer_facts"]
    if (
        unit["max_line_bytes"] > envelope["max_document_bytes"]
        or facts["peak_process_tree_rss_bytes"] > envelope["max_rss_bytes"]
        or facts["elapsed_seconds"] > envelope["deadline_seconds"]
        or facts["min_observed_free_bytes"] < envelope["free_reserve_bytes"]
    ):
        raise QualityError("audit unit measured facts contradict its producer envelope")
    # Independently accumulated statistics: the largest canonical text cannot exceed
    # the largest row, and the largest row cannot exceed all row bytes together.
    longest = unit["max_line_bytes"]
    text_max = [p["metrics"]["utf8_bytes"]["max"] for p in unit["populations"].values()]
    line_total = sum(int(p["line_bytes"]) for p in unit["populations"].values())
    if any(v is not None and v > longest for v in text_max) or longest > line_total:
        raise QualityError("audit unit largest row contradicts its statistics")


# -- audit ----------------------------------------------------------------------------------


def run_audit(
    manifest_path: Path,
    output: Path,
    *,
    limits: Limits,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    progress_interval: float | None = 5.0,
    progress_log: Path | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """The audit. Progress (stderr every ``progress_interval`` seconds unless None, and
    optionally appended to ``progress_log``) is operational only."""
    started = time.monotonic() if started is None else started
    limits.check()
    envelope = limits.envelope()
    if progress_log is not None:
        check_progress_log(progress_log, output, manifest_path, data_root, proof)
    telemetry = Telemetry(limits.workers, started)
    reporter = Reporter(
        telemetry,
        5.0 if progress_interval is None else progress_interval,
        stderr=progress_interval is not None,
        log=progress_log,
    )
    staged: list[OutputTree] = []
    try:
        with reporter:
            with Guard(
                deadline_seconds=limits.deadline_seconds,
                started=started,
                max_rss_bytes=limits.max_rss_bytes,
                watch=[output],
                reserve_bytes=limits.free_reserve_bytes,
            ) as guard:
                ready = prepare(
                    manifest_path,
                    output,
                    data_root=data_root,
                    proof=proof,
                    allow_authored_proof=allow_authored_proof,
                    line_ceiling=limits.line_ceiling,
                    check=guard.check,
                    telemetry=telemetry,
                )
                manifest, binding = ready.manifest, ready.binding
                guard.check()
                budget = OutputBudget(limits.max_output_bytes)
                tree = _tree(output, ready.protected, budget)
                tree.open(create=True)
                budget.used = tree.used_bytes()
                budget.charge(0)
                if (output / RECEIPT_FILE).exists():
                    raise QualityError(
                        "audit already complete in this output directory; use `report`"
                    )
                if (output / BINDING_FILE).exists():
                    if _read_binding(output) != binding:
                        raise QualityError(
                            "output directory belongs to a different audit binding (code, policy, "
                            "manifest or overlay changed); use a new output directory"
                        )
                else:
                    tree.write(BINDING_FILE, canonical.canonical_bytes(binding))
                tree.remove_owned_staging()
                telemetry.set_output_bytes(tree.used_bytes())
                digest = str(binding["digest"])
                resumed = [f for f in manifest.files if unit_path(output, f.ordinal).exists()]
                if resumed:
                    telemetry.set_phase(
                        "resume-verify", sum(f.file_bytes for f in resumed), "bytes"
                    )
                    for item in resumed:
                        load_unit(output, item, digest)
                    verify_sources(
                        manifest.data_root,
                        resumed,
                        threads=VERIFY_THREADS,
                        check=guard.check,
                        progress=telemetry.advance,
                    )
                    telemetry.resumed(
                        len(resumed),
                        sum(f.documents for f in resumed),
                        sum(f.file_bytes for f in resumed),
                    )
                done = {f.ordinal for f in resumed}
                pending = [f for f in manifest.files if f.ordinal not in done]
                measured, activity = _scan(
                    tree, manifest, ready.overlay, pending, digest, limits, guard, telemetry, budget
                )
                telemetry.set_phase("aggregate", len(manifest.files), "units")
                identities: list[dict[str, Any]] = []
                producers: list[dict[str, Any]] = []
                units_seen: list[dict[str, Any]] = []
                artifacts, result_digest = build_artifacts(
                    binding,
                    _counted(
                        stream_units(
                            output,
                            manifest,
                            digest,
                            identities,
                            check=guard.check,
                            rehash=False,  # resumed files were re-hashed above; fresh at commit
                            envelopes=producers,
                            facts=units_seen,
                        ),
                        telemetry.advance,
                    ),
                )
                guard.check()
                telemetry.set_phase("write", len(ARTIFACTS), "artifacts")
                for name in ARTIFACTS:
                    tree.write(name, artifacts[name])
                    telemetry.advance(1)
                    telemetry.set_output_bytes(budget.used)
                    guard.check()
                telemetry.set_phase("publish")
                guard.check()
                # Phase 1, monitor still active: the receipt is built entirely in memory from
                # measured facts and written to the owned staging directory only.
                execution = {
                    "files_scanned": len(pending),
                    "files_resumed": len(resumed),
                    "wall_seconds": guard.elapsed(),
                    **measured,
                    "peak_process_tree_rss_bytes": guard.peak_rss,
                    "supervisor_samples": int(guard.supervisor.samples),
                    "free_space": guard.free_space(),
                    "output_bytes_before_receipt": tree.used_bytes(),
                    "max_document_bytes_observed": max(
                        (int(u["max_line_bytes"]) for u in units_seen), default=0
                    ),
                    "note": "execution facts are operational and excluded from result_digest",
                }
                raw = build_receipt(
                    binding=binding,
                    manifest_path=manifest_path,
                    implementation=ready.identity,
                    sources=identities,
                    artifacts=artifacts,
                    result_digest=result_digest,
                    envelope=envelope,
                    producer_envelopes=sorted(producers, key=canonical.canonical_bytes),
                    execution=execution,
                )
                guard.check()
                staged.append(tree)
                tree.stage(RECEIPT_FILE, raw)
                guard.check()
            # Phase 2: the monitor is stopped and joined (by leaving the guard).
            _publish_receipt(guard, tree, raw, limits.max_output_bytes)
            telemetry.set_output_bytes(tree.used_bytes())
            telemetry.set_phase("complete")
    except BaseException:
        for tree in staged:  # a staged receipt is never a completion signal; discard it
            with contextlib.suppress(OSError, QualityError):
                tree.discard(RECEIPT_FILE)
        raise
    return {
        "complete": True,
        "result_digest": result_digest,
        "output": str(output),
        "files_scanned": len(pending),
        "files_resumed": len(resumed),
        "wall_seconds": execution["wall_seconds"],
        "scan": {
            **measured,
            "peak_process_tree_rss_bytes": execution["peak_process_tree_rss_bytes"],
        },
        "activity": activity,
        "phase_seconds": telemetry.phase_seconds(),
        "note": "scan.activity and phase_seconds are operational, not in the receipt",
    }


def _counted(units: Iterator[dict[str, Any]], advance: Callable[[int], None]) -> Iterator[Any]:
    for unit in units:
        yield unit
        advance(1)


def check_progress_log(
    log: Path, output: Path, manifest_path: Path, data_root: Path | None, proof: Path | None
) -> None:
    """The progress log is operational: outside the audit output, the corpus data root
    and every audit input; an existing regular file (appended to) or a new file in an
    existing directory; never a link."""
    log = Path(log)
    parent = log.absolute().parent
    if not parent.is_dir():
        raise QualityError("progress log directory does not exist")
    if log.is_symlink() or (log.exists() and not log.is_file()):
        raise QualityError("progress log must be a regular file")
    target = parent.resolve() / log.name
    if target.is_relative_to(output.resolve()):
        raise QualityError("progress log must be outside the audit output directory")
    manifest = load_manifest(manifest_path, data_root)
    if target.is_relative_to(manifest.data_root.resolve()):
        raise QualityError("progress log must be outside the corpus data root")
    inputs = [manifest_path, *([] if proof is None else _proof_inputs(proof))]
    for item in inputs:
        if overlaps(target, Path(item)):
            raise QualityError("progress log overlaps an audit input (manifest/proof/C05)")


def _publish_receipt(
    guard: Guard,
    tree: OutputTree,
    raw: bytes,
    max_output_bytes: int,
    name: str = RECEIPT_FILE,
) -> None:
    """Reconcile, re-measure, atomically publish the staged receipt, then re-check.

    Nothing but the atomic rename happens between the pre-publication gate and the
    post-publication gate. Any failure leaves no COMPLETE receipt: before the rename
    the staged copy is discarded; after it the receipt is withdrawn (directory
    fsynced) and the command refuses.
    """

    def output_check() -> None:
        if tree.used_bytes() > max_output_bytes:
            raise QualityError("output byte ceiling (--max-output-gib) would be exceeded")

    published = False
    try:
        tree.verify_staged(name, raw)
        guard.final(PUBLICATION_MARGIN, output_check)  # reconcile + fresh measurements
        tree.publish(name)  # the ONLY step between the two gates
        published = True
        guard.final(0.0, output_check)  # immediate post-publication synchronous gate
        if guard.failed():
            raise QualityError("supervisor refused; nothing published")
    except BaseException:
        if published:
            tree.withdraw(name)  # an error here propagates: the command refuses
        with contextlib.suppress(OSError, QualityError):
            tree.discard(name)
        raise


def _scan(
    tree: OutputTree,
    manifest: InputManifest,
    overlay: KeptOverlay | None,
    pending: list[AuditFile],
    digest: str,
    limits: Limits,
    guard: Guard,
    telemetry: Telemetry,
    budget: OutputBudget,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measure every pending file; return (receipt execution facts, operational activity)."""
    root = manifest.data_root
    started = time.monotonic()
    envelope = limits.envelope()
    key = review_key(manifest.digest)
    scanned_bytes = sum(f.file_bytes for f in pending)
    telemetry.set_phase("scan", scanned_bytes, "bytes")
    stop = threading.Event()

    def check() -> None:
        if stop.is_set():
            raise QualityError("scan stopped")
        guard.check()

    def tasks() -> Iterator[Any]:
        for item in pending:
            kept = identity = None
            if overlay is not None:
                kept, identity = overlay.kept[item.path], overlay.identity[item.path]
            yield from file_tasks(root, item, kept, limits.line_ceiling, identity=identity, key=key)

    waiting: deque[tuple[FileAccumulator, Future[set[int]]]] = deque()

    def drain(block: bool) -> None:
        while waiting and (block or waiting[0][1].done() or len(waiting) > MAX_PENDING_COMMITS):
            acc, future = waiting[0]
            while True:
                guard.check()
                try:
                    future.result(timeout=POLL_SECONDS)  # bracketing re-hash after measurement
                    break
                except FutureTimeout:
                    continue
            waiting.popleft()
            commit_unit(tree, digest, acc, envelope, guard.unit_facts())
            telemetry.committed(budget.used, len(waiting))

    by_ordinal = {f.ordinal: f for f in pending}
    current: FileAccumulator | None = None
    spans: list[tuple[float, float, int]] = []
    verifier = ThreadPoolExecutor(max_workers=VERIFY_THREADS)
    pool = OrderedPool(limits.workers, guard, telemetry)
    try:
        with pool:
            for result in pool.map(process_chunk, tasks()):
                if current is None or current.item.ordinal != result.ordinal:
                    current = FileAccumulator(by_ordinal[result.ordinal])
                current.add(result)
                spans.append((result.started, result.finished, result.pid))
                telemetry.measured(
                    result.rows,
                    result.nbytes,
                    result.finished - result.started,
                    result.cpu_seconds,
                    result.pid,
                )
                if result.last:
                    # Measurement of this file is complete: re-hash it again before commit.
                    waiting.append(
                        (current, verifier.submit(verify_source, root, current.item, check=check))
                    )
                    telemetry.verifying(len(waiting))
                    current = None
                drain(False)
        telemetry.set_phase("verify-drain", len(waiting), "files")
        drain(True)
    finally:
        stop.set()
        verifier.shutdown(wait=True, cancel_futures=True)
    seconds = time.monotonic() - started
    scanned_docs = sum(f.documents for f in pending)
    measured = {
        "scan_seconds": round(seconds, 3),
        "scanned_documents": scanned_docs,
        "scanned_file_bytes": scanned_bytes,
        "file_mb_per_s": round(scanned_bytes / 1e6 / max(seconds, 1e-9), 3),
        "documents_per_s": round(scanned_docs / max(seconds, 1e-9), 1),
        "workers": pool.workers,
        "peak_tasks_in_flight": pool.peak_in_flight,
    }
    return measured, worker_activity(spans, pool.workers, seconds, telemetry)


def worker_activity(
    spans: Sequence[tuple[float, float, int]],
    workers: int,
    seconds: float,
    telemetry: Telemetry,
) -> dict[str, Any]:
    """Measured (not configured) parallelism: which processes executed chunk tasks, the
    largest number executing at the same instant, and how busy the workers were."""
    events = sorted([(a, 1) for a, _, _ in spans] + [(b, -1) for _, b, _ in spans])
    concurrent = peak = 0
    for _, delta in events:
        concurrent += delta
        peak = max(peak, concurrent)
    snap = telemetry.snapshot()
    busy = sum(b - a for a, b, _ in spans)
    steady = max(b for _, b, _ in spans) - min(a for a, _, _ in spans) if spans else 0.0
    return {
        "chunks": len(spans),
        "worker_span_seconds": round(steady, 3),
        "worker_processes_used": len({pid for _, _, pid in spans}),
        "max_concurrent_tasks": peak,
        "worker_busy_seconds": round(busy, 3),
        "worker_cpu_seconds": round(float(snap["worker_cpu_seconds"]), 3),
        "worker_busy_fraction": round(busy / (workers * seconds), 4) if seconds > 0 else None,
    }


# -- report / verification ------------------------------------------------------------------


def _verify(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None,
    proof: Path | None,
    allow_authored_proof: bool,
    rehash_sources: bool,
    workers: int,
    guard: Guard,
) -> tuple[dict[str, Any], Prepared, dict[str, Any]]:
    receipt = load_receipt(output, ARTIFACTS, RECEIPT_FILE)
    binding_on_disk = _read_binding(output)
    if receipt["binding"] != binding_on_disk:
        raise QualityError("receipt binding differs from the output directory binding")
    ready = prepare(
        manifest_path,
        output,
        data_root=data_root,
        proof=proof,
        allow_authored_proof=allow_authored_proof,
        line_ceiling=int(binding_on_disk["line_ceiling"]),
        check=guard.check,
    )
    if ready.binding != binding_on_disk:
        raise QualityError("current code/policy/manifest/overlay differ from the audit binding")
    tree = _tree(output, ready.protected, None)
    tree.open(create=False)
    identities: list[dict[str, Any]] = []
    producers: list[dict[str, Any]] = []
    unit_facts: list[dict[str, Any]] = []
    artifacts, result_digest = build_artifacts(
        ready.binding,
        stream_units(
            output,
            ready.manifest,
            str(ready.binding["digest"]),
            identities,
            check=guard.check,
            rehash=rehash_sources,
            threads=workers,
            envelopes=producers,
            facts=unit_facts,
        ),
    )
    if identities != receipt["source_files"]:
        raise QualityError("source file identities differ from the receipt")
    if sorted(producers, key=canonical.canonical_bytes) != receipt["producer_envelopes"]:
        raise QualityError("unit producer envelopes differ from the receipt")
    if result_digest != receipt["result_digest"]:
        raise QualityError("re-derived result digest differs from the receipt")
    for name, data in artifacts.items():
        with (output / name).open("rb") as stream:
            on_disk = stream.read(len(data) + 1)  # bounded: one byte past the derivation
        if on_disk != data:
            raise QualityError(f"artifact {name} differs from its re-derivation")
    _verify_envelope_facts(output, tree, ready.manifest, receipt, artifacts, unit_facts, guard)
    result = {
        "verified": True,
        "result_digest": result_digest,
        "artifacts": len(artifacts),
        "sources_rehashed": rehash_sources,
    }
    return result, ready, receipt


def _verify_envelope_facts(
    output: Path,
    tree: OutputTree,
    manifest: InputManifest,
    receipt: Mapping[str, Any],
    artifacts: Mapping[str, bytes],
    unit_facts: Sequence[Mapping[str, Any]],
    guard: Guard,
) -> None:
    """Re-derive what actually happened and check it against the receipt envelope.

    The receipt's own validation already compared its claimed facts with its
    envelope; here the facts themselves are re-derived from the verified files
    (artifacts, units, binding, review manifest), never trusted from the receipt.
    """
    envelope, execution = receipt["envelope"], receipt["execution"]

    def require(condition: bool, what: str) -> None:
        if not condition:
            raise QualityError(f"receipt contradicts the verified audit: {what}")

    # Output bytes: everything the audit publishes under --max-output-gib.
    units = sum(unit_path(output, f.ordinal).stat().st_size for f in manifest.files)
    before = (output / BINDING_FILE).stat().st_size + units
    before += sum(len(data) for data in artifacts.values())
    receipt_bytes = (output / RECEIPT_FILE).stat().st_size
    require(before == execution["output_bytes_before_receipt"], "output bytes")
    require(tree.used_bytes() == before + receipt_bytes, "unaccounted output bytes")
    require(before + receipt_bytes <= envelope["max_output_bytes"], "output ceiling")
    # Largest audited row (unit facts were checked against their producer envelopes).
    longest = max((int(f["max_line_bytes"]) for f in unit_facts), default=0)
    require(longest == execution["max_document_bytes_observed"], "largest audited row")
    require(longest <= envelope["max_document_bytes"], "document ceiling")
    if execution["files_resumed"] == 0:  # every unit was measured by this very run
        for fact in unit_facts:
            require(
                fact["peak_process_tree_rss_bytes"] <= execution["peak_process_tree_rss_bytes"]
                and fact["elapsed_seconds"] <= execution["wall_seconds"]
                and all(
                    fact["min_observed_free_bytes"] >= v["min_observed_free_bytes"]
                    for v in execution["free_space"]
                ),
                "unit facts differ from the run that produced them",
            )
    # Review manifest: re-read the verified file; strata, rows and row size limits.
    entry = receipt["artifacts"]["review-manifest.jsonl"]
    per_stratum: dict[tuple[str, str, int], int] = {}
    total = 0
    files = {f.path for f in manifest.files}
    for row in iter_review_rows(output / "review-manifest.jsonl", entry):
        guard.check()
        check_review_row(row)
        require(row["path"] in files, "review row outside the manifest")
        key = (row["component"], row["detector"], row["coarse_bin"])
        per_stratum[key] = per_stratum.get(key, 0) + 1
        total += 1
    require(total == entry["records"], "review row count")
    require(
        all(count <= envelope["review_per_stratum"] for count in per_stratum.values()),
        "review rows per stratum exceed the envelope",
    )


def verify_report(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    workers: int = VERIFY_THREADS,
    max_rss_bytes: int = 8 * 1024**3,
    deadline_seconds: float = 6 * 3600.0,
    started: float | None = None,
) -> dict[str, Any]:
    """Validate the receipt, re-hash every source, re-derive every artifact."""
    with Guard(
        deadline_seconds=deadline_seconds,
        started=time.monotonic() if started is None else started,
        max_rss_bytes=max_rss_bytes,
        watch=[output],
        reserve_bytes=0,
    ) as guard:
        result, _, _ = _verify(
            manifest_path,
            output,
            data_root=data_root,
            proof=proof,
            allow_authored_proof=allow_authored_proof,
            rehash_sources=True,
            workers=workers,
            guard=guard,
        )
    guard.final(0.0)
    return result


# -- operator review materialization --------------------------------------------------------


@dataclass(frozen=True)
class ReviewLimits:
    max_documents: int = 500
    max_chars: int = 20_000
    max_output_bytes: int = 512 * 1024**2
    max_rss_bytes: int = 8 * 1024**3
    free_reserve_bytes: int = 1024**3
    deadline_seconds: float = 3 * 3600.0

    def check(self) -> None:
        if not 0 < self.max_documents <= MAX_MATERIALIZE_DOCUMENTS:
            raise ReviewError("max documents outside its bound")
        if not 0 < self.max_chars <= MAX_EXCERPT_CHARS:
            raise ReviewError("max characters outside its bound")
        if not 0 < self.max_output_bytes <= 8 * 1024**3:
            raise ReviewError("review output ceiling outside its bound")
        if not 0 < self.max_rss_bytes <= MAX_RSS_BYTES:
            raise ReviewError("review RSS ceiling outside its bound")
        if not 0 < self.deadline_seconds <= MAX_DEADLINE_SECONDS:
            raise ReviewError("review deadline outside its bound")


def materialize_from_audit(
    output: Path,
    destination: Path,
    *,
    limits: ReviewLimits,
    manifest_path: Path | None = None,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    roles: Sequence[str] | None = None,
    detectors: Sequence[str] | None = None,
    components: Sequence[str] | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """OPERATOR ONLY. Copy bounded excerpts of selected review rows to a NEW directory.

    Order of trust: strict COMPLETE receipt -> exact binding (current code, policy,
    manifest, overlay) -> artifacts re-derived from units -> review manifest SHA/size/
    record count from the receipt -> every row's schema and frozen-manifest file ->
    full re-hash of every selected source file with each (offset, row) locator proven
    -> each located row's SHA-256 and doc_id digest. Only then is text written.
    """
    limits.check()
    with Guard(
        deadline_seconds=limits.deadline_seconds,
        started=time.monotonic() if started is None else started,
        max_rss_bytes=limits.max_rss_bytes,
        watch=[destination],
        reserve_bytes=limits.free_reserve_bytes,
    ) as guard:
        receipt = load_receipt(output, ARTIFACTS, RECEIPT_FILE)
        manifest_file = manifest_path or Path(receipt["input_manifest"]["path"])
        _, ready, receipt = _verify(
            manifest_file,
            output,
            data_root=data_root,
            proof=proof,
            allow_authored_proof=allow_authored_proof,
            rehash_sources=False,  # selected sources are re-hashed below, with locators
            workers=VERIFY_THREADS,
            guard=guard,
        )
        manifest = ready.manifest
        files = {f.path: f for f in manifest.files}
        selected: list[dict[str, Any]] = []
        rows = iter_review_rows(
            output / "review-manifest.jsonl", receipt["artifacts"]["review-manifest.jsonl"]
        )
        for row in rows:
            guard.check()
            check_review_row(row)
            if row["path"] not in files:
                raise ReviewError("review row names a file outside the audited manifest")
            if (
                (roles is None or set(row["roles"]) & set(roles))
                and (detectors is None or row["detector"] in detectors)
                and (components is None or row["component"] in components)
            ):
                selected.append(row)
                if len(selected) >= limits.max_documents:
                    break
        check_destination(
            destination,
            [manifest.data_root, output, *ready.protected],
        )
        probes: dict[str, dict[int, int]] = {}
        for row in selected:
            probes.setdefault(row["path"], {})[row["offset"]] = row["row"]
        for path, wanted in sorted(probes.items()):
            proven = verify_source(
                manifest.data_root, files[path], check=guard.check, probes=wanted
            )
            if proven != set(wanted):
                raise ReviewError("a review locator does not start its recorded row")
        writer = ReviewWriter(destination, limits.max_output_bytes)
        line_ceiling = int(ready.binding["line_ceiling"])
        try:
            for row in selected:
                guard.check()
                with (manifest.data_root / row["path"]).open("rb") as stream:
                    stream.seek(row["offset"])
                    line = stream.readline(line_ceiling + 1)
                if len(line) > line_ceiling:
                    raise ReviewError("located row exceeds the document ceiling")
                body = line[:-1] if line.endswith(b"\n") else line

                if hashlib.sha256(body).hexdigest() != row["row_sha256"]:
                    raise ReviewError("located row bytes differ from the audited row")
                document = parse_row(body)
                if (
                    hashlib.sha256(document["doc_id"].encode("utf-8")).hexdigest()
                    != row["doc_id_sha256"]
                ):
                    raise ReviewError("located row does not hold the recorded document")
                text, omitted = excerpt(document["text"], limits.max_chars)
                writer.add(
                    {
                        **{k: row[k] for k in sorted(row)},
                        "doc_id": document["doc_id"],
                        "excerpt": text,
                        "omitted_chars": omitted,
                    }
                )
            writer.close()
        except BaseException:
            writer.abort()
            raise
    guard.final(0.0)
    return {"materialized": writer.count, "destination": str(destination)}
