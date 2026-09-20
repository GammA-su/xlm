"""D02 preparation limits and bounded owned-child output, with authored fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from xlm.prepare.bounds import PrepareBounds
from xlm.prepare.config import PrepareConfig
from xlm.prepare.runner import PrepareRunError, run_prepare


def config_for(root: Path, **budgets: int | float) -> PrepareConfig:
    return PrepareConfig.model_validate(
        {
            "id": "bounds",
            "output_root": str(root / "out"),
            "budgets": budgets,
            "stages": [{"stage_id": "inspect", "command": ["--help"], "check_only": True}],
        }
    )


def test_child_stdout_and_stderr_are_bounded_and_accounted(tmp_path: Path) -> None:
    config = config_for(tmp_path, max_subprocess_output_bytes=1024)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    with pytest.raises((RuntimeError, ValueError), match="output|transfer"):
        bounds.run(
            [sys.executable, "-c", "import os; os.write(1,b'A'*8192); os.write(2,b'B'*8192)"],
            tmp_path,
            5,
        )
    assert sum(p.stat().st_size for p in bounds.spool.glob("*.log")) <= 1024
    restored = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    assert restored.capacity.snapshot()["transferred_bytes"] > 0
    assert restored.capacity.remaining("transfer") < 1024


@pytest.mark.parametrize(
    "program",
    [
        "import os; os.write(1,b'A'*8192); os.write(2,b'B'*8192)",
        "import os; os.write(2,b'B'*8192); os.write(1,b'A'*8192)",
        "import os; [(os.write(1,b'A'*512), os.write(2,b'B'*512)) for _ in range(16)]",
        "import os; os.write(1,b'A'*8192)",
    ],
)
def test_child_output_accounting_covers_write_orders(tmp_path: Path, program: str) -> None:
    """Concurrent pipe readers fill one aggregate allowance without losing commits.

    Reads are capped at the live remaining allowance and hold no reservation
    while blocked, so an idle pipe cannot starve the active one. The first
    bytes beyond the filled cap declare overflow; the journal keeps every
    committed byte across reconstruction.
    """

    config = config_for(tmp_path, max_subprocess_output_bytes=1024)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    with pytest.raises(ValueError, match="output limit"):
        bounds.run([sys.executable, "-c", program], tmp_path, 5)
    assert sum(p.stat().st_size for p in bounds.spool.glob("*.log")) <= 1024
    snapshot = bounds.capacity.snapshot()
    assert snapshot["transferred_bytes"] == 1024
    assert snapshot.get("reserved_transfer_bytes", 0) == 0
    assert snapshot.get("reserved_temp_bytes", 0) == 0
    restored = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    journals = list((tmp_path / "out" / ".accounting").glob("resources.json"))
    assert journals
    assert restored.capacity.snapshot()["transferred_bytes"] == 1024
    assert restored.capacity.remaining("transfer") < 1024


def test_child_output_categorizes_every_returned_byte_under_tiny_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allowance (16) << returned chunk: every returned byte has a category.

    Two scripted pipes each return 8192 bytes against a 16-byte aggregate cap,
    synchronized so both first reads land before either commits. Retained
    transfer bytes plus discarded probe/overflow bytes must exactly equal the
    recorded returned total; spool stays capped; no reservation leaks; the
    durable journal reloads identical totals.
    """
    import io
    import subprocess
    import threading
    from typing import Any

    barrier = threading.Barrier(2)
    returned = {"stdout": 0, "stderr": 0}

    class ScriptedPipe(io.BytesIO):
        def __init__(self, kind: str) -> None:
            super().__init__(b"A" * 8192 if kind == "stdout" else b"B" * 8192)
            self._kind = kind
            self._gated = True

        def read1(self, n: int) -> bytes:
            data = super().read1(n)
            if self._gated:
                self._gated = False
                try:
                    barrier.wait(timeout=10)
                except threading.BrokenBarrierError:
                    pass
            returned[self._kind] += len(data)
            return data

        def read(self, n: int = -1) -> bytes:
            data = super().read(n)
            returned[self._kind] += len(data)
            return data

    class FakeProcess:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.stdout = ScriptedPipe("stdout")
            self.stderr = ScriptedPipe("stderr")
            self.pid = -1
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

    class FakePsProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def children(self, recursive: bool = False) -> list[Any]:
            return []

    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr("psutil.Process", FakePsProcess)

    config = config_for(tmp_path, max_subprocess_output_bytes=16)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    with pytest.raises(ValueError, match="output limit"):
        bounds.run(["child", "argv", "ignored"], tmp_path, 5)
    # Both first reads (16 bytes each) are barrier-synchronized; the winner may
    # or may not reach its 1-byte overflow probe before observing the sibling's
    # error, so the probe fires in some interleavings and not others.
    assert sum(returned.values()) in (32, 33)
    journal = (tmp_path / "out" / ".accounting" / "resources.json").read_bytes()
    import json as json_module

    consumed = json_module.loads(journal)["accounting"]["consumed"]
    assert consumed.get("transfer", 0) == 16
    assert consumed.get("transfer", 0) + consumed.get(
        "discarded_child_overflow_bytes", 0
    ) + consumed.get("discarded_child_probe_bytes", 0) == sum(returned.values())
    assert sum(returned.values()) - consumed.get("transfer", 0) >= 1
    assert sum(p.stat().st_size for p in bounds.spool.glob("*.log")) == 16
    snapshot = bounds.capacity.snapshot()
    assert snapshot.get("reserved_transfer_bytes", 0) == 0
    assert snapshot.get("reserved_temp_bytes", 0) == 0
    restored = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    reloaded = json_module.loads(
        (tmp_path / "out" / ".accounting" / "resources.json").read_bytes()
    )["accounting"]["consumed"]
    assert reloaded == consumed
    assert restored.capacity.snapshot()["transferred_bytes"] == 16
    assert restored.capacity.remaining("transfer") == 0


def test_child_output_tiny_allowance_over_real_pipes(tmp_path: Path) -> None:
    """Same 16-byte cap over real pipes: exact fill, bounded spool, no leak."""
    import json as json_module

    config = config_for(tmp_path, max_subprocess_output_bytes=16)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    with pytest.raises(ValueError, match="output limit"):
        bounds.run(
            [sys.executable, "-c", "import os; os.write(1,b'A'*8192); os.write(2,b'B'*8192)"],
            tmp_path,
            5,
        )
    consumed = json_module.loads(
        (tmp_path / "out" / ".accounting" / "resources.json").read_bytes()
    )["accounting"]["consumed"]
    assert consumed.get("transfer", 0) == 16
    assert (
        consumed.get("discarded_child_probe_bytes", 0)
        + consumed.get("discarded_child_overflow_bytes", 0)
        >= 1
    )
    assert sum(p.stat().st_size for p in bounds.spool.glob("*.log")) == 16
    assert bounds.capacity.snapshot().get("reserved_transfer_bytes", 0) == 0
    restored = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    assert restored.capacity.snapshot()["transferred_bytes"] == 16


def test_stale_rerun_keeps_cumulative_account_without_reuse(tmp_path: Path) -> None:
    """Budget identity is cumulative; execution/output identity still binds content.

    Same input path with changed bytes must not reuse cached outputs (planner
    reports stale); the tightened per-stage limit must be enforced and recorded
    (exceeds); both attempts must retain cumulative spending under one journal
    identity; prior immutable outputs must not be overwritten or relabeled.
    """
    import json as json_module

    from xlm.prepare.config import PrepareStageSpec
    from xlm.prepare.planner import plan_prepare
    from xlm.prepare.runner import load_prior_state

    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    (source / "record.txt").write_text("original", encoding="utf-8")
    config = PrepareConfig(
        id="audit",
        output_root=str(tmp_path / "out"),
        stages=[
            PrepareStageSpec(
                stage_id="copy",
                kind="local_copy",
                copy_from=[str(source)],
                copy_to="{output_root}/copy",
                outputs=["{output_root}/copy"],
            )
        ],
    )
    path = tmp_path / "config.yaml"
    run_prepare(config, path, tmp_path / "home", root, authorize=True)

    def consumed() -> dict[str, int]:
        journal = (tmp_path / "out" / ".accounting" / "resources.json").read_bytes()
        return dict(json_module.loads(journal)["accounting"]["consumed"])

    first_spent = consumed()
    assert first_spent.get("input_bytes", 0) == 8
    first_state = load_prior_state(tmp_path / "out")
    assert first_state["stages"]["copy"]["status"] == "succeeded"
    first_outputs_hash = first_state["stages"]["copy"]["outputs_hash"]
    first_inputs_hash = first_state["stages"]["copy"]["inputs_hash"]
    assert (tmp_path / "out/copy/record.txt").read_text(encoding="utf-8") == "original"

    (source / "record.txt").write_text("changed!", encoding="utf-8")
    plan = plan_prepare(config, path, tmp_path / "home", root, load_prior_state(tmp_path / "out"))
    assert plan.stages[0].status == "stale"
    config.stages[0].max_bytes = 1
    with pytest.raises(PrepareRunError, match="exceeds"):
        run_prepare(config, path, tmp_path / "home", root, authorize=True)

    # Same cumulative journal identity: no reset, no fresh allowance.
    second_spent = consumed()
    assert second_spent.get("input_bytes", 0) == first_spent.get("input_bytes", 0)
    assert second_spent.get("requests", 0) == first_spent.get("requests", 0)
    # The failed attempt is recorded as a distinct attempt, not a relabel.
    second_state = load_prior_state(tmp_path / "out")
    assert second_state["stages"]["copy"]["status"] == "failed"
    assert second_state["stages"]["copy"]["inputs_hash"] != first_inputs_hash
    assert "exceeds" in second_state["stages"]["copy"]["note"]
    # Immutable prior outputs are untouched.
    assert (tmp_path / "out/copy/record.txt").read_text(encoding="utf-8") == "original"
    assert first_outputs_hash


def test_check_only_stage_consumes_aggregate_allowance(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = config_for(tmp_path, max_attempts=1)
    run_prepare(
        config, root / "recipes/prepare/offline_toy.yaml", tmp_path / "home", root, authorize=True
    )
    with pytest.raises(PrepareRunError, match="request limit"):
        run_prepare(
            config,
            root / "recipes/prepare/offline_toy.yaml",
            tmp_path / "home",
            root,
            authorize=True,
        )


def test_changed_prepare_budget_does_not_replenish_account(tmp_path: Path) -> None:
    first = config_for(tmp_path, max_subprocess_output_bytes=4096)
    PrepareBounds(first, tmp_path / "out", tmp_path / "home")
    changed = config_for(tmp_path, max_subprocess_output_bytes=8192)
    with pytest.raises(RuntimeError, match="identity"):
        PrepareBounds(changed, tmp_path / "out", tmp_path / "home")


@pytest.mark.parametrize(
    "budget, payload, message",
    [
        ({"max_records": 1}, b'{"text":"a"}\n{"text":"b"}\n', "record"),
        ({"max_record_bytes": 8}, b'{"text":"long"}\n', "record byte"),
        ({"max_decompressed_bytes": 8}, b'{"text":"long"}\n', "decompressed"),
    ],
)
def test_prepare_record_parser_and_expansion_caps(
    tmp_path: Path, budget: dict[str, int], payload: bytes, message: str
) -> None:
    config = config_for(tmp_path, **budget)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    data = tmp_path / "rows.jsonl"
    data.write_bytes(payload)
    with pytest.raises((ValueError, RuntimeError), match=message):
        bounds.validate_outputs([data])


def test_prepare_aggregate_output_guard(tmp_path: Path) -> None:
    config = config_for(tmp_path, max_output_disk_bytes=16)
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    output = tmp_path / "out/oversized.bin"
    output.write_bytes(b"A" * 17)
    with pytest.raises(ValueError, match="output disk"):
        bounds.check()


def test_timed_out_child_and_descendant_are_stopped(tmp_path: Path) -> None:
    import time

    import psutil

    bounds = PrepareBounds(config_for(tmp_path), tmp_path / "out", tmp_path / "home")
    child_pid = tmp_path / "child.pid"
    program = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    )
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        bounds.run([sys.executable, "-c", program, str(child_pid)], tmp_path, 1)
    assert time.monotonic() - start < 10
    assert child_pid.exists()
    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 3
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not psutil.pid_exists(pid)


def test_prepare_unknown_budget_fields_fail_explicitly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="extra"):
        config_for(tmp_path, invented_memory_guarantee=1)


def test_declared_external_output_is_in_shared_disk_account(tmp_path: Path) -> None:
    external = tmp_path / "explicit-output"
    config = PrepareConfig.model_validate(
        {
            "id": "declared",
            "output_root": str(tmp_path / "out"),
            "budgets": {"max_output_disk_bytes": 16},
            "stages": [{"stage_id": "declared", "command": ["--help"], "outputs": [str(external)]}],
        }
    )
    bounds = PrepareBounds(config, tmp_path / "out", tmp_path / "home")
    external.mkdir()
    payload = external / "output.bin"
    payload.write_bytes(b"A" * 16)
    bounds.check()
    assert bounds.capacity.snapshot()["output_disk_bytes"] == 16
    payload.write_bytes(b"A" * 17)
    with pytest.raises(ValueError, match="output disk"):
        bounds.check()
