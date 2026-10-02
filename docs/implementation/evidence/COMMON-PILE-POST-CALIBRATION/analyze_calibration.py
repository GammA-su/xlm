"""Deterministic offline capacity/bounds previews; never records operator decisions."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from verify_calibration import DATA, OUT, driver, read, write

from xlm.data.acquisition import component_calibration as cc
from xlm.data.acquisition import component_policy as cp
from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import mix01_admission as review

SAFETY = 1.15
GUTENBERG = "project_gutenberg"
MIB = 1024**2


def allocate_c(capacity_tokens: Mapping[str, float]) -> dict[str, dict[str, int]]:
    """Equal 55M core opportunity, explicit 15% margin, residual assigned to Gutenberg."""
    if GUTENBERG not in capacity_tokens or any(
        not math.isfinite(value) or value <= 0 for value in capacity_tokens.values()
    ):
        raise ValueError("capacity must be finite, positive and include Gutenberg")
    first = {
        c: min(55_000_000, math.floor(tokens / SAFETY))
        for c, tokens in capacity_tokens.items()
        if c != GUTENBERG
    }
    first[GUTENBERG] = 330_000_000 - sum(first.values())
    if first[GUTENBERG] <= 0 or first[GUTENBERG] * SAFETY > capacity_tokens[GUTENBERG]:
        raise ValueError("Gutenberg cannot supply the residual without repetition")
    final = {c: v * 10 // 11 for c, v in first.items() if c != GUTENBERG}
    final[GUTENBERG] = 300_000_000 - sum(final.values())
    if any(v <= 0 for v in final.values()):
        raise ValueError("a component has insufficient capacity for a positive final share")
    return {c: {"final_tokens": final[c], "first_pass_tokens": first[c]} for c in sorted(first)}


def summarize_files(files: list[dict[str, Any]], rates: Mapping[str, float]) -> dict[str, Any]:
    result = {}
    for c, rate in sorted(rates.items()):
        selected = [f for f in files if f["file"].split("/")[0] == c]
        compressed = sum(f["size_bytes"] for f in selected)
        result[c] = {
            "files": len(selected),
            "compressed_bytes": compressed,
            "projected_canonical_bytes": compressed * rate,
            "projected_tokens": compressed * rate / 4,
        }
    total = sum(e["projected_canonical_bytes"] for e in result.values())
    return {
        "files": [f["file"] for f in files],
        "components": result,
        "projected_canonical_bytes": total,
        "projected_tokens": total / 4,
        "gutenberg_percent": 100 * result[GUTENBERG]["projected_canonical_bytes"] / total,
    }


def main() -> None:
    cli = driver()
    cal_path = DATA / "calib/common_pile_prose/component-calibration.json"
    cal = json.loads(read(cal_path))
    cc.check_calibration(cal, allowlist=cal["allowlist"], inventory=cal["inventory_snapshot"])
    inventory = cal["inventory_snapshot"]
    files = inventory["files"]
    # Same two tail ranks reserved by the existing driver's benchmark default.
    eligible = files[:-2]
    rates = {
        c: e["measured"]["canonical_bytes_per_compressed_byte"]
        for c, e in cal["components"].items()
    }
    statistics, capacity = {}, {}
    for c, component in cal["components"].items():
        entries = [e for r in cal["sample_receipts"] for e in r["files"] if e["component"] == c]
        measured = component["measured"]
        transferred = measured["transferred_bytes"]
        available = sum(f["size_bytes"] for f in eligible if f["file"].split("/")[0] == c)
        capacity[c] = available * rates[c] / 4
        row_details = [d for e in entries for d in e["rows_detail"]]
        statistics[c] = dict(measured) | {
            "files_sampled_count": len(entries),
            "canonical_bytes_per_accepted_row": measured["canonical_bytes"] / measured["accepted"],
            "decoded_bytes_per_transferred_byte": measured["decoded_bytes"] / transferred,
            "largest_observed_line_bytes": max(d["line_bytes"] for d in row_details),
            "rejection_codes": {
                code: sum(e["adapter"]["rejection_counts_by_code"].get(code, 0) for e in entries)
                for code in sorted(
                    {code for e in entries for code in e["adapter"]["rejection_counts_by_code"]}
                )
            },
            "complete_files": sum(e["whole_file_read"] for e in entries),
            "basis": "two complete selected shards"
            if all(e["whole_file_read"] for e in entries)
            else "two biased prefix samples; not unbiased corpus statistics",
            "individual_file_yield_range": [
                min(
                    e["adapter"]["canonical_bytes"]
                    / e["rows_detail"][-1]["compressed_bytes_at_row_end"]
                    for e in entries
                ),
                max(
                    e["adapter"]["canonical_bytes"]
                    / e["rows_detail"][-1]["compressed_bytes_at_row_end"]
                    for e in entries
                ),
            ],
            "inventory_compressed_bytes": component["inventory"]["bytes"],
            "projected_inventory_canonical_bytes": component["estimated"]["canonical_bytes_total"],
            "projected_inventory_tokens": component["estimated"]["canonical_bytes_total"] / 4,
            "eligible_compressed_bytes": available,
            "eligible_projected_tokens": capacity[c],
        }
    shares_b = {c: {"final_tokens": 50_000_000, "first_pass_tokens": 55_000_000} for c in rates}
    shares_c = allocate_c(capacity)
    shares: dict[str, dict[str, Any]] = {
        "a": {"strategy": "hash_prefix"},
        "b": shares_b,
        "c": shares_c,
    }
    previews = {}
    for name, value in shares.items():
        write(OUT / f"strategy-{name}.input.json", value)
        preview = cp.build_split(
            cal,
            value,
            operator="PREVIEW_ONLY",
            rationale=f"Calibrated strategy {name.upper()}; no operator choice recorded",
        )
        previews[name] = preview
    a_count = math.ceil(1_320_000_000 * SAFETY / cal["combined"]["canonical_bytes_per_file"])
    a = summarize_files(eligible[:a_count], rates)
    cumulative = 0.0
    crossing = 0
    for entry in eligible:
        cumulative += entry["size_bytes"] * rates[entry["file"].split("/")[0]]
        crossing += 1
        if cumulative >= 1_320_000_000 * SAFETY:
            break
    a["first_prefix_crossing_safety_target"] = summarize_files(eligible[:crossing], rates)
    a["selection_rule"] = (
        "existing planner: ceil(safety target / inventory mean calibrated file yield); "
        "actual prefix yields evaluated separately"
    )
    strategy_tables = {}
    for name, allocation in (("b", shares_b), ("c", shares_c)):
        strategy_tables[name] = {
            c: dict(q)
            | {
                "canonical_bytes": 4 * q["first_pass_tokens"],
                "projected_capacity_tokens": capacity[c],
                "margin_before_safety_tokens": capacity[c] - q["first_pass_tokens"],
                "margin_after_safety_tokens": capacity[c] - SAFETY * q["first_pass_tokens"],
                "sufficient": capacity[c] >= SAFETY * q["first_pass_tokens"],
            }
            for c, q in allocation.items()
        }
    selected_c, _ = cp.select_files(
        {"calibration": cal, "component_split": previews["c"]},
        inventory,
        eligible=len(eligible),
        cursors={},
        acquired={},
        safety=SAFETY,
    )
    c_files = [files[e["rank"]] for e in selected_c]
    analysis = {
        "calibration_digest": cal["digest"],
        "operator_choice_recorded": False,
        "statistics": statistics,
        "strategy_a": a,
        "strategy_b": strategy_tables["b"],
        "strategy_c": strategy_tables["c"],
        "strategy_c_selected_projection": summarize_files(c_files, rates),
        "strategy_c_gutenberg_percent": 100
        * shares_c[GUTENBERG]["first_pass_tokens"]
        / 330_000_000,
        "safety_factor": SAFETY,
        "reserved_files": files[-2:],
        "recommendation": "C",
        "uncertainty": "Two files/component; prefixes biased, complete small shards not full "
        "components. 15% is operational headroom, not a statistical confidence interval. "
        "No post-C05 or exact-token guarantee.",
    }
    analysis["digest"] = canonical.digest(analysis)
    write(OUT / "analysis.json", analysis)
    # Operational ceilings, not measured full-shard maxima. Twice the largest
    # observed rate is deliberately separate from allocation's 15% headroom.
    entries = [e for r in cal["sample_receipts"] for e in r["files"]]
    max_file = max(f["size_bytes"] for f in files)
    max_ratio = max(
        e["transfer"]["decoded_bytes"] / e["transfer"]["compressed_bytes_decoded"] for e in entries
    )
    ratio = math.ceil(2 * max_ratio)
    decoded = max_file * ratio
    rows = math.ceil(
        2 * max(e["estimated"]["rows_per_largest_file"] for e in cal["components"].values())
    )
    canonical_cap = math.ceil(
        2
        * max(
            e["estimated"]["canonical_bytes_per_largest_file"] for e in cal["components"].values()
        )
    )
    largest_line = max(d["line_bytes"] for e in entries for d in e["rows_detail"])
    line_cap = 2 ** math.ceil(math.log2(2 * largest_line))
    ledger = 64 * MIB  # independent rejection budget, within the shared output pool
    output = 2 * decoded + rows * 2048 + ledger + MIB
    growth = ProcessingGrowth(output_bytes=output, source_max_bytes=max_file)
    slowest = min(e["transfer"]["transferred_bytes"] / e["transfer"]["seconds"] for e in entries)
    file_deadline = max(600, math.ceil(4 * max_file / slowest / 300) * 300)
    max_selected_bytes = max(
        sum(f["size_bytes"] for f in eligible[:a_count]), sum(f["size_bytes"] for f in c_files)
    )
    plan_deadline = max(
        file_deadline, 3600, math.ceil(4 * max_selected_bytes / slowest / 900) * 900
    )
    bounds = {
        "max_file_bytes": max_file,
        "max_decoded_bytes_per_file": decoded,
        "max_decompression_ratio": ratio,
        "max_rows_per_file": rows,
        "max_record_bytes": line_cap,
        "max_canonical_bytes_per_file": canonical_cap,
        "max_durable_bytes_per_file": max_file + output,
        "max_ledger_bytes": ledger,
        "processing_growth": growth.model_dump(),
        "scratch_cap_bytes": 2 * (max_file + growth.processing_peak + growth.state_peak),
        "file_deadline_seconds": float(file_deadline),
        "plan_deadline_seconds": float(plan_deadline),
    }
    write(OUT / "bounds.input.json", bounds)
    preview = cp.build_bounds(
        cal,
        bounds,
        operator="PREVIEW_ONLY",
        rationale="Calibrated operating ceilings with explicit 2x rate/row/line margins; "
        "not whole-shard measured maxima",
    )
    write(
        OUT / "bounds-derivation.json",
        {
            "preview_digest": preview["digest"],
            "operator_recorded": False,
            "largest_inventory_file_bytes": max_file,
            "max_observed_decoded_ratio": max_ratio,
            "rate_margin": 2,
            "largest_observed_line_bytes": largest_line,
            "max_line_bytes": line_cap,
            "line_bound_mapping": "max_record_bytes enforces both upstream JSONL line "
            "and serialized record",
            "output_rule": "2 * decoded ceiling + 2048 B/row metadata reserve + 64 MiB ledger "
            "+ 1 MiB summary; shared enforced output pool",
            "scratch_rule": "two source + output + atomic metadata/state envelopes; "
            "fixed 32 GiB physical free reserve remains",
            "slowest_observed_transfer_bytes_per_second": slowest,
            "file_deadline_rule": "4x largest compressed file / slowest observed prefix "
            "throughput, rounded up to 300 s; floor 600 s",
            "plan_deadline_rule": "4x larger A/C selected compressed volume / slowest prefix "
            "throughput, rounded up to 900 s; floor 3600 s",
            "limitation": "Chosen fail-closed ceilings, not confidence bounds or runtime "
            "predictions. Prefixes cannot prove full-shard extremes; exceeding any ceiling "
            "requires a new operator review, not an automatic increase.",
        },
    )
    args = cli.build_parser().parse_args(
        [
            "evidence",
            "show",
            "--source-key",
            "common_pile",
            "--data-root",
            str(DATA),
            "--sample-dir",
            str(DATA / "calib/common_pile_cal01"),
            "--sample-dir",
            str(DATA / "calib/common_pile_cal02"),
        ]
    )
    spec = cli.spec_of("common_pile")
    pin, bridge, evidence = cli.build_bridge(args, spec, cli.store())
    write(OUT / "bridge-preview.json", bridge)
    write(
        OUT / "review-facts-preview.json",
        review.review_facts(pin, bridge, cli.registry_notes(spec)),
    )
    paths = [
        cal_path,
        cal_path.with_name("measurement.json"),
        DATA / "calib/calibration.common-pile-post.json",
        DATA / "calib/headroom_estimate.common-pile-post.json",
    ]
    write(
        OUT / "artifact-identities.json",
        {
            str(p): {
                "sha256": hashlib.sha256(read(p)).hexdigest(),
                "canonical_digest": canonical.digest(json.loads(read(p))),
            }
            for p in paths
        },
    )
    print(
        f"analysis {analysis['digest']}; A {a_count} files, "
        f"Gutenberg {a['gutenberg_percent']:.3f}%; "
        f"C Gutenberg {analysis['strategy_c_gutenberg_percent']:.3f}%"
    )
    print(
        f"bounds preview {preview['digest']}; "
        f"bridge preview {bridge['digest']}; no operator records"
    )


if __name__ == "__main__":
    main()
