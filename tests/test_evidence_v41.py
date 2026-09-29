"""Evidence-v4.1 transport-host amendment (offline).

v4.1 is the v4.0 engine with the exact expanded Hugging Face storage/CDN host
set, a fresh version and a fresh root. These tests cover the exact allowlist,
authored CDN redirect replays through the engine and through the production
HTTP parser (including B01/B02 on the CDN hop), the unchanged plan membership
and ranges, the live entry point and the CLI surface. Everything is synthetic
or committed repository bytes: sockets are patched to refuse, and no root
outside ``tmp_path`` is created.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from evidence_v4_support import (
    LAST_CHUNK,
    REDIRECT_BODY,
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
    redirect,
    response_head,
    run,
    wire_of,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_p, state, v41
from xlm.data.evidence_v4 import transport as tp

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import evidence_v41 as cli  # noqa: E402

OP = "M-00-head"
CDN = "us.aws.cdn.hf.co"
EXACT_HOSTS = (
    "huggingface.co",
    "cas-bridge.xethub.hf.co",
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "transfer.xethub.hf.co",
    "transfer.xethub-eu.hf.co",
    "aws.cdn.hf.co",
    "us.aws.cdn.hf.co",
    "us-east-1.aws.cdn.hf.co",
    "us-west-2.aws.cdn.hf.co",
    "eu-west-3.aws.cdn.hf.co",
    "ap-southeast-1.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "us-east1.us.gcp.cdn.hf.co",
    "us-central1.us.gcp.cdn.hf.co",
    "us-west4.us.gcp.cdn.hf.co",
    "europe-west4.us.gcp.cdn.hf.co",
    "asia-southeast1.us.gcp.cdn.hf.co",
)
UNLISTED_HOSTS = (
    "evil.hf.co",
    "foo.us.aws.cdn.hf.co",
    "us.aws.cdn.hf.co.evil.com",
    "hf.co",
    "cdn.hf.co",
    "xethub.hf.co",
    "eu.aws.cdn.hf.co",
    "cdn-lfs-ap-1.hf.co",
    "us.aws.cdn.hf.co.",
    "huggingface.co.evil.com",
)
SOURCE = frozen.REAL_SOURCE
FILE = "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
CANONICAL = SOURCE.canonical_url(FILE)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture(scope="module")
def fx41(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    return build_fixture(tmp_path_factory.mktemp("v41") / "fixture", profile=v41.PROFILE)


@pytest.fixture(scope="module")
def fx40(tmp_path_factory: pytest.TempPathFactory) -> Fixture:
    return build_fixture(tmp_path_factory.mktemp("v40") / "fixture")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def cdn_location(fx: Fixture, call: Call, host: str) -> str:
    key = fx.key(SyntheticTransport(fx).file_of(call))
    return f"https://{host}/repos/5e/1a/{key}?Expires=1&Policy=p&Signature=s&Key-Pair-Id=k"


def origin_redirects_to(fx: Fixture, host: str) -> Rule:
    """Every canonical-origin request answers 302 to ``host`` (opaque signed CDN path)."""
    return Rule(
        lambda c: c.host == "huggingface.co",
        lambda c: redirect(cdn_location(fx, c, host)),
        times=10**6,
    )


def wire_origin_redirects_to(fx: Fixture, host: str) -> WireRule:
    return WireRule(
        lambda c: c.host == "huggingface.co",
        lambda c: [(0.0, wire_of(redirect(cdn_location(fx, c, host))))],
        times=10**6,
    )


def attempts(root: Path) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM attempts ORDER BY attempt_id")]
    finally:
        conn.close()


def run_row(root: Path) -> state.RunRow:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return state.read_run(conn)
    finally:
        conn.close()


# -- exact host allowlist ---------------------------------------------------


def test_the_frozen_host_set_is_exactly_the_19_listed_hosts() -> None:
    assert v41.HOSTS.hosts == EXACT_HOSTS
    assert v41.HOSTS.canonical_host == "huggingface.co"
    assert v41.HOSTS.signed_target_hosts == EXACT_HOSTS[1:]
    assert v41.ADDED_HOSTS == EXACT_HOSTS[2:] and len(v41.ADDED_HOSTS) == 17
    assert len(set(EXACT_HOSTS)) == 19
    assert all(h == h.lower() and "*" not in h and not h.startswith(".") for h in EXACT_HOSTS)
    assert v41.NETWORK["hosts"] == list(EXACT_HOSTS)
    assert v41.NETWORK["signed_target_hosts"] == list(EXACT_HOSTS[1:])
    assert (v41.NETWORK["scheme"], v41.NETWORK["port"]) == ("https", 443)


def test_the_v40_policy_is_unchanged() -> None:
    assert frozen.V40_HOSTS.hosts == ("huggingface.co", "cas-bridge.xethub.hf.co")
    assert frozen.ALLOWED_HOSTS == frozen.V40_HOSTS.hosts
    assert frozen.NETWORK["hosts"] == ["huggingface.co", "cas-bridge.xethub.hf.co"]
    assert frozen.V40.hosts is frozen.V40_HOSTS and frozen.V40.network is frozen.NETWORK


@pytest.mark.parametrize("host", EXACT_HOSTS)
def test_every_exact_frozen_host_is_accepted(host: str) -> None:
    checked = tp.check_url(f"https://{host}/repos/x/y?Signature=s", v41.HOSTS)
    assert (checked.host, checked.path, checked.query) == (host, "/repos/x/y", "Signature=s")
    assert tp.check_url(f"https://{host}:443/z", v41.HOSTS).host == host


@pytest.mark.parametrize("host", v41.ADDED_HOSTS)
def test_the_v40_policy_still_refuses_every_added_host(host: str) -> None:
    with pytest.raises(tp.PolicyError, match="not an exact allowlisted host"):
        tp.check_url(f"https://{host}/x")


@pytest.mark.parametrize("host", UNLISTED_HOSTS)
def test_unlisted_and_lookalike_hosts_are_refused(host: str) -> None:
    with pytest.raises(tp.PolicyError):
        tp.check_url(f"https://{host}/x", v41.HOSTS)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (f"http://{CDN}/x", "scheme"),
        (f"https://{CDN}:444/x", "port 444"),
        (f"https://{CDN}:80/x", "port 80"),
        ("https://18.160.10.1/x", "IP literal"),
        ("https://[2600:9000::1]/x", "exactly spelled"),
        ("https://3.5.6.7:443/x", "IP literal"),
        (f"https://user@{CDN}/x", "userinfo"),
        (f"https://{CDN}/x#frag", "fragment"),
        ("https://US.AWS.CDN.HF.CO/x", "lowercase"),
        (f"ftp://{CDN}/x", "scheme"),
    ],
)
def test_scheme_port_ip_and_spelling_refusals_are_intact(url: str, message: str) -> None:
    with pytest.raises(tp.PolicyError, match=message):
        tp.check_url(url, v41.HOSTS)


@pytest.mark.parametrize("host", v41.HOSTS.signed_target_hosts)
def test_redirect_from_the_origin_to_each_signed_target_is_accepted(host: str) -> None:
    start = tp.start_url(SOURCE, FILE, v41.HOSTS)
    target = tp.resolve_redirect(start, f"https://{host}/opaque?s=1", SOURCE, FILE, v41.HOSTS)
    assert target.host == host


def test_redirect_rules_other_than_the_host_set_are_unchanged() -> None:
    start = tp.start_url(SOURCE, FILE, v41.HOSTS)
    assert tp.resolve_redirect(start, CANONICAL, SOURCE, FILE, v41.HOSTS).path == start.path
    for location in (
        "https://huggingface.co/datasets/other/repo/resolve/main/x.parquet",
        f"{CANONICAL}?revision=main",
        "https://eu.aws.cdn.hf.co/opaque",
        "https://evil.hf.co/opaque",
        None,
        "",
    ):
        with pytest.raises(tp.PolicyError):
            tp.resolve_redirect(start, location, SOURCE, FILE, v41.HOSTS)
    with pytest.raises(tp.PolicyError, match="not an exact allowlisted host"):
        tp.resolve_redirect(tp.start_url(SOURCE, FILE), f"https://{CDN}/o", SOURCE, FILE)


# -- identity for CDN-delivered bytes (unweakened) --------------------------

N = 290684578
ETAG = '"deadbeef"'


def _identity(**overrides: Any) -> None:
    args: dict[str, Any] = {
        "status": 206,
        "headers": {"content-range": f"bytes 0-3/{N}", "etag": ETAG, "content-length": "4"},
        "body": b"PAR1",
        "final_url": tp.check_url(f"https://{CDN}/repos/5e/1a/abc?Signature=s", v41.HOSTS),
        "source": SOURCE,
        "file": FILE,
        "expected": tp.Expected(0, 3, N, ETAG, "equal"),
        "policy": v41.HOSTS,
    }
    args.update(overrides)
    tp.verify_identity(**args)


def test_valid_cdn_response_passes_identity() -> None:
    _identity()


def test_cdn_final_url_is_not_identity_under_the_v40_policy() -> None:
    with pytest.raises(tp.IdentityError, match="neither the canonical path nor the signed"):
        _identity(policy=frozen.V40_HOSTS)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": 200},
        {"headers": {"content-range": f"bytes 0-3/{N + 1}", "etag": ETAG}},
        {"headers": {"content-range": f"bytes 1-4/{N}", "etag": ETAG}},
        {"headers": {"content-range": f"bytes 0-3/{N}", "etag": 'W/"deadbeef"'}},
        {"headers": {"content-range": f"bytes 0-3/{N}", "etag": '"other"'}},
        {"headers": {"content-range": f"bytes 0-3/{N}"}},
        {"headers": {"etag": ETAG}},
        {"headers": {"content-range": f"bytes 0-3/{N}", "etag": ETAG, "content-length": "5"}},
        {"headers": {"content-range": f"bytes 0-3/{N}", "etag": ETAG, "content-encoding": "gzip"}},
        {"body": b"PAR"},
        {"body": b"PAR2"},
    ],
    ids=[
        "status-200",
        "wrong-total",
        "wrong-range",
        "weak-etag",
        "other-etag",
        "no-etag",
        "no-content-range",
        "content-length",
        "gzip",
        "short-body",
        "not-par1",
    ],
)
def test_cdn_identity_checks_are_not_weakened(overrides: dict[str, Any]) -> None:
    with pytest.raises(tp.IdentityError):
        _identity(**overrides)


# -- authored engine replays --------------------------------------------------


@pytest.mark.parametrize("host", v41.HOSTS.signed_target_hosts)
def test_origin_to_cdn_redirect_replay_completes(fx41: Fixture, root: Path, host: str) -> None:
    transport = SyntheticTransport(fx41, rules=[origin_redirects_to(fx41, host)])
    result = run(root, fx41, transport)
    assert result.status == "COMPLETE" and result.run_status == "COMPLETE"
    assert {c.host for c in transport.calls} == {"huggingface.co", host}
    table = attempts(root)
    assert [(a["hop"], a["url_host"], a["outcome"]) for a in table] == [
        (0, "huggingface.co", state.REDIRECT),
        (1, host, state.SUCCESS),
    ] * len(fx41.plan.operations)
    assert all(a["url_query_sha256"] for a in table if a["hop"] == 1)
    assert all("Signature" not in json.dumps(a) for a in table)
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["protocol_version"] == v41.PROTOCOL_VERSION
    assert receipt["protocol_sha256"] == v41.PROTOCOL_SHA256
    assert receipt["freeze_digest"] == v41.FREEZE_DIGEST
    assert receipt["plan_digest"] == fx41.plan.digest
    assert run_row(root).protocol_version == v41.PROTOCOL_VERSION
    head = (root / state.payload_name(OP)).read_bytes()
    assert head == b"PAR1"


@pytest.mark.parametrize("host", UNLISTED_HOSTS)
def test_same_response_redirected_to_an_unlisted_host_stops_policy_refused(
    fx41: Fixture, root: Path, host: str
) -> None:
    transport = SyntheticTransport(fx41, rules=[origin_redirects_to(fx41, host)])
    result = run(root, fx41, transport)
    assert (result.status, result.run_status) == ("INCOMPLETE", "STOPPED")
    assert result.stop_reason is not None and result.stop_reason.startswith(f"{OP}: ")
    assert [c.host for c in transport.calls] == ["huggingface.co"]
    [row] = attempts(root)
    assert (row["op_id"], row["hop"], row["outcome"], row["http_status"]) == (
        OP,
        0,
        state.POLICY_REFUSED,
        302,
    )
    assert row["response_bytes"] == len(REDIRECT_BODY)
    m, t = result.totals["M"], result.totals["T"]
    assert (m["logical_complete"], m["physical_attempts"], m["retained_payload_bytes"]) == (0, 1, 0)
    assert m["attempts_by_outcome"] == {state.POLICY_REFUSED: 1}
    assert (t["physical_attempts"], t["retained_payload_bytes"]) == (0, 0)
    assert list((root / "payload").iterdir()) == []


@pytest.mark.parametrize(
    ("location", "message"),
    [
        (f"http://{CDN}/o", "scheme 'http' is not https"),
        (f"https://{CDN}:444/o", "port 444 is not 443"),
        ("https://18.160.10.1/o", "IP literals are forbidden"),
    ],
)
def test_cdn_redirect_with_bad_scheme_port_or_ip_stops(
    fx41: Fixture, root: Path, location: str, message: str
) -> None:
    rule = Rule(at(fx41, OP, host="huggingface.co"), lambda c: redirect(location))
    result = run(root, fx41, SyntheticTransport(fx41, rules=[rule]))
    assert result.run_status == "STOPPED" and result.stop_reason == f"{OP}: {message}"
    assert [a["outcome"] for a in attempts(root)] == [state.POLICY_REFUSED]


def test_v40_profile_reproduces_the_observed_live_stop_offline(fx40: Fixture, root: Path) -> None:
    """The v4.0 engine refuses the CDN hop exactly as the real v4.0 run reported."""
    transport = SyntheticTransport(fx40, rules=[origin_redirects_to(fx40, CDN)])
    result = run(root, fx40, transport)
    assert (result.status, result.run_status) == ("INCOMPLETE", "STOPPED")
    assert result.stop_reason == f"{OP}: host '{CDN}' is not an exact allowlisted host"
    m, t = result.totals["M"], result.totals["T"]
    assert m["logical_complete"] == 0 and m["physical_attempts"] == 1
    assert m["attempts_by_outcome"] == {state.POLICY_REFUSED: 1}
    assert m["response_body_bytes"] == len(REDIRECT_BODY) and m["retained_payload_bytes"] == 0
    assert (t["physical_attempts"], t["retained_payload_bytes"]) == (0, 0)
    assert [c.host for c in transport.calls] == ["huggingface.co"]


def test_cdn_identity_mismatch_still_stops(fx41: Fixture, root: Path) -> None:
    def wrong_etag(call: Call) -> FakeResponse:
        return SyntheticTransport(fx41).ok(call, headers={"ETag": '"refreshed"'})

    rules = [origin_redirects_to(fx41, CDN), Rule(at(fx41, OP, host=CDN), wrong_etag)]
    rules.reverse()  # the CDN rule first; the origin rule matches only huggingface.co
    result = run(root, fx41, SyntheticTransport(fx41, rules=rules))
    assert result.run_status == "STOPPED" and result.stop_reason is not None
    assert "ETag differs from the frozen strong ETag" in result.stop_reason
    assert [a["outcome"] for a in attempts(root)] == [state.REDIRECT, state.IDENTITY_MISMATCH]


# -- production HTTP parser over the CDN hop (incl. B01/B02) ----------------


def head_206(fx: Fixture, call: Call, *, chunked: bool) -> bytes:
    ok = SyntheticTransport(fx).ok(call)
    headers = {k: v for k, v in ok._headers.items() if k != "content-length"}
    if chunked:
        headers["transfer-encoding"] = "chunked"
    else:
        headers["content-length"] = str(call.end - call.start + 1)
    return response_head(206, headers)


def wire41(fx: Fixture, clock: FakeClock, *rules: WireRule) -> WireTransport:
    return WireTransport(
        fx, clock, rules=[*rules, wire_origin_redirects_to(fx, CDN)], policy=v41.HOSTS
    )


def test_wire_replay_origin_to_cdn_completes_through_the_production_parser(
    fx41: Fixture, root: Path
) -> None:
    clock = FakeClock()
    transport = wire41(fx41, clock)
    result = run(root, fx41, transport, clock)
    assert result.status == "COMPLETE"
    assert {c.host for c in transport.calls} == {"huggingface.co", CDN}
    first_cdn = next(
        s for s, c in zip(transport.sockets, transport.calls, strict=True) if c.host == CDN
    )
    assert bytes(first_cdn.sent).startswith(b"GET /repos/5e/1a/")
    assert b"Range: bytes=0-3\r\n" in first_cdn.sent and f"Host: {CDN}".encode() in first_cdn.sent


def test_wire_replay_to_an_unlisted_host_stops_policy_refused(fx41: Fixture, root: Path) -> None:
    clock = FakeClock()
    rule = wire_origin_redirects_to(fx41, "eu.aws.cdn.hf.co")
    transport = WireTransport(fx41, clock, rules=[rule], policy=v41.HOSTS)
    result = run(root, fx41, transport, clock)
    assert result.run_status == "STOPPED"
    assert result.stop_reason == f"{OP}: host 'eu.aws.cdn.hf.co' is not an exact allowlisted host"
    assert len(transport.sockets) == 1
    [row] = attempts(root)
    assert (row["outcome"], row["response_bytes"]) == (state.POLICY_REFUSED, len(REDIRECT_BODY))


def test_b01_partial_cdn_bytes_are_accounted_and_never_promoted(fx41: Fixture, root: Path) -> None:
    clock = FakeClock()
    partial = WireRule(
        at(fx41, OP, host=CDN),
        lambda c: [(0.0, head_206(fx41, c, chunked=True) + chunk(b"PAR1"))],
        2,
    )
    result = run(root, fx41, wire41(fx41, clock, partial), clock)
    assert result.status == "COMPLETE"
    rows = [a for a in attempts(root) if a["op_id"] == OP]
    assert [(a["try_number"], a["hop"], a["outcome"]) for a in rows] == [
        (1, 0, state.REDIRECT),
        (1, 1, state.TRANSPORT_ERROR),
        (2, 0, state.REDIRECT),
        (2, 1, state.TRANSPORT_ERROR),
        (3, 0, state.REDIRECT),
        (3, 1, state.SUCCESS),
    ]
    for row in rows:
        if row["outcome"] == state.TRANSPORT_ERROR:
            assert "IncompleteRead" in row["error"] and row["response_bytes"] == 4
            assert row["response_sha256"] == hashlib.sha256(b"PAR1").hexdigest()
            assert (root / row["temp_path"]).read_bytes() == b"PAR1"
    assert sum(a["response_bytes"] for a in rows) == 3 * len(REDIRECT_BODY) + 4 + 4 + 4


def test_b02_absolute_deadline_on_the_cdn_hop(fx41: Fixture, root: Path) -> None:
    clock = FakeClock()
    steps = lambda c: [  # noqa: E731
        (0.0, head_206(fx41, c, chunked=True)),
        (60.0, chunk(b"PAR1")),
        (70.0, LAST_CHUNK),
    ]
    transport = wire41(fx41, clock, WireRule(at(fx41, OP, host=CDN), steps))
    result = run(root, fx41, transport, clock)
    assert result.status == "COMPLETE"
    rows = [a for a in attempts(root) if a["op_id"] == OP]
    timeout = rows[1]
    assert (timeout["hop"], timeout["url_host"], timeout["outcome"]) == (1, CDN, state.TIMEOUT)
    assert timeout["response_bytes"] == 4 and "deadline" in timeout["error"]
    sock = transport.sockets[1]
    assert sock.closed_at is not None and sock.closed_at - sock.opened_at == 120
    assert sock.timeouts == [120, 120, 120, 60]
    assert [(a["try_number"], a["outcome"]) for a in rows[2:]] == [
        (2, state.REDIRECT),
        (2, state.SUCCESS),
    ]


def test_v41_live_transport_connects_only_to_listed_hosts(
    monkeypatch: pytest.MonkeyPatch, fx41: Fixture
) -> None:
    clock = FakeClock()
    call = Call(0, f"https://{CDN}/repos/k?s=1", CDN, "/repos/k", "s=1", 0, 3, 120, 0)
    steps: list[Step] = [(0.0, response_head(206, {"content-length": "4"}) + b"PAR1")]
    connected: list[str] = []

    def connect(host: str, deadline: float, clk: Any) -> ScriptedSocket:
        connected.append(host)
        return ScriptedSocket(clock, steps)

    monkeypatch.setattr(tp, "_connect", connect)
    live = tp.LiveHttpsTransport(clock=clock, policy=v41.HOSTS)
    response = live.open(call.url, start=0, end=3, timeout_seconds=120, deadline=clock() + 120)
    assert response.status == 206 and response.read(65536) == b"PAR1"
    response.close()
    for url in ("https://eu.aws.cdn.hf.co/x", f"https://{CDN}:444/x", f"http://{CDN}/x"):
        with pytest.raises(tp.PolicyError):
            live.open(url, start=0, end=3, timeout_seconds=120, deadline=clock() + 120)
    with pytest.raises(tp.PolicyError, match="not an exact allowlisted host"):
        tp.LiveHttpsTransport(clock=clock).open(
            call.url, start=0, end=3, timeout_seconds=120, deadline=clock() + 120
        )
    assert connected == [CDN]


# -- plan, freeze and scientific identity -----------------------------------


def _raw(rel: str) -> dict[str, Any]:
    obj = canonical.loads_bytes_strict((REPO / rel).read_bytes())
    assert isinstance(obj, dict)
    return obj


def test_v41_plan_has_the_identical_membership_and_ranges() -> None:
    old = frozen.load_committed_plan()
    new = v41.load_committed_plan()
    assert (old.digest, new.digest) == (frozen.PLAN_DIGEST, v41.PLAN_DIGEST)
    assert new.digest != old.digest
    assert new.profile is v41.PROFILE and old.profile is frozen.V40
    assert new.execution_root == "G:\\Project\\xlm-evidence-v4.1\\essential-web"
    assert old.execution_root == "G:\\Project\\xlm-evidence-v4\\essential-web"
    assert (new.source, new.files, new.operations) == (old.source, old.files, old.operations)
    assert sum(1 for o in new.operations if o.arm == "M") == 16
    assert sum(1 for o in new.operations if o.arm == "T") == 24
    assert sum(1 for o in new.operations if o.range is None) == 8
    old_obj, new_obj = _raw(frozen.PLAN_PATH), _raw(v41.PLAN_PATH)
    assert sorted(k for k in new_obj if new_obj[k] != old_obj[k]) == [
        "digest",
        *v41.AMENDED_PLAN_KEYS,
    ]
    assert (
        v41.membership_and_ranges_digest(old_obj)
        == v41.membership_and_ranges_digest(new_obj)
        == v41.MEMBERSHIP_AND_RANGES_DIGEST
    )
    assert new_obj["limits"] == frozen.LIMITS == old_obj["limits"]


def test_scientific_identity_constants_are_unchanged() -> None:
    assert frozen.SCIENTIFIC_NAMESPACE == "essential-web-evidence-v2.0"
    assert frozen.SELECTION_DIGEST == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert frozen.SOURCE_REVISION == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert frozen.POLICY_DIGEST == (
        "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    )
    plan = v41.load_committed_plan()
    assert plan.source == frozen.REAL_SOURCE
    assert [f.file for f in plan.files if f.arm == "M"] == [
        f.file for f in frozen.load_committed_plan().files if f.arm == "M"
    ]


def _resealed(mutate: Any) -> dict[str, Any]:
    obj = copy.deepcopy(_raw(v41.PLAN_PATH))
    mutate(obj)
    obj["digest"] = canonical.self_digest(obj)
    return obj


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o["network"]["hosts"].append("*.hf.co"),
        lambda o: o["network"]["hosts"].append("evil.hf.co"),
        lambda o: o["network"]["signed_target_hosts"].pop(),
        lambda o: o["network"].__setitem__("port", 444),
        lambda o: o["network"].__setitem__("scheme", "http"),
        lambda o: o.__setitem__("execution_root", frozen.EXECUTION_ROOT),
        lambda o: o.__setitem__("protocol_version", frozen.PROTOCOL_VERSION),
        lambda o: o["operations"][1]["range"].__setitem__(0, o["operations"][1]["range"][0] - 1),
        lambda o: o["limits"].__setitem__("physical_attempts_per_arm_max", 999),
    ],
)
def test_real_v41_plan_refuses_any_edit(mutate: Any) -> None:
    with pytest.raises(frozen.PlanError):
        frozen.validate_plan(_resealed(mutate), synthetic=False, profile=v41.PROFILE)


def test_plans_and_profiles_are_not_interchangeable() -> None:
    with pytest.raises(frozen.PlanError):
        frozen.validate_plan(_raw(v41.PLAN_PATH), synthetic=False)
    with pytest.raises(frozen.PlanError):
        frozen.validate_plan(_raw(frozen.PLAN_PATH), synthetic=False, profile=v41.PROFILE)


def test_verify_reproduces_every_v41_binding() -> None:
    summary = v41.verify_repository()
    assert summary["protocol_sha256"] == v41.PROTOCOL_SHA256
    assert summary["freeze_digest"] == v41.FREEZE_DIGEST
    assert summary["plan_digest"] == v41.PLAN_DIGEST
    assert summary["membership_and_ranges_digest"] == v41.MEMBERSHIP_AND_RANGES_DIGEST
    assert summary["parent_v4_0"]["plan_digest"] == frozen.PLAN_DIGEST
    assert summary["v4_0_live_status"] == "STOPPED_POLICY_REFUSED"
    assert summary["allowed_hosts"] == list(EXACT_HOSTS)
    assert (summary["M_logical_operations"], summary["T_logical_operations"]) == (16, 24)
    assert summary["execution_root_exists"] is False
    protocol = (REPO / v41.PROTOCOL_PATH).read_bytes()
    assert hashlib.sha256(protocol).hexdigest() == v41.PROTOCOL_SHA256


def test_v40_live_observation_record() -> None:
    record = _raw(v41.OBSERVATION_PATH)
    assert record["digest"] == v41.OBSERVATION_DIGEST
    assert record["record_status"] == "STOPPED_POLICY_REFUSED"
    reported = record["reported"]
    assert (reported["status"], reported["run_status"]) == ("INCOMPLETE", "STOPPED")
    assert reported["stop_reason"] == f"{OP}: host '{CDN}' is not an exact allowlisted host"
    assert reported["totals"]["M"]["physical_attempts"] == 1
    assert reported["totals"]["M"]["response_body_bytes"] == 1098
    assert reported["totals"]["M"]["attempts_by_outcome"] == {"POLICY_REFUSED": 1}
    assert reported["totals"]["M"]["retained_payload_bytes"] == 0
    assert reported["totals"]["T"]["physical_attempts"] == 0
    exposure = record["scientific_exposure"]
    assert exposure["phase_p_operations_completed"] == 0
    assert exposure["T_selected_text_observed"] is False
    assert exposure["M_selected_outcomes_observed"] is False


# -- roots, live entry point and CLI ------------------------------------------


def test_v41_root_is_fresh_and_distinct() -> None:
    assert phase_p.live_root_v41() == Path(v41.EXECUTION_ROOT)
    assert phase_p.live_root_v41() != phase_p.live_root()
    assert v41.EXECUTION_ROOT in phase_p.FROZEN_ROOTS
    assert frozen.EXECUTION_ROOT in phase_p.FROZEN_ROOTS
    assert not Path(v41.EXECUTION_ROOT).exists()


def test_offline_runs_refuse_the_v41_root_and_the_real_v41_plan(fx41: Fixture) -> None:
    transport = SyntheticTransport(fx41)
    for bad in (Path(v41.EXECUTION_ROOT), Path(v41.EXECUTION_ROOT) / "sub"):
        with pytest.raises(phase_p.RefusedError, match="frozen"):
            phase_p.run_offline(bad, plan=fx41.plan, transport=transport)
    with pytest.raises(phase_p.RefusedError, match="synthetic"):
        phase_p.run_offline(Path("unused"), plan=v41.load_committed_plan(), transport=transport)
    assert transport.calls == []


def test_live_entry_point_takes_only_the_confirmation() -> None:
    assert list(inspect.signature(phase_p.run_live_v41).parameters) == ["confirm_plan_digest"]
    assert list(inspect.signature(phase_p.live_root_v41).parameters) == []


def test_wrong_digest_refuses_without_touching_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "v41-root"
    monkeypatch.setattr(phase_p, "live_root_v41", lambda: root)
    for wrong in (frozen.PLAN_DIGEST, v41.PLAN_DIGEST.upper(), "0" * 64):
        assert cli.main(["phase-p", "--confirm-plan-digest", wrong]) == 1
        assert "does not equal the frozen v4.1 plan digest" in capsys.readouterr().err
    with pytest.raises(phase_p.RefusedError, match="frozen v4 plan digest"):
        phase_p.run_live(confirm_plan_digest=v41.PLAN_DIGEST)
    assert not root.exists()


class _RecordingLive41:
    """Stands in for the live transport: origin 302 to the CDN, then a wrong ETag there."""

    calls: list[dict[str, Any]] = []
    policies: list[frozen.HostPolicy] = []

    def __init__(self, policy: frozen.HostPolicy) -> None:
        self.policies.append(policy)

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> FakeResponse:
        self.calls.append({"url": url, "start": start, "end": end, "timeout": timeout_seconds})
        if url.startswith("https://huggingface.co/"):
            return redirect(f"https://{CDN}/repos/5e/1a/k?Signature=s")
        headers = {"Content-Range": f"bytes 0-3/{N}", "ETag": '"not-the-frozen-etag"'}
        return FakeResponse(206, headers, b"PAR1")


def test_cli_live_path_follows_the_cdn_redirect_and_still_enforces_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "v41-root"
    monkeypatch.setattr(phase_p, "live_root_v41", lambda: root)
    monkeypatch.setattr(tp, "LiveHttpsTransport", _RecordingLive41)
    _RecordingLive41.calls, _RecordingLive41.policies = [], []
    assert cli.main(["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST]) == 3
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["run_status"]) == ("INCOMPLETE", "STOPPED")
    assert "ETag" in result["stop_reason"]
    assert _RecordingLive41.policies == [v41.HOSTS]
    assert _RecordingLive41.calls == [
        {"url": CANONICAL, "start": 0, "end": 3, "timeout": 120},
        {"url": f"https://{CDN}/repos/5e/1a/k?Signature=s", "start": 0, "end": 3, "timeout": 120},
    ]
    assert [a["outcome"] for a in attempts(root)] == [state.REDIRECT, state.IDENTITY_MISMATCH]
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["plan_digest"] == v41.PLAN_DIGEST
    assert receipt["protocol_version"] == v41.PROTOCOL_VERSION
    assert run_row(root).protocol_version == v41.PROTOCOL_VERSION
    assert cli.main(["phase-p-status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "STOPPED"
    assert cli.main(["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST]) == 1
    assert "STOPPED" in capsys.readouterr().err
    assert len(_RecordingLive41.calls) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST, "--url", "https://x"],
        ["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST, "--host", CDN],
        ["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST, "--root", "C:\\x"],
        ["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST, "--range", "0-3"],
        ["phase-p", "--confirm-plan-digest", v41.PLAN_DIGEST, "--file", "a.parquet"],
        ["phase-p", "--confirm", v41.PLAN_DIGEST],
        ["phase-p"],
        ["fetch", f"https://{CDN}/x"],
    ],
)
def test_cli_has_no_arbitrary_input(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_cli_options_are_exactly_the_confirmation() -> None:
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    choices: dict[str, argparse.ArgumentParser] = dict(sub.choices or {})
    assert sorted(choices) == ["phase-p", "phase-p-status", "show-plan", "verify"]
    live = sorted(
        o
        for a in choices["phase-p"]._actions
        for o in a.option_strings
        if o not in ("-h", "--help")
    )
    assert live == ["--confirm-plan-digest"]


def test_cli_verify_and_show_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["plan_digest"] == v41.PLAN_DIGEST
    assert cli.main(["show-plan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len([line for line in lines if line.split()[0].isdigit()]) == 40
    hosts = next(line for line in lines if line.startswith("hosts"))
    assert tuple(hosts.split()[1:]) == EXACT_HOSTS
