"""Synthetic whole-file pipeline failures and digest-bound growth planning."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from test_source_plan import PIN
from test_source_run import World, admitted, tokens_for_files
from test_source_run import served as served
from test_source_run import world as world
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_local
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition.source_growth import GrowthLimitError, ProcessingGrowth
from xlm.data.adapters.rejections import build_summary


def test_benchmark_cap_formula_and_identity(world: World) -> None:
    arguments: dict[str, Any] = dict(
        source_key="ultrax",
        label="b3",
        pin=PIN,
        entries=[{"rank": 0, "file": world.ordered()[0]}],
        layout=world.layout,
        seed=0,
        admission={"probe_fingerprint": "f" * 64},
        download_workers=1,
        process_workers=0,
    )
    record = bench.build_benchmark(**arguments, retained_scratch_bytes=1234)
    limits = record["limits"]
    growth = ProcessingGrowth.model_validate(limits["processing_growth"])
    assert (
        limits["scratch_cap_bytes"]
        == 1234 + limits["max_file_bytes"] + growth.processing_peak + growth.state_peak
    )
    assert record == bench.build_benchmark(**arguments, retained_scratch_bytes=1234)
    changed = bench.build_benchmark(**arguments, retained_scratch_bytes=1235)
    assert record["digest"] != changed["digest"]
    assert record["acquisition_plan"]["plan_hash"] != changed["acquisition_plan"]["plan_hash"]
    minted = bench._minted(record)
    altered = minted.model_copy(
        update={
            "source_processing_growth": growth.model_copy(
                update={"progress_bytes": growth.progress_bytes - 1}
            )
        }
    )
    assert altered.compute_behavioral_hash() != minted.plan_hash
    # Adding the optional contract never changes the selection identity.
    assert altered.compute_selection_hash() == minted.compute_selection_hash()
    # Old records without a growth operand still reconstruct their old identity.
    legacy = copy.deepcopy(record)
    legacy["limits"].pop("processing_growth")
    old = planner.acquisition_plan(
        PIN, minted.selected_files, minted.limits, 0, "benchmark b3; not production data"
    )
    legacy["acquisition_plan"]["plan_hash"] = old.plan_hash
    assert bench._minted(legacy).plan_hash == old.plan_hash


@pytest.mark.parametrize("failure", ["canonical", "ledger", "summary", "compressed_ledger"])
def test_production_never_publishes_an_oversized_unit(
    world: World, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    real = planner.plan_limits

    def limits(*args: Any, **kwargs: Any) -> Any:
        policy, acquisition = real(*args, **kwargs)
        if failure == "canonical":
            policy["max_canonical_bytes_per_file"] = 1
        elif failure == "ledger":
            policy["max_ledger_bytes"] = 1
        elif failure == "compressed_ledger":
            policy["max_ledger_bytes"] = 10000
        return policy, acquisition

    monkeypatch.setattr(planner, "plan_limits", limits)
    record = world.plan(tokens_for_files(world, 1))
    if failure == "compressed_ledger":
        monkeypatch.setattr(
            source_local,
            "compress_ledger",
            lambda _: b"x" * (record["limits"]["max_ledger_bytes"] + 1),
        )
    if failure == "summary":
        original = build_summary

        def oversized(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = original(*args, **kwargs)
            result["authored_oversize"] = "x" * (
                2 * record["limits"]["processing_growth"]["metadata_bytes"]
            )
            return result

        monkeypatch.setattr(source_local, "build_summary", oversized)
    with pytest.raises((GrowthLimitError, source_local.SourceAdaptError)):
        world.run(download_workers=1)
    assert runner.resume_state(world.roots, record)["sealed"] == 0
    assert not list(world.roots.canonical.glob("p*/f*/receipt.json"))
    assert len(list(world.roots.plan_dir(1).glob("performance-*.json"))) == 1


def test_benchmark_has_the_same_canonical_refusal(world: World) -> None:
    record = bench.build_benchmark(
        source_key="ultrax",
        label="bounded",
        pin=PIN,
        entries=[{"rank": 0, "file": world.ordered()[0]}],
        layout=world.layout,
        seed=0,
        admission={"probe_fingerprint": "f" * 64},
        download_workers=1,
        process_workers=0,
    )
    record["limits"]["max_canonical_bytes_per_file"] = 1
    runner.self_digest(record)
    bench.store_benchmark(world.roots, record)
    bench.authorize_benchmark(world.roots, "bounded", record["digest"], "authored", admitted)
    with pytest.raises(GrowthLimitError, match="canonical"):
        bench.run_benchmark(
            world.roots, "bounded", admitted=admitted, stream=world.stream, url_for=world.url
        )
    receipt = runner.read_json(bench.benchmark_dir(world.roots, "bounded") / "performance-00.json")
    assert receipt["outcome"]["status"] == "failed"
    assert receipt["processing"]["units"] == 0
    assert world.roots.scratch("bench-bounded", "f00000.parquet.part").is_file()


def test_process_pool_enforces_the_same_envelope(world: World) -> None:
    world.plan(tokens_for_files(world, 1))
    report = world.run(download_workers=1, process_workers=1)
    assert report["outcome"]["status"] == "completed"


def test_old_unsealed_plan_is_refused_without_changing_it(tmp_path: Path) -> None:
    roots = runner.Roots(tmp_path / "durable", tmp_path / "scratch", "source")
    with pytest.raises(runner.RunError, match="new plan"):
        runner.prepare_units(
            roots,
            {"source": PIN, "limits": {}},
            None,  # type: ignore[arg-type]
            [{"rank": 0, "file": "a"}],
            "old",
            tmp_path / "stage",
            lambda _: "",
        )


def test_offline_benchmark_uses_verified_complete_source_without_requests(world: World) -> None:
    name = world.ordered()[0]
    record = bench.build_benchmark(
        source_key="ultrax",
        label="offline",
        pin=PIN,
        entries=[{"rank": 0, "file": name}],
        layout=world.layout,
        seed=0,
        admission={"probe_fingerprint": "f" * 64},
        download_workers=1,
        process_workers=0,
    )
    bench.store_benchmark(world.roots, record)
    bench.authorize_benchmark(world.roots, "offline", record["digest"], "authored", admitted)
    with pytest.raises(sp.ScratchCapError, match="offline"):
        bench.run_benchmark(
            world.roots,
            "offline",
            admitted=admitted,
            stream=world.stream,
            url_for=world.url,
            offline=True,
        )
    assert world.served.requests == []
    sp.download_source(
        world.url(name),
        world.roots.scratch("bench-offline", "f00000.parquet.part"),
        world.roots.scratch("bench-offline", "f00000.state.json"),
        name=name,
        limits=runner.transfer_limits(record),
        revision=PIN["revision"],
    )
    requests = list(world.served.requests)
    receipt = bench.run_benchmark(
        world.roots,
        "offline",
        admitted=admitted,
        stream=world.stream,
        url_for=world.url,
        offline=True,
    )
    assert receipt["outcome"]["status"] == "completed"
    assert receipt["transfer"]["cache_hits"] == 1
    assert world.served.requests == requests
