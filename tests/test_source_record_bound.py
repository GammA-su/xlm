"""Source-specific record byte bounds and offline reuse of a benchmark's complete download."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.records import RecordLimitError

MIB = 1024 * 1024
FINEPDFS = {
    "source_id": "finepdfs_edu",
    "view_id": "eng_Latn",
    "component_id": "finepdfs_en",
    "provider": "huggingface",
    "repository": "HuggingFaceFW/finepdfs-edu",
    "revision": "9cfabe2127faca99b3d5c4dc6d1fcb397399ebde",
    "adapter_id": "finepdfs_en",
}
ULTRAX = {
    **FINEPDFS,
    "source_id": "ultrax_ultrafineweb",
    "view_id": "UltraX-Ultra-FineWeb",
    "component_id": "ultrax_ultrafineweb",
    "repository": "openbmb/UltraX-Preview",
    "revision": "a88527587389fd4ab352e9ad1273f4c0a234d8df",
    "adapter_id": "ultrax_ultrafineweb",
}
FILE = "data/eng_Latn/train/000_00083.parquet"
#: The policy keys every generic plan had before source-specific bounds existed.
GENERIC_KEYS = {
    "rules",
    "max_file_bytes",
    "max_rows_per_file",
    "max_decoded_bytes_per_file",
    "max_record_bytes",
    "max_parser_bytes",
    "max_ledger_bytes",
    "max_decompression_ratio",
    "max_requests_per_file",
    "max_retries",
    "request_timeout_seconds",
    "file_deadline_seconds",
    "plan_deadline_seconds",
    "max_durable_bytes_per_file",
    "max_canonical_bytes_per_file",
    "scratch_cap_bytes",
    "processing_growth",
    "scratch_min_free_bytes",
    "max_in_flight_files",
    "download_workers",
    "process_workers",
    "download_workers_max",
    "process_workers_max",
}


def layout(pin: dict[str, str]) -> tp.SourceLayout:
    return tp.SourceLayout(
        source_id=pin["source_id"],
        file_bytes=1_000_000,
        group_rows=100,
        group_bytes=10_000,
        projected_group_bytes=4_000,
        range_requests_per_group=2.0,
        metadata_requests_per_file=4,
        canonical_bytes_per_row=50.0,
        range_record_bytes_per_row=60.0,
        metadata_bytes_per_file=1_000,
        source_files=None,
        evidence={"rows": "0" * 64},
    )


def admitted(plan: Any) -> None:
    assert plan.source_id == FINEPDFS["source_id"]


def benchmark(roots: runner.Roots, label: str, pin: dict[str, str] = FINEPDFS) -> dict[str, Any]:
    record = bench.build_benchmark(
        source_key="finepdfs",
        label=label,
        pin=pin,
        entries=[{"rank": None, "file": FILE, "reason": "named calibration file"}],
        layout=layout(pin),
        seed=0,
        admission={"probe_fingerprint": "f" * 64},
        download_workers=1,
        process_workers=1,
    )
    bench.store_benchmark(roots, record)
    return record


def test_generic_bound_is_unchanged_and_finepdfs_is_explicit() -> None:
    mode = tp.TransportMode.WHOLE_FILE_LOCAL
    generic, generic_limits = planner.plan_limits(2, layout(ULTRAX), mode, ULTRAX)
    assert generic["max_record_bytes"] == generic_limits.max_record_bytes == 8 * MIB
    assert set(generic) == GENERIC_KEYS
    pdfs, pdfs_limits = planner.plan_limits(2, layout(FINEPDFS), mode, FINEPDFS)
    assert pdfs["max_record_bytes"] == pdfs_limits.max_record_bytes == 32 * MIB
    assert pdfs_limits.max_record_bytes <= pdfs_limits.max_parser_bytes == 32 * MIB
    assert pdfs["max_record_bytes_basis"].startswith("finepdfs-record-v1")
    assert set(pdfs) - GENERIC_KEYS == {
        "max_record_bytes_basis",
        "row_group_parallel",
        "row_group_parallel_basis",
    }
    assert pdfs["row_group_parallel"]["workers"] == 4
    assert pdfs["row_group_parallel_basis"].startswith("finepdfs-intrafile-v1")
    # Another view of the same source keeps the generic bound.
    other = {**FINEPDFS, "view_id": "fra_Latn"}
    assert planner.plan_limits(1, layout(other), mode, other)[0]["max_record_bytes"] == 8 * MIB


def test_record_bound_never_exceeds_the_parser_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    key = ("finepdfs_edu", "eng_Latn")
    monkeypatch.setitem(planner.SOURCE_RECORD_BYTES, key, (64 * MIB, "too large"))
    with pytest.raises(planner.PlanError, match="parser ceiling"):
        planner.record_bound(*key)


def _parquet(path: Path, texts: list[str]) -> None:
    pq.write_table(pa.table({"text": texts}), path, row_group_size=1)


def _payloads(path: Path, bound: int) -> list[int]:
    return [
        row
        for row, _ in sp.selected_payloads(
            path,
            source_file=FILE,
            locator={"source_id": "finepdfs_edu"},
            etag='"e"',
            columns=["text"],
            max_record_bytes=bound,
            max_parser_bytes=32 * MIB,
            max_decoded_bytes=1024 * MIB,
        )
    ]


def test_a_9_06_mb_row_passes_the_finepdfs_bound_and_a_larger_row_fails_closed(
    tmp_path: Path,
) -> None:
    bound = planner.record_bound("finepdfs_edu", "eng_Latn")[0]
    observed = tmp_path / "observed.parquet"
    _parquet(observed, ["a", "b" * 9_064_000])
    with pytest.raises(RecordLimitError, match="row=1 encoded_bytes="):
        _payloads(observed, planner.MAX_RECORD_BYTES)
    assert _payloads(observed, bound) == [0, 1]
    oversized = tmp_path / "oversized.parquet"
    _parquet(oversized, ["a", "c" * bound])
    with pytest.raises(RecordLimitError, match=f"limit={bound}"):
        _payloads(oversized, bound)


def test_changed_bound_is_a_new_benchmark_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = runner.Roots(tmp_path / "data", tmp_path / "scratch", "finepdfs")
    with monkeypatch.context() as patch:
        patch.setattr(planner, "SOURCE_RECORD_BYTES", {})
        old = benchmark(roots, "b1")
    new = benchmark(roots, "b2")
    assert old["limits"]["max_record_bytes"] == 8 * MIB
    assert new["limits"]["max_record_bytes"] == 32 * MIB
    assert old["digest"] != new["digest"]
    assert old["acquisition_plan"]["plan_hash"] != new["acquisition_plan"]["plan_hash"]
    bench.authorize_benchmark(roots, "b1", old["digest"], "tester", admitted)
    with pytest.raises(runner.RunError, match="digest/operator does not match"):
        bench.authorize_benchmark(roots, "b2", old["digest"], "tester", admitted)
    with pytest.raises(runner.RunError, match="not authorized|missing"):
        bench.run_benchmark(roots, "b2", admitted=admitted, stream=io.StringIO())


def _donor_download(roots: runner.Roots, payload: bytes, **changes: Any) -> Path:
    sha = hashlib.sha256(payload).hexdigest()
    part = roots.scratch("bench-b1", "f00000.parquet.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(payload)
    state = {
        "version": sp.STATE_VERSION,
        "name": FILE,
        "url": runner.source_url(FINEPDFS, FILE),
        "complete": True,
        "length": len(payload),
        "verified_bytes": len(payload),
        "prefix_sha256": sha,
        "sha256": sha,
        "etag": '"' + "d" * 64 + '"',
        "linked_etag": f'"{sha}"',
        "linked_size": len(payload),
        "repo_commit": FINEPDFS["revision"],
        "xet_hash": "d" * 64,
        "charged_bytes": len(payload),
        "requests": 2,
        "redirects": 1,
        "retries": 0,
        **changes,
    }
    roots.scratch("bench-b1", "f00000.state.json").write_text(json.dumps(state), encoding="utf-8")
    return part


def _authorized_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> runner.Roots:
    roots = runner.Roots(tmp_path / "data", tmp_path / "scratch", "finepdfs")
    with monkeypatch.context() as patch:
        patch.setattr(planner, "SOURCE_RECORD_BYTES", {})
        benchmark(roots, "b1")
    record = benchmark(roots, "b2")
    bench.authorize_benchmark(roots, "b2", record["digest"], "tester", admitted)
    return roots


def test_adopted_download_is_a_verified_local_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _authorized_pair(tmp_path, monkeypatch)
    payload = b"PAR1" + bytes(range(256)) * 64 + b"PAR1"
    donor = _donor_download(roots, payload)
    receipt = bench.adopt_benchmark_download(roots, "b2", "b1")
    sha = hashlib.sha256(payload).hexdigest()
    assert receipt["files"] == [
        {
            "file": FILE,
            "length": len(payload),
            "benchmark": "b1",
            "digest": receipt["files"][0]["digest"],
            "sha256": sha,
        }
    ]
    assert donor.read_bytes() == payload  # the donor keeps its file
    target = roots.scratch("bench-b2", "f00000.parquet.part")
    state = json.loads(roots.scratch("bench-b2", "f00000.state.json").read_text())
    assert state["charged_bytes"] == 0 and state["adopted_from"]["benchmark"] == "b1"
    record = runner.read_json(bench.benchmark_dir(roots, "b2") / "benchmark.json")
    # No network: a complete verified state never opens a connection.
    result = sp.download_source(
        runner.source_url(FINEPDFS, FILE),
        target,
        roots.scratch("bench-b2", "f00000.state.json"),
        name=FILE,
        limits=runner.transfer_limits(record),
        revision=FINEPDFS["revision"],
    )
    assert result.cache_hit and result.transferred_bytes == 0 and result.requests == 0
    assert result.identity.sha256 == result.identity.expected_sha256 == sha
    assert (bench.benchmark_dir(roots, "b2") / "adoption.json").is_file()
    with pytest.raises(runner.RunError, match="already holds a download"):
        bench.adopt_benchmark_download(roots, "b2", "b1")


@pytest.mark.parametrize(
    "changes",
    [
        {"linked_etag": '"' + "0" * 64 + '"'},
        {"linked_etag": None},
        {"repo_commit": "0" * 40},
        {"name": "data/eng_Latn/train/000_00084.parquet"},
        {"complete": False},
        {"url": "https://huggingface.co/datasets/x/y/resolve/z/" + FILE},
    ],
)
def test_adoption_refuses_unbound_or_different_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    roots = _authorized_pair(tmp_path, monkeypatch)
    _donor_download(roots, b"PAR1-bytes-PAR1", **changes)
    with pytest.raises(runner.RunError, match="not independently bound"):
        bench.adopt_benchmark_download(roots, "b2", "b1")
    assert not roots.scratch("bench-b2", "f00000.parquet.part").exists()


def test_adoption_refuses_tampered_bytes_and_unauthorized_or_foreign_benchmarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _authorized_pair(tmp_path, monkeypatch)
    part = _donor_download(roots, b"PAR1-bytes-PAR1")
    part.write_bytes(b"PAR1-BYTES-PAR1")
    with pytest.raises(runner.RunError, match="do not match"):
        bench.adopt_benchmark_download(roots, "b2", "b1")
    assert not roots.scratch("bench-b2", "f00000.parquet.part").exists()
    benchmark(roots, "b3")
    with pytest.raises(runner.RunError, match="only an authorized benchmark"):
        bench.adopt_benchmark_download(roots, "b3", "b1")
    foreign = runner.Roots(tmp_path / "data", tmp_path / "scratch", "finepdfs")
    benchmark(foreign, "u1", {**FINEPDFS, "revision": "0" * 40})
    with pytest.raises(runner.RunError, match="another source, repository or revision"):
        bench.adopt_benchmark_download(roots, "b2", "u1")
    with pytest.raises(runner.RunError, match="its own download"):
        bench.adopt_benchmark_download(roots, "b2", "b2")
