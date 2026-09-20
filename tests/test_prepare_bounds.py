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
    """Concurrent pipe readers share one aggregate allowance without losing commits.

    Reservation contention between the stdout/stderr drain workers must wait for
    the sibling to settle; only an observed probe byte beyond the cap declares
    overflow. Journal reconstruction must preserve committed consumption.
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
