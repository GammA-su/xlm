# Requires: offline. Reads the operator's frozen discovery inventory read-only.
"""Deterministic real-row certification candidates for not-yet-certified components.

Rule: the first file of each component in the frozen discovery-inventory order.
An allowlist-filtered inventory keeps that order (it is the same hash chain
restricted to the included components), so this is also the first file of the
component in any production inventory that includes it. Rows [0, 16) of that
file are the proposed bounded sample. Nothing here fetches anything.

    <python> <evidence-dir>/certification_candidates.py \
        G:/XLM/inventories/common_pile.discovery.inventory.json \
        <evidence-dir>/certification-candidates.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: Candidate-list components without real certified rows (the 9-row sample
#: certified only news, libretexts and public_domain_review).
UNCERTIFIED = (
    "pressbooks",
    "oercommons",
    "project_gutenberg",
    "doab",
    "foodista",
    "library_of_congress",
    "pre_1929_books",
    "uk_hansard",
    "youtube",
)
ROWS = (0, 16)


def main() -> int:
    inventory: dict[str, Any] = json.loads(Path(sys.argv[1]).read_bytes().decode("utf-8"))
    first: dict[str, dict[str, Any]] = {}
    for rank, entry in enumerate(inventory["files"]):
        component = str(entry["file"]).split("/", 1)[0]
        if component in UNCERTIFIED and component not in first:
            first[component] = {
                "file": entry["file"],
                "discovery_rank": rank,
                "size_bytes": entry["size_bytes"],
            }
    missing = [c for c in UNCERTIFIED if c not in first]
    if missing:
        raise SystemExit(f"components absent from the inventory: {missing}")
    result = {
        "inventory_digest": inventory["inventory_digest"],
        "rule": (
            "first file of each component in frozen discovery-inventory order "
            "(= first in any allowlist-filtered inventory); rows [0, 16)"
        ),
        "rows": list(ROWS),
        "components": {c: first[c] for c in UNCERTIFIED},
    }
    with Path(sys.argv[2]).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for component in UNCERTIFIED:
        print(component, first[component])
    return 0


if __name__ == "__main__":
    sys.exit(main())
