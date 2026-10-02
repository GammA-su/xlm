"""Final allocation chain over a generated corpus; synthetic trust roots only.

The module fixture runs the operator CLI end to end once (authored mode). Each test
works on its own copies when it mutates anything. Protected-claim acceptance uses a
synthetic protected re-signing of the schema-3 receipt, never a real trust root.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts.c05_authored_pilot import KEY
from scripts.c05_synthetic_flow import (
    ISSUER,
    KEY_ENV,
    allocate,
    decide_and_plan,
    execute,
    prepare,
    run_c05,
)

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, signed
from xlm.data.exclusion.policy import C05Error
from xlm.data.exclusion.receipt import (
    BenchmarkClaimBinding,
    FinalExclusionReceipt,
    ReceiptValidationError,
    sign_receipt,
    verify_benchmark_claim,
    verify_receipt,
)
from xlm.data.exclusion.transport import open_gate

TRUST = {ISSUER: KEY.encode()}
load = canonical.loads_bytes_strict


@pytest.fixture(scope="module")
def flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    monkey = pytest.MonkeyPatch()
    monkey.setenv(KEY_ENV, KEY)
    root = tmp_path_factory.mktemp("flow") / "root"
    summary = execute(root)
    yield {"root": root, "summary": summary, "proof": root / "proof.json"}
    monkey.undo()


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)


def gate_of(flow: dict[str, Any]) -> Any:
    return open_gate(flow["proof"], allow_authored=True)


def resign(path: Path, body: dict[str, Any]) -> None:
    canonical.write_canonical_json(path, signed(body, ISSUER, KEY.encode()))


def copy_counts(flow: dict[str, Any], target: Path, edit: Any) -> Path:
    """Re-signed (trusted-issuer) counts whose rows are edited: content checks still apply."""
    shutil.copytree(flow["root"] / "counts", target)
    rows = [load(r) for r in (target / "counts.jsonl").read_bytes().splitlines()]
    rows = edit(rows)
    raw = b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows)
    (target / "counts.jsonl").write_bytes(raw)
    body = load((target / "counts.json").read_bytes())["payload"]
    from xlm.data.exclusion.runner import file_sha

    body.update(counts_sha256=file_sha(target / "counts.jsonl"), counts_bytes=len(raw))
    body["documents"] = len(rows)
    (target / "counts.json").unlink()
    resign(target / "counts.json", body)
    return target


def select_into(flow: dict[str, Any], counts: Path, output: Path) -> dict[str, Any]:
    from xlm.data.exclusion.selection import select

    root = flow["root"]
    with gate_of(flow) as gate:
        return select(
            gate,
            counts,
            root / "tokenizer",
            root / "quotas.yaml",
            root / "ifm-split.json",
            output,
            ISSUER,
            KEY.encode(),
            scratch=output.parent / "scratch",
        )


def test_end_to_end_flow_matches_planted_oracle(flow: dict[str, Any]) -> None:
    summary = flow["summary"]
    assert summary["mode"] == "authored" and summary["receipt_mode"] == "development"
    assert summary["c05"] == {"documents": 383, "kept": 349, "excluded": 17, "duplicates": 17}
    assert summary["selected_valid_targets"] == 10_000
    assert all(a["status"] == "EXACT" for a in summary["allocations"].values())
    assert all(a["selected_valid_targets"] == a["quota"] for a in summary["allocations"].values())
    assert summary["receipt_schema"] == "3" and summary["official_claim_exit"] == 1
    assert summary["storage_admissions"] == 2  # Interrupted run plus resume.
    assert summary["training_binding"]["freeze_digest"] == summary["freeze_digest"]


def test_selection_is_deterministic_and_digest_stable(flow: dict[str, Any], tmp_path: Path) -> None:
    first = select_into(flow, flow["root"] / "counts", tmp_path / "a")
    second = select_into(flow, flow["root"] / "counts", tmp_path / "b")
    original = flow["root"] / "selection/selected.jsonl"
    assert (tmp_path / "a/selected.jsonl").read_bytes() == original.read_bytes()
    assert (tmp_path / "b/selected.jsonl").read_bytes() == original.read_bytes()
    assert first["payload"]["allocations"] == second["payload"]["allocations"]
    assert (
        first["payload"]["selected_membership_sha256"]
        == (
            load((flow["root"] / "selection/selection.json").read_bytes())["payload"][
                "selected_membership_sha256"
            ]
        )
    )
    with pytest.raises(C05Error, match="write-once"):
        select_into(flow, flow["root"] / "counts", tmp_path / "a")


def test_ifm_and_common_pile_internal_allocations_are_exact(flow: dict[str, Any]) -> None:
    from xlm.data.tokens import TokenShardReader

    selection = load((flow["root"] / "selection/selection.json").read_bytes())["payload"]
    rows = [load(r) for r in (flow["root"] / "selection/selected.jsonl").read_bytes().splitlines()]
    by_allocation: dict[tuple[Any, ...], int] = {}
    for row in rows:
        key = tuple(row["allocation"])
        by_allocation[key] = by_allocation.get(key, 0) + row["selected_valid_targets"]
    ifm = "ifm_behaviors_general_planning"
    assert by_allocation[(ifm, "general", None)] == 250
    assert by_allocation[(ifm, "planning", None)] == 250
    common = {k[2]: v for k, v in by_allocation.items() if k[0] == "common_pile_prose"}
    assert common == {
        "libretexts": 80,
        "news": 80,
        "oercommons": 60,
        "pressbooks": 80,
        "project_gutenberg": 160,
        "public_domain_review": 40,
    }
    # The consumed component shards hold exactly these valid targets, no remainder.
    for component in (ifm, "common_pile_prose"):
        reader = TokenShardReader(flow["root"] / "shards" / component)
        assert reader.counters["valid_targets"] == selection["components"][component]["quota"]
    exposure = load((flow["root"] / "freeze/freeze.json").read_bytes())["payload"]
    projections = exposure["exposure_plan"]["projections"]
    assert all(p["repeated_targets"] == 0 for p in projections.values())
    assert all(p["planned_targets"] == p["unique_targets_available"] for p in projections.values())


def test_excluded_and_reallocated_records_cannot_enter_selection(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    decisions = [
        load(r)
        for path in (flow["root"] / "scratch").rglob("decisions.jsonl")
        for r in path.read_bytes().splitlines()
    ]
    excluded = next(r for r in decisions if r["decision"] == "excluded")

    def add_excluded(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allocation = [excluded["component"], excluded["view"], excluded["upstream_component"]]
        extra = {
            "doc_id": excluded["doc_id"],
            "content": excluded["content"],
            "allocation": allocation,
            "valid_targets": 10_000,
        }
        return sorted([*rows, extra], key=lambda r: r["doc_id"])

    counts = copy_counts(flow, tmp_path / "excluded-counts", add_excluded)
    with pytest.raises(C05Error, match="not covered by C05"):
        select_into(flow, counts, tmp_path / "excluded-selection")

    def move(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows[0] = {**rows[0], "allocation": ["simple_stories", "default", None]}
        return rows

    counts = copy_counts(flow, tmp_path / "moved-counts", move)
    with pytest.raises(C05Error, match="allocation differs"):
        select_into(flow, counts, tmp_path / "moved-selection")
    assert not (tmp_path / "excluded-selection").exists()


def test_changed_token_count_is_caught_at_tokenization(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.exclusion.freeze import tokenize_selection

    selected = {
        load(r)["doc_id"]
        for r in (flow["root"] / "selection/selected.jsonl").read_bytes().splitlines()
    }

    def inflate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**r, "valid_targets": r["valid_targets"] + 7} if r["doc_id"] in selected else r
            for r in rows
        ]

    counts = copy_counts(flow, tmp_path / "inflated", inflate)
    drifted = select_into(flow, counts, tmp_path / "drifted")
    assert (
        drifted["digest"]
        != load((flow["root"] / "selection/selection.json").read_bytes())["digest"]
    )
    with gate_of(flow) as gate, pytest.raises(C05Error, match="count drifted"):
        tokenize_selection(gate, tmp_path / "drifted", flow["root"] / "tokenizer", tmp_path / "s")


def test_tokenizer_identity_binding(flow: dict[str, Any], tmp_path: Path) -> None:
    from xlm.data.exclusion.selection import count_tokens
    from xlm.tokenizers.byte import ByteTokenizer

    ByteTokenizer().save(tmp_path / "byte")
    with pytest.raises(C05Error, match="different tokenizer"):
        with gate_of(flow) as gate:
            from xlm.data.exclusion.selection import tokenizer_identity, verify_counts

            _, identity = tokenizer_identity(tmp_path / "byte", gate)
            verify_counts(flow["root"] / "counts", gate, identity)
    changed = tmp_path / "rebound"
    shutil.copytree(flow["root"] / "tokenizer", changed)
    binding = load((changed / "c05-binding.json").read_bytes())
    binding["completion_digest"] = "0" * 64
    (changed / "c05-binding.json").write_bytes(canonical.canonical_bytes(binding))
    with gate_of(flow) as gate, pytest.raises(C05Error, match="different C05 membership"):
        count_tokens(gate, changed, tmp_path / "counts", ISSUER, KEY.encode(), scratch=tmp_path)


def test_selected_pool_receipt_and_freeze_detect_tampering(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.exclusion.freeze import verify_freeze
    from xlm.data.exclusion.selection import verify_selection

    selection = tmp_path / "selection"
    shutil.copytree(flow["root"] / "selection", selection)
    raw = (selection / "selected.jsonl").read_bytes()
    (selection / "selected.jsonl").write_bytes(
        raw.replace(b'"selected_valid_targets":', b'"selected_valid_targets": ', 1)
    )
    with (
        gate_of(flow) as gate,
        pytest.raises(C05Error, match="selected training membership changed"),
    ):
        verify_selection(selection, gate)
    envelope = load((flow["root"] / "selection/selection.json").read_bytes())
    envelope["payload"]["selected_valid_targets"] -= 1
    (selection / "selection.json").write_bytes(canonical.canonical_bytes(envelope))
    with gate_of(flow) as gate, pytest.raises(C05Error, match="untrusted or stale"):
        verify_selection(selection, gate)
    shards = tmp_path / "shards"
    shutil.copytree(flow["root"] / "shards", shards)
    freeze = load((flow["root"] / "freeze/freeze.json").read_bytes())
    for record in freeze["payload"]["shards"].values():
        record["path"] = str((shards / record["source_id"]).resolve())
    resign(tmp_path / "freeze.json", freeze["payload"])
    with gate_of(flow) as gate:
        verify_freeze(tmp_path / "freeze.json", gate)  # Byte-identical relocated copy.
    tokens = shards / "essential_prose/tokens.bin"
    data = bytearray(tokens.read_bytes())
    data[0] ^= 1
    tokens.write_bytes(bytes(data))
    with gate_of(flow) as gate, pytest.raises((C05Error, ValueError)):
        verify_freeze(tmp_path / "freeze.json", gate)


def test_freeze_refuses_missing_extra_or_swapped_shards(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.exclusion.freeze import freeze

    root = flow["root"]

    def attempt(shards: Path, name: str) -> None:
        with gate_of(flow) as gate:
            freeze(
                gate,
                flow["proof"],
                root / "selection",
                shards,
                root / "tokenizer",
                tmp_path / name,
                ISSUER,
                KEY.encode(),
            )

    missing = tmp_path / "missing"
    shutil.copytree(root / "shards", missing)
    shutil.rmtree(missing / "simple_stories")
    with pytest.raises(C05Error, match="shard set"):
        attempt(missing, "f1")
    swapped = tmp_path / "swapped"
    shutil.copytree(root / "shards", swapped)
    (swapped / "finewiki_en").rename(swapped / "tmp")
    (swapped / "essential_prose").rename(swapped / "finewiki_en")
    (swapped / "tmp").rename(swapped / "essential_prose")
    with pytest.raises(C05Error):
        attempt(swapped, "f2")


def test_training_input_requires_exact_freeze(flow: dict[str, Any], tmp_path: Path) -> None:
    from xlm.core.paths import ArtifactPaths
    from xlm.training.inputs import resolve_training_input

    paths = ArtifactPaths(root=tmp_path / "artifacts")
    base = load((flow["root"] / "freeze/training-data.json").read_bytes())
    resolve_training_input(dict(base), paths)
    altered = dict(base)
    altered["exposure_plan"] = {**base["exposure_plan"], "budget_targets": 9_999}
    with pytest.raises(C05Error, match="exposure plan differs"):
        resolve_training_input(altered, paths)
    final_id = {**base, "mixture": {**base["mixture"], "mixture_id": "Mix-01-final"}}
    with pytest.raises((C05Error, ValueError)):
        resolve_training_input(final_id, paths)
    without = {k: v for k, v in final_id.items() if k not in {"c05_freeze", "exposure_plan"}}
    with pytest.raises(ValueError, match="selected-pool freeze"):
        resolve_training_input(without, paths)
    other = dict(base)
    other["c05_binding"] = {
        **load((flow["root"] / "freeze/training-data.json").read_bytes()).get("c05_binding", {}),
        "freeze_digest": "0" * 64,
    }
    with pytest.raises(C05Error, match="binding changed"):
        resolve_training_input(other, paths)


def test_quota_deficit_refuses_without_substitution(tmp_path: Path, monkeypatch: Any) -> None:
    from scripts import c05_synthetic_flow as flow_module

    # Six short records per allocation cannot reach the larger frozen quotas.
    monkeypatch.setattr(flow_module, "TARGETS_PER_RECORD", 10**9)
    monkeypatch.setattr(flow_module, "WORDS", 4)
    paths = prepare(tmp_path / "deficit")
    plan_path = decide_and_plan(paths)
    c05 = run_c05(paths, plan_path)
    with pytest.raises(RuntimeError, match="select"):
        allocate(paths, c05, plan_path)
    report = load((tmp_path / "deficit/deficit.json").read_bytes())
    assert not (tmp_path / "deficit/selection").exists()
    deficits = {k: a for k, a in report["allocations"].items() if a["status"] == "DEFICIT"}
    assert deficits and all(a["deficit"] > 0 for a in deficits.values())
    assert all(
        a["selected_valid_targets"] <= a["eligible_valid_targets"] for a in deficits.values()
    )
    assert "renewed global C05" in report["rule"]


def test_top_up_document_invalidates_counts_and_old_proof(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from scripts.c05_authored_pilot import doc

    from xlm.data.exclusion.freeze import verify_freeze
    from xlm.data.exclusion.selection import count_tokens

    root = tmp_path / "copy"
    shutil.copytree(flow["root"], root)
    plan = ExecutionPlan.model_validate_json((root / "plans/p0001.json").read_bytes())
    data_root = Path(plan.data_root)
    target = data_root / plan.files[0].path
    item = plan.files[0]
    extra = doc(10**6, item.source_id, "A newly acquired top-up record about lanterns.")
    line = canonical.canonical_bytes(extra.to_dict()) + b"\n"
    # A top-up changes the input identity, hence the plan; the old completion is
    # not a C05 result for it and must be renewed before any downstream use.
    private = tmp_path / "private-data"
    shutil.copytree(data_root, private)
    (private / item.path).write_bytes(target.read_bytes() + line)
    from xlm.data.exclusion.runner import file_sha

    topped = item.model_copy(
        update={
            "documents_sha256": file_sha(private / item.path),
            "file_bytes": item.file_bytes + len(line),
            "documents": item.documents + 1,
            "canonical_bytes": item.canonical_bytes + extra.utf8_byte_count,
        }
    )
    changed = plan.model_copy(
        update={"data_root": str(private), "files": (topped, *plan.files[1:])}
    )
    assert changed.identity() != plan.identity()
    spec = load((root / "proof.json").read_bytes())
    canonical.write_canonical_json(root / "topped-plan.json", changed.model_dump(mode="json"))
    spec.update(plan=str(root / "topped-plan.json"), plan_digest=changed.identity())
    canonical.write_canonical_json(root / "topped-proof.json", spec)
    with pytest.raises(C05Error):
        with open_gate(root / "topped-proof.json", allow_authored=True):
            pass  # The old completion is not a C05 result for the top-up plan.
    with gate_of(flow) as gate:
        original = load((root / "plans/p0001.json").read_bytes())
        assert original["data_root"] == str(data_root)
        target_copy = tmp_path / "original-file"
        shutil.copy2(target, target_copy)
        try:
            target.write_bytes(target.read_bytes() + line)
            with pytest.raises(C05Error, match="changed since C05"):
                count_tokens(
                    gate,
                    flow["root"] / "tokenizer",
                    tmp_path / "c",
                    ISSUER,
                    KEY.encode(),
                    scratch=tmp_path,
                )
        finally:
            shutil.copy2(target_copy, target)
        verify_freeze(flow["root"] / "freeze/freeze.json", gate)


def protected_v3(flow: dict[str, Any]) -> tuple[FinalExclusionReceipt, BenchmarkClaimBinding]:
    """Synthetic protected re-signing of the generated schema-3 receipt (test trust only)."""
    receipt = FinalExclusionReceipt.load(flow["root"] / "final-receipt.json")
    receipt = sign_receipt(replace(receipt, mode="protected", signature=None), KEY.encode())
    binding = BenchmarkClaimBinding(**load((flow["root"] / "claim-binding.json").read_bytes()))
    return receipt, binding


def test_official_claim_binds_selected_membership(flow: dict[str, Any]) -> None:
    receipt, binding = protected_v3(flow)
    verify_benchmark_claim(receipt, binding, TRUST)
    development = FinalExclusionReceipt.load(flow["root"] / "final-receipt.json")
    verify_receipt(development, TRUST, policy="development")
    with pytest.raises(ReceiptValidationError, match="development"):
        verify_benchmark_claim(development, binding, TRUST)
    for field in (
        "selection_digest",  # Same global C05, different training selection.
        "freeze_digest",
        "tokenizer_fingerprint",
        "quota_sha256",
        "source_seals_digest",
        "output_membership_digest",  # A changed selected record changes this digest.
    ):
        with pytest.raises(ReceiptValidationError):
            verify_benchmark_claim(receipt, replace(binding, **{field: "0" * 64}), TRUST)


def test_global_kept_membership_receipt_cannot_support_claims(flow: dict[str, Any]) -> None:
    receipt, binding = protected_v3(flow)
    assert receipt.c05_binding is not None
    v2 = replace(
        receipt,
        schema_version="2",
        selection_binding=None,
        output_membership_digest=receipt.c05_binding["membership_sha256"],
        signature=None,
    )
    v2 = sign_receipt(v2, KEY.encode())
    verify_receipt(v2, TRUST)
    kept_binding = replace(binding, output_membership_digest=v2.output_membership_digest)
    with pytest.raises(ReceiptValidationError, match="schema-3"):
        verify_benchmark_claim(v2, kept_binding, TRUST)
    assert receipt.selection_binding is not None
    tampered = dict(receipt.selection_binding)
    tampered["counts_digest"] = "0" * 64
    forged = sign_receipt(replace(receipt, selection_binding=tampered, signature=None), b"x" * 40)
    with pytest.raises(ReceiptValidationError):
        verify_benchmark_claim(forged, binding, TRUST)


def test_parallel_tokenization_transports_selection_into_freeze(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.datasets.shards import ShardedJsonlWriter
    from xlm.data.exclusion.freeze import freeze
    from xlm.data.exclusion.selection import SelectionGate, iter_plan_documents
    from xlm.data.parallel_tokens import tokenize_to_single_shard

    root = flow["root"]
    component = "common_pile_prose"
    with gate_of(flow) as gate:
        selection = SelectionGate(gate, root / "selection")
        writer = ShardedJsonlWriter(
            tmp_path / "canonical", dataset_id="authored", target_shard_bytes=1
        )
        for _, doc in iter_plan_documents(gate, components={component}):
            if selection.selected(doc.doc_id):
                writer.write_line(canonical.canonical_bytes(doc.to_dict()).decode(), doc.doc_id)
        writer.finish()
    shards = tmp_path / "shards"
    shutil.copytree(root / "shards", shards, ignore=shutil.ignore_patterns(component))
    for workers in (1, 2):
        target = tmp_path / f"parallel-{workers}"
        tokenize_to_single_shard(
            tmp_path / "canonical",
            root / "tokenizer",
            target,
            source_id=component,
            shard_id=component,
            pool_hash=load((root / "selection/selection.json").read_bytes())["digest"],
            workers=workers,
            c05_proof=flow["proof"],
            c05_selection=root / "selection",
        )
    assert (tmp_path / "parallel-1/tokens.bin").read_bytes() == (
        tmp_path / "parallel-2/tokens.bin"
    ).read_bytes()
    shutil.copytree(tmp_path / "parallel-2", shards / component)
    with gate_of(flow) as gate:
        envelope = freeze(
            gate,
            flow["proof"],
            root / "selection",
            shards,
            root / "tokenizer",
            tmp_path / "freeze",
            ISSUER,
            KEY.encode(),
        )
    assert envelope["payload"]["valid_targets"] == 10_000
    assert envelope["payload"]["shards"][component]["valid_targets"] == 500


def test_frozen_execution_dry_run_recompiles_exposure_through_freeze(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from test_final_acceptance import tiny_plan
    from xlm.cli.main import app as root_app
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    root = flow["root"]
    tokenizer = ByteLevelBPETokenizer.load(root / "tokenizer")
    training = tiny_plan()
    training["model"]["vocab_size"] = tokenizer.vocab_size
    training["training"]["budget"]["max_valid_targets"] = 10_000
    training["training"]["schedule"]["horizon_valid_targets"] = 10_000
    data = load((root / "freeze/training-data.json").read_bytes())
    # The frozen recipe's seeds are part of its identity; the run must use them.
    training["training"]["data_seed"] = data["mixture"]["data_seed"]
    training["training"]["init_seed"] = data["mixture"]["model_seed"]
    training["data"] = {**data, "tokenizer_artifact": str(root / "tokenizer")}
    path = tmp_path / "training.json"
    canonical.write_canonical_json(path, training)
    result = CliRunner().invoke(root_app, ["train", str(path), "--dry-run"])
    assert result.exit_code == 0, (result.output, result.exception)
    # A different budget no longer matches the frozen exposure plan.
    training["training"]["budget"]["max_valid_targets"] = 9_999
    canonical.write_canonical_json(tmp_path / "other.json", training)
    refused = CliRunner().invoke(root_app, ["train", str(tmp_path / "other.json"), "--dry-run"])
    assert refused.exit_code != 0
