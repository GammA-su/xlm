"""Integration tests for BoundedFetcher using a local HTTP fixture server on 127.0.0.1."""

from __future__ import annotations

import gzip
import http.server
import io
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from xlm.data.acquisition.disk import (
    StorageCapacityManager,
)
from xlm.data.acquisition.fetcher import (
    BoundedDecompressor,
    BoundedFetcher,
    DecompressionBombError,
)
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    PlanAuthorization,
    SourceDriftDetectedError,
)
from xlm.data.acquisition.progress import ProgressJournal
from xlm.data.sources.transport import BudgetExhaustedError, HostNotAllowlistedError


class MockAcquisitionHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP test handler simulating various network edge cases and anomalies."""

    dropout_counter: int = 0
    rate_limit_counter: int = 0

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard logging to keep test output clean
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path == "/normal_file":
            payload = b"Hello XLM World! This is a test dataset file payload." * 100
            etag = '"etag-v1-strong"'
            range_hdr = self.headers.get("Range")

            if range_hdr and range_hdr.startswith("bytes="):
                m = re.match(r"bytes=(\d+)-", range_hdr)
                if m:
                    start_idx = int(m.group(1))
                    if start_idx < len(payload):
                        slice_data = payload[start_idx:]
                        self.send_response(206)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("ETag", etag)
                        self.send_header(
                            "Content-Range",
                            f"bytes {start_idx}-{len(payload) - 1}/{len(payload)}",
                        )
                        self.send_header("Content-Length", str(len(slice_data)))
                        self.end_headers()
                        self.wfile.write(slice_data)
                        return

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/dropout":
            payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 100
            etag = '"etag-dropout-1"'
            range_hdr = self.headers.get("Range")

            if not range_hdr:
                # First attempt: stream 500 bytes and abruptly close connection
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:500])
                self.wfile.flush()
                # Simulate dropout by closing socket
                self.close_connection = True
                return
            else:
                # Resumed attempt with Range: serve the remainder
                m = re.match(r"bytes=(\d+)-", range_hdr)
                start_idx = int(m.group(1)) if m else 0
                slice_data = payload[start_idx:]
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("ETag", etag)
                self.send_header(
                    "Content-Range",
                    f"bytes {start_idx}-{len(payload) - 1}/{len(payload)}",
                )
                self.send_header("Content-Length", str(len(slice_data)))
                self.end_headers()
                self.wfile.write(slice_data)

        elif path == "/ignore_range":
            payload = b"Complete original payload starting from byte 0." * 20
            # Server ignores Range header and responds with 200 OK + full content
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        elif path == "/changed_etag":
            if_range = self.headers.get("If-Range")
            if if_range:
                # Client resuming with old ETag -> server reports 412 (source drifted)
                self.send_response(412)
                self.send_header("ETag", '"etag-v2-changed"')
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("ETag", '"etag-v1-original"')
                payload = b"Initial content before change."
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        elif path == "/rate_limit_429":
            MockAcquisitionHTTPHandler.rate_limit_counter += 1
            if MockAcquisitionHTTPHandler.rate_limit_counter <= 1:
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.end_headers()
            else:
                self.send_response(200)
                payload = b"Success after rate limit."
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        elif path == "/inconsistent_range":
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            # Return wrong start offset 0 when client asked for 500
            self.send_header("Content-Range", "bytes 0-100/1000")
            self.send_header("Content-Length", "101")
            self.end_headers()
            self.wfile.write(b"X" * 101)

        elif path == "/oversized":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "10000000")
            self.end_headers()
            # Send chunks indefinitely until socket breaks
            try:
                for _ in range(500):
                    self.wfile.write(b"A" * 65536)
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass

        elif path == "/malicious_redirect":
            self.send_response(302)
            self.send_header("Location", "http://untrusted-external-host.com/secret_data")
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def local_http_server() -> Any:
    """Run local mock HTTP server on 127.0.0.1 on a free dynamic port."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockAcquisitionHTTPHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def create_authorized_plan(
    base_url: str,
    endpoint: str,
    tmp_path: Path,
    limits: AcquisitionLimits | None = None,
) -> AcquisitionPlan:
    """Helper to create an authorized pilot acquisition plan."""
    plan = AcquisitionPlan(
        plan_id=f"plan_{endpoint.strip('/')}",
        source_id="mock_source",
        view_id="default",
        provider="https",
        repository=base_url,
        revision="mock_commit_123",
        mode=AcquisitionMode.WHOLE_FILE,
        selected_files=[endpoint.lstrip("/")],
        output_artifact_id=f"raw_{endpoint.strip('/')}",
        limits=limits or AcquisitionLimits(),
        is_pilot=True,
    )
    auth = PlanAuthorization(
        authorization_hash=plan.compute_behavioral_hash(),
        authorized_by="test_operator",
        authorized_at="2026-09-19T12:00:00Z",
        is_pilot_approved=True,
    )
    return plan.model_copy(update={"authorization": auth})


def test_fetcher_dropouts_and_range_resumption(local_http_server: str, tmp_path: Path) -> None:
    """Verify that mid-stream dropout resumes via Range and reconstructs bit-for-bit."""
    plan = create_authorized_plan(local_http_server, "dropout", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    state = fetcher.run()

    assert state.status == "COMPLETED"
    final_file = output_dir / "dropout"
    assert final_file.exists()
    expected_content = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 100
    assert final_file.read_bytes() == expected_content
    assert state.transferred_bytes >= len(expected_content)


def test_fetcher_ignored_range_handling(local_http_server: str, tmp_path: Path) -> None:
    """Verify that when server returns 200 (ignored Range), fetcher restarts cleanly."""
    plan = create_authorized_plan(local_http_server, "ignore_range", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    # Pre-populate partial file with some existing data to simulate prior interrupted run
    partial_dir = scratch_dir / "partials" / plan.plan_id
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_file = partial_dir / "ignore_range.part"
    partial_file.write_bytes(b"STALE_OLD_PARTIAL_DATA")

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    state = fetcher.run()

    assert state.status == "COMPLETED"
    final_file = output_dir / "ignore_range"
    assert final_file.exists()
    expected_content = b"Complete original payload starting from byte 0." * 20
    # Must NOT have STALE_OLD_PARTIAL_DATA concatenated!
    assert final_file.read_bytes() == expected_content


def test_fetcher_source_drift_412_blocks(local_http_server: str, tmp_path: Path) -> None:
    """Verify that an upstream ETag change during resume triggers SourceDriftDetectedError."""
    plan = create_authorized_plan(local_http_server, "changed_etag", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    # Pre-populate journal with prior ETag and partial file
    partial_dir = scratch_dir / "partials" / plan.plan_id
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_file = partial_dir / "changed_etag.part"
    partial_file.write_bytes(b"Initial content")

    journal_path = scratch_dir / "journals" / f"{plan.plan_id}.progress.json"
    journal = ProgressJournal(journal_path, plan.plan_id, plan.compute_behavioral_hash())
    journal.update_file_progress("changed_etag", bytes_added=15, etag='"etag-v1-original"')
    journal.save()

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    with pytest.raises(SourceDriftDetectedError, match="Source drift detected"):
        fetcher.run()


def test_fetcher_rate_limit_429_backoff(local_http_server: str, tmp_path: Path) -> None:
    """Verify that HTTP 429 with Retry-After is respected and succeeds on retry."""
    MockAcquisitionHTTPHandler.rate_limit_counter = 0
    plan = create_authorized_plan(local_http_server, "rate_limit_429", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    state = fetcher.run()

    assert state.status == "COMPLETED"
    final_file = output_dir / "rate_limit_429"
    assert final_file.read_bytes() == b"Success after rate limit."


def test_fetcher_inconsistent_content_range_fails(local_http_server: str, tmp_path: Path) -> None:
    """Verify that inconsistent Content-Range header start offset raises an immediate error."""
    plan = create_authorized_plan(local_http_server, "inconsistent_range", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    # Create partial file to trigger Range request
    partial_dir = scratch_dir / "partials" / plan.plan_id
    partial_dir.mkdir(parents=True, exist_ok=True)
    (partial_dir / "inconsistent_range.part").write_bytes(b"X" * 500)

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    with pytest.raises(RuntimeError, match="Inconsistent Content-Range start"):
        fetcher.run()


def test_fetcher_oversized_payload_halts_budget(local_http_server: str, tmp_path: Path) -> None:
    """Verify that stream exceeding max_transferred_bytes triggers BudgetExhaustedError."""
    # Set tight budget: 128 KiB
    limits = AcquisitionLimits(max_transferred_bytes=128 * 1024)
    plan = create_authorized_plan(local_http_server, "oversized", tmp_path, limits=limits)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    with pytest.raises(BudgetExhaustedError, match="Transferred bytes limit"):
        fetcher.run()

    # Progress journal records INTERRUPTED
    assert fetcher.journal.state.status == "INTERRUPTED"


def test_fetcher_malicious_redirect_blocked(local_http_server: str, tmp_path: Path) -> None:
    """Verify that a redirect to an unapproved host is blocked by SafeRedirectHandler."""
    plan = create_authorized_plan(local_http_server, "malicious_redirect", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    fetcher = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    with pytest.raises(HostNotAllowlistedError):
        fetcher.run()


def test_fetcher_cache_hits_and_zero_network(local_http_server: str, tmp_path: Path) -> None:
    """Verify that re-running on an already completed file records cache hit and 0 network bytes."""
    plan = create_authorized_plan(local_http_server, "normal_file", tmp_path)
    output_dir = tmp_path / "output"
    scratch_dir = tmp_path / "scratch"

    # Run 1: initial download
    fetcher1 = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    state1 = fetcher1.run()
    initial_transferred = state1.transferred_bytes
    assert initial_transferred > 0

    # Run 2: re-run
    fetcher2 = BoundedFetcher(plan, scratch_dir=scratch_dir, output_dir=output_dir)
    state2 = fetcher2.run()
    assert state2.cache_hits == 1
    # Zero network bytes transferred during run 2
    assert fetcher2.capacity_mgr.transferred_bytes == 0


def test_fetcher_decompression_bomb_guard(tmp_path: Path) -> None:
    """Verify that BoundedDecompressor detects and halts decompression bombs."""
    cap_mgr = StorageCapacityManager(
        max_transferred_bytes=10 * 1024 * 1024,
        max_decompressed_bytes=100 * 1024,  # Tight 100 KiB uncompressed limit
        max_temp_disk_bytes=10 * 1024 * 1024,
        max_output_disk_bytes=10 * 1024 * 1024,
    )
    # Generate 500 KiB of zeros compressed into a tiny gzip stream (high compression ratio)
    uncompressed = b"\x00" * (500 * 1024)
    compressed = gzip.compress(uncompressed)

    decompressor = BoundedDecompressor(cap_mgr, max_ratio=5.0, max_decompressed_bytes=100 * 1024)
    in_stream = io.BytesIO(compressed)
    out_stream = io.BytesIO()

    with pytest.raises(DecompressionBombError):
        decompressor.decompress_stream(in_stream, out_stream)


def test_fetcher_crash_consistency_reconciliation(tmp_path: Path) -> None:
    """Verify that a partial file with extra bytes is truncated to verified prefix on startup."""
    scratch_dir = tmp_path / "scratch"
    output_dir = tmp_path / "output"
    partial_dir = scratch_dir / "partials" / "test_plan"
    partial_dir.mkdir(parents=True, exist_ok=True)

    partial_file = partial_dir / "file.bin.part"
    # Write 1000 bytes on disk
    partial_file.write_bytes(b"A" * 1000)

    # Journal only recorded 600 bytes as verified
    journal_path = scratch_dir / "journals" / "test_plan.progress.json"
    journal = ProgressJournal(journal_path, "test_plan", "hash123")
    journal.update_file_progress("file.bin", bytes_added=600)
    journal.save()

    # Run reconciliation
    journal.reconcile_with_disk(output_dir, partial_dir)

    # Disk file must be truncated to 600 bytes
    assert partial_file.stat().st_size == 600
    assert journal.state.file_progress["file.bin"].bytes_downloaded == 600
