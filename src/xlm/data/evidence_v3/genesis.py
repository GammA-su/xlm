"""Exclusive epoch genesis and the strict epoch_start loader.

Publication accepts only minted objects: a validated authorization, a
validated operator approval bound to it, and a verified root context. No
mapping, claim dict or boolean is accepted.

Genesis steps (all inside the verified root via :class:`fsroot.RootFS`):

1. Root precondition: absent or an empty plain directory; no reparse point
   anywhere in its ancestry; REAL mode only at the frozen path.
2. Exclusive claim ``genesis.claim`` created with ``O_EXCL``. The claim
   binds the epoch ID, the root path AND its physical identity (volume
   serial + directory file ID), the authorization and approval digests.
   A claim copied into another directory no longer matches that
   directory's identity and is refused.
3. ``epoch_start.json`` created exclusively with the complete, exact
   record; then the journal is created with a GENESIS record chained to
   the epoch_start digest.

A crash mid-genesis leaves a claim without a valid epoch_start: the root
is no longer empty and the loader refuses — the epoch is BLOCKED, never
silently re-genesised. There is no second genesis.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import authorization, frozen_v3, fsroot, journal, plan, trust


class GenesisError(RuntimeError):
    """Genesis or epoch-load refusal."""


CLAIM_REL = "genesis.claim"
EPOCH_START_REL = "epoch_start.json"
LOCK_REL = "state/lock"
EPOCH_START_KIND = "essential-web-evidence-v3-epoch-start"
CLAIM_KIND = "essential-web-evidence-v3-genesis-claim"
SCHEMA_VERSION = 2
MEMORY_SAMPLER_REAL = "psutil-process-tree-plus-registered"
MEMORY_SAMPLER_SYNTHETIC = "synthetic-reader"
ZERO_LEDGER = {"requests": 0, "body_bytes": 0, "time_ns": 0, "network_events": 0}
EPOCH_START_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "protocol_version",
        "protocol_sha256",
        "freeze_digest",
        "freeze_commit",
        "epoch_id",
        "mode",
        "execution_root",
        "root_identity",
        "implementation_commit",
        "child_manifest_digest",
        "m_phase_p_plan_digest",
        "t_phase_p_plan_digest",
        "authorization_digest",
        "operator_approval_digest",
        "operator_identity",
        "code_hashes",
        "environment_identity",
        "resource_caps",
        "resource_caps_digest",
        "p_ceilings",
        "supervision_identity",
        "genesis_utc",
        "runtime_baseline",
        "initial_inventory",
        "initial_ledgers",
        "claim_sha256",
        "network_events_before_genesis",
        "digest",
    }
)
CLAIM_KEYS = frozenset(
    {
        "kind",
        "epoch_id",
        "root_identity",
        "authorization_digest",
        "operator_approval_digest",
        "owner",
        "nonce",
    }
)
_UTC = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


class VerifiedRootContext(trust.TrustedObject):
    """An execution root that passed the physical precondition checks."""

    __slots__ = ("mode", "identity")
    mode: str
    identity: fsroot.RootIdentity


def verify_root(root: Path, *, mode: str, create: bool) -> VerifiedRootContext:
    """Check (and for genesis, create) the root; return its physical identity."""
    text = fsroot.root_string(root)
    if mode == "REAL":
        if text != frozen_v3.EXECUTION_ROOT:
            raise GenesisError("REAL execution root must be the frozen root verbatim")
    elif mode == "SYNTHETIC":
        if authorization.is_under_frozen_project(root):
            raise GenesisError("synthetic roots may not touch the frozen project root")
    else:
        raise GenesisError(f"unknown mode {mode!r}")
    path = Path(os.path.abspath(root))
    fsroot.check_ancestors(path)
    if create:
        missing: list[Path] = []
        for candidate in [path, *path.parents]:
            if os.path.lexists(candidate):
                break
            missing.append(candidate)
        for candidate in reversed(missing):
            os.mkdir(candidate)
            st = os.lstat(candidate)
            if fsroot.is_reparse(st) or not stat.S_ISDIR(st.st_mode):
                raise GenesisError(f"created root component is not a plain directory: {candidate}")
        fsroot.check_ancestors(path)
        if any(os.scandir(path)):
            raise GenesisError("execution root is not empty (existing or dirty root refused)")
    identity = fsroot.observe_root(path)
    return trust._mint(VerifiedRootContext, trust._MINT, mode=mode, identity=identity)


def plan_summary(p: plan.ValidatedPhasePPlan) -> dict[str, Any]:
    """The per-arm plan facts the journal reducer enforces on replay."""
    return {
        "plan_digest": p.digest,
        "ops": [
            {
                "file": op.file,
                "op": op.op,
                "range": list(op.range) if op.range is not None else None,
                "remote_length": op.remote_length,
            }
            for op in p.operations
        ],
        "files": {f.file: dict(f.ceiling) for f in p.files},
        "arm_ceiling": dict(p.arm_ceiling),
        "response_body_bytes_max": p.response_body_bytes_max,
        "time_seconds": dict(p.time_seconds),
    }


def disk_caps() -> dict[str, dict[str, int]]:
    m, t = frozen_v3.ARM_M_CAPS, frozen_v3.ARM_T_CAPS
    return {
        "M": {
            "scratch": m["disk_scratch_bytes"],
            "final": m["disk_final_bytes"],
            "combined": m["disk_combined_bytes"],
        },
        "T": {
            "scratch": t["disk_scratch_bytes"],
            "final": t["disk_final_bytes"],
            "combined": t["disk_combined_bytes"],
        },
    }


def supervision_identity(mode: str, interval_s: float) -> dict[str, Any]:
    return {
        "memory_cap_bytes": frozen_v3.MEMORY_RESIDENT_BYTES_MAX,
        "sampler": MEMORY_SAMPLER_REAL if mode == "REAL" else MEMORY_SAMPLER_SYNTHETIC,
        "sampling_interval_ms": int(round(interval_s * 1000)),
        "deadline": "per-step absolute monotonic deadline; request <= 30 s",
        "clock": "time.monotonic_ns" if mode == "REAL" else "synthetic-clock",
    }


def _control_entry(size: int, sha: str | None, *, allowance: bool) -> dict[str, Any]:
    return {
        "size": size,
        "sha256": sha,
        "category": "scratch",
        "arms": ["M", "T"],
        "control_allowance": allowance,
    }


def publish(
    *,
    auth: authorization.ValidatedPhasePAuthorization,
    approval: authorization.ValidatedOperatorApproval,
    root_ctx: VerifiedRootContext,
    utc_now: str,
    monotonic_ns: int,
    sampling_interval_s: float,
) -> dict[str, Any]:
    """Publish genesis from trusted objects only (exclusive, durable)."""
    auth = trust.require_minted(auth, authorization.ValidatedPhasePAuthorization)
    approval = trust.require_minted(approval, authorization.ValidatedOperatorApproval)
    root_ctx = trust.require_minted(root_ctx, VerifiedRootContext)
    if approval.authorization_digest != auth.digest:
        raise GenesisError("operator approval does not bind this authorization")
    if auth.mode != root_ctx.mode or auth.execution_root != root_ctx.identity.path:
        raise GenesisError("authorization binds a different root/mode")
    if not _UTC.match(utc_now) or type(monotonic_ns) is not int or monotonic_ns < 0:
        raise GenesisError("genesis clock anchors malformed")
    fs = fsroot.RootFS(root_ctx.identity)
    claim = {
        "kind": CLAIM_KIND,
        "epoch_id": frozen_v3.EPOCH_ID,
        "root_identity": root_ctx.identity.as_record(),
        "authorization_digest": auth.digest,
        "operator_approval_digest": approval.digest,
        "owner": approval.operator_identity,
        "nonce": secrets.token_hex(16),
    }
    claim_raw = canonical.canonical_bytes(claim)
    try:
        claim_size, claim_sha = fs.create_exclusive(CLAIM_REL, claim_raw)
    except fsroot.ContainmentError as exc:
        raise GenesisError(f"genesis already claimed or root unusable: {exc}") from exc
    if set(fs.scan()) != {CLAIM_REL}:
        raise GenesisError("root changed during genesis claim")
    fs.mkdirs("state")
    fs.create_exclusive(LOCK_REL, b"")
    lock_sha = hashlib.sha256(b"").hexdigest()
    plans = {arm: auth.plans[arm] for arm in ("M", "T")}
    body: dict[str, Any] = {
        "kind": EPOCH_START_KIND,
        "schema_version": SCHEMA_VERSION,
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "protocol_sha256": frozen_v3.PROTOCOL_SHA256,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
        "epoch_id": frozen_v3.EPOCH_ID,
        "mode": auth.mode,
        "execution_root": auth.execution_root,
        "root_identity": root_ctx.identity.as_record(),
        "implementation_commit": auth.implementation_commit,
        "child_manifest_digest": auth.child_manifest_digest,
        "m_phase_p_plan_digest": plans["M"].digest,
        "t_phase_p_plan_digest": plans["T"].digest,
        "authorization_digest": auth.digest,
        "operator_approval_digest": approval.digest,
        "operator_identity": approval.operator_identity,
        "code_hashes": dict(auth.code_hashes),
        "environment_identity": dict(auth.environment),
        "resource_caps": {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS},
        "resource_caps_digest": auth.resource_caps_digest,
        "p_ceilings": {
            arm: {
                "arm": dict(p.arm_ceiling),
                "per_file": {f.file: dict(f.ceiling) for f in p.files},
                "future_d_reserve": {f.file: dict(f.d_reserve) for f in p.files},
            }
            for arm, p in plans.items()
        },
        "supervision_identity": supervision_identity(auth.mode, sampling_interval_s),
        "genesis_utc": utc_now,
        "runtime_baseline": {"monotonic_ns": monotonic_ns, "active_ns": {"M": 0, "T": 0}},
        "initial_inventory": {
            CLAIM_REL: {"size": claim_size, "sha256": claim_sha},
            LOCK_REL: {"size": 0, "sha256": lock_sha},
        },
        "initial_ledgers": {"M": dict(ZERO_LEDGER), "T": dict(ZERO_LEDGER)},
        "claim_sha256": claim_sha,
        "network_events_before_genesis": 0,
    }
    body["digest"] = canonical.self_digest(body)
    start_raw = canonical.canonical_bytes(body)
    start_size, start_sha = fs.create_exclusive(EPOCH_START_REL, start_raw)
    inventory = {
        CLAIM_REL: _control_entry(claim_size, claim_sha, allowance=False),
        LOCK_REL: _control_entry(0, lock_sha, allowance=False),
        EPOCH_START_REL: _control_entry(start_size, start_sha, allowance=False),
        journal.JOURNAL_REL: _control_entry(journal.JOURNAL_ALLOWANCE, None, allowance=True),
        journal.HEAD_REL: _control_entry(journal.HEAD_ALLOWANCE // 2, None, allowance=True),
        journal.HEAD_TMP_REL: _control_entry(journal.HEAD_ALLOWANCE // 2, None, allowance=True),
    }
    genesis_body = {
        "epoch_start_digest": body["digest"],
        "initial_inventory": inventory,
        "plans": {arm: plan_summary(p) for arm, p in plans.items()},
        "disk_caps": disk_caps(),
    }
    state = journal.new_state(genesis_body, frozen_v3.EPOCH_ID)
    record = {"seq": 0, "type": "GENESIS", "body": genesis_body}
    state.apply(record)
    log = journal.Journal(
        fs, epoch_id=frozen_v3.EPOCH_ID, seed=journal.chain_seed(frozen_v3.EPOCH_ID, body["digest"])
    )
    try:
        log.create(genesis_body)
    finally:
        log.close()
    return {
        "epoch_id": frozen_v3.EPOCH_ID,
        "mode": auth.mode,
        "execution_root": auth.execution_root,
        "epoch_start_digest": body["digest"],
        "epoch_start_sha256": start_sha,
        "claim_sha256": claim_sha,
    }


def load_epoch_start(
    fs: fsroot.RootFS,
    *,
    auth: authorization.ValidatedPhasePAuthorization,
    approval: authorization.ValidatedOperatorApproval,
    root_ctx: VerifiedRootContext,
    sampling_interval_s: float,
) -> dict[str, Any]:
    """Load epoch_start.json and validate EVERY binding against trusted state."""
    auth = trust.require_minted(auth, authorization.ValidatedPhasePAuthorization)
    approval = trust.require_minted(approval, authorization.ValidatedOperatorApproval)
    root_ctx = trust.require_minted(root_ctx, VerifiedRootContext)
    try:
        raw = fs.read_bytes(EPOCH_START_REL, max_bytes=1_048_576)
        claim_raw = fs.read_bytes(CLAIM_REL, max_bytes=65_536)
        body = canonical.loads_bytes_strict(raw)
        claim = canonical.loads_bytes_strict(claim_raw)
    except (OSError, fsroot.ContainmentError, canonical.CanonicalError) as exc:
        raise GenesisError(f"epoch_start/claim unreadable: {exc}") from exc
    if not isinstance(body, dict) or canonical.canonical_bytes(body) != raw:
        raise GenesisError("epoch_start is not canonical JSON")
    if set(body) != EPOCH_START_KEYS:
        raise GenesisError(
            f"epoch_start schema mismatch: missing={sorted(EPOCH_START_KEYS - set(body))} "
            f"unknown={sorted(set(body) - EPOCH_START_KEYS)}"
        )
    if canonical.self_digest(body) != body["digest"]:
        raise GenesisError("epoch_start self-digest mismatch")
    plans = auth.plans
    expected = {
        "kind": EPOCH_START_KIND,
        "schema_version": SCHEMA_VERSION,
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "protocol_sha256": frozen_v3.PROTOCOL_SHA256,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
        "epoch_id": frozen_v3.EPOCH_ID,
        "mode": auth.mode,
        "execution_root": auth.execution_root,
        "root_identity": root_ctx.identity.as_record(),
        "implementation_commit": auth.implementation_commit,
        "child_manifest_digest": auth.child_manifest_digest,
        "m_phase_p_plan_digest": plans["M"].digest,
        "t_phase_p_plan_digest": plans["T"].digest,
        "authorization_digest": auth.digest,
        "operator_approval_digest": approval.digest,
        "operator_identity": approval.operator_identity,
        "code_hashes": dict(auth.code_hashes),
        "environment_identity": dict(auth.environment),
        "resource_caps": {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS},
        "resource_caps_digest": frozen_v3.RESOURCE_CAPS_DIGEST,
        "p_ceilings": {
            arm: {
                "arm": dict(p.arm_ceiling),
                "per_file": {f.file: dict(f.ceiling) for f in p.files},
                "future_d_reserve": {f.file: dict(f.d_reserve) for f in p.files},
            }
            for arm, p in plans.items()
        },
        "supervision_identity": supervision_identity(auth.mode, sampling_interval_s),
        "initial_ledgers": {"M": dict(ZERO_LEDGER), "T": dict(ZERO_LEDGER)},
        "claim_sha256": hashlib.sha256(claim_raw).hexdigest(),
        "network_events_before_genesis": 0,
    }
    for key, want in expected.items():
        if canonical.canonical_bytes(body[key]) != canonical.canonical_bytes(want):
            raise GenesisError(f"epoch_start.{key} does not match the validated binding")
    if type(body["genesis_utc"]) is not str or not _UTC.match(body["genesis_utc"]):
        raise GenesisError("epoch_start.genesis_utc malformed")
    baseline = body["runtime_baseline"]
    if (
        not isinstance(baseline, dict)
        or set(baseline) != {"monotonic_ns", "active_ns"}
        or type(baseline["monotonic_ns"]) is not int
        or baseline["active_ns"] != {"M": 0, "T": 0}
    ):
        raise GenesisError("epoch_start.runtime_baseline malformed")
    inventory = body["initial_inventory"]
    lock_raw_sha = hashlib.sha256(b"").hexdigest()
    if inventory != {
        CLAIM_REL: {"size": len(claim_raw), "sha256": hashlib.sha256(claim_raw).hexdigest()},
        LOCK_REL: {"size": 0, "sha256": lock_raw_sha},
    }:
        raise GenesisError(
            "epoch_start.initial_inventory does not match the physical control files"
        )
    if not isinstance(claim, dict) or set(claim) != CLAIM_KEYS or claim["kind"] != CLAIM_KIND:
        raise GenesisError("genesis claim schema mismatch")
    if claim["epoch_id"] != frozen_v3.EPOCH_ID:
        raise GenesisError("genesis claim belongs to another epoch")
    if claim["root_identity"] != root_ctx.identity.as_record():
        raise GenesisError("genesis claim belongs to another physical directory")
    if (
        claim["authorization_digest"] != auth.digest
        or claim["operator_approval_digest"] != approval.digest
    ):
        raise GenesisError("genesis claim binds a different authorization/approval")
    return body
