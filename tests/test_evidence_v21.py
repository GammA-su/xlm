"""Evidence-v2.1 amendment: authored synthetic fixtures + repo frozen files.

No network (sockets blocked), no X: reads, no G: operator-receipt reads,
no real acquisition. Covers v2.1 identity/lineage, amended transfer
caps, durable carry-in, exact range schedules, the M historical audit,
memory/disk/time reservations, and child-plan semantics.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import (
    budgets,
    canonical,
    carry,
    child_plans,
    frozen,
    lineage,
    m_audit,
    reserves,
    schedule,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_V21 = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _freeze() -> dict[str, Any]:
    return json.loads((EVIDENCE_V21 / "freeze.json").read_bytes())


# --------------------------------------------------------------------------
# v2.1 identity and lineage.
# --------------------------------------------------------------------------


def test_v21_freeze_verifies() -> None:
    freeze = _freeze()
    assert freeze["digest"] == frozen.V21_FREEZE_DIGEST
    assert freeze["digest"] == ("bac82d6b9538f4005f7f0ffee6aa5c4f3a5394098c0fae8f63fc94c76832a7cd")
    lineage.verify_freeze(freeze)
    protocol_raw = (
        ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md"
    ).read_bytes()
    lineage.verify_protocol_bytes(protocol_raw)
    assert len(protocol_raw) == freeze["artifacts"][0]["bytes"]


def test_v21_parent_freeze_bound() -> None:
    freeze = _freeze()
    assert freeze["parent_freeze_digest"] == frozen.FREEZE_DIGEST
    assert freeze["parent_freeze_digest"] == (
        "fe2157799a86fe777e45c220716563f8a73245a12593c40909222555bf1b8248"
    )
    parent = json.loads(
        (ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json").read_bytes()
    )
    assert parent["digest"] == frozen.FREEZE_DIGEST


def test_v21_protocol_sha_verifies() -> None:
    raw = (
        ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md"
    ).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == frozen.V21_PROTOCOL_SHA256
    assert frozen.V21_PROTOCOL_SHA256 == (
        "1c437881148c3d6c1c42ca610e364055a0625732b07361f2e9271d0918fcdb4b"
    )


def test_v21_parent_roles_and_digests() -> None:
    freeze = _freeze()
    assert freeze["arm_m_footer_evidence_digest"] == frozen.V21_M_EVIDENCE_DIGEST
    assert freeze["arm_t_v2_0_cost_map_digest"] == frozen.V21_COSTMAP_DIGEST
    assert freeze["selection_digest"] == frozen.V21_SELECTION_DIGEST
    assert (
        freeze["parents"]["footer_evidence.json"]["role"]
        == "adopted_observation_not_resource_compliance"
    )
    assert freeze["parents"]["text_cost_evidence_attempt2.incomplete.json"]["role"] == (
        "historical_incomplete_cost_map"
    )
    assert freeze["parents"]["text_selection_manifest.json"]["role"] == "byte_identical_selection"


def test_v21_freeze_tamper_refused() -> None:
    freeze = _freeze()
    bad = dict(freeze)
    bad["digest"] = "0" * 64
    with pytest.raises(lineage.LineageError):
        lineage.verify_freeze(bad)
    with pytest.raises(lineage.LineageError):
        lineage.verify_protocol_bytes(b"tampered")
    with pytest.raises(lineage.LineageError):
        lineage.verify_parent(freeze, "no-such-parent", raw=None, digest=None)


def test_selection_identity_constants() -> None:
    assert frozen.V21_SELECTION_DIGEST == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert frozen.V21_SELECTION_BYTES == 23807
    assert frozen.V21_SELECTION_SHA256 == (
        "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
    )
    lineage.check_scientific_namespace()


def test_scientific_namespace_stays_v2_0() -> None:
    import hashlib
    import hmac as hmac_mod

    from xlm.data.evidence_v2 import blinding, text_select

    assert frozen.V21_SCIENTIFIC_NAMESPACE == "essential-web-evidence-v2.0"
    assert frozen.V21_SCIENTIFIC_NAMESPACE == frozen.PROTOCOL_VERSION
    assert frozen.V21_PROTOCOL_VERSION == "essential-web-evidence-v2.1"
    # Ranking, review IDs, and review order pin the v2.0 strings literally.
    locator = [frozen.REPOSITORY, frozen.REVISION, "f.parquet", 7]
    expected_rank = hashlib.sha256(
        canonical.canonical_bytes(
            ["essential-web-evidence-v2.0", "text-select", 20260927, "s", "c", *locator]
        )
    ).hexdigest()
    assert text_select.rank_digest("s", "c", locator) == expected_rank
    secret = b"k" * 32
    expected_id = (
        "ew2-"
        + hmac_mod.new(
            secret,
            canonical.canonical_bytes(["essential-web-evidence-v2.0", "review-id", locator]),
            hashlib.sha256,
        ).hexdigest()
    )
    assert blinding.review_id(secret, locator) == expected_id
    assert blinding.order_key("reviewer-1", expected_id) == canonical.digest(
        ["essential-web-evidence-v2.0", "review-order", 20260928, "reviewer-1", expected_id]
    )


def test_selection_rewrite_refused() -> None:
    body = {
        "total_selected": 118,
        "protocol_version": "essential-web-evidence-v2.1",
        "digest": frozen.V21_SELECTION_DIGEST,
    }
    raw = canonical.canonical_bytes(body)
    with pytest.raises(lineage.LineageError):
        lineage.verify_selection_identity(raw)


# --------------------------------------------------------------------------
# Amended transfer caps.
# --------------------------------------------------------------------------


def test_v21_transfer_caps_exact() -> None:
    caps = frozen.V21_T_LIMITS
    assert caps["footer_bytes_per_file_max"] == 2097152
    assert caps["data_bytes_per_file_max"] == 31457280
    assert caps["transfer_bytes_per_file_max"] == 33554432
    assert caps["footer_bytes_total_max"] == 16777216
    assert caps["data_bytes_arm_max"] == 251658240
    assert caps["transfer_bytes_arm_max"] == 268435456
    assert caps["data_bytes_per_file_max"] * 8 == caps["data_bytes_arm_max"]
    for key in frozen.ARM_T_LIMITS:
        if key not in (
            "data_bytes_per_file_max",
            "data_bytes_arm_max",
            "transfer_bytes_per_file_max",
            "transfer_bytes_arm_max",
        ):
            assert caps[key] == frozen.ARM_T_LIMITS[key], key


def test_v21_no_borrowing_between_stages() -> None:
    ledger = budgets.new_arm_t_v21()
    ledger.charge_transfer("f.parquet", 2097152, kind="footer")
    with pytest.raises(budgets.BudgetRefusal, match="footer"):
        ledger.charge_transfer("f.parquet", 1, kind="footer")
    # Data keeps its own full subcap despite a full footer cell (split in
    # 4 MiB bodies: a single 31 MiB body would violate the body cap).
    for _ in range(7):
        ledger.charge_transfer("f.parquet", 4194304, kind="data")
    ledger.charge_transfer("f.parquet", 2097152, kind="data")
    with pytest.raises(budgets.BudgetRefusal, match="data"):
        ledger.charge_transfer("f.parquet", 1, kind="data")
    cell = ledger.snapshot()["transfer_per_file"]["f.parquet"]
    assert cell == {"footer": 2097152, "data": 31457280}


def test_v21_totals_bind_both_stages() -> None:
    ledger = budgets.new_arm_t_v21()
    assert ledger.snapshot()["transfer_by_category"] == {"footer": 0, "data": 0}
    ledger.charge_transfer("f.parquet", 100, kind="footer")
    snapshot = ledger.snapshot()
    assert snapshot["transfer_per_file"]["f.parquet"] == {"footer": 100, "data": 0}
    assert "requests_per_file" in snapshot


def test_v21_combined_per_file_requests() -> None:
    ledger = budgets.new_arm_t_v21()
    for _ in range(60):
        ledger.charge_file_request("f.parquet", kind="footer")
    for _ in range(40):
        ledger.charge_file_request("f.parquet", kind="data")
    with pytest.raises(budgets.BudgetRefusal, match="combined"):
        ledger.charge_file_request("f.parquet", kind="data")
    # The refused 101st attempt still consumed its charge (fail-closed).
    assert ledger.snapshot()["requests_per_file"]["f.parquet"] == {
        "footer": 60,
        "data": 41,
    }


def test_v21_response_body_cap_still_enforced() -> None:
    ledger = budgets.new_arm_t_v21()
    with pytest.raises(budgets.BudgetRefusal, match="single response body"):
        ledger.charge_response_body(4194305, kind="data")


def test_v20_defaults_unchanged() -> None:
    ledger = budgets.new_arm_t()
    assert ledger.ceilings["data_bytes_per_file_max"] == 14680064
    assert ledger.ceilings["transfer_bytes_per_file_max"] == 16777216
    assert ledger.ceilings["transfer_bytes_arm_max"] == 134217728


# --------------------------------------------------------------------------
# Durable carry-in.
# --------------------------------------------------------------------------


def _carry_cells() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    attempt2 = {
        f"dev-{i}.parquet": {
            "requests": 6,
            "bytes": 200000 + i,
            "measurement": "derived_uniform_split_of_measured_arm_total",
        }
        for i in range(8)
    }
    total_requests = sum(c["requests"] for c in attempt2.values())
    total_bytes = sum(c["bytes"] for c in attempt2.values())
    prior = {
        "dev-0.parquet": {
            "requests": 54 - total_requests,
            "bytes": 2231492 - total_bytes,
            "measurement": "measured",
        }
    }
    return attempt2, prior


def test_carry_entries_sum_to_frozen_totals() -> None:
    attempt2, prior = _carry_cells()
    entries = carry.v21_t_carry_entries(
        frozen.V21_COSTMAP_DIGEST, "g:/attempt2", attempt2, "ab" * 32, "g:/prior", prior
    )
    assert sum(e.requests for e in entries) == 54
    assert sum(e.bytes for e in entries) == 2231492
    assert len(entries) == 9
    bad = dict(attempt2)
    bad["dev-0.parquet"] = dict(bad["dev-0.parquet"], requests=7)
    with pytest.raises(carry.CarryError):
        carry.v21_t_carry_entries(
            frozen.V21_COSTMAP_DIGEST, "g:/attempt2", bad, "ab" * 32, "g:/prior", prior
        )


def test_carry_no_reset_duplicates_unknown(tmp_path: Path) -> None:
    attempt2, prior = _carry_cells()
    entries = carry.v21_t_carry_entries(
        frozen.V21_COSTMAP_DIGEST, "g:/attempt2", attempt2, "ab" * 32, "g:/prior", prior
    )
    durable = carry.DurableLedger(budgets.new_arm_t_v21())
    for entry in entries:
        assert durable.adopt(entry) is True
    assert durable.adopt(entries[0]) is False
    assert durable.duplicates == [entries[0].entry_id]
    effective = durable.effective()
    assert effective["requests"] == 54
    assert effective["response_body_bytes"] == 2231492
    assert effective["transfer_per_file"]["dev-0.parquet"]["footer"] == (
        attempt2["dev-0.parquet"]["bytes"] + prior["dev-0.parquet"]["bytes"]
    )
    assert durable.readiness()["ready"] is True
    durable.register_unknown("dev-9.parquet", "no record for this file")
    assert durable.readiness()["ready"] is False
    assert "unknown history blocks" in durable.readiness()["reasons"][0]
    binding = durable.save(tmp_path / "carry.json")
    assert binding["bytes"] > 0
    reloaded = carry.DurableLedger.load(tmp_path / "carry.json", budgets.new_arm_t_v21())
    assert reloaded.effective()["requests"] == 54
    assert reloaded.effective()["response_body_bytes"] == 2231492
    assert reloaded.duplicates == []
    receipt = durable.reconciliation_receipt(command="reconcile-carry-in", exit_status=0)
    assert receipt["adopted_requests"] == 54
    assert receipt["adopted_bytes"] == 2231492
    assert len(receipt["adopted"]) == 9
    assert receipt["freeze_digest"] == frozen.V21_FREEZE_DIGEST


# --------------------------------------------------------------------------
# Exact range schedules.
# --------------------------------------------------------------------------


def test_range_split_exact_no_gaps_no_overlap() -> None:
    ranges = schedule.split_span(158729170, 22512998)
    assert len(ranges) == 6
    assert ranges[0][0] == 158729170
    assert ranges[-1][1] == 158729170 + 22512998
    for start, end in ranges:
        assert end - start <= 4194304
    assert sum(end - start for start, end in ranges) == 22512998
    exact = schedule.split_span(0, 2 * 4194304)
    assert exact == [(0, 4194304), (4194304, 8388608)]
    with pytest.raises(schedule.ScheduleError):
        schedule.split_span(0, 0)
    with pytest.raises(schedule.ScheduleError):
        schedule.chunk_span({"offset": None, "compressed_bytes": 10}, 100)


def test_schedule_arm_remaining_and_attempt_bounds() -> None:
    files = {
        "dev-a.parquet": [{"offset": 100, "compressed_bytes": 9000000}],
        "dev-b.parquet": [{"offset": 0, "compressed_bytes": 100}],
    }
    lengths = {"dev-a.parquet": 20000000, "dev-b.parquet": 1000}
    scheduled = schedule.schedule_arm(files, lengths)
    assert scheduled["nominal_data_range_count"] == 3 + 1
    assert scheduled["nominal_data_bytes"] == 9000100
    remaining = schedule.remaining_budgets(
        nominal_data_ranges=3,
        nominal_data_bytes=9000000,
        nominal_controls=2,
        carried_requests=12,
        carried_bytes=473454,
        request_cap=100,
        byte_cap=31457280,
    )
    assert remaining["nominal_requests"] == 5
    assert remaining["requests_remaining_after_nominal"] == 100 - 12 - 5
    assert remaining["bytes_remaining_after_nominal"] == 31457280 - 473454 - 9000000
    bounds = remaining["attempt_bounds"]
    assert bounds["full_nominal_executions_fitting"] == 3
    assert bounds["reattempts_beyond_nominal"] == 2
    caps = schedule.v21_data_caps()
    assert caps["per_file_data"] == 31457280
    assert caps["arm_requests"] == 800


def test_real_2014_15_schedule_shape() -> None:
    # Recorded chunk geometry as literals (no receipt reads in tests).
    scheduled = schedule.schedule_file(
        "dev", [{"offset": 158729170, "compressed_bytes": 22512998}], 246305126
    )
    assert scheduled["nominal_data_range_count"] == 6
    assert scheduled["nominal_data_bytes"] == 22512998
    assert scheduled["nominal_requests"] == 8


# --------------------------------------------------------------------------
# M historical audit: A, B-compliant, C.
# --------------------------------------------------------------------------


def _old_receipt() -> dict[str, Any]:
    return {
        "reason": "footer discovery refused: redirect hop 4 exceeds the 3-hop ceiling",
        "failed_file": "dev-b.parquet",
        "completed_units": [
            {"file": "dev-a.parquet", "footer_requests_used": 3, "footer_bytes_used": 100}
        ],
        "budget": {"failed_requests": 1},
    }


def _complete_receipt() -> dict[str, Any]:
    return {
        "units": [
            {
                "file": "dev-a.parquet",
                "footer_requests_used": 6,
                "footer_bytes_used": 200,
                "footer_ranges": [
                    {"start": 0, "end": 3, "bytes": 4, "hops": 1},
                    {"start": 10, "end": 20, "bytes": 11, "hops": 1},
                    {"start": 30, "end": 40, "bytes": 11, "hops": 1},
                ],
            }
        ]
    }


def test_m_audit_conclusion_a_blocked() -> None:
    verdict = m_audit.audit_m_file_compliance(_old_receipt(), _complete_receipt(), "dev-a.parquet")
    assert verdict["conclusion"] == "A"
    assert verdict["blocked"] is True
    evidence = verdict["evidence"]
    assert evidence["old_attempt"]["physical_requests"] == 6
    assert evidence["complete_pass"]["physical_requests"] == 6
    assert evidence["cumulative_physical_requests"] == 12
    assert m_audit.m_file_cap() == 10


def test_m_audit_compliant_and_unknown_paths() -> None:
    old = _old_receipt()
    complete = {
        "units": [
            {
                "file": "dev-a.parquet",
                "footer_requests_used": 1,
                "footer_bytes_used": 10,
                "footer_ranges": [{"start": 0, "end": 3, "bytes": 4, "hops": 0}],
            }
        ]
    }
    verdict = m_audit.audit_m_file_compliance(old, complete, "dev-a.parquet")
    assert verdict["conclusion"] == "B_COMPLIANT"
    assert verdict["blocked"] is False
    assert m_audit.audit_unknown_history(True)["blocked"] is False
    assert m_audit.audit_unknown_history(False)["conclusion"] == "C"
    with pytest.raises(m_audit.AuditError):
        m_audit.old_attempt_file_physical({"reason": "no hop here", "completed_units": []}, "x")
    with pytest.raises(m_audit.AuditError):
        m_audit.audit_m_file_compliance(
            _old_receipt(), _complete_receipt(), "dev-a.parquet", shared_event_ids=True
        )
    with pytest.raises(m_audit.AuditError):
        m_audit.old_attempt_file_physical(
            _old_receipt(), "dev-a.parquet", earlier_files=["dev-0.parquet"]
        )


# --------------------------------------------------------------------------
# Memory / disk / time reservations.
# --------------------------------------------------------------------------


def test_memory_reservation_overflow_refuses() -> None:
    good = reserves.memory_reservation(
        compressed_staged_bytes=22512998,
        decompressed_upper_bytes=38450828,
        output_retained_bytes=16 * 65536,
    )
    assert good["fits"] is True
    assert good["reservation_bytes"] == (
        22512998 + 38450828 + 4194304 + 33554432 + 1048576 + 1048576
    )
    bad = reserves.memory_reservation(
        compressed_staged_bytes=200000000,
        decompressed_upper_bytes=200000000,
        output_retained_bytes=100,
    )
    assert bad["fits"] is False
    guard = reserves.supervisor_guard(
        reservation_bytes=bad["reservation_bytes"], measured_rss_bytes=None
    )
    assert guard["allowed"] is False
    guard2 = reserves.supervisor_guard(
        reservation_bytes=good["reservation_bytes"], measured_rss_bytes=100000000
    )
    assert guard2["allowed"] is True
    with pytest.raises(reserves.ReservationError):
        reserves.memory_reservation(
            compressed_staged_bytes=-1, decompressed_upper_bytes=0, output_retained_bytes=0
        )


def test_disk_high_water_includes_atomic_temp() -> None:
    schedule_out = reserves.disk_schedule(
        adopted_artifact_bytes=0,
        per_file_stages=[
            {"file": "a", "scratch": 50000000, "final": 1000000},
            {"file": "b", "scratch": 1000000, "final": 500000},
        ],
        review_package_bytes=16777216,
        log_bytes=0,
    )
    assert schedule_out["high_water_bytes"] == 50000000 + 1000000
    assert schedule_out["final_bytes"] == 16777216
    assert schedule_out["fits"]["scratch"] is True
    assert schedule_out["fits"]["final"] is True
    assert schedule_out["fits"]["combined"] is True
    assert reserves.output_retained_upper(118) == 118 * 65536
    overflowing = reserves.disk_schedule(
        adopted_artifact_bytes=0,
        per_file_stages=[{"file": "a", "scratch": 600000000, "final": 0}],
        review_package_bytes=0,
        log_bytes=0,
    )
    assert overflowing["fits"]["scratch"] is False


def test_deadline_enforcement() -> None:
    state = reserves.deadlines(elapsed_seconds=0.0)
    assert state["exhausted"] is False
    assert state["arm_remaining"] == 1800.0
    assert reserves.deadlines(elapsed_seconds=1800.0)["exhausted"] is True
    ledger = budgets.new_arm_t_v21()
    ledger.charge_time(1799.0, kind="stage")
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_time(2.0, kind="stage")


# --------------------------------------------------------------------------
# Lineage roles and child artifacts.
# --------------------------------------------------------------------------


def test_parent_roles_preserved_not_relabeled() -> None:
    freeze = _freeze()
    m_parent = lineage.verify_parent(
        freeze, "footer_evidence.json", raw=None, digest=frozen.V21_M_EVIDENCE_DIGEST
    )
    assert m_parent["role"] == "adopted_observation_not_resource_compliance"
    t_parent = lineage.verify_parent(
        freeze,
        "text_cost_evidence_attempt2.incomplete.json",
        raw=None,
        digest=frozen.V21_COSTMAP_DIGEST,
    )
    assert t_parent["role"] == "historical_incomplete_cost_map"
    with pytest.raises(lineage.LineageError):
        lineage.verify_parent(freeze, "footer_evidence.json", raw=None, digest="0" * 64)
    with pytest.raises(lineage.LineageError):
        lineage.verify_parent(
            freeze,
            "footer_evidence.json",
            raw=None,
            digest=frozen.V21_M_EVIDENCE_DIGEST,
            expected_role="wrong_role",
        )


def test_m_blocked_plan_requires_blocked_audit() -> None:

    audit = {"conclusion": "A", "evidence": {"cumulative_physical_requests": 12}}
    blocked = child_plans.seal(
        child_plans.build_m_blocked_plan(
            audit=audit, m_windows=[{"file": "f", "absolute_window": [0, 512]}], command="t"
        )
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["executable"] is False
    assert blocked["authorization"] == "NONE"
    assert blocked["scientific_identity_namespace"] == "essential-web-evidence-v2.0"
    with pytest.raises(child_plans.ChildPlanError):
        child_plans.build_m_blocked_plan(
            audit={"conclusion": "B_COMPLIANT"}, m_windows=[], command="t"
        )


def test_t_child_plan_binds_frozen_selection() -> None:

    plan = child_plans.seal(
        child_plans.build_t_child_plan(
            selection_digest=frozen.V21_SELECTION_DIGEST,
            total_selected=118,
            wanted_by_file={"f.parquet": [1, 2]},
            schedule={"files": {}},
            remaining={},
            reservations={},
            carry_digest="ab" * 32,
            costmap_digest=frozen.V21_COSTMAP_DIGEST,
            command="t",
        )
    )
    assert plan["status"] == "DRY_NOT_AUTHORIZED"
    assert plan["executable"] is False
    assert plan["selection_total"] == 118
    with pytest.raises(child_plans.ChildPlanError):
        child_plans.build_t_child_plan(
            selection_digest="0" * 64,
            total_selected=118,
            wanted_by_file={},
            schedule={},
            remaining={},
            reservations={},
            carry_digest="ab" * 32,
            costmap_digest=frozen.V21_COSTMAP_DIGEST,
            command="t",
        )
