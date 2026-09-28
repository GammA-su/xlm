"""Arm-M historical resource-compliance audit (offline, receipts only).

Determines exactly one of:
  A. cumulative first-file planning usage provably exceeds the unchanged
     10-request/file cap -> resource compliance BLOCKED.
  B. some events are duplicate references to the SAME physical requests
     and reconcile without double counting (identity proven, capped).
  C. historical evidence is insufficient -> BLOCKED on unknown usage.

Inputs are the old incomplete receipt, the complete receipt, and the
frozen cap. No network, no rerun, no cap change. Code-version markers
(pre-fix receipts lack per-range hop detail; fixed receipts record it)
distinguish the two planning generations; no resume mechanism exists in
the footer transport, so a later pass showing full per-file discovery
counts proves re-fetch, not reuse.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import frozen

M_FILE_CAP = 10
_HOP_RE = re.compile(r"redirect hop (\d+)")


class AuditError(ValueError):
    """Refusal to determine compliance without sufficient evidence."""


def _hop_count_from_reason(reason: str) -> int | None:
    """Parse the refused hop number; None when the reason carries none."""
    match = _HOP_RE.search(reason or "")
    if match is None:
        return None
    return int(match.group(1))


def old_attempt_file_physical(
    old_receipt: Mapping[str, Any],
    target_file: str,
    *,
    earlier_files: Sequence[str] = (),
) -> dict[str, Any]:
    """Physical requests for one file in the pre-fix incomplete attempt.

    Measured: successful logical ranges (unit counter) plus the failed
    range (only when the failure belongs to this file). Proven follows:
    the hop-N refusal proves N redirect_request calls arm-wide; all but
    the refused one completed inside earlier ranges. When earlier files
    precede the target, those follows are unattributable and this raises
    instead of guessing (the caller then reports C for that file).
    """
    units = [u for u in old_receipt.get("completed_units", []) if u.get("file") == target_file]
    if len(units) != 1:
        raise AuditError(f"old receipt has no unique completed unit for {target_file}")
    if earlier_files:
        raise AuditError(
            f"completed follows before {target_file} are unattributable "
            f"across {list(earlier_files)}: refusing to guess"
        )
    unit = units[0]
    budget = old_receipt.get("budget", {})
    if not isinstance(unit.get("footer_requests_used"), int):
        raise AuditError("old unit lacks a measured range count")
    if budget.get("failed_requests", 0) < 1:
        raise AuditError("old budget lacks the measured failed attempt")
    hops = _hop_count_from_reason(str(old_receipt.get("reason", "")))
    if hops is None:
        raise AuditError("old reason carries no hop count; follows unprovable")
    failed_file = str(old_receipt.get("failed_file", ""))
    own_failure = 1 if failed_file == target_file else 0
    # The refused Nth follow belongs to the failed file's in-flight range,
    # so exactly N-1 follows completed; all of them sit inside earlier
    # ranges, i.e. inside the target file when it precedes the failure.
    # The failed range attempt itself belongs to the failed file only.
    completed_follows = hops - 1
    attempts = int(unit["footer_requests_used"]) + own_failure
    return {
        "measured_range_attempts": int(unit["footer_requests_used"]),
        "measured_failed_attempts": own_failure,
        "proven_redirect_follows": completed_follows,
        "physical_requests": attempts + completed_follows,
        "bytes": int(unit.get("footer_bytes_used", 0)),
    }


def complete_file_physical(complete_receipt: Mapping[str, Any], target_file: str) -> dict[str, Any]:
    """Physical requests for one file in the fixed complete pass.

    Requires per-range hop detail (absent pre-fix); refuses to guess.
    """
    units = [u for u in complete_receipt.get("units", []) if u.get("file") == target_file]
    if len(units) != 1:
        raise AuditError(f"complete receipt has no unique unit for {target_file}")
    unit = units[0]
    ranges = unit.get("footer_ranges")
    if not isinstance(ranges, list) or not ranges:
        raise AuditError(f"complete unit for {target_file} lacks per-range detail")
    attempts = len(ranges)
    follows = 0
    for entry in ranges:
        if not isinstance(entry, dict) or type(entry.get("hops")) is not int:
            raise AuditError("complete per-range hop detail malformed")
        follows += entry["hops"]
    if unit.get("footer_requests_used") != attempts + follows:
        raise AuditError("complete per-file request counter disagrees with ranges")
    return {
        "measured_range_attempts": attempts,
        "proven_redirect_follows": follows,
        "physical_requests": attempts + follows,
        "bytes": int(unit.get("footer_bytes_used", 0)),
    }


def audit_m_file_compliance(
    old_receipt: Mapping[str, Any],
    complete_receipt: Mapping[str, Any],
    target_file: str,
    *,
    shared_event_ids: bool = False,
) -> dict[str, Any]:
    """Determine A, B, or C for one file's cumulative planning usage.

    ``shared_event_ids`` models a claimed reuse: only when the caller
    supplies proven shared physical-event identities (never true across
    these separate processes) may duplicates reconcile under B.
    """
    old = old_attempt_file_physical(old_receipt, target_file)
    new = complete_file_physical(complete_receipt, target_file)
    if shared_event_ids:
        raise AuditError(
            "shared physical-event identities across separate planning "
            "processes were claimed but cannot exist: no resume mechanism "
            "carries bytes or redirect resolutions between runs"
        )
    cumulative = old["physical_requests"] + new["physical_requests"]
    evidence = {
        "file": target_file,
        "cap": M_FILE_CAP,
        "old_attempt": old,
        "complete_pass": new,
        "cumulative_physical_requests": cumulative,
        "distinct_runs": True,
        "resume_mechanism": False,
    }
    if cumulative > M_FILE_CAP:
        return {"conclusion": "A", "blocked": True, "evidence": evidence}
    return {"conclusion": "B_COMPLIANT", "blocked": False, "evidence": evidence}


def audit_unknown_history(known: bool) -> dict[str, Any]:
    """Unknown usage is never zero: an explicit unknown blocks (C)."""
    if not known:
        return {
            "conclusion": "C",
            "blocked": True,
            "evidence": {"unknown_usage": "unbounded: cannot determine the true count"},
        }
    return {"conclusion": "SUFFICIENT", "blocked": False, "evidence": {}}


def m_file_cap() -> int:
    """The unchanged Arm-M per-file footer planning cap."""
    if frozen.ARM_M_LIMITS["footer_requests_per_file"] != M_FILE_CAP:
        raise AuditError("frozen M per-file cap drifted")
    return M_FILE_CAP


# --------------------------------------------------------------------------
# Historical response-body audit: is there a mechanically justified bound?
# --------------------------------------------------------------------------

# Code-archaeology facts (verified offline via git object hashes):
# - At 943b816 (old attempt) and at every later revision through HEAD,
#   redirect/error response bodies pass through
#   SafeRedirectHandler -> TransportBudget.read_body(fp, budget.max_bytes),
#   with max_bytes = the 268,435,456-byte arm total. No 4 MiB bound ever
#   applied to redirect bodies; the closure-level 4 MiB check covers only
#   planned range bodies.
# - Consumed redirect bodies accumulated in TransportBudget.bytes_transferred
#   (in-memory only) and were never exported into arm receipts at either
#   generation. Per-response Content-Length gating existed but no lengths
#   were recorded, so no tighter bound is derivable from the receipts.
OLD_TRANSPORT_REVISION = "943b816"
OLD_REDIRECT_BODY_LIMIT = 268435456


def audit_m_bytes_unknown() -> dict[str, Any]:
    """Historical redirect/error body bytes: no sound bound -> BLOCKED.

    Recorded range-body bytes are exact (2,191,448 arm-wide); redirect and
    error response bodies are unmeasured at both code generations, and the
    only mechanically enforced bound (256 MiB per body) is vacuous. An
    analyst small estimate is insufficient, so actual all-body bytes per
    file and arm remain unknown and block acquisition.
    """
    if frozen.V22_M_HISTORY_BYTES_TOTAL != sum(frozen.V22_M_HISTORY_BYTES.values()):
        raise AuditError("frozen M byte history does not sum")
    return {
        "conclusion": "BLOCKED_BYTES_UNKNOWN",
        "blocked": True,
        "evidence": {
            "recorded_range_body_bytes_total": frozen.V22_M_HISTORY_BYTES_TOTAL,
            "redirect_error_bodies": "unmeasured at both generations",
            "enforced_redirect_body_limit": OLD_REDIRECT_BODY_LIMIT,
            "enforced_limit_source": (
                f"{OLD_TRANSPORT_REVISION}:transport.read_body(budget.max_bytes)"
            ),
            "four_mib_cap_applies_to_redirects": False,
            "reason": (
                "no exact historical redirect/error-body measurements and no "
                "sufficiently tight mechanically enforced bound exist in the "
                "supplied artifacts"
            ),
        },
    }
