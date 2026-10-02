"""Policy workloads for an operator-selected component population (offline)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xlm.data.acquisition import component_policy as cp
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.source_growth import ProcessingGrowth


def evaluate(
    component: Mapping[str, Any],
    layout: tp.SourceLayout,
    requirement: tp.Requirement,
    models: Mapping[tp.TransportMode, tp.ThroughputModel],
    *,
    benchmark_reserved: int,
    durable_budget_bytes: int | None,
) -> dict[str, Any]:
    cal = component["calibration"]
    bounds = cp.check_bounds(component["reviewed_bounds"], cal)["bounds"]
    inventory = cal["inventory_snapshot"]
    eligible = len(inventory["files"]) - benchmark_reserved
    if not 0 < eligible <= len(inventory["files"]):
        raise cp.ComponentPolicyError("benchmark reservation leaves no plannable inventory")
    selected, cursors = cp.select_files(
        component,
        inventory,
        acquired={},
        cursors={},
        safety=requirement.safety_margin,
        eligible=eligible,
    )
    files = [e["file"] for e in selected]
    expected = cp.expected_selection(cal, files, files)
    growth = ProcessingGrowth.model_validate(bounds["processing_growth"])
    envelope = bounds["max_file_bytes"] + growth.processing_peak + growth.state_peak
    # One downloader plus one processor, as enforced by component plan limits.
    scratch = min(2, len(files)) * envelope
    durable = (
        len(files)
        * (
            bounds["max_durable_bytes_per_file"]
            + bounds["max_file_bytes"]
            + 3 * growth.metadata_bytes
        )
        + growth.run_peak
    )
    work = tp.ModeWorkload(
        tp.TransportMode.WHOLE_FILE_LOCAL,
        len(files),
        expected["rows"],
        expected["transfer_bytes"],
        expected["requests"],
        scratch,
        durable,
        "independent component prefixes; exact inventory transfer; projected rows; "
        "reviewed worst-case scratch/durable envelopes, including temporary/state files",
    )
    candidates = [work]
    # Direct means the entire eligible source, never a hidden change to a C prefix.
    all_files = [e["file"] for e in inventory["files"][:eligible]]
    all_bytes = sum(e["size_bytes"] for e in inventory["files"][:eligible])
    if all_bytes <= tp.SMALL_SOURCE_MAX_BYTES and set(files) == set(all_files):
        from dataclasses import replace

        candidates.append(replace(work, mode=tp.TransportMode.SMALL_SOURCE_DIRECT))
    ceilings = tp.Ceilings(
        scratch_bytes=bounds["scratch_cap_bytes"],
        durable_bytes=durable_budget_bytes,
        in_flight_files=2,
    )
    report = tp.evaluate(layout, requirement, ceilings, models, selected_workloads=candidates)
    report["component_selection"] = {
        "expected": expected,
        "cursors": cursors,
        "reviewed_bounds_digest": component["reviewed_bounds"]["digest"],
        "component_split_digest": component["component_split"]["digest"],
        "small_source_direct": "candidate"
        if len(candidates) == 2
        else "inapplicable: eligible source exceeds 2 GiB or differs from the selected population",
    }
    return report
