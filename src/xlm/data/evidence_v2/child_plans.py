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
    protocol_version: str = frozen.V21_PROTOCOL_VERSION,
    freeze_digest: str = frozen.V21_FREEZE_DIGEST,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "protocol_version": protocol_version,
        "scientific_identity_namespace": frozen.V21_SCIENTIFIC_NAMESPACE,
        "freeze_digest": freeze_digest,
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
    parents: Mapping[str, Mapping[str, Any]] | None = None,
    disk_schedule: Mapping[str, Any] | None = None,
    memory_schedule: Mapping[str, Any] | None = None,
    deadline_schedule: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
    receipt_version: str = "essential-web-evidence-v2.2",
    protocol_version: str = frozen.V21_PROTOCOL_VERSION,
    freeze_digest: str = frozen.V21_FREEZE_DIGEST,
) -> dict[str, Any]:
    """Dry offline T execution child plan (no authorization to execute)."""
    if selection_digest != frozen.V21_SELECTION_DIGEST:
        raise ChildPlanError("T child plan binds only the frozen 118 selection")
    if int(total_selected) != 118:
        raise ChildPlanError("T child plan binds exactly 118 locators")
    body = _base(
        "essential_web_evidence_v2_1_arm_t_child_plan_dry",
        parents=dict(parents) if parents is not None else {},
        command=command,
        exit_status=0,
        protocol_version=protocol_version,
        freeze_digest=freeze_digest,
    )
    body.update(
        {
            "receipt_version": receipt_version,
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
    if disk_schedule is not None:
        body["disk_schedule"] = dict(disk_schedule)
    if memory_schedule is not None:
        body["memory_schedule"] = dict(memory_schedule)
    if deadline_schedule is not None:
        body["deadline_schedule"] = dict(deadline_schedule)
    if readiness is not None:
        body["readiness"] = dict(readiness)
    return body


def readiness_review(
    arm: str,
    checks: Mapping[str, bool],
    *,
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Per-arm readiness vector: every non-authorization item must be true.

    Only an all-true vector (with authorization separately NONE) may be
    READY_FOR_ACQUISITION_AUTHORIZATION_REVIEW; otherwise BLOCKED. Never
    executable, never an acquisition command.
    """
    items = dict(checks)
    failed = sorted(name for name, ok in items.items() if not ok)
    return {
        "arm": arm,
        "items": items,
        "failed": failed,
        "extra_reasons": list(reasons),
        "authorization": "NONE",
        "executable": False,
        "status": "READY_FOR_ACQUISITION_AUTHORIZATION_REVIEW"
        if not failed and not reasons
        else "BLOCKED",
    }


def seal(body: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the canonical self-digest."""
    sealed = dict(body)
    sealed["digest"] = canonical.self_digest(body)
    return sealed


def build_v22_bundle(
    *,
    freeze22: Mapping[str, Any],
    parents: Mapping[str, Mapping[str, Any]],
    m_audit_verdict: Mapping[str, Any],
    m_windows: Sequence[Mapping[str, Any]],
    m_data_schedule: Mapping[str, Any],
    m_revalidation: Mapping[str, Any],
    t_schedule: Mapping[str, Any],
    t_remaining: Mapping[str, Mapping[str, Any]],
    t_physical: Mapping[str, Any],
    t_reservations: Mapping[str, Any],
    carry_digest: str,
    costmap_digest: str,
    selection_digest: str,
    total_selected: int,
    wanted_by_file: Mapping[str, Sequence[int]],
    command: str,
    m_readiness_items: Mapping[str, bool] | None = None,
    m_readiness_reasons: Sequence[str] = (),
    t_readiness_items: Mapping[str, bool] | None = None,
    t_readiness_reasons: Sequence[str] = (),
    t_disk: Mapping[str, Any] | None = None,
    t_memory: Mapping[str, Any] | None = None,
    t_deadlines: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Assemble the nine v2.2 offline child artifacts (all DRY, none authorized).

    Every artifact binds complete parent descriptors; readiness vectors
    are computed mechanically (all-true or BLOCKED); nothing here is
    executable and no acquisition command is produced. Readiness inputs
    are caller-supplied evidence verdicts; defaults preserve the audited
    real-arm vectors.
    """
    if freeze22.get("digest") != frozen.V22_FREEZE_DIGEST:
        raise ChildPlanError("v2.2 bundle binds only the frozen v2.2 digest")
    base_parents = dict(parents)
    m_blocked = seal(
        build_m_blocked_plan(audit=m_audit_verdict, m_windows=m_windows, command=command)
    )
    m_blocked["parents"] = base_parents
    m_blocked["protocol_version"] = frozen.V22_PROTOCOL_VERSION
    m_blocked["freeze_digest"] = frozen.V22_FREEZE_DIGEST
    m_blocked["digest"] = canonical.self_digest(
        {k: v for k, v in m_blocked.items() if k != "digest"}
    )
    t_child = seal(
        build_t_child_plan(
            selection_digest=selection_digest,
            total_selected=total_selected,
            wanted_by_file=wanted_by_file,
            schedule=t_schedule,
            remaining=t_remaining,
            reservations=t_reservations,
            carry_digest=carry_digest,
            costmap_digest=costmap_digest,
            command=command,
            parents=base_parents,
            disk_schedule=t_disk,
            memory_schedule=t_memory,
            deadline_schedule=t_deadlines,
            protocol_version=frozen.V22_PROTOCOL_VERSION,
            freeze_digest=frozen.V22_FREEZE_DIGEST,
        )
    )
    readiness_m = readiness_review(
        "M",
        dict(m_readiness_items)
        if m_readiness_items is not None
        else {
            "scientific_identity_ok": True,
            "lineage_ok": True,
            "historical_accounting_ok": False,
            "range_schedule_ok": True,
            "requests_ok": False,
            "response_bytes_ok": False,
            "decompression_ok": True,
            "scan_ok": True,
            "memory_supervision_ok": False,
            "disk_schedule_ok": False,
            "deadlines_ok": False,
        },
        reasons=list(m_readiness_reasons)
        or [
            "cumulative 12 > 10 first-file planning usage (conclusion A)",
            "historical redirect/error bodies unmeasured (no sound bound)",
            "historical durations unmeasured",
            "no supervised live enforcement has run",
        ],
    )
    readiness_t = readiness_review(
        "T",
        dict(t_readiness_items)
        if t_readiness_items is not None
        else {
            "scientific_identity_ok": True,
            "lineage_ok": True,
            "historical_accounting_ok": False,
            "range_schedule_ok": True,
            "requests_ok": True,
            "response_bytes_ok": False,
            "decompression_ok": True,
            "scan_ok": True,
            "memory_supervision_ok": False,
            "disk_schedule_ok": False,
            "deadlines_ok": False,
        },
        reasons=list(t_readiness_reasons)
        or [
            "historical redirect/error bodies unmeasured (bounded gaps only)",
            "historical durations unmeasured",
            "final-disk fit conditional on measured labeling volume",
            "no supervised live enforcement has run",
        ],
    )
    return {
        "arm_m_child_plan.json": m_blocked,
        "arm_t_child_plan.json": t_child,
        "readiness_review.json": seal(
            {
                "kind": "essential_web_evidence_v2_2_readiness_review",
                "protocol_version": frozen.V22_PROTOCOL_VERSION,
                "freeze_digest": frozen.V22_FREEZE_DIGEST,
                "parents": base_parents,
                "arms": {"M": readiness_m, "T": readiness_t},
                "command": command,
                "exit_status": 0,
            }
        ),
        "ledger_reconciliation.json": seal(
            {
                "kind": "essential_web_evidence_v2_2_ledger_reconciliation",
                "protocol_version": frozen.V22_PROTOCOL_VERSION,
                "freeze_digest": frozen.V22_FREEZE_DIGEST,
                "parents": base_parents,
                "carry_digest": carry_digest,
                "command": command,
                "exit_status": 0,
            }
        ),
        "disk_schedule.json": seal(dict(t_disk or {})),
        "memory_schedule.json": seal(dict(t_reservations)),
        "deadline_schedule.json": seal(dict(t_deadlines or {})),
        "range_schedule_m.json": seal(
            dict(
                m_data_schedule,
                **{
                    "protocol_version": frozen.V22_PROTOCOL_VERSION,
                    "revalidation": m_revalidation,
                },
            )
        ),
        "range_schedule_t.json": seal(
            dict(t_schedule, **{"protocol_version": frozen.V22_PROTOCOL_VERSION})
        ),
    }
