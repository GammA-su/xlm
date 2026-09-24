"""Bounded discovery transports, metered streaming, and snapshot pinning adhering to C04."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from xlm.data.acquisition.disk import StorageCapacityManager

ALLOWLISTED_HOSTS: frozenset[str] = frozenset(
    [
        "huggingface.co",
        "hf.co",
        "cdn-lfs.huggingface.co",
        "raw.githubusercontent.com",
        "127.0.0.1",
        "localhost",
    ]
)

#: DNS suffixes exclusively operated by Hugging Face for Hub storage/CDN
#: delivery (xet-bridge, cas-server, transfer, and cdn-lfs endpoints).
#: Download redirects from huggingface.co routinely land on these subdomains,
#: and only the zone owner can mint names under them — so accepting genuine
#: subdomains trusts no new party beyond the already-allowlisted
#: huggingface.co itself. Scoping is by DNS-zone ownership: no non-HF
#: hostname gains any permission from this rule, and lookalikes such as
#: "hf.co.evil.example" or "evilhf.co" never match a dot-anchored suffix.
TRUSTED_HUGGINGFACE_SUFFIXES: tuple[str, ...] = (".hf.co", ".huggingface.co")


class BudgetExhaustedError(RuntimeError):
    """Raised when the discovery byte ceiling, request count, or stream limit is exceeded."""


class DeadlineExceededError(TimeoutError):
    """Raised when the total operation deadline is exceeded."""


class HostNotAllowlistedError(PermissionError):
    """Raised when an outbound URL or redirect target is not on the allowlist."""


class InsecurePathError(PermissionError):
    """Raised when a local manifest attempts directory traversal or unauthorized paths."""


@dataclass
class TransportBudget:
    """Shared budget tracking bytes, decompressed size, request count, and deadlines."""

    max_bytes: int = 32 * 1024 * 1024  # 32 MiB discovery ceiling
    max_decompressed_bytes: int = 64 * 1024 * 1024  # 64 MiB decompression ceiling
    max_requests: int = 20
    deadline_seconds: float = 60.0
    per_request_timeout: float = 10.0
    max_sample_rows: int = 5

    bytes_transferred: int = 0
    decompressed_bytes_produced: int = 0
    requests_made: int = 0
    start_time: float = field(default_factory=time.monotonic)
    capacity: StorageCapacityManager | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _reserved_bytes: int = 0

    def check_deadline(self) -> None:
        if self.capacity is not None:
            self.capacity.check_deadline()
        elapsed = time.monotonic() - self.start_time
        if elapsed > self.deadline_seconds:
            raise DeadlineExceededError(
                f"Discovery operation exceeded total deadline of {self.deadline_seconds:.1f}s "
                f"(elapsed: {elapsed:.2f}s)"
            )

    def record_request(self) -> None:
        self.check_deadline()
        if self.capacity is not None and self.capacity.journal is not None:
            self.capacity.journal.record_request(self.max_requests)
            self.requests_made = self.capacity.journal.state.requests_made
            return
        with self._lock:
            if self.requests_made >= self.max_requests:
                raise BudgetExhaustedError(
                    f"Exceeded maximum discovery request allowance of {self.max_requests} requests."
                )
            self.requests_made += 1

    def read_chunk(self, response: Any, maximum: int = 65536) -> bytes:
        """Reserve before a bounded body read; interrupted reads retain their allowance."""
        self.check_deadline()
        with self._lock:
            remaining = (
                self.capacity.remaining("transfer")
                if self.capacity
                else self.max_bytes - self.bytes_transferred - self._reserved_bytes
            )
            amount = min(maximum, remaining)
            if amount <= 0:
                raise BudgetExhaustedError("response-body byte allowance exhausted")
            token = self.capacity.reserve_transfer(amount) if self.capacity else ""
            if self.capacity is None:
                self._reserved_bytes += amount
        # On an exception the outcome is unresolved, so no reservation is released.
        chunk = bytes(response.read(amount))
        if len(chunk) > amount:
            raise BudgetExhaustedError("transport returned more bytes than requested")
        with self._lock:
            if self.capacity:
                self.capacity.settle("transfer", token, len(chunk))
                self.bytes_transferred = self.capacity.snapshot()["transferred_bytes"]
            else:
                self._reserved_bytes -= amount
                self.bytes_transferred += len(chunk)
        return chunk

    def transfer_lease(self, cap: int | None) -> Any:
        """Durable transfer lease for one response stream (capacity-backed budgets only)."""
        from xlm.data.acquisition.disk import CapacityLease

        assert self.capacity is not None
        return CapacityLease(
            self.capacity,
            "transfer",
            cap=cap,
            message="response-body byte allowance exhausted",
            on_consumed=self._sync_transferred,
        )

    def _sync_transferred(self, consumed: dict[str, int]) -> None:
        self.bytes_transferred = consumed.get("transfer", 0)

    def read_leased(self, response: Any, lease: Any, maximum: int = 65536) -> bytes:
        """``read_chunk`` semantics against a pre-reserved lease: identical sizes and totals."""
        self.check_deadline()
        amount = lease.take(maximum)
        try:
            chunk = bytes(response.read(amount))
        except BaseException:
            # Outcome unresolved: the in-flight amount stays reserved, as before.
            lease.fail(amount)
            raise
        if len(chunk) > amount:
            lease.fail(amount)
            raise BudgetExhaustedError("transport returned more bytes than requested")
        lease.commit(len(chunk))
        return chunk

    def read_body(self, response: Any, limit: int, *, retain: bool = True) -> bytes:
        """Bound metadata/error bodies; no unbounded read or silent truncation."""
        length = response.headers.get("Content-Length")
        expected = int(length) if length is not None else None
        if expected is not None and (expected < 0 or expected > limit):
            raise BudgetExhaustedError("response exceeds its allocated body limit")
        lease = (
            self.transfer_lease(expected if expected is not None else limit)
            if self.capacity is not None
            else None
        )
        data, consumed = bytearray(), 0
        try:
            while expected is None or consumed < expected:
                if consumed == limit:
                    raise BudgetExhaustedError("unframed response reached body limit before EOF")
                amount = min(
                    65536, limit - consumed, expected - consumed if expected is not None else limit
                )
                chunk = (
                    self.read_chunk(response, amount)
                    if lease is None
                    else self.read_leased(response, lease, amount)
                )
                if not chunk:
                    if expected is not None and consumed != expected:
                        raise OSError("response ended before declared length")
                    break
                consumed += len(chunk)
                if retain:
                    data.extend(chunk)
        finally:
            if lease is not None:
                lease.close()
        return bytes(data)

    def record_bytes(self, n: int) -> None:
        self.check_deadline()
        if self.capacity:
            self.capacity.record_transfer(n)
            self.bytes_transferred = self.capacity.snapshot()["transferred_bytes"]
            return
        self.bytes_transferred += n
        if self.bytes_transferred > self.max_bytes:
            raise BudgetExhaustedError(
                f"Exceeded discovery transferred bytes limit of {self.max_bytes:,} bytes "
                f"(consumed: {self.bytes_transferred:,} bytes)."
            )

    def record_decompressed_bytes(self, n: int) -> None:
        self.check_deadline()
        if self.capacity:
            self.capacity.record_decompressed(n)
            self.decompressed_bytes_produced = self.capacity.snapshot()["decompressed_bytes"]
            return
        self.decompressed_bytes_produced += n
        if self.decompressed_bytes_produced > self.max_decompressed_bytes:
            raise BudgetExhaustedError(
                f"Exceeded maximum decompressed stream limit of "
                f"{self.max_decompressed_bytes:,} bytes."
            )

    def to_metrics(self) -> dict[str, Any]:
        return {
            "bytes_transferred": self.bytes_transferred,
            "decompressed_bytes": self.decompressed_bytes_produced,
            "requests_made": self.requests_made,
            "elapsed_seconds": round(time.monotonic() - self.start_time, 3),
            "measurement": "application response-body bytes; no TCP/TLS wire accounting",
            "outstanding_reservations": self.capacity.snapshot()
            if self.capacity
            else {"response_body_bytes": self._reserved_bytes},
        }


def is_allowlisted_host(hostname: str) -> bool:
    """Shared host predicate: exact allowlist plus genuine HF storage subdomains.

    The suffix rule requires a dot-anchored match under an HF-operated zone,
    so "us.aws.cdn.hf.co" passes while "evilhf.co",
    "hf.co.evil.example", and "huggingface.co.attacker.example" do not.
    Default-deny is preserved: anything else must be an exact allowlist hit.
    """
    host = hostname.lower().rstrip(".")
    if host in ALLOWLISTED_HOSTS:
        return True
    return host.endswith(TRUSTED_HUGGINGFACE_SUFFIXES)


def validate_host(url: str) -> None:
    """Validate that the given URL's hostname is on the strict allowlist."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or parsed.scheme not in ("http", "https"):
        raise HostNotAllowlistedError(
            "only explicit HTTP(S) endpoints without credentials are allowed"
        )
    if parsed.scheme == "http" and hostname not in ("127.0.0.1", "localhost"):
        raise HostNotAllowlistedError("cleartext HTTP is restricted to loopback fixtures")
    if not is_allowlisted_host(hostname):
        raise HostNotAllowlistedError(
            f"Host '{hostname}' in URL '{url}' is not in the allowlist "
            f"{sorted(ALLOWLISTED_HOSTS)} and is not a trusted Hugging Face "
            "storage subdomain."
        )


@dataclass
class SnapshotInfo:
    """Metadata describing a resolved, pinned dataset snapshot."""

    provider: str
    target: str
    immutable_revision: str  # Pinned Git commit SHA or SHA-256 digest
    declared_license: str | None = None
    is_gated: bool = False
    is_private: bool = False
    is_not_found: bool = False
    description: str = ""
    card_data: dict[str, Any] = field(default_factory=dict)
    conversion_status: dict[str, Any] | None = None
    error_reason: str | None = None


class DiscoveryTransport(Protocol):
    """Protocol for reading repository snapshots, file inventories, and streaming chunks."""

    def get_snapshot_info(
        self, target: str, pinned_revision: str | None = None
    ) -> SnapshotInfo: ...

    def list_files(
        self, target: str, pinned_revision: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]: ...

    def fetch_stream_chunk(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> bytes: ...


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that strictly verifies every redirect target against the host allowlist."""

    def __init__(self, budget: TransportBudget | None = None, observer: Any = None) -> None:
        super().__init__()
        self.budget = budget
        # Optional performance observer with a ``record_redirect(host)`` method.
        # Only the sanitized hostname is ever reported; never the URL or query.
        self.observer = observer

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        validate_host(newurl)
        if self.observer is not None:
            hostname = (urllib.parse.urlparse(newurl).hostname or "").lower()
            try:
                self.observer.record_redirect(hostname)
            except Exception:
                pass
        if self.budget is not None:
            self.budget.read_body(fp, self.budget.max_bytes, retain=False)
            self.budget.record_request()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


#: HTTP statuses from a cached redirect target that mean the signed target
#: expired or was revoked (not a transient). The caller must invalidate the
#: cached entry and re-resolve through the canonical repository URL once,
#: inside existing retry budgets. Never retried silently against the stale target.
CACHED_TARGET_EXPIRY_STATUSES: frozenset[int] = frozenset([400, 401, 403, 404, 410])

#: Redirect statuses a pooled direct request never follows itself. A 3xx from
#: an already-resolved target means the provider changed destination: invalidate
#: and re-resolve canonically rather than chasing unvalidated hops.
CACHED_TARGET_REDIRECT_STATUSES: frozenset[int] = frozenset([301, 302, 303, 307, 308])

#: Upper bound for an error body consumed from a pooled direct request before
#: surfacing a redacted expiry error. Real auth errors are tiny; the cap keeps
#: a malicious endpoint from forcing an unbounded read.
_POOLED_ERROR_BODY_CAP = 65536


class RedirectTargetCache:
    """Process-local bounded reuse of resolved redirect targets.

    Keyed by immutable selection identity
    ``(provider, repository, revision, source file)`` — never by hostname
    alone, so different files or revisions never cross-use targets.

    Entries live only in memory on the owning fetcher (no artifact, telemetry,
    log, journal, or receipt persistence — signed query strings never leave
    this object). Every target is revalidated with :func:`validate_host`
    before use, with the same HTTPS/allowlist/SSRF rules as ordinary
    redirects. Stale entries are invalidated on expiry/auth/redirect
    semantics and re-resolved canonically inside existing retry budgets.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max(1, max_entries)
        self._lock = threading.RLock()
        self._targets: dict[tuple[str, str, str, str], str] = {}

    def get(self, key: tuple[str, str, str, str]) -> str | None:
        with self._lock:
            target = self._targets.get(key)
            if target is None:
                return None
            # LRU refresh without growth.
            del self._targets[key]
            self._targets[key] = target
            return target

    def put(self, key: tuple[str, str, str, str], target_url: str) -> None:
        validate_host(target_url)
        with self._lock:
            if key in self._targets:
                del self._targets[key]
            elif len(self._targets) >= self._max_entries:
                oldest = next(iter(self._targets))
                del self._targets[oldest]
            self._targets[key] = target_url

    def invalidate(self, key: tuple[str, str, str, str]) -> bool:
        with self._lock:
            return self._targets.pop(key, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._targets)


def _pool_bypassed_for_url(url: str) -> bool:
    """True when a configured proxy would handle ``url`` (pool must not bypass it)."""
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        return True
    if not proxies:
        return False
    scheme = (urllib.parse.urlparse(url).scheme or "").lower()
    proxy = proxies.get(scheme) or proxies.get("all")
    if not proxy:
        return False
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    try:
        return not urllib.request.proxy_bypass(host)
    except Exception:
        return True


class _PoolFallbackRequired(RuntimeError):
    """Internal: pooled direct attempt unavailable; use the urllib opener path."""


class PooledRangeResponse:
    """Response wrapper around one pooled ``http.client`` connection.

    Exposes the narrow surface acquisition uses (``status``, ``headers``,
    ``read``, ``geturl``, context manager). Returning the connection to the
    pool happens only after a fully consumed successful body; partial reads,
    errors, or ``Connection: close`` peers close the socket instead.
    """

    def __init__(
        self,
        *,
        conn: http.client.HTTPConnection,
        resp: http.client.HTTPResponse,
        url: str,
        pool: PooledRangeClient,
        pool_key: tuple[str, str, int],
        expected_length: int | None,
    ) -> None:
        self._conn = conn
        self._resp = resp
        self._url = url
        self._pool = pool
        self._pool_key = pool_key
        self._expected = expected_length
        self._read_bytes = 0
        self._closed = False
        self.status: int = resp.status
        self.headers: Any = resp.headers
        self.reason: str = resp.reason

    def geturl(self) -> str:
        return self._url

    def read(self, amount: int = -1) -> bytes:
        if self._closed:
            return b""
        if amount is not None and amount < 0:
            # Callers always use bounded reads; refuse unbounded pooling reads.
            raise ValueError("unbounded pooled read refused")
        chunk = self._resp.read(amount)
        self._read_bytes += len(chunk)
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        complete = (
            self._expected is not None and self._read_bytes >= self._expected and self.status < 400
        )
        try:
            if complete and self._resp.getheader("Connection", "").lower() != "close":
                self._pool._release(self._conn, self._pool_key)
            else:
                try:
                    self._conn.close()
                except Exception:
                    pass
        finally:
            self._pool._forget_in_use(self._conn, self._pool_key)

    def __enter__(self) -> PooledRangeResponse:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class PooledRangeClient:
    """Narrow stdlib connection reuse for cached-target direct Range GETs.

    One client per fetcher; connections pooled per ``(scheme, host, port)``
    with hard bounds and no cross-thread socket sharing (checkout is
    exclusive; a worker holds its connection only for one request). Only
    already-validated redirect targets are fetched here — canonical
    repository resolution (with redirect validation) stays on the urllib
    opener. Redirects are never followed inside the pool: a 3xx from a
    cached target surfaces as an error so the caller invalidates.
    """

    def __init__(
        self,
        *,
        max_per_host: int = 8,
        max_total: int = 32,
        observer: Any | None = None,
    ) -> None:
        self._max_per_host = max(1, max_per_host)
        self._max_total = max(1, max_total)
        self._observer = observer
        self._lock = threading.Lock()
        self._idle: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
        self._in_use: set[int] = set()
        self._per_host_in_use: dict[tuple[str, str, int], int] = {}
        self._total = 0
        self._closed = False

    def _note(self, method: str) -> None:
        observer = self._observer
        if observer is None:
            return
        try:
            getattr(observer, method)()
        except Exception:
            pass

    def _forget_in_use(self, conn: http.client.HTTPConnection, key: tuple[str, str, int]) -> None:
        with self._lock:
            self._in_use.discard(id(conn))
            self._per_host_in_use[key] = max(0, self._per_host_in_use.get(key, 1) - 1)

    def _release(self, conn: http.client.HTTPConnection, key: tuple[str, str, int]) -> None:
        with self._lock:
            self._in_use.discard(id(conn))
            self._per_host_in_use[key] = max(0, self._per_host_in_use.get(key, 1) - 1)
            if self._closed:
                try:
                    conn.close()
                except Exception:
                    pass
                self._total = max(0, self._total - 1)
                return
            idle = self._idle.setdefault(key, [])
            if len(idle) >= self._max_per_host:
                try:
                    conn.close()
                except Exception:
                    pass
                self._total = max(0, self._total - 1)
                return
            idle.append(conn)

    def _drop(self, conn: http.client.HTTPConnection, key: tuple[str, str, int]) -> None:
        """Forget one dead connection: uncheckout plus total adjustment."""
        with self._lock:
            self._in_use.discard(id(conn))
            self._per_host_in_use[key] = max(0, self._per_host_in_use.get(key, 1) - 1)
            self._total = max(0, self._total - 1)
        try:
            conn.close()
        except Exception:
            pass

    def request_direct(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> PooledRangeResponse:
        """GET ``url`` over a reused connection; never follows redirects."""
        validate_host(url)
        if _pool_bypassed_for_url(url):
            raise _PoolFallbackRequired("proxy configured; use opener path")
        parsed = urllib.parse.urlparse(url)
        if parsed.username or parsed.password:
            raise _PoolFallbackRequired("credentials in URL; use opener path")
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme not in ("https", "http"):
            raise _PoolFallbackRequired("unsupported scheme; use opener path")
        if scheme == "http" and host not in ("127.0.0.1", "localhost"):
            raise _PoolFallbackRequired("cleartext non-loopback; use opener path")
        port = parsed.port or (443 if scheme == "https" else 80)
        key = (scheme, host, port)
        selector = parsed.path or "/"
        if parsed.query:
            selector += "?" + parsed.query
        conn: http.client.HTTPConnection | None = None
        with self._lock:
            if self._closed:
                raise _PoolFallbackRequired("pool closed; use opener path")
            idle = self._idle.get(key, [])
            if idle:
                conn = idle.pop()
                self._in_use.add(id(conn))
                self._per_host_in_use[key] = self._per_host_in_use.get(key, 0) + 1
        if conn is not None:
            self._note("record_connection_reuse")
        else:
            with self._lock:
                if self._total >= self._max_total:
                    raise _PoolFallbackRequired("pool saturated; use opener path")
                if (
                    self._per_host_in_use.get(key, 0) + len(self._idle.get(key, []))
                    >= self._max_per_host
                ):
                    raise _PoolFallbackRequired("per-host pool full; use opener path")
                self._total += 1
            try:
                conn = (
                    http.client.HTTPSConnection(host, port, timeout=timeout)
                    if scheme == "https"
                    else http.client.HTTPConnection(host, port, timeout=timeout)
                )
            except Exception:
                with self._lock:
                    self._total = max(0, self._total - 1)
                raise
            with self._lock:
                self._in_use.add(id(conn))
                self._per_host_in_use[key] = self._per_host_in_use.get(key, 0) + 1
            self._note("record_connection_creation")
        assert conn is not None
        send_headers = dict(headers)
        send_headers.setdefault("Connection", "keep-alive")
        try:
            conn.request("GET", selector, headers=send_headers)
            resp = conn.getresponse()
        except Exception:
            self._drop(conn, key)
            raise
        if resp.status in CACHED_TARGET_REDIRECT_STATUSES or (
            resp.status in CACHED_TARGET_EXPIRY_STATUSES and resp.status != 200
        ):
            body = resp.read(_POOLED_ERROR_BODY_CAP + 1)
            self._drop(conn, key)
            raise urllib.error.HTTPError(
                url, resp.status, resp.reason, resp.headers, io.BytesIO(body)
            )
        if resp.status >= 400:
            body = resp.read(_POOLED_ERROR_BODY_CAP + 1)
            self._drop(conn, key)
            raise urllib.error.HTTPError(
                url, resp.status, resp.reason, resp.headers, io.BytesIO(body)
            )
        length_header = resp.getheader("Content-Length")
        try:
            expected = int(length_header) if length_header is not None else None
        except ValueError:
            expected = None
        if expected is not None and expected < 0:
            self._drop(conn, key)
            raise ValueError("negative response length")
        return PooledRangeResponse(
            conn=conn, resp=resp, url=url, pool=self, pool_key=key, expected_length=expected
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = {}
            dropped = sum(len(conns) for conns in idle.values())
            self._total = max(0, self._total - dropped)
        for conns in idle.values():
            for conn in conns:
                try:
                    conn.close()
                except Exception:
                    pass


class HuggingFaceTransport:
    """Hugging Face Hub discovery transport with strict snapshot pinning and budget guards."""

    def __init__(self, budget: TransportBudget) -> None:
        self.budget = budget
        self.opener = urllib.request.build_opener(SafeRedirectHandler(budget))

    def _make_request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        validate_host(url)
        self.budget.record_request()

        req_headers = {
            "User-Agent": "xlm-data-discovery/1.0",
            "Accept": "application/json, */*",
            **(headers or {}),
        }
        # Explicit unauthenticated public probing: do not send auth tokens automatically
        req = urllib.request.Request(url, headers=req_headers, method="GET")

        try:
            with self.opener.open(req, timeout=self.budget.per_request_timeout) as resp:
                requested_range = req_headers.get("Range")
                if requested_range:
                    start, end = requested_range.removeprefix("bytes=").split("-")
                    content_range = resp.headers.get("Content-Range", "")
                    if resp.status != 206 or not content_range.startswith(f"bytes {start}-{end}/"):
                        raise ValueError("server ignored or changed the requested byte range")
                return self.budget.read_body(resp, max_bytes or self.budget.max_bytes)
        except urllib.error.HTTPError as e:
            with e:
                self.budget.read_body(e, self.budget.max_bytes, retain=False)
            raise

    def get_snapshot_info(self, target: str, pinned_revision: str | None = None) -> SnapshotInfo:
        """Resolve dataset repository metadata and pin its immutable commit revision."""
        url = f"https://huggingface.co/api/datasets/{target}"
        if pinned_revision:
            url += f"?revision={urllib.parse.quote(pinned_revision, safe='')}"

        try:
            body = self._make_request(url, max_bytes=512 * 1024)
            data = json.loads(body.decode("utf-8"))

            sha = data.get("sha")
            if not sha:
                # If revision wasn't returned in top-level, treat as unpinned/missing revision
                return SnapshotInfo(
                    provider="huggingface",
                    target=target,
                    immutable_revision="",
                    is_not_found=False,
                    error_reason=(
                        "Provider API did not return an immutable commit SHA (revision_missing)."
                    ),
                )

            card_data = data.get("cardData") or {}
            declared_license = card_data.get("license") or data.get("license")
            if isinstance(declared_license, list) and declared_license:
                declared_license = declared_license[0]

            is_gated = bool(data.get("gated", False))
            is_private = bool(data.get("private", False))

            return SnapshotInfo(
                provider="huggingface",
                target=target,
                immutable_revision=str(sha),
                declared_license=str(declared_license) if declared_license else None,
                is_gated=is_gated,
                is_private=is_private,
                description=str(data.get("description", "")),
                card_data=card_data,
            )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return SnapshotInfo(
                    provider="huggingface",
                    target=target,
                    immutable_revision="",
                    is_gated=True,
                    is_private=(e.code == 403),
                    error_reason=(
                        f"Access denied ({e.code}): repository is gated or private. "
                        "Operator terms agreement not performed."
                    ),
                )
            elif e.code == 404:
                return SnapshotInfo(
                    provider="huggingface",
                    target=target,
                    immutable_revision="",
                    is_not_found=True,
                    error_reason=f"Repository '{target}' not found (HTTP 404).",
                )
            return SnapshotInfo(
                provider="huggingface",
                target=target,
                immutable_revision="",
                error_reason=f"Provider returned HTTP error {e.code}: {e.reason}",
            )
        except (BudgetExhaustedError, DeadlineExceededError, TimeoutError, HostNotAllowlistedError):
            raise
        except Exception as e:
            return SnapshotInfo(
                provider="huggingface",
                target=target,
                immutable_revision="",
                error_reason=f"Transport error during discovery: {e}",
            )

    def list_files(
        self, target: str, pinned_revision: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        """Fetch file inventory for the pinned commit revision."""
        encoded_rev = urllib.parse.quote(pinned_revision, safe="")
        url = f"https://huggingface.co/api/datasets/{target}/tree/{encoded_rev}"
        if cursor:
            url += f"?cursor={urllib.parse.quote(cursor, safe='')}"

        try:
            body = self._make_request(url, max_bytes=1024 * 1024)
            items = json.loads(body.decode("utf-8"))
            if not isinstance(items, list):
                return [], None, None

            files: list[dict[str, Any]] = []
            for item in items:
                if item.get("type") == "file":
                    files.append(
                        {
                            "path": item.get("path", ""),
                            "size": item.get("size", 0),
                            "oid": item.get("oid", ""),
                        }
                    )
            # HF tree endpoint does not always provide pagination cursor in body
            return files, None, len(files)
        except (BudgetExhaustedError, DeadlineExceededError, TimeoutError, HostNotAllowlistedError):
            raise
        except Exception:
            return [], None, None

    def fetch_stream_chunk(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        """Fetch a specific byte slice (e.g. Parquet metadata/footer) within budget."""
        headers: dict[str, str] = {}
        if byte_range:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        return self._make_request(url, headers=headers, max_bytes=max_bytes)


class HttpsManifestTransport:
    """HTTPS manifest discovery transport computing canonical SHA-256 digest of remote manifests."""

    def __init__(self, budget: TransportBudget) -> None:
        self.budget = budget
        self.opener = urllib.request.build_opener(SafeRedirectHandler(budget))

    def get_snapshot_info(self, target: str, pinned_revision: str | None = None) -> SnapshotInfo:
        validate_host(target)
        self.budget.record_request()
        try:
            req = urllib.request.Request(
                target,
                headers={"User-Agent": "xlm-data-discovery/1.0"},
                method="GET",
            )
            with self.opener.open(req, timeout=self.budget.per_request_timeout) as resp:
                content = self.budget.read_body(resp, 1024 * 1024)

            # Canonical immutable revision is the SHA-256 digest of the manifest content
            digest = hashlib.sha256(content).hexdigest()
            manifest_data = json.loads(content.decode("utf-8"))

            return SnapshotInfo(
                provider="https",
                target=target,
                immutable_revision=digest,
                declared_license=manifest_data.get("license"),
                description=manifest_data.get("description", ""),
                card_data=manifest_data.get("metadata", {}),
            )
        except urllib.error.HTTPError as e:
            with e:
                self.budget.read_body(e, self.budget.max_bytes, retain=False)
            return SnapshotInfo(
                provider="https",
                target=target,
                immutable_revision="",
                is_not_found=(e.code == 404),
                error_reason=f"HTTP Error {e.code}: {e.reason}",
            )
        except (BudgetExhaustedError, DeadlineExceededError, TimeoutError, HostNotAllowlistedError):
            raise
        except Exception as e:
            return SnapshotInfo(
                provider="https",
                target=target,
                immutable_revision="",
                error_reason=f"HTTPS manifest discovery error: {e}",
            )

    def list_files(
        self, target: str, pinned_revision: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        return [], None, 0

    def fetch_stream_chunk(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        headers = {"User-Agent": "xlm-data-discovery/1.0"}
        if byte_range:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"

        transport = HuggingFaceTransport(self.budget)
        transport.opener = self.opener
        return transport._make_request(url, headers=headers, max_bytes=max_bytes or 64 * 1024)


class LocalManifestTransport:
    """Local filesystem manifest transport enforcing root boundary constraints."""

    DISALLOWED_EXTENSIONS: frozenset[str] = frozenset(
        [".exe", ".dll", ".so", ".dylib", ".bat", ".sh", ".cmd", ".ps1"]
    )

    def __init__(self, allowed_roots: list[Path]) -> None:
        self.allowed_roots = [r.resolve() for r in allowed_roots]

    def _validate_path(self, path: Path) -> Path:
        resolved = path.resolve()
        # Ensure path is inside at least one allowed root
        is_safe = any(resolved == root or root in resolved.parents for root in self.allowed_roots)
        if not is_safe:
            raise InsecurePathError(
                f"Local path '{resolved}' escapes allowed root directories: {self.allowed_roots}"
            )
        if resolved.suffix.lower() in self.DISALLOWED_EXTENSIONS:
            raise PermissionError(
                f"Executable file '{resolved.name}' is rejected by security policy."
            )
        return resolved

    def get_snapshot_info(self, target: str, pinned_revision: str | None = None) -> SnapshotInfo:
        manifest_path = self._validate_path(Path(target))
        if not manifest_path.is_file():
            return SnapshotInfo(
                provider="local",
                target=target,
                immutable_revision="",
                is_not_found=True,
                error_reason=f"Local manifest not found: {manifest_path}",
            )

        if manifest_path.stat().st_size > 1024**2:
            raise BudgetExhaustedError("local discovery manifest exceeds 1 MiB")
        content = manifest_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()

        try:
            import yaml

            data: Any = yaml.safe_load(content.decode("utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

        return SnapshotInfo(
            provider="local",
            target=target,
            immutable_revision=digest,
            declared_license=data.get("license"),
            description=data.get("description", ""),
            card_data=data.get("metadata", {}),
        )

    def list_files(
        self, target: str, pinned_revision: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        manifest_path = self._validate_path(Path(target))
        if not manifest_path.is_file():
            return [], None, 0

        try:
            import yaml

            if manifest_path.stat().st_size > 1024**2:
                raise BudgetExhaustedError("local discovery manifest exceeds 1 MiB")
            data: Any = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw_files = data.get("files", [])
                out_files: list[dict[str, Any]] = []
                for rf in raw_files:
                    if isinstance(rf, dict):
                        f_path = self._validate_path(manifest_path.parent / rf.get("path", ""))
                        f_size = f_path.stat().st_size if f_path.is_file() else 0
                        out_files.append({"path": rf.get("path"), "size": f_size})
                return out_files, None, len(out_files)
        except (BudgetExhaustedError, InsecurePathError):
            raise
        except Exception:
            pass
        return [], None, 0

    def fetch_stream_chunk(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        p = self._validate_path(Path(url))
        if not p.is_file():
            return b""
        with p.open("rb") as f:
            if byte_range:
                f.seek(byte_range[0])
                length = byte_range[1] - byte_range[0] + 1
                return f.read(min(length, max_bytes or length))
            return f.read(max_bytes or 64 * 1024)


class MockStreamingTransport:
    """Configurable offline transport simulating streaming, redirects, gating, and chunk limits."""

    def __init__(
        self,
        snapshot_info: SnapshotInfo,
        files: list[dict[str, Any]] | None = None,
        sample_chunks: list[bytes] | None = None,
        budget: TransportBudget | None = None,
        redirect_target: str | None = None,
    ) -> None:
        self.snapshot_info = snapshot_info
        self.files = files or []
        self.sample_chunks = sample_chunks or []
        self.budget = budget or TransportBudget()
        self.redirect_target = redirect_target

    def get_snapshot_info(self, target: str, pinned_revision: str | None = None) -> SnapshotInfo:
        self.budget.record_request()
        if self.redirect_target:
            validate_host(self.redirect_target)
        return self.snapshot_info

    def list_files(
        self, target: str, pinned_revision: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        self.budget.record_request()
        return self.files, None, len(self.files)

    def fetch_stream_chunk(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        max_bytes: int | None = None,
    ) -> bytes:
        self.budget.record_request()
        if self.redirect_target:
            validate_host(self.redirect_target)

        combined = bytearray()
        for chunk in self.sample_chunks:
            self.budget.record_bytes(len(chunk))
            combined.extend(chunk)
            if max_bytes and len(combined) >= max_bytes:
                return bytes(combined[:max_bytes])
        return bytes(combined)
