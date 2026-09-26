"""P35 M3: rescoring M2 evaluation events from their exact retained checkpoints.

Authored synthetic fixtures, tiny CPU models, real native LM evaluators over
authored inventories. Nothing here measures quality.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from p35_eval_support import (
    CodepointTokenizer,
    build_trainer,
    dev_inventories,
    fixture_plan,
    run_all,
    write_inventory,
)
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.evaluation.cadence import AUTHORED_FIXTURE, EventTier, build_checkpoint_plan
from xlm.evaluation.lm_validation import (
    PRIMARY_METRIC,
    LMScoringPolicy,
    LMValidationEvaluator,
    load_pinned_inventory,
)
from xlm.evaluation.outcome import EvaluationContext, EvaluationOutcome
from xlm.evaluation.rescore import (
    RescoreInputs,
    RescoreRefused,
    load_run_evaluation_state,
    rescore_event,
    rescore_from_run_checkpoint,
)
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.science import rng_state_digest

QUICK, FULL = EventTier.QUICK_LM, EventTier.FULL_LM


class FailOnce(LMValidationEvaluator):
    """The real native evaluator (same identity) whose first scoring raises."""

    failures = 1

    def evaluate(self, model: Any, *, device: str, context: EvaluationContext) -> EvaluationOutcome:
        if type(self).failures > 0:
            type(self).failures -= 1
            raise RuntimeError("transient scorer failure at the live boundary")
        return super().evaluate(model, device=device, context=context)


def evaluator(root: Path, tier: EventTier, *, fail: bool = False, stride: int = 4) -> Any:
    tokenizer = ByteTokenizer()
    inventories = dev_inventories(root, tokenizer)
    path, manifest_id = inventories["quick" if tier is QUICK else "full"]
    cls = FailOnce if fail else LMValidationEvaluator
    if fail:
        FailOnce.failures = 1
    return cls(
        tier,
        load_pinned_inventory(
            path, manifest_id=manifest_id, tokenizer_fingerprint=tokenizer.fingerprint
        ),
        tokenizer,
        LMScoringPolicy(context_length=8, rolling_stride=stride),
    )


def checkpoints(milestones: list[int], recovery: list[int], budget: int = 48) -> Any:
    return build_checkpoint_plan(
        AUTHORED_FIXTURE, budget, fixture_milestones=milestones, fixture_recovery=recovery
    )


def failing_run(root: Path, *, auto: bool) -> Any:
    """quick_lm@16 fails at its live boundary (C=16); training continues to C=48."""
    trainer = build_trainer(
        root / "run",
        budget=48,
        evaluators={QUICK: evaluator(root / "inputs", QUICK, fail=True)},
        plan=fixture_plan(48, quick_lm=[16, 48]),
        checkpoint_plan=checkpoints([0, 16, 48], [32]),
    )
    if auto:
        trainer.train()
    else:
        run_all(trainer)
    return trainer


def inputs_for(ev: Any, **overrides: Any) -> RescoreInputs:
    base = RescoreInputs.from_evaluator(ev)
    values = {**base.__dict__, **overrides}
    return RescoreInputs(**values)


def history(trainer: Any, event_id: str) -> list[dict[str, Any]]:
    return trainer.evaluation._store.history(event_id)  # type: ignore[no-any-return]


# --------------------------------------------------------------- success


def test_failed_live_attempt_is_completed_from_the_exact_retained_checkpoint(
    tmp_path: Path,
) -> None:
    trainer = failing_run(tmp_path, auto=True)
    attempts = history(trainer, "quick_lm@16")
    assert [(a["number"], a["status"]) for a in attempts] == [(1, "failed"), (2, "complete")]
    assert attempts[0]["failure"]["message"] == "transient scorer failure at the live boundary"
    assert attempts[1]["route"] == "retained_exact_checkpoint_rescore_v1"
    record = trainer.evaluation.ledger.records["quick_lm@16"]
    assert record.canonical == attempts[1]["outcome_artifact"]  # first complete of the lineage
    assert [a["number"] for a in record.lineage_attempts()] == [1, 2]
    assert trainer.evaluation.completeness().complete
    outcome = trainer.evaluation._store.read(attempts[1]["outcome_artifact"])
    assert outcome["lineage_identity"] == record.due["computation_identity"]
    assert outcome["model_state"]["kind"] == "checkpoint_v1"
    assert outcome["model_state"]["checkpoint_id"] == "m2_run_ckpt-t16-a001"
    assert outcome["loaded_checkpoint"]["state_digest"] == record.due["model_state_digest"]
    assert outcome["actual_committed_targets"] == 16 and outcome["step"] == 1
    # The same state scored live (an uninterrupted run) gives identical metrics.
    clean = build_trainer(
        tmp_path / "clean",
        budget=48,
        evaluators={QUICK: evaluator(tmp_path / "inputs", QUICK)},
        plan=fixture_plan(48, quick_lm=[16, 48]),
        checkpoint_plan=checkpoints([0, 16, 48], [32]),
    )
    clean.train()
    live = clean.evaluation.canonical_receipts()["quick_lm@16"]
    assert live["model_state"]["state_digest"] == record.due["model_state_digest"]
    assert outcome["metrics"][PRIMARY_METRIC] == live["metrics"][PRIMARY_METRIC]
    assert outcome["metrics"] == live["metrics"]
    # The rescore report is recorded, and retention may now release the state.
    ledger = trainer.checkpoints.ledger
    passes = [h["rescore_pass"] for h in ledger.history if "rescore_pass" in h]
    assert passes[-1][0]["action"] == "rescored" and passes[-1][0]["status"] == "complete"


def test_recovery_restores_training_rng_and_training_state(tmp_path: Path) -> None:
    trainer = failing_run(tmp_path, auto=False)
    before = rng_state_digest("cpu")
    report = trainer.recover_failed_evaluations()
    assert report is not None and report[0]["status"] == "complete"
    assert rng_state_digest("cpu") == before
    assert not trainer._evaluation_compromised


def test_deliberate_rerun_keeps_the_first_complete_receipt_canonical(tmp_path: Path) -> None:
    trainer = build_trainer(
        tmp_path / "run",
        budget=48,
        evaluators={QUICK: evaluator(tmp_path / "inputs", QUICK)},
        plan=fixture_plan(48, quick_lm=[16, 48]),
        checkpoint_plan=checkpoints([0, 16, 48], [32]),
    )
    trainer.train()
    final = tmp_path / "run" / "checkpoints" / "m2_run_ckpt-t48-a001"
    ev = evaluator(tmp_path / "inputs", QUICK)
    with pytest.raises(RescoreRefused, match="event_already_complete"):
        rescore_from_run_checkpoint(final, "quick_lm@16", inputs=inputs_for(ev), device="cpu")
    result = rescore_from_run_checkpoint(
        final, "quick_lm@16", inputs=inputs_for(ev), device="cpu", deliberate_rerun=True
    )
    assert result["attempt"]["number"] == 2 and result["attempt"]["status"] == "complete"
    first = history(trainer, "quick_lm@16")[0]["outcome_artifact"]
    assert result["canonical_attempt"] == first


def test_offline_rescore_of_a_finished_run_completes_the_event(tmp_path: Path) -> None:
    failing_run(tmp_path, auto=False)  # no end-of-run recovery: quick_lm@16 stays failed
    final = tmp_path / "run" / "checkpoints" / "m2_run_ckpt-t48-a001"
    state = load_run_evaluation_state(final)
    assert state.ledger.records["quick_lm@16"].status.value == "failed"
    assert not state.ledger.completeness().complete
    result = rescore_from_run_checkpoint(
        final,
        "quick_lm@16",
        inputs=inputs_for(evaluator(tmp_path / "inputs", QUICK)),
        device="cpu",
    )
    assert result["checkpoint"] == "m2_run_ckpt-t16-a001"
    assert result["completeness"]["complete"] is True
    again = load_run_evaluation_state(final)  # durable: reconciled from the store
    assert again.ledger.records["quick_lm@16"].status.value == "complete"


# -------------------------------------------------------------- refusals


@pytest.fixture
def failed(tmp_path: Path) -> dict[str, Any]:
    trainer = failing_run(tmp_path, auto=False)
    final = tmp_path / "run" / "checkpoints" / "m2_run_ckpt-t48-a001"
    state = load_run_evaluation_state(final)
    return {"root": tmp_path, "trainer": trainer, "state": state, "final": final}


def attempt(case: dict[str, Any], checkpoint: str, inputs: RescoreInputs, **kw: Any) -> Any:
    state = case["state"]
    return rescore_event(
        ledger=state.ledger,
        event_id=kw.pop("event_id", "quick_lm@16"),
        store=state.store,
        run=state.run,
        checkpoint_dir=state.root / "checkpoints" / checkpoint,
        inputs=inputs,
        device=kw.pop("device", "cpu"),
        runtime=kw.pop("runtime", state.runtime),
        **kw,
    )


def unchanged(case: dict[str, Any]) -> None:
    attempts = case["trainer"].evaluation._store.history("quick_lm@16")
    assert [a["status"] for a in attempts] == ["failed"], attempts


def test_wrong_checkpoint_is_refused(failed: dict[str, Any]) -> None:
    ev = evaluator(failed["root"] / "inputs", QUICK)
    for other in ("m2_run_ckpt-t0-a001", "m2_run_ckpt-t48-a001"):
        with pytest.raises(RescoreRefused, match="checkpoint_counters_mismatch"):
            attempt(failed, other, inputs_for(ev))
    unchanged(failed)


def test_same_count_and_step_with_different_weights_is_refused(failed: dict[str, Any]) -> None:
    forger = build_trainer(failed["root"] / "run", budget=48, init_seed=99)
    forger.committed_valid_targets, forger.step = 16, 1
    forged = forger._save_checkpoint("m2_run_forged_same_count")
    assert (forged / "checkpoint_meta.json").read_text().count('"committed_valid_targets": 16')
    ev = evaluator(failed["root"] / "inputs", QUICK)
    with pytest.raises(RescoreRefused, match="checkpoint_state_mismatch"):
        attempt(failed, "m2_run_forged_same_count", inputs_for(ev))
    unchanged(failed)


def test_tokenizer_inventory_and_policy_mismatches_are_refused(failed: dict[str, Any]) -> None:
    root = failed["root"]
    ev = evaluator(root / "inputs", QUICK)
    other_tokenizer = CodepointTokenizer()
    with pytest.raises(RescoreRefused, match="evaluator_mismatch"):
        attempt(failed, "m2_run_ckpt-t16-a001", inputs_for(ev, tokenizer=other_tokenizer))
    other_path, other_id = write_inventory(
        root / "other-inventory",
        {"prose": [("p9", "different frozen text")], "science": [("s9", "other text")]},
        ByteTokenizer(),
        subset="quick",
        nested_in=ev.inventory.manifest.nested_in,
    )
    with pytest.raises(RescoreRefused, match=r"evaluator_mismatch.*'inputs'"):
        attempt(failed, "m2_run_ckpt-t16-a001",
                inputs_for(ev, manifest_path=other_path, manifest_id=other_id))  # fmt: skip
    with pytest.raises(RescoreRefused, match=r"evaluator_mismatch.*'scoring_policy'"):
        attempt(failed, "m2_run_ckpt-t16-a001", inputs_for(ev, rolling_stride=3))
    with pytest.raises(RescoreRefused, match="computation_mismatch"):
        attempt(failed, "m2_run_ckpt-t16-a001", inputs_for(ev), runtime=None)
    unchanged(failed)


def test_missing_or_retired_checkpoint_leaves_the_event_incomplete(tmp_path: Path) -> None:
    # quick_lm@32 is crossed at C=32, where no checkpoint is planned.
    trainer = build_trainer(
        tmp_path / "run",
        budget=48,
        evaluators={QUICK: evaluator(tmp_path / "inputs", QUICK, fail=True)},
        plan=fixture_plan(48, quick_lm=[32]),
        checkpoint_plan=checkpoints([0, 48], []),
    )
    report = None
    run_all(trainer)
    report = trainer.recover_failed_evaluations()
    assert report == [
        {
            "event_id": "quick_lm@32",
            "actual_committed_targets": 32,
            "status_before": "failed",
            "action": "refused",
            "code": "checkpoint_missing",
            "reason": report[0]["reason"],
        }
    ]
    assert [a["status"] for a in history(trainer, "quick_lm@32")] == ["failed"]
    assert not trainer.evaluation.completeness().complete
    # Never falls back to the latest (C=48) or any other retained checkpoint.
    assert (tmp_path / "run" / "checkpoints" / "m2_run_ckpt-t48-a001").is_dir()


def test_checkpoint_no_longer_retained_is_refused(failed: dict[str, Any]) -> None:
    root = failed["root"] / "run"
    store = ArtifactStore(ArtifactPaths(root=root))
    record = failed["state"].checkpoints.records["m2_run_ckpt-t16-a001"]
    store.retire_artifact(
        "m2_run_ckpt-t16-a001", "checkpoints", expected_manifest_sha256=record.manifest_sha256
    )
    ev = evaluator(failed["root"] / "inputs", QUICK)
    with pytest.raises(RescoreRefused, match="checkpoint_not_retained"):
        rescore_from_run_checkpoint(failed["final"], "quick_lm@16", inputs=inputs_for(ev),
                                    device="cpu")  # fmt: skip
    unchanged(failed)


def test_benchmark_tier_is_not_rescored(tmp_path: Path) -> None:
    from p35_eval_support import CallbackEvaluator

    def fail(_model: Any) -> None:
        raise RuntimeError("benchmark failure")

    trainer = build_trainer(
        tmp_path / "run",
        budget=48,
        evaluators={
            EventTier.SEARCH_BENCHMARK: CallbackEvaluator(EventTier.SEARCH_BENCHMARK, fail)
        },
        plan=fixture_plan(48, search_benchmark=[16]),
        checkpoint_plan=checkpoints([0, 16, 48], []),
    )
    run_all(trainer)
    report = trainer.recover_failed_evaluations()
    assert report is not None and report[0]["code"] == "tier_not_rescorable"
    assert not trainer.evaluation.completeness().complete
    # Its checkpoint stays retained, explicitly, for the unresolved event.
    decision, _ = trainer.checkpoints.retention_view(trainer)
    assert decision.keep["m2_run_ckpt-t16-a001"] == [
        "pinned_milestone",
        "evaluation_dependency:search_benchmark@16",
    ]
