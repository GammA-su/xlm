"""Independent offline safety assertions against commit B; failures are findings.

Only authored fixtures and disposable roots. No production code is modified.
"""
from __future__ import annotations

import hashlib
import socket
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    authorization, executor, fsroot, genesis, memory, readiness, synthetic, transport,
)

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("NO NETWORK: socket/DNS blocked by independent review")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)


def epoch(tmp_path: Path) -> synthetic.SyntheticEpoch:
    ep = synthetic.build_epoch(REPO, tmp_path, m_count=1, t_count=1)
    executor.perform_genesis(**ep.paths(), harness=ep.harness())
    return ep


def run(ep: synthetic.SyntheticEpoch, **kwargs: Any) -> dict[str, Any]:
    return executor.execute_phase_p(**ep.paths(), harness=ep.harness(), **kwargs)


def test_eof_after_31_seconds_must_not_complete_operation(tmp_path: Path) -> None:
    ep = epoch(tmp_path)
    def slow_eof(_req: Any, resp: synthetic.FakeResponse) -> synthetic.FakeResponse:
        resp.on_chunk = lambda pos: ep.clock.advance(31) if pos == len(resp.body) else None
        return resp
    ep.transport.overrides[1] = slow_eof
    refused = False
    try:
        result = run(ep, max_operations=1)
    except executor.PhasePError:
        refused = True
        result = {}
    state = executor.inspect_epoch(ep.root)
    print({"result": result, "M": state["arms"]["M"]})
    assert refused, "31-second EOF read completed and staged the operation"


@pytest.mark.parametrize("breach", ["time", "memory"])
def test_final_write_breach_must_not_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breach: str) -> None:
    ep = epoch(tmp_path)
    original = fsroot.RootFS.create_exclusive
    supervisor: list[memory.Supervisor] = []
    original_start = memory.Supervisor.start
    def start(self: memory.Supervisor) -> None:
        supervisor.append(self)
        original_start(self)
    monkeypatch.setattr(memory.Supervisor, "start", start)
    def delayed(self: fsroot.RootFS, rel: str, data: bytes) -> tuple[int, str]:
        result = original(self, rel, data)
        if rel == "phase_p/t/result.json.tmp":
            if breach == "time":
                ep.clock.advance(1801)
            else:
                # The real sampler's breach path, injected while the final write is active.
                monkeypatch.setattr(supervisor[-1], "_reader", lambda: 268435457)
                supervisor[-1].sample_once()
        return result
    monkeypatch.setattr(fsroot.RootFS, "create_exclusive", delayed)
    refused = False
    try:
        result = run(ep)
    except executor.PhasePError:
        refused = True
        result = {}
    state = executor.inspect_epoch(ep.root)
    print({"result": result, "T": state["arms"]["T"], "abort": supervisor[-1].abort_reason})
    assert refused, f"final {breach} breach was sealed as success"


def test_seal_parse_time_must_be_charged_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ep = epoch(tmp_path)
    original = executor.footer.compare_m
    def timed(*args: Any, **kwargs: Any) -> Any:
        ep.clock.advance(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(executor.footer, "compare_m", timed)
    run(ep)
    state = executor.inspect_epoch(ep.root)["arms"]["M"]
    print(state)
    assert state["time_ns_by_file"].get(ep.m_files[0].name, 0) >= 2_000_000_000


def test_minted_nested_fields_cannot_change_genesis(tmp_path: Path) -> None:
    ep = synthetic.build_epoch(REPO, tmp_path, m_count=1, t_count=1)
    expect = authorization.derive_expectations(
        repo=REPO, root=ep.root, review_path=ep.review_path,
        review_bytes=ep.review_path.read_bytes(), harness=ep.harness(),
    )
    auth = authorization.validate_authorization(ep.auth_path.read_bytes(), expect)
    approval = authorization.validate_operator_approval(ep.approval_path.read_bytes(), auth)
    refused = False
    try:
        auth.environment["python_version"] = "invented-after-validation"
        auth.plans["M"].arm_ceiling["requests"] = 999
        root = genesis.verify_root(ep.root, mode="SYNTHETIC", create=True)
        genesis.publish(auth=auth, approval=approval, root_ctx=root,
                        utc_now=ep.clock.utc_now(), monotonic_ns=ep.clock.monotonic_ns(),
                        sampling_interval_s=0.01)
    except (TypeError, RuntimeError):
        refused = True
    if not refused:
        body = canonical.loads_bytes_strict((ep.root / "epoch_start.json").read_bytes())
        print({"python": body["environment_identity"]["python_version"],
               "M_requests": body["p_ceilings"]["M"]["arm"]["requests"],
               "same_authorization": body["authorization_digest"] == auth.digest})
    assert refused, "public validators -> nested mutation -> public genesis.publish succeeded"


def test_inspect_must_detect_journal_tail_rollback(tmp_path: Path) -> None:
    ep = epoch(tmp_path)
    original = (ep.root / "state/journal.jsonl").read_bytes()
    run(ep, max_operations=1)
    (ep.root / "state/journal.jsonl").write_bytes(original)
    refused = False
    try:
        state = executor.inspect_epoch(ep.root)
        print({"reported_M_requests_after_rollback": state["arms"]["M"]["requests"]})
    except (executor.PhasePError, RuntimeError):
        refused = True
    # The execution path correctly rejects this; the public inspector does not.
    with pytest.raises(executor.PhasePError):
        run(ep)
    assert refused, "public inspector accepts rollback that the execution loader rejects"


def test_live_transport_must_not_send_after_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0]
    events: list[tuple[str, float]] = []
    class Sock:
        def settimeout(self, value: float) -> None:
            events.append(("timeout", value))
        def connect(self, address: Any) -> None:
            now[0] += 20_000_000_000
        def close(self) -> None:
            pass
    class TLS:
        def wrap_socket(self, raw: Any, **kwargs: Any) -> Any:
            now[0] += 20_000_000_000
            return raw
    class Response:
        status = 206
        def getheaders(self) -> list[tuple[str, str]]:
            return []
        def close(self) -> None:
            pass
    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
        def putrequest(self, *args: Any, **kwargs: Any) -> None:
            events.append(("GET", now[0] / 1e9))
        def putheader(self, *args: Any) -> None:
            pass
        def endheaders(self) -> None:
            events.append(("SEND", now[0] / 1e9))
        def getresponse(self) -> Response:
            return Response()
    live = transport._LiveHttpsTransport()
    monkeypatch.setattr(live, "_context", TLS())
    monkeypatch.setattr(transport.time, "monotonic_ns", lambda: now[0])
    monkeypatch.setattr(transport, "_global_addresses", lambda *args: [(socket.AF_INET, "203.0.113.1")])
    monkeypatch.setattr(transport.socket, "socket", lambda *args: Sock())
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", Connection)
    request = transport._make_request(url="https://huggingface.co/test", range_start=0, range_end=3,
                                     deadline_ns=30_000_000_000, timeout_seconds=30,
                                     max_read_bytes=65537, attempt_id="offline-test")
    transport._mark_issued(request)
    try:
        live.open(request).close()
    except transport.DeadlineExceeded:
        pass
    print(events)
    assert not any(name == "SEND" and value > 30 for name, value in events)


def test_readiness_must_detect_disabled_arms_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = authorization._same
    def ignore_arms(got: Any, want: Any, what: str) -> None:
        if what != "arms":
            original(got, want, what)
    monkeypatch.setattr(authorization, "_same", ignore_arms)
    ep = synthetic.build_epoch(REPO, tmp_path / "unsafe", m_count=1, t_count=1)
    body = canonical.loads_bytes_strict(ep.auth_path.read_bytes())
    body["arms"] = "MT"
    review = synthetic.authorization_review_bytes(body)
    body["review_artifact_digest"] = hashlib.sha256(review).hexdigest()
    raw = synthetic.reseal(body)
    ep.auth_path.write_bytes(raw)
    ep.review_path.write_bytes(review)
    digest = canonical.loads_bytes_strict(raw)["digest"]
    ep.approval_path.write_bytes(canonical.canonical_bytes(synthetic.approval_body(digest)))
    executor.perform_genesis(**ep.paths(), harness=ep.harness())
    result = run(ep, max_operations=1)
    assert result["executed_operations"] == 1
    derived = readiness.derive_readiness(REPO, "3dd5ebce0edb7d8e676966c9195b73fb5a1978c9")
    print({"unsafe_executed": result, "readiness_verdict": derived["verdict"], "blocked": derived["blocked"]})
    assert derived["verdict"] == "BLOCKED", "readiness stayed READY with disabled arms guard"
