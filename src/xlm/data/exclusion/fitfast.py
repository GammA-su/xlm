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
   every completion aggregate. No SQLite, no per-record queries.
2. **Selection** is the exact shortest rank prefix per allocation (vectorized; same
   SHA-256 rank, doc-id tie order, budgets, 1 MiB cap, whole crossing document).
   A deficit refuses here, before any source byte is read.
3. **One source pass** hashes every byte of every plan file once (size, SHA-256 and
   row count must equal the plan). Every kept row is strict-JSON parsed to check
   its id and ORIGINAL split (a C05-train row whose canonical ``split`` is not
   ``train`` refuses, exactly as bb886bd) and byte count. Only selected rows are
   fully decoded and content-digested. Non-kept rows are hashed, never parsed.
4. **BPE** runs in a child process with a controlled environment
   (``TOKENIZERS_PARALLELISM=true``, explicit ``RAYON_NUM_THREADS``) and the
   operator deadline; nothing publishes unless every check passes.

The same pass writes the reusable kept-membership index (:mod:`keptindex`).
Worker and thread counts are operational only and never change any output.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import psutil

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed, verify_signed
from xlm.data.exclusion.fitscan import (
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
from xlm.data.exclusion.keptindex import ROW_DTYPE, open_index, write_index
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.runner import file_sha, verify_completion_envelope
from xlm.data.exclusion.selection import check_binding
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
from xlm.tokenizers.bpe import fit_input_line, write_fit_frame

FIT_PATH = "c06-fast-v1"
RESOURCE_PLAN_KIND = "c06_tokenizer_fit_fast_resource_plan_v1"
KEPT_INDEX_DIR = "kept-index"
SLO_SECONDS = 1200
DEFAULT_WORKERS = 8
DEFAULT_BPE_THREADS = 16
DEFAULT_RSS_CEILING = 32 * 1024**3
RSS_TARGET = 24 * 1024**3
MEMBERSHIP_CHUNK_BYTES = 16 * 1024**2
# Planning figures for the in-memory membership columns (not measurements).
COLUMN_BYTES_PER_ROW = 4 + 4 + 8 + 32 + 1 + 2 + 32 + 8
INDEX_BYTES_PER_ROW = ROW_DTYPE.itemsize + 8 + 4


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
    )


# -- resource plan / SLO --------------------------------------------------------------------


def fast_resource_plan(
    policy: FitPolicy,
    plan: ExecutionPlan,
    requirements: Mapping[str, Any],
    budgets: Mapping[str, Budget],
    completion_digest: str,
    completion: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic, content-free plan; operational knobs (workers, threads) excluded."""
    allocations = len(budgets)
    file_bytes = sum(f.file_bytes for f in plan.files)
    membership_bytes = int(completion["membership_bytes"])
    kept = int(completion["kept"])
    overshoot = allocations * policy.max_document_bytes
    sample_bound = policy.target_sample_bytes + overshoot
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
        },
        "memory": {
            "membership_columns_bound_bytes": kept * COLUMN_BYTES_PER_ROW + membership_bytes,
            "bpe_peak_estimate_bytes": estimated_fit_peak_memory_bytes(
                sample_bound, policy.tokenizer.target_vocab_size
            ),
            "process_tree_target_bytes": RSS_TARGET,
            "process_tree_ceiling_bytes": DEFAULT_RSS_CEILING,
        },
        "scratch": {
            "bpe_spool_bound_bytes": sample_bound + SPOOL_FRAME_BYTES * MAX_SAMPLE_DOCUMENTS
        },
        "output": {
            "kept_index_bound_bytes": kept * INDEX_BYTES_PER_ROW + 8 + membership_bytes,
            "sample_file_ceiling_bytes": plan.resources.output_bytes,
        },
        "slo_seconds": SLO_SECONDS,
        "network_required": False,
        "gpu_required": False,
        "basis": (
            "Bounds from the frozen policy, C05 plan inventory and signed completion "
            "counts. Memory figures are planning estimates (BPE reuses the P11 formula); "
            "measured stage times, throughput and peak RSS are appended after the run."
        ),
    }
    body["digest"] = canonical.self_digest(body)
    return body


def plan_from_proof_fast(
    proof: Path, policy: FitPolicy, quotas: Path, ifm_split: Path
) -> dict[str, Any]:
    """Metadata-only plan: no corpus or membership bytes. Call ``guard_proof`` first."""
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
    requirements = _requirements(policy, manifest, quotas, ifm_split)
    budgets = fit_budgets(policy, requirements["allocations"])
    return fast_resource_plan(
        policy, plan, requirements, budgets, spec.completion_digest, completion["payload"]
    )


class Deadline:
    """Operational wall-clock ceiling; never part of any scientific identity."""

    def __init__(self, seconds: float | None, started: float) -> None:
        if seconds is not None and seconds <= 0:
            raise C05Error("deadline must be positive")
        self.seconds, self.started = seconds, started

    def remaining(self) -> float | None:
        if self.seconds is None:
            return None
        return self.seconds - (time.monotonic() - self.started)

    def check(self) -> None:
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise C05Error("operator deadline exceeded; nothing published")


class _TreeReporter(_Reporter):
    """Process-tree RSS (parent, scan workers, BPE child) with a hard ceiling."""

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
        if total > self.ceiling:
            raise C05Error("C06 process-tree RSS exceeded its reviewed ceiling")
        return {"rss": total, "peak_rss": self.peak, "ram_limit": self.ceiling}


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
    )


def _membership_blocks(
    path: Path, expected_bytes: int, ceiling: int, state: dict[str, Any]
) -> Iterator[bytes]:
    """Complete lines in order; ``state`` accumulates the SHA-256 of exactly these bytes."""
    digest = state["sha"]
    pending = b""
    with path.open("rb", buffering=0) as stream:
        while block := stream.read(MEMBERSHIP_CHUNK_BYTES):
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


def stream_membership(
    view: StreamedC05,
    tables: MembershipTables,
    pool: OrderedPool,
    reporter: _Reporter,
    deadline: Deadline,
    measured: dict[str, Any],
) -> Membership:
    """One authenticated pass; refuses unless SHA, size and count equal the completion."""
    completion = view.completion
    plan = view.plan
    expected_bytes = int(completion["membership_bytes"])
    kept = int(completion["kept"])
    if expected_bytes > plan.resources.output_bytes or kept > plan.resources.records:
        raise C05Error("membership exceeds its reviewed ceilings")
    reporter.stage("MEMBERSHIP STREAM", kept, "rows")
    started = time.monotonic()
    state: dict[str, Any] = {"sha": hashlib.sha256(), "bytes": 0}
    parts: list[MembershipChunk] = []
    rows = 0
    last: bytes | None = None
    blocks = _membership_blocks(
        view.directory / "membership.jsonl", expected_bytes, tables.line_ceiling, state
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
    """Every signed completion aggregate must be reproduced from the authenticated rows."""
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


# -- one source pass ------------------------------------------------------------------------


@dataclass
class SourcePass:
    index_rows: npt.NDArray[Any]
    fed_documents: int
    fed_bytes: int
    spool_bytes: int
    training_input_hash: str


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
        )


def source_pass(
    view: StreamedC05,
    m: Membership,
    selected: npt.NDArray[np.bool_],
    chosen: Mapping[str, _Chosen],
    allocation_keys: list[str],
    pool: OrderedPool,
    reporter: _Reporter,
    deadline: Deadline,
    measured: dict[str, Any],
    spool: Path | None,
    started: float,
) -> SourcePass:
    """Hash every plan file once; fill the index; spool selected records in feed order."""
    plan = view.plan
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
    fed = fed_bytes = spool_bytes = done_rows = done_bytes = parsed = selected_parsed = 0
    warned = False
    seen: set[str] = set()
    stream = spool.open("xb") if spool is not None else None
    try:
        tasks = _source_tasks(view, m, selected, order, bounds, parse=True)
        for result in pool.map(scan_source_file, tasks):
            item = plan.files[result.ordinal]
            if (result.sha256, result.rows, result.file_bytes) != (
                item.documents_sha256,
                item.documents,
                item.file_bytes,
            ):
                raise C05Error("input content changed since C05")
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
                if stream is not None:
                    spool_bytes += write_fit_frame(stream, doc)
                if fed:
                    digest.update(b"\n")
                digest.update(fit_input_line(doc))
                fed += 1
                fed_bytes += doc.utf8_byte_count
                selected_parsed += 1
            done_rows += result.rows
            done_bytes += result.file_bytes
            elapsed = max(time.monotonic() - begun, 1e-9)
            rate = done_bytes / elapsed
            reporter.update(
                done_rows,
                files_committed=result.ordinal + 1,
                files_total=len(plan.files),
                bytes_done=done_bytes,
                bytes_total=total_bytes,
                gb_per_s=rate / 1e9,
                split_checked=parsed,
                selected_parsed=selected_parsed,
            )
            if not warned and elapsed >= 10 and done_bytes >= total_bytes * 0.02:
                projected = time.monotonic() - started + (total_bytes - done_bytes) / rate
                measured["projected_pre_bpe_seconds"] = round(projected, 1)
                if projected > SLO_SECONDS:
                    warned = True
                    reporter.note(
                        f"SLO WARNING | projected pre-BPE time {projected:,.0f} s already "
                        f"exceeds the {SLO_SECONDS} s target"
                    )
                else:
                    reporter.note(
                        f"SLO | projected pre-BPE time {projected:,.0f} s of {SLO_SECONDS} s; "
                        "BPE time is not projectable before it runs"
                    )
                    warned = True
            deadline.check()
        if stream is not None:
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if stream is not None:
            stream.close()
    if fed != len(chosen) or done_rows != total_documents:
        raise C05Error("selected fit record missing from the second pass")
    seconds = time.monotonic() - begun
    measured["source_seconds"] = round(seconds, 3)
    measured["source_gb_per_s"] = round(total_bytes / 1e9 / max(seconds, 1e-9), 3)
    measured["split_checked_per_s"] = round(parsed / max(seconds, 1e-9), 1)
    measured["selected_parsed"] = selected_parsed
    reporter.note(
        f"SLO | source {measured['source_gb_per_s']:,.3f} GB/s, "
        f"{measured['split_checked_per_s']:,.0f} split checks/s, {seconds:,.1f} s"
    )
    return SourcePass(index_rows, fed, fed_bytes, spool_bytes, digest.hexdigest())


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
    deadline: Deadline,
    log: Path,
    heartbeat: float,
) -> None:
    """Bounded child; the deadline terminates it and the parent publishes nothing."""
    with log.open("wb") as sink:
        child = subprocess.Popen(  # noqa: S603 - fixed interpreter and module, no shell
            bpe_command(job), env=bpe_environment(threads), stdout=sink, stderr=sink
        )
        try:
            while True:
                remaining = deadline.remaining()
                wait = heartbeat if remaining is None else max(0.01, min(heartbeat, remaining))
                try:
                    code = child.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    reporter.update(force=True)
                    deadline.check()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    if code != 0:
        raise C05Error("BPE child failed; nothing published")


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
    workers: int = DEFAULT_WORKERS,
    bpe_threads: int = DEFAULT_BPE_THREADS,
    deadline_seconds: float | None = None,
    rss_ceiling: int = DEFAULT_RSS_CEILING,
    progress: RunProgress | NullProgress | None = None,
    heartbeat_seconds: float = 30.0,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = Deadline(deadline_seconds, started)
    if view.mode == "protected" and policy.mode != "production":
        raise C05Error("protected C05 membership requires the production fit policy")
    if output.exists():
        raise C05Error("tokenizer-fit output is write-once")
    production = policy.mode == "production" and view.mode == "protected"
    requirements = _requirements(policy, view.input_manifest, quotas, ifm_split)
    budgets = fit_budgets(policy, requirements["allocations"])
    planned = fast_resource_plan(
        policy, view.plan, requirements, budgets, view.receipt_digest, view.completion
    )
    implementation = implementation_identity()
    if planned["digest"] != accepted_plan_digest:
        raise C05Error("tokenizer-fit resource plan differs from the accepted plan")
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < planned["scratch"]["bpe_spool_bound_bytes"]:
        raise C05Error("scratch volume cannot hold the planned BPE spool bound")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < planned["output"]["kept_index_bound_bytes"]:
        raise C05Error("output volume cannot hold the planned kept index")
    reporter = _TreeReporter(progress or NullProgress(), view.plan.resources.ram_bytes, rss_ceiling)
    allocation_keys = sorted(budgets)
    tables = membership_tables(view, policy, allocation_keys)
    token = uuid.uuid4().hex
    work = scratch / f"c06-fit-{token}"
    stage = output.with_name(f"{output.name}.partial-{token}")
    measured: dict[str, Any] = {"workers": workers, "bpe_threads": bpe_threads}
    published = False
    try:
        work.mkdir()
        stage.mkdir()
        with OrderedPool(workers, tables) as pool:
            m = stream_membership(view, tables, pool, reporter, deadline, measured)
            reconcile(view, m, allocation_keys)
            states, selected = select_exact(m, policy, budgets, allocation_keys)
            report = deficit_report(view, policy, requirements, budgets, states)
            if report is not None:
                raise FitDeficit(report)
            chosen, allocations = select_sample(states, budgets, reporter)
            del states
            sample_sha, sample_bytes = export_sample(
                chosen, stage / FIT_SAMPLE, view.plan.resources.output_bytes
            )
            selected_bytes = sum(entry.size for entry in chosen.values())
            deadline.check()
            spool = work / "fit.spool"
            passed = source_pass(
                view,
                m,
                selected,
                chosen,
                allocation_keys,
                pool,
                reporter,
                deadline,
                measured,
                spool,
                started,
            )
        if (passed.fed_documents, passed.fed_bytes) != (len(chosen), selected_bytes):
            raise C05Error("BPE input is not the complete frozen sample")
        reporter.stage("INDEX WRITE", None, "files")
        index_started = time.monotonic()
        index_envelope = write_index(
            stage / KEPT_INDEX_DIR,
            view,
            passed.index_rows,
            m.ids,
            m.id_offsets,
            [list(canonical.loads_strict(k)) for k in allocation_keys],
            implementation,
            issuer,
            key,
        )
        measured["index_write_seconds"] = round(time.monotonic() - index_started, 3)
        del m
        passed.index_rows = np.zeros(0, dtype=ROW_DTYPE)
        deadline.check()
        directory = stage / TOKENIZER_DIR
        job = work / "bpe-job.json"
        job.write_text(
            json.dumps(
                {
                    "spool": str(spool),
                    "output": str(directory),
                    "target_vocab_size": policy.tokenizer.target_vocab_size,
                    "training_input_hash": passed.training_input_hash,
                    "documents": passed.fed_documents,
                    "spool_bytes": passed.spool_bytes,
                    "production": production,
                }
            ),
            encoding="utf-8",
        )
        reporter.stage("TOKENIZER FIT", None, "steps")
        reporter.note(
            "TOKENIZER FIT | native BPE in a controlled child process; merge progress is "
            "not observable, only elapsed/RSS heartbeats are shown"
        )
        bpe_started = time.monotonic()
        run_bpe_child(job, bpe_threads, reporter, deadline, work / "bpe.log", heartbeat_seconds)
        measured["bpe_seconds"] = round(time.monotonic() - bpe_started, 3)
        reporter.stage("TOKENIZER SAVE", None, "files")
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
        if file_sha(stage / FIT_SAMPLE) != sample_sha:
            raise C05Error("tokenizer-fit sample artifact changed")
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
            "kept_index": {
                "directory": KEPT_INDEX_DIR,
                "manifest_digest": index_envelope["digest"],
            },
            "resource_plan_digest": planned["digest"],
            "implementation": implementation,
        }
        envelope = signed(body, issuer, key)
        write_once(stage / FIT_MANIFEST, envelope)
        reporter.telemetry()
        total = time.monotonic() - started
        measured.update(
            total_seconds=round(total, 3),
            slo_seconds=SLO_SECONDS,
            slo_met=total <= SLO_SECONDS,
            peak_process_tree_rss_bytes=reporter.peak,
            bpe_spool_bytes=passed.spool_bytes + SPOOL_FRAME_BYTES * passed.fed_documents,
        )
        write_once(
            stage / RESOURCE_PLAN,
            {
                "plan": planned,
                "measured": {
                    **measured,
                    "basis": "measured in this run; operational, not part of the signed manifest",
                },
            },
        )
        deadline.check()
        if output.exists():
            raise C05Error("tokenizer-fit output is write-once")
        os.rename(stage, output)
        published = True
        reporter.note(
            f"SLO | total {total:,.1f} s of {SLO_SECONDS} s target: "
            + ("MET" if total <= SLO_SECONDS else "MISSED")
        )
        reporter.complete()
        return envelope
    finally:
        if not published:
            shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


# -- verification ---------------------------------------------------------------------------


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
) -> dict[str, Any]:
    """Re-derive the sample from authenticated membership and compare every artifact.

    ``sources=True`` additionally re-runs the source pass (all file hashes, original
    splits, index locations and the exact BPE input hash).
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
    requirements = _requirements(policy, view.input_manifest, quotas, ifm_split)
    budgets = fit_budgets(policy, requirements["allocations"])
    planned = read_metadata(directory / RESOURCE_PLAN, digested=False)["plan"]
    if planned != fast_resource_plan(
        policy, view.plan, requirements, budgets, view.receipt_digest, view.completion
    ) or body.get("resource_plan_digest") != planned.get("digest"):
        raise C05Error("tokenizer fit resource plan changed")
    reporter = _TreeReporter(
        progress or NullProgress(), view.plan.resources.ram_bytes, DEFAULT_RSS_CEILING
    )
    allocation_keys = sorted(budgets)
    tables = membership_tables(view, policy, allocation_keys)
    deadline = Deadline(None, time.monotonic())
    measured: dict[str, Any] = {}
    with OrderedPool(workers, tables) as pool:
        m = stream_membership(view, tables, pool, reporter, deadline, measured)
        reconcile(view, m, allocation_keys)
        states, selected = select_exact(m, policy, budgets, allocation_keys)
        if deficit_report(view, policy, requirements, budgets, states) is not None:
            raise C05Error("tokenizer fit sample violates the shortfall rule")
        chosen, allocations = select_sample(states, budgets, reporter)
        sample = directory / FIT_SAMPLE
        replay = directory.parent / f".{directory.name}.verify-{uuid.uuid4().hex}.jsonl"
        try:
            replay_sha, _ = export_sample(chosen, replay, view.plan.resources.output_bytes)
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
            passed = source_pass(
                view,
                m,
                selected,
                chosen,
                allocation_keys,
                pool,
                reporter,
                deadline,
                measured,
                None,
                time.monotonic(),
            )
            for column in ("offset", "length", "original_split"):
                if not np.array_equal(passed.index_rows[column], index.rows[column]):
                    raise C05Error("kept index locations differ from the sources")
            if passed.training_input_hash != body["sample"]["training_input_hash"]:
                raise C05Error("tokenizer training input differs from the sources")
    identity = _verify_tokenizer(
        directory / TOKENIZER_DIR, view, policy, production, body["sample"]["training_input_hash"]
    )
    if identity != body["tokenizer"]:
        raise C05Error("tokenizer files differ from the signed fit")
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
) -> dict[str, Any]:
    """Signature, C05/plan bindings and section hashes; optionally re-derive from inputs."""
    index = open_index(directory, view)
    result: dict[str, Any] = {
        "verified": True,
        "rows": len(index),
        "index_digest": index.manifest["digest"],
        "membership_rederived": False,
        "sources_rehashed": False,
    }
    if not (membership or sources):
        return result
    requirements = _requirements(policy, view.input_manifest, quotas, ifm_split)
    allocation_keys = sorted(fit_budgets(policy, requirements["allocations"]))
    if [list(a) for a in index.allocations] != [
        list(canonical.loads_strict(k)) for k in allocation_keys
    ]:
        raise C05Error("kept index allocation table differs")
    tables = membership_tables(view, policy, allocation_keys)
    reporter = _TreeReporter(
        progress or NullProgress(), view.plan.resources.ram_bytes, DEFAULT_RSS_CEILING
    )
    deadline = Deadline(None, time.monotonic())
    with OrderedPool(workers, tables) as pool:
        m = stream_membership(view, tables, pool, reporter, deadline, {})
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
                deadline,
                {},
                None,
                time.monotonic(),
            )
            for column in ("offset", "length", "original_split"):
                if not np.array_equal(passed.index_rows[column], index.rows[column]):
                    raise C05Error("kept index locations differ from the sources")
            result["sources_rehashed"] = True
    return result
