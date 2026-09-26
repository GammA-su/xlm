"""P35 microbatch evidence hardening through the real Trainer, checkpoints and evaluator.

Tiny authored science-v1 trainers (1 layer, 16 wide) over authored tokens and
authored two-source shards, through the synchronous ``MixtureBatcher`` and the
real spawned P34 ``process_depth1`` producer. SYNTHETIC fixtures only; nothing
here is the pilot, a B8/B16/B32 study or a measurement of quality.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from p35_eval_support import (  # noqa: E402
    CallbackEvaluator,
    build_trainer,
    fixture_plan,
    reload,
    run_all,
)
from test_prefetch import CONTEXT, GLOBAL, make_batcher, prefetcher, shards  # noqa: E402, F401
from xlm.config.science import ScientificPolicy  # noqa: E402
from xlm.data.sampling import update_payload as up  # noqa: E402
from xlm.data.sampling.update_payload import (  # noqa: E402
    PayloadReceiptError,
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
    chain_digest,
)
from xlm.evaluation.cadence import AUTHORED_FIXTURE, EventTier, build_checkpoint_plan  # noqa: E402
from xlm.training.checkpoint import (  # noqa: E402
    CheckpointError,
    IncompatibleCheckpointError,
)
from xlm.training.science import ScientificRuntimeError  # noqa: E402
from xlm.training.trainer import (  # noqa: E402
    RecoveryRequiredError,
    ScienceReceiptCommitError,
)

QUICK = EventTier.QUICK_LM
BATCH = 16


# ---------------------------------------------------------------- helpers


def receipt_trainer(
    root: Path,
    *,
    budget: int = 48,
    milestones: list[int] | None = None,
    receipt: bool = True,
    run_id: str = "hard_run",
    evaluators: dict[EventTier, Any] | None = None,
    plan: Any = None,
) -> Any:
    """A tiny science-v1 trainer with an absolute checkpoint cadence and optional receipt."""
    trainer = build_trainer(
        root,
        budget=budget,
        evaluators=evaluators,
        plan=plan,
        checkpoint_plan=build_checkpoint_plan(
            AUTHORED_FIXTURE,
            budget,
            fixture_milestones=milestones or [0, BATCH, budget],
            fixture_recovery=[],
        ),
        run_id=run_id,
    )
    if receipt:
        trainer.science.update_payloads = UpdatePayloadChain()
    return trainer


def checkpoint_at(trainer: Any, committed: int) -> Path:
    root = Path(trainer.checkpoint_manager.store.paths.root) / "checkpoints"
    [path] = [p for p in root.iterdir() if f"-t{committed}-" in p.name]
    return path


def published(trainer: Any) -> list[str]:
    root = Path(trainer.checkpoint_manager.store.paths.root) / "checkpoints"
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def snapshot(trainer: Any) -> dict[str, Any]:
    return {
        "weights": {k: v.clone() for k, v in trainer.model.state_dict().items()},
        "data": json.dumps(trainer.batcher.get_state(), sort_keys=True),
        "optimizer": len(trainer.optimizer.state),
    }


def unchanged(trainer: Any, before: dict[str, Any]) -> bool:
    now = trainer.model.state_dict()
    return (
        all(torch.equal(before["weights"][k], now[k]) for k in now)
        and json.dumps(trainer.batcher.get_state(), sort_keys=True) == before["data"]
        and len(trainer.optimizer.state) == before["optimizer"]
    )


def reseal(checkpoint: Path, edits: dict[str, Callable[[Any], Any]]) -> None:
    """Edit JSON files of a published checkpoint and recompute its manifest (authored tamper)."""
    manifest_path = checkpoint / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, edit in edits.items():
        path = checkpoint / name
        value = edit(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(value), encoding="utf-8")
        for entry in manifest["files"]:
            if entry["path"] == name:
                entry["size_bytes"] = path.stat().st_size
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["content_hash"] = hashlib.sha256(
        json.dumps(
            sorted(manifest["files"], key=lambda e: e["path"]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


class ForwardSpy:
    """Records the input ids every forward call actually received (CPU copies)."""

    def __init__(self, trainer: Any) -> None:
        self.inputs: list[Any] = []
        model = trainer.exec_model

        def call(**kwargs: Any) -> Any:
            self.inputs.append(kwargs["input_ids"].detach().cpu().clone())
            return model(**kwargs)

        trainer.exec_model = call


def with_microbatches(trainer: Any, change: Callable[[list[Any]], None]) -> None:
    """Alter the microbatches the batcher hands to the trainer, after it produced them."""
    original = trainer.batcher.next_step_microbatches

    def patched(remaining_budget: int | None = None) -> list[Any]:
        batches = original(remaining_budget=remaining_budget)
        change(batches)
        return batches

    trainer.batcher.next_step_microbatches = patched


@pytest.fixture
def producer_trainer(tmp_path: Path, shards: Any) -> Iterator[Any]:  # noqa: F811
    trainer = build_trainer(
        tmp_path / "producer", budget=3 * GLOBAL, global_batch=GLOBAL, context=CONTEXT
    )
    trainer.batcher = prefetcher(shards)  # real spawned process_depth1 producer, verified
    trainer.science.update_payloads = UpdatePayloadChain()
    try:
        yield trainer
    finally:
        trainer.batcher.close()


def direct_trainer(root: Path, shards: Any) -> Any:  # noqa: F811
    trainer = build_trainer(root, budget=3 * GLOBAL, global_batch=GLOBAL, context=CONTEXT)
    trainer.batcher = make_batcher(shards)
    trainer.science.update_payloads = UpdatePayloadChain()
    return trainer


# ---------------------------------------------------- consumed payload


def test_direct_consumed_tensors_are_the_receipt_source(tmp_path: Path, shards: Any) -> None:  # noqa: F811
    trainer = direct_trainer(tmp_path, shards)
    seen: list[list[Any]] = []
    with_microbatches(trainer, seen.append)
    spy = ForwardSpy(trainer)
    trainer.train_step()
    [batches] = seen
    assert (
        trainer.science.update_payloads.rows[0][3] == canonical_from_microbatches(batches).digest()
    )
    fed = [mb.input_ids for mb in batches if int(mb.loss_mask.sum()) > 0]
    assert len(spy.inputs) == len(fed)
    assert all(torch.equal(a, b) for a, b in zip(spy.inputs, fed, strict=True))


def test_producer_consumed_tensors_are_the_receipt_source(
    tmp_path: Path,
    producer_trainer: Any,
    shards: Any,  # noqa: F811
) -> None:
    trainer = producer_trainer
    seen: list[tuple[list[Any], Any]] = []
    with_microbatches(trainer, lambda b: seen.append((b, trainer.batcher.pending_update)))
    spy = ForwardSpy(trainer)
    run_all(trainer)
    direct = direct_trainer(tmp_path / "direct", shards)
    run_all(direct)
    chain, reference = trainer.science.update_payloads, direct.science.update_payloads
    assert len(chain.rows) == 3 and chain.to_dict() == reference.to_dict()
    fed = iter(spy.inputs)
    for (batches, pending), row in zip(seen, chain.rows, strict=True):
        assert row[3] == canonical_from_prepared(pending).digest()
        start = 0
        for mb, size in zip(batches, pending.rows, strict=True):
            assert np.array_equal(mb.input_ids.numpy(), pending.input_ids[start : start + size])
            if int(mb.loss_mask.sum()) > 0:
                assert torch.equal(next(fed), mb.input_ids)
            start += size


def replace_value(field: str) -> Callable[[list[Any]], None]:
    def change(batches: list[Any]) -> None:
        tensor = getattr(batches[0], field).clone()  # a detached replacement, same shape
        tensor[0, 1] += 1
        setattr(batches[0], field, tensor)

    return change


def refused_before_compute(trainer: Any, spy: ForwardSpy, match: str) -> None:
    before = snapshot(trainer)
    with pytest.raises(PayloadReceiptError, match=match):
        trainer.train_step()
    assert spy.inputs == [] and unchanged(trainer, before)
    assert trainer.science.update_payloads.rows == trainer.science.lr_receipts == []
    assert trainer.science.update_payloads.staged is None and trainer.committed_valid_targets == 0
    assert not trainer._update_in_doubt


@pytest.mark.parametrize("field", ["input_ids", "labels"])
def test_a_detached_replacement_consumed_tensor_is_refused(
    tmp_path: Path,
    producer_trainer: Any,
    shards: Any,  # noqa: F811
    field: str,
) -> None:
    trainer = producer_trainer
    original = trainer.batcher.next_step_microbatches
    with_microbatches(trainer, replace_value(field))
    spy = ForwardSpy(trainer)
    refused_before_compute(trainer, spy, f"microbatch 0 {field} differs")
    # Nothing was consumed: the producer regenerates the same update, which binds.
    trainer.batcher.next_step_microbatches = original
    trainer.train_step()
    direct = direct_trainer(tmp_path / "direct", shards)
    direct.train_step()
    assert trainer.science.update_payloads.rows == direct.science.update_payloads.rows


def test_provenance_correct_but_tensor_replaced_is_refused(producer_trainer: Any) -> None:
    trainer = producer_trainer

    def swap(batches: list[Any]) -> None:
        batches[1].labels = torch.full_like(batches[1].labels, 4)  # pending provenance intact

    with_microbatches(trainer, swap)
    refused_before_compute(trainer, ForwardSpy(trainer), "microbatch 1 labels differs")


def test_tensor_correct_but_provenance_row_shifted_is_refused(producer_trainer: Any) -> None:
    trainer = producer_trainer
    batcher = trainer.batcher

    def shift(batches: list[Any]) -> None:
        pending = batcher._pending
        rolled = np.roll(pending.provenance.doc_ids, 1, axis=0)
        batcher._pending = replace(pending, provenance=replace(pending.provenance, doc_ids=rolled))

    with_microbatches(trainer, shift)
    refused_before_compute(trainer, ForwardSpy(trainer), "producer seal")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="needs a CUDA device tensor")
def test_a_device_tensor_is_refused_without_synchronizing(
    producer_trainer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    trainer = producer_trainer

    def to_device(batches: list[Any]) -> None:
        batches[0].input_ids = batches[0].input_ids.to("cuda")

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the receipt must never synchronize a device")

    with_microbatches(trainer, to_device)
    monkeypatch.setattr(torch.cuda, "synchronize", forbidden)
    monkeypatch.setattr(torch.cuda.Stream, "synchronize", forbidden)
    refused_before_compute(trainer, ForwardSpy(trainer), "is on cuda")


# ---------------------------------------------- chain / LR / data consistency


def extend_history(science: dict[str, Any]) -> dict[str, Any]:
    """Astra A7: a self-consistent LR + chain row one update beyond the committed data."""
    chain = science["update_payloads"]
    step, before, valid = (int(v) for v in chain["rows"][-1][:3])
    step, before = step + 1, before + valid
    link = chain_digest(chain["head"], step, before, valid, "a" * 64)
    chain["rows"].append([step, before, valid, "a" * 64, link])
    chain["head"] = link
    science["lr_receipts"]["rows"].append([step, before, valid, before + valid, [1e-5]])
    return science


def drop_last(science: dict[str, Any]) -> dict[str, Any]:
    chain = science["update_payloads"]
    chain["rows"].pop()
    chain["head"] = chain["rows"][-1][4] if chain["rows"] else up.CHAIN_GENESIS
    science["lr_receipts"]["rows"].pop()
    return science


def plus(key: str, amount: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def edit(value: dict[str, Any]) -> dict[str, Any]:
        value[key] += amount
        return value

    return edit


TAMPERS: dict[str, tuple[dict[str, Callable[[Any], Any]], str]] = {
    "history_ahead_A7": ({"science.json": extend_history}, "ahead of the committed data"),
    "data_ahead": (
        {"data_state.json": plus("committed_valid_targets", BATCH)},
        "without its required receipts",
    ),
    "history_behind": ({"science.json": drop_last}, "without its required receipts"),
    "lr_ahead": (
        {
            "science.json": lambda s: {
                **s,
                "lr_receipts": {
                    **s["lr_receipts"],
                    "rows": [*s["lr_receipts"]["rows"], [3, 32, 16, 48, [1e-5]]],
                },
            }
        },
        "update by update",
    ),
    "meta_committed": (
        {"checkpoint_meta.json": plus("committed_valid_targets", 1)},
        "metadata records",
    ),
    "meta_step": ({"checkpoint_meta.json": plus("step", 1)}, "checkpoint step 3"),
}


@pytest.mark.parametrize("tamper", sorted(TAMPERS))
def test_inconsistent_history_is_refused_before_any_restore(tmp_path: Path, tamper: str) -> None:
    first = receipt_trainer(tmp_path / "first", milestones=[0, 2 * BATCH, 48])
    while first.committed_valid_targets < 2 * BATCH:
        first.train_step()
    checkpoint = checkpoint_at(first, 2 * BATCH)
    edits, message = TAMPERS[tamper]
    reseal(checkpoint, edits)
    first.checkpoint_manager.store.verify_artifact(checkpoint)  # checksums are valid
    other = receipt_trainer(tmp_path / "other", milestones=[0, 2 * BATCH, 48])
    before = snapshot(other)
    with pytest.raises(IncompatibleCheckpointError, match=message):
        reload(other, checkpoint)
    assert unchanged(other, before) and other.science.update_payloads.rows == []


def test_the_authored_A7_state_is_never_published(tmp_path: Path) -> None:
    trainer = receipt_trainer(tmp_path)
    trainer.science.update_payloads.stage(
        step=1, committed_before=0, valid_targets=16, payload="a" * 64
    )
    trainer.science.update_payloads.commit()
    trainer.science.record_lr([1, 0, 16, 16, [0.001]])
    names = published(trainer)
    with pytest.raises(CheckpointError, match="not coherent"):
        trainer._save_checkpoint("authored_inconsistent")
    assert published(trainer) == names


def test_a_consistent_checkpoint_and_a_final_partial_update_validate_exactly(
    tmp_path: Path,
) -> None:
    trainer = receipt_trainer(tmp_path / "run", budget=40, milestones=[0, 16, 40])
    run_all(trainer)
    rows = trainer.science.update_payloads.rows
    assert [r[:3] for r in rows] == [[1, 0, 16], [2, 16, 16], [3, 32, 8]]  # partial final update
    endpoint = checkpoint_at(trainer, 40)
    resumed = receipt_trainer(tmp_path / "resumed", budget=40, milestones=[0, 16, 40])
    reload(resumed, endpoint)
    assert resumed.science.update_payloads.to_dict() == trainer.science.update_payloads.to_dict()
    assert resumed.train_step() is None  # at budget: nothing more is receipted
    reseal(
        endpoint,
        {
            "data_state.json": plus("committed_valid_targets", -1),
            "checkpoint_meta.json": plus("committed_valid_targets", -1),
        },
    )
    other = receipt_trainer(tmp_path / "other", budget=40, milestones=[0, 16, 40])
    with pytest.raises(IncompatibleCheckpointError, match="C=40, ahead of the committed data"):
        reload(other, endpoint)


def test_resume_cannot_duplicate_or_skip_receipt_rows(tmp_path: Path) -> None:
    uninterrupted = receipt_trainer(tmp_path / "a")
    run_all(uninterrupted)
    first = receipt_trainer(tmp_path / "b")
    while first.committed_valid_targets < BATCH:
        first.train_step()
    resumed = receipt_trainer(tmp_path / "b")
    reload(resumed, checkpoint_at(first, BATCH))
    with pytest.raises(PayloadReceiptError, match="does not follow"):
        resumed.science.update_payloads.stage(
            step=1, committed_before=0, valid_targets=16, payload="a" * 64
        )  # a replay of update 1 is never appended
    stale = receipt_trainer(tmp_path / "c")
    reload(stale, checkpoint_at(first, BATCH))
    stale.step, stale.committed_valid_targets = 0, 0  # counters that forgot the resume
    with pytest.raises(PayloadReceiptError, match="does not follow"):
        stale.train_step()
    run_all(resumed)
    steps = [r[0] for r in resumed.science.update_payloads.rows]
    assert steps == [1, 2, 3]
    assert (
        resumed.science.update_payloads.to_dict() == uninterrupted.science.update_payloads.to_dict()
    )


# ------------------------------------------------- receipt commit failure


def fail_chain_commit(trainer: Any, *, at_rows: int = 1) -> None:
    """Fault between the LR receipt commit and the payload-chain commit."""
    chain = trainer.science.update_payloads
    original = chain.commit

    def commit() -> Any:
        if len(chain.rows) == at_rows:
            assert len(trainer.science.lr_receipts) == at_rows + 1  # LR already committed
            raise RuntimeError("authored payload chain commit fault")
        return original()

    chain.commit = commit


def test_a_payload_commit_failure_after_data_commit_poisons_the_boundary(tmp_path: Path) -> None:
    uninterrupted = receipt_trainer(tmp_path / "clean")
    run_all(uninterrupted)
    trainer = receipt_trainer(tmp_path / "run")
    trainer.train_step()  # update 1 and its t16 checkpoint are authoritative
    names = published(trainer)
    fail_chain_commit(trainer)
    steps = []
    original_step = trainer.optimizer.step
    trainer.optimizer.step = lambda *a, **k: (steps.append(1), original_step(*a, **k))[1]
    with pytest.raises(ScienceReceiptCommitError) as caught:
        trainer.train_step()
    assert isinstance(caught.value, RecoveryRequiredError)
    assert "authored payload chain commit fault" in str(caught.value.__cause__)
    # Data and optimizer authority committed; the receipt set did not.
    assert (
        trainer.committed_valid_targets
        == 32
        == trainer.batcher.get_state()["committed_valid_targets"]
    )
    assert (len(trainer.science.lr_receipts), len(trainer.science.update_payloads.rows)) == (2, 1)
    assert trainer._update_in_doubt and steps == [1]
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()
    assert steps == [1]  # no further optimizer update

    def manager_save() -> Any:  # the manager's own boundary guard, below the trainer
        return trainer.checkpoint_manager.save_checkpoint(
            checkpoint_id="after_poison_manager",
            run_id=trainer.run_id,
            step=trainer.step,
            committed_valid_targets=trainer.committed_valid_targets,
            processed_valid_targets=trainer.processed_valid_targets,
            plan_id=trainer.plan_id,
            model=trainer.model,
            objective=trainer.objective,
            optimizer=trainer.optimizer,
            optimizer_manifest=trainer.optimizer_manifest,
            schedule=trainer.schedule,
            batcher=trainer.batcher,
            science=trainer.science,
        )

    for publish in (
        lambda: trainer._save_checkpoint("after_poison"),
        lambda: trainer.save_terminal_checkpoint("final"),
        manager_save,
    ):
        with pytest.raises(RecoveryRequiredError):
            publish()
    with pytest.raises(ScientificRuntimeError, match="disagree in length"):
        trainer.science.to_checkpoint()
    assert published(trainer) == names  # no normal checkpoint after the poisoned boundary
    prior = checkpoint_at(trainer, BATCH)
    trainer.checkpoint_manager.store.verify_artifact(prior)
    recovered = receipt_trainer(tmp_path / "run")
    reload(recovered, prior)  # the previous authoritative checkpoint remains valid
    run_all(recovered)
    assert (
        recovered.science.update_payloads.to_dict()
        == uninterrupted.science.update_payloads.to_dict()
    )


def test_training_never_succeeds_after_a_receipt_commit_failure(tmp_path: Path) -> None:
    trainer = receipt_trainer(tmp_path)
    fail_chain_commit(trainer)
    with pytest.raises(ScienceReceiptCommitError):
        trainer.train()
    assert trainer.termination_reason == "failed"
    assert trainer.checkpoint_manager.ledger.get_run(trainer.run_id)["status"] == "FAILED"
    assert not any("-t48-" in name or "final" in name for name in published(trainer))
    assert trainer.committed_valid_targets == 32 and trainer._update_in_doubt


# ------------------------------------------------------------ evaluator guard


def guarded_trainer(root: Path, action: Callable[[Any, Any], Any], *, receipt: bool = True) -> Any:
    holder: dict[str, Any] = {}
    evaluator = CallbackEvaluator(QUICK, lambda model: action(holder["trainer"], model), label="q")
    trainer = receipt_trainer(
        root,
        evaluators={QUICK: evaluator},
        plan=fixture_plan(48, quick_lm=[BATCH]),
        milestones=[0, 48],
        receipt=receipt,
    )
    holder["trainer"] = trainer
    return trainer


def guard_report(trainer: Any, event: str) -> dict[str, Any]:
    [summary] = trainer.evaluation._store.history(event)
    record = trainer.evaluation._store.read(summary["outcome_artifact"])
    assert record is not None
    return record["guard"]  # type: ignore[no-any-return]


def _stage_row(trainer: Any) -> None:
    trainer.science.update_payloads.stage(
        step=2, committed_before=16, valid_targets=16, payload="c" * 64
    )


def _add_row(trainer: Any) -> None:
    _stage_row(trainer)
    trainer.science.update_payloads.commit()


MUTATIONS: dict[str, Callable[..., None]] = {
    "chain_head": lambda t, m, mp: t.science.update_payloads.rows[-1].__setitem__(4, "0" * 64),
    "add_row": lambda t, m, mp: _add_row(t),
    "remove_row": lambda t, m, mp: t.science.update_payloads.rows.pop(),
    "staged_receipt": lambda t, m, mp: _stage_row(t),
    "declaration_removed": lambda t, m, mp: setattr(t.science, "update_payloads", None),
    "version_field": lambda t, m, mp: mp.setattr(up, "PAYLOAD_VERSION", "tampered_v9"),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_evaluator_mutation_of_receipt_state_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    trainer = guarded_trainer(tmp_path, lambda t, m: MUTATIONS[mutation](t, m, monkeypatch))
    with pytest.raises(RecoveryRequiredError):
        trainer.train_step()  # update 1 commits, then its C=16 evaluation mutates live state
    report = guard_report(trainer, f"quick_lm@{BATCH}")
    assert "update_payload_receipt" in report["changed"], report
    assert trainer.evaluation.ledger.records[f"quick_lm@{BATCH}"].status.value == "failed"
    assert trainer._evaluation_compromised
    with pytest.raises(RecoveryRequiredError):
        trainer._save_checkpoint("after_mutation")


def test_an_untouched_receipt_passes_and_is_a_verified_guard_component(tmp_path: Path) -> None:
    trainer = guarded_trainer(tmp_path, lambda t, m: None)
    trainer.train_step()
    report = guard_report(trainer, f"quick_lm@{BATCH}")
    assert report["changed"] == [] and "update_payload_receipt" in report["verified"]


def test_receipt_free_runs_keep_the_historical_guard_and_catch_an_attached_chain(
    tmp_path: Path,
) -> None:
    clean = guarded_trainer(tmp_path / "clean", lambda t, m: None, receipt=False)
    clean.train_step()
    verified = guard_report(clean, f"quick_lm@{BATCH}")["verified"]
    assert "update_payload_receipt" not in verified  # historical component set
    attach = guarded_trainer(
        tmp_path / "attach",
        lambda t, m: setattr(t.science, "update_payloads", UpdatePayloadChain()),
        receipt=False,
    )
    with pytest.raises(RecoveryRequiredError):
        attach.train_step()
    assert "science_receipts" in guard_report(attach, f"quick_lm@{BATCH}")["changed"]


# ------------------------------------------------------------------- forks


def other_policy(trainer: Any) -> None:
    policy = trainer.science.policy
    trainer.science.policy = ScientificPolicy(
        policy.science_version, policy.lr_policy, policy.rng_policy, 999, policy.runtime
    )


def parent_checkpoint(root: Path, *, receipt: bool, committed: int = BATCH) -> Path:
    parent = receipt_trainer(root, receipt=receipt, run_id="parent")
    parent._evaluate_boundary()  # publishes checkpoint@0
    while parent.committed_valid_targets < committed:
        parent.train_step()
    return checkpoint_at(parent, committed)


@pytest.mark.parametrize(
    ("case", "parent_receipt", "child_receipt", "change_policy", "message"),
    [
        ("on_to_off_same_policy", True, False, False, "presence differs"),
        ("off_to_on_same_policy", False, True, False, "presence differs"),
        ("on_to_off_changed_policy", True, False, True, "presence differs"),
        ("off_to_on_changed_policy", False, True, True, "presence differs"),
        ("on_to_on_changed_policy", True, True, True, "changed-scientific-policy fork"),
    ],
)
def test_fork_receipt_semantics_refuse_before_restore(
    tmp_path: Path,
    case: str,
    parent_receipt: bool,
    child_receipt: bool,
    change_policy: bool,
    message: str,
) -> None:
    checkpoint = parent_checkpoint(tmp_path / "parent", receipt=parent_receipt)
    child = receipt_trainer(tmp_path / "child", receipt=child_receipt, run_id="child")
    if change_policy:
        other_policy(child)
    before = snapshot(child)
    with pytest.raises(IncompatibleCheckpointError, match=message):
        reload(child, checkpoint, is_fork=True)
    assert unchanged(child, before)


def test_a_same_policy_fork_inherits_and_continues_the_chain(tmp_path: Path) -> None:
    checkpoint = parent_checkpoint(tmp_path / "parent", receipt=True)
    child = receipt_trainer(tmp_path / "child", run_id="child")
    reload(child, checkpoint, is_fork=True)
    assert [r[:3] for r in child.science.update_payloads.rows] == [[1, 0, 16]]
    run_all(child)
    assert [r[0] for r in child.science.update_payloads.rows] == [1, 2, 3]


def test_a_changed_policy_fork_of_the_initial_state_starts_its_own_chain(tmp_path: Path) -> None:
    checkpoint = parent_checkpoint(tmp_path / "parent", receipt=True, committed=0)
    child = receipt_trainer(tmp_path / "child", run_id="child")
    other_policy(child)
    reload(child, checkpoint, is_fork=True)
    assert child.science.update_payloads.rows == []
    child.train_step()
    assert [r[:3] for r in child.science.update_payloads.rows] == [[1, 0, 16]]


@pytest.mark.parametrize("fork", [False, True])
def test_a_changed_receipt_version_is_refused(tmp_path: Path, fork: bool) -> None:
    checkpoint = parent_checkpoint(tmp_path / "parent", receipt=True)

    def version(science: dict[str, Any]) -> dict[str, Any]:
        science["update_payloads"]["version"] = "global_update_payload_digest_v2"
        return science

    reseal(checkpoint, {"science.json": version})
    child = receipt_trainer(tmp_path / "child", run_id="parent" if not fork else "child")
    before = snapshot(child)
    with pytest.raises(IncompatibleCheckpointError, match="receipt version differs"):
        reload(child, checkpoint, is_fork=fork)
    assert unchanged(child, before)


def test_a_pre_readiness_parent_keeps_its_historical_forks(tmp_path: Path) -> None:
    """A science.json written before the receipt existed: receipt-free children unchanged."""
    checkpoint = parent_checkpoint(tmp_path / "parent", receipt=False)
    assert "update_payloads" not in json.loads((checkpoint / "science.json").read_text())
    child = receipt_trainer(tmp_path / "child", receipt=False, run_id="child")
    reload(child, checkpoint, is_fork=True)
    run_all(child)
    receipted = receipt_trainer(tmp_path / "receipted", run_id="child2")
    with pytest.raises(IncompatibleCheckpointError, match="receipt-free or pre-readiness"):
        reload(receipted, checkpoint, is_fork=True)


def test_a_legacy_parent_cannot_seed_a_receipted_lineage(tmp_path: Path) -> None:
    legacy = build_trainer(tmp_path / "legacy", budget=48, science=False, run_id="legacy")
    legacy.train_step()
    checkpoint = legacy._save_checkpoint("legacy_c16")
    child = receipt_trainer(tmp_path / "child", run_id="child")
    before = snapshot(child)
    with pytest.raises(IncompatibleCheckpointError, match="presence differs"):
        reload(child, checkpoint, is_fork=True)
    assert unchanged(child, before)


# ------------------------------------------------------ pilot non-regression


HISTORICAL_SCIENCE_KEYS = {
    "version",
    "policy",
    "train_start_rng",
    "lr_receipts",
    "runtime_receipts",
    "evaluation",
    "checkpoints",
}


def test_receipt_absent_runs_never_execute_the_receipt_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shards: Any,  # noqa: F811
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a receipt path ran for a run without the receipt")

    for target in (
        "xlm.data.sampling.update_payload.canonical_update",
        "xlm.data.sampling.update_payload.bind_consumed",
        "xlm.data.sampling.update_payload.canonical_from_prepared",
        "xlm.data.sampling.update_payload.canonical_from_microbatches",
        "xlm.training.science.check_receipt_history",
        "xlm.training.science.ScientificState.commit_receipted_update",
        "xlm.training.science.ScientificState.check_receipt_alignment",
    ):
        monkeypatch.setattr(target, forbidden)
    trainer = guarded_trainer(tmp_path / "run", lambda t, m: None, receipt=False)
    trainer.train()
    endpoint = checkpoint_at(trainer, 48)
    saved = json.loads((endpoint / "science.json").read_text(encoding="utf-8"))
    assert set(saved) == HISTORICAL_SCIENCE_KEYS
    resumed = guarded_trainer(tmp_path / "run", lambda t, m: None, receipt=False)
    reload(resumed, checkpoint_at(trainer, 0))
    run_all(resumed)
    fork = guarded_trainer(tmp_path / "fork", lambda t, m: None, receipt=False)
    other_policy(fork)
    reload(fork, checkpoint_at(trainer, 0), is_fork=True)  # changed-policy fork: M1 semantics
    assert fork.science.lr_receipts == []
    producer = build_trainer(
        tmp_path / "producer", budget=2 * GLOBAL, global_batch=GLOBAL, context=CONTEXT
    )
    producer.batcher = prefetcher(shards)  # the pilot's process_depth1 route, receipt absent
    try:
        run_all(producer)
    finally:
        producer.batcher.close()
    assert producer.committed_valid_targets == 2 * GLOBAL and len(producer.science.lr_receipts) == 2


# --------------------------------------------------- real frozen queue worker


@pytest.mark.serial
@pytest.mark.skipif(
    not torch.cuda.is_available(), reason="frozen queue plans bind the CUDA-extra runtime"
)
def test_a_real_queue_worker_never_succeeds_after_a_receipt_commit_fault(tmp_path: Path) -> None:
    """Actual frozen subprocess: fault between LR append and chain commit on update 2."""
    from test_p35_wall_allowance import queue_for, record
    from test_queue import make_tree, submit_toy
    from xlm.experiments.plans import ExecutablePlan, freeze_execution
    from xlm.experiments.snapshot import capture_snapshot

    tree = make_tree(tmp_path / "ws_poison")
    science = tree / "src/xlm/training/science.py"
    text = science.read_text(encoding="utf-8")
    anchor = "        self.lr_receipts.append(receipt)\n        chain.commit()\n"
    assert text.count(anchor) == 1
    fault = (
        "        self.lr_receipts.append(receipt)\n"
        "        if step == 2:\n"
        '            raise RuntimeError("authored payload chain commit fault")\n'
        "        chain.commit()\n"
    )
    science.write_text(text.replace(anchor, fault), encoding="utf-8")
    snapshot_dir = tmp_path / "snap_poison"
    snapshot = capture_snapshot(tree, snapshot_dir)
    resolved = {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 64,
            "num_layers": 1,
            "hidden_size": 16,
            "num_attention_heads": 2,
            "intermediate_size": 32,
            "context_length": 16,
            "attention_backend": "eager",
        },
        "training": {
            "device": "cuda",
            "precision": "fp32",
            "context_length": 16,
            "global_batch_valid_targets": 32,
            "microbatch_sequences": 2,
            "budget": {"max_valid_targets": 64, "max_train_seconds": 60},
            "init_seed": 7,
            "data_seed": 20260918,
            "checkpoint_every_valid_targets": 32,
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 8,
                "horizon_valid_targets": 256,
            },
            "science_version": "xlm-science-v1",
            "lr_policy": "target_endpoint_before_update_v1",
            "training_seed": 10001,
            "runtime": {
                "attention_policy": "strict_deterministic_v1",
                "matmul_tf32": "disabled",
                "bf16_reduced_precision_reduction": "allowed",
            },
            "update_payload_receipt": "global_update_payload_digest_v1",
        },
        "optimizer": {"lr": 0.01, "weight_decay": 0.0},
        "data": {"synthetic_tokens": [((i % 60) + 4) for i in range(400)]},
        "resources": {"total_wall_seconds": 240, "max_gpu_processes": 1},
    }
    plan = ExecutablePlan(
        plan_version="1",
        plan_id="plan_poison",
        plan_hash=hashlib.sha256(b"poison").hexdigest(),
        draft_id="poison",
        track="baseline",
        horizon_kind="continuation_prefix",
        resolved_config=resolved,
        code_snapshot=snapshot,
        dependency_hash="dep",
        seeds={"init_seed": 7, "data_seed": 20260918},
        budget_valid_targets=64,
        budget_max_seconds=None,
        estimated_new_disk_gib=0.01,
        gpu_processes=1,
        evaluation_tier="search",
        checkpoint_every_valid_targets=32,
        evaluation_every_valid_targets=32,
        exposure={"weights": {}, "data_seed": 20260918},
        cost_estimate={"basis": "test"},
        storage_estimate_gib=0.01,
    )
    plan_path = tmp_path / "poison.plan.json"
    freeze_execution(plan, snapshot_dir, ["cuda"])
    plan.save(plan_path)
    queue = queue_for(tmp_path, "poison")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir, device="cuda")
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED", result
    assert "ScienceReceiptCommitError" in result["reason"]
    assert "completion" not in result  # a poisoned boundary is not an evaluation outcome
    assert record(queue, job_id)["status"] == "FAILED"
    job = queue.get_job(job_id)
    assert job is not None and job.state == "FAILED"
    assert queue.ledger.get_run(job_id)["status"] == "FAILED"
    metas = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in Path(job.work_dir).rglob("checkpoint_meta.json")
    ]
    assert [m["committed_valid_targets"] for m in metas] == [32]  # nothing after the poison
    [update_1] = [p.parent for p in Path(job.work_dir).rglob("checkpoint_meta.json")]
    saved = json.loads((update_1 / "science.json").read_text(encoding="utf-8"))
    assert len(saved["update_payloads"]["rows"]) == len(saved["lr_receipts"]["rows"]) == 1
