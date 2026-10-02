# Requires: offline. Reads the operator's frozen Common Pile discovery artifacts read-only.
"""ESTIMATE ONLY: first-plan prefix composition of each candidate allowlist.

Nothing here is a measurement. Per-component canonical bytes are estimated as
the upstream Comma v0.1 card's main-stage token count (Comma tokenizer) times
XLM's 4 bytes/token planning assumption; that card is pinned at the repository
revision. The XLM planner sizes a plan with ONE average canonical-bytes-per-file
value, so this emulates the ideal case (the true per-file mean over the filtered
inventory) and reports which files the hash-ordered prefix would take.

    <python> docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/simulate_allowlists.py \
        --listing G:/XLM/inventories/common_pile.discovery.listing.json \
        --inventory G:/XLM/inventories/common_pile.discovery.inventory.json \
        --output <evidence-dir>/allowlist-simulation.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from xlm.data.sources import hf_inventory

#: Main-stage "Tokens (B)" column of the Comma v0.1 dataset card at revision
#: 5afc546db324e7f39f297ba757c9a60547151e7c (README.md sha256 d6460380...2866).
COMMA_TOKENS_B = {
    "arxiv_abstracts": 0.57,
    "arxiv_papers": 6.0,
    "biodiversity_heritage_library": 9.8,
    "caselaw_access_project": 19.7,
    "cccc": 15.2,
    "data_provenance_initiative": 0.92,
    "doab": 3.0,
    "foodista": 0.025,
    "github_archive": 11.0,
    "library_of_congress": 9.5,
    "libretexts": 0.093,
    "news": 0.064,
    "oercommons": 0.012,
    "peS2o": 43.3,
    "pre_1929_books": 12.4,
    "pressbooks": 0.14,
    "project_gutenberg": 5.7,
    "public_domain_review": 0.0017,
    "pubmed": 36.6,
    "python_enhancement_proposals": 0.0027,
    "regulations": 1.4,
    "stackexchange": 23.9,
    "stackv2_edu": 67.8,
    "stackv2_html": 1.2,
    "ubuntu_irc": 1.9,
    "uk_hansard": 2.3,
    "usgpo": 8.8,
    "uspto": 157.4,
    "wikimedia": 15.8,
    "wikiteam": 4.3,
    "youtube": 4.7,
}
BYTES_PER_TOKEN = 4
REQUIRED_CANONICAL_BYTES = 1_320_000_000
SAFETY_MARGIN = 1.15

CONSERVATIVE = ["libretexts", "news", "oercommons", "pressbooks", "public_domain_review"]
BALANCED = sorted([*CONSERVATIVE, "project_gutenberg"])
MAXIMAL = sorted(
    [
        *BALANCED,
        "doab",
        "foodista",
        "library_of_congress",
        "pre_1929_books",
        "uk_hansard",
        "youtube",
    ]
)
CANDIDATES = {
    "A_conservative": sorted(CONSERVATIVE),
    "B_balanced": BALANCED,
    "C_maximal_reasonable": MAXIMAL,
    "B_plus_doab": sorted([*CONSERVATIVE, "doab", "project_gutenberg"]),
}


def simulate(
    name: str, members: list[str], ordered: list[str], sizes: dict[str, int]
) -> dict[str, Any]:
    files = [f for f in ordered if f.split("/", 1)[0] in members]
    compressed: dict[str, int] = {}
    for f in files:
        compressed[f.split("/", 1)[0]] = compressed.get(f.split("/", 1)[0], 0) + sizes[f]
    estimate = {c: COMMA_TOKENS_B[c] * 1e9 * BYTES_PER_TOKEN for c in members}
    per_file_est = {
        f: estimate[f.split("/", 1)[0]] * sizes[f] / compressed[f.split("/", 1)[0]] for f in files
    }
    available = sum(estimate.values())
    target = REQUIRED_CANONICAL_BYTES * SAFETY_MARGIN
    mean = available / len(files)
    wanted = min(len(files), math.ceil(target / mean))
    prefix = files[:wanted]
    taken: dict[str, dict[str, float]] = {}
    for f in prefix:
        c = f.split("/", 1)[0]
        entry = taken.setdefault(c, {"files": 0, "compressed_bytes": 0, "est_canonical_bytes": 0.0})
        entry["files"] += 1
        entry["compressed_bytes"] += sizes[f]
        entry["est_canonical_bytes"] += per_file_est[f]
    got = sum(e["est_canonical_bytes"] for e in taken.values())
    return {
        "allowlist": name,
        "components": members,
        "files_available": len(files),
        "compressed_bytes_available": sum(compressed.values()),
        "est_canonical_bytes_available": round(available),
        "est_comma_tokens_available": round(available / BYTES_PER_TOKEN),
        "available_over_requirement": round(available / REQUIRED_CANONICAL_BYTES, 3),
        "available_over_requirement_with_safety": round(available / target, 3),
        "planner_mean_canonical_bytes_per_file": round(mean),
        "first_plan_files": wanted,
        "first_plan_whole_inventory": wanted == len(files),
        "first_plan_est_canonical_bytes": round(got),
        "first_plan_composition": {
            c: {
                "files": int(e["files"]),
                "compressed_bytes": int(e["compressed_bytes"]),
                "est_canonical_share": round(e["est_canonical_bytes"] / got, 4) if got else 0.0,
            }
            for c, e in sorted(taken.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listing", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    listing = hf_inventory.read_listing(args.listing)
    _, sizes = hf_inventory.listing_to_freeze_inputs(listing)
    inventory = json.loads(args.inventory.read_bytes().decode("utf-8"))
    ordered = [str(e["file"]) for e in inventory["files"]]
    if sorted(ordered) != sorted(sizes):
        raise SystemExit("inventory and listing disagree")
    spread: dict[str, list[int]] = {}
    for f, size in sizes.items():
        spread.setdefault(f.split("/", 1)[0], []).append(size)
    if sorted(spread) != sorted(COMMA_TOKENS_B):
        raise SystemExit("component universe differs from the Comma card table")
    result = {
        "kind": "ESTIMATE_NOT_MEASUREMENT",
        "basis": (
            "Comma v0.1 card main-stage tokens x 4 B/token; planner emulated with the true "
            "per-file mean; required 1,320,000,000 B x safety 1.15"
        ),
        "inventory_digest": inventory["inventory_digest"],
        "within_component_file_bytes": {
            c: {"min": min(v), "max": max(v), "max_over_min": round(max(v) / min(v), 3)}
            for c, v in sorted(spread.items())
        },
        "allowlists": [
            simulate(name, members, ordered, sizes) for name, members in CANDIDATES.items()
        ],
    }
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    for item in result["allowlists"]:
        print(
            f"{item['allowlist']}: avail {item['est_canonical_bytes_available']:,} B est "
            f"({item['available_over_requirement']}x req, "
            f"{item['available_over_requirement_with_safety']}x req*safety); "
            f"first plan {item['first_plan_files']}/{item['files_available']} files"
        )
        for c, e in item["first_plan_composition"].items():
            print(f"    {c:22} {e['files']:3} files  share {e['est_canonical_share']:.3f}")
    worst = max(result["within_component_file_bytes"].items(), key=lambda kv: kv[1]["max_over_min"])
    print(f"largest within-component size spread: {worst[0]} {worst[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
