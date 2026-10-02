# Requires: operator-run only, offline (reads frozen artifacts and prefix-sample receipts).
"""Build or show the component-aware calibration of a multi-component source.

``build`` binds the prefix-sample receipts of every allowlisted component to
the recorded component allowlist and production inventory, then writes:

- ``<data-root>/calib/<component>/component-calibration.json`` (write-once,
  self-digested), the planner's layout input;
- ``<data-root>/calib/<component>/measurement.json``, the input of
  ``mix01_inventory.py record --measurement`` (component-weighted estimate).

It prints the proposed per-file bounds that become the reviewed
``source_plan.SOURCE_FILE_BOUNDS`` entry before any plan can be made.

    uv run --offline --locked --extra cpu --extra eval python scripts/component_calibration.py \
        build --source-key common_pile --data-root G:\\XLM \
        --receipt G:\\XLM\\calib\\common_pile_cert02\\sample-receipt.json \
        --receipt G:\\XLM\\calib\\common_pile_cal01\\sample-receipt.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import component_calibration as calibration
from xlm.data.sources import hf_inventory


def _fail(message: str) -> int:
    print(f"component_calibration: refused: {message}", file=sys.stderr)
    return 1


def _render(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_once(path: Path, data: bytes) -> bool:
    if path.exists():
        if path.read_bytes() != data:
            raise calibration.CalibrationError(f"{path} exists with other content; refusing")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "show"))
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    root: Path = args.data_root
    key = str(args.source_key)
    try:
        listing = hf_inventory.read_listing(root / "inventories" / f"{key}.discovery.listing.json")
        record = allow.check_allowlist(
            allow.read_json(root / "calib" / "component_allowlists" / f"{key}.json"), listing
        )
        inventory = allow.read_json(root / "inventories" / f"{key}.inventory.json")
        allow.check_inventory_binding(record, listing, inventory)
        directory = root / "calib" / str(record["component_id"])
        target = directory / "component-calibration.json"
        if args.action == "show":
            stored = calibration.check_calibration(
                allow.read_json(target), allowlist=record, inventory=inventory
            )
            print(json.dumps(stored, indent=2, sort_keys=True))
            return 0
        receipts = [allow.read_json(path) for path in args.receipt]
        built = calibration.build_calibration(receipts, allowlist=record, inventory=inventory)
        written = _write_once(target, _render(built))
        _write_once(directory / "measurement.json", _render(calibration.measurement(built)))
    except (
        allow.AllowlistError,
        calibration.CalibrationError,
        hf_inventory.HfInventoryError,
        KeyError,
    ) as exc:
        return _fail(str(exc))
    combined, bounds = built["combined"], built["proposed_file_bounds"]
    state = "written" if written else "identical calibration already recorded"
    print(f"component calibration {built['digest']} ({state}) -> {target}")
    for name, entry in built["components"].items():
        measured = entry["measured"]
        print(
            f"  {name}: {measured['rows']} rows, accepted {measured['accepted_fraction']:.4f}, "
            f"{measured['canonical_bytes_per_compressed_byte']:.3f} canonical/compressed, "
            f"amplification {measured['decompression_amplification']:.2f}"
        )
    print(
        f"ESTIMATE {combined['estimated_canonical_bytes']:,.0f} canonical B "
        f"({combined['estimated_tokens']:,.0f} est. tokens) over {combined['inventory_files']} files"
    )
    print(
        "PROPOSED SOURCE_FILE_BOUNDS "
        f"rows {bounds['max_rows_per_file']:,}, canonical {bounds['max_canonical_bytes_per_file']:,} "
        f"({bounds['rule']}); review, then add them with this calibration's digest as evidence"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
