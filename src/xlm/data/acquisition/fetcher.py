"""Existing bounded fetcher: durable budgets, verified originals and explicit selections."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import math
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import compute_file_sha256
from xlm.data.acquisition.disk import (
    AtomicFileWriter,
    CapacityLease,
    DiskCeilingExceededError,
    StorageCapacityManager,
    grown_window,
)
from xlm.data.acquisition.perf import PerfTelemetry
from xlm.data.acquisition.plan import (
    AcquisitionMode,
    AcquisitionPlan,
    SourceDriftDetectedError,
    validate_plan_authorization,
)
from xlm.data.acquisition.progress import (
    AcquisitionState,
    ProgressCorruptionError,
    ProgressJournal,
    apply_file_progress,
)
from xlm.data.acquisition.publication import publish_output, reconcile_publications
from xlm.data.acquisition.records import RecordLimitError, inspect_records
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    PooledRangeClient,
    RedirectTargetCache,
    SafeRedirectHandler,
    TransportBudget,
    _PoolFallbackRequired,
    validate_host,
)

CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")

#: Whole-file body read size (unchanged historical value: identical reads and totals).
WHOLE_FILE_READ_BYTES = 65536
#: Execution-only cap for whole-file durable accounting/checkpoint windows.
#: Windows grow geometrically from 64 KiB (see ``grown_window``): one data fsync
#: plus one journal write per window; a crash re-downloads and leaves charged at
#: most max(64 KiB, bytes already verified in this response), never above the cap.
WHOLE_FILE_WINDOW_BYTES = 16 * 1024 * 1024

#: Pooled direct statuses that invalidate a cached redirect target and
#: re-resolve canonically inside existing retry budgets. Never logged with
#: URLs: the surfaced error carries file plus status only.
_CACHED_TARGET_EXPIRY_CODES: frozenset[int] = frozenset([400, 401, 403, 404, 410])
_CACHED_TARGET_REDIRECT_CODES: frozenset[int] = frozenset([301, 302, 303, 307, 308])


class _CachedTargetExpiredError(OSError):
    """Internal: cached redirect target expired; retry canonically.

    Carries file plus status only — never the signed target URL — so journal
    error reasons cannot persist query signatures or tokens.
    """


class AcquisitionLockHeldError(RuntimeError):
    """Another process owns execution of this plan."""


class DecompressionBombError(RuntimeError):
    """Decompression ceiling or expansion ratio exceeded."""


class _CountingReader(io.RawIOBase):
    def __init__(self, raw: Any) -> None:
        self.raw, self.bytes_read = raw, 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            raise ValueError("unbounded compressed read refused")
        value = bytes(self.raw.read(n))
        self.bytes_read += len(value)
        return value


class BoundedDecompressor:
    def __init__(
        self,
        capacity_mgr: StorageCapacityManager,
        max_ratio: float = 15.0,
        max_decompressed_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.capacity_mgr, self.max_ratio = capacity_mgr, max_ratio
        self.max_decompressed_bytes = max_decompressed_bytes
        self.compressed_read = self.decompressed_written = 0

    def decompress_stream(self, input_stream: Any, output_stream: Any) -> int:
        counting = _CountingReader(input_stream)
        with gzip.GzipFile(fileobj=counting, mode="rb") as stream:
            while True:
                chunk = stream.read(
                    min(65536, self.max_decompressed_bytes - self.decompressed_written + 1)
                )
                if not chunk:
                    break
                self.compressed_read = counting.bytes_read
                self.decompressed_written += len(chunk)
                try:
                    self.capacity_mgr.record_decompressed(len(chunk))
                except BudgetExhaustedError as exc:
                    raise DecompressionBombError(str(exc)) from exc
                if (
                    self.decompressed_written > self.max_decompressed_bytes
                    or self.decompressed_written > max(1, self.compressed_read) * self.max_ratio
                ):
                    raise DecompressionBombError("decompression byte/ratio limit exceeded")
                output_stream.write(chunk)
        return self.decompressed_written


class BoundedFetcher:
    def __init__(
        self,
        plan: AcquisitionPlan,
        scratch_dir: Path,
        output_dir: Path,
        catalog_source_approved: bool = False,
        *,
        enable_redirect_cache: bool = True,
        enable_connection_pool: bool = True,
    ) -> None:
        validate_plan_authorization(plan, catalog_source_approved)
        for path in (scratch_dir, output_dir):
            ensure_plain_path(path)
        if (
            scratch_dir.resolve() == output_dir.resolve()
            or scratch_dir.resolve().is_relative_to(output_dir.resolve())
            or output_dir.resolve().is_relative_to(scratch_dir.resolve())
        ):
            raise ValueError("scratch and output directories must be disjoint")
        self.plan, self.scratch_dir, self.output_dir = plan, scratch_dir, output_dir
        for name in plan.selected_files:
            validate_host(self._resolve_url(name))
        self.partial_dir = scratch_dir / "partials" / plan.plan_id
        self.journal_path = scratch_dir / "journals" / f"{plan.plan_id}.progress.json"
        self.lock_dir = scratch_dir / "locks"
        self.journal = ProgressJournal(
            self.journal_path, plan.plan_id, plan.compute_behavioral_hash()
        )
        limits = plan.limits
        # The first atomic journal write is scratch usage too. Bind its limits
        # and roots together, before authorization or any other startup write.
        self.journal.bind_roots(
            scratch=scratch_dir,
            output=output_dir,
            resource_limits={
                "transfer": limits.max_transferred_bytes,
                "decompressed": limits.max_decompressed_bytes,
                "temp": limits.max_temp_disk_bytes,
                "output": limits.max_output_disk_bytes,
            },
        )
        with self.journal.transaction() as state:
            if plan.authorization and not state.authorization:
                state.authorization = plan.authorization.model_dump()
        self.capacity_mgr = StorageCapacityManager(
            limits.max_transferred_bytes,
            limits.max_decompressed_bytes,
            limits.max_temp_disk_bytes,
            limits.max_output_disk_bytes,
            journal=self.journal,
        )
        self.budget = TransportBudget(
            max_bytes=limits.max_transferred_bytes,
            max_requests=limits.max_requests,
            deadline_seconds=limits.overall_deadline_seconds,
            per_request_timeout=limits.per_request_timeout_seconds,
            capacity=self.capacity_mgr,
        )
        # Observational only: monotonic timings and counters. Never alters
        # plan hashes, artifact identity, receipts, or selected-record bytes.
        self.perf = PerfTelemetry(process_time=time.process_time)
        self.opener = urllib.request.build_opener(
            SafeRedirectHandler(self.budget, observer=self.perf)
        )
        # Transport optimizations (execution-only toggles, never plan identity):
        # redirect-target reuse skips repeated repository resolutions; the pooled
        # keep-alive client reuses CDN connections for cached-target Range GETs.
        # Both are independently measurable via telemetry and disableable in tests.
        self.enable_redirect_cache = enable_redirect_cache
        self.enable_connection_pool = enable_connection_pool
        self.redirect_cache = RedirectTargetCache(max_entries=256)
        # Source validators are write-once in the journal (a mismatch raises),
        # so an identical binding already made durable by this fetcher is not
        # rewritten on every range request.
        self._bound_sources: dict[str, tuple[str | None, int | None]] = {}
        self._bound_lock = threading.Lock()
        self.pooled: PooledRangeClient | None = (
            PooledRangeClient(observer=self.perf) if enable_connection_pool else None
        )

    def close(self) -> None:
        """Release pooled keep-alive connections; idempotent and exception-safe."""
        pooled, self.pooled = self.pooled, None
        if pooled is not None:
            try:
                pooled.close()
            except Exception:
                pass

    def bind_source(self, rel_path: str, etag: str | None, length: int | None) -> None:
        """Durably bind one source validator; identical re-binds are elided."""
        value = (etag, length)
        with self._bound_lock:
            if self._bound_sources.get(rel_path) == value:
                return
        self.journal.bind_source(rel_path, etag, length)
        with self._bound_lock:
            self._bound_sources[rel_path] = value

    def _resolve_url(self, rel_path: str) -> str:
        path = urllib.parse.quote(rel_path, safe="/")
        if self.plan.provider == "huggingface":
            revision = urllib.parse.quote(self.plan.revision, safe="")
            return (
                f"https://huggingface.co/datasets/{self.plan.repository}/resolve/{revision}/{path}"
            )
        if self.plan.provider == "https":
            return f"{self.plan.repository.rstrip('/')}/{path}"
        raise ValueError(
            "acquisition supports reviewed HTTP(S)/Hub endpoints; use import-local for local data"
        )

    def _check_deadline(self) -> None:
        self.capacity_mgr.check_deadline()

    def _cache_key(self, rel_path: str) -> tuple[str, str, str, str]:
        return (self.plan.provider, self.plan.repository, self.plan.revision, rel_path)

    def _base_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {
            "User-Agent": "xlm-acquisition/2",
            "Accept-Encoding": "identity",
            **headers,
        }

    def _count_error_body(self, error: urllib.error.HTTPError) -> None:
        """Charge one bounded error body without leaking its URL upstream."""
        try:
            with error:
                self.budget.read_body(error, self.plan.limits.max_transferred_bytes, retain=False)
        except BudgetExhaustedError:
            raise
        except Exception:
            pass

    def _open_direct(self, target: str, headers: dict[str, str], timeout: float) -> Any:
        """GET an already-validated cached target; never follows redirects here."""
        pooled = self.pooled
        if pooled is not None:
            try:
                return pooled.request_direct(target, self._base_headers(headers), timeout)
            except _PoolFallbackRequired:
                pass
        request = urllib.request.Request(target, headers=self._base_headers(headers))
        return self.opener.open(request, timeout=timeout)

    def _open(self, rel_path: str, headers: dict[str, str], *, category: str | None = None) -> Any:
        url = self._resolve_url(rel_path)
        validate_host(url)
        self.budget.record_request()
        resolved_category = category or ("range" if "Range" in headers else "open")
        timeout = self.plan.limits.per_request_timeout_seconds
        key = self._cache_key(rel_path)
        target = self.redirect_cache.get(key) if self.enable_redirect_cache else None
        if target is not None:
            # Revalidate with the same trust rules as ordinary redirects.
            validate_host(target)
            host = (urllib.parse.urlparse(target).hostname or "").lower()
            self.perf.record_request(host=host, file=rel_path)
            self.perf.record_redirect_cache_hit()
            with self.perf.timed("open", host=host, file=rel_path, label=resolved_category):
                try:
                    response = self._open_direct(target, headers, timeout)
                except urllib.error.HTTPError as exc:
                    if exc.code in _CACHED_TARGET_EXPIRY_CODES or (
                        exc.code in _CACHED_TARGET_REDIRECT_CODES
                    ):
                        self._count_error_body(exc)
                        if self.redirect_cache.invalidate(key):
                            self.perf.record_redirect_cache_invalidation()
                        raise _CachedTargetExpiredError(
                            f"cached redirect target refused for {rel_path}: HTTP {exc.code}"
                        ) from exc
                    raise
            final = getattr(response, "geturl", lambda: target)()
            if final != target:
                # Provider moved the destination: revalidate, swap, keep going.
                validate_host(final)
                self.redirect_cache.invalidate(key)
                self.redirect_cache.put(key, final)
                self.perf.record_redirect_cache_invalidation()
            return response
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        self.perf.record_request(host=host, file=rel_path)
        if self.enable_redirect_cache:
            self.perf.record_redirect_cache_miss()
        with self.perf.timed("open", host=host, file=rel_path, label=resolved_category):
            request = urllib.request.Request(url, headers=self._base_headers(headers))
            response = self.opener.open(request, timeout=timeout)
        if self.enable_redirect_cache:
            final = getattr(response, "geturl", lambda: url)()
            if final != url:
                validate_host(final)
                self.redirect_cache.put(key, final)
        return response

    def _retry_error(self, error: BaseException, attempt: int) -> None:
        delay = min(0.1 * 2**attempt, 2.0)
        if isinstance(error, urllib.error.HTTPError):
            retry_after = error.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    delay = max(
                        delay,
                        (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds(),
                    )
            with error:
                self.budget.read_body(error, self.plan.limits.max_transferred_bytes, retain=False)
            if error.code in (412, 416):
                raise SourceDriftDetectedError(
                    f"Source drift detected: HTTP {error.code}"
                ) from error
            if error.code != 429 and error.code < 500:
                raise error
        if attempt >= self.plan.limits.max_retries:
            raise error
        self.perf.record_retry(delay)
        self._check_deadline()
        deadline = self.journal.state.accounting.deadline_at
        if (
            not math.isfinite(delay)
            or delay > 60
            or (deadline is not None and time.time() + delay >= deadline)
        ):
            raise TimeoutError("Retry-After exceeds bounded retry/deadline allowance") from error
        time.sleep(delay)
        self._check_deadline()

    def fetch_range(
        self, rel_path: str, start: int, end: int, *, purpose: str | None = None
    ) -> tuple[bytes, int, str | None]:
        """Exact byte ranges only; no ignored-range full-shard fallback."""
        if not 0 <= start <= end or end - start + 1 > self.plan.limits.max_parser_bytes:
            raise ValueError("range exceeds parser bound")
        for attempt in range(self.plan.limits.max_retries + 1):
            try:
                with self._open(
                    rel_path,
                    {"Range": f"bytes={start}-{end}"},
                    category=purpose or "range",
                ) as response:
                    match = CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", ""))
                    if (
                        response.status != 206
                        or not match
                        or (int(match[1]), int(match[2])) != (start, end)
                        or int(match[3]) <= end
                    ):
                        raise ValueError(
                            "inconsistent or ignored Content-Range; whole-shard fallback refused"
                        )
                    with self.perf.timed("body", file=rel_path):
                        body = self.budget.read_body(response, end - start + 1)
                    if len(body) != end - start + 1:
                        raise ValueError("range payload length mismatch")
                    etag = response.headers.get("ETag")
                    if not etag or etag.startswith("W/"):
                        raise ValueError("selected ranges require a stable strong ETag")
                    self.perf.record_file_bytes(rel_path, len(body))
                    with self.perf.timed("accounting"):
                        self.bind_source(rel_path, etag, int(match[3]))
                    return body, int(match[3]), etag
            except (urllib.error.URLError, OSError, http.client.IncompleteRead) as exc:
                self._retry_error(exc, attempt)
        raise RuntimeError("range attempts exhausted")

    def _stream_body(
        self,
        rel_path: str,
        response: Any,
        output: Any,
        digest: Any,
        offset: int,
        remaining: int | None,
        etag: str | None,
    ) -> int:
        """Stream one original body with window-granular durable accounting.

        Reads keep the historical 64 KiB size, so bytes, digest and every
        consumed total are identical to per-chunk accounting. Each window
        reserves transfer and scratch allowance in one durable exchange that
        also settles the previous window and checkpoints its fsynced verified
        prefix (one data fsync plus one journal write per window instead of
        several per chunk). A failure settles known usage, keeps the in-flight
        amount reserved and checkpoints what was durably written; a crash
        leaves at most one window per stream charged and unverified bytes are
        truncated to the checkpoint on resume. Returns the final offset.
        """
        capacity = self.capacity_mgr
        transfer = self.budget.transfer_lease(remaining)
        temp = CapacityLease(capacity, "temp")
        written = 0  # appended since the last durable checkpoint
        streamed = 0  # read from this response so far (drives window growth)
        in_flight = 0  # scratch amount of a read whose outcome is unresolved

        def checkpoint(*, reserve_next: bool, hold_temp: int = 0, failing: bool = False) -> None:
            nonlocal written
            durable = True
            if written:
                try:
                    output.flush()
                    os.fsync(output.fileno())
                except OSError:
                    if not failing:
                        raise
                    durable = False
            progress = None
            if written and durable:
                added, prefix = written, digest.hexdigest()

                def progress(state: AcquisitionState) -> None:
                    apply_file_progress(state, rel_path, added, etag, prefix_sha256=prefix)

            settle = tuple(
                entry
                for entry in (transfer.settlement(), temp.settlement(hold_temp))
                if entry is not None
            )
            refusal: BaseException | None = None
            reserve: tuple[tuple[str, int, int], ...] = ()
            if reserve_next:
                want = grown_window(streamed, WHOLE_FILE_WINDOW_BYTES)
                if remaining is not None:
                    want = min(want, remaining)
                room = min(want, capacity.remaining("temp") + temp.left)
                if room <= 0:
                    refusal = BudgetExhaustedError("scratch allowance exhausted")
                else:
                    existing = self.scratch_dir
                    while not existing.exists():
                        existing = existing.parent
                    if shutil.disk_usage(existing).free - room < capacity.min_free_headroom_bytes:
                        refusal = DiskCeilingExceededError("physical disk headroom guard reached")
                    else:
                        reserve = (("transfer", want, 1), ("temp", room, 1))
            with self.perf.timed("accounting"):
                grants, consumed = capacity.exchange(
                    settle=settle, reserve=reserve, progress=progress
                )
            transfer.retire(consumed)
            temp.retire(consumed)
            if progress is not None:
                written = 0
            if refusal is not None:
                raise refusal
            if reserve:
                (transfer_grant, temp_grant) = grants
                if not transfer_grant[0] or not temp_grant[0]:
                    release = tuple(
                        (resource, token, 0, 0)
                        for resource, (token, _) in zip(("transfer", "temp"), grants, strict=True)
                        if token
                    )
                    if release:
                        capacity.exchange(settle=release)
                    if not transfer_grant[0]:
                        raise BudgetExhaustedError("response-body byte allowance exhausted")
                    raise DiskCeilingExceededError(
                        "temp limit reached including outstanding reservations"
                    )
                transfer.adopt(transfer_grant)
                temp.adopt(temp_grant)

        try:
            while remaining is None or remaining > 0:
                self._check_deadline()
                if not transfer.left or not temp.left:
                    checkpoint(reserve_next=True)
                amount = min(WHOLE_FILE_READ_BYTES, transfer.left, temp.left)
                in_flight = amount
                with self.perf.timed("body", file=rel_path):
                    chunk = self.budget.read_leased(response, transfer, amount)
                if not chunk:
                    in_flight = 0
                    if remaining:
                        raise http.client.IncompleteRead(b"", remaining)
                    break
                output.write(chunk)
                temp.commit(len(chunk))
                in_flight = 0
                self.perf.record_file_bytes(rel_path, len(chunk))
                digest.update(chunk)
                written += len(chunk)
                streamed += len(chunk)
                offset += len(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
            checkpoint(reserve_next=False)
        except BaseException:
            try:
                checkpoint(reserve_next=False, hold_temp=in_flight, failing=True)
            except BaseException:
                pass  # never mask the original failure; resume truncates to the checkpoint
            raise
        return offset

    def _fetch_file(self, rel_path: str) -> Path:
        final_path, partial = self.output_dir / rel_path, self.partial_dir / f"{rel_path}.part"
        ensure_plain_path(final_path)
        ensure_plain_path(partial)
        with self.perf.timed("accounting"):
            self.journal.save()
        fp = self.journal.state.file_progress.get(rel_path)
        if final_path.exists():
            if not fp or fp.status != "completed" or not fp.content_sha256:
                raise ProgressCorruptionError(
                    "incomplete/untracked original exists; refusing overwrite"
                )
            actual = compute_file_sha256(
                final_path, max_bytes=self.plan.limits.max_output_disk_bytes
            )
            expected = self.plan.expected_file_digests.get(rel_path, fp.content_sha256)
            if actual != expected or actual != fp.content_sha256:
                raise ProgressCorruptionError("completed original integrity checksum mismatch")
            self.capacity_mgr.record_cache_hit()
            self.perf.record_cache_hit()
            return final_path
        if fp and fp.status == "completed":
            raise ProgressCorruptionError("completed original is missing; refusing repair")
        if partial.exists() and not fp:
            raise ProgressCorruptionError(
                "unowned partial file; cannot infer historical consumption"
            )
        partial.parent.mkdir(parents=True, exist_ok=True)
        with self.perf.file_worker(rel_path):
            for attempt in range(self.plan.limits.max_retries + 1):
                self._check_deadline()
                fp = self.journal.state.file_progress.get(rel_path)
                verified = fp.verified_prefix_bytes if fp else 0
                if partial.exists() and partial.stat().st_size > verified:
                    # Bytes past the durable checkpoint (a failed window whose
                    # checkpoint could not be made durable) are never trusted.
                    AtomicFileWriter.truncate_to_length(partial, verified)
                offset = partial.stat().st_size if partial.exists() else 0
                etag = fp.etag if fp else None
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                if offset and etag and not etag.startswith("W/"):
                    headers["If-Range"] = etag
                try:
                    with self._open(rel_path, headers) as response:
                        new_etag = response.headers.get("ETag")
                        if offset and etag and new_etag and etag != new_etag:
                            raise SourceDriftDetectedError(
                                "source ETag changed during continuation"
                            )
                        length = response.headers.get("Content-Length")
                        remaining = int(length) if length is not None else None
                        if remaining is not None and remaining < 0:
                            raise ValueError("negative response length")
                        if response.status == 206:
                            match = CONTENT_RANGE_RE.fullmatch(
                                response.headers.get("Content-Range", "")
                            )
                            if (
                                not offset
                                or not match
                                or int(match[1]) != offset
                                or int(match[2]) != int(match[3]) - 1
                            ):
                                raise RuntimeError(
                                    "Inconsistent Content-Range start/end for continuation"
                                )
                            if remaining is not None and remaining != int(match[2]) - offset + 1:
                                raise ValueError("Content-Range length mismatch")
                            remaining = int(match[3]) - offset
                        elif response.status == 200:
                            if offset:
                                # Only this plan's verified private prefix may be retired.
                                AtomicFileWriter.truncate_to_length(partial, 0)
                                with self.perf.timed("accounting"):
                                    with self.journal.transaction() as state:
                                        state.file_progress[rel_path].bytes_downloaded = 0
                                        state.file_progress[rel_path].verified_prefix_bytes = 0
                                offset = 0
                        else:
                            raise ValueError(f"unexpected response status {response.status}")
                        if remaining is not None and remaining > self.capacity_mgr.remaining(
                            "transfer"
                        ):
                            raise BudgetExhaustedError(
                                "Transferred bytes limit: declared response "
                                "exceeds remaining allowance"
                            )
                        digest = hashlib.sha256()
                        if offset:
                            with partial.open("rb") as prior:
                                while chunk := prior.read(65536):
                                    digest.update(chunk)
                        if fp is None:
                            # Establish ownership before creating the partial. A
                            # process death before the first window checkpoint
                            # must resume from a verified empty prefix, while
                            # pre-existing unowned partials still fail above.
                            with self.journal.transaction() as state:
                                apply_file_progress(
                                    state,
                                    rel_path,
                                    0,
                                    new_etag,
                                    prefix_sha256=digest.hexdigest(),
                                )
                        with partial.open("ab") as output:
                            offset = self._stream_body(
                                rel_path, response, output, digest, offset, remaining, new_etag
                            )
                        if offset == 0:
                            raise ValueError("empty original cannot establish a corpus acquisition")
                        expected_digest = self.plan.expected_file_digests.get(rel_path)
                        if expected_digest and digest.hexdigest() != expected_digest.lower():
                            raise ProgressCorruptionError(
                                "download checksum differs from independent expected digest"
                            )
                        try:
                            decompressed_before = self.capacity_mgr.snapshot()["decompressed_bytes"]
                        except Exception:
                            decompressed_before = 0
                        if rel_path.endswith(".parquet"):
                            records = inspect_records(
                                partial,
                                rel_path,
                                self.plan.limits,
                                self.capacity_mgr,
                                perf=self.perf,
                            )
                        else:
                            with self.perf.timed("decode", file=rel_path):
                                records = inspect_records(
                                    partial,
                                    rel_path,
                                    self.plan.limits,
                                    self.capacity_mgr,
                                    perf=self.perf,
                                )
                        try:
                            decompressed_after = self.capacity_mgr.snapshot()["decompressed_bytes"]
                        except Exception:
                            decompressed_after = decompressed_before
                        self.perf.record_decompressed(
                            max(0, decompressed_after - decompressed_before)
                        )
                        with self.perf.timed("accounting"):
                            with self.journal.transaction() as state:
                                count = sum(
                                    item.record_count or 0
                                    for name, item in state.file_progress.items()
                                    if name != rel_path
                                )
                                if count + (records or 0) > self.plan.limits.max_records:
                                    raise RecordLimitError("aggregate record limit exceeded")
                                state.file_progress[rel_path].record_count = records
                        self.perf.record_scanned(records or 0, file=rel_path)
                        self.perf.record_retained(records or 0, file=rel_path)
                        with self.perf.timed("serialize", file=rel_path):
                            publish_output(
                                self,
                                rel_path,
                                partial,
                                offset,
                                digest.hexdigest(),
                                records,
                                new_etag,
                            )
                        return final_path
                except (urllib.error.URLError, OSError, http.client.IncompleteRead) as exc:
                    self._retry_error(exc, attempt)
            raise RuntimeError("file attempts exhausted")

    def run(self) -> AcquisitionState:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        wall_start = time.monotonic()
        cpu_start: float | None = None
        try:
            cpu_start = time.process_time()
        except Exception:
            cpu_start = None
        final_status = "FAILED"
        try:
            with FileLock(str(self.lock_dir / f"acq_{self.plan.plan_id}.lock"), timeout=1):
                self.capacity_mgr.bind_deadline(self.plan.limits.overall_deadline_seconds)
                with self.perf.timed("accounting"):
                    self.journal.set_status("IN_PROGRESS")
                try:
                    reconcile_publications(self)
                    if self.plan.mode == AcquisitionMode.SELECTED_RECORDS:
                        from xlm.data.acquisition.selection import acquire_selection

                        acquire_selection(self)
                    else:
                        self.journal.reconcile_with_disk(self.output_dir, self.partial_dir)
                        self.capacity_mgr.reconcile_disk("temp", self.partial_dir)
                        self.capacity_mgr.reconcile_disk("output", self.output_dir)
                        with ThreadPoolExecutor(
                            max_workers=min(
                                self.plan.limits.max_workers, len(self.plan.selected_files)
                            )
                        ) as pool:
                            list(pool.map(self._fetch_file, self.plan.selected_files))
                    self.capacity_mgr.reconcile_disk("temp", self.partial_dir)
                    self.capacity_mgr.reconcile_disk("output", self.output_dir)
                    with self.perf.timed("accounting"):
                        self.journal.set_status("COMPLETED")
                    final_status = "COMPLETED"
                except BaseException as exc:
                    final_status = (
                        "INTERRUPTED"
                        if isinstance(exc, (KeyboardInterrupt, BudgetExhaustedError, TimeoutError))
                        else "FAILED"
                    )
                    with self.perf.timed("accounting"):
                        self.journal.set_status(
                            final_status,
                            str(exc)[:500],
                        )
                    raise
                return self.journal.state
        except Timeout as exc:
            raise AcquisitionLockHeldError(
                "another process holds this plan's acquisition lock"
            ) from exc
        finally:
            # Observational sidecar only. Journal, plan, receipt, and all
            # selected-record bytes are written (or not) exactly as before.
            try:
                state = self.journal.state
                doc = self.perf.snapshot(
                    plan=self.plan,
                    status=final_status,
                    wall_seconds=max(0.0, time.monotonic() - wall_start),
                    transferred_bytes=state.transferred_bytes,
                    journal_decompressed_bytes=state.decompressed_bytes,
                    journal_requests=state.requests_made,
                    journal_cache_hits=state.cache_hits,
                    journal_records=state.records_acquired,
                    journal_stats=self.journal.io_stats(),
                )
                if cpu_start is not None:
                    try:
                        doc["cpu_process_seconds"] = max(0.0, time.process_time() - cpu_start)
                    except Exception:
                        pass
                self.perf.write_sidecar(
                    self.scratch_dir, self.plan.plan_id, doc, journal=self.journal
                )
            except Exception:
                pass
            try:
                self.close()
            except Exception:
                pass
