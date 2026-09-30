"""Verified whole-source-Parquet transport and local selected-record derivation.

One sequential HTTP stream per file into bounded fast scratch, resumable from a
durable verified prefix, then an exclusive hashed copy into the durable store.
Records are derived from the local file by the same projected decode and the
same serialization as the certified window reader, so the selected-record bytes
are identical; only the I/O source differs (a local file instead of HTTP
ranges). Nothing here selects, filters or rewrites a record.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from xlm.artifacts.manifest import ensure_plain_path
from xlm.data.acquisition.disk import AtomicFileWriter
from xlm.data.acquisition.plan import SourceDriftDetectedError
from xlm.data.acquisition.projection import (
    ProjectionRefusal,
    parquet_field_leaves,
    resolve_projection,
)
from xlm.data.acquisition.records import LOCATOR_FIELD, RecordLimitError
from xlm.data.sources.transport import (
    HostNotAllowlistedError,
    SafeRedirectHandler,
    validate_host,
)

MIB = 1024**2
#: Body read size of the single sequential stream.
READ_BYTES = MIB
#: Durable checkpoint interval: one data fsync and one state write per window.
CHECKPOINT_BYTES = 64 * MIB
STATE_VERSION = 1
IDENTITY_KIND = "verified_source_parquet"
IDENTITY_VERSION = 1
#: Decode batch rows; order is preserved, so output bytes do not depend on it.
DECODE_BATCH_ROWS = 512
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_SHA256_ETAG = re.compile(r'^"([0-9a-f]{64})"$')
_LINKED_HEADERS = ("X-Linked-ETag", "X-Linked-Size", "X-Repo-Commit")
_MAX_RETRY_DELAY_SECONDS = 60.0


class SourceTransferError(RuntimeError):
    """A bound, identity or integrity rule of the whole-file transport was violated."""


class TransferCancelledError(RuntimeError):
    """A sibling file failed; this stream stopped at a durable checkpoint."""


class ScratchCapError(RuntimeError):
    """The scratch byte cap or the scratch volume reserve would be exceeded."""


@dataclass(frozen=True)
class TransferLimits:
    """Bounds of one file stream. Every value is a hard refusal, not a target."""

    max_file_bytes: int
    max_transfer_bytes: int
    max_requests: int
    max_retries: int
    request_timeout_seconds: float
    deadline_seconds: float
    #: A 64-hex strong ETag must equal the locally computed SHA-256.
    require_etag_sha256: bool = True

    def __post_init__(self) -> None:
        if (
            min(self.max_file_bytes, self.max_transfer_bytes, self.max_requests) < 1
            or self.max_retries < 0
            or self.request_timeout_seconds <= 0
            or self.deadline_seconds <= 0
            or self.max_transfer_bytes < self.max_file_bytes
        ):
            raise ValueError("transfer limits must be positive and cover one whole file")


@dataclass(frozen=True)
class SourceIdentity:
    """What identifies one immutable upstream file after it was transferred."""

    etag: str
    length: int
    sha256: str
    #: True/False when the strong ETag is a 64-hex digest; None when it is not.
    etag_is_content_sha256: bool | None
    linked_etag: str | None = None
    linked_size: int | None = None
    repo_commit: str | None = None


@dataclass(frozen=True)
class TransferResult:
    identity: SourceIdentity
    path: Path
    #: Response-body bytes of every attempt, including bytes received again.
    transferred_bytes: int
    requests: int
    redirects: int
    retries: int
    #: Verified prefix bytes adopted from the state an earlier process left behind.
    resumed_bytes: int
    seconds: float
    cache_hit: bool


class TransferMeter:
    """Thread-safe cumulative response-body bytes and requests of one batch."""

    def __init__(self, max_bytes: int, max_requests: int, *, bytes_used: int = 0) -> None:
        self._lock = threading.Lock()
        self.max_bytes, self.max_requests = max_bytes, max_requests
        self.bytes, self.requests = bytes_used, 0

    def charge(self, amount: int) -> None:
        with self._lock:
            if self.bytes + amount > self.max_bytes:
                raise SourceTransferError("batch transfer byte ceiling reached")
            self.bytes += amount

    def request(self) -> None:
        with self._lock:
            if self.requests >= self.max_requests:
                raise SourceTransferError("batch request ceiling reached")
            self.requests += 1


class ScratchBudget:
    """Hard byte cap over one scratch root, shared by concurrent file streams.

    A reservation is the largest size its file may reach. Files below the root
    that no reservation owns (another campaign, an abandoned attempt) count
    against the cap and are never deleted here.
    """

    def __init__(
        self,
        root: Path,
        cap_bytes: int,
        min_free_bytes: int,
        *,
        disk_free: Callable[[Path], int] | None = None,
    ) -> None:
        if cap_bytes < 1 or min_free_bytes < 0:
            raise ValueError("scratch cap must be positive and the reserve non-negative")
        ensure_plain_path(root)
        self.root, self.cap_bytes, self.min_free_bytes = root, cap_bytes, min_free_bytes
        self._disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self._lock = threading.Lock()
        self._reserved: dict[str, tuple[int, Path]] = {}
        self.peak_reserved_bytes = 0

    def occupied(self) -> int:
        """Bytes of every regular file below the root (partials, staging, leftovers)."""
        total = 0
        for directory, _, names in os.walk(self.root):
            for name in names:
                try:
                    total += (Path(directory) / name).stat().st_size
                except OSError:
                    continue
        return total

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def reserved(self) -> int:
        with self._lock:
            return sum(amount for amount, _ in self._reserved.values())

    def reserve(self, key: str, amount: int, path: Path) -> bool:
        """Reserve ``amount`` bytes for ``path``; False when it does not fit now."""
        if amount < 1:
            raise ValueError("scratch reservation must be positive")
        if amount > self.cap_bytes:
            raise ScratchCapError("one file reservation exceeds the whole scratch cap")
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if key in self._reserved:
                raise ValueError(f"scratch key '{key}' is already reserved")
            owned = sum(self._size(owner) for _, owner in self._reserved.values())
            reserved = sum(value for value, _ in self._reserved.values())
            foreign = max(0, self.occupied() - owned - self._size(path))
            if foreign + reserved + amount > self.cap_bytes:
                return False
            unwritten = reserved - owned + amount - self._size(path)
            if self._disk_free(self.root) - max(0, unwritten) < self.min_free_bytes:
                return False
            self._reserved[key] = (amount, path)
            self.peak_reserved_bytes = max(self.peak_reserved_bytes, reserved + amount)
            return True

    def shrink(self, key: str, amount: int) -> None:
        """Lower a reservation to the declared length; it can never grow."""
        with self._lock:
            current, path = self._reserved[key]
            if amount > current:
                raise ScratchCapError("declared length exceeds its scratch reservation")
            self._reserved[key] = (amount, path)

    def release(self, key: str) -> None:
        with self._lock:
            self._reserved.pop(key, None)


class _ObservedRedirects(SafeRedirectHandler):
    """Allowlist-checked redirects that are counted and whose link headers are kept."""

    def __init__(self) -> None:
        super().__init__()
        self.hops = 0
        self.linked: dict[str, str] = {}

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        self.hops += 1
        for name in _LINKED_HEADERS:
            value = headers.get(name)
            if value:
                self.linked[name] = str(value)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path, limit: int = MIB) -> dict[str, Any]:
    if path.stat().st_size > limit:
        raise SourceTransferError(f"'{path.name}' exceeds its bounded size")
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise SourceTransferError(f"'{path.name}' is not a JSON object")
    return value


def file_sha256(path: Path) -> tuple[str, int]:
    """SHA-256 and size of one file in bounded reads."""
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(READ_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class _Download:
    def __init__(
        self,
        url: str,
        partial: Path,
        state_path: Path,
        name: str,
        limits: TransferLimits,
        *,
        revision: str | None,
        expected_sha256: str | None,
        scratch: ScratchBudget | None,
        scratch_key: str | None,
        meter: TransferMeter | None,
        cancel: threading.Event | None,
        sleep: Callable[[float], None],
    ) -> None:
        validate_host(url)
        for path in (partial, state_path):
            ensure_plain_path(path)
        self.url, self.partial, self.state_path, self.name = url, partial, state_path, name
        self.limits, self.revision, self.expected_sha256 = limits, revision, expected_sha256
        self.scratch, self.meter, self.cancel, self.sleep = scratch, meter, cancel, sleep
        self.scratch_key = scratch_key or name
        self.state: dict[str, Any] = {}
        self.digest = hashlib.sha256()
        self.offset = self.transferred = self.requests = self.redirects = self.retries = 0
        self.resumed = 0
        self.started = time.monotonic()

    # ------------------------------------------------------------ durable state

    def _save(self, **changes: Any) -> None:
        self.state.update(changes)
        self.state.update(requests=self.requests, redirects=self.redirects, retries=self.retries)
        _write_json(self.state_path, self.state)

    def _restart_from_zero(self) -> None:
        if self.partial.exists():
            AtomicFileWriter.truncate_to_length(self.partial, 0)
        self.offset, self.digest = 0, hashlib.sha256()
        self._save(verified_bytes=0, prefix_sha256=self.digest.hexdigest(), complete=False)

    def _restore(self) -> None:
        """Adopt only the durably verified prefix this state file owns."""
        if not self.state_path.exists():
            if self.partial.exists():
                raise SourceTransferError(f"unowned partial file for '{self.name}'; refusing reuse")
            self.state = {
                "version": STATE_VERSION,
                "name": self.name,
                "url": self.url,
                "verified_bytes": 0,
                "prefix_sha256": self.digest.hexdigest(),
                "charged_bytes": 0,
                "complete": False,
            }
            return
        self.state = _read_json(self.state_path)
        if (self.state.get("version"), self.state.get("name"), self.state.get("url")) != (
            STATE_VERSION,
            self.name,
            self.url,
        ):
            raise SourceTransferError(f"transfer state of '{self.name}' belongs to another source")
        # A crash leaves the whole in-flight window charged: conservative, never lost.
        self.transferred = int(self.state.get("charged_bytes", 0))
        self.requests = int(self.state.get("requests", 0))
        self.redirects = int(self.state.get("redirects", 0))
        self.retries = int(self.state.get("retries", 0))
        verified = int(self.state.get("verified_bytes", 0))
        size = self.partial.stat().st_size if self.partial.exists() else 0
        if size < verified:
            self._restart_from_zero()
            return
        if size > verified:
            # Bytes past the last durable checkpoint are never trusted.
            AtomicFileWriter.truncate_to_length(self.partial, verified)
        if verified:
            digest = hashlib.sha256()
            with self.partial.open("rb") as stream:
                while chunk := stream.read(READ_BYTES):
                    digest.update(chunk)
            if digest.hexdigest() != self.state.get("prefix_sha256"):
                self._restart_from_zero()
                return
            self.digest = digest
        self.offset = self.resumed = verified

    # ----------------------------------------------------------------- bounds

    def _check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise TransferCancelledError(f"'{self.name}' cancelled after a sibling failure")
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            raise SourceTransferError(
                f"'{self.name}' exceeded its {self.limits.deadline_seconds:.0f}s file deadline"
            )

    def _count_request(self) -> None:
        if self.requests >= self.limits.max_requests:
            raise SourceTransferError(f"'{self.name}' reached its request ceiling")
        self.requests += 1
        if self.meter is not None:
            self.meter.request()

    def _charge(self, amount: int) -> None:
        if self.transferred + amount > self.limits.max_transfer_bytes:
            raise SourceTransferError(f"'{self.name}' reached its transfer byte ceiling")
        if self.meter is not None:
            self.meter.charge(amount)
        self.transferred += amount

    # --------------------------------------------------------------- transfer

    def _checkpoint(self, output: Any, remaining: int) -> None:
        output.flush()
        os.fsync(output.fileno())
        self._save(
            verified_bytes=self.offset,
            prefix_sha256=self.digest.copy().hexdigest(),
            charged_bytes=self.transferred + min(CHECKPOINT_BYTES, remaining),
        )

    def _bind(self, etag: str, total: int, linked: Mapping[str, str]) -> None:
        if total < 12 or total > self.limits.max_file_bytes:
            raise SourceTransferError(
                f"'{self.name}' declares {total} bytes, outside the per-file bound "
                f"of {self.limits.max_file_bytes}"
            )
        bound = (self.state.get("etag"), self.state.get("length"))
        if bound != (None, None) and bound != (etag, total):
            raise SourceDriftDetectedError(
                f"source validator of '{self.name}' changed between requests"
            )
        size, commit = linked.get("X-Linked-Size"), linked.get("X-Repo-Commit")
        if size is not None and (not size.isdigit() or int(size) != total):
            raise SourceDriftDetectedError(f"'{self.name}' length differs from its linked size")
        if commit is not None and self.revision is not None and commit != self.revision:
            raise SourceDriftDetectedError(f"'{self.name}' resolved to another revision")
        if bound == (None, None):
            self._save(
                etag=etag,
                length=total,
                linked_etag=linked.get("X-Linked-ETag"),
                linked_size=None if size is None else int(size),
                repo_commit=commit,
            )
        if self.scratch is not None:
            self.scratch.shrink(self.scratch_key, total)

    def _attempt(self) -> None:
        self._check()
        headers = {"User-Agent": "xlm-acquisition/2", "Accept-Encoding": "identity"}
        if self.offset:
            headers["Range"] = f"bytes={self.offset}-"
            headers["If-Range"] = str(self.state["etag"])
        redirects = _ObservedRedirects()
        opener = urllib.request.build_opener(redirects)
        self._count_request()
        try:
            response = opener.open(
                urllib.request.Request(self.url, headers=headers),
                timeout=self.limits.request_timeout_seconds,
            )
        finally:
            for _ in range(redirects.hops):
                self.redirects += 1
                self._count_request()
        with response:
            etag = response.headers.get("ETag")
            if not etag or etag.startswith("W/"):
                raise SourceTransferError(f"'{self.name}' has no stable strong ETag")
            declared = response.headers.get("Content-Length")
            if response.status == 206:
                match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
                if (
                    not self.offset
                    or not match
                    or int(match[1]) != self.offset
                    or int(match[2]) != int(match[3]) - 1
                    or (declared is not None and int(declared) != int(match[3]) - self.offset)
                ):
                    raise SourceTransferError(f"'{self.name}': inconsistent Content-Range")
                total = int(match[3])
            elif response.status == 200:
                if declared is None or not declared.isdigit():
                    raise SourceTransferError(f"'{self.name}': unframed body refused")
                total = int(declared)
            else:
                raise SourceTransferError(
                    f"'{self.name}': unexpected response status {response.status}"
                )
            self._bind(etag, total, redirects.linked)
            if response.status == 200 and self.offset:
                # Same validator but the range was ignored: only a restart is safe.
                self._restart_from_zero()
            remaining = total - self.offset
            with self.partial.open("ab") as output:
                self._checkpoint(output, remaining)
                window = 0
                while remaining:
                    self._check()
                    amount = min(READ_BYTES, remaining)
                    self._charge(amount)
                    chunk = response.read(amount)
                    if not chunk:
                        raise http.client.IncompleteRead(b"", remaining)
                    if len(chunk) > amount:
                        raise SourceTransferError("transport returned more bytes than requested")
                    self.transferred -= amount - len(chunk)
                    output.write(chunk)
                    self.digest.update(chunk)
                    self.offset += len(chunk)
                    remaining -= len(chunk)
                    window += len(chunk)
                    if window >= CHECKPOINT_BYTES:
                        self._checkpoint(output, remaining)
                        window = 0
                self._checkpoint(output, 0)

    def _after_failure(self, error: BaseException, attempt: int) -> None:
        delay = min(0.25 * 2**attempt, 8.0)
        if isinstance(error, urllib.error.HTTPError):
            code = error.code
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
            if code in (412, 416):
                raise SourceDriftDetectedError(
                    f"'{self.name}': HTTP {code} on a verified continuation"
                ) from error
            if code != 429 and code < 500:
                raise SourceTransferError(f"'{self.name}': HTTP {code}") from error
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        wait = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (TypeError, ValueError):
                        wait = delay
                    delay = max(delay, wait)
        if attempt >= self.limits.max_retries:
            raise SourceTransferError(
                f"'{self.name}': attempts exhausted ({type(error).__name__})"
            ) from error
        elapsed = time.monotonic() - self.started
        if delay > _MAX_RETRY_DELAY_SECONDS or elapsed + delay > self.limits.deadline_seconds:
            raise SourceTransferError(
                f"'{self.name}': retry delay exceeds the bounded allowance"
            ) from error
        self.retries += 1
        self._save()
        self.sleep(delay)
        # Resume strictly from the last durable checkpoint.
        verified = int(self.state.get("verified_bytes", 0))
        if self.partial.exists() and self.partial.stat().st_size != verified:
            AtomicFileWriter.truncate_to_length(self.partial, verified)
        digest = hashlib.sha256()
        if verified:
            with self.partial.open("rb") as stream:
                while chunk := stream.read(READ_BYTES):
                    digest.update(chunk)
        self.offset, self.digest = verified, digest

    def _result(self, cache_hit: bool) -> TransferResult:
        etag, sha256 = str(self.state["etag"]), str(self.state["sha256"])
        match = _SHA256_ETAG.fullmatch(etag)
        return TransferResult(
            identity=SourceIdentity(
                etag=etag,
                length=int(self.state["length"]),
                sha256=sha256,
                etag_is_content_sha256=None if match is None else match[1] == sha256,
                linked_etag=self.state.get("linked_etag"),
                linked_size=self.state.get("linked_size"),
                repo_commit=self.state.get("repo_commit"),
            ),
            path=self.partial,
            transferred_bytes=self.transferred,
            requests=self.requests,
            redirects=self.redirects,
            retries=self.retries,
            resumed_bytes=self.resumed,
            seconds=time.monotonic() - self.started,
            cache_hit=cache_hit,
        )

    def _finish(self) -> TransferResult:
        sha256 = self.digest.hexdigest()
        size = self.partial.stat().st_size
        match = _SHA256_ETAG.fullmatch(str(self.state["etag"]))
        refusal = None
        if size != int(self.state["length"]) or size != self.offset:
            refusal = "size differs from the declared length"
        elif self.expected_sha256 is not None and sha256 != self.expected_sha256.lower():
            refusal = "content differs from its independent expected digest"
        elif match is not None and match[1] != sha256 and self.limits.require_etag_sha256:
            refusal = "content differs from its 64-hex strong ETag"
        if refusal is not None:
            self._restart_from_zero()
            raise SourceTransferError(f"'{self.name}': {refusal}")
        self._save(sha256=sha256, complete=True, charged_bytes=self.transferred)
        return self._result(cache_hit=False)

    def run(self) -> TransferResult:
        self._restore()
        if self.state.get("complete"):
            sha256, size = file_sha256(self.partial)
            if (sha256, size) == (self.state.get("sha256"), self.state.get("length")):
                if self.scratch is not None:
                    self.scratch.shrink(self.scratch_key, size)
                return self._result(cache_hit=True)
            self._restart_from_zero()
        for attempt in range(self.limits.max_retries + 1):
            try:
                self._attempt()
                return self._finish()
            except (HostNotAllowlistedError, SourceDriftDetectedError):
                raise
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                self._after_failure(exc, attempt)
        raise SourceTransferError(f"'{self.name}': attempts exhausted")


def download_source(
    url: str,
    partial: Path,
    state_path: Path,
    *,
    name: str,
    limits: TransferLimits,
    revision: str | None = None,
    expected_sha256: str | None = None,
    scratch: ScratchBudget | None = None,
    scratch_key: str | None = None,
    meter: TransferMeter | None = None,
    cancel: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TransferResult:
    """Stream one whole source file into scratch and verify it.

    One GET (plus its allowlisted redirect) when nothing fails. An interrupted
    stream resumes with ``Range`` and ``If-Range`` from the last fsynced
    checkpoint whose prefix hash still matches; anything else restarts from
    zero with the spent transfer kept charged. A changed validator, length or
    resolved revision is source drift and is never retried.
    """
    return _Download(
        url,
        partial,
        state_path,
        name,
        limits,
        revision=revision,
        expected_sha256=expected_sha256,
        scratch=scratch,
        scratch_key=scratch_key,
        meter=meter,
        cancel=cancel,
        sleep=sleep,
    ).run()


# ------------------------------------------------------------------ durable raw


def identity_path(destination: Path) -> Path:
    return destination.with_name(destination.name + ".identity.json")


def identity_record(
    identity: SourceIdentity, *, source_file: str, repository: str, revision: str
) -> dict[str, Any]:
    """The sidecar stored next to an immutable source Parquet (no timestamps)."""
    return {
        "kind": IDENTITY_KIND,
        "version": IDENTITY_VERSION,
        "source_file": source_file,
        "repository": repository,
        "revision": revision,
        **asdict(identity),
    }


def check_parquet_magic(path: Path) -> None:
    size = path.stat().st_size
    with path.open("rb") as stream:
        head = stream.read(4)
        stream.seek(max(0, size - 4))
        tail = stream.read(4)
    if size < 12 or head != b"PAR1" or tail != b"PAR1":
        raise SourceTransferError(f"'{path.name}' is not a complete Parquet file")


def promote_source(source: Path, destination: Path, record: Mapping[str, Any]) -> bool:
    """Copy a verified scratch file into the durable store; True when it was written.

    The copy is hashed while it is written and must reproduce the verified
    SHA-256 before it is linked, exclusively, under its final name. An existing
    destination is never replaced: it must hash to the same identity.
    """
    ensure_plain_path(destination)
    sidecar = identity_path(destination)
    expected, length = str(record["sha256"]), int(record["length"])
    if destination.exists():
        if file_sha256(destination) != (expected, length):
            raise SourceTransferError(
                f"durable '{destination.name}' differs from the verified source; refusing overwrite"
            )
        if sidecar.exists():
            if _read_json(sidecar) != json.loads(json.dumps(dict(record))):
                raise SourceTransferError(f"durable identity of '{destination.name}' differs")
        else:
            _write_json(sidecar, record)
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
    digest, size = hashlib.sha256(), 0
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            while chunk := reader.read(READ_BYTES):
                writer.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if (digest.hexdigest(), size) != (expected, length):
            raise SourceTransferError(f"copy of '{destination.name}' differs from its source")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    _write_json(sidecar, record)
    return True


def load_durable_source(destination: Path) -> dict[str, Any] | None:
    """A durable source and its sidecar, rehashed; None when it is not there yet."""
    sidecar = identity_path(destination)
    if not destination.exists() or not sidecar.exists():
        return None
    record = _read_json(sidecar)
    if record.get("kind") != IDENTITY_KIND or record.get("version") != IDENTITY_VERSION:
        raise SourceTransferError(f"'{sidecar.name}' is not a verified-source identity")
    if file_sha256(destination) != (record.get("sha256"), record.get("length")):
        raise SourceTransferError(f"durable '{destination.name}' no longer matches its identity")
    return record


# ------------------------------------------------------------- local records


def _canonical_line(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def located_record(record: Mapping[str, Any], locator: Mapping[str, Any]) -> tuple[bytes, bytes]:
    """``(canonical record line, selected-record line)`` of one freshly decoded row.

    Byte-identical to ``encode_record`` followed by ``selected_record``. Both
    lines are produced here in one step from a record nothing else can reach,
    so the mutation snapshots those two functions need between their calls are
    not needed; the locator pair is spliced only where sorted compact JSON
    places it, and anything else takes the full encode.
    """
    if LOCATOR_FIELD in record:
        raise ValueError("source record conflicts with reserved acquisition locator field")
    raw = _canonical_line(record)
    value = {**locator, "original_record_sha256": hashlib.sha256(raw).hexdigest()}
    if record and all(type(key) is str and key > LOCATOR_FIELD for key in record):
        pair = _canonical_line({LOCATOR_FIELD: value})[1:-2]
        return raw, b"{" + pair + b"," + raw[1:]
    return raw, _canonical_line({**record, LOCATOR_FIELD: value})


def selected_payloads(
    path: Path,
    *,
    source_file: str,
    locator: Mapping[str, Any],
    etag: str,
    columns: Sequence[str],
    max_record_bytes: int,
    max_parser_bytes: int,
    max_decoded_bytes: int,
    row_range: tuple[int, int] | None = None,
    counters: dict[str, int] | None = None,
) -> Iterator[tuple[int, bytes]]:
    """``(row_index, selected-record line)`` for the rows of a local source Parquet.

    Same logical projection, decode, canonical record serialization and locator
    as the certified window reader, so each line is byte-identical to the one
    that reader writes for the same row and selection identity.
    """
    parquet = pq.ParquetFile(
        path,
        pre_buffer=False,
        thrift_string_size_limit=max_parser_bytes,
        thrift_container_size_limit=max_parser_bytes,
    )
    try:
        logical = list(
            resolve_projection(parquet_field_leaves(parquet), tuple(columns)).logical_fields
        )
    except ProjectionRefusal as exc:
        raise RecordLimitError(f"projection refused for '{source_file}': {exc}") from exc
    start, stop = row_range or (0, int(parquet.metadata.num_rows))
    if not 0 <= start < stop <= int(parquet.metadata.num_rows):
        raise ValueError("selected row range extends beyond Parquet corpus")
    decoded = 0
    base = 0
    try:
        for group in range(parquet.num_row_groups):
            rows = int(parquet.metadata.row_group(group).num_rows)
            if base + rows <= start or base >= stop:
                base += rows
                continue
            local = 0
            for batch in parquet.iter_batches(
                batch_size=DECODE_BATCH_ROWS,
                row_groups=[group],
                columns=logical,
                use_threads=False,
            ):
                decoded += int(batch.nbytes)
                if decoded > max_decoded_bytes:
                    raise RecordLimitError(f"'{source_file}' exceeds its decoded byte bound")
                low = max(0, start - base - local)
                high = min(batch.num_rows, stop - base - local)
                if low < high:
                    for offset, record in enumerate(
                        batch.slice(low, high - low).to_pylist(), start=low
                    ):
                        raw, payload = located_record(
                            record,
                            {
                                "row_index": base + local + offset,
                                "row_group": group,
                                "row_in_group": local + offset,
                                "format": "parquet",
                                "etag": etag,
                                "original_record_hash_convention": (
                                    "canonical JSON serialization, not compressed bytes"
                                ),
                                **locator,
                                "source_file": source_file,
                            },
                        )
                        if len(raw) > max_record_bytes:
                            raise RecordLimitError("Parquet record byte bound exceeded")
                        if len(payload) > max_record_bytes + 8192:
                            raise RecordLimitError(
                                "selected record plus locator exceeds bounded serialization"
                            )
                        yield base + local + offset, payload
                local += batch.num_rows
                if base + local >= stop:
                    break
            base += rows
    finally:
        if counters is not None:
            counters["decoded_bytes"] = decoded
        parquet.close()
