"""OFFLINE, read-only REHEARSAL: the IFM continuations after admission renewal.

Builds, in memory only, (a) each IFM view's new bridge receipt under the
running adapter code (what ``evidence show`` prints), (b) the General p03
admission repair of p02 and (c) the Planning p03 supersession of unauthorized
p02, both under the admission the operator WOULD record if the renewed bridge
is admitted with the same decision contract and benchmark risk as today.
Nothing is published, recorded, stored or authorized; the real p03 digests
exist only after the operator renews admission and runs the driver.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/rehearse_continuations.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]


def driver_module() -> Any:
    spec = importlib.util.spec_from_file_location("mix01_source", REPO / "scripts/mix01_source.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("driver not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules["mix01_source"] = module
    spec.loader.exec_module(module)
    return module


def renewed_admission(driver: Any, key: str, stored_plan: dict[str, Any]) -> dict[str, Any]:
    """The plan's admission with the bridge digest the running code certifies."""
    args = driver.build_parser().parse_args(["evidence", "show", "--source-key", key])
    pin, receipt, _ = driver.build_bridge(args, driver.spec_of(key), driver.store())
    old = dict(stored_plan["inputs"]["admission"])
    if receipt["probe_fingerprint"] != old["probe_fingerprint"]:
        raise RuntimeError(f"{key}: probe fingerprint changed; the rehearsal assumption fails")
    return {
        "bridge": {
            "digest": receipt["digest"],
            "probe_fingerprint": receipt["probe_fingerprint"],
            "code_sha256": receipt["adapter"]["code_sha256"],
            "row_sets": receipt["adapter"]["row_sets"],
            "pin": pin.as_dict(),
        },
        "admission": {**old, "bridge_receipt_digest": receipt["digest"]},
        "assumption": "operator re-admits with the same decision_contract and benchmark_risk",
    }


def main() -> None:
    driver = driver_module()
    from xlm.data.acquisition import source_plan as planner
    from xlm.data.acquisition import source_run as runner

    report: dict[str, Any] = {"rehearsal_only": True}
    for key in ("ifm_general", "ifm_planning"):
        spec = driver.spec_of(key)
        args = driver.build_parser().parse_args(["plan-repair", "--source-key", key, "--plan", "2"])
        roots = driver.roots_of(args)
        stored = runner.load_plan(roots, 2)
        renewal = renewed_admission(driver, key, stored)
        inventory_path = driver.data_root(args) / "inventories" / f"{key}.inventory.json"
        layout, extra = driver.layout_of(args, spec)
        common: dict[str, Any] = {
            "source_key": key,
            "pin": driver.pin_of(spec).as_dict(),
            "requirement": driver.requirement_of(args, spec),
            "inventory": driver.load_json(inventory_path),
            "inventory_sha256": driver.sha256(inventory_path),
            "layout": layout,
            "calibration": extra["evidence"],
            "policy": runner.read_json(roots.plans / "transport-policy.json"),
            "admission": renewal["admission"],
        }
        entry: dict[str, Any] = {"renewal": renewal, "p02_digest": stored["digest"]}
        if key == "ifm_general":
            record = planner.build_repair_plan(**common, repaired=driver.repaired_of(roots, 2))
            resume = runner.resume_state(roots, record)
            restart = runner.classify(roots, record, resume, "p03")
            entry["p03"] = {
                "kind": "admission repair of p02",
                "digest": record["digest"],
                "sequence": record["sequence"],
                "ranks": record["selection"]["ranks"],
                "files": record["selection"]["files"],
                "next_cursor": record["selection"]["next_cursor"],
                "acquired_before": record["acquired_before"],
                "repair": record["repair"],
                "expected": record["expected"],
                "acquisition_limits": record["acquisition_plan"]["limits"],
                "expected_file_digests": record["acquisition_plan"].get("expected_file_digests"),
                "restart": restart,
            }
        else:
            record = planner.build_plan(**common, superseded=driver.superseded_of(roots, 2))
            entry["p03"] = {
                "kind": "supersession of unauthorized p02",
                "digest": record["digest"],
                "sequence": record["sequence"],
                "selection": record["selection"],
                "supersedes": record["supersedes"],
                "expected": record["expected"],
            }
        planner.check_plan(record)
        report[key] = entry
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
