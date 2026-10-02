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
from xlm.data.exclusion.disk import DiskGroups
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.policy import C05Error, require_engine_acceptance
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
        self.peak_rss = 0
        self.peak_scratch = 0
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
        journals = sum(p.stat().st_size for p in self.work.glob("*-journal"))
        if journals > r.journal_bytes:
            raise C05Error("journal ceiling")


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
    if state_path.exists():
        state = verify_signed(read_metadata(state_path, digested=False), trusted)
        if state.get("plan") != identity:
            raise C05Error("journal plan mismatch")
    else:
        state = {
            "plan": identity,
            "started": time.time(),
            "stage_started": time.time(),
            "stage": "scan",
        }
        canonical.write_canonical_json(state_path, signed(state, issuer, key))
    budget = Budget(plan, work, output, state)
    budget.benchmark_bytes = index.stat().st_size
    credits = {"attempted_records": 0, "comparisons": 0}

    def spend(name: str) -> None:
        # Reserve before work in fsynced blocks. Unused credits are conservatively
        # lost after interruption; replay can never reset spent work accounting.
        if credits[name] == 0:
            maximum = int(getattr(plan.resources, name))
            spent = int(state.get("spent_" + name, 0))
            amount = min(1024, maximum - spent)
            if amount <= 0:
                raise C05Error("spent " + name + " ceiling")
            state["spent_" + name] = spent + amount
            canonical.write_canonical_json(state_path, signed(state, issuer, key))
            credits[name] = amount
        credits[name] -= 1

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
        for item in plan.files:
            path = contained(Path(plan.data_root), item.path)
            if path.stat().st_size != item.file_bytes:
                raise C05Error("input file size changed")
            done = groups.db.execute(
                "SELECT sha,attestation FROM files WHERE path=?", (item.path,)
            ).fetchone()
            if done:
                if (
                    done[0] != item.documents_sha256
                    or file_sha(path, budget.check) != item.documents_sha256
                ):
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
                    while raw := stream.readline(plan.resources.document_bytes + 1):
                        spend("attempted_records")
                        budget.check()
                        if len(raw) > plan.resources.document_bytes:
                            raise C05Error("canonical record ceiling")
                        actual.update(raw)
                        doc = CanonicalDocument(**canonical.loads_bytes_strict(raw))
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
            canonical.write_canonical_json(state_path, signed(state, issuer, key))
        if state["stage"] == "group":
            with groups.db:
                groups.group(budget.check, lambda: spend("comparisons"))
                seal = signed(
                    {"plan": identity, "groups": groups.group_digest(budget.check)}, issuer, key
                )
                groups.db.execute(
                    "INSERT OR REPLACE INTO seals VALUES(?,?)",
                    ("groups", canonical.canonical_bytes(seal).decode()),
                )
                checkpoint("grouped")
                budget.check(disk=True)
            state.update(stage="publish", stage_started=time.time())
            canonical.write_canonical_json(state_path, signed(state, issuer, key))
        stored_group = groups.db.execute("SELECT value FROM seals WHERE key='groups'").fetchone()
        if stored_group is None or verify_signed(
            canonical.loads_strict(stored_group[0]), trusted
        ) != {"plan": identity, "groups": groups.group_digest(budget.check), "issuer": issuer}:
            raise C05Error("group journal changed")
        final = output / identity
        if final.exists():
            return verify_completion(final, plan, trusted)
        staging = output / (identity + ".partial")
        staging.mkdir(exist_ok=True)
        # Only our two exact staging files are rewritten. No recursive deletion.
        membership = staging / "membership.jsonl"
        total = kept = excluded = duplicate = written = 0
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
                "dedup_stats": dict(groups.db.execute("SELECT key,value FROM stats")),
                "peak_rss_sampled": budget.peak_rss,
                "peak_scratch_sampled": budget.peak_scratch,
                "spent_work_reservations": {
                    name: int(state.get("spent_" + name, 0)) for name in credits
                },
            },
            issuer,
            key,
        )
        canonical.write_canonical_json(staging / "completion.json", result)
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
    if (
        membership.stat().st_size != body["membership_bytes"]
        or file_sha(membership) != body["membership_sha256"]
    ):
        raise C05Error("completion membership changed")
    return envelope
