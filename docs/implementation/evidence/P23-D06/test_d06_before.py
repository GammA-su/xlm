"""D06 before-only evidence. Authored queue fixtures, no production authorization.

Copies only implementation/configuration code and lock pins into private fixtures.
Uses the real queue and Trainer without an executor override; <= 8 targets/job.
No D06 implementation changes are made by these tests.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.experiments.authorization import smoke_ticket
from xlm.experiments.plans import _canonical_payload, _hash_payload
from xlm.experiments.snapshot import capture_snapshot, verify_snapshot

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("d06_authored_queue_helpers", ROOT / "tests/test_queue.py")
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def fixture(tmp_path: Path):
    plan, plan_path, _ = helpers.make_plan(tmp_path, "d06_fixture", budget=8)
    tree = tmp_path / "ws_d06_fixture"
    selected = [p for p in (ROOT / "src").rglob("*")
                if p.is_file() and p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts]
    selected += [ROOT / name for name in ("pyproject.toml", "uv.lock", ".python-version")]
    assert len(selected) < 400
    assert sum(p.stat().st_size for p in selected) < 16 * 1024**2
    for source in selected:
        target = tree / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    snapshot_dir = tmp_path / "complete_snapshot"
    plan.code_snapshot = capture_snapshot(tree, snapshot_dir, max_snapshot_bytes=16 * 1024**2)
    plan.dependency_hash = hashlib.sha256((tree / "uv.lock").read_bytes()).hexdigest()
    plan.horizon_kind = "continuation_prefix"
    plan.budget_max_seconds = 10
    plan.resolved_config["training"]["budget"] = {"max_valid_targets": 8, "max_train_seconds": 10}
    plan.plan_hash = _hash_payload(_canonical_payload(
        plan.resolved_config, plan.code_snapshot.code_hash, plan.dependency_hash,
        plan.seeds, {}, plan.track, plan.horizon_kind, plan.exposure,
    ))
    plan.plan_id = "authored_d06_" + plan.plan_hash[:16]
    plan.save(plan_path)
    queue = helpers.make_queue(tmp_path, "d06_fixture", tree)
    ticket = smoke_ticket(plan.plan_hash)
    job_id, _ = queue.submit(plan, plan_path, snapshot_dir, "cpu", ticket.ticket_id)
    return plan, queue, job_id, tree, snapshot_dir


def observed_run(tmp_path: Path, queue, job_id: str):
    origins = set()
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "train_step":
            origins.add(str(Path(frame.f_code.co_filename).resolve()))

    sys.setprofile(observe)
    try:
        result = queue.run_job(job_id)
    finally:
        sys.setprofile(previous)
    job = queue.get_job(job_id)
    receipt = {"result": result, "observed_train_step_origins": sorted(origins),
               "queue_fixture_only": True, "python": sys.executable}
    if result.get("state") == "SUCCEEDED":
        checkpoint = Path(job.work_dir) / "artifacts/checkpoints" / result["checkpoint_id"]
        receipt["checkpoint_manifest"] = ArtifactStore(queue.paths).verify_artifact(checkpoint).model_dump()
    (tmp_path / "observed.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return result, origins, receipt


def test_enqueued_a_must_survive_working_tree_edit_b(tmp_path: Path) -> None:
    plan, queue, job_id, tree, snapshot = fixture(tmp_path)
    (tree / "src/xlm/training/trainer.py").write_text(
        'raise RuntimeError("authored B must not execute for queued A")\n', encoding="utf-8"
    )
    assert verify_snapshot(snapshot / "code", plan.code_snapshot) == []
    result, _, _ = observed_run(tmp_path, queue, job_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 8


def test_tampered_frozen_code_must_block_before_training(tmp_path: Path) -> None:
    plan, queue, job_id, tree, snapshot = fixture(tmp_path)
    (snapshot / "code/src/xlm/training/trainer.py").write_text(
        'raise RuntimeError("authored snapshot tampering")\n', encoding="utf-8"
    )
    assert verify_snapshot(tree, plan.code_snapshot) == []
    assert verify_snapshot(snapshot / "code", plan.code_snapshot)
    result, origins, _ = observed_run(tmp_path, queue, job_id)
    assert result["state"] == "BLOCKED" and not origins, result


def test_executed_code_and_saved_identity_must_match_snapshot(tmp_path: Path) -> None:
    plan, queue, job_id, _, snapshot = fixture(tmp_path)
    result, origins, receipt = observed_run(tmp_path, queue, job_id)
    assert result["state"] == "SUCCEEDED", result
    assert origins == {str((snapshot / "code/src/xlm/training/trainer.py").resolve())}, receipt
    manifest = receipt["checkpoint_manifest"]
    assert manifest["producer_code_hash"] == plan.code_snapshot.code_hash
    assert manifest["dependency_hash"] == plan.dependency_hash
    assert manifest["resolved_config_hash"] == plan.plan_hash


def test_authored_control_really_executes_eight_targets(tmp_path: Path) -> None:
    _, queue, job_id, _, _ = fixture(tmp_path)
    result, origins, receipt = observed_run(tmp_path, queue, job_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 8
    assert origins
    assert receipt["checkpoint_manifest"]["schema_version"] == 2
