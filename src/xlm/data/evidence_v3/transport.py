"""Single-hop, range-exact transport for the Phase-P executor.

A transport performs exactly ONE physical HTTP GET for an immutable
:class:`TransportRequest` and returns the raw response: status, headers
and a bounded chunk reader. It never follows redirects, never retries,
never widens the range and never buffers a whole body. Redirect handling,
metering, identity and budgets belong to the executor, which is the only
producer of ``TransportRequest`` objects (they carry an executor seal).

:class:`_LiveHttpsTransport` is the only network implementation. It is
private, constructed exclusively by the executor in REAL mode at the
frozen root, never accepted from a synthetic harness, and it opens only
request objects the executor marked as issued AFTER durably journalling
``ATTEMPT_RESERVE`` and ``ATTEMPT_ISSUED`` (each such object is single-use).
It resolves DNS itself and refuses non-global addresses (defeats DNS answers
pointing at loopback/private space), verifies TLS with the system trust
store and SNI, and enforces the absolute deadline on every socket operation.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from xlm.data.evidence_v3 import frozen_v3, netpolicy


class TransportError(RuntimeError):
    """A physical transport failure. ``retryable`` follows inherited rules."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class DeadlineExceeded(TransportError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


_REQUEST_SEAL = object()
_ISSUED: weakref.WeakSet[TransportRequest] = weakref.WeakSet()
USER_AGENT = "xlm-evidence-v3/phase-p"


@dataclass(frozen=True, eq=False)
class TransportRequest:
    """Immutable single-hop request spec, derived only from a plan operation."""

    url: str
    host: str
    range_start: int
    range_end: int
    deadline_ns: int
    timeout_seconds: float
    max_read_bytes: int
    attempt_id: str
    seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.seal is not _REQUEST_SEAL:
            raise TransportError(
                "TransportRequest is produced only by the executor", retryable=False
            )
        checked = netpolicy.check_url(self.url)
        if checked.host != self.host:
            raise TransportError("request host does not match its URL", retryable=False)
        if not 0 <= self.range_start <= self.range_end:
            raise TransportError("invalid inclusive byte range", retryable=False)
        if not 0 < self.timeout_seconds <= frozen_v3.REQUEST_SECONDS_MAX:
            raise TransportError("per-request timeout must be within (0, 30] s", retryable=False)
        if not 0 < self.max_read_bytes <= frozen_v3.RESPONSE_BODY_BYTES_MAX + 65537:
            raise TransportError("per-response read bound out of range", retryable=False)

    @property
    def range_header(self) -> str:
        return f"bytes={self.range_start}-{self.range_end}"

    def headers(self) -> dict[str, str]:
        """The complete, fixed header set sent on the wire."""
        return {
            "Host": self.host,
            "Range": self.range_header,
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }


def _make_request(
    *,
    url: str,
    range_start: int,
    range_end: int,
    deadline_ns: int,
    timeout_seconds: float,
    max_read_bytes: int,
    attempt_id: str,
) -> TransportRequest:
    host = netpolicy.check_url(url).host
    return TransportRequest(
        url=url,
        host=host,
        range_start=range_start,
        range_end=range_end,
        deadline_ns=deadline_ns,
        timeout_seconds=timeout_seconds,
        max_read_bytes=max_read_bytes,
        attempt_id=attempt_id,
        seal=_REQUEST_SEAL,
    )


class TransportResponse(Protocol):
    """One physical response; the body is read only through ``read_chunk``."""

    @property
    def status(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]:
        """Lowercase names, single values."""
        ...

    def read_chunk(self, max_bytes: int) -> bytes:
        """Return 0 < n <= max_bytes bytes, or b"" at end of body."""

    def close(self) -> None: ...


class Transport(Protocol):
    def open(self, request: TransportRequest) -> TransportResponse: ...


def _normalize_headers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in pairs:
        key = name.lower()
        if key in out and key in ("content-range", "etag", "location", "content-length"):
            raise TransportError(f"duplicate {key} header", retryable=False)
        out[key] = value
    return out


class _LiveResponse:
    def __init__(
        self, response: http.client.HTTPResponse, sock: socket.socket, request: TransportRequest
    ) -> None:
        self._response = response
        self._sock = sock
        self._request = request
        self.status = int(response.status)
        self.headers = _normalize_headers(list(response.getheaders()))

    def _remaining(self) -> float:
        remaining = (self._request.deadline_ns - time.monotonic_ns()) / 1e9
        if remaining <= 0:
            raise DeadlineExceeded("request deadline exceeded while reading the body")
        return remaining

    def read_chunk(self, max_bytes: int) -> bytes:
        self._sock.settimeout(min(self._remaining(), self._request.timeout_seconds))
        try:
            return self._response.read1(max_bytes) if max_bytes > 0 else b""
        except TimeoutError as exc:
            raise DeadlineExceeded(f"body read timed out: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"body read failed: {type(exc).__name__}", retryable=True) from exc

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._sock.close()


def _global_addresses(host: str, deadline_ns: int) -> list[tuple[int, str]]:
    if deadline_ns <= time.monotonic_ns():
        raise DeadlineExceeded("deadline exceeded before DNS resolution")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise TransportError(f"DNS resolution failed for {host}", retryable=True) from exc
    out: list[tuple[int, str]] = []
    for family, _, _, _, sockaddr in infos:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise TransportError(
                f"{host} resolved to non-global address {address}", retryable=False
            )
        out.append((family, str(address)))
    if not out:
        raise TransportError(f"{host} has no addresses", retryable=True)
    return out


def _mark_issued(request: TransportRequest) -> None:
    """Executor-only: the request's attempt is durably reserved and issued."""
    if request.seal is not _REQUEST_SEAL:
        raise TransportError("unsealed request", retryable=False)
    _ISSUED.add(request)


class _LiveHttpsTransport:
    """The only network transport; executor-constructed in REAL mode only."""

    def __init__(self) -> None:
        self._context = ssl.create_default_context()
        self._context.check_hostname = True
        self._context.verify_mode = ssl.CERT_REQUIRED

    def open(self, request: TransportRequest) -> TransportResponse:
        if request.seal is not _REQUEST_SEAL or request not in _ISSUED:
            raise TransportError("request was not durably issued by the executor", retryable=False)
        _ISSUED.discard(request)  # single use
        checked = netpolicy.check_url(request.url)
        split = request.url[len("https://") + len(checked.host) :]
        target = split if split.startswith("/") else "/" + split
        family, address = _global_addresses(request.host, request.deadline_ns)[0]
        remaining = (request.deadline_ns - time.monotonic_ns()) / 1e9
        if remaining <= 0:
            raise DeadlineExceeded("deadline exceeded before connect")
        timeout = min(remaining, request.timeout_seconds)
        try:
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(timeout)
            raw.connect((address, 443))
            sock = self._context.wrap_socket(raw, server_hostname=request.host)
        except TimeoutError as exc:
            raise DeadlineExceeded(f"connect timed out: {exc}") from exc
        except (OSError, ssl.SSLError) as exc:
            raise TransportError(f"connect failed: {type(exc).__name__}", retryable=True) from exc
        connection = http.client.HTTPSConnection(request.host, 443, timeout=timeout)
        connection.sock = sock
        try:
            connection.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
            for name, value in request.headers().items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
        except TimeoutError as exc:
            sock.close()
            raise DeadlineExceeded(f"response timed out: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            sock.close()
            raise TransportError(f"request failed: {type(exc).__name__}", retryable=True) from exc
        return _LiveResponse(response, sock, request)
