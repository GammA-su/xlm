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
    MatcherPolicy,
    Resources,
    ReviewPolicy,
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


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {
        "lineage-policy",
        "resources",
        "policy",
        "benchmark-receipt",
        "plan",
        "authorize",
        "run",
        "resume",
        "status",
        "resume-check",
        "verify",
        "publish",
        "quota-report",
        "final-receipt",
        "count-tokens",
        "select",
        "tokenize-selection",
        "freeze",
        "claim-binding",
        "claim-check",
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
        spec = MaterialSpec.model_validate(read_metadata(args.spec, digested=False))
        if args.command == "inspect-local":
            inspection = inspect(spec, args.material_root)
            write_once(args.output, inspection)
            return 0 if inspection["complete_local_sizes"] else 2
        key = os.environ.get(args.key_env)
        if key is None or len(key) < 32:
            raise C05Error("protected signing key missing or too short")
        build(
            spec,
            args.material_root,
            args.output,
            policy=MatcherPolicy.model_validate(read_metadata(args.policy, digested=False)),
            resources=Resources.model_validate(read_metadata(args.resources, digested=False)),
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
        )
        print(json.dumps({"prepared": True, "mode": spec.isolation.mode}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        # Do not print exception values: malformed protected JSON can contain text.
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
