"""Offline A/B/C estimates from saved publisher counts, never measured XLM tokens."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent
CAPACITY = {
    "libretexts": 93_000_000,
    "news": 64_000_000,
    "oercommons": 12_000_000,
    "pressbooks": 140_000_000,
    "project_gutenberg": 5_700_000_000,
    "public_domain_review": 1_700_000,
}


def main() -> None:
    inventory = json.loads(Path("G:/XLM/inventories/common_pile.inventory.json").read_bytes())
    files = inventory["files"]
    compressed = {
        c: sum(e["size_bytes"] for e in files if e["file"].split("/")[0] == c) for c in CAPACITY
    }
    available = {
        c: sum(e["size_bytes"] for e in files[:-2] if e["file"].split("/")[0] == c)
        / compressed[c]
        * CAPACITY[c]
        for c in CAPACITY
    }
    count = math.ceil(330_000_000 * 1.15 / (sum(CAPACITY.values()) / len(files)))
    prefix = files[:count]
    taken = {
        c: sum(e["size_bytes"] for e in prefix if e["file"].split("/")[0] == c) for c in CAPACITY
    }
    tokens = {c: taken[c] / compressed[c] * CAPACITY[c] for c in CAPACITY}
    first_a = {c: round(330_000_000 * t / sum(tokens.values())) for c, t in tokens.items()}
    final_a = {c: round(300_000_000 * t / sum(tokens.values())) for c, t in tokens.items()}
    for values, total in ((first_a, 330_000_000), (final_a, 300_000_000)):
        values["project_gutenberg"] += total - sum(values.values())
    # Max-min core allocation: each core gets up to an equal 55M first-pass
    # opportunity, capped by estimated eligible capacity / existing 1.15 safety.
    # Gutenberg alone fills the residual, rather than silently resizing the core.
    first_c = {
        c: min(55_000_000, math.floor(v / 1.15))
        for c, v in available.items()
        if c != "project_gutenberg"
    }
    first_c["project_gutenberg"] = 330_000_000 - sum(first_c.values())
    final_c = {c: v * 10 // 11 for c, v in first_c.items() if c != "project_gutenberg"}
    final_c["project_gutenberg"] = 300_000_000 - sum(final_c.values())
    strategies: dict[str, Any] = {}
    for label, first, final in (
        ("A_hash_prefix", first_a, final_a),
        ("B_equal", dict.fromkeys(CAPACITY, 55_000_000), dict.fromkeys(CAPACITY, 50_000_000)),
        ("C_capacity_capped_core", first_c, final_c),
    ):
        strategies[label] = {
            "components": {
                c: {
                    "final_tokens": final[c],
                    "first_pass_tokens": first[c],
                    "required_canonical_bytes": 4 * first[c],
                    "available_token_proxy": CAPACITY[c],
                    "eligible_token_proxy": available[c],
                    "available_canonical_byte_proxy": 4 * CAPACITY[c],
                }
                for c in sorted(CAPACITY)
            },
            "gutenberg_percent": 100 * first["project_gutenberg"] / 330_000_000,
        }
    value = {
        "basis": "ESTIMATE: pinned publisher Comma token counts; not XLM tokens; "
        "canonical proxy = publisher tokens x 4; must be replaced by calibration",
        "operator_choice_recorded": False,
        "strategies": strategies,
        "A_actual_prefix_files": count,
        "A_estimated_prefix_tokens": sum(tokens.values()),
        "A_estimated_prefix_canonical_bytes": 4 * sum(tokens.values()),
        "A_inventory_compressed_shares": {
            c: v / sum(compressed.values()) for c, v in compressed.items()
        },
        "A_prefix_compressed_shares": {c: v / sum(taken.values()) for c, v in taken.items()},
        "A_prefix_canonical_shares": {c: v / sum(tokens.values()) for c, v in tokens.items()},
        "B_volume_limited": [c for c in CAPACITY if available[c] < 55_000_000 * 1.15],
    }
    (OUT / "strategy-analysis.json").write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
