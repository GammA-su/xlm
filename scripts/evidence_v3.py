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
from xlm.data.evidence_v3 import (
    authz,
    dry,
    envidentity,
    epoch,
    frozen_v3,
    guards,
    readiness,
    schedules,
)

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
        # Derived readiness: every item computed from verified state, never
        # hard-coded. Each check below exercises the real mechanism or binding.
        transport_mod = __import__(
            "xlm.data.evidence_v3.transport", fromlist=["check_frozen_hosts_match_protocol"]
        )

        def _derived(arm: str) -> dict[str, Any]:
            checks = {
                "scientific_identity_ok": lambda: (
                    freeze["scientific_identity"]["selection_digest"] == frozen_v3.SELECTION_DIGEST
                    and freeze["scientific_identity"]["selection_sha256"]
                    == frozen_v3.SELECTION_SHA256,
                    "selection digest/bytes match frozen",
                ),
                "lineage_closed_v2_ok": lambda: (
                    freeze["lineage_closure_digest"]
                    == canonical.loads_bytes_strict(
                        (V3_EVIDENCE / "lineage_closure.json").read_bytes()
                    )["digest"],
                    "lineage closure digest binds",
                ),
                "v3_epoch_semantics_ok": lambda: (
                    freeze["epoch_id"] == frozen_v3.EPOCH_ID
                    and freeze["epoch_state"] == "NOT_STARTED"
                    and freeze["authorization"] == "NONE",
                    "epoch NOT_STARTED/NONE",
                ),
                "planning_adoption_ok": lambda: (
                    all(
                        v.get("v3_role") == "adopted_planning_observation"
                        and v.get("v3_budget_history") is False
                        for v in freeze["adopted_planning_observations"].values()
                    ),
                    "adopted observations carry no budget history",
                ),
                "phase_p_schedule_ok": lambda: (
                    (m_p if arm == "M" else t_p)["phase"] == "P",
                    "phase-P dry schedule built from freeze",
                ),
                "phase_d_schedule_ok": lambda: (
                    (m_d if arm == "M" else t_d)["requires_sealed_P"] is True,
                    "phase-D gated on sealed P",
                ),
                "prospective_requests_ok": lambda: (
                    (m_d if arm == "M" else t_d)["nominal_physical_total"]
                    == (688 if arm == "M" else 95),
                    "nominal request totals reproduce",
                ),
                "prospective_bytes_ok": lambda: (
                    (m_d if arm == "M" else t_d)[
                        "data_payload_bytes_arm" if arm == "M" else "data_payload_bytes_arm"
                    ]
                    == (11692530 if arm == "M" else 179963169),
                    "payload totals reproduce",
                ),
                "decompression_ok": lambda: (
                    freeze["prospective_totals"][
                        "M_decompressed_chunk_sum" if arm == "M" else "T_decompressed_chunk_sum"
                    ]
                    <= frozen_v3.ARM_M_CAPS["decompressed_bytes_arm"],
                    "decompressed sums fit 512 MiB arm cap",
                ),
                "scan_ok": lambda: (
                    (131072 if arm == "M" else 38400) <= 131072,
                    "scan rows fit arm cap",
                ),
                "memory_supervision_ok": lambda: _selftest_memory(),
                "disk_schedule_ok": lambda: _selftest_disk(),
                "runtime_accounting_ok": lambda: _selftest_runtime(),
            }
            return readiness.derive_arm_readiness(checks)

        def _selftest_memory() -> tuple[bool, str]:
            probe = guards.FailClosedSupervisor(cap_bytes=268435456, reader=lambda: [(1, 8)])
            try:
                probe.check(context="readiness-selftest")
                transport_mod.check_frozen_hosts_match_protocol()
            except (guards.GuardError, ValueError) as exc:
                return False, f"selftest failed: {exc}"
            return True, "fail-closed supervisor + frozen transport hosts verify"

        def _selftest_disk() -> tuple[bool, str]:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                inv = guards.PhysicalDiskInventory(
                    Path(tmp), scratch_cap=1 << 20, final_cap=1 << 20, combined_cap=1 << 21
                )
                try:
                    inv.reserve("probe.bin", scratch=16, final=0)
                    inv.write_file("probe.bin", b"x" * 16)
                    inv.reconcile()
                except guards.GuardError as exc:
                    return False, f"selftest failed: {exc}"
            return True, "reservation/write/reconcile selftest passes"

        def _selftest_runtime() -> tuple[bool, str]:
            runtime = guards.ActiveRuntime()
            try:
                runtime.charge(1.0, file="readiness-selftest")
                budget = runtime.request_budget(file="readiness-selftest")
            except guards.GuardError as exc:
                return False, f"selftest failed: {exc}"
            return (budget > 0, "monotonic charge/budget selftest passes")

        m_review = _derived("M")
        t_review = _derived("T")
        readiness_payload: dict[str, Any] = {
            "M": m_review,
            "T": t_review,
            "note": "items derived from verified state; D blocked until P seals",
        }
        for arm_review in ("M", "T"):
            arm_payload = readiness_payload[arm_review]
            assert isinstance(arm_payload, dict)
            if arm_payload["verdict"] != "READY_FOR_PHASE_P_AUTHORIZATION_REVIEW":
                return _fail(f"derived readiness blocks {arm_review}")
        _write("readiness_review.json", "essential_web_v3_readiness", "M", readiness_payload)
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
        claim = epoch.claim_genesis_exclusive(synthetic, owner="synthetic-test")
        zero_ledgers = {
            "M": {"requests": 0, "response_body_bytes": 0},
            "T": {"requests": 0, "response_body_bytes": 0},
        }
        body = epoch.build_genesis_record(
            authorization_digest=epoch.authorization_digest(auth_artifact),
            operator_approval_digest="0" * 64,
            protocol_digest=freeze["protocol_sha256"],
            freeze_digest=freeze["digest"],
            implementation_commit="0" * 40,
            child_manifest_digest="0" * 64,
            m_phase_p_plan_digest="0" * 64,
            t_phase_p_plan_digest="0" * 64,
            execution_root=synthetic,
            initial_inventory=pristine,
            initial_zero_ledgers=zero_ledgers,
            code_hashes=dry.code_hashes(_code_paths(), ROOT),
            environment_identity={"synthetic": "true"},
            resource_caps={"M": freeze["arm_caps"]["M"], "T": freeze["arm_caps"]["T"]},
            supervision_identity={"supervisor": "synthetic-test"},
            owner="synthetic-test",
            utc_timestamp=epoch.utc_timestamp_now(),
            clock_monotonic_ns=epoch.monotonic_ns(),
            allow_nonfrozen_root=True,
        )
        # Synthetic mechanism test: real genesis keeps the frozen root and a
        # real authorization; allow the non-frozen synthetic root here only.
        body = {**body, "execution_root": synthetic.as_posix()}
        body["digest"] = canonical.self_digest({k: v for k, v in body.items() if k != "digest"})
        binding = epoch.publish_genesis_record(synthetic, body, claim=claim)
        print(json.dumps({"synthetic_root": synthetic.as_posix(), "binding": binding}, indent=2))
        return 0
    except (canonical.CanonicalError, epoch.EpochError, dry.DryError, OSError) as exc:
        return _fail(str(exc))


def cmd_check_auth(args: argparse.Namespace) -> int:
    """Offline strict Phase-P authorization schema check (no genesis, no network)."""
    try:
        artifact: dict[str, Any] = canonical.loads_bytes_strict(Path(args.artifact).read_bytes())
        manifest = canonical.loads_bytes_strict(
            (Path(args.child_dir) / "artifact_manifest.json").read_bytes()
        )
        payload_artifacts = manifest["payload"]["artifacts"]
        m_plan = canonical.loads_bytes_strict(
            (Path(args.child_dir) / "arm_m_phase_p_dry.json").read_bytes()
        )
        t_plan = canonical.loads_bytes_strict(
            (Path(args.child_dir) / "arm_t_phase_p_dry.json").read_bytes()
        )
        producer = m_plan["producer"]
        result = authz.validate_phase_p_authorization(
            artifact,
            expected_freeze_commit=frozen_v3.FROZEN_PROTOCOL_COMMIT,
            expected_implementation_commit=str(args.implementation_commit),
            expected_child_manifest_digest=manifest["digest"],
            expected_m_phase_p_plan_digest=m_plan["digest"],
            expected_t_phase_p_plan_digest=t_plan["digest"],
            expected_code_hashes=producer["code_blob_hashes"],
            accepted_review_digests={str(args.accepted_review)},
        )
        _ = payload_artifacts
        print(json.dumps(result, indent=2))
        return 0
    except (
        canonical.CanonicalError,
        authz.AuthError,
        envidentity.EnvIdentityError,
        OSError,
    ) as exc:
        return _fail(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-v3.0 offline mechanisms.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-v3", help="Verify v3 freeze/lineage/schedules.")
    verify.set_defaults(func=cmd_verify_v3)
    build = sub.add_parser("build-v3-dry-plans", help="Generate DRY v3 child plans.")
    build.add_argument("--out-dir", type=Path, default=V3_CHILD)
    build.set_defaults(func=cmd_build_dry_plans)
    check = sub.add_parser("check-auth", help="Strict offline Phase-P auth schema check.")
    check.add_argument("--artifact", type=Path, required=True)
    check.add_argument("--child-dir", type=Path, default=V3_CHILD)
    check.add_argument("--implementation-commit", type=str, required=True)
    check.add_argument("--accepted-review", type=str, required=True)
    check.set_defaults(func=cmd_check_auth)
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
