"""Authorized C05 compact parallel engine: per-file fact units and atomic publication.

Run only inside the operator's isolated environment for protected plans. Authored
plans exercise the same scan/group/publication code, but cannot issue protected proof.

Scan: the parent reads each plan file, reserves ``bytes_read`` for the whole frozen
file before its first read and ``attempted_records`` for every raw line as it is
read (before any worker sees it), hashes the exact raw bytes, and dispatches bounded
batches (rows and bytes) to ``Resources.workers`` spawned children (``1``: the same
preparation in-process). Prepared facts are integrated strictly in file/row order;
a file becomes reusable only when its SHA-256, row count and canonical byte count
verify and its immutable fact unit (signed header last, then rename) is published.
A failure before that leaves only untrusted staging. Grouping and publication are
in :mod:`xlm.data.exclusion.grouping` and :mod:`xlm.data.exclusion.publish`.

The historical single-process SQLite engine is :mod:`xlm.data.exclusion.reference`
(equivalence oracle only).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
from filelock import FileLock

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, completion_kind, signed, verify_signed
from xlm.data.exclusion.capacity import (
    COMPLETION_BYTES,
    FACTS_DIR,
    GROUP_DIR,
    REVIEW_DB,
    SEAL_BYTES,
    STATE_BYTES,
    admit_runtime,
    bounded_bytes,
    physical_reserve,
    storage_bounds,
    summary,
    tree_size,
)
from xlm.data.exclusion.compact import (
    MATCHER_DIR,
    STAGING_DIR,
    CompactExactMatcher,
    prepare,
)
from xlm.data.exclusion.factstore import (
    IndexLedger,
    Unit,
    UnitWriter,
    discard,
    load_unit,
    tree_bytes,
    unit_body,
)
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.isolation import VolumeInspector
from xlm.data.exclusion.policy import C05Error, require_engine_acceptance, scoped, trigger_of
from xlm.data.exclusion.progress import NullProgress, Progress, RollingRate
from xlm.data.exclusion.review import ReviewQueue, queue_digest
from xlm.data.exclusion.scanpool import (
    MatcherSpec,
    Pool,
    ProcessTree,
    ScanInit,
    ScanRole,
    Task,
    make_pool,
    ordered_jobs,
)
from xlm.data.exclusion.scanprep import (
    FORMAT,
    FORMAT_V3,
    RECORD,
    FileContext,
    PreparedBatch,
    review_fragment,
)
from xlm.data.exclusion.storage import OrderedConnection
from xlm.data.exclusion.streaming import Pattern, index_record

#: Scan batches are bounded by rows and raw bytes (both; whichever comes first).
BATCH_ROWS: Final = 256
BATCH_BYTES: Final = 4 * 1024 * 1024
#: Expensive filesystem accounting is sampled at most this often (seconds).
DISK_SECONDS: Final = 5.0
RAM_SECONDS: Final = 0.25
SEAL: Final = "seal.json"
EMPTY_QUEUE_DIGEST: Final = hashlib.sha256().hexdigest()


def file_sha(
    path: Path,
    check: Callable[[], None] = lambda: None,
    report: Callable[[int], None] = lambda done: None,
) -> str:
    value = hashlib.sha256()
    done = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check()
            value.update(block)
            done += len(block)
            report(done)
    return value.hexdigest()


def index_patterns(path: Path, max_record: int) -> Iterator[Pattern]:
    with path.open("rb") as stream:
        while raw := stream.readline(max_record + 1):
            yield index_record(raw, max_record)


def effective_workers(plan: ExecutionPlan) -> int:
    """``Resources.workers`` is the reviewed maximum; never above the CPU count.

    Worker count changes only throughput: every output is a function of the plan.
    """
    return max(1, min(plan.resources.workers, os.cpu_count() or 1))


class Budget:
    def __init__(
        self, plan: ExecutionPlan, work: Path, output: Path, state: dict[str, Any]
    ) -> None:
        self.plan, self.work, self.output, self.state = plan, work, output, state
        self.identity = plan.identity()
        self.review = plan.policy.review.enabled
        self.index: Path | None = None
        self.ledger: IndexLedger | None = None
        self.tree = ProcessTree()
        self.peak_rss = 0
        self.last_rss = 0
        self.peak_scratch = 0
        self.peak_journal = 0
        self.last_scratch = 0
        self.last_free = 0
        self.journal_bound = min(
            plan.resources.journal_bytes,
            storage_bounds(plan.resources, plan.storage, review=self.review, files=len(plan.files))[
                "review_rollback_journal"
            ],
        )
        self._last_disk = float("-inf")
        self._last_ram = float("-inf")
        self.benchmark_bytes = 0

    def check(self, *, disk: bool = False) -> None:
        now = time.time()
        r = self.plan.resources
        if now < self.state["started"] or now - self.state["started"] > r.overall_seconds:
            raise C05Error("overall deadline exhausted or clock moved backwards")
        if now - self.state["stage_started"] > r.stage_seconds:
            raise C05Error("stage deadline exhausted")
        mono = time.monotonic()
        if disk or mono - self._last_ram >= RAM_SECONDS:
            self._last_ram = mono
            rss, _children = self.tree.sample()
            self.last_rss = rss
            self.peak_rss = max(self.peak_rss, rss)
            if rss > r.ram_bytes:
                raise C05Error("process-tree RAM ceiling")
        if not disk and mono - self._last_disk < DISK_SECONDS:
            return
        self._last_disk = mono
        scratch = self._scratch()
        output = sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
        aggregate = scratch + output + self.benchmark_bytes
        self.last_scratch = aggregate
        self.peak_scratch = max(self.peak_scratch, aggregate)
        if aggregate > r.scratch_bytes or output > r.output_bytes:
            raise C05Error("aggregate scratch/output ceiling")
        free = [shutil.disk_usage(root).free for root in (self.work, self.output)]
        self.last_free = min(free)
        if self.last_free < r.free_bytes:
            raise C05Error("free-space reserve")
        if self.index is not None:
            # Sampled: refuses once other consumers leave too little for our growth.
            physical_reserve(
                r,
                self.plan.storage,
                self.work,
                self.output,
                self.identity,
                self.index,
                review=self.review,
                files=len(self.plan.files),
                index_used=None if self.ledger is None else self.ledger.used,
            )
        # Monitored sample of the derived-hard bound; a WAL/SHM file is never accounted.
        journal = self.work / (REVIEW_DB + "-journal")
        size = journal.stat().st_size if journal.is_file() else 0
        self.peak_journal = max(self.peak_journal, size)
        if size > self.journal_bound:
            raise C05Error("journal ceiling")
        if any(self.work.glob("*-wal")) or any(self.work.glob("*-shm")):
            raise C05Error("unaccounted SQLite WAL/shared-memory file")

    def _scratch(self) -> int:
        if self.ledger is None:
            return tree_size(self.work)
        # The ledger counts every working-index byte; walk only the small remainder.
        other = sum(
            p.stat().st_size for p in self.work.iterdir() if p.is_file() and not p.is_symlink()
        )
        return (
            self.ledger.used
            + other
            + sum(tree_size(self.work / name) for name in (MATCHER_DIR, STAGING_DIR))
        )

    def telemetry(self) -> dict[str, int | float]:
        values: dict[str, int | float] = {
            "rss": self.last_rss,
            "peak_rss": self.peak_rss,
            "ram_limit": self.plan.resources.ram_bytes,
            "scratch": self.last_scratch,
            "free": self.last_free,
        }
        if self.ledger is not None:
            values.update(index=self.ledger.used, index_limit=self.ledger.limit)
        return values


def matcher_directory(
    plan: ExecutionPlan, work: Path, inspector: VolumeInspector | None = None
) -> Path:
    """Private compiled-matcher location (it holds protected signatures).

    Always inside this plan's C05 scratch job directory; protected plans also refuse
    any overlap with the repository checkout, data root or published output, and a
    detached-volume plan requires the protected volume (never F:/G:/C: scratch).
    """
    from xlm.data.exclusion.isolation import checkout_root, os_volume, overlaps

    directory = work / MATCHER_DIR
    if not directory.resolve().is_relative_to(Path(plan.scratch_root).resolve()):
        raise C05Error("compiled matcher must stay inside the plan's C05 scratch")
    if plan.mode == "protected":
        for role, root in (
            ("repository checkout", checkout_root()),
            ("data root", Path(plan.data_root)),
            ("C05 output", Path(plan.output_root)),
        ):
            if overlaps(directory, root):
                raise C05Error(f"compiled matcher overlaps the {role}")
    if plan.isolation is not None:
        protected = plan.isolation.protected_root
        if overlaps(directory, protected.path):
            raise C05Error("compiled matcher overlaps the protected benchmark root")
        if (inspector or os_volume)(directory) != protected.volume:
            raise C05Error("compiled matcher must stay on the protected volume")
    return directory


def facts_format(plan: ExecutionPlan) -> str:
    """Fact-unit/group-seal format of a plan: c05-facts-v3 for c05-production-v3."""
    return FORMAT_V3 if scoped(plan.policy) else FORMAT


def check_trigger_coverage(plan: ExecutionPlan, receipt: Mapping[str, Any], matcher: Any) -> None:
    """c05-production-v3: the compiled trigger set must leave exactly the reviewed
    number of benchmark items without any active pattern (content-free counts)."""
    trigger = trigger_of(plan.policy)
    section = matcher.manifest.get("trigger")
    if trigger is None:
        if section is not None:
            raise C05Error("unfiltered plan reuses a trigger-filtered compiled matcher")
        return
    if not isinstance(section, dict) or section.get("policy_digest") != trigger.identity():
        raise C05Error("compiled matcher trigger binding differs from the plan")
    if section.get("index_records") != receipt["patterns"]:
        raise C05Error("compiled matcher did not read every protected index record")
    inactive = int(receipt["items"]) - int(section["active_items"])
    if inactive != trigger.reviewed_items_without_active_trigger:
        raise C05Error(
            "benchmark items without an active trigger differ from the reviewed count: "
            f"{inactive} != {trigger.reviewed_items_without_active_trigger}"
        )


def isolation_check(
    plan: ExecutionPlan,
    receipt: Mapping[str, Any],
    index: Path,
    inspector: VolumeInspector | None = None,
) -> None:
    """Versioned isolation contract of the verified benchmark receipt, live.

    Protected separate_principal_v1 needs a distinct denied agent identity;
    detached_volume_v1 permits the same principal only with the plan-bound protected
    root mounted, its index read in place and every plan root still separated.
    """
    from pydantic import TypeAdapter

    from xlm.data.exclusion.isolation import (
        AnyIsolation,
        DetachedVolumeIsolation,
        Isolation,
        require_operator,
        verify_protected_root,
        verify_separation,
    )

    raw = receipt["isolation"]
    detached = isinstance(raw, Mapping) and "mechanism" in raw
    if plan.mode != "protected" and not detached and plan.isolation is None:
        return  # Historical authored fixtures carry a minimal isolation record.
    isolation: Isolation | DetachedVolumeIsolation = TypeAdapter(AnyIsolation).validate_python(raw)
    if isinstance(isolation, DetachedVolumeIsolation):
        if plan.isolation is None or plan.isolation.protected_root != isolation.protected_root:
            raise C05Error("plan does not bind the receipt's protected benchmark root")
        if index.resolve() != plan.isolation.index_path().resolve():
            raise C05Error("C05 must read the plan-bound index on the protected volume")
        protected = verify_protected_root(isolation.protected_root, inspector)
        verify_separation(
            protected,
            (
                ("data_root", plan.data_root),
                ("c05_scratch", plan.scratch_root),
                ("c05_output", plan.output_root),
            ),
            inspector,
        )
    elif plan.isolation is not None:
        raise C05Error("plan binds a detached volume but the receipt does not")
    if plan.mode == "protected":
        require_operator(isolation)


def run(
    plan: ExecutionPlan,
    authorization: Mapping[str, Any],
    *,
    index: Path,
    benchmark: Mapping[str, Any],
    trusted: Mapping[str, bytes],
    issuer: str,
    key: bytes,
    current_code: str,
    current_dependencies: str,
    checkpoint: Callable[[str], None] = lambda event: None,
    inspector: VolumeInspector | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Resume a plan without resetting spent time; reusable units are re-verified.

    ``checkpoint`` is a fault-injection seam for authored tests, never an input from
    plan JSON. It may raise at file rows/commits, grouping or pre-publication.
    ``progress`` is operational display only (stderr); it never enters artifacts.
    """
    reporter: Progress = progress or NullProgress()
    reporter.stage("PREFLIGHT", None, "checks")
    identity = plan.identity()
    require_engine_acceptance(plan.mode)
    auth = verify_signed(authorization, trusted)
    if (auth.get("kind"), auth.get("plan_digest"), auth.get("mode")) != (
        "c05_authorization_v2",
        identity,
        plan.mode,
    ):
        raise C05Error("authorization does not match plan")
    if current_code != plan.code_identity or current_dependencies != plan.dependency_sha256:
        raise C05Error("runner code/dependency identity changed")
    receipt = verify_signed(benchmark, trusted)
    if (
        benchmark["digest"] != plan.benchmark_receipt_digest
        or receipt["index_sha256"] != plan.index_sha256
    ):
        raise C05Error("benchmark receipt/index identity changed")
    if receipt["isolation"]["mode"] != plan.mode:
        raise C05Error("benchmark protection mode mismatch")
    isolation_check(plan, receipt, index, inspector)
    if plan.mode == "protected":
        from xlm.data.exclusion.identity import implementation_identity

        actual = implementation_identity()
        if (plan.code_commit, plan.code_identity, plan.dependency_sha256) != (
            actual["code_commit"],
            actual["code_identity"],
            actual["dependency_sha256"],
        ):
            raise C05Error("protected runtime code/dependencies differ")
    work = Path(plan.scratch_root) / identity
    output = Path(plan.output_root)
    from xlm.artifacts.manifest import ensure_plain_path

    for path in (work, output, work / FACTS_DIR, work / GROUP_DIR, work / "state.json"):
        ensure_plain_path(path)
    # Fail closed before creating any job file when a volume cannot hold the
    # remaining worst-case growth; repeated under the lock before work starts.
    review = plan.policy.review.enabled
    admit_runtime(
        plan.resources,
        plan.storage,
        work,
        output,
        identity,
        index,
        review=review,
        files=len(plan.files),
    )
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work / "run.lock"), timeout=0):
        engine = _Engine(
            plan,
            identity,
            work,
            output,
            index,
            receipt,
            trusted,
            issuer,
            key,
            checkpoint,
            matcher_directory(plan, work, inspector),
            reporter,
        )
        return engine.execute()


@dataclass
class _Marker:
    """An ordered non-batch scan event: end of one file or a deferred refusal."""

    sequence: int
    ordinal: int
    kind: str  # "end" | "error"
    sha: str = ""
    rows: int = 0
    reason: str = ""


@dataclass
class _Open:
    ordinal: int
    writer: UnitWriter
    facts: Any
    rows: int = 0
    text_bytes: int = 0


class _Review:
    """Heuristic review store (only when enabled): the historical queue in SQLite.

    The queue table is rebuilt from committed fact units at every start, so its
    durable truth is the unit's private ``review`` section; per-file insertions
    commit only after that file's unit is published.
    """

    def __init__(self, path: Path, plan: ExecutionPlan, check: Callable[[], None]) -> None:
        from xlm.data.exclusion.storage import connect

        self.db = connect(path, plan.resources.index_bytes, check)
        self.queue = ReviewQueue(self.db, plan.policy.review, plan.resources)

    def rebuild(self, units: list[Unit]) -> None:
        with self.db:
            self.db.execute("DELETE FROM review_queue")
            for unit in units:
                for line in unit.review_lines().splitlines():
                    doc, ref, pattern, matched, total = canonical.loads_bytes_strict(line)
                    self.db.execute(
                        "INSERT INTO review_queue VALUES(?,?,?,?,?)",
                        (doc, ref, pattern, matched, total),
                    )

    def close(self) -> None:
        self.db.close()


class _Engine:
    def __init__(
        self,
        plan: ExecutionPlan,
        identity: str,
        work: Path,
        output: Path,
        index: Path,
        benchmark: dict[str, Any],
        trusted: Mapping[str, bytes],
        issuer: str,
        key: bytes,
        checkpoint: Callable[[str], None],
        compiled: Path,
        progress: Progress,
    ) -> None:
        self.plan, self.identity, self.work, self.output = plan, identity, work, output
        self.index, self.benchmark, self.trusted = index, benchmark, trusted
        self.issuer, self.key, self.checkpoint = issuer, key, checkpoint
        self.compiled, self.progress = compiled, progress
        self.review_enabled = plan.policy.review.enabled
        self.scoped = scoped(plan.policy)
        self.workers = effective_workers(plan)
        self.contexts = tuple(
            FileContext(
                ordinal=n,
                path=item.path,
                source_id=item.source_id,
                source_revision=item.source_revision,
                component=item.component,
                view=item.view,
                upstream=item.upstream_component,
                document_bytes=plan.resources.document_bytes,
                document_tokens=plan.resources.document_tokens,
            )
            for n, item in enumerate(plan.files)
        )
        self.credits = {"attempted_records": 0, "comparisons": 0, "bytes_read": 0}
        self.matcher: CompactExactMatcher | None = None
        self.review: _Review | None = None
        self.pools: list[Pool] = []
        self.group_pool: Pool | None = None
        self.units: list[Unit] = []
        self.group_metrics: dict[str, Any] = {}

    # -- signed state and spent work ----------------------------------------------------

    def _state(self) -> dict[str, Any]:
        state_path = self.work / "state.json"
        self.admission = admit_runtime(
            self.plan.resources,
            self.plan.storage,
            self.work,
            self.output,
            self.identity,
            self.index,
            review=self.review_enabled,
            files=len(self.plan.files),
        )
        if state_path.exists():
            state = verify_signed(read_metadata(state_path, digested=False), self.trusted)
            if state.get("plan") != self.identity:
                raise C05Error("journal plan mismatch")
            return state
        # Signed state is saved before any other job file exists, so job files
        # without it mean deletion; restarting would reset spent time/work/storage.
        names = (FACTS_DIR, GROUP_DIR, REVIEW_DB, REVIEW_DB + "-journal", "decisions.jsonl")
        leftovers = [name for name in names if (self.work / name).exists()] + [
            p.name
            for p in (self.output / (self.identity + ".partial"), self.output / self.identity)
            if p.exists()
        ]
        if leftovers:
            raise C05Error(
                "C05 job files exist without signed state; spent accounting cannot restart: "
                + ", ".join(leftovers)
            )
        now = time.time()
        return {"plan": self.identity, "started": now, "stage_started": now, "stage": "scan"}

    def save_state(self) -> None:
        # Storage peaks are maxima over every attempt; a retry never resets them.
        storage = dict(self.state.get("storage", {}))
        for name, value in (
            ("peak_aggregate_sampled", self.budget.peak_scratch),
            ("peak_journal_sampled", self.budget.peak_journal),
        ):
            storage[name] = max(int(storage.get(name, 0)), value)
        self.state["storage"] = storage
        raw = canonical.canonical_bytes(signed(self.state, self.issuer, self.key))
        canonical.write_atomic(
            self.work / "state.json", bounded_bytes(raw, STATE_BYTES, "signed state")
        )

    def spend(self, name: str, amount_needed: int = 1) -> None:
        # Reserve before work in fsynced blocks. Unused credits are conservatively
        # lost after interruption; replay can never reset spent work accounting.
        credits = self.credits
        if credits[name] < amount_needed:
            maximum = int(getattr(self.plan.resources, name))
            spent = int(self.state.get("spent_" + name, 0))
            block = 1024 * 1024 if name == "bytes_read" else 1024
            amount = min(max(block, amount_needed - credits[name]), maximum - spent)
            if amount < amount_needed - credits[name]:
                raise C05Error("spent " + name + " ceiling")
            self.state["spent_" + name] = spent + amount
            self.save_state()
            credits[name] += amount
        credits[name] -= amount_needed

    def check(self) -> None:
        self.budget.check()

    # -- run ---------------------------------------------------------------------------

    def execute(self) -> dict[str, Any]:
        self.state = self._state()
        self.budget = Budget(self.plan, self.work, self.output, self.state)
        self.budget.benchmark_bytes = self.index.stat().st_size
        self.budget.index = self.index
        self.progress.attach(self.budget.telemetry)
        self.state["storage"] = {
            **self.state.get("storage", {}),
            "admissions": int(self.state.get("storage", {}).get("admissions", 0)) + 1,
        }
        self.save_state()
        self.budget.check(disk=True)
        try:
            self._verify_index()
            self._prepare_matcher()
            self.budget.check(disk=True)
            used = tree_bytes(self.work / FACTS_DIR) + tree_bytes(self.work / GROUP_DIR)
            self.ledger = IndexLedger(self.plan.resources.index_bytes, used)
            self.budget.ledger = self.ledger
            if self.review_enabled:
                self.review = _Review(self.work / REVIEW_DB, self.plan, self.check)
                self.review.queue.compile(
                    index_patterns(self.index, self.plan.resources.document_bytes), self.check
                )
            self._scan()
            if self.state["stage"] == "scan":
                self.state.update(stage="group", stage_started=time.time())
                self.save_state()
            if self.state["stage"] == "group":
                self._group()
                self.state.update(stage="publish", stage_started=time.time())
                self.save_state()
            seal = self._verify_seal()
            final = self.output / self.identity
            if final.exists():
                return verify_completion(final, self.plan, self.trusted)
            result = self._publish(seal)
            self.progress.complete()
            return result
        finally:
            self._close()

    def _close(self) -> None:
        for pool in self.pools:
            pool.close()
        self.pools.clear()
        for unit in self.units:
            unit.close()
        if self.review is not None:
            self.review.close()
        if self.matcher is not None:
            self.matcher.close()

    def _verify_index(self) -> None:
        plan, size = self.plan, self.index.stat().st_size
        if size != self.benchmark["index_bytes"] or size > plan.resources.benchmark_bytes:
            raise C05Error("benchmark index byte ceiling/identity")
        self.progress.stage("INDEX VERIFY", size, "bytes")
        if file_sha(self.index, self.check, lambda done: self.progress.update(done)) != (
            plan.index_sha256
        ):
            raise C05Error("benchmark index hash changed")

    def _prepare_matcher(self) -> None:
        # Compact exact backend (same hits as the historical automaton). A published
        # compile is reused only after every binding and file hash verifies; a crash
        # leaves only staging, which is never trusted. No row is read before this.
        plan = self.plan
        current: list[str] = [""]

        def report(phase: str, done: int, total: int | None) -> None:
            name = "MATCHER VERIFY: " if phase.startswith("verify") else "MATCHER COMPILE: "
            name += phase.upper()
            if name != current[0]:
                current[0] = name
                self.progress.stage(name, total, "bytes" if total else "steps")
            self.progress.update(done)

        self.matcher = prepare(
            self.compiled,
            self.index,
            index_sha256=plan.index_sha256,
            index_bytes=self.benchmark["index_bytes"],
            max_record=plan.resources.document_bytes,
            max_records=plan.resources.benchmark_patterns,
            max_logical_nodes=plan.resources.automaton_nodes,
            check=self.check,
            before_publish=lambda: self.checkpoint("matcher_staged"),
            report=report,
            trigger=trigger_of(plan.policy),
        )
        # Before any corpus row is read: the reviewed trigger coverage must hold.
        check_trigger_coverage(plan, self.benchmark, self.matcher)

    # -- scan -------------------------------------------------------------------------

    def _unit_expectation(self, ordinal: int) -> dict[str, Any]:
        item = self.plan.files[ordinal]
        return {
            "plan": self.identity,
            "ordinal": ordinal,
            "file": item.path,
            "sha": item.documents_sha256,
            "documents": item.documents,
            "canonical_bytes": item.canonical_bytes,
            "issuer": self.issuer,
        }

    def _scan(self) -> None:
        plan = self.plan
        facts = self.work / FACTS_DIR
        facts.mkdir(exist_ok=True)
        assert self.ledger is not None
        expected = {f"{n:05d}.unit" for n in range(len(plan.files))}
        for entry in sorted(facts.iterdir()):
            if entry.name.endswith(".staging"):
                discard(entry, self.ledger)
            elif entry.name not in expected:
                raise C05Error("fact unit outside the plan file range")
        reused: list[int] = []
        todo: list[int] = []
        for ordinal, item in enumerate(plan.files):
            path = contained(Path(plan.data_root), item.path)
            if path.stat().st_size != item.file_bytes:
                raise C05Error("input file size changed")
            if (facts / f"{ordinal:05d}.unit").exists():
                reused.append(ordinal)
            elif self.state["stage"] != "scan":
                raise C05Error("journal claims later stage with missing input")
            else:
                todo.append(ordinal)
        units: dict[int, Unit] = self._verify_reused(reused)
        if self.review is not None:
            self.review.rebuild([units[n] for n in sorted(units)])
        if todo:
            self._scan_files(todo, units)
        self.units = [units[n] for n in range(len(plan.files))]

    def _verify_reused(self, ordinals: list[int]) -> dict[int, Unit]:
        """Re-hash each reused source and verify its unit; no document is re-prepared."""
        plan = self.plan
        if not ordinals:
            return {}
        total = sum(plan.files[n].file_bytes for n in ordinals)
        self.progress.stage("SCAN: VERIFY COMMITTED", total, "bytes")
        for ordinal in ordinals:
            # Reserve the complete frozen file before reading it; never reset on retry.
            self.spend("bytes_read", plan.files[ordinal].file_bytes)

        def verify(ordinal: int) -> Unit:
            item = plan.files[ordinal]
            path = contained(Path(plan.data_root), item.path)
            if file_sha(path) != item.documents_sha256:
                raise C05Error("reusable input hash changed")
            return load_unit(
                self.work / FACTS_DIR / f"{ordinal:05d}.unit",
                self.trusted,
                self._unit_expectation(ordinal),
                review=self.review_enabled,
                scoped=self.scoped,
            )

        units: dict[int, Unit] = {}
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as threads:
            futures = {ordinal: threads.submit(verify, ordinal) for ordinal in ordinals}
            for ordinal in ordinals:
                while True:
                    self.check()
                    try:
                        units[ordinal] = futures[ordinal].result(timeout=0.5)
                        break
                    except TimeoutError:
                        continue
                done += plan.files[ordinal].file_bytes
                self.progress.update(done, files_committed=len(units))
        return units

    def _reader(self, todo: list[int]) -> Iterator[Task | _Marker]:
        """Raw lines in bounded batches; reservations always precede dispatch."""
        plan, limit = self.plan, self.plan.resources.document_bytes
        sequence = 0
        for ordinal in todo:
            item, context = plan.files[ordinal], self.contexts[ordinal]
            path = contained(Path(plan.data_root), item.path)
            digest = hashlib.sha256()
            rows = 0
            batch: list[bytes] = []
            size = 0
            first = 1
            with path.open("rb") as stream:
                # Reserve the complete frozen file before the first read. Failed
                # attempts conservatively spend it; restart never resets I/O.
                self.spend("bytes_read", item.file_bytes)
                while raw := stream.readline(limit + 1):
                    self.spend("attempted_records")
                    self.check()
                    refusal = ""
                    if len(raw) > limit:
                        refusal = "canonical record ceiling"
                    else:
                        digest.update(raw)
                        rows += 1
                        if rows > item.documents:
                            refusal = "input counters exceed frozen file"
                    if refusal:
                        if batch:
                            yield Task(context, sequence, first, tuple(batch))
                            sequence += 1
                        yield _Marker(sequence, ordinal, "error", reason=refusal)
                        return
                    batch.append(raw)
                    size += len(raw)
                    self.bytes_read += len(raw)
                    if len(batch) >= BATCH_ROWS or size >= BATCH_BYTES:
                        yield Task(context, sequence, first, tuple(batch))
                        sequence += 1
                        first += len(batch)
                        batch, size = [], 0
            if batch:
                yield Task(context, sequence, first, tuple(batch))
                sequence += 1
            yield _Marker(sequence, ordinal, "end", sha=digest.hexdigest(), rows=rows)
            sequence += 1

    def _scan_files(self, todo: list[int], units: dict[int, Unit]) -> None:
        plan = self.plan
        assert self.matcher is not None and self.ledger is not None
        spec = MatcherSpec(
            directory=str(self.compiled),
            index_sha256=plan.index_sha256,
            index_bytes=self.benchmark["index_bytes"],
            max_records=plan.resources.benchmark_patterns,
            max_logical_nodes=plan.resources.automaton_nodes,
            trigger=None if trigger_of(plan.policy) is None else trigger_of(plan.policy).identity(),  # type: ignore[union-attr]
        )
        init = ScanInit(plan.policy.model_dump(mode="json"), spec, self.review_enabled)
        matcher = self.matcher
        pool = make_pool(self.workers, "scan", init, lambda: ScanRole(init, matcher=matcher))
        self.pools.append(pool)
        self.progress.stage("SCAN: START WORKERS", self.workers, "workers")
        pool.start(self.check)
        self.budget.tree.rescan()
        total_docs = sum(f.documents for f in plan.files)
        self.committed_docs = sum(units[n].rows for n in units)
        self.committed_files = len(units)
        self.processed = self.committed_docs
        self.bytes_read = 0
        self.byte_rate = RollingRate()
        self.progress.stage("SCAN", total_docs, "docs")
        self.open: _Open | None = None
        reader = self._reader(todo)
        buffered: dict[int, PreparedBatch | _Marker] = {}
        expected = 0
        in_flight = 0
        exhausted = False
        try:
            while True:
                while not exhausted and in_flight < pool.capacity:
                    try:
                        item = next(reader)
                    except StopIteration:
                        exhausted = True
                        break
                    if isinstance(item, Task):
                        pool.submit(item)
                        in_flight += 1
                    else:
                        buffered[item.sequence] = item
                        exhausted = exhausted or item.kind == "error"
                while expected in buffered:
                    self._integrate(buffered.pop(expected), units)
                    expected += 1
                if not in_flight:
                    if exhausted and not buffered:
                        break
                    continue
                batch: PreparedBatch = pool.get(self.check)
                in_flight -= 1
                buffered[batch.sequence] = batch
                self._scan_progress(pool)
        finally:
            if self.open is not None:
                self.open.writer.abort()
                self.open = None
                if self.review is not None:
                    self.review.db.rollback()
            pool.close()
            self.pools.remove(pool)
        self._scan_progress(pool, force=True)

    def _scan_progress(self, pool: Pool, *, force: bool = False) -> None:
        now = time.monotonic()
        self.byte_rate.add(now, self.bytes_read)
        rate = self.byte_rate.rate()
        fields: dict[str, Any] = {
            "committed": self.committed_docs,
            "files_committed": self.committed_files,
            "files_total": len(self.plan.files),
            "bytes_done": self.bytes_read,
            "bytes_total": sum(f.file_bytes for f in self.plan.files),
            **pool.telemetry(),
        }
        if rate is not None:
            fields["mib_per_s"] = rate / 1024**2
        self.progress.update(self.processed, force=force, **fields)

    def _integrate(self, item: PreparedBatch | _Marker, units: dict[int, Unit]) -> None:
        plan = self.plan
        if isinstance(item, _Marker):
            if item.kind == "error":
                raise C05Error(item.reason)
            self._commit(item, units)
            return
        if item.error is not None:
            raise C05Error(item.error[1])
        assert self.ledger is not None
        if self.open is None or self.open.ordinal != item.file:
            if self.open is not None:
                raise C05Error("scan results interleaved across files")
            self.open = _Open(
                item.file,
                UnitWriter(
                    self.work / FACTS_DIR,
                    item.file,
                    self.ledger,
                    review=self.review_enabled,
                    scoped=self.scoped,
                ),
                hashlib.sha256(),
            )
        state = self.open
        frozen = plan.files[item.file]
        sizes = np.frombuffer(item.records, dtype=RECORD)["bytes"]
        cumulative = state.text_bytes + np.cumsum(sizes.astype(np.int64))
        if cumulative.size and int(cumulative[-1]) > frozen.canonical_bytes:
            raise C05Error("input counters exceed frozen file")
        state.writer.append(item)
        if self.review is None:
            state.facts.update(item.fragments)
        else:
            self._review_batch(state, item)
        state.rows += item.count
        state.text_bytes = int(cumulative[-1]) if cumulative.size else state.text_bytes
        self.processed += item.count
        for _ in range(item.count):
            self.checkpoint("row")

    def _review_batch(self, state: _Open, item: PreparedBatch) -> None:
        assert self.review is not None and item.review is not None
        offsets = np.frombuffer(item.fragment_offsets, "<u8").tolist()
        id_offsets = np.frombuffer(item.id_offsets, "<u8").tolist()
        lines: list[bytes] = []
        for index, (count, unique) in enumerate(item.review):
            state.facts.update(item.fragments[offsets[index] : offsets[index + 1]])
            doc = item.ids[id_offsets[index] : id_offsets[index + 1]].decode("utf-8")
            rows = self.review.queue.consider_unique(
                doc, count, set(unique), lambda: self.spend("comparisons")
            )
            state.facts.update(review_fragment(rows))
            lines.extend(canonical.canonical_bytes([doc, *row]) + b"\n" for row in rows)
        if lines:
            state.writer.add_review(b"".join(lines))

    def _commit(self, marker: _Marker, units: dict[int, Unit]) -> None:
        plan = self.plan
        assert self.ledger is not None
        ordinal = marker.ordinal
        if self.open is None:
            self.open = _Open(
                ordinal,
                UnitWriter(
                    self.work / FACTS_DIR,
                    ordinal,
                    self.ledger,
                    review=self.review_enabled,
                    scoped=self.scoped,
                ),
                hashlib.sha256(),
            )
        state = self.open
        frozen = plan.files[ordinal]
        if state.ordinal != ordinal or (
            marker.sha,
            marker.rows,
            state.rows,
            state.text_bytes,
        ) != (frozen.documents_sha256, frozen.documents, frozen.documents, frozen.canonical_bytes):
            raise C05Error("input content/count identity mismatch")
        facts = state.facts.hexdigest()

        def attest(sections: dict[str, Any]) -> dict[str, Any]:
            return signed(
                unit_body(
                    plan=self.identity,
                    ordinal=ordinal,
                    file=frozen.path,
                    sha=frozen.documents_sha256,
                    documents=frozen.documents,
                    canonical_bytes=frozen.canonical_bytes,
                    facts=facts,
                    sections=sections,
                    scoped=self.scoped,
                ),
                self.issuer,
                self.key,
            )

        self.budget.check(disk=True)
        state.writer.commit(attest)
        self.open = None
        if self.review is not None:
            self.review.db.commit()
        path = self.work / FACTS_DIR / f"{ordinal:05d}.unit"
        units[ordinal] = load_unit(
            path,
            self.trusted,
            self._unit_expectation(ordinal),
            review=self.review_enabled,
            verify_sections=False,
            scoped=self.scoped,
        )
        self.committed_docs += frozen.documents
        self.committed_files += 1
        self.checkpoint("file_committed")

    # -- group ------------------------------------------------------------------------

    def _group_pool(self) -> Pool:
        from xlm.data.exclusion.grouping import GroupInit, GroupRole

        offsets = [0]
        for unit in self.units:
            offsets.append(offsets[-1] + unit.rows)
        init = GroupInit(
            units=tuple(str(u.path) for u in self.units),
            offsets=tuple(offsets),
            group=str(self.work / GROUP_DIR),
            seed=self.plan.policy.seed,
            permutations=self.plan.policy.permutations,
            bands=self.plan.policy.bands,
            contexts=self.contexts,
            scoped=self.scoped,
        )
        pool = make_pool(self.workers, "group", init, lambda: GroupRole(init))
        self.pools.append(pool)
        pool.start(self.check)
        self.budget.tree.rescan()
        self.group_pool = pool
        return pool

    def _group(self) -> None:
        from xlm.data.exclusion.grouping import Grouper

        assert self.ledger is not None
        group = self.work / GROUP_DIR
        if group.exists():
            # An interrupted grouping attempt is never trusted (as a rolled-back
            # SQLite transaction was); spent comparisons are never reset.
            size = tree_bytes(group)
            shutil.rmtree(group)
            self.ledger.release(size)
        (self.work / SEAL).unlink(missing_ok=True)
        if self.plan.policy.bands != 32 or self.plan.policy.permutations != 128:
            raise C05Error("compact grouping supports the frozen 128x32 MinHash banding")
        pool = self._group_pool()
        grouper = Grouper(
            self.units,
            self.contexts,
            self.plan.policy,
            self.plan.resources,
            group,
            self.ledger,
            pool,
            check=self.check,
            spend_comparison=lambda: self.spend("comparisons"),
            progress=self.progress,
        )
        if grouper.memory.lineage_run_records < 1:
            raise C05Error("RAM ceiling too small for grouping")
        result = grouper.run()
        self.group_metrics = result.metrics
        seal = signed(
            {
                "plan": self.identity,
                "groups": result.digest,
                "review_queue_digest": self._review_digest(),
                "format": facts_format(self.plan),
                "stats": result.stats,
                "files": result.files,
            },
            self.issuer,
            self.key,
        )
        canonical.write_atomic(
            self.work / SEAL,
            bounded_bytes(canonical.canonical_bytes(seal), SEAL_BYTES, "group seal"),
        )
        self.checkpoint("grouped")
        self.budget.check(disk=True)

    def _review_digest(self) -> str:
        return EMPTY_QUEUE_DIGEST if self.review is None else self.review.queue.private_digest()

    def _verify_seal(self) -> dict[str, Any]:
        from xlm.data.exclusion.grouping import verify_group_files

        path = self.work / SEAL
        if not path.is_file():
            raise C05Error("group journal changed")
        body = verify_signed(canonical.loads_bytes_strict(path.read_bytes()), self.trusted)
        if (
            body.get("plan") != self.identity
            or body.get("issuer") != self.issuer
            or body.get("format") != facts_format(self.plan)
            or body.get("review_queue_digest") != self._review_digest()
        ):
            raise C05Error("group journal changed")
        verify_group_files(self.work / GROUP_DIR, body["files"], scoped=self.scoped)
        return body

    # -- publication ----------------------------------------------------------------

    def _publish(self, seal: dict[str, Any]) -> dict[str, Any]:
        from xlm.artifacts.manifest import ensure_plain_path
        from xlm.data.exclusion.publish import merge_counts

        plan = self.plan
        staging = self.output / (self.identity + ".partial")
        ensure_plain_path(staging)
        staging.mkdir(exist_ok=True)
        if any(
            p.name not in {"membership.jsonl", "completion.json", "completion.json.tmp"}
            or not p.is_file()
            for p in staging.iterdir()
        ):
            raise C05Error("unexpected orphan staging entry")
        for staged_file in staging.iterdir():
            ensure_plain_path(staged_file)
        # Only our two exact staging files are rewritten. No recursive deletion.
        membership = staging / "membership.jsonl"
        pool = self.group_pool if self.group_pool is not None else self._group_pool()
        documents = sum(u.rows for u in self.units)
        self.progress.stage("PUBLISH: MEMBERSHIP", documents, "docs")
        totals = {"documents": 0, "kept": 0, "excluded": 0, "duplicates": 0}
        by_component: dict[str, dict[str, int]] = {}
        by_allocation: dict[str, dict[str, int]] = {}
        sizes = {"written": 0, "private": 0}
        step = 1 << 14
        jobs = [
            ("publish", i, lo, min(documents, lo + step))
            for i, lo in enumerate(range(0, documents, step))
        ]
        with (
            membership.open("wb") as output_stream,
            (self.work / "decisions.jsonl").open("wb") as private_stream,
        ):

            def consume(result: tuple[int, bytes, bytes, dict[str, Any]]) -> None:
                _index, decisions, members, counts = result
                sizes["private"] += len(decisions)
                if sizes["private"] > plan.resources.decision_bytes:
                    raise C05Error("private decision ledger ceiling")
                private_stream.write(decisions)
                sizes["written"] += len(members)
                if sizes["written"] > plan.resources.output_bytes:
                    raise C05Error("membership output ceiling")
                output_stream.write(members)
                for name, value in counts["totals"].items():
                    totals[name] += value
                merge_counts(by_component, counts["components"])
                merge_counts(by_allocation, counts["allocations"])
                self.progress.update(
                    totals["documents"],
                    kept=totals["kept"],
                    excluded=totals["excluded"],
                    duplicates=totals["duplicates"],
                    written_bytes=sizes["written"] + sizes["private"],
                )

            ordered_jobs(pool, jobs, lambda r: r[0], consume, self.check)
            self.progress.stage("PUBLISH: FSYNC", None, "files")
            output_stream.flush()
            os.fsync(output_stream.fileno())
            private_stream.flush()
            os.fsync(private_stream.fileno())
        if totals["documents"] != sum(f.documents for f in plan.files):
            raise C05Error("publication aggregate count mismatch")
        self.budget.check(disk=True)
        self.save_state()
        self.progress.stage("PUBLISH: COMPLETION", None, "files")
        bounds = storage_bounds(
            plan.resources, plan.storage, review=self.review_enabled, files=len(plan.files)
        )
        result = signed(
            {
                "kind": completion_kind(plan.policy),
                "mode": plan.mode,
                "plan_digest": self.identity,
                "input_manifest_digest": plan.input_manifest_digest,
                "benchmark_receipt_digest": plan.benchmark_receipt_digest,
                "membership_sha256": file_sha(membership, self.check),
                "membership_bytes": sizes["written"],
                "documents": totals["documents"],
                "kept": totals["kept"],
                "excluded": totals["excluded"],
                "duplicates": totals["duplicates"],
                "components": by_component,
                "allocations": by_allocation,
                "policy_digest": plan.policy.identity(),
                "source_seals": plan.source_seals,
                "index_sha256": plan.index_sha256,
                "review": self._review_summary(),
                "review_decisions": plan.review_decisions,
                "dedup_stats": dict(seal["stats"]),
                "peak_rss_sampled": self.budget.peak_rss,
                "peak_scratch_sampled": self.state["storage"]["peak_aggregate_sampled"],
                "storage": {
                    **summary(bounds),
                    "worst_case_bytes": self.admission["worst_case_bytes"],
                    "admissions": self.state["storage"]["admissions"],
                    "peak_journal_sampled": self.state["storage"]["peak_journal_sampled"],
                },
                "spent_work_reservations": {
                    name: int(self.state.get("spent_" + name, 0)) for name in self.credits
                },
                **self._scoped_summary(),
            },
            self.issuer,
            self.key,
        )
        canonical.write_atomic(
            staging / "completion.json",
            bounded_bytes(canonical.canonical_bytes(result), COMPLETION_BYTES, "completion"),
        )
        self.budget.check(disk=True)
        self.checkpoint("before_publication")
        os.rename(staging, self.output / self.identity)
        return result

    def _scoped_summary(self) -> dict[str, Any]:
        """c05-production-v3 completion fields (absent for v2: historical bodies)."""
        trigger = trigger_of(self.plan.policy)
        if trigger is None:
            return {}
        assert self.matcher is not None
        section = dict(self.matcher.manifest["trigger"])
        return {
            "output_contract": self.plan.output_contract,
            "trigger": {
                **section,
                "benchmark_items": int(self.benchmark["items"]),
                "items_without_active_trigger": int(self.benchmark["items"])
                - int(section["active_items"]),
            },
            "exclusion_lineage": self.plan.policy.exclusion_lineage,  # type: ignore[attr-defined]
            "split_lineage": self.plan.policy.lineage,
            "exclusion_families": int(self.group_metrics.get("exclusion_families", -1)),
        }

    def _review_summary(self) -> dict[str, int | str | bool]:
        if self.review is not None:
            return self.review.queue.summary()
        policy = self.plan.policy.review
        return {
            "enabled": policy.enabled,
            "disposition": policy.disposition,
            "candidates": 0,
            "total_capacity_reached": 0 >= self.plan.resources.review_candidates,
            "automatic_exclusions": 0,
        }


def verify_completion(
    directory: Path, plan: ExecutionPlan, trusted: Mapping[str, bytes]
) -> dict[str, Any]:
    envelope = verify_completion_envelope(directory, plan, trusted)
    body = envelope["payload"]
    if file_sha(directory / "membership.jsonl") != body["membership_sha256"]:
        raise C05Error("completion membership changed")
    return envelope


def verify_completion_envelope(
    directory: Path, plan: ExecutionPlan, trusted: Mapping[str, bytes]
) -> dict[str, Any]:
    """Signature, plan bindings, aggregates and membership size; NOT the membership hash.

    Only for a consumer that hashes exactly the membership bytes it consumes in the
    same stream and refuses before publishing on a mismatch (C06 fast path).
    """
    envelope = read_metadata(directory / "completion.json", digested=False)
    body = verify_signed(envelope, trusted)
    if (body.get("kind"), body.get("plan_digest"), body.get("mode")) != (
        completion_kind(plan.policy),
        plan.identity(),
        plan.mode,
    ):
        raise C05Error("completion plan/mode mismatch")
    trigger = trigger_of(plan.policy)
    if trigger is not None and (
        body.get("output_contract") != plan.output_contract
        or not isinstance(body.get("trigger"), dict)
        or body["trigger"].get("policy_digest") != trigger.identity()
        or body["trigger"].get("items_without_active_trigger")
        != trigger.reviewed_items_without_active_trigger
    ):
        raise C05Error("completion trigger/contract binding mismatch")
    membership = directory / "membership.jsonl"
    for name, expected in {
        "input_manifest_digest": plan.input_manifest_digest,
        "benchmark_receipt_digest": plan.benchmark_receipt_digest,
        "policy_digest": plan.policy.identity(),
        "source_seals": plan.source_seals,
        "index_sha256": plan.index_sha256,
        "review_decisions": plan.review_decisions,
        "documents": sum(f.documents for f in plan.files),
    }.items():
        if body.get(name) != expected:
            raise C05Error("completion binding mismatch: " + name)
    if body["kept"] + body["excluded"] + body["duplicates"] != body["documents"]:
        raise C05Error("completion aggregate mismatch")
    if membership.stat().st_size != body["membership_bytes"]:
        raise C05Error("completion membership changed")
    return envelope


def resume_check(
    plan: ExecutionPlan,
    authorization: Mapping[str, Any],
    *,
    index: Path,
    benchmark: Mapping[str, Any],
    trusted: Mapping[str, bytes],
    inspector: VolumeInspector | None = None,
) -> dict[str, Any]:
    """Read-only authentication of every reusable stage, never starts a scan stage."""
    from xlm.data.exclusion.grouping import verify_group_files
    from xlm.data.exclusion.identity import implementation_identity

    identity = plan.identity()
    actual = implementation_identity()
    if (actual["code_identity"], actual["dependency_sha256"]) != (
        plan.code_identity,
        plan.dependency_sha256,
    ):
        raise C05Error("resume code/dependencies changed")
    auth = verify_signed(authorization, trusted)
    if (auth.get("kind"), auth.get("plan_digest"), auth.get("mode")) != (
        "c05_authorization_v2",
        identity,
        plan.mode,
    ):
        raise C05Error("resume authorization mismatch")
    receipt = verify_signed(benchmark, trusted)
    if (
        benchmark["digest"] != plan.benchmark_receipt_digest
        or receipt["index_sha256"] != plan.index_sha256
    ):
        raise C05Error("resume benchmark changed")
    isolation_check(plan, receipt, index, inspector)
    if index.stat().st_size != receipt["index_bytes"] or file_sha(index) != plan.index_sha256:
        raise C05Error("resume protected index changed")
    work = Path(plan.scratch_root) / identity
    if not (work / "state.json").is_file():
        raise C05Error("no signed state to resume")
    review = plan.policy.review.enabled
    with FileLock(str(work / "run.lock"), timeout=0):
        state = verify_signed(read_metadata(work / "state.json", digested=False), trusted)
        if state.get("plan") != identity or state.get("stage") not in {"scan", "group", "publish"}:
            raise C05Error("resume stage/plan mismatch")
        budget = Budget(plan, work, Path(plan.output_root), state)
        budget.benchmark_bytes = index.stat().st_size
        budget.check(disk=True)
        compiled = matcher_directory(plan, work, inspector)
        compiled_matcher = "absent; compiled before scanning"
        if compiled.exists():
            trigger = trigger_of(plan.policy)
            with CompactExactMatcher(
                compiled,
                index_sha256=plan.index_sha256,
                index_bytes=receipt["index_bytes"],
                max_records=plan.resources.benchmark_patterns,
                max_logical_nodes=plan.resources.automaton_nodes,
                check=budget.check,
                trigger=None if trigger is None else trigger.identity(),
            ) as reused:
                check_trigger_coverage(plan, receipt, reused)
            compiled_matcher = "verified"
        verified = 0
        documents = 0

        def report() -> dict[str, Any]:
            return {
                "plan_digest": identity,
                "stage": state["stage"],
                "reusable_files_verified": verified,
                "files_total": len(plan.files),
                "documents_committed": documents,
                "documents_total": sum(f.documents for f in plan.files),
                "compiled_matcher": compiled_matcher,
                # An interrupted compile is never reused; run discards and rebuilds it.
                "incomplete_matcher_staging": (work / STAGING_DIR).exists(),
                "resume_checked": True,
                "execution_performed": False,
            }

        facts = work / FACTS_DIR
        if not facts.exists():
            # Interrupted before the first committed file (e.g. matcher compilation).
            if state["stage"] != "scan" or (Path(plan.output_root) / identity).exists():
                raise C05Error("resume facts journal missing after the scan stage")
            return report()
        for ordinal, item in enumerate(plan.files):
            source = contained(Path(plan.data_root), item.path)
            if source.stat().st_size != item.file_bytes:
                raise C05Error("resume input size changed")
            path = facts / f"{ordinal:05d}.unit"
            if not path.exists():
                if state["stage"] != "scan":
                    raise C05Error("resume missing file in completed stage")
                continue
            if file_sha(source, budget.check) != item.documents_sha256:
                raise C05Error("resume reusable source changed")
            try:
                load_unit(
                    path,
                    trusted,
                    {
                        "plan": identity,
                        "ordinal": ordinal,
                        "file": item.path,
                        "sha": item.documents_sha256,
                        "documents": item.documents,
                        "canonical_bytes": item.canonical_bytes,
                    },
                    review=review,
                    scoped=scoped(plan.policy),
                ).close()
            except C05Error as exc:
                raise C05Error("resume reusable facts changed") from exc
            verified += 1
            documents += item.documents
        if state["stage"] == "publish":
            seal = work / SEAL
            if not seal.is_file():
                raise C05Error("resume group seal missing")
            body = verify_signed(canonical.loads_strict(seal.read_text(encoding="utf-8")), trusted)
            if review:
                db = sqlite3.connect(
                    (work / REVIEW_DB).resolve().as_uri() + "?mode=ro",
                    uri=True,
                    factory=OrderedConnection,
                )
                try:
                    digest = queue_digest(db)
                finally:
                    db.close()
            else:
                digest = EMPTY_QUEUE_DIGEST
            if (
                body.get("plan") != identity
                or body.get("review_queue_digest") != digest
                or body.get("format") != facts_format(plan)
            ):
                raise C05Error("resume group seal changed")
            try:
                verify_group_files(work / GROUP_DIR, body["files"], scoped=scoped(plan.policy))
            except C05Error as exc:
                raise C05Error("resume group seal changed") from exc
        final = Path(plan.output_root) / identity
        if final.exists():
            verify_completion(final, plan, trusted)
        return report()
