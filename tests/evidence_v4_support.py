"""Authored synthetic fixtures for the Evidence-v4 Phase-P tests (offline only).

Everything here is SYNTHETIC: small Parquet files written by PyArrow into a
temporary directory, a synthetic source repository/revision, a scripted
HTTPS-like transport and a controllable clock. Frozen bindings of the
synthetic plan are computed independently from the authored files' metadata.

Run as a script (``crash-run <fixture_dir> <root>``) it performs the
E2E crash phase in a separate process that dies with ``os._exit`` mid-body.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:  # script mode
    sys.path.insert(0, str(REPO / "src"))

from xlm.data.evidence_v2 import canonical  # noqa: E402
from xlm.data.evidence_v4 import frozen, phase_p  # noqa: E402
from xlm.data.evidence_v4 import transport as tp  # noqa: E402

SYNTHETIC_REVISION = "0123456789abcdef0123456789abcdef01234567"
REDIRECT_BODY = b"Found. Redirecting to the signed target"
SIGNED_PREFIX = "https://cas-bridge.xethub.hf.co/xet-bridge-us/"
PREFIX = f"/datasets/{frozen.SYNTHETIC_REPOSITORY}/resolve/{SYNTHETIC_REVISION}/"


class SimulatedCrash(BaseException):
    """In-process stand-in for a process death (not an Exception: never caught)."""


def block_network(monkeypatch: Any) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# -- authored Parquet files ------------------------------------------------


def _m_bytes(rows: int, seed: int) -> bytes:
    eai = pa.array(
        [
            {"primary": {"code": f"{(i + seed) % 7}", "label": f"L{i % 5}"}, "bloom": i % 6}
            for i in range(rows)
        ]
    )
    quality = pa.array([{"score": (i * 7 + seed) / rows, "length": i * 3} for i in range(rows)])
    table = pa.table(
        {
            "id": [f"m{seed}-{i:05d}" for i in range(rows)],
            "eai_taxonomy": eai,
            "quality_signals": quality,
            "text": [f"authored synthetic row {i} of file {seed}" for i in range(rows)],
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=40, compression="snappy")
    return sink.getvalue()


def _t_bytes(rows: int, seed: int, pad: int) -> bytes:
    table = pa.table(
        {
            "id": [f"t{seed}-{i:05d}" for i in range(rows)],
            "text": [f"authored synthetic document {i} of file {seed} " * 4 for i in range(rows)],
        }
    )
    if pad:
        table = table.replace_schema_metadata({"synthetic_pad": "p" * pad})
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=30, compression="snappy")
    return sink.getvalue()


def _chunk_span(column: Any) -> tuple[int, int]:
    start = int(column.data_page_offset)
    if column.dictionary_page_offset is not None and int(column.dictionary_page_offset) > 0:
        start = min(start, int(column.dictionary_page_offset))
    return start, start + int(column.total_compressed_size)


def _footer_length(raw: bytes) -> int:
    return int.from_bytes(raw[-8:-4], "little")


def _m_entry(ordinal: int, name: str, raw: bytes) -> dict[str, Any]:
    meta = pq.ParquetFile(io.BytesIO(raw)).metadata
    group = meta.row_group(1)  # rows 40..79 hold the window [45, 60)
    chunks = [
        group.column(i)
        for i in range(group.num_columns)
        if str(group.column(i).path_in_schema).split(".")[0] in frozen.PROJECTION
    ]
    return {
        "arm": "M",
        "ordinal": ordinal,
        "file": name,
        "remote_length": len(raw),
        "strong_etag": f'"{hashlib.sha256(raw).hexdigest()}"',
        "bindings": {
            "window": [45, 60],
            "projection": list(frozen.PROJECTION),
            "data_chunk_count": len(chunks),
            "data_payload_bytes": sum(int(c.total_compressed_size) for c in chunks),
            "data_uncompressed_bytes": sum(int(c.total_uncompressed_size) for c in chunks),
        },
    }


def _t_entry(ordinal: int, name: str, raw: bytes) -> dict[str, Any]:
    meta = pq.ParquetFile(io.BytesIO(raw)).metadata
    group = meta.row_group(1)
    text = next(
        group.column(i)
        for i in range(group.num_columns)
        if group.column(i).path_in_schema == "text"
    )
    start, end = _chunk_span(text)
    return {
        "arm": "T",
        "ordinal": ordinal,
        "file": name,
        "remote_length": len(raw),
        "strong_etag": f'"{hashlib.sha256(raw).hexdigest()}"',
        "bindings": {
            "text_column": "text",
            "data_span_half_open": [start, end],
            "data_range_count": 1,
            "data_payload_bytes": end - start,
        },
    }


def plan_dict(
    files: list[dict[str, Any]], data: dict[str, bytes], profile: frozen.Profile = frozen.V40
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for f in files:
        n = f["remote_length"]
        prefix = f"{f['arm']}-{f['ordinal']:02d}"
        base = {"arm": f["arm"], "ordinal": f["ordinal"]}
        operations.append({"op_id": f"{prefix}-head", **base, "kind": frozen.HEAD, "range": [0, 3]})
        if f["arm"] == "M":
            length = _footer_length(data[f["file"]])
            operations.append(
                {
                    "op_id": f"{prefix}-footer",
                    **base,
                    "kind": frozen.M_FOOTER,
                    "range": [n - 8 - length, n - 1],
                    "expected_footer_length": length,
                }
            )
        else:
            operations.append(
                {
                    "op_id": f"{prefix}-trailer",
                    **base,
                    "kind": frozen.T_TRAILER,
                    "range": [n - 8, n - 1],
                }
            )
            operations.append(
                {
                    "op_id": f"{prefix}-footer",
                    **base,
                    "kind": frozen.T_FOOTER,
                    "range": None,
                    "range_rule": frozen.T_FOOTER_RULE,
                }
            )
    for seq, op in enumerate(operations):
        op["seq"] = seq
    body: dict[str, Any] = {
        "kind": "essential_web_v4_phase_p_plan",
        "protocol_version": profile.protocol_version,
        "plan_schema_version": 1,
        "synthetic": True,
        "phase": "P",
        "scientific_namespace": frozen.SCIENTIFIC_NAMESPACE,
        "selection_digest": frozen.SELECTION_DIGEST,
        "policy_digest": frozen.POLICY_DIGEST,
        "source": {
            "host": "huggingface.co",
            "repository_type": "datasets",
            "repository": frozen.SYNTHETIC_REPOSITORY,
            "revision": SYNTHETIC_REVISION,
        },
        "execution_root": "SYNTHETIC",
        "arms": ["M", "T"],
        "files": files,
        "operations": operations,
        "limits": frozen.LIMITS,
        "network": profile.network,
    }
    body["digest"] = canonical.self_digest(body)
    return body


@dataclass
class Fixture:
    directory: Path
    data: dict[str, bytes]  # plan file path -> authored bytes
    plan_obj: dict[str, Any]
    plan: frozen.Plan

    def key(self, file: str) -> str:
        return hashlib.sha256(self.data[file]).hexdigest()

    def file_of_key(self, key: str) -> str:
        return next(f for f in self.data if self.key(f) == key)

    def etag(self, file: str) -> str:
        return f'"{self.key(file)}"'

    def op(self, op_id: str) -> frozen.Operation:
        return self.plan.operation(op_id)


def build_fixture(
    directory: Path,
    *,
    m_files: int = 2,
    t_files: int = 2,
    t_pad: int = 0,
    profile: frozen.Profile = frozen.V40,
) -> Fixture:
    """Author the Parquet files and the synthetic plan; T file 1 carries ``t_pad``."""
    directory.mkdir(parents=True, exist_ok=True)
    data: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for i in range(m_files):
        name = f"data/crawl=SYN-M/train-{i:05d}.parquet"
        data[name] = _m_bytes(120, i)
        entries.append(_m_entry(i, name, data[name]))
    for i in range(t_files):
        name = f"data/crawl=SYN-T/train-{i:05d}.parquet"
        data[name] = _t_bytes(90, i, t_pad if i == 1 else 0)
        entries.append(_t_entry(i, name, data[name]))
    plan_obj = plan_dict(entries, data, profile)
    for raw in data.values():
        target = directory / "files" / hashlib.sha256(raw).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (directory / "plan.json").write_bytes(canonical.canonical_bytes(plan_obj))
    plan = frozen.validate_plan(plan_obj, synthetic=True, profile=profile)
    return Fixture(directory, data, plan_obj, plan)


def load_fixture(directory: Path) -> Fixture:
    plan_obj = canonical.loads_bytes_strict((directory / "plan.json").read_bytes())
    data = {
        f["file"]: (directory / "files" / f["strong_etag"].strip('"')).read_bytes()
        for f in plan_obj["files"]
    }
    return Fixture(directory, data, plan_obj, frozen.validate_plan(plan_obj, synthetic=True))


# -- scripted transport ----------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Call:
    index: int
    url: str
    host: str
    path: str
    query: str
    start: int
    end: int
    timeout_seconds: float
    deadline: float


class FakeResponse:
    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        body: bytes = b"",
        *,
        fail_after: int | None = None,
        crash_after: int | None = None,
        crash: Callable[[], None] | None = None,
        on_read: Callable[[int], None] | None = None,
    ) -> None:
        self.status = status
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._body = body
        self._pos = 0
        self._fail_after = fail_after
        self._crash_after = crash_after
        self._crash = crash
        self._on_read = on_read
        self.closed = False

    def header(self, name: str) -> str | None:
        return self._headers.get(name.lower())

    def read(self, amount: int) -> bytes:
        if self._on_read is not None:
            self._on_read(self._pos)
        if self._crash_after is not None and self._pos >= self._crash_after:
            if self._crash is not None:
                self._crash()
            raise SimulatedCrash(f"crash after {self._pos} bytes")
        if self._fail_after is not None and self._pos >= self._fail_after:
            raise tp.TransportError("connection reset by synthetic peer")
        limit = amount
        for bound in (self._fail_after, self._crash_after):
            if bound is not None:
                limit = min(limit, bound - self._pos)
        chunk = self._body[self._pos : self._pos + limit]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


Responder = Callable[[Call], "FakeResponse | BaseException"]


@dataclass
class Rule:
    match: Callable[[Call], bool]
    respond: Responder
    times: int = 1


@dataclass
class SyntheticTransport:
    fixture: Fixture
    rules: list[Rule] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> FakeResponse:
        parts = urlsplit(url)
        call = Call(
            len(self.calls),
            url,
            parts.hostname or "",
            parts.path,
            parts.query,
            start,
            end,
            timeout_seconds,
            deadline,
        )
        self.calls.append(call)
        for rule in self.rules:
            if rule.times > 0 and rule.match(call):
                rule.times -= 1
                answer = rule.respond(call)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        return self.default(call)

    def file_of(self, call: Call) -> str:
        if call.host == "huggingface.co":
            return unquote(call.path)[len(PREFIX) :]
        return self.fixture.file_of_key(call.path.rsplit("/", 1)[-1])

    def default(self, call: Call) -> FakeResponse:
        file = self.file_of(call)
        if call.host == "huggingface.co":
            return redirect(f"{SIGNED_PREFIX}{self.fixture.key(file)}?X-Amz-Signature=s&Expires=1")
        return self.ok(call)

    def ok(self, call: Call, **overrides: Any) -> FakeResponse:
        file = self.file_of(call)
        raw = self.fixture.data[file]
        body = overrides.pop("body", raw[call.start : call.end + 1])
        headers = {
            "Content-Range": f"bytes {call.start}-{call.end}/{len(raw)}",
            "ETag": self.fixture.etag(file),
            "Content-Length": str(len(body)),
        }
        headers.update(overrides.pop("headers", {}))
        headers = {k: v for k, v in headers.items() if v is not None}
        return FakeResponse(overrides.pop("status", 206), headers, body, **overrides)


def redirect(location: str, status: int = 302) -> FakeResponse:
    return FakeResponse(status, {"Location": location}, REDIRECT_BODY)


def at(fixture: Fixture, op_id: str, *, host: str | None = None) -> Callable[[Call], bool]:
    """Match calls of one operation (by file and range start; T footer: after the trailer)."""
    op = fixture.op(op_id)

    def match(call: Call) -> bool:
        if host is not None and call.host != host:
            return False
        file = SyntheticTransport(fixture).file_of(call)
        if file != op.source_file.file:
            return False
        if op.range is not None:
            return (call.start, call.end) == op.range
        return call.end == op.source_file.remote_length - 9

    return match


def run(
    root: Path,
    fixture: Fixture,
    transport: tp.Transport,
    clock: FakeClock | None = None,
) -> phase_p.Result:
    return phase_p.run_offline(
        root,
        plan=fixture.plan,
        transport=transport,
        sleep=lambda _: None,
        clock=clock or FakeClock(),
    )


# -- authored wire: the production HTTP parser over a scripted socket ------

Step = tuple[float, bytes]  # (virtual seconds until the bytes arrive, bytes; b"" = EOF)
LAST_CHUNK = b"0\r\n\r\n"


def chunk(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


def response_head(status: int, headers: dict[str, str]) -> bytes:
    lines = [f"HTTP/1.1 {status} Synthetic"] + [f"{k}: {v}" for k, v in headers.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def wire_of(response: FakeResponse, *, chunked: bool = False) -> bytes:
    """One authored HTTP/1.1 response carrying a FakeResponse's status/headers/body."""
    headers = {k: v for k, v in response._headers.items() if k != "content-length"}
    if chunked:
        headers["transfer-encoding"] = "chunked"
        body = (chunk(response._body) if response._body else b"") + LAST_CHUNK
    else:
        headers["content-length"] = str(len(response._body))
        body = response._body
    return response_head(response.status, headers) + body


class ScriptedSocket:
    """Authored raw socket on a virtual clock: no network, no wall-clock sleeps.

    Each receive waits its step's virtual delay, then yields the step's bytes.
    A receive whose delay reaches the armed timeout advances the clock by the
    timeout and raises the socket timeout instead (the stdlib behaviour).
    """

    def __init__(self, clock: FakeClock, steps: list[Step]) -> None:
        self.clock = clock
        self.steps = list(steps)
        self.timeouts: list[float | None] = []
        self.sent = bytearray()
        self.opened_at = clock.now
        self.closed_at: float | None = None
        self._timeout: float | None = None

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)
        self._timeout = value

    def send(self, data: Any) -> int:
        self.sent += bytes(data)
        return len(data)

    def recv_into(self, buffer: Any) -> int:
        if not self.steps:
            return 0
        delay, data = self.steps[0]
        if self._timeout is not None and delay >= self._timeout:
            self.clock.now += self._timeout
            self.steps[0] = (delay - self._timeout, data)
            raise TimeoutError("timed out")
        self.clock.now += delay
        count = min(len(buffer), len(data))
        buffer[:count] = data[:count]
        if count < len(data):
            self.steps[0] = (0.0, data[count:])
        else:
            self.steps.pop(0)
        return count

    def close(self) -> None:
        if self.closed_at is None:
            self.closed_at = self.clock.now


WireResponder = Callable[[Call], list[Step]]


@dataclass
class WireRule:
    match: Callable[[Call], bool]
    respond: WireResponder
    times: int = 1


@dataclass
class WireTransport:
    """Offline transport through the PRODUCTION request writer, HTTPResponse parser,
    deadline reader and ``_LiveResponse`` (``tp.exchange``); only TCP/TLS is scripted."""

    fixture: Fixture
    clock: FakeClock
    rules: list[WireRule] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    sockets: list[ScriptedSocket] = field(default_factory=list)
    policy: frozen.HostPolicy = frozen.V40_HOSTS

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> tp.Response:
        parts = urlsplit(url)
        call = Call(
            len(self.calls),
            url,
            parts.hostname or "",
            parts.path,
            parts.query,
            start,
            end,
            timeout_seconds,
            deadline,
        )
        self.calls.append(call)
        steps = self.default(call)
        for rule in self.rules:
            if rule.times > 0 and rule.match(call):
                rule.times -= 1
                steps = rule.respond(call)
                break
        sock = ScriptedSocket(self.clock, steps)
        self.sockets.append(sock)
        try:
            return tp.exchange(
                cast(socket.socket, sock),
                tp.check_url(url, self.policy),
                start=start,
                end=end,
                deadline=deadline,
                clock=self.clock,
            )
        except BaseException:
            sock.close()
            raise

    def default(self, call: Call) -> list[Step]:
        return [(0.0, wire_of(SyntheticTransport(self.fixture).default(call)))]


def e2e_crash_rules(fixture: Fixture, crash: Callable[[], None] | None) -> list[Rule]:
    """Three-transition redirect chain, one retryable 503, a mid-body crash."""
    t = SyntheticTransport(fixture)
    canonical_m0 = fixture.plan.source.canonical_url(fixture.op("M-00-head").source_file.file)
    key = fixture.key(fixture.op("M-00-head").source_file.file)
    return [
        Rule(
            at(fixture, "M-00-head", host="huggingface.co"), lambda c: redirect(canonical_m0, 307)
        ),
        Rule(
            at(fixture, "M-00-head", host="huggingface.co"),
            lambda c: redirect(f"{SIGNED_PREFIX}{key}?hop=1"),
        ),
        Rule(
            at(fixture, "M-00-head", host="cas-bridge.xethub.hf.co"),
            lambda c: redirect(f"https://cas-bridge.xethub.hf.co/xet-bridge-eu/{key}?hop=2"),
        ),
        Rule(
            at(fixture, "M-01-footer", host="huggingface.co"),
            lambda c: FakeResponse(503, {}, b"synthetic busy"),
        ),
        Rule(
            at(fixture, "T-01-footer", host="cas-bridge.xethub.hf.co"),
            lambda c: t.ok(c, crash_after=1310720, crash=crash),
        ),
    ]


def _crash_run(fixture_dir: Path, root: Path) -> None:
    fixture = load_fixture(fixture_dir)
    transport = SyntheticTransport(fixture, rules=e2e_crash_rules(fixture, lambda: os._exit(17)))
    run(root, fixture, transport)
    raise SystemExit(0)  # unreachable in the scripted scenario


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "crash-run":
        raise SystemExit("usage: evidence_v4_support.py crash-run <fixture_dir> <root>")
    socket.create_connection = None  # type: ignore[assignment]  # offline: no sockets
    _crash_run(Path(sys.argv[2]), Path(sys.argv[3]))
    print(json.dumps({"unexpected": "completed"}))
