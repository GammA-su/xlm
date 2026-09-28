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
    """Validate one recorded text chunk against its immutable file identity."""
    offset = chunk.get("offset")
    compressed = chunk.get("compressed_bytes")
    if type(offset) is not int or type(compressed) is not int:
        raise ScheduleError("chunk offset/length unavailable: cannot schedule")
    dictionary = chunk.get("dictionary_page_offset")
    if dictionary is not None and not 0 <= int(dictionary) <= offset:
        raise ScheduleError("dictionary page outside the chunk span")
    if not 0 <= offset < offset + compressed <= remote_length:
        raise ScheduleError("chunk span outside the immutable file length")
    return offset, compressed


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
    carried_bytes: int,
    request_cap: int,
    byte_cap: int,
    redirect_allowance_requests: int = 0,
) -> dict[str, Any]:
    """Remaining request/byte budgets after carry-in and the nominal plan."""
    nominal_requests = nominal_data_ranges + nominal_controls + redirect_allowance_requests
    return {
        "nominal_requests": nominal_requests,
        "nominal_data_bytes": nominal_data_bytes,
        "request_cap": request_cap,
        "byte_cap": byte_cap,
        "requests_remaining_after_nominal": request_cap - carried_requests - nominal_requests,
        "bytes_remaining_after_nominal": byte_cap - carried_bytes - nominal_data_bytes,
        "attempt_bounds": max_attempts(
            nominal_requests=nominal_requests,
            nominal_bytes=nominal_data_bytes,
            used_requests=carried_requests,
            used_bytes=carried_bytes,
            request_cap=request_cap,
            byte_cap=byte_cap,
        ),
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
