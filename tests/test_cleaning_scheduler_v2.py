"""Bounded whole-unit dispatch, deterministic publication, and failed-group rejection."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any

import pytest

from test_cleaning_throughput import gen_mixed
from xlm.data.cleaning import sharded
from xlm.data.cleaning.quarantine import QuarantinePolicy


def run(root: Path, source: Path, mode: str, workers: int = 2) -> Any:
    return sharded.run_sharded_clean(
        input_path=source,
        output_dir=root / "cleaned",
        preset="prose",
        workers=workers,
        input_shard_bytes=2048,
        output_shard_bytes=4096,
        quarantine_dir=root / "quarantine",
        quarantine_shard_bytes=None,
        quarantine_policy=QuarantinePolicy(max_records=100, store_previews=False),
        max_docs=100,
        max_input_bytes=1024**2,
        scheduling=mode,
    )


def test_dynamic_and_static_shard_manifests_exact(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    source.write_text("\n".join(gen_mixed(80, 293)) + "\n", encoding="utf-8")
    a = run(tmp_path / "static", source, "static")
    b = run(tmp_path / "dynamic", source, "dynamic")
    assert a[-1].to_dict() == b[-1].to_dict()
    for entry in a[-1].shards:
        assert (tmp_path / "static/cleaned" / entry.path).read_bytes() == (
            tmp_path / "dynamic/cleaned" / entry.path
        ).read_bytes()


def test_dynamic_pending_futures_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.jsonl"
    source.write_text("\n".join(gen_mixed(80, 294)) + "\n", encoding="utf-8")
    sizes = []
    original_wait = concurrent.futures.wait

    def observed_wait(pending: Any, **kwargs: Any) -> Any:
        sizes.append(len(pending))
        return original_wait(pending, **kwargs)

    class ImmediatePool:
        def __init__(self, **kwargs: Any) -> None:
            kwargs["initializer"](*kwargs["initargs"])

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def submit(self, function: Any, spec: Any) -> Any:
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            future.set_result(function(spec))
            return future

    monkeypatch.setattr(concurrent.futures, "wait", observed_wait)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", ImmediatePool)
    result = run(tmp_path / "out", source, "dynamic")
    assert result[0].total_input_docs == 80
    assert len(sizes) > 1 and max(sizes) == 4


@pytest.mark.parametrize("defect", ["missing", "duplicate"])
def test_failed_unit_inventory_cannot_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    source = tmp_path / "input.jsonl"
    source.write_text("\n".join(gen_mixed(10, 295)) + "\n", encoding="utf-8")
    original = sharded.clean_unit_worker

    def broken(spec: Any) -> Any:
        result = original(spec)
        if defect == "missing":
            result["units"].pop()
        else:
            result["units"].append(result["units"][0])
        return result

    monkeypatch.setattr(sharded, "clean_unit_worker", broken)
    with pytest.raises(ValueError, match="missing or duplicate"):
        run(tmp_path / "out", source, "dynamic", workers=1)
    assert not (tmp_path / "out/cleaned/clean-manifest.json").exists()
    assert not (tmp_path / "out/cleaned/cleaning_summary.json").exists()
