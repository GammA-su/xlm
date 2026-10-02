"""OFFLINE: derive text-free targets and operator commands; never execute them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import source_plan
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources import hf_inventory

ROOT = Path("G:/XLM")
OUT = Path(__file__).resolve().parent
REVISION = "5afc546db324e7f39f297ba757c9a60547151e7c"
MIB = 1024**2


def main() -> None:
    listing = hf_inventory.read_listing(ROOT / "inventories/common_pile.discovery.listing.json")
    record = allow.check_allowlist(
        allow.read_json(ROOT / "calib/component_allowlists/common_pile.json"), listing
    )
    inventory = allow.read_json(ROOT / "inventories/common_pile.inventory.json")
    allow.check_inventory_binding(record, listing, inventory)
    source_plan.check_inventory(inventory, "common_pile", record["repository"], REVISION)
    targets = []
    for component in record["included"]:
        files = [e for e in inventory["files"] if allow.component_of(e["file"]) == component][:2]
        for entry in files:
            targets.append(
                {
                    "component": component,
                    "file": entry["file"],
                    "inventory_size_bytes": entry["size_bytes"],
                    "rows_requested": 32 if component == "project_gutenberg" else 256,
                    "chunk_bytes": 131072,
                    "max_requests": 128,
                    "max_transferred_bytes": min(4 * MIB, entry["size_bytes"]),
                    "destination": "G:\\XLM\\calib\\common_pile_cal02"
                    if component == "project_gutenberg"
                    else "G:\\XLM\\calib\\common_pile_cal01",
                }
            )
    receipt = allow.read_json(OUT / "certification-cert02.receipt.json")
    body = dict(receipt)
    digest = body.pop("digest")
    if digest != canonical.digest(body):
        raise ValueError("certification receipt failed its digest")
    value: dict[str, Any] = {
        "kind": "common-pile-calibration-authorization-preview-v1",
        "network_executed": False,
        "revision": REVISION,
        "allowlist_digest": record["digest"],
        "inventory_digest": inventory["inventory_digest"],
        "targets": targets,
        "max_sample_body_bytes": sum(t["max_transferred_bytes"] for t in targets),
        "configured_sample_byte_ceiling": 48 * MIB,
        "max_sample_requests_including_redirects": 1536,
        "metadata_probe_bytes": 8 * MIB,
        "metadata_probe_requests": 20,
        "max_total_body_bytes": sum(t["max_transferred_bytes"] for t in targets) + 8 * MIB,
        "max_total_requests": 1556,
        "certification_receipt_digest": digest,
        "adapter_code_identity": ce.adapter_code_identity("common_pile"),
    }
    value["digest"] = canonical.digest(value)
    (OUT / "calibration-authorization-preview.json").write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    commands = [
        "# OPERATOR ONLY. Review authorization preview before executing.",
        "Set-Location F:\\Project\\xlm-common-pile",
        "$ErrorActionPreference = 'Stop'",
        "$env:XLM_DATA_ROOT = 'G:\\XLM'",
        "$env:XLM_HOME = 'G:\\XLM\\xlm-home'",
        "$env:XLM_SCRATCH_ROOT = 'C:\\XLM-scratch'",
        "$env:HF_HOME = 'G:\\XLM\\hf-cache'",
        "$env:HF_DATASETS_CACHE = 'G:\\XLM\\hf-cache\\datasets'",
        "$env:PYTHONUTF8 = '1'",
        "try {",
        "  $env:HF_HUB_OFFLINE = '0'",
        "  $env:HF_DATASETS_OFFLINE = '0'",
    ]
    prefix = "uv run --offline --locked --no-sync --extra cpu --extra eval python"
    for label in ("cal01", "cal02"):
        selected = [t for t in targets if str(t["destination"]).endswith(label)]
        components = ",".join(sorted({str(t["component"]) for t in selected}))
        target_flags = " ".join(f"--target {t['file']}" for t in selected)
        commands.append(
            f"  {prefix} scripts/jsonl_gz_sample.py --source-key common_pile --data-root G:\\XLM "
            f"--label common-pile-{label} --rows {selected[0]['rows_requested']} "
            f"--authorized-components {components} {target_flags} "
            f"--chunk-bytes 131072 --max-requests-per-file 128 --max-bytes-per-file {4 * MIB} "
            f"--max-total-bytes {len(selected) * 4 * MIB} --max-line-bytes {MIB} "
            f"--max-total-decoded-bytes {128 * MIB} --max-output-bytes {128 * MIB} "
            f"--timeout 30 --deadline 600 --output-dir {selected[0]['destination']}"
        )
        commands.append(
            "  if ($LASTEXITCODE -ne 0) { throw 'Calibration refused; stop and inspect' }"
        )
    commands.extend(
        [
            f"  {prefix} -m xlm.cli.main data probe --source common_pile --view common_pile_prose "
            "--catalog manifests/datasets.catalog.yaml --live --budget-mib 8 "
            "--probe-id common-pile-balanced-cal01 --publish --json",
            "  if ($LASTEXITCODE -ne 0) { throw 'Metadata probe refused; stop and inspect' }",
            "} finally {",
            "  $env:HF_HUB_OFFLINE = '1'",
            "  $env:HF_DATASETS_OFFLINE = '1'",
            "}",
            "# STOP: return the text-free receipts/probe outcome for offline review.",
            "# No admission, production policy, production plan, authorization or production run.",
        ]
    )
    (OUT / "operator-calibration.ps1").write_text(
        "\n".join(commands) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
