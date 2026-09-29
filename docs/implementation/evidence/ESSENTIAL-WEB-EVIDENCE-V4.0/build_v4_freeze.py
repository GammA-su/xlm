"""Reproduce the Essential-Web evidence-v4.0 freeze artifacts (stdlib only).

This is reproduction tooling for the protocol/freeze commit, not the v4
fetcher. It performs no network access and reads no corpus text. It:

1. verifies the committed v3.0 freeze (self-digest) and v3.0 CHILD Phase-P
   dry plans (self-digests + CHILD manifest bytes/SHA-256);
2. derives the v4.0 Phase-P plan mechanically from the v3.0 freeze's
   ``M_prospective_files`` / ``T_prospective_files`` and cross-checks every
   file, length, ETag, binding and range against the v3.0 CHILD dry plans;
3. writes ``phase_p_plan.json`` and ``scientific_adoption.json``;
4. when the protocol document exists, writes ``freeze.json`` and
   ``verification.json`` binding the protocol SHA-256 and all digests.

External v2.0 artifacts (selection manifest, M footer observations, T cost
map) are hash-verified read-only when present at their original G: path or
the documented F: relocation; only structural counts are derived from the
selection manifest (locator count, file set) and nothing else is printed.

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
OUT = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0"
OUT_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0"
PROTOCOL_REL = "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md"
V3_FREEZE_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0/freeze.json"
V3_CHILD_REL = "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD"
V3_IDENTITY_CHILD = "scientific_identity_adoption.json"

PROTOCOL_VERSION = "essential-web-evidence-v4.0"
FROZEN_DATE = "2026-09-29"
EXECUTION_ROOT = "G:\\Project\\xlm-evidence-v4\\essential-web"

V3_FREEZE_DIGEST = "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
V3_PROTOCOL_SHA256 = "c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79"
V3_FREEZE_COMMIT = "52569c525a6613faab096d17b60167b4aaa0f214"
V3_IMPLEMENTATION_COMMIT = "3dd5ebce0edb7d8e676966c9195b73fb5a1978c9"
V3_CHILDREN_COMMIT = "12d85158206378c0d6833d95df9b9d8fa5789761"
V3_REVIEW3_COMMIT = "60ed59c5078114dd54297b8923c45495014f7874"
V2_LINEAGE_CLOSURE_DIGEST = "ceaa2fbc6c0f638d333b3e08fd1591d2619bfe645c75ce7028c0e2bae96dda9b"

SCIENTIFIC_NAMESPACE = "essential-web-evidence-v2.0"
SELECTION_DIGEST = "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
SELECTION_SHA256 = "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
SOURCE_REVISION = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
POLICY_DIGEST = "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
M_OBSERVATION_DIGEST = "2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052"
T_COSTMAP_DIGEST = "ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b"
PROJECTION = ["eai_taxonomy", "quality_signals"]

LIMITS: dict[str, Any] = {
    "physical_attempts_per_arm_max": 200,
    "response_body_bytes_per_arm_max": 67108864,
    "response_body_bytes_per_response_max": 4194304,
    "overflow_detection_bytes": 1,
    "redirect_transitions_per_logical_request_max": 3,
    "additional_attempts_per_operation_max": 2,
    "retry_delays_seconds": [1, 2],
    "retryable_http_statuses": [429, 500, 502, 503, 504],
    "physical_attempt_timeout_seconds": 120,
    "execution_root_bytes_max": 268435456,
    "free_disk_bytes_min": 1073741824,
    "byte_count_flush_interval_bytes": 1048576,
    "parquet_thrift_limit_bytes": 33554432,
}
NETWORK: dict[str, Any] = {
    "method": "GET",
    "scheme": "https",
    "port": 443,
    "hosts": ["huggingface.co", "cas-bridge.xethub.hf.co"],
    "canonical_host": "huggingface.co",
    "signed_target_host": "cas-bridge.xethub.hf.co",
    "redirect_statuses": [301, 302, 303, 307, 308],
    "request_headers": {"Accept-Encoding": "identity", "Range": "bytes=<start>-<end>"},
    "credentials_sent": False,
}
SOURCE = {
    "host": "huggingface.co",
    "repository_type": "datasets",
    "repository": "EssentialAI/essential-web-v1.0",
    "revision": SOURCE_REVISION,
}
EXTERNAL_DIRS = [
    Path("G:/Project/xlm-evidence-v2/essential-web"),
    Path("F:/Project/xlm-evidence-v2/essential-web"),
]


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


def load_json(rel: str) -> tuple[bytes, Any]:
    raw = (REPO / rel).read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def fail(message: str) -> None:
    raise SystemExit(f"build_v4_freeze: REFUSED: {message}")


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


def git_blob_sha256(commit: str, rel: str) -> str:
    raw = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{rel}"], capture_output=True, check=True
    ).stdout
    return sha(raw)


def v3_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    raw, freeze = load_json(V3_FREEZE_REL)
    if self_digest(freeze) != freeze["digest"] or freeze["digest"] != V3_FREEZE_DIGEST:
        fail("v3.0 freeze.json self-digest mismatch")
    if git_blob_sha256(V3_FREEZE_COMMIT, V3_FREEZE_REL) != sha(raw):
        fail("v3.0 freeze.json differs from its frozen commit")
    protocol = (REPO / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md")
    if sha(protocol.read_bytes()) != V3_PROTOCOL_SHA256:
        fail("v3.0 protocol SHA-256 mismatch")
    _, manifest = load_json(f"{V3_CHILD_REL}/artifact_manifest.json")
    if self_digest(manifest) != manifest["digest"]:
        fail("v3.0 CHILD manifest self-digest mismatch")
    children: dict[str, Any] = {}
    for name in ("arm_m_phase_p_dry.json", "arm_t_phase_p_dry.json", V3_IDENTITY_CHILD):
        child_raw, child = load_json(f"{V3_CHILD_REL}/{name}")
        entry = manifest["payload"]["artifacts"][name]
        if entry["sha256"] != sha(child_raw) or entry["bytes"] != len(child_raw):
            fail(f"{name} bytes differ from the v3.0 CHILD manifest")
        if self_digest(child) != child["digest"] or entry["canonical_digest"] != child["digest"]:
            fail(f"{name} self-digest mismatch")
        if git_blob_sha256(V3_CHILDREN_COMMIT, f"{V3_CHILD_REL}/{name}") != sha(child_raw):
            fail(f"{name} differs from the v3.0 children commit")
        children[name] = child
    return freeze, children["arm_m_phase_p_dry.json"], children["arm_t_phase_p_dry.json"], {
        "identity": children[V3_IDENTITY_CHILD],
        "manifest_digest": manifest["digest"],
    }


def build_plan(freeze: dict[str, Any], m_dry: dict[str, Any], t_dry: dict[str, Any]) -> dict[str, Any]:
    science = freeze["scientific_identity"]
    windows = {w["file"]: w["window"] for w in science["M_windows"]}
    files: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    m_rows = freeze["M_prospective_files"]
    t_rows = freeze["T_prospective_files"]
    if len(m_rows) != 8 or len(t_rows) != 8:
        fail("v3.0 freeze must list exactly 8 M and 8 T files")
    if [r["file"] for r in m_rows] != [w["file"] for w in science["M_windows"]]:
        fail("M prospective files differ from the scientific M windows")
    for ordinal, row in enumerate(m_rows):
        n = row["remote_length"]
        (h0, h1), (f0, f1) = row["phase_P_ranges_inclusive"]
        if (h0, h1) != (0, 3) or f1 != n - 1:
            fail(f"M {row['file']}: unexpected Phase-P ranges")
        footer_length = n - 8 - f0
        if row["window"] != windows[row["file"]] or row["window"][1] - row["window"][0] != 512:
            fail(f"M {row['file']}: window drift")
        if row["phase_P_payload_bytes"] != 4 + (f1 - f0 + 1):
            fail(f"M {row['file']}: Phase-P payload arithmetic mismatch")
        dry = m_dry["payload"]["files"][ordinal]
        bindings = {
            "window": row["window"],
            "projection": PROJECTION,
            "data_chunk_count": row["data_chunk_count"],
            "data_payload_bytes": row["data_payload_bytes"],
            "data_uncompressed_bytes": row["data_uncompressed_bytes"],
        }
        if (
            dry["file"] != row["file"]
            or dry["remote_length"] != n
            or dry["strong_etag"] != row["etag"]
            or dry["bindings"] != bindings
            or dry["operations"]
            != [
                {"op": "IDENTITY_HEAD_0_3", "range": [0, 3]},
                {"expected_footer_length": footer_length, "op": "M_FOOTER_AND_TRAILER", "range": [f0, f1]},
            ]
        ):
            fail(f"M {row['file']}: v3.0 CHILD dry plan disagrees with the v3.0 freeze")
        files.append(
            {
                "arm": "M",
                "ordinal": ordinal,
                "file": row["file"],
                "remote_length": n,
                "strong_etag": row["etag"],
                "bindings": bindings,
            }
        )
        operations.append(
            {"op_id": f"M-{ordinal:02d}-head", "arm": "M", "ordinal": ordinal, "kind": "HEAD_0_3", "range": [0, 3]}
        )
        operations.append(
            {
                "op_id": f"M-{ordinal:02d}-footer",
                "arm": "M",
                "ordinal": ordinal,
                "kind": "M_FOOTER_AND_TRAILER",
                "range": [f0, f1],
                "expected_footer_length": footer_length,
            }
        )
    for ordinal, row in enumerate(t_rows):
        n = row["remote_length"]
        ranges = row["data_ranges_half_open"]
        span = [ranges[0]["start"], ranges[-1]["end"]]
        if sum(r["bytes"] for r in ranges) != row["data_payload_bytes"] or span[1] - span[0] != row["data_payload_bytes"]:
            fail(f"T {row['file']}: data span arithmetic mismatch")
        if row["phase_P_range_pattern"] != ["0-3", "N-8..N-1", "N-8-L..N-9 after trailer establishes L"]:
            fail(f"T {row['file']}: unexpected Phase-P range pattern")
        bindings = {
            "text_column": "text",
            "data_span_half_open": span,
            "data_range_count": row["data_range_count"],
            "data_payload_bytes": row["data_payload_bytes"],
        }
        dry = t_dry["payload"]["files"][ordinal]
        if (
            dry["file"] != row["file"]
            or dry["remote_length"] != n
            or dry["strong_etag"] != row["etag"]
            or dry["bindings"] != bindings
            or dry["operations"]
            != [
                {"op": "IDENTITY_HEAD_0_3", "range": [0, 3]},
                {"op": "T_TRAILER", "range": [n - 8, n - 1]},
                {"op": "T_FOOTER_FROM_TRAILER", "range": None},
            ]
        ):
            fail(f"T {row['file']}: v3.0 CHILD dry plan disagrees with the v3.0 freeze")
        files.append(
            {
                "arm": "T",
                "ordinal": ordinal,
                "file": row["file"],
                "remote_length": n,
                "strong_etag": row["etag"],
                "bindings": bindings,
            }
        )
        operations.append(
            {"op_id": f"T-{ordinal:02d}-head", "arm": "T", "ordinal": ordinal, "kind": "HEAD_0_3", "range": [0, 3]}
        )
        operations.append(
            {"op_id": f"T-{ordinal:02d}-trailer", "arm": "T", "ordinal": ordinal, "kind": "T_TRAILER", "range": [n - 8, n - 1]}
        )
        operations.append(
            {
                "op_id": f"T-{ordinal:02d}-footer",
                "arm": "T",
                "ordinal": ordinal,
                "kind": "T_FOOTER_FROM_TRAILER",
                "range": None,
                "range_rule": "N-8-L..N-9",
            }
        )
    for seq, op in enumerate(operations):
        op["seq"] = seq
    return seal(
        {
            "kind": "essential_web_v4_phase_p_plan",
            "protocol_version": PROTOCOL_VERSION,
            "plan_schema_version": 1,
            "synthetic": False,
            "phase": "P",
            "scientific_namespace": SCIENTIFIC_NAMESPACE,
            "selection_digest": SELECTION_DIGEST,
            "policy_digest": POLICY_DIGEST,
            "source": SOURCE,
            "execution_root": EXECUTION_ROOT,
            "arms": ["M", "T"],
            "files": files,
            "operations": operations,
            "limits": LIMITS,
            "network": NETWORK,
        }
    )


def external_checks() -> dict[str, Any]:
    wanted = {
        "text_selection_manifest.json": (SELECTION_SHA256, SELECTION_DIGEST, 23807),
        "footer_evidence.json": (
            "ba1af742b81f4502beec44624891f869092b0e440f98fb48ea182b3b86cdc276",
            M_OBSERVATION_DIGEST,
            879128,
        ),
        "text_cost_evidence_attempt2.incomplete.json": (
            "4b5acee72e925071f012a163024b3d54d56f95a9e80283742cb01b26160e475a",
            T_COSTMAP_DIGEST,
            24617,
        ),
    }
    out: dict[str, Any] = {}
    for name, (want_sha, want_digest, want_bytes) in wanted.items():
        found = next((d / name for d in EXTERNAL_DIRS if (d / name).is_file()), None)
        if found is None:
            out[name] = {"status": "NOT AVAILABLE"}
            continue
        raw = found.read_bytes()
        obj = json.loads(raw.decode("utf-8"))
        ok = sha(raw) == want_sha and self_digest(obj) == want_digest and len(raw) == want_bytes
        out[name] = {
            "status": "VERIFIED" if ok else "MISMATCH",
            "read_path": str(found),
            "bytes": len(raw),
            "sha256": sha(raw),
            "canonical_self_digest": self_digest(obj),
        }
        if not ok:
            fail(f"external artifact {name} does not reproduce its frozen binding")
        if name == "text_selection_manifest.json":
            units: list[Any] = []
            for cell in obj["cells"]:
                units += [cell] if "identities" in cell else cell["crawls"]
            locators = [tuple(i) for u in units for i in u["identities"]]
            files = sorted({i[2] for i in locators})
            out[name]["structural"] = {
                "strata_cells": len(obj["cells"]),
                "allocation_units": len(units),
                "locators": len(locators),
                "unique_locators": len(set(locators)),
                "total_selected": obj["total_selected"],
                "policy_digest": obj["policy_digest"],
                "revision": obj["revision"],
                "text_selection_seed": obj["text_selection_seed"],
                "development_files": files,
                "fields_read": "structure/counts/file paths only; no document text",
            }
    return out


def build_adoption(freeze: dict[str, Any], v3: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    science = freeze["scientific_identity"]
    identity_child = v3["identity"]
    if identity_child["payload"]["scientific_identity"] != science:
        fail("v3.0 CHILD scientific identity differs from the v3.0 freeze")
    checks = {
        "scientific_namespace": SCIENTIFIC_NAMESPACE,
        "selection_digest": SELECTION_DIGEST,
        "revision": SOURCE_REVISION,
        "policy_digest": POLICY_DIGEST,
        "metadata_and_text_seed": 20260927,
        "review_order_seed": 20260928,
        "projection": PROJECTION,
        "selection_total": 118,
        "M_observation_digest": M_OBSERVATION_DIGEST,
        "T_costmap_digest": T_COSTMAP_DIGEST,
    }
    for key, value in checks.items():
        if science[key] != value:
            fail(f"scientific identity field {key} differs from the adopted value")
    baseline = freeze["audited_baseline"]["code_and_dependency_bindings"]
    code: dict[str, Any] = {}
    for rel, binding in sorted(baseline.items()):
        head = git_blob_sha256("HEAD", rel)
        worktree = sha((REPO / rel).read_bytes())
        match = "committed_blob" if head == binding["sha256"] else (
            "checkout_representation" if worktree == binding["sha256"] else "MISMATCH"
        )
        if match == "MISMATCH":
            fail(f"audited scientific baseline {rel} drifted")
        code[rel] = {"frozen_sha256": binding["sha256"], "head_blob_sha256": head, "match": match}
    m_files = [f for f in plan["files"] if f["arm"] == "M"]
    t_files = [f for f in plan["files"] if f["arm"] == "T"]
    return seal(
        {
            "kind": "essential_web_v4_scientific_adoption",
            "protocol_version": PROTOCOL_VERSION,
            "scientific_namespace": SCIENTIFIC_NAMESPACE,
            "adoption": "exact; the v4.0 execution lineage adopts the frozen v2.0 scientific membership unchanged",
            "adopted_from": {
                "v3_freeze": {
                    "path": V3_FREEZE_REL,
                    "digest": V3_FREEZE_DIGEST,
                    "commit": V3_FREEZE_COMMIT,
                },
                "v3_child_scientific_identity": {
                    "path": f"{V3_CHILD_REL}/{V3_IDENTITY_CHILD}",
                    "digest": identity_child["digest"],
                    "commit": V3_CHILDREN_COMMIT,
                },
                "v3_child_manifest_digest": v3["manifest_digest"],
            },
            "scientific_identity": science,
            "scientific_identity_digest": H(science),
            "M": {
                "files": [
                    {"file": f["file"], "window": f["bindings"]["window"], "remote_length": f["remote_length"], "strong_etag": f["strong_etag"]}
                    for f in m_files
                ],
                "file_count": len(m_files),
                "rows_per_file": 512,
                "rows_total": 512 * len(m_files),
                "metadata_seed": science["metadata_and_text_seed"],
                "projection": science["projection"],
                "window_rule": "exact frozen deterministic windows; no recomputation",
            },
            "T": {
                "locator_count": science["selection_total"],
                "development_files": [
                    {"file": f["file"], "remote_length": f["remote_length"], "strong_etag": f["strong_etag"]}
                    for f in t_files
                ],
                "file_count": len(t_files),
                "strata_censuses_precedence_rubric_blinding": science["rubric_blinding_strata_censuses"],
                "review_order_seed": science["review_order_seed"],
                "selection_binding": science["selection_binding"],
            },
            "scientific_code_baseline": code,
            "prohibitions": [
                "no reselection",
                "no replacement file",
                "no replacement window",
                "no new locator",
                "no inspection-driven adaptation",
                "no rubric, blinding, strata, census, precedence or review-order change",
            ],
        }
    )


def environment() -> dict[str, Any]:
    uv = subprocess.run(["uv", "--version"], capture_output=True, text=True).stdout.strip()
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "machine": platform.machine(),
        "uv": uv,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("plan", "freeze"):
        fail("usage: build_v4_freeze.py plan|freeze")
    freeze_v3, m_dry, t_dry, v3 = v3_inputs()
    plan = build_plan(freeze_v3, m_dry, t_dry)
    adoption = build_adoption(freeze_v3, v3, plan)
    plan_binding = write("phase_p_plan.json", plan)
    adoption_binding = write("scientific_adoption.json", adoption)
    print(f"plan digest       {plan['digest']}")
    print(f"adoption digest   {adoption['digest']}")
    print(f"identity digest   {adoption['scientific_identity_digest']}")
    if mode == "plan":
        return 0
    protocol_raw = (REPO / PROTOCOL_REL).read_bytes()
    text = protocol_raw.decode("utf-8")
    for needle in (plan["digest"], adoption["digest"], adoption["scientific_identity_digest"]):
        if needle not in text:
            fail(f"protocol does not state digest {needle}")
    external = external_checks()
    t_files = [f["file"] for f in plan["files"] if f["arm"] == "T"]
    structural = external.get("text_selection_manifest.json", {}).get("structural")
    if structural is not None and (
        structural["locators"] != 118
        or structural["unique_locators"] != 118
        or structural["development_files"] != sorted(t_files)
        or structural["policy_digest"] != POLICY_DIGEST
        or structural["revision"] != SOURCE_REVISION
    ):
        fail("selection manifest structure differs from the adopted T membership")
    ops = plan["operations"]
    freeze = seal(
        {
            "kind": "essential_web_evidence_v4_freeze",
            "protocol_version": PROTOCOL_VERSION,
            "frozen_date": FROZEN_DATE,
            "status": "FROZEN_READY_TO_IMPLEMENT",
            "protocol": {"path": PROTOCOL_REL, "bytes": len(protocol_raw), "sha256": sha(protocol_raw)},
            "phase_p_plan": plan_binding,
            "scientific_adoption": adoption_binding,
            "scientific_identity_digest": adoption["scientific_identity_digest"],
            "scientific_namespace": SCIENTIFIC_NAMESPACE,
            "selection_digest": SELECTION_DIGEST,
            "policy_digest": POLICY_DIGEST,
            "source": SOURCE,
            "execution_root": EXECUTION_ROOT,
            "operational_limits": LIMITS,
            "network": NETWORK,
            "operation_counts": {
                "M_logical": sum(1 for o in ops if o["arm"] == "M"),
                "T_logical": sum(1 for o in ops if o["arm"] == "T"),
                "T_derived_after_trailer": sum(1 for o in ops if o["range"] is None),
            },
            "lineage": {
                "v2.x": {
                    "status": "CLOSED_NON_EXECUTABLE",
                    "accounting": "HISTORICALLY_UNCERTIFIABLE",
                    "lineage_closure_digest": V2_LINEAGE_CLOSURE_DIGEST,
                },
                "v3.0": {
                    "status": "SCIENTIFIC_PLANNING_LINEAGE_ADOPTED; EXECUTION_IMPLEMENTATION_ABANDONED_BEFORE_REAL_ACQUISITION",
                    "freeze_digest": V3_FREEZE_DIGEST,
                    "protocol_sha256": V3_PROTOCOL_SHA256,
                    "freeze_commit": V3_FREEZE_COMMIT,
                    "implementation_commit": V3_IMPLEMENTATION_COMMIT,
                    "children_commit": V3_CHILDREN_COMMIT,
                    "authorization_review_3_commit": V3_REVIEW3_COMMIT,
                    "authorization_review_3_verdict": "PHASE-P AUTHORIZATION BLOCKED",
                    "phase_p_authorized": False,
                    "real_epoch_genesis": False,
                    "real_network_requests": 0,
                    "selected_M_outcomes_inspected": False,
                    "selected_T_document_text_inspected": False,
                    "execution_root": "G:/Project/xlm-evidence-v3/essential-web (never created by v4)",
                },
                "v4.0": "new prospective SIMPLE execution lineage; adopts the exact scientific membership; only the operational acquisition mechanism is simplified",
            },
            "phase_d": "NOT AUTHORIZED; requires a separate future freeze and review",
            "implementation_contract": {
                "package": "src/xlm/data/evidence_v4",
                "cli": "scripts/evidence_v4.py",
                "commands": ["verify", "show-plan", "phase-p-status", "phase-p"],
                "live_confirmation": "--confirm-plan-digest <exact v4 plan digest>",
                "forbidden_inputs": ["url", "file", "range", "etag", "operation kind", "plan override", "output path", "non-frozen live root"],
                "state": "state.sqlite (Python stdlib sqlite3)",
            },
            "current_network_requests": 0,
            "current_corpus_text_bytes_read": 0,
        }
    )
    freeze_binding = write("freeze.json", freeze)
    verification = seal(
        {
            "kind": "essential_web_evidence_v4_freeze_verification",
            "protocol_version": PROTOCOL_VERSION,
            "freeze_digest": freeze["digest"],
            "plan_digest": plan["digest"],
            "scientific_adoption_digest": adoption["digest"],
            "protocol_sha256": sha(protocol_raw),
            "command": "uv run --offline --locked python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0/build_v4_freeze.py freeze",
            "checks": {
                "v3_freeze_self_digest_and_frozen_commit_blob": "VERIFIED",
                "v3_protocol_sha256": "VERIFIED",
                "v3_child_plans_manifest_digest_and_children_commit_blob": "VERIFIED",
                "plan_derived_from_v3_freeze_equals_v3_child_dry_plans": "VERIFIED",
                "v3_child_scientific_identity_equals_v3_freeze": "VERIFIED",
                "scientific_code_baseline_29_bindings_match_head": "VERIFIED",
                "external_v2_artifacts": external,
                "M_files": 8,
                "M_logical_operations": freeze["operation_counts"]["M_logical"],
                "T_files": 8,
                "T_logical_operations": freeze["operation_counts"]["T_logical"],
                "phase_d_ranges_in_plan": 0,
            },
            "environment": environment(),
            "network_requests": 0,
            "corpus_text_bytes_read": 0,
            "tests": "NOT RUN in the freeze commit; the implementation commit carries tests",
        }
    )
    write("verification.json", verification)
    print(f"protocol sha256   {sha(protocol_raw)}")
    print(f"freeze digest     {freeze['digest']}")
    print(f"freeze sha256     {freeze_binding['sha256']}")
    print(f"verification      {verification['digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
