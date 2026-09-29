"""Read-only check: the committed Phase-D plan binds the real COMPLETE Phase-P parent.

Runs only the engine's parent verification (``_verify_parents``) against the real
v4.1 Phase-P root. No network (sockets refused), no Phase-D root, no decoding,
no Phase-P write: a stat+hash inventory of the parent is taken before and after.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from pathlib import Path
from typing import Any

from xlm.data.evidence_v4 import phase_d
from xlm.data.evidence_v4 import phase_d_plan as pd


def _refuse(*_: Any, **__: Any) -> None:
    raise RuntimeError("network is forbidden in this check")


socket.create_connection = _refuse  # type: ignore[assignment]
socket.getaddrinfo = _refuse  # type: ignore[assignment]


def inventory(root: Path) -> dict[str, list[Any]]:
    return {
        p.relative_to(root).as_posix(): [
            p.stat().st_size,
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None,
        ]
        for p in sorted(root.rglob("*"))
    }


class _NoTransport:
    def open(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("no request may be issued by this check")


def main() -> int:
    plan = pd.load_committed_plan()
    parent = phase_d.parent_root()
    before = inventory(parent)
    engine = phase_d._PhaseDEngine(
        plan,
        Path(pd.EXECUTION_ROOT),
        parent,
        _NoTransport(),
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )
    engine._verify_parents()  # raises RefusedError on any difference
    phase_d._check_dry_plan(plan)
    after = inventory(parent)
    result = {
        "plan_digest": plan.digest,
        "dry_plan_digest": plan.parents.dry_plan_digest,
        "phase_p_root": str(parent),
        "parent_artifacts_verified": len(plan.parents.artifacts),
        "layouts_equal_plan": True,
        "parent_inventory_entries": len(before),
        "parent_inventory_sha256": hashlib.sha256(
            json.dumps(before, sort_keys=True).encode()
        ).hexdigest(),
        "parent_unchanged": before == after,
        "phase_d_root_exists": Path(pd.EXECUTION_ROOT).exists(),
        "network_requests": 0,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["parent_unchanged"] and not result["phase_d_root_exists"] else 1


if __name__ == "__main__":
    sys.exit(main())
