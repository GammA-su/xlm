"""OFFLINE, read-only identity check after the intra-file parallelism change.

Verifies that the frozen FinePDFs transport policy and its sizing measurement
still verify under the current code, that the protected code files keep their
bound hashes, and shows how the planner's process limits change for FinePDFs.
Writes nothing to the operator store; prints one JSON document.

    uv run --offline --locked --no-sync --extra cpu --extra eval python identity_check.py \
        G:/XLM/plans/finepdfs/transport-policy.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition.transport_policy import TransportMode, check_frozen

REPO = Path(__file__).resolve().parents[4]
POLICY_DIGEST = "f3a524116f38ac9b49e55d5a33e81ecbc503074f21af9f3fe5263b02a6e7fc73"
SIZING_DIGEST = "4709fb8460c1cca6bc54624ce9e8ad3069fea7434c8a98c9c2eb856f835b2a58"
#: Hashes bound by the Essential-Web code-compatibility chain and the adapters.
PROTECTED = {
    "src/xlm/data/acquisition/source_parquet.py": (
        "537f4a3bbeb95f423834844febbf4da81c6c42f1fe3322bfced7695856387123"
    ),
    "src/xlm/data/sources/essential_web_local.py": (
        "02594e9a2761826a4966abbb36e694062a0a8e19a4c6bb7fd380ac481a36ce7d"
    ),
    "src/xlm/data/adapters/mix01_adapters.py": (
        "3651ff2af4fcb46c207404e052caa59e1ec42f2ac5a429e5dec752f7e83a7ef6"
    ),
    "src/xlm/data/adapters/columns.py": (
        "3e594f755eea797ac0d29d819c28b4ccf8e387cfc7889470dfc58e8657e58db1"
    ),
}


def main() -> int:
    policy = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    pin = {key: policy["subject"][key] for key in policy["subject"]}
    mode = check_frozen(policy, pin["source_id"])
    sizing = policy["sizing"]
    planner.check_sizing(sizing, pin)
    code = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() == digest
        for name, digest in PROTECTED.items()
    }
    entry = planner.SOURCE_ROW_GROUP_PARALLEL.get((pin["source_id"], pin["view_id"]))
    print(
        json.dumps(
            {
                "transport_policy": {
                    "digest": policy["digest"],
                    "expected": POLICY_DIGEST,
                    "verifies": policy["digest"] == POLICY_DIGEST,
                    "selected_mode": mode.value,
                    "whole_file_local": mode is TransportMode.WHOLE_FILE_LOCAL,
                },
                "sizing": {
                    "digest": sizing["digest"],
                    "expected": SIZING_DIGEST,
                    "verifies": sizing["digest"] == SIZING_DIGEST,
                },
                "protected_code_unchanged": code,
                "planner_row_group_parallel": None
                if entry is None
                else {"config": entry[0].model_dump(), "basis": entry[1]},
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if all(code.values()) and policy["digest"] == POLICY_DIGEST else 1


if __name__ == "__main__":
    raise SystemExit(main())
