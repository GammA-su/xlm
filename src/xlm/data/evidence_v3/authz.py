"""Strict Phase-P authorization schema and distinct operator approval.

The previous validator accepted artifacts missing ``phase``, with
``reviewer_decision = BLOCKED``, or with wrong epoch/root bindings, as long
as the supplied digest matched. This module replaces it with a strict typed
schema: exact fields, no unknown fields, digest-bound semantics, and a
separate explicit operator approval bound to the complete authorization.

Canonical self-digest excludes ONLY ``digest``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import frozen_v3


class AuthError(ValueError):
    """Any authorization-schema or approval violation: refuse."""


AUTH_KIND = "essential-web-evidence-v3-phase-p-authorization"
AUTH_SCHEMA_VERSION = 1
APPROVAL_KIND = "essential-web-evidence-v3-operator-approval"
APPROVAL_SCHEMA_VERSION = 1

REQUIRED_AUTH_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "protocol_sha256",
        "freeze_digest",
        "freeze_commit",
        "implementation_commit",
        "child_manifest_digest",
        "epoch_id",
        "execution_root",
        "phase",
        "arms",
        "m_phase_p_plan_digest",
        "t_phase_p_plan_digest",
        "scientific_namespace",
        "selection_digest",
        "source_revision",
        "resource_caps",
        "resource_caps_digest",
        "code_hashes",
        "environment_identity",
        "reviewer_decision",
        "review_artifact_digest",
        "operator_approval",
        "authorization_scope",
        "root_precondition",
        "digest",
    }
)

REQUIRED_APPROVAL_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "authorization_core_digest",
        "operator_identity",
        "utc_timestamp",
        "statement",
        "digest",
    }
)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"\A[0-9a-f]{40}\Z")
_UTC = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def _self(body: Mapping[str, Any]) -> str:
    return canonical.self_digest({k: v for k, v in body.items() if k != "digest"})


def _hex64(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise AuthError(f"{what} must be a 64-char lowercase hex digest")
    return value


def _commit(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _HEX40.match(value):
        raise AuthError(f"{what} must be a 40-char git commit SHA")
    return value


def auth_core_digest(artifact: Mapping[str, Any]) -> str:
    """Digest of the operator-independent authorization core.

    Excludes ``digest`` and the embedded ``operator_approval`` so the
    approval can bind the core without circularity.
    """
    if not isinstance(artifact, Mapping):
        raise AuthError("authorization artifact must be a mapping")
    core = {k: v for k, v in artifact.items() if k not in ("digest", "operator_approval")}
    return canonical.digest(core)


def validate_operator_approval(approval: Mapping[str, Any], *, core_digest: str) -> str:
    """Validate a separate explicit operator approval bound to the auth core."""
    if isinstance(approval, bool) or not isinstance(approval, Mapping):
        raise AuthError("operator approval must be an explicit mapping, never a boolean")
    unknown = sorted(k for k in approval if k not in REQUIRED_APPROVAL_FIELDS)
    if unknown:
        raise AuthError(f"operator approval has unknown fields: {unknown}")
    missing = sorted(k for k in REQUIRED_APPROVAL_FIELDS if k not in approval)
    if missing:
        raise AuthError(f"operator approval is missing fields: {missing}")
    if approval["kind"] != APPROVAL_KIND:
        raise AuthError("operator approval kind mismatch")
    if approval["schema_version"] != APPROVAL_SCHEMA_VERSION:
        raise AuthError("operator approval schema_version mismatch")
    if approval["authorization_core_digest"] != core_digest:
        raise AuthError("operator approval does not bind this authorization core")
    operator = approval["operator_identity"]
    if not isinstance(operator, str) or not operator.strip():
        raise AuthError("operator_identity must be a non-empty string")
    stamp = approval["utc_timestamp"]
    if not isinstance(stamp, str) or not _UTC.match(stamp):
        raise AuthError("utc_timestamp must be ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
    statement = approval["statement"]
    if not isinstance(statement, str) or "phase-P" not in statement:
        raise AuthError("operator statement must explicitly approve phase-P execution")
    if _self(approval) != approval["digest"]:
        raise AuthError("operator approval digest mismatch")
    return str(approval["digest"])


def validate_phase_p_authorization(
    artifact: Mapping[str, Any],
    *,
    expected_freeze_commit: str,
    expected_implementation_commit: str,
    expected_child_manifest_digest: str,
    expected_m_phase_p_plan_digest: str,
    expected_t_phase_p_plan_digest: str,
    expected_code_hashes: Mapping[str, str],
    accepted_review_digests: Set[str],
) -> dict[str, Any]:
    """Strictly validate a Phase-P authorization artifact.

    Every binding is checked against caller-supplied expectations derived
    from the frozen protocol and regenerated child artifacts. Stale or
    superseded review artifacts (not in ``accepted_review_digests``) refuse.
    Returns the validated artifact plus derived digests.
    """
    if isinstance(artifact, bool) or not isinstance(artifact, Mapping):
        raise AuthError("phase-P authorization must be a mapping, never a boolean")
    unknown = sorted(k for k in artifact if k not in REQUIRED_AUTH_FIELDS)
    if unknown:
        raise AuthError(f"phase-P authorization has unknown fields: {unknown}")
    missing = sorted(k for k in REQUIRED_AUTH_FIELDS if k not in artifact)
    if missing:
        raise AuthError(f"phase-P authorization is missing fields: {missing}")
    if artifact["kind"] != AUTH_KIND:
        raise AuthError("authorization kind mismatch")
    if artifact["schema_version"] != AUTH_SCHEMA_VERSION:
        raise AuthError("authorization schema_version mismatch")
    if artifact["phase"] != "P":
        raise AuthError("phase must be exactly 'P'")
    if artifact["reviewer_decision"] != "APPROVED":
        raise AuthError("reviewer_decision is not a successful authorization decision")
    if artifact["authorization_scope"] != "phase-P-only":
        raise AuthError("authorization_scope must be exactly 'phase-P-only'")
    if artifact["root_precondition"] != "absent-or-pristine-empty":
        raise AuthError("root_precondition must be exactly 'absent-or-pristine-empty'")
    if artifact["epoch_id"] != frozen_v3.EPOCH_ID:
        raise AuthError("wrong epoch binding")
    if artifact["execution_root"] != frozen_v3.EXECUTION_ROOT:
        raise AuthError("wrong execution-root binding")
    if artifact["protocol_sha256"] != frozen_v3.PROTOCOL_SHA256:
        raise AuthError("wrong protocol binding")
    if artifact["freeze_digest"] != frozen_v3.FREEZE_DIGEST:
        raise AuthError("wrong freeze binding")
    if _commit(artifact["freeze_commit"], "freeze_commit") != expected_freeze_commit:
        raise AuthError("wrong freeze-commit binding")
    if (
        _commit(artifact["implementation_commit"], "implementation_commit")
        != expected_implementation_commit
    ):
        raise AuthError("wrong implementation-commit binding")
    child = _hex64(artifact["child_manifest_digest"], "child_manifest")
    if child != expected_child_manifest_digest:
        raise AuthError("wrong child-manifest binding")
    m_plan = _hex64(artifact["m_phase_p_plan_digest"], "m_phase_p_plan")
    if m_plan != expected_m_phase_p_plan_digest:
        raise AuthError("wrong M phase-P plan binding")
    t_plan = _hex64(artifact["t_phase_p_plan_digest"], "t_phase_p_plan")
    if t_plan != expected_t_phase_p_plan_digest:
        raise AuthError("wrong T phase-P plan binding")
    if artifact["scientific_namespace"] != frozen_v3.SCIENTIFIC_NAMESPACE:
        raise AuthError("wrong scientific-namespace binding")
    if artifact["selection_digest"] != frozen_v3.SELECTION_DIGEST:
        raise AuthError("wrong selection binding")
    if artifact["source_revision"] != frozen_v3.SOURCE_REVISION:
        raise AuthError("wrong source-revision binding")
    if list(artifact["arms"]) != ["M", "T"]:
        raise AuthError("arms must be exactly ['M', 'T']")
    caps = artifact["resource_caps"]
    if not isinstance(caps, Mapping) or set(caps) != {"M", "T"}:
        raise AuthError("resource_caps must bind exactly M and T cap maps")
    if dict(caps["M"]) != dict(frozen_v3.ARM_M_CAPS):
        raise AuthError("resource caps differ from the frozen protocol")
    if dict(caps["T"]) != dict(frozen_v3.ARM_T_CAPS):
        raise AuthError("resource caps differ from the frozen protocol")
    recomputed_caps = canonical.digest({"M": dict(caps["M"]), "T": dict(caps["T"])})
    if recomputed_caps != artifact["resource_caps_digest"]:
        raise AuthError("resource_caps_digest does not match resource_caps")
    if artifact["resource_caps_digest"] != frozen_v3.RESOURCE_CAPS_DIGEST:
        raise AuthError("resource_caps_digest is not the frozen cap-map digest")
    code = artifact["code_hashes"]
    if not isinstance(code, Mapping) or not code:
        raise AuthError("code_hashes must be a non-empty mapping")
    for path, digest in code.items():
        if not isinstance(path, str) or not path:
            raise AuthError("code_hashes paths must be non-empty strings")
        _hex64(digest, f"code hash for {path}")
    if dict(code) != dict(expected_code_hashes):
        raise AuthError("code_hashes do not match the approved implementation")
    env = artifact["environment_identity"]
    if not isinstance(env, Mapping) or not env:
        raise AuthError("environment_identity must be a non-empty mapping")
    for key in (
        "python_version",
        "python_executable_class",
        "uv_version",
        "uv_lock_blob_sha",
        "pyproject_blob_sha",
        "python_version_blob_sha",
        "pyarrow_version",
        "psutil_version",
        "torch_version",
        "cpu_cuda_build",
        "os_build",
        "architecture",
    ):
        if key not in env or not isinstance(env[key], str) or not env[key]:
            raise AuthError(f"environment_identity is missing {key}")
    review = _hex64(artifact["review_artifact_digest"], "review_artifact_digest")
    if review not in set(accepted_review_digests):
        raise AuthError("stale or superseded review artifact")
    core = auth_core_digest(artifact)
    approval_digest = validate_operator_approval(artifact["operator_approval"], core_digest=core)
    if _self(artifact) != artifact["digest"]:
        raise AuthError("authorization digest mismatch")
    return {
        "authorization_digest": str(artifact["digest"]),
        "authorization_core_digest": core,
        "operator_approval_digest": approval_digest,
        "epoch_id": frozen_v3.EPOCH_ID,
        "phase": "P",
    }
