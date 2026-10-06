"""C06 FAST tokenizer fit: one authenticated membership stream, one source pass, BPE.

Scientifically identical to the bb886bd reference (:mod:`tokenizer_fit`) on every
input it accepts: same policy, budgets, eligibility, rank, cap, crossing rule,
selected records, sample bytes, BPE feed order (plan file, then row),
``training_input_hash`` and tokenizer. Only the work changes:

1. **Membership** (``membership.jsonl``, signed by the C05 completion) is read once.
   The bytes that are parsed are the bytes that are hashed; nothing is used until
   the final SHA-256, size and row count equal the signed completion. Rows are
   schema-checked, must be in strictly ascending doc-id byte order (no repeats),
   must name a frozen plan file/row and its allocation, and are reconciled against
   the completion's kept/train-byte/non-kept totals. No SQLite, no per-record queries.
2. **Selection** is the exact shortest rank prefix per allocation (vectorized; same
   SHA-256 rank, doc-id tie order, budgets, 1 MiB cap, whole crossing document).
   A deficit refuses here, before any source byte is read.
3. **One source pass** hashes every byte of every plan file once (size, SHA-256 and
   row count must equal the plan; growth refuses immediately). Every kept row is
   strict-JSON parsed to check its id and ORIGINAL split (a C05-train row whose
   canonical ``split`` is not ``train`` refuses, exactly as bb886bd) and byte count.
   Only selected rows are fully decoded and content-digested.
4. **The BPE spool is authenticated**: its exact framed bytes are hashed as written;
   the BPE child hashes exactly the bytes it consumes and refuses before saving
   unless they match; the parent re-checks and binds the digest in the manifest.
5. **One supervisor** (:mod:`supervisor`) owns the absolute deadline (started at
   command dispatch), process-tree RAM, volume reserves and the projected total; it
   kills descendants on failure. Final publication is the parent's single atomic
   rename, only after every verification and a passing supervisor check.

The same pass writes the reusable kept-membership index (:mod:`keptindex`).
Worker and thread counts are operational only and never change any output; the
operational envelope is bound into the reviewed plan digest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import psutil
from pydantic import ConfigDict, Field, model_validator

from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed, verify_signed
from xlm.data.exclusion.fitscan import (
    SPLIT_NAMES,
    FileRef,
    MembershipChunk,
    MembershipTables,
    OrderedPool,
    SourceTask,
    parse_membership_chunk,
    scan_source_file,
)
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.keptindex import (
    OWNED_FILES,
    ROW_DTYPE,
    index_bound,
    open_index,
    reverify_sections,
    write_index,
)
from xlm.data.exclusion.policy import C05Error, FrozenModel
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.runner import file_sha, verify_completion_envelope
from xlm.data.exclusion.selection import check_binding
from xlm.data.exclusion.supervisor import (
    Checkable,
    Deadline,
    Projection,
    Supervisor,
    device_of,
    existing_ancestor,
    terminate_processes,
)
from xlm.data.exclusion.tokenizer_fit import (
    FIT_KIND,
    FIT_MANIFEST,
    FIT_SAMPLE,
    MAX_SAMPLE_DOCUMENTS,
    RANK_TAG,
    RESOURCE_PLAN,
    SPOOL_FRAME_BYTES,
    TOKENIZER_DIR,
    Budget,
    FitDeficit,
    FitPolicy,
    _Allocation,
    _Chosen,
    _Entry,
    _Reporter,
    _requirements,
    _verify_tokenizer,
    deficit_report,
    export_sample,
    fit_budgets,
    fit_record,
    select_sample,
)
from xlm.data.pools.tokenizer_fit import estimated_fit_peak_memory_bytes
from xlm.tokenizers.bpe import fit_frame, fit_input_line

FIT_PATH = "c06-fast-v1"
RESOURCE_PLAN_KIND = "c06_tokenizer_fit_fast_resource_plan_v2"
KEPT_INDEX_DIR = "kept-index"
SLO_SECONDS = 1200
DEFAULT_WORKERS = 8
DEFAULT_BPE_THREADS = 16
DEFAULT_RSS_CEILING = 24 * 1024**3
MAX_RSS_CEILING = 32 * 1024**3
MEMBERSHIP_CHUNK_BYTES = 16 * 1024**2
SAMPLE_CEILING = 256 * 1024**2
TOKENIZER_CEILING = 64 * 1024**2
MANIFEST_CEILING = 8 * 1024**2
JOB_CEILING = 1024**2
DEVICE_SLACK = 64 * 1024**2
CHILD_POLL_SECONDS = 0.05
# Planning figures for the in-memory membership columns (not measurements).
COLUMN_BYTES_PER_ROW = 4 + 4 + 8 + 32 + 1 + 2 + 32 + 8
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_manifest.json", "c05-binding.json")


# -- operational envelope ---------------------------------------------------------------


class OperationalEnvelope(FrozenModel):
    """Reviewed operational settings; bound into the plan digest, not scientific identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    source_workers: int = DEFAULT_WORKERS
    bpe_threads: int = DEFAULT_BPE_THREADS
    deadline_seconds: float = float(SLO_SECONDS)
    ram_ceiling_bytes: int = Field(default=DEFAULT_RSS_CEILING, ge=1, le=MAX_RSS_CEILING)
    free_reserve_bytes: int = Field(default=16 * 1024**3, ge=0)
    monitor_interval_seconds: float = Field(default=0.25, gt=0, le=2)
    shutdown_grace_seconds: float = Field(default=2.0, gt=0, le=30)
    worker_queue_tasks: int = 2 * DEFAULT_WORKERS
    membership_chunk_bytes: int = Field(default=MEMBERSHIP_CHUNK_BYTES, ge=4096, le=256 * 1024**2)
    source_block_bytes: int = Field(default=8 * 1024**2, ge=512, le=256 * 1024**2)
    bpe_reserve_seconds: float = Field(default=240.0, ge=0)
    selection_reserve_seconds: float = Field(default=20.0, ge=0)
    finalization_reserve_seconds: float = Field(default=60.0, ge=0)
    membership_planning_bytes_per_s: float = Field(default=40e6, gt=0)
    source_planning_bytes_per_s: float = Field(default=0.2e9, gt=0)
    early_abort_ratio: float = Field(default=1.25, ge=1)
    publication_margin_seconds: float = Field(default=1.0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def derived_queue(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("worker_queue_tasks") is None:
            workers = value.get("source_workers", DEFAULT_WORKERS)
            if type(workers) is int:
                return {**value, "worker_queue_tasks": 2 * workers}
        return value

    @model_validator(mode="after")
    def bounded(self) -> OperationalEnvelope:
        for name in ("source_workers", "bpe_threads", "ram_ceiling_bytes", "worker_queue_tasks"):
            if isinstance(getattr(self, name), bool):
                raise ValueError("operational integers must not be booleans")
        if self.source_workers not in (1, 2, 4, 8, 16):
            raise ValueError("source workers must be 1, 2, 4, 8 or 16")
        if self.bpe_threads not in (1, 2, 4, 8, 16):
            raise ValueError("BPE threads must be 1, 2, 4, 8 or 16")
        if not 0 < self.deadline_seconds <= 86400:
            raise ValueError("deadline must be a finite number of seconds in (0, 86400]")
        if self.worker_queue_tasks != 2 * self.source_workers:
            raise ValueError("worker queue bound must be twice the source workers")
        return self

    def projection(self) -> Projection:
        return Projection(
            deadline_seconds=self.deadline_seconds,
            membership_planning_rate=self.membership_planning_bytes_per_s,
            source_planning_rate=self.source_planning_bytes_per_s,
            selection_reserve=self.selection_reserve_seconds,
            bpe_reserve=self.bpe_reserve_seconds,
            finalization_reserve=self.finalization_reserve_seconds,
            early_abort_ratio=self.early_abort_ratio,
        )


@dataclass(frozen=True)
class Locations:
    scratch: Path
    output: Path
    deficit_report: Path


# -- streamed C05 view --------------------------------------------------------------------


@dataclass
class StreamedC05:
    """Verified completion envelope and plan; the membership file is NOT yet trusted."""

    directory: Path
    plan: ExecutionPlan
    input_manifest: dict[str, Any]
    trusted: dict[str, bytes]
    envelope: dict[str, Any]
    mode: str
    plan_digest: str
    receipt_digest: str
    completion: dict[str, Any]
    # The proof's (plan, manifest) paths, for a cleaned plan's admission lineage.
    proof_paths: tuple[Path, Path] | None = None
    _requirements_manifest: dict[str, Any] | None = None

    def requirements_manifest(self) -> dict[str, Any]:
        """Source/quota provenance (``cleaned.requirements_manifest``), verified once."""
        if self._requirements_manifest is None:
            from xlm.data.exclusion.cleaned import requirements_manifest

            self._requirements_manifest = requirements_manifest(
                self.plan, self.input_manifest, self.proof_paths
            )
        return self._requirements_manifest


def open_streamed(proof: Path, *, allow_authored: bool, consumes: list[Path | str]) -> StreamedC05:
    """``open_gate`` without the SQLite import: guard first, then signed metadata only."""
    from xlm.data.exclusion.control import trust_from_file
    from xlm.data.exclusion.transport import ProofSpec, protected_guard

    spec = ProofSpec.model_validate(read_metadata(proof, digested=False))
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec.plan), digested=False))
    if plan.identity() != spec.plan_digest:
        raise C05Error("C05 proof plan changed")
    protected_guard(
        plan,
        (proof, spec.plan, spec.manifest, spec.completion, spec.trust, spec.scratch, *consumes),
    )
    if plan.mode == "authored" and not allow_authored:
        raise C05Error("development evidence cannot satisfy protected C05")
    trusted = trust_from_file(Path(spec.trust))
    manifest = read_metadata(Path(spec.manifest))
    if (
        manifest.get("digest") != plan.input_manifest_digest
        or canonical.self_digest(manifest) != plan.input_manifest_digest
    ):
        raise C05Error("current corpus manifest differs from C05")
    envelope = verify_completion_envelope(Path(spec.completion), plan, trusted)
    if envelope["digest"] != spec.completion_digest:
        raise C05Error("C05 completion changed")
    return StreamedC05(
        directory=Path(spec.completion),
        plan=plan,
        input_manifest=manifest,
        trusted=trusted,
        envelope=envelope,
        mode=plan.mode,
        plan_digest=plan.identity(),
        receipt_digest=str(envelope["digest"]),
        completion=envelope["payload"],
        proof_paths=(Path(spec.plan), Path(spec.manifest)),
    )


# -- resource plan / storage ------------------------------------------------------------------


def _sample_bound(policy: FitPolicy, allocations: int) -> int:
    return policy.target_sample_bytes + allocations * policy.max_document_bytes


def spool_bound(policy: FitPolicy, allocations: int) -> int:
    return _sample_bound(policy, allocations) + SPOOL_FRAME_BYTES * MAX_SAMPLE_DOCUMENTS


def max_frame_bytes(policy: FitPolicy) -> int:
    # NFC normalization of a <= cap UTF-8 text cannot exceed 3x its bytes.
    return 3 * policy.max_document_bytes


def storage_plan(
    policy: FitPolicy,
    allocations: int,
    completion: Mapping[str, Any],
    envelope: OperationalEnvelope,
    locations: Locations,
) -> dict[str, Any]:
    """Every owned growth item, grouped by the volume that holds it, plus the reserve."""
    kept = int(completion["kept"])
    membership_bytes = int(completion["membership_bytes"])
    items = {
        "bpe_spool": ("scratch", spool_bound(policy, allocations)),
        "bpe_job_and_result": ("scratch", 2 * JOB_CEILING),
        "kept_index": ("output", index_bound(kept, membership_bytes)),
        "sample": ("output", SAMPLE_CEILING),
        "tokenizer": ("output", TOKENIZER_CEILING),
        "fit_manifest_and_resource_plan": ("output", 2 * MANIFEST_CEILING),
        "deficit_report": ("deficit_report", MANIFEST_CEILING),
    }
    paths = {
        "scratch": locations.scratch,
        "output": locations.output.parent,
        "deficit_report": locations.deficit_report.parent,
    }
    devices: dict[int, list[str]] = {}
    for role, path in paths.items():
        devices.setdefault(device_of(path), []).append(role)
    groups = []
    for roles in devices.values():
        growth = sum(size for role, size in items.values() if role in roles) + DEVICE_SLACK
        groups.append(
            {
                "roles": sorted(roles),
                "growth_bytes": growth,
                "reserve_bytes": envelope.free_reserve_bytes,
                "required_free_bytes": growth + envelope.free_reserve_bytes,
            }
        )
    return {
        "items": {name: {"role": role, "bytes": size} for name, (role, size) in items.items()},
        "devices": sorted(groups, key=lambda g: g["roles"]),
        "allocation_slack_bytes_per_device": DEVICE_SLACK,
    }


def fast_resource_plan(
    policy: FitPolicy,
    plan: ExecutionPlan,
    requirements: Mapping[str, Any],
    budgets: Mapping[str, Budget],
    completion_digest: str,
    completion: Mapping[str, Any],
    envelope: OperationalEnvelope | None = None,
    locations: Locations | None = None,
) -> dict[str, Any]:
    """Deterministic, content-free plan binding the reviewed operational envelope."""
    envelope = envelope or OperationalEnvelope()
    allocations = len(budgets)
    file_bytes = sum(f.file_bytes for f in plan.files)
    membership_bytes = int(completion["membership_bytes"])
    kept = int(completion["kept"])
    overshoot = allocations * policy.max_document_bytes
    sample_bound = _sample_bound(policy, allocations)
    body: dict[str, Any] = {
        "kind": RESOURCE_PLAN_KIND,
        "policy_digest": policy.identity(),
        "plan_digest": plan.identity(),
        "completion_digest": completion_digest,
        "input_manifest_digest": plan.input_manifest_digest,
        "requirements_digest": canonical.digest(dict(requirements)),
        "inputs": {
            "files": len(plan.files),
            "documents": sum(f.documents for f in plan.files),
            "file_bytes": file_bytes,
            "canonical_bytes": sum(f.canonical_bytes for f in plan.files),
            "membership_bytes": membership_bytes,
            "kept_records": kept,
            "membership_passes": 1,
            "source_passes": 1,
            "membership_read_bytes": membership_bytes,
            "source_read_bytes": file_bytes,
            "application_bytes_read": membership_bytes + file_bytes,
            "strict_json_decodes_bound": kept + kept,
            "full_record_decodes_bound": MAX_SAMPLE_DOCUMENTS,
            "sqlite_queries": 0,
        },
        "sample": {
            "target_bytes": policy.target_sample_bytes,
            "allocations": allocations,
            "max_document_bytes": policy.max_document_bytes,
            "overshoot_bound_bytes": overshoot,
            "selected_bytes_bound": sample_bound,
            "max_sample_documents": MAX_SAMPLE_DOCUMENTS,
            "max_frame_bytes": max_frame_bytes(policy),
        },
        "memory": {
            "membership_columns_bound_bytes": kept * COLUMN_BYTES_PER_ROW + membership_bytes,
            "bpe_peak_estimate_bytes": estimated_fit_peak_memory_bytes(
                sample_bound, policy.tokenizer.target_vocab_size
            ),
            "process_tree_ram_bytes": envelope.ram_ceiling_bytes,
        },
        "operational": envelope.model_dump(mode="json"),
        "storage": None
        if locations is None
        else storage_plan(policy, allocations, completion, envelope, locations),
        "locations": None
        if locations is None
        else {
            "scratch": str(locations.scratch),
            "output": str(locations.output),
            "deficit_report": str(locations.deficit_report),
        },
        "slo_seconds": SLO_SECONDS,
        "network_required": False,
        "gpu_required": False,
        "basis": (
            "Bounds from the frozen policy, C05 plan inventory, signed completion counts "
            "and the reviewed operational envelope. Memory figures are planning estimates; "
            "measured stage times, throughput and peak RSS are appended after the run."
        ),
    }
    body["digest"] = canonical.self_digest(body)
    return body


def plan_from_proof_fast(
    proof: Path,
    policy: FitPolicy,
    quotas: Path,
    ifm_split: Path,
    envelope: OperationalEnvelope | None = None,
    locations: Locations | None = None,
) -> dict[str, Any]:
    """Metadata-only plan: no corpus or membership bytes. Call ``guard_proof`` first."""
    from xlm.data.exclusion.cleaned import requirements_manifest
    from xlm.data.exclusion.transport import ProofSpec

    spec = ProofSpec.model_validate(read_metadata(proof, digested=False))
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec.plan), digested=False))
    if plan.identity() != spec.plan_digest:
        raise C05Error("C05 proof plan changed")
    manifest = read_metadata(Path(spec.manifest))
    if manifest["digest"] != plan.input_manifest_digest:
        raise C05Error("current corpus manifest differs from C05")
    completion = read_metadata(Path(spec.completion) / "completion.json", digested=False)
    if completion.get("digest") != spec.completion_digest:
        raise C05Error("C05 completion changed")
    provenance = requirements_manifest(plan, manifest, (Path(spec.plan), Path(spec.manifest)))
    requirements = _requirements(policy, manifest, quotas, ifm_split, provenance)
    budgets = fit_budgets(policy, requirements["allocations"])
    return fast_resource_plan(
        policy,
        plan,
        requirements,
        budgets,
        spec.completion_digest,
        completion["payload"],
        envelope,
        locations,
    )


def admit_storage(storage: Mapping[str, Any], locations: Locations) -> dict[str, int]:
    """Refuse unless every volume holds its planned growth plus reserve; return watches."""
    paths = {
        "scratch": locations.scratch,
        "output": locations.output.parent,
        "deficit_report": locations.deficit_report.parent,
    }
    watches: dict[str, int] = {}
    for group in storage["devices"]:
        anchor = existing_ancestor(paths[group["roles"][0]])
        if shutil.disk_usage(anchor).free < group["required_free_bytes"]:
            raise C05Error("insufficient free space on a C06 volume (growth plus reserve)")
        watches[str(anchor)] = int(group["reserve_bytes"])
    return watches


class _TreeReporter(_Reporter):
    """Display-only process-tree RSS telemetry; enforcement belongs to the supervisor."""

    def __init__(self, progress: RunProgress | NullProgress, ram_limit: int, ceiling: int) -> None:
        self.ceiling = ceiling
        super().__init__(progress, ram_limit)

    def telemetry(self) -> dict[str, int]:
        total = 0
        peak_self = 0
        try:
            info = self.process.memory_info()
            total = int(info.rss)
            peak_self = int(getattr(info, "peak_wset", 0))
            for child in self.process.children(recursive=True):
                try:
                    total += int(child.memory_info().rss)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except psutil.Error:
            pass
        self.peak = max(self.peak, total, peak_self)
        return {"rss": total, "peak_rss": self.peak, "ram_limit": self.ceiling}


# -- owned paths ---------------------------------------------------------------------------


@dataclass
class OwnedPaths:
    """Exactly the paths this job creates; cleanup never deletes anything unregistered."""

    files: list[Path] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)

    def directory(self, path: Path, *, create: bool = True) -> Path:
        if create:
            path.mkdir()
        self.directories.append(path)
        return path

    def file(self, path: Path) -> Path:
        self.files.append(path)
        return path

    def cleanup(self) -> list[str]:
        """Remove registered files, then registered directories; report what remains."""
        residue: list[str] = []
        for path in reversed(self.files):
            if not path.exists():
                continue
            try:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                path.unlink()
            except OSError:
                residue.append(str(path))
        for path in sorted(self.directories, key=lambda p: len(p.parts), reverse=True):
            if not path.exists():
                continue
            try:
                path.rmdir()  # Fails (reported) if anything unregistered remains.
            except OSError:
                residue.append(str(path))
        return residue


# -- membership -----------------------------------------------------------------------------


@dataclass
class Membership:
    """Authenticated kept-membership columns in membership (doc-id) order."""

    rows: int
    ids: bytes
    id_offsets: npt.NDArray[np.uint64]
    file: npt.NDArray[np.uint32]
    row: npt.NDArray[np.uint32]
    nbytes: npt.NDArray[np.uint64]
    content: npt.NDArray[np.uint8]
    split: npt.NDArray[np.uint8]
    allocation: npt.NDArray[np.uint16]
    rank: npt.NDArray[np.uint8]

    def doc_id(self, position: int) -> str:
        start, end = int(self.id_offsets[position]), int(self.id_offsets[position + 1])
        return self.ids[start:end].decode("utf-8")


def membership_tables(
    view: StreamedC05, policy: FitPolicy, allocation_keys: list[str]
) -> MembershipTables:
    position = {key: n for n, key in enumerate(allocation_keys)}
    files: dict[str, FileRef] = {}
    from xlm.data.exclusion.selection import allocation_key

    for ordinal, item in enumerate(view.plan.files):
        key = allocation_key(item.component, item.view, item.upstream_component)
        if key not in position:
            raise C05Error("record outside the frozen fit allocations")
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
    return MembershipTables(
        files=files,
        allocation_keys=tuple(allocation_keys),
        seed=policy.seed,
        fit_document_cap=policy.max_document_bytes,
        line_ceiling=view.plan.resources.document_bytes,
        rank_tag=RANK_TAG,
        contract=view.plan.output_contract,
    )


def _membership_blocks(
    path: Path,
    expected_bytes: int,
    ceiling: int,
    state: dict[str, Any],
    chunk_bytes: int | None = None,
) -> Iterator[bytes]:
    """Complete lines in order; ``state`` accumulates the SHA-256 of exactly these bytes."""
    digest = state["sha"]
    size = chunk_bytes or MEMBERSHIP_CHUNK_BYTES
    pending = b""
    with path.open("rb", buffering=0) as stream:
        # Never request more than one byte past the signed size.
        while block := stream.read(min(size, expected_bytes - state["bytes"] + 1)):
            digest.update(block)
            state["bytes"] += len(block)
            if state["bytes"] > expected_bytes:
                raise C05Error("completion membership changed")
            data = pending + block if pending else block
            cut = data.rfind(b"\n") + 1
            if cut == 0:
                pending = data
                if len(pending) > ceiling:
                    raise C05Error("membership record ceiling")
                continue
            pending = data[cut:]
            yield data[:cut]
    if pending:
        yield pending


def _projection(guard: Checkable) -> Projection | None:
    projection = getattr(guard, "projection", None)
    return projection if isinstance(projection, Projection) else None


def stream_membership(
    view: StreamedC05,
    tables: MembershipTables,
    pool: OrderedPool,
    reporter: _Reporter,
    deadline: Checkable,
    measured: dict[str, Any],
    *,
    chunk_bytes: int | None = None,
) -> Membership:
    """One authenticated pass; refuses unless SHA, size and count equal the completion."""
    completion = view.completion
    plan = view.plan
    expected_bytes = int(completion["membership_bytes"])
    kept = int(completion["kept"])
    if expected_bytes > plan.resources.output_bytes or kept > plan.resources.records:
        raise C05Error("membership exceeds its reviewed ceilings")
    projection = _projection(deadline)
    reporter.stage("MEMBERSHIP STREAM", kept, "rows")
    started = time.monotonic()
    state: dict[str, Any] = {"sha": hashlib.sha256(), "bytes": 0}
    parts: list[MembershipChunk] = []
    rows = 0
    last: bytes | None = None
    blocks = _membership_blocks(
        view.directory / "membership.jsonl", expected_bytes, tables.line_ceiling, state, chunk_bytes
    )
    for part in pool.map(parse_membership_chunk, blocks):
        if part.rows:
            if last is not None and part.first_id <= last:
                raise C05Error("membership is not in strictly ascending document id order")
            last = part.last_id
        parts.append(part)
        rows += part.rows
        if rows > kept:
            raise C05Error("membership count disagrees with completion")
        if projection is not None:
            projection.progress("membership", state["bytes"], expected_bytes)
        elapsed = max(time.monotonic() - started, 1e-9)
        reporter.update(
            rows,
            bytes_done=state["bytes"],
            bytes_total=expected_bytes,
            mib_per_s=state["bytes"] / 2**20 / elapsed,
        )
        deadline.check()
    # Authenticate before any parsed value is used.
    if (
        state["bytes"] != expected_bytes
        or state["sha"].hexdigest() != completion["membership_sha256"]
    ):
        raise C05Error("completion membership changed")
    if rows != kept:
        raise C05Error("membership count disagrees with completion")
    if projection is not None:
        projection.finish("membership")
    seconds = time.monotonic() - started
    measured["membership_seconds"] = round(seconds, 3)
    measured["membership_mb_per_s"] = round(expected_bytes / 1e6 / max(seconds, 1e-9), 1)
    measured["membership_rows_per_s"] = round(rows / max(seconds, 1e-9), 1)
    reporter.note(
        f"SLO | membership {measured['membership_mb_per_s']:,.1f} MB/s, "
        f"{measured['membership_rows_per_s']:,.0f} rows/s, {seconds:,.1f} s"
    )
    lengths = np.concatenate([p.id_lengths for p in parts]) if parts else np.zeros(0, np.uint32)
    offsets = np.zeros(rows + 1, dtype=np.uint64)
    np.cumsum(lengths, out=offsets[1:])
    return Membership(
        rows=rows,
        ids=b"".join(p.ids for p in parts),
        id_offsets=offsets,
        file=np.concatenate([p.file for p in parts]),
        row=np.concatenate([p.row for p in parts]),
        nbytes=np.concatenate([p.nbytes for p in parts]),
        content=np.concatenate([p.content for p in parts]),
        split=np.concatenate([p.split for p in parts]),
        allocation=np.concatenate([p.allocation for p in parts]),
        rank=np.concatenate([p.rank for p in parts]),
    )


def reconcile(view: StreamedC05, m: Membership, allocation_keys: list[str]) -> None:
    """Kept/train-byte/non-kept totals of the signed completion, from authenticated rows."""
    plan, completion = view.plan, view.completion
    count = len(allocation_keys)
    documents = np.zeros(count, dtype=np.int64)
    position = {key: n for n, key in enumerate(allocation_keys)}
    from xlm.data.exclusion.selection import allocation_key

    for item in plan.files:
        documents[position[allocation_key(item.component, item.view, item.upstream_component)]] += (
            item.documents
        )
    kept = np.bincount(m.allocation, minlength=count).astype(np.int64)
    train = m.split == 0
    train_bytes = np.zeros(count, dtype=np.uint64)
    np.add.at(train_bytes, m.allocation[train], m.nbytes[train])
    expected: Mapping[str, Mapping[str, int]] = completion["allocations"]
    if not set(expected) <= set(allocation_keys):
        raise C05Error("completion allocation outside the frozen fit allocations")
    components: dict[str, dict[str, int]] = {}
    for n, key in enumerate(allocation_keys):
        counts = expected.get(key, {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0})
        actual = (int(kept[n]), int(train_bytes[n]), int(documents[n] - kept[n]))
        if actual != (
            counts["kept"],
            counts["train_bytes"],
            counts["excluded"] + counts["duplicate"],
        ):
            raise C05Error("membership disagrees with C05 completion accounting")
        bucket = components.setdefault(
            canonical.loads_strict(key)[0], {"kept": 0, "train_bytes": 0}
        )
        bucket["kept"] += actual[0]
        bucket["train_bytes"] += actual[1]
    for component, bucket in components.items():
        signed_bucket = completion["components"].get(component, {})
        if (bucket["kept"], bucket["train_bytes"]) != (
            signed_bucket.get("kept"),
            signed_bucket.get("train_bytes"),
        ):
            raise C05Error("membership disagrees with C05 completion accounting")
    if int(documents.sum()) != completion["documents"]:
        raise C05Error("membership disagrees with C05 completion accounting")
    location = (m.file.astype(np.uint64) << np.uint64(32)) | m.row.astype(np.uint64)
    if np.unique(location).size != m.rows:
        raise C05Error("membership repeats a source location")


# -- exact selection ------------------------------------------------------------------------


def select_exact(
    m: Membership,
    policy: FitPolicy,
    budgets: Mapping[str, Budget],
    allocation_keys: list[str],
    guard: Checkable | None = None,
) -> tuple[dict[str, _Allocation], npt.NDArray[np.bool_]]:
    """Exact shortest rank prefix per allocation (identical to the bb886bd heaps).

    Order: rank SHA-256 bytes ascending, then doc id; membership position IS doc-id
    byte order (verified ascending), and UTF-8 byte order equals code-point order.
    """
    selected = np.zeros(m.rows, dtype=np.bool_)
    states: dict[str, _Allocation] = {}
    cap = policy.max_document_bytes
    retained = 0
    for n, key in enumerate(allocation_keys):
        if guard is not None:
            guard.check()
        budget = budgets[key]
        state = _Allocation(budget.requested)
        in_allocation = m.allocation == n
        train = in_allocation & (m.split == 0)
        oversized = train & (m.nbytes > cap)
        eligible = train & ~oversized
        state.kept = int(np.count_nonzero(in_allocation))
        state.kept_train = int(np.count_nonzero(train))
        state.kept_train_bytes = int(m.nbytes[train].sum(dtype=np.uint64))
        state.kept_non_train = state.kept - state.kept_train
        state.oversized = int(np.count_nonzero(oversized))
        state.oversized_bytes = int(m.nbytes[oversized].sum(dtype=np.uint64))
        index = np.flatnonzero(eligible)
        state.eligible_documents = len(index)
        state.eligible_bytes = int(m.nbytes[index].sum(dtype=np.uint64))
        if 0 < budget.requested <= state.eligible_bytes:
            words = np.ascontiguousarray(m.rank[index]).view(">u8").reshape(len(index), 4)
            order = np.lexsort((index, words[:, 3], words[:, 2], words[:, 1], words[:, 0]))
            sizes = m.nbytes[index][order]
            cumulative = np.cumsum(sizes, dtype=np.uint64)
            take = int(np.searchsorted(cumulative, np.uint64(budget.requested), side="left")) + 1
            chosen = index[order[:take]]
            retained += take
            if retained > MAX_SAMPLE_DOCUMENTS:
                raise C05Error("tokenizer-fit retained sample document ceiling")
            selected[chosen] = True
            state.heap = [
                _Entry(
                    m.rank[p].tobytes(),
                    m.doc_id(int(p)),
                    m.content[p].tobytes().hex(),
                    int(m.nbytes[p]),
                )
                for p in chosen
            ]
            state.held = int(cumulative[take - 1])
        states[key] = state
    return states, selected


# -- authenticated spool --------------------------------------------------------------------


class HashedSpool:
    """Exact framed spool bytes, hashed as written, under a hard byte ceiling.

    ``path=None`` hashes without writing (verification re-derivation).
    """

    def __init__(self, path: Path | None, ceiling: int, max_frame: int) -> None:
        self.path = path
        self.ceiling, self.max_frame = ceiling, max_frame
        self.digest = hashlib.sha256()
        self.file_bytes = self.frames = self.payload_bytes = 0
        self.stream = path.open("xb") if path is not None else None

    def add(self, doc: CanonicalDocument) -> None:
        frame = fit_frame(doc)
        if len(frame) - SPOOL_FRAME_BYTES > self.max_frame:
            raise C05Error("tokenizer-fit spool frame exceeds its bound")
        if self.file_bytes + len(frame) > self.ceiling:
            raise C05Error("tokenizer-fit spool exceeds its reviewed byte ceiling")
        if self.stream is not None:
            self.stream.write(frame)
        self.digest.update(frame)
        self.file_bytes += len(frame)
        self.frames += 1
        self.payload_bytes += len(frame) - SPOOL_FRAME_BYTES

    def seal(self) -> dict[str, Any]:
        """fsync, close and mark read-only; return the authenticated identity."""
        if self.stream is not None:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.stream = None
            assert self.path is not None
            os.chmod(self.path, stat.S_IREAD)
        return {
            "sha256": self.digest.hexdigest(),
            "file_bytes": self.file_bytes,
            "frames": self.frames,
            "payload_bytes": self.payload_bytes,
        }

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None


# -- one source pass ------------------------------------------------------------------------


@dataclass
class SourcePass:
    index_rows: npt.NDArray[Any]
    fed_documents: int
    fed_bytes: int
    training_input_hash: str
    spool: dict[str, Any]


def _locations(m: Membership, files: int) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    order = np.lexsort((m.row, m.file))
    bounds = np.searchsorted(m.file[order], np.arange(files + 1, dtype=np.uint32))
    return order.astype(np.int64), bounds.astype(np.int64)


def _source_tasks(
    view: StreamedC05,
    m: Membership,
    selected: npt.NDArray[np.bool_],
    order: npt.NDArray[np.int64],
    bounds: npt.NDArray[np.int64],
    parse: bool,
    block_bytes: int = 0,
) -> Iterator[SourceTask]:
    root = Path(view.plan.data_root)
    for ordinal, item in enumerate(view.plan.files):
        positions = order[bounds[ordinal] : bounds[ordinal + 1]]
        starts = m.id_offsets[positions].tolist()
        ends = m.id_offsets[positions + 1].tolist()
        chosen = selected[positions]
        yield SourceTask(
            ordinal=ordinal,
            path=str(contained(root, item.path)),
            sha256=item.documents_sha256,
            file_bytes=item.file_bytes,
            documents=item.documents,
            line_ceiling=view.plan.resources.document_bytes,
            rows=m.row[positions],
            ids=[m.ids[s:e] for s, e in zip(starts, ends, strict=True)],
            nbytes=m.nbytes[positions],
            assigned=m.split[positions],
            selected=chosen,
            selected_content=[m.content[p].tobytes().hex() for p in positions[chosen]],
            parse=parse,
            block_bytes=block_bytes,
        )


def source_pass(
    view: StreamedC05,
    m: Membership,
    selected: npt.NDArray[np.bool_],
    chosen: Mapping[str, _Chosen],
    allocation_keys: list[str],
    pool: OrderedPool,
    reporter: _Reporter,
    deadline: Checkable,
    measured: dict[str, Any],
    spool: HashedSpool,
    started: float,
    *,
    block_bytes: int = 0,
) -> SourcePass:
    """Hash every plan file once; fill the index; spool selected records in feed order."""
    plan = view.plan
    projection = _projection(deadline)
    order, bounds = _locations(m, len(plan.files))
    index_rows = np.zeros(m.rows, dtype=ROW_DTYPE)
    index_rows["file"], index_rows["row"] = m.file, m.row
    index_rows["bytes"], index_rows["content"] = m.nbytes, m.content
    index_rows["assigned_split"], index_rows["allocation"] = m.split, m.allocation
    from xlm.data.exclusion.selection import allocation_key

    file_allocation = [
        allocation_key(i.component, i.view, i.upstream_component) for i in plan.files
    ]
    total_documents = sum(f.documents for f in plan.files)
    total_bytes = sum(f.file_bytes for f in plan.files)
    reporter.stage("SOURCE PASS", total_documents, "docs")
    begun = time.monotonic()
    digest = hashlib.sha256()
    fed = fed_bytes = done_rows = done_bytes = parsed = 0
    reported = False
    seen: set[str] = set()
    tasks = _source_tasks(view, m, selected, order, bounds, True, block_bytes)
    for result in pool.map(scan_source_file, tasks):
        item = plan.files[result.ordinal]
        if (result.sha256, result.rows, result.file_bytes) != (
            item.documents_sha256,
            item.documents,
            item.file_bytes,
        ):
            raise C05Error("input content changed since C05")
        done_bytes += result.file_bytes
        if done_bytes > total_bytes:
            raise C05Error("source reads exceed the reviewed source read bound")
        positions = order[bounds[result.ordinal] : bounds[result.ordinal + 1]]
        index_rows["offset"][positions] = result.offsets
        index_rows["length"][positions] = result.lengths
        index_rows["original_split"][positions] = result.original
        parsed += result.parsed
        for doc, content in result.selected:
            entry = chosen.get(doc.doc_id)
            # The C05 screen of every fed record: exact kept-train membership,
            # same allocation, content and size as selected; fed exactly once.
            if (
                entry is None
                or entry.allocation != file_allocation[result.ordinal]
                or entry.content != content
                or entry.size != doc.utf8_byte_count
                or doc.split != "train"
                or doc.doc_id in seen
            ):
                raise C05Error("selected fit record differs from its pass-1 C05 binding")
            seen.add(doc.doc_id)
            spool.add(doc)
            if fed:
                digest.update(b"\n")
            digest.update(fit_input_line(doc))
            fed += 1
            fed_bytes += doc.utf8_byte_count
        done_rows += result.rows
        if projection is not None:
            projection.progress("source", done_bytes, total_bytes)
        elapsed = max(time.monotonic() - begun, 1e-9)
        rate = done_bytes / elapsed
        fields: dict[str, Any] = {
            "files_committed": result.ordinal + 1,
            "files_total": len(plan.files),
            "bytes_done": done_bytes,
            "bytes_total": total_bytes,
            "gb_per_s": rate / 1e9,
            "split_checked": parsed,
            "selected_parsed": fed,
        }
        if projection is not None:
            view_now = projection.evaluate(time.monotonic() - started)
            fields["projected_total_s"] = round(float(view_now["projected_total_s"]), 1)
            if projection.deadline_seconds is not None:
                fields["deadline_s"] = projection.deadline_seconds
        reporter.update(done_rows, **fields)
        if not reported and elapsed >= 10 and done_bytes >= total_bytes * 0.02:
            reported = True
            measured["projected_pre_bpe_seconds"] = round(
                time.monotonic() - started + (total_bytes - done_bytes) / rate, 1
            )
        deadline.check()
    if fed != len(chosen) or done_rows != total_documents or done_bytes != total_bytes:
        raise C05Error("selected fit record missing from the second pass")
    if projection is not None:
        projection.finish("source")
    seconds = time.monotonic() - begun
    measured["source_seconds"] = round(seconds, 3)
    measured["source_gb_per_s"] = round(total_bytes / 1e9 / max(seconds, 1e-9), 3)
    measured["split_checked_per_s"] = round(parsed / max(seconds, 1e-9), 1)
    measured["selected_parsed"] = fed
    reporter.note(
        f"SLO | source {measured['source_gb_per_s']:,.3f} GB/s, "
        f"{measured['split_checked_per_s']:,.0f} split checks/s, {seconds:,.1f} s"
    )
    return SourcePass(index_rows, fed, fed_bytes, digest.hexdigest(), spool.seal())


# -- BPE child ------------------------------------------------------------------------------


def bpe_environment(threads: int) -> dict[str, str]:
    """Explicit child environment: an inherited TOKENIZERS_PARALLELISM=false is overridden."""
    if threads not in (1, 2, 4, 8, 16):
        raise C05Error("BPE threads must be 1, 2, 4, 8 or 16")
    environment = dict(os.environ)
    environment["TOKENIZERS_PARALLELISM"] = "true"
    environment["RAYON_NUM_THREADS"] = str(threads)
    return environment


def bpe_command(job: Path) -> list[str]:
    return [sys.executable, "-m", "xlm.data.exclusion.fitfast_child", str(job)]


def run_bpe_child(
    job: Path,
    threads: int,
    reporter: _Reporter,
    deadline: Checkable,
    log: Path | None = None,
    heartbeat: float = 30.0,
    *,
    supervisor: Supervisor | None = None,
) -> None:
    """Supervised child; deadline/RAM/interrupt terminate its whole tree, publish nothing.

    Without an enclosing ``supervisor`` (direct use), a local one enforces the given
    deadline and the reporter's RSS ceiling independently of progress display.
    ``log`` is accepted for compatibility; child output is never retained.
    """
    del log
    local: Supervisor | None = None
    if supervisor is None:
        ceiling = getattr(reporter, "ceiling", None)
        base = deadline if isinstance(deadline, Deadline) else Deadline(None, time.monotonic())
        local = Supervisor(base, ceiling if isinstance(ceiling, int) else None, interval=0.1)
        supervisor = local.__enter__()
    failed = True
    try:
        child = subprocess.Popen(  # noqa: S603 - fixed interpreter and module, no shell
            bpe_command(job),
            env=bpe_environment(threads),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            last_beat = time.monotonic()
            while True:
                supervisor.check()
                deadline.check()
                try:
                    code = child.wait(timeout=CHILD_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() - last_beat >= heartbeat:
                    last_beat = time.monotonic()
                    reporter.update(force=True)
            supervisor.check()  # A breach seen while the child was exiting still refuses.
        finally:
            if child.poll() is None:
                try:
                    root = psutil.Process(child.pid)
                    tree = [root, *root.children(recursive=True)]
                except psutil.Error:
                    tree = []
                terminate_processes(tree, supervisor.grace)
                child.wait()
        if code != 0:
            raise C05Error("BPE child failed; nothing published")
        failed = False
    finally:
        if local is not None:
            local.__exit__(None if not failed else C05Error, None, None)


def _bpe_result(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > JOB_CEILING:
        raise C05Error("BPE child produced no bounded result; nothing published")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise C05Error("BPE child result is malformed")
    return value


# -- fit ------------------------------------------------------------------------------------


def fit_tokenizer_fast(
    view: StreamedC05,
    policy: FitPolicy,
    policy_sha256: str,
    *,
    quotas: Path,
    ifm_split: Path,
    scratch: Path,
    output: Path,
    issuer: str,
    key: bytes,
    accepted_plan_digest: str,
    envelope: OperationalEnvelope | None = None,
    deficit_report_path: Path | None = None,
    supervisor: Supervisor | None = None,
    progress: RunProgress | NullProgress | None = None,
    inline: bool = False,
    heartbeat_seconds: float = 30.0,
) -> dict[str, Any]:
    """Supervised fast fit; the caller's supervisor deadline started at command dispatch."""
    envelope = envelope or OperationalEnvelope()
    locations = Locations(
        scratch,
        output,
        deficit_report_path or output.with_name(output.name + ".deficit.json"),
    )
    if supervisor is None:
        projection = envelope.projection()
        with Supervisor(
            Deadline(envelope.deadline_seconds, time.monotonic()),
            envelope.ram_ceiling_bytes,
            interval=envelope.monitor_interval_seconds,
            grace=envelope.shutdown_grace_seconds,
            projection=projection,
        ) as owned:
            return _fit(
                view, policy, policy_sha256, quotas, ifm_split, locations, issuer, key,
                accepted_plan_digest, envelope, owned, progress, inline, heartbeat_seconds,
            )  # fmt: skip
    return _fit(
        view, policy, policy_sha256, quotas, ifm_split, locations, issuer, key,
        accepted_plan_digest, envelope, supervisor, progress, inline, heartbeat_seconds,
    )  # fmt: skip


def _fit(
    view: StreamedC05,
    policy: FitPolicy,
    policy_sha256: str,
    quotas: Path,
    ifm_split: Path,
    locations: Locations,
    issuer: str,
    key: bytes,
    accepted_plan_digest: str,
    envelope: OperationalEnvelope,
    supervisor: Supervisor,
    progress: RunProgress | NullProgress | None,
    inline: bool,
    heartbeat_seconds: float,
) -> dict[str, Any]:
    started = supervisor.deadline.started
    supervisor.check()
    output, scratch = locations.output, locations.scratch
    if view.mode == "protected" and policy.mode != "production":
        raise C05Error("protected C05 membership requires the production fit policy")
    if output.exists():
        raise C05Error("tokenizer-fit output is write-once")
    production = policy.mode == "production" and view.mode == "protected"
    requirements = _requirements(
        policy, view.input_manifest, quotas, ifm_split, view.requirements_manifest()
    )
    budgets = fit_budgets(policy, requirements["allocations"])
    scratch.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    planned = fast_resource_plan(
        policy,
        view.plan,
        requirements,
        budgets,
        view.receipt_digest,
        view.completion,
        envelope,
        locations,
    )
    if planned["digest"] != accepted_plan_digest:
        raise C05Error("tokenizer-fit resource plan differs from the accepted plan")
    if supervisor.ram_ceiling != envelope.ram_ceiling_bytes:
        raise C05Error("supervisor RAM ceiling differs from the reviewed envelope")
    implementation = implementation_identity()
    supervisor.volumes.update(admit_storage(planned["storage"], locations))
    if supervisor.projection is not None:
        supervisor.projection.plan("membership", float(view.completion["membership_bytes"]))
        supervisor.projection.plan("source", float(planned["inputs"]["source_read_bytes"]))
    # Projected TOTAL from planning rates before any byte is read: warn (or, once
    # measured, abort) at the earliest point, not only on the next monitor tick.
    supervisor.sample()
    supervisor.check()
    storage = {k: v["bytes"] for k, v in planned["storage"]["items"].items()}
    reporter = _TreeReporter(
        progress or NullProgress(), envelope.ram_ceiling_bytes, envelope.ram_ceiling_bytes
    )
    allocation_keys = sorted(budgets)
    tables = membership_tables(view, policy, allocation_keys)
    token = uuid.uuid4().hex
    owned = OwnedPaths()
    work = scratch / f"c06-fit-{token}"
    stage = output.with_name(f"{output.name}.partial-{token}")
    measured: dict[str, Any] = {
        "source_workers": envelope.source_workers,
        "bpe_threads": envelope.bpe_threads,
    }
    published = False
    spool: HashedSpool | None = None
    try:
        owned.directory(work)
        owned.directory(stage)
        spool_path = owned.file(work / "fit.spool")
        job_path = owned.file(work / "bpe-job.json")
        result_path = owned.file(work / "bpe-result.json")
        sample_path = owned.file(stage / FIT_SAMPLE)
        index_dir = owned.directory(stage / KEPT_INDEX_DIR, create=False)
        for name in OWNED_FILES:
            owned.file(index_dir / name)
        directory = owned.directory(stage / TOKENIZER_DIR, create=False)
        for name in TOKENIZER_FILES:
            owned.file(directory / name)
        manifest_path = owned.file(stage / FIT_MANIFEST)
        resource_path = owned.file(stage / RESOURCE_PLAN)
        with OrderedPool(
            envelope.source_workers,
            tables,
            supervisor,
            inline=inline,
            grace=envelope.shutdown_grace_seconds,
        ) as pool:
            m = stream_membership(
                view,
                tables,
                pool,
                reporter,
                supervisor,
                measured,
                chunk_bytes=envelope.membership_chunk_bytes,
            )
            reconcile(view, m, allocation_keys)
            supervisor.sample()
            supervisor.check()
            selection_started = time.monotonic()
            states, selected = select_exact(m, policy, budgets, allocation_keys, supervisor)
            report = deficit_report(view, policy, requirements, budgets, states)
            if report is not None:
                raise FitDeficit(report)
            chosen, allocations = select_sample(states, budgets, reporter)
            del states
            sample_sha, sample_bytes = export_sample(chosen, sample_path, SAMPLE_CEILING)
            selected_bytes = sum(entry.size for entry in chosen.values())
            measured["selection_seconds"] = round(time.monotonic() - selection_started, 3)
            supervisor.check()
            spool = HashedSpool(spool_path, storage["bpe_spool"], max_frame_bytes(policy))
            passed = source_pass(
                view,
                m,
                selected,
                chosen,
                allocation_keys,
                pool,
                reporter,
                supervisor,
                measured,
                spool,
                started,
                block_bytes=envelope.source_block_bytes,
            )
        if (passed.fed_documents, passed.fed_bytes) != (len(chosen), selected_bytes):
            raise C05Error("BPE input is not the complete frozen sample")
        supervisor.check()
        reporter.stage("INDEX WRITE", None, "files")
        index_started = time.monotonic()
        index_envelope = write_index(
            index_dir,
            view,
            passed.index_rows,
            m.ids,
            m.id_offsets,
            [list(canonical.loads_strict(k)) for k in allocation_keys],
            implementation,
            issuer,
            key,
            ceiling=storage["kept_index"],
        )
        measured["index_write_seconds"] = round(time.monotonic() - index_started, 3)
        del m
        passed.index_rows = np.zeros(0, dtype=ROW_DTYPE)
        supervisor.check()
        expected_spool = passed.spool
        write_once(
            job_path,
            {
                "spool": str(spool_path),
                "output": str(directory),
                "result": str(result_path),
                "target_vocab_size": policy.tokenizer.target_vocab_size,
                "training_input_hash": passed.training_input_hash,
                "documents": expected_spool["frames"],
                "payload_bytes": expected_spool["payload_bytes"],
                "spool_sha256": expected_spool["sha256"],
                "spool_file_bytes": expected_spool["file_bytes"],
                "max_frame_bytes": max_frame_bytes(policy),
                "tokenizer_bytes_ceiling": storage["tokenizer"],
                "production": production,
            },
        )
        reporter.stage("TOKENIZER FIT", None, "steps")
        reporter.note(
            "TOKENIZER FIT | native BPE in a supervised child process; merge progress is "
            "not observable, only elapsed/RSS heartbeats are shown"
        )
        bpe_started = time.monotonic()
        if supervisor.projection is not None:
            supervisor.projection.bpe(started=True)
        run_bpe_child(
            job_path,
            envelope.bpe_threads,
            reporter,
            supervisor,
            None,
            heartbeat_seconds,
            supervisor=supervisor,
        )
        if supervisor.projection is not None:
            supervisor.projection.bpe(started=False)
        measured["bpe_seconds"] = round(time.monotonic() - bpe_started, 3)
        result = _bpe_result(result_path)
        consumed = (
            result.get("consumed_spool_sha256"),
            result.get("consumed_spool_bytes"),
            result.get("consumed_frames"),
            result.get("consumed_payload_bytes"),
        )
        expected = (
            expected_spool["sha256"],
            expected_spool["file_bytes"],
            expected_spool["frames"],
            expected_spool["payload_bytes"],
        )
        if result.get("status") != "ok" or consumed != expected:
            raise C05Error("BPE consumed bytes differ from the authenticated spool")
        if (file_sha(spool_path), spool_path.stat().st_size) != (
            expected_spool["sha256"],
            expected_spool["file_bytes"],
        ):
            raise C05Error("authenticated spool changed during BPE")
        reporter.stage("TOKENIZER SAVE", None, "files")
        names = sorted(p.name for p in directory.iterdir())
        if names != ["tokenizer.json", "tokenizer_manifest.json"]:
            raise C05Error("BPE child wrote unexpected tokenizer files")
        if sum((directory / n).stat().st_size for n in names) > storage["tokenizer"]:
            raise C05Error("tokenizer output exceeds its ceiling")
        from xlm.tokenizers.bpe import ByteLevelBPETokenizer

        fingerprint = ByteLevelBPETokenizer.load(directory).fingerprint
        write_once(
            directory / "c05-binding.json",
            {
                "plan_digest": view.plan_digest,
                "completion_digest": view.receipt_digest,
                "tokenizer_fingerprint": fingerprint,
            },
        )
        reporter.stage("VERIFY", None, "checks")
        identity = _verify_tokenizer(
            directory, view, policy, production, passed.training_input_hash
        )
        body = {
            **fit_record(
                view,
                policy,
                policy_sha256,
                requirements,
                allocations,
                production=production,
                selected_documents=len(chosen),
                selected_bytes=selected_bytes,
                sample_sha=sample_sha,
                sample_bytes=sample_bytes,
                training_input_hash=passed.training_input_hash,
                fed_documents=passed.fed_documents,
                fed_bytes=passed.fed_bytes,
                tokenizer=identity,
            ),
            "fit_path": FIT_PATH,
            "bpe_spool": {
                "sha256": expected_spool["sha256"],
                "file_bytes": expected_spool["file_bytes"],
                "frames": expected_spool["frames"],
                "payload_bytes": expected_spool["payload_bytes"],
                "consumed_by_bpe": True,
            },
            "kept_index": {
                "directory": KEPT_INDEX_DIR,
                "manifest_digest": index_envelope["digest"],
            },
            "resource_plan_digest": planned["digest"],
            "implementation": implementation,
        }
        envelope_out = signed(body, issuer, key)
        write_once(manifest_path, envelope_out)
        reporter.telemetry()
        measured.update(
            elapsed_before_publication_seconds=round(time.monotonic() - started, 3),
            deadline_seconds=envelope.deadline_seconds,
            peak_process_tree_rss_bytes=max(reporter.peak, supervisor.peak_rss),
            supervisor_samples=supervisor.samples,
            bpe_spool_bytes=expected_spool["file_bytes"],
        )
        write_once(
            resource_path,
            {
                "plan": planned,
                "measured": {
                    **measured,
                    "basis": "measured in this run; operational, not part of the signed manifest",
                },
            },
        )
        # Work paths (spool, job, result) are removed BEFORE publication; any residue
        # refuses rather than leaving a published fit with unaccounted scratch.
        residue = OwnedPaths([spool_path, job_path, result_path], [work]).cleanup()
        if residue:
            raise C05Error(f"cleanup residue in owned scratch ({len(residue)} paths)")
        # Pre-publication re-verification of exactly what will be published.
        reverify_sections(index_dir, index_envelope["payload"]["sections"])
        if file_sha(sample_path) != sample_sha:
            raise C05Error("tokenizer-fit sample artifact changed")
        if _verify_tokenizer(directory, view, policy, production, passed.training_input_hash) != (
            identity
        ):
            raise C05Error("tokenizer files changed before publication")
        if sorted(p.name for p in stage.iterdir()) != sorted(
            [FIT_SAMPLE, KEPT_INDEX_DIR, TOKENIZER_DIR, FIT_MANIFEST, RESOURCE_PLAN]
        ):
            raise C05Error("staging holds unaccounted files")
        supervisor.check()
        remaining = supervisor.remaining()
        if remaining is not None and remaining < envelope.publication_margin_seconds:
            raise C05Error("operator deadline too close to publish; nothing published")
        if output.exists():
            raise C05Error("tokenizer-fit output is write-once")
        os.rename(stage, output)
        published = True
        total = time.monotonic() - started
        reporter.note(
            f"SLO | total {total:,.1f} s of {envelope.deadline_seconds:,.0f} s deadline: published"
        )
        reporter.complete()
        return envelope_out
    except BaseException as exc:
        if spool is not None:
            spool.close()
        residue = [] if published else owned.cleanup()
        if isinstance(exc, FitDeficit):
            if residue:
                exc.report["cleanup_residue_paths"] = len(residue)
            raise
        if isinstance(exc, KeyboardInterrupt) and supervisor.failure is None and not residue:
            raise  # A genuine operator interrupt stays an interrupt (CLI exit 130).
        if supervisor.failure is not None:
            reason = supervisor.failure
        elif isinstance(exc, C05Error):
            reason = str(exc)
        else:  # I/O or unexpected failure: a content-free refusal, never success.
            reason = f"tokenizer fit refused: {type(exc).__name__}"
        suffix = ""
        if supervisor.deadline.expired():
            suffix += f" (cleanup finished {supervisor.overshoot():.2f} s after the deadline)"
        if residue:
            suffix += f"; cleanup residue in {len(residue)} owned paths"
        if isinstance(exc, C05Error) and reason == str(exc) and not suffix:
            raise
        raise C05Error(reason + suffix) from exc


# -- verification ---------------------------------------------------------------------------


def _verifier(envelope: OperationalEnvelope) -> Supervisor:
    return Supervisor(
        Deadline(None, time.monotonic()),
        envelope.ram_ceiling_bytes,
        interval=envelope.monitor_interval_seconds,
        grace=envelope.shutdown_grace_seconds,
    )


def verify_fit_fast(
    view: StreamedC05,
    directory: Path,
    policy: FitPolicy,
    quotas: Path,
    ifm_split: Path,
    *,
    workers: int = DEFAULT_WORKERS,
    sources: bool = False,
    progress: RunProgress | NullProgress | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Re-derive the sample from authenticated membership and compare every artifact.

    ``sources=True`` additionally re-runs the source pass (all file hashes, original
    splits, index locations, the exact BPE input hash and the spool digest).
    """
    envelope = read_metadata(directory / FIT_MANIFEST, digested=False)
    body = verify_signed(envelope, view.trusted)
    check_binding(body, view, FIT_KIND)
    if body.get("fit_path") != FIT_PATH:
        raise C05Error("not a C06 fast-path fit")
    if body.get("policy_digest") != policy.identity() or body.get("policy") != policy.model_dump(
        mode="json"
    ):
        raise C05Error("tokenizer fit used a different fit policy")
    production = policy.mode == "production" and view.mode == "protected"
    if body.get("production") != production:
        raise C05Error("tokenizer fit production flag mismatch")
    requirements = _requirements(
        policy, view.input_manifest, quotas, ifm_split, view.requirements_manifest()
    )
    budgets = fit_budgets(policy, requirements["allocations"])
    planned = read_metadata(directory / RESOURCE_PLAN, digested=False)["plan"]
    if planned.get("digest") != canonical.self_digest(planned) or body.get(
        "resource_plan_digest"
    ) != planned.get("digest"):
        raise C05Error("tokenizer fit resource plan changed")
    operational = OperationalEnvelope.model_validate(planned["operational"])
    expected_plan = fast_resource_plan(
        policy,
        view.plan,
        requirements,
        budgets,
        view.receipt_digest,
        view.completion,
        operational,
        None,
    )
    for name in ("policy_digest", "plan_digest", "completion_digest", "inputs", "sample"):
        if planned.get(name) != expected_plan[name]:
            raise C05Error("tokenizer fit resource plan changed")
    reporter = _TreeReporter(
        progress or NullProgress(), view.plan.resources.ram_bytes, DEFAULT_RSS_CEILING
    )
    allocation_keys = sorted(budgets)
    tables = membership_tables(view, policy, allocation_keys)
    measured: dict[str, Any] = {}
    with (
        _verifier(operational) as supervisor,
        OrderedPool(workers, tables, supervisor, inline=inline) as pool,
    ):
        m = stream_membership(view, tables, pool, reporter, supervisor, measured)
        reconcile(view, m, allocation_keys)
        states, selected = select_exact(m, policy, budgets, allocation_keys, supervisor)
        if deficit_report(view, policy, requirements, budgets, states) is not None:
            raise C05Error("tokenizer fit sample violates the shortfall rule")
        chosen, allocations = select_sample(states, budgets, reporter)
        sample = directory / FIT_SAMPLE
        replay = directory.parent / f".{directory.name}.verify-{uuid.uuid4().hex}.jsonl"
        try:
            replay_sha, _ = export_sample(chosen, replay, SAMPLE_CEILING)
        finally:
            replay.unlink(missing_ok=True)
        if (
            replay_sha != file_sha(sample)
            or replay_sha != body["sample"]["selected_membership_sha256"]
        ):
            raise C05Error("tokenizer fit sample differs from the authenticated selection")
        for name, row in allocations.items():
            signed_row = body["allocations"].get(name, {})
            if any(signed_row.get(k) != v for k, v in row.items()):
                raise C05Error("tokenizer fit allocation totals differ from the selection")
        index = open_index(directory / KEPT_INDEX_DIR, view)
        if index.manifest["digest"] != body["kept_index"]["manifest_digest"]:
            raise C05Error("kept index differs from the signed fit")
        _compare_index(index.rows, m)
        if sources:
            spool = HashedSpool(None, 1 << 62, max_frame_bytes(policy))
            passed = source_pass(
                view,
                m,
                selected,
                chosen,
                allocation_keys,
                pool,
                reporter,
                supervisor,
                measured,
                spool,
                time.monotonic(),
            )
            for column in ("offset", "length", "original_split"):
                if not np.array_equal(passed.index_rows[column], index.rows[column]):
                    raise C05Error("kept index locations differ from the sources")
            if passed.training_input_hash != body["sample"]["training_input_hash"]:
                raise C05Error("tokenizer training input differs from the sources")
            signed_spool = body["bpe_spool"]
            if (
                passed.spool["sha256"],
                passed.spool["file_bytes"],
                passed.spool["frames"],
                passed.spool["payload_bytes"],
            ) != (
                signed_spool["sha256"],
                signed_spool["file_bytes"],
                signed_spool["frames"],
                signed_spool["payload_bytes"],
            ):
                raise C05Error("BPE spool differs from the one the sources produce")
    identity = _verify_tokenizer(
        directory / TOKENIZER_DIR, view, policy, production, body["sample"]["training_input_hash"]
    )
    if identity != body["tokenizer"]:
        raise C05Error("tokenizer files differ from the signed fit")
    index.reverify()  # The verified on-disk snapshot is still what is on disk.
    if file_sha(sample) != body["sample"]["selected_membership_sha256"]:
        raise C05Error("tokenizer fit sample changed during verification")
    return {
        "verified": True,
        "mode": view.mode,
        "production": production,
        "fit_digest": envelope["digest"],
        "tokenizer_fingerprint": identity["fingerprint"],
        "selected_documents": body["sample"]["documents"],
        "selected_canonical_bytes": body["sample"]["canonical_bytes"],
        "sources_rehashed": sources,
    }


def _compare_index(rows: npt.NDArray[Any], m: Membership) -> None:
    for column, values in (
        ("file", m.file),
        ("row", m.row),
        ("bytes", m.nbytes),
        ("content", m.content),
        ("assigned_split", m.split),
        ("allocation", m.allocation),
    ):
        if not np.array_equal(rows[column], values):
            raise C05Error("kept index differs from authenticated membership: " + column)


def verify_kept_index(
    view: StreamedC05,
    directory: Path,
    policy: FitPolicy,
    quotas: Path,
    ifm_split: Path,
    *,
    membership: bool = False,
    sources: bool = False,
    workers: int = DEFAULT_WORKERS,
    progress: RunProgress | NullProgress | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Signature, bindings, private snapshot, structure; optionally re-derive from inputs."""
    index = open_index(directory, view)
    assigned = np.bincount(index.rows["assigned_split"], minlength=len(SPLIT_NAMES))
    train = index.rows["assigned_split"] == 0
    result: dict[str, Any] = {
        "verified": True,
        "rows": len(index),
        "index_digest": index.manifest["digest"],
        "plan_digest": view.plan_digest,
        "completion_digest": view.receipt_digest,
        # Content-free counts of the verified snapshot: kept rows by C05-assigned split.
        "assigned_splits": {name: int(assigned[n]) for n, name in enumerate(SPLIT_NAMES)},
        "train_canonical_bytes": int(index.rows["bytes"][train].sum(dtype=np.uint64)),
        "membership_rederived": False,
        "sources_rehashed": False,
    }
    if membership or sources:
        requirements = _requirements(
            policy, view.input_manifest, quotas, ifm_split, view.requirements_manifest()
        )
        allocation_keys = sorted(fit_budgets(policy, requirements["allocations"]))
        if [list(a) for a in index.allocations] != [
            list(canonical.loads_strict(k)) for k in allocation_keys
        ]:
            raise C05Error("kept index allocation table differs")
        tables = membership_tables(view, policy, allocation_keys)
        reporter = _TreeReporter(
            progress or NullProgress(), view.plan.resources.ram_bytes, DEFAULT_RSS_CEILING
        )
        with (
            _verifier(OperationalEnvelope()) as supervisor,
            OrderedPool(workers, tables, supervisor, inline=inline) as pool,
        ):
            m = stream_membership(view, tables, pool, reporter, supervisor, {})
            reconcile(view, m, allocation_keys)
            _compare_index(index.rows, m)
            if bytes(index.ids) != m.ids or not np.array_equal(index.id_offsets, m.id_offsets):
                raise C05Error("kept index ids differ from authenticated membership")
            result["membership_rederived"] = True
            if sources:
                passed = source_pass(
                    view,
                    m,
                    np.zeros(m.rows, dtype=np.bool_),
                    {},
                    allocation_keys,
                    pool,
                    reporter,
                    supervisor,
                    {},
                    HashedSpool(None, 1 << 62, max_frame_bytes(policy)),
                    time.monotonic(),
                )
                for column in ("offset", "length", "original_split"):
                    if not np.array_equal(passed.index_rows[column], index.rows[column]):
                        raise C05Error("kept index locations differ from the sources")
                result["sources_rehashed"] = True
    index.reverify()
    return result
