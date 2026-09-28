"""Evidence-v2.2 amendment: authored synthetic fixtures + repo frozen files.

No network (sockets blocked), no X: reads, no G: operator-receipt reads,
no real acquisition. Covers v2.2 identity/lineage, the M request-cap
change, M historical byte accounting, revalidation, data schedules, the
T dictionary-prefix correction, durable-ledger repair, memory/disk/
deadline enforcement, and child-plan semantics.
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
    frozen,
    lineage,
    m_audit,
    reserves,
    schedule,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_V22 = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _freeze22() -> dict[str, Any]:
    return json.loads((EVIDENCE_V22 / "freeze.json").read_bytes())


# --------------------------------------------------------------------------
# v2.2 identity and lineage.
# --------------------------------------------------------------------------


def test_v22_freeze_verifies() -> None:
    freeze = _freeze22()
    assert freeze["digest"] == frozen.V22_FREEZE_DIGEST
    assert freeze["digest"] == ("b6602a445307d9638913c046b4cfebab3356e20559d89b3f2ab601aa924ebd0c")
    lineage.verify_v22_freeze(freeze)
    lineage.verify_v22_protocol_bytes(
        (ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md").read_bytes()
    )
    assert freeze["parent_freeze_digest"] == frozen.V21_FREEZE_DIGEST
    assert freeze["scientific_identity_namespace"] == "essential-web-evidence-v2.0"


def test_v22_protocol_sha_verifies() -> None:
    raw = (
        ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.2-PROTOCOL.md"
    ).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == frozen.V22_PROTOCOL_SHA256
    assert frozen.V22_PROTOCOL_SHA256 == (
        "fd698793068458b31563418fdca29c97fa1efe1099b8776c7fd1407887f4f49d"
    )


def test_v22_parent_graph_verifies() -> None:
    freeze = _freeze22()
    assert set(freeze["parents"]) == {
        "M_child",
        "M_complete",
        "M_old",
        "T_child",
        "T_costmap",
        "T_old",
        "selection",
        "v21_freeze",
    }
    assert freeze["parents"]["M_child"]["role"] == "historical_blocked_child_keep_blocked"
    assert freeze["parents"]["T_child"]["role"] == "historical_dry_child_reviewed_defective"
    assert freeze["parents"]["selection"]["role"] == "byte_identical_scientific_selection"
    with pytest.raises(lineage.LineageError):
        lineage.complete_parent_descriptors(freeze, {})
    contents = {key: b"x" * 0 + b"y" for key in freeze["parents"]}
    with pytest.raises(lineage.LineageError):
        lineage.complete_parent_descriptors(freeze, contents)


def test_v22_namespace_and_selection_unchanged() -> None:
    assert frozen.V22_SCIENTIFIC_NAMESPACE == "essential-web-evidence-v2.0"
    assert frozen.V21_SELECTION_DIGEST == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert frozen.V21_SELECTION_SHA256 == (
        "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
    )
    assert frozen.V22_M_EVIDENCE_DIGEST == (
        "2ecf3eb9df01a41f7f9defa20633841211396f654350df80e460f5c2b5740052"
    )
    assert frozen.V22_COSTMAP_DIGEST == (
        "ed713a0a6fe22cdd396f758182b841b0208f0fe92795b3784f8d71bb6bbf665b"
    )


# --------------------------------------------------------------------------
# M request cap: cumulative 16/file, everything else unchanged.
# --------------------------------------------------------------------------


def test_v22_m_request_caps() -> None:
    ledger = budgets.new_arm_m_v22()
    assert ledger.ceilings["footer_requests_per_file"] == 16
    assert ledger.ceilings["footer_requests_total"] == 80
    assert ledger.ceilings["data_requests_per_plan"] == 100
    assert ledger.ceilings["data_requests_total"] == 800
    assert ledger.ceilings["requests_total"] == 880
    assert ledger.ceilings["max_redirect_hops"] == 3
    assert ledger.ceilings["max_retries"] == 2
    assert frozen.V22_M_FOOTER_REQUESTS_PER_FILE == 16
    assert frozen.ARM_M_LIMITS["footer_requests_per_file"] == 10


def test_v22_historical_12_accepted_marked_violation() -> None:
    ledger = budgets.new_arm_m_v22()
    ledger.adopt_history(kind="footer", requests=12, source_file="f.parquet")
    assert ledger.snapshot()["requests"] == 12
    # Prospective revalidation (<=4) still fits the cumulative 16.
    for _ in range(4):
        ledger.charge_file_request("f.parquet", kind="footer")
    assert ledger.snapshot()["requests"] == 16
    # The 17th cumulative footer request refuses.
    with pytest.raises(budgets.BudgetRefusal):
        ledger.charge_file_request("f.parquet", kind="footer")


def test_v22_m_other_caps_unchanged() -> None:
    assert frozen.ARM_M_LIMITS["data_bytes_per_file"] == 29360128
    assert frozen.ARM_M_LIMITS["response_body_bytes_total"] == 268435456
    assert frozen.ARM_M_LIMITS["memory_resident_bytes"] == 268435456


# --------------------------------------------------------------------------
# M historical bytes: no sound bound -> BLOCKED.
# --------------------------------------------------------------------------


def test_m_byte_audit_blocked() -> None:
    verdict = m_audit.audit_m_bytes_unknown()
    assert verdict["conclusion"] == "BLOCKED_BYTES_UNKNOWN"
    assert verdict["blocked"] is True
    evidence = verdict["evidence"]
    assert evidence["recorded_range_body_bytes_total"] == 2191448
    assert evidence["enforced_redirect_body_limit"] == 268435456
    assert evidence["four_mib_cap_applies_to_redirects"] is False


def test_m_byte_totals_match_frozen() -> None:
    assert frozen.V22_M_HISTORY_BYTES_TOTAL == 2191448
    assert sum(frozen.V22_M_HISTORY_BYTES.values()) == 2191448
    assert (
        frozen.V22_M_HISTORY_REQUESTS["data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"]
        == 12
    )
    assert sum(frozen.V22_M_HISTORY_REQUESTS.values()) == 55


# --------------------------------------------------------------------------
# M revalidation: exact bytes 0-3 GET with ETag binding.
# --------------------------------------------------------------------------


def test_m_revalidation_plan_shape() -> None:
    files = {
        "f.parquet": {"etag": '"abc"', "remote_length": 1000},
        "g.parquet": {"etag": '"def"', "remote_length": 2000},
    }
    plan = schedule.m_revalidation_plan(files)
    assert plan["nominal_physical_requests_arm"] == 4
    assert plan["max_physical_requests_arm"] == 8
    assert plan["files"]["f.parquet"]["nominal_physical_requests"] == 2
    assert plan["files"]["f.parquet"]["max_physical_requests"] == 4
    assert plan["files"]["f.parquet"]["expected_body"] == "PAR1"
    with pytest.raises(schedule.ScheduleError):
        schedule.m_revalidation_plan({"f.parquet": {"etag": "weak", "remote_length": 1}})
    historic = schedule.m_revalidation_plan(
        files, historical_requests={"f.parquet": 12, "g.parquet": 7}
    )
    assert historic["files"]["f.parquet"]["remaining_after_nominal"] == 16 - 12 - 2
    assert historic["files"]["g.parquet"]["remaining_v22"] == 16 - 7
    assert historic["arm_remaining_after_nominal"] == 80 - 19 - 4


def test_m_revalidation_response_checks() -> None:
    good = schedule.check_revalidation_response(
        expected_etag='"abc"',
        expected_length=1000,
        status=206,
        content_range=(0, 3, 1000),
        body=b"PAR1",
        etag='"abc"',
        final_host_allowed=True,
    )
    assert good == {"accepted": True, "reasons": []}
    for kwargs in [
        {"status": 200},
        {"content_range": (0, 3, 999)},
        {"body": b"XXXX"},
        {"etag": '"different"'},
        {"etag": 'W/"abc"'},
        {"etag": None},
        {"final_host_allowed": False},
    ]:
        params: dict[str, Any] = {
            "expected_etag": '"abc"',
            "expected_length": 1000,
            "status": 206,
            "content_range": (0, 3, 1000),
            "body": b"PAR1",
            "etag": '"abc"',
            "final_host_allowed": True,
        }
        params.update(kwargs)
        result = schedule.check_revalidation_response(**params)
        assert result["accepted"] is False and result["reasons"], kwargs


# --------------------------------------------------------------------------
# M data schedule: redirect-aware physical counts within caps.
# --------------------------------------------------------------------------


def _m_unit(name: str, estimate: int, compressed: int) -> dict[str, Any]:
    return {
        "file": name,
        "future_plan_feasibility": {"request_estimate": estimate},
        "window_report": {"projected_physical_leaves": [{"compressed_bytes": compressed}]},
    }


def test_m_data_schedule_fits_caps() -> None:
    units = [_m_unit(f"f{i}.parquet", 85, 582200) for i in range(8)]
    result = schedule.m_data_schedule(units)
    assert len(result["plans"]) == 8
    assert all(v["fits_plan"] for v in result["plans"].values())
    assert result["nominal_requests_arm"] == 8 * 85
    assert result["max_physical_requests_arm"] == 8 * 88
    assert result["fits_data_arm"] is True
    with pytest.raises(schedule.ScheduleError):
        schedule.m_data_schedule([{"file": "x", "future_plan_feasibility": {}}])


def test_m_data_overrun_refuses() -> None:
    units = [_m_unit("f.parquet", 98, 100)]
    result = schedule.m_data_schedule(units)
    assert result["plans"]["f.parquet"]["fits_plan"] is False
    assert result["plans"]["f.parquet"]["max_physical_requests"] == 101


# --------------------------------------------------------------------------
# T dictionary-prefix correction.
# --------------------------------------------------------------------------


def test_t_chunk_starts_at_dictionary() -> None:
    start, length = schedule.chunk_span(
        {"offset": 158729170, "dictionary_page_offset": 156637786, "compressed_bytes": 22512998},
        246305126,
    )
    assert (start, length) == (156637786, 22512998)
    assert start + length == 179150784
    ranges = schedule.split_span(start, length)
    assert len(ranges) == 6
    assert ranges[0][0] == 156637786
    assert ranges[-1][1] == 179150784


def test_t_omitted_prefix_regression() -> None:
    # Real recorded geometry as literals: the old data-offset schedule
    # omitted these dictionary prefixes and overran past the true ends.
    prefixes = {
        "2014-15": (158729170, 156637786),
        "2015-32": (262393506, 259990771),
        "2016-50": (147633836, 145763654),
        "2018-05": (192929439, 190807685),
        "2019-09": (199754111, 197584400),
        "2021-04": (132222075, 129999706),
        "2021-49": (35268448, 32748062),
        "2024-26": (33648881, 31508248),
    }
    total = 0
    for data, dictionary in prefixes.values():
        assert dictionary < data
        start, _ = schedule.chunk_span(
            {"offset": data, "dictionary_page_offset": dictionary, "compressed_bytes": 100},
            10**12,
        )
        assert start == dictionary
        total += data - dictionary
    assert total == 17539154


def test_t_47_ranges_reproduced() -> None:
    cases = [
        (156637786, 22512998, 6),
        (145763654, 20044734, 5),
    ]
    for dictionary, compressed, expected in cases:
        ranges = schedule.split_span(dictionary, compressed)
        assert len(ranges) == expected
        assert sum(end - start for start, end in ranges) == compressed


def test_t_physical_schedule_bounds() -> None:
    physical = schedule.t_physical_plan(
        files={
            "a.parquet": {"nominal_ranges": 6, "nominal_bytes": 22512998},
            "b.parquet": {"nominal_ranges": 5, "nominal_bytes": 20044734},
        },
        carried_requests=12,
        carried_bytes=737738,
        request_cap=800,
        data_cap=251658240,
        total_cap=268435456,
    )
    assert physical["nominal_data_ranges"] == 11
    assert physical["with_carry_requests"] == 11 + 4 + 2 + 12
    assert physical["fits_requests"] is True
    assert physical["fits_data"] is True
    assert physical["fits_total"] is True


# --------------------------------------------------------------------------
# Durable ledger repair: save/load preserves live charges.
# --------------------------------------------------------------------------


def test_durable_save_load_preserves_live_charges(tmp_path: Path) -> None:
    from xlm.data.evidence_v2 import carry as carry_mod

    durable = carry_mod.DurableLedger(budgets.new_arm_t_v21())
    durable.adopt(
        carry_mod.CarryEntry(
            entry_id="e1",
            source_receipt="r",
            source_path="p",
            file="f.parquet",
            category="footer",
            requests=2,
            bytes=100,
            measurement="measured",
        )
    )
    durable.ledger.charge_file_request("f.parquet", kind="data")
    durable.ledger.charge_transfer("f.parquet", 23, kind="data")
    durable.ledger.charge_time(7.0, kind="stage")
    before = durable.effective()
    assert (before["requests"], before["response_body_bytes"], before["elapsed_seconds"]) == (
        3,
        123,
        7.0,
    )
    durable.save(tmp_path / "carry.json")
    reloaded = carry_mod.DurableLedger.load(tmp_path / "carry.json", budgets.new_arm_t_v21())
    after = reloaded.effective()
    assert after["requests"] == 3
    assert after["response_body_bytes"] == 123
    assert after["elapsed_seconds"] == 7.0
    assert after["kind_requests"] == {"footer": 2, "data": 1}
    assert after["transfer_per_file"]["f.parquet"] == {"footer": 100, "data": 23}


def test_durable_conflict_refuses_and_crash_reserves(tmp_path: Path) -> None:
    from xlm.data.evidence_v2 import carry as carry_mod

    durable = carry_mod.DurableLedger(budgets.new_arm_t_v21())
    entry = carry_mod.CarryEntry(
        entry_id="e1",
        source_receipt="r",
        source_path="p",
        file=None,
        category="footer",
        requests=1,
        bytes=10,
        measurement="measured",
    )
    assert durable.adopt(entry) is True
    assert durable.adopt(entry) is False
    with pytest.raises(carry_mod.CarryError, match="conflicting"):
        durable.adopt(
            carry_mod.CarryEntry(
                entry_id="e1",
                source_receipt="r",
                source_path="p",
                file=None,
                category="footer",
                requests=2,
                bytes=10,
                measurement="measured",
            )
        )
    token = durable.begin_range("f.parquet", 0, 1000)
    durable.save(tmp_path / "carry.json")
    reloaded = carry_mod.DurableLedger.load(tmp_path / "carry.json", budgets.new_arm_t_v21())
    # Crash reservation: the attempted 1000 bytes are charged, never refunded.
    assert reloaded.effective()["response_body_bytes"] == 10 + 1000
    assert token in reloaded.in_flight
    reloaded.complete_range(token)
    assert token not in reloaded.in_flight
    with pytest.raises(carry_mod.CarryError):
        reloaded.complete_range("nope")
    with pytest.raises(carry_mod.CarryError):
        reloaded.adopt(
            carry_mod.CarryEntry(
                entry_id="",
                source_receipt="",
                source_path="",
                file=None,
                category="bogus",
                requests=-1,
                bytes=0,
                measurement="x",
            )
        )


# --------------------------------------------------------------------------
# Memory supervisor, disk inventory, deadlines.
# --------------------------------------------------------------------------


def test_supervisor_measures_tree_and_refuses() -> None:
    reader_calls = {"n": 0}

    def reader() -> list[tuple[int, int]]:
        reader_calls["n"] += 1
        return [(100, 100000000), (101, 50000000)]

    supervisor = reserves.ProcessTreeSupervisor(cap_bytes=268435456, reader=reader)
    assert supervisor.check(context="t")["total_rss"] == 150000000

    class Child:
        pid = 101

    supervisor.register_child(Child())
    assert supervisor.check(context="t")["total_rss"] == 150000000

    def big_reader() -> list[tuple[int, int]]:
        return [(100, 300000000)]

    supervisor2 = reserves.ProcessTreeSupervisor(cap_bytes=268435456, reader=big_reader)
    with pytest.raises(reserves.ReservationError, match="exceeds"):
        supervisor2.check(context="t")
    supervisor3 = reserves.ProcessTreeSupervisor(cap_bytes=1, reader=lambda: None)
    with pytest.raises(reserves.ReservationError, match="unobservable"):
        supervisor3.check(context="t")
    with pytest.raises(reserves.ReservationError):
        supervisor.register_child(object())


def test_supervisor_psutil_reader_shape() -> None:
    reading = reserves.psutil_tree_reader()
    assert reading is None or (
        isinstance(reading, list)
        and all(isinstance(pid, int) and isinstance(rss, int) for pid, rss in reading)
        and sum(rss for _, rss in reading) > 0
    )


def test_disk_intermediate_final_refused() -> None:
    inventory = reserves.DiskInventory(scratch_cap=1000, final_cap=16, combined_cap=2000)
    with pytest.raises(reserves.ReservationError, match="final would exceed"):
        inventory.stage("big", scratch=10, final=17)
    inventory.stage("ok", scratch=10, final=10)
    with pytest.raises(reserves.ReservationError, match="already staged"):
        inventory.stage("ok", scratch=1, final=1)
    with pytest.raises(reserves.ReservationError, match="unstaged"):
        inventory.release("ghost")
    inventory.stage("tmp", scratch=5, final=5)
    snapshot = inventory.snapshot()
    assert snapshot["peak_combined"] == 10 + 10 + 5 + 5
    assert snapshot["fits"]["combined"] is True
    inventory.release("tmp")
    assert "tmp" not in inventory.snapshot()["objects"]


def test_disk_atomic_replace_counts_old_and_new() -> None:
    inventory = reserves.DiskInventory(scratch_cap=100, final_cap=100, combined_cap=150)
    inventory.stage("doc", scratch=0, final=40)
    inventory.atomic_replace("doc", scratch_tmp=0, final_new=40)
    assert inventory.snapshot()["objects"]["doc"] == {"scratch": 0, "final": 40}
    with pytest.raises(reserves.ReservationError):
        inventory.atomic_replace("doc", scratch_tmp=0, final_new=120)


def test_staged_publication_gate() -> None:
    gate = reserves.staged_publication_gate(
        acquisition_final_bytes=1000, label_budget_upper_bytes=500
    )
    assert gate["verdict"] == "COMPLETE_FITS"
    gate2 = reserves.staged_publication_gate(
        acquisition_final_bytes=1000, label_budget_upper_bytes=10**9
    )
    assert gate2["verdict"] == "INCOMPLETE_FALLBACK"
    assert gate2["acquisition_allowed"] is True
    gate3 = reserves.staged_publication_gate(
        acquisition_final_bytes=10**9, label_budget_upper_bytes=0
    )
    assert gate3["verdict"] == "REFUSE_ACQUISITION"


def test_deadline_persisted_restart_unknown_blocks(tmp_path: Path) -> None:
    tracker = reserves.DeadlineTracker(history_unknown=True)
    assert tracker.readiness()["ready"] is False
    tracker.charge(10.0, file_elapsed=5.0)
    assert tracker.state()["elapsed_seconds"] == 10.0
    restored = reserves.DeadlineTracker.load(tracker.state())
    assert restored.state()["elapsed_seconds"] == 10.0
    assert restored.readiness()["ready"] is False
    clean = reserves.DeadlineTracker(history_unknown=False)
    assert clean.readiness()["ready"] is True
    with pytest.raises(reserves.ReservationError):
        clean.charge(1801.0, file_elapsed=0.0)
    with pytest.raises(reserves.ReservationError):
        clean.charge(1.0, file_elapsed=601.0)
    with pytest.raises(reserves.ReservationError):
        reserves.DeadlineTracker.load({"elapsed_seconds": -1})


# --------------------------------------------------------------------------
# Child artifacts: parents, readiness, no executable.
# --------------------------------------------------------------------------


def test_child_parents_require_resolution() -> None:
    freeze = json.loads(
        (ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json").read_bytes()
    )
    with pytest.raises(lineage.LineageError):
        lineage.complete_parent_descriptors(freeze, {})
    assert freeze["parents"]["selection"]["role"] == "byte_identical_scientific_selection"
    assert freeze["parents"]["M_complete"]["original_status"] == "COMPLETE"
    assert freeze["parents"]["T_costmap"]["original_status"] == "INCOMPLETE"


def test_readiness_semantics() -> None:
    from xlm.data.evidence_v2 import child_plans

    items = {
        "scientific_identity_ok": True,
        "lineage_ok": True,
        "historical_accounting_ok": True,
        "range_schedule_ok": True,
        "requests_ok": True,
        "response_bytes_ok": True,
        "decompression_ok": True,
        "scan_ok": True,
        "memory_supervision_ok": True,
        "disk_schedule_ok": True,
        "deadlines_ok": True,
    }
    review = child_plans.readiness_review("T", items)
    assert review["status"] == "READY_FOR_ACQUISITION_AUTHORIZATION_REVIEW"
    assert review["executable"] is False
    assert review["authorization"] == "NONE"
    blocked = child_plans.readiness_review("T", {**items, "deadlines_ok": False}, reasons=["x"])
    assert blocked["status"] == "BLOCKED"
    assert blocked["failed"] == ["deadlines_ok"]
