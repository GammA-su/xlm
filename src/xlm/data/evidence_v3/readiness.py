"""Derived Phase-P readiness: every item computed from verified state.

Astra found readiness booleans assigned directly by the builder. Here a
readiness item is true only after its evidence check passes. Each check is
a small callable returning ``(ok, evidence)``; the builder supplies checks
bound to the actual frozen artifacts, regenerated plans, and live
mechanism self-tests. ``authorization`` remains ``NONE`` until the
independent review passes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

Check = Callable[[], tuple[bool, str]]

DERIVED_ITEMS = (
    "scientific_identity_ok",
    "lineage_closed_v2_ok",
    "v3_epoch_semantics_ok",
    "planning_adoption_ok",
    "phase_p_schedule_ok",
    "phase_d_schedule_ok",
    "prospective_requests_ok",
    "prospective_bytes_ok",
    "decompression_ok",
    "scan_ok",
    "memory_supervision_ok",
    "disk_schedule_ok",
    "runtime_accounting_ok",
)


def derive_arm_readiness(checks: Mapping[str, Check]) -> dict[str, Any]:
    """Evaluate every derived readiness item from its evidence check."""
    missing = sorted(set(DERIVED_ITEMS) - set(checks))
    if missing:
        raise ValueError(f"readiness is missing derived checks: {missing}")
    unknown = sorted(set(checks) - set(DERIVED_ITEMS))
    if unknown:
        raise ValueError(f"readiness has unknown checks: {unknown}")
    items: dict[str, Any] = {}
    evidence: dict[str, str] = {}
    failed: list[str] = []
    for name in DERIVED_ITEMS:
        ok, note = checks[name]()
        if not isinstance(ok, bool):
            raise ValueError(f"readiness check {name} did not return a boolean")
        items[name] = ok
        evidence[name] = note
        if not ok:
            failed.append(name)
    items["authorization"] = "NONE"
    verdict = "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW" if not failed else "BLOCKED"
    return {"items": items, "evidence": evidence, "failed": failed, "verdict": verdict}
