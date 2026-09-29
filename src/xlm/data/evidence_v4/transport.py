"""URL/redirect policy, response identity checks and the single-hop transport.

Policy (protocol section 7) is structural: ``urllib.parse.urlsplit`` plus
exact component comparison, never substring matching. The transport performs
exactly one GET per call, never follows redirects and never retries; the
engine in :mod:`phase_p` owns redirects, retries, accounting and identity.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
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


class _LiveResponse:
    def __init__(
        self, conn: http.client.HTTPSConnection, resp: http.client.HTTPResponse, deadline: float
    ) -> None:
        self._conn = conn
        self._resp = resp
        self._deadline = deadline
        self.status = resp.status

    def header(self, name: str) -> str | None:
        return self._resp.getheader(name)

    def read(self, amount: int) -> bytes:
        try:
            _arm_socket(self._conn, self._deadline)
            return self._resp.read(amount)
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"read failed: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        self._resp.close()
        self._conn.close()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransportError("the 120-second attempt deadline elapsed")
    return remaining


def _arm_socket(conn: http.client.HTTPSConnection, deadline: float) -> None:
    sock: socket.socket | None = conn.sock
    if sock is not None:
        sock.settimeout(_remaining(deadline))


class LiveHttpsTransport:
    """The only network implementation: one policy-checked HTTPS GET per call."""

    def open(
        self, url: str, *, start: int, end: int, timeout_seconds: float, deadline: float
    ) -> Response:
        checked = check_url(url)
        if not 0 <= start <= end:
            raise PolicyError("invalid inclusive range")
        if timeout_seconds != frozen.ATTEMPT_TIMEOUT_SECONDS:
            raise PolicyError("the physical attempt timeout is frozen at 120 seconds")
        conn = http.client.HTTPSConnection(
            checked.host,
            frozen.ALLOWED_PORT,
            timeout=min(timeout_seconds, _remaining(deadline)),
            context=ssl.create_default_context(),
        )
        try:
            conn.connect()
            _arm_socket(conn, deadline)
            conn.putrequest("GET", checked.target, skip_accept_encoding=True)
            conn.putheader("Range", f"bytes={start}-{end}")
            conn.putheader("Accept-Encoding", "identity")
            conn.putheader("User-Agent", USER_AGENT)
            conn.endheaders()
            _arm_socket(conn, deadline)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise TransportError(f"request failed: {type(exc).__name__}: {exc}") from exc
        except TransportError:
            conn.close()
            raise
        return _LiveResponse(conn, resp, deadline)
