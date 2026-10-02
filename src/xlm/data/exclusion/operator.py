"""Offline C05 operator interfaces; inspect/schema need no protected signing key.

Engineering remains under validation. The CLI intentionally refuses protected
plan allocation until production recovery/resource and downstream integration
acceptance is complete; an authored run is never a protected certificate.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from xlm.data.acquisition.source_run import write_once
from xlm.data.exclusion.artifacts import BenchmarkReceipt, ExecutionPlan
from xlm.data.exclusion.inputs import read_metadata
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, Resources
from xlm.data.exclusion.protected import MaterialSpec, build, inspect


def main(argv: list[str] | None = None) -> int:
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
    build_parser.add_argument("--issuer", required=True)
    build_parser.add_argument("--key-env", required=True)
    build_parser.add_argument("--code-commit", required=True)
    build_parser.add_argument("--code-identity", required=True)
    build_parser.add_argument("--dependency-sha256", required=True)
    sub.add_parser("plan-readiness")
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            write_once(
                args.output,
                {
                    "material_spec": MaterialSpec.model_json_schema(),
                    "benchmark_receipt": BenchmarkReceipt.model_json_schema(),
                    "execution_plan": ExecutionPlan.model_json_schema(),
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
                            "Protected content-free benchmark inventory/receipt not supplied",
                            "Production resource and final-mixture gate acceptance incomplete",
                        ],
                    }
                )
            )
            return 2
        spec = MaterialSpec.model_validate(read_metadata(args.spec, digested=False))
        if args.command == "inspect-local":
            write_once(args.output, inspect(spec, args.material_root))
            return 0
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
        )
        print(json.dumps({"prepared": True, "mode": spec.isolation.mode}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # Do not print exception values: malformed protected JSON can contain text.
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
