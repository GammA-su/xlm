"""High-throughput source engine on an authored loopback endpoint (offline).

Covers a full run, interruption and exact resume, local processing retry,
local-complete reuse, bounded concurrency, scratch and durable caps, fatal
reporting, performance receipts, sufficiency, deterministic top-up, the
first-pass seal, the dashboard and the bounded benchmark.
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import socket
import threading
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mix01_source_fixtures import REPOSITORY, REVISION, parquet_bytes, ultrax_row
from test_source_plan import PIN, inventory, models
from test_source_rowgroups import crashing_worker
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_local
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.source_dashboard import (
    FRESH_DOWNLOAD,
    LOCAL_COMPLETE_REUSE,
    LOCAL_PROCESSING_RETRY,
    RESUMABLE_PARTIAL,
    SEALED_SKIP,
    Dashboard,
    ObservedScratch,
    Snapshot,
)
from xlm.data.acquisition.source_rowgroups import RowGroupError, RowGroupParallel

FILES = 7
ROWS_PER_FILE = 300
GROUP_ROWS = 100


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        if address[0] not in ("127.0.0.1", "localhost"):
            raise AssertionError("offline test attempted a non-loopback connection")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@dataclass
class Served:
    files: dict[str, bytes] = field(default_factory=dict)
    drops: dict[str, list[int]] = field(default_factory=dict)
    delay: float = 0.0
    requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def etag(self, name: str) -> str:
        return '"' + hashlib.sha256(b"storage:" + self.files[name]).hexdigest() + '"'

    def hits(self, name: str) -> int:
        return sum(1 for kind, n, _ in self.requests if kind == "object" and n == name)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state: Served = self.server.state  # type: ignore[attr-defined]
        kind, _, name = urllib.parse.unquote(self.path).lstrip("/").partition("/")
        with state.lock:
            state.requests.append((kind, name, self.headers.get("Range")))
        payload = state.files.get(name)
        if payload is None:
            self.send_error(404)
            return
        if kind == "resolve":
            self.send_response(302)
            port = self.server.server_port  # type: ignore[attr-defined]
            self.send_header(
                "Location", f"http://127.0.0.1:{port}/object/{urllib.parse.quote(name)}"
            )
            self.send_header("X-Linked-ETag", '"' + hashlib.sha256(payload).hexdigest() + '"')
            self.send_header("X-Linked-Size", str(len(payload)))
            self.send_header("X-Repo-Commit", REVISION)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with state.lock:
            state.active += 1
            state.max_active = max(state.max_active, state.active)
            drops = state.drops.get(name) or []
            cut = drops.pop(0) if drops else None
        try:
            if state.delay:
                time.sleep(state.delay)
            requested = self.headers.get("Range")
            if requested and self.headers.get("If-Range") in (None, state.etag(name)):
                first, _, last = requested.removeprefix("bytes=").partition("-")
                start, end = int(first), int(last) if last else len(payload) - 1
                body = payload[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            else:
                body = payload
                self.send_response(200)
            self.send_header("ETag", state.etag(name))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if cut is None:
                self.wfile.write(body)
            else:
                self.wfile.write(body[:cut])
                self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            with state.lock:
                state.active -= 1


@pytest.fixture
def served() -> Iterator[tuple[Served, str]]:
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.daemon_threads = True
    state = Served()
    service.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{service.server_port}"
    finally:
        service.shutdown()
        service.server_close()


@dataclass
class World:
    roots: runner.Roots
    served: Served
    base: str
    inventory: dict[str, Any]
    layout: tp.SourceLayout
    policy: dict[str, Any]
    stream: io.StringIO

    def url(self, name: str) -> str:
        return f"{self.base}/resolve/{name}"

    def ordered(self) -> list[str]:
        return planner.check_inventory(
            self.inventory, PIN["source_id"], PIN["repository"], PIN["revision"]
        )

    def plan(self, tokens: int, predecessor: planner.Predecessor | None = None) -> dict[str, Any]:
        quotas = {"first_pass_headroom_quotas": {"ultrax_ultrafineweb": tokens}}
        estimate = {
            "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
            "sources": {
                "ultrax_ultrafineweb": {
                    "status": "ESTIMATED",
                    "first_pass_usable_token_target": tokens,
                    "required_canonical_bytes_base": tokens * 4.0,
                }
            },
        }
        record = planner.build_plan(
            source_key="ultrax",
            pin=PIN,
            requirement=planner.requirement_from(
                "ultrax_ultrafineweb",
                quotas,
                estimate,
                quotas_sha256="1" * 64,
                estimate_sha256="2" * 64,
            ),
            inventory=self.inventory,
            inventory_sha256="3" * 64,
            layout=self.layout,
            calibration={"rows": "0" * 64},
            policy=self.policy,
            admission={"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64},
            predecessor=predecessor,
        )
        runner.store_plan(self.roots, record)
        runner.authorize(self.roots, int(record["sequence"]), record["digest"], "tester", admitted)
        return record

    def run(self, sequence: int = 1, **kwargs: Any) -> dict[str, Any]:
        options: dict[str, Any] = {"process_workers": 0, "download_workers": 2}
        options.update(kwargs)
        return runner.run_plan(
            self.roots,
            sequence,
            admitted=admitted,
            stream=self.stream,
            url_for=self.url,
            **options,
        )


def admitted(plan: Any) -> None:
    """Stands in for the store admission check, which test_mix01_admission covers."""
    assert plan.source_id == PIN["source_id"] and plan.revision == REVISION


@pytest.fixture
def world(tmp_path: Path, served: tuple[Served, str], monkeypatch: pytest.MonkeyPatch) -> World:
    state, base = served
    monkeypatch.setattr(sp, "READ_BYTES", 256)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 1024)
    names = [f"data/part-{i:04d}.parquet" for i in range(FILES)]
    for index, name in enumerate(names):
        rows = [
            ultrax_row(index * ROWS_PER_FILE + r, empty=r % 50 == 7) for r in range(ROWS_PER_FILE)
        ]
        state.files[name] = parquet_bytes(rows, GROUP_ROWS)
    sizes = [len(payload) for payload in state.files.values()]
    texts = [len(ultrax_row(i)["cleaned_content"].encode()) for i in range(FILES * ROWS_PER_FILE)]
    canonical_per_row = sum(t for i, t in enumerate(texts) if i % ROWS_PER_FILE % 50 != 7) / len(
        texts
    )
    layout = tp.SourceLayout(
        source_id=PIN["source_id"],
        file_bytes=max(sizes),
        group_rows=GROUP_ROWS,
        group_bytes=max(sizes) // (ROWS_PER_FILE // GROUP_ROWS),
        projected_group_bytes=max(sizes) // (ROWS_PER_FILE // GROUP_ROWS) // 2,
        range_requests_per_group=2.0,
        metadata_requests_per_file=4,
        canonical_bytes_per_row=canonical_per_row,
        range_record_bytes_per_row=120.0,
        metadata_bytes_per_file=1_000,
        source_files=None,
        evidence={"rows": "0" * 64},
    )
    report = tp.evaluate(
        layout, tp.Requirement(PIN["source_id"], 10_000, 1.15), tp.Ceilings(), models()
    )
    assert report["selected_mode"] == "whole_file_local"
    roots = runner.Roots(tmp_path / "data", tmp_path / "scratch", "ultrax")
    return World(
        roots=roots,
        served=state,
        base=base,
        inventory=inventory_of(names),
        layout=layout,
        policy=tp.freeze(report, basis="modeled", inputs={"model": "authored"}),
        stream=io.StringIO(),
    )


def inventory_of(names: list[str]) -> dict[str, Any]:
    value = inventory(len(names))
    seed = value["seed"]
    entries = sorted(
        (
            {
                "file": name,
                "size_bytes": None,
                "order_key": hashlib.sha256(
                    f"{seed}|{REPOSITORY}|{REVISION}|{name}".encode()
                ).hexdigest(),
            }
            for name in names
        ),
        key=lambda e: (e["order_key"], e["file"]),
    )
    value["files"] = entries
    value["inventory_digest"] = planner.inventory_digest(value)
    return value


def tokens_for_files(world: World, files: int) -> int:
    """First-pass tokens for which the planner selects exactly ``files`` files."""
    per_file = world.layout.rows_per_file * world.layout.canonical_bytes_per_row
    return int((files - 0.5) * per_file / 1.15 / 4)


# ---------------------------------------------------------------- tests


def test_full_run_seals_verifies_and_never_redoes(world: World) -> None:
    record = world.plan(tokens_for_files(world, 3))
    files = [e["file"] for e in record["selection"]["files"]]
    assert files == world.ordered()[:3]
    report = world.run()
    assert report["outcome"]["status"] == "completed"
    assert report["kind"] == runner.PERFORMANCE_KIND
    assert report["transport_mode"] == "whole_file_local"
    assert report["transfer"]["files"] == 3 and report["transfer"]["requests"] == 6
    assert report["processing"]["rows"] == 3 * ROWS_PER_FILE
    assert report["processing"]["rejected"] == 3 * 6
    assert report["concurrency"] == {"download_workers": 2, "process_workers": 0}
    resume = runner.resume_state(world.roots, record)
    assert resume["sealed"] == 3
    for receipt in resume["receipts"]:
        raw = world.roots.data_root / receipt["raw"]["path"]
        assert raw.is_file() and sp.identity_path(raw).is_file()
        assert receipt["identity"]["expected_sha256_source"] == "x-linked-etag"
        assert receipt["documents"] + receipt["rejected"] == receipt["rows"] == ROWS_PER_FILE
    assert runner.verify_plan(world.roots, record, content=True)["units_verified"] == 3
    assert list((world.roots.plan_dir(1)).glob("performance-*.json"))
    assert not list(world.roots.scratch().rglob("*.part"))
    hits = {name: world.served.hits(name) for name in files}
    again = world.run()
    assert again["transfer"]["files"] == 0
    assert {name: world.served.hits(name) for name in files} == hits
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["counts"][SEALED_SKIP] == 3
    output = world.stream.getvalue()
    assert "Authored web paragraph" not in output and "UNITS 3/3" in output


def test_interruption_preserves_partials_and_resumes_exactly(world: World) -> None:
    record = world.plan(tokens_for_files(world, 3))
    victim = record["selection"]["files"][0]["file"]
    world.served.drops[victim] = [1300] * 8
    with pytest.raises(sp.SourceTransferError, match="attempts exhausted"):
        world.run(download_workers=1)
    output = world.stream.getvalue()
    for heading in (
        "ROOT FAILURE",
        "CANCELLED BECAUSE OF ROOT FAILURE",
        "PRESERVED WORK",
        "RESTART CLASSIFICATION",
    ):
        assert heading in output
    failed = json.loads(next(world.roots.plan_dir(1).glob("performance-*.json")).read_text())
    assert failed["outcome"]["status"] == "failed"
    assert failed["outcome"]["root_failure"]["key"] == "f00000"
    resume = runner.resume_state(world.roots, record)
    restart = runner.classify(world.roots, record, resume, "p01")
    assert restart["counts"][RESUMABLE_PARTIAL] == 1
    assert restart["resumable_verified_bytes"] >= 1024
    assert restart["counts"][FRESH_DOWNLOAD] == 2
    world.served.drops.clear()
    world.run(download_workers=1)
    ranges = [r for kind, name, r in world.served.requests if kind == "object" and name == victim]
    assert ranges[-1] is not None and ranges[-1].startswith("bytes=")
    assert runner.resume_state(world.roots, record)["sealed"] == 3


def test_processing_failure_keeps_download_and_retries_locally(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = world.plan(tokens_for_files(world, 2))
    victim = record["selection"]["files"][1]["file"]
    real = source_local.adapt_source_file

    def failing(path: Path, output: Path, **kwargs: Any) -> dict[str, Any]:
        if kwargs["source_file"] == victim:
            raise source_local.SourceAdaptError("authored processing failure")
        return real(path, output, **kwargs)

    monkeypatch.setattr(source_local, "adapt_source_file", failing)
    with pytest.raises(source_local.SourceAdaptError):
        world.run(download_workers=1)
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["units"][LOCAL_PROCESSING_RETRY] == ["f00001"]
    monkeypatch.setattr(source_local, "adapt_source_file", real)
    hits = world.served.hits(victim)
    world.run(download_workers=1)
    assert world.served.hits(victim) == hits
    assert runner.resume_state(world.roots, record)["sealed"] == 2


def test_worker_processes_produce_identical_units(world: World) -> None:
    record = world.plan(tokens_for_files(world, 3))
    world.run(download_workers=3, process_workers=2)
    receipts = runner.resume_state(world.roots, record)["receipts"]
    assert len(receipts) == 3
    for receipt in receipts:
        raw = world.roots.data_root / receipt["raw"]["path"]
        again = source_local.adapt_source_file(
            raw,
            world.roots.data_root / "check" / receipt["file"].replace("/", "_"),
            source_file=receipt["file"],
            source_id=PIN["source_id"],
            view_id=PIN["view_id"],
            adapter_id=PIN["adapter_id"],
            repository=PIN["repository"],
            revision=PIN["revision"],
            plan_id=record["acquisition_plan"]["plan_id"],
            plan_hash=record["acquisition_plan"]["plan_hash"],
            selection_hash=record["acquisition_plan"]["selection_hash"],
            identity=receipt["identity"],
            limits={k: record["limits"][k] for k in runner.PROCESS_LIMIT_KEYS},
        )
        assert again["documents_sha256"] == receipt["documents_sha256"]
        assert again["selected_records_sha256"] == receipt["selected_records"]["sha256"]


def bind_row_group_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        planner.SOURCE_ROW_GROUP_PARALLEL,
        (PIN["source_id"], PIN["view_id"]),
        (
            RowGroupParallel(workers=2, lookahead=1, processing_slots=6, memory_bytes=32 * 1024**3),
            "authored test binding",
        ),
    )


def serial_unit(world: World, record: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    limits = {k: record["limits"][k] for k in runner.PROCESS_LIMIT_KEYS}
    return source_local.adapt_source_file(
        world.roots.data_root / receipt["raw"]["path"],
        world.roots.data_root / "serial" / receipt["file"].replace("/", "_"),
        source_file=receipt["file"],
        source_id=PIN["source_id"],
        view_id=PIN["view_id"],
        adapter_id=PIN["adapter_id"],
        repository=PIN["repository"],
        revision=PIN["revision"],
        plan_id=record["acquisition_plan"]["plan_id"],
        plan_hash=record["acquisition_plan"]["plan_hash"],
        selection_hash=record["acquisition_plan"]["selection_hash"],
        identity=receipt["identity"],
        limits=limits,
    )


def test_row_group_parallel_plan_seals_serial_identical_units(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    bind_row_group_parallel(monkeypatch)
    record = world.plan(tokens_for_files(world, 3))
    assert record["limits"]["row_group_parallel"]["workers"] == 2
    assert record["limits"]["process_workers"] == 2  # 6 slots // (2 workers + 1 coordinator)
    with pytest.raises(runner.RunError, match="authorized processing slots"):
        world.run(process_workers=3)
    # Two file processes, each coordinating its own pool of two row-group workers.
    report = world.run(process_workers=2)
    assert report["outcome"]["status"] == "completed"
    receipts = runner.resume_state(world.roots, record)["receipts"]
    assert len(receipts) == 3
    for receipt in receipts:
        serial = serial_unit(world, record, receipt)
        assert serial["row_group_workers"] == 1
        assert receipt["documents_sha256"] == serial["documents_sha256"]
        assert receipt["selected_records"]["sha256"] == serial["selected_records_sha256"]
        assert receipt["rejections"]["sha256"] == serial["rejections_sha256"]
        assert receipt["rejection_counts_by_code"] == serial["rejection_counts_by_code"]
        assert (receipt["documents"], receipt["rejected"], receipt["canonical_bytes"]) == (
            serial["documents"],
            serial["rejected"],
            serial["canonical_bytes"],
        )
    assert runner.verify_plan(world.roots, record, content=True)["units_verified"] == 3


def test_row_group_worker_failure_publishes_nothing_and_resumes(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    bind_row_group_parallel(monkeypatch)
    record = world.plan(tokens_for_files(world, 2))
    with monkeypatch.context() as patch:
        patch.setattr(source_local, "adapt_row_group", crashing_worker)
        with pytest.raises(RowGroupError, match="terminated abnormally"):
            world.run()
    assert runner.resume_state(world.roots, record)["sealed"] == 0
    assert not list(world.roots.canonical.glob("p*/f*"))
    world.run()
    receipts = runner.resume_state(world.roots, record)["receipts"]
    assert len(receipts) == 2
    for receipt in receipts:
        assert (
            receipt["documents_sha256"] == serial_unit(world, record, receipt)["documents_sha256"]
        )


def test_complete_scratch_download_is_reused(world: World) -> None:
    record = world.plan(tokens_for_files(world, 2))
    name = record["selection"]["files"][0]["file"]
    limits = runner.transfer_limits(record)
    sp.download_source(
        world.url(name),
        world.roots.scratch("p01", "f00000.parquet.part"),
        world.roots.scratch("p01", "f00000.state.json"),
        name=name,
        limits=limits,
        revision=REVISION,
    )
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["units"][LOCAL_COMPLETE_REUSE] == ["f00000"]
    hits = world.served.hits(name)
    world.run()
    assert world.served.hits(name) == hits
    assert runner.resume_state(world.roots, record)["sealed"] == 2


def test_download_concurrency_is_bounded(world: World) -> None:
    world.plan(tokens_for_files(world, 4))
    world.served.delay = 0.05
    world.run(download_workers=2)
    assert 1 <= world.served.max_active <= 2


def test_workers_cannot_exceed_the_plan(world: World) -> None:
    world.plan(tokens_for_files(world, 2))
    with pytest.raises(runner.RunError, match="concurrency ceiling"):
        world.run(download_workers=17)


def test_durable_and_scratch_caps_refuse_before_transfer(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.plan(tokens_for_files(world, 2))
    monkeypatch.setattr(runner, "free_bytes", lambda path: 10)
    with pytest.raises(runner.RunError, match="durable volume"):
        world.run()
    assert world.served.requests == []
    monkeypatch.undo()
    budget = ObservedScratch(world.roots.scratch(), 100, 0)
    with pytest.raises(sp.ScratchCapError):
        budget.reserve("k", 101, world.roots.scratch("x"))
    tight = ObservedScratch(world.roots.scratch(), 1000, 10**18)
    assert tight.reserve("k", 10, world.roots.scratch("y")) is False


def test_unauthorized_or_altered_plans_do_not_run(world: World) -> None:
    record = world.plan(tokens_for_files(world, 2))
    with pytest.raises(runner.RunError, match="does not match"):
        runner.authorize(world.roots, 1, "0" * 64, "tester", admitted)
    (world.roots.plan_dir(1) / "authorization.json").unlink()
    with pytest.raises(runner.RunError, match="not authorized"):
        world.run()
    with pytest.raises(planner.PlanError, match="digest"):
        runner.store_plan(world.roots, {**record, "live_run": True})


def test_sufficiency_top_up_and_first_pass_seal(world: World) -> None:
    # Ask for 3 files' worth but plan from a doubled yield: the first plan under-delivers.
    tokens = tokens_for_files(world, 3)
    doubled = 2 * world.layout.canonical_bytes_per_row
    optimistic = tp.SourceLayout(**{**world.layout.__dict__, "canonical_bytes_per_row": doubled})
    world.layout = optimistic
    first = world.plan(tokens)
    assert len(first["selection"]["files"]) == 2
    world.run()
    status = runner.sufficiency(world.roots)
    assert status["status"] == "TOP_UP" and status["deficit_canonical_bytes"] > 0
    totals = runner.account(world.roots, first)
    second = world.plan(
        tokens,
        planner.Predecessor(
            first, int(totals["canonical_bytes"]), int(totals["units_sealed"]), totals["digest"]
        ),
    )
    assert second["selection"]["start_rank"] == 2
    assert [e["file"] for e in second["selection"]["files"]] == world.ordered()[
        2 : 2 + len(second["selection"]["files"])
    ]
    with pytest.raises(runner.RunError, match="TOP_UP|INCOMPLETE"):
        runner.first_pass_seal(world.roots)
    world.run(sequence=2)
    assert runner.sufficiency(world.roots)["status"] == "SUFFICIENT"
    seal = runner.first_pass_seal(world.roots)
    assert seal["kind"] == runner.SEAL_KIND and seal["training_permitted"] is False
    assert [u["sequence"] for u in seal["units"]] == [1, 1, 2]
    assert runner.first_pass_seal(world.roots)["digest"] == seal["digest"]
    # A sealed unit that changes is caught.
    unit = world.roots.unit_dir(1, 0) / "documents.jsonl"
    unit.write_bytes(unit.read_bytes() + b"\n")
    with pytest.raises(runner.RunError):
        runner.verify_plan(world.roots, first, content=True)


def test_benchmark_is_bounded_reserved_and_discarded(world: World) -> None:
    entries = bench.reserved_entries(world.ordered(), planner.BENCHMARK_RESERVED_POSITIONS, 1)
    assert entries[0]["rank"] == FILES - 2
    record = bench.build_benchmark(
        source_key="ultrax",
        label="b1",
        pin=PIN,
        entries=entries,
        layout=world.layout,
        seed=int(world.inventory["seed"]),
        admission={"probe_fingerprint": "f" * 64},
        download_workers=2,
        process_workers=0,
    )
    bench.store_benchmark(world.roots, record)
    with pytest.raises(runner.RunError, match="not authorized|missing"):
        bench.run_benchmark(
            world.roots, "b1", admitted=admitted, stream=world.stream, url_for=world.url
        )
    bench.authorize_benchmark(world.roots, "b1", record["digest"], "tester", admitted)
    receipt = bench.run_benchmark(
        world.roots, "b1", admitted=admitted, stream=world.stream, url_for=world.url
    )
    assert receipt["benchmark"]["label"] == "b1" and receipt["outcome"]["status"] == "completed"
    assert receipt["transfer"]["files"] == 1 and receipt["processing"]["rows"] == ROWS_PER_FILE
    assert not (world.roots.data_root / "acq-raw").exists()
    assert not list(world.roots.scratch().rglob("*.part"))
    assert receipt["concurrency"] == {"download_workers": 1, "process_workers": 0}
    model = tp.measured_model(receipt)
    assert model.basis.startswith("measured") and model.streams == 1
    with pytest.raises(runner.RunError, match="reserved inventory tail"):
        bench.reserved_entries(world.ordered(), planner.BENCHMARK_RESERVED_POSITIONS, 3)


def test_range_benchmark_receipt_normalizes_a_pilot_fetch(tmp_path: Path) -> None:
    documents = tmp_path / "documents.jsonl"
    documents.write_text(json.dumps({"text": "abc", "utf8_byte_count": 3}) + "\n", encoding="utf-8")
    plan = {
        "plan_id": "p",
        "plan_hash": "h",
        **{k: PIN[k] for k in ("source_id", "view_id", "revision")},
    }
    perf = {
        "plan_id": "p",
        "status": "COMPLETED",
        "requests_made": 10,
        "records_acquired": 1,
        "wall_seconds": 2.0,
        "transferred_bytes": 1000,
        "max_workers_configured": 2,
        "telemetry": {"open_seconds": 3.0, "parquet_groups": 4},
    }
    receipt = bench.range_benchmark_receipt(
        source_key="ultrax",
        pin=PIN,
        plan=plan,
        perf=perf,
        journal={"plan_hash": "h", "status": "COMPLETED"},
        documents=documents,
        adapt_log="Adapt throughput: 1 records in 0.10s (10.0/s, x)",
    )
    assert receipt["transport_mode"] == "range_selected"
    assert receipt["transfer"]["mean_request_open_seconds"] == 0.3
    model = tp.measured_model({**receipt, "processing": receipt["processing"]})
    assert model.seconds_per_request == 0.3 and model.rows_per_process_second == 10.0
    with pytest.raises(runner.RunError, match="different plans"):
        bench.range_benchmark_receipt(
            source_key="ultrax",
            pin=PIN,
            plan=plan,
            perf={**perf, "plan_id": "q"},
            journal={"plan_hash": "h", "status": "COMPLETED"},
            documents=documents,
            adapt_log="Adapt throughput: 1 records in 0.10s (10.0/s, x)",
        )


def test_dashboard_renders_metadata_only() -> None:
    stream = io.StringIO()
    dashboard = Dashboard(stream, Path("unused.jsonl"), now=0.0)
    snap = Snapshot(
        source="ultrax_ultrafineweb",
        plan="plan 1",
        mode="whole_file_local",
        total_units=4,
        sealed_units=1,
    )
    snap.transfer_bytes, snap.downloaded_bytes, snap.expected_bytes = (
        50_000_000,
        50_000_000,
        200_000_000,
    )
    snap.processed_rows, snap.known_rows, snap.canonical_bytes = 1000, 4000, 400_000
    snap.target_canonical_bytes, snap.requests, snap.retries = 4_000_000, 2, 0
    snap.downloading = ["data/\x1b[2Jevil.parquet"]
    lines = dashboard.render(snap, 10.0)
    text = "\n".join(lines)
    for token in (
        "UNITS 1/4",
        "DOWNLOAD",
        "recent=",
        "PROCESS",
        "YIELD",
        "TARGET",
        "ETA~",
        "DISK",
        "ACTIVE",
    ):
        assert token in text
    assert "\x1b" not in text
