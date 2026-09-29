"""Independent-style B01/B02 probe, runnable against the pre-repair or repaired tree.

Usage: python probe_b01_b02.py <tree>. Offline: authored wire bytes, real stdlib
HTTPResponse, scripted socket on a virtual clock, public run_offline engine.
"""

from __future__ import annotations

import http.client
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

tree = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(tree / "src"), str(tree / "tests")]
import socket  # noqa: E402

socket.create_connection = None  # type: ignore[assignment]
socket.getaddrinfo = None  # type: ignore[assignment]

import evidence_v4_support as sup  # noqa: E402
from xlm.data.evidence_v4 import phase_p, state  # noqa: E402
from xlm.data.evidence_v4 import transport as tp  # noqa: E402

NEW = hasattr(tp, "exchange")


class Clock:
    def __init__(self) -> None:
        self.now = time.monotonic()  # old code reads real monotonic for its socket timeout

    def __call__(self) -> float:
        return self.now


class Sock:
    def __init__(self, clock: Clock, steps: list[tuple[float, bytes]]) -> None:
        self.clock, self.steps, self.timeout, self.timeouts = clock, list(steps), None, []
        self.opened, self.closed = clock.now, None
        self.physical_body_bytes = 0

    def settimeout(self, v: Any) -> None:
        self.timeout = v
        self.timeouts.append(round(v, 3) if v is not None else v)

    def send(self, data: Any) -> int:
        return len(data)

    def sendall(self, data: Any) -> None:
        pass

    def recv_into(self, buf: Any) -> int:
        if not self.steps:
            return 0
        delay, data = self.steps[0]
        if self.timeout is not None and delay >= self.timeout:
            self.clock.now += self.timeout
            self.steps[0] = (delay - self.timeout, data)
            raise TimeoutError("timed out")
        self.clock.now += delay
        n = min(len(buf), len(data))
        buf[:n] = data[:n]
        if n < len(data):
            self.steps[0] = (0.0, data[n:])
        else:
            self.steps.pop(0)
        return n

    def makefile(self, mode: str) -> io.BufferedReader:
        sock = self

        class Raw(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, b: Any) -> int:
                return sock.recv_into(b)

        return io.BufferedReader(Raw())

    def close(self) -> None:
        if self.closed is None:
            self.closed = self.clock.now


class OldConn:
    def __init__(self, sock: Sock) -> None:
        self.sock = sock

    def close(self) -> None:
        self.sock.close()


def open_on(sock: Sock, clock: Clock, url: str, start: int, end: int, deadline: float) -> Any:
    if NEW:
        return tp.exchange(
            sock, tp.check_url(url), start=start, end=end, deadline=deadline, clock=clock
        )  # type: ignore[arg-type]
    conn = OldConn(sock)
    tp._arm_socket(conn, deadline)  # as the old open() armed before getresponse
    resp = http.client.HTTPResponse(sock, method="GET")  # type: ignore[arg-type]
    resp.begin()
    return tp._LiveResponse(conn, resp, deadline)


def head(call: Any, fx: Any) -> bytes:
    ok = sup.SyntheticTransport(fx).ok(call)
    h = {k: v for k, v in ok._headers.items() if k != "content-length"}
    h["transfer-encoding"] = "chunked"
    lines = ["HTTP/1.1 206 Partial Content"] + [f"{k}: {v}" for k, v in h.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


PAR1_CHUNK = b"4\r\nPAR1\r\n"
LAST = b"0\r\n\r\n"


class Transport:
    def __init__(self, fx: Any, clock: Clock, script: Any, times: int) -> None:
        self.fx, self.clock, self.script, self.times, self.sockets = fx, clock, script, times, []

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> Any:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        call = sup.Call(
            0,
            url,
            parts.hostname or "",
            parts.path,
            parts.query,
            start,
            end,
            timeout_seconds,
            deadline,
        )
        if (
            call.host == "huggingface.co"
            and (start, end) == (0, 3)
            and self.times > 0
            and sup.SyntheticTransport(self.fx).file_of(call)
            == self.fx.op("M-00-head").source_file.file
        ):
            self.times -= 1
            steps = self.script(call, self.fx)
            physical = 4
        else:
            fr = sup.SyntheticTransport(self.fx).default(call)
            h = {k: v for k, v in fr._headers.items()}
            h["content-length"] = str(len(fr._body))
            lines = [f"HTTP/1.1 {fr.status} X"] + [f"{k}: {v}" for k, v in h.items()]
            steps = [(0.0, ("\r\n".join(lines) + "\r\n\r\n").encode() + fr._body)]
            physical = 0
        sock = Sock(self.clock, steps)
        sock.physical_body_bytes = physical
        self.sockets.append(sock)
        try:
            return open_on(sock, self.clock, url, start, end, deadline)
        except BaseException:
            sock.close()
            raise


def attempts(root: Path) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")]
    finally:
        conn.close()


def engine_probe(name: str, script: Any, times: int) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix=f"probe-{name}-"))
    fx = sup.build_fixture(work / "fixture")
    clock = Clock()
    t = Transport(fx, clock, script, times)
    result = phase_p.run_offline(
        work / "root", plan=fx.plan, transport=t, sleep=lambda _: None, clock=clock
    )
    rows = [a for a in attempts(work / "root") if a["op_id"] == "M-00-head"]
    return {
        "result": [result.status, result.run_status, result.stop_reason],
        "M-00-head attempts": [
            {
                "try": a["try_number"],
                "hop": a["hop"],
                "outcome": a["outcome"],
                "sqlite_bytes": a["response_bytes"],
                "temp_bytes": (work / "root" / a["temp_path"]).read_bytes().hex()
                if (work / "root" / a["temp_path"]).exists()
                else None,
            }
            for a in rows
        ],
        "physically_supplied_body_bytes": sum(s.physical_body_bytes for s in t.sockets),
        "sqlite_total_M-00-head": sum(a["response_bytes"] for a in rows),
        "scripted_attempt_durations_s": [
            round(s.closed - s.opened, 3) for s in t.sockets if s.physical_body_bytes
        ],
        "scripted_socket_timeouts": [s.timeouts for s in t.sockets if s.physical_body_bytes],
    }


def parser_probe() -> dict[str, Any]:
    """Astra's buffered-parser probe: 60 s to the PAR1 chunk, 70 s to the last chunk."""
    import tempfile as _t

    fx = sup.build_fixture(Path(_t.mkdtemp()) / "fixture")
    clock = Clock()
    key = fx.key(fx.op("M-00-head").source_file.file)
    url = f"{sup.SIGNED_PREFIX}{key}?X-Amz-Signature=s"
    call = sup.Call(0, url, "cas-bridge.xethub.hf.co", f"/xet-bridge-us/{key}", "", 0, 3, 120, 0)
    sock = Sock(clock, [(0.0, head(call, fx)), (60.0, PAR1_CHUNK), (70.0, LAST)])
    start = clock.now
    deadline = start + 120
    response = open_on(sock, clock, url, 0, 3, deadline)
    events = []
    try:
        while True:
            data = response.read(65536)
            events.append({"t": round(clock.now - start, 3), "read": data.hex()})
            if not data:
                break
    except Exception as exc:  # noqa: BLE001
        events.append({"t": round(clock.now - start, 3), "raised": type(exc).__name__})
    return {"events": events, "socket_timeouts": sock.timeouts}


out = {
    "tree": str(tree),
    "repaired_transport": NEW,
    "B01_par1_then_eof_x3": engine_probe("b01", lambda c, fx: [(0.0, head(c, fx) + PAR1_CHUNK)], 3),
    "B02_parser_60_plus_70": parser_probe(),
    "B02_engine_60_plus_70_x3": engine_probe(
        "b02", lambda c, fx: [(0.0, head(c, fx)), (60.0, PAR1_CHUNK), (70.0, LAST)], 3
    ),
}
print(json.dumps(out, indent=1))
