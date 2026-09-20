"""Maintained D02 adversarial cases: authored bytes, isolated journals, loopback only."""

from __future__ import annotations

import gzip
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.progress import ProgressCorruptionError, ProgressJournal
from xlm.data.acquisition.verifier import AcquisitionVerifier
from xlm.data.sources.transport import BudgetExhaustedError, HuggingFaceTransport, TransportBudget

PAYLOAD = b'{"text":"authored A"}\n{"text":"authored B"}\n'


def plan_for(url: str, name: str = "rows.jsonl", **changes: Any) -> AcquisitionPlan:
    values: dict[str, Any] = dict(
        plan_id="authored_d02",
        source_id="authored",
        provider="https",
        repository=url,
        revision="authored-fixture-v1",
        selected_files=[name],
        output_artifact_id="authored_d02",
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


def offline_fetch(root: Path, records: int = 2) -> BoundedFetcher:
    limits = AcquisitionLimits(
        max_transferred_bytes=4096,
        max_decompressed_bytes=4096,
        max_temp_disk_bytes=16384,
        max_output_disk_bytes=4096,
        max_records=records,
        max_requests=2,
        max_retries=0,
        max_workers=1,
        overall_deadline_seconds=30,
    )
    plan = plan_for(
        "https://huggingface.co/authored-fixture",
        limits=limits,
        expected_file_digests={"rows.jsonl": hashlib.sha256(PAYLOAD).hexdigest()},
    )
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    fetcher.opener = OfflineOpener()
    return fetcher


def test_original_valid_transfer_and_restart_consumption(tmp_path: Path) -> None:
    first = offline_fetch(tmp_path).run()
    assert first.status == "COMPLETED" and first.records_acquired == 2
    assert first.transferred_bytes == len(PAYLOAD)
    assert (tmp_path / "output/rows.jsonl").read_bytes() == PAYLOAD
    resumed = offline_fetch(tmp_path)
    assert resumed.capacity_mgr.snapshot()["transferred_bytes"] == first.transferred_bytes
    after = resumed.run()
    assert after.transferred_bytes == first.transferred_bytes
    assert after.requests_made == first.requests_made and after.cache_hits == 1


@pytest.mark.parametrize("damage", ["same_length", "missing", "incomplete"])
def test_completed_original_is_never_repaired(tmp_path: Path, damage: str) -> None:
    offline_fetch(tmp_path).run()
    original = tmp_path / "output/rows.jsonl"
    if damage == "missing":
        original.unlink()
    else:
        original.write_bytes(b"X" * (len(PAYLOAD) if damage == "same_length" else 1))
    before = original.read_bytes() if original.exists() else None
    with pytest.raises(ProgressCorruptionError, match="original|integrity"):
        offline_fetch(tmp_path).run()
    assert (original.read_bytes() if original.exists() else None) == before


def test_whole_original_over_record_limit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="record limit"):
        offline_fetch(tmp_path, records=1).run()
    assert not (tmp_path / "output/rows.jsonl").exists()


def test_original_concurrent_reservations_share_allowance() -> None:
    manager = StorageCapacityManager(4096, 4096, 4096, 4096)

    def reserve(_: int) -> bool:
        try:
            manager.reserve_transfer(3000)
            return True
        except BudgetExhaustedError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, range(2))) == 1
    assert manager.remaining("transfer") == 1096


def test_fresh_process_reservations_and_killed_owner_remain_spent(tmp_path: Path) -> None:
    journal_path = tmp_path / "account.json"
    worker = tmp_path / "reserve.py"
    worker.write_text(
        """import os, sys
from pathlib import Path
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.acquisition.disk import StorageCapacityManager
from xlm.data.sources.transport import BudgetExhaustedError
j = ProgressJournal(Path(sys.argv[1]), 'authored', 'fixed')
m = StorageCapacityManager(4096,4096,16384,4096,journal=j)
try:
    m.reserve_transfer(3000)
except BudgetExhaustedError:
    sys.exit(7)
os._exit(0)
""",
        encoding="utf-8",
    )
    processes = [
        subprocess.Popen([sys.executable, str(worker), str(journal_path)]) for _ in range(2)
    ]
    assert sorted(p.wait(timeout=20) for p in processes) == [0, 7]
    journal = ProgressJournal(journal_path, "authored", "fixed")
    manager = StorageCapacityManager(4096, 4096, 16384, 4096, journal=journal)
    assert manager.remaining("transfer") == 1096
    assert manager.snapshot()["transferred_bytes"] == 0
    assert manager.snapshot()["reserved_transfer_bytes"] == 3000


def test_killed_download_resumes_in_a_fresh_process_without_new_allowance(tmp_path: Path) -> None:
    payload = (b'{"text":"' + b"A" * 40000 + b'"}\n') * 4
    prefix_sent = threading.Event()
    continue_response = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def do_GET(self) -> None:
            header = self.headers.get("Range")
            start = int(header.removeprefix("bytes=").split("-")[0]) if header else 0
            self.send_response(206 if header else 200)
            self.send_header("Content-Length", str(len(payload) - start))
            self.send_header("ETag", '"authored-stable"')
            if header:
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                )
            self.end_headers()
            try:
                if not header:
                    self.wfile.write(payload[:65536])
                    self.wfile.flush()
                    prefix_sent.set()
                    continue_response.wait(timeout=20)
                    self.wfile.write(payload[65536:])
                else:
                    self.wfile.write(payload[start:])
            except (BrokenPipeError, ConnectionResetError):
                pass

    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    limits = AcquisitionLimits(
        max_records=4,
        max_transferred_bytes=512 * 1024,
        max_requests=3,
        max_retries=0,
        overall_deadline_seconds=120,
    )
    plan = plan_for(f"http://127.0.0.1:{service.server_port}", limits=limits)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    script = tmp_path / "fetch.py"
    script.write_text(
        """import sys
from pathlib import Path
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import load_acquisition_plan
root=Path(sys.argv[1])
BoundedFetcher(load_acquisition_plan(root/'plan.json'), root/'scratch', root/'output').run()
""",
        encoding="utf-8",
    )
    worker = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    journal_path = tmp_path / "scratch/journals/authored_d02.progress.json"
    try:
        assert prefix_sent.wait(timeout=10)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = ProgressJournal(
                journal_path, plan.plan_id, plan.compute_behavioral_hash()
            ).state.model_dump()
            if state["file_progress"].get("rows.jsonl", {}).get("verified_prefix_bytes") == 65536:
                break
            time.sleep(0.02)
        else:
            pytest.fail("worker did not commit the authored prefix")
        worker.kill()
        worker.wait(timeout=5)
        continue_response.set()
        before = json.loads(journal_path.read_text())
        resumed = subprocess.run(
            [sys.executable, str(script), str(tmp_path)], capture_output=True, text=True, timeout=90
        )
        assert resumed.returncode == 0, resumed.stderr
        after = json.loads(journal_path.read_text())
        assert (tmp_path / "output/rows.jsonl").read_bytes() == payload
        assert after["transferred_bytes"] == len(payload)
        assert after["requests_made"] == before["requests_made"] + 1
        assert after["accounting"]["deadline_at"] == before["accounting"]["deadline_at"]
        assert sum(after["accounting"]["reservations"].get("transfer", {}).values()) >= sum(
            before["accounting"]["reservations"].get("transfer", {}).values()
        )
    finally:
        continue_response.set()
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
        if worker.stderr:
            worker.stderr.close()
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("path", ["../escape.jsonl", "CON", "a/../b", "A.jsonl,a.jsonl"])
def test_paths_and_collisions_fail_before_filesystem_effects(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        plan_for("https://huggingface.co/authored", selected_files=path.split(","))
    assert list(tmp_path.iterdir()) == []


def test_corrupt_journal_is_not_reset(tmp_path: Path) -> None:
    offline_fetch(tmp_path).run()
    path = tmp_path / "scratch/journals/authored_d02.progress.json"
    path.write_bytes(b"{corrupt")
    with pytest.raises(ProgressCorruptionError):
        offline_fetch(tmp_path)
    assert path.read_bytes() == b"{corrupt"


@pytest.fixture
def server() -> Any:
    sink = io.BytesIO()
    pq.write_table(
        pa.table({"text": ["alpha", "beta", "gamma", "delta"]}),
        sink,
        row_group_size=2,
        compression="NONE",
    )
    bodies = {
        "/rows.jsonl": PAYLOAD,
        "/rows.parquet": sink.getvalue(),
        "/bomb.jsonl.gz": gzip.compress(b'{"text":"' + b"A" * 100000 + b'"}\n'),
    }
    calls: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_: Any) -> None:
            pass

        def do_GET(self) -> None:
            calls.append(self.path)
            if self.path == "/redirect.jsonl":
                self.send_response(302)
                self.send_header("Location", "/rows.jsonl")
                body = b"redirect body"
            elif self.path == "/retry.jsonl" and calls.count(self.path) == 1:
                self.send_response(503)
                body = b"retry body"
            else:
                body = bodies.get(self.path, PAYLOAD)
                range_header = self.headers.get("Range")
                if range_header and self.path != "/ignored.parquet":
                    start, end = map(int, range_header.removeprefix("bytes=").split("-"))
                    total = len(body)
                    body = body[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                else:
                    self.send_response(200)
                self.send_header("ETag", '"authored-v1"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{service.server_port}", calls, bodies
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "name, extra", [("redirect.jsonl", b"redirect body"), ("retry.jsonl", b"retry body")]
)
def test_redirect_retry_bodies_and_requests_are_charged(
    server: Any, tmp_path: Path, name: str, extra: bytes
) -> None:
    url, calls, _ = server
    fetcher = BoundedFetcher(plan_for(url, name), tmp_path / "scratch", tmp_path / "out")
    state = fetcher.run()
    assert state.transferred_bytes == len(PAYLOAD) + len(extra)
    assert state.requests_made == 2 and len(calls) == 2
    assert state.records_acquired == 2


def test_metadata_uses_the_same_body_and_request_budget(server: Any) -> None:
    url, calls, _ = server
    budget = TransportBudget(max_bytes=4096, max_requests=1)
    with pytest.raises(BudgetExhaustedError, match="request"):
        HuggingFaceTransport(budget)._make_request(url + "/redirect.jsonl")
    assert budget.bytes_transferred == len(b"redirect body")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "name, interval, texts",
    [
        ("rows.jsonl", (1, 2), ["authored B"]),
        ("rows.parquet", (1, 3), ["beta", "gamma"]),
    ],
)
def test_selected_records_have_exact_original_locators(
    server: Any, tmp_path: Path, name: str, interval: tuple[int, int], texts: list[str]
) -> None:
    url, calls, _ = server
    plan = plan_for(url, name, mode="selected_records", row_ranges={name: interval})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out")
    state = fetcher.run()
    records = [
        json.loads(line)
        for line in (tmp_path / "out/selected_records.jsonl").read_bytes().splitlines()
    ]
    assert [record["text"] for record in records] == texts
    assert [record["_xlm_acquisition"]["row_index"] for record in records] == list(range(*interval))
    assert all(record["_xlm_acquisition"]["source_file"] == name for record in records)
    assert all(
        record["_xlm_acquisition"]["selection_hash"] == plan.compute_behavioral_hash()
        for record in records
    )
    assert state.records_acquired == len(texts) and not (tmp_path / "out" / name).exists()
    receipt = AcquisitionVerifier(plan, tmp_path / "out", fetcher.journal).verify()
    assert receipt.files[0].record_count == len(texts)
    requests = len(calls)
    resumed = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out").run()
    assert resumed.transferred_bytes == state.transferred_bytes and len(calls) == requests


def test_ignored_parquet_range_has_no_whole_shard_fallback(server: Any, tmp_path: Path) -> None:
    url, calls, _ = server
    plan = plan_for(
        url, "ignored.parquet", mode="selected_records", row_ranges={"ignored.parquet": (0, 1)}
    )
    with pytest.raises(ValueError, match="fallback"):
        BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out").run()
    assert len(calls) == 1 and not (tmp_path / "out/selected_records.jsonl").exists()


def test_compressed_corpus_expansion_is_refused(server: Any, tmp_path: Path) -> None:
    url, _, _ = server
    with pytest.raises(ValueError, match="decompression"):
        BoundedFetcher(plan_for(url, "bomb.jsonl.gz"), tmp_path / "scratch", tmp_path / "out").run()
    assert not (tmp_path / "out/bomb.jsonl.gz").exists()


def test_deadline_and_plan_identity_cannot_be_reset(tmp_path: Path) -> None:
    fetcher = offline_fetch(tmp_path)
    fetcher.run()
    with fetcher.journal.transaction() as state:
        state.accounting.deadline_at = time.time() - 1
    # A cached result may be inspected, but cannot grant a new execution allowance.
    with pytest.raises(TimeoutError):
        resumed = offline_fetch(tmp_path)
        resumed.capacity_mgr.check_deadline()
    changed = fetcher.plan.model_copy(
        update={"limits": fetcher.plan.limits.model_copy(update={"max_requests": 3})}
    )
    changed = changed.model_copy(
        update={
            "authorization": changed.authorization.model_copy(
                update={"authorization_hash": changed.compute_behavioral_hash()}
            )
        }
    )
    with pytest.raises(ProgressCorruptionError, match="identity"):
        BoundedFetcher(changed, tmp_path / "scratch", tmp_path / "output")


def test_public_plan_fetch_status_verify_prepare(server: Any, tmp_path: Path) -> None:
    url, _, _ = server
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    commands = tmp_path / "commands"
    commands.mkdir()

    def cli(*args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "xlm.cli.main", *args],
            cwd=root,
            env={**os.environ, "XLM_HOME": str(home)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        (commands / f"{len(list(commands.iterdir())):02}.json").write_text(
            json.dumps(
                {
                    "argv": args,
                    "exit": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            ),
            encoding="utf-8",
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result

    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "catalog_id": "authored",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "authored",
                        "provider": "https",
                        "repository": url,
                        "revision": "authored-fixture-v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ranges = tmp_path / "ranges.json"
    ranges.write_text('{"rows.jsonl":[1,2]}', encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    cli(
        "data",
        "plan",
        "--source",
        "authored",
        "--catalog",
        str(catalog),
        "--files",
        "rows.jsonl",
        "--mode",
        "selected_records",
        "--row-ranges",
        str(ranges),
        "--max-bytes",
        "4096",
        "--max-records",
        "1",
        "--output",
        str(plan_path),
    )
    plan = AcquisitionPlan.model_validate_json(plan_path.read_text())
    raw = home / "acquisition" / plan.plan_id / "raw"
    cli("data", "fetch", "--plan", str(plan_path), success=False)
    cli("data", "fetch", "--plan", str(plan_path), "--pilot-approved")
    status = json.loads(cli("data", "status", "--plan", str(plan_path), "--json").stdout)
    assert status["records_acquired"] == 1 and status["transferred_bytes"] == len(PAYLOAD)
    first = json.loads(
        cli("data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json").stdout
    )
    assert first["files"][0]["record_count"] == 1 and first["eligibility"] == "pilot_only"
    second = json.loads(
        cli("data", "verify", "--plan", str(plan_path), "--output-dir", str(raw), "--json").stdout
    )
    assert first == second
    config_path = (
        root
        / "data"
        / "audit"
        / "p23-remediation"
        / "stage04"
        / f"authored-prepare-{tmp_path.name}.json"
    )
    assert not config_path.exists()
    config = {
        "id": "authored_d02",
        "output_root": str(tmp_path / "prepared"),
        "budgets": {"max_records": 10, "max_subprocess_output_bytes": 65536},
        "stages": [
            {
                "stage_id": "copy",
                "kind": "local_copy",
                "copy_from": [str(raw)],
                "copy_to": "{output_root}/raw",
                "outputs": [
                    "{output_root}/raw/copy_manifest.json",
                    "{output_root}/raw/selected_records.jsonl",
                ],
            },
            {
                "stage_id": "inspect",
                "check_only": True,
                "command": ["data", "status", "--plan", str(plan_path), "--json"],
            },
        ],
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    try:
        cli("prepare", "--config", str(config_path), "--plan-only", "--json")
        first_prepare = json.loads(
            cli("prepare", "--config", str(config_path), "--authorize", "--json").stdout
        )
        second_prepare = json.loads(
            cli("prepare", "--config", str(config_path), "--authorize", "--json").stdout
        )
        assert first_prepare["stages"][0]["action"] == "ran"
        assert second_prepare["stages"][0]["action"] == "reused"
        assert (tmp_path / "prepared/raw/selected_records.jsonl").read_bytes() == (
            raw / "selected_records.jsonl"
        ).read_bytes()
    finally:
        config_path.unlink()  # Only the fixture config created by this test.


def test_nested_acquisition_consumes_parent_preparation_budget(server: Any, tmp_path: Path) -> None:
    from xlm.prepare.config import PrepareConfig
    from xlm.prepare.runner import run_prepare

    url, calls, _ = server
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    limits = AcquisitionLimits(
        max_transferred_bytes=4096,
        max_decompressed_bytes=4096,
        max_temp_disk_bytes=16384,
        max_output_disk_bytes=32768,
        max_requests=2,
        max_retries=0,
        max_workers=1,
        max_records=2,
    )
    plan = plan_for(url, limits=limits)
    path = tmp_path / "plan.json"
    path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    raw = home / "acquisition" / plan.plan_id / "raw/rows.jsonl"
    config = PrepareConfig.model_validate(
        {
            "id": "nested",
            "output_root": str(tmp_path / "prepared"),
            "budgets": {
                "fetch_max_bytes": 8192,
                "max_temp_disk_bytes": 65536,
                "max_output_disk_bytes": 262144,
            },
            "stages": [
                {
                    "stage_id": "fetch",
                    "command": ["data", "fetch", "--plan", str(path)],
                    "outputs": [str(raw)],
                    "watched_inputs": [str(path)],
                }
            ],
        }
    )
    run_prepare(config, root / "recipes/prepare/offline_toy.yaml", home, root, authorize=True)
    journal_path = tmp_path / "prepared/.accounting/resources.json"
    before = json.loads(journal_path.read_text())
    assert before["accounting"]["consumed"]["input_bytes"] == len(PAYLOAD)
    assert before["accounting"]["consumed"]["network_requests"] == 1
    run_prepare(
        config, root / "recipes/prepare/offline_toy.yaml", home, root, authorize=True, force=True
    )
    after = json.loads(journal_path.read_text())
    assert after["accounting"]["consumed"]["input_bytes"] == len(PAYLOAD)
    assert after["accounting"]["consumed"]["network_requests"] == 1
    assert len(calls) == 1 and raw.read_bytes() == PAYLOAD


def test_selected_private_attempt_resumes_in_new_process(
    server: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xlm.data.acquisition.selection as selection

    url, calls, _ = server
    plan = plan_for(url, mode="selected_records", row_ranges={"rows.jsonl": (0, 2)})
    fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out")
    serialize = selection.selected_record
    seen = 0

    def fail_second(*args: Any) -> bytes:
        nonlocal seen
        seen += 1
        if seen == 2:
            raise RuntimeError("authored interruption after first selected row")
        return serialize(*args)

    monkeypatch.setattr(selection, "selected_record", fail_second)
    with pytest.raises(RuntimeError, match="authored interruption"):
        fetcher.run()
    retained = {p: p.read_bytes() for p in fetcher.partial_dir.glob("*.part")}
    assert retained and not (tmp_path / "out/selected_records.jsonl").exists()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.cli.main",
            "data",
            "fetch",
            "--plan",
            str(plan_path),
            "--scratch-dir",
            str(tmp_path / "scratch"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "XLM_HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 0, result.stderr
    rows = [
        json.loads(line)
        for line in (tmp_path / "out/selected_records.jsonl").read_bytes().splitlines()
    ]
    assert [row["text"] for row in rows] == ["authored A", "authored B"]
    assert [row["_xlm_acquisition"]["row_index"] for row in rows] == [0, 1]
    assert all(path.read_bytes() == value for path, value in retained.items())
    fetcher.journal.save()
    assert fetcher.journal.state.transferred_bytes == 2 * len(PAYLOAD)
    assert fetcher.journal.state.requests_made == len(calls) == 2


def test_blank_gzip_expansion_is_bounded(tmp_path: Path) -> None:
    from xlm.data.acquisition.records import inspect_records

    path = tmp_path / "blank.jsonl.gz"
    path.write_bytes(gzip.compress(b"\n" * 100000))
    with pytest.raises(ValueError, match="decompression"):
        inspect_records(path, path.name, AcquisitionLimits(max_decompression_ratio=2))


def test_production_admission_reference_is_not_evidence() -> None:
    from xlm.data.acquisition.plan import AuthorizationRequiredError, validate_plan_authorization

    plan = plan_for(
        "https://huggingface.co/authored",
        is_pilot=False,
        admitted_source_reference="invented-reference",
    )
    with pytest.raises(AuthorizationRequiredError, match="prior operator admission verified"):
        validate_plan_authorization(plan)


def test_public_probe_attempt_has_durable_body_request_and_deadline_account(
    server: Any, tmp_path: Path
) -> None:
    url, calls, bodies = server
    bodies["/manifest.json"] = b'{"license":"authored-fixture","metadata":{}}'
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "catalog_id": "authored",
                "sources": [
                    {
                        "candidate_number": 1,
                        "source_id": "authored",
                        "provider": "https",
                        "repository": url + "/manifest.json",
                        "revision": "authored-fixture-v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "XLM_HOME": str(tmp_path / "home")}
    argv = [
        sys.executable,
        "-m",
        "xlm.cli.main",
        "data",
        "probe",
        "--source",
        "authored",
        "--catalog",
        str(catalog),
        "--live",
        "--no-publish",
        "--probe-id",
        "authored",
        "--budget-mib",
        "1",
        "--json",
    ]
    states = []
    for _ in range(2):
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        evidence = json.loads(result.stdout)
        assert (
            evidence["immutable_revision"] == hashlib.sha256(bodies["/manifest.json"]).hexdigest()
        )
        assert not evidence["verified_schema"]  # Metadata never proves corpus admission.
        states.append(
            json.loads((tmp_path / "home/discovery/authored_default_authored.json").read_text())
        )
    assert (
        states[1]["transferred_bytes"]
        == 2 * states[0]["transferred_bytes"]
        == 2 * len(bodies["/manifest.json"])
    )
    assert states[1]["requests_made"] == len(calls) == 2
    assert states[1]["accounting"]["deadline_at"] == states[0]["accounting"]["deadline_at"]


@pytest.mark.parametrize("resource", ["temp", "output"])
def test_shared_storage_reservations_and_verified_reconciliation(
    tmp_path: Path, resource: str
) -> None:
    from xlm.data.acquisition.disk import DiskCeilingExceededError

    path = tmp_path / "journal.json"
    first = StorageCapacityManager(
        16384, 16384, 16384, 16384, journal=ProgressJournal(path, "storage", "fixed")
    )
    token = first.reserve(resource, 10000)
    second = StorageCapacityManager(
        16384, 16384, 16384, 16384, journal=ProgressJournal(path, "storage", "fixed")
    )
    with pytest.raises(DiskCeilingExceededError, match="outstanding reservations"):
        second.reserve(resource, 10000)
    assert second.snapshot()[f"reserved_{resource}_bytes"] == 10000
    assert token in second.journal.state.accounting.reservations[resource]
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "committed.part").write_bytes(b"A" * 100)
    second.reconcile_disk(resource, owned)  # Test owns all writers and this private tree.
    assert second.snapshot()[f"reserved_{resource}_bytes"] == 0
    assert second.snapshot()[f"{'temp' if resource == 'temp' else 'output'}_disk_bytes"] == 100
    second.reserve(resource, 200, persistent=True)
    second.reconcile_disk(resource, owned)
    assert second.snapshot()[f"reserved_{resource}_bytes"] == 200


def test_selected_parquet_refuses_parser_bound_without_original_fallback(
    server: Any, tmp_path: Path
) -> None:
    url, calls, _ = server
    limits = AcquisitionLimits(max_record_bytes=32, max_parser_bytes=32)
    plan = plan_for(
        url,
        "rows.parquet",
        mode="selected_records",
        row_ranges={"rows.parquet": (0, 1)},
        limits=limits,
    )
    with pytest.raises(ValueError, match="parser bound"):
        BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out").run()
    assert not (tmp_path / "out/rows.parquet").exists()
    assert not (tmp_path / "out/selected_records.jsonl").exists()
    assert calls == ["/rows.parquet"]  # Only the four-byte magic request.


def test_source_validator_is_durable_across_selected_attempts(tmp_path: Path) -> None:
    plan = plan_for(
        "https://huggingface.co/authored",
        mode="selected_records",
        row_ranges={"rows.jsonl": (0, 1)},
    )
    first = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out")
    first.journal.bind_source("rows.jsonl", '"first"', 44)
    resumed = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "out")
    with pytest.raises(ProgressCorruptionError, match="changed"):
        resumed.journal.bind_source("rows.jsonl", '"second"', 44)
    assert not (tmp_path / "out/selected_records.jsonl").exists()
