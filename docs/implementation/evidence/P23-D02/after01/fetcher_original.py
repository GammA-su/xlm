"""Bounded, resumable HTTP/HF fetcher engine with shared reservations complying with C04."""

from __future__ import annotations

import gzip
import http.client
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from xlm.data.acquisition.disk import AtomicFileWriter, StorageCapacityManager
from xlm.data.acquisition.plan import (
    AcquisitionMode,
    AcquisitionPlan,
    SourceDriftDetectedError,
    validate_plan_authorization,
)
from xlm.data.acquisition.progress import AcquisitionState, ProgressJournal
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    SafeRedirectHandler,
    validate_host,
)

CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)


class AcquisitionLockHeldError(RuntimeError):
    """Raised when another acquisition process holds the plan-bound acquisition lock."""


class DecompressionBombError(RuntimeError):
    """Raised when decompression expansion ratio or decompressed byte ceiling is breached."""


class _CountingReader:
    """Wrapper around binary stream that counts total raw compressed bytes read."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.bytes_read = 0

    def read(self, n: int = -1) -> bytes:
        chunk: bytes = bytes(self.raw.read(n))
        self.bytes_read += len(chunk)
        return chunk

    def readinto(self, b: Any) -> int:
        if hasattr(self.raw, "readinto"):
            n: int = int(self.raw.readinto(b))
            self.bytes_read += n
            return n
        chunk: bytes = bytes(self.raw.read(len(b)))
        n_len = len(chunk)
        b[:n_len] = chunk
        self.bytes_read += n_len
        return n_len

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)


class BoundedDecompressor:
    """Safely stream-decompresses gzip data guarding against zip-bombs."""

    def __init__(
        self,
        capacity_mgr: StorageCapacityManager,
        max_ratio: float = 15.0,
        max_decompressed_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.capacity_mgr = capacity_mgr
        self.max_ratio = max_ratio
        self.max_decompressed_bytes = max_decompressed_bytes
        self.compressed_read = 0
        self.decompressed_written = 0

    def decompress_stream(self, input_stream: Any, output_stream: Any) -> int:
        """Decompress gzip input to output stream in bounded chunks, checking ratio."""
        counting_in = _CountingReader(input_stream)
        decompressor = gzip.GzipFile(fileobj=counting_in, mode="rb")
        while True:
            chunk = decompressor.read(65536)
            if not chunk:
                break
            self.decompressed_written += len(chunk)
            self.compressed_read = counting_in.bytes_read

            try:
                self.capacity_mgr.record_decompressed(len(chunk))
            except BudgetExhaustedError as e:
                raise DecompressionBombError(
                    f"Decompressed output exceeded capacity budget: {e}"
                ) from e

            if self.decompressed_written > self.max_decompressed_bytes:
                raise DecompressionBombError(
                    f"Decompressed output exceeded ceiling of "
                    f"{self.max_decompressed_bytes:,} bytes."
                )

            # Check ratio once initial bytes have been decompressed
            if self.decompressed_written > 8192:
                ratio = self.decompressed_written / max(1, self.compressed_read)
                if ratio > self.max_ratio:
                    raise DecompressionBombError(
                        f"Decompression ratio ({ratio:.1f}x) exceeded safety "
                        f"limit of {self.max_ratio}x."
                    )
            output_stream.write(chunk)
        return self.decompressed_written


class BoundedFetcher:
    """Executes data acquisition from an approved AcquisitionPlan with bounded budgets."""

    def __init__(
        self,
        plan: AcquisitionPlan,
        scratch_dir: Path,
        output_dir: Path,
        catalog_source_approved: bool = False,
    ) -> None:
        self.plan = plan
        self.scratch_dir = scratch_dir
        self.output_dir = output_dir
        self.catalog_source_approved = catalog_source_approved
        if plan.mode == AcquisitionMode.SELECTED_RECORDS or plan.row_ranges:
            raise ValueError(
                "selected-record acquisition is not implemented; refusing a whole-file substitute"
            )

        self.partial_dir = self.scratch_dir / "partials" / self.plan.plan_id
        self.journal_path = self.scratch_dir / "journals" / f"{self.plan.plan_id}.progress.json"
        self.lock_dir = self.scratch_dir / "locks"

        # Validate authorization and direct denial before doing any work
        validate_plan_authorization(self.plan, catalog_source_approved=self.catalog_source_approved)

        self.capacity_mgr = StorageCapacityManager(
            max_transferred_bytes=self.plan.limits.max_transferred_bytes,
            max_decompressed_bytes=self.plan.limits.max_decompressed_bytes,
            max_temp_disk_bytes=self.plan.limits.max_temp_disk_bytes,
            max_output_disk_bytes=self.plan.limits.max_output_disk_bytes,
        )

        self.journal = ProgressJournal(
            self.journal_path, self.plan.plan_id, self.plan.compute_behavioral_hash()
        )
        self.opener = urllib.request.build_opener(SafeRedirectHandler())
        self.start_time = time.monotonic()

    def _resolve_url(self, rel_path: str) -> str:
        """Resolve full download URL from provider, repository, and revision."""
        if self.plan.provider == "huggingface":
            # Direct raw file URL on Hugging Face Hub pinned to immutable revision
            encoded_repo = self.plan.repository
            encoded_rev = urllib.parse.quote(self.plan.revision, safe="")
            encoded_path = urllib.parse.quote(rel_path, safe="/")
            return f"https://huggingface.co/datasets/{encoded_repo}/resolve/{encoded_rev}/{encoded_path}"
        elif self.plan.provider == "https":
            base = self.plan.repository.rstrip("/")
            encoded_path = urllib.parse.quote(rel_path.lstrip("/"), safe="/")
            return f"{base}/{encoded_path}"
        elif self.plan.provider == "local":
            # Local file URI or path
            return f"file:///{Path(self.plan.repository).resolve() / rel_path}"
        else:
            raise ValueError(f"Unsupported acquisition provider: '{self.plan.provider}'")

    def _check_deadline(self) -> None:
        """Check whether overall execution deadline has expired."""
        elapsed = time.monotonic() - self.start_time
        if elapsed > self.plan.limits.overall_deadline_seconds:
            raise TimeoutError(
                f"Acquisition execution deadline ({self.plan.limits.overall_deadline_seconds}s) "
                f"exceeded (elapsed {elapsed:.1f}s)."
            )

    def _fetch_file(self, rel_path: str) -> Path:
        """Acquire a single file, supporting Range resumption and server revalidation."""
        final_path = self.output_dir / rel_path
        partial_path = self.partial_dir / f"{rel_path}.part"

        # Check if final file already exists and is complete
        fp = self.journal.state.file_progress.get(rel_path)
        if fp and fp.status == "completed" and final_path.exists():
            # Check size matches
            if final_path.stat().st_size == fp.bytes_downloaded:
                self.capacity_mgr.record_cache_hit()
                return final_path

        partial_path.parent.mkdir(parents=True, exist_ok=True)
        url = self._resolve_url(rel_path)
        validate_host(url)

        attempt = 0
        while attempt <= self.plan.limits.max_retries:
            self._check_deadline()
            attempt += 1

            # Determine existing durable offset on disk
            existing_offset = 0
            existing_etag: str | None = None
            if partial_path.exists():
                existing_offset = partial_path.stat().st_size
                if fp:
                    existing_etag = fp.etag

            headers: dict[str, str] = {
                "User-Agent": "xlm-data-acquisition/1.0",
                "Accept": "*/*",
            }

            # Send Range if resuming and we have durable bytes
            is_resuming = existing_offset > 0
            if is_resuming:
                headers["Range"] = f"bytes={existing_offset}-"
                # Do NOT use weak ETags (W/"...") as If-Range validators
                if existing_etag and not existing_etag.startswith("W/"):
                    headers["If-Range"] = existing_etag

            req = urllib.request.Request(url, headers=headers, method="GET")
            self.capacity_mgr.reserve_transfer(1024)  # Reserve minimum request envelope

            try:
                self.journal.record_request(self.plan.limits.max_requests)
                with self.opener.open(
                    req, timeout=self.plan.limits.per_request_timeout_seconds
                ) as resp:
                    status = resp.status if hasattr(resp, "status") else resp.code
                    resp_headers = resp.headers
                    etag = resp_headers.get("ETag")
                    content_length_str = resp_headers.get("Content-Length")
                    total_expected: int | None = None

                    if is_resuming and status == 206:
                        # 206 Partial Content: Validate Content-Range header
                        cr = resp_headers.get("Content-Range")
                        if not cr:
                            raise RuntimeError(
                                f"Server returned 206 without Content-Range for {url}"
                            )
                        match = CONTENT_RANGE_RE.match(cr.strip())
                        if not match:
                            raise RuntimeError(f"Malformed Content-Range header '{cr}' from {url}")

                        start_byte = int(match.group(1))
                        total_str = match.group(3)
                        total_expected = int(total_str) if total_str != "*" else None

                        if start_byte != existing_offset:
                            raise RuntimeError(
                                f"Inconsistent Content-Range start: served {start_byte} "
                                f"!= requested {existing_offset}."
                            )

                        # Check if ETag changed when If-Range was used
                        if existing_etag and etag and etag != existing_etag:
                            raise SourceDriftDetectedError(
                                f"Source ETag changed during resume of '{rel_path}': "
                                f"prior '{existing_etag}' != current '{etag}'."
                            )

                        mode = "a+b"
                    elif status == 200:
                        # 200 OK: Full content sent (new download or server ignored Range header)
                        if is_resuming:
                            # Server ignored Range header! Truncate partial file and start from 0
                            existing_offset = 0
                            AtomicFileWriter.truncate_to_length(partial_path, 0)

                        if content_length_str:
                            total_expected = int(content_length_str)
                        mode = "w+b"
                    else:
                        raise RuntimeError(f"Unexpected HTTP status {status} from {url}")

                    # Stream payload in chunks
                    with partial_path.open(mode) as out_f:
                        chunk_size = 65536
                        while True:
                            self._check_deadline()
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            self.capacity_mgr.record_transfer(len(chunk))
                            self.capacity_mgr.reserve_disk_space(
                                self.scratch_dir, len(chunk), is_temp=True
                            )
                            out_f.write(chunk)
                            out_f.flush()
                            existing_offset += len(chunk)

                            # Periodically journal progress
                            self.journal.update_file_progress(
                                rel_path=rel_path,
                                bytes_added=len(chunk),
                                etag=etag,
                                total_expected=total_expected,
                            )

                    # Check if connection was closed early without delivering full payload
                    if total_expected is not None and existing_offset < total_expected:
                        raise http.client.IncompleteRead(
                            partial=b"", expected=total_expected - existing_offset
                        )

                    # Successful stream completion: fsync and atomic rename
                    final_size = partial_path.stat().st_size
                    self.capacity_mgr.reserve_disk_space(self.output_dir, final_size, is_temp=False)
                    AtomicFileWriter.atomic_complete(partial_path, final_path)
                    self.capacity_mgr.release_temp_disk(final_size)
                    self.journal.mark_file_completed(rel_path, final_size, etag=etag)
                    self.journal.save()
                    return final_path

            except urllib.error.HTTPError as e:
                self.capacity_mgr.record_transfer(1024)
                if e.code == 412:
                    # Precondition Failed: ETag changed upstream
                    raise SourceDriftDetectedError(
                        f"Source drift detected (HTTP 412): remote entity for '{rel_path}' changed."
                    ) from e
                elif e.code == 416:
                    # Range Not Satisfiable: offset past file length
                    raise RuntimeError(
                        f"Range not satisfiable for '{rel_path}' at offset {existing_offset}."
                    ) from e
                elif e.code == 429:
                    # Too Many Requests: inspect Retry-After
                    retry_after_str = e.headers.get("Retry-After")
                    wait_time = (
                        float(retry_after_str)
                        if retry_after_str and retry_after_str.isdigit()
                        else (2.0**attempt)
                    )
                    wait_time += random.uniform(0.1, 0.5)  # Jitter
                    time.sleep(min(wait_time, 30.0))
                    continue
                elif e.code >= 500:
                    # Server transient error: backoff and retry
                    wait_time = (1.5**attempt) + random.uniform(0.1, 0.4)
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            except (
                TimeoutError,
                urllib.error.URLError,
                http.client.IncompleteRead,
                ConnectionResetError,
            ):
                # Connection dropout or network timeout: backoff and resume
                wait_time = (1.5**attempt) + random.uniform(0.1, 0.4)
                time.sleep(wait_time)
                continue

        # If retries exhausted:
        self.journal.mark_file_failed(
            rel_path, f"Exhausted {self.plan.limits.max_retries} retries."
        )
        self.journal.save()
        raise RuntimeError(
            f"Failed to fetch '{rel_path}' after {self.plan.limits.max_retries} retries."
        )

    def run(self) -> AcquisitionState:
        """Execute acquisition across all selected files under plan process lock."""
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

        lock_path = self.lock_dir / f"acq_{self.plan.plan_id}.lock"
        try:
            with FileLock(str(lock_path), timeout=1.0):
                self.journal.state.status = "IN_PROGRESS"
                self.journal.reconcile_with_disk(self.output_dir, self.partial_dir)
                self.journal.save()

                # Process selected files using worker pool
                max_workers = min(self.plan.limits.max_workers, len(self.plan.selected_files))
                if max_workers <= 1:
                    for rel_file in self.plan.selected_files:
                        self._fetch_file(rel_file)
                else:
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_file = {
                            executor.submit(self._fetch_file, f): f
                            for f in self.plan.selected_files
                        }
                        for future in as_completed(future_to_file):
                            future.result()  # Raises exception if worker failed

                # Update final state metrics
                snap = self.capacity_mgr.snapshot()
                self.journal.state.transferred_bytes = snap["transferred_bytes"]
                self.journal.state.decompressed_bytes = snap["decompressed_bytes"]
                self.journal.state.temp_disk_bytes = snap["temp_disk_bytes"]
                self.journal.state.output_disk_bytes = snap["output_disk_bytes"]
                self.journal.state.cache_hits = snap["cache_hits"]
                self.journal.state.status = "COMPLETED"
                self.journal.save()
                return self.journal.state

        except Timeout as err:
            raise AcquisitionLockHeldError(
                f"Another process currently holds acquisition lock for plan '{self.plan.plan_id}'."
            ) from err
        except Exception as e:
            self.journal.state.status = (
                "INTERRUPTED" if isinstance(e, (BudgetExhaustedError, TimeoutError)) else "FAILED"
            )
            self.journal.state.error_reason = str(e)
            snap = self.capacity_mgr.snapshot()
            self.journal.state.transferred_bytes = snap["transferred_bytes"]
            self.journal.state.temp_disk_bytes = snap["temp_disk_bytes"]
            self.journal.state.output_disk_bytes = snap["output_disk_bytes"]
            self.journal.save()
            raise
