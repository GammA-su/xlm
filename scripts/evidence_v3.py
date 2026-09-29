"""Essential-Web evidence v3.0 CLI (Phase P only; no Phase D).

Offline commands:
  verify-v3           recompute protocol/freeze/science/cap/schedule bindings
  readiness           derive readiness (static + integrated synthetic scenarios)
  build-v3-children   regenerate the derived CHILD artifacts (refuses if BLOCKED)
  check-auth          validate a REAL Phase-P authorization + operator approval
                      against the committed children and the actual runtime
  inspect             read-only journal replay summary of an execution root

Operator commands (REAL mode, frozen root only; never run by this task):
  phase-p-genesis     exclusive epoch genesis after full validation
  phase-p-execute     run/resume the authorized Phase-P plan (M then T)

There is no boolean/force/skip flag: every command re-validates the
authorization, approval and review artifacts from their bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    dry,
    envidentity,
    executor,
    frozen_v3,
    genesis,
    journal,
    memory,
    plan,
    readiness,
    schedules,
)
from xlm.data.evidence_v3.harness import EpochPaths

ROOT = Path(__file__).resolve().parents[1]
V3_EVIDENCE = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"
V3_CHILD = ROOT / plan.CHILD_DIR


def _fail(message: str) -> int:
    print(f"evidence_v3: error: {message}", file=sys.stderr)
    return 1


def _freeze() -> dict[str, Any]:
    return plan._load_freeze(ROOT)


def cmd_verify_v3(_: argparse.Namespace) -> int:
    try:
        protocol = (
            ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
        ).read_bytes()
        protocol_sha = hashlib.sha256(protocol).hexdigest()
        if protocol_sha != frozen_v3.PROTOCOL_SHA256:
            return _fail(f"protocol bytes drift: {protocol_sha}")
        freeze = _freeze()
        if freeze["authorization"] != "NONE" or freeze["epoch_state"] != "NOT_STARTED":
            return _fail("epoch must be NOT_STARTED with authorization NONE")
        sci = freeze["scientific_identity"]
        for got, want, what in (
            (sci["selection_digest"], frozen_v3.SELECTION_DIGEST, "selection digest"),
            (sci["selection_sha256"], frozen_v3.SELECTION_SHA256, "selection bytes"),
            (sci["scientific_namespace"], frozen_v3.SCIENTIFIC_NAMESPACE, "namespace"),
            (sci["revision"], frozen_v3.SOURCE_REVISION, "revision"),
            (sci["policy_digest"], frozen_v3.POLICY_DIGEST, "policy"),
        ):
            if got != want:
                return _fail(f"scientific identity drift: {what}")
        caps = {"M": freeze["arm_caps"]["M"], "T": freeze["arm_caps"]["T"]}
        if caps != {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS}:
            return _fail("cap map drift")
        if canonical.digest(caps) != frozen_v3.RESOURCE_CAPS_DIGEST:
            return _fail("cap-map digest drift")
        m_d = schedules.build_m_phase_d(freeze["M_prospective_files"])
        t_d = schedules.build_t_phase_d(freeze["T_prospective_files"])
        payloads = plan.recompute_real_payloads(ROOT)
        frozen_root = Path(frozen_v3.EXECUTION_ROOT)
        print(
            json.dumps(
                {
                    "protocol_sha256": protocol_sha,
                    "freeze_digest": freeze["digest"],
                    "cap_map_digest": canonical.digest(caps),
                    "epoch_id": freeze["epoch_id"],
                    "epoch_state": freeze["epoch_state"],
                    "authorization": freeze["authorization"],
                    "selection_digest": sci["selection_digest"],
                    "M_nominal": m_d["nominal_physical_total"],
                    "M_cold": m_d["cold_chain_max_no_retry_total"],
                    "T_nominal": t_d["nominal_physical_total"],
                    "T_cold": t_d["cold_chain_max_no_retry_total"],
                    "M_P_ceiling_requests": payloads["M"]["ceilings"]["arm"]["requests"],
                    "T_P_ceiling_requests": payloads["T"]["ceilings"]["arm"]["requests"],
                    "execution_root": frozen_v3.EXECUTION_ROOT,
                    "execution_root_absent": not frozen_root.exists(),
                },
                indent=2,
            )
        )
        return 0
    except (canonical.CanonicalError, schedules.ScheduleError, plan.PlanError, OSError) as exc:
        return _fail(str(exc))


def cmd_readiness(args: argparse.Namespace) -> int:
    commit = envidentity.resolve_commit(ROOT, args.implementation_commit)
    result = readiness.derive_readiness(ROOT, commit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW" else 2


def _descriptive_payloads() -> dict[str, dict[str, Any]]:
    m, t = frozen_v3.ARM_M_CAPS, frozen_v3.ARM_T_CAPS
    return {
        "memory_guard.json": {
            "cap_bytes": frozen_v3.MEMORY_RESIDENT_BYTES_MAX,
            "supervisor": "executor-owned monitor thread (memory.Supervisor)",
            "sampling_interval_ms": int(memory.wait_interval() * 1000),
            "measured": (
                "main process + recursive descendants + registered PIDs and their descendants"
            ),
            "fail_closed": [
                "over cap",
                "measurement failure",
                "registered PID vanished without reap",
            ],
            "release": "registry-observed termination only (no caller proof, no boolean)",
            "parser": "4 MiB bounded input + Thrift limits = parser_bytes_max",
            "limit": "periodic sampling cannot prove an infinitesimal transient stayed under cap",
        },
        "disk_schedule.json": {
            "M": {
                "scratch_cap": m["disk_scratch_bytes"],
                "final_cap": m["disk_final_bytes"],
                "combined_cap": m["disk_combined_bytes"],
            },
            "T": {
                "scratch_cap": t["disk_scratch_bytes"],
                "final_cap": t["disk_final_bytes"],
                "combined_cap": t["disk_combined_bytes"],
            },
            "control_allowances_both_arms": {
                journal.JOURNAL_REL: journal.JOURNAL_ALLOWANCE,
                journal.HEAD_REL: journal.HEAD_ALLOWANCE // 2,
                journal.HEAD_TMP_REL: journal.HEAD_ALLOWANCE // 2,
                "genesis.claim/epoch_start.json/state/lock": "exact size at genesis",
            },
            "write_api": (
                "single journalled write: reserve(old+new peak) -> temp -> rename"
                " -> reconcile -> commit"
            ),
            "containment": (
                "strict relative grammar; reparse/junction refusal; root volume+file-id identity"
            ),
        },
        "runtime_schedule.json": {
            "arm_seconds": m["time_seconds_arm"],
            "file_seconds": m["time_seconds_plan"],
            "request_seconds": m["time_seconds_request"],
            "clock": "monotonic active seconds charged durably at every step boundary",
            "crash": "an open step hold is charged in full at the next load",
            "pause": "only at a quiescent SESSION_CLOSE (no open attempt, hold or pending write)",
            "note": "no time is reserved for Phase D; P and D share the cumulative arm budget",
        },
        "epoch_genesis_template.json": {
            "epoch_id": frozen_v3.EPOCH_ID,
            "execution_root": frozen_v3.EXECUTION_ROOT,
            "state": "NOT_STARTED",
            "authorization": "NONE",
            "epoch_start_fields": sorted(genesis.EPOCH_START_KEYS),
            "claim_fields": sorted(genesis.CLAIM_KEYS),
            "note": "template only; genesis requires validated authorization + operator approval",
        },
    }


def cmd_build_children(args: argparse.Namespace) -> int:
    try:
        out_dir = Path(args.out_dir)
        if out_dir.exists() and any(out_dir.iterdir()):
            return _fail("output directory must be empty or absent (children are immutable)")
        commit = envidentity.resolve_commit(ROOT, args.implementation_commit)
        freeze = _freeze()
        producer = dry.producer_identity(
            ROOT, command="build-v3-children", implementation_commit=commit
        )
        derived = readiness.derive_readiness(ROOT, commit)
        if derived["verdict"] != "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW":
            return _fail(f"derived readiness BLOCKED: {derived['blocked']}")
        payloads = plan.recompute_real_payloads(ROOT)
        parents = {
            "freeze": freeze["digest"],
            "protocol": freeze["protocol_sha256"],
            "epoch_definition": freeze["epoch_definition_digest"],
            "lineage_closure": freeze["lineage_closure_digest"],
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        listed: dict[str, Any] = {}

        def write(name: str, kind: str, arm: str, payload: Mapping[str, Any]) -> None:
            artifact = dry.envelope(
                kind=kind, arm=arm, producer=producer, parents=parents, payload=payload
            )
            listed[name] = dry.publish(out_dir, name, artifact)

        write(plan.PLAN_FILES["M"], plan.PLAN_KINDS["M"], "M", payloads["M"])
        write(plan.PLAN_FILES["T"], plan.PLAN_KINDS["T"], "T", payloads["T"])
        write(
            "arm_m_phase_d_dry.json",
            "essential_web_v3_m_phase_d_dry",
            "M",
            schedules.build_m_phase_d(freeze["M_prospective_files"]),
        )
        write(
            "arm_t_phase_d_dry.json",
            "essential_web_v3_t_phase_d_dry",
            "T",
            schedules.build_t_phase_d(freeze["T_prospective_files"]),
        )
        for arm in ("M", "T"):
            write(
                f"prospective_budget_{arm.lower()}.json",
                "essential_web_v3_prospective_budget",
                arm,
                {
                    "caps": freeze["arm_caps"][arm],
                    "prospective_totals": {
                        k: v
                        for k, v in freeze["prospective_totals"].items()
                        if k.startswith(f"{arm}_")
                    },
                    "phase_p_ceilings": payloads[arm]["ceilings"],
                },
            )
        write(
            "adopted_planning_evidence.json",
            "essential_web_v3_adopted_planning",
            "M",
            {
                "role": "adopted_planning_observation",
                "observations": freeze["adopted_planning_observations"],
                "note": "old footer/cost records are observations, never v3 budget_history",
            },
        )
        write(
            "scientific_identity_adoption.json",
            "essential_web_v3_scientific_identity",
            "M",
            {"scientific_identity": freeze["scientific_identity"], "adopted_byte_identical": True},
        )
        for name, payload in _descriptive_payloads().items():
            write(name, f"essential_web_v3_{name.removesuffix('.json')}", "M", payload)
        write("readiness_review.json", "essential_web_v3_readiness", "M", derived)
        manifest = dry.envelope(
            kind=plan.MANIFEST_KIND,
            arm="M",
            producer=producer,
            parents=parents,
            payload={"artifacts": listed},
        )
        listed_manifest = dry.publish(out_dir, plan.MANIFEST_FILE, manifest)
        print(json.dumps({**listed, plan.MANIFEST_FILE: listed_manifest}, indent=2))
        return 0
    except (
        canonical.CanonicalError,
        schedules.ScheduleError,
        plan.PlanError,
        dry.DryError,
        envidentity.EnvIdentityError,
        OSError,
    ) as exc:
        return _fail(str(exc))


def _real_paths(args: argparse.Namespace) -> EpochPaths:
    return {
        "repo_root": ROOT,
        "root": Path(frozen_v3.EXECUTION_ROOT),
        "authorization_path": Path(args.authorization),
        "approval_path": Path(args.approval),
        "review_path": Path(args.review),
    }


def cmd_check_auth(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(executor.check_authorization(**_real_paths(args)), indent=2))
        return 0
    except executor.PhasePError as exc:
        return _fail(str(exc))


def cmd_genesis(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(executor.perform_genesis(**_real_paths(args)), indent=2))
        return 0
    except executor.PhasePError as exc:
        return _fail(str(exc))


def cmd_execute(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(executor.execute_phase_p(**_real_paths(args)), indent=2))
        return 0
    except executor.PhasePError as exc:
        return _fail(str(exc))


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(executor.inspect_epoch(Path(args.root)), indent=2, sort_keys=True))
        return 0
    except executor.PhasePError as exc:
        return _fail(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-v3.0 Phase-P boundary CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-v3", help="Verify frozen v3 bindings.").set_defaults(func=cmd_verify_v3)
    ready = sub.add_parser("readiness", help="Derive readiness (static + synthetic).")
    ready.add_argument("--implementation-commit", required=True)
    ready.set_defaults(func=cmd_readiness)
    build = sub.add_parser("build-v3-children", help="Regenerate derived CHILD artifacts.")
    build.add_argument("--out-dir", type=Path, default=V3_CHILD)
    build.add_argument("--implementation-commit", required=True)
    build.set_defaults(func=cmd_build_children)
    for name, func, text in (
        ("check-auth", cmd_check_auth, "Validate REAL authorization + approval (offline)."),
        ("phase-p-genesis", cmd_genesis, "OPERATOR: exclusive REAL epoch genesis."),
        ("phase-p-execute", cmd_execute, "OPERATOR: run/resume REAL Phase P."),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--authorization", type=Path, required=True)
        cmd.add_argument("--approval", type=Path, required=True)
        cmd.add_argument("--review", type=Path, required=True)
        cmd.set_defaults(func=func)
    insp = sub.add_parser("inspect", help="Read-only epoch journal summary.")
    insp.add_argument("--root", type=Path, required=True)
    insp.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
