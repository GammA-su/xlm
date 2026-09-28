"""Exact offline Arm-T data range schedules (v2.1, no network).

Builds executable-shaped range plans from already-recorded immutable
text-column chunk offsets and compressed lengths (v2.0 cost-map units):
each chunk span is split into response ranges of at most 4,194,304
bytes, plus nominal control/revalidation requests per file. Whole text
column chunks remain the decoding unit; no page-level sparse reads are
planned or promised. Retry/redirect maxima are ceilings that stop
execution, never guaranteed completed retries. All arithmetic is
offline and deterministic; the schedule binds chunk spans, file
identity, caps, and carry-in, and reports remaining budgets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import frozen


class ScheduleError(ValueError):
    """Any uncovered span, overlap, cap breach, or identity drift: refuse."""


RANGE_MAX = 4194304
CONTROL_REQUESTS_PER_FILE = 2


def split_span(
    offset: int, compressed: int, *, range_max: int = RANGE_MAX
) -> list[tuple[int, int]]:
    """Split one [offset, offset+compressed) chunk span into bounded ranges.

    Ranges are exact, contiguous, non-overlapping, and cover the complete
    chunk; the dictionary page travels inside the chunk span by Parquet
    layout (dictionary first), so no separate dictionary range is needed.
    """
    if type(offset) is not int or type(compressed) is not int:
        raise ScheduleError("chunk offset/length must be JSON integers")
    if offset < 0 or compressed <= 0:
        raise ScheduleError("chunk span is empty or negative")
    if range_max <= 0:
        raise ScheduleError("range maximum must be positive")
    ranges: list[tuple[int, int]] = []
    cursor = offset
    stop = offset + compressed
    while cursor < stop:
        end = min(cursor + range_max, stop)
        ranges.append((cursor, end))
        cursor = end
    covered = sum(end - start for start, end in ranges)
    if covered != compressed:
        raise ScheduleError("range split does not cover the chunk exactly")
    for (first_start, first_end), (second_start, second_end) in zip(
        ranges, ranges[1:], strict=False
    ):
        if not first_start < first_end <= second_start < second_end:
            raise ScheduleError("range split overlaps or leaves a gap")
        if first_end - first_start > range_max or second_end - second_start > range_max:
            raise ScheduleError("range exceeds the response-body ceiling")
    return ranges


def chunk_span(chunk: Mapping[str, Any], remote_length: int) -> tuple[int, int]:
    """Validate one recorded text chunk against its immutable file identity.

    The complete chunk begins at the earliest required dictionary or data
    page offset (dictionaries precede data pages in every observed chunk);
    the full compressed length covers that complete chunk. Starting at the
    data offset instead omits the dictionary prefix and overruns the true
    end by exactly the omitted length.
    """
    offset = chunk.get("offset")
    compressed = chunk.get("compressed_bytes")
    if type(offset) is not int or type(compressed) is not int:
        raise ScheduleError("chunk offset/length unavailable: cannot schedule")
    dictionary = chunk.get("dictionary_page_offset")
    if dictionary is not None and type(dictionary) is not int:
        raise ScheduleError("dictionary offset is not an integer")
    start = min(offset, dictionary) if dictionary is not None else offset
    if dictionary is not None and not 0 <= dictionary <= offset:
        raise ScheduleError("dictionary page outside the chunk span")
    if not 0 <= start < start + compressed <= remote_length:
        raise ScheduleError("chunk span outside the immutable file length")
    return start, compressed


def schedule_file(
    source_file: str,
    chunks: Sequence[Mapping[str, Any]],
    remote_length: int,
    *,
    control_requests: int = CONTROL_REQUESTS_PER_FILE,
) -> dict[str, Any]:
    """Nominal data ranges plus controls for one file's recorded chunks."""
    spans = [chunk_span(chunk, remote_length) for chunk in chunks]
    ranges: list[dict[str, int]] = []
    for offset, compressed in spans:
        for start, end in split_span(offset, compressed):
            ranges.append({"start": start, "end": end, "bytes": end - start})
    nominal_data_bytes = sum(entry["bytes"] for entry in ranges)
    return {
        "file": source_file,
        "chunk_count": len(spans),
        "nominal_data_ranges": ranges,
        "nominal_data_range_count": len(ranges),
        "nominal_data_bytes": nominal_data_bytes,
        "nominal_control_requests": control_requests,
        "nominal_requests": len(ranges) + control_requests,
    }


def max_attempts(
    *,
    nominal_requests: int,
    nominal_bytes: int,
    used_requests: int,
    used_bytes: int,
    request_cap: int,
    byte_cap: int,
    redirect_allowance_requests: int = 0,
) -> dict[str, Any]:
    """Bounded re-attempt capacity before each cap binds (not a promise).

    A full nominal re-attempt costs the nominal requests plus the redirect
    allowance and the nominal bytes. Execution stops at the first binding
    ceiling; partial retries are not modeled as completable paths.
    """
    if nominal_requests <= 0 or nominal_bytes <= 0:
        raise ScheduleError("nominal schedule must be positive")
    by_requests = (request_cap - used_requests) // (nominal_requests + redirect_allowance_requests)
    by_bytes = (byte_cap - used_bytes) // nominal_bytes
    fitting = max(0, min(by_requests, by_bytes))
    return {
        "nominal_requests_per_attempt": nominal_requests,
        "nominal_bytes_per_attempt": nominal_bytes,
        "redirect_allowance_requests": redirect_allowance_requests,
        "full_attempts_by_requests": max(0, by_requests),
        "full_attempts_by_bytes": max(0, by_bytes),
        "full_nominal_executions_fitting": fitting,
        "reattempts_beyond_nominal": max(0, fitting - 1),
    }


def schedule_arm(
    files: Mapping[str, Sequence[Mapping[str, Any]]],
    remote_lengths: Mapping[str, int],
    *,
    control_requests: int = CONTROL_REQUESTS_PER_FILE,
) -> dict[str, Any]:
    """Nominal schedules for every file, in sorted file order."""
    scheduled = {
        name: schedule_file(
            name, files[name], remote_lengths[name], control_requests=control_requests
        )
        for name in sorted(files)
    }
    return {
        "files": scheduled,
        "nominal_data_range_count": sum(v["nominal_data_range_count"] for v in scheduled.values()),
        "nominal_data_bytes": sum(v["nominal_data_bytes"] for v in scheduled.values()),
        "nominal_control_requests": sum(v["nominal_control_requests"] for v in scheduled.values()),
    }


def remaining_budgets(
    *,
    nominal_data_ranges: int,
    nominal_data_bytes: int,
    nominal_controls: int,
    carried_requests: int,
    carried_data_bytes: int = 0,
    carried_footer_bytes: int = 0,
    request_cap: int,
    data_cap: int,
    total_cap: int,
    redirect_allowance_requests: int = 0,
) -> dict[str, Any]:
    """Remaining budgets after carry-in and the nominal plan.

    Footer usage is NEVER subtracted from the data subcap; the combined
    total binds recorded bodies of both stages plus nominal data bytes.
    """
    nominal_requests = nominal_data_ranges + nominal_controls + redirect_allowance_requests
    return {
        "nominal_requests": nominal_requests,
        "nominal_data_bytes": nominal_data_bytes,
        "request_cap": request_cap,
        "data_cap": data_cap,
        "total_cap": total_cap,
        "requests_remaining_after_nominal": request_cap - carried_requests - nominal_requests,
        "data_remaining_after_nominal": data_cap - carried_data_bytes - nominal_data_bytes,
        "total_remaining_after_nominal": (
            total_cap - carried_footer_bytes - carried_data_bytes - nominal_data_bytes
        ),
        "attempt_bounds": max_attempts(
            nominal_requests=nominal_requests,
            nominal_bytes=nominal_data_bytes,
            used_requests=carried_requests,
            used_bytes=carried_data_bytes,
            request_cap=request_cap,
            byte_cap=data_cap,
        ),
    }


def physical_schedule(
    *,
    nominal_data_ranges: int,
    nominal_controls: int,
    revalidation_logical: int = 0,
    cached_reuse: bool = True,
    max_follows_per_resolution: int = 3,
) -> dict[str, Any]:
    """Redirect-aware bounded physical request counts for one file or arm.

    Model grounded in the existing immutable target-cache semantics: the
    first resolution per file (revalidation, when present, else the first
    data range) may follow up to ``max_follows_per_resolution`` redirects;
    with ``cached_reuse`` later ranges reuse the validated target with zero
    new follows. Without reuse every logical request may follow the
    maximum. Retries are bounded stop paths counted in ``max_attempts``,
    never promised paths.
    """
    logical = nominal_data_ranges + nominal_controls + revalidation_logical
    if cached_reuse:
        resolution_events = 1 if logical > 0 else 0
    else:
        resolution_events = logical
    max_follows = resolution_events * max_follows_per_resolution
    return {
        "logical_requests": logical,
        "resolution_events": resolution_events,
        "max_redirect_follows": max_follows,
        "nominal_physical_requests": logical,
        "max_physical_requests": logical + max_follows,
        "cached_reuse": cached_reuse,
    }


def m_revalidation_plan(
    files: Mapping[str, Mapping[str, Any]],
    *,
    historical_requests: Mapping[str, int] | None = None,
    file_cap: int = 16,
    arm_cap: int = 80,
) -> dict[str, Any]:
    """Minimal future M revalidation: one 0-3 GET per file, ETag-bound.

    ``files`` maps path -> {"etag": strong ETag, "remote_length": int}.
    Prospective reservations: <=4 physical/file, <=25/arm, nested inside
    cumulative 16/file and 80/arm. Nominal routing observes one redirect
    (2 physical/file, 16/arm). Never HEAD, footer refetch, relist, or
    window changes. With ``historical_requests``, reports remaining
    budgets after nominal revalidation (history + nominal vs caps).
    """
    per_file: dict[str, Any] = {}
    for name in sorted(files):
        identity = files[name]
        etag = identity.get("etag")
        length = identity.get("remote_length")
        if not isinstance(etag, str) or not etag.startswith('"') or type(length) is not int:
            raise ScheduleError(f"revalidation identity unavailable for {name}")
        used = int(historical_requests.get(name, 0)) if historical_requests else 0
        per_file[name] = {
            "logical_request": {"method": "GET", "range": [0, 3]},
            "expected_status": 206,
            "expected_content_range": [0, 3, length],
            "expected_body": "PAR1",
            "expected_etag": etag,
            "nominal_physical_requests": 2,
            "max_physical_requests": 4,
            "historical_requests": used,
            "remaining_after_nominal": file_cap - used - 2,
            "remaining_v22": file_cap - used,
        }
    per_file_max = 4 * len(per_file)
    historical_total = sum(v["historical_requests"] for v in per_file.values())
    return {
        "files": per_file,
        "nominal_physical_requests_arm": 2 * len(per_file),
        "max_physical_requests_arm": min(per_file_max, 25),
        "max_physical_unbounded_sum": per_file_max,
        "arm_reservation_cap": 25,
        "historical_requests_arm": historical_total,
        "arm_remaining_after_nominal": arm_cap - historical_total - 2 * len(per_file),
        "note": (
            "per-file maxima are mutually exclusive under the 25 arm cap: "
            "all eight 4-request chains (32) do not fit; stop incomplete"
        ),
    }


def check_revalidation_response(
    *,
    expected_etag: str,
    expected_length: int,
    status: int | None,
    content_range: tuple[int, int, int] | None,
    body: bytes,
    etag: str | None,
    final_host_allowed: bool,
) -> dict[str, Any]:
    """Validate one revalidation response; any mismatch is a STOP."""
    reasons: list[str] = []
    if status != 206:
        reasons.append(f"status {status} is not 206")
    if content_range != (0, 3, expected_length):
        reasons.append("Content-Range is not exactly 0-3/recorded-length")
    if bytes(body) != b"PAR1":
        reasons.append("body is not the 4-byte PAR1 magic")
    if not isinstance(etag, str) or etag != expected_etag:
        reasons.append("ETag missing/weak/different from the adopted observation")
    if not final_host_allowed:
        reasons.append("final host failed allowlist validation")
    return {"accepted": not reasons, "reasons": reasons}


def m_data_schedule(
    units: Sequence[Mapping[str, Any]],
    *,
    controls_per_plan: int = 4,
) -> dict[str, Any]:
    """Redirect-aware M data request schedule from adopted window evidence.

    Per plan nominal comes from the recorded planner estimate
    (``future_plan_feasibility.request_estimate``, 85): a leaf-range
    estimate plus controls, not a redirect-aware physical guarantee. The
    physical model adds first-resolution follows under cached target
    reuse (later ranges reuse the validated target). Fits are checked
    against 100/plan, 800 data/arm, 880 combined. Chunk byte positions
    are not recorded in M evidence, so range positions resolve at
    execution from the revalidated immutable identity; counts recorded
    here are exact, positions deferred (stated, not hidden).
    """
    plans: dict[str, Any] = {}
    for unit in units:
        feasibility = unit.get("future_plan_feasibility", {})
        nominal = feasibility.get("request_estimate")
        if type(nominal) is not int or nominal <= 0:
            raise ScheduleError(f"plan estimate missing for {unit.get('file')}")
        report = unit.get("window_report", {})
        leaves = report.get("projected_physical_leaves", [])
        selected = sum(int(leaf.get("compressed_bytes", 0)) for leaf in leaves)
        physical = physical_schedule(
            nominal_data_ranges=nominal,
            nominal_controls=0,
            revalidation_logical=0,
            cached_reuse=True,
        )
        plans[str(unit.get("file"))] = {
            "recorded_estimate": nominal,
            "selected_compressed_bytes": selected,
            "nominal_requests": physical["nominal_physical_requests"],
            "max_physical_requests": physical["max_physical_requests"],
            "fits_plan": physical["max_physical_requests"] <= 100,
            "positions": "UNRESOLVED_until_revalidated_execution",
        }
    nominal_total = sum(v["nominal_requests"] for v in plans.values())
    max_total = sum(v["max_physical_requests"] for v in plans.values())
    return {
        "plans": plans,
        "nominal_requests_arm": nominal_total,
        "max_physical_requests_arm": max_total,
        "fits_data_arm": max_total <= 800,
    }


def t_physical_plan(
    *,
    files: Mapping[str, Mapping[str, int]],
    carried_requests: int,
    carried_bytes: int,
    request_cap: int = 800,
    data_cap: int = 251658240,
    total_cap: int = 268435456,
) -> dict[str, Any]:
    """Redirect-aware Arm-T physical schedule from nominal file schedules.

    ``files`` maps path -> {nominal_ranges, nominal_bytes} (dictionary-
    corrected). Each file revalidates once (1 logical + <=3 follows, cached
    reuse after), then data ranges reuse the validated target. Retries are
    bounded stop paths via attempt bounds, never promised paths.
    """
    per_file: dict[str, Any] = {}
    for name in sorted(files):
        cell = files[name]
        physical = physical_schedule(
            nominal_data_ranges=int(cell["nominal_ranges"]),
            nominal_controls=int(cell.get("nominal_controls", 2)),
            revalidation_logical=1,
            cached_reuse=True,
        )
        per_file[name] = {
            "nominal_data_ranges": int(cell["nominal_ranges"]),
            "nominal_data_bytes": int(cell["nominal_bytes"]),
            "nominal_physical_requests": physical["nominal_physical_requests"],
            "max_physical_requests": physical["max_physical_requests"],
            "fits_file_requests": physical["max_physical_requests"] <= 100,
        }
    nominal_ranges = sum(int(c["nominal_ranges"]) for c in files.values())
    nominal_bytes = sum(int(c["nominal_bytes"]) for c in files.values())
    nominal_physical = sum(v["nominal_physical_requests"] for v in per_file.values())
    max_physical = sum(v["max_physical_requests"] for v in per_file.values())
    return {
        "files": per_file,
        "nominal_data_ranges": nominal_ranges,
        "nominal_data_bytes": nominal_bytes,
        "nominal_physical_requests": nominal_physical,
        "max_physical_requests": max_physical,
        "with_carry_requests": nominal_physical + carried_requests,
        "with_carry_bytes": nominal_bytes + carried_bytes,
        "fits_requests": nominal_physical + carried_requests <= request_cap,
        "fits_data": nominal_bytes + carried_bytes <= data_cap,
        "fits_total": nominal_bytes + carried_bytes <= total_cap,
        "fits_max_requests": max_physical + carried_requests <= request_cap,
    }


def v21_data_caps() -> dict[str, int]:
    """Frozen v2.1 Arm-T transfer ceilings for schedule checks."""
    return {
        "per_file_data": frozen.V21_T_LIMITS["data_bytes_per_file_max"],
        "per_file_total": frozen.V21_T_LIMITS["transfer_bytes_per_file_max"],
        "arm_data": frozen.V21_T_LIMITS["data_bytes_arm_max"],
        "arm_total": frozen.V21_T_LIMITS["transfer_bytes_arm_max"],
        "per_file_requests": frozen.V21_T_LIMITS["requests_per_file_max"],
        "arm_requests": frozen.V21_T_LIMITS["requests_arm_max"],
    }
