"""Adversarial tests: the plan-bound executor and its transport/body/identity path.

Every test drives the supported public API (``perform_genesis`` /
``execute_phase_p``) over a synthetic epoch with a scripted fake server.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from evidence_v3_support import REPO, block_network, build, execute, genesis, inspect, load_json
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    executor,
    footer,
    frozen_v3,
    journal,
    netpolicy,
    synthetic,
    transport,
)
from xlm.data.evidence_v3.synthetic import FakeResponse


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _ready(tmp_path: Path, **kwargs: Any) -> synthetic.SyntheticEpoch:
    ep = build(tmp_path, **kwargs)
    genesis(ep)
    return ep


def _stop(ep: synthetic.SyntheticEpoch) -> dict[str, Any]:
    with pytest.raises(executor.PhasePStop):
        execute(ep)
    return inspect(ep)


def _attempt(state: dict[str, Any], index: int) -> dict[str, Any]:
    return sorted(state["attempts"], key=lambda a: a["attempt_id"])[index]


def _records(ep: synthetic.SyntheticEpoch, kind: str) -> list[dict[str, Any]]:
    raw = (ep.root / "state/journal.jsonl").read_bytes()
    return [
        r["body"]
        for r in (canonical.loads_bytes_strict(x) for x in raw.splitlines())
        if r["type"] == kind
    ]


# --------------------------------------------------------------------------
# Plan-bound execution
# --------------------------------------------------------------------------


def test_exact_plan_operations_only(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    execute(ep)
    ranges = [c.range_header for c in ep.transport.calls]
    m0 = ep.m_files[0]
    assert ranges[:3] == [
        "bytes=0-3",
        "bytes=0-3",
        f"bytes={m0.length - 8 - m0.footer_length}-{m0.length - 1}",
    ]
    t0 = ep.t_files[0]
    assert f"bytes={t0.length - 8}-{t0.length - 1}" in ranges
    assert f"bytes={t0.length - 8 - t0.footer_length}-{t0.length - 9}" in ranges
    allowed = set()
    for f in ep.m_files:
        allowed |= {"bytes=0-3", f"bytes={f.length - 8 - f.footer_length}-{f.length - 1}"}
    for f in ep.t_files:
        allowed |= {
            "bytes=0-3",
            f"bytes={f.length - 8}-{f.length - 1}",
            f"bytes={f.length - 8 - f.footer_length}-{f.length - 9}",
        }
    assert set(ranges) <= allowed, "no data pages / text chunk ranges are ever requested"
    state = inspect(ep)
    assert state["arms"]["M"]["phase"] == state["arms"]["T"]["phase"] == "P_COMPLETE_SEALED"


def test_swapped_plan_not_bound_by_authorization_refused(tmp_path: Path) -> None:
    ep = build(tmp_path)
    other = synthetic.plan_bytes("M", [synthetic.make_m_file(7)])
    swapped = synthetic.SyntheticEpoch(**{**ep.__dict__, "plan_m": other})
    with pytest.raises(executor.PhasePError, match="plan_digest|child_manifest_digest"):
        executor.check_authorization(**swapped.paths(), harness=swapped.harness())


def test_arbitrary_16_19_range_plan_refused_even_when_resealed(tmp_path: Path) -> None:
    ep = build(tmp_path)
    body = canonical.loads_bytes_strict(ep.plan_m)
    body["payload"]["files"][0]["operations"][0]["range"] = [16, 19]
    body["digest"] = canonical.self_digest(body)
    bad = canonical.canonical_bytes(body)
    auth = synthetic.authorization_body(
        repo=REPO,
        root=ep.root,
        plan_m=ep.plan_m,
        plan_t=ep.plan_t,
        review_bytes=ep.review_path.read_bytes(),
    )
    ep.auth_path.write_bytes(canonical.canonical_bytes(auth))
    tampered = synthetic.SyntheticEpoch(**{**ep.__dict__, "plan_m": bad})
    with pytest.raises(executor.PhasePError, match="identity range"):
        executor.check_authorization(**tampered.paths(), harness=tampered.harness())


def test_caller_cannot_relabel_data_as_footer(tmp_path: Path) -> None:
    ep = build(tmp_path)
    body = canonical.loads_bytes_strict(ep.plan_m)
    ops = body["payload"]["files"][0]["operations"]
    ops[1] = {"op": "footer", "range": [16, 4096], "expected_footer_length": 10}
    body["digest"] = canonical.self_digest(body)
    tampered = synthetic.SyntheticEpoch(
        **{**ep.__dict__, "plan_m": canonical.canonical_bytes(body)}
    )
    with pytest.raises(executor.PhasePError, match="operations must be exactly"):
        executor.check_authorization(**tampered.paths(), harness=tampered.harness())


def test_ledger_reducer_refuses_off_plan_attempt(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    execute(ep, max_operations=0)
    raw = (ep.root / "state/journal.jsonl").read_bytes()
    records = [canonical.loads_bytes_strict(x) for x in raw.splitlines()]
    state = journal.new_state(records[0]["body"], frozen_v3.EPOCH_ID)
    for r in records:
        state.apply(r)
    seq = state.seq
    state.apply({"seq": seq + 1, "type": "SESSION_OPEN", "body": {"session": "adv"}})
    state.apply(
        {
            "seq": seq + 2,
            "type": "PHASE",
            "body": {"arm": "M", "to": "P_RUNNING", "reason": None, "result_digest": None},
        }
    )
    f0 = ep.m_files[0].name
    state.apply(
        {
            "seq": seq + 3,
            "type": "TIME",
            "body": {
                "arm": "M",
                "delta_ns": 0,
                "hold_file": f0,
                "hold_ns": 10**9,
                "step": "request",
            },
        }
    )
    base = {
        "attempt_id": "adv-1",
        "arm": "M",
        "file": f0,
        "op": "IDENTITY_HEAD_0_3",
        "op_index": 0,
        "logical_attempt": 0,
        "hop": 0,
        "target_kind": "canonical",
        "host": "huggingface.co",
        "reservation": 65537,
        "category": "footer",
    }
    for bad in (
        {**base, "range": [16, 19]},
        {**base, "range": [0, 3], "op_index": 1},
        {**base, "range": [0, 3], "file": ep.m_files[1].name},
        {**base, "range": [0, 3], "op": "M_FOOTER_AND_TRAILER"},
        {**base, "range": [0, 3], "reservation": frozen_v3.RESPONSE_BODY_BYTES_MAX + 1},
    ):
        with pytest.raises(journal.StateError):
            state.apply({"seq": seq + 4, "type": "ATTEMPT_RESERVE", "body": bad})


def test_transport_request_cannot_be_forged() -> None:
    with pytest.raises(transport.TransportError, match="executor"):
        transport.TransportRequest(
            url="https://huggingface.co/x",
            host="huggingface.co",
            range_start=0,
            range_end=3,
            deadline_ns=1,
            timeout_seconds=1.0,
            max_read_bytes=10,
            attempt_id="x",
            seal=object(),
        )


# --------------------------------------------------------------------------
# Transport request spec: exact Range and absolute deadline
# --------------------------------------------------------------------------


def test_request_spec_carries_exact_range_and_deadline(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    seen: list[tuple[int, int]] = []
    ep.transport.before_open = lambda _i, r: seen.append((ep.clock.monotonic_ns(), r.deadline_ns))
    execute(ep, max_operations=1)
    first = ep.transport.calls[0]
    assert first.headers()["Range"] == "bytes=0-3"
    assert first.headers()["Accept-Encoding"] == "identity"
    assert first.range_start == 0 and first.range_end == 3
    for now, deadline in seen:
        assert 0 < deadline - now <= 30_000_000_000
    assert all(0 < c.timeout_seconds <= 30 for c in ep.transport.calls)
    assert all(c.max_read_bytes == 65537 for c in ep.transport.calls)


# --------------------------------------------------------------------------
# Redirects come only from actual responses; exact destination policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "http://cas-bridge.xethub.hf.co/x",
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://evil.hf.co/x",
        "https://cdn-lfs.hf.co/x",
        "https://raw.githubusercontent.com/x",
        "https://cas-bridge.xethub.hf.co:444/x",
        "https://user:pw@cas-bridge.xethub.hf.co/x",
        "https://huggingface.co/datasets/synthetic/other/resolve/main/x",
        "https://huggingface.co/other/path?revision=" + "5" * 40,
        "/relative/path",
        "//cas-bridge.xethub.hf.co/x",
    ],
)
def test_actual_redirect_to_bad_destination_refused_and_charged(
    tmp_path: Path, location: str
) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[0] = lambda _r, _resp: FakeResponse(
        302, {"location": location}, b"moved!"
    )
    state = _stop(ep)
    assert len(ep.transport.calls) == 1, "the bad destination is never contacted"
    refused = _attempt(state, 0)
    assert refused["outcome"] == "REFUSED" and refused["charged"] == 6
    assert state["arms"]["M"]["phase"] == "P_INCOMPLETE"


def test_fourth_redirect_refused(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    f0 = ep.m_files[0].name

    def rule(index: int, _r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        if index < 4:
            return FakeResponse(
                302, {"location": synthetic.signed_url(f0, f"hop{index + 1}")}, b"r"
            )
        return resp

    ep.transport.rule = rule
    state = _stop(ep)
    assert len(ep.transport.calls) == 4
    assert [a["outcome"] for a in state["attempts"]] == ["REDIRECT"] * 3 + ["REFUSED"]
    assert state["arms"]["M"]["requests"] == 4


def test_three_redirects_allowed(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    f0 = ep.m_files[0].name

    def rule(index: int, _r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        if index < 3:
            return FakeResponse(
                302, {"location": synthetic.signed_url(f0, f"hop{index + 1}")}, b"r"
            )
        return resp

    ep.transport.rule = rule
    execute(ep, max_operations=1)
    assert inspect(ep)["arms"]["M"]["requests"] == 4


# --------------------------------------------------------------------------
# Streaming body accounting
# --------------------------------------------------------------------------


def test_success_and_redirect_bodies_counted_exactly(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    execute(ep, max_operations=1)
    state = inspect(ep)
    assert state["arms"]["M"]["body_charged"] == len(b"Found. Redirecting") + 4
    body_records = _records(ep, "ATTEMPT_BODY")
    assert [b["total"] for b in body_records] == [18, 4]


def test_invalid_status_body_counted(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[1] = lambda _r, _resp: FakeResponse(500, {}, b"e" * 100)
    state = _stop(ep)
    assert _attempt(state, 1)["charged"] == 100
    assert state["arms"]["M"]["body_charged"] == 18 + 100


def test_partial_short_body_counted_and_refused(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[1] = lambda _r, resp: FakeResponse(206, resp.headers, resp.body[:2])
    state = _stop(ep)
    assert _attempt(state, 1)["charged"] == 2
    assert "identity" in state["arms"]["M"]["incomplete_reason"]


def test_exception_after_bytes_keeps_reservation_then_retries(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def boom(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        resp.chunk_limit = 2
        resp.raise_after_bytes = 2
        return resp

    ep.transport.overrides[1] = boom
    execute(ep, max_operations=1)
    state = inspect(ep)
    failed = _attempt(state, 1)
    assert failed["body_total"] == 2 and failed["charged"] == failed["reservation"] == 65537
    assert ep.clock.sleeps == [1.0], "inherited 1 s backoff before the retry"
    assert state["arms"]["M"]["ops_done"] == 1


def test_uncertain_open_failure_retains_reservation(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def fail(index: int, _r: transport.TransportRequest) -> None:
        if index == 0:
            raise transport.TransportError("connection refused", retryable=True)

    ep.transport.before_open = fail
    execute(ep, max_operations=1)
    first = _attempt(inspect(ep), 0)
    assert first["body_total"] == 0 and first["charged"] == 65537


def test_oversized_redirect_body_cannot_disappear(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[0] = lambda _r, resp: FakeResponse(302, resp.headers, b"x" * 70_000)
    state = _stop(ep)
    first = _attempt(state, 0)
    assert first["body_total"] == 65537
    assert first["charged"] == max(first["reservation"], first["body_total"])
    assert state["arms"]["M"]["body_charged"] >= 65537


def test_transport_overdelivery_is_charged_and_refused(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def over(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        return FakeResponse(206, resp.headers, resp.body + b"x" * 9000, overdeliver=True)

    ep.transport.overrides[1] = over
    state = _stop(ep)
    attempt = _attempt(state, 1)
    assert attempt["body_total"] > 4
    assert attempt["charged"] >= attempt["body_total"]


def test_full_200_response_is_stop(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[1] = lambda _r, _resp: FakeResponse(200, {}, b"P" * 300_000)
    state = _stop(ep)
    assert _attempt(state, 1)["body_total"] == 65537
    assert state["arms"]["M"]["phase"] == "P_INCOMPLETE"


def test_retryable_503_backs_off_then_exhausts(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def rule(index: int, _r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        return FakeResponse(503, {}, b"busy") if resp.status == 206 else resp

    ep.transport.rule = rule
    state = _stop(ep)
    assert ep.clock.sleeps == [1.0, 2.0]
    assert state["arms"]["M"]["requests"] == 6
    assert "retries exhausted" in state["arms"]["M"]["incomplete_reason"]


# --------------------------------------------------------------------------
# Exact remote identity
# --------------------------------------------------------------------------


def _mutate_206(ep: synthetic.SyntheticEpoch, **headers: str) -> None:
    def change(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        merged = {**resp.headers, **headers}
        return FakeResponse(
            resp.status, {k: v for k, v in merged.items() if v != "<drop>"}, resp.body
        )

    ep.transport.overrides[1] = change


@pytest.mark.parametrize(
    "headers",
    [
        {"etag": 'W/"weak"'},
        {"etag": "<drop>"},
        {"etag": '"0000"'},
        {"content-range": "bytes 0-4/1000"},
        {"content-range": "bytes 1-3/1000"},
        {"content-range": "<drop>"},
        {"content-range": "bytes 0-3/*"},
        {"content-encoding": "gzip"},
        {"content-length": "5"},
    ],
)
def test_identity_mismatch_is_stop_with_bytes_kept(tmp_path: Path, headers: dict[str, str]) -> None:
    ep = _ready(tmp_path)
    if "content-range" in headers and headers["content-range"].endswith("/1000"):
        headers = {
            "content-range": headers["content-range"].replace("1000", str(ep.m_files[0].length))
        }
    _mutate_206(ep, **headers)
    state = _stop(ep)
    assert _attempt(state, 1)["charged"] == 4
    assert "identity" in state["arms"]["M"]["incomplete_reason"]


def test_total_length_mismatch_is_stop(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    _mutate_206(ep, **{"content-range": f"bytes 0-3/{ep.m_files[0].length + 1}"})
    state = _stop(ep)
    assert "total" in state["arms"]["M"]["incomplete_reason"]


def test_long_206_body_refused(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[1] = lambda _r, resp: FakeResponse(206, resp.headers, resp.body + b"!!")
    state = _stop(ep)
    assert _attempt(state, 1)["body_total"] == 5


def test_wrong_magic_is_stop(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[1] = lambda _r, resp: FakeResponse(206, resp.headers, b"PAR2")
    state = _stop(ep)
    assert "PAR1" in state["arms"]["M"]["incomplete_reason"]


def test_weak_expected_etag_cannot_be_planned(tmp_path: Path) -> None:
    ep = build(tmp_path)
    body = canonical.loads_bytes_strict(ep.plan_m)
    body["payload"]["files"][0]["strong_etag"] = 'W/"abc"'
    body["digest"] = canonical.self_digest(body)
    tampered = synthetic.SyntheticEpoch(
        **{**ep.__dict__, "plan_m": canonical.canonical_bytes(body)}
    )
    with pytest.raises(executor.PhasePError, match="strong ETag"):
        executor.check_authorization(**tampered.paths(), harness=tampered.harness())


def test_structural_resource_identity() -> None:
    src = netpolicy.FROZEN_SOURCE
    file = "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
    good = netpolicy.check_url(src.canonical_url(file))
    assert netpolicy.is_canonical_resource(good, src, file)
    decoy = netpolicy.check_url(
        f"https://huggingface.co/datasets/EssentialAI/other/resolve/main/{file}?rev={src.revision}"
    )
    assert not netpolicy.is_canonical_resource(decoy, src, file)
    with pytest.raises(netpolicy.PolicyError):
        netpolicy.classify_target(decoy, src, file)
    with pytest.raises(netpolicy.PolicyError):
        netpolicy.classify_target(netpolicy.check_url(good.url + "?x=1"), src, file)
    other_file = netpolicy.check_url(src.canonical_url(file.replace("01860", "01861")))
    assert not netpolicy.is_canonical_resource(other_file, src, file)
    for bad in (
        "HTTPS://huggingface.co/x",
        "https://HuggingFace.co/x",
        "https://huggingface.co./x",
    ):
        with pytest.raises(netpolicy.PolicyError):
            netpolicy.check_url(bad)


# --------------------------------------------------------------------------
# Runtime: durable elapsed, deadlines, correct file key
# --------------------------------------------------------------------------


def test_31s_open_request_times_out_and_is_charged(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def slow(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        resp.on_chunk = lambda _pos: ep.clock.advance(31)
        return resp

    ep.transport.overrides[0] = slow
    execute(ep, max_operations=1)
    state = inspect(ep)
    first = _attempt(state, 0)
    assert first["outcome"] == "FAILED" and first["charged"] == first["reservation"]
    f0 = ep.m_files[0].name
    assert state["arms"]["M"]["time_ns_by_file"][f0] >= 31_000_000_000
    assert state["arms"]["M"]["time_ns"] >= 31_000_000_000


def test_elapsed_survives_reload(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.before_open = lambda _i, _r: ep.clock.advance(2)
    execute(ep, max_operations=1)
    first = inspect(ep)["arms"]["M"]["time_ns"]
    assert first >= 4_000_000_000
    execute(ep, max_operations=1)
    assert inspect(ep)["arms"]["M"]["time_ns"] > first


def test_exhausted_file_budget_never_reaches_transport(tmp_path: Path) -> None:
    ep = _ready(tmp_path)

    def burn(index: int, _r: transport.TransportRequest) -> None:
        if index == 1:
            ep.clock.advance(600)

    ep.transport.before_open = burn
    state = _stop(ep)
    assert len(ep.transport.calls) == 2
    f0 = ep.m_files[0].name
    assert state["arms"]["M"]["time_ns_by_file"][f0] >= 600_000_000_000
    assert "runtime budget exhausted" in state["arms"]["M"]["incomplete_reason"]
    assert set(state["arms"]["M"]["time_ns_by_file"]) == {f0}, "time charged to THAT file only"


def test_staging_and_parse_are_inside_active_time(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    execute(ep)
    steps = [t for t in _records(ep, "TIME")]
    assert any(t["step"] == "work" and t["hold_file"] is not None for t in steps), "staging"
    assert any(t["step"] == "work" and t["hold_file"] is None for t in steps), "seal/parse"


def test_pause_cannot_bypass_open_request(tmp_path: Path) -> None:
    ep = _ready(tmp_path)
    ep.transport.overrides[0] = lambda _r, resp: FakeResponse(
        302, resp.headers, resp.body, crash_after_bytes=0
    )
    with pytest.raises(synthetic.SimulatedCrash):
        execute(ep)
    raw = (ep.root / "state/journal.jsonl").read_bytes()
    records = [canonical.loads_bytes_strict(x) for x in raw.splitlines()]
    state = journal.new_state(records[0]["body"], frozen_v3.EPOCH_ID)
    for r in records:
        state.apply(r)
    with pytest.raises(journal.StateError, match="open work|time hold"):
        state.apply(
            {"seq": state.seq + 1, "type": "SESSION_CLOSE", "body": {"session": state.session}}
        )


# --------------------------------------------------------------------------
# Future-D reservation: P may not consume D-reserved controls
# --------------------------------------------------------------------------


def _chain_rule(ep: synthetic.SyntheticEpoch, extra_503: dict[str, int]) -> None:
    """Identity: 3 redirects (4 physical). Footer: N x 503 then success."""
    seen: dict[str, int] = {}

    def rule(_index: int, req: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        checked = netpolicy.check_url(req.url)
        for f in ep.m_files:
            if req.range_header == "bytes=0-3" and netpolicy.is_canonical_resource(
                checked, synthetic.SYNTHETIC_SOURCE, f.name
            ):
                return FakeResponse(302, {"location": synthetic.signed_url(f.name, "hop1")}, b"r")
            for hop in (1, 2):
                if req.url == synthetic.signed_url(f.name, f"hop{hop}"):
                    return FakeResponse(
                        302, {"location": synthetic.signed_url(f.name, f"hop{hop + 1}")}, b"r"
                    )
            if req.range_header != "bytes=0-3" and req.url.startswith(
                synthetic.signed_url(f.name, "hop3")[:80]
            ):
                count = seen.get(f.name, 0)
                seen[f.name] = count + 1
                if count < extra_503.get(f.name, 0):
                    return FakeResponse(503, {}, b"busy")
        return resp

    ep.transport.rule = rule


def test_m_48th_p_request_permitted_while_d_reserve_retained(tmp_path: Path) -> None:
    ep = _ready(tmp_path, m_count=8, t_count=1)
    _chain_rule(ep, {f.name: 1 for f in ep.m_files})
    execute(ep, max_operations=16)
    state = inspect(ep)["arms"]["M"]
    assert state["requests"] == 48
    assert all(v == 6 for v in state["requests_by_file"].values())
    assert state["phase"] == "P_COMPLETE_SEALED"


def test_m_49th_p_request_refused_to_preserve_d_reserve(tmp_path: Path) -> None:
    ep = _ready(tmp_path, m_count=8, t_count=1)
    extra = {f.name: 1 for f in ep.m_files}
    extra[ep.m_files[-1].name] = 2
    _chain_rule(ep, extra)
    state = _stop(ep)["arms"]["M"]
    assert state["requests"] == 48, "the 49th physical request is never reserved"
    assert len(ep.transport.calls) == 48
    assert "request ceiling" in state["incomplete_reason"]


def test_m_per_file_ceiling_12_retains_4_for_d(tmp_path: Path) -> None:
    ep = _ready(tmp_path, m_count=1, t_count=1)
    f0 = ep.m_files[0].name

    def rule(_index: int, req: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        checked = netpolicy.check_url(req.url)
        if netpolicy.is_canonical_resource(checked, synthetic.SYNTHETIC_SOURCE, f0):
            return FakeResponse(302, {"location": synthetic.signed_url(f0, "hop1")}, b"r")
        for hop in (1, 2):
            if req.url == synthetic.signed_url(f0, f"hop{hop}"):
                return FakeResponse(
                    302, {"location": synthetic.signed_url(f0, f"hop{hop + 1}")}, b"r"
                )
        if req.range_header == "bytes=0-3":
            return FakeResponse(503, {}, b"busy") if len(rule_calls) < 2 else resp
        return resp

    rule_calls: list[int] = []

    def counting(index: int, req: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        out = rule(index, req, resp)
        if out.status == 503:
            rule_calls.append(index)
        return out

    ep.transport.rule = counting
    ep.transport.overrides[12] = lambda _r, _resp: FakeResponse(503, {}, b"busy")
    state = _stop(ep)["arms"]["M"]
    assert state["requests_by_file"][f0] == 12
    assert "request ceiling" in state["incomplete_reason"]


def test_t_reserved_control_ceiling(tmp_path: Path) -> None:
    ep = _ready(tmp_path, m_count=1, t_count=1, t_data_ranges=90)
    plan_t = load_json(ep.inputs / "authorization.json")
    assert plan_t["t_phase_p_plan_digest"]
    t0 = ep.t_files[0].name

    def rule(_index: int, req: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        checked = netpolicy.check_url(req.url)
        if netpolicy.is_canonical_resource(checked, synthetic.SYNTHETIC_SOURCE, t0):
            return FakeResponse(302, {"location": synthetic.signed_url(t0, "hop1")}, b"r")
        for hop in (1, 2):
            if req.url == synthetic.signed_url(t0, f"hop{hop}"):
                return FakeResponse(
                    302, {"location": synthetic.signed_url(t0, f"hop{hop + 1}")}, b"r"
                )
        return resp

    ep.transport.rule = rule
    execute(ep)  # 4 + 1 + 1 = 6 == T per-file P ceiling (100 - 4 - 90)
    assert inspect(ep)["arms"]["T"]["requests_by_file"][t0] == 6

    ep2 = _ready(tmp_path / "b", m_count=1, t_count=1, t_data_ranges=90)
    t0b = ep2.t_files[0].name
    calls: list[int] = []

    def rule2(index: int, req: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        out = rule(index, req, resp) if t0b == t0 else resp
        if (
            req.range_header.startswith("bytes=")
            and req.range_header != "bytes=0-3"
            and "SYN-T" not in req.url
        ):
            if (
                resp.status == 206
                and int(req.range_header.split("-")[-1]) == ep2.t_files[0].length - 9
            ):
                if not calls:
                    calls.append(index)
                    return FakeResponse(503, {}, b"busy")
        return out

    ep2.transport.rule = rule2
    state = _stop(ep2)["arms"]["T"]
    assert state["requests_by_file"][t0b] == 6
    assert "request ceiling" in state["incomplete_reason"]


# --------------------------------------------------------------------------
# Memory supervision during work
# --------------------------------------------------------------------------


def test_transient_overcap_during_body_cancels(tmp_path: Path) -> None:
    breach = threading.Event()
    sampled = threading.Event()

    def reader() -> int:
        if breach.is_set():
            sampled.set()
            return frozen_v3.MEMORY_RESIDENT_BYTES_MAX + 1
        return 32 * 1024 * 1024

    ep = _ready(tmp_path, memory_reader=reader)

    def trigger(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        def on_chunk(pos: int) -> None:
            if pos >= 1:
                breach.set()
                assert sampled.wait(10), "supervisor thread must sample during work"

        resp.chunk_limit = 1
        resp.on_chunk = on_chunk
        return resp

    ep.transport.overrides[1] = trigger
    state = _stop(ep)
    attempt = _attempt(state, 1)
    assert attempt["body_total"] >= 1 and attempt["charged"] == attempt["reservation"]
    assert "memory" in state["arms"]["M"]["incomplete_reason"]


def test_memory_measurement_failure_aborts(tmp_path: Path) -> None:
    broken = threading.Event()
    observed = threading.Event()

    def reader() -> int:
        if broken.is_set():
            observed.set()
            raise PermissionError("access denied to child process")
        return 1024

    ep = _ready(tmp_path, memory_reader=reader)

    def fail_measurement(_index: int, _r: transport.TransportRequest) -> None:
        broken.set()
        assert observed.wait(10), "supervisor thread must observe the failure during work"

    ep.transport.before_open = fail_measurement
    state = _stop(ep)
    assert "memory" in state["arms"]["M"]["incomplete_reason"]


def test_memory_unavailable_at_start_refuses_before_network(tmp_path: Path) -> None:
    def reader() -> int:
        raise PermissionError("no access")

    ep = _ready(tmp_path, memory_reader=reader)
    with pytest.raises(executor.PhasePError, match="memory supervision unavailable"):
        execute(ep)
    assert ep.transport.calls == []


def test_parser_input_is_bounded() -> None:
    big = b"\0" * frozen_v3.RESPONSE_BODY_BYTES_MAX
    trailer = len(big).to_bytes(4, "little") + b"PAR1"
    with pytest.raises(footer.FooterError, match="4 MiB"):
        footer.compare_t(big, trailer, footer_start=4, bindings={}, check=lambda: None)
    buffer = executor._BoundedBuffer(4)
    buffer.extend(b"1234")
    with pytest.raises(executor._Stop):
        buffer.extend(b"5")
