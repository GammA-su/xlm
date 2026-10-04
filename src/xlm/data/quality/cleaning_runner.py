"""Phase-B cleaning-policy DRY RUN under the Phase-A supervision and integrity model.

Read-only end to end: the canonical corpus is read, hashed, measured and judged, and
only content-free statistics are written under ``--output``. No cleaned corpus, no
transformed document, no C05, tokenizer or training artifact is ever produced.

Reused from Phase A unchanged: the input-manifest loader, the authenticated C05
kept overlay (diagnostic only), the strict row parser, line-aligned 8 MiB chunks, the
ordered worker pool (one detector pass + policy evaluation per document in the SAME
worker task), the bracketing source re-hash before every unit commit, the job-owned
output tree, the whole-command supervisor (deadline, process-tree RSS, free space,
output bytes), the two-phase receipt publication, live progress and the operator-only
review materialization chain.

Resume: rerun the identical command; committed per-file units are reused after the
binding (manifest, data root, detectors, frozen policy, code, overlay, chunking) is
proven identical and every reused source file is re-hashed.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import (
    CleanChunkResult,
    CleanStats,
    CleanTask,
    merge_candidates,
    process_clean_chunk,
    quota_of,
    sampling_key,
)
from xlm.data.quality.cleaning_policy import (
    OCR_SIGNALS,
    OUTCOMES,
    REP_SIGNALS,
    RULE_IDS,
    CompiledPolicy,
    PolicyError,
    load_frozen,
)
from xlm.data.quality.cleaning_report import (
    ARTIFACTS,
    REQUIRES_REVIEW,
    REVIEW_MANIFEST,
    ROW_KEYS,
    WITHIN_GUARDRAILS,
    build_artifacts,
)
from xlm.data.quality.outputs import OutputTree, overlaps
from xlm.data.quality.policy import POLICY_VERSION, policy_identity
from xlm.data.quality.progress import Reporter, Telemetry
from xlm.data.quality.receipt import check_envelope, check_execution_envelope
from xlm.data.quality.review import (
    ReviewError,
    ReviewWriter,
    check_destination,
    excerpt,
    iter_review_rows,
)
from xlm.data.quality.runner import (
    MAX_PENDING_COMMITS,
    VERIFY_THREADS,
    Guard,
    Limits,
    Prepared,
    ReviewLimits,
    _publish_receipt,
    check_progress_log,
    prepare,
    verify_sources,
    worker_activity,
)
from xlm.data.quality.scan import (
    CHUNK_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_UNIT_FILE_BYTES,
    POLL_SECONDS,
    AuditFile,
    InputManifest,
    OrderedPool,
    OutputBudget,
    QualityError,
    check_unit_facts,
    decode_unit,
    encode_unit,
    file_tasks,
    parse_row,
    read_bounded,
    verify_source,
)

BINDING_FILE = "cleaning-policy-binding.json"
RECEIPT_FILE = "cleaning-dry-run-receipt.json"
BINDING_KIND = "xlm_quality_cleaning_dry_run_binding_v1"
UNIT_KIND = "xlm_quality_cleaning_unit_v1"
RECEIPT_KIND = "xlm_quality_cleaning_dry_run_receipt"
RECEIPT_SCHEMA = 1
RUN_PHASE = "B_POLICY_DRY_RUN"
PROGRESS_PREFIX = "[quality-clean]"
MAX_RECEIPT_BYTES = 64 * 1024**2
RECEIPT_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "status",
        "phase",
        "run_phase",
        "corpus_modified",
        "cleaned_corpus_written",
        "actions_executed",
        "policy_status",
        "binding",
        "binding_digest",
        "input_manifest",
        "cleaning_policy",
        "detector_policy",
        "implementation",
        "overlay",
        "source_files",
        "artifacts",
        "result_digest",
        "envelope",
        "producer_envelopes",
        "execution",
        "digest",
    }
)
SOURCE_KEYS = frozenset({"path", "documents_sha256", "file_bytes", "documents"})
ARTIFACT_KEYS = frozenset({"bytes", "sha256", "records"})


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise QualityError(f"dry-run receipt invalid: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


# -- binding -----------------------------------------------------------------------------------


def dry_run_binding(
    manifest: InputManifest,
    ready: Prepared,
    line_ceiling: int,
    policy: CompiledPolicy,
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
        "cleaning_policy": {
            "version": policy.version,
            "digest": policy.digest,
            "file_sha256": policy.file_sha256,
            "phase_a_receipt_digest": policy.provenance["phase_a"]["receipt_digest"],
            "candidate_policy_sha256": policy.provenance["candidate_policy"]["sha256"],
        },
        "implementation": {
            "code_identity": ready.identity["code_identity"],
            "dependency_sha256": ready.identity["dependency_sha256"],
        },
        "overlay": None if ready.overlay is None else ready.overlay.binding,
        "chunk_bytes": CHUNK_BYTES,
        "line_ceiling": line_ceiling,
        "review_key_sha256": hashlib.sha256(
            sampling_key(policy.params, manifest.digest)
        ).hexdigest(),
        "source_identity": "SHA-256 + size + rows, re-hashed on every reuse; never mtime",
        "read_only": "no cleaned corpus, no transform, no C05/tokenizer/training output",
    }
    body["digest"] = canonical.digest(body)
    return body


def _check_policy_matches(policy: CompiledPolicy, manifest: InputManifest) -> None:
    phase_a = policy.provenance["phase_a"]
    if phase_a["input_manifest_digest"] != manifest.digest:
        raise PolicyError(
            "cleaning policy refused: thresholds were frozen from a Phase-A audit of a "
            "different input manifest"
        )
    current = {"version": POLICY_VERSION, "digest": policy_identity()}
    if phase_a["detector_policy"] != current:
        raise PolicyError(
            "cleaning policy refused: frozen under different detector semantics than the "
            "current detectors"
        )
    for item in manifest.files:
        policy.rules_for(item.component)


def _prepare(
    manifest_path: Path,
    output: Path,
    policy_path: Path,
    *,
    data_root: Path | None,
    proof: Path | None,
    allow_authored_proof: bool,
    line_ceiling: int,
    check: Callable[[], None] | None,
    telemetry: Telemetry | None = None,
) -> tuple[Prepared, CompiledPolicy, dict[str, Any]]:
    policy = load_frozen(policy_path)
    if overlaps(output, policy_path):
        raise QualityError("output directory overlaps the cleaning policy file")
    ready = prepare(
        manifest_path,
        output,
        data_root=data_root,
        proof=proof,
        allow_authored_proof=allow_authored_proof,
        line_ceiling=line_ceiling,
        check=check,
        telemetry=telemetry,
    )
    _check_policy_matches(policy, ready.manifest)
    ready.protected.append(policy_path)
    binding = dry_run_binding(ready.manifest, ready, line_ceiling, policy)
    return ready, policy, binding


def _read_binding(output: Path) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(
            read_bounded(output / BINDING_FILE, MAX_MANIFEST_BYTES, "dry-run binding")
        )
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("dry-run binding is not strict canonical JSON") from None
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        raise QualityError("dry-run binding self-digest mismatch")
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


# -- units -------------------------------------------------------------------------------------


def unit_name(ordinal: int) -> str:
    return f"units/f{ordinal:05d}.unit.zz"


def unit_path(output: Path, ordinal: int) -> Path:
    return output / unit_name(ordinal)


@dataclass
class CleanAccumulator:
    item: AuditFile
    rows: int = 0
    canonical_bytes: int = 0
    populations: dict[str, CleanStats] = field(default_factory=dict)
    review: dict[str, list[Any]] = field(default_factory=dict)
    max_line_bytes: int = 0

    def add(self, result: CleanChunkResult, quotas: Mapping[str, int]) -> None:
        self.rows += result.rows
        self.canonical_bytes += result.canonical_bytes
        self.max_line_bytes = max(self.max_line_bytes, result.max_line_bytes)
        for name, stats in result.populations.items():
            mine = self.populations.get(name)
            if mine is None:
                self.populations[name] = stats
            else:
                mine.merge(stats)
        merge_candidates(self.review, result.review, quotas)


def commit_unit(
    tree: OutputTree,
    binding: str,
    acc: CleanAccumulator,
    envelope: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    item = acc.item
    if acc.rows != item.documents:
        raise QualityError("source row count differs from the manifest")
    if acc.canonical_bytes != item.canonical_bytes:
        raise QualityError("source canonical byte total differs from the manifest")
    payload = encode_unit(
        {
            "kind": UNIT_KIND,
            "dry_run_binding": binding,
            "file": item.record(),
            "producer_envelope": dict(envelope),
            "producer_facts": dict(facts),
            "max_line_bytes": acc.max_line_bytes,
            "populations": {k: v.to_json() for k, v in sorted(acc.populations.items())},
            "review": {k: acc.review[k] for k in sorted(acc.review)},
        }
    )
    tree.write(unit_name(item.ordinal), payload)


def load_unit(output: Path, item: AuditFile, binding: str) -> dict[str, Any]:
    path = unit_path(output, item.ordinal)
    body = decode_unit(read_bounded(path, MAX_UNIT_FILE_BYTES, "dry-run unit"))
    if body.get("kind") != UNIT_KIND or body.get("dry_run_binding") != binding:
        raise QualityError("dry-run unit belongs to a different dry-run binding")
    if body.get("file") != item.record():
        raise QualityError("dry-run unit file record differs from the manifest")
    check_unit_facts(body, item)
    populations = body.get("populations")
    if not isinstance(populations, dict) or not isinstance(body.get("review"), dict):
        raise QualityError("dry-run unit schema")
    stats = [CleanStats.from_json(v) for v in populations.values()]
    if (
        sum(s.docs for s in stats) != item.documents
        or sum(s.bytes for s in stats) != item.canonical_bytes
    ):
        raise QualityError("dry-run unit totals differ from the manifest")
    check_against_producer(body, stats)
    return body


def check_against_producer(unit: Mapping[str, Any], stats: Sequence[CleanStats]) -> None:
    envelope = unit["producer_envelope"]
    check_envelope(envelope)
    facts = unit["producer_facts"]
    longest = unit["max_line_bytes"]
    if (
        longest > envelope["max_document_bytes"]
        or facts["peak_process_tree_rss_bytes"] > envelope["max_rss_bytes"]
        or facts["elapsed_seconds"] > envelope["deadline_seconds"]
        or facts["min_observed_free_bytes"] < envelope["free_reserve_bytes"]
    ):
        raise QualityError("dry-run unit measured facts contradict its producer envelope")
    if any(s.max_text_bytes > longest for s in stats) or longest > sum(s.line_bytes for s in stats):
        raise QualityError("dry-run unit largest row contradicts its statistics")


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
        if facts is not None:
            facts.append({"max_line_bytes": unit["max_line_bytes"], **unit["producer_facts"]})
        yield unit


# -- receipt -----------------------------------------------------------------------------------


def build_receipt(
    *,
    binding: Mapping[str, Any],
    manifest_path: Path,
    policy_path: Path,
    implementation: Mapping[str, str],
    sources: list[dict[str, Any]],
    artifacts: Mapping[str, bytes],
    result_digest: str,
    policy_status: str,
    envelope: Mapping[str, Any],
    producer_envelopes: list[dict[str, Any]],
    execution: Mapping[str, Any],
) -> bytes:
    body: dict[str, Any] = {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "phase": "COMPLETE",
        "run_phase": RUN_PHASE,
        "corpus_modified": False,
        "cleaned_corpus_written": False,
        "actions_executed": [],
        "policy_status": policy_status,
        "binding": dict(binding),
        "binding_digest": binding["digest"],
        "input_manifest": {
            "path": manifest_path.resolve().as_posix(),
            "digest": binding["input_manifest"]["digest"],
            "file_sha256": binding["input_manifest"]["file_sha256"],
        },
        "cleaning_policy": {
            "path": policy_path.resolve().as_posix(),
            "digest": binding["cleaning_policy"]["digest"],
            "file_sha256": binding["cleaning_policy"]["file_sha256"],
        },
        "detector_policy": binding["detector_policy"],
        "implementation": {
            "code_commit": implementation["code_commit"],
            "code_identity": implementation["code_identity"],
            "dependency_sha256": implementation["dependency_sha256"],
        },
        "overlay": binding["overlay"],
        "source_files": sources,
        "artifacts": {
            name: {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "records": data.count(b"\n"),
            }
            for name, data in sorted(artifacts.items())
        },
        "result_digest": result_digest,
        "envelope": dict(envelope),
        "producer_envelopes": producer_envelopes,
        "execution": dict(execution),
    }
    body["digest"] = canonical.digest(body)
    return canonical.canonical_bytes(body)


def validate_receipt(receipt: Any, receipt_bytes: int | None = None) -> dict[str, Any]:
    """Exact schema, self-digest, bindings, artifacts and the measured envelope."""
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, "field set")
    _require(receipt["kind"] == RECEIPT_KIND, "kind")
    _require(receipt["schema_version"] == RECEIPT_SCHEMA, "schema version")
    _require(receipt["status"] == "COMPLETE" and receipt["phase"] == "COMPLETE", "status")
    _require(receipt["run_phase"] == RUN_PHASE, "run phase")
    _require(receipt["corpus_modified"] is False, "corpus_modified")
    _require(receipt["cleaned_corpus_written"] is False, "cleaned_corpus_written")
    _require(receipt["actions_executed"] == [], "actions_executed")
    _require(receipt["policy_status"] in (REQUIRES_REVIEW, WITHIN_GUARDRAILS), "policy status")
    _require(receipt["digest"] == canonical.self_digest(receipt), "self-digest")
    binding = receipt["binding"]
    _require(isinstance(binding, dict), "binding")
    _require(binding.get("kind") == BINDING_KIND, "binding kind")
    _require(binding.get("digest") == canonical.self_digest(binding), "binding digest")
    _require(receipt["binding_digest"] == binding["digest"], "binding digest reference")
    manifest = receipt["input_manifest"]
    _require(
        isinstance(manifest, dict)
        and set(manifest) == {"path", "digest", "file_sha256"}
        and manifest["digest"] == binding["input_manifest"]["digest"]
        and manifest["file_sha256"] == binding["input_manifest"]["file_sha256"]
        and type(manifest["path"]) is str,
        "input manifest binding",
    )
    policy = receipt["cleaning_policy"]
    _require(
        isinstance(policy, dict)
        and set(policy) == {"path", "digest", "file_sha256"}
        and policy["digest"] == binding["cleaning_policy"]["digest"]
        and policy["file_sha256"] == binding["cleaning_policy"]["file_sha256"]
        and type(policy["path"]) is str,
        "cleaning policy binding",
    )
    _require(receipt["detector_policy"] == binding["detector_policy"], "detector identity")
    implementation = receipt["implementation"]
    _require(
        isinstance(implementation, dict)
        and set(implementation) == {"code_commit", "code_identity", "dependency_sha256"}
        and implementation["code_identity"] == binding["implementation"]["code_identity"]
        and implementation["dependency_sha256"] == binding["implementation"]["dependency_sha256"],
        "implementation identity",
    )
    _require(receipt["overlay"] == binding["overlay"], "overlay identity")
    sources = receipt["source_files"]
    _require(
        isinstance(sources, list) and len(sources) == binding["input_manifest"]["files"],
        "source count",
    )
    for source in sources:
        _require(isinstance(source, dict) and set(source) == SOURCE_KEYS, "source schema")
        _require(_sha(source["documents_sha256"]), "source SHA-256")
    artifacts = receipt["artifacts"]
    _require(isinstance(artifacts, dict) and set(artifacts) == set(ARTIFACTS), "artifact set")
    for entry in artifacts.values():
        _require(isinstance(entry, dict) and set(entry) == ARTIFACT_KEYS, "artifact schema")
        _require(_sha(entry["sha256"]) and type(entry["bytes"]) is int, "artifact hash")
    expected = canonical.digest({name: artifacts[name]["sha256"] for name in sorted(artifacts)})
    _require(receipt["result_digest"] == expected, "result digest")
    envelopes = receipt["producer_envelopes"]
    _require(isinstance(envelopes, list) and len(envelopes) > 0, "producer envelopes")
    try:
        check_envelope(receipt["envelope"])
        for envelope in envelopes:
            check_envelope(envelope)
        check_execution_envelope(receipt, receipt_bytes)
    except (KeyError, TypeError):
        raise QualityError("dry-run receipt invalid: binding or execution schema") from None
    validated: dict[str, Any] = receipt
    return validated


def load_receipt(output: Path) -> dict[str, Any]:
    path = output / RECEIPT_FILE
    if not path.exists():
        raise QualityError("no dry-run receipt: the dry run is incomplete (never a result)")
    raw = read_bounded(path, MAX_RECEIPT_BYTES, "dry-run receipt")
    try:
        body = canonical.loads_bytes_strict(raw)
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("dry-run receipt invalid: not strict canonical JSON") from None
    return validate_receipt(body, len(raw))


# -- the dry run -------------------------------------------------------------------------------


def run_dry_run(
    manifest_path: Path,
    output: Path,
    policy_path: Path,
    *,
    limits: Limits,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    progress_interval: float | None = 5.0,
    progress_log: Path | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """Scan once, decide every document, write content-free reports (never text)."""
    started = time.monotonic() if started is None else started
    limits.check()
    envelope = limits.envelope()
    if progress_log is not None:
        check_progress_log(progress_log, output, manifest_path, data_root, proof)
        if overlaps(progress_log.absolute().parent / progress_log.name, policy_path):
            raise QualityError("progress log overlaps the cleaning policy file")
    telemetry = Telemetry(limits.workers, started)
    reporter = Reporter(
        telemetry,
        5.0 if progress_interval is None else progress_interval,
        stderr=progress_interval is not None,
        log=progress_log,
        prefix=PROGRESS_PREFIX,
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
                ready, policy, binding = _prepare(
                    manifest_path,
                    output,
                    policy_path,
                    data_root=data_root,
                    proof=proof,
                    allow_authored_proof=allow_authored_proof,
                    line_ceiling=limits.line_ceiling,
                    check=guard.check,
                    telemetry=telemetry,
                )
                manifest = ready.manifest
                guard.check()
                budget = OutputBudget(limits.max_output_bytes)
                tree = _tree(output, ready.protected, budget)
                tree.open(create=True)
                budget.used = tree.used_bytes()
                budget.charge(0)
                if (output / RECEIPT_FILE).exists():
                    raise QualityError(
                        "dry run already complete in this output directory; use `clean-report`"
                    )
                if (output / BINDING_FILE).exists():
                    if _read_binding(output) != binding:
                        raise QualityError(
                            "output directory belongs to a different dry-run binding (code, "
                            "policy, manifest or overlay changed); use a new output directory"
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
                    tree, ready, policy, pending, digest, limits, guard, telemetry, budget
                )
                telemetry.set_phase("aggregate", len(manifest.files), "units")
                identities: list[dict[str, Any]] = []
                producers: list[dict[str, Any]] = []
                units_seen: list[dict[str, Any]] = []
                artifacts, result_digest, policy_status = build_artifacts(
                    binding,
                    _counted(
                        stream_units(
                            output,
                            manifest,
                            digest,
                            identities,
                            check=guard.check,
                            rehash=False,  # resumed files re-hashed above; fresh at commit
                            envelopes=producers,
                            facts=units_seen,
                        ),
                        telemetry.advance,
                    ),
                    policy,
                    sorted({f.component for f in manifest.files}),
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
                    policy_path=policy_path,
                    implementation=ready.identity,
                    sources=identities,
                    artifacts=artifacts,
                    result_digest=result_digest,
                    policy_status=policy_status,
                    envelope=envelope,
                    producer_envelopes=sorted(producers, key=canonical.canonical_bytes),
                    execution=execution,
                )
                validate_receipt(canonical.loads_bytes_strict(raw), len(raw))
                guard.check()
                staged.append(tree)
                tree.stage(RECEIPT_FILE, raw)
                guard.check()
            _publish_receipt(guard, tree, raw, limits.max_output_bytes, RECEIPT_FILE)
            telemetry.set_output_bytes(tree.used_bytes())
            telemetry.set_phase("complete")
    except BaseException:
        for tree in staged:
            with contextlib.suppress(OSError, QualityError):
                tree.discard(RECEIPT_FILE)
        raise
    return {
        "complete": True,
        "dry_run": True,
        "cleaned_corpus_written": False,
        "policy_status": policy_status,
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


def _scan(
    tree: OutputTree,
    ready: Prepared,
    policy: CompiledPolicy,
    pending: list[AuditFile],
    digest: str,
    limits: Limits,
    guard: Guard,
    telemetry: Telemetry,
    budget: OutputBudget,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One detector pass + policy evaluation per document, in the worker task."""
    manifest, overlay = ready.manifest, ready.overlay
    root = manifest.data_root
    started = time.monotonic()
    envelope = limits.envelope()
    key = sampling_key(policy.params, manifest.digest)
    quotas = quota_of(policy.params)
    scanned_bytes = sum(f.file_bytes for f in pending)
    telemetry.set_phase("scan", scanned_bytes, "bytes")
    stop = threading.Event()

    def check() -> None:
        if stop.is_set():
            raise QualityError("scan stopped")
        guard.check()

    def tasks() -> Iterator[CleanTask]:
        for item in pending:
            kept = identity = None
            if overlay is not None:
                kept, identity = overlay.kept[item.path], overlay.identity[item.path]
            rules = policy.rules_for(item.component)
            for chunk in file_tasks(root, item, kept, limits.line_ceiling, identity=identity):
                yield CleanTask(chunk, item.component, rules, policy.params, key)

    waiting: deque[tuple[CleanAccumulator, Future[set[int]]]] = deque()

    def drain(block: bool) -> None:
        while waiting and (block or waiting[0][1].done() or len(waiting) > MAX_PENDING_COMMITS):
            acc, future = waiting[0]
            while True:
                guard.check()
                try:
                    future.result(timeout=POLL_SECONDS)
                    break
                except FutureTimeout:
                    continue
            waiting.popleft()
            commit_unit(tree, digest, acc, envelope, guard.unit_facts())
            telemetry.committed(budget.used, len(waiting))

    by_ordinal = {f.ordinal: f for f in pending}
    current: CleanAccumulator | None = None
    spans: list[tuple[float, float, int]] = []
    verifier = ThreadPoolExecutor(max_workers=VERIFY_THREADS)
    pool = OrderedPool(limits.workers, guard, telemetry)
    try:
        with pool:
            for result in pool.map(process_clean_chunk, tasks()):
                if current is None or current.item.ordinal != result.ordinal:
                    current = CleanAccumulator(by_ordinal[result.ordinal])
                current.add(result, quotas)
                spans.append((result.started, result.finished, result.pid))
                telemetry.measured(
                    result.rows,
                    result.nbytes,
                    result.finished - result.started,
                    result.cpu_seconds,
                    result.pid,
                )
                if result.last:
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


# -- verification ------------------------------------------------------------------------------


def _verify(
    manifest_path: Path,
    output: Path,
    policy_path: Path,
    *,
    data_root: Path | None,
    proof: Path | None,
    allow_authored_proof: bool,
    rehash_sources: bool,
    workers: int,
    guard: Guard,
) -> tuple[dict[str, Any], Prepared, dict[str, Any]]:
    receipt = load_receipt(output)
    binding_on_disk = _read_binding(output)
    if receipt["binding"] != binding_on_disk:
        raise QualityError("receipt binding differs from the output directory binding")
    ready, policy, binding = _prepare(
        manifest_path,
        output,
        policy_path,
        data_root=data_root,
        proof=proof,
        allow_authored_proof=allow_authored_proof,
        line_ceiling=int(binding_on_disk["line_ceiling"]),
        check=guard.check,
    )
    if binding != binding_on_disk:
        raise QualityError("current code/policy/manifest/overlay differ from the dry-run binding")
    tree = _tree(output, ready.protected, None)
    tree.open(create=False)
    identities: list[dict[str, Any]] = []
    producers: list[dict[str, Any]] = []
    unit_facts: list[dict[str, Any]] = []
    artifacts, result_digest, policy_status = build_artifacts(
        binding,
        stream_units(
            output,
            ready.manifest,
            str(binding["digest"]),
            identities,
            check=guard.check,
            rehash=rehash_sources,
            threads=workers,
            envelopes=producers,
            facts=unit_facts,
        ),
        policy,
        sorted({f.component for f in ready.manifest.files}),
    )
    if identities != receipt["source_files"]:
        raise QualityError("source file identities differ from the receipt")
    if sorted(producers, key=canonical.canonical_bytes) != receipt["producer_envelopes"]:
        raise QualityError("unit producer envelopes differ from the receipt")
    if result_digest != receipt["result_digest"]:
        raise QualityError("re-derived result digest differs from the receipt")
    if policy_status != receipt["policy_status"]:
        raise QualityError("re-derived policy status differs from the receipt")
    for name, data in artifacts.items():
        with (output / name).open("rb") as stream:
            on_disk = stream.read(len(data) + 1)
        if on_disk != data:
            raise QualityError(f"artifact {name} differs from its re-derivation")
    execution = receipt["execution"]
    units = sum(unit_path(output, f.ordinal).stat().st_size for f in ready.manifest.files)
    before = (output / BINDING_FILE).stat().st_size + units + sum(map(len, artifacts.values()))
    receipt_bytes = (output / RECEIPT_FILE).stat().st_size
    if before != execution["output_bytes_before_receipt"]:
        raise QualityError("receipt contradicts the verified dry run: output bytes")
    if tree.used_bytes() != before + receipt_bytes:
        raise QualityError("receipt contradicts the verified dry run: unaccounted output bytes")
    longest = max((int(f["max_line_bytes"]) for f in unit_facts), default=0)
    if longest != execution["max_document_bytes_observed"]:
        raise QualityError("receipt contradicts the verified dry run: largest row")
    rows = 0
    files = {f.path for f in ready.manifest.files}
    for row in iter_review_rows(output / REVIEW_MANIFEST, receipt["artifacts"][REVIEW_MANIFEST]):
        guard.check()
        check_review_row(row)
        if row["path"] not in files:
            raise QualityError("review row outside the manifest")
        rows += 1
    if rows > policy.params.max_rows:
        raise QualityError("review manifest exceeds the policy's max rows")
    result = {
        "verified": True,
        "result_digest": result_digest,
        "policy_status": policy_status,
        "artifacts": len(artifacts),
        "sources_rehashed": rehash_sources,
    }
    return result, ready, receipt


def check_review_row(row: Mapping[str, Any]) -> None:
    """Exact dry-run review-row schema: locators, digests, names and numbers only."""
    if set(row) != ROW_KEYS:
        raise ReviewError("review manifest row schema")
    for name in ("path", "component", "doc_class", "selected_by"):
        if type(row[name]) is not str:
            raise ReviewError("review manifest row schema")
    for name in ("row", "offset", "rank", "severe_repetition_signal_count"):
        if type(row[name]) is not int or row[name] < 0:
            raise ReviewError("review manifest row schema")
    if row["ocr_signal_count"] is not None and type(row["ocr_signal_count"]) is not int:
        raise ReviewError("review manifest row schema")
    for name in ("doc_id_sha256", "row_sha256"):
        if not _sha(row[name]):
            raise ReviewError("review manifest row schema")
    if row["outcome"] not in OUTCOMES or row["kept"] not in (True, False, None):
        raise ReviewError("review manifest row schema")
    for name, allowed in (
        ("rules", set(RULE_IDS)),
        ("severe_repetition_signals", set(REP_SIGNALS)),
        ("ocr_signals", set(OCR_SIGNALS)),
    ):
        values = row[name]
        if not isinstance(values, list) or not set(values) <= allowed:
            raise ReviewError("review manifest row schema")
    strata = row["strata"]
    if not isinstance(strata, list) or not all(type(s) is str for s in strata):
        raise ReviewError("review manifest row schema")
    values = row["values"]
    if not isinstance(values, dict) or not all(
        v is None or type(v) in (int, float) for v in values.values()
    ):
        raise ReviewError("review manifest row schema")


def verify_dry_run(
    manifest_path: Path,
    output: Path,
    policy_path: Path,
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
            policy_path,
            data_root=data_root,
            proof=proof,
            allow_authored_proof=allow_authored_proof,
            rehash_sources=True,
            workers=workers,
            guard=guard,
        )
    guard.final(0.0)
    return result


# -- operator review materialization -----------------------------------------------------------


def materialize_review(
    output: Path,
    destination: Path,
    *,
    limits: ReviewLimits,
    manifest_path: Path | None = None,
    policy_path: Path | None = None,
    data_root: Path | None = None,
    proof: Path | None = None,
    allow_authored_proof: bool = False,
    strata: Sequence[str] | None = None,
    components: Sequence[str] | None = None,
    outcomes: Sequence[str] | None = None,
    started: float | None = None,
) -> dict[str, Any]:
    """OPERATOR ONLY. Copy bounded excerpts of SELECTED dry-run review rows.

    Same chain of trust as Phase A: strict COMPLETE receipt -> exact binding (current
    code, frozen policy, manifest, overlay) -> artifacts re-derived from the units ->
    review manifest SHA/size/records from the receipt -> exact row schema and manifest
    file -> full re-hash of every selected source file with each (offset, row) locator
    proven -> each row's SHA-256 and doc_id digest. Only then is text written, to a NEW
    directory outside the repository, the corpus, the inputs and the dry-run output.
    """
    limits.check()
    with Guard(
        deadline_seconds=limits.deadline_seconds,
        started=time.monotonic() if started is None else started,
        max_rss_bytes=limits.max_rss_bytes,
        watch=[destination],
        reserve_bytes=limits.free_reserve_bytes,
    ) as guard:
        receipt = load_receipt(output)
        manifest_file = manifest_path or Path(receipt["input_manifest"]["path"])
        policy_file = policy_path or Path(receipt["cleaning_policy"]["path"])
        _, ready, receipt = _verify(
            manifest_file,
            output,
            policy_file,
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
        rows = iter_review_rows(output / REVIEW_MANIFEST, receipt["artifacts"][REVIEW_MANIFEST])
        for row in rows:
            guard.check()
            check_review_row(row)
            if row["path"] not in files:
                raise ReviewError("review row names a file outside the dry-run manifest")
            if (
                (strata is None or set(row["strata"]) & set(strata))
                and (components is None or row["component"] in components)
                and (outcomes is None or row["outcome"] in outcomes)
            ):
                selected.append(row)
                if len(selected) >= limits.max_documents:
                    break
        check_destination(destination, [manifest.data_root, output, *ready.protected])
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
        line_ceiling = int(receipt["binding"]["line_ceiling"])
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
                    raise ReviewError("located row bytes differ from the dry-run row")
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
                        "detector": row["selected_by"],
                        "roles": [row["outcome"], *row["rules"]],
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
