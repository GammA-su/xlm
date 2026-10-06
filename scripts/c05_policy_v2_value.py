"""Derive the C05 contamination policy v2 VALUE files from the frozen clean-v1 policy.

Read-only on its input; writes two NEW plain value files (write-once, refused if they
exist) for the operator to review and then sign with ``policy freeze``:

* ``--output``: c05-production-v3 = every field of the frozen c05-production-v2 policy
  value unchanged (MinHash, seed, splits, survivor, Gutenberg, review, the c05-matcher-v4
  generation matcher ...) plus c05-trigger-floors-v1 (with the reviewed uncovered-item
  count) and query-seed-derivation-family-v1 exclusion lineage;
* ``--matcher-output``: the unchanged c05-matcher-v4 generation policy, for the
  protected preparation (``build-local --policy``).

stdout: the digests to review. No key is used; nothing is signed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.policy import (
    MatcherPolicyV4,
    ProductionPolicy,
    ProductionPolicyV3,
    TriggerPolicy,
    production_policy,
)


def derive(base: dict[str, object], reviewed: int) -> ProductionPolicyV3:
    frozen = production_policy(base)
    if type(frozen) is not ProductionPolicy or not isinstance(frozen.matcher, MatcherPolicyV4):
        raise SystemExit("base must be a c05-production-v2 policy value with c05-matcher-v4")
    fields = frozen.model_dump(mode="json")
    del fields["version"], fields["stage_order"]
    return ProductionPolicyV3(
        **fields, trigger=TriggerPolicy(reviewed_items_without_active_trigger=reviewed)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True, help="frozen v2 policy-value.json")
    parser.add_argument("--reviewed-items-without-active-trigger", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matcher-output", type=Path, required=True)
    args = parser.parse_args(argv)
    for path in (args.output, args.matcher_output):
        if path.exists():
            raise SystemExit(f"refusing to overwrite {path.name}")
    policy = derive(json.loads(args.base.read_bytes()), args.reviewed_items_without_active_trigger)
    canonical.write_canonical_json(args.output, policy.model_dump(mode="json"))
    canonical.write_canonical_json(args.matcher_output, policy.matcher.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "policy_version": policy.version,
                "policy_digest": policy.identity(),
                "generation_matcher_digest": policy.matcher.identity(),
                "trigger_policy_digest": policy.trigger.identity(),
                "reviewed_items_without_active_trigger": (
                    policy.trigger.reviewed_items_without_active_trigger
                ),
                "exclusion_lineage": policy.exclusion_lineage,
                "split_lineage": policy.lineage,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
