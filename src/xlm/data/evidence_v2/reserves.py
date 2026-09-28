"""Memory/disk/runtime execution reservations and guards (v2.1, offline).

Numeric workspace estimates are NOT measured peak RSS. This module turns
recorded chunk sizes into conservative offline reservations with explicit
components, refuses when they do not fit the frozen ceilings, and designs
the supervised enforcement (process-tree supervisor, monotonic deadlines,
disk high-water schedule) future execution must implement. Refusal here
is a planning verdict, not a performance measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import frozen


class ReservationError(ValueError):
    """A reservation does not fit its ceiling, or inputs are unbounded: refuse."""


def _require_non_negative(name: str, value: int) -> int:
    if type(value) is not int or value < 0:
        raise ReservationError(f"reservation input {name} must be a non-negative integer")
    return value


def memory_reservation(
    *,
    compressed_staged_bytes: int,
    decompressed_upper_bytes: int,
    range_buffer_bytes: int = 4194304,
    parser_bytes: int = 33554432,
    output_retained_bytes: int,
    duplicate_temp_copies: int = 1,
    cap_bytes: int = 268435456,
) -> dict[str, Any]:
    """Conservative one-file decode reservation with named components.

    Covers the staged compressed chunk, worst-case decompressed Arrow
    buffers, one in-flight range buffer, the parser, retained output text,
    and duplicate temporary copies during atomic serialization. A
    process-tree supervisor must refuse execution when measured RSS would
    exceed the cap; this arithmetic only sizes the reservation.
    """
    components = {
        "compressed_staged_bytes": _require_non_negative("compressed", compressed_staged_bytes),
        "decompressed_arrow_bytes": _require_non_negative("decompressed", decompressed_upper_bytes),
        "range_buffer_bytes": _require_non_negative("range_buffer", range_buffer_bytes),
        "parser_bytes": _require_non_negative("parser", parser_bytes),
        "output_retained_bytes": _require_non_negative("output", output_retained_bytes),
        "duplicate_temp_bytes": _require_non_negative("output", output_retained_bytes)
        * max(0, _require_non_negative("copies", duplicate_temp_copies)),
    }
    total = sum(components.values())
    return {
        "components": components,
        "reservation_bytes": total,
        "cap_bytes": cap_bytes,
        "fits": total <= cap_bytes,
        "note": "reservation formula, not measured process-tree RSS",
    }


def disk_schedule(
    *,
    adopted_artifact_bytes: int,
    per_file_stages: Sequence[Mapping[str, int]],
    review_package_bytes: int,
    log_bytes: int,
    scratch_cap: int = 520093696,
    final_cap: int = 16777216,
    combined_cap: int = 536870912,
) -> dict[str, Any]:
    """Worst staged high-water schedule, one file at a time.

    Each file stage lists scratch bytes held (compressed partials, decoder
    materialization, caches, atomic tmp copies) and final bytes produced.
    High water is the maximum combined occupancy across the ordered
    stages; adopted artifacts persist throughout.
    """
    high_water = adopted_artifact_bytes
    peak = high_water
    stages: list[dict[str, Any]] = []
    for stage in per_file_stages:
        scratch = _require_non_negative("scratch", int(stage.get("scratch", 0)))
        final = _require_non_negative("final", int(stage.get("final", 0)))
        high_water = adopted_artifact_bytes + scratch + final
        peak = max(peak, high_water)
        stages.append(
            {
                "file": stage.get("file"),
                "scratch": scratch,
                "final": final,
                "combined": high_water,
            }
        )
    peak = max(peak, adopted_artifact_bytes + review_package_bytes + log_bytes)
    final_total = adopted_artifact_bytes + review_package_bytes + log_bytes
    return {
        "stages": stages,
        "adopted_artifact_bytes": adopted_artifact_bytes,
        "review_package_bytes": review_package_bytes,
        "log_bytes": log_bytes,
        "high_water_bytes": peak,
        "final_bytes": final_total,
        "fits": {
            "scratch": peak - final_total <= scratch_cap,
            "final": final_total <= final_cap,
            "combined": peak <= combined_cap,
        },
    }


def deadlines(
    *,
    elapsed_seconds: float,
    arm_cap: float = 1800.0,
    file_cap: float = 600.0,
    per_request_cap: float = 30.0,
) -> dict[str, Any]:
    """Monotonic deadline state; earliest deadline wins, no zero placeholder."""
    if not elapsed_seconds >= 0:
        raise ReservationError("elapsed time must be non-negative")
    return {
        "elapsed_seconds": elapsed_seconds,
        "arm_remaining": arm_cap - elapsed_seconds,
        "file_remaining": file_cap,
        "per_request": per_request_cap,
        "exhausted": elapsed_seconds >= arm_cap,
        "note": "elapsed_seconds=0 means unmeasured, never instant readiness",
    }


def output_retained_upper(selected_rows: int, per_document_cap: int = 65536) -> int:
    """Arithmetic retained-text maximum for one file (not measured text)."""
    return _require_non_negative("selected_rows", selected_rows) * _require_non_negative(
        "per_document_cap", per_document_cap
    )


def supervisor_guard(
    *,
    reservation_bytes: int,
    measured_rss_bytes: int | None,
    cap_bytes: int = 268435456,
) -> dict[str, Any]:
    """Supervised enforcement design: refuse unless reservation fits AND the
    supervisor can observe RSS; a missing measurement refuses, never passes."""
    if reservation_bytes > cap_bytes:
        return {"allowed": False, "reason": "reservation exceeds the RSS ceiling"}
    if measured_rss_bytes is None:
        return {"allowed": False, "reason": "no supervised RSS measurement available"}
    if measured_rss_bytes > cap_bytes:
        return {"allowed": False, "reason": "measured RSS exceeds the ceiling"}
    return {"allowed": True, "reason": "reservation fits and RSS is supervised"}


def v21_caps() -> dict[str, int]:
    """Frozen ceilings relevant to reservations (unchanged by v2.1)."""
    limits = frozen.ARM_T_LIMITS
    return {
        "memory_resident_bytes": limits["memory_resident_bytes"],
        "parser_bytes_max": limits["parser_bytes_max"],
        "disk_scratch_bytes": limits["disk_scratch_bytes"],
        "disk_final_bytes": limits["disk_final_bytes"],
        "disk_combined_bytes": limits["disk_combined_bytes"],
        "document_bytes_max": limits["document_bytes_max"],
        "retained_text_bytes_max": limits["retained_text_bytes_max"],
        "final_artifact_bytes_max": limits["final_artifact_bytes_max"],
        "time_seconds_arm": limits["time_seconds_arm"],
        "time_seconds_file": limits["time_seconds_file"],
        "time_seconds_request": limits["time_seconds_request"],
    }
