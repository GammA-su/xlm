"""Mint the additive code-compatibility record of the whole-pass malformed amendment.

Offline, repository-only. It continues exactly from the committed Windows
publication record, refuses unless the running code differs from it in exactly
the two files this amendment owns, and writes the record once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from xlm.data.evidence_v2 import canonical  # noqa: E402
from xlm.data.sources import essential_web_local as local  # noqa: E402
from xlm.data.sources import essential_web_recovery as recovery  # noqa: E402

BASIS = (
    "No source, revision, membership, acquisition plan, resource limit, selector, "
    "adapter, rejection classification, mixture, quota or scientific contract changes; "
    "adapter code identity (including malformed.py) is byte-identical. The frozen 1% "
    "malformed budget per file pass is unchanged and is now judged on the whole pass: "
    "the frozen per-row rule must fire and malformed rows must exceed 1% of the pass. "
    "Every stop is one the frozen rule also makes, so every existing seal remains valid. "
    "Document, ledger and receipt bytes do not change. Existing batch authorizations apply; "
    "an automatic envelope bound to the previous running code does not."
)


def main() -> int:
    windows_path, kind, allowed = recovery.COMPATIBILITY[-1][0], *recovery.COMPATIBILITY[-1][1:]
    if windows_path != recovery.MALFORMED_FIX:
        raise SystemExit("the malformed amendment must be the last compatibility record")
    previous = json.loads((REPO / recovery.WINDOWS_FIX).read_bytes())
    code = recovery.code_identity(REPO)
    changed = {name for name in code if code[name] != previous["code"][name]}
    if changed != set(allowed):
        raise SystemExit(f"running code changes {sorted(changed)}, not {sorted(allowed)}")
    record = {
        "authorization_basis": BASIS,
        "campaign": previous["campaign"],
        "code": code,
        "kind": kind,
        "malformed_policy": local.MALFORMED_POLICY,
        "previous_code": previous["code"],
        "previous_digest": previous["digest"],
        "recovery_digest": previous["recovery_digest"],
    }
    record["digest"] = canonical.digest(record)
    target = REPO / recovery.MALFORMED_FIX
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"path": recovery.MALFORMED_FIX, "digest": record["digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
