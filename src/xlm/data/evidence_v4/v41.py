"""Frozen v4.1 transport-host amendment: constants, profile and offline verifier.

Values are transcribed from the v4.1 protocol/freeze commit (protocol
``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md``).
v4.1 reuses the v4.0 engine, plan grammar, limits and scientific identity
unchanged. It differs from v4.0 in exactly three plan fields:
``protocol_version``, ``execution_root`` and ``network``, whose only
substantive change is the exact expanded host set below. Changing any value
here is a new amendment, never an edit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, verify

PROTOCOL_VERSION = "essential-web-evidence-v4.1"
FREEZE_COMMIT = "cb3014d8da988cab8a84843c87d4eb0c9766571d"
PROTOCOL_SHA256 = "3d667a263b92696a2d7a266f896e460b10a81ae38097f788c8b885ce768079cf"
FREEZE_DIGEST = "285015604d30e1699b5f63fa4f25ec9c747c778be2f372bb87119adc0e455f80"
PLAN_DIGEST = "762cef78051011d518c4918a41aa87cf81fbb3224bf438af8cc1c499cfca7711"
MEMBERSHIP_AND_RANGES_DIGEST = "304bd0761125b48720a4de9c82bb6b3bf47100b8a15fc0953d548a8a1dab38bd"
OBSERVATION_DIGEST = "01799f6f6c0edf1cdc104fcb175251c4fd0a431700800215e41bc9e6831e5544"

EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4.1\\essential-web"

EVIDENCE_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1"
PLAN_PATH = f"{EVIDENCE_DIR}/phase_p_plan.json"
FREEZE_PATH = f"{EVIDENCE_DIR}/freeze.json"
OBSERVATION_PATH = f"{EVIDENCE_DIR}/v40_live_observation.json"
PROTOCOL_PATH = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md"

# The v4.0 plan fields v4.1 replaces; everything else is byte-identical.
AMENDED_PLAN_KEYS = ("execution_root", "network", "protocol_version")

# lfsDomains then cdnDomains of https://huggingface.co/.well-known/meta.json
# (operator transcription, 2026-09-29). Exact names; no suffix or wildcard.
ADDED_HOSTS: tuple[str, ...] = (
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "transfer.xethub.hf.co",
    "transfer.xethub-eu.hf.co",
    "aws.cdn.hf.co",
    "us.aws.cdn.hf.co",
    "us-east-1.aws.cdn.hf.co",
    "us-west-2.aws.cdn.hf.co",
    "eu-west-3.aws.cdn.hf.co",
    "ap-southeast-1.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "us-east1.us.gcp.cdn.hf.co",
    "us-central1.us.gcp.cdn.hf.co",
    "us-west4.us.gcp.cdn.hf.co",
    "europe-west4.us.gcp.cdn.hf.co",
    "asia-southeast1.us.gcp.cdn.hf.co",
)
HOSTS = frozen.HostPolicy(frozen.CANONICAL_HOST, (frozen.SIGNED_TARGET_HOST, *ADDED_HOSTS))

NETWORK: dict[str, Any] = {
    "method": "GET",
    "scheme": "https",
    "port": frozen.ALLOWED_PORT,
    "hosts": list(HOSTS.hosts),
    "canonical_host": HOSTS.canonical_host,
    "signed_target_hosts": list(HOSTS.signed_target_hosts),
    "host_matching": (
        "exact string equality of the urlsplit hostname; no suffix, wildcard or pattern"
    ),
    "redirect_statuses": list(frozen.REDIRECT_STATUSES),
    "request_headers": {"Accept-Encoding": "identity", "Range": "bytes=<start>-<end>"},
    "credentials_sent": False,
}

PROFILE = frozen.Profile(
    label="v4.1",
    protocol_version=PROTOCOL_VERSION,
    protocol_sha256=PROTOCOL_SHA256,
    freeze_digest=FREEZE_DIGEST,
    plan_digest=PLAN_DIGEST,
    plan_path=PLAN_PATH,
    execution_root=EXECUTION_ROOT,
    network=NETWORK,
    hosts=HOSTS,
)


def load_committed_plan() -> frozen.Plan:
    """THE frozen v4.1 plan (fixed path, exact digest, v4.1 profile)."""
    return frozen.load_committed_plan(PROFILE)


def membership_and_ranges_digest(plan_obj: dict[str, Any]) -> str:
    """H of a plan without its execution-version fields: files, ranges, bindings, limits."""
    return canonical.digest(
        {k: v for k, v in plan_obj.items() if k not in (*AMENDED_PLAN_KEYS, "digest")}
    )


def _strict(rel: str) -> tuple[bytes, dict[str, Any]]:
    raw = (frozen.REPO_ROOT / rel).read_bytes()
    try:
        obj = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise verify.VerifyError(f"{rel}: {exc}") from exc
    if not isinstance(obj, dict) or canonical.self_digest(obj) != obj.get("digest"):
        raise verify.VerifyError(f"{rel}: self-digest mismatch")
    return raw, obj


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise verify.VerifyError(message)


def verify_repository() -> dict[str, Any]:
    """Recompute every v4.1 binding offline; the v4.0 parent must verify first."""
    parent = verify.verify_repository()  # v4.0 freeze and exact scientific adoption
    protocol = (frozen.REPO_ROOT / PROTOCOL_PATH).read_bytes()
    protocol_sha = hashlib.sha256(protocol).hexdigest()
    _require(protocol_sha == PROTOCOL_SHA256, f"v4.1 protocol bytes drift: {protocol_sha}")
    _, freeze = _strict(FREEZE_PATH)
    plan_raw, plan_obj = _strict(PLAN_PATH)
    observation_raw, observation = _strict(OBSERVATION_PATH)
    _, parent_plan = _strict(frozen.PLAN_PATH)
    _require(freeze["digest"] == FREEZE_DIGEST, "v4.1 freeze digest drift")
    _require(freeze["protocol"]["sha256"] == protocol_sha, "freeze does not bind the protocol")
    for key, raw, digest in (
        ("phase_p_plan", plan_raw, PLAN_DIGEST),
        ("v40_live_observation", observation_raw, OBSERVATION_DIGEST),
    ):
        binding = freeze[key]
        _require(
            binding["sha256"] == hashlib.sha256(raw).hexdigest()
            and binding["bytes"] == len(raw)
            and binding["digest"] == digest,
            f"freeze binding for {key} does not reproduce",
        )
    bound_parent = freeze["parent"]
    _require(
        (
            bound_parent["protocol"]["sha256"],
            bound_parent["freeze"]["digest"],
            bound_parent["phase_p_plan"]["digest"],
            bound_parent["scientific_adoption"]["digest"],
        )
        == (
            parent["protocol_sha256"],
            parent["freeze_digest"],
            parent["plan_digest"],
            parent["scientific_adoption_digest"],
        ),
        "v4.1 freeze does not bind the verified v4.0 parent",
    )
    changed = sorted(k for k in plan_obj if k != "digest" and plan_obj[k] != parent_plan.get(k))
    _require(
        set(plan_obj) == set(parent_plan) and tuple(changed) == AMENDED_PLAN_KEYS,
        f"v4.1 plan changes {changed}, not exactly {list(AMENDED_PLAN_KEYS)}",
    )
    shared = membership_and_ranges_digest(plan_obj)
    _require(
        shared == membership_and_ranges_digest(parent_plan) == MEMBERSHIP_AND_RANGES_DIGEST,
        "membership/range digest differs from v4.0",
    )
    _require(
        freeze["network"] == NETWORK
        and freeze["operational_limits"] == frozen.LIMITS
        and freeze["execution_root"] == EXECUTION_ROOT
        and freeze["plan_digest_relation"]["membership_and_ranges_digest"] == shared,
        "v4.1 freeze network/limits/root drift",
    )
    _require(
        freeze["scientific_identity"]["scientific_identity_digest"]
        == frozen.SCIENTIFIC_IDENTITY_DIGEST
        and freeze["scientific_identity"]["selection_digest"] == frozen.SELECTION_DIGEST,
        "v4.1 freeze scientific identity drift",
    )
    _require(
        observation["record_status"] == "STOPPED_POLICY_REFUSED"
        and observation["plan_digest"] == frozen.PLAN_DIGEST
        and observation["execution_root"] == frozen.EXECUTION_ROOT,
        "v4.0 live observation record drift",
    )
    _require(EXECUTION_ROOT != frozen.EXECUTION_ROOT, "v4.1 must not reuse the v4.0 root")
    plan = load_committed_plan()
    ops = plan.operations
    return {
        "protocol_version": PROTOCOL_VERSION,
        "protocol_sha256": protocol_sha,
        "freeze_digest": freeze["digest"],
        "plan_digest": plan.digest,
        "membership_and_ranges_digest": shared,
        "parent_v4_0": parent,
        "v4_0_live_status": observation["record_status"],
        "amended_plan_keys": list(AMENDED_PLAN_KEYS),
        "allowed_hosts": list(HOSTS.hosts),
        "M_logical_operations": sum(1 for o in ops if o.arm == "M"),
        "T_logical_operations": sum(1 for o in ops if o.arm == "T"),
        "execution_root": EXECUTION_ROOT,
        "execution_root_exists": Path(EXECUTION_ROOT).exists(),
    }
