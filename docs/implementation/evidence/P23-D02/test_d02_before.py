"""D02 proposal reproductions. In-memory transport only; no sockets or live data."""
from __future__ import annotations

import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.sources.transport import BudgetExhaustedError

PAYLOAD = b'{"text":"authored A"}\n{"text":"authored B"}\n'


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Length": str(len(PAYLOAD)), "ETag": '"authored-v1"'}


class OfflineOpener:
    def open(self, request, *, timeout):
        return Response(PAYLOAD)


def fetcher(root: Path, records: int = 2) -> BoundedFetcher:
    plan = AcquisitionPlan(
        plan_id="authored_d02", source_id="authored", provider="https",
        repository="https://huggingface.co/authored-fixture", revision="fixture-v1",
        selected_files=["rows.jsonl"], output_artifact_id="authored_d02",
        expected_file_digests={"rows.jsonl": hashlib.sha256(PAYLOAD).hexdigest()},
        limits=AcquisitionLimits(max_transferred_bytes=4096, max_decompressed_bytes=4096,
            max_temp_disk_bytes=4096, max_output_disk_bytes=4096, max_records=records,
            max_requests=2, max_retries=0, max_workers=1, overall_deadline_seconds=5),
        authorization=PlanAuthorization(authorization_hash="authored-fixture-only",
            authorized_by="offline test", authorized_at="fixture", is_pilot_approved=True),
    )
    result = BoundedFetcher(plan, root / "scratch", root / "output")
    result.opener = OfflineOpener()
    return result


def observe(root: Path, value: dict) -> None:
    (root / "observed.json").write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_durable_consumption_survives_restart(tmp_path: Path) -> None:
    first = fetcher(tmp_path)
    state = first.run()
    resumed = fetcher(tmp_path)
    actual = resumed.capacity_mgr.snapshot()["transferred_bytes"]
    observe(tmp_path, {"durable_consumed": state.transferred_bytes, "resumed_accounting": actual})
    assert actual >= state.transferred_bytes, "restart must not replenish transfer allowance"


def test_same_length_corrupted_cache_is_not_reused(tmp_path: Path) -> None:
    fetcher(tmp_path).run()
    cached = tmp_path / "output/rows.jsonl"
    cached.write_bytes(b"X" * len(PAYLOAD))
    resumed = fetcher(tmp_path)
    state = resumed.run()
    observe(tmp_path, {"status": state.status, "cache_hits": state.cache_hits,
        "actual_hash": hashlib.sha256(cached.read_bytes()).hexdigest(),
        "expected_hash": hashlib.sha256(PAYLOAD).hexdigest()})
    assert cached.read_bytes() == PAYLOAD, "same length is not content integrity"


def test_declared_record_cap_bounds_retained_corpus(tmp_path: Path) -> None:
    state = fetcher(tmp_path, records=1).run()
    records = (tmp_path / "output/rows.jsonl").read_bytes().splitlines()
    observe(tmp_path, {"declared_cap": 1, "retained_records": len(records),
                      "reported_records": state.records_acquired, "status": state.status})
    assert len(records) <= 1, "max_records must be a processing/retention gate"


def test_concurrent_reservations_share_remaining_allowance(tmp_path: Path) -> None:
    manager = StorageCapacityManager(4096, 4096, 4096, 4096)
    def reserve(_):
        try:
            manager.reserve_transfer(3000)
            return 1
        except BudgetExhaustedError:
            return 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = sum(executor.map(reserve, range(2)))
    observe(tmp_path, {"limit": 4096, "reservation_bytes": 3000, "accepted": accepted})
    assert accepted <= 1, "accepted reservations must reserve shared remaining allowance"


def test_authored_single_transfer_positive_control(tmp_path: Path) -> None:
    state = fetcher(tmp_path).run()
    assert state.status == "COMPLETED"
    assert state.transferred_bytes == len(PAYLOAD)
    assert (tmp_path / "output/rows.jsonl").read_bytes() == PAYLOAD
