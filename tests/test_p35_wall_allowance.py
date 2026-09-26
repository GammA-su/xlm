"""P35 M3: the job's total wall allowance persists across queue attempts.

Unit tests advance an injected clock; queue tests replace the worker launcher
with an authored stand-in (the queue refuses callable executors, so the
module function is patched), and one CUDA test lets a real frozen worker hit
the allowance. No research training.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import torch

from test_queue import make_tree, queue_job_replace, submit_toy
from xlm.artifacts.ledger import RunLedger
from xlm.core.contracts import RunStatus
from xlm.core.paths import ArtifactPaths
from xlm.experiments import wall_budget
from xlm.experiments.plans import ExecutablePlan, freeze_execution
from xlm.experiments.queue import ExperimentQueue
from xlm.experiments.snapshot import capture_snapshot
from xlm.experiments.wall_budget import (
    INCOMPLETE,
    AttemptClock,
    WallAllowance,
    WallAllowanceError,
    WallAllowanceExpired,
    declared_total_seconds,
)

cuda_env = pytest.mark.skipif(
    not torch.cuda.is_available(), reason="frozen queue plans bind the CUDA-extra runtime"
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(wall_budget, "_monotonic", fake)
    return fake


# ----------------------------------------------------------------------- unit


def test_resume_receives_only_the_remaining_allowance(tmp_path: Path, clock: Clock) -> None:
    allowance = WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    assert allowance.remaining_seconds == 3600
    first = AttemptClock(allowance, attempt=1)
    assert first.limit == 3600
    clock.now += 900
    first.finish("failed")
    again = WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    assert again.consumed_seconds == pytest.approx(900)
    assert again.remaining_seconds == pytest.approx(2700)
    second = AttemptClock(again, attempt=2)
    assert second.limit == pytest.approx(2700)  # never reset to 3600
    clock.now += 2700
    second.finish("expired")
    final = WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    assert final.status == "expired" and final.remaining_seconds == 0
    with pytest.raises(WallAllowanceExpired, match=INCOMPLETE):
        AttemptClock(final, attempt=3)
    assert [a["attempt"] for a in final.attempts] == [1, 2]


def test_consumption_is_persisted_while_running_so_a_crash_cannot_refund_it(
    tmp_path: Path, clock: Clock
) -> None:
    allowance = WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    attempt = AttemptClock(allowance, attempt=1)
    clock.now += 600
    attempt.tick()  # a heartbeat persists the consumption
    clock.now += 0.2
    attempt.tick()  # throttled: not written again within the persistence interval
    # The runner dies here: no finish() is ever called.
    recovered = WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    assert recovered.consumed_seconds == pytest.approx(600)
    assert recovered.attempts[0]["outcome"] == "interrupted_runner_lost"
    assert recovered.remaining_seconds == pytest.approx(3000)


def test_allowance_is_bound_to_its_plan_and_total(tmp_path: Path, clock: Clock) -> None:
    WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=3600)
    with pytest.raises(WallAllowanceError, match="different plan or total"):
        WallAllowance.load_or_create(tmp_path, plan_hash="q" * 64, total_seconds=3600)
    with pytest.raises(WallAllowanceError, match="different plan or total"):
        WallAllowance.load_or_create(tmp_path, plan_hash="p" * 64, total_seconds=7200)
    assert declared_total_seconds({"total_wall_seconds": 3600}) == 3600.0
    assert declared_total_seconds({}) is None
    for bad in (0, -1, float("inf"), True, "3600"):
        with pytest.raises(WallAllowanceError):
            declared_total_seconds({"total_wall_seconds": bad})


# ---------------------------------------------------------------------- queue


def wall_plan(
    tmp_path: Path, name: str, *, total: float, device: str = "cuda"
) -> tuple[ExecutablePlan, Path, Path]:
    """The queue suite's toy plan plus ``resources.total_wall_seconds``.

    Frozen for the CUDA extra (the installed environment); stand-in launchers never
    touch the device.
    """
    tree = make_tree(tmp_path / f"ws_{name}")
    snapshot_dir = tmp_path / f"snap_{name}"
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
            "device": device,
            "precision": "fp32",
            "context_length": 16,
            "global_batch_valid_targets": 32,
            "microbatch_sequences": 2,
            "budget": {"max_valid_targets": 64, "max_train_seconds": 30},
            "init_seed": 7,
            "data_seed": 20260918,
            "checkpoint_every_valid_targets": 32,
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 8,
                "horizon_valid_targets": 256,
            },
        },
        "optimizer": {"lr": 0.01, "weight_decay": 0.0},
        "data": {"synthetic_tokens": [((i % 60) + 4) for i in range(400)]},
        "resources": {"total_wall_seconds": total, "max_gpu_processes": 1},
    }
    plan_hash = hashlib.sha256(json.dumps([name, total, device]).encode()).hexdigest()
    plan = ExecutablePlan(
        plan_version="1",
        plan_id=f"plan_{name}",
        plan_hash=plan_hash,
        draft_id=name,
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
    plan_path = tmp_path / f"{name}.plan.json"
    freeze_execution(plan, snapshot_dir, [device])
    plan.save(plan_path)
    return plan, plan_path, snapshot_dir


def queue_for(tmp_path: Path, name: str) -> ExperimentQueue:
    paths = ArtifactPaths(root=tmp_path / f"home_{name}")
    return ExperimentQueue(
        RunLedger(paths.ledger / "ledger.sqlite"),
        paths,
        tree_root=tmp_path / f"ws_{name}",
        heartbeat_stale_seconds=1,
    )


def record(queue: ExperimentQueue, job_id: str) -> dict[str, Any]:
    job = queue.get_job(job_id)
    assert job is not None
    return json.loads((Path(job.work_dir) / "run_record.json").read_text(encoding="utf-8"))


@cuda_env
def test_queue_resume_gets_the_remaining_allowance_and_success_is_recorded(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, plan_path, snapshot = wall_plan(tmp_path, "resume", total=3600)
    queue = queue_for(tmp_path, "resume")
    job_id = submit_toy(queue, plan, plan_path, snapshot, device="cuda", max_retries=1)
    seen: list[dict[str, Any]] = []

    def crashing(request: dict[str, Any], work: Path, **kwargs: Any) -> dict[str, Any]:
        seen.append(request)
        clock.now += 900
        kwargs["heartbeat"]()  # the runner's heartbeat persists consumption
        raise KeyboardInterrupt("runner process lost")

    monkeypatch.setattr("xlm.experiments.launcher.launch_worker", crashing)
    with pytest.raises(KeyboardInterrupt):
        queue.run_job(job_id)
    assert seen[0]["wall_seconds_limit"] == pytest.approx(3600)
    job = queue.get_job(job_id)
    assert job is not None and job.state == RunStatus.RUNNING.value
    queue_job_replace(queue, job, heartbeat_at="2000-01-01T00:00:00+00:00")
    # The runner that recorded its identity is gone (a different process instance).
    identity = queue.paths.runs / job_id / "runner_identity.json"
    identity.write_text(json.dumps({"pid": 4_194_301, "create_time": 1.0}), encoding="utf-8")
    assert queue.recover_stale_jobs() == [job_id]

    def succeeding(request: dict[str, Any], work: Path, **kwargs: Any) -> dict[str, Any]:
        seen.append(request)
        clock.now += 100
        return {"steps": 2, "committed_valid_targets": 64}

    monkeypatch.setattr("xlm.experiments.launcher.launch_worker", succeeding)
    result = queue.run_job(job_id)
    assert result["state"] == "SUCCEEDED"
    assert seen[1]["wall_seconds_limit"] == pytest.approx(2700)  # remaining only
    allowance = record(queue, job_id)["result"]["wall_allowance"]
    assert allowance["consumed_seconds"] == pytest.approx(1000)
    assert [a["outcome"] for a in allowance["attempts"]] == ["interrupted_runner_lost", "succeeded"]
    assert allowance["status"] == "completed"


@cuda_env
def test_expired_allowance_is_incomplete_never_success(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, plan_path, snapshot = wall_plan(tmp_path, "expire", total=120)
    queue = queue_for(tmp_path, "expire")
    job_id = submit_toy(queue, plan, plan_path, snapshot, device="cuda", max_retries=1)

    def slow(request: dict[str, Any], work: Path, **kwargs: Any) -> dict[str, Any]:
        clock.now += request["wall_seconds_limit"] + 1
        raise RuntimeError("frozen worker failed (1): total wall allowance exhausted")

    monkeypatch.setattr("xlm.experiments.launcher.launch_worker", slow)
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED" and result["completion"] == INCOMPLETE
    saved = record(queue, job_id)
    assert saved["completion"] == INCOMPLETE and saved["status"] == "FAILED"
    assert saved["wall_allowance"]["status"] == "expired"
    assert queue.ledger.get_run(job_id)["status"] == "FAILED"  # type: ignore[index]
    # A requeued job whose allowance is spent is refused before any launch.
    job = queue.get_job(job_id)
    assert job is not None
    queue_job_replace(queue, job, state=RunStatus.AUTHORIZED.value)
    with queue.ledger._get_connection() as conn:
        conn.execute("UPDATE runs SET status = 'INTERRUPTED' WHERE run_id = ?", (job_id,))

    def never(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("an expired allowance must not launch a worker")

    monkeypatch.setattr("xlm.experiments.launcher.launch_worker", never)
    again = queue.run_job(job_id)
    assert again["completion"] == INCOMPLETE


@pytest.mark.cuda
@pytest.mark.serial
def test_a_real_frozen_worker_is_stopped_at_the_allowance(tmp_path: Path) -> None:
    """The launcher kills the worker when the remaining allowance runs out (bounded: 4 s)."""
    if not torch.cuda.is_available():
        pytest.skip("CUDA hardware required")
    plan, plan_path, snapshot = wall_plan(tmp_path, "real", total=4, device="cuda")
    queue = queue_for(tmp_path, "real")
    job_id = submit_toy(queue, plan, plan_path, snapshot, device="cuda")
    result = queue.run_job(job_id)
    assert result["state"] == "FAILED" and result["completion"] == INCOMPLETE
    assert "total wall allowance exhausted" in record(queue, job_id)["failure_reason"]
    job = queue.get_job(job_id)
    assert job is not None
    process = json.loads(
        (Path(job.work_dir) / "worker-1" / "process.json").read_text(encoding="utf-8")
    )
    assert process["reason"] == "total wall allowance exhausted"
    assert process["wall_seconds_limit"] <= 4
