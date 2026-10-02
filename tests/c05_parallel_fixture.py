"""Authored synthetic C05 benchmark material for parallel-preparation tests.

Deterministic vocabulary only; no real benchmark payload is read or copied.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.protected import MaterialSpec
from xlm.data.exclusion.runner import file_sha

WORDS = tuple(
    "amber basalt cobalt dune ember fjord granite harbor iris juniper kelp lantern "
    "meadow nickel orchid pebble quartz ridge saffron tundra umber violet willow "
    "xenon yarrow zephyr anvil bramble cinder delta".split()
)


def _phrase(seed: int, length: int) -> str:
    raw = hashlib.sha256(str(seed).encode()).digest()
    return " ".join(WORDS[raw[i] % len(WORDS)] for i in range(length))


def _row(task: str, n: int) -> dict[str, Any]:
    # Every 9th row repeats an earlier one so duplicate accounting is exercised.
    n = n - 4 if n % 9 == 8 else n
    if task == "arc_easy":
        return {"question": _phrase(n, 14), "choices": {"text": [_phrase(n + 1, 9), "no"]}}
    if task == "piqa":
        return {"goal": _phrase(n + 2, 13), "sol1": _phrase(n + 3, 10), "sol2": "no"}
    if task == "blimp":
        return {"sentence_good": _phrase(n + 4, 12), "sentence_bad": _phrase(n + 5, 12)}
    return {
        "ctx_a": _phrase(n + 6, 11),
        "ctx_b": _phrase(n + 7, 6),
        "activity_label": "Authored",
        "endings": [_phrase(n + 8, 10), _phrase(n + 9, 10)],
    }


def synthetic_material(root: Path, rows_per_file: int = 60) -> MaterialSpec:
    """Eight files (JSONL and multi-row-group Parquet) across all four tasks."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True)
    files = []
    for number, task in enumerate(("arc_easy", "piqa", "blimp", "hellaswag") * 2):
        rows = [_row(task, number * 1000 + i) for i in range(rows_per_file)]
        if number % 2:
            path = root / f"{number:02d}-{task}.parquet"
            pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=7)
            form = "parquet"
        else:
            path = root / f"{number:02d}-{task}.jsonl"
            path.write_bytes(b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows))
            form = "jsonl"
        files.append(
            {
                "task": task,
                "repository": "authored/" + task,
                "revision": "1" * 40,
                "config": "authored-config",
                "split": f"split-{number}",
                "path": path.name,
                "sha256": file_sha(path),
                "bytes": path.stat().st_size,
                "items": rows_per_file,
                "format": form,
            }
        )
    return MaterialSpec.model_validate(
        {
            "files": files,
            "publisher_inventory_sha256": "2" * 64,
            "all_published_configs_splits_reviewed": True,
            "isolation": {
                "mode": "authored",
                "operator_principal": "fixture-operator",
                "denied_agent_principal": "fixture-agent",
                "attestation_sha256": "3" * 64,
                "access_controls_verified": True,
            },
        }
    )
