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
from xlm.data.acquisition.written import WrittenPayload

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


def evidence(partial: Path) -> WrittenPayload:
    # Recreate an actual writer and incremental digest for this authored fixture.
    digest = hashlib.sha256()
    with partial.open("wb") as stream:
        stream.write(PAYLOAD)
        digest.update(PAYLOAD)
        stream.flush()
        os.fsync(stream.fileno())
        written = WrittenPayload.capture(stream, len(PAYLOAD), digest)
    return written


@pytest.mark.parametrize("selected", [False, True])
def test_closed_writer_publishes_same_file_without_rehash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool
) -> None:
    fetcher, name, partial = fixture(tmp_path, selected)
    written = evidence(partial)
    original_link = os.link
    links = []

    def link(source: Path, target: Path) -> None:
        assert written.stream.closed
        before = source.stat()
        original_link(source, target)
        after = target.stat()
        assert (
            (before.st_dev, before.st_ino, before.st_size)
            == (after.st_dev, after.st_ino, after.st_size)
            == (written.device, written.inode, written.size)
        )
        assert source.stat().st_nlink == after.st_nlink == 2
        links.append((before.st_dev, before.st_ino))

    def forbidden(*args: Any) -> str:
        raise AssertionError("fresh publication reread the payload")

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(AtomicFileWriter, "hash_durable_prefix", forbidden)
    publish_output(fetcher, name, partial, written.size, written.sha256, 1, written=written)
    destination = fetcher.output_dir / name
    assert len(links) == 1 and not partial.exists()
    assert destination.stat().st_nlink == 1 and destination.read_bytes() == PAYLOAD
    assert fetcher.journal.state.file_progress[name].status == "completed"
    assert fetcher.journal.state.accounting.occupancy["output"] == len(PAYLOAD)
    assert fetcher.journal.state.accounting.reservations["output"] == {}


@pytest.mark.parametrize("conflict", ["hash", "size", "identity", "open", "intent"])
def test_fresh_publication_refuses_conflicting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    fetcher, name, partial = fixture(tmp_path, False)
    written = evidence(partial)
    size, digest = written.size, written.sha256
    open_stream = None
    if conflict == "hash":
        digest = "0" * 64
    elif conflict == "size":
        size += 1
    elif conflict == "identity":
        replacement = partial.with_suffix(".replacement")
        replacement.write_bytes(PAYLOAD)
        os.replace(replacement, partial)
    elif conflict == "open":
        open_stream = partial.open("r+b")
        written = WrittenPayload.capture(open_stream, size, hashlib.sha256(PAYLOAD))
    else:
        reserve = fetcher.capacity_mgr.reserve_disk_space

        def changed(*args: Any, **kwargs: Any) -> str:
            token = reserve(*args, **kwargs)
            with fetcher.journal.transaction() as state:
                intent = state.file_progress[name].publication
                assert intent is not None
                intent.content_sha256 = "0" * 64
            return token

        monkeypatch.setattr(fetcher.capacity_mgr, "reserve_disk_space", changed)
    try:
        with pytest.raises(ProgressCorruptionError):
            publish_output(fetcher, name, partial, size, digest, 1, written=written)
    finally:
        if open_stream:
            open_stream.close()
    assert not (fetcher.output_dir / name).exists()
    assert fetcher.journal.state.accounting.occupancy.get("output", 0) == 0


@pytest.mark.parametrize("phase", ["before_link", "after_link", "after_unlink"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_fresh_evidence_does_not_survive_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, corrupt: bool
) -> None:
    fetcher, name, partial = fixture(tmp_path, False)
    written = evidence(partial)
    original_link, original_unlink = os.link, Path.unlink

    def link(*args: Any) -> None:
        if phase == "before_link":
            raise RuntimeError("interrupted")
        original_link(*args)
        if phase == "after_link":
            raise RuntimeError("interrupted")

    def unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        original_unlink(path, *args, **kwargs)
        if path == partial and phase == "after_unlink":
            raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", link)
        patch.setattr(Path, "unlink", unlink)
        with pytest.raises(RuntimeError, match="interrupted"):
            publish_output(fetcher, name, partial, written.size, written.sha256, 1, written=written)
    destination = fetcher.output_dir / name
    if corrupt:
        (partial if partial.exists() else destination).write_bytes(b"!" * len(PAYLOAD))
    restarted = BoundedFetcher(fetcher.plan, fetcher.scratch_dir, fetcher.output_dir)
    original_hash = AtomicFileWriter.hash_durable_prefix
    reads = []

    def hashed(path: Path, size: int) -> str:
        reads.append(path)
        return original_hash(path, size)

    monkeypatch.setattr(AtomicFileWriter, "hash_durable_prefix", hashed)
    if corrupt:
        with pytest.raises(ProgressCorruptionError, match="checksum"):
            reconcile_publications(restarted)
    else:
        reconcile_publications(restarted)
        assert destination.read_bytes() == PAYLOAD
    assert reads, "restart must cryptographically verify the surviving payload"
