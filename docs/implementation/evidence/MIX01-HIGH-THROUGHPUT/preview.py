"""Read-only previews from the real operator store; writes only into this directory.

Nothing is published to the store, no plan is stored and nothing is downloaded.
Run from the checkout after ``. .\\scripts\\operator_storage.ps1``:

    uv run --offline --locked --no-sync --extra cpu --extra eval python
        docs/implementation/evidence/MIX01-HIGH-THROUGHPUT/preview.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
spec = importlib.util.spec_from_file_location("mix01_source", REPO / "scripts" / "mix01_source.py")
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
sys.modules["mix01_source"] = cli
spec.loader.exec_module(cli)

from xlm.data.acquisition import source_plan as planner  # noqa: E402
from xlm.data.acquisition import transport_policy as tp  # noqa: E402
from xlm.data.sources import mix01_admission as review  # noqa: E402


def write(name: str, value: Any) -> None:
    with (HERE / name).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def args_for(key: str) -> SimpleNamespace:
    return SimpleNamespace(
        source_key=key,
        data_root=None,
        scratch_root=None,
        probe_dir=None,
        basis="modeled",
        aggregate_mbps=None,
        durable_budget_bytes=None,
    )


def main() -> int:
    store = cli.store()
    bridges: dict[str, Any] = {}
    for key in cli.SOURCES:
        _, receipt, _ = cli.build_bridge(args_for(key), cli.spec_of(key), store)
        bridges[key] = {
            "digest": receipt["digest"],
            "probe_fingerprint": receipt["probe_fingerprint"],
            "schema_basis": receipt["schema_basis"],
            "declared_license": receipt["declared_license"],
            "row_sets": receipt["adapter"]["row_sets"],
            "observed_files": receipt["observed_files"],
        }
        if key == "ultrax":
            write("ultrax-bridge-receipt.preview.json", receipt)
            pin = cli.pin_of(cli.spec_of(key))
            facts = review.review_facts(pin, receipt, cli.registry_notes(cli.spec_of(key)))
            write("ultrax-review-input.json", facts)
    write("bridge-dry-run.json", {"status": "dry run; nothing published", "sources": bridges})

    reports = []
    for key in ("ultrax", "finepdfs", "synth", "wiki_rewrite", "finewiki", "simple_stories"):
        reports.append(cli.evaluate_policy(args_for(key), cli.spec_of(key)))
    fast = cli.evaluate_policy(
        SimpleNamespace(**{**vars(args_for("ultrax")), "aggregate_mbps": 142.7}),
        cli.spec_of("ultrax"),
    )
    write(
        "transport-policy-modeled.json",
        {
            "policy": tp.POLICY_ID,
            "basis": "modeled (Essential-Web endpoint rates; per-source calibration latency)",
            "summary": tp.summarize(reports),
            "ultrax_at_142_7_mb_per_s": tp.summarize([fast]),
        },
    )

    args = args_for("ultrax")
    spec_ = cli.spec_of("ultrax")
    layout, extra = cli.layout_of(args, spec_)
    inventory_path = cli.data_root(args) / "inventories" / "ultrax.inventory.json"
    record = planner.build_plan(
        source_key="ultrax",
        pin=cli.pin_of(spec_).as_dict(),
        requirement=cli.requirement_of(args, spec_),
        inventory=cli.load_json(inventory_path),
        inventory_sha256=cli.sha256(inventory_path),
        layout=layout,
        calibration=extra["evidence"],
        policy=tp.freeze(reports[0], basis="modeled", inputs={"model": "preview"}),
        admission={"probe_fingerprint": "PREVIEW", "bridge_receipt_digest": "PREVIEW"},
    )
    write(
        "ultrax-plan.preview.json",
        {
            "status": "PREVIEW ONLY: built with placeholder admission and a preview policy; "
            "its digest is not authorizable. The operator's plan comes from 'plan'.",
            **{k: record[k] for k in ("selection", "expected", "limits", "requirement")},
            "acquisition_limits": record["acquisition_plan"]["limits"],
            "inventory": record["inventory"],
        },
    )
    print("previews written to", HERE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
