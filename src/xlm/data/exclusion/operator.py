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
            print(
                json.dumps(
                    {
                        "ready": False,
                        "plan_digest": None,
                        "blockers": [
                            *ENGINEERING_BLOCKERS,
                            *(
                                f"{name} missing: {path}"
                                if not path.is_file()
                                else f"{name} present; trusted verification required: {path}"
                                for name, path in (
                                    (
                                        "protected benchmark preparation receipt",
                                        args.benchmark_receipt,
                                    ),
                                    ("operator lineage policy", args.lineage_policy),
                                    ("reviewed resource bounds", args.resources),
                                )
                            ),
                        ],
                    }
                )
            )
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
