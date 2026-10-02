# Requires: offline. Reads the operator's Common Pile artifacts read-only.
"""Verify the operator's recorded Balanced allowlist and production inventory.

Refuses (exit 1) unless the allowlist is exactly the Balanced selection at the
pinned repository revision, verifies against the frozen discovery listing,
was decided against the committed evidence matrix, and the production
inventory is exactly the allowlist-filtered freeze of that listing.

    uv run --offline --locked --extra cpu --extra eval python \
        <evidence-dir>/verify_operator_artifacts.py --data-root G:/XLM \
        --output <evidence-dir>/operator-artifacts.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import source_plan as planner
from xlm.data.sources import hf_inventory

REPO = Path(__file__).resolve().parents[4]
REPOSITORY = "common-pile/comma_v0.1_training_dataset"
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
SEED = 20260918
BALANCED = [
    "libretexts",
    "news",
    "oercommons",
    "pressbooks",
    "project_gutenberg",
    "public_domain_review",
]
LISTING_DIGEST = "a32b9b12649da4b72a07fbbe41bd3bbd9d7df9da9df32136fa8080802d8b5038"
MATRIX = REPO / "docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/license-matrix.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freeze_tool() -> Any:
    spec = importlib.util.spec_from_file_location(
        "mix01_inventory", REPO / "scripts" / "mix01_inventory.py"
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load scripts/mix01_inventory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root: Path = args.data_root
    listing_path = root / "inventories" / "common_pile.discovery.listing.json"
    allowlist_path = root / "calib" / "component_allowlists" / "common_pile.json"
    inventory_path = root / "inventories" / "common_pile.inventory.json"

    listing = hf_inventory.read_listing(listing_path)
    record = allow.check_allowlist(allow.read_json(allowlist_path), listing)
    inventory = allow.read_json(inventory_path)
    failures: list[str] = []

    def expect(what: str, actual: Any, wanted: Any) -> None:
        if actual != wanted:
            failures.append(f"{what}: {actual!r} != {wanted!r}")

    expect("listing digest", listing["digest"], LISTING_DIGEST)
    expect("allowlist repository", record["repository"], REPOSITORY)
    expect("allowlist revision", record["revision"], REVISION)
    expect("allowlist source_id", record["source_id"], "common_pile")
    expect("allowlist component_id", record["component_id"], "common_pile_prose")
    expect("included", record["included"], BALANCED)
    expect("accepted flags", record["accepted_flags"], [])
    expect("matrix sha256", record["evidence_matrix"]["sha256"], _sha(MATRIX))
    allow.check_inventory_binding(record, listing, inventory)
    ordered = planner.check_inventory(inventory, "common_pile", REPOSITORY, REVISION)
    names, sizes = allow.filtered_freeze_inputs(record, listing)
    rebuilt = _freeze_tool().freeze_inventory(
        "common_pile",
        REPOSITORY,
        REVISION,
        SEED,
        names,
        sizes,
        allowlist=allow.inventory_binding(record),
    )
    expect("inventory equals the allowlist freeze", inventory == rebuilt, True)
    expect("inventory seed", inventory["seed"], SEED)
    counts: dict[str, int] = {}
    for name in ordered:
        counts[allow.component_of(name)] = counts.get(allow.component_of(name), 0) + 1
    expect("inventory components", sorted(counts), BALANCED)
    if failures:
        for failure in failures:
            print(f"REFUSED {failure}", file=sys.stderr)
        return 1
    result = {
        "repository": REPOSITORY,
        "revision": REVISION,
        "listing_digest": listing["digest"],
        "listing_sha256": _sha(listing_path),
        "allowlist_digest": record["digest"],
        "allowlist_sha256": _sha(allowlist_path),
        "allowlist_operator": record["operator"],
        "included": record["included"],
        "excluded_count": len(record["excluded"]),
        "evidence_matrix_sha256": record["evidence_matrix"]["sha256"],
        "inventory_digest": inventory["inventory_digest"],
        "inventory_sha256": _sha(inventory_path),
        "inventory_file_count": inventory["file_count"],
        "inventory_known_size_bytes": inventory["known_size_bytes"],
        "inventory_files_per_component": dict(sorted(counts.items())),
        "inventory_rederived": True,
    }
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
