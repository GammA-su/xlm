"""Publication intent proves ownership, and completion has one journal commit point."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from test_acquisition_leases import plan_for
from xlm.data.acquisition.disk import AtomicFileWriter
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.progress import ProgressCorruptionError
from xlm.data.acquisition.publication import publish_output, reconcile_publications

PAYLOAD = b'{"text":"authored publication"}\n'


def fixture(root: Path, selected: bool) -> tuple[BoundedFetcher, str, Path]:
    kwargs: dict[str, Any] = (
        {"mode": "selected_records", "row_ranges": {"rows.jsonl": (0, 1)}} if selected else {}
    )
    plan = plan_for(47183, ["rows.jsonl"], **kwargs)
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    fetcher.partial_dir.mkdir(parents=True)
    fetcher.output_dir.mkdir()
    name = "selected_records.jsonl" if selected else "rows.jsonl"
    partial = fetcher.partial_dir / ("selection-owned.part" if selected else "rows.jsonl.part")
    token = fetcher.capacity_mgr.reserve_disk_space(fetcher.partial_dir, len(PAYLOAD))
    with partial.open("wb") as stream:
        stream.write(PAYLOAD)
        stream.flush()
        os.fsync(stream.fileno())
    fetcher.capacity_mgr.settle("temp", token, len(PAYLOAD))
    return fetcher, name, partial


def admit(fetcher: BoundedFetcher, name: str, partial: Path) -> None:
    publish_output(fetcher, name, partial, len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest(), 1)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("phase", ["before_link", "after_link", "after_unlink", "journal_replace"])
def test_interrupted_publication_recovers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool, phase: str
) -> None:
    fetcher, name, partial = fixture(tmp_path, selected)
    original_link, original_unlink, original_replace = os.link, Path.unlink, os.replace

    def link(*args: Any, **kwargs: Any) -> None:
        if phase == "before_link":
            raise RuntimeError("authored interruption")
        original_link(*args, **kwargs)
        if phase == "after_link":
            raise RuntimeError("authored interruption")

    def unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        original_unlink(path, *args, **kwargs)
        if path == partial and phase == "after_unlink":
            raise RuntimeError("authored interruption")

    def replace(source: Any, destination: Any) -> None:
        if phase == "journal_replace" and Path(destination) == fetcher.journal_path:
            fp = fetcher.journal.state.file_progress.get(name)
            if fp and fp.status == "completed":
                raise RuntimeError("authored interruption")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", link)
        patch.setattr(Path, "unlink", unlink)
        patch.setattr(os, "replace", replace)
        with pytest.raises(RuntimeError, match="authored interruption"):
            admit(fetcher, name, partial)
    restarted = BoundedFetcher(fetcher.plan, fetcher.scratch_dir, fetcher.output_dir)
    before = restarted.journal.state.accounting.consumed.copy()
    assert restarted.journal.state.file_progress[name].publication is not None
    reconcile_publications(restarted)
    for _ in range(3):
        restarted = BoundedFetcher(fetcher.plan, fetcher.scratch_dir, fetcher.output_dir)
        reconcile_publications(restarted)
        state = restarted.journal.state
        assert state.accounting.occupancy["output"] == len(PAYLOAD)
        assert state.accounting.reservations["output"] == {}
        assert state.accounting.consumed == before
        assert state.file_progress[name].status == "completed"
        assert state.file_progress[name].publication is None
        assert (fetcher.output_dir / name).read_bytes() == PAYLOAD
        assert not partial.exists()


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("conflict", ["foreign", "hash", "plan", "reservation", "missing", "path"])
def test_recovery_refuses_conflicting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool, conflict: str
) -> None:
    fetcher, name, partial = fixture(tmp_path, selected)
    with monkeypatch.context() as patch:
        patch.setattr(
            AtomicFileWriter,
            "atomic_complete",
            lambda *_: (_ for _ in ()).throw(RuntimeError("before publication")),
        )
        with pytest.raises(RuntimeError, match="before publication"):
            admit(fetcher, name, partial)
    destination = fetcher.output_dir / name
    if conflict == "foreign":
        destination.write_bytes(PAYLOAD)  # Same bytes, unrelated inode: never adopted.
    elif conflict == "hash":
        os.link(partial, destination)
        destination.write_bytes(b"!" * len(PAYLOAD))
    elif conflict == "missing":
        partial.unlink()
    else:
        with fetcher.journal.transaction() as state:
            intent = state.file_progress[name].publication
            assert intent is not None
            if conflict == "plan":
                intent.plan_hash = "unrelated"
            elif conflict == "path":
                intent.partial_path = "../foreign"
            else:
                del state.accounting.reservations["output"][intent.output_token]
    existing = destination.read_bytes() if destination.exists() else None
    with pytest.raises((ProgressCorruptionError, ValueError)):
        reconcile_publications(fetcher)
    fetcher.journal.save()
    assert fetcher.journal.state.file_progress[name].status != "completed"
    assert fetcher.journal.state.accounting.occupancy.get("output", 0) == 0
    assert (destination.read_bytes() if destination.exists() else None) == existing
