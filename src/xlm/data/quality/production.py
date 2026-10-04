"""Phase-C PRODUCTION cleaning: DROP-only, one pass, resumable, bound to the approved dry run.

For every input canonical JSONL row the frozen ``cleaning_policy_v2`` decides KEEP or
DROP (the SAME worker kernel as the Phase-B dry run:
:func:`~xlm.data.quality.cleaning.measure_clean_chunk`, one detector pass per document).
KEEP rows are copied as their ORIGINAL line bytes. The parent writes the very chunk
bytes it read and hashed, minus the DROP rows' byte spans, so nothing is parsed back,
reserialized or normalized. DROP rows are omitted. Row order within each file is
preserved. Evaluation and writing happen in one pass.

Before any byte is written, :func:`prepare_production` verifies the frozen v2 policy,
the manifest and the approved dry run (:mod:`~xlm.data.quality.production_approval`),
the path mapping and every root overlap (:mod:`~xlm.data.quality.production_paths`).

Per-file publication (resume unit). Each output is written to an owned temporary file,
flushed, fsynced and closed. Then, in a verifier thread, the source file is fully
re-hashed against the manifest and the temporary file is re-read and re-hashed against
the bytes written. Then the file's complete decision statistics must equal the approved
dry-run unit; a mismatch is fatal. Then the temporary file is atomically renamed to
its final name (never over an existing file), and only then is the completed unit
committed to ``<state-output>/units/``. The unit holds the input identity, the policy
digest and code identity (through the binding), docs and canonical bytes in/kept/
dropped, output bytes and SHA-256, DROP rule accounting and the file's dropped-
membership rows.

Resume (rerun the identical command): the binding (manifest, policy, approved dry run,
code, output root, mapping) must be identical. Owned temporaries are deleted and never
count. Every committed unit is reloaded, its source re-hashed and its output re-hashed
against the unit; any difference refuses (never overwritten). A final file with no unit
(interrupted between rename and unit commit) is re-derived and adopted only if the
re-derived bytes are identical.

After every file: the artifacts (:mod:`~xlm.data.quality.production_report`) re-prove
the approved global, per-component and per-rule accounting; then the receipt is
published by the Phase-A two-phase protocol. No completion receipt exists unless all of
that passed. Operational records live under ``--state-output``, never in the corpus.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import (
    CleanChunkResult,
    CleanStats,
    CleanTask,
    process_clean_chunk,
    rule_names,
)
from xlm.data.quality.cleaning_policy import DROP, KEEP, REVIEW, CompiledPolicy, load_frozen
from xlm.data.quality.outputs import OutputTree, overlaps
from xlm.data.quality.policy import POLICY_VERSION, policy_identity
from xlm.data.quality.production_approval import (
    ApprovedDryRun,
    check_policy_for_manifest,
    source_identities,
    verify_approved_dry_run,
)
from xlm.data.quality.production_paths import (
    MAPPING_RULE,
    CorpusTree,
    MappedFile,
    build_mapping,
    check_roots,
    mapping_digest,
)
from xlm.data.quality.production_report import (
    ARTIFACTS,
    EMPTY_OUTPUT_POLICY,
    EXECUTION_NOTE,
    SEMANTICS,
    build_production_artifacts,
    build_receipt,
    drop_counts,
    validate_receipt,
)
from xlm.data.quality.progress import Reporter, Telemetry
from xlm.data.quality.runner import (
    MAX_PENDING_COMMITS,
    VERIFY_THREADS,
    Guard,
    Limits,
    _publish_receipt,
    verify_sources,
)
from xlm.data.quality.scan import (
    CHUNK_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_UNIT_FILE_BYTES,
    POLL_SECONDS,
    VERIFY_BLOCK_BYTES,
    AuditFile,
    InputManifest,
    OrderedPool,
    OutputBudget,
    QualityError,
    check_unit_facts,
    decode_unit,
    encode_unit,
    file_tasks,
    implementation,
    load_manifest,
    read_bounded,
    verify_source,
)

BINDING_FILE = "cleaning-production-binding.json"
RECEIPT_FILE = "cleaning-production-receipt.json"
VERIFICATION_FILE = "cleaning-production-verification.json"
CLEANED_MANIFEST_FILE = "cleaned-input-manifest.json"
BINDING_KIND = "xlm_quality_cleaning_production_binding_v1"
UNIT_KIND = "xlm_quality_cleaning_production_unit_v1"
PROGRESS_PREFIX = "[quality-clean-production]"
MAX_STATE_BYTES = 8 * 1024**3
MAX_RECEIPT_BYTES = 64 * 1024**2
ZERO_KEY = b"\0" * 32
GIB = 1024**3


# -- binding ----------------------------------------------------------------------------------


def production_binding(
    manifest: InputManifest,
    policy: CompiledPolicy,
    approved: ApprovedDryRun,
    mapping: tuple[MappedFile, ...],
    output_root: Path,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": BINDING_KIND,
        "semantics": SEMANTICS,
        "input_manifest": {
            "digest": manifest.digest,
            "file_sha256": manifest.file_sha256,
            "kind": manifest.kind,
            "mode": manifest.mode,
            **manifest.totals,
        },
        "data_root": manifest.data_root.resolve().as_posix(),
        "output_root": Path(output_root).resolve().as_posix(),
        "cleaning_policy": {
            "version": policy.version,
            "digest": policy.digest,
            "file_sha256": policy.file_sha256,
        },
        "detector_policy": {"version": POLICY_VERSION, "digest": policy_identity()},
        "approved_dry_run": approved.identity(),
        "implementation": {
            "code_identity": identity["code_identity"],
            "dependency_sha256": identity["dependency_sha256"],
        },
        "mapping": {"rule": MAPPING_RULE, "digest": mapping_digest(mapping), "files": len(mapping)},
        "empty_output_policy": EMPTY_OUTPUT_POLICY,
        "chunk_bytes": CHUNK_BYTES,
        "line_ceiling": approved.line_ceiling,
        "source_identity": "SHA-256 + size + rows, re-hashed on every use; never mtime",
    }
    body["digest"] = canonical.digest(body)
    return body


@dataclass
class ProductionSetup:
    manifest: InputManifest
    policy: CompiledPolicy
    approved: ApprovedDryRun
    mapping: tuple[MappedFile, ...]
    binding: dict[str, Any]
    identity: dict[str, str]
    state_protected: list[Path]
    corpus_protected: list[Path]


def prepare_production(
    manifest_path: Path,
    policy_path: Path,
    approved_dry_run: Path,
    output_root: Path,
    state_output: Path,
    *,
    approved_result_digest: str,
    data_root: Path | None,
    check: Callable[[], None] | None = None,
    telemetry: Telemetry | None = None,
) -> ProductionSetup:
    """Every check that must pass before any corpus byte is written (read-only)."""
    policy = load_frozen(policy_path)  # frozen status, self-digest, pinned v2 section
    manifest = load_manifest(manifest_path, data_root)
    if telemetry is not None:
        telemetry.set_totals(manifest.totals)
    inputs = [Path(manifest_path), Path(policy_path), Path(approved_dry_run)]
    check_roots(output_root, state_output, manifest, inputs)
    check_policy_for_manifest(policy, manifest)
    if telemetry is not None:
        telemetry.set_phase("dry-run-verify", len(manifest.files), "units")
    approved = verify_approved_dry_run(
        approved_dry_run,
        manifest,
        policy,
        approved_result_digest,
        check=check,
        progress=None if telemetry is None else telemetry.advance,
    )
    mapping = build_mapping(manifest.files)
    identity = implementation()
    binding = production_binding(manifest, policy, approved, mapping, output_root, identity)
    corpus_dirs = sorted({(manifest.data_root / f.path).parent for f in manifest.files})
    state_protected = [*inputs, *corpus_dirs, Path(output_root)]
    corpus_protected = [*inputs, manifest.data_root, *corpus_dirs, Path(state_output)]
    return ProductionSetup(
        manifest, policy, approved, mapping, binding, identity, state_protected, corpus_protected
    )


def _read_binding(state: Path) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(
            read_bounded(state / BINDING_FILE, MAX_MANIFEST_BYTES, "production binding")
        )
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("production binding is not strict canonical JSON") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("production binding self-digest mismatch")
    return body


def state_tree(state: Path, protected: list[Path], budget: OutputBudget | None) -> OutputTree:
    def charge(size: int) -> None:
        if budget is None:
            raise QualityError("read-only verification never writes the cleaning state")
        budget.charge(size)

    return OutputTree(
        state,
        marker=BINDING_FILE,
        owned=(RECEIPT_FILE, VERIFICATION_FILE, CLEANED_MANIFEST_FILE, *ARTIFACTS),
        protected=protected,
        charge=charge,
        staged=(RECEIPT_FILE,),
    )


# -- units ------------------------------------------------------------------------------------


def unit_name(ordinal: int) -> str:
    return f"units/f{ordinal:05d}.unit.zz"


def unit_path(state: Path, ordinal: int) -> Path:
    return state / "units" / f"f{ordinal:05d}.unit.zz"


ACCOUNTING_KEYS = frozenset(
    {
        "input_documents",
        "input_canonical_bytes",
        "input_file_bytes",
        "kept_documents",
        "kept_canonical_bytes",
        "dropped_documents",
        "dropped_canonical_bytes",
        "kept_line_bytes",
        "dropped_line_bytes",
    }
)
UNIT_KEYS = frozenset(
    {
        "kind",
        "binding",
        "policy_digest",
        "code_identity",
        "file",
        "output",
        "accounting",
        "drop_rules",
        "statistics",
        "dropped",
        "max_line_bytes",
        "producer_envelope",
        "producer_facts",
        "digest",
    }
)


def load_production_unit(
    state: Path,
    item: AuditFile,
    mapped: MappedFile,
    binding: Mapping[str, Any],
    policy: CompiledPolicy,
    approved: ApprovedDryRun,
) -> dict[str, Any]:
    """A committed unit, bound to this binding, file and output, whose decisions equal
    the approved dry run's (output bytes are checked separately, by re-hashing)."""
    from xlm.data.quality.receipt import check_envelope

    body = decode_unit(read_bounded(unit_path(state, item.ordinal), MAX_UNIT_FILE_BYTES, "unit"))

    def require(condition: bool, what: str) -> None:
        if not condition:
            raise QualityError(f"production unit {item.ordinal} invalid: {what}")

    require(set(body) == UNIT_KEYS, "schema")
    require(
        body["kind"] == UNIT_KIND and body["binding"] == binding["digest"],
        "belongs to a different binding (policy, code, manifest or dry run changed)",
    )
    require(body["policy_digest"] == binding["cleaning_policy"]["digest"], "policy digest")
    require(body["code_identity"] == binding["implementation"]["code_identity"], "code identity")
    require(body["file"] == item.record(), "file record differs from the manifest")
    check_unit_facts(body, item)
    check_envelope(body["producer_envelope"])
    stats = CleanStats.from_json(body["statistics"], policy.params.ruleset)
    require(
        body["statistics"] == approved.expected[item.ordinal],
        "decisions differ from the approved dry run",
    )
    acc, output = body["accounting"], body["output"]
    require(isinstance(acc, dict) and set(acc) == ACCOUNTING_KEYS, "accounting schema")
    require(all(type(v) is int and v >= 0 for v in acc.values()), "accounting schema")
    outcome = stats.arrays["outcome"]
    require(
        acc["input_documents"] == item.documents == stats.docs
        and acc["input_canonical_bytes"] == item.canonical_bytes == stats.bytes
        and acc["input_file_bytes"] == item.file_bytes
        and acc["kept_documents"] == int(outcome[KEEP, 0])
        and acc["kept_canonical_bytes"] == int(outcome[KEEP, 1])
        and acc["dropped_documents"] == int(outcome[DROP, 0]) == len(body["dropped"])
        and acc["dropped_canonical_bytes"] == int(outcome[DROP, 1])
        and int(outcome[REVIEW, 0]) == 0
        and acc["kept_line_bytes"] + acc["dropped_line_bytes"] == item.file_bytes
        and sum(r["canonical_bytes"] for r in body["dropped"]) == acc["dropped_canonical_bytes"]
        and sum(r["line_bytes"] for r in body["dropped"]) == acc["dropped_line_bytes"],
        "accounting identities",
    )
    require(
        isinstance(output, dict)
        and set(output) == {"path", "sha256", "file_bytes", "documents", "canonical_bytes"}
        and output["path"] == mapped.output_path
        and output["file_bytes"] == acc["kept_line_bytes"]
        and output["documents"] == acc["kept_documents"]
        and output["canonical_bytes"] == acc["kept_canonical_bytes"],
        "output record",
    )
    require(body["drop_rules"] == drop_counts(stats), "DROP rule accounting")
    for row in body["dropped"]:
        require(row["ordinal"] == item.ordinal and row["input_path"] == item.path, "drop row")
    return body


# -- per-file writer --------------------------------------------------------------------------


@dataclass
class FileWriter:
    """One input file being cleaned: statistics, drop rows and its owned temporary."""

    item: AuditFile
    mapped: MappedFile
    temp: Path
    ruleset: Any
    stream: IO[bytes] | None = None
    sha: Any = field(default_factory=hashlib.sha256)
    size: int = 0
    rows: int = 0
    canonical_bytes: int = 0
    max_line_bytes: int = 0
    dropped_line_bytes: int = 0
    drops: list[dict[str, Any]] = field(default_factory=list)
    stats: CleanStats | None = None

    def open(self) -> None:
        self.stats = CleanStats(self.ruleset)
        self.stream = self.temp.open("xb")  # exclusive: never over an existing file

    def add(self, result: CleanChunkResult, data: bytes, budget: OutputBudget) -> int:
        """Merge statistics and write the chunk's KEEP bytes (original bytes, in order)."""
        assert self.stream is not None and self.stats is not None
        if result.drops is None:
            raise QualityError("worker returned no drop record")
        self.rows += result.rows
        self.canonical_bytes += result.canonical_bytes
        self.max_line_bytes = max(self.max_line_bytes, result.max_line_bytes)
        for population in result.populations.values():
            self.stats.merge(population)
        view = memoryview(data)
        written = 0
        previous = 0
        segments: list[memoryview] = []
        for start, stop, row, offset, doc_id, row_sha, mask, nbytes in result.drops:
            if start < previous or stop > len(data):
                raise QualityError("drop spans are not ordered within the chunk")
            segments.append(view[previous:start])
            previous = stop
            self.dropped_line_bytes += stop - start
            self.drops.append(
                {
                    "ordinal": self.item.ordinal,
                    "input_path": self.item.path,
                    "component": self.item.component,
                    "row": row,
                    "offset": offset,
                    "line_bytes": stop - start,
                    "canonical_bytes": nbytes,
                    "doc_id_sha256": doc_id,
                    "row_sha256": row_sha,
                    "decision": "DROP",
                    "rules": rule_names(mask, self.ruleset),
                }
            )
        segments.append(view[previous:])
        for segment in segments:
            if not segment.nbytes:
                continue
            budget.charge(segment.nbytes)
            self.stream.write(segment)
            self.sha.update(segment)
            written += segment.nbytes
        self.size += written
        return written

    def close(self) -> None:
        if self.stream is None:
            return
        stream, self.stream = self.stream, None
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()

    def abort(self) -> None:
        with contextlib.suppress(OSError):
            self.close()
        with contextlib.suppress(OSError):
            if self.temp.exists():
                self.temp.unlink()


def hash_file(path: Path, check: Callable[[], None] | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(VERIFY_BLOCK_BYTES):
            if check is not None:
                check()
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


def _verify_written(root: Path, writer: FileWriter, check: Callable[[], None]) -> None:
    """Bracketing source re-hash, then the temporary output re-read against the bytes
    written (both before publication)."""
    verify_source(root, writer.item, check=check)
    if hash_file(writer.temp, check) != (writer.size, writer.sha.hexdigest()):
        raise QualityError("temporary cleaned output differs from the bytes written")


# -- the run ----------------------------------------------------------------------------------


def check_progress_log(
    log: Path,
    manifest_path: Path,
    policy_path: Path,
    approved_dry_run: Path,
    data_root: Path | None,
    output_root: Path,
    state: Path,
) -> None:
    """Operational log: a regular file (or a new file in an existing directory) outside
    the cleaned corpus, the state tree, the input data root and every input."""
    log = Path(log)
    parent = log.absolute().parent
    if not parent.is_dir():
        raise QualityError("progress log directory does not exist")
    if log.is_symlink() or (log.exists() and not log.is_file()):
        raise QualityError("progress log must be a regular file")
    target = parent.resolve() / log.name
    manifest = load_manifest(manifest_path, data_root)
    if target.is_relative_to(manifest.data_root.resolve()):
        raise QualityError("progress log must be outside the input data root")
    for guarded in (output_root, state, manifest_path, policy_path, approved_dry_run):
        if overlaps(target, Path(guarded)):
            raise QualityError("progress log overlaps the corpus output, the state or an input")


def free_bytes(path: Path) -> int:
    current = Path(path).absolute()
    while not current.exists():
        current = current.parent
    return int(shutil.disk_usage(current).free)


def run_production(
    manifest_path: Path,
    policy_path: Path,
    approved_dry_run: Path,
    output_root: Path,
    state_output: Path,
    *,
    approved_result_digest: str,
    limits: Limits,
    data_root: Path | None = None,
    progress_interval: float | None = 5.0,
    progress_log: Path | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """Clean the corpus (DROP-only) in one pass and publish the completion receipt.

    Progress (stderr every ``progress_interval`` seconds unless None, optionally
    appended to ``progress_log``) is operational only.
    """
    started = time.monotonic() if started is None else started
    limits.check()
    output_root, state_output = Path(output_root), Path(state_output)
    if progress_log is not None:
        check_progress_log(
            progress_log,
            manifest_path,
            policy_path,
            approved_dry_run,
            data_root,
            output_root,
            state_output,
        )
    telemetry = Telemetry(limits.workers, started)
    run = _Run(telemetry=telemetry)

    def suffix() -> str:
        free = free_bytes(output_root)
        return f"DROP {run.dropped:,} docs | free {free / GIB:.1f} GiB"

    reporter = Reporter(
        telemetry,
        5.0 if progress_interval is None else progress_interval,
        stderr=progress_interval is not None,
        log=progress_log,
        prefix=PROGRESS_PREFIX,
        suffix=suffix,
    )
    try:
        with reporter:
            with Guard(
                deadline_seconds=limits.deadline_seconds,
                started=started,
                max_rss_bytes=limits.max_rss_bytes,
                watch=[output_root, state_output],
                reserve_bytes=limits.free_reserve_bytes,
            ) as guard:
                run.guard = guard
                setup = prepare_production(
                    manifest_path,
                    policy_path,
                    approved_dry_run,
                    output_root,
                    state_output,
                    approved_result_digest=approved_result_digest,
                    data_root=data_root,
                    check=guard.check,
                    telemetry=telemetry,
                )
                # The document ceiling is the approved dry run's (never an operator flag).
                limits = replace(limits, line_ceiling=setup.approved.line_ceiling)
                limits.check()
                result = run.execute(
                    setup,
                    manifest_path,
                    policy_path,
                    output_root,
                    state_output,
                    limits,
                    limits.envelope(),
                )
            assert run.tree is not None and run.raw is not None
            _publish_receipt(guard, run.tree, run.raw, MAX_STATE_BYTES, RECEIPT_FILE)
            telemetry.set_phase("complete")
    except BaseException:
        if run.tree is not None and run.raw is not None:
            with contextlib.suppress(OSError, QualityError):
                run.tree.discard(RECEIPT_FILE)
        raise
    result["phase_seconds"] = telemetry.phase_seconds()
    result["note"] = "clean facts and phase_seconds are operational, not in the result digest"
    return result


@dataclass
class _Run:
    """The state of one production command (supervised by its Guard)."""

    telemetry: Telemetry
    guard: Guard | None = None
    tree: OutputTree | None = None
    raw: bytes | None = None
    dropped: int = 0
    adopted: int = 0

    def execute(
        self,
        setup: ProductionSetup,
        manifest_path: Path,
        policy_path: Path,
        output_root: Path,
        state: Path,
        limits: Limits,
        envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        guard, telemetry = self.guard, self.telemetry
        assert guard is not None
        manifest, policy, approved, binding = (
            setup.manifest,
            setup.policy,
            setup.approved,
            setup.binding,
        )
        mapped = {m.ordinal: m for m in setup.mapping}
        fresh = not (state / BINDING_FILE).exists()
        corpus = CorpusTree(output_root, setup.mapping, setup.corpus_protected)
        if fresh:
            corpus.check_fresh()  # read-only: refuse before anything is created
        state_budget = OutputBudget(MAX_STATE_BYTES)
        tree = state_tree(state, setup.state_protected, state_budget)
        tree.open(create=True)
        state_budget.used = tree.used_bytes()
        state_budget.charge(0)
        if (state / RECEIPT_FILE).exists():
            raise QualityError(
                "production cleaning already complete in this state output; "
                "use `clean-production-verify`"
            )
        if not fresh and _read_binding(state) != binding:
            raise QualityError(
                "state output belongs to a different production binding (policy, code, "
                "manifest, approved dry run, output root or mapping changed); its units "
                "cannot be trusted: use a NEW --state-output and a NEW --output-root"
            )
        if fresh:  # the state is owned (binding first) before the corpus root is created
            tree.write(BINDING_FILE, canonical.canonical_bytes(binding))
        finals, temps = corpus.open(create=True, fresh=fresh)
        tree.remove_owned_staging()
        corpus.remove_temps(temps)  # an interrupted temporary never counts as completed

        # Resume: every committed unit, its source and its output are re-verified.
        resumed = [f for f in manifest.files if unit_path(state, f.ordinal).exists()]
        outputs: dict[int, Mapping[str, Any]] = {}
        telemetry.set_phase("resume-verify", 2 * sum(f.file_bytes for f in resumed), "bytes")
        for item in resumed:
            unit = load_production_unit(
                state, item, mapped[item.ordinal], binding, policy, approved
            )
            if unit["output"]["path"] not in finals:
                raise QualityError("a completed output file is missing (changed output); refusing")
            outputs[item.ordinal] = unit["output"]
        self._verify_outputs(corpus, outputs)
        verify_sources(
            manifest.data_root,
            resumed,
            threads=VERIFY_THREADS,
            check=guard.check,
            progress=telemetry.advance,
        )
        telemetry.resumed(
            len(resumed), sum(f.documents for f in resumed), sum(f.file_bytes for f in resumed)
        )
        done = {f.ordinal for f in resumed}
        orphans = finals - {str(o["path"]) for o in outputs.values()}
        pending = [f for f in manifest.files if f.ordinal not in done]
        upper = sum(f.file_bytes for f in pending)
        used = sum(int(o["file_bytes"]) for o in outputs.values())
        used += sum(corpus.final(name).stat().st_size for name in orphans)
        if used + upper > limits.max_output_bytes:
            raise QualityError(
                "--max-output-gib is below the cleaned corpus upper bound (input file bytes)"
            )
        if free_bytes(output_root) - upper < limits.free_reserve_bytes:
            raise QualityError(
                "free space minus the remaining output upper bound would violate "
                "--free-reserve-gib; nothing written"
            )
        corpus_budget = OutputBudget(limits.max_output_bytes, used)
        measured = self._clean(
            setup, corpus, tree, pending, orphans, limits, envelope, corpus_budget, mapped
        )

        # Aggregate, re-prove the approved accounting, write artifacts, stage the receipt.
        telemetry.set_phase("aggregate", len(manifest.files), "units")

        def units() -> Iterator[dict[str, Any]]:
            for item in manifest.files:
                guard.check()
                yield load_production_unit(
                    state, item, mapped[item.ordinal], binding, policy, approved
                )
                telemetry.advance(1)

        artifacts, result_digest, built = build_production_artifacts(
            binding, units(), policy, approved
        )
        for entry in built["outputs"]:
            path = corpus.final(str(entry["path"]))
            if not path.is_file() or path.stat().st_size != entry["file_bytes"]:
                raise QualityError("a cleaned output changed before the receipt")
        finals_now, temps_now = corpus.scan()
        if finals_now != set(corpus.expected) or temps_now:
            raise QualityError("the cleaned corpus does not hold exactly the expected outputs")
        guard.check()
        telemetry.set_phase("write", len(ARTIFACTS), "artifacts")
        for name in ARTIFACTS:
            tree.write(name, artifacts[name])
            telemetry.advance(1)
            guard.check()
        telemetry.set_phase("publish")
        totals = built["accounting"]["global"]
        execution = {
            "files_cleaned": len(pending),
            "files_resumed": len(resumed),
            "files_adopted": self.adopted,
            "wall_seconds": float(guard.elapsed()),
            **measured,
            "peak_process_tree_rss_bytes": guard.peak_rss,
            "supervisor_samples": int(guard.supervisor.samples),
            "free_space": guard.free_space(),
            "corpus_output_bytes": int(totals["output"]["file_bytes"]),
            "state_bytes_before_receipt": tree.used_bytes(),
            "note": EXECUTION_NOTE,
        }
        raw = build_receipt(
            binding=binding,
            manifest_path=manifest_path,
            policy_path=policy_path,
            approved=approved,
            implementation=setup.identity,
            output_root=output_root,
            sources=source_identities(manifest),
            built=built,
            artifacts=artifacts,
            result_digest=result_digest,
            envelope=envelope,
            execution=execution,
        )
        receipt = validate_receipt(canonical.loads_bytes_strict(raw))
        guard.check()
        self.tree = tree
        self.raw = raw
        tree.stage(RECEIPT_FILE, raw)
        guard.check()
        return {
            "complete": True,
            "cleaned_corpus_written": True,
            "result_digest": result_digest,
            "receipt_digest": receipt["digest"],
            "output_root": binding["output_root"],
            "state_output": str(state),
            "accounting": built["accounting"]["global"],
            "dry_run_accounting_match": built["match"]["status"],
            "files_cleaned": len(pending),
            "files_resumed": len(resumed),
            "files_adopted": self.adopted,
            "wall_seconds": execution["wall_seconds"],
            "clean": measured,
        }

    def _verify_outputs(self, corpus: CorpusTree, outputs: Mapping[int, Mapping[str, Any]]) -> None:
        """Re-hash every resumed output against its committed unit (bounded threads)."""
        guard = self.guard
        assert guard is not None
        if not outputs:
            return

        def one(entry: Mapping[str, Any]) -> None:
            path = corpus.final(str(entry["path"]))
            size, sha = hash_file(path, guard.check)
            self.telemetry.advance(size)
            if (size, sha) != (entry["file_bytes"], entry["sha256"]):
                raise QualityError(
                    "a completed output changed since its unit was committed (changed "
                    "output); the unit is invalid: refusing, nothing overwritten"
                )

        with ThreadPoolExecutor(max_workers=VERIFY_THREADS) as pool:
            futures = [pool.submit(one, entry) for _, entry in sorted(outputs.items())]
            try:
                for future in futures:
                    while True:
                        guard.check()
                        try:
                            future.result(timeout=POLL_SECONDS)
                            break
                        except FutureTimeout:
                            continue
            finally:
                for future in futures:
                    future.cancel()

    def _clean(
        self,
        setup: ProductionSetup,
        corpus: CorpusTree,
        tree: OutputTree,
        pending: list[AuditFile],
        orphans: set[str],
        limits: Limits,
        envelope: Mapping[str, Any],
        budget: OutputBudget,
        mapped: Mapping[int, MappedFile],
    ) -> dict[str, Any]:
        """ONE pass: decide every row in the workers, write KEEP bytes in the parent."""
        guard, telemetry = self.guard, self.telemetry
        assert guard is not None
        policy, approved = setup.policy, setup.approved
        root = setup.manifest.data_root
        started = time.monotonic()
        total = sum(f.file_bytes for f in pending)
        telemetry.set_phase("clean", total, "bytes")
        telemetry.set_output_bytes(budget.used)
        stop = threading.Event()

        def check() -> None:
            if stop.is_set():
                raise QualityError("cleaning stopped")
            guard.check()

        held: deque[bytes] = deque()  # chunk bytes in task order (results come in order)

        def tasks() -> Iterator[CleanTask]:
            for item in pending:
                rules = policy.rules_for(item.component)
                for chunk in file_tasks(root, item, None, approved.line_ceiling):
                    held.append(chunk.data)
                    yield CleanTask(
                        chunk, item.component, rules, policy.params, ZERO_KEY, record_drops=True
                    )

        waiting: deque[tuple[FileWriter, Future[None]]] = deque()
        open_writers: dict[int, FileWriter] = {}

        def drain(block: bool) -> None:
            while waiting and (block or waiting[0][1].done() or len(waiting) > MAX_PENDING_COMMITS):
                writer, future = waiting[0]
                while True:
                    guard.check()
                    try:
                        future.result(timeout=POLL_SECONDS)
                        break
                    except FutureTimeout:
                        continue
                waiting.popleft()
                self._commit(writer, setup, corpus, tree, orphans, envelope)
                del open_writers[writer.item.ordinal]
                telemetry.committed(budget.used, len(waiting))

        by_ordinal = {f.ordinal: f for f in pending}
        current: FileWriter | None = None
        verifier = ThreadPoolExecutor(max_workers=VERIFY_THREADS)
        pool = OrderedPool(limits.workers, guard, telemetry)
        failed = True
        try:
            with pool:
                for result in pool.map(process_clean_chunk, tasks()):
                    data = held.popleft()
                    if current is None or current.item.ordinal != result.ordinal:
                        item = by_ordinal[result.ordinal]
                        target = mapped[item.ordinal].output_path
                        corpus.ensure_parent(target)
                        current = FileWriter(
                            item, mapped[item.ordinal], corpus.temp(target), policy.params.ruleset
                        )
                        open_writers[item.ordinal] = current
                        current.open()
                    current.add(result, data, budget)
                    self.dropped += len(result.drops or ())
                    telemetry.measured(
                        result.rows,
                        result.nbytes,
                        result.finished - result.started,
                        result.cpu_seconds,
                        result.pid,
                    )
                    telemetry.set_output_bytes(budget.used)
                    if result.last:
                        current.close()
                        waiting.append(
                            (current, verifier.submit(_verify_written, root, current, check))
                        )
                        telemetry.verifying(len(waiting))
                        current = None
                    drain(False)
            telemetry.set_phase("verify-drain", len(waiting), "files")
            drain(True)
            failed = False
        finally:
            stop.set()
            verifier.shutdown(wait=True, cancel_futures=True)
            if failed:
                for writer in open_writers.values():
                    writer.abort()  # uncommitted temporaries never count; resume redoes them
        seconds = time.monotonic() - started
        documents = sum(f.documents for f in pending)
        return {
            "clean_seconds": float(round(seconds, 3)),
            "cleaned_documents": documents,
            "cleaned_file_bytes": total,
            "file_mb_per_s": float(round(total / 1e6 / max(seconds, 1e-9), 3)),
            "documents_per_s": float(round(documents / max(seconds, 1e-9), 1)),
            "workers": pool.workers,
            "peak_tasks_in_flight": pool.peak_in_flight,
        }

    def _commit(
        self,
        writer: FileWriter,
        setup: ProductionSetup,
        corpus: CorpusTree,
        tree: OutputTree,
        orphans: set[str],
        envelope: Mapping[str, Any],
    ) -> None:
        """Prove the file's decisions, publish its output atomically, commit its unit."""
        guard = self.guard
        assert guard is not None and writer.stats is not None
        item, stats, binding = writer.item, writer.stats, setup.binding
        if writer.rows != item.documents or writer.canonical_bytes != item.canonical_bytes:
            raise QualityError("source rows or canonical bytes differ from the manifest")
        statistics = stats.to_json()
        if statistics != setup.approved.expected[item.ordinal]:
            raise QualityError(
                f"production decisions for input file {item.ordinal} differ from the approved "
                "dry run; fatal (its output was not published)"
            )
        outcome = stats.arrays["outcome"]
        if (
            int(outcome[REVIEW, 0]) != 0
            or int(outcome[DROP, 0]) != len(writer.drops)
            or writer.size != item.file_bytes - writer.dropped_line_bytes
        ):
            raise QualityError("cleaned file accounting is internally inconsistent")
        target = writer.mapped.output_path
        sha = writer.sha.hexdigest()
        if target in orphans:
            # Interrupted after rename, before the unit commit: adopt only identical bytes.
            if hash_file(corpus.final(target), guard.check) != (writer.size, sha):
                raise QualityError(
                    "an existing output without a committed unit differs from its "
                    "re-derivation; refusing (never overwritten)"
                )
            writer.temp.unlink()
            orphans.discard(target)
            self.adopted += 1
        else:
            corpus.publish(target)
        unit = {
            "kind": UNIT_KIND,
            "binding": binding["digest"],
            "policy_digest": binding["cleaning_policy"]["digest"],
            "code_identity": binding["implementation"]["code_identity"],
            "file": item.record(),
            "output": {
                "path": target,
                "sha256": sha,
                "file_bytes": writer.size,
                "documents": int(outcome[KEEP, 0]),
                "canonical_bytes": int(outcome[KEEP, 1]),
            },
            "accounting": {
                "input_documents": item.documents,
                "input_canonical_bytes": item.canonical_bytes,
                "input_file_bytes": item.file_bytes,
                "kept_documents": int(outcome[KEEP, 0]),
                "kept_canonical_bytes": int(outcome[KEEP, 1]),
                "dropped_documents": int(outcome[DROP, 0]),
                "dropped_canonical_bytes": int(outcome[DROP, 1]),
                "kept_line_bytes": writer.size,
                "dropped_line_bytes": writer.dropped_line_bytes,
            },
            "drop_rules": drop_counts(stats),
            "statistics": statistics,
            "dropped": writer.drops,
            "max_line_bytes": writer.max_line_bytes,
            "producer_envelope": dict(envelope),
            "producer_facts": guard.unit_facts(),
        }
        tree.write(unit_name(item.ordinal), encode_unit(unit))
