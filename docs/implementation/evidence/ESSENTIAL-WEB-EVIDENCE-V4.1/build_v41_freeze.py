"""Reproduce the Essential-Web evidence-v4.1 host-amendment freeze (stdlib only).

This is reproduction tooling for the v4.1 protocol/freeze commit, not the
fetcher. No network access, no corpus text, no execution root. It:

1. verifies the committed v4.0 plan (canonical bytes, self-digest, frozen
   digest, and blob equality with the v4.0 freeze commit);
2. derives the v4.1 plan by replacing ONLY ``protocol_version``,
   ``execution_root`` and ``network`` (the exact expanded host set) and
   re-sealing; files, operations, ranges, bindings and limits are copied
   byte-for-byte, and their shared digest is recomputed for both plans;
3. writes ``v40_live_observation.json`` (the operator-reported STOPPED v4.0
   live result, recorded verbatim with the fields derivable from it);
4. in ``freeze`` mode, when the protocol states the plan and membership
   digests, writes ``freeze.json`` and ``verification.json``.

Usage:  uv run --offline --locked python <this file> plan|freeze
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
OUT_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1"
OUT = REPO / OUT_REL
PROTOCOL_REL = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md"

PROTOCOL_VERSION = "essential-web-evidence-v4.1"
FROZEN_DATE = "2026-09-29"
EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4.1\\essential-web"

V40_DIR = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0"
V40_PLAN_REL = f"{V40_DIR}/phase_p_plan.json"
V40_FREEZE_REL = f"{V40_DIR}/freeze.json"
V40_ADOPTION_REL = f"{V40_DIR}/scientific_adoption.json"
V40_PROTOCOL_REL = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md"
V40_PROTOCOL_VERSION = "essential-web-evidence-v4.0"
V40_PROTOCOL_SHA256 = "4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727"
V40_FREEZE_DIGEST = "747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57"
V40_PLAN_DIGEST = "16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3"
V40_ADOPTION_DIGEST = "158c3fc9fed030d0290aa50eded70f7d94b162ab6186ae38aca2ce6b1ebdffd3"
V40_IDENTITY_DIGEST = "080caebb962c86562b530ddfa6a130118a49a6d207b044e83ce6a90a6d9691c6"
V40_EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4\\essential-web"
V40_FREEZE_COMMIT = "2ede38f21d4b9d9ba45989eec812213ce9851bee"
V40_IMPLEMENTATION_COMMIT = "791d3b8"
V40_REVIEW_COMMIT = "55d45f4"
V40_REPAIR_COMMIT = "a9f89c0d13ef01e9ab387104c4acd4da948d2366"
V40_RECERTIFICATION_COMMIT = "21266bf7280194a5a548be6f8b58137a3c84c92e"
V40_COMMAND = (
    "uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v4.py "
    f"phase-p --confirm-plan-digest {V40_PLAN_DIGEST}"
)

SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
SOURCE_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"

CANONICAL_HOST = "huggingface.co"
PRIOR_SIGNED_TARGET_HOST = "cas-bridge.xethub.hf.co"
# Exactly the lfsDomains then cdnDomains of https://huggingface.co/.well-known/meta.json
# as transcribed by the operator; not fetched by this tooling (no network).
LFS_DOMAINS = [
    "cdn-lfs.hf.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "transfer.xethub.hf.co",
    "transfer.xethub-eu.hf.co",
]
CDN_DOMAINS = [
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
]
ADDED_HOSTS = LFS_DOMAINS + CDN_DOMAINS
SIGNED_TARGET_HOSTS = [PRIOR_SIGNED_TARGET_HOST, *ADDED_HOSTS]
NETWORK: dict[str, Any] = {
    "method": "GET",
    "scheme": "https",
    "port": 443,
    "hosts": [CANONICAL_HOST, *SIGNED_TARGET_HOSTS],
    "canonical_host": CANONICAL_HOST,
    "signed_target_hosts": SIGNED_TARGET_HOSTS,
    "host_matching": "exact string equality of the urlsplit hostname; no suffix, wildcard or pattern",
    "redirect_statuses": [301, 302, 303, 307, 308],
    "request_headers": {"Accept-Encoding": "identity", "Range": "bytes=<start>-<end>"},
    "credentials_sent": False,
}
AMENDED_PLAN_KEYS = ["execution_root", "network", "protocol_version"]


def cbytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def H(value: Any) -> str:
    return hashlib.sha256(cbytes(value)).hexdigest()


def self_digest(body: dict[str, Any]) -> str:
    return H({k: v for k, v in body.items() if k != "digest"})


def seal(body: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in body.items() if k != "digest"}
    body["digest"] = H(body)
    return body


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"build_v41_freeze: REFUSED: {message}")


def membership_and_ranges_digest(plan: dict[str, Any]) -> str:
    """H of the plan without its execution-version fields: files, ranges, bindings, limits."""
    return H({k: v for k, v in plan.items() if k not in (*AMENDED_PLAN_KEYS, "digest")})


def load(rel: str, digest: str) -> tuple[bytes, dict[str, Any]]:
    raw = (REPO / rel).read_bytes()
    obj = json.loads(raw.decode("utf-8"))
    if cbytes(obj) != raw or self_digest(obj) != obj["digest"] or obj["digest"] != digest:
        fail(f"{rel} is not canonical or its self-digest differs from {digest}")
    return raw, obj


def git_blob_sha256(commit: str, rel: str) -> str:
    raw = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{rel}"], capture_output=True, check=True
    ).stdout
    return sha(raw)


def write(name: str, body: dict[str, Any]) -> dict[str, Any]:
    raw = cbytes(body)
    target = OUT / name
    tmp = target.with_name(name + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)
    return {"path": f"{OUT_REL}/{name}", "bytes": len(raw), "sha256": sha(raw), "digest": body["digest"]}


def build_plan(v40: dict[str, Any]) -> dict[str, Any]:
    if v40["protocol_version"] != V40_PROTOCOL_VERSION or v40["execution_root"] != V40_EXECUTION_ROOT:
        fail("v4.0 plan version/root differ from the recorded parent")
    plan = {k: v for k, v in v40.items() if k != "digest"}
    plan["protocol_version"] = PROTOCOL_VERSION
    plan["execution_root"] = EXECUTION_ROOT
    plan["network"] = NETWORK
    sealed = seal(plan)
    changed = sorted(k for k in sealed if k != "digest" and sealed[k] != v40[k])
    if changed != AMENDED_PLAN_KEYS or set(sealed) != set(v40):
        fail(f"v4.1 plan changes {changed}, expected exactly {AMENDED_PLAN_KEYS}")
    if membership_and_ranges_digest(sealed) != membership_and_ranges_digest(v40):
        fail("membership/range digest differs between v4.0 and v4.1")
    return sealed


def build_observation() -> dict[str, Any]:
    return seal(
        {
            "kind": "essential_web_v4_0_live_observation",
            "record_status": "STOPPED_POLICY_REFUSED",
            "protocol_version": V40_PROTOCOL_VERSION,
            "plan_digest": V40_PLAN_DIGEST,
            "execution_root": V40_EXECUTION_ROOT,
            "command": V40_COMMAND,
            "source_of_record": (
                "operator-reported result of the first real v4.0 phase-p invocation; the root is "
                "on the operator's machine and was not read, hashed or modified by this tooling"
            ),
            "reported": {
                "status": "INCOMPLETE",
                "run_status": "STOPPED",
                "stop_reason": "M-00-head: host 'us.aws.cdn.hf.co' is not an exact allowlisted host",
                "totals": {
                    "M": {
                        "logical_operations": 16,
                        "logical_complete": 0,
                        "physical_attempts": 1,
                        "response_body_bytes": 1098,
                        "attempts_by_outcome": {"POLICY_REFUSED": 1},
                        "retained_payload_bytes": 0,
                    },
                    "T": {
                        "logical_operations": 24,
                        "logical_complete": 0,
                        "physical_attempts": 0,
                        "response_body_bytes": 0,
                        "retained_payload_bytes": 0,
                    },
                },
            },
            "derived_from_v4_0_engine_code": {
                "attempt": "M-00-head, try 1, hop 0, canonical huggingface.co resource, range 0-3",
                "path": (
                    "the only engine path that records POLICY_REFUSED with body bytes on a single "
                    "attempt is a redirect response whose Location failed check_url in "
                    "resolve_redirect; no request was ever sent to us.aws.cdn.hf.co"
                ),
                "body": (
                    "the 1098 bytes are the body of the non-206 origin redirect response, streamed "
                    "to tmp/M-00-head.a1.part, never parsed; they are not Parquet bytes"
                ),
            },
            "scientific_exposure": {
                "phase_p_operations_completed": 0,
                "retained_payload_files": 0,
                "parquet_bytes_received": 0,
                "M_footer_or_layout_observed": False,
                "M_selected_outcomes_observed": False,
                "T_requests": 0,
                "T_selected_text_observed": False,
            },
            "disposition": (
                "v4.0 is NOT successful; its root is historical and must not be deleted, "
                "overwritten or reused"
            ),
        }
    )


def environment() -> dict[str, Any]:
    try:
        uv = subprocess.run(["uv", "--version"], capture_output=True, text=True).stdout.strip()
    except OSError:
        uv = "NOT AVAILABLE"
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "uv": uv,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("plan", "freeze"):
        fail("usage: build_v41_freeze.py plan|freeze")
    v40_raw, v40 = load(V40_PLAN_REL, V40_PLAN_DIGEST)
    if git_blob_sha256(V40_FREEZE_COMMIT, V40_PLAN_REL) != sha(v40_raw):
        fail("v4.0 plan differs from its freeze commit")
    _, v40_freeze = load(V40_FREEZE_REL, V40_FREEZE_DIGEST)
    load(V40_ADOPTION_REL, V40_ADOPTION_DIGEST)
    v40_protocol = (REPO / V40_PROTOCOL_REL).read_bytes()
    if sha(v40_protocol) != V40_PROTOCOL_SHA256 or v40_freeze["protocol"]["sha256"] != V40_PROTOCOL_SHA256:
        fail("v4.0 protocol SHA-256 differs from the parent freeze")
    if v40_freeze["phase_p_plan"]["digest"] != V40_PLAN_DIGEST:
        fail("v4.0 freeze does not bind the v4.0 plan")
    plan = build_plan(v40)
    observation = build_observation()
    plan_binding = write("phase_p_plan.json", plan)
    observation_binding = write("v40_live_observation.json", observation)
    shared = membership_and_ranges_digest(plan)
    print(f"plan digest                  {plan['digest']}")
    print(f"membership/ranges digest     {shared}")
    print(f"v4.0 observation digest      {observation['digest']}")
    if mode == "plan":
        return 0
    protocol_raw = (REPO / PROTOCOL_REL).read_bytes()
    text = protocol_raw.decode("utf-8")
    for needle in (plan["digest"], shared, observation["digest"], V40_PLAN_DIGEST, EXECUTION_ROOT):
        if needle not in text:
            fail(f"protocol does not state {needle}")
    for host in ADDED_HOSTS:
        if f"`{host}`" not in text:
            fail(f"protocol does not list host {host}")
    ops = plan["operations"]
    freeze = seal(
        {
            "kind": "essential_web_evidence_v4_1_freeze",
            "protocol_version": PROTOCOL_VERSION,
            "frozen_date": FROZEN_DATE,
            "status": "FROZEN_READY_TO_IMPLEMENT",
            "amendment": "transport-host only: exact expanded Hugging Face storage/CDN host set plus a fresh execution version/root",
            "protocol": {"path": PROTOCOL_REL, "bytes": len(protocol_raw), "sha256": sha(protocol_raw)},
            "parent": {
                "protocol_version": V40_PROTOCOL_VERSION,
                "protocol": {"path": V40_PROTOCOL_REL, "sha256": V40_PROTOCOL_SHA256},
                "freeze": {"path": V40_FREEZE_REL, "digest": V40_FREEZE_DIGEST},
                "phase_p_plan": {"path": V40_PLAN_REL, "digest": V40_PLAN_DIGEST, "sha256": sha(v40_raw)},
                "scientific_adoption": {"path": V40_ADOPTION_REL, "digest": V40_ADOPTION_DIGEST},
                "commits": {
                    "freeze": V40_FREEZE_COMMIT,
                    "implementation": V40_IMPLEMENTATION_COMMIT,
                    "narrow_review": V40_REVIEW_COMMIT,
                    "b01_b02_repair": V40_REPAIR_COMMIT,
                    "final_recertification": V40_RECERTIFICATION_COMMIT,
                },
                "execution_root": V40_EXECUTION_ROOT,
                "live_status": "STOPPED_POLICY_REFUSED",
                "root_disposition": "historical; never deleted, overwritten or reused by v4.1",
            },
            "v40_live_observation": observation_binding,
            "phase_p_plan": plan_binding,
            "plan_digest_relation": {
                "v4_0_plan_digest": V40_PLAN_DIGEST,
                "v4_1_plan_digest": plan["digest"],
                "digests_differ_because": AMENDED_PLAN_KEYS,
                "membership_and_ranges_digest": shared,
                "membership_and_ranges_digest_definition": "H(plan without digest, execution_root, network, protocol_version); identical for v4.0 and v4.1",
            },
            "scientific_identity": {
                "unchanged": True,
                "scientific_namespace": SCIENTIFIC_NAMESPACE,
                "selection_digest": SELECTION_DIGEST,
                "source_revision": SOURCE_REVISION,
                "policy_digest": POLICY_DIGEST,
                "scientific_identity_digest": V40_IDENTITY_DIGEST,
                "scientific_adoption_digest": V40_ADOPTION_DIGEST,
                "adopted_by_reference": V40_ADOPTION_REL,
            },
            "operation_counts": {
                "M_logical": sum(1 for o in ops if o["arm"] == "M"),
                "T_logical": sum(1 for o in ops if o["arm"] == "T"),
                "T_derived_after_trailer": sum(1 for o in ops if o["range"] is None),
            },
            "host_policy": {
                "canonical_host": CANONICAL_HOST,
                "prior_signed_target_host": PRIOR_SIGNED_TARGET_HOST,
                "added_hosts": ADDED_HOSTS,
                "allowed_hosts": NETWORK["hosts"],
                "allowed_host_count": len(NETWORK["hosts"]),
                "scheme": "https",
                "port": 443,
                "matching": NETWORK["host_matching"],
                "added_hosts_provenance": "lfsDomains then cdnDomains of https://huggingface.co/.well-known/meta.json as transcribed by the operator on 2026-09-29; not re-fetched by this tooling",
                "signed_target_semantics": "unchanged from v4.0: opaque path reached only through the validated redirect chain from the canonical origin; identity proven by 206, exact Content-Range, frozen length, frozen strong ETag, exact body length and PAR1",
            },
            "operational_limits": plan["limits"],
            "network": NETWORK,
            "execution_root": EXECUTION_ROOT,
            "unchanged": [
                "scientific membership", "ranges", "parsing", "accounting", "timeout", "retry behaviour",
                "operational caps", "Phase-P logic", "B01 partial-byte accounting", "B02 absolute deadline",
            ],
            "phase_d": "NOT AUTHORIZED; requires a separate future freeze and review",
            "current_network_requests": 0,
            "current_corpus_text_bytes_read": 0,
        }
    )
    freeze_binding = write("freeze.json", freeze)
    verification = seal(
        {
            "kind": "essential_web_evidence_v4_1_freeze_verification",
            "protocol_version": PROTOCOL_VERSION,
            "freeze_digest": freeze["digest"],
            "plan_digest": plan["digest"],
            "membership_and_ranges_digest": shared,
            "protocol_sha256": sha(protocol_raw),
            "command": f"uv run --offline --locked python {OUT_REL}/build_v41_freeze.py freeze",
            "checks": {
                "v4_0_plan_canonical_self_digest_and_freeze_commit_blob": "VERIFIED",
                "v4_0_freeze_binds_v4_0_protocol_and_plan": "VERIFIED",
                "v4_1_plan_differs_only_in": AMENDED_PLAN_KEYS,
                "membership_and_ranges_digest_equal_v4_0_v4_1": "VERIFIED",
                "protocol_states_digests_root_and_every_added_host": "VERIFIED",
            },
            "environment": environment(),
            "network_requests": 0,
            "corpus_text_bytes_read": 0,
            "tests": "NOT RUN in the freeze commit; the implementation commit carries tests",
        }
    )
    write("verification.json", verification)
    print(f"protocol sha256              {sha(protocol_raw)}")
    print(f"freeze digest                {freeze['digest']}")
    print(f"freeze sha256                {freeze_binding['sha256']}")
    print(f"verification digest          {verification['digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
