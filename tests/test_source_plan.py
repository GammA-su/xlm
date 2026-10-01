"""Transport policy model/freeze and the deterministic Mix-01 source production planner."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest
import yaml

from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.evidence_v2 import canonical

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


# ------------------------------------------- measured sizing and v2 dispositions

SUBJECT = {key: PIN[key] for key in tp.SUBJECT_KEYS}
#: Real-shaped whole-file benchmark: 8,000 rows of which 3,000 are rejected,
#: against a calibration that saw 100% acceptance at 50 canonical B/row.
MEASURED_ROWS, MEASURED_CANONICAL = 8_000, 240_000


def receipt(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "mix01_source_performance_receipt",
        "benchmark": {
            "digest": "a" * 64,
            "files": [{"file": "data/part-0039.parquet", "rank": None, "reason": "named"}],
            "label": "b1",
        },
        "plan": {"digest": "a" * 64, "plan_hash": "5" * 64, "sequence": None},
        "source": dict(PIN),
        "outcome": {"status": "completed"},
        "transport_mode": "whole_file_local",
        "concurrency": {"download_workers": 1, "process_workers": 1},
        "transfer": {
            "files": 1,
            "file_bytes": 1_000_000,
            "transferred_bytes": 1_000_000,
            "requests": 2,
            "wall_seconds": 10.0,
            "sha256_independently_verified": 1,
        },
        "processing": {
            "units": 1,
            "rows": MEASURED_ROWS,
            "documents": 5_000,
            "rejected": 3_000,
            "canonical_bytes": MEASURED_CANONICAL,
            "rows_per_process_second": 1000.0,
        },
    }
    for key, value in changes.items():
        section, _, field = key.partition("__")
        if field:
            body[section] = {**body[section], field: value}
        else:
            body[section] = value
    body["digest"] = canonical.digest(body)
    return body


def sizing(**changes: Any) -> dict[str, Any]:
    return planner.sizing_from_receipt(receipt(**changes), PIN, receipt_sha256="6" * 64)


def reach_disposition(**changes: Any) -> dict[str, str]:
    values: dict[str, Any] = {
        "contract": "range-v1-reach-v1",
        "evidence_digest": "7" * 64,
        "evidence_sha256": "8" * 64,
        "summary": "authored: 1 of 4 row groups refused",
    }
    values.update(changes)
    return tp.mode_disposition(tp.TransportMode.RANGE_SELECTED, "non_comparable", **values)


def measured_policy(
    measurement: dict[str, Any] | None = None,
    dispositions: list[dict[str, str]] | None = None,
    subject: dict[str, str] | None = None,
) -> dict[str, Any]:
    measurement = measurement or sizing()
    sized = planner.sized_layout(layout(source_files=None), measurement)
    local = models()[tp.TransportMode.WHOLE_FILE_LOCAL]
    report = tp.evaluate(
        sized,
        tp.Requirement(PIN["source_id"], 4_000_000, 1.15),
        tp.Ceilings(),
        {tp.TransportMode.WHOLE_FILE_LOCAL: local, tp.TransportMode.SMALL_SOURCE_DIRECT: local},
    )
    return tp.freeze(
        report,
        basis="measured",
        inputs={"whole": "6" * 64, "range_reach": "8" * 64},
        subject=subject or SUBJECT,
        sizing=measurement,
        dispositions=[reach_disposition()] if dispositions is None else dispositions,
    )


def test_sizing_from_receipt_derives_only_measured_quantities() -> None:
    found = sizing()
    assert found["contract"] == planner.SIZING_CONTRACT and found["subject"] == SUBJECT
    assert found["receipt"] == {
        "digest": receipt()["digest"],
        "sha256": "6" * 64,
        "benchmark_digest": "a" * 64,
        "plan_hash": "5" * 64,
    }
    assert found["measured"] == {
        "files": 1,
        "rows": 8_000,
        "documents": 5_000,
        "rejected": 3_000,
        "file_bytes": 1_000_000,
        "canonical_bytes": 240_000,
    }
    derived = found["derived"]
    assert derived["rows_per_file"] == 8_000 and derived["file_bytes_per_file"] == 1_000_000
    assert derived["canonical_bytes_per_row"] == 30.0
    assert derived["accepted_fraction"] == 0.625
    assert derived["canonical_bytes_per_raw_byte"] == 0.24
    assert derived["estimated_tokens"] == 60_000
    assert "exact tokens only after tokenizer freeze" in found["token_method"]
    assert found == sizing()  # deterministic


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"source__revision": "c" * 40}, "another source view"),
        ({"source__view_id": "other"}, "another source view"),
        ({"outcome": {"status": "failed"}}, "completed"),
        ({"transport_mode": "range_selected"}, "whole files"),
        ({"processing__rejected": 2_999}, "whole, verified files"),
        ({"transfer__sha256_independently_verified": 0}, "whole, verified files"),
        ({"processing__units": 2}, "whole, verified files"),
    ],
)
def test_sizing_refuses_mismatched_or_incomplete_receipts(
    changes: dict[str, Any], match: str
) -> None:
    with pytest.raises(planner.PlanError, match=match):
        sizing(**changes)


def test_sizing_refuses_tampered_receipt() -> None:
    tampered = {**receipt(), "processing": {**receipt()["processing"], "rows": 9_000}}
    with pytest.raises(planner.PlanError, match="digest"):
        planner.sizing_from_receipt(tampered, PIN, receipt_sha256="6" * 64)


def test_v1_policy_and_layout_record_shapes_are_unchanged() -> None:
    record = frozen_policy()
    assert record["policy"] == tp.POLICY_ID == "mix01-transport-policy-v1"
    assert set(record) == {
        "kind",
        "policy",
        "basis",
        "inputs",
        "source_id",
        "selected_mode",
        "reason",
        "report",
        "digest",
    }
    assert "rows_per_file_measured" not in record["report"]["layout"]
    assert tp.layout_record(layout())["rows_per_file_estimate"] == 10_000
    # A v1 record still verifies after the v2 extension.
    assert tp.check_frozen(record, PIN["source_id"]) == tp.TransportMode.WHOLE_FILE_LOCAL


def test_v2_policy_binds_disposition_not_a_speed_comparison() -> None:
    record = measured_policy()
    assert record["policy"] == tp.POLICY_ID_V2 and record["basis"] == "measured"
    assert tp.check_frozen(record, PIN["source_id"]) == tp.TransportMode.WHOLE_FILE_LOCAL
    assert record["subject"] == SUBJECT and record["sizing"] == sizing()
    assert {c["mode"] for c in record["report"]["candidates"]} == {"whole_file_local"}
    (disposed,) = record["dispositions"]
    assert disposed["mode"] == "range_selected" and disposed["status"] == "non_comparable"
    assert "not a speed comparison" in record["reason"]
    assert "range_selected non_comparable under range-v1-reach-v1" in record["reason"]
    assert record["report"]["layout"]["rows_per_file_measured"] == MEASURED_ROWS
    # Deterministic, and every bound input moves the digest.
    assert measured_policy()["digest"] == record["digest"]
    other_evidence = measured_policy(dispositions=[reach_disposition(evidence_digest="9" * 64)])
    assert other_evidence["digest"] != record["digest"]
    other_sizing = measured_policy(sizing(processing__canonical_bytes=241_000))
    assert other_sizing["digest"] != record["digest"]
    with pytest.raises(tp.PolicyError, match="digest"):
        tp.check_frozen({**record, "dispositions": []}, PIN["source_id"])


def test_v2_policy_refuses_unsound_dispositions() -> None:
    measurement = sizing()
    sized = planner.sized_layout(layout(source_files=None), measurement)
    req = tp.Requirement(PIN["source_id"], 4_000_000, 1.15)
    timed = tp.evaluate(sized, req, tp.Ceilings(), models(), executable=tp.ALL_MODES)
    common: dict[str, Any] = {"inputs": {"whole": "6" * 64}, "subject": SUBJECT}
    # A mode with a timing cannot also be disposed of structurally.
    with pytest.raises(tp.PolicyError, match="must not also carry a timing"):
        tp.freeze(
            timed,
            basis="measured",
            sizing=measurement,
            dispositions=[reach_disposition()],
            **common,
        )
    whole_only = measured_policy()["report"]
    whole = tp.mode_disposition(
        tp.TransportMode.WHOLE_FILE_LOCAL,
        "infeasible",
        contract="authored",
        evidence_digest="7" * 64,
        evidence_sha256="8" * 64,
        summary="authored",
    )
    with pytest.raises(tp.PolicyError, match="selected mode"):
        tp.freeze(whole_only, basis="measured", sizing=measurement, dispositions=[whole], **common)
    with pytest.raises(tp.PolicyError, match="status"):
        tp.mode_disposition(
            tp.TransportMode.RANGE_SELECTED,
            "slower",
            contract="c",
            evidence_digest="7" * 64,
            evidence_sha256="8" * 64,
            summary="s",
        )
    with pytest.raises(tp.PolicyError, match="measured and binds"):
        tp.freeze(whole_only, basis="modeled", sizing=measurement, **common)
    with pytest.raises(tp.PolicyError, match="measured and binds"):
        tp.freeze(whole_only, basis="measured", dispositions=[reach_disposition()], **common)
    with pytest.raises(tp.PolicyError, match="subject"):
        tp.freeze(
            whole_only,
            basis="measured",
            inputs={"whole": "6" * 64},
            subject={**SUBJECT, "source_id": "finewiki"},
            sizing=measurement,
        )


def test_plan_sizes_from_measurement_over_calibration() -> None:
    calibrated = build()
    measured = build(layout=layout(source_files=None), policy=measured_policy())
    # Calibration: 100% acceptance at 50 B/row -> 10 files of 500,000 canonical bytes.
    assert calibrated["expected"]["files"] == 10
    assert "sizing" not in calibrated["inputs"]
    assert calibrated["expected"]["basis"].startswith("one measured file and calibration yield")
    # Measurement: 8,000 rows at 30 B/row -> 240,000 per file -> ceil(4.6e6 / 2.4e5) = 20 files.
    assert measured["expected"]["files"] == math.ceil(4_000_000 * 1.15 / 240_000) == 20
    assert measured["expected"]["rows"] == 20 * MEASURED_ROWS
    assert measured["expected"]["canonical_bytes"] == 20 * MEASURED_CANONICAL
    assert measured["inputs"]["layout"]["rows_per_file_estimate"] == MEASURED_ROWS
    assert measured["inputs"]["layout"]["canonical_bytes_per_row"] == 30.0
    assert measured["limits"]["max_rows_per_file"] == 2 * MEASURED_ROWS
    assert measured["inputs"]["sizing"]["digest"] == sizing()["digest"]
    assert measured["inputs"]["sizing"]["receipt_digest"] == receipt()["digest"]
    # The calibration evidence is still bound (schema/adapter), only its sizing is superseded.
    assert measured["inputs"]["calibration"] == calibrated["inputs"]["calibration"]
    assert measured["transport_mode"] == "whole_file_local"
    planner.check_plan(measured)
    assert planner.minted_from_record(measured).selected_files == [
        entry["file"] for entry in measured["selection"]["files"]
    ]


def test_plan_refuses_policy_of_another_revision_or_tampered_sizing() -> None:
    other = measured_policy(subject={**SUBJECT, "revision": "c" * 40})
    with pytest.raises(planner.PlanError, match="another source view"):
        build(layout=layout(source_files=None), policy=other)
    record = measured_policy()
    body = {k: v for k, v in record.items() if k != "digest"}
    body["sizing"] = {
        **body["sizing"],
        "derived": {**body["sizing"]["derived"], "rows_per_file": 1},
    }
    body["digest"] = canonical.digest(body)
    with pytest.raises(planner.PlanError, match="sizing measurement digest"):
        build(layout=layout(source_files=None), policy=body)
