"""Essential-Web evidence v3.0: frozen identity, schedules and Phase-P plans.

Offline only (sockets blocked). Real frozen receipts are read as
metadata/hash bindings, never as corpus text. The frozen execution root is
never created.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from evidence_v3_support import REPO, block_network
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import executor, frozen_v3, journal, netpolicy, plan, schedules

V3_EVIDENCE = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _freeze() -> dict[str, Any]:
    value: dict[str, Any] = canonical.loads_bytes_strict((V3_EVIDENCE / "freeze.json").read_bytes())
    return value


# --------------------------------------------------------------------------
# Frozen contract and lineage (unchanged by this remediation).
# --------------------------------------------------------------------------


def test_v3_freeze_and_protocol_verify() -> None:
    freeze = _freeze()
    assert freeze["digest"] == frozen_v3.FREEZE_DIGEST
    assert freeze["digest"] == "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
    assert canonical.self_digest(freeze) == freeze["digest"]
    raw = (
        REPO / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
    ).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == frozen_v3.PROTOCOL_SHA256
    assert frozen_v3.PROTOCOL_SHA256 == (
        "c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79"
    )


def test_cap_map_digest_reproduces_from_freeze() -> None:
    freeze = _freeze()
    assert freeze["arm_caps"]["M"] == frozen_v3.ARM_M_CAPS
    assert freeze["arm_caps"]["T"] == frozen_v3.ARM_T_CAPS
    digest = canonical.digest({"M": freeze["arm_caps"]["M"], "T": freeze["arm_caps"]["T"]})
    assert digest == frozen_v3.RESOURCE_CAPS_DIGEST
    assert digest == "e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b"
    assert freeze["numerical_cap_changes"] == []


def test_v2_parent_graph_preserved_and_closed() -> None:
    freeze = _freeze()
    assert len(freeze["historical_closed_lineage"]) == 39
    assert freeze["parent_v22_freeze_digest"] == frozen_v3.V22_FREEZE_DIGEST
    closure = canonical.loads_bytes_strict((V3_EVIDENCE / "lineage_closure.json").read_bytes())
    assert closure["status"] == "CLOSED_NON_EXECUTABLE"
    assert closure["historical_accounting"] == "HISTORICALLY_UNCERTIFIABLE"
    assert freeze["lineage_closure_digest"] == closure["digest"]
    terminal = canonical.loads_bytes_strict(
        (
            REPO
            / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/readiness_review.json"
        ).read_bytes()
    )
    assert terminal["digest"] == frozen_v3.TERMINAL_V22_READINESS_DIGEST
    assert all(v["status"] == "BLOCKED" and not v["executable"] for v in terminal["arms"].values())


def test_adopted_observations_carry_no_budget_history() -> None:
    adopted = _freeze()["adopted_planning_observations"]
    assert all(v["v3_role"] == "adopted_planning_observation" for v in adopted.values())
    assert all(v["v3_budget_history"] is False for v in adopted.values())


def test_scientific_identity_unchanged() -> None:
    freeze = _freeze()
    sci = freeze["scientific_identity"]
    assert sci["selection_digest"] == frozen_v3.SELECTION_DIGEST
    assert sci["selection_sha256"] == frozen_v3.SELECTION_SHA256
    assert sci["scientific_namespace"] == "essential-web-evidence-v2.0"
    assert sci["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert sci["policy_digest"] == frozen_v3.POLICY_DIGEST
    assert list(sci["projection"]) == ["eai_taxonomy", "quality_signals"]
    assert sci["metadata_and_text_seed"] == 20260927
    windows = [f["window"] for f in freeze["M_prospective_files"]]
    assert len(windows) == 8 and all(w[1] - w[0] == 512 for w in windows)
    assert [w["window"] for w in sci["M_windows"]] == windows


def test_epoch_not_started_and_frozen_root_absent() -> None:
    freeze = _freeze()
    assert freeze["epoch_id"] == frozen_v3.EPOCH_ID
    assert freeze["epoch_state"] == "NOT_STARTED"
    assert freeze["authorization"] == "NONE"
    assert not Path(frozen_v3.EXECUTION_ROOT).exists()


def test_canonical_url_matches_frozen_first_network_boundary() -> None:
    first = _freeze()["first_network_boundary"]
    url = netpolicy.FROZEN_SOURCE.canonical_url(first["file"])
    assert url == first["canonical_url"]
    assert first["range_header"] == "bytes=0-3"


# --------------------------------------------------------------------------
# Dry arithmetic (recomputed, never trusted).
# --------------------------------------------------------------------------


def test_m_dry_schedules_exact() -> None:
    freeze = _freeze()
    p = schedules.build_m_phase_p(freeze["M_prospective_files"])
    assert (
        p["P_logical_arm"],
        p["P_nominal_physical_arm"],
        p["P_cold_chain_max_no_retry_arm"],
    ) == (
        16,
        24,
        40,
    )
    d = schedules.build_m_phase_d(freeze["M_prospective_files"])
    assert d["data_chunks_total"] == 648
    assert d["nominal_physical_total"] == 688
    assert d["cold_chain_max_no_retry_total"] == 720
    assert d["data_payload_bytes_arm"] == 11692530


def test_t_dry_schedules_exact() -> None:
    freeze = _freeze()
    p = schedules.build_t_phase_p(freeze["T_prospective_files"])
    assert (
        p["P_logical_arm"],
        p["P_nominal_physical_arm"],
        p["P_cold_chain_max_no_retry_arm"],
    ) == (
        24,
        32,
        48,
    )
    d = schedules.build_t_phase_d(freeze["T_prospective_files"])
    assert d["data_range_count_arm"] == 47
    assert d["data_payload_bytes_arm"] == 179963169
    assert d["data_transfer_upper_arm"] == 213517601
    assert (d["nominal_physical_total"], d["cold_chain_max_no_retry_total"]) == (95, 127)


def test_m_phase_p_accounting_exact() -> None:
    acc = schedules.m_phase_p_accounting(_freeze()["M_prospective_files"])
    assert acc["P_payload_bytes"] == 1398416
    assert acc["footer_remaining_before_redirect_error_retry"] == 32156016
    assert acc["minimum_per_file_footer_headroom"] == 3991024
    assert acc["P_cold_no_retry"] + acc["D_identity_allocation"] == 72 <= 80
    assert acc["P_arm_capacity_preserving_D"] == 48
    assert acc["P_spare_preserving_D"] == 8


# --------------------------------------------------------------------------
# Executable Phase-P plans with the retained future-D reservation.
# --------------------------------------------------------------------------


def test_m_executable_plan_payload_exact() -> None:
    freeze = _freeze()
    payload = schedules.m_phase_p_plan_payload(freeze["M_prospective_files"])
    assert payload["synthetic"] is False
    assert [f["file"] for f in payload["files"]] == [
        f["file"] for f in freeze["M_prospective_files"]
    ]
    first = payload["files"][0]
    assert first["file"] == "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
    assert first["operations"][0] == {"op": "IDENTITY_HEAD_0_3", "range": [0, 3]}
    assert first["operations"][1]["range"] == [290481302, 290684577]
    assert first["operations"][1]["expected_footer_length"] == 290684578 - 8 - 290481302
    ceilings = payload["ceilings"]
    assert ceilings["arm"]["requests"] == 48
    assert all(cell["requests"] == 12 for cell in ceilings["per_file"])
    assert ceilings["arm"]["footer_bytes"] == 33554432 - 8 * 4 * 65537
    assert payload["arithmetic"]["P_payload_bytes_arm"] == 1398416
    assert payload["arithmetic"]["P_spare_preserving_D"] == 8


def test_t_executable_plan_payload_exact() -> None:
    freeze = _freeze()
    payload = schedules.t_phase_p_plan_payload(freeze["T_prospective_files"])
    assert len(payload["files"]) == 8
    ops = [o["op"] for f in payload["files"] for o in f["operations"]]
    assert ops == ["IDENTITY_HEAD_0_3", "T_TRAILER", "T_FOOTER_FROM_TRAILER"] * 8
    assert all(f["operations"][2]["range"] is None for f in payload["files"])
    reserves = [f["d_reserve"] for f in payload["files"]]
    assert sum(r["requests"] for r in reserves) == 32 + 47
    assert payload["ceilings"]["arm"]["requests"] == 800 - 79
    for f, cell in zip(payload["files"], payload["ceilings"]["per_file"], strict=True):
        assert cell["requests"] == 100 - (4 + f["bindings"]["data_range_count"])
        assert cell["footer_bytes"] == 2097152 - 4 * 65537
    assert payload["ceilings"]["arm"]["transfer_bytes"] == 268435456 - 213517601 - 8 * 4 * 65537


def test_committed_child_plans_equal_freeze_recomputation() -> None:
    children = plan.load_committed_children(REPO)
    assert set(children.plans) == {"M", "T"}
    m = children.plans["M"]
    assert len(m.operations) == 16 and m.arm_ceiling["requests"] == 48
    t = children.plans["T"]
    assert len(t.operations) == 24
    assert t.operations[2].range is None


def test_plan_rejects_synthetic_real_swap_and_tamper() -> None:
    raw = (REPO / plan.CHILD_DIR / plan.PLAN_FILES["M"]).read_bytes()
    with pytest.raises(plan.PlanError):
        plan.validate_plan_bytes(raw, arm="M", synthetic=True)
    with pytest.raises(plan.PlanError):
        plan.validate_plan_bytes(raw, arm="T", synthetic=False)
    body = canonical.loads_bytes_strict(raw)
    body["payload"]["files"][0]["operations"][0]["range"] = [16, 19]
    body["digest"] = canonical.self_digest(body)
    with pytest.raises(plan.PlanError, match="identity range"):
        plan.validate_plan_bytes(canonical.canonical_bytes(body), arm="M", synthetic=False)


def test_phase_d_is_not_executable() -> None:
    assert not any("phase_d" in name.lower() for name in dir(executor))
    assert "D_RUNNING" not in {"P_RUNNING", "P_COMPLETE_SEALED", "P_INCOMPLETE"}
    state = journal.new_state(
        {"epoch_start_digest": "0" * 64, "initial_inventory": {}, "plans": {}, "disk_caps": {}},
        frozen_v3.EPOCH_ID,
    )
    state.seq = 0
    state.session = "s"
    with pytest.raises(journal.StateError):
        state.apply({"seq": 1, "type": "PHASE", "body": {"arm": "M", "to": "D_RUNNING"}})
