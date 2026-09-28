"""Offline v2.1 child artifacts: M blocked plan, T dry execution plan.

Neither artifact authorizes execution. The M plan is emitted ONLY as a
blocked-plan receipt when historical accounting cannot reconcile under
the unchanged caps. The T plan is a dry offline construction binding
freeze, selection, cost map, carry-in, range schedule, chunks, locators,
sparse membership, and reservations; it carries no authorization and is
not executable without one. Parents are referenced by hash with their
original roles and statuses, never relabeled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class ChildPlanError(ValueError):
    """Refusal to emit an executable-looking plan without authorization."""


def _base(
    kind: str,
    *,
    parents: Mapping[str, Mapping[str, Any]],
    command: str,
    exit_status: int,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "protocol_version": frozen.V21_PROTOCOL_VERSION,
        "scientific_identity_namespace": frozen.V21_SCIENTIFIC_NAMESPACE,
        "freeze_digest": frozen.V21_FREEZE_DIGEST,
        "selection_digest": frozen.V21_SELECTION_DIGEST,
        "repository": frozen.REPOSITORY,
        "revision": frozen.REVISION,
        "policy_digest": frozen.POLICY_DIGEST,
        "parents": {name: dict(record) for name, record in parents.items()},
        "executable": False,
        "authorization": "NONE",
        "command": command,
        "exit_status": exit_status,
    }


def build_m_blocked_plan(
    *,
    audit: Mapping[str, Any],
    m_windows: Sequence[Mapping[str, Any]],
    command: str,
) -> dict[str, Any]:
    """Blocked M execution child plan: explains exactly why, emits no plan.

    ``audit`` is the m_audit verdict (conclusion A or C with evidence);
    ``m_windows`` carries the adopted 512-row windows for reference only.
    """
    conclusion = str(audit.get("conclusion", ""))
    if conclusion not in ("A", "C"):
        raise ChildPlanError("M blocked-plan receipts require a BLOCKED audit conclusion")
    body = _base(
        "essential_web_evidence_v2_1_arm_m_blocked_plan",
        parents={},
        command=command,
        exit_status=1,
    )
    body.update(
        {
            "audit_conclusion": conclusion,
            "audit_evidence": audit.get("evidence", {}),
            "adopted_windows": list(m_windows),
            "blocked_reason": (
                "cumulative first-file planning usage exceeds the unchanged 10-request/file cap"
                if conclusion == "A"
                else audit.get("reason", "")
            ),
            "status": "BLOCKED",
        }
    )
    return body


def build_t_child_plan(
    *,
    selection_digest: str,
    total_selected: int,
    wanted_by_file: Mapping[str, Sequence[int]],
    schedule: Mapping[str, Any],
    remaining: Mapping[str, Any],
    reservations: Mapping[str, Any],
    carry_digest: str,
    costmap_digest: str,
    command: str,
) -> dict[str, Any]:
    """Dry offline T execution child plan (no authorization to execute)."""
    if selection_digest != frozen.V21_SELECTION_DIGEST:
        raise ChildPlanError("T child plan binds only the frozen 118 selection")
    if int(total_selected) != 118:
        raise ChildPlanError("T child plan binds exactly 118 locators")
    body = _base(
        "essential_web_evidence_v2_1_arm_t_child_plan_dry",
        parents={},
        command=command,
        exit_status=0,
    )
    body.update(
        {
            "selection_total": 118,
            "wanted_by_file": {name: list(rows) for name, rows in sorted(wanted_by_file.items())},
            "range_schedule": schedule,
            "remaining_budgets": remaining,
            "reservations": reservations,
            "carry_reconciliation_digest": carry_digest,
            "costmap_digest": costmap_digest,
            "sparse_membership": "whole-chunk decode with exact-locator retention",
            "status": "DRY_NOT_AUTHORIZED",
        }
    )
    return body


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the canonical self-digest."""
    sealed = dict(body)
    sealed["digest"] = canonical.self_digest(body)
    return sealed
