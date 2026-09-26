"""Isolated authored adversaries; documented weakness assertions are NOT certifications."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import torch

from p35_eval_support import CallbackEvaluator, reload, run_all
from test_p35_readiness_runtime import (
    BUDGET,
    FULL,
    QUICK,
    SEARCH,
    attempts,
    c0_trainer,
    endpoint_trainer,
    payload_trainer,
    recovery_trainer,
)
from xlm.data.sampling.update_payload import (
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
    canonical_update,
)
from xlm.evaluation.outcome import EvaluationOutcome
from xlm.evaluation.receipts import AttemptOutcome
from xlm.training.trainer import (
    InitialEvaluationBarrierError,
    NonFiniteGradientError,
    RequiredEvaluationIncompleteError,
)


def test_A1_resume_does_not_refund_exhausted_attempts(tmp_path: Path) -> None:
    first = c0_trainer(tmp_path, SEARCH, {1, 2, 3, 4})
    with pytest.raises(InitialEvaluationBarrierError):
        first.train()
    checkpoint = first.checkpoints.checkpoint_dir(first.checkpoints.ledger.last_good().artifact_id)
    second = c0_trainer(tmp_path, SEARCH, set())
    reload(second, checkpoint)
    with pytest.raises(InitialEvaluationBarrierError):
        second.train_step()
    assert attempts(second, "search_benchmark@0") == [(n, "failed") for n in (1, 2, 3)]
    assert second.step == 0 and second.science.lr_receipts == []


def test_A2_endpoint_is_durable_before_required_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = endpoint_trainer(tmp_path, {2, 3, 4})
    original = trainer.require_required_evaluations
    observed = []

    def checked() -> None:
        record = trainer.checkpoints.ledger.last_good()
        assert record.actual_committed_targets == BUDGET
        trainer.checkpoint_manager.store.verify_artifact(
            trainer.checkpoints.checkpoint_dir(record.artifact_id)
        )
        observed.append(record.artifact_id)
        original()

    monkeypatch.setattr(trainer, "require_required_evaluations", checked)
    with pytest.raises(RequiredEvaluationIncompleteError):
        trainer.train()
    assert observed


def test_A3_partial_pins_recovery_state(tmp_path: Path) -> None:
    calls = [0]

    def partial_once(model: Any) -> Any:
        calls[0] += 1
        if calls[0] == 2:
            return EvaluationOutcome(AttemptOutcome.PARTIAL, {}, {"complete": False})
        return None

    trainer = recovery_trainer(tmp_path, CallbackEvaluator(QUICK, partial_once))
    run_all(trainer)
    event = trainer.evaluation.ledger.records["quick_lm@8"]
    assert event.status.value == "partial"
    deps = trainer.checkpoints.evaluation_dependencies(trainer)
    assert deps[event.due["model_state_digest"]] == ["quick_lm@8"]
    record = next(r for r in trainer.checkpoints.ledger.published() if r.planned_thresholds == [8])
    assert trainer.checkpoints.checkpoint_dir(record.artifact_id).is_dir()


def authored_update(tmp_path: Path) -> list[Any]:
    from p35_m5_support import write_sources
    from test_p35_readiness_payload import updates

    return updates(write_sources(tmp_path / "shards"), 8, count=1)[0]


def test_groupings_and_all_semantic_mutations(tmp_path: Path) -> None:
    from test_p35_readiness_payload import flat_rows, mutate, regroup

    batches = authored_update(tmp_path)
    rows = flat_rows(batches)
    assert len(rows) >= 32
    chains = []
    for group in (1, 2, 4, 8, 16, 32):
        payload = canonical_from_microbatches(
            regroup(rows, group, batches[0].metadata["packing_mode"])
        )
        chain = UpdatePayloadChain()
        chain.stage(
            step=1,
            committed_before=0,
            valid_targets=payload.valid_targets,
            payload=payload.digest(),
        )
        chain.commit()
        chains.append(chain.head)
    assert len(set(chains)) == 1
    base = canonical_from_microbatches(batches).digest()
    for field in (
        "input_ids",
        "labels",
        "loss_mask",
        "position_ids",
        "attention_mask",
        "segment_ids",
        "source_attribution",
        "doc_ids",
        "lineage_ids",
        "byte_spans",
        "token_offsets",
        "packing_mode",
    ):
        assert canonical_from_microbatches(mutate(batches, field)).digest() != base, field
    rows[0], rows[1] = rows[1], rows[0]
    assert (
        canonical_from_microbatches(regroup(rows, 8, batches[0].metadata["packing_mode"])).digest()
        != base
    )


def test_A4_shifted_provenance_changes_receipt_and_ipc_digest(tmp_path: Path) -> None:
    from xlm.data.sampling.prefetch import _encode

    batches = authored_update(tmp_path)
    prepared = _encode(batches, 0, 0, None, "start", None, 0.0)
    provenance = replace(
        prepared.provenance, doc_ids=np.roll(prepared.provenance.doc_ids, 1, axis=0)
    )
    shifted = replace(prepared, provenance=provenance)
    assert shifted.compute_content_digest() != prepared.content_digest
    assert canonical_from_prepared(shifted).digest() != canonical_from_prepared(prepared).digest()


def test_documented_prepared_handoff_weakness(tmp_path: Path) -> None:
    """SURVIVING adversary: replacing consumed tensors is invisible to pending-only hashing."""
    from xlm.data.sampling.prefetch import _encode

    batches = authored_update(tmp_path)
    pending = _encode(batches, 0, 0, None, "start", None, 0.0)
    actual = pending.to_microbatches()
    original = canonical_update(SimpleNamespace(pending_update=pending), actual).digest()
    actual[0].input_ids = actual[0].input_ids.clone()
    actual[0].input_ids[0, 0] += 1
    assert canonical_update(SimpleNamespace(pending_update=pending), actual).digest() == original
    assert actual[0].input_ids[0, 0] != pending.input_ids[0, 0]


def test_A5_absent_attention_differs_from_explicit_ones(tmp_path: Path) -> None:
    batches = authored_update(tmp_path)
    missing, explicit = copy.deepcopy(batches), copy.deepcopy(batches)
    for left, right in zip(missing, explicit, strict=True):
        left.metadata.pop("input_attention_mask")
        right.metadata["input_attention_mask"] = np.ones_like(right.loss_mask, dtype=bool)
    assert any(not value for mb in missing for row in mb.loss_mask for value in row)
    assert (
        canonical_from_microbatches(missing).digest()
        != canonical_from_microbatches(explicit).digest()
    )


def test_A6_lr_without_payload_is_rejected(tmp_path: Path) -> None:
    from xlm.config.science import ScientificPolicyError

    trainer = payload_trainer(tmp_path)
    trainer.train_step()
    saved = trainer.science.to_checkpoint()
    saved["update_payloads"] = UpdatePayloadChain().to_dict()
    with pytest.raises(ScientificPolicyError, match="committed LR"):
        trainer.science._saved_update_payloads(saved)


def test_A7_documented_chain_beyond_data_is_accepted_on_load(tmp_path: Path) -> None:
    """SURVIVING adversary: a self-consistent LR/chain pair is not checked against data C."""
    trainer = payload_trainer(tmp_path)
    trainer.science.update_payloads.stage(
        step=1, committed_before=0, valid_targets=16, payload="a" * 64
    )
    trainer.science.update_payloads.commit()
    trainer.science.record_lr([1, 0, 16, 16, [0.001]])
    checkpoint = trainer._save_checkpoint("authored_inconsistent")
    resumed = payload_trainer(tmp_path / "resumed")
    reload(resumed, checkpoint)
    assert resumed.committed_valid_targets == 0
    assert resumed.batcher.get_state()["committed_valid_targets"] == 0
    assert len(resumed.science.update_payloads.rows) == 1


@pytest.mark.parametrize("stage", ["forward", "nonfinite", "optimizer", "data_commit"])
def test_failure_never_commits_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    trainer = payload_trainer(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("authored fault")

    if stage == "forward":
        monkeypatch.setattr(trainer.model, "forward", fail)
    elif stage == "nonfinite":
        monkeypatch.setattr("xlm.optimizers.clipping.gradients_are_finite", lambda p: False)
    elif stage == "optimizer":
        monkeypatch.setattr(trainer.optimizer, "step", fail)
    else:
        monkeypatch.setattr(trainer.batcher, "commit", fail)
    with pytest.raises(NonFiniteGradientError if stage == "nonfinite" else RuntimeError):
        trainer.train_step()
    assert trainer.science.lr_receipts == []
    assert trainer.science.update_payloads.rows == []
    assert trainer.science.update_payloads._staged is None


def test_native_exact_rescore_satisfies_required_endpoint(tmp_path: Path) -> None:
    from test_p35_checkpoint_rescore import evaluator
    from test_p35_readiness_runtime import trainer_with_policy

    trainer = trainer_with_policy(
        tmp_path / "run",
        evaluators={QUICK: evaluator(tmp_path / "inputs", QUICK, fail=True)},
        thresholds={"quick_lm": [8, BUDGET]},
        milestones=[0, BUDGET],
    )
    trainer.train()
    history = trainer.evaluation._store.history("quick_lm@8")
    assert [(a["number"], a["status"]) for a in history] == [(1, "failed"), (2, "complete")]
    assert history[-1]["route"] == "retained_exact_checkpoint_rescore_v1"
    assert trainer.evaluation.completeness().complete


def test_documented_receipt_commit_failure_leaves_unpoisoned_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = payload_trainer(tmp_path)

    def fail() -> Any:
        raise RuntimeError("authored receipt failure")

    monkeypatch.setattr(trainer.science.update_payloads, "commit", fail)
    with pytest.raises(RuntimeError, match="receipt failure"):
        trainer.train_step()
    assert trainer.committed_valid_targets == 16
    assert len(trainer.science.lr_receipts) == 1
    assert trainer.science.update_payloads.rows == []
    assert not trainer._update_in_doubt
    assert trainer.science.to_checkpoint()["update_payloads"]["rows"] == []


@pytest.mark.parametrize("saved_receipt", [False, True])
def test_both_presence_mismatches_refuse_before_restore(
    tmp_path: Path, saved_receipt: bool
) -> None:
    from xlm.training.checkpoint import IncompatibleCheckpointError

    first = payload_trainer(tmp_path / "first")
    if not saved_receipt:
        first.science.update_payloads = None
    first.train_step()
    checkpoint = first.checkpoints.checkpoint_dir(first.checkpoints.ledger.last_good().artifact_id)
    other = payload_trainer(tmp_path / "other")
    if saved_receipt:
        other.science.update_payloads = None
    model = {k: v.clone() for k, v in other.model.state_dict().items()}
    data = copy.deepcopy(other.batcher.get_state())
    with pytest.raises(IncompatibleCheckpointError, match="presence"):
        reload(other, checkpoint)
    assert other.batcher.get_state() == data
    assert all(torch.equal(model[k], value) for k, value in other.model.state_dict().items())


def test_real_process_producer_receipt_matches_direct(tmp_path: Path) -> None:
    from test_prefetch import make_batcher, prefetcher, shards

    readers = shards.__wrapped__(tmp_path)
    direct = make_batcher(readers)
    with prefetcher(readers) as producer:
        for remaining in (205, 109, 13):
            actual = producer.next_step_microbatches(remaining)
            expected = direct.next_step_microbatches(remaining)
            assert all(isinstance(m.input_ids, torch.Tensor) for m in actual + expected)
            assert (
                canonical_update(producer, actual).digest()
                == canonical_from_microbatches(expected).digest()
            )
            for index, mb in enumerate(expected):
                provenance = producer.pending_update.microbatch_provenance(index)
                assert provenance["target_doc_ids"] == mb.metadata["target_doc_ids"]
            producer.commit()
            direct.commit()
        assert producer.get_state() == direct.get_state()
    direct.close()


@pytest.mark.parametrize("kind", ["invalid_full", "partial_search", "planned_only"])
def test_c0_closed_barrier_has_no_lr_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    trainer = c0_trainer(tmp_path, None, set())
    if kind == "planned_only":
        monkeypatch.setattr(trainer.evaluation, "record_crossings", lambda t: None)
    else:
        tier = FULL if kind == "invalid_full" else SEARCH

        def bad(model: Any) -> Any:
            if kind == "invalid_full":
                return EvaluationOutcome(AttemptOutcome.COMPLETE, {"ce": float("nan")}, {})
            return EvaluationOutcome(AttemptOutcome.PARTIAL, {}, {"complete": False})

        trainer.evaluation.evaluators[tier] = CallbackEvaluator(tier, bad, label=tier.value)
    with pytest.raises(InitialEvaluationBarrierError):
        trainer.train_step()
    assert trainer.step == 0 and trainer.science.lr_receipts == []
    assert trainer.committed_valid_targets == 0


@pytest.mark.parametrize("position", ["after_checkpoint", "after_failed_attempt"])
def test_c0_crash_retains_attempt_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    first = c0_trainer(tmp_path, SEARCH, {1})

    def die(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("authored runner death")

    with monkeypatch.context() as isolated:
        if position == "after_checkpoint":
            isolated.setattr(first.evaluation, "run_pending", die)
        else:
            isolated.setattr(first.evaluation, "settle_required", die)
        with pytest.raises(KeyboardInterrupt):
            first.train_step()
    assert first.step == 0 and first.science.lr_receipts == []
    cp = first.checkpoints.checkpoint_dir(first.checkpoints.ledger.last_good().artifact_id)
    second = c0_trainer(tmp_path, SEARCH, set())
    reload(second, cp)
    assert second.train_step() is not None
    expected = (
        [(1, "complete")] if position == "after_checkpoint" else [(1, "failed"), (2, "complete")]
    )
    assert attempts(second, "search_benchmark@0") == expected


def test_historical_chain_tamper_rejected_before_restore(tmp_path: Path) -> None:
    import hashlib

    from xlm.training.checkpoint import IncompatibleCheckpointError

    first = payload_trainer(tmp_path / "first")
    first.train_step()
    cp = first.checkpoints.checkpoint_dir(first.checkpoints.ledger.last_good().artifact_id)
    science_path = cp / "science.json"
    state = json.loads(science_path.read_text(encoding="utf-8"))
    state["update_payloads"]["rows"][0][3] = "f" * 64
    science_path.write_text(json.dumps(state), encoding="utf-8")
    manifest_path = cp / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "science.json":
            entry["size_bytes"] = science_path.stat().st_size
            entry["sha256"] = hashlib.sha256(science_path.read_bytes()).hexdigest()
    manifest["content_hash"] = hashlib.sha256(
        json.dumps(
            sorted(manifest["files"], key=lambda entry: entry["path"]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    first.checkpoint_manager.store.verify_artifact(cp)
    other = payload_trainer(tmp_path / "other")
    weights = {k: v.clone() for k, v in other.model.state_dict().items()}
    with pytest.raises(IncompatibleCheckpointError, match="chain link"):
        reload(other, cp)
    assert all(torch.equal(weights[k], v) for k, v in other.model.state_dict().items())


def test_evaluator_payload_chain_mutation_is_currently_unprotected(tmp_path: Path) -> None:
    """SURVIVING microbatch-study adversary: new receipt is absent from the M2 guard."""
    trainer = c0_trainer(tmp_path, None, set())
    trainer.science.update_payloads = UpdatePayloadChain()

    def mutate_live(model: Any) -> None:
        trainer.science.update_payloads.stage(
            step=1, committed_before=0, valid_targets=16, payload="b" * 64
        )
        trainer.science.update_payloads.commit()

    trainer.evaluation.evaluators[SEARCH] = CallbackEvaluator(
        SEARCH, mutate_live, label=SEARCH.value
    )
    trainer._evaluate_boundary()
    assert trainer.evaluation.ledger.records["search_benchmark@0"].status.value == "complete"
    assert trainer.step == 0 and len(trainer.science.update_payloads.rows) == 1
    assert not trainer._evaluation_compromised


def test_scaler_skip_discards_staged_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = payload_trainer(tmp_path)
    trainer.scaler = torch.amp.GradScaler("cpu", init_scale=16)
    monkeypatch.setattr("xlm.optimizers.clipping.gradients_are_finite", lambda p: False)
    assert trainer.train_step() is None
    assert trainer._last_step_skipped
    assert trainer.step == trainer.committed_valid_targets == 0
    assert trainer.science.lr_receipts == trainer.science.update_payloads.rows == []
    assert trainer.science.update_payloads._staged is None


def test_schema_edge_representations(tmp_path: Path) -> None:
    from test_p35_readiness_payload import flat_rows, regroup
    from xlm.data.sampling.update_payload import PayloadReceiptError

    batches = authored_update(tmp_path)
    for mb in batches:
        mb.metadata["target_doc_ids"] = [
            ["文書-é" if i % 2 else "" for i in range(len(row))]
            for row in mb.metadata["target_doc_ids"]
        ]
    base = canonical_from_microbatches(batches).digest()
    rows = flat_rows(batches)
    for group in (1, 2, 4, 8, 16, 32):
        assert (
            canonical_from_microbatches(
                regroup(rows, group, batches[0].metadata["packing_mode"])
            ).digest()
            == base
        )
    represented = copy.deepcopy(batches)
    for mb in represented:
        mb.input_ids = np.asarray(mb.input_ids, dtype=np.int32)
        mb.labels = np.asarray(mb.labels, dtype=np.int64)
        mb.loss_mask = np.asarray(mb.loss_mask, dtype=bool)
    assert canonical_from_microbatches(represented).digest() == base
    signed = copy.deepcopy(batches)
    signed[0].metadata["target_token_offsets"][0][0] = -2
    assert canonical_from_microbatches(signed).digest() != base
    with pytest.raises(PayloadReceiptError):
        canonical_from_microbatches([])


def test_duplicate_compact_strings_are_a_documented_representation_limitation(
    tmp_path: Path,
) -> None:
    """Equal decoded values can differ in digest; stock _encode emits unique strings."""
    from xlm.data.sampling.prefetch import _encode

    batches = authored_update(tmp_path)
    prepared = _encode(batches, 0, 0, None, "start", None, 0.0)
    provenance = prepared.provenance
    codes = provenance.doc_ids.copy()
    old = int(codes[0, 0])
    codes[0, 0] = len(provenance.strings)
    duplicate = replace(
        provenance, strings=(*provenance.strings, provenance.strings[old]), doc_ids=codes
    )
    alias = replace(prepared, provenance=duplicate)
    assert alias.microbatch_provenance(0) == prepared.microbatch_provenance(0)
    assert canonical_from_prepared(alias).digest() != canonical_from_prepared(prepared).digest()


def test_small_stat_fixture_reports_source_and_both_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data import input_limits
    from xlm.experiments.science_pilot import Findings, check_input_bytes

    assert input_limits.MAX_FROZEN_SHARD_INPUT_BYTES == 2**31
    assert input_limits.MAX_AGGREGATE_FROZEN_INPUT_BYTES == 2**31
    source = tmp_path / "authored"
    source.mkdir()
    (source / "tokens.bin").write_bytes(bytes(64))
    # Lower caps only in this isolated fixture; never enlarge production limits.
    monkeypatch.setattr(input_limits, "MAX_FROZEN_SHARD_INPUT_BYTES", 32)
    monkeypatch.setattr(input_limits, "MAX_AGGREGATE_FROZEN_INPUT_BYTES", 48)
    findings = Findings()
    report = check_input_bytes({"data": {"sources": {"authored": str(source)}}}, findings)
    assert report["per_source"]["authored"]["shard_bytes"] == 64
    assert report["aggregate_bytes"] == 64
    assert report["caps"]["per_shard_bytes"] == 32
    assert report["caps"]["aggregate_bytes"] == 48
    assert len(findings.blockers) == 2
    print(
        json.dumps(
            {
                "fixture_only_lowered_caps": True,
                "report": report,
                "blockers": [b.to_dict() for b in findings.blockers],
            }
        )
    )
