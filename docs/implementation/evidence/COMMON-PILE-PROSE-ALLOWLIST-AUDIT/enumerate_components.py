# Requires: offline. Reads the operator's frozen Common Pile discovery artifacts read-only.
"""Enumerate the top-level Common Pile components from the frozen discovery listing.

Verifies the listing receipt digest, re-derives the discovery inventory from it
(same seed and freeze construction) and compares the digest, then writes
per-component file counts and declared compressed bytes. No network, no corpus
payload: only file paths and declared sizes are read.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/enumerate_components.py \
        --listing G:/XLM/inventories/common_pile.discovery.listing.json \
        --inventory G:/XLM/inventories/common_pile.discovery.inventory.json \
        --output docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/components.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from xlm.data.sources import hf_inventory

REPO = Path(__file__).resolve().parents[4]
REPOSITORY = "common-pile/comma_v0.1_training_dataset"
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
SEED = 20260918


def _freeze_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "mix01_inventory", REPO / "scripts" / "mix01_inventory.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/mix01_inventory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    listing = hf_inventory.read_listing(args.listing)
    if listing["repository"] != REPOSITORY or listing["resolved_revision"] != REVISION:
        raise SystemExit("listing is not the pinned Common Pile repository/revision")
    if listing["completion_status"] != "complete" or not listing["pagination_complete"]:
        raise SystemExit("listing is not complete")
    names, sizes = hf_inventory.listing_to_freeze_inputs(listing)
    inventory = json.loads(args.inventory.read_bytes().decode("utf-8"))
    rebuilt = _freeze_module().freeze_inventory(
        "common_pile", REPOSITORY, REVISION, SEED, names, sizes
    )
    if rebuilt != inventory:
        raise SystemExit("discovery inventory is not the freeze of the discovery listing")

    components: dict[str, dict[str, int]] = {}
    for name in names:
        parts = name.split("/")
        if len(parts) != 2:
            raise SystemExit(f"unexpected path depth: {name!r}")
        entry = components.setdefault(parts[0], {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += sizes[name]
    total = sum(e["bytes"] for e in components.values())
    if total != listing["total_declared_bytes"] or len(names) != listing["item_count"]:
        raise SystemExit("component totals disagree with the listing")
    rows = [
        {
            "component": name,
            "files": entry["files"],
            "compressed_bytes": entry["bytes"],
            "percent_of_repository_bytes": round(100.0 * entry["bytes"] / total, 4),
        }
        for name, entry in sorted(components.items())
    ]
    result = {
        "repository": REPOSITORY,
        "revision": REVISION,
        "listing_digest": listing["digest"],
        "listing_file_list_digest": listing["file_list_digest"],
        "listing_sha256": hashlib.sha256(args.listing.read_bytes()).hexdigest(),
        "inventory_digest": inventory["inventory_digest"],
        "inventory_sha256": hashlib.sha256(args.inventory.read_bytes()).hexdigest(),
        "inventory_rederived_from_listing": True,
        "seed": SEED,
        "file_count": len(names),
        "total_compressed_bytes": total,
        "component_count": len(rows),
        "components": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for row in rows:
        print(
            f"{row['component']:32} {row['files']:5} {row['compressed_bytes']:>16,} "
            f"{row['percent_of_repository_bytes']:8.4f}%"
        )
    print(f"{len(rows)} components, {len(names)} files, {total:,} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
