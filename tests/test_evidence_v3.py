"""Essential-Web evidence v3.0 offline implementation tests (synthetic only).

No network (sockets blocked), no acquisition, no corpus-text inspection,
no selector/sample change, no tokenizer/training/admission, no push.
All fixtures are authored synthetic; real frozen receipts are read only
as metadata/hash bindings, never as text.
"""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import epoch, frozen_v3, guards, ledger, schedules

ROOT = Path(__file__).resolve().parents[1]
V3_EVIDENCE = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _freeze() -> dict[str, Any]:
    return canonical.loads_bytes_strict((V3_EVIDENCE / "freeze.json").read_bytes())


# --------------------------------------------------------------------------
# V3 lineage.
# --------------------------------------------------------------------------


def test_v3_freeze_verifies() -> None:
    freeze = _freeze()
    assert freeze["digest"] == frozen_v3.FREEZE_DIGEST
    assert freeze["digest"] == "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
    assert (
        canonical.self_digest({k: v for k, v in freeze.items() if k != "digest"})
        == freeze["digest"]
    )
    raw = (
        ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-PROTOCOL.md"
    ).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == frozen_v3.PROTOCOL_SHA256
    assert freeze["protocol_sha256"] == frozen_v3.PROTOCOL_SHA256


def test_v2_parent_graph_preserved() -> None:
    freeze = _freeze()
    assert len(freeze["historical_closed_lineage"]) == 39
    assert freeze["parent_v22_freeze_digest"] == frozen_v3.V22_FREEZE_DIGEST
    assert freeze["terminal_v22_readiness_digest"] == frozen_v3.TERMINAL_V22_READINESS_DIGEST
    # Full v2.0/v2.1/v2.2 freezes still present via relative keys.
    for version in ("V2", "V2.1", "V2.2"):
        path = ROOT / f"docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-{version}/freeze.json"
        assert path.is_file()
    # Terminal readiness still BLOCKED / non-executable.
    terminal = canonical.loads_bytes_strict(
        (
            ROOT
            / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/readiness_review.json"
        ).read_bytes()
    )
    assert terminal["digest"] == frozen_v3.TERMINAL_V22_READINESS_DIGEST
    assert all(
        v["status"] == "BLOCKED" and v["executable"] is False for v in terminal["arms"].values()
    )


def test_v2_status_permanently_closed() -> None:
    freeze = _freeze()
    closure = canonical.loads_bytes_strict((V3_EVIDENCE / "lineage_closure.json").read_bytes())
    assert closure["status"] == "CLOSED_NON_EXECUTABLE"
    assert closure["historical_accounting"] == "HISTORICALLY_UNCERTIFIABLE"
    assert freeze["lineage_closure_digest"] == closure["digest"]
    assert frozenset(closure["versions"]) == {
        "essential-web-evidence-v2.0",
        "essential-web-evidence-v2.1",
        "essential-web-evidence-v2.2",
    }


def test_old_usage_not_imported_into_v3_ledger() -> None:
    arm = ledger.new_m_ledger()
    assert arm.snapshot()["counters"]["requests"] == 0
    assert arm.snapshot()["counters"]["response_body_bytes"] == 0
    with pytest.raises(ledger.LedgerError):
        arm.import_v2_counters({"requests": 55})
    assert arm.snapshot()["counters"]["requests"] == 0


def test_old_history_present_in_provenance() -> None:
    freeze = _freeze()
    assert "external/footer_evidence.json" in freeze["historical_closed_lineage"]
    assert "external/text_selection_manifest.json" in freeze["historical_closed_lineage"]
    adopted = freeze["adopted_planning_observations"]
    assert set(adopted) == {
        "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/range_schedule_t.json",
        "external/footer_evidence.json",
        "external/text_cost_evidence_attempt2.incomplete.json",
    }
    assert all(v["v3_role"] == "adopted_planning_observation" for v in adopted.values())
    assert all(v["v3_budget_history"] is False for v in adopted.values())


def test_no_scientific_membership_drift() -> None:
    freeze = _freeze()
    sci = freeze["scientific_identity"]
    assert (
        sci["selection_digest"]
        == "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert (
        sci["selection_sha256"]
        == "8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27"
    )
    assert sci["scientific_namespace"] == "essential-web-evidence-v2.0"
    assert sci["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert (
        sci["policy_digest"] == "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    )
    assert list(sci["projection"]) == ["eai_taxonomy", "quality_signals"]
    assert len(freeze["M_prospective_files"]) == 8
    assert all(w[1] - w[0] == 512 for w in (f["window"] for f in freeze["M_prospective_files"]))


# --------------------------------------------------------------------------
# Epoch genesis.
# --------------------------------------------------------------------------


def test_epoch_id_exact_and_not_started() -> None:
    assert frozen_v3.EPOCH_ID == "essential-web-evidence-v3.0:essential-web:epoch-0001"
    assert frozen_v3.EXECUTION_ROOT == "G:/Project/xlm-evidence-v3/essential-web"
    freeze = _freeze()
    assert freeze["epoch_id"] == frozen_v3.EPOCH_ID
    assert freeze["epoch_state"] == "NOT_STARTED"
    assert freeze["authorization"] == "NONE"
    definition = canonical.loads_bytes_strict((V3_EVIDENCE / "epoch_definition.json").read_bytes())
    assert definition["execution_epoch_id"] == frozen_v3.EPOCH_ID
    assert definition["state"] == "NOT_STARTED"


def test_no_network_before_genesis_flag() -> None:
    # The gate is structural: without epoch_start.json no request path opens.
    # Here we assert the frozen root has no live ledger/genesis artifact.
    frozen_root = Path(frozen_v3.EXECUTION_ROOT)
    assert not (frozen_root / "epoch_start.json").exists()


def test_authorization_required_no_boolean_bypass(tmp_path: Path) -> None:
    gate = epoch.PhaseGate("M")
    with pytest.raises(epoch.EpochError):
        gate.authorize_p(artifact=None, expected_digest="0" * 64)  # type: ignore[arg-type]
    with pytest.raises(epoch.EpochError):
        epoch.verify_authorization(artifact={"phase": "P"}, expected_digest="not-hex", phase="P")
    # A truthy non-mapping or empty mapping is not authorization.
    for bad in ([], True, "yes", {"phase": "P"}):
        if isinstance(bad, dict):
            artifact: Any = bad
            digest = epoch.authorization_digest(artifact)
            # Wrong expected digest still refuses.
            with pytest.raises(epoch.EpochError):
                epoch.verify_authorization(artifact=artifact, expected_digest="f" * 64, phase="P")
            _ = digest
        else:
            with pytest.raises(epoch.EpochError):
                epoch.verify_authorization(artifact=bad, expected_digest="0" * 64, phase="P")  # type: ignore[arg-type]
    _ = tmp_path


def test_genesis_exactly_once_and_second_refuses(tmp_path: Path) -> None:
    synthetic = tmp_path / "epoch"
    pristine = epoch.check_root_pristine(synthetic)
    artifact = {"phase": "P", "ticket": "synthetic-ticket-1", "nonce": 1}
    body = epoch.build_epoch_start(
        protocol_digest=frozen_v3.PROTOCOL_SHA256,
        freeze_digest=frozen_v3.FREEZE_DIGEST,
        implementation_commit="SYNTHETIC",
        code_hashes={"a.py": "0" * 64},
        environment={"synthetic": True},
        m_child_plan_digest="0" * 64,
        t_child_plan_digest="0" * 64,
        authorization_artifact=artifact,
        authorization_digest_expected=epoch.authorization_digest(artifact),
        execution_root=synthetic,
        source_revision=frozen_v3.SOURCE_REVISION,
        resource_caps={},
        owner="synthetic-test",
        clock_monotonic_ns=123,
        initial_inventory=pristine,
        allow_nonfrozen_root=True,
    )
    binding = epoch.publish_epoch_start(synthetic, body)
    assert binding["bytes"] > 0
    with pytest.raises(epoch.EpochError, match="second genesis"):
        epoch.publish_epoch_start(synthetic, body)


def test_root_must_be_pristine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "old-cache.bin").write_bytes(b"stale")
    with pytest.raises(epoch.EpochError, match="not pristine"):
        epoch.check_root_pristine(dirty)
    # Link/reparse escape refuses even without OS symlink privilege: simulate
    # an is_symlink root via monkeypatch.
    fake_root = tmp_path / "fakelink"
    fake_root.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda self: True if self == fake_root else False)  # type: ignore[method-assign]
    try:
        with pytest.raises(epoch.EpochError, match="link"):
            epoch.check_root_pristine(fake_root)
    finally:
        monkeypatch.undo()
    # A real symlink, where privileges permit, must also refuse.
    link = tmp_path / "linkroot"
    target = tmp_path / "real"
    target.mkdir(exist_ok=True)
    try:
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
        with pytest.raises(epoch.EpochError, match="link"):
            epoch.check_root_pristine(link)
    except OSError:
        # No privilege: simulated case above already proves the refusal path.
        pass
    absent = tmp_path / "absent"
    assert epoch.check_root_pristine(absent)["preexisting"] is False


def test_initial_inventory_bound_and_atomic(tmp_path: Path) -> None:
    synthetic = tmp_path / "inv"
    pristine = epoch.check_root_pristine(synthetic)
    artifact = {"phase": "P", "ticket": "t"}
    body = epoch.build_epoch_start(
        protocol_digest=frozen_v3.PROTOCOL_SHA256,
        freeze_digest=frozen_v3.FREEZE_DIGEST,
        implementation_commit="SYNTHETIC",
        code_hashes={},
        environment={},
        m_child_plan_digest="0" * 64,
        t_child_plan_digest="0" * 64,
        authorization_artifact=artifact,
        authorization_digest_expected=epoch.authorization_digest(artifact),
        execution_root=synthetic,
        source_revision=frozen_v3.SOURCE_REVISION,
        resource_caps={},
        owner="owner",
        clock_monotonic_ns=1,
        initial_inventory=pristine,
        allow_nonfrozen_root=True,
    )
    epoch.publish_epoch_start(synthetic, body)
    assert not (synthetic / "epoch_start.json.tmp").exists()
    inventory = epoch.inventory_root(synthetic)
    assert inventory["files"]["epoch_start.json"]["bytes"] > 0
    assert inventory["total_bytes"] == inventory["files"]["epoch_start.json"]["bytes"]


# --------------------------------------------------------------------------
# Ledger.
# --------------------------------------------------------------------------


def test_v3_counters_zero_before_network() -> None:
    for arm in (ledger.new_m_ledger(), ledger.new_t_ledger()):
        snap = arm.snapshot()
        assert snap["counters"]["requests"] == 0
        assert snap["counters"]["response_body_bytes"] == 0
        assert snap["event_count"] == 0
        assert snap["epoch"] == frozen_v3.EPOCH_ID


def test_first_v3_event_charged() -> None:
    arm = ledger.new_m_ledger()
    arm.apply(
        ledger.V3Event(
            event_id="e1",
            kind="request",
            file="f.parquet",
            category="footer",
            requests=1,
            body_bytes=4,
        )
    )
    snap = arm.snapshot()
    assert snap["counters"]["requests"] == 1
    assert snap["counters"]["response_body_bytes"] == 4


def test_ledger_restart_durability(tmp_path: Path) -> None:
    arm = ledger.new_t_ledger()
    arm.apply(
        ledger.V3Event(
            event_id="e1",
            kind="request",
            file="f.parquet",
            category="footer",
            requests=2,
            body_bytes=100,
        )
    )
    path = tmp_path / "v3ledger.json"
    arm.save(path)
    reloaded = ledger.V3Ledger.load(path, "T")
    assert reloaded.snapshot()["counters"]["requests"] == 2
    assert reloaded.snapshot()["counters"]["response_body_bytes"] == 100


def test_duplicate_idempotence_and_conflict_refusal() -> None:
    arm = ledger.new_m_ledger()
    event = ledger.V3Event(
        event_id="e1",
        kind="request",
        file="f.parquet",
        category="footer",
        requests=1,
        body_bytes=4,
    )
    assert arm.apply(event) is True
    assert arm.apply(event) is False
    assert arm.snapshot()["counters"]["requests"] == 1
    with pytest.raises(ledger.LedgerError, match="conflicting"):
        arm.apply(
            ledger.V3Event(
                event_id="e1",
                kind="request",
                file="f.parquet",
                category="footer",
                requests=2,
                body_bytes=4,
            )
        )


def test_crash_reservation_never_refunded(tmp_path: Path) -> None:
    arm = ledger.new_m_ledger()
    path = tmp_path / "crash.json"
    # Simulate a crash with an open in-flight reservation by writing the
    # ledger file manually with an in_flight entry.
    arm.apply(
        ledger.V3Event(
            event_id="settled",
            kind="request",
            file="f.parquet",
            category="footer",
            requests=1,
            body_bytes=10,
        )
    )
    body = {
        "kind": "essential_web_evidence_v3_ledger",
        "protocol_version": frozen_v3.PROTOCOL_VERSION,
        "epoch_id": frozen_v3.EPOCH_ID,
        "arm": "M",
        "events": [
            {
                "event_id": "settled",
                "kind": "request",
                "file": "f.parquet",
                "category": "footer",
                "requests": 1,
                "body_bytes": 10,
                "decompressed_bytes": 0,
                "scanned_rows": 0,
                "elapsed_seconds": 0.0,
                "scratch_bytes": 0,
                "final_bytes": 0,
            }
        ],
        "live": arm.ledger.snapshot(),
        "in_flight": [
            {
                "token": "v3-crashed",
                "event_id": "crashed",
                "kind": "request",
                "file": "f.parquet",
                "category": "footer",
                "requests": 0,
                "body_bytes": 500,
                "decompressed_bytes": 0,
                "scanned_rows": 0,
                "elapsed_seconds": 0.0,
                "scratch_bytes": 0,
                "final_bytes": 0,
            }
        ],
        "duplicates": [],
    }
    canonical.write_canonical_json(path, body)
    reloaded = ledger.V3Ledger.load(path, "M")
    assert reloaded.snapshot()["counters"]["response_body_bytes"] == 10 + 500
    assert "v3-crashed" in reloaded.in_flight


# --------------------------------------------------------------------------
# M phase P / D.
# --------------------------------------------------------------------------


def test_m_phase_p_exact_files_and_budget() -> None:
    freeze = _freeze()
    plan = schedules.build_m_phase_p(freeze["M_prospective_files"])
    assert len(plan["files"]) == 8
    assert plan["P_logical_arm"] == 16
    assert plan["P_nominal_physical_arm"] == 24
    assert plan["P_cold_chain_max_no_retry_arm"] == 40
    for _name, cell in plan["files"].items():
        assert cell["identity_check"]["range"] == [0, 3]
        assert cell["identity_check"]["expected_status"] == 206
        assert cell["footer_range"]["end"] + 1 == cell["remote_length"]
    assert plan["P_cold_chain_max_no_retry_arm"] <= 80 - 32  # P reserves, D keeps 32
    # Full footer/control cold chain (P 40 + D identity 32) fits 80.
    assert 40 + 32 == 72 <= 80


def test_m_phase_d_counts_and_caps() -> None:
    freeze = _freeze()
    plan = schedules.build_m_phase_d(freeze["M_prospective_files"])
    assert plan["data_chunks_total"] == 648
    assert plan["nominal_physical_total"] == 688
    assert plan["cold_chain_max_no_retry_total"] == 720
    assert plan["data_payload_bytes_arm"] == 11692530
    assert plan["requires_sealed_P"] is True
    assert plan["requires_separate_D_authorization"] is True


def test_m_prospective_request_byte_totals() -> None:
    freeze = _freeze()
    totals = freeze["prospective_totals"]
    assert totals["M_total_nominal"] == 688
    assert totals["M_total_cold_max_no_retry"] == 720
    assert totals["M_footer_payload_both_phases"] == 1398448
    assert totals["M_data_payload"] == 11692530
    assert totals["M_nominal_payload_total_excludes_redirect_error"] == 13090978
    assert totals["M_data_byte_headroom"] == 234881024 - 11692530
    assert totals["M_footer_payload_headroom"] == 33554432 - 1398448


# --------------------------------------------------------------------------
# T phase P / D.
# --------------------------------------------------------------------------


def test_t_phase_p_pattern_no_text() -> None:
    freeze = _freeze()
    plan = schedules.build_t_phase_p(freeze["T_prospective_files"])
    assert plan["P_logical_arm"] == 24
    assert plan["P_nominal_physical_arm"] == 32
    assert plan["P_cold_chain_max_no_retry_arm"] == 48
    assert plan["no_text_read"] is True
    for cell in plan["files"].values():
        assert cell["phase_P_pattern"] == [
            "0-3",
            "N-8..N-1",
            "N-8-L..N-9 after trailer establishes L",
        ]
        assert cell["no_text_read"] is True
        assert cell["footer_reservation_bytes"] == 2097152


def test_t_phase_d_ranges_bytes_counts() -> None:
    freeze = _freeze()
    plan = schedules.build_t_phase_d(freeze["T_prospective_files"])
    assert plan["data_range_count_arm"] == 47
    assert plan["data_payload_bytes_arm"] == 179963169
    assert plan["data_transfer_upper_arm"] == 213517601
    assert plan["nominal_physical_total"] == 95
    assert plan["cold_chain_max_no_retry_total"] == 127
    assert plan["no_page_decoder_redesign"] is True
    # No old bad schedule: every range is dictionary-inclusive and bounded.
    for cell in plan["files"].values():
        assert cell["dictionary_inclusive"] is True
        for r in cell["data_ranges_half_open"]:
            assert 0 < r["bytes"] <= 4194304


def test_t_prospective_request_byte_totals() -> None:
    freeze = _freeze()
    totals = freeze["prospective_totals"]
    assert totals["T_total_nominal"] == 95
    assert totals["T_total_cold_max_no_retry"] == 127
    assert totals["T_data_transfer_upper"] == 213517601
    assert totals["T_footer_bytes_reserved"] == 16777216
    assert totals["T_upper_with_full_footer_reserve"] == 230294817


# --------------------------------------------------------------------------
# Phase authorization.
# --------------------------------------------------------------------------


def _ticket(phase: str, sealed: str | None = None, nonce: int = 0) -> dict[str, Any]:
    body: dict[str, Any] = {"phase": phase, "ticket": f"synthetic-{phase}", "nonce": nonce}
    if sealed is not None:
        body["sealed_p_digest"] = sealed
    return body


def test_p_auth_cannot_authorize_d() -> None:
    gate = epoch.PhaseGate("M")
    ticket_p = _ticket("P", nonce=1)
    gate.authorize_p(artifact=ticket_p, expected_digest=epoch.authorization_digest(ticket_p))
    gate.begin_p()
    gate.seal_p(sealed_p_digest="a" * 64)
    # Reusing the P ticket for D refuses (wrong phase + missing sealed binding).
    with pytest.raises(epoch.EpochError):
        gate.authorize_d(
            artifact=ticket_p,
            expected_digest=epoch.authorization_digest(ticket_p),
            sealed_p_digest="a" * 64,
        )


def test_d_auth_binds_sealed_p_digest() -> None:
    gate = epoch.PhaseGate("T")
    ticket_p = _ticket("P", nonce=2)
    gate.authorize_p(artifact=ticket_p, expected_digest=epoch.authorization_digest(ticket_p))
    gate.begin_p()
    gate.seal_p(sealed_p_digest="b" * 64)
    good = _ticket("D", sealed="b" * 64, nonce=3)
    gate.authorize_d(
        artifact=good, expected_digest=epoch.authorization_digest(good), sealed_p_digest="b" * 64
    )
    assert gate.state == "D_AUTHORIZED"
    gate2 = epoch.PhaseGate("T")
    gate2.authorize_p(artifact=ticket_p, expected_digest=epoch.authorization_digest(ticket_p))
    gate2.begin_p()
    gate2.seal_p(sealed_p_digest="b" * 64)
    wrong = _ticket("D", sealed="c" * 64, nonce=4)
    with pytest.raises(epoch.EpochError):
        gate2.authorize_d(
            artifact=wrong,
            expected_digest=epoch.authorization_digest(wrong),
            sealed_p_digest="c" * 64,
        )


def test_phase_state_machine_separation() -> None:
    gate = epoch.PhaseGate("M")
    with pytest.raises(epoch.EpochError):
        gate.begin_d()
    ticket_p = _ticket("P", nonce=5)
    gate.authorize_p(artifact=ticket_p, expected_digest=epoch.authorization_digest(ticket_p))
    assert gate.state == "P_AUTHORIZED"
    gate.begin_p()
    good_d = _ticket("D", sealed="x" * 64)
    with pytest.raises(epoch.EpochError):
        gate.authorize_d(
            artifact=good_d,
            expected_digest=epoch.authorization_digest(good_d),
            sealed_p_digest="x" * 64,
        )


# --------------------------------------------------------------------------
# Memory supervision (fail-closed repair).
# --------------------------------------------------------------------------


def test_memory_child_inclusion_and_dedup() -> None:
    def reader() -> list[tuple[int, int]]:
        return [(100, 100_000_000), (101, 50_000_000)]

    supervisor = guards.FailClosedSupervisor(cap_bytes=268435456, reader=reader)

    class Child:
        pid = 101

    supervisor.register_child(Child())
    assert supervisor.check(context="t")["total_rss"] == 150_000_000


def test_memory_unobservable_pid_fails_closed() -> None:
    def reader() -> list[tuple[int, int]]:
        return [(100, 10)]

    supervisor = guards.FailClosedSupervisor(cap_bytes=268435456, reader=reader)

    class Child:
        pid = 999

    supervisor.register_child(Child())
    with pytest.raises(guards.GuardError, match="absent without terminated proof"):
        supervisor.check(context="t")
    # Explicit terminated proof removes the PID; then the check passes.
    supervisor.release_child(999, terminated=True)
    assert supervisor.check(context="t")["total_rss"] == 10
    # Ambiguous release refuses.
    supervisor.register_child(Child())
    with pytest.raises(guards.GuardError):
        supervisor.release_child(999, terminated=False)


def test_memory_unreadable_child_fails_closed() -> None:
    def bad_reader() -> list[tuple[int, int]] | None:
        raise guards.GuardError("unreadable child process: boom")

    supervisor = guards.FailClosedSupervisor(cap_bytes=268435456, reader=bad_reader)
    with pytest.raises(guards.GuardError, match="unreadable"):
        supervisor.check(context="t")
    none_reader = guards.FailClosedSupervisor(cap_bytes=1, reader=lambda: None)
    with pytest.raises(guards.GuardError, match="unobservable"):
        none_reader.check(context="t")


def test_memory_over_cap_refuses() -> None:
    supervisor = guards.FailClosedSupervisor(cap_bytes=100, reader=lambda: [(1, 101)])
    with pytest.raises(guards.GuardError, match="exceeds"):
        supervisor.check(context="t")


def test_memory_process_exit_race() -> None:
    # PID present at registration, gone at measure without proof -> blocked.
    seen = {"first": True}

    def flaky() -> list[tuple[int, int]]:
        if seen["first"]:
            seen["first"] = False
            return [(100, 10), (200, 20)]
        return [(100, 10)]

    supervisor = guards.FailClosedSupervisor(cap_bytes=10**9, reader=flaky)

    class Child:
        pid = 200

    supervisor.register_child(Child())
    assert supervisor.check(context="first")["total_rss"] == 30
    with pytest.raises(guards.GuardError, match="absent without terminated proof"):
        supervisor.check(context="second")


# --------------------------------------------------------------------------
# Disk schedule (physical inventory repair).
# --------------------------------------------------------------------------


def test_disk_fresh_root_and_unknown_refusal(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    inv = guards.PhysicalDiskInventory(root, scratch_cap=10**9, final_cap=10**9, combined_cap=10**9)
    inv.reserve("a.json", scratch=10, final=10)
    inv.write_file("a.json", b'{"a":1}')
    (root / "stowaway.bin").write_bytes(b"unknown")
    with pytest.raises(guards.GuardError, match="unknown files"):
        inv.reconcile()


def test_disk_reservation_before_write_and_atomic_tmp(tmp_path: Path) -> None:
    root = tmp_path / "disk"
    root.mkdir()
    inv = guards.PhysicalDiskInventory(root, scratch_cap=10**9, final_cap=10**9, combined_cap=10**9)
    with pytest.raises(guards.GuardError, match="without prior reservation"):
        inv.write_file("b.json", b"x")
    inv.reserve("b.json", scratch=1, final=1)
    inv.write_file("b.json", b"x")
    assert not (root / "b.json.tmp").exists()
    assert (root / "b.json").read_bytes() == b"x"


def test_disk_release_requires_verified_deletion(tmp_path: Path) -> None:
    root = tmp_path / "rel"
    root.mkdir()
    inv = guards.PhysicalDiskInventory(root, scratch_cap=10**9, final_cap=10**9, combined_cap=10**9)
    inv.reserve("c.json", scratch=1, final=1)
    inv.write_file("c.json", b"x")
    with pytest.raises(guards.GuardError, match="still present"):
        inv.release("c.json")
    inv.delete_verified("c.json")
    assert "c.json" not in inv.snapshot()["objects"]
    assert not (root / "c.json").exists()


def test_disk_occupancy_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "rec"
    root.mkdir()
    inv = guards.PhysicalDiskInventory(root, scratch_cap=10**9, final_cap=10**9, combined_cap=10**9)
    inv.reserve("a.json", scratch=5, final=5)
    inv.write_file("a.json", b"12345")
    report = inv.reconcile()
    assert report["physical_files"]["a.json"] == 5
    assert report["reserved_total"] == 10


def test_staged_final_publication_semantics() -> None:
    from xlm.data.evidence_v2 import reserves as reserves_mod

    gate = reserves_mod.staged_publication_gate(
        acquisition_final_bytes=1000, label_budget_upper_bytes=500
    )
    assert gate["verdict"] == "COMPLETE_FITS"
    over = reserves_mod.staged_publication_gate(
        acquisition_final_bytes=10**9, label_budget_upper_bytes=0
    )
    assert over["verdict"] == "REFUSE_ACQUISITION"


# --------------------------------------------------------------------------
# Runtime accounting (monotonic repair).
# --------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000_000

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1_000_000_000)


def test_runtime_monotonic_active_time_and_resume() -> None:
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(clock=clock)
    runtime.start_segment()
    clock.advance(2.5)
    charged = runtime.end_segment(file="f.parquet")
    assert charged == pytest.approx(2.5)
    state = runtime.state()
    assert state["arm_elapsed"] == pytest.approx(2.5)
    restored = guards.ActiveRuntime.load(state, clock=clock)
    assert restored.state()["arm_elapsed"] == pytest.approx(2.5)
    restored.start_segment()
    clock.advance(1.0)
    restored.end_segment(file="f.parquet")
    assert restored.state()["arm_elapsed"] == pytest.approx(3.5)


def test_runtime_earliest_deadline_wins() -> None:
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(arm_cap=10.0, file_cap=5.0, per_request_cap=30.0, clock=clock)
    assert runtime.request_budget(file="f.parquet") == pytest.approx(5.0)
    with pytest.raises(guards.GuardError, match="30 s absolute"):
        runtime.charge(31.0, file="f.parquet")
    with pytest.raises(guards.GuardError, match="file deadline"):
        runtime.charge(6.0, file="f.parquet")
    runtime.charge(5.0, file="f.parquet")
    runtime.charge(5.0, file="g.parquet")
    assert runtime.state()["arm_elapsed"] == pytest.approx(10.0)
    with pytest.raises(guards.GuardError, match="arm deadline"):
        runtime.charge(0.1, file="h.parquet")


def test_runtime_sealed_pause_semantics() -> None:
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(clock=clock)
    runtime.start_segment()
    with pytest.raises(guards.GuardError, match="open active segment"):
        runtime.seal_pause()
    clock.advance(1.0)
    runtime.end_segment(file="f.parquet")
    runtime.seal_pause()
    with pytest.raises(guards.GuardError, match="while paused"):
        runtime.charge(1.0, file="f.parquet")
    runtime.resume()
    runtime.charge(1.0, file="f.parquet")
    assert runtime.state()["arm_elapsed"] == pytest.approx(2.0)


def test_no_live_network_or_text_paths() -> None:
    # Static guard: v3 modules must not import sockets/requests or read text.
    for module in ("epoch.py", "ledger.py", "schedules.py", "guards.py", "dry.py"):
        raw = (ROOT / "src/xlm/data/evidence_v3" / module).read_bytes().decode("utf-8")
        assert "socket" not in raw
        assert "requests.get" not in raw
        assert "corpus_text" not in raw
