"""Real parent/worker crash after a committed authored checkpoint, before recovery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import torch

from test_frozen_execution import frozen_fixture
from xlm.experiments.execution import read_json
from xlm.experiments.queue import ExperimentQueue, QueueJob


def test_real_crash_preserves_committed_boundary_on_retry(tmp_path: Path) -> None:
    plan, queue, job_id, _, _ = frozen_fixture(
        tmp_path,
        "if self.committed_valid_targets == 8 and "
        'self.checkpoint_manager.execution["observations"]["work_dir"].endswith("worker-1"):\n'
        '            __import__("time").sleep(30)',
    )
    # Retry authority is explicit and bound at admission, never changed after it.
    job = queue.get_job(job_id)
    assert job is not None
    snapshot = Path(job.snapshot_dir)
    job_id, _ = queue.submit(
        plan, tmp_path / "plan.json", snapshot, "cpu", "smoke", max_retries=1, allow_duplicate=True
    )
    job = queue.get_job(job_id)
    assert job is not None
    # Cancel the unused fixture admission so the public runner takes the crash case.
    queue.cancel(queue.list_jobs()[0].job_id)
    step = Path(job.work_dir) / f"artifacts/checkpoints/{job_id}_step_1_ckpt"
    with (tmp_path / "crashed-parent.log").open("wb") as output:
        proc = subprocess.Popen(
            [sys.executable, "-m", "xlm.cli.main", "queue", "run", "--once"],
            env={**os.environ, "XLM_HOME": str(queue.paths.root)},
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 140
            while (
                not (step / "_COMPLETED").is_file()
                and proc.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
            assert (step / "_COMPLETED").is_file(), (tmp_path / "crashed-parent.log").read_text()
            live = queue.get_job(job_id)
            assert live is not None
            queue._write_job_row(
                QueueJob(**{**live.to_dict(), "heartbeat_at": "2000-01-01T00:00:00+00:00"})
            )
            assert queue.recover_stale_jobs() == []  # The real owner/worker is still alive.
            duplicate = queue.run_job(job_id)
            assert duplicate["ran"] is False
            assert "owned by another runner" in duplicate["reason"]
        finally:
            if proc.poll() is None:
                descendants = psutil.Process(proc.pid).children(recursive=True)
                proc.kill()
                for child in descendants:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
            proc.wait(timeout=10)
            _wait_for_recorded_processes_to_exit(queue, job_id)
    original = (step / "manifest.json").read_bytes()
    current = queue.get_job(job_id)
    assert current is not None and current.state == "RUNNING"
    queue._write_job_row(
        QueueJob(**{**current.to_dict(), "heartbeat_at": "2000-01-01T00:00:00+00:00"})
    )
    assert queue.recover_stale_jobs() == [job_id]
    result = queue.run_job(job_id)
    (tmp_path / "recovery.json").write_text(json.dumps(result, indent=2))
    assert result.get("state") == "SUCCEEDED", result
    assert result["committed_valid_targets"] == 8
    assert result["resumed_checkpoint"] == str(step)
    final = Path(job.work_dir) / "artifacts/checkpoints" / result["checkpoint_id"]
    before = torch.load(step / "model.pt", weights_only=True)
    after = torch.load(final / "model.pt", weights_only=True)
    assert all(torch.equal(before[k], after[k]) for k in before)
    assert (step / "manifest.json").read_bytes() == original
    assert [item["state"] for item in queue.attempts(job_id)] == ["INTERRUPTED", "SUCCEEDED"]


def _wait_for_recorded_processes_to_exit(queue: ExperimentQueue, job_id: str) -> None:
    """Wait until every process recorded in the job identity files has exited.

    Killing a process tree is asynchronous: the worker (torch teardown takes
    seconds) may still be alive when ``Popen.wait`` returns for the parent.
    Recovery must never reap a live owner, so the test has to synchronize on
    actual death instead of assuming it. A timeout here fails loudly — that
    would mean the kill did not take, which is a real defect, not slowness.
    """
    identity_files = [queue.paths.runs / job_id / "runner_identity.json"]
    identity_files.extend(sorted((queue.paths.runs / job_id).rglob("process_identity.json")))
    identity_files.extend(sorted((queue.paths.runs / job_id).rglob("worker_identity.json")))
    wanted: dict[int, float] = {}
    for path in identity_files:
        if not path.is_file():
            continue
        try:
            identity = read_json(path)
            wanted[int(identity["pid"])] = float(identity["create_time"])
        except (KeyError, TypeError, ValueError):
            continue
    deadline = time.monotonic() + 120
    while wanted:
        for pid in list(wanted):
            try:
                alive = psutil.Process(pid).create_time() == wanted[pid]
            except psutil.NoSuchProcess:
                alive = False
            if not alive:
                del wanted[pid]
        if wanted and time.monotonic() < deadline:
            time.sleep(0.2)
        elif wanted:
            break
    assert not wanted, f"recorded owner processes did not exit after kill: {sorted(wanted)}"
