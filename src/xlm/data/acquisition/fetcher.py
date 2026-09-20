"""Existing bounded fetcher: durable budgets, verified originals and explicit selections."""
from __future__ import annotations

import gzip
import hashlib
import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import compute_file_sha256
from xlm.data.acquisition.disk import AtomicFileWriter, StorageCapacityManager
from xlm.data.acquisition.plan import AcquisitionMode, AcquisitionPlan, SourceDriftDetectedError, validate_plan_authorization
from xlm.data.acquisition.progress import AcquisitionState, ProgressCorruptionError, ProgressJournal
from xlm.data.acquisition.records import RecordLimitError, inspect_records
from xlm.data.sources.transport import BudgetExhaustedError, SafeRedirectHandler, TransportBudget, validate_host

CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class AcquisitionLockHeldError(RuntimeError):
    """Another process owns execution of this plan."""


class DecompressionBombError(RuntimeError):
    """Decompression ceiling or expansion ratio exceeded."""


class _CountingReader:
    def __init__(self, raw: Any) -> None:
        self.raw, self.bytes_read = raw, 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            raise ValueError("unbounded compressed read refused")
        value = bytes(self.raw.read(n))
        self.bytes_read += len(value)
        return value


class BoundedDecompressor:
    def __init__(self, capacity_mgr: StorageCapacityManager, max_ratio: float = 15.0,
                 max_decompressed_bytes: int = 512 * 1024 * 1024) -> None:
        self.capacity_mgr, self.max_ratio = capacity_mgr, max_ratio
        self.max_decompressed_bytes = max_decompressed_bytes
        self.compressed_read = self.decompressed_written = 0

    def decompress_stream(self, input_stream: Any, output_stream: Any) -> int:
        counting = _CountingReader(input_stream)
        with gzip.GzipFile(fileobj=counting, mode="rb") as stream:
            while True:
                chunk = stream.read(min(65536, self.max_decompressed_bytes - self.decompressed_written + 1))
                if not chunk:
                    break
                self.compressed_read = counting.bytes_read
                self.decompressed_written += len(chunk)
                try:
                    self.capacity_mgr.record_decompressed(len(chunk))
                except BudgetExhaustedError as exc:
                    raise DecompressionBombError(str(exc)) from exc
                if self.decompressed_written > self.max_decompressed_bytes or self.decompressed_written > max(1, self.compressed_read) * self.max_ratio:
                    raise DecompressionBombError("decompression byte/ratio limit exceeded")
                output_stream.write(chunk)
        return self.decompressed_written


class BoundedFetcher:
    def __init__(self, plan: AcquisitionPlan, scratch_dir: Path, output_dir: Path,
                 catalog_source_approved: bool = False) -> None:
        validate_plan_authorization(plan, catalog_source_approved)
        for path in (scratch_dir, output_dir):
            ensure_plain_path(path)
        if scratch_dir.resolve() == output_dir.resolve() or scratch_dir.resolve().is_relative_to(output_dir.resolve()) or output_dir.resolve().is_relative_to(scratch_dir.resolve()):
            raise ValueError("scratch and output directories must be disjoint")
        self.plan, self.scratch_dir, self.output_dir = plan, scratch_dir, output_dir
        self.partial_dir = scratch_dir / "partials" / plan.plan_id
        self.journal_path = scratch_dir / "journals" / f"{plan.plan_id}.progress.json"
        self.lock_dir = scratch_dir / "locks"
        self.journal = ProgressJournal(self.journal_path, plan.plan_id, plan.compute_behavioral_hash())
        self.journal.bind_roots(scratch=scratch_dir, output=output_dir)
        limits = plan.limits
        self.capacity_mgr = StorageCapacityManager(limits.max_transferred_bytes,
            limits.max_decompressed_bytes, limits.max_temp_disk_bytes,
            limits.max_output_disk_bytes, journal=self.journal)
        self.budget = TransportBudget(max_bytes=limits.max_transferred_bytes,
            max_requests=limits.max_requests, deadline_seconds=limits.overall_deadline_seconds,
            per_request_timeout=limits.per_request_timeout_seconds, capacity=self.capacity_mgr)
        self.opener = urllib.request.build_opener(SafeRedirectHandler(self.budget))

    def _resolve_url(self, rel_path: str) -> str:
        path = urllib.parse.quote(rel_path, safe="/")
        if self.plan.provider == "huggingface":
            revision = urllib.parse.quote(self.plan.revision, safe="")
            return f"https://huggingface.co/datasets/{self.plan.repository}/resolve/{revision}/{path}"
        if self.plan.provider == "https":
            return f"{self.plan.repository.rstrip('/')}/{path}"
        raise ValueError("acquisition supports reviewed HTTP(S)/Hub endpoints; use import-local for local data")

    def _check_deadline(self) -> None:
        self.capacity_mgr.check_deadline()

    def _open(self, rel_path: str, headers: dict[str, str]) -> Any:
        url = self._resolve_url(rel_path)
        validate_host(url)
        self.budget.record_request()
        request = urllib.request.Request(url, headers={"User-Agent": "xlm-acquisition/2", "Accept-Encoding": "identity", **headers})
        return self.opener.open(request, timeout=self.plan.limits.per_request_timeout_seconds)

    def _retry_error(self, error: BaseException, attempt: int) -> None:
        if isinstance(error, urllib.error.HTTPError):
            with error:
                self.budget.read_body(error, self.plan.limits.max_transferred_bytes, retain=False)
            if error.code in (412, 416):
                raise SourceDriftDetectedError(f"source/range changed: HTTP {error.code}") from error
            if error.code != 429 and error.code < 500:
                raise error
        if attempt >= self.plan.limits.max_retries:
            raise error
        self._check_deadline()
        time.sleep(min(0.1 * 2**attempt, 2.0))
        self._check_deadline()

    def fetch_range(self, rel_path: str, start: int, end: int) -> tuple[bytes, int, str | None]:
        """Exact byte ranges only; no ignored-range full-shard fallback."""
        if not 0 <= start <= end or end - start + 1 > self.plan.limits.max_parser_bytes:
            raise ValueError("range exceeds parser bound")
        for attempt in range(self.plan.limits.max_retries + 1):
            try:
                with self._open(rel_path, {"Range": f"bytes={start}-{end}"}) as response:
                    match = CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", ""))
                    if response.status != 206 or not match or (int(match[1]), int(match[2])) != (start, end) or int(match[3]) <= end:
                        raise ValueError("inconsistent or ignored Content-Range; whole-shard fallback refused")
                    body = self.budget.read_body(response, end - start + 1)
                    if len(body) != end - start + 1:
                        raise ValueError("range payload length mismatch")
                    return body, int(match[3]), response.headers.get("ETag")
            except (urllib.error.URLError, OSError, http.client.IncompleteRead) as exc:
                self._retry_error(exc, attempt)
        raise RuntimeError("range attempts exhausted")

    def _fetch_file(self, rel_path: str) -> Path:
        final_path, partial = self.output_dir / rel_path, self.partial_dir / f"{rel_path}.part"
        ensure_plain_path(final_path)
        ensure_plain_path(partial)
        self.journal.save()
        fp = self.journal.state.file_progress.get(rel_path)
        if final_path.exists():
            if not fp or fp.status != "completed" or not fp.content_sha256:
                raise ProgressCorruptionError("incomplete/untracked original exists; refusing overwrite")
            actual = compute_file_sha256(final_path, max_bytes=self.plan.limits.max_output_disk_bytes)
            expected = self.plan.expected_file_digests.get(rel_path, fp.content_sha256)
            if actual != expected or actual != fp.content_sha256:
                raise ProgressCorruptionError("completed original integrity checksum mismatch")
            self.capacity_mgr.record_cache_hit()
            return final_path
        if fp and fp.status == "completed":
            raise ProgressCorruptionError("completed original is missing; refusing repair")
        if partial.exists() and not fp:
            raise ProgressCorruptionError("unowned partial file; cannot infer historical consumption")
        partial.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.plan.limits.max_retries + 1):
            self._check_deadline()
            offset = partial.stat().st_size if partial.exists() else 0
            fp = self.journal.state.file_progress.get(rel_path)
            etag = fp.etag if fp else None
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            if offset and etag and not etag.startswith("W/"):
                headers["If-Range"] = etag
            try:
                with self._open(rel_path, headers) as response:
                    new_etag = response.headers.get("ETag")
                    if offset and etag and new_etag and etag != new_etag:
                        raise SourceDriftDetectedError("source ETag changed during continuation")
                    length = response.headers.get("Content-Length")
                    remaining = int(length) if length is not None else None
                    if remaining is not None and remaining < 0:
                        raise ValueError("negative response length")
                    if response.status == 206:
                        match = CONTENT_RANGE_RE.fullmatch(response.headers.get("Content-Range", ""))
                        if not offset or not match or int(match[1]) != offset or int(match[2]) != int(match[3]) - 1:
                            raise ValueError("inconsistent Content-Range for continuation")
                        if remaining is not None and remaining != int(match[2]) - offset + 1:
                            raise ValueError("Content-Range length mismatch")
                        remaining = int(match[3]) - offset
                    elif response.status == 200:
                        if offset:
                            # Only this plan's verified private prefix may be retired.
                            AtomicFileWriter.truncate_to_length(partial, 0)
                            with self.journal.transaction() as state:
                                state.file_progress[rel_path].bytes_downloaded = 0
                                state.file_progress[rel_path].verified_prefix_bytes = 0
                            offset = 0
                    else:
                        raise ValueError(f"unexpected response status {response.status}")
                    if remaining is not None and remaining > self.capacity_mgr.remaining("transfer"):
                        raise BudgetExhaustedError("declared response exceeds remaining transfer allowance")
                    digest = hashlib.sha256()
                    if offset:
                        with partial.open("rb") as prior:
                            while chunk := prior.read(65536):
                                digest.update(chunk)
                    with partial.open("ab") as output:
                        while remaining is None or remaining > 0:
                            self._check_deadline()
                            amount = min(65536, remaining if remaining is not None else 65536)
                            amount = min(amount, self.capacity_mgr.remaining("temp"))
                            if amount <= 0:
                                raise BudgetExhaustedError("scratch allowance exhausted")
                            disk_token = self.capacity_mgr.reserve_disk_space(self.scratch_dir, amount)
                            chunk = self.budget.read_chunk(response, amount)
                            if not chunk:
                                self.capacity_mgr.settle("temp", disk_token, 0)
                                if remaining:
                                    raise http.client.IncompleteRead(b"", remaining)
                                break
                            output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                            self.capacity_mgr.settle("temp", disk_token, len(chunk))
                            digest.update(chunk)
                            offset += len(chunk)
                            if remaining is not None:
                                remaining -= len(chunk)
                            self.journal.update_file_progress(rel_path, len(chunk), new_etag,
                                prefix_sha256=digest.hexdigest())
                    expected = self.plan.expected_file_digests.get(rel_path)
                    if expected and digest.hexdigest() != expected.lower():
                        raise ProgressCorruptionError("download checksum differs from independent expected digest")
                    records = inspect_records(partial, rel_path, self.plan.limits, self.capacity_mgr)
                    with self.journal.transaction() as state:
                        count = sum(item.record_count or 0 for name, item in state.file_progress.items() if name != rel_path)
                        if count + (records or 0) > self.plan.limits.max_records:
                            raise RecordLimitError("aggregate record limit exceeded")
                        state.file_progress[rel_path].record_count = records
                    token = self.capacity_mgr.reserve_disk_space(self.output_dir, offset, is_temp=False)
                    AtomicFileWriter.atomic_complete(partial, final_path)
                    self.capacity_mgr.settle("output", token, offset)
                    self.journal.mark_file_completed(rel_path, offset, new_etag, digest=digest.hexdigest(), records=records)
                    return final_path
            except (urllib.error.URLError, OSError, http.client.IncompleteRead) as exc:
                self._retry_error(exc, attempt)
        raise RuntimeError("file attempts exhausted")

    def run(self) -> AcquisitionState:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(self.lock_dir / f"acq_{self.plan.plan_id}.lock"), timeout=1):
                self.capacity_mgr.bind_deadline(self.plan.limits.overall_deadline_seconds)
                self.journal.set_status("IN_PROGRESS")
                try:
                    if self.plan.mode == AcquisitionMode.SELECTED_RECORDS:
                        from xlm.data.acquisition.selection import acquire_selection
                        acquire_selection(self)
                    else:
                        self.journal.reconcile_with_disk(self.output_dir, self.partial_dir)
                        self.capacity_mgr.reconcile_disk("temp", self.partial_dir)
                        self.capacity_mgr.reconcile_disk("output", self.output_dir)
                        with ThreadPoolExecutor(max_workers=min(self.plan.limits.max_workers, len(self.plan.selected_files))) as pool:
                            list(pool.map(self._fetch_file, self.plan.selected_files))
                    self.capacity_mgr.reconcile_disk("temp", self.partial_dir)
                    self.capacity_mgr.reconcile_disk("output", self.output_dir)
                    self.journal.set_status("COMPLETED")
                except BaseException as exc:
                    self.journal.set_status("INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, BudgetExhaustedError, TimeoutError)) else "FAILED", str(exc)[:500])
                    raise
                return self.journal.state
        except Timeout as exc:
            raise AcquisitionLockHeldError("another process holds this plan's acquisition lock") from exc
