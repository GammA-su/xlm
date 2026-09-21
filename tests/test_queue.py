"""Acceptance tests for P16 queue: leases, sequential runs, recovery, retries, cancel.

CPU-only toy runs through the real Trainer with tiny budgets. GPU-lease logic
is device-agnostic and tested without hardware; no test needs a GPU.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from test_frozen_execution import authored_tree
from xlm.artifacts.ledger import RunLedger
from xlm.core.contracts import RunStatus
from xlm.core.paths import ArtifactPaths
from xlm.experiments.authorization import issue_ticket
from xlm.experiments.plans import ExecutablePlan, freeze_execution
from xlm.experiments.queue import (
    ExperimentQueue,
    GpuLeaseManager,
    LeaseBusyError,
    PrepPool,
    QueueError,
    QueueJob,
)
from xlm.experiments.snapshot import capture_snapshot


def make_tree(root: Path, behavior: str | None = None) -> Path:
    authored_tree(root, behavior)
    (root / "src" / "xlm" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "recipes" / "experiments").mkdir(parents=True, exist_ok=True)
    return root


def make_plan(
    tmp_path: Path,
    name: str,
    *,
    budget: int = 64,
    device: str = "cpu",
    seed: int = 7,
    blockers: list[Any] | None = None,
    behavior: str | None = None,
) -> tuple[ExecutablePlan, Path, Path]:
    tree = make_tree(tmp_path / f"ws_{name}", behavior)
    snapshot_dir = tmp_path / f"snap_{name}"
    snapshot = capture_snapshot(tree, snapshot_dir)
    tokens = [((i % 60) + 4) for i in range(400)]
    resolved = {
        "model": {
            "architecture": "transformer_baseline",
            "vocab_size": 64,
            "num_layers": 2,
            "hidden_size": 32,
            "num_attention_heads": 2,
            "intermediate_size": 64,
            "context_length": 16,
            "attention_backend": "eager",
            "tie_embeddings": True,
            "initialization_policy": "baseline_v1",
        },
        "training": {
            "device": device,
            "precision": "fp32",
            "context_length": 16,
            "global_batch_valid_targets": 32,
            "microbatch_sequences": 2,
            "gradient_clip_norm": 1.0,
            "budget": {"max_valid_targets": budget, "max_train_seconds": 30},
            "init_seed": seed,
            "data_seed": 20260918,
            "checkpoint_every_valid_targets": 32,
            "schedule": {
                "type": "warmup_cosine",
                "warmup_valid_targets": 8,
                "horizon_valid_targets": 256,
                "min_lr_ratio": 0.1,
            },
            "activation_checkpointing": False,
            "compile": False,
        },
        "optimizer": {"lr": 0.01, "weight_decay": 0.0},
        "data": {"synthetic_tokens": tokens},
    }
    plan_hash = hashlib.sha256(
        json.dumps(
            {"name": name, "budget": budget, "seed": seed, "device": device},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    plan = ExecutablePlan(
        plan_version="1",
        plan_id=f"plan_{name}_{plan_hash[:12]}",
        plan_hash=plan_hash,
        draft_id=name,
        track="baseline",
        horizon_kind="standalone",
        resolved_config=resolved,
        code_snapshot=snapshot,
        dependency_hash="dep",
        seeds={"init_seed": seed, "data_seed": 20260918},
        budget_valid_targets=budget,
        budget_max_seconds=None,
        estimated_new_disk_gib=0.01,
        gpu_processes=1,
        evaluation_tier="search",
        checkpoint_every_valid_targets=32,
        evaluation_every_valid_targets=32,
        exposure={"weights": {}, "data_seed": 20260918},
        cost_estimate={"basis": "test"},
        storage_estimate_gib=0.01,
        blockers=list(blockers or []),
    )
    plan_path = tmp_path / f"{name}.plan.json"
    freeze_execution(plan, snapshot_dir, [device])
    plan.save(plan_path)
    return plan, plan_path, snapshot_dir


def make_queue(tmp_path: Path, name: str, tree: Path | None = None) -> ExperimentQueue:
    root = tmp_path / f"home_{name}"
    paths = ArtifactPaths(root=root)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    return ExperimentQueue(ledger, paths, tree_root=tree or (tmp_path / f"ws_{name}"))


def submit_toy(
    queue: ExperimentQueue,
    plan: ExecutablePlan,
    plan_path: Path,
    snapshot_dir: Path,
    device: str = "cpu",
    max_retries: int = 0,
) -> str:
    ticket = issue_ticket(
        plan_hash=plan.plan_hash,
        max_valid_targets=plan.budget_valid_targets,
        approver="test",
        ticket_id=f"T-{plan.plan_id}",
    )
    job_id, _ = queue.submit(
        plan,
        plan_path,
        snapshot_dir,
        device,
        authorization_token=f"{ticket.ticket_id}:{ticket.plan_hash[:12]}",
        authorization=ticket,
        max_retries=max_retries,
    )
    return job_id


# ------------------------------------------------------------------ submission


def test_submit_deduplicates_identical_plans(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "dup")
    queue = make_queue(tmp_path, "dup", tmp_path / "ws_dup")
    first, duplicate_first = queue.submit(
        plan, plan_path, snapshot_dir, "cpu", authorization_token="tok", max_retries=0
    )
    second, duplicate_second = queue.submit(
        plan, plan_path, snapshot_dir, "cpu", authorization_token="tok", max_retries=0
    )
    assert duplicate_first is False
    assert duplicate_second is True
    assert first == second
    assert len(queue.list_jobs()) == 1


def test_submit_refuses_blocked_plans_and_missing_snapshots(tmp_path: Path) -> None:
    from xlm.experiments.plans import PlanBlocker

    plan, plan_path, snapshot_dir = make_plan(
        tmp_path, "blocked", blockers=[PlanBlocker(code="missing_pool_artifact", detail="x")]
    )
    queue = make_queue(tmp_path, "blocked", tmp_path / "ws_blocked")
    with pytest.raises(QueueError, match="approval blockers"):
        queue.submit(plan, plan_path, snapshot_dir, "cpu", authorization_token="tok")
    clean, clean_path, _ = make_plan(tmp_path, "clean")
    with pytest.raises(QueueError, match="snapshot manifest missing"):
        queue.submit(clean, clean_path, tmp_path / "absent", "cpu", authorization_token="tok")


def test_submit_refuses_legacy_unfrozen_plan(tmp_path: Path) -> None:
    """A version-1 plan without an execution envelope must not execute.

    Historical plans stay inspectable, but submission requires a frozen
    version-2 plan; the queue must refuse rather than silently upgrade the
    legacy representation.
    """
    from xlm.experiments.plans import ExecutablePlan, PlanError
    from xlm.experiments.snapshot import CodeSnapshot

    snapshot = CodeSnapshot("1", "abc", None, [], {}, 0, "")
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "manifest.json").write_text(json.dumps(snapshot.to_dict()), encoding="utf-8")
    plan = ExecutablePlan(
        plan_version="1",
        plan_id="plan_x",
        plan_hash="h" * 32,
        draft_id="d",
        track="baseline",
        horizon_kind="standalone",
        resolved_config={},
        code_snapshot=snapshot,
        dependency_hash="dep",
        seeds={"init_seed": 1, "data_seed": 2},
        budget_valid_targets=10,
        budget_max_seconds=None,
        estimated_new_disk_gib=None,
        gpu_processes=1,
        evaluation_tier="search",
        checkpoint_every_valid_targets=10,
        evaluation_every_valid_targets=10,
        exposure={},
        cost_estimate={},
        storage_estimate_gib=None,
        blockers=[],
    )
    plan_path = tmp_path / "plan.json"
    plan.save(plan_path)
    queue = make_queue(tmp_path, "legacy", tmp_path / "ws_legacy")
    with pytest.raises(PlanError, match="legacy/unfrozen plan cannot execute"):
        queue.submit(plan, plan_path, snap_dir, "cpu", authorization_token="tok")


def test_submit_allows_explicit_duplicates(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "dup2")
    queue = make_queue(tmp_path, "dup2", tmp_path / "ws_dup2")
    first, _ = queue.submit(plan, plan_path, snapshot_dir, "cpu", authorization_token="t")
    second, duplicate = queue.submit(
        plan, plan_path, snapshot_dir, "cpu", authorization_token="t", allow_duplicate=True
    )
    assert duplicate is False
    assert first != second


# ---------------------------------------------------------------------- leases


def test_only_one_gpu_lease_can_be_held(tmp_path: Path) -> None:
    manager = GpuLeaseManager(tmp_path / "leases")
    record = manager.acquire("cuda:0", "job_a")
    assert record["job_id"] == "job_a"
    with pytest.raises(LeaseBusyError, match="leased by job 'job_a'"):
        manager.acquire("cuda:0", "job_b")
    # A different device is unaffected.
    manager.acquire("cuda:1", "job_b")
    manager.heartbeat("cuda:0", "job_a")
    manager.release("cuda:0", "job_a")
    manager.acquire("cuda:0", "job_b")
    holder = manager.holder("cuda:0")
    assert holder is not None and holder["job_id"] == "job_b"


def test_stale_lease_is_reclaimable_and_recorded(tmp_path: Path) -> None:
    manager = GpuLeaseManager(tmp_path / "leases", timeout_seconds=0)
    manager.acquire("cuda:0", "crashed_job")
    record = manager.acquire("cuda:0", "job_b")
    assert record["stole_stale_lease"] is True
    assert record["previous_holder"] == "crashed_job"


def test_cpu_environment_refuses_cuda_job_without_execution(tmp_path: Path) -> None:
    # CPU-only evidence: mismatched devices fail before any GPU execution.
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "gpu")
    queue = make_queue(tmp_path, "gpu", tmp_path / "ws_gpu")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir, device="cuda:0")
    queue.leases.acquire("cuda:0", "other_job")

    calls: list[str] = []

    def never_runs(
        plan: ExecutablePlan, job: Any, should_cancel: Any, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append("executed")
        return {}

    result = queue.run_job(job_id, executor=never_runs)
    assert result["state"] == "BLOCKED"
    assert "does not match job device" in result["reason"]
    assert calls == []
    current = queue.get_job(job_id)
    assert current is not None and current.state == RunStatus.BLOCKED.value

    queue.leases.release("cuda:0", "other_job")
    assert queue.leases.holder("cuda:0") is None


# ------------------------------------------------------------------- execution


def test_toy_campaign_runs_sequentially_to_success(tmp_path: Path) -> None:
    # One queue drains three toy jobs in submission order. All fixture trees
    # carry identical file contents, so one tree root verifies every snapshot.
    first_tree = tmp_path / "ws_toy_a"
    queue = make_queue(tmp_path, "camp", first_tree)
    job_ids: list[str] = []
    for seed, name in enumerate(("toy_a", "toy_b", "toy_c")):
        plan, plan_path, snapshot_dir = make_plan(tmp_path, name, seed=seed)
        job_ids.append(submit_toy(queue, plan, plan_path, snapshot_dir))
    queues = queue

    order: list[str] = []
    for job_id in job_ids:
        result = queues.run_job(job_id)
        assert result["state"] == "SUCCEEDED", result
        order.append(result["job_id"])
    assert order == job_ids
    for job_id in job_ids:
        job = queues.get_job(job_id)
        assert job is not None and job.state == RunStatus.SUCCEEDED.value
        record = json.loads((Path(job.work_dir) / "run_record.json").read_text(encoding="utf-8"))
        assert record["status"] == RunStatus.SUCCEEDED.value
        attempts = queues.attempts(job_id)
        assert len(attempts) == 1 and attempts[0]["state"] == "SUCCEEDED"


def test_changed_live_tree_preserves_frozen_run(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "frozen")
    tree = tmp_path / "ws_frozen"
    queue = make_queue(tmp_path, "frozen", tree)
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir)
    (tree / "src" / "xlm" / "core.py").write_text("VALUE = 999\n", encoding="utf-8")
    result = queue.run_job(job_id)
    assert result["state"] == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 64
    current = queue.get_job(job_id)
    assert current is not None and current.state == RunStatus.SUCCEEDED.value


def test_failed_jobs_keep_evidence_and_stay_terminal(tmp_path: Path) -> None:
    """Execution failures are terminal, recorded, and never silently rerun."""
    plan, plan_path, snapshot_dir = make_plan(
        tmp_path, "flaky", behavior='raise RuntimeError("authored worker failure")'
    )
    queue = make_queue(tmp_path, "flaky", tmp_path / "ws_flaky")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir, max_retries=1)

    first = queue.run_job(job_id)
    assert first["state"] == "FAILED"
    assert "authored worker failure" in first["reason"]
    current = queue.get_job(job_id)
    assert current is not None and current.state == RunStatus.FAILED.value

    # Cancel is a no-op on terminal jobs, and a second launch is refused:
    # FAILED jobs do not silently rerun.
    queue.cancel(job_id)
    current = queue.get_job(job_id)
    assert current is not None and current.state == RunStatus.FAILED.value
    again = queue.run_job(job_id)
    assert again["ran"] is False and "not authorized" in again["reason"]

    attempts = queue.attempts(job_id)
    assert len(attempts) == 1
    assert attempts[0]["state"] == "FAILED"
    assert "authored worker failure" in (attempts[0]["reason"] or "")


def test_crash_recovery_requeues_once_when_retries_remain(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "crash")
    queue = ExperimentQueue(
        RunLedger(ArtifactPaths(root=tmp_path / "home_crash").ledger / "ledger.sqlite"),
        ArtifactPaths(root=tmp_path / "home_crash"),
        tree_root=tmp_path / "ws_crash",
        heartbeat_stale_seconds=60,
    )
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir, max_retries=1)

    crashed = simulate_crash(queue, job_id)
    assert crashed.state == RunStatus.RUNNING.value

    recovered = queue.recover_stale_jobs()
    assert recovered == [job_id]
    requeued = queue.get_job(job_id)
    assert requeued is not None and requeued.state == RunStatus.AUTHORIZED.value
    assert requeued.attempts_made == 1

    # Recovery is idempotent: nothing more to reap.
    assert queue.recover_stale_jobs() == []
    # And the requeued job runs exactly once, keeping the crash evidence.
    result = queue.run_next()
    assert result["job_id"] == job_id and result["state"] == "SUCCEEDED"
    history = queue.attempts(job_id)
    assert [a["state"] for a in history] == ["INTERRUPTED", "SUCCEEDED"]
    assert "presumed crashed" in (history[0]["reason"] or "")


def queue_job_replace(queue: ExperimentQueue, job: Any, **overrides: Any) -> Any:
    updated = QueueJob(**{**job.to_dict(), **overrides})
    queue._write_job_row(updated)
    return updated


def simulate_crash(queue: ExperimentQueue, job_id: str) -> Any:
    """Fake a crashed holder: queue AND ledger both read RUNNING, heartbeat stale."""
    job = queue.get_job(job_id)
    assert job is not None
    queue.ledger.transition_run(job_id, RunStatus.AUTHORIZED, RunStatus.RUNNING)
    return queue_job_replace(
        queue, job, state=RunStatus.RUNNING.value, heartbeat_at="2000-01-01T00:00:00+00:00"
    )


def test_crash_without_retries_fails_loudly(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "crash0")
    queue = make_queue(tmp_path, "crash0", tmp_path / "ws_crash0")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir, max_retries=0)
    simulate_crash(queue, job_id)
    assert queue.recover_stale_jobs() == [job_id]
    failed = queue.get_job(job_id)
    assert failed is not None and failed.state == RunStatus.FAILED.value
    assert "retry budget exhausted" in (failed.last_reason or "")


def test_cancel_stops_a_queued_job_and_a_running_job(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "cancelq")
    queue = make_queue(tmp_path, "cancelq", tmp_path / "ws_cancelq")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir)
    cancelled = queue.cancel(job_id)
    assert cancelled.state == RunStatus.CANCELLED.value
    assert queue.run_next()["ran"] is False

    plan2, plan_path2, snapshot_dir2 = make_plan(tmp_path, "cancelr")
    queue2 = make_queue(tmp_path, "cancelr", tmp_path / "ws_cancelr")
    job2 = submit_toy(queue2, plan2, plan_path2, snapshot_dir2)

    outcome: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: outcome.update(queue2.run_job(job2)), daemon=True)
    thread.start()
    for _ in range(100):
        current = queue2.get_job(job2)
        if current is not None and current.state == RunStatus.RUNNING.value:
            break
        time.sleep(0.05)
    queue2.cancel(job2)
    thread.join(timeout=120)
    assert outcome.get("state") == "CANCELLED"
    assert queue2.get_job(job2).state == RunStatus.CANCELLED.value  # type: ignore[union-attr]


def test_prep_pool_is_bounded_and_ordered(tmp_path: Path) -> None:
    pool = PrepPool(max_workers=2)
    try:
        live = {"current": 0, "peak": 0}
        lock = threading.Lock()

        def work(item: int) -> int:
            with lock:
                live["current"] += 1
                live["peak"] = max(live["peak"], live["current"])
            time.sleep(0.05)
            with lock:
                live["current"] -= 1
            return item * 2

        assert pool.map(work, [1, 2, 3, 4]) == [2, 4, 6, 8]
        assert live["peak"] <= 2
    finally:
        pool.shutdown()


def test_status_lists_jobs_with_attempts(tmp_path: Path) -> None:
    plan, plan_path, snapshot_dir = make_plan(tmp_path, "st")
    queue = make_queue(tmp_path, "st", tmp_path / "ws_st")
    job_id = submit_toy(queue, plan, plan_path, snapshot_dir)
    jobs = queue.list_jobs()
    assert [j.job_id for j in jobs] == [job_id]
    assert jobs[0].state == RunStatus.AUTHORIZED.value
    assert queue.attempts(job_id) == []


# ------------------------------------------------------------------------ CLI


def _invoke(args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return CliRunner().invoke(app, args)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_experiment_plan_reports_blockers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(
        [
            "experiment",
            "plan",
            "recipes/experiments/baseline_50m.yaml",
            "--output",
            str(tmp_path / "plan.json"),
            "--snapshot-dir",
            str(tmp_path / "snap"),
        ],
        tmp_path,
        monkeypatch,
    )
    assert result.exit_code == 0, result.output
    assert "missing_pool_artifact" in result.output
    assert "continuation_prefix" in result.output
    assert (tmp_path / "plan.json").is_file()


def test_cli_campaign_plan_shows_six_mixtures_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(
        ["campaign", "plan", "recipes/campaigns/data_search.yaml"], tmp_path, monkeypatch
    )
    assert result.exit_code == 0, result.output
    assert "Trials:          54" in result.output
    assert "Mixtures (6):" in result.output
    assert "Nothing was executed" in result.output


def test_cli_submit_blocks_unauthorized_large_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned = _invoke(
        [
            "experiment",
            "plan",
            "recipes/experiments/baseline_50m.yaml",
            "--output",
            str(tmp_path / "plan.json"),
            "--snapshot-dir",
            str(tmp_path / "snap"),
        ],
        tmp_path,
        monkeypatch,
    )
    assert planned.exit_code == 0, planned.output
    # Baseline draft carries blockers; submit refuses them first.
    blocked = _invoke(
        [
            "experiment",
            "submit",
            str(tmp_path / "plan.json"),
            "--snapshot-dir",
            str(tmp_path / "snap"),
            "--device",
            "cpu",
        ],
        tmp_path,
        monkeypatch,
    )
    assert blocked.exit_code == 1
    assert "approval blockers" in blocked.output

    # A blocker-free large plan without a ticket hits the smoke caps instead.
    import json as _json

    payload = _json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    payload["blockers"] = []
    (tmp_path / "plan.json").write_text(_json.dumps(payload), encoding="utf-8")
    unauthorized = _invoke(
        [
            "experiment",
            "submit",
            str(tmp_path / "plan.json"),
            "--snapshot-dir",
            str(tmp_path / "snap"),
            "--device",
            "cpu",
        ],
        tmp_path,
        monkeypatch,
    )
    assert unauthorized.exit_code == 1
    assert "smoke caps" in unauthorized.output


def test_cli_queue_status_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = _invoke(["queue", "status"], tmp_path, monkeypatch)
    assert empty.exit_code == 0 and "Queue is empty" in empty.output

    result = _invoke(["queue", "cancel", "run_missing"], tmp_path, monkeypatch)
    assert result.exit_code == 1
    assert "unknown job" in result.output
