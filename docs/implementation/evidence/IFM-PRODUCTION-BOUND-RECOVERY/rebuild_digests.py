"""OFFLINE, read-only: rebuild every stored Mix-01 source plan from its frozen inputs.

Run from a checkout (its own ``uv`` environment and ``scripts/mix01_source.py``);
compares each rebuilt digest with the stored ``plan.json`` digest. Nothing is
written: plans are built in memory, never stored. Ordinary plans are rebuilt
from scratch (top-ups from their predecessor's stored accounting digest);
repair plans from the stored repair block.

    XLM_HOME=G:/XLM/xlm-home uv run --offline --locked --extra cpu --extra eval \
        python <this file> --checkout <repo> --data-root G:/XLM --scratch-root C:/XLM-scratch
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--scratch-root", required=True)
    options = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "mix01_source", Path(options.checkout) / "scripts" / "mix01_source.py"
    )
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    sys.modules["mix01_source"] = driver
    spec.loader.exec_module(driver)
    planner, runner = driver.planner, driver.runner
    results: dict[str, Any] = {}
    for key in sorted(driver.SOURCES):
        args = argparse.Namespace(
            source_key=key,
            data_root=options.data_root,
            scratch_root=options.scratch_root,
            requirement_split="",
        )
        roots = driver.roots_of(args)
        for sequence in roots.sequences():
            stored = runner.load_plan(roots, sequence)
            name = f"{key}/p{sequence:02d}"
            try:
                inventory_path = Path(options.data_root) / "inventories" / f"{key}.inventory.json"
                spec_ = driver.spec_of(key)
                layout, extra = driver.layout_of(args, spec_)
                common = {
                    "source_key": key,
                    "pin": driver.pin_of(spec_).as_dict(),
                    "requirement": driver.requirement_of(args, spec_),
                    "inventory": driver.load_json(inventory_path),
                    "inventory_sha256": driver.sha256(inventory_path),
                    "layout": layout,
                    "calibration": extra["evidence"],
                    "policy": runner.read_json(roots.plans / "transport-policy.json"),
                    "admission": dict(stored["inputs"]["admission"]),
                }
                repair = stored.get("repair")
                if repair is not None:
                    prior = runner.load_plan(roots, int(repair["plan_sequence"]))
                    rebuilt = planner.build_repair_plan(
                        **common,
                        repaired=planner.Repaired(
                            plan=prior,
                            authorized_digest=prior["digest"],
                            sealed_ranks=tuple(repair["sealed_ranks"]),
                            sealed_canonical_bytes=int(stored["acquired_before"]["canonical_bytes"])
                            - int(prior["acquired_before"]["canonical_bytes"]),
                            accounting_digest=repair["accounting_digest"],
                            failures=tuple(repair["failures"]),
                            retained=dict(repair["retained_sha256"]),
                        ),
                    )
                elif stored.get("supersedes") is not None:
                    prior = runner.load_plan(roots, int(stored["supersedes"]["plan_sequence"]))
                    rebuilt = planner.build_plan(
                        **common, superseded=planner.Superseded(plan=prior)
                    )
                elif sequence == 1:
                    rebuilt = planner.build_plan(**common)
                else:
                    prior = runner.load_plan(roots, sequence - 1)
                    rebuilt = planner.build_plan(
                        **common,
                        predecessor=planner.Predecessor(
                            plan=prior,
                            sealed_canonical_bytes=int(stored["acquired_before"]["canonical_bytes"])
                            - int(prior["acquired_before"]["canonical_bytes"]),
                            sealed_files=len(prior["selection"]["files"]),
                            accounting_digest=stored["lineage"]["previous_accounting_digest"],
                        ),
                    )
                results[name] = {
                    "stored": stored["digest"],
                    "rebuilt": rebuilt["digest"],
                    "reproduces": rebuilt["digest"] == stored["digest"],
                    "anchored": "file_size_anchor" in rebuilt["limits"],
                }
            except Exception as exc:  # recorded, never hidden
                results[name] = {
                    "stored": stored["digest"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
    json.dump(results, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
