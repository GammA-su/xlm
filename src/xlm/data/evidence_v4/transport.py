"""URL/redirect policy, response identity checks and the single-hop transport.

Policy (protocol section 7) is structural: ``urllib.parse.urlsplit`` plus
exact component comparison, never substring matching. The transport performs
exactly one GET per call, never follows redirects and never retries; the
engine in :mod:`phase_p` owns redirects, retries, accounting and identity.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import unquote, urljoin, urlsplit

from xlm.data.evidence_v4 import frozen

USER_AGENT = "xlm-evidence-v4/phase-p"
_CONTENT_RANGE = re.compile(r"\Abytes (0|[1-9][0-9]*)-(0|[1-9][0-9]*)/([1-9][0-9]*)\Z")


class PolicyError(ValueError):
    """URL or redirect policy violation: STOP."""


class IdentityError(ValueError):
    """Remote identity mismatch: STOP, never reselect or refresh."""


class TransportError(RuntimeError):
    """A physical transport failure (connection, TLS, timeout): retryable."""


class DeadlineError(TransportError):
    """The one absolute 120-second physical-attempt deadline elapsed: retryable TIMEOUT."""


_DEADLINE_MESSAGE = "the 120-second attempt deadline elapsed"


@dataclass(frozen=True)
class CheckedUrl:
    url: str
    host: str
    path: str
    query: str

    def identity(self) -> dict[str, str | None]:
        """Receipt identity: signed query strings are hashed, never stored."""
        return {
            "scheme": "https",
            "host": self.host,
            "path": self.path,
            "query_sha256": hashlib.sha256(self.query.encode()).hexdigest() if self.query else None,
        }

    @property
    def target(self) -> str:
        return self.path + (f"?{self.query}" if self.query else "")


def check_url(url: str) -> CheckedUrl:
    """Validate one absolute URL against the exact frozen network policy."""
    if type(url) is not str or not url or len(url) > 8192:
        raise PolicyError("URL must be a non-empty bounded string")
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in url):
        raise PolicyError("URL must be printable ASCII without spaces")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise PolicyError(f"URL does not parse: {exc}") from exc
    if parts.scheme != "https" or not url.startswith("https://"):
        raise PolicyError(f"scheme {parts.scheme!r} is not https")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise PolicyError("userinfo is forbidden")
    if parts.fragment:
        raise PolicyError("fragments are forbidden")
    host = parts.hostname or ""
    if parts.netloc.split(":")[0] != host or host != host.lower() or not host:
        raise PolicyError("host must be present, lowercase and exactly spelled")
    if host == "localhost" or host.endswith((".localhost", ".")):
        raise PolicyError("localhost hosts are forbidden")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise PolicyError("IP literals are forbidden")
    if host not in frozen.ALLOWED_HOSTS:
        raise PolicyError(f"host {host!r} is not an exact allowlisted host")
    if port is not None and port != frozen.ALLOWED_PORT:
        raise PolicyError(f"port {port} is not 443")
    if not parts.path.startswith("/"):
        raise PolicyError("URL path must be absolute")
    return CheckedUrl(url=url, host=host, path=parts.path, query=parts.query)


def is_canonical(checked: CheckedUrl, source: frozen.Source, file: str) -> bool:
    """Path identity: exact host and exact decoded path. A query is not identity."""
    return checked.host == source.host and unquote(checked.path) == unquote(
        source.canonical_path(file)
    )


def start_url(source: frozen.Source, file: str) -> CheckedUrl:
    checked = check_url(source.canonical_url(file))
    if not is_canonical(checked, source, file) or checked.query:
        raise PolicyError("canonical resource URL does not satisfy its own identity")
    return checked


def resolve_redirect(
    current: CheckedUrl, location: str | None, source: frozen.Source, file: str
) -> CheckedUrl:
    """Validate the actual Location destination: signed target or the canonical path."""
    if location is None or not location.strip():
        raise PolicyError("redirect response without a Location header")
    checked = check_url(urljoin(current.url, location.strip()))
    if checked.host == frozen.SIGNED_TARGET_HOST:
        return checked
    if is_canonical(checked, source, file) and not checked.query:
        return checked
    raise PolicyError(f"redirect to {checked.host}{checked.path} is not the frozen resource")


@dataclass(frozen=True)
class Expected:
    start: int
    end: int
    total_length: int
    strong_etag: str
    magic: str  # "equal" (body == PAR1) | "tail" (last 4 bytes PAR1)


def parse_content_range(value: str | None) -> tuple[int, int, int]:
    if value is None:
        raise IdentityError("missing Content-Range")
    match = _CONTENT_RANGE.match(value)
    if match is None:
        raise IdentityError(f"malformed Content-Range {value!r}")
    start, end, total = (int(g) for g in match.groups())
    if not start <= end < total:
        raise IdentityError(f"inconsistent Content-Range {value!r}")
    return start, end, total


def verify_identity(
    *,
    status: int,
    headers: Mapping[str, str | None],
    body: bytes,
    final_url: CheckedUrl,
    source: frozen.Source,
    file: str,
    expected: Expected,
) -> None:
    """Exact identity of one successful range response (protocol section 7)."""
    if status != 206:
        raise IdentityError(f"status {status} is not 206")
    if final_url.host != frozen.SIGNED_TARGET_HOST and not (
        is_canonical(final_url, source, file) and not final_url.query
    ):
        raise IdentityError("final resource is neither the canonical path nor the signed target")
    start, end, total = parse_content_range(headers.get("content-range"))
    if (start, end) != (expected.start, expected.end):
        raise IdentityError(
            f"Content-Range {start}-{end} != requested {expected.start}-{expected.end}"
        )
    if total != expected.total_length:
        raise IdentityError(f"Content-Range total {total} != frozen length {expected.total_length}")
    etag = headers.get("etag")
    if not frozen.is_strong_etag(etag):
        raise IdentityError("response ETag is missing or weak")
    if etag != expected.strong_etag:
        raise IdentityError("response ETag differs from the frozen strong ETag")
    encoding = headers.get("content-encoding")
    if encoding not in (None, "identity"):
        raise IdentityError(f"content-encoding {encoding!r} is not identity")
    length = expected.end - expected.start + 1
    declared = headers.get("content-length")
    if declared is not None and declared != str(length):
        raise IdentityError(f"Content-Length {declared!r} != range length {length}")
    if len(body) != length:
        raise IdentityError(f"body has {len(body)} bytes, the range requires exactly {length}")
    if expected.magic == "equal" and body != b"PAR1":
        raise IdentityError("bytes 0-3 are not PAR1")
    if expected.magic == "tail" and body[-4:] != b"PAR1":
        raise IdentityError("the trailer does not end in PAR1")


class Response(Protocol):
    status: int

    def header(self, name: str) -> str | None: ...

    def read(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class Transport(Protocol):
    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> Response: ...


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    """Seconds left before the absolute attempt deadline; DeadlineError when none."""
    remaining = deadline - clock()
    if remaining <= 0:
        raise DeadlineError(_DEADLINE_MESSAGE)
    return remaining


@contextmanager
def _classified(stage: str) -> Iterator[None]:
    """Socket timeouts are the deadline (each timeout is the remaining time)."""
    try:
        yield
    except TimeoutError as exc:
        raise DeadlineError(_DEADLINE_MESSAGE) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise TransportError(f"{stage} failed: {type(exc).__name__}: {exc}") from exc


class _DeadlineReader(io.RawIOBase):
    """Raw socket input whose every blocking receive is bounded by the attempt deadline.

    ``http.client`` parses through a ``BufferedReader``; one parser call can
    issue several receives. Each receive here re-arms the socket timeout to
    the time remaining before the one absolute deadline, so no sequence of
    receives can outlast it (B02).
    """

    def __init__(self, sock: socket.socket, deadline: float, clock: Callable[[], float]) -> None:
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        self._clock = clock

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        self._sock.settimeout(_remaining(self._deadline, self._clock))
        return self._sock.recv_into(buffer)


class _DeadlineSocket:
    """All ``http.client.HTTPResponse`` uses of a socket: a deadline-bounded ``makefile``."""

    def __init__(self, sock: socket.socket, deadline: float, clock: Callable[[], float]) -> None:
        self._reader = _DeadlineReader(sock, deadline, clock)

    def makefile(self, mode: str) -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("the response stream is read-only binary")
        return io.BufferedReader(self._reader)


class _LiveResponse:
    def __init__(self, sock: socket.socket, resp: http.client.HTTPResponse) -> None:
        self._sock = sock
        self._resp = resp
        self.status = resp.status

    def header(self, name: str) -> str | None:
        return self._resp.getheader(name)

    def read(self, amount: int) -> bytes:
        """Return body bytes as soon as the parser has them (B01).

        ``read1`` performs at most one body receive and returns its bytes
        before any later framing step can fail, so a failure (IncompleteRead,
        reset, deadline) never holds body bytes the parser already received:
        an earlier call returned them and the engine persisted them. b""
        means the HTTP framing ended correctly.
        """
        with _classified("read"):
            return self._resp.read1(amount)

    def close(self) -> None:
        self._resp.close()
        self._sock.close()


def _send_all(
    sock: socket.socket, data: bytes, deadline: float, clock: Callable[[], float]
) -> None:
    view = memoryview(data)
    while view:
        sock.settimeout(_remaining(deadline, clock))
        view = view[sock.send(view) :]


def _connect(host: str, deadline: float, clock: Callable[[], float]) -> socket.socket:
    """TCP connect and TLS handshake, each blocking step bounded by the remaining time.

    Name resolution is outside the v4 deadline (protocol section 4); its
    elapsed time is still charged against the same absolute deadline.
    """
    with _classified("connect"):
        infos = socket.getaddrinfo(host, frozen.ALLOWED_PORT, type=socket.SOCK_STREAM)
        failure: OSError = OSError(f"no address for {host}")
        for family, kind, proto, _, address in infos:
            raw = socket.socket(family, kind, proto)
            try:
                raw.settimeout(_remaining(deadline, clock))
                raw.connect(address)
            except TimeoutError:
                raw.close()
                raise
            except OSError as exc:  # refused/unreachable: next address, same deadline
                raw.close()
                failure = exc
                continue
            except BaseException:
                raw.close()
                raise
            break
        else:
            raise failure
        try:
            tls = ssl.create_default_context().wrap_socket(
                raw, server_hostname=host, do_handshake_on_connect=False
            )
        except BaseException:
            raw.close()
            raise
        try:
            tls.settimeout(_remaining(deadline, clock))
            tls.do_handshake()
        except BaseException:
            tls.close()
            raise
        return tls


def request_bytes(checked: CheckedUrl, *, start: int, end: int) -> bytes:
    """The one exact GET (the request ``HTTPSConnection`` produced before B02)."""
    lines = (
        f"GET {checked.target} HTTP/1.1",
        f"Host: {checked.host}",
        f"Range: bytes={start}-{end}",
        "Accept-Encoding: identity",
        f"User-Agent: {USER_AGENT}",
    )
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def exchange(
    sock: socket.socket,
    checked: CheckedUrl,
    *,
    start: int,
    end: int,
    deadline: float,
    clock: Callable[[], float],
) -> Response:
    """Send the request on a connected socket and parse the response head.

    Every send and every receive (status line, headers, body framing and
    body) re-arms the socket timeout from the one absolute deadline. The
    caller owns ``sock`` until a response is returned; then the response does.
    """
    with _classified("request"):
        _send_all(sock, request_bytes(checked, start=start, end=end), deadline, clock)
        resp = http.client.HTTPResponse(
            cast(socket.socket, _DeadlineSocket(sock, deadline, clock)), method="GET"
        )
        try:
            resp.begin()
        except BaseException:
            resp.close()
            raise
    return _LiveResponse(sock, resp)


class LiveHttpsTransport:
    """The only network implementation: one policy-checked HTTPS GET per call.

    One absolute deadline covers connect, TLS, request, headers and body:
    each blocking socket operation gets ``deadline - clock()`` as its timeout
    and fails at once when nothing remains; a socket timeout is DeadlineError.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> Response:
        checked = check_url(url)
        if not 0 <= start <= end:
            raise PolicyError("invalid inclusive range")
        if timeout_seconds != frozen.ATTEMPT_TIMEOUT_SECONDS:
            raise PolicyError("the physical attempt timeout is frozen at 120 seconds")
        _remaining(deadline, self._clock)
        sock = _connect(checked.host, deadline, self._clock)
        try:
            return exchange(
                sock, checked, start=start, end=end, deadline=deadline, clock=self._clock
            )
        except BaseException:
            sock.close()
            raise
