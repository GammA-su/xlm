"""Offline C05 operator interfaces; inspect/schema need no protected signing key.

Engineering remains under validation. The CLI intentionally refuses protected
plan allocation until production recovery/resource and downstream integration
acceptance is complete; an authored run is never a protected certificate.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_run import write_once
from xlm.data.exclusion.artifacts import BenchmarkReceipt, ExecutionPlan
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import (
    ENGINEERING_BLOCKERS,
    C05Error,
    Resources,
    ReviewPolicy,
    matcher_policy,
)
from xlm.data.exclusion.protected import MaterialSpec, build, inspect


def readiness_report(args: argparse.Namespace) -> dict[str, Any]:
    """Separate engineering blockers from operator decisions and protected evidence.

    Presence of a decision/receipt file is not acceptance: the plan command verifies
    signatures, bindings and the exact current manifest before allocating a plan.
    """

    def state(path: Path) -> str:
        return "present; trusted verification at plan time" if path.is_file() else "missing"

    decisions = {
        "gutenberg_lineage_decision": state(args.lineage_policy),
        "reviewed_resource_decision": state(args.resources),
    }
    evidence = {"protected_benchmark_preparation_receipt": state(args.benchmark_receipt)}
    checks: dict[str, Any] = {}
    if args.manifest is not None and args.ifm_split is not None:
        from xlm.data.exclusion.quotas import frozen_requirements

        requirements = frozen_requirements(
            read_metadata(args.manifest), args.quotas, args.ifm_split
        )
        checks["frozen_quota_allocations"] = {
            "allocations": len(requirements["allocations"]),
            "valid_target_quota": requirements["valid_target_quota"],
            "quota_sha256": requirements["quota_sha256"],
            "ifm_split_digest": requirements["ifm_split_digest"],
            "common_pile_split_digest": requirements["common_pile_split_digest"],
        }
    if args.geometry_probe_dir is not None:
        from xlm.data.exclusion.capacity import admit_plan, probe_geometry

        geometry = probe_geometry(args.geometry_probe_dir)
        try:
            admitted: dict[str, Any] = admit_plan(Resources(), geometry)
        except C05Error as exc:
            admitted = {"refused": str(exc)}
        checks["proposed_resources_storage_admission"] = {
            "proposal_only": True,
            "geometry": geometry.model_dump(),
            **admitted,
        }
    remaining = [
        *(f"engineering: {b}" for b in ENGINEERING_BLOCKERS),
        *(f"operator decision {k} {v}" for k, v in decisions.items() if v == "missing"),
        *(f"evidence {k} {v}" for k, v in evidence.items() if v == "missing"),
    ]
    return {
        "ready": False,
        "plan_digest": None,
        "engineering_blockers": list(ENGINEERING_BLOCKERS),
        "operator_decisions": decisions,
        "protected_evidence": evidence,
        "engineering_checks": checks,
        "blockers": remaining,
    }


def protected_root_command(args: argparse.Namespace) -> dict[str, Any]:
    """Create the root marker, or describe a measured detached-volume isolation.

    ``describe`` is a proposal for the material spec: content-free paths, device
    identities and the marker digest. ``build-local`` re-measures all of it.
    """
    from xlm.data.exclusion.isolation import (
        DetachedVolumeIsolation,
        Role,
        RootIdentity,
        init_protected_root,
        inspect_protected_root,
        os_volume,
    )

    if args.action == "init":
        if not args.logical_id:
            raise C05Error("--logical-id is required to initialize a protected root")
        marker = init_protected_root(args.root, args.logical_id)
        return {"initialized": str(args.root), "logical_id": marker["logical_id"]}
    if not args.operator or not args.attestation_sha256:
        raise C05Error("describe requires --operator and --attestation-sha256")
    declared: tuple[tuple[Role, list[Path]], ...] = (
        ("repository", args.repository),
        ("data_root", args.data_root),
        ("training_data", args.training_data),
        ("c05_scratch", args.c05_scratch),
        ("c05_output", args.c05_output),
    )
    roots = [
        RootIdentity(role=role, path=str(path.resolve()), volume=os_volume(path))
        for role, paths in declared
        for path in paths
    ]
    isolation = DetachedVolumeIsolation(
        mechanism="detached_volume_v1",
        mode=args.mode,
        operator_principal=args.operator,
        # Same Windows account by deployment choice; no agent OS denial is claimed.
        agent_principal=args.operator,
        protected_root=inspect_protected_root(args.root),
        separated_roots=tuple(roots),
        attestation_sha256=args.attestation_sha256,
    )
    return {"proposal_only": True, "isolation": isolation.model_dump(mode="json")}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {
        "lineage-policy",
        "resources",
        "policy",
        "admit-cleaned",
        "benchmark-receipt",
        "plan",
        "authorize",
        "run",
        "resume",
        "status",
        "resume-check",
        "verify",
        "publish",
        "proof",
        "quota-report",
        "final-receipt",
        "count-tokens",
        "count-tokens-reference",
        "select",
        "tokenize-selection",
        "freeze",
        "claim-binding",
        "claim-check",
        "fit-tokenizer",
        "fit-tokenizer-reference",
        "verify-tokenizer-fit",
        "verify-kept-index",
    }:
        from xlm.data.exclusion.control import main as control_main

        return control_main(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("--output", type=Path, required=True)
    inspect_parser = sub.add_parser("inspect-local")
    build_parser = sub.add_parser("build-local")
    for command in (inspect_parser, build_parser):
        command.add_argument("--spec", type=Path, required=True)
        command.add_argument("--material-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--policy", type=Path, required=True)
    build_parser.add_argument("--resources", type=Path, required=True)
    build_parser.add_argument("--review-policy", type=Path)
    build_parser.add_argument("--issuer", required=True)
    build_parser.add_argument("--key-env", required=True)
    build_parser.add_argument("--code-commit", required=True)
    build_parser.add_argument("--code-identity", required=True)
    build_parser.add_argument("--dependency-sha256", required=True)
    # detached_volume_v1 only: the content-free receipt copy outside the protected root.
    build_parser.add_argument("--receipt-export", type=Path)
    # Operational display only (stderr); never part of any artifact or receipt.
    build_parser.add_argument("--progress-interval", type=float, default=1.0)
    build_parser.add_argument("--no-progress", action="store_true")
    # Content-free signature-coverage audit: reads protected material, writes nothing.
    audit_parser = sub.add_parser("benchmark-audit-local")
    audit_parser.add_argument("--spec", type=Path, required=True)
    audit_parser.add_argument("--material-root", type=Path, required=True)
    audit_parser.add_argument("--policy", type=Path, required=True)
    audit_parser.add_argument("--resources", type=Path, required=True)
    audit_parser.add_argument("--workers", type=int, choices=range(1, 17))
    audit_parser.add_argument("--progress-interval", type=float, default=1.0)
    audit_parser.add_argument("--no-progress", action="store_true")
    # Content-free compiled-matcher capacity audit: compiles into protected scratch.
    matcher_parser = sub.add_parser("benchmark-matcher-audit-local")
    matcher_parser.add_argument("--index", type=Path, required=True)
    matcher_parser.add_argument("--resources", type=Path, required=True)
    matcher_parser.add_argument("--scratch", type=Path, required=True)
    matcher_parser.add_argument("--backend", choices=["compact", "streaming"], default="compact")
    matcher_parser.add_argument("--mode", choices=["protected", "authored"], default="protected")
    matcher_parser.add_argument("--self-check", type=int, default=1000)
    root_parser = sub.add_parser("protected-root")
    root_parser.add_argument("action", choices=["init", "describe"])
    root_parser.add_argument("--root", type=Path, required=True)
    root_parser.add_argument("--logical-id")
    root_parser.add_argument("--operator")
    root_parser.add_argument("--attestation-sha256")
    root_parser.add_argument("--mode", choices=["protected", "authored"], default="protected")
    for role in ("repository", "data-root", "training-data", "c05-scratch", "c05-output"):
        root_parser.add_argument("--" + role, type=Path, action="append", default=[])
    readiness = sub.add_parser("plan-readiness")
    readiness.add_argument(
        "--benchmark-receipt",
        type=Path,
        default=Path("G:/XLM/c05/benchmark-preparation.receipt.json"),
    )
    readiness.add_argument(
        "--lineage-policy", type=Path, default=Path("G:/XLM/c05/lineage-policy.json")
    )
    readiness.add_argument("--resources", type=Path, default=Path("G:/XLM/c05/resources.json"))
    # Optional metadata-only engineering checks; nothing here reads corpus text.
    readiness.add_argument("--manifest", type=Path)
    readiness.add_argument(
        "--quotas", type=Path, default=Path("recipes/mixtures/mix01_quotas_6b.yaml")
    )
    readiness.add_argument("--ifm-split", type=Path)
    readiness.add_argument("--geometry-probe-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            from xlm.data.exclusion.control import OperatorDecision
            from xlm.data.exclusion.transport import ProofSpec

            write_once(
                args.output,
                {
                    "material_spec": MaterialSpec.model_json_schema(),
                    "benchmark_receipt": BenchmarkReceipt.model_json_schema(),
                    "execution_plan": ExecutionPlan.model_json_schema(),
                    "operator_decision": OperatorDecision.model_json_schema(),
                    "downstream_proof": ProofSpec.model_json_schema(),
                },
            )
            return 0
        if args.command == "plan-readiness":
            print(json.dumps(readiness_report(args), sort_keys=True))
            return 2
        if args.command == "protected-root":
            print(json.dumps(protected_root_command(args), sort_keys=True))
            return 0
        if args.command == "benchmark-matcher-audit-local":
            from xlm.data.exclusion.matcher_audit import audit as matcher_audit

            if not 0 <= args.self_check <= 1_000_000:
                raise C05Error("self-check sample outside 0..1000000")
            report = matcher_audit(
                args.index,
                Resources.model_validate(read_metadata(args.resources, digested=False)),
                args.scratch,
                mode=args.mode,
                backend=args.backend,
                self_check=args.self_check,
            )
            print(json.dumps(report, sort_keys=True))
            if report.get("refused"):
                return 1
            return 0 if report.get("all_fit", True) else 2
        spec = MaterialSpec.model_validate(read_metadata(args.spec, digested=False))
        if args.command == "inspect-local":
            inspection = inspect(spec, args.material_root)
            write_once(args.output, inspection)
            return 0 if inspection["complete_local_sizes"] else 2
        if args.command == "benchmark-audit-local":
            from xlm.data.exclusion.prepare_workers import Progress
            from xlm.data.exclusion.protected import audit

            resources = Resources.model_validate(read_metadata(args.resources, digested=False))
            if args.workers is not None:
                resources = resources.model_copy(update={"workers": args.workers})
            reporter = Progress(
                files=len(spec.files),
                rows=sum(f.items for f in spec.files),
                workers=resources.workers,
                interval=args.progress_interval,
            )
            result = audit(
                spec,
                args.material_root,
                policy=matcher_policy(read_metadata(args.policy, digested=False)),
                resources=resources,
                progress=None if args.no_progress else reporter.update,
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["totals"]["items_without_patterns"] == 0 else 2
        key = os.environ.get(args.key_env)
        if key is None or len(key) < 32:
            raise C05Error("protected signing key missing or too short")
        from xlm.data.exclusion.prepare_workers import Progress

        resources = Resources.model_validate(read_metadata(args.resources, digested=False))
        reporter = Progress(
            files=len(spec.files),
            rows=sum(f.items for f in spec.files),
            workers=resources.workers,
            interval=args.progress_interval,
        )
        build(
            spec,
            args.material_root,
            args.output,
            policy=matcher_policy(read_metadata(args.policy, digested=False)),
            resources=resources,
            issuer=args.issuer,
            key=key.encode(),
            code_commit=args.code_commit,
            code_identity=args.code_identity,
            dependency_sha256=args.dependency_sha256,
            review_policy=ReviewPolicy.model_validate(
                read_metadata(args.review_policy, digested=False)
            )
            if args.review_policy
            else None,
            receipt_export=args.receipt_export,
            progress=None if args.no_progress else reporter.update,
        )
        print(json.dumps({"prepared": True, "mode": spec.isolation.mode}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        # Do not print exception values: malformed protected JSON can contain text.
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}))
        return 1
    except KeyboardInterrupt:
        # Workers are already terminated; the destination keeps its incomplete marker.
        print(json.dumps({"refused": True, "error_type": "KeyboardInterrupt"}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
