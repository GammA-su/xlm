"""Authored tests for explicit component policy and fail-closed source integration."""

from __future__ import annotations

import copy
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from test_component_allowlist import PIN, _load
from test_component_calibration import COMPONENTS, fixture, redigest
from xlm.data.acquisition import component_calibration as cc
from xlm.data.acquisition import component_policy as cp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.sources import common_pile_license as lic


def calibration() -> dict[str, Any]:
    allowlist, inventory, receipt, _ = fixture()
    return cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)


def ceilings() -> dict[str, Any]:
    return {
        "max_file_bytes": 65536,
        "max_decoded_bytes_per_file": 2_000_000,
        "max_decompression_ratio": 40.0,
        "max_rows_per_file": 10000,
        "max_record_bytes": 100000,
        "max_canonical_bytes_per_file": 1_000_000,
        "max_durable_bytes_per_file": 3_000_000,
        "max_ledger_bytes": 100000,
        "scratch_cap_bytes": 10_000_000,
        "file_deadline_seconds": 60.0,
        "plan_deadline_seconds": 600.0,
        "processing_growth": ProcessingGrowth(
            output_bytes=2_000_000, source_max_bytes=65536
        ).model_dump(),
    }


def shares() -> dict[str, Any]:
    return {c: {"final_tokens": 50_000_000, "first_pass_tokens": 55_000_000} for c in COMPONENTS}


def test_bounds_reproduce_and_apply_without_global_mutation() -> None:
    cal = calibration()
    bounds = cp.build_bounds(cal, ceilings(), operator="fixture", rationale="authored limits")
    assert cp.check_bounds(bounds, cal) == bounds
    before = copy.deepcopy(planner.SOURCE_FILE_BOUNDS)
    policy, limits = planner.plan_limits(
        2,
        cc.layout_of(cal, record_sha256="a" * 64),
        tp.TransportMode.WHOLE_FILE_LOCAL,
        PIN.as_dict(),
        reviewed_bounds=bounds,
    )
    assert policy["max_record_bytes"] == 100000
    assert limits.max_decompression_ratio == 40
    assert limits.max_decompressed_bytes == 4_000_000
    assert policy["processing_growth"] == bounds["bounds"]["processing_growth"]
    assert planner.SOURCE_FILE_BOUNDS == before


@pytest.mark.parametrize(
    "mutation", ["missing", "negative", "nan", "scratch", "durable", "file", "identity"]
)
def test_reviewed_bounds_refuse_inconsistent_or_stale_inputs(mutation: str) -> None:
    cal, bounds = calibration(), ceilings()
    if mutation == "missing":
        bounds.pop("max_ledger_bytes")
    elif mutation == "negative":
        bounds["max_rows_per_file"] = -1
    elif mutation == "nan":
        bounds["max_decompression_ratio"] = float("nan")
    elif mutation == "scratch":
        bounds["scratch_cap_bytes"] = 100
    elif mutation == "durable":
        bounds["max_durable_bytes_per_file"] = 100
    elif mutation == "file":
        bounds["max_file_bytes"] = 100
    else:
        built = cp.build_bounds(cal, bounds, operator="fixture", rationale="fixture")
        built["binding"]["revision"] = "b" * 40
        redigest(built)
        with pytest.raises(cp.ComponentPolicyError, match="stale"):
            cp.check_bounds(built, cal)
        return
    with pytest.raises(cp.ComponentPolicyError):
        cp.build_bounds(cal, bounds, operator="fixture", rationale="fixture")


def test_split_binds_all_components_and_refuses_infeasible_capacity() -> None:
    cal = calibration()
    split = cp.build_split(cal, shares(), operator="fixture", rationale="fixture")
    assert cp.check_split(split, cal) == split
    with pytest.raises(cp.ComponentPolicyError, match="capacity"):
        cp.select_files(
            {"calibration": cal, "component_split": split},
            cal["inventory_snapshot"],
            acquired={},
            cursors={},
            safety=1,
            eligible=12,
        )
    # Every component has only a 1-byte deficit; its next file, never another
    # component's surplus, satisfies that deficit. This is a top-up test.
    acquired = {c: 220_000_000 - 1 for c in COMPONENTS}
    selected, cursors = cp.select_files(
        {"calibration": cal, "component_split": split},
        cal["inventory_snapshot"],
        acquired=acquired,
        cursors={},
        safety=1,
        eligible=12,
    )
    assert len(selected) == 6
    assert {e["file"].split("/")[0] for e in selected} == set(COMPONENTS)
    selected2, _ = cp.select_files(
        {"calibration": cal, "component_split": split},
        cal["inventory_snapshot"],
        acquired=acquired,
        cursors=cursors,
        safety=1,
        eligible=12,
    )
    assert not {e["file"] for e in selected} & {e["file"] for e in selected2}


def test_hash_prefix_requires_an_explicit_operator_decision() -> None:
    cal = calibration()
    decision = cp.build_split(
        cal, {"strategy": "hash_prefix"}, operator="fixture", rationale="explicit A choice"
    )
    assert cp.check_split(decision, cal) == decision
    selected, following = cp.select_files(
        {"calibration": cal, "component_split": decision},
        cal["inventory_snapshot"],
        acquired={"news": 1_319_999_999},
        cursors={},
        safety=1.15,
        eligible=12,
    )
    assert selected == [{"rank": 0, "file": cal["inventory_snapshot"]["files"][0]["file"]}]
    assert following == {"__hash__": 1}
    with pytest.raises(cp.ComponentPolicyError):
        cp.build_split(cal, {"strategy": "hash_prefix"}, operator="", rationale="")


@pytest.mark.parametrize("mutation", ["missing", "extra", "total", "negative", "revision"])
def test_split_refusals(mutation: str) -> None:
    cal, values = calibration(), shares()
    if mutation == "missing":
        values.pop("news")
    elif mutation == "extra":
        values["excluded"] = values["news"]
    elif mutation == "total":
        values["news"]["final_tokens"] += 1
    elif mutation == "negative":
        values["news"]["first_pass_tokens"] = -1
    else:
        built = cp.build_split(cal, values, operator="fixture", rationale="fixture")
        built["binding"]["revision"] = "b" * 40
        redigest(built)
        with pytest.raises(cp.ComponentPolicyError):
            cp.check_split(built, cal)
        return
    with pytest.raises(cp.ComponentPolicyError):
        cp.build_split(cal, values, operator="fixture", rationale="fixture")


def test_component_license_basis_is_narrow_and_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, _, _, _ = fixture()
    path = tmp_path / "matrix.json"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(lic, "MATRIX", path)
    basis = lic.build_basis(record)
    assert basis["declared_repository_license"] is None
    assert lic.valid_basis(basis, PIN.source_id, PIN.repository, PIN.revision)
    assert not lic.valid_basis(basis, "unrelated", PIN.repository, PIN.revision)
    assert not lic.valid_basis(basis, PIN.source_id, PIN.repository, "b" * 40)
    path.write_bytes(b"changed evidence matrix")
    assert not lic.valid_basis(basis, PIN.source_id, PIN.repository, PIN.revision)


def test_driver_uses_component_layout_and_only_gzip_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load("mix01_source")
    cal = calibration()
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(cal), encoding="utf-8")
    monkeypatch.setattr(cli, "component_calibration", lambda *_: (cal, path))
    monkeypatch.setattr(
        cli,
        "requirement_of",
        lambda *_: planner.Requirement("common_pile_prose", 100, 400, 1.15, "q", "e"),
    )
    args = cli.build_parser().parse_args(["policy", "model", "--source-key", "common_pile"])
    spec = cli.spec_of("common_pile")
    layout, extra = cli.layout_of(args, spec)
    assert extra["perf"] == cal["observed_transfer"]
    assert layout.source_files == 12
    component = {
        "calibration": cal,
        "reviewed_bounds": cp.build_bounds(cal, ceilings(), operator="fixture", rationale="test"),
        "component_split": cp.build_split(
            cal, {"strategy": "hash_prefix"}, operator="fixture", rationale="test"
        ),
    }
    monkeypatch.setattr(cli, "component_ready", lambda *_: component)
    # This tiny authored corpus cannot fulfill the real 1.32 GB split.
    with pytest.raises(cp.ComponentPolicyError, match="capacity exhausted"):
        cli.evaluate_policy(args, spec)


def test_component_expected_uses_each_density_and_retained_files() -> None:
    cal = {
        "inventory_snapshot": {
            "files": [
                {"file": "news/a.jsonl.gz", "size_bytes": 10},
                {"file": "project_gutenberg/b.jsonl.gz", "size_bytes": 100},
            ]
        },
        "components": {
            "news": {
                "measured": {
                    "canonical_bytes_per_compressed_byte": 2,
                    "compressed_bytes_per_row": 1,
                }
            },
            "project_gutenberg": {
                "measured": {
                    "canonical_bytes_per_compressed_byte": 8,
                    "compressed_bytes_per_row": 20,
                }
            },
        },
    }
    names = ["news/a.jsonl.gz", "project_gutenberg/b.jsonl.gz"]
    result = cp.expected_selection(cal, names, names[1:])
    assert (result["canonical_bytes"], result["rows"], result["transfer_bytes"]) == (820, 15, 100)
    assert result["requests"] == 2
    assert result["components"]["news"]["canonical_bytes"] == 20
    with pytest.raises(cp.ComponentPolicyError):
        cp.expected_selection(cal, names + names, names)
    with pytest.raises(cp.ComponentPolicyError):
        cp.expected_selection(cal, names, ["excluded/x.jsonl.gz"])


def test_component_plan_topup_keeps_independent_cursors_and_mints_identically() -> None:
    from test_source_plan import models

    allowlist, inventory, receipt, _ = fixture()
    for entry in inventory["files"]:
        entry["size_bytes"] = 1_000_000_000
    inventory["inventory_digest"] = planner.inventory_digest(inventory)
    for entry in receipt["files"]:
        entry["identity"]["total_bytes"] = 1_000_000_000
    receipt["bindings"]["production_inventory_digest"] = inventory["inventory_digest"]
    redigest(receipt)
    cal = cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)
    bound = ceilings() | {
        "max_file_bytes": 1_000_000_000,
        "max_decoded_bytes_per_file": 8_000_000_000,
        "max_canonical_bytes_per_file": 4_000_000_000,
        "max_durable_bytes_per_file": 10_000_000_000,
        "scratch_cap_bytes": 20_000_000_000,
        "processing_growth": ProcessingGrowth(
            output_bytes=8_000_000_000, source_max_bytes=1_000_000_000
        ).model_dump(),
    }
    component = {
        "calibration": cal,
        "reviewed_bounds": cp.build_bounds(cal, bound, operator="fixture", rationale="fixture"),
        "component_split": cp.build_split(cal, shares(), operator="fixture", rationale="fixture"),
    }
    layout = cc.layout_of(cal, record_sha256="a" * 64)
    report = tp.evaluate(
        layout,
        tp.Requirement(PIN.source_id, 1_320_000_000, 1.15),
        tp.Ceilings(),
        {m: v for m, v in models().items() if m in planner.JSONL_GZ_MODES},
    )
    frozen = tp.freeze(report, basis="modeled", inputs={"fixture": "authored"})
    kwargs: dict[str, Any] = dict(
        source_key="common_pile",
        pin=PIN.as_dict(),
        requirement=planner.Requirement(
            PIN.component_id, 330_000_000, 1_320_000_000, 1.15, "q", "e"
        ),
        inventory=inventory,
        inventory_sha256="a" * 64,
        layout=layout,
        calibration={"fixture": "a" * 64},
        policy=frozen,
        admission={
            "decision_id": "fixture",
            "evidence_id": "fixture",
            "probe_fingerprint": "a" * 64,
        },
        benchmark_reserved=0,
        component_policy=component,
    )
    first = planner.build_plan(**kwargs)
    assert len(first["selection"]["files"]) == 6
    assert first["expected"]["transfer_bytes"] == 6_000_000_000
    assert set(first["expected"]["components"]) == set(COMPONENTS)
    assert first["limits"]["download_workers"] == first["limits"]["process_workers"] == 1
    from xlm.data.acquisition import component_transport as ct

    modeled = ct.evaluate(
        component,
        layout,
        tp.Requirement(PIN.source_id, 1_320_000_000, 1.15),
        models(),
        benchmark_reserved=0,
        durable_budget_bytes=None,
    )
    assert len(modeled["candidates"]) == 1
    candidate = modeled["candidates"][0]
    assert candidate["transfer_bytes"] == 6_000_000_000
    assert candidate["files"] == 6
    assert modeled["ceilings"]["scratch_bytes"] == 20_000_000_000
    with pytest.raises(tp.PolicyError, match="no transport mode"):
        ct.evaluate(
            component,
            layout,
            tp.Requirement(PIN.source_id, 1_320_000_000, 1.15),
            models(),
            benchmark_reserved=0,
            durable_budget_bytes=1,
        )
    assert planner.minted_from_record(first).plan_hash == first["acquisition_plan"]["plan_hash"]
    before = planner.Predecessor(first, 1_000_000_000, 6, "b" * 64)
    second = planner.build_plan(
        **kwargs, predecessor=before, component_acquired={c: 219_999_999 for c in COMPONENTS}
    )
    assert len(second["selection"]["files"]) == 6
    assert not {e["file"] for e in first["selection"]["files"]} & {
        e["file"] for e in second["selection"]["files"]
    }


def test_component_license_admission_still_requires_explicit_operator_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from xlm.data.sources import certified_evidence as ce
    from xlm.data.sources import common_pile_evidence as cpe
    from xlm.data.sources import mix01_admission as review
    from xlm.data.sources.admission import AdmissionGate
    from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome

    allowlist, inventory, receipt, rows = fixture()
    path = tmp_path / "matrix.json"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(lic, "MATRIX", path)
    facts = cpe.translate_component_samples(
        PIN, [(json.dumps(receipt).encode(), rows)], allowlist=allowlist, inventory=inventory
    )
    facts.probe["component_license_basis"] = lic.build_basis(allowlist)
    metadata = ProbeEvidenceRecord(
        source_id=PIN.source_id,
        view_id=PIN.view_id,
        provider=PIN.provider,
        repository=PIN.repository,
        immutable_revision=PIN.revision,
        outcome=ProbeOutcome.PARTIAL,
        observed_timestamp="authored fixture",
    )
    ce.translate_store_probe(PIN, facts, metadata, "fixture", "a" * 64)
    bridge, evidence = ce.build_bridge(PIN, facts, evidence_type=EvidenceType.REAL_OBSERVED)
    assert evidence.declared_license is None
    assert not AdmissionGate.evaluate(evidence).admitted
    decisions = review.OperatorDecisions(
        "fixture",
        "approve_research_pretraining",
        "approved",
        "suspect_with_mitigation",
        "authored unit test",
    )
    reviews = review.build_reviews(PIN, bridge, decisions, [])
    hashes = {
        name: hashlib.sha256(json.dumps(value).encode()).hexdigest()
        for name, value in reviews.items()
    }
    decision = review.build_decision(PIN, evidence, bridge, reviews, hashes)
    result = AdmissionGate.evaluate(evidence, decision)
    assert result.admitted, result.reasons
    assert ce.record_from_receipt(bridge) == evidence
    cli = _load("mix01_source")
    monkeypatch.setattr(cli, "stored_receipt", lambda *_: (PIN, bridge))
    monkeypatch.setattr(cli, "load_probe_evidence", lambda *_: evidence)
    monkeypatch.setattr(cli, "load_admission_decision", lambda *_: decision)
    spec = cli.spec_of("common_pile")
    original_identity = cli.current_admission(spec, None)
    assert original_identity["admission_decision_digest"]
    decision.operator_notes += " renewed operator review"
    assert cli.current_admission(spec, None) != original_identity
    evidence.view_id = "unreviewed_view"
    assert not AdmissionGate.evaluate(evidence, decision).admitted
    evidence.view_id = PIN.view_id
    decision.license_review = "rejected"
    assert not AdmissionGate.evaluate(evidence, decision).admitted
    decision.license_review = "approved"
    evidence.resource_metrics.pop("component_license_basis")
    assert not AdmissionGate.evaluate(evidence, decision).admitted


@pytest.mark.parametrize("command", ["cmd_authorize", "cmd_run"])
@pytest.mark.parametrize("changed", ["component_split", "reviewed_bounds"])
def test_driver_refuses_policy_drift_before_authorization_or_execution(
    monkeypatch: pytest.MonkeyPatch, command: str, changed: str
) -> None:
    cli = _load("mix01_source")
    current = {"component_split": {"digest": "a"}, "reviewed_bounds": {"digest": "b"}}
    planned = copy.deepcopy(current)
    planned[changed]["digest"] = "old"
    monkeypatch.setattr(cli, "roots_of", lambda *_: None)
    monkeypatch.setattr(cli.runner, "load_plan", lambda *_: {"inputs": planned})
    monkeypatch.setattr(cli, "component_ready", lambda *_: current)
    with pytest.raises(cli.DriverError, match=f"{changed} changed after planning"):
        getattr(cli, command)(Namespace(source_key="common_pile", plan=1))


def test_component_ready_requires_bounds_split_and_current_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _load("mix01_source")
    cal = calibration()
    path = tmp_path / "component-calibration.json"
    monkeypatch.setattr(cli, "component_calibration", lambda *_: (cal, path))
    args, spec = Namespace(), cli.spec_of("common_pile")
    with pytest.raises(cli.DriverError, match="reviewed source bounds missing"):
        cli.component_ready(args, spec)
    bounds = cp.build_bounds(cal, ceilings(), operator="fixture", rationale="fixture")
    path.with_name("reviewed-bounds.json").write_text(json.dumps(bounds), encoding="utf-8")
    with pytest.raises(cli.DriverError, match="allocation decision missing"):
        cli.component_ready(args, spec)
    split = cp.build_split(cal, shares(), operator="fixture", rationale="fixture")
    path.with_name("component-split.json").write_text(json.dumps(split), encoding="utf-8")
    target = object()
    monkeypatch.setattr(cli, "store", lambda: target)
    monkeypatch.setattr(cli, "build_bridge", lambda *_: (None, "bridge", "evidence"))
    calls = []

    def verify(store: Any, pin: Any, *, rebuild: Any) -> None:
        assert store is target and pin.source_id == "common_pile"
        assert rebuild() == ("bridge", "evidence")
        calls.append("verify")

    monkeypatch.setattr(cli.ce, "verify_current", verify)
    monkeypatch.setattr(cli, "current_admission", lambda *_: calls.append("admit"))
    assert cli.component_ready(args, spec)["component_split"] == split
    assert calls == ["verify", "admit"]

    def stale(*args: Any, **kwargs: Any) -> None:
        raise cli.ce.BridgeRefusal("fixture stale bridge")

    calls.clear()
    monkeypatch.setattr(cli.ce, "verify_current", stale)
    with pytest.raises(cli.ce.BridgeRefusal, match="stale bridge"):
        cli.component_ready(args, spec)
    assert not calls
