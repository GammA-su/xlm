"""Plan-bound execution authorization with smoke-cap defaults (C13, A29).

Production execution requires an authorization ticket matching the exact plan
hash and covering the plan's budgets. There is no default unlimited flag.
Without a ticket, a run may proceed only inside the default implementation
smoke caps (200,000 valid targets, ten training minutes, one GPU process,
2 GiB of new artifacts); anything larger is blocked with the exceeded cap
named. A ticket that does not cover the plan is refused, never trimmed to fit.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUTHORIZATION_VERSION = "1"

# Default implementation smoke caps from C13. Guardrails, not promises.
SMOKE_MAX_VALID_TARGETS = 200_000
SMOKE_MAX_TRAIN_SECONDS = 600.0
SMOKE_MAX_GPU_PROCESSES = 1
SMOKE_MAX_NEW_DISK_GIB = 2.0


class AuthorizationError(RuntimeError):
    """Raised when execution lacks a matching authorization."""


@dataclass(frozen=True)
class AuthorizationTicket:
    """An operator-issued ticket bound to one plan hash and explicit limits."""

    ticket_version: str
    plan_hash: str
    max_valid_targets: int
    max_train_seconds: float | None
    max_new_disk_gib: float | None
    max_gpu_processes: int
    approver: str
    ticket_id: str
    created_at: str = ""
    smoke_tier: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthorizationTicket:
        return cls(
            ticket_version=str(data.get("ticket_version", AUTHORIZATION_VERSION)),
            plan_hash=str(data["plan_hash"]),
            max_valid_targets=int(data["max_valid_targets"]),
            max_train_seconds=(
                None if data.get("max_train_seconds") is None else float(data["max_train_seconds"])
            ),
            max_new_disk_gib=(
                None if data.get("max_new_disk_gib") is None else float(data["max_new_disk_gib"])
            ),
            max_gpu_processes=int(data.get("max_gpu_processes", 1)),
            approver=str(data.get("approver", "")),
            ticket_id=str(data.get("ticket_id", "")),
            created_at=str(data.get("created_at", "")),
            smoke_tier=bool(data.get("smoke_tier", False)),
        )


def issue_ticket(
    plan_hash: str,
    max_valid_targets: int,
    approver: str,
    ticket_id: str,
    max_train_seconds: float | None = None,
    max_new_disk_gib: float | None = None,
    max_gpu_processes: int = 1,
) -> AuthorizationTicket:
    """Issue an explicit operator ticket. No defaults grant large budgets."""
    if max_valid_targets <= 0:
        raise AuthorizationError("ticket max_valid_targets must be positive")
    if not approver:
        raise AuthorizationError("ticket requires a named approver")
    return AuthorizationTicket(
        ticket_version=AUTHORIZATION_VERSION,
        plan_hash=plan_hash,
        max_valid_targets=max_valid_targets,
        max_train_seconds=max_train_seconds,
        max_new_disk_gib=max_new_disk_gib,
        max_gpu_processes=max_gpu_processes,
        approver=approver,
        ticket_id=ticket_id,
        created_at=datetime.now(UTC).isoformat(),
    )


def smoke_ticket(plan_hash: str) -> AuthorizationTicket:
    """The only authorization that exists by default: the C13 smoke caps."""
    return AuthorizationTicket(
        ticket_version=AUTHORIZATION_VERSION,
        plan_hash=plan_hash,
        max_valid_targets=SMOKE_MAX_VALID_TARGETS,
        max_train_seconds=SMOKE_MAX_TRAIN_SECONDS,
        max_new_disk_gib=SMOKE_MAX_NEW_DISK_GIB,
        max_gpu_processes=SMOKE_MAX_GPU_PROCESSES,
        approver="smoke_cap_policy",
        ticket_id=f"smoke_{plan_hash[:12]}",
        created_at=datetime.now(UTC).isoformat(),
        smoke_tier=True,
    )


def check_ticket(
    ticket: AuthorizationTicket,
    plan_hash: str,
    required_targets: int,
    required_seconds: float | None = None,
    required_disk_gib: float | None = None,
    required_gpu_processes: int = 1,
) -> list[str]:
    """Return the list of authorization violations; empty means covered.

    A mismatched plan hash is always a violation, even when the budgets would
    cover the run: the ticket authorizes one plan, not a spending account.
    """
    violations: list[str] = []
    if ticket.plan_hash != plan_hash:
        violations.append(
            f"ticket '{ticket.ticket_id}' binds plan '{ticket.plan_hash[:12]}', "
            f"not '{plan_hash[:12]}'"
        )
        return violations
    if required_targets > ticket.max_valid_targets:
        violations.append(
            f"run needs {required_targets:,} valid targets but ticket allows "
            f"{ticket.max_valid_targets:,}"
        )
    if required_seconds is not None and ticket.max_train_seconds is not None:
        if required_seconds > ticket.max_train_seconds:
            violations.append(
                f"run needs {required_seconds:.0f}s but ticket allows "
                f"{ticket.max_train_seconds:.0f}s"
            )
    if required_disk_gib is not None and ticket.max_new_disk_gib is not None:
        if required_disk_gib > ticket.max_new_disk_gib:
            violations.append(
                f"run needs {required_disk_gib:.2f} GiB but ticket allows "
                f"{ticket.max_new_disk_gib:.2f} GiB"
            )
    if required_gpu_processes > ticket.max_gpu_processes:
        violations.append(
            f"run needs {required_gpu_processes} GPU processes but ticket allows "
            f"{ticket.max_gpu_processes}"
        )
    return violations


def save_ticket(ticket: AuthorizationTicket, path: Path | str) -> Path:
    """Persist a ticket atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(ticket.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def load_ticket(path: Path | str) -> AuthorizationTicket:
    """Load a ticket file, refusing malformed payloads."""
    source = Path(path)
    if not source.is_file():
        raise AuthorizationError(f"authorization ticket not found: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthorizationError(f"authorization ticket is not valid JSON: {source}") from exc
    if not isinstance(data, dict):
        raise AuthorizationError(f"authorization ticket must be a mapping: {source}")
    try:
        return AuthorizationTicket.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorizationError(f"authorization ticket is malformed: {exc}") from exc


def ticket_fingerprint(ticket: AuthorizationTicket) -> str:
    """Stable fingerprint of a ticket for ledger records."""
    encoded = json.dumps(ticket.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def validate_against_ticket(
    plan: Mapping[str, Any],
    ticket: AuthorizationTicket | None,
) -> AuthorizationTicket:
    """Return the effective ticket for a plan, enforcing smoke caps by default.

    Without an explicit ticket the smoke tier applies; any requirement above the
    smoke caps raises with the exceeded cap named.
    """
    plan_hash = str(plan["plan_hash"])
    required_targets = int(plan["budget_valid_targets"])
    required_seconds = plan.get("budget_max_seconds")
    required_disk = plan.get("estimated_new_disk_gib")
    gpu_processes = int(plan.get("gpu_processes", 1))

    effective = ticket if ticket is not None else smoke_ticket(plan_hash)
    violations = check_ticket(
        effective,
        plan_hash,
        required_targets,
        required_seconds=required_seconds,
        required_disk_gib=required_disk,
        required_gpu_processes=gpu_processes,
    )
    if violations:
        raise AuthorizationError(
            "execution refused: "
            + "; ".join(violations)
            + ("" if ticket is not None else " (no ticket supplied; smoke caps apply)")
        )
    return effective
