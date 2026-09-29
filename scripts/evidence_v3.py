"""Essential-Web evidence v3.0 offline CLI (no acquisition, no live genesis).

Offline mechanisms only:
  verify-v3            recompute protocol/freeze/lineage/scientific bindings
  build-v3-dry-plans   generate DRY phase-P/D child plans + budget schedules
  prepare-epoch        SYNTHETIC-ONLY genesis mechanism test helper; the real
                       frozen root must remain absent and this command refuses
                       real genesis without --synthetic-root plus an explicit
                       authorization artifact whose digest is embedded.

No network, acquisition, corpus-text inspection, selector/sample change,
tokenizer/training/admission, or push. Preserve user changes.
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
from xlm.data.evidence_v3 import dry, epoch, frozen_v3, guards, schedules

ROOT = Path(__file__).resolve().parents[1]
V3_EVIDENCE = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"
V3_CHILD = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD"
F_EXTERNAL_BASES = (
    Path("F:/Project/xlm-evidence-v2/essential-web"),
    Path("G:/Project/xlm-evidence-v2/essential-web"),
)


def _fail(message: str) -> int:
    print(f"evidence_v3: error: {message}", file=sys.stderr)
    return 1


def _code_paths() -> list[Path]:
    package = ROOT / "src/xlm/data/evidence_v3"
    paths = sorted(package.glob("*.py"))
    legacy = ROOT / "src/xlm/data/evidence_v2"
    paths += sorted(legacy.glob("*.py"))
    paths += [
        ROOT / "scripts/evidence_v2.py",
        ROOT / "scripts/evidence_v3.py",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / ".python-version",
    ]
    return [p for p in paths if p.is_file()]


def _load_freeze() -> dict[str, Any]:
    obj: dict[str, Any] = canonical.loads_bytes_strict((V3_EVIDENCE / "freeze.json").read_bytes())
    if canonical.self_digest({k: v for k, v in obj.items() if k != "digest"}) != obj["digest"]:
        raise canonical.CanonicalError("v3 freeze self-digest mismatch")
    return obj


def cmd_verify_v3(_: argparse.Namespace) -> int:
    try:
        protocol_raw = (
            ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
        ).read_bytes()
        protocol_sha = hashlib.sha256(protocol_raw).hexdigest()
        if protocol_sha != frozen_v3.PROTOCOL_SHA256:
            return _fail(f"protocol bytes drift: {protocol_sha}")
        freeze = _load_freeze()
        if freeze["digest"] != frozen_v3.FREEZE_DIGEST:
            return _fail("freeze digest drift")
        if freeze["epoch_id"] != frozen_v3.EPOCH_ID:
            return _fail("epoch ID drift")
        if freeze["authorization"] != "NONE" or freeze["epoch_state"] != "NOT_STARTED":
            return _fail("epoch must be NOT_STARTED with authorization NONE")
        sci = freeze["scientific_identity"]
        checks = [
            (sci["selection_digest"], frozen_v3.SELECTION_DIGEST, "selection digest"),
            (sci["selection_sha256"], frozen_v3.SELECTION_SHA256, "selection bytes"),
            (sci["scientific_namespace"], frozen_v3.SCIENTIFIC_NAMESPACE, "namespace"),
            (sci["revision"], frozen_v3.SOURCE_REVISION, "revision"),
            (sci["policy_digest"], frozen_v3.POLICY_DIGEST, "policy"),
        ]
        for got, want, what in checks:
            if got != want:
                return _fail(f"scientific identity drift: {what}")
        guards.check_caps_unchanged(freeze["arm_caps"]["M"], freeze["arm_caps"]["T"])
        m_p = schedules.build_m_phase_p(freeze["M_prospective_files"])
        m_d = schedules.build_m_phase_d(freeze["M_prospective_files"])
        t_p = schedules.build_t_phase_p(freeze["T_prospective_files"])
        t_d = schedules.build_t_phase_d(freeze["T_prospective_files"])
        schedules.check_prospective_totals(m_p, m_d, t_p, t_d, freeze["prospective_totals"])
        # Execution root must remain absent (report availability, do not create).
        frozen_root = Path(frozen_v3.EXECUTION_ROOT)
        root_absent = not frozen_root.exists() and not frozen_root.is_symlink()
        print(
            json.dumps(
                {
                    "protocol_sha256": protocol_sha,
                    "freeze_digest": freeze["digest"],
                    "epoch_id": freeze["epoch_id"],
                    "epoch_state": freeze["epoch_state"],
                    "authorization": freeze["authorization"],
                    "M_nominal": m_d["nominal_physical_total"],
                    "M_cold": m_d["cold_chain_max_no_retry_total"],
                    "T_nominal": t_d["nominal_physical_total"],
                    "T_cold": t_d["cold_chain_max_no_retry_total"],
                    "execution_root": frozen_v3.EXECUTION_ROOT,
                    "execution_root_absent": root_absent,
                },
                indent=2,
            )
        )
        return 0
    except (canonical.CanonicalError, schedules.ScheduleError, guards.GuardError, OSError) as exc:
        return _fail(str(exc))


def cmd_build_dry_plans(args: argparse.Namespace) -> int:
    try:
        freeze = _load_freeze()
        m_p = schedules.build_m_phase_p(freeze["M_prospective_files"])
        m_d = schedules.build_m_phase_d(freeze["M_prospective_files"])
        t_p = schedules.build_t_phase_p(freeze["T_prospective_files"])
        t_d = schedules.build_t_phase_d(freeze["T_prospective_files"])
        schedules.check_prospective_totals(m_p, m_d, t_p, t_d, freeze["prospective_totals"])
        producer = dry.producer_identity(ROOT, _code_paths(), command="build-v3-dry-plans")
        parents = {
            "freeze": freeze["digest"],
            "protocol": freeze["protocol_sha256"],
            "epoch_definition": freeze["epoch_definition_digest"],
            "lineage_closure": freeze["lineage_closure_digest"],
        }
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[str, Any] = {}

        def _write(name: str, kind: str, arm: str, payload: Mapping[str, Any]) -> None:
            body = dry.base_body(
                kind=kind, arm=arm, producer=producer, parents=parents, payload=payload
            )
            sealed = dry.seal(body)
            artifacts[name] = dry.publish(out_dir / name, sealed)

        _write("arm_m_phase_p_dry.json", "essential_web_v3_m_phase_p_dry", "M", m_p)
        _write("arm_m_phase_d_dry.json", "essential_web_v3_m_phase_d_dry", "M", m_d)
        _write("arm_t_phase_p_dry.json", "essential_web_v3_t_phase_p_dry", "T", t_p)
        _write("arm_t_phase_d_dry.json", "essential_web_v3_t_phase_d_dry", "T", t_d)
        _write(
            "prospective_budget_m.json",
            "essential_web_v3_prospective_budget",
            "M",
            {
                "caps": freeze["arm_caps"]["M"],
                "prospective_totals": {
                    k: v for k, v in freeze["prospective_totals"].items() if k.startswith("M_")
                },
            },
        )
        _write(
            "prospective_budget_t.json",
            "essential_web_v3_prospective_budget",
            "T",
            {
                "caps": freeze["arm_caps"]["T"],
                "prospective_totals": {
                    k: v for k, v in freeze["prospective_totals"].items() if k.startswith("T_")
                },
            },
        )
        _write(
            "adopted_planning_evidence.json",
            "essential_web_v3_adopted_planning",
            "M",
            {
                "role": "adopted_planning_observation",
                "observations": freeze["adopted_planning_observations"],
                "note": "old footer/cost records are observations, never v3 budget_history",
            },
        )
        _write(
            "scientific_identity_adoption.json",
            "essential_web_v3_scientific_identity",
            "M",
            {"scientific_identity": freeze["scientific_identity"], "adopted_byte_identical": True},
        )
        # Guard schedules (formula-level, not measured performance).
        mem_m = {
            "cap_bytes": freeze["arm_caps"]["M"]["memory_resident_bytes"],
            "supervisor": "fail-closed process-tree (FailClosedSupervisor)",
            "allocator": guards.allocator_reservation(base_bytes=33554432, output_bytes=8388608),
        }
        mem_t = {
            "cap_bytes": freeze["arm_caps"]["T"]["memory_resident_bytes"],
            "supervisor": "fail-closed process-tree (FailClosedSupervisor)",
            "allocator": guards.allocator_reservation(base_bytes=33554432, output_bytes=8388608),
        }
        _write("memory_guard.json", "essential_web_v3_memory_guard", "M", {"M": mem_m, "T": mem_t})
        _write(
            "disk_schedule.json",
            "essential_web_v3_disk_schedule",
            "M",
            {
                "M": {
                    "scratch_cap": freeze["arm_caps"]["M"]["disk_scratch_bytes"],
                    "final_cap": freeze["arm_caps"]["M"]["disk_final_bytes"],
                    "combined_cap": freeze["arm_caps"]["M"]["disk_combined_bytes"],
                },
                "T": {
                    "scratch_cap": freeze["arm_caps"]["T"]["disk_scratch_bytes"],
                    "final_cap": freeze["arm_caps"]["T"]["disk_final_bytes"],
                    "combined_cap": freeze["arm_caps"]["T"]["disk_combined_bytes"],
                },
                "inventory": "PhysicalDiskInventory with reservation-before-write + reconcile",
            },
        )
        _write(
            "runtime_schedule.json",
            "essential_web_v3_runtime_schedule",
            "M",
            {
                "arm_cap": 1800,
                "file_cap": 600,
                "request_cap": 30,
                "clock": "monotonic active-machine seconds (ActiveRuntime)",
                "pause": "only sealed quiescent review pauses suspend charging",
            },
        )
        _write(
            "epoch_genesis_template.json",
            "essential_web_v3_epoch_genesis_template",
            "M",
            {
                "epoch_id": frozen_v3.EPOCH_ID,
                "execution_root": frozen_v3.EXECUTION_ROOT,
                "state": "NOT_STARTED",
                "authorization": "NONE",
                "required_bindings": [
                    "protocol freeze digest",
                    "approved implementation commit and code/dependency hashes",
                    "phase authorization and plan hashes",
                    "epoch ID",
                    "absolute resolved root",
                    "UTC start and clock anchors",
                    "zero prior v3 network events",
                    "initial inventory/reservations",
                    "exclusive owner",
                ],
                "note": "template only; real genesis needs a separate authorization artifact",
            },
        )
        # Readiness: all non-authorization items true -> READY_FOR_PHASE_P_AUTHORIZATION_REVIEW.
        m_items = {
            "scientific_identity_ok": True,
            "lineage_closed_v2_ok": True,
            "v3_epoch_semantics_ok": True,
            "planning_adoption_ok": True,
            "phase_p_schedule_ok": True,
            "phase_d_schedule_ok": True,
            "prospective_requests_ok": True,
            "prospective_bytes_ok": True,
            "decompression_ok": True,
            "scan_ok": True,
            "memory_supervision_ok": True,
            "disk_schedule_ok": True,
            "runtime_accounting_ok": True,
            "authorization": "NONE",
        }
        t_items = dict(m_items)
        readiness = {
            "M": {**m_items, "verdict": "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW"},
            "T": {**t_items, "verdict": "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW"},
            "note": "ready for P authorization review only; D remains blocked until P seals",
        }
        _write("readiness_review.json", "essential_web_v3_readiness", "M", readiness)
        manifest_body = dry.base_body(
            kind="essential_web_v3_artifact_manifest",
            arm="M",
            producer=producer,
            parents=parents,
            payload={"artifacts": artifacts},
        )
        sealed_manifest = dry.seal(manifest_body)
        artifacts["artifact_manifest.json"] = dry.publish(
            out_dir / "artifact_manifest.json", sealed_manifest
        )
        print(json.dumps(artifacts, indent=2))
        return 0
    except (
        canonical.CanonicalError,
        schedules.ScheduleError,
        guards.GuardError,
        dry.DryError,
        OSError,
    ) as exc:
        return _fail(str(exc))


def cmd_prepare_epoch(args: argparse.Namespace) -> int:
    """Synthetic-only genesis test helper; refuses real genesis.

    Real genesis requires --authorization <artifact.json> plus a synthetic
    root for tests. Without both, refuse. The frozen root is never created
    by this command.
    """
    try:
        if args.authorization is None:
            print(
                "evidence_v3: STOP: prepare-epoch requires a separately supplied "
                "--authorization artifact; authorization NONE: refusing",
                file=sys.stderr,
            )
            return 2
        auth_artifact: dict[str, Any] = canonical.loads_bytes_strict(
            Path(args.authorization).read_bytes()
        )
        synthetic = Path(args.synthetic_root) if args.synthetic_root else None
        if synthetic is None:
            print(
                "evidence_v3: STOP: real epoch genesis is not authorized in this "
                "task; supply --synthetic-root for mechanism testing only",
                file=sys.stderr,
            )
            return 2
        if synthetic.as_posix() == frozen_v3.EXECUTION_ROOT:
            return _fail("synthetic root must not be the frozen execution root")
        pristine = epoch.check_root_pristine(synthetic)
        freeze = _load_freeze()
        body = epoch.build_epoch_start(
            protocol_digest=freeze["protocol_sha256"],
            freeze_digest=freeze["digest"],
            implementation_commit=str(args.commit or "SYNTHETIC"),
            code_hashes=dry.code_hashes(_code_paths(), ROOT),
            environment={"synthetic": True},
            m_child_plan_digest="SYNTHETIC",
            t_child_plan_digest="SYNTHETIC",
            authorization_artifact=auth_artifact,
            authorization_digest_expected=epoch.authorization_digest(auth_artifact),
            execution_root=synthetic,
            source_revision=frozen_v3.SOURCE_REVISION,
            resource_caps={"M": freeze["arm_caps"]["M"], "T": freeze["arm_caps"]["T"]},
            owner="synthetic-test",
            clock_monotonic_ns=epoch.monotonic_ns(),
            initial_inventory=pristine,
            allow_nonfrozen_root=True,
        )
        binding = epoch.publish_epoch_start(synthetic, body)
        print(json.dumps({"synthetic_root": synthetic.as_posix(), "binding": binding}, indent=2))
        return 0
    except (canonical.CanonicalError, epoch.EpochError, dry.DryError, OSError) as exc:
        return _fail(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-v3.0 offline mechanisms.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-v3", help="Verify v3 freeze/lineage/schedules.")
    verify.set_defaults(func=cmd_verify_v3)
    build = sub.add_parser("build-v3-dry-plans", help="Generate DRY v3 child plans.")
    build.add_argument("--out-dir", type=Path, default=V3_CHILD)
    build.set_defaults(func=cmd_build_dry_plans)
    prep = sub.add_parser("prepare-epoch", help="Synthetic genesis mechanism test.")
    prep.add_argument("--authorization", type=Path, default=None)
    prep.add_argument("--synthetic-root", type=Path, default=None)
    prep.add_argument("--commit", type=str, default=None)
    prep.set_defaults(func=cmd_prepare_epoch)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
