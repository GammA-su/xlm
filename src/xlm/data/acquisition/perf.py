"""Bounded acquisition-performance telemetry (measurement only).

A :class:`PerfTelemetry` instance rides along a single
:class:`~xlm.data.acquisition.fetcher.BoundedFetcher` run and records
monotonic-clock timings plus aggregate counters. It never touches the data
path: plan hashes, artifact identity, receipt identity, and
selected-record bytes are bit-for-bit unaffected by observation.

Results persist as a versioned diagnostic sidecar beneath the attempt's own
scratch tree (``scratch/performance/<plan_id>.perf.json``). The progress
journal, plan file, and receipt are never modified for telemetry.

Bounds (all enforced by construction):
- No per-request or per-row lists. Only aggregate counters plus the slowest
  few request opens (``MAX_SLOWEST_REQUESTS``) and per-file aggregates
  (files are already capped at 256 by plan validation).
- Never persist URLs, query strings, credentials, tokens, or response
  bodies. Only lowercased hostnames and caller-supplied fixed categories.
- All rates divide safely: a zero or missing duration yields 0.0, never an
  exception or infinity.

Bytes throughout mean application response-body bytes (the existing
``TransportBudget`` meaning). No TCP/TLS wire accounting is claimed and no
provider billing can be derived from these numbers.

Request semantics: ``logical_requests`` counts attempted request opens
(``record_request``); ``redirect_requests`` counts redirects actually
followed; ``accounted_network_requests`` (``logical + redirect``) reconciles
with the journal/status ``requests_made``, which charges both. Do not read
``logical_requests`` alone as the budget charge. With redirect-target reuse,
repeated ranges hit ``redirect_target_cache_hits`` (direct, no hop) while
``redirect_target_cache_misses`` count canonical resolutions and
``redirect_target_invalidations`` count expiry-triggered re-resolutions;
``connection_reuses``/``connection_creations`` cover pooled cached-target
Range GETs only.

Timing semantics: ``parquet_decode``/``decode_seconds`` is inclusive
Parquet row-group processing time (footer/metadata handling plus
blocking range I/O nested inside iteration plus CPU decode), not pure CPU.
Compare it against wall time, never as CPU-only; whole-run process CPU
can be far smaller.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PERF_VERSION = 1
MAX_SLOWEST_REQUESTS = 8


def _safe_div(numerator: float, denominator: float) -> float:
    """Divide defensively: zero-duration fixtures yield 0.0, never an error."""
    if denominator <= 0:
        return 0.0
    value = numerator / denominator
    if value != value or value in (float("inf"), float("-inf")):  # NaN guard
        return 0.0
    return value


def _sanitize_host(host: str) -> str:
    """Keep only a lowercased hostname; never URLs, queries, or credentials.

    Rejects anything resembling a URL, query string, userinfo, port path, or
    whitespace so a caller bug cannot persist secrets into the sidecar.
    """
    cleaned = (host or "").strip().lower()
    if not cleaned:
        return ""
    if any(token in cleaned for token in ("://", "?", "#", "@", "/", "\\", " ", "\t", "\n")):
        return ""
    # Drop a trailing dot and any port suffix; keep a plain hostname only.
    cleaned = cleaned.rstrip(".")
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[0]
    return cleaned


def cache_class_for(*, cache_hits: int, selected_files: int, transferred_bytes: int) -> str:
    """Classify uncached-network / partial-cache / cache-hit/local.

    - ``uncached-network``: no cache hits.
    - ``partial-cache``: some hits but network bytes still flowed.
    - ``cache-hit/local``: hits cover the workload (all files hit or no
      network bytes with at least one hit).
    """
    if cache_hits <= 0:
        return "uncached-network"
    if selected_files > 0 and cache_hits >= selected_files:
        return "cache-hit/local"
    if transferred_bytes <= 0:
        return "cache-hit/local"
    return "partial-cache"


def slowest_stage_of(shares: dict[str, float]) -> str | None:
    """Name the largest wall-share stage, or None when nothing was observed."""
    if not shares:
        return None
    best = max(shares.items(), key=lambda item: float(item[1] or 0.0))
    if float(best[1] or 0.0) <= 0:
        return None
    return str(best[0])


class PerfTelemetry:
    """Thread-safe timing aggregates for one acquisition attempt.

    ``now`` is injectable so tests can drive deterministic fake clocks;
    production uses :func:`time.monotonic`. ``process_time`` may be left
    ``None`` where CPU clocks are unavailable.
    """

    def __init__(
        self,
        *,
        now: Callable[[], float] | None = None,
        process_time: Callable[[], float] | None = None,
    ) -> None:
        self._now = now or time.monotonic
        self._process_time = process_time
        self._lock = threading.Lock()
        self.requests = 0
        self.redirects = 0
        self.redirect_cache_hits = 0
        self.redirect_cache_misses = 0
        self.redirect_cache_invalidations = 0
        self.connection_reuses = 0
        self.connection_creations = 0
        self.projection_selected_bytes = 0
        self.projection_skipped_bytes = 0
        self.coalesced_ranges = 0
        self.coalesced_gap_bytes = 0
        self.column_chunks_read = 0
        self.peak_rss_bytes = 0
        self.retries = 0
        self.retry_wait_seconds = 0.0
        self.open_seconds = 0.0
        self.body_seconds = 0.0
        self.metadata_seconds = 0.0
        self.decode_seconds = 0.0
        self.serialize_seconds = 0.0
        self.write_seconds = 0.0
        self.accounting_seconds = 0.0
        self.cache_hits = 0
        self.scanned_records = 0
        self.retained_records = 0
        self.decompressed_bytes = 0
        self.parquet_groups = 0
        self.by_host: dict[str, dict[str, float]] = {}
        self.slowest: list[dict[str, Any]] = []
        self.files: dict[str, dict[str, float]] = {}
        self._active_workers = 0
        self.max_active_workers = 0
        self.worker_seconds = 0.0

    # -- generic timing ----------------------------------------------------

    @contextmanager
    def timed(
        self,
        category: str,
        *,
        host: str = "",
        file: str = "",
        label: str | None = None,
    ) -> Iterator[None]:
        """Time one observational span and attribute it to ``category``.

        Categories are fixed caller labels (``open``, ``body``, ``metadata``,
        ``decode``, ``serialize``, ``write``, ``accounting``); hosts are sanitized
        hostnames only. Multi-worker overlap means category sums can exceed
        wall time; shares are reported against wall with that caveat.

        ``decode`` is inclusive Parquet row-group processing time: it wraps
        iteration that performs nested bounded range I/O (timed separately
        as ``open``/``body``) plus CPU decode. Never present it as CPU-only.
        """
        start = self._now()
        try:
            yield
        finally:
            elapsed = max(0.0, self._now() - start)
            safe_host = _sanitize_host(host)
            with self._lock:
                if category == "open":
                    self.open_seconds += elapsed
                    if safe_host:
                        entry = self.by_host.setdefault(
                            safe_host, {"requests": 0, "open_seconds": 0.0}
                        )
                        entry["open_seconds"] = float(entry["open_seconds"]) + elapsed
                    self.slowest.append(
                        {
                            "host": safe_host,
                            "category": label or category,
                            "file": file,
                            "seconds": elapsed,
                        }
                    )
                    self.slowest.sort(key=lambda item: float(item["seconds"]), reverse=True)
                    del self.slowest[MAX_SLOWEST_REQUESTS:]
                elif category == "body":
                    self.body_seconds += elapsed
                    if file:
                        self.files.setdefault(file, self._new_file_entry())["body_seconds"] += (
                            elapsed
                        )
                elif category == "metadata":
                    self.metadata_seconds += elapsed
                    if file:
                        self.files.setdefault(file, self._new_file_entry())["metadata_seconds"] += (
                            elapsed
                        )
                elif category == "decode":
                    self.decode_seconds += elapsed
                    if file:
                        self.files.setdefault(file, self._new_file_entry())["decode_seconds"] += (
                            elapsed
                        )
                elif category == "serialize":
                    self.serialize_seconds += elapsed
                    if file:
                        self.files.setdefault(file, self._new_file_entry())[
                            "serialize_seconds"
                        ] += elapsed
                elif category == "write":
                    self.write_seconds += elapsed
                    if file:
                        self.files.setdefault(file, self._new_file_entry())["write_seconds"] += (
                            elapsed
                        )
                elif category == "accounting":
                    self.accounting_seconds += elapsed

    @staticmethod
    def _new_file_entry() -> dict[str, float]:
        return {
            "seconds": 0.0,
            "requests": 0,
            "bytes": 0,
            "records": 0,
            "body_seconds": 0.0,
            "decode_seconds": 0.0,
            "metadata_seconds": 0.0,
            "serialize_seconds": 0.0,
            "write_seconds": 0.0,
        }

    # -- counters ----------------------------------------------------------

    def record_request(self, *, host: str = "", file: str = "") -> None:
        """Count one attempted request open (after budget admission)."""
        safe_host = _sanitize_host(host)
        with self._lock:
            self.requests += 1
            if safe_host:
                entry = self.by_host.setdefault(safe_host, {"requests": 0, "open_seconds": 0.0})
                entry["requests"] = float(entry["requests"]) + 1
            if file:
                self.files.setdefault(file, self._new_file_entry())["requests"] += 1

    def record_redirect(self, host: str) -> None:
        """Count one followed redirect; sanitized hostname only."""
        safe_host = _sanitize_host(host)
        with self._lock:
            self.redirects += 1
            if safe_host:
                self.by_host.setdefault(safe_host, {"requests": 0, "open_seconds": 0.0})

    def record_redirect_cache_hit(self) -> None:
        """Count one range operation served from a cached redirect target."""
        with self._lock:
            self.redirect_cache_hits += 1

    def record_redirect_cache_miss(self) -> None:
        """Count one canonical resolution performed with an empty cache entry."""
        with self._lock:
            self.redirect_cache_misses += 1

    def record_redirect_cache_invalidation(self) -> None:
        """Count one cached target discarded for expiry/auth/redirect semantics."""
        with self._lock:
            self.redirect_cache_invalidations += 1

    def record_connection_reuse(self) -> None:
        """Count one pooled keep-alive connection checkout (no new handshake)."""
        with self._lock:
            self.connection_reuses += 1

    def record_connection_creation(self) -> None:
        """Count one newly opened pooled connection (fresh TCP/TLS handshake)."""
        with self._lock:
            self.connection_creations += 1

    def record_retry(self, waited_seconds: float) -> None:
        """Count one scheduled re-attempt plus its bounded backoff wait."""
        with self._lock:
            self.retries += 1
            self.retry_wait_seconds += max(0.0, waited_seconds)

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_scanned(self, count: int, *, file: str = "") -> None:
        with self._lock:
            safe = max(0, count)
            self.scanned_records += safe
            if file:
                self.files.setdefault(file, self._new_file_entry())["records"] += safe

    def record_retained(self, count: int, *, file: str = "") -> None:
        with self._lock:
            self.retained_records += max(0, count)

    def record_decompressed(self, count: int) -> None:
        with self._lock:
            self.decompressed_bytes += max(0, count)

    def record_parquet_group(self) -> None:
        with self._lock:
            self.parquet_groups += 1

    def record_projection(self, *, selected_bytes: int, skipped_bytes: int) -> None:
        """Charge footer-known selected vs skipped column bytes for one group."""
        with self._lock:
            self.projection_selected_bytes += max(0, selected_bytes)
            self.projection_skipped_bytes += max(0, skipped_bytes)

    def record_coalesced_ranges(self, *, ranges: int, gap_bytes: int) -> None:
        """Count merged column-data spans fetched plus unused gap bytes inside."""
        with self._lock:
            self.coalesced_ranges += max(0, ranges)
            self.coalesced_gap_bytes += max(0, gap_bytes)

    def record_column_chunks(self, count: int) -> None:
        with self._lock:
            self.column_chunks_read += max(0, count)

    def record_peak_rss(self, rss_bytes: int) -> None:
        """Track the maximum observed worker RSS (observational only)."""
        with self._lock:
            self.peak_rss_bytes = max(self.peak_rss_bytes, max(0, rss_bytes))

    def record_file_bytes(self, file: str, count: int) -> None:
        with self._lock:
            self.files.setdefault(file, self._new_file_entry())["bytes"] += max(0, count)

    def record_write_seconds(self, seconds: float, *, file: str = "") -> None:
        """Attribute staged file-write seconds measured by the streaming writer."""
        with self._lock:
            self.write_seconds += max(0.0, seconds)
            if file:
                self.files.setdefault(file, self._new_file_entry())["write_seconds"] += max(
                    0.0, seconds
                )

    # -- file-worker concurrency -------------------------------------------

    @contextmanager
    def file_worker(self, file: str) -> Iterator[None]:
        """Track one file's worker lifetime: wall share plus concurrency.

        Callers must hold this around the actual file operation (network
        ranges, Parquet decode, record serialization), not merely around
        executor submission, so ``max_active_workers`` reflects real overlap.
        """
        start = self._now()
        with self._lock:
            self._active_workers += 1
            self.max_active_workers = max(self.max_active_workers, self._active_workers)
        try:
            yield
        finally:
            elapsed = max(0.0, self._now() - start)
            with self._lock:
                self._active_workers -= 1
                self.worker_seconds += elapsed
                self.files.setdefault(file, self._new_file_entry())["seconds"] += elapsed

    # -- snapshot / sidecar --------------------------------------------------

    def snapshot(
        self,
        *,
        plan: Any,
        status: str,
        wall_seconds: float,
        transferred_bytes: int,
        journal_decompressed_bytes: int,
        journal_requests: int,
        journal_cache_hits: int,
        journal_records: int,
        journal_stats: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Build the versioned sidecar document (pure data, no IO)."""
        cpu_seconds: float | None = None
        if self._process_time is not None:
            try:
                cpu_seconds = float(self._process_time())
            except Exception:
                cpu_seconds = None
        with self._lock:
            files = {name: dict(entry) for name, entry in self.files.items()}
            slowest = [dict(item) for item in self.slowest]
            by_host = {host: dict(entry) for host, entry in self.by_host.items()}
            counters = {
                "requests": self.requests,
                "logical_requests": self.requests,
                "redirects": self.redirects,
                "redirect_requests": self.redirects,
                "accounted_network_requests": self.requests + self.redirects,
                "redirect_target_cache_hits": self.redirect_cache_hits,
                "redirect_target_cache_misses": self.redirect_cache_misses,
                "redirect_target_invalidations": self.redirect_cache_invalidations,
                "connection_reuses": self.connection_reuses,
                "connection_creations": self.connection_creations,
                "projection_selected_bytes": self.projection_selected_bytes,
                "projection_skipped_bytes": self.projection_skipped_bytes,
                "coalesced_ranges": self.coalesced_ranges,
                "coalesced_gap_bytes": self.coalesced_gap_bytes,
                "column_chunks_read": self.column_chunks_read,
                "peak_rss_bytes": self.peak_rss_bytes,
                "retries": self.retries,
                "retry_wait_seconds": self.retry_wait_seconds,
                "open_seconds": self.open_seconds,
                "body_seconds": self.body_seconds,
                "metadata_seconds": self.metadata_seconds,
                "decode_seconds": self.decode_seconds,
                "serialize_seconds": self.serialize_seconds,
                "write_seconds": self.write_seconds,
                "accounting_seconds": self.accounting_seconds,
                "cache_hits_telemetry": self.cache_hits,
                "scanned_records": self.scanned_records,
                "retained_records": self.retained_records,
                "decompressed_bytes_telemetry": self.decompressed_bytes,
                "parquet_groups": self.parquet_groups,
                "max_active_workers": self.max_active_workers,
                "worker_seconds": self.worker_seconds,
            }
        limits = plan.limits
        shares = {
            "request_open": _safe_div(counters["open_seconds"], wall_seconds),
            "body_stream": _safe_div(counters["body_seconds"], wall_seconds),
            "parquet_metadata": _safe_div(counters["metadata_seconds"], wall_seconds),
            "parquet_decode": _safe_div(counters["decode_seconds"], wall_seconds),
            "serialize_write": _safe_div(
                counters["serialize_seconds"] + counters["write_seconds"], wall_seconds
            ),
            "accounting_lock": _safe_div(counters["accounting_seconds"], wall_seconds),
            "retry_wait": _safe_div(counters["retry_wait_seconds"], wall_seconds),
        }
        ranked_files = [
            (name, float(entry.get("seconds", 0.0) or 0.0)) for name, entry in files.items()
        ]
        ranked_files.sort(key=lambda pair: pair[1], reverse=True)
        slowest_files = [
            {"file": name, "seconds": seconds}
            for name, seconds in ranked_files[:MAX_SLOWEST_REQUESTS]
        ]
        selected = sorted(plan.selected_files)
        cache_class = cache_class_for(
            cache_hits=journal_cache_hits,
            selected_files=len(selected),
            transferred_bytes=transferred_bytes,
        )
        return {
            "perf_version": PERF_VERSION,
            "plan_id": plan.plan_id,
            "attempt": getattr(plan, "attempt", 1),
            "source_id": plan.source_id,
            "view_id": plan.view_id,
            "revision": plan.revision,
            "provider": getattr(plan, "provider", ""),
            "repository": getattr(plan, "repository", ""),
            "mode": plan.mode.value if hasattr(plan.mode, "value") else str(plan.mode),
            "selected_files": selected,
            "row_ranges": {
                name: [int(start), int(stop)]
                for name, (start, stop) in sorted((plan.row_ranges or {}).items())
            },
            "projected_fields": (
                sorted(plan.projected_fields)
                if getattr(plan, "projected_fields", None) is not None
                else None
            ),
            "range_coalesce_bytes": getattr(plan, "range_coalesce_bytes", None),
            "limits": {
                "max_transferred_bytes": limits.max_transferred_bytes,
                "max_decompressed_bytes": limits.max_decompressed_bytes,
                "max_records": limits.max_records,
                "max_requests": limits.max_requests,
                "max_workers": limits.max_workers,
                "overall_deadline_seconds": limits.overall_deadline_seconds,
                "max_temp_disk_bytes": limits.max_temp_disk_bytes,
                "max_output_disk_bytes": limits.max_output_disk_bytes,
                "max_retries": limits.max_retries,
                "per_request_timeout_seconds": limits.per_request_timeout_seconds,
                "max_decompression_ratio": limits.max_decompression_ratio,
                "max_record_bytes": limits.max_record_bytes,
                "max_parser_bytes": limits.max_parser_bytes,
                "max_scanned_records": limits.max_scanned_records,
            },
            "status": status,
            "wall_seconds": wall_seconds,
            "cpu_process_seconds": cpu_seconds,
            "max_workers_configured": limits.max_workers,
            "cache_hits": journal_cache_hits,
            "cache_class": cache_class,
            "transferred_bytes": transferred_bytes,
            "decompressed_bytes": journal_decompressed_bytes,
            "requests_made": journal_requests,
            "records_acquired": journal_records,
            "telemetry": counters,
            "by_host": by_host,
            "slowest_requests": slowest,
            "slowest_files": slowest_files,
            "files": files,
            "rates": {
                "application_mb_per_sec": _safe_div(transferred_bytes / (1024**2), wall_seconds),
                "decompressed_mb_per_sec": _safe_div(
                    journal_decompressed_bytes / (1024**2), wall_seconds
                ),
                "records_scanned_per_sec": _safe_div(counters["scanned_records"], wall_seconds),
                "retained_records_per_sec": _safe_div(counters["retained_records"], wall_seconds),
                "requests_per_sec": _safe_div(counters["requests"], wall_seconds),
            },
            "time_shares_of_wall": shares,
            "slowest_stage": slowest_stage_of(shares),
            "average_concurrency": _safe_div(counters["worker_seconds"], wall_seconds),
            "journal": dict(journal_stats) if journal_stats else {},
            "request_accounting": {
                "logical_requests": counters["logical_requests"],
                "redirect_requests": counters["redirect_requests"],
                "accounted_network_requests": counters["accounted_network_requests"],
                "redirect_target_cache_hits": counters["redirect_target_cache_hits"],
                "redirect_target_cache_misses": counters["redirect_target_cache_misses"],
                "redirect_target_invalidations": counters["redirect_target_invalidations"],
                "connection_reuses": counters["connection_reuses"],
                "connection_creations": counters["connection_creations"],
                "coalesced_ranges": counters["coalesced_ranges"],
                "coalesced_gap_bytes": counters["coalesced_gap_bytes"],
                "journal_requests_made": journal_requests,
                "reconciled": bool(counters["accounted_network_requests"] == journal_requests),
            },
            "notes": [
                "Bytes are application response-body bytes; no TCP/TLS wire accounting.",
                "Category sums may exceed wall time under multi-worker overlap.",
                "Hosts are sanitized hostnames; no URLs, queries, or credentials stored.",
                "requests=logical opens; redirects are followed hops; "
                "accounted_network_requests=logical+redirect reconciles with "
                "journal requests_made (budget charges both).",
                "redirect_target_cache_hits served direct with no hop; misses "
                "resolved canonically; invalidations re-resolved on expiry/auth.",
                "connection_reuses/creations cover pooled cached-target Range "
                "GETs only; canonical resolutions stay on the urllib opener.",
                "projection_selected/skipped_bytes come from footer metadata for "
                "touched row groups; coalesced_ranges counts merged column-data "
                "spans; peak_rss_bytes is a best-effort worker maximum.",
                "serialize_write share covers serialize plus file-write time, so it "
                "stays comparable with pre-write-split sidecars.",
                "Signed redirect targets live only in fetcher memory; never in "
                "sidecars, journals, receipts, or logs.",
                "parquet_decode is inclusive row-group processing time including "
                "nested range I/O plus CPU decode, not CPU-only; compare to wall, "
                "not to process CPU.",
                "average_concurrency is observational only and never alters identity.",
            ],
        }

    def write_sidecar(self, scratch_dir: Path, plan_id: str, doc: dict[str, Any]) -> Path:
        """Persist the sidecar atomically under the attempt's owned scratch tree."""
        target_dir = scratch_dir / "performance"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{plan_id}.perf.json"
        payload = json.dumps(doc, indent=2).encode("utf-8")
        temporary = target_dir / f"{target.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target


def load_perf_doc(path: Path) -> dict[str, Any]:
    """Load one sidecar with fail-closed validation (no secrets ever logged).

    Raises ValueError with a short reason for missing/oversize/malformed
    documents or unsupported versions. Callers surface the reason without
    echoing file contents.
    """
    if not path.is_file():
        raise ValueError(f"performance sidecar not found: {path.name}")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError(f"performance sidecar exceeds 1 MiB: {path.name}")
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            f"malformed performance sidecar {path.name}: {type(exc).__name__}"
        ) from exc
    if not isinstance(doc, dict):
        raise ValueError(f"malformed performance sidecar {path.name}: not an object")
    if doc.get("perf_version") != PERF_VERSION:
        raise ValueError(f"unsupported perf_version {doc.get('perf_version')!r} in {path.name}")
    for key in ("plan_id", "status", "wall_seconds", "telemetry"):
        if key not in doc:
            raise ValueError(f"malformed performance sidecar {path.name}: missing {key!r}")
    return doc


def _doc_cache_class(doc: dict[str, Any]) -> str:
    """Prefer the recorded class; recompute from counts for older sidecars."""
    recorded = doc.get("cache_class")
    if isinstance(recorded, str) and recorded:
        return recorded
    return cache_class_for(
        cache_hits=int(doc.get("cache_hits", 0) or 0),
        selected_files=len(doc.get("selected_files", []) or []),
        transferred_bytes=int(doc.get("transferred_bytes", 0) or 0),
    )


def compare_perf_docs(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare performance sidecars from separate attempts of one workload.

    Equivalence is strict on workload identity (source, view, revision,
    provider semantics, files, row ranges, mode, projected fields,
    byte/record/parser/decompression limits) and run class (all COMPLETED,
    same cache class). Attempt number, plan identity, max_workers, and range
    coalescing may differ — those are the comparison dimensions. Anything
    else is refused with explicit reasons, never silently merged.
    """
    refusals: list[str] = []
    if len(docs) < 2:
        refusals.append("comparison needs at least two performance documents")
    for index, doc in enumerate(docs):
        if doc.get("perf_version") != PERF_VERSION:
            refusals.append(
                f"document {index}: unsupported perf_version {doc.get('perf_version')!r}"
            )
        if doc.get("status") != "COMPLETED":
            refusals.append(
                f"document {index} ({doc.get('plan_id')}): status "
                f"{doc.get('status')!r} is not COMPLETED; failed runs are never ranked"
            )
    if not refusals:
        identity_keys = (
            "source_id",
            "view_id",
            "revision",
            "provider",
            "selected_files",
            "row_ranges",
            "mode",
        )
        first = docs[0]
        for index, doc in enumerate(docs[1:], start=1):
            for key in identity_keys:
                if doc.get(key) != first.get(key):
                    refusals.append(
                        f"document {index} ({doc.get('plan_id')}): {key} differs "
                        f"({doc.get(key)!r} != {first.get(key)!r})"
                    )
            if (doc.get("projected_fields") or None) != (first.get("projected_fields") or None):
                refusals.append(
                    f"document {index} ({doc.get('plan_id')}): projected fields differ "
                    "(different acquired artifacts are never ranked together)"
                )
        limit_keys = (
            "max_transferred_bytes",
            "max_decompressed_bytes",
            "max_records",
            "max_requests",
            "max_temp_disk_bytes",
            "max_output_disk_bytes",
            "max_record_bytes",
            "max_parser_bytes",
            "max_scanned_records",
            "max_decompression_ratio",
        )
        for index, doc in enumerate(docs[1:], start=1):
            for key in limit_keys:
                if doc.get("limits", {}).get(key) != first.get("limits", {}).get(key):
                    refusals.append(f"document {index} ({doc.get('plan_id')}): limit {key} differs")
        cache_classes = {index: _doc_cache_class(doc) for index, doc in enumerate(docs)}
        if len(set(cache_classes.values())) > 1:
            detail = ", ".join(f"{index}:{cls}" for index, cls in sorted(cache_classes.items()))
            refusals.append(
                "cached and uncached runs are not comparable as equivalent "
                f"({detail}); compare within one cache class"
            )
    if refusals:
        return {"comparable": False, "refusals": refusals, "entries": [], "fastest": None}
    entries = []
    for doc in docs:
        rates = doc.get("rates", {})
        shares = doc.get("time_shares_of_wall", {})
        telemetry = doc.get("telemetry", {})
        logical = telemetry.get("logical_requests", telemetry.get("requests"))
        redirect = telemetry.get("redirect_requests", telemetry.get("redirects"))
        accounted = telemetry.get("accounted_network_requests")
        if accounted is None and logical is not None and redirect is not None:
            try:
                accounted = int(logical) + int(redirect)
            except (TypeError, ValueError):
                accounted = None
        entries.append(
            {
                "plan_id": doc.get("plan_id"),
                "attempt": doc.get("attempt"),
                "status": doc.get("status"),
                "max_workers": doc.get("limits", {}).get("max_workers"),
                "wall_seconds": doc.get("wall_seconds"),
                "application_mb": doc.get("transferred_bytes", 0) / (1024**2),
                "application_mb_per_sec": rates.get("application_mb_per_sec"),
                "decompressed_mb_per_sec": rates.get("decompressed_mb_per_sec"),
                "records_scanned_per_sec": rates.get("records_scanned_per_sec"),
                "retained_records_per_sec": rates.get("retained_records_per_sec"),
                "requests_per_sec": rates.get("requests_per_sec"),
                "request_open_share": shares.get("request_open"),
                "body_stream_share": shares.get("body_stream"),
                "parquet_metadata_share": shares.get("parquet_metadata"),
                "parquet_decode_share": shares.get("parquet_decode"),
                "serialize_write_share": shares.get("serialize_write"),
                "accounting_lock_share": shares.get("accounting_lock"),
                "max_active_workers": telemetry.get("max_active_workers"),
                "average_concurrency": doc.get("average_concurrency"),
                "cache_class": _doc_cache_class(doc),
                "cache_hits": doc.get("cache_hits"),
                "requests": telemetry.get("requests"),
                "logical_requests": logical,
                "redirect_requests": redirect,
                "accounted_network_requests": accounted,
                "journal_requests_made": doc.get("requests_made"),
                "redirect_target_cache_hits": telemetry.get("redirect_target_cache_hits", 0),
                "redirect_target_cache_misses": telemetry.get("redirect_target_cache_misses", 0),
                "redirect_target_invalidations": telemetry.get("redirect_target_invalidations", 0),
                "connection_reuses": telemetry.get("connection_reuses", 0),
                "connection_creations": telemetry.get("connection_creations", 0),
                "projected_fields": doc.get("projected_fields"),
                "range_coalesce_bytes": doc.get("range_coalesce_bytes"),
                "projection_skipped_bytes": telemetry.get("projection_skipped_bytes", 0),
                "coalesced_ranges": telemetry.get("coalesced_ranges", 0),
                "coalesced_gap_bytes": telemetry.get("coalesced_gap_bytes", 0),
                "column_chunks_read": telemetry.get("column_chunks_read", 0),
                "peak_rss_bytes": telemetry.get("peak_rss_bytes", 0),
                "retries": telemetry.get("retries"),
                "slowest_stage": doc.get("slowest_stage"),
            }
        )
    fastest = min(entries, key=lambda entry: float(entry["wall_seconds"] or float("inf")))
    return {
        "comparable": True,
        "refusals": [],
        "entries": entries,
        "fastest": {
            "plan_id": fastest["plan_id"],
            "attempt": fastest["attempt"],
            "max_workers": fastest["max_workers"],
            "wall_seconds": fastest["wall_seconds"],
            "note": "observed fastest completed measurement, not a universal recommendation",
        },
    }
