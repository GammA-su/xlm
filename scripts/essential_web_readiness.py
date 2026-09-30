"""Freeze production-readiness evidence offline; never executes live commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import mix01_inventory
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from xlm.data.acquisition.plan import AcquisitionLimits
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.sources import essential_web_readiness as ready

REPO = Path(__file__).resolve().parents[1]
LAYOUT_SHA256 = "3bcafccb81737694d731dad9cda4949b5e221c51ca9cde4f69102b087267f5e4"


def read_json(path: Path) -> Any:
    if path.stat().st_size > 32 * ready.MIB:
        raise ValueError("readiness JSON exceeds bounded input size")
    return json.loads(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    mix01_inventory._atomic_write_json(path, value)


def physical_inputs(root: Path) -> list[dict[str, Any]]:
    """Read verified M footers only; never opens T layouts or any record payload."""
    raw = (root / "m_phase_p_layout.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != LAYOUT_SHA256:
        raise ValueError("M layout differs from the reviewed Phase-P parent")
    layout = json.loads(raw)
    if layout["source"]["revision"] != selector.SOURCE_REVISION or layout["synthetic"]:
        raise ValueError("physical evidence is not the pinned real source")
    fields = columns_for("essential_web_bnormal", "essential_science")
    files = []
    for i, file in enumerate(layout["files"]):
        descriptor = file["payloads"][f"M-{i:02d}-footer"]
        data = (root / descriptor["retained_file"]).read_bytes()
        if (
            len(data) != descriptor["bytes"]
            or hashlib.sha256(data).hexdigest() != descriptor["sha256"]
        ):
            raise ValueError("frozen footer integrity mismatch")
        metadata = pq.read_metadata(pa.BufferReader(b"PAR1" + data))
        if metadata.num_rows != file["layout"]["num_rows"]:
            raise ValueError("footer row accounting mismatch")
        group = metadata.row_group(0)
        chunks = []
        for j in range(group.num_columns):
            col = group.column(j)
            top = col.path_in_schema.split(".")[0]
            if top in fields:
                chunks.append(
                    {
                        "path": col.path_in_schema,
                        "compressed_bytes": col.total_compressed_size,
                        "uncompressed_bytes": col.total_uncompressed_size,
                        "has_offset_index": col.has_offset_index,
                        "has_column_index": col.has_column_index,
                    }
                )
        files.append(
            {
                "file": file["file"],
                "crawl": file["file"].split("/")[1],
                "remote_length": file["remote_length"],
                "strong_etag": file["strong_etag"],
                "footer_sha256": descriptor["sha256"],
                "footer_bytes": len(data),
                "file_rows": metadata.num_rows,
                "file_row_groups": metadata.num_row_groups,
                "group_rows": group.num_rows,
                "confirmation_window": file["bindings"]["window"],
                "projected_compressed_bytes": sum(c["compressed_bytes"] for c in chunks),
                "projected_uncompressed_bytes": sum(c["uncompressed_bytes"] for c in chunks),
                "selector_compressed_bytes": sum(
                    c["compressed_bytes"]
                    for c in chunks
                    if c["path"].split(".")[0] in ("quality_signals", "eai_taxonomy")
                ),
                "text_compressed_bytes": sum(
                    c["compressed_bytes"] for c in chunks if c["path"] == "text"
                ),
                "chunks": chunks,
                "nominal_requests_one_redirect": 2
                * (4 + sum(math.ceil(c["compressed_bytes"] / (4 * ready.MIB)) for c in chunks)),
            }
        )
    return files


def command_script(out: Path, files: list[dict[str, Any]], stage: str) -> str:
    """Exact CLI syntax, dry until explicit operator execution; shared physical fetch."""
    lines = [
        "# Future operator commands; NOT executed by the offline readiness freeze.",
        "# BLOCKED until source admission and matching production plan authorization exist.",
        "$ErrorActionPreference = 'Stop'",
        "if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }",
        "$storage = Get-Content recipes/operator/storage.json -Raw | ConvertFrom-Json",
        "if ($env:XLM_DATA_ROOT -ne $storage.data_root) { throw 'Unexpected operator root' }",
        "if ($env:XLM_HOME -ne (Join-Path $env:XLM_DATA_ROOT $storage.artifact_store_relative)) "
        "{ throw 'Unexpected artifact store' }",
        "$E = Join-Path (Get-Location) "
        "'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'",
        "$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')",
        f"$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/{stage}'",
        "# Complete dry plan/admission review before executing this conditional sequence.",
    ]
    selected = files[:1] if stage == "probe" else files
    for i, file in enumerate(selected):
        tag = f"{stage}-{i:02d}"
        plan_path = out / f"{tag}.plan.json"
        # The plan was generated offline with the real CLI; use its exact hash.
        plan = read_json(plan_path)
        prefix = (
            "uv @U xlm data plan --source essential_web --view essential_science "
            f'--catalog "$E/production-catalog.json" --files {file["file"]} '
            f'--mode selected_records --row-ranges "$E/{tag}.rows.json" '
            f"--adapter-spec essential_web_bnormal:essential_science --seed {ready.SEED} "
            f'--limits "$E/{tag}.limits.json" --parquet-window-scan-rows 2048 '
            "--parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 "
            "--parquet-window-policy-version 2"
        )
        lines += [
            f'{prefix} --authorization-hash {plan["plan_hash"]} --output "$R/{tag}.plan.json"',
            "if ($LASTEXITCODE -ne 0) { throw 'plan failed' }",
            "$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE",
            "try {",
            "  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'",
            f'  uv @U xlm data fetch --plan "$R/{tag}.plan.json" '
            f'--output-dir "$R/{tag}/raw" --scratch-dir "$R/{tag}/scratch"',
            "  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }",
            "} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }",
            f'uv @U xlm data verify --plan "$R/{tag}.plan.json" '
            f'--output-dir "$R/{tag}/raw" --scratch-dir "$R/{tag}/scratch" --json',
            "if ($LASTEXITCODE -ne 0) { throw 'verification failed' }",
        ]
        for view in selector.ADMITTED_COMPONENTS:
            lines += [
                f'uv @U xlm data adapt --plan "$R/{tag}.plan.json" '
                f"--adapter essential_web_bnormal --adapter-config {view} "
                f'--input "$R/{tag}/raw/selected_records.jsonl" --output-dir "$R/{tag}/{view}" '
                "--on-reject record --max-input-bytes 268435456",
                "if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }",
            ]
        lines += [
            f'uv @U xlm data status --plan "$R/{tag}.plan.json" '
            f'--scratch-dir "$R/{tag}/scratch" --json',
            "if ($LASTEXITCODE -ne 0) { throw 'status failed' }",
        ]
    lines += [
        'uv @U python scripts/essential_web_measure.py --root "$R" --freeze "$E" '
        f'--stage {stage} --output "$R/measurement.json"',
        "if ($LASTEXITCODE -ne 0) { throw 'measurement failed' }",
        'Get-Content "$R/measurement.json" -Raw',
    ]
    return "\n".join(lines) + "\n"


def build(out: Path, footer_root: Path, metadata_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app

    out.mkdir(parents=True, exist_ok=True)
    binding = ready.source_binding()
    ready.check_binding(binding)
    binding["adapter_code_sha256"] = hashlib.sha256(
        (REPO / "src/xlm/data/adapters/mix01_adapters.py").read_bytes()
    ).hexdigest()
    binding["b_normal_semantics_digest"] = (
        "56f86d728852b724f22b9d53b65f1566692efdc43a6e5e14d1bf02261249a5ac"
    )
    catalog = read_json(REPO / "manifests/datasets.catalog.yaml")
    for source in catalog["sources"]:
        if source["source_id"] == "essential_web":
            source["revision"] = binding["revision"]
    write_json(out / "production-catalog.json", catalog)
    # Loading verifies policy and evaluator SHA-256 without reading any text.
    selector.FrozenEssentialWebSelector.load(REPO)
    freeze_path = (
        REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json"
    )
    files = physical_inputs(footer_root)
    names = ready.adopt_paths(read_json(freeze_path))
    inventory = mix01_inventory.freeze_inventory(
        "essential_web",
        ready.REPOSITORY,
        selector.SOURCE_REVISION,
        ready.SEED,
        names,
        {f["file"]: f["remote_length"] for f in files},
    )
    write_json(out / "production.inventory.json", inventory)
    write_json(
        out / "inventory-adoption.json",
        {
            "source": binding,
            "parent": str(freeze_path.relative_to(REPO)),
            "parent_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
            "inventory_digest": inventory["inventory_digest"],
            "file_count": len(names),
            "scope": "complete eligible listings of eight frozen crawls, NOT all 101 source crawls",
            "excluded": "eight development files; inherited from verified inventory",
            "unknown_sizes": len(names) - len(files),
            "physical_metadata": "physical-inputs.json",
        },
    )
    write_json(out / "physical-inputs.json", files)
    transfer = ready.transfer_model(files)
    write_json(out / "transfer-strategy.json", transfer)
    calibration = ready.calibration_plan(files)
    write_json(out / "calibration-plan.json", calibration)
    quotas_path = REPO / "recipes/mixtures/mix01_quotas_6b.yaml"
    quotas = yaml.safe_load(quotas_path.read_text(encoding="utf-8"))
    write_json(out / "science-capacity.json", ready.science_capacity(quotas, len(names), files))
    metadata = read_json(metadata_path)
    if metadata["sha"] != binding["revision"] or metadata["repository"] != ready.REPOSITORY:
        raise ValueError("license metadata source identity mismatch")
    write_json(
        out / "source-provenance.json",
        {
            "binding": binding,
            "repository_metadata": metadata,
            "repository_metadata_path": str(metadata_path),
            "repository_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            "evidence_limit": (
                "license tag and public/gated flags only; no archived license terms, "
                "reviewed upstream rights/attribution record or benchmark audit"
            ),
        },
    )
    reasons = [
        "missing license/provenance/attribution and benchmark-risk review at this revision",
        "missing accessible schema-verified production ProbeEvidenceRecord and fingerprint "
        "(existing probe budget_exhausted)",
    ]
    write_json(
        out / "admission-decisions.json",
        [
            {
                "binding": binding,
                "component": view,
                "component_semantics": f"frozen final == {view}",
                "production_admission_ok": False,
                "decision": "BLOCKED",
                "reasons": reasons,
                "canonicalization": binding["canonicalization"],
                "resource_contract": "C04/C13; exact limits and matching authorization required",
                "t_human_review_required": False,
            }
            for view in selector.ADMITTED_COMPONENTS
        ],
    )
    write_json(
        out / "malformed-policy.json",
        {
            "row_action": "drop with text-free ledger reason; continue",
            "minimum_fraction_rows": 100,
            "max_fraction": 0.01,
            "early_max_bad_rows": 2,
            "structural_action": "STOP",
            "selector_unchanged": True,
        },
    )
    # Dry plans use the existing CLI, never a parallel plan implementation.
    for stage in ("probe", "calibration"):
        for i, file in enumerate(files[:1] if stage == "probe" else files):
            tag = f"{stage}-{i:02d}"
            full_bytes = file["projected_compressed_bytes"] + file["footer_bytes"] + 65540
            budget = max(128 * ready.MIB, math.ceil(full_bytes * 2 / ready.MIB) * ready.MIB)
            requests = math.ceil(file["nominal_requests_one_redirect"] * 1.5)
            limits = AcquisitionLimits(
                max_transferred_bytes=budget,
                max_requests=requests,
                max_retries=1,
                max_workers=1,
                max_records=256 if stage == "probe" else 2048,
                max_scanned_records=2048,
            )
            write_json(out / f"{tag}.limits.json", limits.model_dump())
            write_json(
                out / f"{tag}.rows.json", {file["file"]: [0, 256 if stage == "probe" else 2048]}
            )
            args = [
                "plan",
                "--source",
                "essential_web",
                "--view",
                "essential_science",
                "--catalog",
                str(out / "production-catalog.json"),
                "--files",
                file["file"],
                "--mode",
                "selected_records",
                "--row-ranges",
                str(out / f"{tag}.rows.json"),
                "--adapter-spec",
                "essential_web_bnormal:essential_science",
                "--seed",
                str(ready.SEED),
                "--limits",
                str(out / f"{tag}.limits.json"),
                "--parquet-window-scan-rows",
                "2048",
                "--parquet-window-buffer-bytes",
                "4194304",
                "--parquet-window-batch-rows",
                "256",
                "--parquet-window-policy-version",
                "2",
                "--output",
                str(out / f"{tag}.plan.json"),
            ]
            result = CliRunner().invoke(app, args)
            if result.exit_code != 0:
                raise ValueError(f"offline CLI plan failed: {result.output}: {result.exception}")
        script = out / f"future-{stage}.ps1"
        temporary = script.with_suffix(".ps1.tmp")
        temporary.write_text(command_script(out, files, stage), encoding="utf-8", newline="\n")
        temporary.replace(script)
    write_json(
        out / "probe-plan.json",
        {
            "binding": binding,
            "shared_by": list(selector.ADMITTED_COMPONENTS),
            "rows": 256,
            "file": files[0]["file"],
            "range": [0, 256],
            "calibration_overlap": (
                "probe is subset of first calibration window; "
                "do not add probe rows to calibration totals"
            ),
            "body_budget_bytes": read_json(out / "probe-00.limits.json")["max_transferred_bytes"],
            "budget_basis": "max(128 MiB, 2 x projected group + footer/read-ahead), rounded to MiB",
            "nominal_requests_one_redirect": files[0]["nominal_requests_one_redirect"],
            "blocker": (
                "nested leaf requests exceed C13 pilot 100-request ceiling; production fetch "
                "needs prior accessible schema-verified probe/admission; "
                "generic data probe does not verify this schema"
            ),
            "live_run": False,
        },
    )
    write_json(
        out / "dry-acquisition-plan.json",
        {
            "binding": binding,
            "components": {
                v: {
                    "final_quota": quotas["final_quotas"][v],
                    "first_pass_estimated_target": quotas["first_pass_headroom_quotas"][v],
                }
                for v in selector.ADMITTED_COMPONENTS
            },
            "quotas_sha256": hashlib.sha256(quotas_path.read_bytes()).hexdigest(),
            "stages": [
                "resolve C04 license/provenance and bound probe admission",
                "adopt frozen eight-crawl inventory",
                "shared source/B-normal probe",
                "16384-row frozen calibration",
                "measure science yield/cost and capacity",
                "mix01_inventory estimate; acquire canonical pool in frozen inventory order",
                "later freeze training tokenizer; exact count",
                "mix01_inventory sufficiency; next inventory prefix top-up of deficient views only",
            ],
            "science_controls_scan": True,
            "bulk_limits": None,
            "bulk_authorized": False,
            "blockers": reasons
            + [
                "science retained-byte yield and source capacity unmeasured",
                "physical probe cannot use current pilot request ceiling",
            ],
        },
    )
    write_json(
        out / "readiness.json",
        {
            "vector": {
                "selector_frozen": True,
                "selector_integration_ok": True,
                "source_revision_ok": True,
                "production_admission_ok": False,
                "inventory_ready": True,
                "malformed_policy_ready": True,
                "transfer_strategy_ready": True,
                "probe_plan_ready": False,
                "calibration_plan_ready": True,
                "acquisition_plan_ready": False,
            },
            "live_probe_run": False,
            "live_calibration_run": False,
            "verdict": "ESSENTIAL-WEB PRODUCTION READINESS BLOCKED",
            "dry_plan_exists_does_not_mean_executable": True,
        },
    )
    print(
        json.dumps(
            {
                "inventory_files": len(names),
                "transfer": transfer,
                "calibration_digest": calibration["digest"],
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footer-root", type=Path, required=True)
    parser.add_argument("--repository-metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        build(args.output, args.footer_root, args.repository_metadata)
    except (ValueError, OSError) as exc:
        print(f"essential_web_readiness: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
