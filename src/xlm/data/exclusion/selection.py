"""Exact token counts and deterministic final quota selection over C05 kept membership.

Chain: C05 completion -> tokenizer identity -> per-record exact valid-target counts
-> unchanged frozen allocation quotas -> per-allocation selection -> signed
selected-training-membership. Only kept ``train`` records of the verified completion
can be counted or selected; excluded, duplicate, changed or new (top-up) records
fail. A deficit refuses publication: no cross-allocation substitution, quota
renormalization, repetition or silent top-up. Artifacts are content-free.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from xlm.core.contracts import CanonicalDocument
from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import InputFile, signed, verify_signed
from xlm.data.exclusion.gates import C05View, MembershipGate
from xlm.data.exclusion.inputs import contained, read_metadata
from xlm.data.exclusion.policy import C05Error, FrozenModel
from xlm.data.exclusion.progress import NullProgress, RunProgress
from xlm.data.exclusion.quotas import view_requirements
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.storage import OrderedConnection, connect

if TYPE_CHECKING:
    from xlm.tokenizers.base import BaseTokenizer

# Identical to TokenShardWriter(add_special_tokens=True): every token but the first.
COUNT_RULE: dict[str, Any] = {
    "version": "c05-valid-targets-v1",
    "add_special_tokens": True,
    "valid_targets": "max(0, len(encode_with_offsets(text, add_special_tokens=True)) - 1)",
}
MAX_TOKENIZER_FILES = 64
MAX_TOKENIZER_FILE_BYTES = 512 * 1024**2


class SelectionPolicy(FrozenModel):
    version: Literal["c05-quota-selection-v1"] = "c05-quota-selection-v1"
    seed: int = 20260919
    order: Literal["sha256(seed, allocation, doc_id, content) ascending, then doc_id"] = (
        "sha256(seed, allocation, doc_id, content) ascending, then doc_id"
    )
    stop: Literal[
        "whole documents until the quota; the crossing document is truncated to the remainder"
    ] = "whole documents until the quota; the crossing document is truncated to the remainder"
    eligibility: Literal["kept train records with positive exact valid targets"] = (
        "kept train records with positive exact valid targets"
    )
    deficit: Literal["refuse; no substitution, renormalization, repetition or top-up"] = (
        "refuse; no substitution, renormalization, repetition or top-up"
    )

    def identity(self) -> str:
        return canonical.digest(self.model_dump(mode="json"))


class SelectionDeficit(C05Error):
    """At least one frozen allocation lacks enough screened exact valid targets."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("frozen allocation deficit; renewed acquisition and C05 required")
        self.report = report


def allocation_key(component: str, view: str, upstream: str | None) -> str:
    return canonical.canonical_bytes([component, view, upstream]).decode()


def load_tokenizer(directory: Path) -> BaseTokenizer:
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer
    from xlm.tokenizers.byte import ByteTokenizer

    manifest = json.loads((directory / "tokenizer_manifest.json").read_text(encoding="utf-8"))
    kind = manifest.get("type", "bpe")
    if kind == "byte_fixture":
        return ByteTokenizer.load(directory)
    if kind == "bpe":
        return ByteLevelBPETokenizer.load(directory)
    raise C05Error("unknown tokenizer artifact type")


def tokenizer_identity(directory: Path, gate: C05View) -> tuple[BaseTokenizer, dict[str, Any]]:
    """Fingerprint plus every artifact file; a fitted tokenizer must name this C05."""
    entries = sorted(directory.iterdir())
    if len(entries) > MAX_TOKENIZER_FILES:
        raise C05Error("tokenizer artifact file ceiling")
    files: dict[str, str] = {}
    for entry in entries:
        if not entry.is_file() or entry.stat().st_size > MAX_TOKENIZER_FILE_BYTES:
            raise C05Error("tokenizer artifact entry is not a bounded regular file")
        files[entry.name] = file_sha(entry)
    tokenizer = load_tokenizer(directory)
    manifest = json.loads((directory / "tokenizer_manifest.json").read_text(encoding="utf-8"))
    binding_path = directory / "c05-binding.json"
    binding = (
        json.loads(binding_path.read_text(encoding="utf-8")) if binding_path.is_file() else None
    )
    expected = {
        "plan_digest": gate.plan_digest,
        "completion_digest": gate.receipt_digest,
        "tokenizer_fingerprint": tokenizer.fingerprint,
    }
    if binding is not None and binding != expected:
        raise C05Error("tokenizer was fitted on different C05 membership")
    if manifest.get("type", "bpe") == "bpe" and gate.mode == "protected" and binding is None:
        raise C05Error("protected selection requires a tokenizer fitted on this C05 membership")
    return tokenizer, {
        "fingerprint": tokenizer.fingerprint,
        "files_digest": canonical.digest(files),
        "vocab_size": tokenizer.vocab_size,
        "c05_fit_binding": binding is not None,
    }


def valid_targets(tokenizer: BaseTokenizer, text: str) -> int:
    token_ids, _ = tokenizer.encode_with_offsets(text, COUNT_RULE["add_special_tokens"])
    return max(0, len(token_ids) - 1)


def iter_plan_documents(
    gate: MembershipGate, *, components: set[str] | None = None
) -> Iterator[tuple[InputFile, CanonicalDocument]]:
    """Re-read the exact C05 input files; a byte or count change refuses.

    The file hash is checked when each file ends, so consumers publish only after
    the iterator is exhausted.
    """
    plan = gate.plan
    for item in plan.files:
        if components is not None and item.component not in components:
            continue
        path = contained(Path(plan.data_root), item.path)
        if path.stat().st_size != item.file_bytes:
            raise C05Error("input file size changed since C05")
        value = hashlib.sha256()
        rows = 0
        with path.open("rb") as stream:
            while raw := stream.readline(plan.resources.document_bytes + 1):
                if len(raw) > plan.resources.document_bytes:
                    raise C05Error("canonical record ceiling")
                value.update(raw)
                rows += 1
                yield item, CanonicalDocument(**canonical.loads_bytes_strict(raw))
        if (value.hexdigest(), rows) != (item.documents_sha256, item.documents):
            raise C05Error("input content changed since C05")


def _kept_train(gate: MembershipGate, doc_id: str, content: str, allocation: str) -> bool:
    """True for kept train, False for kept non-train; anything else refuses."""
    row = gate.lookup(doc_id)
    if row is None:
        raise C05Error("record is not covered by C05 kept membership")
    stored, decision, split, component, view, upstream = row
    if stored != content or decision != "kept":
        raise C05Error("record differs from C05 kept membership")
    if allocation_key(component, view, upstream) != allocation:
        raise C05Error("record allocation differs from C05 membership")
    return split == "train"


def _scratch(gate: MembershipGate, scratch: Path, name: str) -> tuple[OrderedConnection, Path]:
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{name}-{uuid.uuid4().hex}.sqlite"
    return connect(path, gate.plan.resources.index_bytes), path


def _publish(stage: Path, output: Path) -> None:
    if output.exists():
        raise C05Error("selection artifacts are write-once")
    os.rename(stage, output)


def _bounded_export(rows: Iterator[dict[str, Any]], path: Path, ceiling: int) -> tuple[str, int]:
    value = hashlib.sha256()
    written = 0
    with path.open("xb") as stream:
        for row in rows:
            raw = canonical.canonical_bytes(row) + b"\n"
            written += len(raw)
            if written > ceiling:
                raise C05Error("selection artifact output ceiling")
            stream.write(raw)
            value.update(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return value.hexdigest(), written


def binding_of(gate: C05View) -> dict[str, Any]:
    return {
        "mode": gate.mode,
        "plan_digest": gate.plan_digest,
        "completion_digest": gate.receipt_digest,
        "input_manifest_digest": gate.plan.input_manifest_digest,
        "kept_membership_sha256": gate.completion["membership_sha256"],
        "source_seals": dict(gate.plan.source_seals),
    }


def check_binding(body: Mapping[str, Any], gate: C05View, kind: str) -> None:
    if body.get("kind") != kind:
        raise C05Error("selection artifact kind mismatch")
    for name, expected in binding_of(gate).items():
        if body.get(name) != expected:
            raise C05Error("selection artifact binding mismatch: " + name)


def count_tokens(
    gate: MembershipGate,
    tokenizer_dir: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    scratch: Path,
    progress: RunProgress | NullProgress | None = None,
) -> dict[str, Any]:
    """Count every kept training record of the C05 inputs exactly, then publish.

    The single-process reference (``count-tokens-reference``) and the oracle of
    :mod:`countfast`. ``progress`` is display only (stderr); a failure removes the
    staging directory this call created.
    """
    progress = progress or NullProgress()
    progress.stage("TOKENIZER VERIFY", None, "steps")
    tokenizer, identity = tokenizer_identity(tokenizer_dir, gate)
    db, db_path = _scratch(gate, scratch, "counts")
    stage = output.with_name(output.name + f".partial-{uuid.uuid4().hex}")
    published = False
    total_rows = sum(f.documents for f in gate.plan.files)
    try:
        db.execute(
            "CREATE TABLE counts(id TEXT PRIMARY KEY,content TEXT,allocation TEXT,"
            "tokens INTEGER) WITHOUT ROWID"
        )
        progress.stage("SOURCE COUNT", total_rows, "rows")
        seen = 0
        with db:
            for item, doc in iter_plan_documents(gate):
                seen += 1
                if seen % 1024 == 0:
                    progress.update(seen)
                if gate.lookup(doc.doc_id) is None:
                    continue  # Excluded or duplicate: never counted or selected.
                content = canonical.digest(doc.to_dict())
                allocation = allocation_key(item.component, item.view, item.upstream_component)
                if not _kept_train(gate, doc.doc_id, content, allocation):
                    continue
                if doc.split != "train":
                    raise C05Error("record split differs from C05 membership")
                try:
                    db.execute(
                        "INSERT INTO counts VALUES(?,?,?,?)",
                        (doc.doc_id, content, allocation, valid_targets(tokenizer, doc.text)),
                    )
                except sqlite3.IntegrityError as exc:
                    raise C05Error("repeated record in exact counts") from exc
        progress.update(seen)
        progress.stage("AGGREGATE", None, "steps")
        expected = gate.db.execute("SELECT COUNT(*) FROM membership WHERE split='train'")
        counted = db.execute("SELECT COUNT(*) FROM counts").fetchone()[0]
        if counted != expected.fetchone()[0]:
            raise C05Error("exact counts do not cover every kept training record")
        allocations: dict[str, dict[str, int]] = {}
        progress.stage("EXPORT COUNTS", counted, "rows")

        def rows() -> Iterator[dict[str, Any]]:
            query = "SELECT id,content,allocation,tokens FROM counts ORDER BY id"
            for number, (doc_id, content, allocation, tokens) in enumerate(db.execute(query)):
                if number % 1024 == 0:
                    progress.update(number)
                total = allocations.setdefault(allocation, {"documents": 0, "valid_targets": 0})
                total["documents"] += 1
                total["valid_targets"] += tokens
                yield {
                    "doc_id": doc_id,
                    "content": content,
                    "allocation": canonical.loads_strict(allocation),
                    "valid_targets": tokens,
                }

        stage.mkdir(parents=True)
        sha, size = _bounded_export(
            rows(), stage / "counts.jsonl", gate.plan.resources.output_bytes
        )
        envelope = signed(
            {
                "kind": "c05_exact_token_counts_v1",
                **binding_of(gate),
                "tokenizer": identity,
                "rule": COUNT_RULE,
                "documents": counted,
                "allocations": dict(sorted(allocations.items())),
                "counts_sha256": sha,
                "counts_bytes": size,
            },
            issuer,
            key,
        )
        write_once(stage / "counts.json", envelope)
        progress.stage("PUBLISH", None, "steps")
        _publish(stage, output)
        published = True
        progress.complete()
        return envelope
    finally:
        db.close()
        db_path.unlink(missing_ok=True)
        if not published and stage.is_dir():
            for name in ("counts.jsonl", "counts.json"):
                (stage / name).unlink(missing_ok=True)
            stage.rmdir()  # Fails loudly if anything this call did not create remains.


def verify_counts(
    directory: Path, gate: MembershipGate, tokenizer: Mapping[str, Any]
) -> dict[str, Any]:
    envelope = read_metadata(directory / "counts.json", digested=False)
    body = verify_signed(envelope, gate.trusted)
    check_binding(body, gate, "c05_exact_token_counts_v1")
    if body.get("rule") != COUNT_RULE or body.get("tokenizer") != dict(tokenizer):
        raise C05Error("exact counts use a different tokenizer or counting rule")
    path = directory / "counts.jsonl"
    if path.stat().st_size != body["counts_bytes"] or file_sha(path) != body["counts_sha256"]:
        raise C05Error("exact count artifact changed")
    return envelope


def select(
    gate: MembershipGate,
    counts_dir: Path,
    tokenizer_dir: Path,
    quotas: Path,
    ifm_split: Path,
    output: Path,
    issuer: str,
    key: bytes,
    *,
    scratch: Path,
    policy: SelectionPolicy | None = None,
) -> dict[str, Any]:
    """Deterministically select exactly each frozen allocation quota, then sign it."""
    policy = policy or SelectionPolicy()
    _, identity = tokenizer_identity(tokenizer_dir, gate)
    counts_envelope = verify_counts(counts_dir, gate, identity)
    counts = counts_envelope["payload"]
    requirements = view_requirements(gate, quotas, ifm_split)
    if identity["vocab_size"] != requirements["tokenizer_vocab_size"]:
        raise C05Error("tokenizer vocabulary differs from the frozen quota table")
    quotas_by_allocation: dict[str, int] = requirements["allocations"]
    db, db_path = _scratch(gate, scratch, "selection")
    stage = output.with_name(output.name + f".partial-{uuid.uuid4().hex}")
    try:
        db.execute(
            "CREATE TABLE candidates(allocation TEXT,rank TEXT,id TEXT,content TEXT,"
            "tokens INTEGER,PRIMARY KEY(allocation,rank,id)) WITHOUT ROWID"
        )
        db.execute(
            "CREATE TABLE chosen(id TEXT PRIMARY KEY,content TEXT,allocation TEXT,"
            "counted INTEGER,selected INTEGER) WITHOUT ROWID"
        )
        value = hashlib.sha256()
        records = 0
        with db, (counts_dir / "counts.jsonl").open("rb") as stream:
            while raw := stream.readline(64 * 1024 + 1):
                if len(raw) > 64 * 1024:
                    raise C05Error("count record ceiling")
                value.update(raw)
                row = canonical.loads_bytes_strict(raw)
                if set(row) != {"doc_id", "content", "allocation", "valid_targets"}:
                    raise C05Error("count record schema")
                tokens = row["valid_targets"]
                allocation = allocation_key(*row["allocation"])
                if type(tokens) is not int or tokens < 0:
                    raise C05Error("count record value")
                if allocation not in quotas_by_allocation:
                    raise C05Error("count outside the frozen allocations")
                if not _kept_train(gate, row["doc_id"], row["content"], allocation):
                    raise C05Error("non-training record in exact counts")
                rank = canonical.digest([policy.seed, allocation, row["doc_id"], row["content"]])
                db.execute(
                    "INSERT INTO candidates VALUES(?,?,?,?,?)",
                    (allocation, rank, row["doc_id"], row["content"], tokens),
                )
                records += 1
        if value.hexdigest() != counts["counts_sha256"] or records != counts["documents"]:
            raise C05Error("exact count artifact changed during selection")
        # Even a trusted-signer count artifact must cover the complete eligible pool;
        # an omitted record would silently change the deterministic selection.
        kept_train = gate.db.execute("SELECT COUNT(*) FROM membership WHERE split='train'")
        if records != kept_train.fetchone()[0]:
            raise C05Error("exact counts do not cover every kept training record")
        report: dict[str, dict[str, Any]] = {}
        with db:
            for allocation in sorted(quotas_by_allocation):
                report[allocation] = _select_allocation(
                    db, allocation, quotas_by_allocation[allocation]
                )
        if any(r["status"] != "EXACT" for r in report.values()):
            raise SelectionDeficit(
                {
                    "kind": "c05_selection_deficit_v1",
                    **binding_of(gate),
                    "counts_digest": counts_envelope["digest"],
                    "quota_sha256": requirements["quota_sha256"],
                    "allocations": report,
                    "rule": "same allocation only; separate acquisition authorization "
                    "and renewed global C05 required",
                }
            )
        components: dict[str, dict[str, int]] = {}
        for allocation, row in report.items():
            component = canonical.loads_strict(allocation)[0]
            total = components.setdefault(component, {"quota": 0, "selected_valid_targets": 0})
            total["quota"] += row["quota"]
            total["selected_valid_targets"] += row["selected_valid_targets"]
        if {c: v["quota"] for c, v in components.items()} != requirements["final_quotas"]:
            raise C05Error("selected component totals differ from frozen quotas")

        def rows() -> Iterator[dict[str, Any]]:
            query = "SELECT id,content,allocation,counted,selected FROM chosen ORDER BY id"
            for doc_id, content, allocation, counted, chosen in db.execute(query):
                yield {
                    "doc_id": doc_id,
                    "content": content,
                    "allocation": canonical.loads_strict(allocation),
                    "counted_valid_targets": counted,
                    "selected_valid_targets": chosen,
                }

        stage.mkdir(parents=True)
        sha, size = _bounded_export(
            rows(), stage / "selected.jsonl", gate.plan.resources.output_bytes
        )
        selected_documents = sum(r["selected_documents"] for r in report.values())
        envelope = signed(
            {
                "kind": "c05_selected_pool_v1",
                **binding_of(gate),
                "counts_digest": counts_envelope["digest"],
                "counts_sha256": counts["counts_sha256"],
                "tokenizer": identity,
                "rule": COUNT_RULE,
                "quota_sha256": requirements["quota_sha256"],
                "ifm_split_digest": requirements["ifm_split_digest"],
                "common_pile_split_digest": requirements["common_pile_split_digest"],
                "requirements_digest": canonical.digest(requirements),
                "selection_policy": policy.model_dump(mode="json"),
                "selection_policy_digest": policy.identity(),
                "allocations": report,
                "components": dict(sorted(components.items())),
                "selected_documents": selected_documents,
                "selected_valid_targets": requirements["valid_target_quota"],
                "valid_target_quota": requirements["valid_target_quota"],
                "selected_membership_sha256": sha,
                "selected_membership_bytes": size,
            },
            issuer,
            key,
        )
        write_once(stage / "selection.json", envelope)
        _publish(stage, output)
        return envelope
    finally:
        db.close()
        db_path.unlink(missing_ok=True)


def _select_allocation(db: OrderedConnection, allocation: str, quota: int) -> dict[str, Any]:
    eligible = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM candidates WHERE allocation=? AND tokens>0",
        (allocation,),
    ).fetchone()
    total = documents = truncated = 0
    query = "SELECT id,content,tokens FROM candidates WHERE allocation=? ORDER BY rank,id"
    # Streams the index-ordered cursor; inserts go to a different table.
    for doc_id, content, tokens in db.execute(query, (allocation,)):
        if total == quota:
            break
        if tokens == 0:
            continue
        used = min(tokens, quota - total)
        truncated += int(used < tokens)
        db.execute(
            "INSERT INTO chosen VALUES(?,?,?,?,?)", (doc_id, content, allocation, tokens, used)
        )
        total += used
        documents += 1
    return {
        "quota": quota,
        "eligible_documents": eligible[0],
        "eligible_valid_targets": eligible[1],
        "selected_documents": documents,
        "selected_valid_targets": total,
        "truncated_documents": truncated,
        "deficit": quota - total,
        "status": "EXACT" if total == quota else "DEFICIT",
    }


def verify_selection(directory: Path, gate: MembershipGate) -> dict[str, Any]:
    envelope = read_metadata(directory / "selection.json", digested=False)
    body = verify_signed(envelope, gate.trusted)
    check_binding(body, gate, "c05_selected_pool_v1")
    if body.get("rule") != COUNT_RULE:
        raise C05Error("selection counting rule changed")
    if body["selected_valid_targets"] != sum(
        r["selected_valid_targets"] for r in body["allocations"].values()
    ) or any(r["status"] != "EXACT" for r in body["allocations"].values()):
        raise C05Error("selection is not exact")
    path = directory / "selected.jsonl"
    if (
        path.stat().st_size != body["selected_membership_bytes"]
        or file_sha(path) != body["selected_membership_sha256"]
    ):
        raise C05Error("selected training membership changed")
    return envelope


class SelectionGate:
    """Exact selected-training-membership lookup in the C05 gate's private index."""

    def __init__(self, gate: MembershipGate, directory: Path) -> None:
        self.gate = gate
        self.directory = directory
        self.envelope = verify_selection(directory, gate)
        self.body: dict[str, Any] = self.envelope["payload"]
        self.digest = str(self.envelope["digest"])
        db = gate.db
        db.execute("DROP TABLE IF EXISTS selected")
        db.execute(
            "CREATE TABLE selected(id TEXT PRIMARY KEY,content TEXT,component TEXT,"
            "counted INTEGER,chosen INTEGER)"
        )
        db.execute("CREATE INDEX selected_component ON selected(component)")
        value = hashlib.sha256()
        documents = tokens = 0
        report: dict[str, dict[str, Any]] = self.body["allocations"]
        actual = {a: {"documents": 0, "tokens": 0, "truncated": 0} for a in report}
        with db, (directory / "selected.jsonl").open("rb") as stream:
            while raw := stream.readline(64 * 1024 + 1):
                if len(raw) > 64 * 1024:
                    raise C05Error("selected record ceiling")
                value.update(raw)
                row = canonical.loads_bytes_strict(raw)
                allocation = allocation_key(*row["allocation"])
                if allocation not in actual:
                    raise C05Error("selected record outside the signed allocations")
                if not _kept_train(gate, row["doc_id"], row["content"], allocation):
                    raise C05Error("selected record is not kept training membership")
                chosen, counted = row["selected_valid_targets"], row["counted_valid_targets"]
                if type(chosen) is not int or type(counted) is not int or not 0 < chosen <= counted:
                    raise C05Error("selected valid targets outside the exact count")
                db.execute(
                    "INSERT INTO selected VALUES(?,?,?,?,?)",
                    (row["doc_id"], row["content"], row["allocation"][0], counted, chosen),
                )
                documents += 1
                tokens += chosen
                totals = actual[allocation]
                totals["documents"] += 1
                totals["tokens"] += chosen
                totals["truncated"] += int(chosen < counted)
        if (value.hexdigest(), documents, tokens) != (
            self.body["selected_membership_sha256"],
            self.body["selected_documents"],
            self.body["selected_valid_targets"],
        ):
            raise C05Error("selected membership changed during import")
        # Re-prove each internal allocation from the actual rows, so surplus in one
        # allocation (an IFM view, a Common Pile upstream) can never cover another.
        components: dict[str, int] = {}
        for allocation, totals in actual.items():
            signed_row = report[allocation]
            if (
                totals["tokens"] != signed_row["quota"]
                or totals["tokens"] != signed_row["selected_valid_targets"]
                or totals["documents"] != signed_row["selected_documents"]
                or totals["truncated"] != signed_row["truncated_documents"]
                or totals["truncated"] > 1
            ):
                raise C05Error("selected allocation totals differ from the signed allocation")
            component = canonical.loads_strict(allocation)[0]
            components[component] = components.get(component, 0) + totals["tokens"]
        if {c: v["quota"] for c, v in self.body["components"].items()} != components or sum(
            components.values()
        ) != self.body["valid_target_quota"]:
            raise C05Error("selected component totals differ from the signed allocations")

    def selected(self, doc_id: str) -> bool:
        row = self.gate.db.execute("SELECT 1 FROM selected WHERE id=?", (doc_id,)).fetchone()
        return row is not None

    def expect(self, doc: CanonicalDocument, component: str) -> tuple[int, int]:
        """Exact counted and selected valid targets of a record selected for ``component``."""
        row = self.gate.db.execute(
            "SELECT content,component,counted,chosen FROM selected WHERE id=?", (doc.doc_id,)
        ).fetchone()
        if row is None or (row[0], row[1]) != (canonical.digest(doc.to_dict()), component):
            raise C05Error("document is not exact selected training membership")
        return int(row[2]), int(row[3])

    def verify_component_shard(
        self, directory: Path, component: str, *, reset_seen: bool = True
    ) -> dict[str, Any]:
        """Actual shard bytes and every offset must equal this component's selection."""
        from xlm.data.tokens import TokenShardReader

        proof = self.gate.verify_token_shard(
            directory, reset_seen=reset_seen, rehearsal=self.gate.mode == "authored"
        )
        reader = TokenShardReader(directory)
        if reader.manifest.source_id != component:
            raise C05Error("shard component differs from selection")
        if reader.manifest.tokenizer_hash != self.body["tokenizer"]["fingerprint"]:
            raise C05Error("shard tokenizer differs from the exact-count tokenizer")
        documents = tokens = 0
        for offset in reader.iter_document_offsets():
            row = self.gate.db.execute(
                "SELECT content,component,counted,chosen FROM selected WHERE id=?",
                (offset["doc_id"],),
            ).fetchone()
            if row is None or (row[0], row[1]) != (offset.get("c05_content"), component):
                raise C05Error("shard document outside selected training membership")
            if (
                offset.get("c05_selection") != self.digest
                or offset.get("c05_counted_valid_targets") != row[2]
                or offset.get("c05_selected_valid_targets") != row[3]
                or offset.get("valid_targets") != row[3]
            ):
                raise C05Error("shard exact count or selection drifted")
            documents += 1
            tokens += row[3]
        expected = self.gate.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(chosen),0) FROM selected WHERE component=?",
            (component,),
        ).fetchone()
        if (documents, tokens) != tuple(expected) or tokens != proof["counters"]["valid_targets"]:
            raise C05Error("shard does not contain exactly the selected component membership")
        if tokens != self.body["components"][component]["quota"]:
            raise C05Error("shard valid targets differ from the frozen component quota")
        attestation = canonical.loads_bytes_strict(
            (directory / "c05-attestation.json").read_bytes()
        )
        return {
            "source_id": component,
            "shard_id": reader.manifest.shard_id,
            "manifest_digest": canonical.digest(reader.manifest.to_dict()),
            "counters_digest": canonical.digest(reader.counters),
            "attestation_digest": attestation["digest"],
            "tokens_sha256": reader.manifest.checksum_sha256,
            "offsets_sha256": reader.manifest.offsets_checksum_sha256,
            "documents": documents,
            "valid_targets": tokens,
        }
