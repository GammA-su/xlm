"""Authored benchmark/corpus fixtures only; no official examples or real scan."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, InputFile, authorize, signed, verify_signed
from xlm.data.exclusion.capacity import probe_geometry
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.runner import file_sha, run, verify_completion
from xlm.data.exclusion.streaming import Pattern, StreamingMatcher, patterns, render

KEY = b"authored-local-test-key-not-a-protected-issuer"
TRUST = {"fixture": KEY}
PROMPT = "Why do copper bridges expand during summer?"


def document(
    identity: str, text: str, *, source: str = "authored", **metadata: Any
) -> CanonicalDocument:
    return CanonicalDocument(
        identity,
        source,
        "revision",
        "authored.jsonl",
        0,
        "raw",
        "clean",
        text,
        len(text.encode()),
        "en",
        1.0,
        "text",
        metadata,
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def small_resources(**overrides: Any) -> Resources:
    """Internally consistent authored ceilings (64 MiB database, ~197 MiB worst case)."""
    values: dict[str, Any] = {
        "free_bytes": 0,
        "index_bytes": 64 * 1024**2,
        "journal_bytes": 96 * 1024**2,
        "decision_bytes": 8 * 1024**2,
        "output_bytes": 8 * 1024**2,
        "benchmark_bytes": 8 * 1024**2,
        "scratch_bytes": 256 * 1024**2,
    }
    values.update(overrides)
    return Resources(**values)


def matcher() -> StreamingMatcher:
    entries = patterns(
        render("arc_easy", {"question": PROMPT, "choices": ["yes", "no"]}),
        "fixture",
        MatcherPolicy(),
    )
    return StreamingMatcher(entries, max_patterns=100, max_nodes=1000)


@pytest.mark.parametrize("copies", [1, 4, 100])
def test_repetition_partition_order_and_normalization(copies: int) -> None:
    from xlm.data.dedup.matchview import match_tokens

    scan = matcher()
    text = "An introduction. WHY do copper bridges expand during summer! Closing notes."
    expected = scan.match(match_tokens(text))
    assert expected
    for _ in range(copies):
        assert scan.match(match_tokens(text)) == expected
    assert scan.match(match_tokens("copperbridge unrelated words yes no")) is None


def test_duplicate_provenance_and_token_boundaries() -> None:
    scan = StreamingMatcher(
        [
            Pattern(("cat", "sat"), ("one",)),
            Pattern(("cat", "sat"), ("two",)),
            Pattern(("sat",), ("suffix",)),
        ],
        max_patterns=3,
        max_nodes=10,
    )
    assert scan.provenance[canonical.digest(("cat", "sat"))] == ("one", "two")
    assert scan.match(["bobcat", "satin"]) is None
    assert scan.match(["the", "cat", "sat"])


@pytest.mark.parametrize(
    "task,row,text",
    [
        (
            "piqa",
            {"goal": "Fasten a loose wooden shelf", "sol1": "yes", "sol2": "no"},
            "Fasten a loose wooden shelf",
        ),
        (
            "blimp",
            {
                "sentence_good": "Those clever owls sing.",
                "sentence_bad": "Those clever owls sings.",
            },
            "Those clever owls sings.",
        ),
        (
            "hellaswag",
            {
                "ctx_a": "A sailor repairs the sail.",
                "ctx_b": "The sailor",
                "endings": ["yes", "no"],
            },
            "A sailor repairs the sail.",
        ),
    ],
)
def test_short_task_rendering(task: str, row: dict[str, Any], text: str) -> None:
    from xlm.data.dedup.matchview import match_tokens

    scan = StreamingMatcher(
        patterns(render(task, row), "fixture", MatcherPolicy()), max_patterns=100, max_nodes=1000
    )
    assert scan.match(match_tokens("prefix " + text + " suffix"))
    assert scan.match(["yes"]) is None
    assert scan.match(["no"]) is None


def setup_run(
    root: Path,
    docs: list[CanonicalDocument],
    *,
    resources: Resources | None = None,
    policy: ProductionPolicy | None = None,
) -> tuple[ExecutionPlan, Path, dict[str, Any]]:
    data = root / "data"
    data.mkdir(parents=True)
    files = []
    for n, doc in enumerate(docs):
        path = data / f"{n}.jsonl"
        path.write_bytes(canonical.canonical_bytes(doc.to_dict()) + b"\n")
        files.append(
            InputFile(
                path=path.name,
                source_key=doc.source_id,
                source_id=doc.source_id,
                source_revision=doc.source_revision,
                component=doc.source_id,
                view="view",
                source_file=doc.source_file,
                documents_sha256=file_sha(path),
                file_bytes=path.stat().st_size,
                canonical_bytes=doc.utf8_byte_count,
                documents=1,
            )
        )
    index = root / "authored-index.jsonl"
    index.write_bytes(
        b"".join(
            canonical.canonical_bytes({"tokens": p.tokens, "provenance": p.provenance}) + b"\n"
            for p in patterns(
                render("arc_easy", {"question": PROMPT, "choices": ["yes", "no"]}),
                "authored",
                MatcherPolicy(),
            )
        )
    )
    receipt = signed(
        {
            "index_sha256": file_sha(index),
            "index_bytes": index.stat().st_size,
            "isolation": {"mode": "authored"},
        },
        "fixture",
        KEY,
    )
    plan = ExecutionPlan(
        sequence=1,
        mode="authored",
        input_manifest_digest="1" * 64,
        source_seals={d.source_id: "2" * 64 for d in docs},
        files=tuple(files),
        benchmark_receipt_digest=receipt["digest"],
        index_sha256=file_sha(index),
        policy=policy or ProductionPolicy(diagnostic_bytes=0, quick_bytes=0, audit_bytes=0),
        resources=resources or small_resources(),
        storage=probe_geometry(root / "scratch"),
        data_root=str(data),
        scratch_root=str(root / "scratch"),
        output_root=str(root / "output"),
        code_commit="3" * 40,
        code_identity="4" * 64,
        dependency_sha256="5" * 64,
    )
    return plan, index, receipt


def execute(
    plan: ExecutionPlan, index: Path, receipt: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    return run(
        plan,
        authorize(plan, "fixture", KEY),
        index=index,
        benchmark=receipt,
        trusted=TRUST,
        issuer="fixture",
        key=KEY,
        current_code="4" * 64,
        current_dependencies="5" * 64,
        **kwargs,
    )


def membership(plan: ExecutionPlan) -> list[dict[str, Any]]:
    return [
        canonical.loads_bytes_strict(row)
        for row in (Path(plan.scratch_root) / plan.identity() / "decisions.jsonl")
        .read_bytes()
        .splitlines()
    ]


def test_disk_group_propagation_aliases_and_no_shard_book_inference(tmp_path: Path) -> None:
    docs = [
        document("a", PROMPT, source="synth", query_seed_url="https://example.invalid/seed"),
        document(
            "b",
            "Independent answer text to a shared seed.",
            source="synth",
            additional_seed_url="http://www.example.invalid/seed?utm_x=y",
        ),
        document("c", "Independent answer text to a shared seed.", source="other"),
        document(
            "d",
            "Distant unrelated material about a fictional planet.",
            source="common_pile",
            upstream_component="project_gutenberg",
        ),
        document(
            "e",
            "Notes on building a tiny mechanical clock.",
            source="common_pile",
            upstream_component="project_gutenberg",
        ),
    ]
    plan, index, receipt = setup_run(tmp_path, docs)
    result = execute(plan, index, receipt)
    rows = {row["doc_id"]: row for row in membership(plan)}
    assert [rows[k]["decision"] for k in "abc"] == ["excluded"] * 3
    assert rows["d"]["decision"] == rows["e"]["decision"] == "kept"
    assert rows["d"]["lineage_group"] != rows["e"]["lineage_group"]
    assert result["payload"]["documents"] == 5
    exported = (Path(plan.output_root) / plan.identity() / "membership.jsonl").read_bytes()
    public_rows = [canonical.loads_bytes_strict(line) for line in exported.splitlines()]
    assert {row["doc_id"] for row in public_rows} == {"d", "e"}
    assert all(row["decision"] == "kept" for row in public_rows)
    assert verify_completion(Path(plan.output_root) / plan.identity(), plan, TRUST) == result


@pytest.mark.parametrize("event", ["row", "file_committed", "grouped", "before_publication"])
def test_interruption_resume_matches_clean_membership(tmp_path: Path, event: str) -> None:
    docs = [document("a", PROMPT), document("b", "A unique narrative about a distant lighthouse.")]
    plan, index, receipt = setup_run(tmp_path / "resumed", docs)

    def crash(point: str) -> None:
        if point == event:
            raise RuntimeError("authored interruption")

    with pytest.raises(RuntimeError, match="authored interruption"):
        execute(plan, index, receipt, checkpoint=crash)
    assert not (Path(plan.output_root) / plan.identity()).exists()
    execute(plan, index, receipt)
    clean, clean_index, clean_receipt = setup_run(tmp_path / "clean", docs)
    execute(clean, clean_index, clean_receipt)
    assert membership(plan) == membership(clean)


def test_retained_same_size_source_drift_refused(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)])

    def crash(event: str) -> None:
        if event == "file_committed":
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=crash)
    path = Path(plan.data_root) / "0.jsonl"
    path.write_bytes(path.read_bytes().replace(b"copper", b"silver"))
    with pytest.raises(C05Error, match="hash changed"):
        execute(plan, index, receipt)


@pytest.mark.parametrize("change", ["index", "code", "authorization", "membership"])
def test_stale_bindings_refused(tmp_path: Path, change: str) -> None:
    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)])
    if change == "index":
        index.write_bytes(index.read_bytes().replace(b"copper", b"silver"))
        with pytest.raises(C05Error, match="index hash"):
            execute(plan, index, receipt)
    elif change == "code":
        with pytest.raises(C05Error, match="code/dependency"):
            run(
                plan,
                authorize(plan, "fixture", KEY),
                index=index,
                benchmark=receipt,
                trusted=TRUST,
                issuer="fixture",
                key=KEY,
                current_code="0" * 64,
                current_dependencies="5" * 64,
            )
    elif change == "authorization":
        auth = authorize(plan, "fixture", KEY)
        auth["payload"]["plan_digest"] = "0" * 64
        with pytest.raises(C05Error):
            verify_signed(auth, TRUST)
    else:
        execute(plan, index, receipt)
        path = Path(plan.output_root) / plan.identity() / "membership.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with pytest.raises(C05Error, match="membership changed"):
            verify_completion(path.parent, plan, TRUST)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ram_bytes", 1),
        ("output_bytes", 1),
        ("decision_bytes", 1),
        ("document_bytes", 20),
        ("document_tokens", 2),
        ("free_bytes", 10**18),
    ],
)
def test_budget_refusal_never_publishes(tmp_path: Path, field: str, value: int) -> None:
    resources = Resources.model_validate({**small_resources().model_dump(), field: value})
    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)], resources=resources)
    with pytest.raises(C05Error):
        execute(plan, index, receipt)
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_deadline_cannot_reset_on_resume(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)])

    def crash(event: str) -> None:
        raise RuntimeError(event)

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=crash)
    state_path = Path(plan.scratch_root) / plan.identity() / "state.json"
    state = verify_signed(canonical.loads_bytes_strict(state_path.read_bytes()), TRUST)
    state["started"] -= plan.resources.overall_seconds + 1
    canonical.write_canonical_json(state_path, signed(state, "fixture", KEY))
    with pytest.raises(C05Error, match="overall deadline"):
        execute(plan, index, receipt)


def test_all_parents_propagate(tmp_path: Path) -> None:
    child = replace(document("c", "Harmless fictional essay."), parent_ids=["a", "b"])
    plan, index, receipt = setup_run(
        tmp_path, [document("a", "Parent one biography."), document("b", PROMPT), child]
    )
    execute(plan, index, receipt)
    assert all(row["decision"] == "excluded" for row in membership(plan))


@pytest.mark.parametrize("table", ["docs", "bands", "lineage", "families", "state"])
def test_journal_tampering_refused(tmp_path: Path, table: str) -> None:
    import sqlite3

    plan, index, receipt = setup_run(tmp_path, [document("a", PROMPT)])

    def crash(event: str) -> None:
        if event == "before_publication":
            raise RuntimeError("interrupt")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=crash)
    root = Path(plan.scratch_root) / plan.identity()
    if table == "state":
        state = canonical.loads_bytes_strict((root / "state.json").read_bytes())
        state["payload"]["stage_started"] += 100
        canonical.write_canonical_json(root / "state.json", state)
    else:
        with sqlite3.connect(root / "facts.sqlite") as db:
            if table == "docs":
                db.execute("UPDATE docs SET hit=NULL")
            elif table == "families":
                db.execute("UPDATE families SET hit=0")
            else:
                db.execute(f"DELETE FROM {table}")
    with pytest.raises(C05Error):
        execute(plan, index, receipt)


def test_membership_gate_rejects_excluded_changed_and_new_records(tmp_path: Path) -> None:
    from xlm.data.exclusion.gates import MembershipGate, screened_documents

    clean = document("b", "A safe fictional account of a mechanical star chart.")
    dirty = document("a", PROMPT)
    plan, index, receipt = setup_run(tmp_path, [dirty, clean])
    manifest = {"kind": "authored_manifest"}
    manifest["digest"] = canonical.self_digest(manifest)
    plan = plan.model_copy(update={"input_manifest_digest": manifest["digest"]})
    execute(plan, index, receipt)
    directory = Path(plan.output_root) / plan.identity()
    with pytest.raises(C05Error, match="development"):
        MembershipGate(directory, plan, manifest, TRUST, tmp_path / "refused.sqlite")
    gate = MembershipGate(
        directory, plan, manifest, TRUST, tmp_path / "lookup.sqlite", authored=True
    )
    try:
        assert list(screened_documents([clean], gate)) == [clean]
        for doc in (
            dirty,
            replace(clean, doc_id="renamed"),
            replace(clean, source_file="regenerated"),
        ):
            with pytest.raises(C05Error, match="screened"):
                list(screened_documents([doc], gate))
        with pytest.raises(C05Error, match="duplicate document"):
            list(screened_documents([clean, clean], gate))
        with pytest.raises(C05Error, match="authored"):
            list(screened_documents([clean], gate, required=True))
    finally:
        gate.close()


def test_unscreened_baseline_rejected_before_tokenization(tmp_path: Path) -> None:
    from xlm.data.tokens import TokenShardWriter
    from xlm.tokenizers.byte import ByteTokenizer

    doc = document("baseline", PROMPT, source="common_pile")
    writer = TokenShardWriter(tmp_path / "tokens", "fixture", "common_pile", ByteTokenizer())
    with pytest.raises(C05Error, match="baseline first-pass"):
        writer.write_documents([doc])
    assert not (tmp_path / "tokens" / "shard_manifest.json").exists()


@pytest.mark.parametrize("event", ["row", "file_committed", "grouped", "before_publication"])
def test_process_death_recovers_hot_journal(tmp_path: Path, event: str) -> None:
    import subprocess
    import sys

    docs = [
        document("a", PROMPT),
        document("b", "A separate authored story about ceramic astrolabes."),
    ]
    plan, index, receipt = setup_run(tmp_path, docs)
    canonical.write_canonical_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    canonical.write_canonical_json(tmp_path / "benchmark.json", receipt)
    script = """
import os,sys
from pathlib import Path
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan,authorize
from xlm.data.exclusion.runner import run
root=Path(sys.argv[1]); event=sys.argv[2]
plan=ExecutionPlan.model_validate(canonical.loads_bytes_strict((root/'plan.json').read_bytes()))
receipt=canonical.loads_bytes_strict((root/'benchmark.json').read_bytes())
key=b'authored-local-test-key-not-a-protected-issuer'
def checkpoint(point):
    if point==event:
        os._exit(29)
run(plan,authorize(plan,'fixture',key),index=root/'authored-index.jsonl',benchmark=receipt,
    trusted={'fixture':key},issuer='fixture',key=key,current_code='4'*64,current_dependencies='5'*64,
    checkpoint=checkpoint)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), event], capture_output=True, timeout=30
    )
    assert process.returncode == 29, process.stderr.decode()
    assert not (Path(plan.output_root) / plan.identity()).exists()
    result = execute(plan, index, receipt)
    assert result["payload"]["documents"] == 2
    assert [row["decision"] for row in membership(plan)] == ["excluded", "kept"]


def test_spent_record_reservation_survives_abort(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(
        tmp_path, [document("a", PROMPT)], resources=small_resources(attempted_records=1)
    )

    def crash(event: str) -> None:
        if event == "row":
            raise RuntimeError("interrupt")

    with pytest.raises(RuntimeError):
        execute(plan, index, receipt, checkpoint=crash)
    with pytest.raises(C05Error, match="spent attempted_records"):
        execute(plan, index, receipt)


def test_mix01_cannot_compile_from_unverified_availability() -> None:
    from xlm.data.sampling import (
        MixtureRecipe,
        SourceAvailability,
        compile_exposure_plan,
        validate_mixture,
    )

    recipe = MixtureRecipe.model_validate(
        {"mixture_id": "Mix-01", "components": [{"source_id": "fixture", "weight": 1.0}]}
    )
    source = SourceAvailability("fixture", "fixture", 100, 100, 0, 400, 1)
    validation = validate_mixture(recipe, {"fixture": source})
    with pytest.raises(C05Error, match="C05 token membership"):
        compile_exposure_plan(recipe, validation, 50)


def test_bounded_authored_engine_pilot(tmp_path: Path) -> None:
    import hashlib
    import json
    import time

    docs = []
    for n in range(300):
        words = [hashlib.sha256(f"authored:{n}:{i}".encode()).hexdigest()[:12] for i in range(32)]
        docs.append(document(f"clean-{n:04d}", " ".join(words)))
    docs.extend(replace(docs[n], doc_id=f"alias-{n:04d}") for n in range(20))
    docs.extend(
        document(f"hit-{n:04d}", f"Authored wrapper {n}. {PROMPT} End.") for n in range(100)
    )
    docs.extend(
        replace(document(f"child-{n:04d}", f"Seed derivative {n}"), parent_ids=[f"hit-{n:04d}"])
        for n in range(10)
    )
    limits = small_resources(
        ram_bytes=1024**3,
        records=430,
        attempted_records=2048,
        files=430,
        comparisons=30000,
        overall_seconds=180,
        stage_seconds=120,
    )
    plan, index, receipt = setup_run(tmp_path, docs, resources=limits)
    started = time.perf_counter()
    result = execute(plan, index, receipt)["payload"]
    assert (result["documents"], result["kept"], result["duplicates"], result["excluded"]) == (
        430,
        300,
        20,
        110,
    )
    print(
        json.dumps(
            {
                "fixture": "authored-only",
                "wall_seconds": time.perf_counter() - started,
                "physical_input_bytes": sum(f.file_bytes for f in plan.files),
                "canonical_bytes": sum(f.canonical_bytes for f in plan.files),
                "counts": {k: result[k] for k in ("documents", "kept", "duplicates", "excluded")},
                "peak_rss_sampled": result["peak_rss_sampled"],
                "peak_scratch_sampled": result["peak_scratch_sampled"],
                "membership_bytes": result["membership_bytes"],
                "dedup_stats": result["dedup_stats"],
                "limits": limits.model_dump(),
            }
        )
    )


def test_near_threshold_boundary_and_bucket_cap(tmp_path: Path) -> None:
    from xlm.data.dedup.minhash import MinHasher, estimated_jaccard

    text = " ".join(f"authoredword{i}" for i in range(60))
    longer = text + " additional content"
    hasher = MinHasher()
    similarity = estimated_jaccard(hasher.signature(text), hasher.signature(longer))
    assert 0.8 < similarity < 1.0
    for n, threshold in enumerate((similarity, similarity + 0.000001)):
        policy = ProductionPolicy(
            near_threshold=threshold, diagnostic_bytes=0, quick_bytes=0, audit_bytes=0
        )
        plan, index, receipt = setup_run(
            tmp_path / str(n), [document("a", text), document("b", longer)], policy=policy
        )
        execute(plan, index, receipt)
        assert sum(r["decision"] == "kept" for r in membership(plan)) == n + 1
        assert next(r for r in membership(plan) if r["doc_id"] == "b")["decision"] == "kept"
    policy = ProductionPolicy(max_bucket_size=2, diagnostic_bytes=0, quick_bytes=0, audit_bytes=0)
    docs = [document(str(i), text, source="a" if i == 4 else "z") for i in range(5)]
    plan, index, receipt = setup_run(tmp_path / "cap", docs, policy=policy)
    result = execute(plan, index, receipt)["payload"]
    assert result["dedup_stats"]["oversized_bands"] > 0
    assert [r["doc_id"] for r in membership(plan) if r["decision"] == "kept"] == ["4"]


def test_shard_order_does_not_change_membership(tmp_path: Path) -> None:
    docs = [
        document("z", PROMPT),
        document("a", "An independent fictional diary of a clock maker."),
    ]
    plan, index, receipt = setup_run(tmp_path / "ordered", docs)
    execute(plan, index, receipt)
    first = membership(plan)
    reversed_plan = plan.model_copy(
        update={
            "files": tuple(reversed(plan.files)),
            "output_root": str(tmp_path / "reversed-output"),
            "scratch_root": str(tmp_path / "reversed-scratch"),
        }
    )
    execute(reversed_plan, index, receipt)
    assert membership(reversed_plan) == first


def test_informative_answer_option_permutation_and_generic_negatives() -> None:
    from xlm.data.dedup.matchview import match_tokens

    answer = "Secure the wooden bracket with three copper bolts beneath the upper shelf"
    row: dict[str, Any] = {
        "ctx": "An artisan assembles a patterned cabinet.",
        "endings": [answer, "yes", "no"],
    }
    scan = StreamingMatcher(
        patterns(render("hellaswag", row), "authored", MatcherPolicy()),
        max_patterns=100,
        max_nodes=1000,
    )
    assert scan.match(match_tokens("Prefix " + answer + " suffix"))
    assert scan.match(match_tokens("no yes " + row["ctx"]))
    for negative in (
        "yes",
        "no",
        "A B C D",
        "Water freezes when sufficiently cold.",
        "A person opens a door.",
        "This is an ordinary sentence.",
    ):
        assert scan.match(match_tokens(negative)) is None


def test_trusted_gate_and_signed_token_shard_authored_trust_fixture(tmp_path: Path) -> None:
    """Exercise protected-mode verification with an ephemeral authored issuer only.

    This deliberately constructs a synthetic trust artifact, not isolation evidence
    or a certificate for real data. Nothing leaves pytest's private directory.
    """
    from xlm.data.exclusion.gates import MembershipGate, count_exact_tokens
    from xlm.data.tokens import TokenShardWriter
    from xlm.tokenizers.byte import ByteTokenizer

    clean = document("b", "A quiet authored account of a brass observatory.")
    dirty = document("a", PROMPT)
    plan, index, receipt = setup_run(tmp_path / "authored", [dirty, clean])
    manifest = {"kind": "authored trust fixture"}
    manifest["digest"] = canonical.self_digest(manifest)
    plan = plan.model_copy(update={"input_manifest_digest": manifest["digest"]})
    result = execute(plan, index, receipt)
    protected = plan.model_copy(update={"mode": "protected"})
    directory = tmp_path / "synthetic-trust"
    directory.mkdir()
    (directory / "membership.jsonl").write_bytes(
        (Path(plan.output_root) / plan.identity() / "membership.jsonl").read_bytes()
    )
    body = {**result["payload"], "mode": "protected", "plan_digest": protected.identity()}
    canonical.write_canonical_json(directory / "completion.json", signed(body, "fixture", KEY))
    gate = MembershipGate(
        directory,
        protected,
        manifest,
        TRUST,
        tmp_path / "membership.sqlite",
        signer=("fixture", KEY),
    )
    try:
        tokenizer = ByteTokenizer()
        counts = count_exact_tokens([clean], tokenizer, gate)
        assert counts["tokens"] == len(tokenizer.encode(clean.text))
        with pytest.raises(C05Error, match="screened"):
            count_exact_tokens([dirty], tokenizer, gate)
        writer = TokenShardWriter(
            tmp_path / "tokens", "fixture", "authored", tokenizer, c05_gate=gate
        )
        writer.write_documents([clean])
        assert gate.verify_token_shard(tmp_path / "tokens")["manifest"]["num_documents"] == 1
        proof = canonical.loads_bytes_strict(
            (tmp_path / "tokens" / "c05-attestation.json").read_bytes()
        )
        proof["payload"]["receipt_digest"] = "0" * 64
        canonical.write_canonical_json(tmp_path / "tokens" / "c05-attestation.json", proof)
        with pytest.raises(C05Error):
            gate.verify_token_shard(tmp_path / "tokens")
    finally:
        gate.close()


def test_sqlite_requires_index_order_and_creates_no_sort_spill(tmp_path: Path) -> None:
    from xlm.data.exclusion.storage import connect

    db = connect(tmp_path / "bounded.sqlite", 1024 * 1024)
    try:
        db.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY,value TEXT)")
        db.executemany("INSERT INTO fixture VALUES(?,?)", [(1, "z"), (2, "a")])
        assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert db.execute("PRAGMA automatic_index").fetchone()[0] == 0
        with pytest.raises(C05Error, match="temporary sort"):
            db.execute("SELECT value FROM fixture ORDER BY value")
        assert db.execute("SELECT value FROM fixture ORDER BY id").fetchall() == [("z",), ("a",)]
    finally:
        db.close()


def test_automaton_build_checks_budget_before_growth() -> None:
    calls = 0

    def refuse() -> None:
        nonlocal calls
        calls += 1
        raise C05Error("authored automaton budget")

    with pytest.raises(C05Error, match="automaton budget"):
        StreamingMatcher(
            [Pattern(("a", "b"), ("fixture",))], max_patterns=10, max_nodes=10, check=refuse
        )
    assert calls == 1
