"""Offline selected-record file concurrency: authored fixtures, loopback only."""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.perf import PerfTelemetry, compare_perf_docs, load_perf_doc
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.records import RecordLimitError
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.sources.transport import BudgetExhaustedError

PAYLOAD_A = b'{"text":"authored A1"}\n{"text":"authored A2"}\n'
PAYLOAD_B = b'{"text":"authored B1"}\n{"text":"authored B2"}\n'


def plan_for_concurrency(
    url: str,
    files: list[str],
    ranges: dict[str, tuple[int, int]],
    max_workers: int,
    **limit_changes: Any,
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_transferred_bytes=1024**2,
        max_decompressed_bytes=1024**2,
        max_temp_disk_bytes=1024**2,
        max_output_disk_bytes=1024**2,
        max_records=100,
        max_requests=30,
        max_retries=0,
        max_workers=max_workers,
        overall_deadline_seconds=30,
    )
    if limit_changes:
        limits = limits.model_copy(update=limit_changes)
    plan = AcquisitionPlan(
        plan_id="authored_conc",
        source_id="authored",
        provider="https",
        repository=url,
        revision="authored-fixture-v1",
        mode="selected_records",
        selected_files=files,
        row_ranges=ranges,
        output_artifact_id="authored_conc",
        limits=limits,
    )
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


class MapOpener:
    """Offline opener serving per-file payloads with optional delays/barriers."""

    def __init__(
        self,
        payloads: dict[str, bytes],
        *,
        delay_per_file: dict[str, float] | None = None,
        barrier: threading.Barrier | None = None,
        entered: list[str] | None = None,
        entered_lock: threading.Lock | None = None,
        fail_files: dict[str, Exception] | None = None,
    ) -> None:
        self.payloads = payloads
        self.delay_per_file = delay_per_file or {}
        self.barrier = barrier
        self.entered = entered
        self.entered_lock = entered_lock
        self.fail_files = fail_files or {}
        self._lock = threading.Lock()
        self.open_counts: dict[str, int] = {}

    def open(self, request: Any, *, timeout: float) -> Any:
        url = request.full_url if hasattr(request, "full_url") else str(request)
        name = url.rsplit("/", 1)[-1]
        with self._lock:
            self.open_counts[name] = self.open_counts.get(name, 0) + 1
        if name in self.fail_files:
            raise self.fail_files[name]
        if self.entered is not None and self.entered_lock is not None:
            with self.entered_lock:
                self.entered.append(name)
        if self.barrier is not None:
            try:
                self.barrier.wait(timeout=10)
            except threading.BrokenBarrierError as exc:
                raise RuntimeError("barrier broken: workers did not overlap") from exc
        delay = self.delay_per_file.get(name, 0.0)
        if delay:
            time.sleep(delay)
        payload = self.payloads.get(name, PAYLOAD_A)

        class Response(io.BytesIO):
            status = 200
            headers = {
                "Content-Length": str(len(payload)),
                "ETag": '"authored-v1"',
            }

        return Response(payload)


def _run_with_opener(
    tmp_path: Path, plan: AcquisitionPlan, opener: Any
) -> tuple[BoundedFetcher, Any]:
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    state = fetcher.run()
    return fetcher, state


def test_1_worker_reports_concurrency_le_one(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        {"a.jsonl": (0, 1), "b.jsonl": (0, 1)},
        max_workers=1,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher, state = _run_with_opener(tmp_path, plan, opener)
    assert state.status == "COMPLETED"
    doc = load_perf_doc(tmp_path / "scratch/performance/authored_conc.perf.json")
    assert doc["max_workers_configured"] == 1
    assert doc["telemetry"]["max_active_workers"] <= 1
    assert doc["telemetry"]["max_active_workers"] == 1


def test_2_workers_overlap_via_barrier(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        {"a.jsonl": (0, 1), "b.jsonl": (0, 1)},
        max_workers=2,
    )
    barrier = threading.Barrier(2, timeout=10)
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B}, barrier=barrier)
    fetcher, state = _run_with_opener(tmp_path, plan, opener)
    assert state.status == "COMPLETED"
    doc = load_perf_doc(tmp_path / "scratch/performance/authored_conc.perf.json")
    assert doc["telemetry"]["max_active_workers"] >= 2


def test_more_workers_than_files(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        {"a.jsonl": (0, 2), "b.jsonl": (0, 2)},
        max_workers=4,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    _, state = _run_with_opener(tmp_path, plan, opener)
    assert state.status == "COMPLETED"
    assert state.records_acquired == 4
    doc = load_perf_doc(tmp_path / "scratch/performance/authored_conc.perf.json")
    assert doc["telemetry"]["max_active_workers"] >= 1
    assert doc["telemetry"]["max_active_workers"] <= 2


def test_byte_identical_across_worker_counts(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 2), "b.jsonl": (0, 2)}
    outputs: list[bytes] = []
    for workers, leaf in [(1, "one"), (2, "two"), (4, "four")]:
        root = tmp_path / leaf
        plan = plan_for_concurrency(
            "https://huggingface.co/authored-fixture", files, ranges, max_workers=workers
        )
        opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
        _run_with_opener(root, plan, opener)
        outputs.append((root / "output/selected_records.jsonl").read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]
    # Plan order then row order: a rows then b rows.
    rows = [json.loads(line) for line in outputs[0].splitlines()]
    assert [r["_xlm_acquisition"]["source_file"] for r in rows] == ["a.jsonl"] * 2 + ["b.jsonl"] * 2
    assert [r["_xlm_acquisition"]["row_index"] for r in rows] == [0, 1, 0, 1]


def test_deterministic_order_despite_reversed_completion(tmp_path: Path) -> None:
    # a.jsonl is slow, b.jsonl is fast: b finishes first, but output stays plan order.
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=2
    )
    opener = MapOpener(
        {"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B},
        delay_per_file={"a.jsonl": 0.4, "b.jsonl": 0.0},
    )
    _run_with_opener(tmp_path, plan, opener)
    data = (tmp_path / "output/selected_records.jsonl").read_bytes()
    rows = [json.loads(line) for line in data.splitlines()]
    assert [r["_xlm_acquisition"]["source_file"] for r in rows] == ["a.jsonl", "b.jsonl"]
    # Serial reference with same logical data must match byte-for-byte.
    ref_root = tmp_path / "ref"
    ref_plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=1
    )
    ref_opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    _run_with_opener(ref_root, ref_plan, ref_opener)
    assert data == (ref_root / "output/selected_records.jsonl").read_bytes()


def test_selection_hash_worker_independent() -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    one = plan_for_concurrency("https://huggingface.co/u", files, ranges, max_workers=1)
    two = plan_for_concurrency("https://huggingface.co/u", files, ranges, max_workers=2)
    four = plan_for_concurrency("https://huggingface.co/u", files, ranges, max_workers=4)
    assert one.compute_selection_hash() == two.compute_selection_hash()
    assert two.compute_selection_hash() == four.compute_selection_hash()
    assert one.compute_behavioral_hash() != two.compute_behavioral_hash()


def test_transfer_budget_contention_exact(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 2), "b.jsonl": (0, 2)}
    # Each file transfers len(payload); budget fits one file only.
    one_len = len(PAYLOAD_A)
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        ranges,
        max_workers=2,
        max_transferred_bytes=one_len + one_len // 2,
        max_requests=30,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(BudgetExhaustedError):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    journal = ProgressJournal(
        tmp_path / "scratch/journals/authored_conc.progress.json",
        "authored_conc",
        plan.compute_behavioral_hash(),
    )
    assert journal.state.status in ("FAILED", "INTERRUPTED")
    # Consumed never exceeds the bound including outstanding reservations.
    assert journal.state.transferred_bytes <= plan.limits.max_transferred_bytes


def test_request_budget_contention_exact(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        ranges,
        max_workers=2,
        max_requests=1,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(BudgetExhaustedError, match="request"):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    journal = ProgressJournal(
        tmp_path / "scratch/journals/authored_conc.progress.json",
        "authored_conc",
        plan.compute_behavioral_hash(),
    )
    assert journal.state.requests_made <= 1


def test_decompressed_budget_contention(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 2), "b.jsonl": (0, 2)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        ranges,
        max_workers=2,
        max_decompressed_bytes=10,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(BudgetExhaustedError):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()


def test_scanned_record_global_limit(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 2), "b.jsonl": (0, 2)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        ranges,
        max_workers=2,
        max_scanned_records=3,
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(BudgetExhaustedError, match="scanned"):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()


def test_one_worker_http_failure_no_publish(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=2
    )
    import urllib.error

    opener = MapOpener(
        {"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B},
        fail_files={"b.jsonl": urllib.error.URLError("authored boom")},
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(Exception, match="authored boom"):
        fetcher.run()
    # No partial published artifact.
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    journal = ProgressJournal(
        tmp_path / "scratch/journals/authored_conc.progress.json",
        "authored_conc",
        plan.compute_behavioral_hash(),
    )
    # Journal remains readable with truthful counters.
    assert journal.state.status in ("FAILED", "INTERRUPTED")
    assert journal.state.requests_made >= 1


def test_parquet_parser_bound_failure_preserves_diagnostic(tmp_path: Path) -> None:
    tiny = pa.table({"text": ["a", "b"]})
    big_texts = [f"row-{i:05d}-" + chr(65 + (i % 26)) * 500 for i in range(200)]
    big = pa.table({"text": big_texts})
    good = tmp_path / "good.parquet"
    bad = tmp_path / "bad.parquet"
    pq.write_table(tiny, good, row_group_size=1, compression="NONE")
    pq.write_table(big, bad, row_group_size=200, compression="NONE", use_dictionary=False)
    good_bytes, bad_bytes = good.read_bytes(), bad.read_bytes()
    good.unlink()
    bad.unlink()

    class ParquetOpener:
        def open(self, request: Any, *, timeout: float) -> Any:
            from urllib.parse import urlparse

            name = urlparse(request.full_url).path.rsplit("/", 1)[-1]
            payload = good_bytes if name == "good.parquet" else bad_bytes
            headers = dict(request.header_items())
            total = len(payload)
            if "Range" in headers:
                start_s, end_s = headers["Range"].removeprefix("bytes=").split("-")
                start, end = int(start_s), int(end_s)

                class R(io.BytesIO):
                    status = 206
                    headers = {
                        "Content-Range": f"bytes {start}-{end}/{total}",
                        "Content-Length": str(end - start + 1),
                        "ETag": '"parquet-v1"',
                    }

                return R(payload[start : end + 1])
            etag = '"parquet-v1"'

            class F(io.BytesIO):
                status = 200
                headers = {
                    "Content-Length": str(total),
                    "ETag": etag,
                }

            return F(payload)

    files = ["good.parquet", "bad.parquet"]
    # Big row group (~103KB) exceeds the bound; tiny row groups (~57) pass.
    # Footer prefetch reads up to 64KB, so the bound must exceed that to reach
    # the numeric row-group check instead of failing on the footer range.
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        {"good.parquet": (0, 1), "bad.parquet": (0, 1)},
        max_workers=2,
        max_parser_bytes=70000,
        max_record_bytes=5000,
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = ParquetOpener()  # type: ignore[assignment]
    with pytest.raises(RecordLimitError, match="total_byte_size|max_parser_bytes"):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()


def test_deadline_timeout_no_publish(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        files,
        ranges,
        max_workers=2,
        overall_deadline_seconds=1.0,
    )
    opener = MapOpener(
        {"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B},
        delay_per_file={"a.jsonl": 1.5, "b.jsonl": 1.5},
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = opener
    with pytest.raises(TimeoutError):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    journal = ProgressJournal(
        tmp_path / "scratch/journals/authored_conc.progress.json",
        "authored_conc",
        plan.compute_behavioral_hash(),
    )
    assert journal.state.status == "INTERRUPTED"


def test_resume_after_transient_failure_is_deterministic(tmp_path: Path) -> None:
    import urllib.error

    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=2
    )
    failing = MapOpener(
        {"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B},
        fail_files={"a.jsonl": urllib.error.URLError("transient")},
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = failing
    with pytest.raises(Exception, match="transient"):
        fetcher.run()
    assert not (tmp_path / "output/selected_records.jsonl").exists()
    # Retry with the same plan identity and generous budgets still succeeds.
    retry = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    retry.opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    state = retry.run()
    assert state.status == "COMPLETED"
    data = (tmp_path / "output/selected_records.jsonl").read_bytes()
    # Deterministic reference with serial workers.
    ref_root = tmp_path / "ref"
    ref_plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=1
    )
    ref = BoundedFetcher(ref_plan, ref_root / "scratch", ref_root / "output")
    ref.opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    ref.run()
    assert data == (ref_root / "output/selected_records.jsonl").read_bytes()
    # Old attempt history intact: journal readable, no replacement of completed entry.
    journal = ProgressJournal(
        tmp_path / "scratch/journals/authored_conc.progress.json",
        "authored_conc",
        plan.compute_behavioral_hash(),
    )
    assert journal.state.file_progress["selected_records.jsonl"].status == "completed"


def test_request_accounting_reconciles_with_journal(tmp_path: Path) -> None:
    files = ["a.jsonl", "b.jsonl"]
    ranges = {"a.jsonl": (0, 1), "b.jsonl": (0, 1)}
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture", files, ranges, max_workers=2
    )
    opener = MapOpener({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    fetcher, state = _run_with_opener(tmp_path, plan, opener)
    assert state.status == "COMPLETED"
    doc = load_perf_doc(tmp_path / "scratch/performance/authored_conc.perf.json")
    telemetry = doc["telemetry"]
    assert telemetry["accounted_network_requests"] == doc["requests_made"]
    assert (
        telemetry["logical_requests"] + telemetry["redirect_requests"]
        == telemetry["accounted_network_requests"]
    )
    assert doc["request_accounting"]["reconciled"] is True
    # Verifier still accepts the worker-independent selection identity.
    verifier = AcquisitionVerifier(plan, tmp_path / "output", fetcher.journal)
    receipt = verifier.verify()
    assert receipt.files[0].record_count == 2


def test_old_telemetry_sidecar_still_loads(tmp_path: Path) -> None:
    perf = PerfTelemetry()
    plan = plan_for_concurrency(
        "https://huggingface.co/authored-fixture",
        ["a.jsonl"],
        {"a.jsonl": (0, 1)},
        max_workers=1,
    )
    doc = perf.snapshot(
        plan=plan,
        status="COMPLETED",
        wall_seconds=1.0,
        transferred_bytes=100,
        journal_decompressed_bytes=100,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=1,
    )
    # Simulate a pre-upgrade sidecar: strip the new explicit fields.
    doc["telemetry"].pop("logical_requests", None)
    doc["telemetry"].pop("redirect_requests", None)
    doc["telemetry"].pop("accounted_network_requests", None)
    doc.pop("request_accounting", None)
    path = tmp_path / "old.perf.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load_perf_doc(path)
    assert loaded["telemetry"]["requests"] == 0
    # Comparison still works across old/new (workers may differ).
    other_plan = plan.model_copy(
        update={"limits": plan.limits.model_copy(update={"max_workers": 2})}
    )
    other = PerfTelemetry().snapshot(
        plan=other_plan,
        status="COMPLETED",
        wall_seconds=0.5,
        transferred_bytes=100,
        journal_decompressed_bytes=100,
        journal_requests=2,
        journal_cache_hits=0,
        journal_records=1,
    )
    result = compare_perf_docs([loaded, other])
    assert result["comparable"] is True
    assert result["entries"][0]["accounted_network_requests"] == 0


def test_whole_file_path_unchanged(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import AcquisitionMode

    payload = b"0123456789ABCDEF" * 64
    seen: dict[str, int] = {}

    class WholeOpener:
        def open(self, request: Any, *, timeout: float) -> Any:
            name = request.full_url.rsplit("/", 1)[-1]
            seen[name] = seen.get(name, 0) + 1

            class R(io.BytesIO):
                status = 200
                headers = {"Content-Length": str(len(payload)), "ETag": '"w-v1"'}

            return R(payload)

    limits = AcquisitionLimits(
        max_transferred_bytes=1024**2,
        max_decompressed_bytes=1024**2,
        max_temp_disk_bytes=1024**2,
        max_output_disk_bytes=1024**2,
        max_records=100,
        max_requests=10,
        max_retries=0,
        max_workers=2,
        overall_deadline_seconds=30,
    )
    base = AcquisitionPlan(
        plan_id="authored_whole",
        source_id="authored",
        provider="https",
        repository="https://huggingface.co/authored-fixture",
        revision="authored-fixture-v1",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=["one.bin", "two.bin"],
        output_artifact_id="authored_whole",
        limits=limits,
    )
    plan = base.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=base.compute_behavioral_hash(),
                authorized_by="authored offline test",
                authorized_at="fixture",
                is_pilot_approved=True,
            )
        }
    )
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
    fetcher.opener = WholeOpener()  # type: ignore[assignment]
    state = fetcher.run()
    assert state.status == "COMPLETED"
    assert (tmp_path / "output/one.bin").read_bytes() == payload
    assert (tmp_path / "output/two.bin").read_bytes() == payload
