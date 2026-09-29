"""Evidence-v4 B01/B02 regressions through the production HTTP parser (offline).

Every response here is authored HTTP/1.1 bytes parsed by the real stdlib
``http.client.HTTPResponse`` through the production request writer, deadline
reader and ``_LiveResponse`` (``tp.exchange``). Only TCP/TLS is replaced by a
scripted socket on a virtual clock: no network, no wall-clock sleeps, and the
production 120-second timeout is unchanged.

B01: body bytes the parser received before an ``IncompleteRead`` are persisted
and accounted; the attempt stays failed. B02: the 120-second physical-attempt
deadline is absolute across every blocking socket operation.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import ssl
from pathlib import Path
from typing import Any, cast

import pytest

from evidence_v4_support import (
    LAST_CHUNK,
    REDIRECT_BODY,
    SIGNED_PREFIX,
    Call,
    FakeClock,
    FakeResponse,
    Fixture,
    Rule,
    ScriptedSocket,
    Step,
    SyntheticTransport,
    WireRule,
    WireTransport,
    at,
    block_network,
    build_fixture,
    chunk,
    response_head,
    run,
)
from xlm.data.evidence_v4 import frozen, phase_p, state
from xlm.data.evidence_v4 import transport as tp

OP = "M-00-head"  # range 0-3, body must equal PAR1
DEADLINE = frozen.ATTEMPT_TIMEOUT_SECONDS


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def attempts(root: Path, op_id: str | None = None) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        table = [dict(r) for r in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")]
        ops = {
            r["op_id"]: r["status"] for r in conn.execute("SELECT op_id, status FROM operations")
        }
    finally:
        conn.close()
    for row in table:
        row["op_status"] = ops[row["op_id"]]
    return [a for a in table if op_id is None or a["op_id"] == op_id]


def head_206(fx: Fixture, call: Call, *, chunked: bool) -> bytes:
    """The exact 206 head of the requested range (only the framing varies)."""
    ok = SyntheticTransport(fx).ok(call)
    headers = {k: v for k, v in ok._headers.items() if k != "content-length"}
    if chunked:
        headers["transfer-encoding"] = "chunked"
    else:
        headers["content-length"] = str(call.end - call.start + 1)
    return response_head(206, headers)


def par1_then_eof(fx: Fixture) -> Any:
    """B01 wire: one complete four-byte PAR1 chunk, then EOF before the last chunk."""
    return lambda call: [(0.0, head_206(fx, call, chunked=True) + chunk(b"PAR1"))]


def par1_waits(fx: Fixture, first: float, second: float) -> Any:
    """B02 wire: headers at once, PAR1 chunk after ``first`` s, last chunk after ``second`` s."""
    return lambda call: [
        (0.0, head_206(fx, call, chunked=True)),
        (first, chunk(b"PAR1")),
        (second, LAST_CHUNK),
    ]


def direct(fx: Fixture) -> Any:
    """Match the operation's first hop (canonical host): a 206 there is a final response."""
    return at(fx, OP, host="huggingface.co")


def signed_host_call(fx: Fixture) -> Call:
    key = fx.key(fx.op(OP).source_file.file)
    url = f"{SIGNED_PREFIX}{key}?X-Amz-Signature=s"
    return Call(0, url, "cas-bridge.xethub.hf.co", f"/xet-bridge-us/{key}", "", 0, 3, 120, 0)


def exchange(sock: ScriptedSocket, clock: FakeClock, url: str) -> tp.Response:
    return tp.exchange(
        cast(socket.socket, sock),
        tp.check_url(url),
        start=0,
        end=3,
        deadline=clock() + DEADLINE,
        clock=clock,
    )


# -- B01: real parser ----------------------------------------------------


def test_b01_stdlib_positive_control_raises_incomplete_read_with_par1(fx: Fixture) -> None:
    """The authored wire makes the unmodified stdlib parser raise IncompleteRead(b'PAR1')."""
    call = signed_host_call(fx)
    wire = par1_then_eof(fx)(call)[0][1]

    class _Raw:
        def makefile(self, mode: str) -> io.BufferedReader:
            return io.BufferedReader(io.BytesIO(wire))

    resp = http.client.HTTPResponse(cast(socket.socket, _Raw()), method="GET")
    resp.begin()
    assert resp.status == 206 and resp.chunked
    with pytest.raises(http.client.IncompleteRead) as caught:
        resp.read(65536)
    assert caught.value.partial == b"PAR1"


def test_b01_live_response_returns_par1_before_the_framing_failure(fx: Fixture) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, par1_then_eof(fx)(call))
    response = exchange(sock, clock, call.url)
    assert response.status == 206
    assert response.read(65536) == b"PAR1"
    with pytest.raises(tp.TransportError, match="IncompleteRead") as caught:
        response.read(65536)
    assert not isinstance(caught.value, tp.DeadlineError)


def test_b01_partial_bytes_are_persisted_accounted_and_never_promoted(
    fx: Fixture, root: Path
) -> None:
    clock = FakeClock()
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), par1_then_eof(fx), 99)])
    result = run(root, fx, transport, clock)
    assert result.status == "INCOMPLETE" and result.run_status == "STOPPED"
    assert (
        result.stop_reason is not None and "retries exhausted after 3 tries" in result.stop_reason
    )
    table = attempts(root)
    assert [(a["op_id"], a["try_number"], a["hop"]) for a in table] == [
        (OP, 1, 0),
        (OP, 2, 0),
        (OP, 3, 0),
    ]
    for row in table:
        assert row["outcome"] == state.TRANSPORT_ERROR and row["http_status"] == 206
        assert "IncompleteRead" in row["error"]
        assert row["response_bytes"] == 4
        assert row["response_sha256"] == hashlib.sha256(b"PAR1").hexdigest()
        assert (root / row["temp_path"]).read_bytes() == b"PAR1"
        assert row["op_status"] != "COMPLETE"
    assert len({a["attempt_id"] for a in table}) == 3 == len(transport.sockets)
    assert result.totals["all"]["physical_attempts"] == 3
    assert result.totals["all"]["response_body_bytes"] == 12
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["status"] == "INCOMPLETE" and receipt["totals"] == result.totals
    assert not (root / state.payload_name(OP)).exists()
    assert phase_p.inspect(root)["arms"]["M"]["response_body_bytes"] == 12


def test_b01_retry_after_partial_reads_gets_new_attempts_and_totals_keep_failures(
    fx: Fixture, root: Path
) -> None:
    clock = FakeClock()
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), par1_then_eof(fx), 2)])
    result = run(root, fx, transport, clock)
    assert result.status == "COMPLETE"
    first, second, *rest = attempts(root, OP)
    for failed, try_number in ((first, 1), (second, 2)):
        assert (failed["outcome"], failed["try_number"], failed["response_bytes"]) == (
            state.TRANSPORT_ERROR,
            try_number,
            4,
        )
        assert (root / failed["temp_path"]).read_bytes() == b"PAR1"
    assert [(a["try_number"], a["outcome"]) for a in rest] == [
        (3, state.REDIRECT),
        (3, state.SUCCESS),
    ]
    assert sum(a["response_bytes"] for a in attempts(root, OP)) == 4 + 4 + len(REDIRECT_BODY) + 4
    assert result.totals["all"]["response_body_bytes"] == sum(
        a["response_bytes"] for a in attempts(root)
    )


def test_b01_content_length_path_completes_through_the_production_parser(
    fx: Fixture, root: Path
) -> None:
    """Control: every default response is a well-formed Content-Length wire."""
    clock = FakeClock()
    transport = WireTransport(fx, clock)
    assert run(root, fx, transport, clock).status == "COMPLETE"
    assert all(s.closed_at is not None for s in transport.sockets)


# -- B02: absolute deadline, parser/buffer level --------------------------


def test_b02_fixture_reproduces_the_130_second_read_under_a_once_armed_timeout(
    fx: Fixture,
) -> None:
    """Control for the fixture: a socket armed once with 120 s lets one parser read take 130 s."""
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, par1_waits(fx, 60, 70)(call))
    sock.settimeout(120)

    class _OnceArmed(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def readinto(self, buffer: Any) -> int:
            return sock.recv_into(buffer)

    class _Raw:
        def makefile(self, mode: str) -> io.BufferedReader:
            return io.BufferedReader(_OnceArmed())

    start = clock()
    resp = http.client.HTTPResponse(cast(socket.socket, _Raw()), method="GET")
    resp.begin()
    assert resp.read(65536) == b"PAR1"
    assert clock() - start == 130


def test_b02_the_same_parser_read_is_cut_at_the_absolute_deadline(fx: Fixture) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, par1_waits(fx, 60, 70)(call))
    start = clock()
    deadline = start + DEADLINE
    resp = http.client.HTTPResponse(
        cast(socket.socket, tp._DeadlineSocket(cast(socket.socket, sock), deadline, clock)),
        method="GET",
    )
    resp.begin()
    with pytest.raises(TimeoutError):
        resp.read(65536)
    assert clock() == deadline
    assert sock.timeouts == [120, 120, 60]  # head, PAR1 chunk (arrives at 60), last chunk


def test_b02_live_response_fails_at_the_deadline_after_returning_par1(fx: Fixture) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, par1_waits(fx, 60, 70)(call))
    start = clock()
    response = exchange(sock, clock, call.url)
    assert response.read(65536) == b"PAR1" and clock() == start + 60
    with pytest.raises(tp.DeadlineError):
        response.read(65536)
    assert clock() == start + DEADLINE
    assert sock.timeouts == [120, 120, 120, 60]  # send, head, PAR1, last chunk


def test_b02_every_blocking_step_is_armed_with_the_remaining_time(fx: Fixture) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    head = head_206(fx, call, chunked=True)
    steps: list[Step] = [(10.0, head[:20]), (15.0, head[20:]), (30.0, chunk(b"PA"))]
    steps += [(40.0, chunk(b"R1")), (20.0, LAST_CHUNK)]
    sock = ScriptedSocket(clock, steps)
    start = clock()
    response = exchange(sock, clock, call.url)
    body = b""
    while chunk_bytes := response.read(65536):
        body += chunk_bytes
    assert body == b"PAR1" and clock() == start + 115
    assert sock.timeouts == [120, 120, 110, 95, 65, 25]


# -- B02: connect and TLS -------------------------------------------------


class _Step:
    def __init__(self, clock: FakeClock, delay: float, error: OSError | None = None) -> None:
        self.clock, self.delay, self.error = clock, delay, error
        self.timeout: float | None = None
        self.timeouts: list[float | None] = []
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self.timeout = value
        self.timeouts.append(value)

    def wait(self) -> None:
        if self.timeout is not None and self.delay >= self.timeout:
            self.clock.now += self.timeout
            raise TimeoutError("timed out")
        self.clock.now += self.delay
        if self.error is not None:
            raise self.error

    def connect(self, address: Any) -> None:
        self.wait()

    def do_handshake(self) -> None:
        self.wait()

    def close(self) -> None:
        self.closed = True


def _scripted_network(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    connects: list[tuple[float, OSError | None]],
    handshake: float,
) -> tuple[list[_Step], list[_Step]]:
    raws: list[_Step] = []
    tlss: list[_Step] = []
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"192.0.2.{i}", 443))
        for i in range(len(connects))
    ]
    script = iter(connects)

    def fake_socket(*_: Any) -> _Step:
        delay, error = next(script)
        raw = _Step(clock, delay, error)
        raws.append(raw)
        return raw

    class _Context:
        def wrap_socket(self, raw: Any, **kwargs: Any) -> _Step:
            assert kwargs == {"server_hostname": "huggingface.co", "do_handshake_on_connect": False}
            tls = _Step(clock, handshake)
            tlss.append(tls)
            return tls

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: addresses)
    monkeypatch.setattr(socket, "socket", fake_socket)
    monkeypatch.setattr(ssl, "create_default_context", lambda: _Context())
    return raws, tlss


def test_b02_connect_and_handshake_share_the_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    refused = ConnectionRefusedError("synthetic refusal")
    raws, tlss = _scripted_network(monkeypatch, clock, [(10, refused), (30, None)], 79)
    start = clock()
    tls = tp._connect("huggingface.co", start + DEADLINE, clock)
    assert cast(object, tls) is tlss[0] and clock() == start + 119
    assert [r.timeouts for r in raws] == [[120], [110]]
    assert tlss[0].timeouts == [80]
    assert raws[0].closed and not raws[1].closed


@pytest.mark.parametrize("handshake", [80, 81])
def test_b02_handshake_reaching_the_deadline_fails(
    monkeypatch: pytest.MonkeyPatch, handshake: float
) -> None:
    clock = FakeClock()
    _, tlss = _scripted_network(monkeypatch, clock, [(40, None)], handshake)
    start = clock()
    with pytest.raises(tp.DeadlineError):
        tp._connect("huggingface.co", start + DEADLINE, clock)
    assert clock() == start + DEADLINE and tlss[0].closed


def test_b02_a_connect_timeout_is_the_deadline_and_no_address_gets_fresh_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    raws, tlss = _scripted_network(monkeypatch, clock, [(500, None), (1, None)], 1)
    start = clock()
    with pytest.raises(tp.DeadlineError):
        tp._connect("huggingface.co", start + DEADLINE, clock)
    assert clock() == start + DEADLINE and len(raws) == 1 and raws[0].closed and tlss == []


def test_b02_live_transport_wires_its_clock_through_connect_and_exchange(
    monkeypatch: pytest.MonkeyPatch, fx: Fixture
) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, par1_waits(fx, 60, 70)(call))

    def connect(host: str, deadline: float, clk: Any) -> ScriptedSocket:
        assert (host, deadline, clk) == ("cas-bridge.xethub.hf.co", start + DEADLINE, clock)
        clock.now += 5  # connect + TLS
        return sock

    monkeypatch.setattr(tp, "_connect", connect)
    start = clock()
    live = tp.LiveHttpsTransport(clock=clock)
    response = live.open(call.url, start=0, end=3, timeout_seconds=120, deadline=start + 120)
    assert response.read(65536) == b"PAR1"
    with pytest.raises(tp.DeadlineError):
        response.read(65536)
    assert clock() == start + DEADLINE
    assert sock.timeouts == [115, 115, 115, 55]
    response.close()
    assert sock.closed_at == start + DEADLINE


def test_b02_live_transport_closes_the_socket_when_the_head_times_out(
    monkeypatch: pytest.MonkeyPatch, fx: Fixture
) -> None:
    clock = FakeClock()
    call = signed_host_call(fx)
    sock = ScriptedSocket(clock, [(121.0, head_206(fx, call, chunked=False) + b"PAR1")])
    monkeypatch.setattr(tp, "_connect", lambda host, deadline, clk: sock)
    start = clock()
    with pytest.raises(tp.DeadlineError):
        tp.LiveHttpsTransport(clock=clock).open(
            call.url, start=0, end=3, timeout_seconds=120, deadline=start + 120
        )
    assert sock.closed_at == start + DEADLINE


# -- B02: public engine ---------------------------------------------------


def test_b02_60_plus_70_fails_at_the_deadline_keeps_par1_and_retries(
    fx: Fixture, root: Path
) -> None:
    clock = FakeClock()
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), par1_waits(fx, 60, 70))])
    result = run(root, fx, transport, clock)
    assert result.status == "COMPLETE"
    first, *rest = attempts(root, OP)
    assert (first["outcome"], first["http_status"], first["try_number"]) == (state.TIMEOUT, 206, 1)
    assert first["response_bytes"] == 4 and (root / first["temp_path"]).read_bytes() == b"PAR1"
    assert "deadline" in first["error"]
    sock = transport.sockets[0]
    assert sock.closed_at is not None and sock.closed_at - sock.opened_at == DEADLINE
    assert sock.timeouts == [120, 120, 120, 60]
    assert [(a["try_number"], a["outcome"]) for a in rest] == [
        (2, state.REDIRECT),
        (2, state.SUCCESS),
    ]
    assert all(a["attempt_id"] > first["attempt_id"] for a in rest)


def test_b02_repeated_deadline_failures_stop_with_every_byte_accounted(
    fx: Fixture, root: Path
) -> None:
    clock = FakeClock()
    rule = WireRule(direct(fx), par1_waits(fx, 60, 70), 99)
    result = run(root, fx, WireTransport(fx, clock, rules=[rule]), clock)
    assert result.run_status == "STOPPED" and result.stop_reason is not None
    assert "retries exhausted after 3 tries" in result.stop_reason
    table = attempts(root)
    assert [(a["try_number"], a["outcome"], a["response_bytes"]) for a in table] == [
        (1, state.TIMEOUT, 4),
        (2, state.TIMEOUT, 4),
        (3, state.TIMEOUT, 4),
    ]
    assert all(a["op_status"] != "COMPLETE" for a in table)
    assert result.totals["all"]["response_body_bytes"] == 12


@pytest.mark.parametrize(
    ("second", "outcome"),
    [(59, state.SUCCESS), (60, state.TIMEOUT), (61, state.TIMEOUT)],
    ids=["119s-permitted", "120s-timeout", "121s-timeout"],
)
def test_b02_boundary(fx: Fixture, root: Path, second: float, outcome: str) -> None:
    clock = FakeClock()
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), par1_waits(fx, 60, second))])
    assert run(root, fx, transport, clock).status == "COMPLETE"
    first = attempts(root, OP)[0]
    assert (first["outcome"], first["try_number"], first["response_bytes"]) == (outcome, 1, 4)
    sock = transport.sockets[0]
    assert sock.closed_at is not None
    assert sock.closed_at - sock.opened_at == min(60 + second, DEADLINE)
    if outcome == state.SUCCESS:
        assert (root / state.payload_name(OP)).read_bytes() == b"PAR1"
        assert len(attempts(root, OP)) == 1


def test_b02_timeout_before_any_byte_records_a_failed_empty_attempt(
    fx: Fixture, root: Path
) -> None:
    clock = FakeClock()
    call_wire = lambda call: [(130.0, head_206(fx, call, chunked=False) + b"PAR1")]  # noqa: E731
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), call_wire)])
    assert run(root, fx, transport, clock).status == "COMPLETE"
    first, *rest = attempts(root, OP)
    assert (first["outcome"], first["http_status"], first["response_bytes"]) == (
        state.TIMEOUT,
        None,
        0,
    )
    assert (root / first["temp_path"]).read_bytes() == b""
    assert transport.sockets[0].closed_at == transport.sockets[0].opened_at + DEADLINE
    assert [a["try_number"] for a in rest] == [2, 2]


def test_b02_head_received_but_no_body_before_the_deadline(fx: Fixture, root: Path) -> None:
    clock = FakeClock()
    call_wire = lambda call: [  # noqa: E731
        (0.0, head_206(fx, call, chunked=False)),
        (125.0, b"PAR1"),
    ]
    transport = WireTransport(fx, clock, rules=[WireRule(direct(fx), call_wire)])
    assert run(root, fx, transport, clock).status == "COMPLETE"
    first = attempts(root, OP)[0]
    assert (first["outcome"], first["http_status"], first["response_bytes"]) == (
        state.TIMEOUT,
        206,
        0,
    )


def test_b02_a_late_end_of_body_is_not_a_success(fx: Fixture, root: Path) -> None:
    """Engine complement: an EOF observed at/after the deadline fails the attempt."""
    clock = FakeClock()
    body = b"PAR1"

    def late(call: Call) -> FakeResponse:
        def advance(position: int) -> None:
            if position == len(body):
                clock.now += DEADLINE

        return SyntheticTransport(fx).ok(call, on_read=advance)

    rule = Rule(at(fx, OP, host="cas-bridge.xethub.hf.co"), late)
    assert run(root, fx, SyntheticTransport(fx, rules=[rule]), clock).status == "COMPLETE"
    failed = attempts(root, OP)[1]
    assert (failed["outcome"], failed["response_bytes"]) == (state.TIMEOUT, 4)
    assert "after the 120-second deadline" in failed["error"]
