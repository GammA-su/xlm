"""Write a NEW protected material spec: an existing spec with a new measured isolation.

Files, publisher inventory and review coverage are copied unchanged; only ``isolation``
is replaced by the ``protected-root describe`` proposal (its ``isolation`` object). The
result is validated as a :class:`MaterialSpec` and written once (never in place).
Reads and writes JSON metadata only; no protected material is opened.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.protected import MaterialSpec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="existing material spec")
    parser.add_argument("--describe", type=Path, required=True, help="describe output JSON")
    parser.add_argument("--output", type=Path, required=True, help="new spec (write-once)")
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.resolve() == args.spec.resolve():
        raise SystemExit("refusing to overwrite a material spec")
    spec = json.loads(args.spec.read_bytes())
    proposal = json.loads(args.describe.read_bytes().decode("utf-8-sig"))
    if proposal.get("proposal_only") is not True or "isolation" not in proposal:
        raise SystemExit("describe output must be a protected-root describe proposal")
    updated = {**spec, "isolation": proposal["isolation"]}
    validated = MaterialSpec.model_validate(updated)
    if [f.model_dump(mode="json") for f in validated.files] != [
        f.model_dump(mode="json") for f in MaterialSpec.model_validate(spec).files
    ]:
        raise SystemExit("material files changed")
    canonical.write_canonical_json(args.output, updated)
    print(json.dumps({"spec_digest": canonical.digest(validated.model_dump(mode="json"))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
