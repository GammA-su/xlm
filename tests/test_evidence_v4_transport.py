"""Evidence-v4 URL policy, redirect destinations and response identity (offline)."""

from __future__ import annotations

import http.client
from typing import Any

import pytest

from evidence_v4_support import block_network
from xlm.data.evidence_v4 import frozen
from xlm.data.evidence_v4 import transport as tp

SOURCE = frozen.REAL_SOURCE
FILE = "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
CANONICAL = SOURCE.canonical_url(FILE)
ETAG = '"a898462fb164d7eea3e8248a28cb1f855843d72bd55cfd126f537653c4eae323"'
N = 290684578
SIGNED = "https://cas-bridge.xethub.hf.co/xet-bridge-us/abc?X-Amz-Signature=s"


@pytest.mark.parametrize(
    "url",
    [
        CANONICAL.replace("https://", "http://"),
        "https://evil.example/datasets/x",
        "https://huggingface.co.evil.example/x",
        "https://evil.huggingface.co/x",
        "https://other.hf.co/x",
        "https://xethub.hf.co/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://localhost/x",
        "https://huggingface.co:8443/x",
        "https://huggingface.co:80/x",
        "https://user:pw@huggingface.co/x",
        "https://huggingface.co/x#frag",
        "https://HuggingFace.co/x",
        "https://huggingface.co/a b",
        "HTTPS://huggingface.co/x",
        "ftp://huggingface.co/x",
        "https://huggingface.co.",
    ],
)
def test_url_policy_refuses(url: str) -> None:
    with pytest.raises(tp.PolicyError):
        tp.check_url(url)


def test_url_policy_accepts_exact_hosts_and_port_443() -> None:
    assert tp.check_url(CANONICAL).host == "huggingface.co"
    assert tp.check_url("https://huggingface.co:443/x").path == "/x"
    checked = tp.check_url(SIGNED)
    assert checked.host == "cas-bridge.xethub.hf.co"
    assert checked.identity()["query_sha256"] is not None
    assert "X-Amz-Signature" not in str(checked.identity())


def test_start_url_is_the_pinned_revision_path() -> None:
    start = tp.start_url(SOURCE, FILE)
    assert start.url == (
        "https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/"
        "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d/" + FILE
    )
    assert start.query == ""


def test_redirect_destinations_allowed() -> None:
    start = tp.start_url(SOURCE, FILE)
    assert tp.resolve_redirect(start, SIGNED, SOURCE, FILE).host == "cas-bridge.xethub.hf.co"
    assert tp.resolve_redirect(start, CANONICAL, SOURCE, FILE).path == start.path
    relative = tp.resolve_redirect(start, start.path, SOURCE, FILE)
    assert relative.url == CANONICAL


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        "https://evil.example/x",
        CANONICAL.replace("https://", "http://"),
        "//evil.example/x",
        SOURCE.canonical_url("data/crawl=CC-MAIN-2014-15/other.parquet"),
        CANONICAL.replace(SOURCE.revision, "main") + f"?revision={SOURCE.revision}",
        CANONICAL + "?download=true",
        "/api/resolve-cache/datasets/EssentialAI/essential-web-v1.0/" + SOURCE.revision,
        "https://cas-bridge.xethub.hf.co:444/x",
        "https://127.0.0.1/x",
    ],
)
def test_redirect_destinations_refused(location: str | None) -> None:
    with pytest.raises(tp.PolicyError):
        tp.resolve_redirect(tp.start_url(SOURCE, FILE), location, SOURCE, FILE)


def _verify(
    *,
    status: int = 206,
    headers: dict[str, str | None] | None = None,
    body: bytes = b"PAR1",
    final: str = SIGNED,
    start: int = 0,
    end: int = 3,
    magic: str = "equal",
) -> None:
    base: dict[str, str | None] = {
        "content-range": f"bytes {start}-{end}/{N}",
        "etag": ETAG,
        "content-length": str(end - start + 1),
    }
    base.update(headers or {})
    tp.verify_identity(
        status=status,
        headers=base,
        body=body,
        final_url=tp.check_url(final),
        source=SOURCE,
        file=FILE,
        expected=tp.Expected(start, end, N, ETAG, magic),
    )


def test_identity_accepts_the_exact_response() -> None:
    _verify()
    _verify(final=CANONICAL)
    _verify(headers={"content-length": None, "content-encoding": "identity"})
    _verify(start=N - 8, end=N - 1, body=b"\x00\x01\x02\x03PAR1", magic="tail")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"status": 200}, "not 206"),
        ({"headers": {"content-range": f"bytes 1-4/{N}"}}, "Content-Range"),
        ({"headers": {"content-range": f"bytes 0-3/{N + 1}"}}, "total"),
        ({"headers": {"content-range": "bytes 0-3/*"}}, "malformed"),
        ({"headers": {"content-range": None}}, "missing Content-Range"),
        ({"headers": {"etag": "W/" + ETAG}}, "weak"),
        ({"headers": {"etag": None}}, "weak"),
        ({"headers": {"etag": '"0000"'}}, "differs"),
        ({"headers": {"etag": f"{ETAG}, {ETAG}"}}, "weak"),
        ({"headers": {"content-encoding": "gzip"}}, "content-encoding"),
        ({"headers": {"content-length": "5"}}, "Content-Length"),
        ({"body": b"PAR"}, "exactly"),
        ({"body": b"PAR1!"}, "exactly"),
        ({"body": b"PAR2"}, "PAR1"),
        ({"final": SOURCE.canonical_url("data/other.parquet")}, "neither"),
        ({"final": CANONICAL + "?revision=x"}, "neither"),
        (
            {"start": N - 8, "end": N - 1, "body": b"\x00" * 8, "magic": "tail"},
            "PAR1",
        ),
    ],
)
def test_identity_refuses(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(tp.IdentityError, match=match):
        _verify(**kwargs)


class _FakeConnection:
    instances: list[_FakeConnection] = []

    def __init__(self, host: str, port: int, *, timeout: float, context: Any) -> None:
        self.args = (host, port, timeout)
        self.headers: list[tuple[str, str]] = []
        self.request_line: tuple[str, str] | None = None
        self.sock = None
        self.closed = False
        _FakeConnection.instances.append(self)

    def connect(self) -> None:
        pass

    def putrequest(self, method: str, target: str, skip_accept_encoding: bool = False) -> None:
        assert skip_accept_encoding
        self.request_line = (method, target)

    def putheader(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def endheaders(self) -> None:
        pass

    def getresponse(self) -> Any:
        class _Resp:
            status = 302

            def getheader(self, name: str) -> str | None:
                return SIGNED if name.lower() == "location" else None

            def read(self, amount: int) -> bytes:
                return b""

            def close(self) -> None:
                pass

        return _Resp()

    def close(self) -> None:
        self.closed = True


def test_live_transport_sends_one_exact_request_and_never_follows_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_network(monkeypatch)
    _FakeConnection.instances = []
    monkeypatch.setattr(http.client, "HTTPSConnection", _FakeConnection)
    live = tp.LiveHttpsTransport()
    import time

    response = live.open(
        CANONICAL, start=10, end=20, timeout_seconds=120, deadline=time.monotonic() + 120
    )
    assert response.status == 302 and response.header("Location") == SIGNED
    (conn,) = _FakeConnection.instances
    assert conn.args[0:2] == ("huggingface.co", 443) and 0 < conn.args[2] <= 120
    assert conn.request_line == ("GET", tp.check_url(CANONICAL).target)
    assert dict(conn.headers) == {
        "Range": "bytes=10-20",
        "Accept-Encoding": "identity",
        "User-Agent": tp.USER_AGENT,
    }


def test_live_transport_refuses_policy_violations_before_any_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_network(monkeypatch)
    _FakeConnection.instances = []
    monkeypatch.setattr(http.client, "HTTPSConnection", _FakeConnection)
    live = tp.LiveHttpsTransport()
    for url, timeout in (("http://huggingface.co/x", 120), (CANONICAL, 30), (CANONICAL, 121)):
        with pytest.raises(tp.PolicyError):
            live.open(url, start=0, end=3, timeout_seconds=timeout, deadline=1e18)
    with pytest.raises(tp.TransportError, match="deadline"):
        live.open(CANONICAL, start=0, end=3, timeout_seconds=120, deadline=0.0)
    assert _FakeConnection.instances == []
