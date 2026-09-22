"""Offline HF-style redirect/range transport: authored loopback fixtures only."""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.perf import load_perf_doc
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, PlanAuthorization
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    HostNotAllowlistedError,
    RedirectTargetCache,
    validate_host,
)

PAYLOAD_A = b'{"text":"authored A1"}\n{"text":"authored A2"}\n'
PAYLOAD_B = b'{"text":"authored B1"}\n{"text":"authored B2"}\n'


class RedirectObjectState:
    """Shared loopback state: canonical 302s plus Range-capable object endpoint."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.lock = threading.Lock()
        self.canonical_hits: dict[str, int] = {}
        self.object_hits: dict[tuple[str, str], int] = {}
        self.connection_count = 0
        self.request_connections: list[int] = []
        self.expire_once: set[str] = set()
        self.sig_nonce: dict[str, int] = {}


class RedirectObjectHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "XLMFixture/1"

    def log_message(self, *_: Any) -> None:
        pass

    def setup(self) -> None:
        state: RedirectObjectState = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.connection_count += 1
        super().setup()

    def _send(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        state: RedirectObjectState = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.request_connections.append(id(self.connection))
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "repo":
            rev, name = parts[1], "/".join(parts[2:])
            with state.lock:
                state.canonical_hits[name] = state.canonical_hits.get(name, 0) + 1
                nonce = state.sig_nonce.get(name, 0) + 1
                state.sig_nonce[name] = nonce
            sig = f"sig-{rev}-{nonce}-SECRET"
            location = f"/object/{name}?sig={sig}"
            self._send(302, [("Location", location), ("ETag", '"redir"')], b"redirect")
            return
        if len(parts) >= 2 and parts[0] == "repo-evil":
            self._send(
                302,
                [("Location", "http://evil.example/object"), ("ETag", '"redir"')],
                b"redirect",
            )
            return
        if len(parts) >= 2 and parts[0] == "repo-lookalike":
            self._send(
                302,
                [("Location", "https://hf.co.evil.example/object"), ("ETag", '"redir"')],
                b"redirect",
            )
            return
        if parts[0] == "object" and len(parts) >= 2:
            name = "/".join(parts[1:])
            query = urllib.parse.parse_qs(parsed.query)
            sig = (query.get("sig") or [""])[0]
            with state.lock:
                state.object_hits[(name, sig)] = state.object_hits.get((name, sig), 0) + 1
                expired = name in state.expire_once
                if expired:
                    state.expire_once.discard(name)
            if expired:
                self._send(401, [("ETag", '"expired"')], b"expired")
                return
            payload = state.payloads.get(name)
            if payload is None:
                self._send(404, [], b"missing")
                return
            etag = f'"{name}-v1"'
            range_header = self.headers.get("Range")
            if range_header:
                try:
                    start_s, end_s = range_header.removeprefix("bytes=").split("-")
                    start, end = int(start_s), int(end_s)
                except ValueError:
                    self._send(400, [], b"bad range")
                    return
                if not 0 <= start <= end < len(payload):
                    self._send(416, [], b"unsatisfiable")
                    return
                body = payload[start : end + 1]
                self._send(
                    206,
                    [
                        ("Content-Range", f"bytes {start}-{end}/{len(payload)}"),
                        ("ETag", etag),
                    ],
                    body,
                )
                return
            self._send(200, [("ETag", etag)], payload)
            return
        self._send(404, [], b"missing")


def serve(state: RedirectObjectState) -> tuple[Any, str]:
    service = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectObjectHandler)
    service.daemon_threads = True
    service.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    return service, f"http://127.0.0.1:{service.server_port}"


def teardown(service: Any) -> None:
    service.shutdown()
    service.server_close()


def range_plan(
    base: str,
    files: list[str],
    *,
    max_requests: int = 100,
    max_retries: int = 2,
    max_workers: int = 1,
    plan_id: str = "authored_transport",
    max_parser_bytes: int = 1024**2,
    repo_path: str = "/repo/rev1",
) -> AcquisitionPlan:
    limits = AcquisitionLimits(
        max_transferred_bytes=1024**2,
        max_decompressed_bytes=1024**2,
        max_temp_disk_bytes=1024**2,
        max_output_disk_bytes=1024**2,
        max_records=100,
        max_requests=max_requests,
        max_retries=max_retries,
        max_workers=max_workers,
        overall_deadline_seconds=60,
        max_parser_bytes=max_parser_bytes,
    )
    plan = AcquisitionPlan(
        plan_id=plan_id,
        source_id="authored",
        provider="https",
        repository=f"{base}{repo_path}",
        revision="authored-fixture-v1",
        mode="whole_file",
        selected_files=files,
        output_artifact_id="authored_transport",
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


def test_repeated_ranges_reuse_redirect_target(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 64})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        for _ in range(3):
            body, total, _ = fetcher.fetch_range("rows.bin", 0, 15)
            assert body == state.payloads["rows.bin"][:16] and total == 1024
        assert state.canonical_hits["rows.bin"] == 1
        assert sum(state.object_hits.values()) == 3
        doc = fetcher.perf.snapshot(
            plan=plan,
            status="COMPLETED",
            wall_seconds=1.0,
            transferred_bytes=48,
            journal_decompressed_bytes=0,
            journal_requests=fetcher.journal.state.requests_made,
            journal_cache_hits=0,
            journal_records=0,
        )
        telemetry = doc["telemetry"]
        assert telemetry["redirect_target_cache_hits"] == 2
        assert telemetry["redirect_target_cache_misses"] == 1
        assert telemetry["redirects"] == 1
        assert telemetry["accounted_network_requests"] == doc["requests_made"] == 4
        assert doc["request_accounting"]["reconciled"] is True
    finally:
        fetcher.close()
        teardown(service)


def test_expiry_invalidates_and_reresolves(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 64})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        body, _, _ = fetcher.fetch_range("rows.bin", 0, 7)
        assert body == state.payloads["rows.bin"][:8]
        assert state.canonical_hits["rows.bin"] == 1
        # Expire the cached signed target; the next range must re-resolve once.
        state.expire_once.add("rows.bin")
        body, _, _ = fetcher.fetch_range("rows.bin", 8, 15)
        assert body == state.payloads["rows.bin"][8:16]
        assert state.canonical_hits["rows.bin"] == 2
        doc = fetcher.perf.snapshot(
            plan=plan,
            status="COMPLETED",
            wall_seconds=1.0,
            transferred_bytes=16,
            journal_decompressed_bytes=0,
            journal_requests=fetcher.journal.state.requests_made,
            journal_cache_hits=0,
            journal_records=0,
        )
        assert doc["telemetry"]["redirect_target_invalidations"] == 1
        # Re-resolution repopulates exactly one live entry.
        assert len(fetcher.redirect_cache) == 1
    finally:
        fetcher.close()
        teardown(service)


def test_files_do_not_cross_use_targets(tmp_path: Path) -> None:
    state = RedirectObjectState({"a.bin": b"A" * 128, "b.bin": b"B" * 128})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["a.bin", "b.bin"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        first, _, _ = fetcher.fetch_range("a.bin", 0, 3)
        second, _, _ = fetcher.fetch_range("b.bin", 0, 3)
        assert first == b"A" * 4 and second == b"B" * 4
        assert state.canonical_hits == {"a.bin": 1, "b.bin": 1}
        assert len(fetcher.redirect_cache) == 2
    finally:
        fetcher.close()
        teardown(service)


def test_cache_key_isolates_revisions() -> None:
    cache = RedirectTargetCache(max_entries=4)
    cache.put(("https", "r", "revA", "f"), "http://127.0.0.1:9/object/f?sig=a")
    assert cache.get(("https", "r", "revB", "f")) is None
    assert cache.get(("https", "r", "revA", "g")) is None
    assert cache.get(("https", "r", "revA", "f")) == "http://127.0.0.1:9/object/f?sig=a"
    assert cache.invalidate(("https", "r", "revA", "f")) is True
    assert cache.get(("https", "r", "revA", "f")) is None


def test_malicious_redirect_refused_and_not_cached(tmp_path: Path) -> None:
    state = RedirectObjectState({})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"], max_retries=0, repo_path="/repo-evil")
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        with pytest.raises(HostNotAllowlistedError):
            fetcher.fetch_range("rows.bin", 0, 3)
        assert len(fetcher.redirect_cache) == 0
    finally:
        fetcher.close()
        teardown(service)


def test_lookalike_redirect_refused(tmp_path: Path) -> None:
    state = RedirectObjectState({})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"], max_retries=0, repo_path="/repo-lookalike")
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        with pytest.raises(HostNotAllowlistedError):
            fetcher.fetch_range("rows.bin", 0, 3)
        assert len(fetcher.redirect_cache) == 0
    finally:
        fetcher.close()
        teardown(service)


def test_signed_query_never_in_telemetry(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 64})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        fetcher.fetch_range("rows.bin", 0, 15)
        fetcher.fetch_range("rows.bin", 16, 31)
        doc = fetcher.perf.snapshot(
            plan=plan,
            status="COMPLETED",
            wall_seconds=1.0,
            transferred_bytes=32,
            journal_decompressed_bytes=0,
            journal_requests=fetcher.journal.state.requests_made,
            journal_cache_hits=0,
            journal_records=0,
        )
        dumped = json.dumps({"telemetry": doc["telemetry"], "by_host": doc["by_host"]})
        assert "SECRET" not in dumped and "sig=" not in dumped and "?" not in dumped
        assert "://" not in dumped
    finally:
        fetcher.close()
        teardown(service)


def test_budgets_count_actual_requests(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 64})
    service, base = serve(state)
    try:
        # First range: 1 logical + 1 redirect hop = 2; second (cached): 1.
        plan = range_plan(base, ["rows.bin"], max_requests=3)
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        fetcher.fetch_range("rows.bin", 0, 3)
        fetcher.fetch_range("rows.bin", 4, 7)
        assert fetcher.journal.state.requests_made == 3
        fetcher.close()
        tight = range_plan(base, ["rows.bin"], max_requests=2, plan_id="authored_tight")
        root = tmp_path / "tight"
        fetcher2 = BoundedFetcher(tight, root / "scratch", root / "output")
        fetcher2.fetch_range("rows.bin", 0, 3)
        with pytest.raises(BudgetExhaustedError, match="request"):
            fetcher2.fetch_range("rows.bin", 4, 7)
        fetcher2.close()
    finally:
        teardown(service)


def test_connection_reuse_structural(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 256})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"])
        fetcher = BoundedFetcher(plan, tmp_path / "scratch", tmp_path / "output")
        for index in range(6):
            fetcher.fetch_range("rows.bin", index * 16, index * 16 + 15)
        with state.lock:
            pooled_connections = state.connection_count
        assert fetcher.perf.connection_reuses == 4
        assert fetcher.perf.connection_creations == 1
        assert pooled_connections == 3  # resolve + redirect hop + one pooled socket
        fetcher.close()
        state2 = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 256})
        service2, base2 = serve(state2)
        try:
            plan2 = range_plan(base2, ["rows.bin"], plan_id="authored_nopool")
            fetcher2 = BoundedFetcher(
                plan2,
                tmp_path / "s2" / "scratch",
                tmp_path / "s2" / "output",
                enable_connection_pool=False,
            )
            for index in range(6):
                fetcher2.fetch_range("rows.bin", index * 16, index * 16 + 15)
            with state2.lock:
                unpooled_connections = state2.connection_count
            # No pooling but redirect cache still on: 1 resolve + 1 hop + 5 direct.
            assert unpooled_connections == 7
            assert pooled_connections < unpooled_connections
            fetcher2.close()
        finally:
            teardown(service2)
    finally:
        teardown(service)


def test_pool_disabled_still_caches_redirect(tmp_path: Path) -> None:
    state = RedirectObjectState({"rows.bin": b"0123456789ABCDEF" * 64})
    service, base = serve(state)
    try:
        plan = range_plan(base, ["rows.bin"])
        fetcher = BoundedFetcher(
            plan,
            tmp_path / "scratch",
            tmp_path / "output",
            enable_connection_pool=False,
        )
        fetcher.fetch_range("rows.bin", 0, 3)
        fetcher.fetch_range("rows.bin", 4, 7)
        assert state.canonical_hits["rows.bin"] == 1
        assert fetcher.perf.redirect_cache_hits == 1
        assert fetcher.perf.connection_reuses == 0
        assert fetcher.perf.connection_creations == 0
        fetcher.close()
    finally:
        teardown(service)


def test_deterministic_selected_output_with_redirects(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import AcquisitionMode

    state = RedirectObjectState({"a.jsonl": PAYLOAD_A, "b.jsonl": PAYLOAD_B})
    service, base = serve(state)
    try:

        def selected_plan(workers: int, leaf: str) -> AcquisitionPlan:
            limits = AcquisitionLimits(
                max_transferred_bytes=1024**2,
                max_decompressed_bytes=1024**2,
                max_temp_disk_bytes=1024**2,
                max_output_disk_bytes=1024**2,
                max_records=100,
                max_requests=100,
                max_retries=1,
                max_workers=workers,
                overall_deadline_seconds=60,
            )
            plan = AcquisitionPlan(
                plan_id=f"authored_sel_{leaf}",
                source_id="authored",
                provider="https",
                repository=f"{base}/repo/rev1",
                revision="authored-fixture-v1",
                mode=AcquisitionMode.SELECTED_RECORDS,
                selected_files=["a.jsonl", "b.jsonl"],
                row_ranges={"a.jsonl": (0, 2), "b.jsonl": (0, 2)},
                output_artifact_id="authored_sel",
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

        outputs: list[bytes] = []
        for workers, leaf in [(1, "one"), (2, "two")]:
            root = tmp_path / leaf
            plan = selected_plan(workers, leaf)
            fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
            fetcher.run()
            outputs.append((root / "output/selected_records.jsonl").read_bytes())
            journal = ProgressJournal(
                root / "scratch/journals" / f"{plan.plan_id}.progress.json",
                plan.plan_id,
                plan.compute_behavioral_hash(),
            )
            assert journal.state.status == "COMPLETED"
            doc = load_perf_doc(root / "scratch/performance" / f"{plan.plan_id}.perf.json")
            assert doc["request_accounting"]["reconciled"] is True
            fetcher.close()
        assert outputs[0] == outputs[1]
        rows = [json.loads(line) for line in outputs[0].splitlines()]
        assert [r["_xlm_acquisition"]["source_file"] for r in rows] == ["a.jsonl"] * 2 + [
            "b.jsonl"
        ] * 2
    finally:
        teardown(service)


def test_validate_host_still_rejects_unsigned_bypass() -> None:
    with pytest.raises(HostNotAllowlistedError):
        validate_host("http://evil.example/object?sig=x")
    with pytest.raises(HostNotAllowlistedError):
        validate_host("https://hf.co.evil.example/object")
    validate_host("http://127.0.0.1:9/object?sig=x")
