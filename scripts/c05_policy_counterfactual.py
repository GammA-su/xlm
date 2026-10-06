"""Read-only C05 contamination-policy audit: ``audit`` (stage 1) and ``project-tokens`` (stage 2).

``audit`` (:mod:`xlm.data.exclusion.counterfactual`) needs the protected volume attached:
it reproduces the finished C05 exactly, then evaluates every matcher candidate x lineage
policy and writes nothing except the optional ``--state-out`` file. ``project-tokens``
(:mod:`xlm.data.exclusion.supply`) must run with the protected volume DETACHED: it
tokenizes only documents whose train membership changes, with the current tokenizer.

stdout carries only the JSON report; progress and refusals go to stderr. Exit 0 = report
written; 1 = refused; 130 = interrupted; 2 = usage.
"""

from __future__ import annotations

import sys

from xlm.data.exclusion import counterfactual, supply

COMMANDS = {"audit": counterfactual.main, "project-tokens": supply.main}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments or arguments[0] not in COMMANDS:
        print("usage: c05_policy_counterfactual {audit,project-tokens} ...", file=sys.stderr)
        return 2
    return COMMANDS[arguments[0]](arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
