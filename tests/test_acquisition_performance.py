"""Offline acquisition-performance telemetry: authored fixtures, loopback only."""

from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.perf import (
    PERF_VERSION,
    PerfTelemetry,
    cache_class_for,
    compare_perf_docs,
    load_perf_doc,
)
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.records import inspect_records

PAYLOAD = b'{"text":"authored A"}\n{"text":"authored B"}\n'


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now_value = start

    def __call__(self) -> float:
        return self.now_value

    def advance(self, seconds: float) -> None:
        self.now_value += seconds


def plan_for(url: str, name: str = "rows.jsonl", **changes: Any) -> AcquisitionPlan:
    values: dict[str, Any] = dict(
        plan_id="authored_perf",
        source_id="authored",
        provider="https",
        repository=url,
        revision="authored-fixture-v1",
        selected_files=[name],
        output_artifact_id="authored_perf",
        limits=AcquisitionLimits(
            max_transferred_bytes=1024**2,
            max_decompressed_bytes=1024**2,
            max_temp_disk_bytes=1024**2,
            max_output_disk_bytes=1024**2,
            max_records=100,
            max_requests=30,
            max_retries=1,
            max_workers=1,
            overall_deadline_seconds=30,
        ),
    )
    values.update(changes)
    plan = AcquisitionPlan(**values)
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="authored offline test",
                authorized_at="fixture",
                is_pilot_approved=True,
            )
        }
    )


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Length": str(len(PAYLOAD)), "ETag": '"authored-v1"'}


class OfflineOpener:
    def open(self, request: Any, *, timeout: float) -> Response:
        return Response(PAYLOAD)


def offline_fetch(root: Path, **changes: Any) -> BoundedFetcher:
    plan = plan_for("https://huggingface.co/authored-fixture", **changes)
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    fetcher.opener = OfflineOpener()  # type: ignore[assignment]
    return fetcher


def test_telemetry_exists_on_successful_acquisition(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path)
    state = fetcher.run()
    assert state.status == "COMPLETED"
    sidecar = tmp_path / "scratch/performance/authored_perf.perf.json"
    assert sidecar.is_file()
    doc = load_perf_doc(sidecar)
    assert doc["perf_version"] == PERF_VERSION
    assert doc["status"] == "COMPLETED"
    assert doc["plan_id"] == "authored_perf"
    assert doc["transferred_bytes"] == len(PAYLOAD)
    assert doc["wall_seconds"] >= 0
    assert doc["telemetry"]["requests"] >= 1
    assert doc["telemetry"]["max_active_workers"] <= 1
    assert "rows.jsonl" in doc["files"]
    assert doc["files"]["rows.jsonl"]["requests"] >= 1


def test_telemetry_exists_on_bounded_refusal(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path, limits=AcquisitionLimits(max_records=1))
    with pytest.raises(ValueError, match="record limit"):
        fetcher.run()
    sidecar = tmp_path / "scratch/performance/authored_perf.perf.json"
    assert sidecar.is_file()
    doc = load_perf_doc(sidecar)
    assert doc["status"] in ("FAILED", "INTERRUPTED")
    assert doc["wall_seconds"] >= 0


def test_telemetry_does_not_alter_selected_record_bytes(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        plan = plan_for(
            "https://huggingface.co/authored-fixture",
            mode="selected_records",
            row_ranges={"rows.jsonl": (0, 2)},
        )
        fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
        fetcher.opener = OfflineOpener()  # type: ignore[assignment]
        fetcher.run()
    first = (first_root / "output/selected_records.jsonl").read_bytes()
    second = (second_root / "output/selected_records.jsonl").read_bytes()
    assert first == second
    assert len(first.splitlines()) == 2


def test_telemetry_does_not_alter_deterministic_identities(tmp_path: Path) -> None:
    plan = plan_for("https://huggingface.co/authored-fixture")
    expected_hash = plan.compute_behavioral_hash()
    for leaf in ("a", "b"):
        root = tmp_path / leaf
        fetcher = offline_fetch(root)
        fetcher.run()
        assert fetcher.plan.compute_behavioral_hash() == expected_hash
    first = (tmp_path / "a/output/rows.jsonl").read_bytes()
    second = (tmp_path / "b/output/rows.jsonl").read_bytes()
    assert first == second == PAYLOAD
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(PAYLOAD).hexdigest()


def test_max_workers_one_reports_concurrency_le_one(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path)
    fetcher.run()
    doc = load_perf_doc(tmp_path / "scratch/performance/authored_perf.perf.json")
    assert doc["max_workers_configured"] == 1
    assert doc["telemetry"]["max_active_workers"] <= 1


def test_multi_file_worker_fixture_observes_concurrency_gt_one() -> None:
    clock = FakeClock()
    perf = PerfTelemetry(now=clock)
    entered = threading.Event()
    release = threading.Event()

    def worker(name: str) -> None:
        with perf.file_worker(name):
            if name == "a":
                entered.set()
                assert release.wait(timeout=10)
            else:
                assert entered.wait(timeout=10)
                release.set()

    first = threading.Thread(target=worker, args=("a",))
    second = threading.Thread(target=worker, args=("b",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert perf.max_active_workers == 2


def test_old_journal_without_telemetry_still_loads(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path)
    state = fetcher.run()
    assert state.status == "COMPLETED"
    sidecar = tmp_path / "scratch/performance/authored_perf.perf.json"
    sidecar.unlink()
    journal_path = tmp_path / "scratch/journals/authored_perf.progress.json"
    journal = ProgressJournal(journal_path, "authored_perf", fetcher.plan.compute_behavioral_hash())
    assert journal.state.status == "COMPLETED"
    with pytest.raises(ValueError, match="not found"):
        load_perf_doc(sidecar)


def test_malformed_telemetry_refuses_cleanly(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.perf.json"
    bad_json.write_bytes(b"{not json")
    with pytest.raises(ValueError, match="malformed"):
        load_perf_doc(bad_json)
    wrong_version = tmp_path / "version.perf.json"
    wrong_version.write_text(json.dumps({"perf_version": 999, "plan_id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_perf_doc(wrong_version)
    missing_keys = tmp_path / "missing.perf.json"
    missing_keys.write_text(json.dumps({"perf_version": PERF_VERSION}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        load_perf_doc(missing_keys)


def test_telemetry_never_stores_secrets_or_urls(tmp_path: Path) -> None:
    perf = PerfTelemetry(now=FakeClock())
    perf.record_request(host="https://example.com?token=secret", file="rows.jsonl")
    perf.record_redirect("https://cdn.example.com/path?signature=abc#frag")
    perf.record_request(host="user:pass@example.com", file="rows.jsonl")
    plan = plan_for("https://huggingface.co/authored-fixture")
    doc = perf.snapshot(
        plan=plan,
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=10,
        journal_decompressed_bytes=10,
        journal_requests=1,
        journal_cache_hits=0,
        journal_records=1,
    )
    # Host/category aggregates must never retain URLs, queries, or credentials.
    # (The plan's own public repository identifier legitimately contains "://".)
    sensitive = json.dumps({"by_host": doc["by_host"], "slowest": doc["slowest_requests"]})
    for secret in ("token", "secret", "signature", "://", "?", "#", "@", "user:pass"):
        assert secret not in sensitive
    assert doc["by_host"] == {}


def test_parquet_metadata_timing_is_observed(tmp_path: Path) -> None:
    table = pa.table({"text": ["alpha", "beta"]})
    path = tmp_path / "rows.parquet"
    pq.write_table(table, path, row_group_size=1, compression="NONE")
    perf = PerfTelemetry(now=FakeClock())
    limits = AcquisitionLimits()
    count = inspect_records(path, "rows.parquet", limits, None, perf=perf)
    assert count == 2
    assert perf.metadata_seconds >= 0
    assert perf.decode_seconds >= 0
    assert perf.parquet_groups >= 1


def _snapshot_doc(plan: AcquisitionPlan, wall: float, cache_hits: int = 0) -> dict[str, Any]:
    perf = PerfTelemetry(now=FakeClock())
    return perf.snapshot(
        plan=plan,
        status="COMPLETED",
        wall_seconds=wall,
        transferred_bytes=1000,
        journal_decompressed_bytes=2000,
        journal_requests=2,
        journal_cache_hits=cache_hits,
        journal_records=2,
    )


def test_compare_accepts_equivalent_runs_differing_only_in_workers() -> None:
    base = plan_for("https://huggingface.co/authored-fixture")
    one = _snapshot_doc(base, wall=2.0)
    other_plan = base.model_copy(
        update={"limits": base.limits.model_copy(update={"max_workers": 4})}
    )
    four = PerfTelemetry(now=FakeClock()).snapshot(
        plan=other_plan,
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=1000,
        journal_decompressed_bytes=2000,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=2,
    )
    result = compare_perf_docs([one, four])
    assert result["comparable"] is True
    assert len(result["entries"]) == 2
    assert result["fastest"]["wall_seconds"] == 1.0


def test_compare_rejects_revision_mismatch() -> None:
    first = _snapshot_doc(plan_for("https://huggingface.co/authored-fixture"), wall=1.0)
    second_plan = plan_for("https://huggingface.co/authored-fixture", revision="other-revision-v2")
    second = _snapshot_doc(second_plan, wall=1.0)
    result = compare_perf_docs([first, second])
    assert result["comparable"] is False
    assert result["fastest"] is None
    assert any("revision" in reason for reason in result["refusals"])


def test_compare_rejects_selection_mismatch() -> None:
    first = _snapshot_doc(plan_for("https://huggingface.co/authored-fixture"), wall=1.0)
    second = _snapshot_doc(
        plan_for("https://huggingface.co/authored-fixture", "other.jsonl"), wall=1.0
    )
    result = compare_perf_docs([first, second])
    assert result["comparable"] is False
    assert any("selected_files" in reason for reason in result["refusals"])


def test_compare_distinguishes_cached_from_uncached() -> None:
    cold = _snapshot_doc(
        plan_for("https://huggingface.co/authored-fixture"), wall=1.0, cache_hits=0
    )
    warm_plan = plan_for("https://huggingface.co/authored-fixture")
    warm = PerfTelemetry(now=FakeClock()).snapshot(
        plan=warm_plan,
        status="COMPLETED",
        wall_seconds=0.1,
        transferred_bytes=0,
        journal_decompressed_bytes=2000,
        journal_requests=0,
        journal_cache_hits=1,
        journal_records=2,
    )
    result = compare_perf_docs([cold, warm])
    assert result["comparable"] is False
    assert any("cache" in reason.lower() for reason in result["refusals"])


def test_zero_duration_avoids_division_by_zero() -> None:
    doc = _snapshot_doc(plan_for("https://huggingface.co/authored-fixture"), wall=0.0)
    assert doc["rates"]["application_mb_per_sec"] == 0.0
    assert doc["rates"]["requests_per_sec"] == 0.0
    assert all(value == 0.0 for value in doc["time_shares_of_wall"].values())
    assert doc["average_concurrency"] == 0.0


def test_failed_run_is_never_ranked_as_fastest() -> None:
    good = _snapshot_doc(plan_for("https://huggingface.co/authored-fixture"), wall=5.0)
    bad_perf = PerfTelemetry(now=FakeClock())
    bad = bad_perf.snapshot(
        plan=plan_for("https://huggingface.co/authored-fixture"),
        status="FAILED",
        wall_seconds=0.01,
        transferred_bytes=10,
        journal_decompressed_bytes=10,
        journal_requests=1,
        journal_cache_hits=0,
        journal_records=0,
    )
    result = compare_perf_docs([good, bad])
    assert result["comparable"] is False
    assert result["fastest"] is None
    assert any("FAILED" in reason for reason in result["refusals"])


def test_cache_classification() -> None:
    assert (
        cache_class_for(cache_hits=0, selected_files=2, transferred_bytes=100) == "uncached-network"
    )
    assert cache_class_for(cache_hits=1, selected_files=2, transferred_bytes=100) == "partial-cache"
    assert (
        cache_class_for(cache_hits=2, selected_files=2, transferred_bytes=100) == "cache-hit/local"
    )
    assert cache_class_for(cache_hits=1, selected_files=2, transferred_bytes=0) == "cache-hit/local"


def test_performance_cli_reads_existing_telemetry(tmp_path: Path) -> None:
    offline_fetch(tmp_path).run()
    plan_path = tmp_path / "plan.json"
    plan = plan_for("https://huggingface.co/authored-fixture")
    plan_path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "performance",
            "--plan",
            str(plan_path),
            "--scratch-dir",
            str(tmp_path / "scratch"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["plan_id"] == "authored_perf"
    assert payload["status"] == "COMPLETED"


def test_performance_compare_cli_compares_sidecars(tmp_path: Path) -> None:
    first = _snapshot_doc(plan_for("https://huggingface.co/authored-fixture"), wall=2.0)
    other_plan = plan_for("https://huggingface.co/authored-fixture")
    other_plan = other_plan.model_copy(
        update={"limits": other_plan.limits.model_copy(update={"max_workers": 4})}
    )
    second = PerfTelemetry(now=FakeClock()).snapshot(
        plan=other_plan,
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=1000,
        journal_decompressed_bytes=2000,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=2,
    )
    first_path = tmp_path / "one.perf.json"
    second_path = tmp_path / "four.perf.json"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        ["performance-compare", "--perf", str(first_path), "--perf", str(second_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["comparable"] is True
    assert payload["fastest"]["wall_seconds"] == 1.0


def test_performance_cli_old_journal_without_sidecar_still_loads(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path)
    fetcher.run()
    (tmp_path / "scratch/performance/authored_perf.perf.json").unlink()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(fetcher.plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "performance",
            "--plan",
            str(plan_path),
            "--scratch-dir",
            str(tmp_path / "scratch"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["telemetry_available"] is False
