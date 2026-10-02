"""Authorized C05 file transactions and atomic content-free publication.

Run only inside the operator's isolated environment for protected plans. Authored
plans exercise the same scan/group/publication code, but cannot issue protected proof.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import shutil
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psutil
from filelock import FileLock

from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed, verify_signed
from xlm.data.exclusion.capacity import (
    COMPLETION_BYTES,
    STATE_BYTES,
    admit_runtime,
    bounded_bytes,
    physical_reserve,
    storage_bounds,
    summary,
)
from xlm.data.exclusion.disk import DiskGroups
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.policy import C05Error, require_engine_acceptance
from xlm.data.exclusion.review import ReviewQueue, queue_digest
from xlm.data.exclusion.streaming import Pattern, StreamingMatcher


def file_sha(path: Path, check: Callable[[], None] = lambda: None) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check()
            value.update(block)
    return value.hexdigest()


def index_patterns(path: Path, max_record: int) -> Iterator[Pattern]:
    with path.open("rb") as stream:
        while raw := stream.readline(max_record + 1):
            if len(raw) > max_record:
                raise C05Error("index record ceiling")
            item = canonical.loads_bytes_strict(raw)
            if not isinstance(item, dict) or set(item) != {"tokens", "provenance"}:
                raise C05Error("index record schema")
            if any(
                not isinstance(item[k], list)
                or not item[k]
                or any(not isinstance(s, str) or not s for s in item[k])
                for k in ("tokens", "provenance")
            ):
                raise C05Error("index record values")
            yield Pattern(tuple(item["tokens"]), tuple(item["provenance"]))


class Budget:
    def __init__(
        self, plan: ExecutionPlan, work: Path, output: Path, state: dict[str, Any]
    ) -> None:
        self.plan, self.work, self.output, self.state = plan, work, output, state
        self.identity = plan.identity()
        self.index: Path | None = None
        self.peak_rss = 0
        self.peak_scratch = 0
        self.peak_journal = 0
        self.journal_bound = min(
            plan.resources.journal_bytes,
            storage_bounds(plan.resources, plan.storage)["facts_rollback_journal"],
        )
        self._last_disk = 0.0
        self._last_ram = 0.0
        self.benchmark_bytes = 0

    def check(self, *, disk: bool = False) -> None:
        now = time.time()
        r = self.plan.resources
        if now < self.state["started"] or now - self.state["started"] > r.overall_seconds:
            raise C05Error("overall deadline exhausted or clock moved backwards")
        if now - self.state["stage_started"] > r.stage_seconds:
            raise C05Error("stage deadline exhausted")
        if disk or time.monotonic() - self._last_ram >= 0.05:
            self._last_ram = time.monotonic()
            process = psutil.Process()
            rss = process.memory_info().rss
            for child in process.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            self.peak_rss = max(self.peak_rss, rss)
            if rss > r.ram_bytes:
                raise C05Error("process-tree RAM ceiling")
        if not disk and time.monotonic() - self._last_disk < 0.25:
            return
        self._last_disk = time.monotonic()
        scratch = sum(p.stat().st_size for p in self.work.rglob("*") if p.is_file())
        output = sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
        aggregate = scratch + output + self.benchmark_bytes
        self.peak_scratch = max(self.peak_scratch, aggregate)
        if aggregate > r.scratch_bytes or output > r.output_bytes:
            raise C05Error("aggregate scratch/output ceiling")
        for root in (self.work, self.output):
            if shutil.disk_usage(root).free < r.free_bytes:
                raise C05Error("free-space reserve")
        if self.index is not None:
            # Sampled: refuses once other consumers leave too little for our growth.
            physical_reserve(
                r, self.plan.storage, self.work, self.output, self.identity, self.index
            )
        # Monitored sample of the derived-hard bound; a WAL/SHM file is never accounted.
        journals = sum(p.stat().st_size for p in self.work.glob("*-journal"))
        self.peak_journal = max(self.peak_journal, journals)
        if journals > self.journal_bound:
            raise C05Error("journal ceiling")
        if any(self.work.glob("*-wal")) or any(self.work.glob("*-shm")):
            raise C05Error("unaccounted SQLite WAL/shared-memory file")


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
) -> dict[str, Any]:
    """Resume a plan without resetting spent time; reusable files are rehashed.

    ``checkpoint`` is a fault-injection seam for authored tests, never an input from
    plan JSON. It may raise at file rows/commits, grouping or pre-publication.
    """
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
    if plan.mode == "protected":
        from xlm.data.exclusion.identity import implementation_identity

        actual = implementation_identity()
        if (plan.code_commit, plan.code_identity, plan.dependency_sha256) != (
            actual["code_commit"],
            actual["code_identity"],
            actual["dependency_sha256"],
        ):
            raise C05Error("protected runtime code/dependencies differ")
        isolation = receipt["isolation"]
        if (
            getpass.getuser().casefold() != str(isolation["operator_principal"]).casefold()
            or str(isolation["operator_principal"]).casefold()
            == str(isolation["denied_agent_principal"]).casefold()
        ):
            raise C05Error("protected runner principal mismatch")
    work = Path(plan.scratch_root) / identity
    output = Path(plan.output_root)
    from xlm.artifacts.manifest import ensure_plain_path

    for path in (work, output, work / "facts.sqlite", work / "state.json"):
        ensure_plain_path(path)
    # Fail closed before creating any job file when a volume cannot hold the
    # remaining worst-case growth; repeated under the lock before work starts.
    admit_runtime(plan.resources, plan.storage, work, output, identity, index)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work / "run.lock"), timeout=0):
        return _locked(
            plan, identity, work, output, index, receipt, trusted, issuer, key, checkpoint
        )


def _locked(
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
) -> dict[str, Any]:
    state_path = work / "state.json"
    admission = admit_runtime(plan.resources, plan.storage, work, output, identity, index)
    if state_path.exists():
        state = verify_signed(read_metadata(state_path, digested=False), trusted)
        if state.get("plan") != identity:
            raise C05Error("journal plan mismatch")
    else:
        # Signed state is saved before any other job file exists, so job files
        # without it mean deletion; restarting would reset spent time/work/storage.
        leftovers = [
            name
            for name in ("facts.sqlite", "facts.sqlite-journal", "decisions.jsonl")
            if (work / name).exists()
        ] + [p.name for p in (output / (identity + ".partial"), output / identity) if p.exists()]
        if leftovers:
            raise C05Error(
                "C05 job files exist without signed state; spent accounting cannot restart: "
                + ", ".join(leftovers)
            )
        state = {
            "plan": identity,
            "started": time.time(),
            "stage_started": time.time(),
            "stage": "scan",
        }
    budget = Budget(plan, work, output, state)
    budget.benchmark_bytes = index.stat().st_size
    budget.index = index

    def save_state() -> None:
        # Storage peaks are maxima over every attempt; a retry never resets them.
        storage = dict(state.get("storage", {}))
        for name, value in (
            ("peak_aggregate_sampled", budget.peak_scratch),
            ("peak_journal_sampled", budget.peak_journal),
        ):
            storage[name] = max(int(storage.get(name, 0)), value)
        state["storage"] = storage
        raw = canonical.canonical_bytes(signed(state, issuer, key))
        canonical.write_atomic(state_path, bounded_bytes(raw, STATE_BYTES, "signed state"))

    state["storage"] = {
        **state.get("storage", {}),
        "admissions": int(state.get("storage", {}).get("admissions", 0)) + 1,
    }
    save_state()
    credits = {"attempted_records": 0, "comparisons": 0, "bytes_read": 0}

    def spend(name: str, amount_needed: int = 1) -> None:
        # Reserve before work in fsynced blocks. Unused credits are conservatively
        # lost after interruption; replay can never reset spent work accounting.
        if credits[name] < amount_needed:
            maximum = int(getattr(plan.resources, name))
            spent = int(state.get("spent_" + name, 0))
            block = 1024 * 1024 if name == "bytes_read" else 1024
            amount = min(max(block, amount_needed - credits[name]), maximum - spent)
            if amount < amount_needed - credits[name]:
                raise C05Error("spent " + name + " ceiling")
            state["spent_" + name] = spent + amount
            save_state()
            credits[name] += amount
        credits[name] -= amount_needed

    def input_hash(path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            remaining = path.stat().st_size
            while remaining:
                size = min(1024 * 1024, remaining)
                spend("bytes_read", size)
                block = stream.read(size)
                if not block:
                    raise C05Error("input truncated while hashing")
                remaining -= len(block)
                value.update(block)
                budget.check()
        return value.hexdigest()

    budget.check(disk=True)
    if (
        index.stat().st_size != benchmark["index_bytes"]
        or index.stat().st_size > plan.resources.benchmark_bytes
    ):
        raise C05Error("benchmark index byte ceiling/identity")
    if file_sha(index, budget.check) != plan.index_sha256:
        raise C05Error("benchmark index hash changed")
    matcher = StreamingMatcher(
        index_patterns(index, plan.resources.document_bytes),
        max_patterns=plan.resources.benchmark_patterns,
        max_nodes=plan.resources.automaton_nodes,
        check=budget.check,
    )
    budget.check(disk=True)
    groups = DiskGroups(work / "facts.sqlite", plan.policy, plan.resources, budget.check)
    try:
        review = ReviewQueue(groups.db, plan.policy.review, plan.resources)
        if plan.policy.review.enabled:
            review.compile(index_patterns(index, plan.resources.document_bytes), budget.check)
        for item in plan.files:
            path = contained(Path(plan.data_root), item.path)
            if path.stat().st_size != item.file_bytes:
                raise C05Error("input file size changed")
            done = groups.db.execute(
                "SELECT sha,attestation FROM files WHERE path=?", (item.path,)
            ).fetchone()
            if done:
                if done[0] != item.documents_sha256 or input_hash(path) != item.documents_sha256:
                    raise C05Error("reusable input hash changed")
                facts = verify_signed(canonical.loads_strict(done[1]), trusted)
                if facts != {
                    "plan": identity,
                    "file": item.path,
                    "sha": item.documents_sha256,
                    "facts": groups.facts_digest(item.path, budget.check),
                    "issuer": issuer,
                }:
                    raise C05Error("reusable file journal/facts changed")
                continue
            if state["stage"] != "scan":
                raise C05Error("journal claims later stage with missing input")
            actual = hashlib.sha256()
            rows = text_bytes = 0
            with groups.db:
                with path.open("rb") as stream:
                    # Reserve the complete frozen file before the first read. Failed
                    # attempts conservatively spend it; restart never resets I/O.
                    spend("bytes_read", item.file_bytes)
                    while raw := stream.readline(plan.resources.document_bytes + 1):
                        spend("attempted_records")
                        budget.check()
                        if len(raw) > plan.resources.document_bytes:
                            raise C05Error("canonical record ceiling")
                        actual.update(raw)
                        doc = CanonicalDocument(**canonical.loads_bytes_strict(raw))
                        if len(doc.text.encode("utf-8")) != doc.utf8_byte_count:
                            raise C05Error("canonical text byte count mismatch")
                        if (
                            doc.source_id != item.source_id
                            or doc.source_revision != item.source_revision
                        ):
                            raise C05Error("canonical source identity mismatch")
                        tokens = match_tokens(doc.text)
                        if len(tokens) > plan.resources.document_tokens:
                            raise C05Error("normalized document token ceiling")
                        rows += 1
                        text_bytes += doc.utf8_byte_count
                        if rows > item.documents or text_bytes > item.canonical_bytes:
                            raise C05Error("input counters exceed frozen file")
                        groups.add(
                            doc,
                            file=item.path,
                            row=rows,
                            component=item.component,
                            view=item.view,
                            tokens=tokens,
                            hit=matcher.match(tokens),
                            upstream=item.upstream_component,
                        )
                        if plan.policy.review.enabled:
                            review.consider(doc.doc_id, tokens, lambda: spend("comparisons"))
                        checkpoint("row")
                if (actual.hexdigest(), rows, text_bytes) != (
                    item.documents_sha256,
                    item.documents,
                    item.canonical_bytes,
                ):
                    raise C05Error("input content/count identity mismatch")
                facts = signed(
                    {
                        "plan": identity,
                        "file": item.path,
                        "sha": item.documents_sha256,
                        "facts": groups.facts_digest(item.path, budget.check),
                    },
                    issuer,
                    key,
                )
                groups.db.execute(
                    "INSERT INTO files VALUES(?,?,?)",
                    (item.path, actual.hexdigest(), canonical.canonical_bytes(facts).decode()),
                )
                budget.check(disk=True)
            checkpoint("file_committed")
        if state["stage"] == "scan":
            state.update(stage="group", stage_started=time.time())
            save_state()
        if state["stage"] == "group":
            with groups.db:
                groups.group(budget.check, lambda: spend("comparisons"))
                seal = signed(
                    {
                        "plan": identity,
                        "groups": groups.group_digest(budget.check),
                        "review_queue_digest": review.private_digest(),
                    },
                    issuer,
                    key,
                )
                groups.db.execute(
                    "INSERT OR REPLACE INTO seals VALUES(?,?)",
                    ("groups", canonical.canonical_bytes(seal).decode()),
                )
                checkpoint("grouped")
                budget.check(disk=True)
            state.update(stage="publish", stage_started=time.time())
            save_state()
        stored_group = groups.db.execute("SELECT value FROM seals WHERE key='groups'").fetchone()
        if stored_group is None or verify_signed(
            canonical.loads_strict(stored_group[0]), trusted
        ) != {
            "plan": identity,
            "groups": groups.group_digest(budget.check),
            "issuer": issuer,
            "review_queue_digest": review.private_digest(),
        }:
            raise C05Error("group journal changed")
        final = output / identity
        if final.exists():
            return verify_completion(final, plan, trusted)
        staging = output / (identity + ".partial")
        from xlm.artifacts.manifest import ensure_plain_path

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
        total = kept = excluded = duplicate = written = private_written = 0
        by_component: dict[str, dict[str, int]] = {}
        by_allocation: dict[str, dict[str, int]] = {}
        with (
            membership.open("wb") as output_stream,
            (work / "decisions.jsonl").open("wb") as private_stream,
        ):
            query = """SELECT d.id,d.source,d.component,d.view,d.file,d.row,d.content,d.bytes,
                       d.duplicate,d.family,d.survivor,f.hit,f.split,f.quick,d.upstream
                       FROM docs d JOIN families f ON d.family=f.id ORDER BY d.id"""
            for (
                doc,
                source,
                component,
                view,
                file,
                row,
                content,
                size,
                dup,
                family,
                survivor,
                hit,
                split,
                quick,
                upstream,
            ) in groups.db.execute(query):
                budget.check()
                decision = "excluded" if hit else "kept" if survivor else "duplicate"
                entry = {
                    "doc_id": doc,
                    "source_id": source,
                    "component": component,
                    "view": view,
                    "file": file,
                    "row": row,
                    "content": content,
                    "bytes": size,
                    "duplicate_group": dup,
                    "lineage_group": family,
                    "decision": decision,
                    "split": split,
                    "quick": bool(quick),
                    "upstream_component": upstream,
                }
                raw = canonical.canonical_bytes(entry) + b"\n"
                private_written += len(raw)
                if private_written > plan.resources.decision_bytes:
                    raise C05Error("private decision ledger ceiling")
                private_stream.write(raw)
                if decision == "kept":
                    written += len(raw)
                    if written > plan.resources.output_bytes:
                        raise C05Error("membership output ceiling")
                    output_stream.write(raw)
                total += 1
                kept += int(decision == "kept")
                excluded += int(decision == "excluded")
                duplicate += int(decision == "duplicate")
                counts = by_component.setdefault(
                    component, {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0}
                )
                counts[decision] += 1
                if decision == "kept" and split == "train":
                    counts["train_bytes"] += size
                allocation = canonical.canonical_bytes([component, view, upstream]).decode()
                subcounts = by_allocation.setdefault(
                    allocation, {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0}
                )
                subcounts[decision] += 1
                if decision == "kept" and split == "train":
                    subcounts["train_bytes"] += size
            output_stream.flush()
            os.fsync(output_stream.fileno())
            private_stream.flush()
            os.fsync(private_stream.fileno())
        if total != sum(f.documents for f in plan.files):
            raise C05Error("publication aggregate count mismatch")
        budget.check(disk=True)
        save_state()
        result = signed(
            {
                "kind": "c05_completion_v2",
                "mode": plan.mode,
                "plan_digest": identity,
                "input_manifest_digest": plan.input_manifest_digest,
                "benchmark_receipt_digest": plan.benchmark_receipt_digest,
                "membership_sha256": file_sha(membership, budget.check),
                "membership_bytes": written,
                "documents": total,
                "kept": kept,
                "excluded": excluded,
                "duplicates": duplicate,
                "components": by_component,
                "allocations": by_allocation,
                "policy_digest": plan.policy.identity(),
                "source_seals": plan.source_seals,
                "index_sha256": plan.index_sha256,
                "review": review.summary(),
                "review_decisions": plan.review_decisions,
                "dedup_stats": dict(groups.db.execute("SELECT key,value FROM stats")),
                "peak_rss_sampled": budget.peak_rss,
                "peak_scratch_sampled": state["storage"]["peak_aggregate_sampled"],
                "storage": {
                    **summary(storage_bounds(plan.resources, plan.storage)),
                    "worst_case_bytes": admission["worst_case_bytes"],
                    "admissions": state["storage"]["admissions"],
                    "peak_journal_sampled": state["storage"]["peak_journal_sampled"],
                },
                "spent_work_reservations": {
                    name: int(state.get("spent_" + name, 0)) for name in credits
                },
            },
            issuer,
            key,
        )
        canonical.write_atomic(
            staging / "completion.json",
            bounded_bytes(canonical.canonical_bytes(result), COMPLETION_BYTES, "completion"),
        )
        budget.check(disk=True)
        checkpoint("before_publication")
        os.rename(staging, final)
        return result
    finally:
        groups.close()


def verify_completion(
    directory: Path, plan: ExecutionPlan, trusted: Mapping[str, bytes]
) -> dict[str, Any]:
    envelope = read_metadata(directory / "completion.json", digested=False)
    body = verify_signed(envelope, trusted)
    if (body.get("kind"), body.get("plan_digest"), body.get("mode")) != (
        "c05_completion_v2",
        plan.identity(),
        plan.mode,
    ):
        raise C05Error("completion plan/mode mismatch")
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
    if (
        membership.stat().st_size != body["membership_bytes"]
        or file_sha(membership) != body["membership_sha256"]
    ):
        raise C05Error("completion membership changed")
    return envelope


def resume_check(
    plan: ExecutionPlan,
    authorization: Mapping[str, Any],
    *,
    index: Path,
    benchmark: Mapping[str, Any],
    trusted: Mapping[str, bytes],
) -> dict[str, Any]:
    """Read-only authentication of every reusable stage, never starts a scan stage."""
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
    if index.stat().st_size != receipt["index_bytes"] or file_sha(index) != plan.index_sha256:
        raise C05Error("resume protected index changed")
    work = Path(plan.scratch_root) / identity
    if not (work / "state.json").is_file():
        raise C05Error("no signed state to resume")
    with FileLock(str(work / "run.lock"), timeout=0):
        state = verify_signed(read_metadata(work / "state.json", digested=False), trusted)
        if state.get("plan") != identity or state.get("stage") not in {"scan", "group", "publish"}:
            raise C05Error("resume stage/plan mismatch")
        budget = Budget(plan, work, Path(plan.output_root), state)
        budget.benchmark_bytes = index.stat().st_size
        budget.check(disk=True)
        groups = DiskGroups(
            work / "facts.sqlite", plan.policy, plan.resources, budget.check, read_only=True
        )
        verified = 0
        try:
            for item in plan.files:
                source = contained(Path(plan.data_root), item.path)
                if source.stat().st_size != item.file_bytes:
                    raise C05Error("resume input size changed")
                row = groups.db.execute(
                    "SELECT sha,attestation FROM files WHERE path=?", (item.path,)
                ).fetchone()
                if row is None:
                    if state["stage"] != "scan":
                        raise C05Error("resume missing file in completed stage")
                    continue
                if (
                    row[0] != item.documents_sha256
                    or file_sha(source, budget.check) != item.documents_sha256
                ):
                    raise C05Error("resume reusable source changed")
                facts = verify_signed(canonical.loads_strict(row[1]), trusted)
                if any(
                    facts.get(k) != v
                    for k, v in {
                        "plan": identity,
                        "file": item.path,
                        "sha": item.documents_sha256,
                        "facts": groups.facts_digest(item.path, budget.check),
                    }.items()
                ):
                    raise C05Error("resume reusable facts changed")
                verified += 1
            if state["stage"] == "publish":
                seal = groups.db.execute("SELECT value FROM seals WHERE key='groups'").fetchone()
                if seal is None:
                    raise C05Error("resume group seal missing")
                sealed_group = verify_signed(canonical.loads_strict(seal[0]), trusted)
                if (
                    sealed_group.get("plan") != identity
                    or sealed_group.get("groups") != groups.group_digest(budget.check)
                    or sealed_group.get("review_queue_digest") != queue_digest(groups.db)
                ):
                    raise C05Error("resume group seal changed")
            final = Path(plan.output_root) / identity
            if final.exists():
                verify_completion(final, plan, trusted)
        finally:
            groups.close()
    return {
        "plan_digest": identity,
        "stage": state["stage"],
        "reusable_files_verified": verified,
        "resume_checked": True,
        "execution_performed": False,
    }
