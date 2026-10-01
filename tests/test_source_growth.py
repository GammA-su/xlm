"""Authored storage adversaries: source reuse and pre-write processing ceilings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition.plan import SourceDriftDetectedError
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.acquisition.source_growth import (
    GrowthLimitError,
    OutputBudget,
    ProcessingGrowth,
    bounded_json,
    check_result,
)
from xlm.data.acquisition.source_local import process_source_unit
from xlm.data.acquisition.source_reservations import SourceReservations
from xlm.data.sources import essential_web_local as pipe

URL = "https://huggingface.co/datasets/authored/source/resolve/" + "a" * 40 + "/file.parquet"
LIMITS = sp.TransferLimits(10000, 20000, 10, 0, 1, 10, max_state_bytes=2048)


def local(tmp_path: Path, *, complete: bool = True) -> pipe.Unit:
    payload = b"PAR1" + b"authored" * 32 + b"PAR1"
    prefix = payload if complete else payload[:64]
    part, state = tmp_path / "file.part", tmp_path / "file.state.json"
    part.write_bytes(prefix)
    state.write_text(
        json.dumps(
            {
                "version": sp.STATE_VERSION,
                "name": "file.parquet",
                "url": URL,
                "length": len(payload),
                "verified_bytes": len(prefix),
                "prefix_sha256": hashlib.sha256(prefix).hexdigest(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "complete": complete,
                "etag": '"authored"',
                "linked_etag": hashlib.sha256(payload).hexdigest(),
                "repo_commit": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    return pipe.Unit(
        "file", "file.parquet", URL, part, state, {"staging_dir": str(tmp_path / "out")}
    )


def inspect(unit: pipe.Unit) -> sp.SourceResume:
    return sp.inspect_source(
        URL, unit.partial, unit.state, name=unit.source_file, limits=LIMITS, revision="a" * 40
    )


def test_complete_reuse_zero_growth_and_existing_bytes_still_count(tmp_path: Path) -> None:
    unit = local(tmp_path)
    resume = inspect(unit)
    assert resume.kind == "local_complete_reuse" and resume.new_growth == 0
    assert resume.final_reservation == unit.partial.stat().st_size
    budget = sp.ScratchBudget(tmp_path, 8000, 0)
    occupied = budget.occupied()
    growth = ProcessingGrowth(
        output_bytes=1000, state_bytes=2048, metadata_bytes=100, progress_bytes=100
    )
    manager = SourceReservations(budget, budget, LIMITS, growth, "a" * 40)
    assert manager.reserve(unit)
    assert budget.occupied() == occupied
    assert (
        budget.reserved() == resume.final_reservation + growth.state_peak + growth.processing_peak
    )
    manager.processing(
        unit,
        sp.download_source(
            URL,
            unit.partial,
            unit.state,
            name=unit.source_file,
            limits=LIMITS,
            revision="a" * 40,
            require_complete=True,
        ),
    )
    assert unit.job is not None and unit.job["growth_reserved"] == growth.model_dump()


def test_partial_reserves_bounded_remaining_growth(tmp_path: Path) -> None:
    unit = local(tmp_path, complete=False)
    resume = inspect(unit)
    assert resume.kind == "resumable_partial"
    assert resume.new_growth == 264 - 64
    assert resume.final_reservation == 264
    with unit.partial.open("ab") as output:
        output.write(b"unverified tail")
    resume = inspect(unit)
    assert resume.new_growth == 200
    assert resume.final_reservation == unit.partial.stat().st_size + 200


def test_fresh_source_retains_full_bound(tmp_path: Path) -> None:
    unit = local(tmp_path)
    unit.partial.unlink()
    unit.state.unlink()
    result = inspect(unit)
    assert result.kind == "fresh_download"
    assert result.new_growth == result.final_reservation == LIMITS.max_file_bytes


@pytest.mark.parametrize("mutation", ["bytes", "url", "revision", "hash", "size", "prefix"])
def test_wrong_complete_source_never_enters_reuse(tmp_path: Path, mutation: str) -> None:
    unit = local(tmp_path)
    state = json.loads(unit.state.read_bytes())
    if mutation == "bytes":
        unit.partial.write_bytes(b"PAR1" + b"replaced" * 32 + b"PAR1")
    elif mutation == "url":
        state["url"] = URL + "wrong"
    elif mutation == "revision":
        state["repo_commit"] = "b" * 40
    elif mutation == "hash":
        state["sha256"] = "0" * 64
    elif mutation == "prefix":
        state["prefix_sha256"] = "0" * 64
    else:
        state["verified_bytes"] -= 1
    unit.state.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises((sp.SourceTransferError, SourceDriftDetectedError)):
        inspect(unit)


def test_processing_reservation_precedes_worker_and_rolls_back_on_refusal(tmp_path: Path) -> None:
    unit = local(tmp_path)
    budget = sp.ScratchBudget(tmp_path, 3000, 0)
    manager = SourceReservations(
        budget,
        budget,
        LIMITS,
        ProcessingGrowth(
            output_bytes=2000, state_bytes=100, metadata_bytes=100, progress_bytes=100
        ),
        "a" * 40,
    )
    ran: list[bool] = []

    def worker(job: Any) -> dict[str, Any]:
        ran.append(True)
        return {}

    with pytest.raises(sp.ScratchCapError):
        pipe.run_pipeline(
            [unit],
            limits=LIMITS,
            revision="a" * 40,
            download_workers=1,
            process_workers=0,
            scratch=budget,
            meter=None,
            deadline_seconds=10,
            identity_for=lambda u, t: {},
            on_done=lambda u, t, r: None,
            process=worker,
            reserve_unit=manager.reserve,
            release_unit=manager.release,
            before_process=manager.processing,
        )
    assert not ran and budget.reserved() == 0


def test_exact_canonical_serialized_byte_ceiling(tmp_path: Path) -> None:
    path = tmp_path / "documents.jsonl"
    budget = OutputBudget(4)
    writer = StreamingJsonlWriter(path, before_write=budget.charge)
    writer.write_line(b"abc\n")
    with pytest.raises(GrowthLimitError):
        writer.write_line(b"x\n")
    writer.close()
    assert path.read_bytes() == b"abc\n" and budget.used == 4


@pytest.mark.parametrize(
    "name", ["adaptation_rejections.jsonl.zst", "adaptation_summary.json", "ledger"]
)
def test_individual_and_aggregate_output_bound(tmp_path: Path, name: str) -> None:
    budget = OutputBudget(10)
    first = tmp_path / name
    with pytest.raises(GrowthLimitError):
        budget.write(first, b"x" * 7, 6)
    assert not first.exists() and budget.used == 0
    budget.write(first, b"x" * 6, 6)
    with pytest.raises(GrowthLimitError):
        budget.write(tmp_path / "second", b"x" * 5, 6)
    assert not (tmp_path / "second").exists() and budget.used == 6


def test_progress_state_and_atomic_overlap(tmp_path: Path) -> None:
    growth = ProcessingGrowth(
        output_bytes=100, metadata_bytes=10, progress_bytes=20, state_bytes=30
    )
    assert growth.processing_peak == 150 and growth.state_peak == 60
    progress = tmp_path / "progress.json"
    assert pipe.publish_progress(progress, {"rows": 1}, max_bytes=20)
    previous = progress.read_bytes()
    with pytest.raises(GrowthLimitError):
        pipe.publish_progress(progress, {"rows": "x" * 40}, max_bytes=20)
    assert progress.read_bytes() == previous
    assert not progress.with_suffix(".tmp").exists()
    state = tmp_path / "state.json"
    with pytest.raises(GrowthLimitError):
        sp._write_json(state, {"key": "x" * 40}, max_bytes=30)
    assert not state.exists() and not state.with_name(state.name + ".tmp").exists()
    with pytest.raises(GrowthLimitError):
        bounded_json({"key": "x" * 40}, 30)


def test_worker_requires_reservation_before_creating_files(tmp_path: Path) -> None:
    growth = ProcessingGrowth(output_bytes=100)
    with pytest.raises(GrowthLimitError, match="reservation"):
        process_source_unit(
            {
                "source_path": str(tmp_path / "none"),
                "identity_record": {"length": 12},
                "limits": {"processing_growth": growth.model_dump()},
            }
        )
    assert list(tmp_path.iterdir()) == []


def test_shared_final_enforcement_and_source_slack() -> None:
    growth = ProcessingGrowth(output_bytes=1000, source_max_bytes=1000)
    assert growth.for_source(300).output_bytes == 1700
    assert growth.for_source(1000).output_bytes == 1000
    limits = {
        "processing_growth": growth.model_dump(),
        "max_canonical_bytes_per_file": 900,
        "max_durable_bytes_per_file": 2000,
    }
    check_result({"canonical_bytes": 900, "processing_output_bytes": 1700}, limits, 300)
    for result in (
        {"canonical_bytes": 901, "processing_output_bytes": 1700},
        {"canonical_bytes": 900, "processing_output_bytes": 1701},
    ):
        with pytest.raises(GrowthLimitError):
            check_result(result, limits, 300)


def test_aggregate_reservations_and_retained_worker_leftovers(tmp_path: Path) -> None:
    growth = ProcessingGrowth(output_bytes=1000, metadata_bytes=10, progress_bytes=10)
    budget = sp.ScratchBudget(tmp_path, 2500, 0)
    manager = SourceReservations(budget, budget, LIMITS, growth, "a" * 40)
    units = [
        pipe.Unit(
            str(i),
            "file.parquet",
            None,
            tmp_path / f"{i}.part",
            tmp_path / f"{i}.state",
            {"staging_dir": str(tmp_path / str(i))},
            identity_record={"length": 100},
        )
        for i in range(2)
    ]
    previous = tmp_path / "0" / "abandoned"
    previous.parent.mkdir()
    previous.write_bytes(b"x" * 500)
    assert manager.reserve(units[0])
    assert not manager.reserve(units[1])
    assert budget.reserved() == 1530  # 500 retained PLUS 1030 new, never credited away.
    manager.release(units[0])
    assert manager.reserve(units[1])
    assert previous.stat().st_size == 500


def test_same_volume_budgets_share_physical_free_reserve(tmp_path: Path) -> None:
    first = sp.ScratchBudget(tmp_path / "scratch", 10000, 500, disk_free=lambda _: 2000)
    second = sp.ScratchBudget(tmp_path / "output", 10000, 500, disk_free=lambda _: 2000)
    assert first.reserve("source", 1000, first.root / "file")
    assert second.reserve("output", 1000, second.root / "file")
    manager = SourceReservations(
        first, second, LIMITS, ProcessingGrowth(output_bytes=100), "a" * 40
    )
    assert not manager.physical_fits()
    second.release("output")
    assert manager.physical_fits()


def test_durable_promotion_refuses_oversize_before_publication(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"x" * 100)
    with pytest.raises(sp.SourceTransferError, match="verified length"):
        sp.promote_source(
            source, target, {"length": 99, "sha256": "a" * 64}, max_metadata_bytes=1000
        )
    assert not target.exists() and not list(tmp_path.glob("*.tmp"))


def test_offline_admission_refuses_partial_and_never_submits_download(tmp_path: Path) -> None:
    unit = local(tmp_path, complete=False)
    budget = sp.ScratchBudget(tmp_path, 100000, 0)
    manager = SourceReservations(
        budget, budget, LIMITS, ProcessingGrowth(output_bytes=1000), "a" * 40, offline=True
    )
    with pytest.raises(sp.ScratchCapError, match="offline"):
        manager.reserve(unit)
    assert budget.reserved() == 0


def test_new_essential_compatibility_link_is_exact_and_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.evidence_v2 import canonical
    from xlm.data.sources import essential_web_recovery as recovery

    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo / recovery.MANIFEST).read_bytes())
    previous = json.loads((repo / recovery.MALFORMED_FIX).read_bytes())
    current = json.loads((repo / recovery.SOURCE_GROWTH_FIX).read_bytes())
    assert current["previous_code"] == previous["code"]
    assert current["previous_digest"] == previous["digest"]
    assert recovery.compatible_code(repo, manifest)
    for relative, _, _ in recovery.COMPATIBILITY:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo / relative).read_bytes())
    monkeypatch.setattr(recovery, "code_identity", lambda _: current["code"])
    assert recovery.compatible_code(tmp_path, manifest)
    current["code"]["src/xlm/data/adapters/columns.py"] = "0" * 64
    current["digest"] = canonical.digest({k: v for k, v in current.items() if k != "digest"})
    (tmp_path / recovery.SOURCE_GROWTH_FIX).write_text(json.dumps(current))
    assert not recovery.compatible_code(tmp_path, manifest)
