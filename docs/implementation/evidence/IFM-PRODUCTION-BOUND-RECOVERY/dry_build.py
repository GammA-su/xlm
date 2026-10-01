"""OFFLINE, read-only: build IFM General p02 (repair) and Planning p02 (supersession) in memory.

Uses the driver's own gathering functions (``repaired_of``, ``superseded_of``)
and the planner; nothing is stored. Prints each record's digest and limits.

    XLM_HOME=G:/XLM/xlm-home uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-PRODUCTION-BOUND-RECOVERY/dry_build.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("mix01_source", REPO / "scripts" / "mix01_source.py")
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
sys.modules["mix01_source"] = cli
spec.loader.exec_module(cli)


def common(key: str) -> tuple[argparse.Namespace, dict[str, object]]:
    args = argparse.Namespace(
        source_key=key, data_root="G:/XLM", scratch_root="C:/XLM-scratch", requirement_split=""
    )
    source = cli.spec_of(key)
    roots = cli.roots_of(args)
    inventory_path = Path("G:/XLM/inventories") / f"{key}.inventory.json"
    layout, extra = cli.layout_of(args, source)
    return args, {
        "source_key": key,
        "pin": cli.pin_of(source).as_dict(),
        "requirement": cli.requirement_of(args, source),
        "inventory": cli.load_json(inventory_path),
        "inventory_sha256": cli.sha256(inventory_path),
        "layout": layout,
        "calibration": extra["evidence"],
        "policy": cli.runner.read_json(roots.plans / "transport-policy.json"),
        "admission": cli.current_admission(source, cli.store()),
    }


def main() -> int:
    out = {}
    args, inputs = common("ifm_general")
    roots = cli.roots_of(args)
    general = cli.planner.build_repair_plan(**inputs, repaired=cli.repaired_of(roots, 1))
    cli.planner.check_selected_file_bounds(general, inputs["inventory"])
    out["general_p02"] = general
    args, inputs = common("ifm_planning")
    roots = cli.roots_of(args)
    planning = cli.planner.build_plan(**inputs, superseded=cli.superseded_of(roots, 1))
    cli.planner.check_selected_file_bounds(planning, inputs["inventory"])
    out["planning_p02"] = planning
    json.dump(out, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
