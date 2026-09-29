"""Independent assertions/counting for the narrow review. Authored bytes only."""
from __future__ import annotations

import hashlib
from contextlib import closing
import http.client
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]


def denied(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("Network forbidden by recertification")


socket.getaddrinfo = denied
socket.create_connection = denied
socket.socket = denied  # type: ignore[assignment]

from evidence_v4_support import Call, SyntheticTransport, build_fixture  # noqa: E402
from xlm.data.evidence_v4 import phase_p, transport as tp  # noqa: E402


def identities() -> dict[str, Any]:
    base = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0"
    result: dict[str, Any] = {}
    protocol_path = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md"
    raw = (ROOT / protocol_path).read_bytes()
    committed = subprocess.check_output(["git", "show", f"HEAD:{protocol_path}"], cwd=ROOT)
    assert raw.replace(b"\r\n", b"\n") == committed
    result["protocol_worktree_sha256"] = hashlib.sha256(raw).hexdigest()
    result["protocol"] = hashlib.sha256(committed).hexdigest()
    assert result["protocol"] == "4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727"
    for name, expected in (("freeze", "747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57"), ("phase_p_plan", "16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3")):
        obj = json.loads((base / f"{name}.json").read_bytes())
        own = obj.pop("digest")
        actual = hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        assert actual == own == expected
        assert obj["selection_digest"] == "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
        assert obj["source"]["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
        result[name] = actual
    changed = subprocess.check_output(["git", "diff", "2ede38f", "--", str(base), protocol_path], cwd=ROOT)
    assert not changed
    result["selection"] = obj["selection_digest"]
    result["revision"] = obj["source"]["revision"]
    return result


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountedSocket:
    """Count actual delivered payload intersections, never a preassigned byte total."""
    def __init__(self, clock: Clock, packets: list[tuple[float, bytes, int, int]]) -> None:
        self.clock = clock
        self.packets = list(packets)
        self.offset = 0
        self.timeout = 0.0
        self.timeouts: list[float] = []
        self.delivered = bytearray()
        self.started = clock()
        self.closed: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value
        self.timeouts.append(value)

    def send(self, value: Any) -> int:
        return len(value)

    def recv_into(self, buffer: Any) -> int:
        if not self.packets:
            return 0
        delay, data, begin, end = self.packets[0]
        if delay >= self.timeout:
            self.clock.now += self.timeout
            raise TimeoutError("authored wait exhausted remaining time")
        self.clock.now += delay
        n = min(len(buffer), len(data) - self.offset)
        left, right = max(self.offset, begin), min(self.offset + n, end)
        if left < right:
            self.delivered.extend(data[left:right])
        buffer[:n] = data[self.offset:self.offset + n]
        self.offset += n
        if self.offset == len(data):
            self.packets.pop(0)
            self.offset = 0
        else:
            self.packets[0] = (0.0, data, begin, end)
        return n

    def close(self) -> None:
        self.closed = self.clock()


def packets(head: bytes, second: float | None) -> list[tuple[float, bytes, int, int]]:
    chunk = b"4\r\nPAR1\r\n"
    if second is None:
        return [(0, head + chunk, len(head) + 3, len(head) + 7)]
    return [(0, head, 0, 0), (60, chunk, 3, 7), (second, b"0\r\n\r\n", 0, 0)]


class ProbeTransport:
    def __init__(self, fx: Any, clock: Clock, second: float | None, failures: int) -> None:
        self.fx, self.clock, self.second, self.remaining = fx, clock, second, failures
        self.sockets: list[CountedSocket] = []

    def open(self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float) -> Any:
        checked = tp.check_url(url)
        call = Call(0, url, checked.host, checked.path, checked.query, start, end, timeout_seconds, deadline)
        default = SyntheticTransport(self.fx)
        if self.remaining and checked.host == "huggingface.co" and (start, end) == (0, 3) and default.file_of(call) == self.fx.op("M-00-head").source_file.file:
            self.remaining -= 1
            headers = {k: v for k, v in default.ok(call)._headers.items() if k != "content-length"}
            headers["transfer-encoding"] = "chunked"
            head = ("HTTP/1.1 206 Partial Content\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode()
            sock = CountedSocket(self.clock, packets(head, self.second))
            self.sockets.append(sock)
            return tp.exchange(sock, checked, start=start, end=end, deadline=deadline, clock=self.clock)
        return default.default(call)


def engine(work: Path, second: float | None, tries: int = 3) -> dict[str, Any]:
    fx = build_fixture(work / "fixture")
    root = work / "root"
    clock = Clock()
    wire = ProbeTransport(fx, clock, second, tries)
    result = phase_p.run_offline(root, plan=fx.plan, transport=wire, sleep=lambda _: None, clock=clock)
    with closing(sqlite3.connect(root / "state.sqlite")) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM attempts WHERE op_id='M-00-head' ORDER BY attempt_id")]
        op_status = conn.execute("SELECT status FROM operations WHERE op_id='M-00-head'").fetchone()[0]
    expected = "TRANSPORT_ERROR" if second is None else "SUCCESS" if second < 60 else "TIMEOUT"
    scripted = rows[:len(wire.sockets)]
    evidence = []
    for row, sock in zip(scripted, wire.sockets, strict=True):
        retained = root / row["temp_path"]
        if expected == "SUCCESS":
            retained = root / "payload/M-00-head.bin"
        body = retained.read_bytes()
        assert bytes(sock.delivered) == body == b"PAR1"
        assert row["response_bytes"] == len(body) == 4
        assert row["response_sha256"] == hashlib.sha256(body).hexdigest()
        assert row["outcome"] == expected
        duration = sock.closed - sock.started
        assert duration == (0 if second is None else min(60 + second, 120))
        evidence.append(dict(attempt_id=row["attempt_id"], try_number=row["try_number"], physical_bytes=len(sock.delivered), retained_bytes=len(body), sqlite_bytes=row["response_bytes"], body_hex=body.hex(), outcome=row["outcome"], seconds=duration, timeouts=sock.timeouts))
    if expected != "SUCCESS" and tries == 3:
        assert len(rows) == 3
        assert (result.status, result.run_status, op_status) == ("INCOMPLETE", "STOPPED", "PENDING")
        assert result.totals["all"]["response_body_bytes"] == 12
        assert not (root / "payload/M-00-head.bin").exists()
    else:
        assert result.status == "COMPLETE" and op_status == "COMPLETE"
        if expected != "SUCCESS":
            assert rows[1]["attempt_id"] > rows[0]["attempt_id"] and rows[1]["try_number"] == 2
            assert sum(r["response_bytes"] for r in rows) > 4
    return dict(attempts=evidence, operation=op_status, result=[result.status, result.run_status], stop_reason=result.stop_reason, total_physical=sum(len(s.delivered) for s in wire.sockets), total_sqlite_scripted=sum(r["response_bytes"] for r in scripted), all_operation_attempts=[dict(id=r["attempt_id"], attempt=r["try_number"], outcome=r["outcome"], bytes=r["response_bytes"]) for r in rows])


def parser() -> dict[str, Any]:
    head = b"HTTP/1.1 206 Partial Content\r\nTransfer-Encoding: chunked\r\n\r\n"
    class MemorySocket:
        def makefile(self, mode: str) -> Any:
            return io.BufferedReader(io.BytesIO(head + b"4\r\nPAR1\r\n"))
    control = http.client.HTTPResponse(MemorySocket(), method="GET")
    control.begin()
    try:
        control.read(65536)
        raise AssertionError("Expected IncompleteRead positive control")
    except http.client.IncompleteRead as exc:
        assert exc.partial == b"PAR1"
    clock = Clock()
    sock = CountedSocket(clock, packets(head, 70))
    response = tp.exchange(sock, tp.check_url("https://huggingface.co/authored"), start=0, end=3, deadline=120, clock=clock)
    assert response.read(65536) == b"PAR1" and clock() == 60
    try:
        response.read(65536)
        raise AssertionError("Expected absolute deadline")
    except tp.DeadlineError:
        assert clock() == 120
    response.close()
    assert sock.timeouts == [120, 120, 120, 60]
    return dict(events=[dict(t=60, body="PAR1"), dict(t=120, raised="DeadlineError")], timeouts=sock.timeouts, stdlib_partial_control="PAR1")


def main() -> None:
    start = time.perf_counter()
    result = dict(identities=identities(), parser=parser())
    with tempfile.TemporaryDirectory(prefix="v4-final-review-") as temp:
        work = Path(temp)
        for name, second, tries in (("B01", None, 3), ("B02", 70, 3), ("119", 59, 1), ("120", 60, 3), ("121", 61, 3), ("timeout_then_retry", 70, 1)):
            result[name] = engine(work / name, second, tries)
        result["fixture_disk_bytes_before_cleanup"] = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
    supplied = json.loads((HERE / "repair-probe.txt").read_text(encoding="utf-8"))
    for name, outcome in (("B01_par1_then_eof_x3", "TRANSPORT_ERROR"), ("B02_engine_60_plus_70_x3", "TIMEOUT")):
        case = supplied[name]
        assert case["physically_supplied_body_bytes"] == case["sqlite_total_M-00-head"] == 12
        assert case["result"][:2] == ["INCOMPLETE", "STOPPED"]
        assert len(case["M-00-head attempts"]) == 3
        for row in case["M-00-head attempts"]:
            assert (row["outcome"], row["sqlite_bytes"], row["temp_bytes"]) == (outcome, 4, "50415231")
    assert supplied["B02_parser_60_plus_70"] == {"events": [{"t": 60.0, "read": "50415231"}, {"t": 120.0, "raised": "DeadlineError"}], "socket_timeouts": [120.0, 120.0, 120.0, 60.0]}
    assert supplied["B02_engine_60_plus_70_x3"]["scripted_attempt_durations_s"] == [120, 120, 120]
    assert not Path("G:/Project/xlm-evidence-v4/essential-web").exists()
    result["seconds"] = time.perf_counter() - start
    result["assertions"] = "PASS"
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
