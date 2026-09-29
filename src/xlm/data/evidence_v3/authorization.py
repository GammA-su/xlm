"""Phase-P authorization and operator approval: bytes -> minted objects.

Nothing here accepts a pre-validated mapping. The flow is:

1. :func:`derive_expectations` computes every binding from the ACTUAL
   repository, committed child artifacts (REAL) or harness plans
   (SYNTHETIC), and the ACTUAL running environment -> minted
   :class:`Expectations`.
2. :func:`validate_authorization` strictly parses canonical JSON bytes
   (exact key set, exact types, exact literals, canonical-byte equality
   for structured values) against those expectations ->
   :class:`ValidatedPhasePAuthorization`.
3. :func:`validate_operator_approval` strictly parses a separate approval
   artifact whose ``decision`` must be exactly ``APPROVE_PHASE_P`` and
   which binds the authorization digest -> :class:`ValidatedOperatorApproval`.
   Free-text ``notes`` exist but have no effect.

Neither trusted class can be constructed by callers (see :mod:`trust`).
Authenticity limit (stated): artifacts are integrity-bound by digests,
not cryptographically signed. The reviewer's decision is a strict typed
review-decision artifact that binds the exact authorization REQUEST digest
(all authorization fields except the review fields and ``digest``); the
authorization binds that artifact's SHA-256; in REAL mode the review
artifact must be committed at HEAD (auditable, but not proof of identity).
"""

from __future__ import annotations

import datetime
import hashlib
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import envidentity, frozen_v3, fsroot, plan, trust
from xlm.data.evidence_v3.harness import SyntheticHarness


class AuthorizationError(ValueError):
    """Authorization/approval refused. Never downgraded to a warning."""


AUTH_KIND = "essential-web-evidence-v3-phase-p-authorization"
AUTH_SCHEMA_VERSION = 2
APPROVAL_KIND = "essential-web-evidence-v3-operator-approval"
REVIEW_KIND = "essential-web-evidence-v3-phase-p-review-decision"
REVIEW_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 2
REVIEWER_DECISION = "APPROVE_PHASE_P_AUTHORIZATION"
OPERATOR_DECISION = "APPROVE_PHASE_P"
AUTHORIZATION_SCOPE = "PHASE_P_ONLY"
ROOT_PRECONDITION = "ABSENT_OR_EMPTY_DIRECTORY"
ARMS = ["M", "T"]
MAX_ARTIFACT_BYTES = 1_048_576

AUTH_KEYS = frozenset(
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
        "authorization_scope",
        "root_precondition",
        "digest",
    }
)
APPROVAL_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "authorization_core_digest",
        "epoch_id",
        "phase",
        "decision",
        "operator_identity",
        "approved_at_utc",
        "notes",
        "digest",
    }
)
REVIEW_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "authorization_request_digest",
        "epoch_id",
        "phase",
        "decision",
        "reviewer_identity",
        "reviewed_at_utc",
        "review_report_sha256",
        "digest",
    }
)
REQUEST_EXCLUDED = ("reviewer_decision", "review_artifact_digest", "digest")
_OPERATOR = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 ._@\-]{0,127}\Z")
_UTC = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_FROZEN_ROOT_PARENT = "G:/Project/xlm-evidence-v3"


class Expectations(trust.TrustedObject):
    """Bindings derived from the actual repository and runtime."""

    __slots__ = (
        "mode",
        "execution_root",
        "implementation_commit",
        "child_manifest_digest",
        "plans",
        "code_hashes",
        "environment",
        "review_sha256",
        "review_bytes",
        "review_commit_check",
    )
    mode: str
    execution_root: str
    implementation_commit: str | None
    child_manifest_digest: str
    plans: Mapping[str, plan.ValidatedPhasePPlan]
    code_hashes: Mapping[str, str]
    environment: Mapping[str, str]
    review_sha256: str
    review_bytes: bytes
    review_commit_check: tuple[Path, Path] | None


class ValidatedPhasePAuthorization(trust.TrustedObject):
    __slots__ = (
        "digest",
        "mode",
        "execution_root",
        "implementation_commit",
        "child_manifest_digest",
        "plans",
        "code_hashes",
        "environment",
        "review_sha256",
        "resource_caps_digest",
        "raw_sha256",
    )
    digest: str
    mode: str
    execution_root: str
    implementation_commit: str
    child_manifest_digest: str
    plans: Mapping[str, plan.ValidatedPhasePPlan]
    code_hashes: Mapping[str, str]
    environment: Mapping[str, str]
    review_sha256: str
    resource_caps_digest: str
    raw_sha256: str


class ValidatedOperatorApproval(trust.TrustedObject):
    __slots__ = ("digest", "authorization_digest", "operator_identity", "approved_at_utc")
    digest: str
    authorization_digest: str
    operator_identity: str
    approved_at_utc: str


def is_under_frozen_project(root: Path) -> bool:
    text = fsroot.root_string(root).lower()
    parent = _FROZEN_ROOT_PARENT.lower()
    return text == parent or text.startswith(parent + "/")


def synthetic_manifest_digest(m_digest: str, t_digest: str) -> str:
    return canonical.digest({"synthetic_child_manifest": {"M": m_digest, "T": t_digest}})


def authorization_request_digest(body: Mapping[str, Any]) -> str:
    """Digest of the authorization request the reviewer decides on."""
    return canonical.digest({k: v for k, v in body.items() if k not in REQUEST_EXCLUDED})


def _review_committed(repo: Path, review_path: Path, review_bytes: bytes) -> None:
    try:
        rel = Path(review_path).resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise AuthorizationError("REAL review artifact must live inside the repository") from exc
    head = envidentity.resolve_commit(repo, "HEAD")
    try:
        envidentity.representation(review_bytes, envidentity.committed_blob(repo, head, rel))
    except envidentity.EnvIdentityError as exc:
        raise AuthorizationError(f"REAL review artifact is not committed at HEAD: {exc}") from exc


def _check_loaded_modules(repo: Path, code: Mapping[str, str]) -> None:
    """REAL mode: every loaded ``xlm`` module must be bound code."""
    src = (repo / "src").resolve()
    for name, module in list(sys.modules.items()):
        if name != "xlm" and not name.startswith("xlm."):
            continue
        location = getattr(module, "__file__", None)
        if location is None:
            continue
        try:
            rel = "src/" + Path(location).resolve().relative_to(src).as_posix()
        except ValueError as exc:
            raise AuthorizationError(f"module {name} loaded from outside the repository") from exc
        if rel not in code:
            raise AuthorizationError(f"loaded module {name} ({rel}) is not bound code")


def derive_expectations(
    *,
    repo: Path,
    root: Path,
    review_path: Path,
    review_bytes: bytes,
    harness: SyntheticHarness | None,
) -> Expectations:
    """Compute every authorization binding from the actual state."""
    paths = envidentity.code_paths(repo)
    try:
        if harness is None:
            mode = "REAL"
            if fsroot.root_string(root) != frozen_v3.EXECUTION_ROOT:
                raise AuthorizationError("REAL mode runs only at the frozen execution root")
            children = plan.load_committed_children(repo)
            commit = children.implementation_commit
            if set(children.code_hashes) != set(paths):
                raise AuthorizationError(
                    "child code identity does not cover exactly the bound code"
                )
            committed = envidentity.committed_identity(repo, commit, paths)
            if canonical.canonical_bytes(committed) != canonical.canonical_bytes(
                dict(sorted(children.code_hashes.items()))
            ):
                raise AuthorizationError("child code identity differs from the committed blobs")
            envidentity.verify_worktree_against_commit(repo, commit, paths)
            config = envidentity.committed_identity(repo, commit, envidentity.CONFIG_PATHS)
            envidentity.verify_worktree_against_commit(repo, commit, envidentity.CONFIG_PATHS)
            _check_loaded_modules(repo, committed)
            review_check: tuple[Path, Path] | None = (repo, review_path)
            plans = dict(children.plans)
            manifest_digest = children.manifest_digest
            code = committed
            implementation_commit: str | None = commit
            execution_root = frozen_v3.EXECUTION_ROOT
        else:
            mode = "SYNTHETIC"
            if is_under_frozen_project(harness.root) or is_under_frozen_project(root):
                raise AuthorizationError("synthetic roots may not touch the frozen project root")
            if fsroot.root_string(root) != fsroot.root_string(harness.root):
                raise AuthorizationError("synthetic root differs from the harness root")
            plans = {
                "M": plan.validate_plan_bytes(harness.plan_m_bytes, arm="M", synthetic=True),
                "T": plan.validate_plan_bytes(harness.plan_t_bytes, arm="T", synthetic=True),
            }
            manifest_digest = synthetic_manifest_digest(plans["M"].digest, plans["T"].digest)
            code = envidentity.worktree_identity(repo, paths)
            config = envidentity.worktree_identity(repo, envidentity.CONFIG_PATHS)
            if not envidentity.is_commit(harness.implementation_commit):
                raise AuthorizationError("harness implementation_commit must be 40-hex")
            implementation_commit = harness.implementation_commit
            execution_root = fsroot.root_string(harness.root)
            review_check = None
        environment = envidentity.runtime_environment(repo, config_identity=config)
    except (envidentity.EnvIdentityError, plan.PlanError, OSError) as exc:
        raise AuthorizationError(f"cannot derive authorization expectations: {exc}") from exc
    return trust._mint(
        Expectations,
        trust._MINT,
        mode=mode,
        execution_root=execution_root,
        implementation_commit=implementation_commit,
        child_manifest_digest=manifest_digest,
        plans=plans,
        code_hashes=dict(code),
        environment=dict(environment),
        review_sha256=hashlib.sha256(review_bytes).hexdigest(),
        review_bytes=review_bytes,
        review_commit_check=review_check,
    )


def _parse(raw: bytes, what: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise AuthorizationError(f"{what} must be non-empty bytes <= 1 MiB")
    try:
        value = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise AuthorizationError(f"{what} is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorizationError(f"{what} must be a JSON object")
    if canonical.canonical_bytes(value) != raw:
        raise AuthorizationError(f"{what} bytes are not canonical JSON")
    return value


def _same(got: Any, want: Any, what: str) -> None:
    """Canonical-byte equality: bool is never 1, 1.0 is never 1."""
    try:
        equal = canonical.canonical_bytes(got) == canonical.canonical_bytes(want)
    except canonical.CanonicalError:
        equal = False
    if not equal:
        raise AuthorizationError(f"{what} does not match the reviewed binding")


def _string(value: Any, what: str) -> str:
    if type(value) is not str:
        raise AuthorizationError(f"{what} must be a string")
    return value


def validate_authorization(raw: bytes, expect: Expectations) -> ValidatedPhasePAuthorization:
    """Strict Phase-P authorization validation against derived expectations."""
    expect = trust.require_minted(expect, Expectations)
    body = _parse(raw, "authorization")
    if set(body) != AUTH_KEYS:
        missing = sorted(AUTH_KEYS - set(body))
        unknown = sorted(set(body) - AUTH_KEYS)
        raise AuthorizationError(f"authorization keys wrong: missing={missing} unknown={unknown}")
    _same(body["kind"], AUTH_KIND, "kind")
    _same(body["schema_version"], AUTH_SCHEMA_VERSION, "schema_version")
    _same(body["protocol_sha256"], frozen_v3.PROTOCOL_SHA256, "protocol_sha256")
    _same(body["freeze_digest"], frozen_v3.FREEZE_DIGEST, "freeze_digest")
    _same(body["freeze_commit"], frozen_v3.FROZEN_PROTOCOL_COMMIT, "freeze_commit")
    commit = _string(body["implementation_commit"], "implementation_commit")
    if not envidentity.is_commit(commit):
        raise AuthorizationError("implementation_commit must be a 40-hex Git commit ID")
    if expect.implementation_commit is not None:
        _same(commit, expect.implementation_commit, "implementation_commit")
    _same(body["child_manifest_digest"], expect.child_manifest_digest, "child_manifest_digest")
    _same(body["epoch_id"], frozen_v3.EPOCH_ID, "epoch_id")
    _same(body["execution_root"], expect.execution_root, "execution_root")
    _same(body["phase"], "P", "phase")
    _same(body["arms"], ARMS, "arms")
    _same(body["m_phase_p_plan_digest"], expect.plans["M"].digest, "m_phase_p_plan_digest")
    _same(body["t_phase_p_plan_digest"], expect.plans["T"].digest, "t_phase_p_plan_digest")
    _same(body["scientific_namespace"], frozen_v3.SCIENTIFIC_NAMESPACE, "scientific_namespace")
    _same(body["selection_digest"], frozen_v3.SELECTION_DIGEST, "selection_digest")
    _same(body["source_revision"], frozen_v3.SOURCE_REVISION, "source_revision")
    caps = {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS}
    _same(body["resource_caps"], caps, "resource_caps")
    if canonical.digest(caps) != frozen_v3.RESOURCE_CAPS_DIGEST:
        raise AuthorizationError("frozen cap map no longer reproduces its frozen digest")
    _same(body["resource_caps_digest"], frozen_v3.RESOURCE_CAPS_DIGEST, "resource_caps_digest")
    try:
        code = envidentity.check_code_map_schema(body["code_hashes"])
        env = envidentity.check_environment_schema(body["environment_identity"])
    except envidentity.EnvIdentityError as exc:
        raise AuthorizationError(str(exc)) from exc
    _same(code, dict(sorted(expect.code_hashes.items())), "code_hashes")
    _same(env, dict(expect.environment), "environment_identity (actual runtime)")
    _same(body["reviewer_decision"], REVIEWER_DECISION, "reviewer_decision")
    _same(body["authorization_scope"], AUTHORIZATION_SCOPE, "authorization_scope")
    _same(body["root_precondition"], ROOT_PRECONDITION, "root_precondition")
    digest = canonical.self_digest(body)
    _same(body["digest"], digest, "authorization digest")
    _same(body["review_artifact_digest"], expect.review_sha256, "review_artifact_digest")
    _validate_review(expect.review_bytes, authorization_request_digest(body))
    if expect.review_commit_check is not None:
        repo, review_path = expect.review_commit_check
        _review_committed(repo, review_path, expect.review_bytes)
    return trust._mint(
        ValidatedPhasePAuthorization,
        trust._MINT,
        digest=digest,
        mode=expect.mode,
        execution_root=expect.execution_root,
        implementation_commit=commit,
        child_manifest_digest=expect.child_manifest_digest,
        plans=dict(expect.plans),
        code_hashes=dict(code),
        environment=dict(env),
        review_sha256=expect.review_sha256,
        resource_caps_digest=frozen_v3.RESOURCE_CAPS_DIGEST,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _validate_review(raw: bytes, request_digest: str) -> None:
    """Strict typed reviewer decision bound to this exact authorization request."""
    body = _parse(raw, "review decision")
    if set(body) != REVIEW_KEYS:
        missing = sorted(REVIEW_KEYS - set(body))
        unknown = sorted(set(body) - REVIEW_KEYS)
        raise AuthorizationError(f"review keys wrong: missing={missing} unknown={unknown}")
    _same(body["kind"], REVIEW_KIND, "review kind")
    _same(body["schema_version"], REVIEW_SCHEMA_VERSION, "review schema_version")
    _same(body["authorization_request_digest"], request_digest, "review request digest")
    _same(body["epoch_id"], frozen_v3.EPOCH_ID, "review epoch_id")
    _same(body["phase"], "P", "review phase")
    _same(body["decision"], REVIEWER_DECISION, "review decision")
    reviewer = _string(body["reviewer_identity"], "reviewer_identity")
    if not _OPERATOR.match(reviewer):
        raise AuthorizationError("reviewer_identity must be 1-128 printable identity characters")
    stamp = _string(body["reviewed_at_utc"], "reviewed_at_utc")
    if not _UTC.match(stamp):
        raise AuthorizationError("reviewed_at_utc must be YYYY-MM-DDTHH:MM:SSZ")
    report = _string(body["review_report_sha256"], "review_report_sha256")
    if not envidentity.is_sha256(report):
        raise AuthorizationError("review_report_sha256 must be SHA-256 hex")
    _same(body["digest"], canonical.self_digest(body), "review digest")


def validate_operator_approval(
    raw: bytes, auth: ValidatedPhasePAuthorization
) -> ValidatedOperatorApproval:
    """Strict, semantic-free operator approval bound to one authorization."""
    auth = trust.require_minted(auth, ValidatedPhasePAuthorization)
    body = _parse(raw, "operator approval")
    if set(body) != APPROVAL_KEYS:
        missing = sorted(APPROVAL_KEYS - set(body))
        unknown = sorted(set(body) - APPROVAL_KEYS)
        raise AuthorizationError(f"approval keys wrong: missing={missing} unknown={unknown}")
    _same(body["kind"], APPROVAL_KIND, "approval kind")
    _same(body["schema_version"], APPROVAL_SCHEMA_VERSION, "approval schema_version")
    _same(body["authorization_core_digest"], auth.digest, "authorization_core_digest")
    _same(body["epoch_id"], frozen_v3.EPOCH_ID, "approval epoch_id")
    _same(body["phase"], "P", "approval phase")
    _same(body["decision"], OPERATOR_DECISION, "approval decision")
    operator = _string(body["operator_identity"], "operator_identity")
    if not _OPERATOR.match(operator):
        raise AuthorizationError("operator_identity must be 1-128 printable identity characters")
    stamp = _string(body["approved_at_utc"], "approved_at_utc")
    if not _UTC.match(stamp):
        raise AuthorizationError("approved_at_utc must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise AuthorizationError("approved_at_utc is not a real UTC time") from exc
    notes = _string(body["notes"], "notes")
    if len(notes) > 4096:
        raise AuthorizationError("notes are bounded to 4096 characters")
    digest = canonical.self_digest(body)
    _same(body["digest"], digest, "approval digest")
    return trust._mint(
        ValidatedOperatorApproval,
        trust._MINT,
        digest=digest,
        authorization_digest=auth.digest,
        operator_identity=operator,
        approved_at_utc=stamp,
    )
