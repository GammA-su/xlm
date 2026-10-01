"""Authored fixtures: preserve scratch history without breaking verified donor reuse."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from test_source_record_bound import FINEPDFS, _authorized_pair, _donor_download, admitted
from xlm.data.acquisition import source_archive as archive
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_run as runner


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[runner.Roots, Path]:
    roots = _authorized_pair(tmp_path, monkeypatch)
    _donor_download(roots, b"PAR1" + bytes(range(128)) + b"PAR1")
    staging = roots.scratch("bench-b1", "staging", "unit")
    staging.mkdir(parents=True)
    (staging / "documents.jsonl").write_bytes(b'{"text":"authored fixture"}\n')
    return roots, tmp_path / "history" / "finepdfs" / "bench-b1"


def test_archive_preserves_existing_adoption_and_future_donor_reuse(
    world: tuple[runner.Roots, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    roots, destination = world
    adoption = bench.adopt_benchmark_download(roots, "b2", "b1")
    part = roots.scratch("bench-b2", "f00000.parquet.part")
    state = roots.scratch("bench-b2", "f00000.state.json")
    before_part, before_state = sp.file_sha256(part), state.read_bytes()
    original = roots.scratch("bench-b1")
    before = archive.inventory(original, 100_000)
    budget = sp.ScratchBudget(roots.scratch(), 100_000, 0)
    occupied = budget.occupied()
    receipt = archive.archive_benchmark(roots, "b1", destination, max_bytes=100_000)
    assert receipt["files"] == before == archive.inventory(destination, 100_000)
    assert not original.exists()
    assert budget.occupied() == occupied - sum(f["size"] for f in before)
    assert budget.occupied() == part.stat().st_size + state.stat().st_size
    assert sp.file_sha256(part) == before_part and state.read_bytes() == before_state
    assert part.samefile(destination / "f00000.parquet.part")
    assert runner.read_json(bench.benchmark_dir(roots, "b2") / "adoption.json") == adoption
    assert archive.archive_benchmark(roots, "b1", destination, max_bytes=100_000) == receipt
    donor = runner.read_json(bench.benchmark_dir(roots, "b1") / "benchmark.json")
    assert archive.donor_directory(roots, "b1", donor) == destination.resolve()
    # Remove only the synthetic target, then prove adoption finds the archived donor.
    part.unlink()
    state.unlink()
    assert bench.adopt_benchmark_download(roots, "b2", "b1") == adoption

    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline reuse attempted network")

    monkeypatch.setattr(urllib.request, "build_opener", no_network)
    record = runner.read_json(bench.benchmark_dir(roots, "b2") / "benchmark.json")
    result = sp.download_source(
        runner.source_url(FINEPDFS, str(record["files"][0]["file"])),
        part,
        state,
        name=str(record["files"][0]["file"]),
        limits=runner.transfer_limits(record),
        revision=FINEPDFS["revision"],
    )
    assert result.cache_hit and result.requests == result.transferred_bytes == 0
    with pytest.raises(runner.RunError, match="scratch is archived"):
        bench.run_benchmark(roots, "b1", admitted=admitted)


@pytest.mark.parametrize("mutation", ["bytes", "state", "extra", "identity", "original"])
def test_archive_resolution_fails_closed(world: tuple[runner.Roots, Path], mutation: str) -> None:
    roots, destination = world
    receipt = archive.archive_benchmark(roots, "b1", destination, max_bytes=100_000)
    if mutation == "bytes":
        (destination / "f00000.parquet.part").write_bytes(b"replaced")
    elif mutation == "state":
        (destination / "f00000.state.json").write_text("{}", encoding="utf-8")
    elif mutation == "extra":
        (destination / "extra").write_bytes(b"extra")
    elif mutation == "original":
        roots.scratch("bench-b1").mkdir()
    else:
        receipt["benchmark_digest"] = "0" * 64
        (bench.benchmark_dir(roots, "b1") / archive.RECEIPT).write_text(
            json.dumps(runner.self_digest(receipt)), encoding="utf-8"
        )
    with pytest.raises(runner.RunError, match="archive"):
        bench.adopt_benchmark_download(roots, "b2", "b1")
    assert not roots.scratch("bench-b2", "f00000.parquet.part").exists()


def test_archive_resumes_after_receipt_publication_failure(
    world: tuple[runner.Roots, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    roots, destination = world
    real = runner.write_once

    def fail_receipt(path: Path, record: Any) -> bool:
        if path.name == archive.RECEIPT:
            raise OSError("authored publication failure")
        return real(path, record)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "write_once", fail_receipt)
        with pytest.raises(OSError, match="publication failure"):
            archive.archive_benchmark(roots, "b1", destination, max_bytes=100_000)
    with pytest.raises(runner.RunError, match="pending verification"):
        bench.adopt_benchmark_download(roots, "b2", "b1")
    assert destination.is_dir() and not roots.scratch("bench-b1").exists()
    archive.archive_benchmark(roots, "b1", destination, max_bytes=100_000)
    assert bench.adopt_benchmark_download(roots, "b2", "b1")["files"]


@pytest.mark.parametrize("bad", ["active", "durable", "exists", "bound"])
def test_archive_refuses_unsafe_moves(world: tuple[runner.Roots, Path], bad: str) -> None:
    roots, destination = world
    before = archive.inventory(roots.scratch("bench-b1"), 100_000)
    if bad == "active":
        destination = roots.scratch("history")
    elif bad == "durable":
        destination = roots.data_root / "archive"
    elif bad == "exists":
        destination.mkdir(parents=True)
    with pytest.raises(runner.RunError):
        archive.archive_benchmark(
            roots, "b1", destination, max_bytes=1 if bad == "bound" else 100_000
        )
    assert archive.inventory(roots.scratch("bench-b1"), 100_000) == before


def test_run_rechecks_archival_after_admission(
    world: tuple[runner.Roots, Path],
) -> None:
    roots, destination = world
    bench.adopt_benchmark_download(roots, "b2", "b1")
    b2_archive = destination.with_name("bench-b2")

    def archive_during_admission(plan: Any) -> None:
        archive.archive_benchmark(roots, "b2", b2_archive, max_bytes=100_000)

    with pytest.raises(runner.RunError, match="scratch is archived"):
        bench.run_benchmark(roots, "b2", admitted=archive_during_admission)
    assert not roots.scratch("bench-b2").exists()
    assert (b2_archive / "f00000.parquet.part").is_file()
    with pytest.raises(runner.RunError, match="target is archived"):
        bench.adopt_benchmark_download(roots, "b2", "b1")
