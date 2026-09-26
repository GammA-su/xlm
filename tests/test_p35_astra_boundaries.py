"""Independent authored regressions for the readiness review's process boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xlm.experiments.wall_budget import AttemptClock, WallAllowance, WallAllowanceExpired


def test_stat_cap_stops_before_order_or_execution_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_p35_readiness_planner import draft
    from xlm.experiments import science_pilot as pilot

    raw = draft()
    raw["data"]["sources"] = {"authored": str(tmp_path)}
    findings = pilot.Findings()
    monkeypatch.setattr(pilot, "check_storage_roots", lambda *a, **k: None)
    monkeypatch.setattr(
        pilot, "frozen_input_sizes", lambda sources: {"authored": {"tokens.bin": 2**31 + 1}}
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("expensive scan after known stat blocker")

    monkeypatch.setattr(pilot, "check_document_order", forbidden)
    monkeypatch.setattr("xlm.experiments.execution.resolve_execution_config", forbidden)
    result = pilot.run_preflight(
        raw,
        pilot.SciencePilotConfig.model_validate(raw["science_pilot"]),
        findings,
        artifact_home=tmp_path,
        outputs=[],
        snapshot_bytes=0,
        measured_profile=None,
        measure_size=False,
    )
    assert result.resolved is None
    assert {b.code for b in findings.blockers} == {"frozen_input_cap_exceeded"}
    report = findings.preflight["input_bytes"]
    assert report["per_source"]["authored"]["shard_bytes"] == 2**31 + 1
    assert report["aggregate_bytes"] == 2**31 + 1
    assert report["caps"]["aggregate_bytes"] == 2**31


def test_success_arriving_after_wall_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.experiments import wall_budget

    now = [0.0]
    monkeypatch.setattr(wall_budget, "_monotonic", lambda: now[0])
    allowance = WallAllowance.load_or_create(tmp_path, plan_hash="p", total_seconds=2)
    clock = AttemptClock(allowance, 1)
    now[0] = 2.01
    with pytest.raises(WallAllowanceExpired):
        clock.finish("succeeded")
    saved = WallAllowance.load_or_create(tmp_path, plan_hash="p", total_seconds=2)
    assert saved.status == "expired"
    assert saved.attempts[-1]["outcome"] == "expired"


@pytest.mark.parametrize("late", [False, True])
def test_queue_never_promotes_failed_evaluation_or_late_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late: bool
) -> None:
    from test_p35_wall_allowance import queue_for, record, wall_plan
    from test_queue import submit_toy
    from xlm.experiments import wall_budget

    now = [0.0]
    monkeypatch.setattr(wall_budget, "_monotonic", lambda: now[0])
    plan, path, snapshot = wall_plan(tmp_path, "astra", total=10)
    queue = queue_for(tmp_path, "astra")
    job_id = submit_toy(queue, plan, path, snapshot, device="cuda")

    def worker(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if late:
            now[0] = 11
            return {"steps": 2, "committed_valid_targets": 64}
        return {
            "state": "FAILED",
            "completion": "EVALUATION_INCOMPLETE",
            "reason": "RequiredEvaluationIncompleteError: authored exhausted search",
        }

    monkeypatch.setattr("xlm.experiments.launcher.launch_worker", worker)
    result = queue.run_job(job_id)
    completion = "INCOMPLETE" if late else "EVALUATION_INCOMPLETE"
    assert result["state"] == "FAILED"
    assert result["completion"] == completion
    saved = record(queue, job_id)
    assert saved["status"] == "FAILED" and saved["completion"] == completion
    assert queue.get_job(job_id).state == "FAILED"
    assert queue.ledger.get_run(job_id)["status"] == "FAILED"


@pytest.mark.parametrize("failure", ["RequiredEvaluationIncompleteError", "RecoveryRequiredError"])
@pytest.mark.serial
def test_real_worker_transports_only_evaluation_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Actual frozen subprocess, real training/final checkpoint, isolated authored fault."""
    import json

    from test_p35_wall_allowance import queue_for, record, wall_plan
    from test_queue import make_tree, submit_toy

    def tree_with_endpoint_fault(root: Path) -> Path:
        tree = make_tree(root)
        source = tree / "src/xlm/training/trainer.py"
        text = source.read_text(encoding="utf-8")
        signature = "    def require_required_evaluations(self) -> None:\n"
        assert text.count(signature) == 1
        text = text.replace(
            signature,
            signature + f'        raise {failure}("authored endpoint fault")\n',
        )
        source.write_text(text, encoding="utf-8")
        return tree

    monkeypatch.setattr("test_p35_wall_allowance.make_tree", tree_with_endpoint_fault)
    plan, path, snapshot = wall_plan(tmp_path, "transport", total=120)
    queue = queue_for(tmp_path, "transport")
    job_id = submit_toy(queue, plan, path, snapshot, device="cuda")
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED", result
    assert failure in result["reason"]
    saved = record(queue, job_id)
    assert saved["status"] == "FAILED"
    if failure == "RequiredEvaluationIncompleteError":
        assert result["completion"] == saved["completion"] == "EVALUATION_INCOMPLETE"
    else:
        assert "completion" not in result
    job = queue.get_job(job_id)
    completed = list(Path(job.work_dir).rglob("checkpoint_meta.json"))
    endpoint = [
        p
        for p in completed
        if json.loads(p.read_text(encoding="utf-8"))["committed_valid_targets"] == 64
    ]
    assert endpoint
    assert all((p.parent / "_COMPLETED").is_file() for p in endpoint)
