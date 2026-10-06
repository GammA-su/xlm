"""Read-only training consumer admission/verification; never constructs a model."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psutil

from xlm.core.paths import ArtifactPaths
from xlm.data.input_policy import POLICY_ID, policy_from_binding
from xlm.training.inputs import MixtureInput, resolve_training_input


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-data", type=Path, required=True)
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    began = time.monotonic()
    try:
        if args.training_data.stat().st_size > 8 * 1024**2:
            raise ValueError("training data metadata ceiling")
        data = json.loads(args.training_data.read_bytes())
        policy = policy_from_binding(data.get("training_input_policy"))
        if policy is None:
            raise ValueError("this production preflight requires training-input-policy-v2")
        source, identity = resolve_training_input(data, ArtifactPaths.from_env())
        if not isinstance(source, MixtureInput):
            raise ValueError("production preflight requires a mixture")
        stats = {
            name: {
                "documents": r.manifest.num_documents,
                "token_ids": r.manifest.num_tokens,
                "valid_targets": r.counters["valid_targets"],
            }
            for name, r in source.readers.items()
        }
        totals = {
            name: sum(component[name] for component in stats.values())
            for name in ("documents", "token_ids", "valid_targets")
        }
        if args.production and (
            data["c05_binding"]["mode"] != "protected"
            or totals
            != {
                "documents": 5_824_661,
                "token_ids": 6_005_824_661,
                "valid_targets": 6_000_000_000,
            }
        ):
            raise ValueError("production preflight requires the exact protected Mix-01 totals")
        print(
            json.dumps(
                {
                    "verified": True,
                    "policy": POLICY_ID,
                    "policy_digest": policy.binding()["digest"],
                    "input_identity": identity,
                    "c05_binding": data["c05_binding"],
                    "components": stats,
                    **totals,
                    "production": args.production,
                    "seconds": time.monotonic() - began,
                    "parent_rss_bytes": psutil.Process().memory_info().rss,
                    "model_constructed": False,
                    "training_started": False,
                },
                sort_keys=True,
            )
        )
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
