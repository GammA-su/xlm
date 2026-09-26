"""Independent authored adversaries; scratch-only injections, never corpus data."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import torch

from p35_eval_support import reload, run_all
from test_p35_hardening_runtime import (
    checkpoint_at,
    guard_report,
    guarded_trainer,
    other_policy,
    published,
    receipt_trainer,
    reseal,
    snapshot,
    unchanged,
)
from test_p35_m4_evidence import publish_run
from test_prefetch import make_batcher, shards  # noqa: F401
from xlm.comparison.science_evidence import EvidenceError, extract_run_evidence
from xlm.data.sampling.prefetch import _encode
from xlm.data.sampling.update_payload import (
    PayloadReceiptError,
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
    chain_digest,
)
from xlm.training.checkpoint import IncompatibleCheckpointError
from xlm.training.trainer import RecoveryRequiredError, ScienceReceiptCommitError


def test_A1_after_binding_mutation_is_a_demonstrated_trust_boundary(tmp_path: Path) -> None:
    trainer = receipt_trainer(tmp_path)
    captured: list[Any] = []
    original_take = trainer.batcher.next_step_microbatches

    def take(**kwargs: Any) -> Any:
        batches = original_take(**kwargs)
        captured.extend(batches)
        return batches

    trainer.batcher.next_step_microbatches = take
    chain = trainer.science.update_payloads
    original_stage = chain.stage
    before: list[str] = []

    def stage(**kwargs: Any) -> None:
        original_stage(**kwargs)
        before.append(canonical_from_microbatches(captured).digest())
        captured[0].input_ids[0, 0] = (int(captured[0].input_ids[0, 0]) + 1) % 260

    chain.stage = stage
    observed: list[torch.Tensor] = []
    forward = trainer.exec_model

    def spy(**kwargs: Any) -> Any:
        observed.append(kwargs["input_ids"].clone())
        return forward(**kwargs)

    trainer.exec_model = spy
    trainer.train_step()
    assert len(chain.rows) == 1 and observed
    assert chain.rows[0][3] == before[0]
    assert chain.rows[0][3] != canonical_from_microbatches(captured).digest()
    assert torch.equal(observed[0], captured[0].input_ids)


def rechain(science: dict[str, Any]) -> dict[str, Any]:
    raw = science["update_payloads"]
    head = raw["genesis"]
    for row in raw["rows"]:
        head = chain_digest(head, *row[:4])
        row[4] = head
    raw["head"] = head
    return science


@pytest.mark.parametrize("attack", ["A2_ahead", "A2_behind", "A2_step", "A2_partial", "A7", "A8"])
def test_resealed_history_refuses_before_restore(tmp_path: Path, attack: str) -> None:
    first = receipt_trainer(tmp_path / "first", budget=37)
    run_all(first)
    checkpoint = checkpoint_at(first, 37)

    def damage(science: dict[str, Any]) -> dict[str, Any]:
        lr = science["lr_receipts"]["rows"]
        rows = science["update_payloads"]["rows"]
        if attack == "A7":
            rows.append(copy.deepcopy(rows[-1]))
            lr.append(copy.deepcopy(lr[-1]))
            rows[-1][0] += 1
            lr[-1][0] += 1
        else:  # final C still 37, an intermediate C is wrong in both histories
            rows[1][1] += 1
            lr[1][1] += 1
            lr[1][3] += 1
        return rechain(science)

    def data_delta(data: dict[str, Any]) -> dict[str, Any]:
        data["committed_valid_targets"] += -16 if attack == "A2_ahead" else 16
        return data

    def meta_delta(meta: dict[str, Any]) -> dict[str, Any]:
        key = "step" if attack == "A2_step" else "committed_valid_targets"
        meta[key] += 1
        return meta

    if attack in ("A7", "A8"):
        edits = {"science.json": damage}
    elif attack in ("A2_ahead", "A2_behind"):
        edits = {"data_state.json": data_delta}
    else:
        edits = {"checkpoint_meta.json": meta_delta}
    reseal(checkpoint, edits)
    child = receipt_trainer(tmp_path / "child", budget=37)
    before = snapshot(child)
    with pytest.raises(IncompatibleCheckpointError):
        reload(child, checkpoint)
    assert unchanged(child, before)
    assert child.science.update_payloads.rows == []


def test_A3_fault_inside_joint_commit_requires_recovery(tmp_path: Path) -> None:
    trainer = receipt_trainer(tmp_path / "run")
    trainer.train_step()
    prior = checkpoint_at(trainer, 16)
    names = published(trainer)
    chain = trainer.science.update_payloads

    def fault() -> None:
        assert trainer.batcher.get_state()["committed_valid_targets"] == 32
        assert len(trainer.science.lr_receipts) == 2 and len(chain.rows) == 1
        raise OSError("independent receipt authority fault")

    chain.commit = fault
    with pytest.raises(ScienceReceiptCommitError, match="poisoned") as exc:
        trainer.train_step()
    assert isinstance(exc.value.__cause__, OSError)
    for operation in (trainer.train_step, lambda: trainer.save_terminal_checkpoint("final")):
        with pytest.raises(RecoveryRequiredError):
            operation()
    assert trainer._update_in_doubt and published(trainer) == names
    resumed = receipt_trainer(tmp_path / "resumed")
    reload(resumed, prior)
    run_all(resumed)
    assert resumed.committed_valid_targets == 48


def test_A4_evaluator_changes_an_existing_staged_receipt(tmp_path: Path) -> None:
    def mutate(t: Any, model: Any) -> None:
        t.science.update_payloads._staged[3] = "b" * 64

    trainer = guarded_trainer(tmp_path, mutate)
    # Stage immediately before guard capture, at the evaluation boundary.
    from xlm.training.evaluation import TrainingStateGuard

    original = TrainingStateGuard.capture
    with pytest.MonkeyPatch.context() as patch:

        def capture(guard: Any) -> None:
            guard.trainer.science.update_payloads.stage(
                step=2, committed_before=16, valid_targets=16, payload="a" * 64
            )
            original(guard)

        patch.setattr(TrainingStateGuard, "capture", capture)
        with pytest.raises(RecoveryRequiredError):
            trainer.train_step()
    assert "update_payload_receipt" in guard_report(trainer, "quick_lm@16")["changed"]
    assert trainer._evaluation_compromised


def test_guard_unserializable_receipt_must_poison(tmp_path: Path) -> None:
    def mutate(t: Any, model: Any) -> None:
        t.science.update_payloads._staged = [2, 16, 16, object()]

    trainer = guarded_trainer(tmp_path, mutate)
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()
    assert trainer._evaluation_compromised
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()


@pytest.mark.parametrize("alias", ["duplicate", "non_string", "out_of_table", "changed", "unique"])
def test_A5_alias_semantics(shards: Any, alias: str) -> None:  # noqa: F811
    source = make_batcher(shards)
    batches = source.next_step_microbatches(remaining_budget=96)
    # _encode requires the stock list-mode output.
    source.rollback()
    source.emit_tensors = False
    batches = source.next_step_microbatches(remaining_budget=96)
    pending = _encode(batches, 0, 1, 96, "authored", None, 0.0)
    reference = canonical_from_prepared(pending).digest()
    provenance = pending.provenance
    if alias == "unique":
        assert len(set(provenance.strings)) == len(provenance.strings)
        return
    if alias == "out_of_table":
        provenance.doc_ids[0, 0] = len(provenance.strings)
    else:
        strings = list(provenance.strings)
        code = int(provenance.doc_ids[0, 0])
        if alias == "duplicate":
            strings.append(strings[code])
            provenance.doc_ids[0, 0] = len(strings) - 1
        elif alias == "non_string":
            strings[code] = 17
        else:
            strings[code] += "_different"
        object.__setattr__(provenance, "strings", tuple(strings))
    if alias == "changed":
        assert canonical_from_prepared(pending).digest() != reference
    else:
        with pytest.raises(PayloadReceiptError):
            canonical_from_prepared(pending)


def test_A6_changed_policy_fork_pre_readiness(tmp_path: Path) -> None:
    parent = receipt_trainer(tmp_path / "parent", receipt=False)
    parent.train_step()
    child = receipt_trainer(tmp_path / "child")
    other_policy(child)
    before = snapshot(child)
    with pytest.raises(IncompatibleCheckpointError, match="pre-readiness"):
        reload(child, checkpoint_at(parent, 16), is_fork=True)
    assert unchanged(child, before)


def test_evidence_rejects_wrong_lr_schedule_counter(tmp_path: Path) -> None:
    chain = UpdatePayloadChain()
    for step in range(1, 5):
        chain.stage(step=step, committed_before=(step - 1) * 16, valid_targets=16, payload="a" * 64)
        chain.commit()
    checkpoint = publish_run(
        tmp_path, "independent", (101, 10001, 20260918), update_payloads=chain.to_dict()
    )

    def damage(science: dict[str, Any]) -> dict[str, Any]:
        science["lr_receipts"]["rows"][1][3] += 1
        return science

    reseal(checkpoint, {"science.json": damage})
    with pytest.raises(EvidenceError, match="LR|history|schedule"):
        extract_run_evidence(checkpoint, parameter_counter=lambda _: 6768)


@pytest.mark.parametrize("targets", [1_000_000_000, 3_000_000_000, 6_000_000_000])
def test_planned_history_guard_and_evidence_fit(tmp_path: Path, targets: int) -> None:
    from xlm.comparison.science_evidence import _update_boundaries
    from xlm.training.evaluation import training_state_fingerprint

    trainer = receipt_trainer(tmp_path)
    chain = trainer.science.update_payloads
    before = 0
    while before < targets:
        step = len(chain.rows) + 1
        valid = min(65536, targets - before)
        chain.stage(step=step, committed_before=before, valid_targets=valid, payload="a" * 64)
        chain.commit()
        trainer.science.lr_receipts.append([step, before, valid, before + valid, [1e-3, 1e-3]])
        before += valid
    # Fabricated histories prove bounded metadata handling only, no training claim.
    fingerprint = training_state_fingerprint(trainer)
    assert "update_payload_receipt" in fingerprint
    kwargs = (
        {"receipted": True}
        if "receipted" in __import__("inspect").signature(_update_boundaries).parameters
        else {}
    )
    digest, count = _update_boundaries(trainer.science.lr_receipts, targets, **kwargs)
    assert len(digest) == 64 and count == (targets + 65535) // 65536
