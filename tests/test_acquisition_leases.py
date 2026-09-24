"""Durable accounting leases: exact totals, exact refusal, bounded crash stranding.

Authored offline fixtures plus loopback HTTP only; never live-source evidence.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition.disk import (
    ACCOUNTING_INITIAL_WINDOW_BYTES,
    ACCOUNTING_WINDOW_BYTES,
    CapacityLease,
    StorageCapacityManager,
    grown_window,
)
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    PlanAuthorization,
)
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.sources.transport import BudgetExhaustedError, TransportBudget

KIB = 1024
MIB = 1024 * KIB


class Body(io.BytesIO):
    """Response stand-in; optionally fails on the n-th read."""

    def __init__(self, data: bytes, fail_on_read: int | None = None) -> None:
        super().__init__(data)
        self.reads = 0
        self.fail_on_read = fail_on_read

    def read(self, size: int | None = -1) -> bytes:
        self.reads += 1
        if self.fail_on_read is not None and self.reads == self.fail_on_read:
            raise OSError("authored mid-stream failure")
        return super().read(size)


def manager(root: Path, transfer: int = 64 * MIB) -> StorageCapacityManager:
    journal = ProgressJournal(root / "journals" / "lease.progress.json", "lease", "h" * 64)
    return StorageCapacityManager(transfer, 64 * MIB, 64 * MIB, 64 * MIB, journal=journal)


def budget(capacity: StorageCapacityManager) -> TransportBudget:
    return TransportBudget(
        max_bytes=capacity.max_transferred_bytes,
        max_requests=10_000,
        deadline_seconds=600,
        capacity=capacity,
    )


def drain(read: Any, response: Any, total: int) -> tuple[int, BaseException | None]:
    got = 0
    try:
        while got < total:
            chunk = read(response, min(65536, total - got))
            if not chunk:
                break
            got += len(chunk)
    except (BudgetExhaustedError, OSError) as exc:
        return got, exc
    return got, None


def test_grown_window_is_geometric_and_capped() -> None:
    assert grown_window(0) == ACCOUNTING_INITIAL_WINDOW_BYTES
    assert grown_window(64 * KIB) == 64 * KIB
    assert grown_window(3 * MIB) == 3 * MIB
    assert grown_window(10**12) == ACCOUNTING_WINDOW_BYTES


def test_lease_totals_equal_per_read_accounting(tmp_path: Path) -> None:
    data = bytes(range(256)) * (4 * 4096) + b"tail"  # 4 MiB + 4 bytes
    legacy, leased = manager(tmp_path / "a"), manager(tmp_path / "b")
    legacy_budget, leased_budget = budget(legacy), budget(leased)
    got_a, err_a = drain(legacy_budget.read_chunk, Body(data), len(data))
    lease = leased_budget.transfer_lease(len(data))
    got_b, err_b = drain(
        lambda response, n: leased_budget.read_leased(response, lease, n), Body(data), len(data)
    )
    lease.close()
    assert (got_a, err_a, got_b, err_b) == (len(data), None, len(data), None)
    assert legacy.snapshot()["transferred_bytes"] == leased.snapshot()["transferred_bytes"]
    assert leased.snapshot().get("reserved_transfer_bytes", 0) == 0
    assert leased_budget.bytes_transferred == len(data)
    assert leased.journal is not None and legacy.journal is not None
    # Windows grow geometrically: a handful of journal writes, not two per read.
    assert leased.journal.tx_persisted < 20 < legacy.journal.tx_persisted


def test_lease_refuses_at_the_same_byte_as_per_read_accounting(tmp_path: Path) -> None:
    data = b"x" * (3 * MIB)
    limit = 200 * KIB + 123
    legacy, leased = manager(tmp_path / "a", limit), manager(tmp_path / "b", limit)
    got_a, err_a = drain(budget(legacy).read_chunk, Body(data), len(data))
    leased_budget = budget(leased)
    lease = leased_budget.transfer_lease(len(data))
    got_b, err_b = drain(
        lambda response, n: leased_budget.read_leased(response, lease, n), Body(data), len(data)
    )
    lease.close()
    assert got_a == got_b == limit
    assert isinstance(err_a, BudgetExhaustedError) and isinstance(err_b, BudgetExhaustedError)
    assert legacy.snapshot()["transferred_bytes"] == leased.snapshot()["transferred_bytes"] == limit


def test_failed_read_keeps_only_the_in_flight_amount_reserved(tmp_path: Path) -> None:
    data = b"y" * MIB
    legacy, leased = manager(tmp_path / "a"), manager(tmp_path / "b")
    got_a, err_a = drain(budget(legacy).read_chunk, Body(data, fail_on_read=3), len(data))
    leased_budget = budget(leased)
    lease = leased_budget.transfer_lease(len(data))
    got_b, err_b = drain(
        lambda response, n: leased_budget.read_leased(response, lease, n),
        Body(data, fail_on_read=3),
        len(data),
    )
    lease.close()
    assert got_a == got_b == 2 * 65536
    assert isinstance(err_a, OSError) and isinstance(err_b, OSError)
    for account in (legacy, leased):
        snap = account.snapshot()
        assert snap["transferred_bytes"] == 2 * 65536
        assert snap["reserved_transfer_bytes"] == 65536


def test_crash_strands_bounded_allowance_and_never_undercounts(tmp_path: Path) -> None:
    capacity = manager(tmp_path)
    lease = CapacityLease(capacity, "transfer")
    used = 0
    for _ in range(40):  # 40 x 64 KiB reads, lease never closed ("crash")
        used += lease.take(65536)
        lease.commit(65536)
    assert capacity.journal is not None
    reopened = ProgressJournal(capacity.journal.journal_path, "lease", "h" * 64)
    account = reopened.state.accounting
    consumed = account.consumed.get("transfer", 0)
    reserved = sum(account.reservations.get("transfer", {}).values())
    assert consumed + reserved >= used  # conservative: never under-counted
    assert consumed + reserved - used <= max(ACCOUNTING_INITIAL_WINDOW_BYTES, used)


def test_consume_refuses_exactly_like_record_style_charges(tmp_path: Path) -> None:
    capacity = StorageCapacityManager(MIB, 100_000, MIB, MIB)
    lease = CapacityLease(capacity, "decompressed")
    for _ in range(9):
        lease.consume(10_000)
    with pytest.raises(BudgetExhaustedError):
        lease.consume(10_001)
    lease.consume(10_000)
    lease.close()
    assert capacity.snapshot()["decompressed_bytes"] == 100_000


def test_exchange_refusal_keeps_settlement_durable(tmp_path: Path) -> None:
    capacity = manager(tmp_path, transfer=100 * KIB)
    grants, _ = capacity.exchange(reserve=(("transfer", 100 * KIB, 1),))
    token, amount = grants[0]
    assert amount == 100 * KIB
    grants, consumed = capacity.exchange(
        settle=(("transfer", token, amount, 0),), reserve=(("transfer", 1, 1),)
    )
    assert grants == [("", 0)]
    assert consumed["transfer"] == 100 * KIB
    assert capacity.journal is not None
    reopened = ProgressJournal(capacity.journal.journal_path, "lease", "h" * 64)
    assert reopened.state.accounting.consumed["transfer"] == 100 * KIB
    assert not reopened.state.accounting.reservations.get("transfer")


def test_journal_reload_sees_an_external_atomic_replace(tmp_path: Path) -> None:
    path = tmp_path / "journals" / "shared.progress.json"
    first = ProgressJournal(path, "shared", "h" * 64)
    with first.transaction() as state:
        state.cache_hits = 1
    second = ProgressJournal(path, "shared", "h" * 64)
    with second.transaction() as state:
        assert state.cache_hits == 1
        state.cache_hits = 7
    with first.transaction(persist=False) as state:
        assert state.cache_hits == 7
    with first.transaction(persist=False) as state:
        assert state.cache_hits == 7
    assert first.io_stats()["journal_reads_elided"] == 0  # stat tuples cannot prove freshness


def test_cached_deadline_is_still_enforced(tmp_path: Path) -> None:
    capacity = manager(tmp_path)
    capacity.bind_deadline(0.05)
    capacity.check_deadline()
    time.sleep(0.1)
    with pytest.raises(TimeoutError):
        capacity.check_deadline()


# --------------------------------------------------------------------- loopback


class AuthoredHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    files: dict[str, bytes] = {}
    cut_first_response_at: int | None = None
    served = 0
    lock = threading.Lock()

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        name = self.path.split("?", 1)[0].removeprefix("/repo/")
        payload = self.files[name]
        with self.lock:
            AuthoredHandler.served += 1
            first = AuthoredHandler.served == 1
        start = 0
        header = self.headers.get("Range")
        if header:
            first_byte, _, last = header.removeprefix("bytes=").partition("-")
            start = int(first_byte)
            end = int(last) if last else len(payload) - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            body = payload[start : end + 1]
        else:
            self.send_response(200)
            body = payload
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"' + hashlib.sha256(payload).hexdigest()[:24] + '"')
        self.end_headers()
        if first and self.cut_first_response_at is not None:
            self.wfile.write(body[: self.cut_first_response_at])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture
def loopback() -> Iterator[int]:
    AuthoredHandler.served = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AuthoredHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def plan_for(port: int, names: list[str], **changes: Any) -> AcquisitionPlan:
    values: dict[str, Any] = dict(
        plan_id="authored_leases",
        source_id="authored",
        provider="https",
        repository=f"http://127.0.0.1:{port}/repo",
        revision="authored-leases-v1",
        selected_files=names,
        output_artifact_id="authored_leases",
        limits=AcquisitionLimits(
            max_transferred_bytes=64 * MIB,
            max_decompressed_bytes=64 * MIB,
            max_temp_disk_bytes=64 * MIB,
            max_output_disk_bytes=64 * MIB,
            max_records=1000,
            max_requests=50,
            max_retries=1,
            max_workers=2,
            overall_deadline_seconds=120,
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


def blob(size: int, seed: int) -> bytes:
    block = hashlib.sha256(str(seed).encode()).digest() * 2048
    return (block * (size // len(block) + 1))[:size]


def test_whole_file_bytes_totals_and_journal_writes(tmp_path: Path, loopback: int) -> None:
    files = {"a.bin": blob(5 * MIB + 7, 1), "b.bin": blob(3 * MIB, 2)}
    AuthoredHandler.files = files
    AuthoredHandler.cut_first_response_at = None
    fetcher = BoundedFetcher(plan_for(loopback, sorted(files)), tmp_path / "s", tmp_path / "o")
    state = fetcher.run()
    assert state.status == "COMPLETED"
    for name, data in files.items():
        assert (tmp_path / "o" / name).read_bytes() == data
        progress = state.file_progress[name]
        assert progress.content_sha256 == hashlib.sha256(data).hexdigest()
        assert progress.bytes_downloaded == len(data)
    assert state.transferred_bytes == sum(len(data) for data in files.values())
    assert not state.accounting.reservations.get("transfer")
    assert not state.accounting.reservations.get("temp")
    # Per-64 KiB journal rewrites would need hundreds of fsyncs for 8 MiB.
    assert fetcher.journal.io_stats()["journal_fsyncs"] < 60


def test_mid_stream_failure_resumes_from_checkpoint_without_refetch(
    tmp_path: Path, loopback: int
) -> None:
    data = blob(3 * MIB + 5, 3)
    AuthoredHandler.files = {"c.bin": data}
    cut = 1 * MIB + 12345
    AuthoredHandler.cut_first_response_at = cut
    fetcher = BoundedFetcher(plan_for(loopback, ["c.bin"]), tmp_path / "s", tmp_path / "o")
    state = fetcher.run()
    AuthoredHandler.cut_first_response_at = None
    assert state.status == "COMPLETED"
    assert (tmp_path / "o" / "c.bin").read_bytes() == data
    # Every byte received before the failure was checkpointed durably, so the
    # continuation starts exactly there: transfer equals the file size.
    assert state.transferred_bytes == len(data)
    assert AuthoredHandler.served == 2


def test_selected_jsonl_leases_charge_exactly_what_was_read(tmp_path: Path, loopback: int) -> None:
    rows = b"".join(b'{"text":"row %d %s"}\n' % (i, b"z" * (i % 97)) for i in range(4000))
    AuthoredHandler.files = {"rows.jsonl": rows}
    AuthoredHandler.cut_first_response_at = None
    plan = plan_for(
        loopback,
        ["rows.jsonl"],
        mode=AcquisitionMode.SELECTED_RECORDS,
        row_ranges={"rows.jsonl": (10, 900)},
    )
    state = BoundedFetcher(plan, tmp_path / "s", tmp_path / "o").run()
    assert state.status == "COMPLETED"
    lines = (tmp_path / "o" / "selected_records.jsonl").read_bytes().splitlines()
    assert len(lines) == 890
    transferred = state.transferred_bytes
    # Historical 8 KiB lookahead reads: early stop, never the whole shard.
    assert 0 < transferred < len(rows) and transferred % 8192 == 0
    assert state.decompressed_bytes == transferred
    assert not state.accounting.reservations.get("transfer")
    assert not state.accounting.reservations.get("decompressed")
