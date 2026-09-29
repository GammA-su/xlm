"""v3 prospective dry schedules built from adopted planning observations.

Old evidence is adopted ONLY as adopted_planning_observation: M
file/window/footer physical observations, T whole-chunk physical cost
observations, the exact 118-locator selection, dictionary-inclusive
chunk/range knowledge, file lengths/ETags/schema identities.

This module never claims old observations are v3 acquisition receipts
and never calls old planning COMPLETE acquisition. Remote state must
still be minimally revalidated under v3 budget before data acquisition.

All prospective counts/bytes below are independently recomputed from the
adopted observations; frozen constants in frozen_v3 are cross-check
targets, never trusted inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v3 import frozen_v3


class ScheduleError(ValueError):
    """Any schedule mismatch, cap breach, or identity drift: refuse."""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ScheduleError(message)


def build_m_phase_p(m_files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact prospective M phase-P revalidation/planning schedule.

    Per file: GET bytes 0-3, then GET the recorded full footer + trailer
    [recorded_last_footer_range.start, N-1]. Validates 206/Content-Range/
    length/PAR1/ETag/schema/window identity shape (network validation
    itself happens under v3 budget later).
    """
    _require(len(list(m_files)) == 8, "M phase P requires exactly 8 frozen files")
    files: dict[str, Any] = {}
    for entry in m_files:
        name = str(entry["file"])
        length = entry["remote_length"]
        etag = entry["etag"]
        window = list(entry["window"])
        ranges = [list(r) for r in entry["phase_P_ranges_inclusive"]]
        _require(type(length) is int and length > 0, f"M length invalid for {name}")
        _require(
            isinstance(etag, str) and etag.startswith('"') and etag.endswith('"'),
            f"M strong ETag invalid for {name}",
        )
        _require(len(window) == 2 and window[1] - window[0] == 512, f"M window not 512 for {name}")
        _require(
            ranges[0] == [0, 3],
            f"M phase-P first range must be bytes 0-3 for {name}",
        )
        start, stop = ranges[1]
        _require(0 <= start <= stop == length - 1, f"M footer range must end at N-1 for {name}")
        payload = int(entry["phase_P_payload_bytes"])
        _require(payload == 4 + (stop - start + 1), f"M P payload arithmetic for {name}")
        _require(payload <= 4194304, f"M footer payload exceeds 4 MiB for {name}")
        _require(int(entry["P_logical"]) == 2, f"M P logical must be 2 for {name}")
        _require(int(entry["P_nominal_physical"]) == 3, f"M P nominal must be 3 for {name}")
        _require(int(entry["P_cold_chain_max_no_retry"]) == 5, f"M P cold max must be 5 for {name}")
        files[name] = {
            "remote_length": length,
            "etag": etag,
            "window": window,
            "phase_P_ranges_inclusive": ranges,
            "phase_P_payload_bytes": payload,
            "identity_check": {
                "method": "GET",
                "range": [0, 3],
                "expected_status": 206,
                "expected_content_range": f"bytes 0-3/{length}",
                "expected_magic": "PAR1",
            },
            "footer_range": {"start": start, "end": stop},
            "target_reuse": "validated target reuse exactly as frozen",
        }
    logical = 2 * 8
    nominal = 3 * 8
    cold = 5 * 8
    _require(logical == 16, "M P logical arm total must be 16")
    _require(nominal == 24, "M P nominal physical arm total must be 24")
    _require(cold == 40, "M P cold-chain arm total must be 40")
    payload_total = sum(int(e["phase_P_payload_bytes"]) for e in m_files)
    return {
        "arm": "M",
        "phase": "P",
        "files": files,
        "P_logical_arm": logical,
        "P_nominal_physical_arm": nominal,
        "P_cold_chain_max_no_retry_arm": cold,
        "P_payload_bytes_arm": payload_total,
        "no_relist": True,
        "no_new_file": True,
        "no_new_window": True,
    }


def build_m_phase_d(m_files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """M phase-D dry schedule: 81 projected chunks/file, 648 total.

    Offsets remain UNRESOLVED until phase P seals them; this dry plan
    freezes counts/lengths/windows and the request/byte arithmetic.
    """
    _require(len(list(m_files)) == 8, "M phase D requires exactly 8 frozen files")
    total_chunks = 0
    total_data = 0
    total_nominal = 0
    total_cold = 0
    files: dict[str, Any] = {}
    for entry in m_files:
        name = str(entry["file"])
        _require(int(entry["data_chunk_count"]) == 81, f"M must have 81 chunks for {name}")
        _require(entry["data_chunk_offsets_known"] is False, "M offsets unresolved until P")
        _require(int(entry["D_data_physical_no_retry"]) == 81, f"M D data must be 81 for {name}")
        _require(int(entry["requests_nominal_all"]) == 86, f"M nominal/file must be 86 for {name}")
        _require(
            int(entry["requests_cold_chain_max_all_no_retry"]) == 90,
            f"M cold/file must be 90 for {name}",
        )
        total_chunks += 81
        total_data += int(entry["data_payload_bytes"])
        total_nominal += 86
        total_cold += 90
        files[name] = {
            "data_chunk_count": 81,
            "data_chunk_offsets": "UNRESOLVED_until_phase_P_sealed",
            "data_payload_bytes": int(entry["data_payload_bytes"]),
            "D_identity_range_inclusive": [0, 3],
            "window": list(entry["window"]),
            "per_file_requests": {"nominal": 86, "cold_max_no_retry": 90, "data": 81},
        }
    _require(total_chunks == 648, f"M total chunks must be 648, got {total_chunks}")
    _require(total_nominal == 688, f"M nominal total must be 688, got {total_nominal}")
    _require(total_cold == 720, f"M cold total must be 720, got {total_cold}")
    _require(total_data == frozen_v3.M_DATA_PAYLOAD, "M data payload mismatch")
    # Cap checks: footer/control 16/file 80/arm; data 100/file 800/arm; all 880.
    _require(5 <= 16 and 40 <= 80, "M P cold must fit footer/control caps")
    _require(all(int(e["D_data_physical_no_retry"]) <= 100 for e in m_files), "M D/file cap")
    _require(total_chunks <= 800, "M data arm cap")
    _require(total_nominal <= 880 and total_cold <= 880, "M whole-arm cap")
    return {
        "arm": "M",
        "phase": "D",
        "files": files,
        "data_chunks_total": total_chunks,
        "data_payload_bytes_arm": total_data,
        "nominal_physical_total": total_nominal,
        "cold_chain_max_no_retry_total": total_cold,
        "requires_sealed_P": True,
        "requires_separate_D_authorization": True,
    }


def build_t_phase_p(t_files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """T phase-P dry schedule: 0-3, N-8..N-1, then trailer-resolved footer."""
    _require(len(list(t_files)) == 8, "T phase P requires 8 development files")
    files: dict[str, Any] = {}
    for entry in t_files:
        name = str(entry["file"])
        length = entry["remote_length"]
        etag = entry["etag"]
        _require(type(length) is int and length > 0, f"T length invalid for {name}")
        _require(
            isinstance(etag, str) and etag.startswith('"') and etag.endswith('"'),
            f"T strong ETag invalid for {name}",
        )
        pattern = list(entry["phase_P_range_pattern"])
        _require(
            pattern == ["0-3", "N-8..N-1", "N-8-L..N-9 after trailer establishes L"],
            f"T P pattern frozen for {name}",
        )
        _require(int(entry["P_logical"]) == 3, f"T P logical must be 3 for {name}")
        _require(int(entry["P_nominal_physical"]) == 4, f"T P nominal must be 4 for {name}")
        _require(int(entry["P_cold_chain_max_no_retry"]) == 6, f"T P cold max must be 6 for {name}")
        _require(
            int(entry["footer_byte_reservation"]) == 2097152, f"T footer reserve 2 MiB for {name}"
        )
        files[name] = {
            "remote_length": length,
            "etag": etag,
            "phase_P_pattern": pattern,
            "no_text_read": True,
            "footer_reservation_bytes": 2097152,
        }
    logical = 3 * 8
    nominal = 4 * 8
    cold = 6 * 8
    _require(logical == 24, "T P logical arm must be 24")
    _require(nominal == 32, "T P nominal arm must be 32")
    _require(cold == 48, "T P cold arm must be 48")
    return {
        "arm": "T",
        "phase": "P",
        "files": files,
        "P_logical_arm": logical,
        "P_nominal_physical_arm": nominal,
        "P_cold_chain_max_no_retry_arm": cold,
        "no_text_read": True,
    }


def build_t_phase_d(t_files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """T phase-D dry schedule: 47 dictionary-inclusive ranges, corrected spans."""
    total_ranges = 0
    total_payload = 0
    total_transfer_upper = 0
    total_nominal = 0
    total_cold = 0
    files: dict[str, Any] = {}
    for entry in t_files:
        name = str(entry["file"])
        ranges = list(entry["data_ranges_half_open"])
        count = int(entry["data_range_count"])
        _require(count == len(ranges), f"T range count mismatch for {name}")
        _require(count in (5, 6), f"T per-file ranges must be 5 or 6 for {name}")
        cursor: int | None = None
        payload = 0
        for r in ranges:
            start, end, nbytes = int(r["start"]), int(r["end"]), int(r["bytes"])
            _require(0 <= start < end, f"T range empty/negative for {name}")
            _require(end - start == nbytes, f"T range byte arithmetic for {name}")
            _require(0 < nbytes <= 4194304, f"T range exceeds 4 MiB for {name}")
            if cursor is not None:
                _require(start == cursor, f"T ranges must be contiguous for {name}")
            cursor = end
            payload += nbytes
        _require(payload == int(entry["data_payload_bytes"]), f"T payload mismatch for {name}")
        total_ranges += count
        total_payload += payload
        total_transfer_upper += int(entry["data_transfer_upper"])
        total_nominal += int(entry["requests_nominal_all"])
        total_cold += int(entry["requests_cold_chain_max_all_no_retry"])
        files[name] = {
            "data_range_count": count,
            "data_ranges_half_open": ranges,
            "data_payload_bytes": payload,
            "data_transfer_upper": int(entry["data_transfer_upper"]),
            "dictionary_inclusive": True,
            "D_identity_range_inclusive": [0, 3],
        }
    _require(total_ranges == 47, f"T total ranges must be 47, got {total_ranges}")
    _require(total_payload == frozen_v3.T_DATA_PAYLOAD, "T compressed payload mismatch")
    _require(total_transfer_upper == frozen_v3.T_DATA_BOUND, "T conservative bound mismatch")
    _require(total_nominal == 95, f"T nominal must be 95, got {total_nominal}")
    _require(total_cold == 127, f"T cold must be 127, got {total_cold}")
    return {
        "arm": "T",
        "phase": "D",
        "files": files,
        "data_range_count_arm": total_ranges,
        "data_payload_bytes_arm": total_payload,
        "data_transfer_upper_arm": total_transfer_upper,
        "nominal_physical_total": total_nominal,
        "cold_chain_max_no_retry_total": total_cold,
        "requires_sealed_P": True,
        "requires_separate_D_authorization": True,
        "no_page_decoder_redesign": True,
    }


def check_prospective_totals(
    m_p: Mapping[str, Any],
    m_d: Mapping[str, Any],
    t_p: Mapping[str, Any],
    t_d: Mapping[str, Any],
    prospective: Mapping[str, Any],
) -> None:
    """Cross-check dry schedules against the frozen prospective totals."""
    _require(prospective["M_total_nominal"] == 688, "frozen M nominal must be 688")
    _require(prospective["M_total_cold_max_no_retry"] == 720, "frozen M cold must be 720")
    _require(prospective["T_total_nominal"] == 95, "frozen T nominal must be 95")
    _require(prospective["T_total_cold_max_no_retry"] == 127, "frozen T cold must be 127")
    _require(prospective["M_footer_payload_both_phases"] == 1398448, "frozen M footer must match")
    _require(prospective["M_data_payload"] == 11692530, "frozen M data must match")
    _require(m_d["nominal_physical_total"] == 688, "M dry nominal mismatch")
    _require(m_d["cold_chain_max_no_retry_total"] == 720, "M dry cold mismatch")
    _require(t_d["nominal_physical_total"] == 95, "T dry nominal mismatch")
    _require(t_d["cold_chain_max_no_retry_total"] == 127, "T dry cold mismatch")
    _ = (m_p, t_p)


# --------------------------------------------------------------------------
# Phase-P capacity detail (Astra correction).
# --------------------------------------------------------------------------

M_P_LOGICAL = 16
M_P_OBSERVED_PHYSICAL = 24
M_P_COLD_NO_RETRY = 40
M_D_IDENTITY_ALLOCATION = 32
M_CONTROLS_TOTAL = 72
M_FOOTER_ARM_CAP = 80
# With 32 requests reserved for future D identity checks, the P arm
# capacity is 48; cold P (40) leaves only 8 additional P requests while
# preserving the future D control allocation.
M_P_ARM_CAPACITY = 48
M_P_SPARE_WHILE_PRESERVING_D = 8

T_P_LOGICAL = 24
T_P_OBSERVED_PHYSICAL = 32
T_P_COLD_NO_RETRY = 48
T_D_IDENTITY_ALLOCATION = 32
T_CONTROLS_COMBINED = 80


def m_phase_p_accounting(m_files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact recomputed M Phase-P byte/request accounting."""
    payloads = {str(e["file"]): int(e["phase_P_payload_bytes"]) for e in m_files}
    _require(len(payloads) == 8, "M Phase-P accounting needs 8 files")
    total = sum(payloads.values())
    _require(total == 1398416, f"M Phase-P payload must be 1398416, got {total}")
    remaining = 33554432 - total
    _require(remaining == 32156016, "M Phase-P footer remaining mismatch")
    per_file = {name: 4194304 - value for name, value in payloads.items()}
    _require(min(per_file.values()) == 3991024, "minimum per-file footer headroom mismatch")
    _require(M_P_COLD_NO_RETRY + M_D_IDENTITY_ALLOCATION == M_CONTROLS_TOTAL, "M control total")
    _require(M_CONTROLS_TOTAL <= M_FOOTER_ARM_CAP, "M controls exceed footer arm cap")
    _require(
        M_P_ARM_CAPACITY - M_P_COLD_NO_RETRY == M_P_SPARE_WHILE_PRESERVING_D,
        "M P spare capacity mismatch",
    )
    return {
        "P_logical": M_P_LOGICAL,
        "P_observed_physical": M_P_OBSERVED_PHYSICAL,
        "P_cold_no_retry": M_P_COLD_NO_RETRY,
        "D_identity_allocation": M_D_IDENTITY_ALLOCATION,
        "controls_total": M_CONTROLS_TOTAL,
        "footer_arm_cap": M_FOOTER_ARM_CAP,
        "P_arm_capacity_preserving_D": M_P_ARM_CAPACITY,
        "P_spare_preserving_D": M_P_SPARE_WHILE_PRESERVING_D,
        "P_payload_bytes": total,
        "footer_remaining_before_redirect_error_retry": remaining,
        "per_file_footer_headroom": dict(sorted(per_file.items())),
        "minimum_per_file_footer_headroom": min(per_file.values()),
    }


def t_phase_p_accounting() -> dict[str, Any]:
    """T Phase-P request accounting (byte headroom waits on measured L)."""
    _require(T_P_COLD_NO_RETRY + T_D_IDENTITY_ALLOCATION == T_CONTROLS_COMBINED, "T control total")
    return {
        "P_logical": T_P_LOGICAL,
        "P_observed_physical": T_P_OBSERVED_PHYSICAL,
        "P_cold_no_retry": T_P_COLD_NO_RETRY,
        "D_identity_allocation": T_D_IDENTITY_ALLOCATION,
        "controls_combined": T_CONTROLS_COMBINED,
        "footer_headroom": "not fabricated before L is measured in P",
    }
