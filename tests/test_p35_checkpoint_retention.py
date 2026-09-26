"""P35 M3: bounded science-v1 checkpoint retention over verified artifact ids.

Authored synthetic fixtures, tiny CPU models. Nothing here measures quality.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from p35_eval_support import (
    CallbackEvaluator,
    build_trainer,
    fixture_plan,
    reload,
    run_all,
)
from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.retention import (
    MILESTONE,
    RECOVERY,
    SUPERSEDED,
    RetentionCandidate,
    decide_retention,
)
from xlm.artifacts.store import ArtifactConflictError, ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.evaluation.cadence import AUTHORED_FIXTURE, EventTier, build_checkpoint_plan
from xlm.training.milestones import CheckpointPublicationError

QUICK = EventTier.QUICK_LM


def plan(budget: int, milestones: list[int], recovery: list[int]) -> Any:
    return build_checkpoint_plan(
        AUTHORED_FIXTURE, budget, fixture_milestones=milestones, fixture_recovery=recovery
    )


def present(root: Path) -> list[str]:
    directory = root / "checkpoints"
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def ledger_rows(root: Path) -> dict[str, str]:
    ledger = RunLedger(root / "ledger" / "ledger.sqlite")
    with ledger._get_connection() as conn:
        rows = conn.execute("SELECT artifact_id, status FROM artifacts").fetchall()
    return {str(r["artifact_id"]): str(r["status"]) for r in rows}


def rolling(root: Path, **kwargs: Any) -> Any:
    """Batch 8: recovery thresholds 8..40 each get their own boundary; milestones 0 and 48."""
    return build_trainer(
        root,
        budget=48,
        global_batch=8,
        checkpoint_plan=plan(48, [0, 48], [8, 16, 24, 32, 40]),
        **kwargs,
    )


# ------------------------------------------------------------------- policy


def candidate(name: str, role: str, committed: int, digest: str = "d") -> RetentionCandidate:
    return RetentionCandidate(name, role, committed, committed // 8, 1, digest + name, "0" * 64)


def test_policy_keeps_latest_two_recovery_pinned_references_and_dependencies() -> None:
    candidates = [
        candidate("m0", MILESTONE, 0),
        candidate("r8", RECOVERY, 8),
        candidate("r16", RECOVERY, 16),
        candidate("r24", RECOVERY, 24),
        candidate("r32", RECOVERY, 32),
        candidate("lost", SUPERSEDED, 24),
    ]
    decision = decide_retention(
        candidates,
        protected={"r8": "fork_parent"},
        evaluation_dependencies={"dr16": ["quick_lm@16"]},
    )
    assert decision.keep == {
        "r32": ["last_good_state", "latest_recovery_1"],
        "r24": ["latest_recovery_2"],
        "m0": ["pinned_milestone"],
        "r8": ["reference:fork_parent"],
        "r16": ["evaluation_dependency:quick_lm@16"],
    }
    assert decision.retire == {"lost": "superseded_lost_lineage"}
    without = decide_retention(candidates, protected={}, evaluation_dependencies={})
    assert without.retire == {
        "r8": "rolling_recovery_beyond_latest_two",
        "r16": "rolling_recovery_beyond_latest_two",
        "lost": "superseded_lost_lineage",
    }
    # The only state is always kept, even when it is a recovery state.
    single = decide_retention([candidate("r8", RECOVERY, 8)], protected={},
                              evaluation_dependencies={})  # fmt: skip
    assert single.keep == {"r8": ["last_good_state", "latest_recovery_1"]} and not single.retire
    with pytest.raises(ValueError, match="unique"):
        decide_retention([candidates[0], candidates[0]], protected={}, evaluation_dependencies={})
    with pytest.raises(ValueError, match="unsupported retention policy"):
        decide_retention([], protected={}, evaluation_dependencies={}, policy="keep_all")


# --------------------------------------------------------------- integration


def test_training_retains_latest_two_recovery_and_all_milestones(tmp_path: Path) -> None:
    trainer = rolling(tmp_path)
    run_all(trainer)
    assert present(tmp_path) == [
        "m2_run_ckpt-t0-a001",
        "m2_run_ckpt-t32-a001",
        "m2_run_ckpt-t40-a001",
        "m2_run_ckpt-t48-a001",
    ]
    ledger = trainer.checkpoints.ledger  # type: ignore[union-attr]
    statuses = {r.slug: r.status for r in ledger.records.values()}
    assert statuses == {
        "t0": "published",
        "t8": "retired",
        "t16": "retired",
        "t24": "retired",
        "t32": "published",
        "t40": "published",
        "t48": "published",
    }
    retired = ledger.records["m2_run_ckpt-t8-a001"].retired
    assert retired is not None and retired["reason"] == "rolling_recovery_beyond_latest_two"
    assert retired["after"] == "m2_run_ckpt-t24-a001" and retired["freed_bytes"] > 0
    rows = ledger_rows(tmp_path)
    assert rows["m2_run_ckpt-t8-a001"] == "retired" and rows["m2_run_ckpt-t48-a001"] == "completed"
    # The retention decision behind every retirement is kept, with reasons.
    last = ledger.retention_log[-1]["decision"]
    assert last["keep"]["m2_run_ckpt-t0-a001"] == ["pinned_milestone"]
    assert "last_good_state" in last["keep"]["m2_run_ckpt-t48-a001"]
    # Audits report retired rows as retired, never as missing references.
    paths = ArtifactPaths(root=tmp_path)
    audit = RunLedger(paths.ledger / "ledger.sqlite").audit_ledger_references(
        paths, ArtifactStore(paths)
    )
    assert audit["unusable"] == 0 and audit["retired"] == 3


def test_protected_reference_is_never_retired(tmp_path: Path) -> None:
    run_all(rolling(tmp_path, protected_references=["m2_run_ckpt-t8-a001"]))
    assert "m2_run_ckpt-t8-a001" in present(tmp_path)
    assert "m2_run_ckpt-t16-a001" not in present(tmp_path)


def test_checkpoint_needed_by_an_unresolved_evaluation_is_kept(tmp_path: Path) -> None:
    def fail(_model: Any) -> None:
        raise RuntimeError("scorer failed at C=16")

    trainer = rolling(
        tmp_path,
        evaluators={QUICK: CallbackEvaluator(QUICK, fail)},
        plan=fixture_plan(48, quick_lm=[16]),
    )
    run_all(trainer)
    ledger = trainer.checkpoints.ledger  # type: ignore[union-attr]
    assert "m2_run_ckpt-t16-a001" in present(tmp_path)  # beyond the latest two, still kept
    assert "m2_run_ckpt-t8-a001" not in present(tmp_path)
    reasons = ledger.retention_log[-1]["decision"]["keep"]["m2_run_ckpt-t16-a001"]
    assert reasons == ["evaluation_dependency:quick_lm@16"]
    dependencies = ledger.retention_log[-1]["decision"]["evaluation_dependencies"]
    assert list(dependencies.values()) == [["quick_lm@16"]]


def test_failed_publication_retires_nothing(tmp_path: Path) -> None:
    trainer = build_trainer(
        tmp_path, budget=48, global_batch=8, checkpoint_plan=plan(48, [48], [8, 16, 24])
    )
    trainer.train_step()
    trainer.train_step()  # t8 and t16 published: both within the latest two
    original = trainer._save_checkpoint

    def broken(checkpoint_id: str) -> Path:
        if "t24" in checkpoint_id:
            raise OSError("device error while writing")
        return original(checkpoint_id)

    trainer._save_checkpoint = broken  # type: ignore[method-assign]
    with pytest.raises(CheckpointPublicationError):
        trainer.train_step()
    # Had t24 been published, t8 would now be retired. It was not: t8 remains.
    assert present(tmp_path) == ["m2_run_ckpt-t16-a001", "m2_run_ckpt-t8-a001"]
    for name in present(tmp_path):
        ArtifactStore(ArtifactPaths(root=tmp_path)).verify_artifact(tmp_path / "checkpoints" / name)


def test_replacement_is_published_and_verified_before_any_retirement(tmp_path: Path) -> None:
    trainer = rolling(tmp_path)
    store = trainer.checkpoint_manager.store
    original = store.retire_artifact
    seen: list[tuple[str, list[str]]] = []
    violations: list[str] = []

    def observe(artifact_id: str, kind: str, *, expected_manifest_sha256: str) -> int:
        # Record, never raise: retention records a failed retirement and continues,
        # so an exception raised here would be swallowed instead of failing the test.
        newest = max(
            trainer.checkpoints.ledger.published(),  # type: ignore[union-attr]
            key=lambda r: r.actual_committed_targets,
        )
        try:
            store.verify_artifact(tmp_path / "checkpoints" / newest.artifact_id)
        except Exception as exc:  # noqa: BLE001 - asserted empty below
            violations.append(f"{artifact_id} retired before {newest.artifact_id}: {exc}")
        seen.append((artifact_id, present(tmp_path)))
        return original(artifact_id, kind, expected_manifest_sha256=expected_manifest_sha256)

    store.retire_artifact = observe  # type: ignore[method-assign]
    run_all(trainer)
    assert violations == []
    assert [name for name, _ in seen] == [
        "m2_run_ckpt-t8-a001",
        "m2_run_ckpt-t16-a001",
        "m2_run_ckpt-t24-a001",
    ]
    # When t8 was retired, its replacements t16 and t24 were already durable.
    assert {"m2_run_ckpt-t16-a001", "m2_run_ckpt-t24-a001"} <= set(seen[0][1])


def test_only_owned_verified_artifacts_are_retired(tmp_path: Path) -> None:
    # A foreign run's checkpoint in the same store is never a candidate.
    foreign = build_trainer(tmp_path, budget=48, global_batch=8, run_id="other_run")
    foreign.train_step()
    foreign_path = foreign._save_checkpoint("other_run_ckpt-t8-a001")
    trainer = rolling(tmp_path)
    run_all(trainer)
    assert foreign_path.is_dir()
    assert "other_run_ckpt-t8-a001" in present(tmp_path)


def test_superseded_lost_lineage_is_retired_after_its_replacement(tmp_path: Path) -> None:
    budget_plan = plan(32, [0, 32], [16])
    run_all(build_trainer(tmp_path, budget=32, checkpoint_plan=budget_plan))
    other = [4 + (i * 11) % 250 for i in range(400)]
    replay = build_trainer(tmp_path, budget=32, checkpoint_plan=budget_plan, tokens=other)
    reload(replay, tmp_path / "checkpoints" / "m2_run_ckpt-t0-a001")
    replay.train_step()
    ledger = replay.checkpoints.ledger  # type: ignore[union-attr]
    assert "m2_run_ckpt-t16-a001" not in present(tmp_path)
    assert "m2_run_ckpt-t16-a002" in present(tmp_path)
    assert any(
        h.get("retired") == "m2_run_ckpt-t16-a001" and h["reason"] == "superseded_lost_lineage"
        for h in ledger.history
    )


def test_store_refuses_a_changed_or_missing_target_and_hides_tombstones(tmp_path: Path) -> None:
    trainer = build_trainer(tmp_path, budget=32, checkpoint_plan=plan(32, [0, 32], []))
    trainer._evaluate_boundary()
    store = trainer.checkpoint_manager.store
    target = tmp_path / "checkpoints" / "m2_run_ckpt-t0-a001"
    with pytest.raises(ArtifactConflictError, match="changed since its retention decision"):
        store.retire_artifact(
            "m2_run_ckpt-t0-a001", "checkpoints", expected_manifest_sha256="0" * 64
        )
    store.verify_artifact(target)  # intact
    with pytest.raises(FileNotFoundError):
        store.retire_artifact("m2_run_nothing", "checkpoints", expected_manifest_sha256="0" * 64)
    # An interrupted retirement leaves only an invisible tombstone, later purged.
    leftover = tmp_path / ".retired" / "checkpoints__x__deadbeef"
    shutil.copytree(target, leftover)
    paths = ArtifactPaths(root=tmp_path)
    fresh = RunLedger(tmp_path / "fresh.sqlite")
    report = fresh.rebuild_from_filesystem(paths, ArtifactStore(paths))
    assert not any("deadbeef" in c for c in report["corrupt_artifacts"])
    assert fresh.get_artifact("m2_run_ckpt-t0-a001") is not None
    assert store.purge_tombstones() == 1 and not leftover.exists()


def test_resume_after_retention_knows_what_was_retired(tmp_path: Path) -> None:
    trainer = rolling(tmp_path)
    for _ in range(4):
        trainer.train_step()  # C=32: t8 retired after t24 was published
    checkpoint = tmp_path / "checkpoints" / "m2_run_ckpt-t32-a001"
    saved = json.loads((checkpoint / "science.json").read_text(encoding="utf-8"))
    by_id = {r["artifact_id"]: r["status"] for r in saved["checkpoints"]["records"]}
    assert by_id["m2_run_ckpt-t16-a001"] == "published"  # retired after t32 was serialized
    resumed = rolling(tmp_path)
    reload(resumed, checkpoint)
    run_all(resumed)
    ledger = resumed.checkpoints.ledger  # type: ignore[union-attr]
    assert ledger.records["m2_run_ckpt-t16-a001"].status == "retired"
    assert present(tmp_path) == [
        "m2_run_ckpt-t0-a001",
        "m2_run_ckpt-t32-a001",
        "m2_run_ckpt-t40-a001",
        "m2_run_ckpt-t48-a001",
    ]
