"""Owned control replacements are retired under FileLock before scratch admission."""

from __future__ import annotations

import json
import multiprocessing
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.progress import ProgressCorruptionError, ProgressJournal


def manager(root: Path, limit: int = 8192) -> StorageCapacityManager:
    return StorageCapacityManager(
        1024**2, 1024**2, limit, 1024**2, journal=ProgressJournal(root / "job.json", "job", "fixed")
    )


def _die_before_replace(root: str) -> None:
    path = Path(root) / "job.json"
    journal = ProgressJournal(path, "job", "fixed")
    original = os.replace

    def replace(source: Any, destination: Any) -> None:
        if Path(destination) == path:
            os._exit(73)  # _write already flushed and fsynced this replacement.
        original(source, destination)

    with patch("os.replace", replace), journal.transaction() as state:
        state.cache_hits += 1


@pytest.mark.serial
def test_repeated_real_deaths_reclaim_scratch_before_admission(tmp_path: Path) -> None:
    capacity = manager(tmp_path)
    context = multiprocessing.get_context("spawn")
    hypothetical_unreclaimed = 0
    for _ in range(16):
        process = context.Process(target=_die_before_replace, args=(str(tmp_path),))
        process.start()
        process.join(20)
        if process.is_alive():
            process.kill()
            process.join()
        assert process.exitcode == 73
        orphans = list(tmp_path.glob("job.json.*.tmp"))
        assert len(orphans) == 1
        hypothetical_unreclaimed += orphans[0].stat().st_size
        # At death the old journal, lock and fsynced replacement fit the cap.
        assert sum(path.stat().st_size for path in tmp_path.iterdir()) <= 8192
        capacity = manager(tmp_path)
        assert not list(tmp_path.glob("job.json.*.tmp"))
        actual = sum(path.stat().st_size for path in tmp_path.iterdir())
        assert capacity.snapshot()["control_disk_bytes"] == actual <= 8192
        assert capacity.journal is not None and capacity.journal.state.cache_hits == 0
    assert hypothetical_unreclaimed > 8192  # The old implementation would accumulate past cap.


def _active_writer(root: str, ready: Any, release: Any) -> None:
    path = Path(root) / "job.json"
    journal = ProgressJournal(path, "job", "fixed")
    original = os.replace

    def replace(source: Any, destination: Any) -> None:
        if Path(destination) == path:
            ready.set()
            if not release.wait(15):
                raise RuntimeError("writer barrier timed out")
        original(source, destination)

    with patch("os.replace", replace), journal.transaction() as state:
        state.cache_hits += 1


def _competing_reader(root: str, entered: Any, finished: Any) -> None:
    entered.set()
    journal = ProgressJournal(Path(root) / "job.json", "job", "fixed")
    assert journal.state.cache_hits == 1
    finished.set()


@pytest.mark.serial
def test_competing_process_cannot_delete_active_replacement(tmp_path: Path) -> None:
    manager(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready, release, entered, finished = (context.Event() for _ in range(4))
    writer = context.Process(target=_active_writer, args=(str(tmp_path), ready, release))
    reader = context.Process(target=_competing_reader, args=(str(tmp_path), entered, finished))
    writer.start()
    try:
        assert ready.wait(15)
        reader.start()
        assert entered.wait(15)
        assert not finished.wait(0.2)
        assert len(list(tmp_path.glob("job.json.*.tmp"))) == 1
        release.set()
        writer.join(20)
        reader.join(20)
        assert writer.exitcode == reader.exitcode == 0
        assert finished.is_set()
        assert not list(tmp_path.glob("job.json.*.tmp"))
    finally:
        release.set()
        for process in (writer, reader):
            if process.pid is not None and process.is_alive():
                process.kill()
                process.join()


@pytest.mark.parametrize(
    "kind", ["name", "foreign", "malformed", "hardlink", "directory", "symlink"]
)
def test_unproven_replacement_refused_without_deletion(tmp_path: Path, kind: str) -> None:
    capacity = manager(tmp_path)
    assert capacity.journal is not None
    raw = capacity.journal.state.model_dump_json().encode()
    valid = tmp_path / f"job.json.{uuid.uuid4().hex}.tmp"
    valid.write_bytes(raw)
    suspect = tmp_path / f"job.json.{uuid.uuid4().hex}.tmp"
    if kind == "name":
        suspect = tmp_path / "job.json.unexpected.tmp"
        suspect.write_bytes(raw)
    elif kind == "foreign":
        value = json.loads(raw)
        value["plan_hash"] = "foreign"
        suspect.write_text(json.dumps(value))
    elif kind == "malformed":
        suspect.write_bytes(b'{"truncated"')
    elif kind == "hardlink":
        os.link(valid, suspect)
    elif kind == "directory":
        suspect.mkdir()
    elif kind == "symlink":
        try:
            suspect.symlink_to(valid)
        except OSError as exc:
            pytest.skip(f"symlink capability unavailable: {exc}")
    with pytest.raises(ProgressCorruptionError, match="control-file reconciliation"):
        manager(tmp_path)
    assert valid.exists() and suspect.exists()


def test_unrelated_names_are_never_adopted_or_deleted(tmp_path: Path) -> None:
    manager(tmp_path)
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"foreign file")
    manager(tmp_path)
    assert unrelated.read_bytes() == b"foreign file"


@pytest.mark.parametrize("attribute", ["is_symlink", "is_junction"])
def test_reparse_metadata_is_refused_without_following_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribute: str
) -> None:
    """Guard logic is testable even without Windows symlink creation privilege."""
    capacity = manager(tmp_path)
    assert capacity.journal is not None
    suspect = tmp_path / f"job.json.{uuid.uuid4().hex}.tmp"
    suspect.write_text(capacity.journal.state.model_dump_json())
    original = getattr(Path, attribute)
    monkeypatch.setattr(Path, attribute, lambda path: path == suspect or original(path))
    with pytest.raises(ProgressCorruptionError, match="control-file reconciliation"):
        manager(tmp_path)
    assert suspect.exists()


def test_cleanup_count_bound_refuses_before_deleting_anything(tmp_path: Path) -> None:
    capacity = manager(tmp_path)
    assert capacity.journal is not None
    for _ in range(65):
        (tmp_path / f"job.json.{uuid.uuid4().hex}.tmp").write_text(
            capacity.journal.state.model_dump_json()
        )
    with pytest.raises(ProgressCorruptionError):
        manager(tmp_path)
    assert len(list(tmp_path.glob("job.json.*.tmp"))) == 65


def test_diagnostics_share_scratch_limit_and_orphan_lock(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    journal = ProgressJournal(scratch / "journals/job.progress.json", "job", "fixed")
    journal.bind_roots(scratch=scratch, output=tmp_path / "output")
    capacity = StorageCapacityManager(1024**2, 1024**2, 8192, 1024**2, journal=journal)
    payload = json.dumps({"plan_id": "job", "perf_version": 1, "diagnostic": "x" * 1000}).encode()
    target = journal.write_diagnostic(payload)
    orphan = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    orphan.write_bytes(payload)
    journal.save()
    assert not orphan.exists()
    assert capacity.snapshot()["control_disk_bytes"] == sum(
        path.stat().st_size for path in scratch.rglob("*") if path.is_file()
    )
    capacity.reserve("temp", 4000)
    with pytest.raises(ProgressCorruptionError, match="scratch limit"):
        journal.write_diagnostic(
            json.dumps({"plan_id": "job", "perf_version": 1, "diagnostic": "x" * 7000}).encode()
        )
    assert target.read_bytes() == payload
    assert not list(target.parent.glob("*.tmp"))


def test_initial_journal_bytes_are_bounded_before_first_write(tmp_path: Path) -> None:
    from test_acquisition_leases import plan_for
    from xlm.data.acquisition.fetcher import BoundedFetcher
    from xlm.data.acquisition.plan import AcquisitionLimits

    plan = plan_for(47183, ["rows.jsonl"], limits=AcquisitionLimits(max_temp_disk_bytes=128))
    with pytest.raises(ProgressCorruptionError, match="scratch limit"):
        BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    files = [path for path in (tmp_path / "scratch").rglob("*") if path.is_file()]
    assert sum(path.stat().st_size for path in files) <= 128
    assert not list((tmp_path / "scratch").rglob("*.json"))
    assert not list((tmp_path / "scratch").rglob("*.tmp"))
