"""Real inventory/cache equivalence and a small oracle for the retained queue campaign."""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
from pathlib import Path

import pytest

import test_queue
from runtime_fixture import RuntimeSeed
from xlm.experiments import environment, execution
from xlm.experiments.plans import ExecutablePlan, freeze_execution

pytestmark = pytest.mark.serial
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cached_plan(
    tmp_path: Path, runtime_seed: RuntimeSeed, monkeypatch: pytest.MonkeyPatch
) -> tuple[ExecutablePlan, Path, Path]:
    def freeze(plan: ExecutablePlan, snapshot: Path, extras: list[str]) -> ExecutablePlan:
        result, hit = runtime_seed.freeze(plan, snapshot, extras)
        assert hit
        return result

    monkeypatch.setattr(test_queue, "freeze_execution", freeze)
    return test_queue.make_plan(tmp_path, "oracle")


def test_cached_freeze_equals_fresh_inventory_and_plan(
    cached_plan: tuple[ExecutablePlan, Path, Path],
) -> None:
    plan, _, snapshot = cached_plan
    cached = json.loads(json.dumps(plan.to_dict()))
    assert vars(execution)["installed_runtime"] is environment.installed_runtime
    freeze_execution(plan, snapshot, ["cpu"])
    assert plan.to_dict() == cached
    assert vars(execution)["installed_runtime"] is environment.installed_runtime


def test_runtime_seed_returns_private_nested_values(runtime_seed: RuntimeSeed) -> None:
    before = runtime_seed.payload
    left = runtime_seed.inventory(ROOT / "uv.lock", ["cpu"])
    right = runtime_seed.inventory(ROOT / "uv.lock", ["cpu"])
    assert left == right
    left["distributions"]["torch"] = "authored damage"
    left["extras"].append("eval")
    assert right == json.loads(before)
    assert runtime_seed.payload == before


@pytest.mark.parametrize("name", ["uv.lock", "pyproject.toml", ".python-version"])
def test_changed_capture_input_bypasses_seed(
    tmp_path: Path, runtime_seed: RuntimeSeed, name: str
) -> None:
    for file in ("uv.lock", "pyproject.toml", ".python-version"):
        shutil.copyfile(ROOT / file, tmp_path / file)
    assert runtime_seed.matches(tmp_path / "uv.lock", ["cpu"])
    (tmp_path / name).write_text("authored invalid input", encoding="utf-8")
    assert not runtime_seed.matches(tmp_path / "uv.lock", ["cpu"])
    with pytest.raises(ValueError):
        runtime_seed.inventory(tmp_path / "uv.lock", ["cpu"])


def test_explicit_authored_site_is_always_inventoried(
    tmp_path: Path, runtime_seed: RuntimeSeed
) -> None:
    # Authored metadata/files test inventory logic, not installed-package usability.
    # No package is installed or imported from this synthetic private site.
    site = tmp_path / "site"
    names = ["torch", "pydantic", "filelock", "typer", "psutil", "safetensors"]
    for name in names:
        version = importlib.metadata.version(name)
        metadata = site / f"{name}-{version}.dist-info" / "METADATA"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    # Matching lock/project/pin still must not use the default-site seed for an
    # explicit site: this authored site lacks the real project's other packages.
    assert runtime_seed.matches(ROOT / "uv.lock", ["cpu"])
    with pytest.raises(ValueError, match="missing/incompatible offline dependency"):
        runtime_seed.inventory(ROOT / "uv.lock", ["cpu"], site)
    shutil.copyfile(ROOT / "uv.lock", tmp_path / "uv.lock")
    shutil.copyfile(ROOT / ".python-version", tmp_path / ".python-version")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\ndependencies = []\n[project.optional-dependencies]\ncpu = []\n",
        encoding="utf-8",
    )
    payload = site / "authored.py"
    payload.write_bytes(b"VALUE = 1\n")
    stamp = payload.stat()
    first = runtime_seed.inventory(tmp_path / "uv.lock", ["cpu"], site)
    payload.write_bytes(b"VALUE = 2\n")
    os.utime(payload, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = runtime_seed.inventory(tmp_path / "uv.lock", ["cpu"], site)
    assert first["installed_bytes"] == second["installed_bytes"]
    assert first["installed_files_hash"] != second["installed_files_hash"]


def test_queue_order_and_cancellation_survive_reopening(
    tmp_path: Path, cached_plan: tuple[ExecutablePlan, Path, Path]
) -> None:
    # Routine order/persistence oracle; the original three-job SUCCESS campaign
    # still executes every worker in serial_heavy. No executor is substituted here.
    plan, path, snapshot = cached_plan
    queue = test_queue.make_queue(tmp_path, "oracle")
    ids = [
        queue.submit(plan, path, snapshot, "cpu", "smoke", allow_duplicate=True)[0]
        for _ in range(3)
    ]
    for expected in ids:
        queue = test_queue.make_queue(tmp_path, "oracle")
        next_job = queue.next_job()
        assert next_job is not None and next_job.job_id == expected
        queue.cancel(expected)
    assert test_queue.make_queue(tmp_path, "oracle").next_job() is None
    assert [job.state for job in queue.list_jobs()] == ["CANCELLED"] * 3
