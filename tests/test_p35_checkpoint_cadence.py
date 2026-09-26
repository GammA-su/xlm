"""P35 M3: absolute committed-target checkpoint events in the shared boundary planner.

Authored synthetic fixtures, tiny CPU models. Nothing here measures quality.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
import torch

from p35_eval_support import (
    RecordingEvaluator,
    build_trainer,
    events_by_id,
    fixture_plan,
    reload,
    run_all,
    science_json,
)
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.evaluation.cadence import (
    AUTHORED_FIXTURE,
    BOUNDARY_PHASES,
    CadenceError,
    CheckpointPlan,
    CheckpointRole,
    EventTier,
    build_checkpoint_plan,
    build_plan,
    projected_first_crossings,
    update_arithmetic,
)
from xlm.evaluation.state_digest import model_state_digest
from xlm.training.checkpoint import IncompatibleCheckpointError
from xlm.training.milestones import (
    CHECKPOINT_RECEIPT_KIND,
    CheckpointLedger,
    CheckpointLedgerError,
    CheckpointPublicationError,
)
from xlm.training.trainer import TrainerError

M = 1_000_000
QUICK = EventTier.QUICK_LM


def ckpt_plan(budget: int, milestones: list[int], recovery: list[int]) -> CheckpointPlan:
    return build_checkpoint_plan(
        AUTHORED_FIXTURE, budget, fixture_milestones=milestones, fixture_recovery=recovery
    )


def checkpoint_dirs(root: Path) -> dict[str, dict[str, Any]]:
    """Published checkpoint directories -> their metadata."""
    found: dict[str, dict[str, Any]] = {}
    directory = root / "checkpoints"
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            meta = json.loads((path / "checkpoint_meta.json").read_text(encoding="utf-8"))
            found[path.name] = meta
    return found


def receipts(root: Path) -> list[str]:
    directory = root / CHECKPOINT_RECEIPT_KIND
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def ledger_of(trainer: Any) -> CheckpointLedger:
    assert trainer.checkpoints is not None
    return trainer.checkpoints.ledger  # type: ignore[no-any-return]


# -------------------------------------------------------------------- planner


def test_pilot_contract_thresholds_and_exact_32m_arithmetic() -> None:
    plan = build_checkpoint_plan("pilot_32m", 32 * M)
    assert [(e.threshold, e.role, e.is_endpoint) for e in plan.events] == [
        (0, CheckpointRole.MILESTONE, False),
        (8 * M, CheckpointRole.MILESTONE, False),
        (16 * M, CheckpointRole.MILESTONE, False),
        (32 * M, CheckpointRole.MILESTONE, True),
    ]
    arithmetic = update_arithmetic(32 * M, 65_536)
    assert arithmetic == {
        "budget_valid_targets": 32_000_000,
        "global_batch_valid_targets": 65_536,
        "full_updates": 488,
        "final_update_targets": 18_432,
        "total_updates": 489,
    }
    assert 488 * 65_536 + 18_432 == 32_000_000
    projected = projected_first_crossings(
        [e.threshold for e in plan.events],
        budget_valid_targets=32 * M,
        global_batch_valid_targets=65_536,
    )
    assert projected[0] == {
        "planned_threshold": 0,
        "projected_committed_targets": 0,
        "projected_step": 0,
        "overshoot_targets": 0,
    }
    # First crossings overshoot 8M/16M: the planned and actual counts differ.
    assert projected[8 * M]["projected_committed_targets"] == 8_060_928
    assert projected[8 * M]["projected_step"] == 123
    assert projected[16 * M]["projected_committed_targets"] == 16_056_320
    assert projected[16 * M]["projected_step"] == 245
    # The endpoint is reached exactly by the masked 489th update.
    assert projected[32 * M]["projected_committed_targets"] == 32_000_000
    assert projected[32 * M]["overshoot_targets"] == 0
    assert BOUNDARY_PHASES == ("evaluation_crossing", "checkpoint", "evaluation_attempt")


def test_full_1b_table_and_fail_closed_cadences() -> None:
    plan = build_checkpoint_plan("full_1b", 1000 * M)
    milestones = [e.threshold for e in plan.events if e.role is CheckpointRole.MILESTONE]
    recovery = [e.threshold for e in plan.events if e.role is CheckpointRole.RECOVERY]
    assert milestones == [0, 128 * M, 256 * M, 512 * M, 1000 * M]
    assert recovery[:3] == [64 * M, 192 * M, 320 * M] and 960 * M in recovery
    assert not set(milestones) & set(recovery)
    with pytest.raises(CadenceError, match="frozen for a 32000000-target budget"):
        build_checkpoint_plan("pilot_32m", 31 * M)
    with pytest.raises(CadenceError, match="unknown checkpoint cadence"):
        build_checkpoint_plan("screen_128m", 128 * M)
    with pytest.raises(CadenceError, match="endpoint must be a pinned"):
        ckpt_plan(40, [0, 16], [32])
    with pytest.raises(CadenceError, match="exceeds the run budget"):
        ckpt_plan(40, [0, 40, 48], [])
    with pytest.raises(CadenceError, match="only accepted for authored_fixture"):
        build_checkpoint_plan("pilot_32m", 32 * M, fixture_milestones=[0], fixture_recovery=[])
    # Same threshold as milestone and recovery is one pinned event.
    both = ckpt_plan(48, [16, 48], [16, 32])
    assert [(e.threshold, e.role.value) for e in both.events] == [
        (16, "milestone"),
        (32, "recovery"),
        (48, "milestone"),
    ]
    assert CheckpointPlan.from_dict(both.to_dict()).digest() == both.digest()


# ------------------------------------------------------------------ crossings


def test_exact_and_overshoot_crossings_record_planned_and_actual(tmp_path: Path) -> None:
    """Batch 16: threshold 8 overshoots to C=16; threshold 32 is crossed exactly at C=32."""
    trainer = build_trainer(tmp_path, budget=48, checkpoint_plan=ckpt_plan(48, [0, 48], [8, 32]))
    run_all(trainer)
    ledger = ledger_of(trainer)
    assert {e: (d["actual_committed_targets"], d["step"]) for e, d in ledger.due.items()} == {
        "checkpoint@0": (0, 0),
        "checkpoint@8": (16, 1),
        "checkpoint@32": (32, 2),
        "checkpoint@48": (48, 3),
    }
    # Updates are never split to land on a threshold.
    assert [row[2] for row in trainer.science.lr_receipts] == [16, 16, 16]
    published = {r.slug: (r.planned_thresholds, r.actual_committed_targets, r.step)
                 for r in ledger.published()}  # fmt: skip
    assert published == {
        "t0": ([0], 0, 0),
        "t8": ([8], 16, 1),
        "t32": ([32], 32, 2),
        "t48": ([48], 48, 3),
    }
    overshoot = ledger.records["m2_run_ckpt-t8-a001"].identity["checkpoint_events"][0]
    assert overshoot["planned_threshold"] == 8
    assert ledger.records["m2_run_ckpt-t8-a001"].identity["actual_committed_targets"] == 16


def test_several_thresholds_in_one_update_publish_one_checkpoint(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=ckpt_plan(32, [12, 32], [4, 8]))
    trainer.train_step()  # C 0 -> 16 crosses 4, 8 and 12
    ledger = ledger_of(trainer)
    assert [r.events for r in ledger.published()] == [
        ["checkpoint@4", "checkpoint@8", "checkpoint@12"]
    ]
    only = ledger.published()[0]
    assert only.role == "milestone"  # any pinned event pins the shared state
    assert only.planned_thresholds == [4, 8, 12] and only.actual_committed_targets == 16
    assert len(checkpoint_dirs(tmp_path)) == 1


def test_final_partial_update_endpoint_fires_once_and_final_is_not_duplicated(
    tmp_path: Path,
) -> None:
    trainer = build_trainer(tmp_path, budget=40, checkpoint_plan=ckpt_plan(40, [0, 40], [8, 24]))
    summary = trainer.train()
    assert summary.termination_reason == "completed"
    assert [row[2] for row in trainer.science.lr_receipts] == [16, 16, 8]
    ledger = ledger_of(trainer)
    assert ledger.due["checkpoint@40"]["actual_committed_targets"] == 40
    names = list(checkpoint_dirs(tmp_path))
    # 0, 8->16, 24->32, 40: four boundaries, and train()'s final state reuses the endpoint.
    assert sorted(checkpoint_dirs(tmp_path)[n]["committed_valid_targets"] for n in names) == [
        0,
        16,
        32,
        40,
    ]
    assert not any(n.endswith("_final") for n in names)
    assert (
        trainer.save_terminal_checkpoint("final").name
        == ledger.records[ledger.due["checkpoint@40"]["record"]].artifact_id
    )
    assert len(checkpoint_dirs(tmp_path)) == 4


@dataclasses.dataclass
class ZeroValidOnce:
    inner: Any
    used: bool = False

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[Any]:
        batches = self.inner.next_step_microbatches(remaining_budget=remaining_budget)
        if not self.used:
            self.used = True
            for batch in batches:
                batch.loss_mask.zero_()
        return batches  # type: ignore[no-any-return]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def test_zero_valid_work_never_triggers_a_checkpoint_event(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=ckpt_plan(32, [32], [1]))
    trainer.batcher = ZeroValidOnce(trainer.batcher)  # type: ignore[assignment]
    assert trainer.train_step() is None
    assert trainer.committed_valid_targets == 0 and ledger_of(trainer).due == {}
    assert checkpoint_dirs(tmp_path) == {}


def test_checkpoint_owes_the_evaluation_of_its_boundary_and_receipts_stay_separate(
    tmp_path: Path,
) -> None:
    trainer = build_trainer(
        tmp_path,
        budget=32,
        evaluators={QUICK: RecordingEvaluator(QUICK)},
        plan=fixture_plan(32, quick_lm=[16]),
        checkpoint_plan=ckpt_plan(32, [16, 32], []),
    )
    trainer.train_step()
    record = ledger_of(trainer).records[ledger_of(trainer).due["checkpoint@16"]["record"]]
    saved = science_json(tmp_path / "checkpoints" / record.artifact_id)
    # Crossing recorded before the checkpoint, scoring after it.
    assert events_by_id(saved["evaluation"])["quick_lm@16"]["status"] == "due"
    assert saved["checkpoints"]["records"][-1]["status"] == "publishing"
    history = trainer.evaluation._store.history("quick_lm@16")  # type: ignore[union-attr]
    assert [h["status"] for h in history] == ["complete"]
    assert history[0]["model_state_digest"] == record.model_state_digest
    # One checkpoint for the shared state; separate checkpoint and evaluation receipts.
    assert len(checkpoint_dirs(tmp_path)) == 1
    assert [r for r in receipts(tmp_path) if r.endswith("_published")]
    assert (tmp_path / "evaluations").is_dir()


# ---------------------------------------------------------------- identities


def test_record_binds_the_full_checkpoint_identity(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=ckpt_plan(32, [16, 32], []))
    trainer.train_step()
    ledger = ledger_of(trainer)
    record = ledger.records[ledger.due["checkpoint@16"]["record"]]
    identity = record.identity
    assert identity["run"]["run_id"] == "m2_run" and identity["run"]["plan_id"] == "m2_plan"
    assert identity["checkpoint_events"][0]["event_id"] == "checkpoint@16"
    assert len(identity["checkpoint_events"][0]["event_identity"]) == 64
    assert identity["actual_committed_targets"] == 16 and identity["step"] == 1
    assert identity["model_state_digest"] == model_state_digest(trainer.model)
    assert len(identity["data_cursor_digest"]) == 64
    assert len(identity["science_identity"]) == 64
    assert identity["artifact_id"] == record.artifact_id == "m2_run_ckpt-t16-a001"
    assert identity["creation_attempt"] == 1
    path = tmp_path / "checkpoints" / record.artifact_id
    manifest = ArtifactStore(ArtifactPaths(root=tmp_path)).verify_artifact(path)
    assert record.status == "published" and record.content_hash == manifest.content_hash
    assert len(record.manifest_sha256 or "") == 64
    assert record.payload_bytes == sum(f.size_bytes for f in manifest.files)
    # Round trip is strict: a tampered identity is refused.
    payload = ledger.to_dict()
    assert CheckpointLedger.from_dict(payload).records[record.artifact_id].identity == identity
    tampered = copy.deepcopy(payload)
    tampered["records"][0]["identity"]["step"] = 99
    with pytest.raises(CheckpointLedgerError, match="identity mismatch"):
        CheckpointLedger.from_dict(tampered)


# ------------------------------------------------------------------- resume


def test_resume_does_not_drift_or_duplicate_milestones(tmp_path: Path) -> None:
    """Batch 12: thresholds 16/32 fall inside updates; resume keeps absolute targets."""
    plan = ckpt_plan(48, [0, 48], [16, 32])
    tokens = [4 + (i * 7) % 250 for i in range(400)]
    straight = build_trainer(
        tmp_path / "straight", budget=48, global_batch=12, tokens=tokens, checkpoint_plan=plan
    )
    run_all(straight)
    expected = {e: straight.checkpoints.ledger.due[e]["actual_committed_targets"]  # type: ignore[union-attr]
                for e in straight.checkpoints.ledger.due}  # type: ignore[union-attr]  # fmt: skip
    assert expected == {"checkpoint@0": 0, "checkpoint@16": 24, "checkpoint@32": 36,
                        "checkpoint@48": 48}  # fmt: skip

    first = build_trainer(
        tmp_path / "run", budget=48, global_batch=12, tokens=tokens, checkpoint_plan=plan
    )
    first.train_step()
    first.train_step()  # C=24: checkpoint@16 published at its first crossing
    at24 = tmp_path / "run" / "checkpoints" / "m2_run_ckpt-t16-a001"
    assert json.loads((at24 / "checkpoint_meta.json").read_text())["committed_valid_targets"] == 24
    resumed = build_trainer(
        tmp_path / "run", budget=48, global_batch=12, tokens=tokens, checkpoint_plan=plan
    )
    reload(resumed, at24)
    run_all(resumed)
    ledger = ledger_of(resumed)
    # The next checkpoint is the first boundary >= 32 (C=36), not 24 + 16 = 40.
    assert {e: ledger.due[e]["actual_committed_targets"] for e in ledger.due} == expected
    # Completed milestones are not republished and every artifact exists once.
    names = sorted(checkpoint_dirs(tmp_path / "run"))
    assert names == [
        "m2_run_ckpt-t0-a001",
        "m2_run_ckpt-t16-a001",
        "m2_run_ckpt-t32-a001",
        "m2_run_ckpt-t48-a001",
    ]
    assert ledger.records["m2_run_ckpt-t16-a001"].status == "published"  # settled on resume
    # Contrast: the historical relative cadence re-anchors at the resumed count.
    legacy = build_trainer(
        tmp_path / "legacy", budget=48, global_batch=12, tokens=tokens, checkpoint_every=16
    )
    legacy.committed_valid_targets = 24
    legacy.next_checkpoint_target = 24 + 16  # what every historical resume path computes
    assert legacy.next_checkpoint_target == 40


def test_publication_failure_keeps_the_last_good_state_and_is_recorded(tmp_path: Path) -> None:
    plan = ckpt_plan(32, [0, 32], [16])
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=plan)
    trainer._evaluate_boundary()  # C=0 milestone
    good = tmp_path / "checkpoints" / "m2_run_ckpt-t0-a001"
    original = trainer._save_checkpoint

    def full_disk(checkpoint_id: str) -> Path:
        if "t16" in checkpoint_id:
            raise OSError("No space left on device")
        return original(checkpoint_id)

    trainer._save_checkpoint = full_disk  # type: ignore[method-assign]
    with pytest.raises(CheckpointPublicationError, match="was not published"):
        trainer.train_step()
    ledger = ledger_of(trainer)
    failed = ledger.records["m2_run_ckpt-t16-a001"]
    assert failed.status == "failed" and "No space left" in failed.failure["message"]  # type: ignore[index]
    assert ledger.event_status("checkpoint@16") == "failed"
    assert "ck_" in receipts(tmp_path)[-1] and receipts(tmp_path)[-1].endswith("_failed")
    ArtifactStore(ArtifactPaths(root=tmp_path)).verify_artifact(good)  # untouched
    # A new process resumes from the last good state and publishes attempt 2.
    resumed = build_trainer(tmp_path, budget=32, checkpoint_plan=plan)
    reload(resumed, good)
    resumed.train_step()
    later = ledger_of(resumed)
    assert later.due["checkpoint@16"]["record"] == "m2_run_ckpt-t16-a002"
    assert any(h.get("attempt") == 1 and h.get("status") == "failed" for h in later.history), (
        later.history
    )


def test_crash_after_durable_publication_is_adopted_not_republished(tmp_path: Path) -> None:
    plan = ckpt_plan(32, [0, 32], [16])
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=plan)
    controller = trainer.checkpoints
    assert controller is not None
    original = controller._finish

    def die(record: Any, path: Path) -> None:
        if record.slug == "t16":
            raise KeyboardInterrupt("process died after the rename, before finalization")
        original(record, path)

    controller._finish = die  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run_all(trainer)
    assert (tmp_path / "checkpoints" / "m2_run_ckpt-t16-a001").is_dir()
    # Replay from the initial milestone reaches the identical state: adopted.
    replay = build_trainer(tmp_path, budget=32, checkpoint_plan=plan)
    reload(replay, tmp_path / "checkpoints" / "m2_run_ckpt-t0-a001")
    replay.train_step()
    record = ledger_of(replay).records["m2_run_ckpt-t16-a001"]
    assert record.status == "published" and record.adopted_existing
    assert not (tmp_path / "checkpoints" / "m2_run_ckpt-t16-a002").exists()
    # Resuming from that checkpoint itself settles its own publishing record.
    direct = build_trainer(tmp_path, budget=32, checkpoint_plan=plan)
    reload(direct, tmp_path / "checkpoints" / "m2_run_ckpt-t16-a001")
    direct.checkpoints.settle()  # type: ignore[union-attr]
    assert ledger_of(direct).records["m2_run_ckpt-t16-a001"].status == "published"


def test_divergent_replay_publishes_a_new_attempt_and_records_the_lost_lineage(
    tmp_path: Path,
) -> None:
    plan = ckpt_plan(32, [0, 32], [16])
    run_all(build_trainer(tmp_path, budget=32, checkpoint_plan=plan))
    other_tokens = [4 + (i * 11) % 250 for i in range(400)]
    replay = build_trainer(tmp_path, budget=32, checkpoint_plan=plan, tokens=other_tokens)
    reload(replay, tmp_path / "checkpoints" / "m2_run_ckpt-t0-a001")
    replay.train_step()
    ledger = ledger_of(replay)
    assert ledger.due["checkpoint@16"]["record"] == "m2_run_ckpt-t16-a002"
    assert ledger.records["m2_run_ckpt-t16-a002"].superseded == ["m2_run_ckpt-t16-a001"]
    assert {"superseded": "m2_run_ckpt-t16-a001", "by": "m2_run_ckpt-t16-a002",
            "reason": "lost_lineage"} in ledger.history  # fmt: skip


def test_changed_checkpoint_plan_refuses_ordinary_resume_and_fork_rebases(tmp_path: Path) -> None:
    plan = ckpt_plan(48, [0, 48], [16, 32])
    trainer = build_trainer(tmp_path, budget=48, checkpoint_plan=plan)
    trainer._evaluate_boundary()
    trainer.train_step()
    checkpoint = tmp_path / "checkpoints" / "m2_run_ckpt-t16-a001"
    for other, message in (
        (ckpt_plan(48, [0, 48], [16]), "checkpoint plan changed"),
        (None, "presence differs"),
    ):
        resumed = build_trainer(tmp_path, budget=48, checkpoint_plan=other)
        with pytest.raises(IncompatibleCheckpointError, match=message):
            reload(resumed, checkpoint)
    fork = build_trainer(
        tmp_path, budget=48, checkpoint_plan=plan, run_id="m3_fork",
        parent_checkpoint_id="m2_run_ckpt-t16-a001",
    )  # fmt: skip
    reload(fork, checkpoint, is_fork=True)
    fork.checkpoints.bind_trainer(fork)  # type: ignore[union-attr]
    ledger = ledger_of(fork)
    assert ledger.plan.origin_committed_targets == 16
    assert ledger.plan.excluded_before_origin == ("checkpoint@0",)
    assert ledger.protected["m2_run_ckpt-t16-a001"] == "fork_parent"


# ------------------------------------------------------------------- legacy


def test_legacy_and_relative_cadence_are_unchanged(tmp_path: Path) -> None:
    with pytest.raises(TrainerError, match="xlm-science-v1"):
        build_trainer(tmp_path / "a", science=False, checkpoint_plan=ckpt_plan(64, [64], []))
    with pytest.raises(TrainerError, match="replaces checkpoint_every_valid_targets"):
        build_trainer(tmp_path / "b", checkpoint_every=16, checkpoint_plan=ckpt_plan(64, [64], []))
    legacy = build_trainer(tmp_path / "legacy", science=False, checkpoint_every=16, budget=48)
    assert legacy.checkpoints is None
    legacy.train()
    assert sorted(checkpoint_dirs(tmp_path / "legacy")) == [
        "m2_run_final",
        "m2_run_step_1_ckpt",
        "m2_run_step_2_ckpt",
        "m2_run_step_3_ckpt",
    ]
    assert not (tmp_path / "legacy" / "checkpoints" / "m2_run_final" / "science.json").exists()
    # Science-v1 without a checkpoint cadence keeps the M1/M2 science.json shape.
    science = build_trainer(tmp_path / "science", checkpoint_every=16, budget=32)
    science.train()
    payload = science_json(tmp_path / "science" / "checkpoints" / "m2_run_final")
    assert "checkpoints" not in payload


def test_checkpoint_cadence_configuration_is_science_only(tmp_path: Path) -> None:
    from xlm.config.schemas import TrainingConfig

    base: dict[str, Any] = {
        "budget": {"max_valid_targets": 48},
        "schedule": {"type": "constant"},
        "checkpoint_cadence": {
            "version": "xlm-checkpoint-cadence-v1",
            "cadence": "authored_fixture",
            "fixture_milestones": [0, 48],
            "fixture_recovery": [16],
            "retention": "latest_two_recovery_plus_pinned_v1",
            "protected_references": [],
        },
    }
    with pytest.raises(ValueError, match="requires training.science_version"):
        TrainingConfig.model_validate(base)
    wrong = copy.deepcopy(base)
    wrong["checkpoint_cadence"]["fixture_milestones"] = None
    with pytest.raises(ValueError, match="required for, and only for"):
        TrainingConfig.model_validate(wrong)
    from xlm.config.science import omit_absent_science_fields

    legacy = TrainingConfig.model_validate(
        {k: v for k, v in base.items() if k != "checkpoint_cadence"}
    )
    assert "checkpoint_cadence" not in omit_absent_science_fields(legacy.model_dump(mode="json"))
    assert torch.__version__  # torch present; configuration never builds a model here
    assert build_plan  # the evaluation planner is unchanged and importable
