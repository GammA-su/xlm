"""Independent review probes; authored bytes, no sockets, no implementation edits.

Assertions express v4 guarantees. Failures are review evidence, not xfails.
"""
from __future__ import annotations

import http.client
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests"))
from evidence_v4_support import (  # noqa: E402
    FakeClock, Rule, SyntheticTransport, at, block_network, build_fixture, run,
)
from xlm.data.evidence_v4 import phase_p, state, transport as tp  # noqa: E402


class MemorySocket:
    def __init__(self, wire: bytes) -> None:
        self.stream = io.BytesIO(wire)

    def makefile(self, *_: Any) -> io.BytesIO:
        return self.stream


class ClosedConnection:
    # HTTPConnection.getresponse closes the connection when will_close is true;
    # the response still owns its file object. No socket is made by this probe.
    sock = None

    def close(self) -> None:
        pass


def test_completed_chunk_bytes_survive_live_parser_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture", m_files=1, t_files=1)
    root = tmp_path / "root"
    op = fx.op("M-00-head")
    wire = (
        b"HTTP/1.1 206 Partial Content\r\n"
        b"Transfer-Encoding: chunked\r\nConnection: close\r\n"
        + f"ETag: {op.source_file.strong_etag}\r\n".encode()
        + f"Content-Range: bytes 0-3/{op.source_file.remote_length}\r\n\r\n".encode()
        # One COMPLETE four-byte HTTP chunk, followed by missing chunk terminator.
        + b"4\r\nPAR1\r\n"
    )

    def response(_: Any) -> Any:
        parsed = http.client.HTTPResponse(MemorySocket(wire))
        parsed.begin()
        assert parsed.will_close
        return tp._LiveResponse(ClosedConnection(), parsed, time.monotonic() + 120)

    # Positive control: the real stdlib parser preserves these bytes on its exception.
    parsed = http.client.HTTPResponse(MemorySocket(wire))
    parsed.begin()
    with pytest.raises(http.client.IncompleteRead) as caught:
        parsed.read(65536)
    assert caught.value.partial == b"PAR1"
    transport = SyntheticTransport(fx, rules=[Rule(at(fx, op.op_id), response, 3)])
    result = run(root, fx, transport)
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        attempts = state.read_attempts(conn)
    finally:
        conn.close()
    observed = {
        "status": result.status,
        "physical_attempts": len(attempts),
        "completed_body_bytes_supplied": 12,
        "accounted_body_bytes": sum(a["response_bytes"] for a in attempts),
        "temp_sizes": [(root / a["temp_path"]).stat().st_size for a in attempts],
        "outcomes": [a["outcome"] for a in attempts],
    }
    print(json.dumps(observed, sort_keys=True))
    assert result.status == "INCOMPLETE" and len(attempts) == 3
    assert observed["accounted_body_bytes"] == 12


def test_eof_after_attempt_deadline_cannot_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture", m_files=1, t_files=1)
    clock = FakeClock()
    root = tmp_path / "root"
    base = SyntheticTransport(fx)

    def delayed_eof(call: Any) -> Any:
        def tick(pos: int) -> None:
            if pos == 4:
                clock.now += 121
        return base.ok(call, on_read=tick)

    transport = SyntheticTransport(
        fx, rules=[Rule(at(fx, "M-00-head"), delayed_eof, 3)],
    )
    result = run(root, fx, transport, clock)
    print(json.dumps({"status": result.status, "clock_seconds": clock.now}))
    assert result.status == "INCOMPLETE"


def test_live_close_framed_response_late_eof_cannot_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture", m_files=1, t_files=1)
    root = tmp_path / "root"
    clock = FakeClock()
    op = fx.op("M-00-head")
    wire = (
        b"HTTP/1.1 206 Partial Content\r\nConnection: close\r\n"
        + f"ETag: {op.source_file.strong_etag}\r\n".encode()
        + f"Content-Range: bytes 0-3/{op.source_file.remote_length}\r\n\r\n".encode()
        + b"PAR1"
    )

    class LateEOF(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            chunk = super().read(size)
            if not chunk:
                clock.now += 121  # bounded virtual blocking time; no wall-clock sleep
            return chunk

    def response(call: Any) -> Any:
        sock = MemorySocket(wire)
        sock.stream = LateEOF(wire)
        parsed = http.client.HTTPResponse(sock)
        parsed.begin()
        assert parsed.will_close and parsed.length is None
        return tp._LiveResponse(ClosedConnection(), parsed, call.deadline)

    transport = SyntheticTransport(fx, rules=[Rule(at(fx, op.op_id), response, 3)])
    result = run(root, fx, transport, clock)
    print(json.dumps({
        "live_parser_late_eof_status": result.status,
        "elapsed_virtual_seconds": clock.now - 1000,
    }))
    assert result.status == "INCOMPLETE"


def test_live_chunked_read_obeys_absolute_attempt_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real HTTPResponse + BufferedReader, multiple sub-timeout raw reads in one read()."""
    block_network(monkeypatch)
    clock = FakeClock()

    class TrickleRaw(io.RawIOBase):
        def __init__(self) -> None:
            self.parts = [
                (0, b"HTTP/1.1 206 Partial Content\r\nTransfer-Encoding: chunked\r\n\r\n"),
                (60, b"4\r\nPAR1\r\n"),
                (70, b"0\r\n\r\n"),
            ]
            self.timeout = 120.0
            self.timeouts: list[float] = []

        def readable(self) -> bool:
            return True

        def settimeout(self, seconds: float) -> None:
            self.timeout = seconds
            self.timeouts.append(seconds)

        def readinto(self, buffer: Any) -> int:
            if not self.parts:
                return 0
            delay, data = self.parts.pop(0)
            assert delay < self.timeout  # every OS-style read meets its armed timeout
            clock.now += delay
            assert len(buffer) >= len(data)
            buffer[:len(data)] = data
            return len(data)

    raw = TrickleRaw()

    class BufferedSocket:
        def makefile(self, *_: Any) -> io.BufferedReader:
            return io.BufferedReader(raw)

    class Connected:
        sock = raw

        def close(self) -> None:
            pass

    parsed = http.client.HTTPResponse(BufferedSocket())
    parsed.begin()
    response = tp._LiveResponse(Connected(), parsed, clock.now + 120)
    # Patch only for this one read; never run or wait for 130 wall-clock seconds.
    with monkeypatch.context() as scoped:
        scoped.setattr(tp.time, "monotonic", clock)
        try:
            with pytest.raises(tp.TransportError):
                response.read(65536)
        finally:
            print(json.dumps({"elapsed_virtual_seconds": clock.now - 1000,
                              "armed_timeouts": raw.timeouts}))
