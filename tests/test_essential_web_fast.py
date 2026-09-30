"""Fast Essential-Web campaign: whole-file transport, local processing, accounting.

Authored synthetic fixtures and a loopback HTTP server only. No dataset access:
these tests prove transport rules, byte equivalence with the certified reader on
authored rows and campaign logic, not live endpoint behaviour or live dataset
compatibility.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

import test_essential_web_bulk as historical_tests
from xlm.cli.data_cmd import app
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import (
    AcquisitionMode,
    AcquisitionPlan,
    ParquetWindowDecode,
    PlanAuthorization,
    SourceDriftDetectedError,
    load_acquisition_plan,
    plan_requires_production_admission,
    save_acquisition_plan,
)
from xlm.data.acquisition.records import encode_record, selected_record
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.malformed import MalformedLimitError
from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID, RecordRejectedError
from xlm.data.adapters.rejections import serialize_document
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_local as local
from xlm.data.sources.transport import HostNotAllowlistedError

REPO = Path(__file__).resolve().parents[1]
MIB = 1024**2
REVISION = selector.SOURCE_REVISION
VIEWS = ("essential_science", "essential_practical", "essential_prose")
SCIENCE, PRACTICAL, PROSE = VIEWS
KINDS = historical_tests.KINDS
PROCESS_LIMITS: dict[str, Any] = {
    "max_decompression_ratio": 15.0,
    "max_parser_bytes": 32 * MIB,
    "max_rows_per_file": 20_000,
    "max_record_bytes": MIB,
    "max_decoded_bytes_per_file": 64 * MIB,
    "max_ledger_bytes": 8 * MIB,
}
FAST_LIMITS: dict[str, Any] = {
    **PROCESS_LIMITS,
    "max_file_bytes": 4 * MIB,
    "transfer_factor": 1.5,
    "max_requests_per_file": 16,
    "max_retries": 2,
    "request_timeout_seconds": 5.0,
    "file_deadline_seconds": 60.0,
    "batch_deadline_seconds": 120.0,
    "max_durable_bytes_per_file": 8 * MIB,
}


# ------------------------------------------------------------------- fixtures


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
    """Authored loopback endpoint: a resolve redirect and a Range-capable object store."""

    files: dict[str, bytes] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    #: Per name: the X-Linked-ETag of the resolve response; None omits the header.
    linked: dict[str, str | None] = field(default_factory=dict)
    linked_sizes: dict[str, int] = field(default_factory=dict)
    #: Per name: an X-Xet-Hash on the object response.
    xet: dict[str, str] = field(default_factory=dict)
    #: Per name: body byte counts after which the connection is cut, one per request.
    drops: dict[str, list[int]] = field(default_factory=dict)
    ignore_range: set[str] = field(default_factory=set)
    commit: str = REVISION
    delay: float = 0.0
    requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def etag(self, name: str) -> str:
        """The storage validator: 64 hex like the live endpoint's, and NOT the SHA-256."""
        opaque = hashlib.sha256(b"storage-hash:" + self.files[name]).hexdigest()
        return self.etags.get(name, f'"{opaque}"')

    def linked_etag(self, name: str) -> str | None:
        """The repository's declared content digest: the real SHA-256 unless overridden."""
        default = '"' + hashlib.sha256(self.files[name]).hexdigest() + '"'
        return self.linked.get(name, default)

    def hits(self, kind: str, name: str | None = None) -> int:
        return sum(1 for k, n, _ in self.requests if k == kind and name in (None, n))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        state: Served = self.server.state  # type: ignore[attr-defined]
        kind, _, name = urllib.parse.unquote(self.path).lstrip("/").partition("/")
        with state.lock:
            state.requests.append((kind, name, self.headers.get("Range")))
        if kind == "evil":
            self.send_response(302)
            self.send_header("Location", "http://example.org/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = state.files.get(name)
        if payload is None:
            self.send_error(404)
            return
        if kind == "resolve":
            self.send_response(302)
            port = self.server.server_port  # type: ignore[attr-defined]
            location = f"http://127.0.0.1:{port}/object/{urllib.parse.quote(name)}"
            self.send_header("Location", location)
            declared = state.linked_etag(name)
            if declared is not None:
                self.send_header("X-Linked-ETag", declared)
            self.send_header("X-Linked-Size", str(state.linked_sizes.get(name, len(payload))))
            self.send_header("X-Repo-Commit", state.commit)
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
            start = 0
            requested = self.headers.get("Range")
            conditional = self.headers.get("If-Range")
            if (
                requested
                and name not in state.ignore_range
                and conditional in (None, state.etag(name))
            ):
                first, _, last = requested.removeprefix("bytes=").partition("-")
                start = int(first)
                end = int(last) if last else len(payload) - 1
                body = payload[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            else:
                body = payload
                self.send_response(200)
            self.send_header("ETag", state.etag(name))
            if name in state.xet:
                self.send_header("X-Xet-Hash", state.xet[name])
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
def served() -> Any:
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.daemon_threads = True
    service.state = Served()  # type: ignore[attr-defined]
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield service.state, f"http://127.0.0.1:{service.server_port}"  # type: ignore[attr-defined]
    finally:
        service.shutdown()
        service.server_close()


def row(kind: str, index: int) -> dict[str, Any]:
    record = historical_tests.fixture_row(kind, index)
    record["metadata"] = {"source_domain": "authored.example"}
    return record


def rows_of(count: int, malformed: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    return [
        row("malformed" if index in malformed else KINDS[index % len(KINDS)], index)
        for index in range(count)
    ]


def parquet_bytes(rows: list[dict[str, Any]], group_rows: int) -> bytes:
    """The projected columns plus one unprojected column the adapters never read."""
    table = pa.Table.from_pylist(
        [{**record, "line_start_n_end_idx": {"line_start_idx": i}} for i, record in enumerate(rows)]
    )
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=group_rows, compression="snappy")
    return sink.getvalue()


def expected_documents(name: str, rows: list[dict[str, Any]], view: str) -> bytes:
    """Independent oracle: the frozen adapter applied to the authored rows directly."""
    adapter = ADAPTERS_BY_ID["essential_web_bnormal"](view)
    lines = []
    for index, record in enumerate(rows):
        try:
            document = adapter.adapt(
                record, source_file=name, source_row=index, source_revision=REVISION
            )
        except RecordRejectedError:
            continue
        lines.append(serialize_document(document).encode("utf-8") + b"\n")
    return b"".join(lines)


def transfer_limits(**changes: Any) -> sp.TransferLimits:
    values: dict[str, Any] = {
        "max_file_bytes": 8 * MIB,
        "max_transfer_bytes": 16 * MIB,
        "max_requests": 16,
        "max_retries": 3,
        "request_timeout_seconds": 5.0,
        "deadline_seconds": 30.0,
    }
    return sp.TransferLimits(**{**values, **changes})


def fetch(base: str, tmp: Path, name: str, route: str = "resolve", **options: Any) -> Any:
    limits = options.pop("limits", transfer_limits())
    return sp.download_source(
        f"{base}/{route}/{name}",
        tmp / "x.parquet.part",
        tmp / "x.state.json",
        name=name,
        limits=limits,
        revision=options.pop("revision", REVISION),
        sleep=lambda seconds: None,
        **options,
    )


def adapt_local(path: Path, out: Path, name: str, etag: str, **options: Any) -> dict[str, Any]:
    return local.adapt_source_file(
        path,
        out,
        source_file=name,
        views=list(VIEWS),
        source_id="essential_web",
        repository=options.pop("repository", "authored"),
        revision=REVISION,
        plan_id=options.pop("plan_id", "plan_authored"),
        plan_hash=options.pop("plan_hash", "a" * 64),
        selection_hash=options.pop("selection_hash", "b" * 64),
        identity={"etag": etag, "sha256": "c" * 64, "length": path.stat().st_size},
        limits=PROCESS_LIMITS,
        **options,
    )


# ------------------------------------------------------------------ transport


def test_whole_file_is_one_stream_with_full_source_identity(served: Any, tmp_path: Path) -> None:
    state, base = served
    state.files["data/a.parquet"] = os.urandom(300_000)
    result = fetch(base, tmp_path, "data/a.parquet")
    digest = hashlib.sha256(state.files["data/a.parquet"]).hexdigest()
    assert result.path.read_bytes() == state.files["data/a.parquet"]
    assert (result.requests, result.redirects, result.retries) == (2, 1, 0)
    assert state.hits("resolve") == 1 and state.hits("object") == 1
    assert state.requests[-1][2] is None  # no Range: one sequential stream
    identity = result.identity
    assert (identity.etag, identity.length, identity.sha256) == (
        state.etag("data/a.parquet"),
        300_000,
        digest,
    )
    assert identity.expected_sha256 == digest and identity.sha256_independently_verified
    assert identity.expected_sha256_source == sp.EXPECTED_FROM_REPOSITORY
    assert (identity.linked_size, identity.repo_commit) == (300_000, REVISION)
    assert result.transferred_bytes == 300_000 and not result.cache_hit
    # A completed scratch file is rehashed and reused without any request.
    again = fetch(base, tmp_path, "data/a.parquet")
    assert again.cache_hit and again.identity == identity and state.hits("object") == 1


def test_a_64_hex_etag_is_an_opaque_validator_not_a_content_hash(
    served: Any, tmp_path: Path
) -> None:
    state, base = served
    payload = os.urandom(50_000)
    state.files["a.parquet"] = payload
    digest = hashlib.sha256(payload).hexdigest()
    storage = "f0c954afd30a9b985ec4dbcb938498ea29e842210417680bdb6e809dd847d9ce"
    state.etags["a.parquet"] = f'"{storage}"'  # 64 hex, and not the SHA-256 of the file
    state.xet["a.parquet"] = "1" * 64
    identity = fetch(base, tmp_path, "a.parquet").identity
    # Four identities, each in its own field; none substituted for another.
    assert identity.etag == f'"{storage}"' and storage != digest
    assert identity.sha256 == digest  # always the hash of the received bytes
    assert identity.expected_sha256 == digest  # the repository's declared digest
    assert identity.linked_etag == f'"{digest}"'
    assert identity.xet_hash == "1" * 64 and identity.xet_hash != identity.sha256
    record = sp.identity_record(identity, source_file="a.parquet", repository="r", revision="v")
    assert record["sha256"] == digest and record["etag"] == f'"{storage}"'
    assert record["xet_hash"] == "1" * 64 and record["sha256_independently_verified"] is True
    assert "etag_is_content_sha256" not in record
    # An ETag of any other shape is accepted as a validator just the same.
    state.etags["a.parquet"] = '"opaque-validator-7"'
    other = fetch(base, tmp_path / "opaque", "a.parquet").identity
    assert other.etag == '"opaque-validator-7"' and other.sha256 == digest
    # Even an ETag that IS the SHA-256 earns no trust as a content hash.
    state.etags["a.parquet"] = f'"{digest}"'
    state.linked["a.parquet"] = None
    alone = fetch(base, tmp_path / "alone", "a.parquet").identity
    assert alone.sha256 == digest and alone.expected_sha256 is None
    assert alone.expected_sha256_source is None and not alone.sha256_independently_verified


def test_local_sha256_must_equal_the_independent_expected_digest(
    served: Any, tmp_path: Path
) -> None:
    state, base = served
    payload = os.urandom(50_000)
    state.files["a.parquet"] = payload
    digest = hashlib.sha256(payload).hexdigest()
    # The repository declares another digest than the bytes that arrive: corruption.
    state.linked["a.parquet"] = '"' + "0" * 64 + '"'
    with pytest.raises(sp.SourceTransferError, match=r"independent expected SHA-256 \(x-linked"):
        fetch(base, tmp_path, "a.parquet")
    assert (tmp_path / "x.parquet.part").stat().st_size == 0  # nothing unverified is kept
    del state.linked["a.parquet"]
    # A reviewed digest in the plan is required the same way and takes precedence.
    ok = fetch(base, tmp_path / "plan", "a.parquet", expected_sha256=digest.upper()).identity
    assert ok.expected_sha256 == digest and ok.expected_sha256_source == sp.EXPECTED_FROM_PLAN
    state.linked["a.parquet"] = None
    with pytest.raises(sp.SourceTransferError, match=r"independent expected SHA-256 \(plan\)"):
        fetch(base, tmp_path / "bad", "a.parquet", expected_sha256="1" * 64)
    # The plan and the repository disagreeing about the same path is drift, not a retry.
    del state.linked["a.parquet"]
    before = state.hits("object")
    with pytest.raises(SourceDriftDetectedError, match="differs from the plan"):
        fetch(base, tmp_path / "both", "a.parquet", expected_sha256="1" * 64)
    assert state.hits("object") == before + 1
    # A 40-hex linked ETag (a Git blob id, not a SHA-256) is kept but never used as one.
    state.linked["a.parquet"] = '"' + "a" * 40 + '"'
    blob = fetch(base, tmp_path / "blob", "a.parquet").identity
    assert blob.linked_etag == '"' + "a" * 40 + '"' and blob.expected_sha256 is None


def test_wrong_length_path_or_corrupted_scratch_is_refused(
    served: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, base = served
    payload = os.urandom(60_000)
    state.files["a.parquet"] = payload
    state.files["b.parquet"] = payload
    state.linked_sizes["a.parquet"] = 59_999
    with pytest.raises(SourceDriftDetectedError, match="linked size"):
        fetch(base, tmp_path, "a.parquet")
    del state.linked_sizes["a.parquet"]
    done = fetch(base, tmp_path, "a.parquet")
    # Transfer state belongs to one exact path: another path cannot adopt it.
    with pytest.raises(sp.SourceTransferError, match="belongs to another source"):
        fetch(base, tmp_path, "b.parquet")
    # A completed scratch file that was corrupted afterwards is never reused.
    damaged = bytearray(done.path.read_bytes())
    damaged[100] ^= 0xFF
    done.path.write_bytes(bytes(damaged))
    hits = state.hits("object", "a.parquet")
    again = fetch(base, tmp_path, "a.parquet")
    assert not again.cache_hit and again.path.read_bytes() == payload
    assert state.hits("object", "a.parquet") == hits + 1
    # A body shorter than its declared length never completes.
    state.drops["b.parquet"] = [10_000] * 8
    with pytest.raises(sp.SourceTransferError, match="attempts exhausted"):
        fetch(base, tmp_path / "short", "b.parquet", limits=transfer_limits(max_retries=1))
    assert not json.loads((tmp_path / "short/x.state.json").read_bytes())["complete"]
    # On a verified continuation both the validator and the repository digest must hold.
    monkeypatch.setattr(sp, "READ_BYTES", 1024)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 8192)
    for change in ("etag", "linked"):
        directory = tmp_path / change
        state.etags.pop("b.parquet", None)
        state.linked.pop("b.parquet", None)
        state.drops["b.parquet"] = [30_000]
        with pytest.raises(sp.SourceTransferError):
            fetch(base, directory, "b.parquet", limits=transfer_limits(max_retries=0))
        assert json.loads((directory / "x.state.json").read_bytes())["verified_bytes"] > 0
        if change == "etag":
            state.etags["b.parquet"] = '"' + "9" * 64 + '"'
        else:
            state.linked["b.parquet"] = '"' + "8" * 64 + '"'
        with pytest.raises(SourceDriftDetectedError):
            fetch(base, directory, "b.parquet")
        assert state.requests[-1][2] is not None  # it was a Range/If-Range continuation


def test_identity_and_bound_refusals(served: Any, tmp_path: Path) -> None:
    state, base = served
    state.files["a.parquet"] = os.urandom(20_000)
    state.etags["a.parquet"] = 'W/"weak"'
    with pytest.raises(sp.SourceTransferError, match="strong ETag"):
        fetch(base, tmp_path, "a.parquet")
    del state.etags["a.parquet"]
    with pytest.raises(sp.SourceTransferError, match="outside the per-file bound"):
        fetch(
            base,
            tmp_path / "b",
            "a.parquet",
            limits=transfer_limits(max_file_bytes=10_000, max_transfer_bytes=20_000),
        )
    assert not (tmp_path / "b" / "x.parquet.part").exists()  # refused before any body byte
    with pytest.raises(HostNotAllowlistedError):
        fetch(base, tmp_path / "c", "a.parquet", route="evil")
    state.commit = "f" * 40
    with pytest.raises(SourceDriftDetectedError, match="another revision"):
        fetch(base, tmp_path / "d", "a.parquet")
    (tmp_path / "e").mkdir()
    (tmp_path / "e" / "x.parquet.part").write_bytes(b"unowned")
    with pytest.raises(sp.SourceTransferError, match="unowned partial"):
        fetch(base, tmp_path / "e", "a.parquet")
    meter = sp.TransferMeter(10_000, 100)
    state.commit = REVISION
    with pytest.raises(sp.SourceTransferError, match="batch transfer byte ceiling"):
        fetch(base, tmp_path / "f", "a.parquet", meter=meter)


def test_interrupted_stream_resumes_from_its_verified_prefix(
    served: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp, "READ_BYTES", 1024)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 8192)
    state, base = served
    payload = os.urandom(100_000)
    state.files["a.parquet"] = payload
    state.drops["a.parquet"] = [30_000]
    result = fetch(base, tmp_path, "a.parquet")
    assert result.path.read_bytes() == payload and result.retries == 1
    ranges = [r for kind, _, r in state.requests if kind == "object"]
    assert ranges[0] is None and ranges[1] is not None
    resumed_at = int(ranges[1].removeprefix("bytes=").rstrip("-"))
    assert 0 < resumed_at <= 30_000 and resumed_at % 8192 == 0
    # Re-sent bytes stay charged: more than the file, less than two files.
    assert 100_000 < result.transferred_bytes < 200_000


def test_resume_across_processes_never_trusts_unverified_bytes(
    served: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp, "READ_BYTES", 1024)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 8192)
    state, base = served
    payload = os.urandom(100_000)
    state.files["a.parquet"] = payload
    state.drops["a.parquet"] = [50_000]
    with pytest.raises(sp.SourceTransferError, match="attempts exhausted"):
        fetch(base, tmp_path, "a.parquet", limits=transfer_limits(max_retries=0))
    partial, saved = tmp_path / "x.parquet.part", tmp_path / "x.state.json"
    verified = json.loads(saved.read_bytes())["verified_bytes"]
    assert 0 < verified <= 50_000
    with partial.open("ab") as stream:
        stream.write(b"garbage past the checkpoint")
    result = fetch(base, tmp_path, "a.parquet")
    assert result.path.read_bytes() == payload and result.resumed_bytes == verified
    assert state.requests[-1][2] == f"bytes={verified}-"
    # A corrupted verified prefix is detected and the file restarts from zero.
    other = tmp_path / "other"
    other.mkdir()
    state.drops["a.parquet"] = [50_000]
    with pytest.raises(sp.SourceTransferError):
        fetch(base, other, "a.parquet", limits=transfer_limits(max_retries=0))
    damaged = bytearray((other / "x.parquet.part").read_bytes())
    damaged[0] ^= 0xFF
    (other / "x.parquet.part").write_bytes(bytes(damaged))
    again = fetch(base, other, "a.parquet")
    assert again.path.read_bytes() == payload and again.resumed_bytes == 0
    assert state.requests[-1][2] is None


def test_source_drift_on_continuation_is_refused_not_retried(
    served: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp, "READ_BYTES", 1024)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 8192)
    state, base = served
    state.files["a.parquet"] = os.urandom(100_000)
    state.drops["a.parquet"] = [40_000]
    with pytest.raises(sp.SourceTransferError):
        fetch(base, tmp_path, "a.parquet", limits=transfer_limits(max_retries=0))
    state.files["a.parquet"] = os.urandom(100_000)  # the upstream entity changed
    before = state.hits("object")
    with pytest.raises(SourceDriftDetectedError):
        fetch(base, tmp_path, "a.parquet")
    assert state.hits("object") == before + 1
    # A server that ignores Range for an unchanged entity forces a clean restart.
    other = tmp_path / "other"
    other.mkdir()
    state.drops["a.parquet"] = [40_000]
    state.ignore_range.add("a.parquet")
    result = fetch(base, other, "a.parquet")
    assert result.path.read_bytes() == state.files["a.parquet"] and result.retries == 1


def test_scratch_cap_counts_everything_under_the_root(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    (root / "leftover.bin").write_bytes(b"x" * 400)
    budget = sp.ScratchBudget(root, 1000, 0)
    assert budget.reserve("a", 500, root / "a.part")
    assert not budget.reserve("b", 200, root / "b.part")  # 400 foreign + 500 + 200 > 1000
    budget.shrink("a", 300)
    assert budget.reserve("b", 200, root / "b.part")
    with pytest.raises(sp.ScratchCapError):
        budget.shrink("a", 301)
    with pytest.raises(sp.ScratchCapError):
        budget.reserve("c", 1001, root / "c.part")
    budget.release("a")
    assert budget.reserved() == 200 and budget.peak_reserved_bytes == 500
    assert (root / "leftover.bin").exists()  # files of other owners are never deleted
    # The reserve on the scratch volume is a second, independent refusal.
    tight = sp.ScratchBudget(root, 10_000, 5000, disk_free=lambda path: 5100)
    assert not tight.reserve("a", 200, root / "a.part")
    assert tight.reserve("a", 100, root / "a.part")


def test_pipeline_waits_for_scratch_and_downloads_files_concurrently(
    served: Any, tmp_path: Path
) -> None:
    state, base = served
    state.delay = 0.3
    names = [f"f{index}.parquet" for index in range(4)]
    for name in names:
        state.files[name] = os.urandom(10_000)

    def units(directory: Path) -> list[local.Unit]:
        directory.mkdir(parents=True)
        return [
            local.Unit(
                key=f"k{index}",
                source_file=name,
                url=f"{base}/resolve/{name}",
                partial=directory / f"k{index}.part",
                state=directory / f"k{index}.json",
                job=None,
            )
            for index, name in enumerate(names)
        ]

    def run(directory: Path, workers: int, cap: int) -> tuple[dict[str, Any], int]:
        state.max_active = 0
        done: list[str] = []

        def finished(unit: local.Unit, transfer: Any, result: Any) -> None:
            assert unit.partial.read_bytes() == state.files[unit.source_file]
            unit.partial.unlink()  # released: the next file may now reserve scratch
            done.append(unit.key)

        stats = local.run_pipeline(
            units(directory),
            limits=transfer_limits(max_file_bytes=20_000, max_transfer_bytes=40_000),
            revision=REVISION,
            download_workers=workers,
            process_workers=0,
            scratch=sp.ScratchBudget(directory, cap, 0),
            meter=sp.TransferMeter(MIB, 100),
            deadline_seconds=60,
            identity_for=lambda unit, transfer: {},
            on_done=finished,
        )
        assert sorted(done) == ["k0", "k1", "k2", "k3"]
        return stats, state.max_active

    stats, active = run(tmp_path / "one", 1, MIB)
    assert active == 1 and stats["peak_downloads"] == 1
    stats, active = run(tmp_path / "four", 4, MIB)
    assert active == 4 and stats["peak_downloads"] == 4
    # A cap with room for one reservation serializes the same four workers.
    stats, active = run(tmp_path / "capped", 4, 25_000)
    assert active == 1 and stats["peak_scratch_reserved_bytes"] <= 25_000
    with pytest.raises(sp.ScratchCapError):
        run(tmp_path / "none", 4, 15_000)


def test_durable_raw_is_immutable_and_bound_to_its_identity(tmp_path: Path) -> None:
    source = tmp_path / "scratch.part"
    source.write_bytes(b"PAR1" + os.urandom(5000) + b"PAR1")
    sha256, size = sp.file_sha256(source)
    identity = sp.SourceIdentity('"opaque"', size, sha256, sha256, sp.EXPECTED_FROM_PLAN)
    record = sp.identity_record(
        identity, source_file="data/a.parquet", repository="r", revision="v"
    )
    destination = tmp_path / "durable/data/a.parquet"
    assert sp.promote_source(source, destination, record) is True
    assert destination.read_bytes() == source.read_bytes()
    assert sp.promote_source(source, destination, record) is False  # idempotent, no rewrite
    assert sp.load_durable_source(destination) == json.loads(json.dumps(record))
    assert not list(destination.parent.glob("*.tmp"))
    sp.check_parquet_magic(destination)
    other = tmp_path / "other.part"
    other.write_bytes(os.urandom(size))
    other_sha, _ = sp.file_sha256(other)
    with pytest.raises(sp.SourceTransferError, match="refusing overwrite"):
        sp.promote_source(other, destination, {**record, "sha256": other_sha})
    with pytest.raises(sp.SourceTransferError, match="differs from its source"):
        sp.promote_source(other, tmp_path / "durable/b.parquet", record)
    assert not (tmp_path / "durable/b.parquet").exists()
    with pytest.raises(sp.SourceTransferError, match="not a complete Parquet"):
        sp.check_parquet_magic(other)
    destination.write_bytes(other.read_bytes())
    with pytest.raises(sp.SourceTransferError, match="no longer matches"):
        sp.load_durable_source(destination)


# ------------------------------------------------------------- local records


def test_located_record_is_byte_identical_to_the_certified_serialization() -> None:
    locator = {"row_index": 3, "etag": '"e"', "source_file": "f", "selection_hash": "s" * 64}
    records: list[dict[str, Any]] = [
        row("science", 1),
        {},
        {"Zeta": 1, "alpha": [1, 2.5, None], "nested": {"b": True, "a": -0.0}},
        {"A": "before the locator field", "text": "naïve ☃ \u2028 text"},
        {"text": "x", "id": 2**62, "score": 1e-300},
    ]
    for record in records:
        raw = encode_record(record)
        assert sp.located_record(record, locator) == (
            bytes(raw),
            selected_record(record, locator, raw),
        )
    with pytest.raises(ValueError, match="reserved"):
        sp.located_record({"_xlm_acquisition": {}}, locator)


def certified(tmp: Path, base: str, name: str, count: int) -> tuple[AcquisitionPlan, Path]:
    """The certified window reader over HTTP ranges, then nothing else."""
    plan = AcquisitionPlan(
        plan_id="plan_cert",
        source_id="essential_web",
        view_id="essential_science",
        provider="https",
        repository=f"{base}/object",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[name],
        row_ranges={name: (0, count)},
        projected_fields=list(columns_for("essential_web_bnormal", "essential_science")),
        parquet_window=ParquetWindowDecode(
            policy_version=2,
            stream_buffer_bytes=65536,
            max_window_scan_rows=count,
            batch_rows=min(256, count),
        ),
        output_artifact_id="raw_cert",
    )
    plan = plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="authored offline test",
                authorized_at="fixture",
                is_pilot_approved=True,
            )
        }
    )
    state = BoundedFetcher(plan, tmp / "s", tmp / "o").run()
    assert state.status == "COMPLETED"
    save_acquisition_plan(plan, tmp / "plan.json")
    return plan, tmp / "o" / "selected_records.jsonl"


def test_local_processing_reproduces_the_certified_path_byte_for_byte(
    served: Any, tmp_path: Path
) -> None:
    state, base = served
    name = "data/crawl=AUTHORED/train-00000.parquet"
    rows = rows_of(60, malformed=frozenset({33}))
    state.files[name] = parquet_bytes(rows, 60)
    plan, raw = certified(tmp_path / "c", base, name, 60)
    ranged = state.hits("object")
    assert ranged > 4  # the certified reader needs many range requests
    whole = fetch(base, tmp_path, name, route="object")
    assert state.hits("object") == ranged + 1
    result = adapt_local(
        whole.path,
        tmp_path / "local",
        name,
        whole.identity.etag,
        repository=plan.repository,
        plan_id=plan.plan_id,
        plan_hash=plan.compute_behavioral_hash(),
        selection_hash=plan.compute_selection_hash(),
    )
    # 1. The selected-record stream that is never written equals the certified file.
    assert result["selected_records_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert result["selected_records_bytes"] == raw.stat().st_size and result["rows"] == 60
    for view in VIEWS:
        out = tmp_path / "cli" / view
        done = CliRunner().invoke(
            app,
            [
                "adapt",
                "--plan",
                str(tmp_path / "c/plan.json"),
                "--adapter",
                "essential_web_bnormal",
                "--adapter-config",
                view,
                "--input",
                str(raw),
                "--output-dir",
                str(out),
                "--on-reject",
                "record",
            ],
        )
        assert done.exit_code == 0, done.output
        mine = tmp_path / "local" / view
        # 2. Canonical documents and the rejection ledger are byte-identical.
        documents = (out / "documents.jsonl").read_bytes()
        assert (mine / "documents.jsonl").read_bytes() == documents
        assert documents == expected_documents(name, rows, view)
        summary = json.loads((mine / "adaptation_summary.json").read_bytes())
        ledger = local.read_ledger(
            mine / local.LEDGER_FILENAME, summary["rejections"]["uncompressed_bytes"]
        )
        assert ledger == (out / "adaptation_rejections.jsonl").read_bytes()
        certified_summary = json.loads((out / "adaptation_summary.json").read_bytes())
        for key in (
            "accepted_records",
            "rejected_records",
            "rejection_counts_by_code",
            "total_input_records",
            "plan_hash",
            "adapter_id",
            "on_reject",
        ):
            assert summary[key] == certified_summary[key]
        assert summary["documents"]["sha256"] == certified_summary["documents"]["sha256"]
        assert summary["rejections"]["sha256"] == certified_summary["rejections"]["sha256"]
        assert summary["rejections"]["count"] == certified_summary["rejections"]["count"]
        assert summary["adaptation_summary_version"] == 2
        assert summary["input"]["kind"] == "verified_source_parquet"
    assert result["views"][SCIENCE]["documents"] == 12
    bulk.check_conservation(60, result["views"])
    # 3. Row-group layout and the unprojected column never reach a canonical byte.
    regrouped = tmp_path / "regrouped.parquet"
    regrouped.write_bytes(parquet_bytes(rows, 7))
    again = adapt_local(regrouped, tmp_path / "local7", name, whole.identity.etag)
    assert again["row_groups"] == 9
    for view in VIEWS:
        assert again["views"][view]["documents_sha256"] == result["views"][view]["documents_sha256"]
        assert (
            again["views"][view]["rejections_sha256"] == result["views"][view]["rejections_sha256"]
        )
    # 4. A row range is the same rows, not a different selection.
    part = adapt_local(regrouped, tmp_path / "part", name, whole.identity.etag, row_range=(10, 25))
    assert part["rows"] == 15
    assert (tmp_path / "part" / SCIENCE / "documents.jsonl").read_bytes() == b"".join(
        line
        for line in expected_documents(name, rows, SCIENCE).splitlines(keepends=True)
        if 10 <= json.loads(line)["source_row"] < 25
    )


def test_compressed_ledger_keeps_the_exact_lineage(tmp_path: Path) -> None:
    name = "data/a.parquet"
    path = tmp_path / "a.parquet"
    path.write_bytes(parquet_bytes(rows_of(200), 50))
    first = adapt_local(path, tmp_path / "one", name, '"e"')
    second = adapt_local(path, tmp_path / "two", name, '"e"')
    for view in VIEWS:
        entry = first["views"][view]
        stored = tmp_path / "one" / view / local.LEDGER_FILENAME
        assert stored.read_bytes() == (tmp_path / "two" / view / local.LEDGER_FILENAME).read_bytes()
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == entry["rejections_file_sha256"]
        assert entry == second["views"][view]  # deterministic
        ledger = local.read_ledger(stored, entry["rejections_uncompressed_bytes"])
        assert hashlib.sha256(ledger).hexdigest() == entry["rejections_sha256"]
        assert entry["rejections_file_bytes"] < entry["rejections_uncompressed_bytes"] / 4
        lines = [json.loads(line) for line in ledger.splitlines()]
        assert len(lines) == 200 - entry["documents"]
        for line in lines:
            assert set(line) == {
                "input_line",
                "source_id",
                "source_revision",
                "source_file",
                "source_row",
                "adapter_id",
                "rejection_code",
                "rejection_category",
                "reason",
                "original_record_sha256",
            }
            assert line["source_file"] == name and line["input_line"] == line["source_row"] + 1
            assert len(line["original_record_sha256"]) == 64 and "text" not in line
        with pytest.raises(local.LocalAdaptError, match="outside its bound"):
            local.read_ledger(stored, entry["rejections_uncompressed_bytes"], max_bytes=10)


def test_local_processing_fails_closed(tmp_path: Path) -> None:
    name = "data/a.parquet"
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(parquet_bytes(rows_of(40, malformed=frozenset({1, 2, 3})), 40))
    with pytest.raises(MalformedLimitError):
        adapt_local(bad, tmp_path / "bad", name, '"e"')
    good = tmp_path / "good.parquet"
    good.write_bytes(parquet_bytes(rows_of(40), 40))
    for change, message in (
        ({"max_rows_per_file": 39}, "row count exceeds"),
        ({"max_ledger_bytes": 100}, "rejection ledger exceeds"),
    ):
        with pytest.raises(local.LocalAdaptError, match=message):
            local.adapt_source_file(
                good,
                tmp_path / message[:5],
                source_file=name,
                views=list(VIEWS),
                source_id="essential_web",
                repository="r",
                revision=REVISION,
                plan_id="p",
                plan_hash="a" * 64,
                selection_hash="b" * 64,
                identity={"etag": '"e"', "sha256": "c" * 64, "length": 1},
                limits={**PROCESS_LIMITS, **change},
            )
    with pytest.raises(Exception, match="decoded byte bound|record byte bound"):
        local.adapt_source_file(
            good,
            tmp_path / "rec",
            source_file=name,
            views=list(VIEWS),
            source_id="essential_web",
            repository="r",
            revision=REVISION,
            plan_id="p",
            plan_hash="a" * 64,
            selection_hash="b" * 64,
            identity={"etag": '"e"', "sha256": "c" * 64, "length": 1},
            limits={**PROCESS_LIMITS, "max_record_bytes": 64},
        )
    truncated = tmp_path / "cut.parquet"
    truncated.write_bytes(good.read_bytes()[:-9])
    with pytest.raises(sp.SourceTransferError, match="not a complete Parquet"):
        adapt_local(truncated, tmp_path / "cut", name, '"e"')
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "x").write_bytes(b"x")
    with pytest.raises(local.LocalAdaptError, match="fresh private"):
        adapt_local(good, tmp_path / "used", name, '"e"')


# ------------------------------------------------------------------- campaign


@dataclass
class World:
    tool: Any
    historical: Any
    path: Path
    root: Path
    scratch: Path
    config: dict[str, Any]
    state: Served
    rows: dict[str, list[dict[str, Any]]]

    def run(self, *argv: str) -> int:
        return int(
            self.tool.main(
                [
                    "--campaign",
                    str(self.path),
                    "--data-root",
                    str(self.root),
                    "--scratch-root",
                    str(self.scratch),
                    *argv,
                ]
            )
        )

    def campaign(self) -> Any:
        return self.tool.load_campaign(self.path, self.root, self.scratch)

    def batch(self, index: int) -> dict[str, Any]:
        record: dict[str, Any] = json.loads(
            (self.root / f"plans/ew-fast/b{index:04d}/batch.json").read_bytes()
        )
        return record

    def prepare(self, index: int) -> dict[str, Any]:
        assert self.run("plan", "--batch", str(index)) == 0
        return self.batch(index)

    def authorize(self, index: int) -> None:
        digest = self.prepare(index)["authorization_digest"]
        assert (
            self.run("authorize", "--batch", str(index), "--digest", digest, "--operator", "t") == 0
        )

    def pass_benchmark(self) -> None:
        self.tool.write_json(
            self.root / "plans/ew-fast/benchmark/benchmark.json",
            {
                "verdict": "PASS",
                "plan_digest": self.config["benchmark"]["plan_digest"],
                "transport_code_sha256": self.config["transport_code_sha256"],
            },
        )

    def receipts(self) -> dict[str, dict[str, Any]]:
        found = sorted((self.root / "canonical/ew-fast").glob("b*/f*/receipt.json"))
        return {path.parent.name: json.loads(path.read_bytes()) for path in found}

    def scratch_files(self) -> list[Path]:
        return [path for path in self.scratch.rglob("*") if path.is_file()]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: Any) -> World:
    state, base = served
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    old_tool = importlib.import_module("essential_web_bulk")
    tool = importlib.import_module("essential_web_fast")
    required = {
        SCIENCE: 2 * historical_tests.science_bytes_per_batch() - 1,
        PRACTICAL: 1,
        PROSE: 1,
    }
    historical = historical_tests.make_world(tmp_path, monkeypatch, old_tool, required)
    names = [entry["file"] for entry in historical.campaign().inventory["files"]]
    rows = {name: rows_of(10) for name in names}
    for name in names:
        state.files[name] = parquet_bytes(rows[name], 5)
    code = fast.transport_code_identity(REPO)
    parity_name = names[-1]
    parity_path = tmp_path / "parity.parquet"
    parity_path.write_bytes(state.files[parity_name])
    sealed = adapt_local(
        parity_path,
        tmp_path / "parity-out",
        parity_name,
        state.etag(parity_name),
        repository=historical.config["binding"]["repository"],
        plan_id="plan_cal",
        plan_hash="d" * 64,
        selection_hash="e" * 64,
        row_range=(0, 5),
    )
    parity = {
        "file": parity_name,
        "etag": state.etag(parity_name),
        "length": len(state.files[parity_name]),
        "row_range": [0, 5],
        "plan_id": "plan_cal",
        "plan_hash": "d" * 64,
        "selection_hash": "e" * 64,
        "raw_sha256": sealed["selected_records_sha256"],
        "raw_bytes": sealed["selected_records_bytes"],
        "views": {
            view: {
                "documents_sha256": sealed["views"][view]["documents_sha256"],
                "rejections_sha256": sealed["views"][view]["rejections_sha256"],
            }
            for view in VIEWS
        },
    }
    bench = fast.benchmark_plan(
        {
            "binding": historical.config["binding"],
            "transport_code_sha256": code,
            "limits": FAST_LIMITS,
        },
        names[:2],
        (1, 1),
        parity,
    )
    repo = historical.repo
    (repo / "benchmark-plan.json").write_text(json.dumps(bench), encoding="utf-8")
    config = fast.campaign_config(
        historical=historical.config,
        historical_path="campaign.json",
        transport_code_sha256=code,
        batch_files=2,
        limits=FAST_LIMITS,
        scratch={
            "relative": "ew-fast",
            "cap_bytes": 64 * MIB,
            "min_free_bytes": 0,
            "max_in_flight_files": 4,
        },
        concurrency={
            "download_workers": 2,
            "download_workers_max": 16,
            "process_workers": 0,
            "process_workers_max": 16,
            "process_threads": 1,
        },
        min_free_bytes=MIB,
        footprint_cap_bytes=1024 * MIB,
        benchmark_digest=bench["digest"],
    )
    path = repo / "fast-campaign.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(tool, "REPO", repo)
    monkeypatch.setattr(tool, "check_admission", lambda plan: None)
    monkeypatch.setattr(tool, "source_url", lambda plan, name: f"{base}/resolve/{name}")
    return World(
        tool=tool,
        historical=historical,
        path=path,
        root=historical.root,
        scratch=tmp_path / "scratch",
        config=config,
        state=state,
        rows=rows,
    )


def test_fast_campaign_keeps_the_historical_science_exactly(world: World) -> None:
    campaign = world.campaign()
    old = world.historical.campaign()
    for batch in range(3):
        assert campaign.members(batch) == old.members(batch)
    for key in (
        "binding",
        "adapter_code_sha256",
        "inventory",
        "views",
        "adapt",
        "ceiling",
        "quotas",
        "stop",
        "calibration_seal_digest",
    ):
        assert world.config[key] == world.historical.config[key]
    assert world.config["c05"]["status"] == "NOT RUN"
    assert world.config["raw_contract"]["representation"] == "verified_source_parquet"

    def resealed(change: Any) -> dict[str, Any]:
        altered: dict[str, Any] = json.loads(json.dumps(world.config))
        change(altered)
        altered["digest"] = canonical.digest({k: v for k, v in altered.items() if k != "digest"})
        return altered

    def stop(config: dict[str, Any]) -> None:
        config["stop"]["targets"][SCIENCE]["required_canonical_bytes"] -= 1

    def quota(config: dict[str, Any]) -> None:
        config["quotas"]["changed"] = True

    def selector_binding(config: dict[str, Any]) -> None:
        config["binding"]["selector"] = {"condition": "other"}

    def code(config: dict[str, Any]) -> None:
        config["transport_code_sha256"][fast.TRANSPORT_CODE_FILES[0]] = "0" * 64

    for change, message in (
        (stop, "scientific fields"),
        (quota, "scientific fields"),
        (selector_binding, "scientific fields"),
        (code, "code changed"),
    ):
        world.path.write_text(json.dumps(resealed(change)), encoding="utf-8")
        with pytest.raises(bulk.BulkError, match=message):
            world.campaign()
    tampered = json.loads(json.dumps(world.config))
    tampered["batch"]["files"] = 4
    world.path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(bulk.BulkError, match="altered"):
        world.campaign()
    world.path.write_text(json.dumps(world.config), encoding="utf-8")
    with pytest.raises(bulk.BulkError, match="disjoint"):
        world.tool.load_campaign(world.path, world.root, world.root / "inside")


def test_prepare_is_offline_and_binds_membership_and_ceilings(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Prepare attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    started = time.monotonic()
    record = world.prepare(0)
    assert time.monotonic() - started < 30
    assert not world.state.requests
    campaign = world.campaign()
    assert record["files"] == campaign.members(0)
    assert record["membership_digest"] == canonical.digest(record["files"])
    plan = load_acquisition_plan(world.root / "plans/ew-fast/b0000/batch.dry.plan.json")
    assert plan.mode == AcquisitionMode.WHOLE_FILE and plan.revision == REVISION
    assert plan.selected_files == record["files"] and plan.row_ranges is None
    assert plan_requires_production_admission(plan) and plan.authorization is None
    limits = record["limits"]
    assert limits["max_transferred_bytes"] == 2 * 4 * MIB * 1.5
    assert limits["max_output_disk_bytes"] == 2 * 8 * MIB
    assert limits["max_temp_disk_bytes"] == 64 * MIB and limits["max_requests"] == 32
    assert record["authorization_digest"] == fast.authorization_digest(
        world.config["digest"], 0, record["membership_digest"], plan.plan_hash, limits
    )
    assert world.prepare(0) == record  # idempotent
    # Without the benchmark, or without authorization, nothing can run.
    assert world.run("run", "--batch", "0") == 1
    world.pass_benchmark()
    assert world.run("run", "--batch", "0") == 1
    assert world.run("authorize", "--batch", "0", "--digest", "0" * 64, "--operator", "t") == 1
    assert not world.state.requests and not world.receipts()


def test_campaign_runs_whole_files_to_a_deterministic_stop(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    assert world.run("run", "--batch", "0", "--workers", "1") == 0
    campaign = world.campaign()
    files = campaign.members(0)
    receipts = world.receipts()
    assert sorted(receipts) == ["f00000", "f00001"]
    assert world.state.hits("resolve") == 2 and world.state.hits("object") == 2  # 2 requests/file
    assert not world.scratch_files()  # scratch is released after publication
    for position, name in enumerate(files):
        receipt = receipts[f"f{position:05d}"]
        raw = world.root / "acq-raw/ew-fast/source" / name
        digest = hashlib.sha256(world.state.files[name]).hexdigest()
        # The single raw representation is the unmodified upstream file.
        assert raw.read_bytes() == world.state.files[name]
        assert receipt["source"]["sha256"] == digest == receipt["raw"]["sha256"]
        assert receipt["source"]["etag"] == world.state.etag(name) != f'"{digest}"'
        assert receipt["source"]["expected_sha256"] == digest
        assert receipt["source"]["expected_sha256_source"] == "x-linked-etag"
        assert receipt["source"]["sha256_independently_verified"] is True
        assert receipt["source"]["revision"] == REVISION
        assert receipt["raw"]["representation"] == "verified_source_parquet"
        assert receipt["selected_records"]["stored"] is False
        assert receipt["rows"] == 10 and receipt["row_groups"] == 2
        assert receipt["transfer"]["requests"] == 2 and receipt["inventory_rank"] == position
        unit = world.root / "canonical/ew-fast/b0000" / f"f{position:05d}"
        assert not list(unit.rglob("selected_records.jsonl"))
        for view in VIEWS:
            documents = (unit / view / "documents.jsonl").read_bytes()
            assert documents == expected_documents(name, world.rows[name], view)
            assert (
                receipt["views"][view]["documents_sha256"] == hashlib.sha256(documents).hexdigest()
            )
        assert receipt["views"][SCIENCE]["documents"] == 2
    assert not (world.root / "canonical/ew-fast/.staging/b0000").exists()
    status = json.loads((world.root / "plans/ew-fast/campaign-status.json").read_bytes())[
        "decision"
    ]
    assert not status["target_reached"] and status["complete_batches"] == 1
    assert status["training_permitted"] is False
    # A completed batch is never redone.
    before = len(world.state.requests)
    assert world.run("run", "--batch", "0") == 4
    assert len(world.state.requests) == before
    # Batch 1 with two streams and real worker processes reaches the target.
    world.authorize(1)
    assert world.run("run", "--batch", "1", "--workers", "2", "--process-workers", "2") == 3
    receipts = world.receipts()
    assert sorted(receipts) == ["f00000", "f00001", "f00002", "f00003"]
    for position, name in enumerate(world.campaign().members(1), start=2):
        for view in VIEWS:
            assert receipts[f"f{position:05d}"]["views"][view]["documents_sha256"] == (
                hashlib.sha256(expected_documents(name, world.rows[name], view)).hexdigest()
            )
    cumulative = json.loads((world.root / "plans/ew-fast/cumulative.json").read_bytes())
    assert cumulative["counted"]["rows"] == 40 and cumulative["counted"]["files"] == 4
    assert cumulative["counted"]["views"][SCIENCE]["canonical_bytes"] == (
        2 * historical_tests.science_bytes_per_batch()
    )
    performance = json.loads((world.root / "plans/ew-fast/b0001/performance-00.json").read_bytes())
    assert performance["transfer"]["requests_per_file"] == 2
    assert performance["units_sealed_this_run"] == 2 and performance["process_workers"] == 2
    # After the stop the next batch needs an explicit top-up reason.
    world.prepare(2)
    assert world.run("gate", "--batch", "2") == 3
    assert world.run("gate", "--batch", "2", "--top-up-reason", "exact count deficient") == 0
    assert world.run("status", "--batch", "1") == 3


def test_interrupted_batch_resumes_without_redoing_sealed_units(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    first, second = world.campaign().members(0)
    world.state.drops[second] = [1000, 1000, 1000]  # every attempt of the second file fails
    assert world.run("run", "--batch", "0", "--workers", "1") == 1
    assert sorted(world.receipts()) == ["f00000"]
    assert not (world.root / "acq-raw/ew-fast/source" / second).exists()
    assert sorted(path.name for path in world.scratch_files()) == [
        "f00001.parquet.part",
        "f00001.state.json",
    ]
    sealed_hits = world.state.hits("object", first)
    assert world.run("run", "--batch", "0", "--workers", "1") == 0
    assert world.state.hits("object", first) == sealed_hits  # not transferred again
    assert sorted(world.receipts()) == ["f00000", "f00001"] and not world.scratch_files()
    # The charge of the failed attempts survives in the receipt of the unit.
    assert world.receipts()["f00001"]["transfer"]["retries"] >= 2


def test_retained_source_is_reused_after_a_crash_before_sealing(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    first, second = world.campaign().members(0)
    # As if an earlier run had died right after the durable copy of the first file.
    scratch = world.scratch / "seed.part"
    scratch.parent.mkdir(parents=True)
    scratch.write_bytes(world.state.files[first])
    sha256, size = sp.file_sha256(scratch)
    durable = world.root / "acq-raw/ew-fast/source" / first
    sp.promote_source(
        scratch,
        durable,
        sp.identity_record(
            sp.SourceIdentity('"opaque"', size, sha256),
            source_file=first,
            repository=world.config["binding"]["repository"],
            revision=REVISION,
        ),
    )
    scratch.unlink()
    assert world.run("run", "--batch", "0") == 0
    assert world.state.hits("object", first) == 0 and world.state.hits("object", second) == 1
    receipt = world.receipts()["f00000"]
    assert receipt["transfer"]["transferred_bytes"] == 0 and "accounting" in receipt["transfer"]
    assert receipt["views"][SCIENCE]["documents"] == 2
    # A retained source that no longer matches its identity stops the batch.
    world.authorize(1)
    third = world.campaign().members(1)[0]
    other = world.root / "acq-raw/ew-fast/source" / third
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_bytes(b"PAR1 tampered PAR1")
    sp.identity_path(other).write_text(
        json.dumps({"kind": sp.IDENTITY_KIND, "version": 1, "sha256": "0" * 64, "length": 18}),
        encoding="utf-8",
    )
    assert world.run("run", "--batch", "1") == 1
    assert "f00002" not in world.receipts()


def test_malformed_and_drifted_files_are_never_sealed(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    first, second = world.campaign().members(0)
    world.state.files[first] = parquet_bytes(rows_of(10, malformed=frozenset({0, 1, 2})), 5)
    assert world.run("run", "--batch", "0", "--workers", "1") == 1
    assert "f00000" not in world.receipts()
    # Its source is retained as evidence, its canonical unit was never published.
    assert (world.root / "acq-raw/ew-fast/source" / first).exists()
    assert not (world.root / "canonical/ew-fast/b0000/f00000").exists()
    assert world.run("gate", "--batch", "1") == 1  # a later batch cannot start


def test_accounting_refuses_forged_duplicate_and_out_of_order_units(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    assert world.run("run", "--batch", "0") == 0
    receipt_path = world.root / "canonical/ew-fast/b0000/f00000/receipt.json"
    original = receipt_path.read_bytes()
    forged = json.loads(original)
    forged["views"][SCIENCE]["canonical_bytes"] += 10_000
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")
    assert world.run("account") == 1
    receipt_path.write_bytes(original)
    assert world.run("account") == 0
    good = json.loads(original)
    batches = {0: {"files": world.campaign().members(0), "plan_hash": good["plan"]["plan_hash"]}}
    receipts = list(world.receipts().values())
    assert fast.cumulative(batches, receipts)["complete_batches"] == 1
    with pytest.raises(bulk.BulkError, match="sealed twice"):
        fast.cumulative(batches, [*receipts, receipts[0]])
    with pytest.raises(bulk.BulkError, match="planned identity"):
        fast.cumulative({0: {**batches[0], "plan_hash": "0" * 64}}, receipts)
    with pytest.raises(bulk.BulkError, match="no planned batch"):
        fast.cumulative({0: {"files": ["other"], "plan_hash": "x"}}, receipts)
    later = {1: batches[0]}
    with pytest.raises(bulk.BulkError, match="contiguous|no planned batch"):
        fast.cumulative(later, receipts)
    result = json.loads(json.dumps(good))
    result["views"][SCIENCE]["documents"] += 1
    with pytest.raises(bulk.BulkError, match="conserve|reconcile"):
        bulk.check_conservation(10, result["views"])


def test_gate_enforces_benchmark_order_ceiling_disk_and_campaign_separation(
    world: World,
) -> None:
    world.prepare(0)
    assert world.run("gate", "--batch", "0") == 1  # benchmark has not passed
    world.pass_benchmark()
    assert world.run("gate", "--batch", "0") == 0
    assert world.run("gate", "--batch", "1") == 1  # batch 0 is not complete
    assert world.run("gate", "--batch", "3") == 1  # beyond the ceiling
    assert world.run("gate", "--batch", "0", "--free-bytes", str(16 * MIB)) == 1
    report = world.root / "plans/ew-fast/benchmark/benchmark.json"
    failed = json.loads(report.read_bytes())
    report.write_text(json.dumps({**failed, "verdict": "FAIL"}), encoding="utf-8")
    assert world.run("gate", "--batch", "0") == 1
    report.write_text(json.dumps({**failed, "plan_digest": "0" * 64}), encoding="utf-8")
    assert world.run("gate", "--batch", "0") == 1
    world.pass_benchmark()
    ledger = world.root / "plans/ew-bulk/ledger"
    ledger.mkdir(parents=True)
    (ledger / "b0000.s00.json").write_text("{}", encoding="utf-8")
    assert world.run("gate", "--batch", "0") == 1  # never mixed with the historical campaign


def test_benchmark_measures_transport_and_parity_and_retains_nothing(world: World) -> None:
    plan = world.historical.repo / "benchmark-plan.json"
    digest = world.config["benchmark"]["plan_digest"]
    assert world.run("benchmark", "--plan", str(plan), "--authorize", "0" * 64) == 1
    assert not world.state.requests
    assert world.run("benchmark", "--plan", str(plan), "--authorize", digest) == 0
    report = json.loads((world.root / "plans/ew-fast/benchmark/benchmark.json").read_bytes())
    assert report["verdict"] == "PASS" and report["counts_as_campaign_progress"] is False
    phases = {(p["phase"], p.get("download_workers")): p for p in report["phases"]}
    for workers in (1,):
        transfer = phases[("download", workers)]["transfer"]
        assert transfer["requests_per_file"] == 2 and transfer["megabytes_per_second"] > 0
        assert transfer["sha256"] == {"independently_verified": 1, "local_only": 0}
    processing = phases[("local_processing", None)]
    assert processing["rows"] == 20 and processing["documents"][SCIENCE] == 4
    assert all(phases[("parity", None)]["checks"].values())
    assert report["total_requests"] == 6 and len(report["files"]) == 3
    # Nothing is retained and nothing counts as campaign progress.
    assert not world.scratch_files() and report["scratch_removed"]
    assert not world.receipts()
    assert not (world.root / "acq-raw").exists() and not (world.root / "canonical").exists()
    assert world.tool.benchmark_passed(world.campaign())
    # A plan other than the frozen one is refused before any request.
    before = len(world.state.requests)
    bench = json.loads(plan.read_bytes())
    bench["tiers"][0]["download_workers"] = 16
    bench["digest"] = canonical.digest({k: v for k, v in bench.items() if k != "digest"})
    other = plan.with_name("other-plan.json")
    other.write_text(json.dumps(bench), encoding="utf-8")
    assert world.run("benchmark", "--plan", str(other), "--authorize", bench["digest"]) == 1
    assert len(world.state.requests) == before
    # A parity difference fails the benchmark and closes the campaign gate again.
    parity = json.loads(plan.read_bytes())["parity"]["file"]
    world.state.files[parity] = parquet_bytes(rows_of(10, malformed=frozenset({9})), 5)
    assert world.run("benchmark", "--plan", str(plan), "--authorize", digest) == 1
    failed = json.loads((world.root / "plans/ew-fast/benchmark/benchmark.json").read_bytes())
    assert failed["verdict"] == "FAIL" and any("parity" in r for r in failed["reasons"])
    assert not world.tool.benchmark_passed(world.campaign()) and not world.scratch_files()
    world.prepare(0)
    assert world.run("gate", "--batch", "0") == 1


# --------------------------------------------------------------------- models


def physical_model() -> dict[str, Any]:
    footers, units, totals = historical_tests.physical_inputs()
    return bulk.physical_cost(footers, units, totals)


def test_transport_comparison_prefers_whole_files_below_the_threshold() -> None:
    physical = physical_model()
    local_model = {"rows_per_core_second": 2000.0, "basis": "authored"}
    comparison = fast.transport_comparison(physical, local_model, 9000.0)
    projected = sum(int(f["full_file_transfer_bytes"]) for f in physical["files"])
    assert comparison["whole_over_projected"] == 100_000 / projected
    assert comparison["whole_file_stream"]["http_requests_per_file"] == 2
    assert comparison["projected_range_reader"]["http_requests"] == sum(
        int(f["requests"]) for f in physical["files"]
    )
    assert (
        comparison["request_reduction_factor"]
        == comparison["projected_range_reader"]["http_requests"] / 4
    )
    expected = "whole_file_stream" if 100_000 / projected <= 1.35 else "projected_range_reader"
    assert comparison["chosen_transport"] == expected
    assert comparison["threshold_kind"].startswith("operational heuristic")
    for entry in physical["files"]:
        entry["remote_length"] = 10 * int(entry["full_file_transfer_bytes"])
    assert fast.transport_comparison(physical, local_model, 9000.0)["chosen_transport"] == (
        "projected_range_reader"
    )


def test_storage_model_retains_one_raw_representation() -> None:
    footers, units, totals = historical_tests.physical_inputs()
    physical = bulk.physical_cost(footers, units, totals)
    ledger = {"compressed_bytes": 1600, "uncompressed_bytes": 16_000, "ratio": 10.0}
    model = fast.storage_model(
        totals,
        physical,
        ledger,
        {"target": 1000},
        bookkeeping_bytes_per_file=100,
        scratch_cap_bytes=10**6,
        max_in_flight_files=3,
        max_file_bytes=60_000,
    )
    case = model["scenarios"]["target"]
    source_per_row = 100_000 / 50
    assert case["current"]["raw_selected_records"] == 1000 * 80_000 // 8
    assert case["fast"]["retained_source_parquet"] == 1000 * source_per_row
    assert case["fast"]["compressed_rejection_ledgers"] == 1000 * 1600 // 8
    assert case["fast"]["canonical_documents"] == case["current"]["canonical_documents"]
    files = case["estimated_files"]
    assert case["fast"]["manifests_and_receipts"] == files * 100
    steady = case["fast"]["durable_steady_state_bytes"]
    assert steady == sum(
        case["fast"][key]
        for key in (
            "retained_source_parquet",
            "compressed_rejection_ledgers",
            "canonical_documents",
            "manifests_and_receipts",
        )
    )
    assert case["fast"]["durable_peak_bytes"] == steady
    assert case["fast"]["scratch_peak_bytes_bound"] == 3 * 60_000
    assert "selected_records" not in json.dumps(model["bytes_per_input_row"]["fast"])


def test_throughput_and_batch_models_are_parametric_not_promises() -> None:
    footers, units, totals = historical_tests.physical_inputs()
    physical = bulk.physical_cost(footers, units, totals)
    local_model = {
        "rows_per_core_second": 5.0,
        "rows_per_second_by_process_workers": {"1": 5.0, "8": 40.0},
        "basis": "authored",
    }
    model = fast.throughput_model(physical, local_model, 32, 8)
    old = model["historical_range_reader"]
    assert abs(sum(old["components"].values()) - old["seconds_one_worker"]) < 1e-6
    new = model["fast_whole_file"]
    assert new["http_requests"] == 64 and new["local_processing_seconds"] == 32 * 25 / 40
    assert "UNKNOWN" in new["network_rate"]
    for scenario in new["network_scenarios"]:
        assert scenario["batch_seconds_pipelined"] == max(
            scenario["network_seconds"], new["local_processing_seconds"]
        )
    assert (
        fast.throughput_model(physical, local_model, 32, 4)["fast_whole_file"][
            "local_processing_seconds"
        ]
        is None
    )  # an unmeasured worker count is never interpolated
    policy = fast.batch_policy(physical, local_model, totals, 25 * 32 * 20, 8, 32)
    assert policy["chosen_files_per_batch"] == 32 and policy["membership_unchanged"]
    assert policy["options"]["64"]["within_limits"] is False
    assert "ONE FILE" in policy["unit"]


@pytest.fixture
def fast_tool(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    return importlib.import_module("essential_web_fast")


def test_process_workers_stop_where_more_processes_stop_helping(fast_tool: Any) -> None:
    runs = {
        "1": {"rows_per_second": 10},
        "4": {"rows_per_second": 38},
        "8": {"rows_per_second": 70},
        "16": {"rows_per_second": 72},
    }
    assert fast_tool.choose_process_workers(runs, 32) == 8
    runs["16"] = {"rows_per_second": 90}
    assert fast_tool.choose_process_workers(runs, 32) == 16
    # Four logical CPUs stay free for the streams, hashing and sealing.
    assert fast_tool.choose_process_workers(runs, 16) == 8
    assert fast_tool.choose_process_workers(runs, 4) == 1
    assert fast_tool.NETWORK_COMMANDS == {"run", "benchmark"}


def test_saved_live_benchmark_is_reaccepted_offline_under_the_corrected_rule(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = world.historical.repo / "benchmark-plan.json"
    digest = world.config["benchmark"]["plan_digest"]
    assert world.run("benchmark", "--plan", str(plan), "--authorize", digest) == 0
    directory = world.root / "plans/ew-fast/benchmark"
    good = json.loads((directory / "benchmark.json").read_bytes())
    bench = json.loads(plan.read_bytes())
    code = world.config["transport_code_sha256"]
    transport = fast.TRANSPORT_CODE_FILES[0]

    def live(report: dict[str, Any]) -> dict[str, Any]:
        """The report as the first identity rule left it: run by older transport code."""
        old = {**code, transport: "5" * 64}
        body = {k: v for k, v in bench.items() if k != "digest"}
        body["transport_code_sha256"] = old
        return {
            **json.loads(json.dumps(report)),
            "verdict": "FAIL",
            "reasons": [f"{fast.SUPERSEDED_ETAG_REASON}: {name}" for name in report["files"]],
            "transport_code_sha256": old,
            "plan_digest": canonical.digest(body),
        }

    failed = live(good)
    accepted = fast.reaccept_benchmark(failed, "a" * 64, bench, REVISION, code)
    assert accepted["verdict"] == "PASS" and accepted["reasons"] == []
    assert accepted["plan_digest"] == digest and accepted["transport_code_sha256"] == code
    note = accepted["revalidation"]
    assert note["network_requests"] == 0 and note["local_sha256_recomputed_now"] is False
    assert note["files_with_repository_sha256_equal_to_local_sha256"] == 3
    assert note["files_with_etag_equal_to_local_sha256"] == 0
    assert note["code_changed_since_benchmark"] == [transport]
    assert accepted["phases"] == failed["phases"]  # measurements are carried, not altered

    def refused(change: Any, message: str) -> None:
        report = live(good)
        change(report)
        with pytest.raises(bulk.BulkError, match=message):
            fast.reaccept_benchmark(report, "a" * 64, bench, REVISION, code)

    def rejected(change: Any, message: str) -> None:
        report = live(good)
        change(report)
        result = fast.reaccept_benchmark(report, "a" * 64, bench, REVISION, code)
        assert result["verdict"] == "FAIL" and any(message in r for r in result["reasons"])

    name = next(iter(good["files"]))
    refused(lambda r: r["reasons"].append("parity: selected_records"), "does not explain")
    refused(lambda r: r["files"].pop(name), "exactly the planned")
    refused(lambda r: r.update(plan_digest="0" * 64), "differs beyond its code identity")
    rejected(lambda r: r["files"][name].update(sha256="0" * 64), "repository's SHA-256")
    rejected(lambda r: r["files"][name].update(length=1), "declared size")
    rejected(lambda r: r["files"][name].update(repo_commit="f" * 40), "another revision")
    rejected(lambda r: r["files"][name].update(etag='W/"weak"'), "strong ETag")
    rejected(lambda r: r["phases"][-1]["checks"].update(x=False), "parity")
    # Parity is void if the code that processes records is not the code that ran it.
    other = {**code, fast.TRANSPORT_CODE_FILES[1]: "0" * 64}
    with pytest.raises(bulk.BulkError, match="parity is void"):
        fast.reaccept_benchmark(failed, "a" * 64, bench, REVISION, other)
    # Through the tool: offline, the original is kept, and the gate opens.
    original = directory / "benchmark-live.json"
    original.write_text(json.dumps(failed), encoding="utf-8")
    (directory / "benchmark.json").write_text(json.dumps(failed), encoding="utf-8")
    world.prepare(0)
    assert world.run("gate", "--batch", "0") == 1
    before = len(world.state.requests)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("re-acceptance attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert world.run("benchmark-accept", "--plan", str(plan), "--report", str(original)) == 0
    assert len(world.state.requests) == before and not world.scratch_files()
    assert json.loads(original.read_bytes()) == failed
    assert world.run("gate", "--batch", "0") == 0
    assert world.run("benchmark-accept", "--plan", str(plan)) == 1  # never re-accept twice


def test_measured_batch_model_overlaps_transfer_and_processing() -> None:
    footers, units, totals = historical_tests.physical_inputs()
    physical = bulk.physical_cost(footers, units, totals)

    def report(megabytes: float) -> dict[str, Any]:
        tier = {
            "megabytes_per_second": megabytes,
            "megabits_per_second": megabytes * 8,
            "files": 8,
            "file_bytes": 1,
            "wall_seconds": 1.0,
            "requests_per_file": 2,
            "retries": 0,
        }
        return {
            "phases": [
                {
                    "phase": "download",
                    "download_workers": 4,
                    "transfer": {**tier, "megabytes_per_second": megabytes / 10},
                },
                {"phase": "download", "download_workers": 8, "transfer": tier},
                {
                    "phase": "local_processing",
                    "rows_per_second": 10.0,
                    "process_workers": 2,
                    "rows": 100,
                    "files": 2,
                    "wall_seconds": 10.0,
                    "system": {},
                },
                {"phase": "durable_copy", "megabytes_per_second": 1.0},
            ]
        }

    rows, size = 32 * 25, 32 * 25 * 2000  # 25 rows and 50,000 bytes per file
    fast_network = fast.measured_batch_model(physical, report(1.0), 32, 25)
    central = fast_network["modeled_batch"]["central"]
    assert central["limited_by"] == "local processing"
    first, copy = size / 32 / (1e6 / 8), size / 1e6 / 2
    assert central["batch_seconds"] == pytest.approx(first + rows / 10.0 + copy)
    assert central["batch_seconds"] < central["download_seconds"] + central["processing_seconds"]
    assert central["campaign_hours"] == pytest.approx(25 * central["batch_seconds"] / 3600)
    slow = fast.measured_batch_model(physical, report(0.01), 32, 25)["modeled_batch"]["central"]
    assert slow["limited_by"] == "network"
    assert slow["batch_seconds"] == pytest.approx(size / 1e4 + 25 / (10.0 / 2) + copy)
    assert "MODELED" in fast_network["modeled_campaign"]["class"]
    assert "MEASURED" in fast_network["measured_benchmark"]["class"]


def test_committed_live_benchmark_evidence_is_accepted_and_text_free(fast_tool: Any) -> None:
    """The real saved live report and its offline re-acceptance (read-only)."""
    evidence = REPO / fast_tool.FAST_DIR
    config = json.loads((evidence / "campaign.json").read_bytes())
    bench = json.loads((evidence / "benchmark-plan.json").read_bytes())
    live_path = evidence / "live-benchmark.json"
    live = json.loads(live_path.read_bytes())
    accepted = json.loads((evidence / "live-benchmark-accepted.json").read_bytes())
    assert live["verdict"] == "FAIL" and len(live["reasons"]) == 14
    assert all(r.startswith(fast.SUPERSEDED_ETAG_REASON) for r in live["reasons"])
    again = fast.reaccept_benchmark(
        live,
        hashlib.sha256(live_path.read_bytes()).hexdigest(),
        bench,
        REVISION,
        config["transport_code_sha256"],
    )
    assert again["verdict"] == "PASS" == accepted["verdict"]
    assert again["phases"] == accepted["phases"] == live["phases"]
    assert accepted["plan_digest"] == config["benchmark"]["plan_digest"]
    assert accepted["transport_code_sha256"] == config["transport_code_sha256"]
    for entry in live["files"].values():
        # The storage ETag is 64 hex and is not the content hash; the repository digest is.
        assert len(entry["etag"].strip('"')) == 64 and entry["etag"].strip('"') != entry["sha256"]
        assert entry["linked_etag"].strip('"') == entry["sha256"]
        assert entry["linked_size"] == entry["length"] and entry["repo_commit"] == REVISION
    phases = {(p["phase"], p.get("download_workers")): p for p in live["phases"]}
    assert all(phases[("parity", None)]["checks"].values())
    assert round(phases[("download", 8)]["transfer"]["megabytes_per_second"], 1) == 142.7
    assert round(phases[("local_processing", None)]["rows_per_second"]) == 11363
    model = json.loads((evidence / "throughput-model.json").read_bytes())["live"]
    central = model["modeled_batch"]["central"]
    assert 240 < central["batch_seconds"] < 260 and central["limited_by"] == "local processing"
    assert 1.6 < model["modeled_campaign"]["central_hours"] < 1.8
    assert '"text"' not in live_path.read_text(encoding="utf-8")
    assert config["limits"]["identity_rule"] == fast.IDENTITY_RULE
    assert "require_etag_sha256" not in config["limits"]


def test_committed_fast_campaign_matches_the_historical_science(fast_tool: Any) -> None:
    """The real frozen campaign: read-only checks of committed evidence."""
    evidence = REPO / fast_tool.FAST_DIR
    config = json.loads((evidence / "campaign.json").read_bytes())
    campaign = fast_tool.load_campaign(evidence / "campaign.json", Path("unused-data-root"))
    historical = json.loads((REPO / fast_tool.HISTORICAL_CAMPAIGN).read_bytes())
    fast.check_same_science(config, historical)
    assert config["supersedes"]["digest"] == historical["digest"]
    assert config["batch"]["files"] == historical["batch"]["files"] == 32
    assert config["stop"] == historical["stop"] and config["quotas"] == historical["quotas"]
    assert config["binding"]["selector"] == selector.selector_identity()
    assert config["binding"]["revision"] == REVISION
    old_dry = json.loads(
        (
            REPO / "docs/implementation/evidence/ESSENTIAL-WEB-BULK-ACQUISITION" / "dry-run.json"
        ).read_bytes()
    )
    first = campaign.members(0)
    assert canonical.digest(first) == old_dry["batch_0"]["membership_digest"]
    assert first == old_dry["batch_0"]["files"]
    assert campaign.members(1) == old_dry["batch_1"]["files"]
    bench = json.loads((evidence / "benchmark-plan.json").read_bytes())
    assert bench["digest"] == config["benchmark"]["plan_digest"]
    assert canonical.digest({k: v for k, v in bench.items() if k != "digest"}) == bench["digest"]
    assert [n for tier in bench["tiers"] for n in tier["files"]] == first[: bench["files"]]
    assert [tier["download_workers"] for tier in bench["tiers"]] == [1, 4, 8]
    comparison = json.loads((evidence / "transport-comparison.json").read_bytes())
    assert comparison["known_layout_files"] == 8
    assert comparison["whole_over_projected"] < fast.WHOLE_FILE_PREFERENCE_RATIO
    assert comparison["chosen_transport"] == "whole_file_stream"
    replay = json.loads((evidence / "local-replay.json").read_bytes())
    assert replay["all_identical"] and replay["rows"] == 16384
    assert replay["transport_code_sha256"] == config["transport_code_sha256"]
    assert replay["contains_document_text"] is False
    storage = json.loads((evidence / "storage-model.json").read_bytes())
    central = storage["scenarios"]["first_pass_target_central_4_bytes_per_token"]
    assert (
        central["fast"]["durable_steady_state_bytes"] < central["current"]["steady_state_bytes"] / 2
    )
    assert json.loads((evidence / "raw-artifact-contract.json").read_bytes()) == fast.raw_contract()
    assert config["raw_contract"] == fast.raw_contract()
    dry = json.loads((evidence / "dry-run.json").read_bytes())
    assert all(dry["membership_equals_historical_campaign"].values())
    assert dry["campaign_digest"] == config["digest"] and dry["campaign_rows_counted"] == 0


def test_operator_drivers_parse_and_the_historical_one_cannot_fetch(tmp_path: Path) -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    for name in (
        "operator_essential_web_fast.ps1",
        "operator_essential_web_bulk.ps1",
        "operator_storage.ps1",
    ):
        parsed = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-Command",
                "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                f"'{REPO / 'scripts' / name}', [ref]$null, [ref]$e); $e.Count",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert parsed.returncode == 0 and parsed.stdout.strip() == "0", parsed.stderr
    storage = json.loads((REPO / "recipes/operator/storage.json").read_bytes())
    assert storage["data_root"] == "G:\\XLM" and storage["scratch_root"] == "C:\\XLM-scratch"

    def driver(script: str, stage: str, environment: dict[str, str]) -> Any:
        return subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPO / "scripts" / script),
                "-Batch",
                "0",
                "-Stage",
                stage,
            ],
            cwd=REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    bare = {
        k: v
        for k, v in os.environ.items()
        if k not in ("XLM_DATA_ROOT", "XLM_HOME", "XLM_SCRATCH_ROOT")
    }
    refused = driver("operator_essential_web_fast.ps1", "Show", bare)
    assert refused.returncode != 0 and "operator_storage.ps1" in refused.stderr
    configured = {
        **bare,
        "XLM_DATA_ROOT": storage["data_root"],
        "XLM_HOME": storage["data_root"] + "\\" + storage["artifact_store_relative"],
    }
    no_scratch = driver("operator_essential_web_fast.ps1", "Show", configured)
    assert no_scratch.returncode != 0 and "scratch root" in no_scratch.stderr
    for stage in ("Layout", "Prepare", "Run"):
        stopped = driver("operator_essential_web_bulk.ps1", stage, configured)
        assert stopped.returncode != 0 and "Superseded campaign" in stopped.stderr
