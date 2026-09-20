"""Sealed-mode readiness: prove isolation, never assert it (P21, A36).

A same-user hidden path or a YAML `sealed: true` flag must fail. Readiness
requires a sealed root that (a) exists, (b) lives outside every agent mount,
(c) is not writable by the calling process, and (d) carries an operator
attestation naming a real separation method. Public availability of benchmark
text and pretrained-agent familiarity remain documented limitations no check
can remove.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SEALED_VERSION = "1"
ATTESTATION_FILENAME = "operator_attestation.json"

SEPARATION_METHODS = ("separate-account", "separate-machine")


class SealedReadinessError(RuntimeError):
    """Raised when sealed mode cannot be established honestly."""


@dataclass
class ReadinessReport:
    """Itemized sealed-mode readiness result."""

    ready: bool
    sealed_root: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fail(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"check": name, "passed": False, "detail": detail})


def _pass(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"check": name, "passed": True, "detail": detail})


def check_sealed_readiness(
    sealed_root: Path | str | None,
    agent_roots: list[Path | str],
    *,
    access: Callable[[str, int], bool] = os.access,
) -> ReadinessReport:
    """Decide whether a sealed environment is genuinely established.

    The `access` callable is injectable so the decision logic is unit-testable;
    production callers pass the real `os.access`.
    """
    checks: list[dict[str, Any]] = []
    limitations = [
        "public benchmark text is intrinsically copyable elsewhere; isolation "
        "is operational, not a proof of non-exposure",
        "pretrained agents may have encountered public data before; this check "
        "cannot establish otherwise",
    ]
    root = Path(sealed_root) if sealed_root else None

    if root is None or not root.exists():
        _fail(checks, "sealed_root_configured", f"no sealed root at '{sealed_root}'")
        return ReadinessReport(False, str(sealed_root), checks, limitations)
    _pass(checks, "sealed_root_configured", f"sealed root exists at '{root}'")
    resolved = root.resolve()

    for agent_root in agent_roots:
        anchor = Path(agent_root).resolve()
        try:
            resolved.relative_to(anchor)
        except ValueError:
            continue
        _fail(
            checks,
            "outside_agent_mounts",
            f"sealed root '{resolved}' lives inside agent mount '{anchor}'; "
            "same-user paths are not isolation",
        )
        return ReadinessReport(False, str(resolved), checks, limitations)
    _pass(checks, "outside_agent_mounts", "sealed root is outside all agent mounts")

    if access(str(resolved), os.W_OK):
        _fail(
            checks,
            "not_writable_by_caller",
            "calling process can write to the sealed root; a same-user directory "
            "is not an isolated store",
        )
        return ReadinessReport(False, str(resolved), checks, limitations)
    _pass(checks, "not_writable_by_caller", "calling process cannot write to the sealed root")

    attestation_path = resolved / ATTESTATION_FILENAME
    if not attestation_path.is_file():
        _fail(
            checks,
            "operator_attestation",
            f"no operator attestation at '{attestation_path}'; a YAML sealed flag "
            "without attestation never counts",
        )
        return ReadinessReport(False, str(resolved), checks, limitations)
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _fail(checks, "operator_attestation", f"attestation unreadable: {exc}")
        return ReadinessReport(False, str(resolved), checks, limitations)
    method = attestation.get("separation_method") if isinstance(attestation, dict) else None
    operator = attestation.get("operator") if isinstance(attestation, dict) else None
    if method not in SEPARATION_METHODS or not operator:
        _fail(
            checks,
            "operator_attestation",
            f"attestation must name separation_method in {list(SEPARATION_METHODS)} "
            "and an operator; a bare sealed flag is refused",
        )
        return ReadinessReport(False, str(resolved), checks, limitations)
    _pass(
        checks,
        "operator_attestation",
        f"attested by '{operator}' via '{method}'",
    )
    return ReadinessReport(True, str(resolved), checks, limitations)


def require_ready(report: ReadinessReport) -> None:
    """Raise unless the readiness report is fully green."""
    if not report.ready:
        failed = "; ".join(f"{c['check']}: {c['detail']}" for c in report.checks if not c["passed"])
        raise SealedReadinessError(f"sealed mode not established: {failed}")
