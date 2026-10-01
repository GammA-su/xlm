"""Transport policy model/freeze and the deterministic Mix-01 source production planner."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest
import yaml

from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import transport_policy as tp

PIN = {
    "source_id": "ultrax_ultrafineweb",
    "view_id": "UltraX-Ultra-FineWeb",
    "component_id": "ultrax_ultrafineweb",
    "provider": "huggingface",
    "repository": "openbmb/UltraX-Preview",
    "revision": "a88527587389fd4ab352e9ad1273f4c0a234d8df",
    "adapter_id": "ultrax_ultrafineweb",
}
ADMISSION = {"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64}


def layout(**changes: Any) -> tp.SourceLayout:
    values: dict[str, Any] = {
        "source_id": PIN["source_id"],
        "file_bytes": 1_000_000,
        "group_rows": 100,
        "group_bytes": 10_000,
        "projected_group_bytes": 4_000,
        "range_requests_per_group": 2.0,
        "metadata_requests_per_file": 4,
        "canonical_bytes_per_row": 50.0,
        "range_record_bytes_per_row": 60.0,
        "metadata_bytes_per_file": 1_000,
        "source_files": 40,
        "evidence": {"rows": "0" * 64},
    }
    values.update(changes)
    return tp.SourceLayout(**values)


def inventory(count: int = 40, seed: int = 20260918) -> dict[str, Any]:
    names = [f"data/part-{i:04d}.parquet" for i in range(count)]
    entries = sorted(
        (
            {
                "file": name,
                "size_bytes": None,
                "order_key": hashlib.sha256(
                    f"{seed}|{PIN['repository']}|{PIN['revision']}|{name}".encode()
                ).hexdigest(),
            }
            for name in names
        ),
        key=lambda e: (e["order_key"], e["file"]),
    )
    value = {
        "inventory_version": 1,
        "source_id": PIN["source_id"],
        "repository": PIN["repository"],
        "revision": PIN["revision"],
        "seed": seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": count,
        "known_size_bytes": None,
    }
    value["inventory_digest"] = planner.inventory_digest(value)
    return value


def requirement(tokens: int = 1_000_000) -> planner.Requirement:
    quotas = yaml.safe_load(f"first_pass_headroom_quotas:\n  ultrax_ultrafineweb: {tokens}\n")
    estimate = {
        "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
        "sources": {
            "ultrax_ultrafineweb": {
                "status": "ESTIMATED",
                "first_pass_usable_token_target": tokens,
                "required_canonical_bytes_base": tokens * 4.0,
            }
        },
    }
    return planner.requirement_from(
        "ultrax_ultrafineweb", quotas, estimate, quotas_sha256="1" * 64, estimate_sha256="2" * 64
    )


def models(
    aggregate: float = 100e6, latency: float = 0.3
) -> dict[tp.TransportMode, tp.ThroughputModel]:
    def model(processes: int, rate: float) -> tp.ThroughputModel:
        return tp.ThroughputModel(8, 20e6, aggregate, latency, rate, processes, "authored")

    local = model(12, 1000.0)
    return {
        tp.TransportMode.WHOLE_FILE_LOCAL: local,
        tp.TransportMode.SMALL_SOURCE_DIRECT: local,
        tp.TransportMode.RANGE_SELECTED: model(1, 5000.0),
    }


def frozen_policy(report: dict[str, Any] | None = None) -> dict[str, Any]:
    report = report or tp.evaluate(
        layout(), tp.Requirement(PIN["source_id"], 4_000_000, 1.15), tp.Ceilings(), models()
    )
    return tp.freeze(report, basis="modeled", inputs={"model": "authored"})


def build(**changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "source_key": "ultrax",
        "pin": PIN,
        "requirement": requirement(),
        "inventory": inventory(),
        "inventory_sha256": "3" * 64,
        "layout": layout(),
        "calibration": {"rows": "0" * 64},
        "policy": frozen_policy(),
        "admission": ADMISSION,
    }
    arguments.update(changes)
    return planner.build_plan(**arguments)


# ------------------------------------------------------------------ policy


def test_workloads_and_byte_request_estimates() -> None:
    req = tp.Requirement(PIN["source_id"], 4_000_000, 1.15)
    work = {w.mode: w for w in tp.workloads(layout(), req, tp.Ceilings())}
    rows = math.ceil(4_000_000 * 1.15 / 50.0)
    assert tp.required_rows(layout(), req) == rows == 92_000
    whole = work[tp.TransportMode.WHOLE_FILE_LOCAL]
    assert layout().rows_per_file == 10_000
    assert whole.files == math.ceil(rows / layout().rows_per_file) == 10
    assert (whole.transfer_bytes, whole.requests, whole.rows_processed) == (10_000_000, 20, 100_000)
    ranged = work[tp.TransportMode.RANGE_SELECTED]
    assert ranged.transfer_bytes == math.ceil(rows * 40) + 10 * 1_000
    assert ranged.requests == math.ceil(rows / 100) * 2 + 10 * 4
    small = work[tp.TransportMode.SMALL_SOURCE_DIRECT]
    assert (small.files, small.transfer_bytes) == (40, 40_000_000)


def test_selection_is_fastest_then_fewer_requests() -> None:
    req = tp.Requirement(PIN["source_id"], 4_000_000, 1.15)
    report = tp.evaluate(layout(), req, tp.Ceilings(), models())
    assert report["selected_mode"] == tp.TransportMode.WHOLE_FILE_LOCAL
    slow_models = models(aggregate=0.05e6, latency=0.0)
    slow = tp.evaluate(layout(), req, tp.Ceilings(), slow_models, executable=tp.ALL_MODES)
    assert slow["selected_mode"] == tp.TransportMode.RANGE_SELECTED
    # Without a range production planner the fastest mode is reported, not used.
    executable = tp.evaluate(layout(), req, tp.Ceilings(), slow_models)
    assert executable["selected_mode"] != tp.TransportMode.RANGE_SELECTED
    assert executable["fastest_modeled"] == {
        "mode": "range_selected",
        "wall_seconds": slow["fastest_modeled"]["wall_seconds"],
        "executable": False,
    }
    capped = tp.evaluate(
        layout(), req, tp.Ceilings(max_requests=50), models(latency=0.0), executable=tp.ALL_MODES
    )
    assert capped["selected_mode"] == tp.TransportMode.WHOLE_FILE_LOCAL
    ranged = next(c for c in capped["candidates"] if c["mode"] == "range_selected")
    assert not ranged["feasible"] and "exceed" in ranged["refusals"][0]
    with pytest.raises(tp.PolicyError, match="no transport mode"):
        tp.evaluate(layout(), req, tp.Ceilings(max_requests=1, scratch_bytes=1), models())


def test_policy_freeze_is_bound_and_tamper_evident() -> None:
    record = frozen_policy()
    assert tp.check_frozen(record, PIN["source_id"]) == tp.TransportMode.WHOLE_FILE_LOCAL
    with pytest.raises(tp.PolicyError, match="another source"):
        tp.check_frozen(record, "finewiki")
    with pytest.raises(tp.PolicyError, match="digest"):
        tp.check_frozen({**record, "selected_mode": "range_selected"}, PIN["source_id"])
    with pytest.raises(tp.PolicyError, match="benchmark receipts"):
        tp.freeze(record["report"], basis="measured", inputs={})


def test_measured_model_and_adapt_log() -> None:
    receipt = {
        "digest": "9" * 64,
        "transfer": {"wall_seconds": 10.0, "transferred_bytes": 500e6, "requests": 4},
        "processing": {"rows_per_process_second": 3000.0},
        "concurrency": {"download_workers": 4, "process_workers": 6},
    }
    model = tp.measured_model(receipt)
    assert model.aggregate_bytes_per_second == 50e6 and model.processes == 6
    assert "measured" in model.basis
    text = "Adapt throughput: 1000 records in 0.17s (5814.0/s, 62.501 MiB/s input"
    assert tp.adapt_rate_from_log(text) == 5814.0
    assert tp.adapt_rate_from_log("nothing") is None


def test_layout_from_calibration_rowgroup_and_window() -> None:
    journal = {
        "source_validators": {"f.parquet": {"etag": '"e"', "length": 1_000_000}},
        "file_progress": {"selected_records.jsonl": {"bytes_downloaded": 6_000}},
    }
    measurement = {"records_sampled": 100, "canonical_bytes": 5_000}
    perf = {
        "transferred_bytes": 5_000,
        "requests_made": 6,
        "telemetry": {
            "parquet_groups": 1,
            "projection_selected_bytes": 4_000,
            "coalesced_ranges": 2,
        },
    }
    rows = {"mode": "rowgroup", "blocks": [{"num_rows": 100, "compressed_bytes": 10_000}]}
    found = tp.layout_from_calibration(
        "s", rows, perf, journal, measurement, source_files=None, evidence_names={}
    )
    assert (found.group_bytes, found.projected_group_bytes, found.metadata_bytes_per_file) == (
        10_000,
        4_000,
        1_000,
    )
    assert found.range_requests_per_group == 2.0 and found.metadata_requests_per_file == 4
    window = {
        "mode": "window",
        "window_policy": {"stream_buffer_bytes": 1_000},
        "windows": [
            {
                "group_rows": 1_000,
                "group_compressed_bytes": 100_000,
                "physical_transfer_ceiling_bytes": 50_000,
                "selected_columns": [{}, {}],
            }
        ],
    }
    perf_w = {"transferred_bytes": 9_000, "requests_made": 5, "telemetry": {"scanned_records": 100}}
    found_w = tp.layout_from_calibration(
        "s", window, perf_w, journal, measurement, source_files=None, evidence_names={}
    )
    assert found_w.range_requests_per_group == 52.0
    assert found_w.metadata_bytes_per_file == 9_000 - 5_000


# ------------------------------------------------------------------ planner


def test_inventory_is_verified() -> None:
    ordered = planner.check_inventory(
        inventory(), PIN["source_id"], PIN["repository"], PIN["revision"]
    )
    assert len(ordered) == 40
    tampered = inventory()
    tampered["files"][0], tampered["files"][1] = tampered["files"][1], tampered["files"][0]
    with pytest.raises(planner.PlanError):
        planner.check_inventory(tampered, PIN["source_id"], PIN["repository"], PIN["revision"])
    with pytest.raises(planner.PlanError, match="another source"):
        planner.check_inventory(inventory(), PIN["source_id"], PIN["repository"], "c" * 40)


def test_requirement_must_match_frozen_quota() -> None:
    quotas = {"first_pass_headroom_quotas": {"ultrax_ultrafineweb": 1_000_000}}
    estimate = {
        "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
        "sources": {
            "ultrax_ultrafineweb": {
                "status": "ESTIMATED",
                "first_pass_usable_token_target": 2_000_000,
                "required_canonical_bytes_base": 8_000_000.0,
            }
        },
    }
    with pytest.raises(planner.PlanError, match="disagrees"):
        planner.requirement_from(
            "ultrax_ultrafineweb", quotas, estimate, quotas_sha256="", estimate_sha256=""
        )


def test_plan_is_deterministic_inventory_prefix() -> None:
    record = build()
    assert record == build()
    planner.check_plan(record)
    ordered = planner.check_inventory(
        inventory(), PIN["source_id"], PIN["repository"], PIN["revision"]
    )
    files = [entry["file"] for entry in record["selection"]["files"]]
    # 4 MB canonical * 1.15 over 1,000,000 / 100 B/row = 10,000 rows * 50 B per file.
    assert len(files) == math.ceil(4_000_000 * 1.15 / (10_000 * 50.0)) == 10
    assert files == ordered[:10]
    assert record["selection"]["start_rank"] == 0 and record["selection"]["next_cursor"] == 10
    assert record["inventory"]["benchmark_reserved_ranks"] == [38, 39]
    assert record["transport_mode"] == "whole_file_local"
    assert record["acquisition_plan"]["is_pilot"] is False
    minted = planner.minted_from_record(record)
    assert minted.plan_hash == record["acquisition_plan"]["plan_hash"]
    assert minted.selected_files == files
    limits = record["limits"]
    assert limits["max_file_bytes"] == 2 * 1024**2  # 2x the measured file, MiB-rounded
    assert record["acquisition_plan"]["limits"]["max_requests"] == 10 * 16
    assert record["requirement"]["required_canonical_bytes"] == 4_000_000
    # Plan identity changes with any input.
    assert (
        build(admission={**ADMISSION, "bridge_receipt_digest": "c" * 64})["digest"]
        != record["digest"]
    )


def test_top_up_continues_at_the_cursor() -> None:
    first = build()
    deficit = planner.Predecessor(
        plan=first, sealed_canonical_bytes=3_000_000, sealed_files=10, accounting_digest="4" * 64
    )
    second = build(predecessor=deficit)
    assert second["sequence"] == 2
    assert second["lineage"]["previous_plan_digest"] == first["digest"]
    assert second["selection"]["start_rank"] == 10
    assert second["acquired_before"]["canonical_bytes"] == 3_000_000
    ordered = planner.check_inventory(
        inventory(), PIN["source_id"], PIN["repository"], PIN["revision"]
    )
    files = [entry["file"] for entry in second["selection"]["files"]]
    assert files == ordered[10 : 10 + len(files)]
    assert len(files) == math.ceil(1_000_000 * 1.15 / 500_000)
    with pytest.raises(planner.PlanError, match="not completely sealed"):
        build(predecessor=planner.Predecessor(first, 1, 9, "4" * 64))
    with pytest.raises(planner.PlanError, match="already meet"):
        build(predecessor=planner.Predecessor(first, 4_000_000, 10, "4" * 64))


def test_planner_refuses_instead_of_guessing() -> None:
    with pytest.raises(planner.PlanError, match="plannable files left"):
        build(requirement=requirement(tokens=100_000_000))
    report = tp.evaluate(
        layout(),
        tp.Requirement(PIN["source_id"], 4_000_000, 1.15),
        tp.Ceilings(),
        models(aggregate=0.05e6, latency=0.0),
        executable=tp.ALL_MODES,
    )
    with pytest.raises(planner.PlanError, match="xlm data plan/fetch"):
        build(policy=frozen_policy(report))
    with pytest.raises(tp.PolicyError):
        build(policy={**frozen_policy(), "digest": "0" * 64})
    with pytest.raises(planner.PlanError, match="another source"):
        build(inventory=inventory(seed=1) | {"source_id": "finewiki"})
