"""OFFLINE, read-only regression against the real operator store. Writes nothing there."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path("F:/Project/xlm-data-ultrax")
sys.path.insert(0, str(REPO / "scripts"))

import mix01_source as driver  # noqa: E402

from xlm.data.acquisition import source_plan as planner  # noqa: E402
from xlm.data.acquisition import source_run as runner  # noqa: E402

V1 = (
    32 * 1024 * 1024,
    "finepdfs-record-v1: whole-file scan of data/eng_Latn/train/000_00083.parquet "
    "(220,407 rows, sha256 4eeb58bc...a38d) found a 24,828,818-byte largest "
    "projected row and 3 rows above the generic 8 MiB; the bound is the existing "
    "32 MiB parser ceiling, about 1.35x that maximum",
)
KEY = ("finepdfs_edu", "eng_Latn")
out: dict[str, object] = {}


def args(key: str) -> argparse.Namespace:
    return argparse.Namespace(source_key=key, data_root="G:/XLM", scratch_root="C:/XLM-scratch")


a = args("finepdfs")
spec = driver.spec_of("finepdfs")
roots = driver.roots_of(a)
pin = driver.pin_of(spec)
frozen = runner.read_json(roots.plans / "transport-policy.json")
inventory_path = Path("G:/XLM/inventories/finepdfs.inventory.json")
layout, extra = driver.layout_of(a, spec)
admission = driver.current_admission(spec, driver.store())
p01 = runner.load_plan(roots, 1)
common = dict(
    source_key="finepdfs",
    pin=pin.as_dict(),
    requirement=driver.requirement_of(a, spec),
    inventory=driver.load_json(inventory_path),
    inventory_sha256=driver.sha256(inventory_path),
    layout=layout,
    calibration=extra["evidence"],
    policy=frozen,
    admission=admission,
)
current = dict(planner.SOURCE_RECORD_BYTES)
planner.SOURCE_RECORD_BYTES[KEY] = V1
rebuilt_v1 = planner.build_plan(**common)
planner.SOURCE_RECORD_BYTES.clear()
planner.SOURCE_RECORD_BYTES.update(current)
rebuilt_v2 = planner.build_plan(**common)
out["p01_stored_digest"] = p01["digest"]
out["p01_rebuilt_under_v1_digest"] = rebuilt_v1["digest"]
out["p01_reconstructs"] = rebuilt_v1["digest"] == p01["digest"]
planner.check_against_inputs(p01, rebuilt_v1)
out["p01_minted_hash_reproduces"] = (
    planner.minted_from_record(p01).plan_hash == p01["acquisition_plan"]["plan_hash"]
)
out["fresh_plan1_under_v2_digest"] = rebuilt_v2["digest"]
out["v2_is_new_identity"] = rebuilt_v2["digest"] != p01["digest"]
out["p01_authorization"] = runner.read_json(roots.plan_dir(1) / "authorization.json")
out["p01_accounting"] = runner.account(roots, p01)
out["p01_verify_content"] = runner.verify_plan(roots, p01, content=True)
out["finepdfs_sufficiency"] = runner.sufficiency(roots)
repaired = driver.repaired_of(roots, 1)
repair = planner.build_repair_plan(**common, repaired=repaired)
out["repair_dry_run_digest"] = repair["digest"]
out["repair_dry_run_again_digest"] = planner.build_repair_plan(**common, repaired=repaired)[
    "digest"
]
out["repair_record"] = {
    k: repair[k] for k in ("repair", "selection", "expected", "acquired_before")
}
out["repair_limits"] = repair["limits"]
out["repair_acquisition_plan"] = repair["acquisition_plan"]
out["repair_minted_hash_reproduces"] = (
    planner.minted_from_record(repair).plan_hash == repair["acquisition_plan"]["plan_hash"]
)
ultrax = driver.roots_of(args("ultrax"))
seal = runner.read_json(ultrax.plans / "first-pass-seal.json")
status = runner.sufficiency(ultrax)
out["ultrax_sufficiency_matches_seal"] = {k: v for k, v in status.items() if k != "digest"} == seal[
    "sufficiency"
]
out["ultrax_sufficiency_file_matches"] = status == runner.read_json(
    ultrax.plans / "sufficiency.json"
)
out["ultrax_reseal_is_identical_noop"] = (
    runner.first_pass_seal(ultrax, content=False)["digest"] == seal["digest"]
)
out["ultrax_seal_digest"] = seal["digest"]
print(json.dumps(out, indent=2, sort_keys=True, default=str))
