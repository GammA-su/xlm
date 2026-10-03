"""C06 tokenizer fit over exact C05 kept training membership (bounded two-pass bridge).

Chain: C05 proof -> frozen data-only fit policy -> exact per-allocation byte budgets
-> pass 1 (one sequential re-read of the hash-verified C05 input files; every kept
``train`` record is content-checked against C05 and ranked; each allocation keeps
only its current rank-ordered prefix) -> frozen sample -> pass 2 (second sequential
re-read; every selected record is re-verified through the C05 gate and fed to
ByteLevel BPE exactly once) -> tokenizer + ``c05-binding.json`` -> signed manifest.

Memory is bounded by the sample, never the corpus: the C05 kept-membership lookup
is the gate's on-disk SQLite index, and pass 1 retains at most
``MAX_SAMPLE_DOCUMENTS`` sample entries. No combined corpus file is written; the
BPE spool holds only the selected text, in the job's own scratch.

Published layout (one write-once directory, renamed into place last)::

    <output>/tokenizer/{tokenizer.json, tokenizer_manifest.json, c05-binding.json}
    <output>/tokenizer_fit_manifest.json      signed, content-free
    <output>/tokenizer_fit_sample.jsonl       selected doc IDs + C05 content digests
    <output>/tokenizer_fit_resource_plan.json accepted plan + measured resources

Downstream ``count-tokens``/``select``/``tokenize-selection``/``freeze`` take
``--tokenizer <output>/tokenizer``; ``selection.tokenizer_identity`` is unchanged.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from xlm.config.composer import load_yaml_str
from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, Sha, signed, verify_signed
from xlm.data.exclusion.gates import MembershipGate
from xlm.data.exclusion.identity import implementation_identity
from xlm.data.exclusion.inputs import COMPONENTS, read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.quotas import frozen_requirements
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.selection import (
    _kept_train,
    allocation_key,
    binding_of,
    check_binding,
    iter_plan_documents,
    tokenizer_identity,
)
from xlm.data.pools.tokenizer_fit import estimated_fit_peak_memory_bytes
from xlm.tokenizers.bpe import MINIMUM_VOCAB_SIZE, SPECIAL_TOKENS

PRODUCTION_TARGET_BYTES = 512 * 1024**2
PRODUCTION_VOCAB_SIZE = 32768
PRODUCTION_SEED = 20260919
PRODUCTION_MAX_DOCUMENT_BYTES = 1024**2
MAX_POLICY_BYTES = 256 * 1024
# Hard ceiling on sample entries held at once (pass-1 prefixes, pass-2 lookup).
MAX_SAMPLE_DOCUMENTS = 4_000_000
# Planning figure per retained entry (digest, ID, content digest, size); not measured.
SAMPLE_ENTRY_RAM_BYTES = 512
SPOOL_FRAME_BYTES = 8
RANK_TAG = "c06-tokenizer-fit-v1"
FIT_KIND = "c06_tokenizer_fit_v1"
DEFICIT_KIND = "c06_tokenizer_fit_deficit_v1"
RESOURCE_PLAN_KIND = "c06_tokenizer_fit_resource_plan_v1"
TOKENIZER_DIR = "tokenizer"
FIT_MANIFEST = "tokenizer_fit_manifest.json"
FIT_SAMPLE = "tokenizer_fit_sample.jsonl"
RESOURCE_PLAN = "tokenizer_fit_resource_plan.json"
COMPOSITE_SPLITS = {
    "ifm_behaviors_general_planning": "ifm_requirement_split",
    "common_pile_prose": "common_pile_component_split",
}
# Fixed non-corpus strings for the post-fit reversibility check.
PROBES = (
    "Hello, world! 123",
    "François naïve café — über",
    "def f(x: int) -> int:\n\treturn x + 42\n",
    "emoji \U0001f680 and literal <eos> <bos> <pad> <unk>",
)
SHORTFALL_RULE = (
    "refuse; no redistribution, renormalization, target reduction, repetition or cap removal"
)


class FitDeficit(C05Error):
    """At least one positive-budget allocation lacks eligible fit bytes."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("tokenizer-fit allocation deficit; operator decision required")
        self.report = report


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class TokenizerSpec(_Strict):
    type: Literal["byte_level_bpe"]
    target_vocab_size: int = Field(ge=MINIMUM_VOCAB_SIZE)
    special_tokens: list[str]


class FitRules(_Strict):
    eligibility: Literal["c05_kept_train_exact_content"]
    order: Literal["sha256_tag_seed_allocation_docid_content_then_docid"]
    crossing: Literal["include_whole_document"]
    oversized: Literal["skip_for_fit_only"]
    shortfall: Literal["refuse_without_redistribution"]
    rounding: Literal["largest_remainder_then_key"]


class InternalSplits(_Strict):
    source: Literal["frozen_requirements"]
    quotas_path: str = Field(min_length=1)
    quotas_sha256: Sha
    composite: dict[str, str]

    @model_validator(mode="after")
    def exact_composites(self) -> InternalSplits:
        if self.composite != COMPOSITE_SPLITS:
            raise ValueError(
                "internal split identities must name exactly the frozen IFM/Common Pile splits"
            )
        return self


class FitPolicy(_Strict):
    """Frozen tokenizer-fit policy. ``development`` exists only for authored fixtures."""

    schema_version: Literal[1]
    kind: Literal["c06_tokenizer_fit_policy"]
    policy_id: str = Field(min_length=1, max_length=128)
    mode: Literal["production", "development"]
    operator: str = Field(min_length=1)
    decided: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    tokenizer: TokenizerSpec
    seed: int = Field(ge=0)
    target_sample_bytes: int = Field(ge=1)
    max_document_bytes: int = Field(ge=1)
    rules: FitRules
    component_weights: dict[str, int]
    internal_splits: InternalSplits
    rationale: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def frozen_contract(self) -> FitPolicy:
        if set(self.component_weights) != COMPONENTS:
            raise ValueError("fit policy must weight exactly the eleven Mix-01 components")
        if any(weight <= 0 for weight in self.component_weights.values()):
            raise ValueError("fit policy component weights must be positive integers")
        if self.tokenizer.special_tokens != SPECIAL_TOKENS:
            raise ValueError("fit policy special tokens differ from the C06 tokenizer contract")
        if self.mode == "production" and (
            self.target_sample_bytes,
            self.tokenizer.target_vocab_size,
            self.seed,
            self.max_document_bytes,
        ) != (
            PRODUCTION_TARGET_BYTES,
            PRODUCTION_VOCAB_SIZE,
            PRODUCTION_SEED,
            PRODUCTION_MAX_DOCUMENT_BYTES,
        ):
            raise ValueError("production fit policy constants differ from the frozen C06 policy")
        return self

    def identity(self) -> str:
        return canonical.digest(self.model_dump(mode="json"))


def load_fit_policy(path: Path) -> tuple[FitPolicy, str]:
    """Parse the data-only policy (duplicate keys refused); return it and its file SHA-256."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_POLICY_BYTES + 1)
    if len(raw) > MAX_POLICY_BYTES:
        raise C05Error("fit policy exceeds its size ceiling")
    return FitPolicy.model_validate(load_yaml_str(raw.decode("utf-8"))), hashlib.sha256(
        raw
    ).hexdigest()


# -- budgets ------------------------------------------------------------------------


def apportion(total: int, weights: Mapping[str, int]) -> dict[str, int]:
    """Exact integer largest-remainder split of ``total``; ties by key ascending."""
    if total < 0 or not weights or any(type(w) is not int or w <= 0 for w in weights.values()):
        raise C05Error("apportionment needs a nonnegative total and positive integer weights")
    denominator = sum(weights.values())
    parts = {key: total * weight // denominator for key, weight in weights.items()}
    order = sorted(weights, key=lambda key: (-(total * weights[key] % denominator), key))
    for key in order[: total - sum(parts.values())]:
        parts[key] += 1
    return parts


@dataclass(frozen=True, slots=True)
class Budget:
    component: str
    weight: int
    quota: int
    requested: int
    declared_share: Fraction


def fit_budgets(policy: FitPolicy, allocations: Mapping[str, int]) -> dict[str, Budget]:
    """Equal-weight component budgets, divided internally by the frozen allocation quotas.

    Quotas only divide a composite component's own budget; they never change the
    top-level component budgets.
    """
    members: dict[str, dict[str, int]] = {}
    for key, quota in allocations.items():
        members.setdefault(canonical.loads_strict(key)[0], {})[key] = quota
    if set(members) != set(policy.component_weights):
        raise C05Error("frozen allocations do not cover exactly the fit policy components")
    for component, parts in members.items():
        if len(parts) > 1 and component not in COMPOSITE_SPLITS:
            raise C05Error("component has several allocations without a frozen internal split")
    weights = policy.component_weights
    component_budgets = apportion(policy.target_sample_bytes, weights)
    weight_total = sum(weights.values())
    result: dict[str, Budget] = {}
    for component in sorted(members):
        parts = members[component]
        quota_total = sum(parts.values())
        for key, requested in apportion(component_budgets[component], parts).items():
            result[key] = Budget(
                component=component,
                weight=weights[component],
                quota=parts[key],
                requested=requested,
                declared_share=Fraction(weights[component], weight_total)
                * Fraction(parts[key], quota_total),
            )
    if sum(b.requested for b in result.values()) != policy.target_sample_bytes:
        raise C05Error("fit budgets do not reproduce the frozen target")
    return dict(sorted(result.items()))


def _ratio(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


# -- resource plan --------------------------------------------------------------------


def resource_plan(
    policy: FitPolicy,
    plan: ExecutionPlan,
    requirements: Mapping[str, Any],
    budgets: Mapping[str, Budget],
) -> dict[str, Any]:
    """Deterministic, content-free plan; the run refuses unless its digest was accepted."""
    allocations = len(budgets)
    file_bytes = sum(f.file_bytes for f in plan.files)
    overshoot = allocations * policy.max_document_bytes
    sample_bound = policy.target_sample_bytes + overshoot
    body: dict[str, Any] = {
        "kind": RESOURCE_PLAN_KIND,
        "policy_digest": policy.identity(),
        "plan_digest": plan.identity(),
        "input_manifest_digest": plan.input_manifest_digest,
        "requirements_digest": canonical.digest(dict(requirements)),
        "inputs": {
            "files": len(plan.files),
            "documents": sum(f.documents for f in plan.files),
            "file_bytes": file_bytes,
            "canonical_bytes": sum(f.canonical_bytes for f in plan.files),
            "sequential_passes": 2,
            "bytes_read_bound": 2 * file_bytes,
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
            "bpe_peak_estimate_bytes": estimated_fit_peak_memory_bytes(
                sample_bound, policy.tokenizer.target_vocab_size
            ),
            "sample_index_bound_bytes": MAX_SAMPLE_DOCUMENTS * SAMPLE_ENTRY_RAM_BYTES,
            "corpus_records_in_memory": False,
        },
        "scratch": {
            "bpe_spool_bound_bytes": sample_bound + SPOOL_FRAME_BYTES * MAX_SAMPLE_DOCUMENTS,
            "membership_lookup_ceiling_bytes": plan.resources.index_bytes,
            "membership_lookup_location": "proof specification scratch",
        },
        "output": {"sample_file_ceiling_bytes": plan.resources.output_bytes},
        "network_required": False,
        "gpu_required": False,
        "basis": (
            "Planning bounds from the frozen fit policy, the C05 plan inventory and fixed "
            "ceilings. Memory figures are estimates (the BPE figure reuses the P11 planning "
            "formula), not measurements; measured values are appended after the fit."
        ),
    }
    body["digest"] = canonical.self_digest(body)
    return body


def _requirements(
    policy: FitPolicy, manifest: Mapping[str, Any], quotas: Path, ifm: Path
) -> dict[str, Any]:
    requirements = frozen_requirements(manifest, quotas, ifm)
    if requirements["quota_sha256"] != policy.internal_splits.quotas_sha256:
        raise C05Error("quota table differs from the fit policy's frozen internal split source")
    if requirements["tokenizer_vocab_size"] != policy.tokenizer.target_vocab_size:
        raise C05Error("fit policy vocabulary differs from the frozen quota table")
    return requirements


def plan_from_proof(
    proof: Path, policy: FitPolicy, quotas: Path, ifm_split: Path
) -> dict[str, Any]:
    """Metadata-only plan (no corpus, no completion import). Call ``guard_proof`` first."""
    from xlm.data.exclusion.transport import ProofSpec

    spec = ProofSpec.model_validate(read_metadata(proof, digested=False))
    plan = ExecutionPlan.model_validate(read_metadata(Path(spec.plan), digested=False))
    if plan.identity() != spec.plan_digest:
        raise C05Error("C05 proof plan changed")
    manifest = read_metadata(Path(spec.manifest))
    if manifest["digest"] != plan.input_manifest_digest:
        raise C05Error("current corpus manifest differs from C05")
    requirements = _requirements(policy, manifest, quotas, ifm_split)
    return resource_plan(
        policy, plan, requirements, fit_budgets(policy, requirements["allocations"])
    )


# -- progress -------------------------------------------------------------------------


class _Reporter:
    """Thread-safe content-free progress with RSS telemetry and a peak record."""

    def __init__(self, progress: RunProgress | NullProgress, ram_limit: int) -> None:
        self.progress = progress
        self.lock = threading.Lock()
        self.process = psutil.Process()
        self.ram_limit = ram_limit
        self.peak = 0
        progress.attach(self.telemetry)

    def telemetry(self) -> dict[str, int]:
        info = self.process.memory_info()
        # Windows reports a true peak working set; elsewhere use the sampled maximum.
        self.peak = max(self.peak, int(info.rss), int(getattr(info, "peak_wset", 0)))
        return {"rss": int(info.rss), "peak_rss": self.peak, "ram_limit": self.ram_limit}

    def stage(self, name: str, total: int | None = None, unit: str = "docs") -> None:
        with self.lock:
            self.telemetry()  # Peak RSS is recorded even with progress disabled.
            self.progress.stage(name, total, unit)

    def update(self, done: int | None = None, *, force: bool = False, **fields: Any) -> None:
        with self.lock:
            self.progress.update(done, force=force, **fields)

    def note(self, text: str) -> None:
        """Fixed explanatory line; callers pass literals only, never data."""
        if isinstance(self.progress, RunProgress):
            with self.lock:
                print(f"[C06] {text}", file=self.progress.stream or sys.stderr, flush=True)

    def complete(self) -> None:
        with self.lock:
            self.progress.complete()


class _Heartbeat:
    """Periodic elapsed/RSS line while the backend gives no progress callback."""

    def __init__(self, reporter: _Reporter, seconds: float) -> None:
        self.reporter, self.seconds = reporter, seconds
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="c06-fit-heartbeat", daemon=True)

    def _run(self) -> None:
        while not self.stopped.wait(self.seconds):
            self.reporter.update(force=True)

    def __enter__(self) -> _Heartbeat:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stopped.set()
        self.thread.join()


# -- pass 1: bounded rank-ordered prefixes ----------------------------------------------


@dataclass(slots=True)
class _Entry:
    rank: bytes
    doc_id: str
    content: str
    size: int

    def key(self) -> tuple[bytes, str]:
        return self.rank, self.doc_id

    def __lt__(self, other: _Entry) -> bool:
        # heapq is a min-heap: invert so the latest-ranked retained entry is on top.
        return self.key() > other.key()


@dataclass(slots=True)
class _Allocation:
    requested: int
    heap: list[_Entry] = field(default_factory=list)
    held: int = 0
    kept: int = 0
    non_kept: int = 0
    kept_non_train: int = 0
    kept_train: int = 0
    kept_train_bytes: int = 0
    eligible_documents: int = 0
    eligible_bytes: int = 0
    oversized: int = 0
    oversized_bytes: int = 0

    def offer(self, entry: _Entry) -> int:
        """Keep exactly the shortest rank-ordered prefix reaching the budget.

        Any seen entry ranked after the current crossing entry can never re-enter
        the prefix, so it is dropped. Returns the change in retained entries.
        """
        if self.held >= self.requested and self.heap and entry.key() > self.heap[0].key():
            return 0
        heapq.heappush(self.heap, entry)
        self.held += entry.size
        change = 1
        while self.heap and self.held - self.heap[0].size >= self.requested:
            self.held -= heapq.heappop(self.heap).size
            change -= 1
        return change


def is_oversized(policy: FitPolicy, size: int) -> bool:
    """Fit-only eligibility cap: the record itself is never modified or truncated."""
    return size > policy.max_document_bytes


def _rank(seed: int, allocation: str, doc_id: str, content: str) -> bytes:
    return hashlib.sha256(
        canonical.canonical_bytes([RANK_TAG, seed, allocation, doc_id, content])
    ).digest()


def index_sample(
    gate: MembershipGate,
    policy: FitPolicy,
    budgets: Mapping[str, Budget],
    reporter: _Reporter,
) -> dict[str, _Allocation]:
    """Pass 1: rank every eligible kept-train record; retain only per-allocation prefixes."""
    plan = gate.plan
    states = {key: _Allocation(budget.requested) for key, budget in budgets.items()}
    total_documents = sum(f.documents for f in plan.files)
    total_bytes = sum(f.canonical_bytes for f in plan.files)
    reporter.stage("SAMPLE INDEX", total_documents, "docs")
    started = time.monotonic()
    done = done_bytes = retained = eligible = oversized = 0
    for item, doc in iter_plan_documents(gate):
        key = allocation_key(item.component, item.view, item.upstream_component)
        state = states.get(key)
        if state is None:
            raise C05Error("record outside the frozen fit allocations")
        done += 1
        size = doc.utf8_byte_count
        done_bytes += size
        if gate.lookup(doc.doc_id) is None:
            state.non_kept += 1  # Excluded or duplicate: never a fit candidate.
        else:
            content = canonical.digest(doc.to_dict())
            state.kept += 1
            if not _kept_train(gate, doc.doc_id, content, key):
                state.kept_non_train += 1  # diagnostic_val / audit.
            elif doc.split != "train":
                raise C05Error("record split differs from C05 membership")
            else:
                state.kept_train += 1
                state.kept_train_bytes += size
                if is_oversized(policy, size):
                    state.oversized += 1
                    state.oversized_bytes += size
                    oversized += 1
                else:
                    state.eligible_documents += 1
                    state.eligible_bytes += size
                    eligible += 1
                    retained += state.offer(
                        _Entry(
                            _rank(policy.seed, key, doc.doc_id, content), doc.doc_id, content, size
                        )
                    )
                    if retained > MAX_SAMPLE_DOCUMENTS:
                        raise C05Error("tokenizer-fit retained sample document ceiling")
        if done % 1024 == 0:
            elapsed = max(time.monotonic() - started, 1e-9)
            reporter.update(
                done,
                bytes_done=done_bytes,
                bytes_total=total_bytes,
                eligible=eligible,
                oversized=oversized,
                retained=retained,
                mib_per_s=done_bytes / 2**20 / elapsed,
            )
    reporter.update(
        done,
        force=True,
        bytes_done=done_bytes,
        bytes_total=total_bytes,
        eligible=eligible,
        oversized=oversized,
        retained=retained,
    )
    _check_accounting(gate, states, done)
    return states


def _check_accounting(
    gate: MembershipGate, states: Mapping[str, _Allocation], documents: int
) -> None:
    """Every record was C05-screened: counts must equal the signed completion exactly."""
    expected: Mapping[str, Mapping[str, int]] = gate.completion["allocations"]
    if not set(expected) <= set(states) or documents != gate.completion["documents"]:
        raise C05Error("tokenizer-fit scan disagrees with C05 completion accounting")
    for key, state in states.items():
        counts = expected.get(key, {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0})
        if (state.kept, state.non_kept, state.kept_train_bytes) != (
            counts["kept"],
            counts["excluded"] + counts["duplicate"],
            counts["train_bytes"],
        ):
            raise C05Error("tokenizer-fit scan disagrees with C05 completion accounting")
    kept_train = gate.db.execute("SELECT COUNT(*) FROM membership WHERE split='train'").fetchone()
    if sum(s.kept_train for s in states.values()) != kept_train[0]:
        raise C05Error("tokenizer-fit scan did not cover every kept training record")


def _allocation_report(budget: Budget, state: _Allocation) -> dict[str, Any]:
    return {
        "component": budget.component,
        "requested_bytes": budget.requested,
        "available_eligible_bytes": state.eligible_bytes,
        "deficit_bytes": max(0, budget.requested - state.eligible_bytes),
        "eligible_documents": state.eligible_documents,
        "oversized_candidates_skipped": state.oversized,
        "oversized_candidate_bytes": state.oversized_bytes,
        "status": "DEFICIT" if state.eligible_bytes < budget.requested else "SUFFICIENT",
    }


def deficit_report(
    gate: MembershipGate,
    policy: FitPolicy,
    requirements: Mapping[str, Any],
    budgets: Mapping[str, Budget],
    states: Mapping[str, _Allocation],
) -> dict[str, Any] | None:
    rows = {key: _allocation_report(budgets[key], states[key]) for key in budgets}
    if all(row["status"] == "SUFFICIENT" for row in rows.values()):
        return None
    return {
        "kind": DEFICIT_KIND,
        **binding_of(gate),
        "policy_digest": policy.identity(),
        "quota_sha256": requirements["quota_sha256"],
        "ifm_split_digest": requirements["ifm_split_digest"],
        "common_pile_split_digest": requirements["common_pile_split_digest"],
        "allocations": rows,
        "rule": SHORTFALL_RULE,
    }


# -- sample ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Chosen:
    allocation: str
    content: str
    size: int
    rank: bytes


def select_sample(
    states: Mapping[str, _Allocation], budgets: Mapping[str, Budget], reporter: _Reporter
) -> tuple[dict[str, _Chosen], dict[str, dict[str, Any]]]:
    reporter.stage("SAMPLE SELECT", len(states), "allocations")
    chosen: dict[str, _Chosen] = {}
    rows: dict[str, dict[str, Any]] = {}
    selected_documents = selected_bytes = 0
    for number, key in enumerate(sorted(states), start=1):
        state, budget = states[key], budgets[key]
        for entry in sorted(state.heap, key=_Entry.key):
            if entry.doc_id in chosen:
                raise C05Error("document repeats across fit allocations")
            chosen[entry.doc_id] = _Chosen(key, entry.content, entry.size, entry.rank)
        rows[key] = {
            **_allocation_report(budget, state),
            "quota": budget.quota,
            "declared_share": _ratio(budget.declared_share),
            "selected_documents": len(state.heap),
            "selected_bytes": state.held,
            "overshoot_bytes": state.held - budget.requested,
        }
        selected_documents += len(state.heap)
        selected_bytes += state.held
        state.heap = []
        reporter.update(number, selected=selected_documents, selected_bytes=selected_bytes)
    return chosen, rows


def export_sample(chosen: Mapping[str, _Chosen], path: Path, ceiling: int) -> tuple[str, int]:
    """Content-free selected membership sorted by doc_id (IDs and C05 content digests)."""
    value = hashlib.sha256()
    written = 0
    with path.open("xb") as stream:
        for doc_id in sorted(chosen):
            entry = chosen[doc_id]
            raw = (
                canonical.canonical_bytes(
                    {
                        "doc_id": doc_id,
                        "content": entry.content,
                        "allocation": canonical.loads_strict(entry.allocation),
                        "bytes": entry.size,
                        "rank": entry.rank.hex(),
                    }
                )
                + b"\n"
            )
            written += len(raw)
            if written > ceiling:
                raise C05Error("tokenizer-fit sample artifact ceiling")
            stream.write(raw)
            value.update(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return value.hexdigest(), written


@dataclass(slots=True)
class _Feed:
    """Pass-2 accounting: what was handed to the BPE trainer, in feed order."""

    documents: int = 0
    bytes: int = 0
    digest: Any = field(default_factory=hashlib.sha256)

    def add(self, doc: CanonicalDocument) -> None:
        if self.documents:
            self.digest.update(b"\n")
        # Exactly the framing of bpe._fit_text_stream's training_input_hash.
        self.digest.update(f"{doc.source_id}:{doc.doc_id}:{doc.clean_hash}".encode())
        self.documents += 1
        self.bytes += doc.utf8_byte_count


def materialize(
    gate: MembershipGate,
    chosen: Mapping[str, _Chosen],
    feed: _Feed,
    reporter: _Reporter,
    on_complete: Callable[[], None],
) -> Iterator[CanonicalDocument]:
    """Pass 2: re-read the hash-verified inputs; yield each selected record exactly once."""
    plan = gate.plan
    total = sum(f.documents for f in plan.files)
    reporter.stage("SAMPLE VERIFY", total, "docs")
    seen: set[str] = set()
    done = 0
    for item, doc in iter_plan_documents(gate):
        done += 1
        entry = chosen.get(doc.doc_id)
        if entry is not None:
            key = allocation_key(item.component, item.view, item.upstream_component)
            if (
                key != entry.allocation
                or doc.utf8_byte_count != entry.size
                or canonical.digest(doc.to_dict()) != entry.content
            ):
                raise C05Error("selected fit record differs from its pass-1 C05 binding")
            if doc.doc_id in seen:
                raise C05Error("selected fit record repeats in the C05 inputs")
            seen.add(doc.doc_id)
            feed.add(doc)
            yield doc
        if done % 1024 == 0:
            reporter.update(done, selected_verified=len(seen), selected_total=len(chosen))
    if len(seen) != len(chosen):
        raise C05Error("selected fit record missing from the second pass")
    reporter.update(done, force=True, selected_verified=len(seen), selected_total=len(chosen))
    on_complete()


# -- fit ------------------------------------------------------------------------------


def _verify_tokenizer(
    directory: Path,
    gate: MembershipGate,
    policy: FitPolicy,
    production: bool,
    training_input_hash: str,
) -> dict[str, Any]:
    from xlm.data.normalization import canonical_normalize

    tokenizer, identity = tokenizer_identity(directory, gate)
    manifest = read_metadata(directory / "tokenizer_manifest.json", digested=False)
    if not identity["c05_fit_binding"]:
        raise C05Error("fitted tokenizer lacks its C05 binding")
    if manifest.get("training_input_hash") != training_input_hash:
        raise C05Error("tokenizer training input differs from the complete frozen sample")
    if identity["vocab_size"] != policy.tokenizer.target_vocab_size:
        raise C05Error("tokenizer target vocabulary differs from the fit policy")
    if production and (
        not tokenizer.is_production_baseline
        or tokenizer.actual_vocab_size != policy.tokenizer.target_vocab_size
    ):
        raise C05Error("production tokenizer failed baseline certification")
    for text in PROBES:
        ids = tokenizer.encode(text)
        if set(ids) & set(tokenizer.special_token_ids) or tokenizer.decode(
            ids
        ) != canonical_normalize(text):
            raise C05Error("fitted tokenizer failed the reversibility probe")
    return {
        **identity,
        "directory": TOKENIZER_DIR,
        "actual_vocab_size": tokenizer.actual_vocab_size,
        "is_production_baseline": tokenizer.is_production_baseline,
        "training_input_hash": training_input_hash,
    }


def fit_tokenizer(
    gate: MembershipGate,
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
    progress: RunProgress | NullProgress | None = None,
    heartbeat_seconds: float = 30.0,
) -> dict[str, Any]:
    """Fit the frozen-policy tokenizer through the C05 gate and publish it atomically.

    Raises :class:`FitDeficit` (content-free report attached) before any text is
    spooled when an allocation cannot reach its budget. On any failure or interrupt
    the staging directory and the job scratch are removed; no complete output exists.
    """
    if gate.mode == "protected" and policy.mode != "production":
        raise C05Error("protected C05 membership requires the production fit policy")
    if output.exists():
        raise C05Error("tokenizer-fit output is write-once")
    production = policy.mode == "production" and gate.mode == "protected"
    requirements = _requirements(policy, gate.input_manifest, quotas, ifm_split)
    budgets = fit_budgets(policy, requirements["allocations"])
    planned = resource_plan(policy, gate.plan, requirements, budgets)
    # Provenance of the code that produced the fit (recorded, not a verification gate).
    implementation = implementation_identity()
    if planned["digest"] != accepted_plan_digest:
        raise C05Error("tokenizer-fit resource plan differs from the accepted plan")
    scratch.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(scratch).free < planned["scratch"]["bpe_spool_bound_bytes"]:
        raise C05Error("scratch volume cannot hold the planned BPE spool bound")
    reporter = _Reporter(progress or NullProgress(), gate.plan.resources.ram_bytes)
    token = uuid.uuid4().hex
    work = scratch / f"c06-fit-{token}"
    stage = output.with_name(f"{output.name}.partial-{token}")
    timings: dict[str, int] = {}
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.monotonic()
        timings[name] = int((now - clock) * 1000)
        clock = now

    published = False
    try:
        work.mkdir()
        stage.mkdir()
        states = index_sample(gate, policy, budgets, reporter)
        lap("sample_index_ms")
        report = deficit_report(gate, policy, requirements, budgets, states)
        if report is not None:
            raise FitDeficit(report)
        chosen, allocations = select_sample(states, budgets, reporter)
        del states
        sample_sha, sample_bytes = export_sample(
            chosen, stage / FIT_SAMPLE, gate.plan.resources.output_bytes
        )
        selected_bytes = sum(entry.size for entry in chosen.values())
        lap("sample_select_ms")

        feed = _Feed()

        def fitting() -> None:
            reporter.stage("TOKENIZER FIT", len(chosen), "docs")
            reporter.note(
                "TOKENIZER FIT | feeding the frozen sample; after the feed, internal BPE "
                "merge progress is not observable and only elapsed/RSS heartbeats are shown"
            )

        def fed(documents: int, size: int) -> None:
            if documents % 1024 == 0 or documents == len(chosen):
                reporter.update(documents, bytes_done=size, bytes_total=selected_bytes)
            if documents == len(chosen):
                reporter.stage("TOKENIZER FIT: MERGES", None, "steps")

        from xlm.tokenizers.bpe import ByteLevelBPETokenizer

        with _Heartbeat(reporter, heartbeat_seconds):
            tokenizer = ByteLevelBPETokenizer.train_from_documents(
                materialize(gate, chosen, feed, reporter, fitting),
                target_vocab_size=policy.tokenizer.target_vocab_size,
                # The frozen sample's exact totals, never the historical defaults.
                max_train_docs=len(chosen),
                max_train_bytes=selected_bytes,
                is_production_baseline=production,
                c05_gate=gate,
                require_complete=True,
                spool_dir=work,
                on_feed=fed,
            )
        if (feed.documents, feed.bytes) != (len(chosen), selected_bytes):
            raise C05Error("BPE did not consume the complete frozen sample")
        training_input_hash = feed.digest.hexdigest()
        lap("fit_ms")
        reporter.stage("TOKENIZER SAVE", None, "files")
        directory = stage / TOKENIZER_DIR
        tokenizer.save(directory)
        write_once(
            directory / "c05-binding.json",
            {
                "plan_digest": gate.plan_digest,
                "completion_digest": gate.receipt_digest,
                "tokenizer_fingerprint": tokenizer.fingerprint,
            },
        )
        del tokenizer
        reporter.stage("VERIFY", None, "checks")
        identity = _verify_tokenizer(directory, gate, policy, production, training_input_hash)
        if file_sha(stage / FIT_SAMPLE) != sample_sha:
            raise C05Error("tokenizer-fit sample artifact changed")
        components: dict[str, dict[str, Any]] = {}
        for row in allocations.values():
            total = components.setdefault(
                row["component"],
                {
                    "weight": policy.component_weights[row["component"]],
                    "declared_share": _ratio(
                        Fraction(
                            policy.component_weights[row["component"]],
                            sum(policy.component_weights.values()),
                        )
                    ),
                },
            )
            for name in (
                "requested_bytes",
                "selected_bytes",
                "overshoot_bytes",
                "selected_documents",
                "eligible_documents",
                "available_eligible_bytes",
                "oversized_candidates_skipped",
                "oversized_candidate_bytes",
            ):
                total[name] = total.get(name, 0) + row[name]
        for rows in (allocations, components):
            for row in rows.values():
                row["achieved_share"] = _ratio(Fraction(row["selected_bytes"], selected_bytes))
        body = {
            "kind": FIT_KIND,
            **binding_of(gate),
            "production": production,
            "policy": policy.model_dump(mode="json"),
            "policy_digest": policy.identity(),
            "policy_file_sha256": policy_sha256,
            "requirements": {
                "quota_sha256": requirements["quota_sha256"],
                "ifm_split_digest": requirements["ifm_split_digest"],
                "common_pile_split_digest": requirements["common_pile_split_digest"],
                "requirements_digest": canonical.digest(requirements),
            },
            "components": dict(sorted(components.items())),
            "allocations": allocations,
            "totals": {
                "requested_bytes": policy.target_sample_bytes,
                "selected_bytes": selected_bytes,
                "overshoot_bytes": selected_bytes - policy.target_sample_bytes,
                "selected_documents": len(chosen),
                "oversized_candidates_skipped": sum(
                    r["oversized_candidates_skipped"] for r in allocations.values()
                ),
                "oversized_candidate_bytes": sum(
                    r["oversized_candidate_bytes"] for r in allocations.values()
                ),
            },
            "sample": {
                "file": FIT_SAMPLE,
                "selected_membership_sha256": sample_sha,
                "selected_membership_bytes": sample_bytes,
                "documents": len(chosen),
                "canonical_bytes": selected_bytes,
                "training_input_hash": training_input_hash,
            },
            "bpe_bounds": {
                "max_train_docs": len(chosen),
                "max_train_bytes": selected_bytes,
                "require_complete": True,
                "fed_documents": feed.documents,
                "fed_canonical_bytes": feed.bytes,
            },
            "tokenizer": identity,
            "resource_plan_digest": planned["digest"],
            "implementation": implementation,
        }
        envelope = signed(body, issuer, key)
        write_once(stage / FIT_MANIFEST, envelope)
        lap("save_verify_ms")
        reporter.telemetry()
        write_once(
            stage / RESOURCE_PLAN,
            {
                "plan": planned,
                "measured": {
                    **timings,
                    "peak_rss_bytes": reporter.peak,
                    "bpe_spool_bytes": selected_bytes + SPOOL_FRAME_BYTES * len(chosen),
                    "basis": "measured in this run; not part of the signed manifest",
                },
            },
        )
        if output.exists():
            raise C05Error("tokenizer-fit output is write-once")
        os.rename(stage, output)
        published = True
        reporter.complete()
        return envelope
    finally:
        if not published:
            shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def verify_fit(
    gate: MembershipGate, directory: Path, policy: FitPolicy, quotas: Path, ifm_split: Path
) -> dict[str, Any]:
    """Re-verify a published fit against the current proof, policy and frozen splits."""
    envelope = read_metadata(directory / FIT_MANIFEST, digested=False)
    body = verify_signed(envelope, gate.trusted)
    check_binding(body, gate, FIT_KIND)
    if body.get("policy_digest") != policy.identity() or body.get("policy") != policy.model_dump(
        mode="json"
    ):
        raise C05Error("tokenizer fit used a different fit policy")
    if body.get("production") != (policy.mode == "production" and gate.mode == "protected"):
        raise C05Error("tokenizer fit production flag mismatch")
    requirements = _requirements(policy, gate.input_manifest, quotas, ifm_split)
    expected_requirements = {
        "quota_sha256": requirements["quota_sha256"],
        "ifm_split_digest": requirements["ifm_split_digest"],
        "common_pile_split_digest": requirements["common_pile_split_digest"],
        "requirements_digest": canonical.digest(requirements),
    }
    if body.get("requirements") != expected_requirements:
        raise C05Error("tokenizer fit used different frozen internal splits")
    budgets = fit_budgets(policy, requirements["allocations"])
    if {k: r["requested_bytes"] for k, r in body["allocations"].items()} != {
        k: b.requested for k, b in budgets.items()
    }:
        raise C05Error("tokenizer fit budgets differ from the frozen policy")
    planned = read_metadata(directory / RESOURCE_PLAN, digested=False)["plan"]
    if planned != resource_plan(policy, gate.plan, requirements, budgets) or body.get(
        "resource_plan_digest"
    ) != planned.get("digest"):
        raise C05Error("tokenizer fit resource plan changed")
    sample = body["sample"]
    path = directory / FIT_SAMPLE
    if (
        path.stat().st_size != sample["selected_membership_bytes"]
        or file_sha(path) != sample["selected_membership_sha256"]
    ):
        raise C05Error("tokenizer fit sample artifact changed")
    actual: dict[str, list[int]] = {k: [0, 0] for k in budgets}
    with path.open("rb") as stream:
        while raw := stream.readline(64 * 1024 + 1):
            if len(raw) > 64 * 1024:
                raise C05Error("tokenizer fit sample record ceiling")
            row = canonical.loads_bytes_strict(raw)
            allocation = allocation_key(*row["allocation"])
            if allocation not in actual or not _kept_train(
                gate, row["doc_id"], row["content"], allocation
            ):
                raise C05Error("tokenizer fit sample record is not kept training membership")
            if type(row["bytes"]) is not int or not 0 <= row["bytes"] <= policy.max_document_bytes:
                raise C05Error("tokenizer fit sample record outside the document cap")
            actual[allocation][0] += 1
            actual[allocation][1] += row["bytes"]
    if {
        k: [r["selected_documents"], r["selected_bytes"]] for k, r in body["allocations"].items()
    } != actual:
        raise C05Error("tokenizer fit sample totals differ from the signed manifest")
    if any(
        bytes_ < budgets[k].requested or bytes_ - budgets[k].requested >= policy.max_document_bytes
        for k, (_, bytes_) in actual.items()
    ):
        raise C05Error("tokenizer fit sample violates the budget or crossing rule")
    identity = _verify_tokenizer(
        directory / TOKENIZER_DIR,
        gate,
        policy,
        bool(body["production"]),
        sample["training_input_hash"],
    )
    if identity != body["tokenizer"]:
        raise C05Error("tokenizer files differ from the signed fit")
    return {
        "verified": True,
        "mode": gate.mode,
        "production": body["production"],
        "fit_digest": envelope["digest"],
        "tokenizer_fingerprint": identity["fingerprint"],
        "selected_documents": sample["documents"],
        "selected_canonical_bytes": sample["canonical_bytes"],
    }
