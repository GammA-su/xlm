"""Read-only stat+hash inventory of the real v4.1 Phase-P parent (nothing is written there).

Usage: ``python parent_inventory.py <label>`` writes ``parent-<label>.json`` next
to this file; ``python parent_inventory.py compare <a> <b>`` exits 1 on any
difference. The Phase-D root is only tested for existence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PARENT = Path("G:\\Project\\xlm-evidence-v4.1\\essential-web")
PHASE_D = Path("G:\\Project\\xlm-evidence-v4.1\\essential-web-phase-d")


def inventory() -> dict[str, Any]:
    entries = {
        p.relative_to(PARENT).as_posix(): [
            p.stat().st_size if p.is_file() else None,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
        ]
        for p in sorted(PARENT.rglob("*"))
    }
    files = [e for e in entries.values() if e[2] is not None]
    return {
        "root": str(PARENT),
        "entries": len(entries),
        "files": len(files),
        "file_bytes": sum(e[0] for e in files),
        "inventory_sha256": hashlib.sha256(
            json.dumps(entries, sort_keys=True).encode()
        ).hexdigest(),
        "phase_d_root_exists": PHASE_D.exists(),
        "inventory": entries,
    }


def main(argv: list[str]) -> int:
    if argv[:1] == ["compare"]:
        a, b = (json.loads((HERE / f"parent-{n}.json").read_bytes()) for n in argv[1:3])
        same = a["inventory"] == b["inventory"]
        print(json.dumps({"identical": same, "inventory_sha256": a["inventory_sha256"]}))
        return 0 if same and not a["phase_d_root_exists"] and not b["phase_d_root_exists"] else 1
    result = inventory()
    (HERE / f"parent-{argv[0]}.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "inventory"}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
