"""Synthetic trust roots and generated material exercise production interfaces offline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_c05_engine import KEY, document, setup_run
from test_c05_engine import execute as engine_execute
from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed
from xlm.data.exclusion.control import main
from xlm.data.exclusion.policy import C05Error, ProductionPolicy, Resources, ReviewPolicy


def test_operator_decision_write_once_and_explicit_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("C05_TEST_KEY", KEY.decode())
    trust = tmp_path / "trust.json"
    canonical.write_canonical_json(trust, {"fixture": "C05_TEST_KEY"})
    value = tmp_path / "choice.json"
    output = tmp_path / "decision.json"
    canonical.write_canonical_json(value, {"choice": "KNOWN_GROUP_ONLY"})
    args = [
        "lineage-policy",
        "record",
        "--value",
        str(value),
        "--output",
        str(output),
        "--input-manifest-digest",
        "1" * 64,
        "--evidence-digest",
        "2" * 64,
        "--operator",
        "fixture",
        "--trust",
        str(trust),
        "--issuer",
        "fixture",
        "--key-env",
        "C05_TEST_KEY",
    ]
    assert main(["lineage-policy", "preview"]) == 0
    assert not output.exists()
    assert main(args) == 0
    assert main(args) == 0  # Identical write-once retry is idempotent.
    canonical.write_canonical_json(value, {"choice": "REQUIRE_VERIFIED_BOOK_LINEAGE"})
    assert main(args) == 1
    assert main(["lineage-policy", "show", "--artifact", str(output), "--trust", str(trust)]) == 0
    canonical.write_canonical_json(value, {"choice": "automatic"})
    assert main(args) == 1


def test_review_queue_caps_do_not_exclude_and_replay_is_unique(tmp_path: Path) -> None:
    policy = ProductionPolicy(
        diagnostic_bytes=0,
        quick_bytes=0,
        audit_bytes=0,
        review=ReviewPolicy(
            enabled=True,
            min_tokens=5,
            min_distinct=3,
            candidates_per_document=1,
            candidates_per_benchmark=2,
        ),
    )
    docs = [document(str(n), "Why do silver bridges expand during summer?") for n in range(5)]
    plan, index, receipt = setup_run(
        tmp_path, docs, policy=policy, resources=Resources(free_bytes=0, review_candidates=3)
    )
    result = engine_execute(plan, index, receipt)
    assert result["payload"]["excluded"] == 0
    assert result["payload"]["review"]["candidates"] == 2
    assert result["payload"]["review"]["automatic_exclusions"] == 0
    assert engine_execute(plan, index, receipt) == result
    serialized = canonical.canonical_bytes(result)
    assert b"silver" not in serialized and b"pattern" not in serialized


def test_byte_and_oversized_bucket_limits(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(
        tmp_path,
        [document(str(i), "Some lengthy repeated authored document.") for i in range(4)],
        policy=ProductionPolicy(max_bucket_size=2),
        resources=Resources(free_bytes=0, oversized_buckets=0),
    )
    with pytest.raises(C05Error, match="oversized"):
        engine_execute(plan, index, receipt)
    with pytest.raises(C05Error, match="byte ceiling"):
        plan.model_copy(update={"resources": Resources(bytes_read=1)}).identity()


def protected_gate_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: CanonicalDocument | None = None
) -> tuple[Path, ExecutionPlan, Any]:
    """Authored signed completion; never a real protected run or external trust root."""
    clean = document("clean", "An independent illustrated history of imaginary glass satellites.")
    plan, index, receipt = setup_run(tmp_path / "engine", [clean, *([extra] if extra else [])])
    manifest = {"kind": "authored-trust-fixture"}
    manifest["digest"] = canonical.self_digest(manifest)
    plan = plan.model_copy(update={"input_manifest_digest": manifest["digest"]})
    result = engine_execute(plan, index, receipt)
    original = Path(plan.output_root) / plan.identity()
    # Verify protected consumers with a synthetic signed issuer. The engine's
    # protected execution guard is intentionally not bypassed or monkeypatched.
    plan = plan.model_copy(update={"mode": "protected"})
    destination = Path(plan.output_root) / plan.identity()
    destination.mkdir()
    (destination / "membership.jsonl").write_bytes((original / "membership.jsonl").read_bytes())
    body = {**result["payload"], "mode": "protected", "plan_digest": plan.identity()}
    completion = signed(body, "fixture", KEY)
    canonical.write_canonical_json(destination / "completion.json", completion)
    canonical.write_canonical_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    canonical.write_canonical_json(tmp_path / "manifest.json", manifest)
    canonical.write_canonical_json(tmp_path / "trust.json", {"fixture": "C05_TEST_KEY"})
    monkeypatch.setenv("C05_TEST_KEY", KEY.decode())
    proof = tmp_path / "proof.json"
    canonical.write_canonical_json(
        proof,
        {
            "plan": str(tmp_path / "plan.json"),
            "manifest": str(tmp_path / "manifest.json"),
            "completion": str(destination),
            "trust": str(tmp_path / "trust.json"),
            "scratch": str(tmp_path / "lookup"),
            "plan_digest": plan.identity(),
            "completion_digest": completion["digest"],
            "signer": "fixture",
            "signer_key_env": "C05_TEST_KEY",
        },
    )
    return proof, plan, clean


def test_downstream_cli_tokenizer_exact_count_mixture_and_training(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from xlm.cli.mixture_cmd import mixture_app
    from xlm.cli.tokenizer_cmd import app as tokenizer_app
    from xlm.core.paths import ArtifactPaths
    from xlm.data.exclusion.transport import open_gate
    from xlm.data.tokens import TokenShardWriter
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer
    from xlm.training.inputs import resolve_training_input

    proof, plan, clean = protected_gate_fixture(tmp_path, monkeypatch)
    documents = Path(plan.data_root) / "0.jsonl"
    cli = CliRunner()
    train_args = ["train", "--data-path", str(documents), "--vocab-size", "260", "--is-production"]
    # Production 32768 constraint is independent: use ordinary BPE fitting with
    # an explicitly supplied protected membership gate for this tiny fixture.
    train_args.remove("--is-production")
    fitted = cli.invoke(
        tokenizer_app,
        train_args + ["--c05-proof", str(proof), "--output-dir", str(tmp_path / "tokenizer")],
    )
    assert fitted.exit_code == 0, fitted.output
    tokenizer = ByteLevelBPETokenizer.load(tmp_path / "tokenizer")
    counted = cli.invoke(
        tokenizer_app,
        [
            "count-exact",
            "--data-path",
            str(documents),
            "--tokenizer",
            str(tmp_path / "tokenizer"),
            "--c05-proof",
            str(proof),
            "--output",
            str(tmp_path / "count.json"),
        ],
    )
    assert counted.exit_code == 0, counted.output
    count = canonical.loads_bytes_strict((tmp_path / "count.json").read_bytes())
    assert count["tokens"] == len(tokenizer.encode(clean.text, add_special_tokens=False))
    with open_gate(proof) as gate:
        assert gate is not None
        TokenShardWriter(
            tmp_path / "shards/authored", "fixture", "authored", tokenizer, c05_gate=gate
        ).write_documents([clean], True)
    recipe = tmp_path / "recipe.json"
    canonical.write_canonical_json(
        recipe,
        {"mixture_id": "Mix-01-authored", "components": [{"source_id": "authored", "weight": 1.0}]},
    )
    args = [
        "plan",
        "--recipe",
        str(recipe),
        "--shards",
        str(tmp_path / "shards"),
        "--budget-targets",
        "10",
        "--output",
        str(tmp_path / "exposure.json"),
    ]
    assert cli.invoke(mixture_app, args).exit_code != 0
    planned = cli.invoke(mixture_app, args + ["--c05-proof", str(proof)])
    assert planned.exit_code == 0, planned.output
    exposure = canonical.loads_bytes_strict((tmp_path / "exposure.json").read_bytes())
    assert exposure["c05_binding"]["plan_digest"] == plan.identity()
    data = {
        "mixture": canonical.loads_bytes_strict(recipe.read_bytes()),
        "sources": {"authored": str(tmp_path / "shards/authored")},
        "c05_proof": str(proof),
    }
    _, identity = resolve_training_input(data, ArtifactPaths(root=tmp_path / "artifacts"))
    assert data["c05_binding"]["plan_digest"] == plan.identity()
    assert len(identity) == 64
    from test_final_acceptance import tiny_plan
    from xlm.cli.main import app as root_app

    training = tiny_plan()
    training["model"]["vocab_size"] = tokenizer.vocab_size
    training["data"] = {**data, "tokenizer_artifact": str(tmp_path / "tokenizer")}
    training_path = tmp_path / "training.json"
    canonical.write_canonical_json(training_path, training)
    validated = cli.invoke(root_app, ["train", str(training_path), "--dry-run"])
    assert validated.exit_code == 0, validated.output
    without_proof = {k: v for k, v in data.items() if k not in {"c05_proof", "c05_binding"}}
    with pytest.raises(C05Error, match="proof"):
        resolve_training_input(without_proof, ArtifactPaths(root=tmp_path / "artifacts"))
    changed = clean.to_dict()
    changed["doc_id"] = "top-up"
    documents.write_bytes(canonical.canonical_bytes(changed) + b"\n")
    refused = cli.invoke(
        tokenizer_app,
        [
            "count-exact",
            "--data-path",
            str(documents),
            "--tokenizer",
            str(tmp_path / "tokenizer"),
            "--c05-proof",
            str(proof),
            "--output",
            str(tmp_path / "count-new.json"),
        ],
    )
    assert refused.exit_code != 0
    assert not (tmp_path / "count-new.json").exists()


def test_generated_control_plane_authorize_resume_verify_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.c05_authored_pilot import KEY as PILOT_KEY
    from scripts.c05_authored_pilot import prepare

    from xlm.data.exclusion.identity import implementation_identity
    from xlm.data.exclusion.receipt import (
        FinalExclusionReceipt,
        ReceiptValidationError,
        verify_receipt,
    )
    from xlm.data.exclusion.runner import run

    monkeypatch.setenv("XLM_AUTHORED_C05_KEY", PILOT_KEY)
    paths = prepare(tmp_path / "pilot", 12)
    root = paths["root"]
    plan = ExecutionPlan.model_validate_json(paths["plan"].read_bytes())
    current = implementation_identity()

    def crash(event: str) -> None:
        if event == "grouped":
            raise RuntimeError("authored crash")

    authorization = canonical.loads_bytes_strict((root / "authorization.json").read_bytes())
    benchmark = canonical.loads_bytes_strict(
        (root / "prepared/benchmark-preparation.receipt.json").read_bytes()
    )
    with pytest.raises(RuntimeError, match="authored crash"):
        run(
            plan,
            authorization,
            index=root / "prepared/index.jsonl",
            benchmark=benchmark,
            trusted={"authored-pilot": PILOT_KEY.encode()},
            issuer="authored-pilot",
            key=PILOT_KEY.encode(),
            current_code=current["code_identity"],
            current_dependencies=current["dependency_sha256"],
            checkpoint=crash,
        )
    common = ["--plan", str(paths["plan"]), "--trust", str(paths["trust"])]
    resume_args = [
        "--authorization",
        str(root / "authorization.json"),
        "--benchmark-receipt",
        str(root / "prepared/benchmark-preparation.receipt.json"),
        "--index",
        str(root / "prepared/index.jsonl"),
    ]
    assert main(["resume-check", *common, *resume_args]) == 0
    assert (
        main(
            [
                "resume",
                *common,
                *resume_args,
                "--issuer",
                "authored-pilot",
                "--key-env",
                "XLM_AUTHORED_C05_KEY",
            ]
        )
        == 0
    )
    assert main(["verify", *common]) == 0
    assert main(["publish", *common]) == 0
    assert (
        main(
            [
                "final-receipt",
                *common,
                "--benchmark-receipt",
                str(root / "prepared/benchmark-preparation.receipt.json"),
                "--issuer",
                "authored-pilot",
                "--key-env",
                "XLM_AUTHORED_C05_KEY",
                "--output",
                str(root / "final.json"),
            ]
        )
        == 0
    )
    final = FinalExclusionReceipt.load(root / "final.json")
    assert final.schema_version == "2" and final.dropped_doc_ids == []
    assert (
        final.c05_binding is not None
        and final.c05_binding["input_manifest_digest"] == plan.input_manifest_digest
    )
    with pytest.raises(ReceiptValidationError, match="development"):
        verify_receipt(final, {"authored-pilot": PILOT_KEY.encode()})
    verify_receipt(final, {"authored-pilot": PILOT_KEY.encode()}, policy="development")


@pytest.mark.parametrize(
    "field",
    ["source_seals", "index_sha256", "policy_digest", "review_decisions", "membership_sha256"],
)
def test_stale_completion_bindings_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    from xlm.data.exclusion.transport import open_gate

    proof, plan, _ = protected_gate_fixture(tmp_path, monkeypatch)
    completion_path = Path(plan.output_root) / plan.identity() / "completion.json"
    envelope = canonical.loads_bytes_strict(completion_path.read_bytes())
    envelope["payload"][field] = {} if isinstance(envelope["payload"][field], dict) else "0" * 64
    if field == "review_decisions":
        envelope["payload"][field] = {"policy": "0" * 64}
    canonical.write_canonical_json(completion_path, signed(envelope["payload"], "fixture", KEY))
    with pytest.raises(C05Error):
        with open_gate(proof):
            pass


def test_lineage_revision_binding_and_real_cross_source_parent(tmp_path: Path) -> None:
    from dataclasses import replace

    from test_c05_engine import PROMPT, membership

    parent = document("parent", PROMPT, source="synth", book_id="book-1")
    independent = replace(
        document(
            "other",
            "A diary describing imaginary orchards and ceramic telescopes.",
            source="synth",
            book_id="book-1",
        ),
        source_revision="different-revision",
    )
    child = replace(
        document(
            "child",
            "Unrelated sentence attached through a known explicit parent.",
            source="different-source",
        ),
        parent_ids=["parent"],
    )
    plan, index, receipt = setup_run(tmp_path, [parent, independent, child])
    engine_execute(plan, index, receipt)
    decisions = {r["doc_id"]: r["decision"] for r in membership(plan)}
    assert decisions == {"parent": "excluded", "other": "kept", "child": "excluded"}


def test_gutenberg_policy_checks_file_provenance_even_without_row_metadata(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(
        tmp_path,
        [document("g", "A harmless authored segment.", source="common_pile")],
        policy=ProductionPolicy(gutenberg="require_book_ids"),
    )
    plan = plan.model_copy(
        update={
            "files": (plan.files[0].model_copy(update={"upstream_component": "project_gutenberg"}),)
        }
    )
    with pytest.raises(C05Error, match="book identity"):
        engine_execute(plan, index, receipt)


def test_orphan_publication_is_refused(tmp_path: Path) -> None:
    plan, index, receipt = setup_run(tmp_path, [document("g", "A harmless authored segment.")])
    stage = Path(plan.output_root) / (plan.identity() + ".partial")
    stage.mkdir(parents=True)
    (stage / "private-detail.json").write_text("authored orphan", encoding="utf-8")
    with pytest.raises(C05Error, match="orphan staging"):
        engine_execute(plan, index, receipt)
    assert not (Path(plan.output_root) / plan.identity()).exists()


def test_quota_report_never_turns_partial_counts_into_sufficiency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.exclusion.quotas import quota_report
    from xlm.data.exclusion.transport import open_gate
    from xlm.data.tokens import TokenShardWriter
    from xlm.tokenizers.byte import ByteTokenizer

    proof, _, clean = protected_gate_fixture(tmp_path, monkeypatch)
    key = canonical.canonical_bytes(["authored", "view", None]).decode()
    requirements = {"allocations": {key: 1000}}
    with open_gate(proof) as gate:
        assert gate is not None
        missing = quota_report(gate, requirements, [])
        assert missing["allocations"][key]["exact_valid_targets"] is None
        assert not missing["all_sufficient"]
        TokenShardWriter(
            tmp_path / "tokens", "fixture", "authored", ByteTokenizer(), c05_gate=gate
        ).write_documents([clean], True)
        report = quota_report(gate, requirements, [tmp_path / "tokens"])
        row = report["allocations"][key]
        assert row["exact_valid_targets"] == len(clean.text.encode()) + 1
        assert row["deficit"] == 1000 - row["exact_valid_targets"]
        assert row["status"] == "DEFICIT" and not report["all_sufficient"]


def test_parallel_workers_and_assembly_transport_current_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.datasets.shards import ShardedJsonlWriter
    from xlm.data.exclusion.transport import open_gate
    from xlm.data.parallel_tokens import tokenize_to_single_shard
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    extra = document("second", "The fictional valley supports orchids under violet clouds at dusk.")
    proof, _, clean = protected_gate_fixture(tmp_path, monkeypatch, extra)
    docs = [clean, extra]
    tokenizer = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=280)
    tokenizer.save(tmp_path / "tokenizer")
    writer = ShardedJsonlWriter(tmp_path / "sharded", dataset_id="authored", target_shard_bytes=1)
    for doc in docs:
        writer.write_line(canonical.canonical_bytes(doc.to_dict()).decode(), doc.doc_id)
    writer.finish()
    for workers in (1, 2):
        target = tmp_path / f"tokens-{workers}"
        tokenize_to_single_shard(
            tmp_path / "sharded",
            tmp_path / "tokenizer",
            target,
            source_id="authored",
            shard_id="fixture",
            pool_hash="fixture",
            workers=workers,
            c05_proof=proof,
        )
        with open_gate(proof) as gate:
            assert gate is not None
            assert gate.verify_token_shard(target)["manifest"]["num_documents"] == 2
    for name in ("tokens.bin", "offsets.jsonl", "c05-attestation.json"):
        assert (tmp_path / "tokens-1" / name).read_bytes() == (
            tmp_path / "tokens-2" / name
        ).read_bytes()
