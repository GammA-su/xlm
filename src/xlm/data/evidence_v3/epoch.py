"""v3 epoch gating, phase state machine, authorization, and root control.

No v3 network request may occur before a valid epoch_start.json exists.
This module implements and synthetic-tests the mechanism only; it never
creates a real genesis at the frozen root (that requires a separately
supplied authorization artifact plus operator review).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import frozen_v3


class EpochError(ValueError):
    """Any epoch, authorization, phase, or root violation: refuse."""


def _require_nonempty_mapping(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or not value:
        raise EpochError(f"genesis {name} must be a non-empty mapping")


VALID_PHASES = set(frozen_v3.PHASE_STATES)

# P_AUTHORIZED may only come from NOT_STARTED; D_AUTHORIZED only from sealed P.
_PHASE_EDGES: dict[str, set[str]] = {
    "NOT_STARTED": {"P_AUTHORIZED", "REFUSED"},
    "P_AUTHORIZED": {"P_RUNNING", "REFUSED"},
    "P_RUNNING": {"P_COMPLETE_SEALED", "INCOMPLETE", "REFUSED"},
    "P_COMPLETE_SEALED": {"D_AUTHORIZED", "REFUSED"},
    "D_AUTHORIZED": {"D_RUNNING", "REFUSED"},
    "D_RUNNING": {"D_COMPLETE", "INCOMPLETE", "REFUSED"},
    "D_COMPLETE": set(),
    "INCOMPLETE": set(),
    "REFUSED": set(),
}


def authorization_digest(artifact: Mapping[str, Any]) -> str:
    """Digest of a separately supplied authorization artifact."""
    if not isinstance(artifact, Mapping) or not artifact:
        raise EpochError("authorization artifact must be a non-empty mapping")
    return canonical.digest(dict(artifact))


def verify_authorization(
    *, artifact: Mapping[str, Any] | None, expected_digest: str, phase: str
) -> str:
    """Require a real artifact whose digest matches; no boolean bypass.

    Returns the verified digest. Any missing/empty/mismatched artifact or
    malformed digest refuses.
    """
    if artifact is None:
        raise EpochError(f"{phase}: network authorization NONE: refusing")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise EpochError(f"{phase}: malformed expected authorization digest")
    try:
        int(expected_digest, 16)
    except ValueError as exc:
        raise EpochError(f"{phase}: malformed expected authorization digest") from exc
    got = authorization_digest(artifact)
    if got != expected_digest:
        raise EpochError(f"{phase}: authorization digest mismatch: refusing")
    phase_marker = str(artifact.get("phase", ""))
    if phase_marker and phase_marker != phase:
        raise EpochError(f"{phase}: authorization artifact is for phase {phase_marker}")
    return got


class PhaseGate:
    """Per-arm phase state machine with digest-bound authorization.

    A phase-P authorization never authorizes D. D authorization must bind
    the sealed P output digest.
    """

    def __init__(self, arm: str) -> None:
        if arm not in ("M", "T"):
            raise EpochError(f"unknown arm: {arm}")
        self.arm = arm
        self.state = "NOT_STARTED"
        self.p_authorization_digest: str | None = None
        self.sealed_p_digest: str | None = None
        self.d_authorization_digest: str | None = None

    def authorize_p(self, *, artifact: Mapping[str, Any], expected_digest: str) -> None:
        if self.state != "NOT_STARTED":
            raise EpochError(f"arm {self.arm}: P authorization only from NOT_STARTED")
        self.p_authorization_digest = verify_authorization(
            artifact=artifact, expected_digest=expected_digest, phase="P"
        )
        self.state = "P_AUTHORIZED"

    def begin_p(self) -> None:
        self._move("P_RUNNING")

    def seal_p(self, *, sealed_p_digest: str) -> None:
        if self.state != "P_RUNNING":
            raise EpochError(f"arm {self.arm}: P seal only while P_RUNNING")
        if not isinstance(sealed_p_digest, str) or len(sealed_p_digest) != 64:
            raise EpochError("sealed P digest malformed")
        self.sealed_p_digest = sealed_p_digest
        self.state = "P_COMPLETE_SEALED"

    def authorize_d(
        self, *, artifact: Mapping[str, Any], expected_digest: str, sealed_p_digest: str
    ) -> None:
        if self.state != "P_COMPLETE_SEALED":
            raise EpochError(f"arm {self.arm}: D authorization requires sealed P")
        if self.sealed_p_digest is None or sealed_p_digest != self.sealed_p_digest:
            raise EpochError(f"arm {self.arm}: D authorization must bind sealed P digest")
        bound = artifact.get("sealed_p_digest") if isinstance(artifact, Mapping) else None
        if bound != self.sealed_p_digest:
            raise EpochError(f"arm {self.arm}: D artifact does not bind sealed P digest")
        self.d_authorization_digest = verify_authorization(
            artifact=artifact, expected_digest=expected_digest, phase="D"
        )
        # A P ticket must never equal a D ticket.
        if self.d_authorization_digest == self.p_authorization_digest:
            raise EpochError(f"arm {self.arm}: P authorization cannot authorize D")
        self.state = "D_AUTHORIZED"

    def begin_d(self) -> None:
        self._move("D_RUNNING")

    def complete_d(self) -> None:
        self._move("D_COMPLETE")

    def mark_incomplete(self) -> None:
        if self.state not in ("P_RUNNING", "D_RUNNING"):
            raise EpochError("INCOMPLETE only while a phase is running")
        self.state = "INCOMPLETE"

    def refuse(self) -> None:
        if self.state in ("D_COMPLETE", "INCOMPLETE", "REFUSED"):
            raise EpochError("terminal state cannot transition")
        self.state = "REFUSED"

    def _move(self, nxt: str) -> None:
        if nxt not in _PHASE_EDGES.get(self.state, set()):
            raise EpochError(f"arm {self.arm}: illegal transition {self.state} -> {nxt}")
        self.state = nxt

    def snapshot(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "state": self.state,
            "p_authorization_digest": self.p_authorization_digest,
            "sealed_p_digest": self.sealed_p_digest,
            "d_authorization_digest": self.d_authorization_digest,
        }


def check_root_pristine(root: Path) -> dict[str, Any]:
    """Require the future execution root absent OR protocol-defined empty.

    Returns an inventory binding of the pre-write state. Any preexisting
    non-empty directory, file, symlink, or reparse point refuses.
    """
    if not root.exists() and not root.is_symlink():
        return {"root": root.as_posix(), "preexisting": False, "entries": []}
    if root.is_symlink():
        raise EpochError(f"execution root is a link/reparse point: {root}: refusing")
    if root.is_file():
        raise EpochError(f"execution root exists as a file: {root}: refusing")
    if not root.is_dir():
        raise EpochError(f"execution root is not observable: {root}: refusing")
    entries = sorted(p.name for p in root.iterdir())
    if entries:
        raise EpochError(f"execution root is not pristine ({len(entries)} entries): refusing")
    return {"root": root.as_posix(), "preexisting": True, "entries": []}


def inventory_root(root: Path) -> dict[str, Any]:
    """Physical inventory of the execution root (relative paths, bytes, sha)."""
    if not root.is_dir() or root.is_symlink():
        raise EpochError(f"cannot inventory non-directory root: {root}")
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise EpochError(f"link/reparse escape in execution root: {path}: refusing")
        if path.is_file():
            raw = path.read_bytes()
            out[path.relative_to(root).as_posix()] = {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
    total = sum(v["bytes"] for v in out.values())
    return {"root": root.as_posix(), "files": out, "total_bytes": total}


def build_epoch_start(
    *,
    protocol_digest: str,
    freeze_digest: str,
    implementation_commit: str,
    code_hashes: Mapping[str, str],
    environment: Mapping[str, Any],
    m_child_plan_digest: str,
    t_child_plan_digest: str,
    authorization_artifact: Mapping[str, Any],
    authorization_digest_expected: str,
    execution_root: Path,
    source_revision: str,
    resource_caps: Mapping[str, Any],
    owner: str,
    clock_monotonic_ns: int,
    initial_inventory: Mapping[str, Any],
    allow_nonfrozen_root: bool = False,
) -> dict[str, Any]:
    """Build (not publish) the epoch_start body; authorization is mandatory."""
    auth = verify_authorization(
        artifact=authorization_artifact,
        expected_digest=authorization_digest_expected,
        phase="P",
    )
    if protocol_digest != frozen_v3.PROTOCOL_SHA256:
        raise EpochError("epoch genesis binds the wrong protocol digest")
    if freeze_digest != frozen_v3.FREEZE_DIGEST:
        raise EpochError("epoch genesis binds the wrong freeze digest")
    if source_revision != frozen_v3.SOURCE_REVISION:
        raise EpochError("epoch genesis binds the wrong source revision")
    if execution_root.as_posix() != frozen_v3.EXECUTION_ROOT and not allow_nonfrozen_root:
        raise EpochError("epoch genesis must use the frozen execution root verbatim")
    if not owner:
        raise EpochError("epoch genesis requires an exclusive owner")
    body: dict[str, Any] = {
        "kind": "essential_web_evidence_v3_epoch_start",
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "epoch_id": frozen_v3.EPOCH_ID,
        "protocol_sha256": protocol_digest,
        "freeze_digest": freeze_digest,
        "implementation_commit": implementation_commit,
        "code_hashes": dict(sorted(code_hashes.items())),
        "environment": dict(environment),
        "m_child_plan_digest": m_child_plan_digest,
        "t_child_plan_digest": t_child_plan_digest,
        "execution_root": execution_root.as_posix(),
        "source_revision": source_revision,
        "resource_caps": json.loads(json.dumps(resource_caps)),
        "authorization_digest": auth,
        "owner": owner,
        "clock_monotonic_ns": int(clock_monotonic_ns),
        "initial_inventory": json.loads(json.dumps(initial_inventory)),
        "v3_network_events_before_genesis": 0,
        "epoch_state": "STARTED",
    }
    body["digest"] = canonical.self_digest(body)
    return body


def _self(body: Mapping[str, Any]) -> str:
    return canonical.self_digest({k: v for k, v in body.items() if k != "digest"})


def publish_epoch_start(root: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    """Crash-safe atomic epoch_start publication; exactly once.

    Refuses when epoch_start.json already exists (no second genesis).
    The caller must have verified root pristineness and inventory first.
    """
    target = root / "epoch_start.json"
    if target.exists() or target.is_symlink():
        raise EpochError("second genesis refused: epoch_start.json already exists")
    payload = canonical.canonical_bytes(dict(body))
    root.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)
    raw = target.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def monotonic_ns() -> int:
    """Monotonic clock baseline source (never wall-clock subtraction)."""
    return time.monotonic_ns()


GENESIS_CLAIM_NAME = "genesis.claim"
GENESIS_RECORD_NAME = "epoch_start.json"


def utc_timestamp_now() -> str:
    """UTC start timestamp in strict ISO-8601 Z form (validated on use)."""
    import datetime

    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def claim_genesis_exclusive(root: Path, *, owner: str) -> dict[str, Any]:
    """Claim exactly-once epoch genesis with a filesystem-exclusive lock file.

    Uses ``O_CREAT | O_EXCL`` so competing genesis attempts resolve to one
    winner on Windows and POSIX alike: losers raise ``EpochError``. The
    claim file itself is fsynced before returning. Callers must still
    refuse when ``epoch_start.json`` already exists (no second genesis
    after restart).
    """
    if not owner:
        raise EpochError("genesis claim requires an owner")
    root.mkdir(parents=True, exist_ok=True)
    claim = root / GENESIS_CLAIM_NAME
    if (root / GENESIS_RECORD_NAME).exists() or (root / GENESIS_RECORD_NAME).is_symlink():
        raise EpochError("second genesis refused: epoch_start.json already exists")
    fd = None
    try:
        fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise EpochError("competing genesis attempt refused: genesis already claimed") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                canonical.canonical_bytes({"epoch_id": frozen_v3.EPOCH_ID, "owner": owner}).decode(
                    "utf-8"
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        fd = None
    finally:
        if fd is not None:
            os.close(fd)
    return {"claim": claim.as_posix(), "owner": owner}


def build_genesis_record(
    *,
    authorization_digest: str,
    operator_approval_digest: str,
    protocol_digest: str,
    freeze_digest: str,
    implementation_commit: str,
    child_manifest_digest: str,
    m_phase_p_plan_digest: str,
    t_phase_p_plan_digest: str,
    execution_root: Path,
    initial_inventory: Mapping[str, Any],
    initial_zero_ledgers: Mapping[str, Any],
    code_hashes: Mapping[str, str],
    environment_identity: Mapping[str, Any],
    resource_caps: Mapping[str, Any],
    supervision_identity: Mapping[str, Any],
    owner: str,
    utc_timestamp: str,
    clock_monotonic_ns: int,
    allow_nonfrozen_root: bool = False,
) -> dict[str, Any]:
    """Build the strict durable genesis record binding every required field."""
    import re as _re

    for name, value in (
        ("authorization_digest", authorization_digest),
        ("operator_approval_digest", operator_approval_digest),
        ("child_manifest_digest", child_manifest_digest),
        ("m_phase_p_plan_digest", m_phase_p_plan_digest),
        ("t_phase_p_plan_digest", t_phase_p_plan_digest),
    ):
        if not isinstance(value, str) or not _re.match(r"\A[0-9a-f]{64}\Z", value):
            raise EpochError(f"genesis {name} must be a 64-char hex digest")
    if protocol_digest != frozen_v3.PROTOCOL_SHA256:
        raise EpochError("genesis binds the wrong protocol digest")
    if freeze_digest != frozen_v3.FREEZE_DIGEST:
        raise EpochError("genesis binds the wrong freeze digest")
    if not _re.match(r"\A[0-9a-f]{40}\Z", implementation_commit or ""):
        raise EpochError("genesis implementation_commit must be a 40-char commit SHA")
    if execution_root.as_posix() != frozen_v3.EXECUTION_ROOT and not allow_nonfrozen_root:
        raise EpochError("genesis must use the frozen execution root verbatim")
    if not _re.match(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", utc_timestamp or ""):
        raise EpochError("genesis utc_timestamp must be ISO-8601 UTC")
    if not isinstance(clock_monotonic_ns, int) or clock_monotonic_ns < 0:
        raise EpochError("genesis clock_monotonic_ns must be a non-negative integer")
    if not owner:
        raise EpochError("genesis requires an exclusive owner")
    _require_nonempty_mapping("initial_zero_ledgers", initial_zero_ledgers)
    _require_nonempty_mapping("code_hashes", code_hashes)
    _require_nonempty_mapping("environment_identity", environment_identity)
    _require_nonempty_mapping("resource_caps", resource_caps)
    _require_nonempty_mapping("supervision_identity", supervision_identity)
    for arm in ("M", "T"):
        cell = initial_zero_ledgers.get(arm)
        if not isinstance(cell, Mapping) or int(cell.get("requests", -1)) != 0:
            raise EpochError(f"genesis {arm} ledger is not zero-initialized")
    body: dict[str, Any] = {
        "kind": "essential_web_evidence_v3_epoch_start",
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "epoch_id": frozen_v3.EPOCH_ID,
        "protocol_sha256": protocol_digest,
        "freeze_digest": freeze_digest,
        "implementation_commit": implementation_commit,
        "child_manifest_digest": child_manifest_digest,
        "m_phase_p_plan_digest": m_phase_p_plan_digest,
        "t_phase_p_plan_digest": t_phase_p_plan_digest,
        "execution_root": execution_root.as_posix(),
        "initial_inventory": json.loads(json.dumps(initial_inventory)),
        "initial_zero_ledgers": json.loads(json.dumps(initial_zero_ledgers)),
        "code_hashes": dict(sorted(code_hashes.items())),
        "environment_identity": dict(environment_identity),
        "resource_caps": json.loads(json.dumps(resource_caps)),
        "supervision_identity": dict(supervision_identity),
        "authorization_digest": authorization_digest,
        "operator_approval_digest": operator_approval_digest,
        "owner": owner,
        "utc_timestamp": utc_timestamp,
        "clock_monotonic_ns": int(clock_monotonic_ns),
        "v3_network_events_before_genesis": 0,
        "epoch_state": "STARTED",
    }
    body["digest"] = canonical.self_digest(body)
    return body


def publish_genesis_record(
    root: Path, body: Mapping[str, Any], *, claim: Mapping[str, Any]
) -> dict[str, Any]:
    """Atomically publish the genesis record under an exclusive claim.

    Refuses without a matching claim, when the record already exists, or
    when the body digest is malformed. No overwrite, no replacement epoch.
    """
    if not isinstance(claim, Mapping) or "claim" not in claim:
        raise EpochError("genesis publication requires an exclusive claim")
    if Path(str(claim["claim"])).resolve() != (root / GENESIS_CLAIM_NAME).resolve():
        raise EpochError("genesis claim does not match this root")
    target = root / GENESIS_RECORD_NAME
    if target.exists() or target.is_symlink():
        raise EpochError("second genesis refused: epoch_start.json already exists")
    if _self(body) != body.get("digest"):
        raise EpochError("genesis record digest mismatch")
    payload = canonical.canonical_bytes(dict(body))
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)
    raw = target.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def load_genesis_record(root: Path, *, allow_nonfrozen_root: bool = False) -> dict[str, Any]:
    """Load and validate the durable genesis record (epoch gate)."""
    target = root / GENESIS_RECORD_NAME
    body = canonical.loads_bytes_strict(target.read_bytes())
    if not isinstance(body, dict):
        raise EpochError("genesis record is not a mapping")
    if _self(body) != body.get("digest"):
        raise EpochError("genesis record digest mismatch")
    if body.get("epoch_id") != frozen_v3.EPOCH_ID:
        raise EpochError("genesis record binds a different epoch")
    if body.get("execution_root") != frozen_v3.EXECUTION_ROOT and not allow_nonfrozen_root:
        raise EpochError("genesis record binds a different root")
    return body
