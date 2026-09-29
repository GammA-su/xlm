"""Essential-Web evidence v3.0 remediation regressions (offline/adversarial).

Every Astra Phase-P review finding gets a reproducing regression here.
All fixtures are authored synthetic; disposable temporary roots only.
No network (sockets blocked), no acquisition, no corpus text, no real
genesis, no G: writes, no push.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    authz,
    envidentity,
    epoch,
    executor,
    frozen_v3,
    guards,
    ledger,
    readiness,
    schedules,
    transport,
)

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
# Authorization-schema fixtures.
# --------------------------------------------------------------------------


def _approval_for(core_digest: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": authz.APPROVAL_KIND,
        "schema_version": authz.APPROVAL_SCHEMA_VERSION,
        "authorization_core_digest": core_digest,
        "operator_identity": "synthetic-operator",
        "utc_timestamp": "2026-09-29T10:00:00Z",
        "statement": "operator approves phase-P execution under this authorization",
    }
    body.update(overrides)
    body["digest"] = canonical.self_digest({k: v for k, v in body.items() if k != "digest"})
    return body


def _valid_auth(**overrides: Any) -> dict[str, Any]:
    core: dict[str, Any] = {
        "kind": authz.AUTH_KIND,
        "schema_version": authz.AUTH_SCHEMA_VERSION,
        "protocol_sha256": frozen_v3.PROTOCOL_SHA256,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
        "implementation_commit": "0" * 40,
        "child_manifest_digest": "a" * 64,
        "epoch_id": frozen_v3.EPOCH_ID,
        "execution_root": frozen_v3.EXECUTION_ROOT,
        "phase": "P",
        "arms": ["M", "T"],
        "m_phase_p_plan_digest": "b" * 64,
        "t_phase_p_plan_digest": "c" * 64,
        "scientific_namespace": frozen_v3.SCIENTIFIC_NAMESPACE,
        "selection_digest": frozen_v3.SELECTION_DIGEST,
        "source_revision": frozen_v3.SOURCE_REVISION,
        "resource_caps": {"M": dict(frozen_v3.ARM_M_CAPS), "T": dict(frozen_v3.ARM_T_CAPS)},
        "resource_caps_digest": frozen_v3.RESOURCE_CAPS_DIGEST,
        "code_hashes": {"src/x.py": "d" * 64},
        "environment_identity": {
            "python_version": "3.12.13",
            "python_executable_class": "project-venv",
            "uv_version": "uv 0.12.19",
            "uv_lock_blob_sha": "blob",
            "pyproject_blob_sha": "blob",
            "python_version_blob_sha": "blob",
            "pyarrow_version": "25.0.1",
            "psutil_version": "7.2.2",
            "torch_version": "2.14.0+cpu",
            "cpu_cuda_build": "2.14.0+cpu+cpu",
            "os_build": "Windows-11",
            "architecture": "AMD64",
        },
        "reviewer_decision": "APPROVED",
        "review_artifact_digest": "e" * 64,
        "authorization_scope": "phase-P-only",
        "root_precondition": "absent-or-pristine-empty",
    }
    core.update({k: v for k, v in overrides.items() if k != "approval_overrides"})
    core_digest = authz.auth_core_digest(core)
    approval = _approval_for(core_digest, **overrides.get("approval_overrides", {}))
    artifact = {**core, "operator_approval": approval}
    artifact["digest"] = canonical.self_digest({k: v for k, v in artifact.items() if k != "digest"})
    return artifact


def _expect() -> dict[str, Any]:
    return {
        "expected_freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
        "expected_implementation_commit": "0" * 40,
        "expected_child_manifest_digest": "a" * 64,
        "expected_m_phase_p_plan_digest": "b" * 64,
        "expected_t_phase_p_plan_digest": "c" * 64,
        "expected_code_hashes": {"src/x.py": "d" * 64},
        "accepted_review_digests": {"e" * 64},
    }


# --------------------------------------------------------------------------
# AUTHORIZATION: strict schema regressions.
# --------------------------------------------------------------------------


def test_auth_missing_phase_refuses() -> None:
    artifact = _valid_auth()
    del artifact["phase"]
    artifact["digest"] = canonical.self_digest(artifact)
    with pytest.raises(authz.AuthError, match="missing fields"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_blocked_decision_refuses() -> None:
    artifact = _valid_auth(reviewer_decision="BLOCKED")
    with pytest.raises(authz.AuthError, match="not a successful authorization"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_wrong_root_refuses() -> None:
    artifact = _valid_auth(execution_root="F:/Project/xlm-evidence-v3/essential-web")
    with pytest.raises(authz.AuthError, match="wrong execution-root"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_wrong_epoch_refuses() -> None:
    artifact = _valid_auth(epoch_id="essential-web-evidence-v3.0:essential-web:epoch-0002")
    with pytest.raises(authz.AuthError, match="wrong epoch"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_wrong_implementation_refuses() -> None:
    artifact = _valid_auth()
    expect = _expect()
    expect["expected_implementation_commit"] = "1" * 40
    with pytest.raises(authz.AuthError, match="wrong implementation"):
        authz.validate_phase_p_authorization(artifact, **expect)


def test_auth_unknown_field_refuses() -> None:
    artifact = _valid_auth(yes=True)
    with pytest.raises(authz.AuthError, match="unknown fields"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_wrong_plan_digest_refuses() -> None:
    artifact = _valid_auth()
    expect = _expect()
    expect["expected_m_phase_p_plan_digest"] = "f" * 64
    with pytest.raises(authz.AuthError, match="wrong M phase-P plan"):
        authz.validate_phase_p_authorization(artifact, **expect)


def test_auth_stale_review_refuses() -> None:
    artifact = _valid_auth()
    expect = _expect()
    expect["accepted_review_digests"] = {"9" * 64}
    with pytest.raises(authz.AuthError, match="stale or superseded"):
        authz.validate_phase_p_authorization(artifact, **expect)


def test_auth_boolean_refuses() -> None:
    with pytest.raises(authz.AuthError):
        authz.validate_phase_p_authorization(True, **_expect())  # type: ignore[arg-type]


def test_auth_missing_operator_approval_refuses() -> None:
    artifact = _valid_auth()
    del artifact["operator_approval"]
    artifact["digest"] = canonical.self_digest(artifact)
    with pytest.raises(authz.AuthError, match="missing fields"):
        authz.validate_phase_p_authorization(artifact, **_expect())


def test_auth_valid_strict_schema_passes() -> None:
    result = authz.validate_phase_p_authorization(_valid_auth(), **_expect())
    assert result["phase"] == "P"
    assert result["epoch_id"] == frozen_v3.EPOCH_ID
    assert len(result["authorization_digest"]) == 64
    assert len(result["operator_approval_digest"]) == 64


def test_cap_digest_independently_reproduced() -> None:
    freeze = _freeze()
    recomputed = canonical.digest(
        {"M": dict(freeze["arm_caps"]["M"]), "T": dict(freeze["arm_caps"]["T"])}
    )
    assert recomputed == "e2485cd438524124c22074d59c48a5ee7dc699f9c9a9ea3f9c11deeef54e0f7b"
    assert recomputed == frozen_v3.RESOURCE_CAPS_DIGEST


# --------------------------------------------------------------------------
# GENESIS: exclusive, exactly-once, fully bound.
# --------------------------------------------------------------------------


def _genesis_kwargs(synthetic: Path, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "authorization_digest": "a" * 64,
        "operator_approval_digest": "b" * 64,
        "protocol_digest": frozen_v3.PROTOCOL_SHA256,
        "freeze_digest": frozen_v3.FREEZE_DIGEST,
        "implementation_commit": "c" * 40,
        "child_manifest_digest": "d" * 64,
        "m_phase_p_plan_digest": "e" * 64,
        "t_phase_p_plan_digest": "f" * 64,
        "execution_root": synthetic,
        "initial_inventory": {"root": synthetic.as_posix(), "entries": []},
        "initial_zero_ledgers": {
            "M": {"requests": 0, "response_body_bytes": 0},
            "T": {"requests": 0, "response_body_bytes": 0},
        },
        "code_hashes": {"src/x.py": "0" * 64},
        "environment_identity": {"python_version": "3.12.13"},
        "resource_caps": {"M": dict(frozen_v3.ARM_M_CAPS), "T": dict(frozen_v3.ARM_T_CAPS)},
        "supervision_identity": {"supervisor": "synthetic"},
        "owner": "synthetic-test",
        "utc_timestamp": "2026-09-29T10:00:00Z",
        "clock_monotonic_ns": 123,
        "allow_nonfrozen_root": True,
    }
    params.update(overrides)
    return params


def test_genesis_concurrent_exactly_once(tmp_path: Path) -> None:
    synthetic = tmp_path / "race"
    barrier = threading.Barrier(8)
    outcomes: list[str] = []

    def attempt() -> None:
        barrier.wait()
        try:
            claim = epoch.claim_genesis_exclusive(synthetic, owner="racer")
            body = epoch.build_genesis_record(**_genesis_kwargs(synthetic))
            epoch.publish_genesis_record(synthetic, body, claim=claim)
            outcomes.append("winner")
        except epoch.EpochError:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("winner") == 1
    assert outcomes.count("refused") == 7
    assert (synthetic / "epoch_start.json").is_file()


def test_genesis_second_refuses_after_restart(tmp_path: Path) -> None:
    synthetic = tmp_path / "once"
    claim = epoch.claim_genesis_exclusive(synthetic, owner="op")
    body = epoch.build_genesis_record(**_genesis_kwargs(synthetic))
    epoch.publish_genesis_record(synthetic, body, claim=claim)
    with pytest.raises(epoch.EpochError, match="second genesis"):
        epoch.claim_genesis_exclusive(synthetic, owner="op2")
    with pytest.raises(epoch.EpochError, match="second genesis"):
        epoch.publish_genesis_record(synthetic, body, claim=claim)


def test_genesis_crash_safe_no_partial(tmp_path: Path) -> None:
    synthetic = tmp_path / "atomic"
    claim = epoch.claim_genesis_exclusive(synthetic, owner="op")
    body = epoch.build_genesis_record(**_genesis_kwargs(synthetic))
    epoch.publish_genesis_record(synthetic, body, claim=claim)
    assert not (synthetic / "epoch_start.json.tmp").exists()
    loaded = epoch.load_genesis_record(synthetic, allow_nonfrozen_root=True)
    assert loaded["digest"] == body["digest"]


def test_genesis_binds_auth_and_approval(tmp_path: Path) -> None:
    synthetic = tmp_path / "bind"
    body = epoch.build_genesis_record(**_genesis_kwargs(synthetic))
    assert body["authorization_digest"] == "a" * 64
    assert body["operator_approval_digest"] == "b" * 64
    with pytest.raises(epoch.EpochError):
        epoch.build_genesis_record(**_genesis_kwargs(synthetic, operator_approval_digest="bad"))


def test_genesis_binds_zero_ledgers_and_clocks(tmp_path: Path) -> None:
    synthetic = tmp_path / "zero"
    body = epoch.build_genesis_record(**_genesis_kwargs(synthetic))
    assert body["initial_zero_ledgers"]["M"]["requests"] == 0
    assert body["v3_network_events_before_genesis"] == 0
    assert body["utc_timestamp"] == "2026-09-29T10:00:00Z"
    assert body["clock_monotonic_ns"] == 123
    with pytest.raises(epoch.EpochError):
        epoch.build_genesis_record(
            **_genesis_kwargs(
                synthetic,
                initial_zero_ledgers={"M": {"requests": 3}, "T": {"requests": 0}},
            )
        )


def test_no_real_g_write() -> None:
    assert not Path(frozen_v3.EXECUTION_ROOT).exists()


# --------------------------------------------------------------------------
# LEDGER: durability and crash regressions.
# --------------------------------------------------------------------------


def _journaled(tmp_path: Path, arm: str = "M") -> tuple[ledger.V3Ledger, Path, Path]:
    journal_dir = tmp_path / f"ledger-{arm}"
    arm_ledger = ledger.V3Ledger(arm, path=journal_dir)
    snapshot = tmp_path / f"snapshot-{arm}.json"
    return arm_ledger, journal_dir, snapshot


def _apply1(arm_ledger: ledger.V3Ledger, event_id: str) -> None:
    arm_ledger.apply(
        ledger.V3Event(
            event_id=event_id, kind="request", file="f.parquet", category="footer", requests=1
        )
    )


def test_ledger_apply_persists_before_return(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path)
    arm_ledger.save(snapshot)  # zero ledger persisted
    event = ledger.V3Event(
        event_id="e1",
        kind="request",
        file="f.parquet",
        category="footer",
        requests=1,
    )
    arm_ledger.apply(event)
    journal = journal_dir / "journal.jsonl"
    assert journal.stat().st_size > 0  # persisted before return
    reloaded = ledger.V3Ledger.load(snapshot, "M", journal_dir=journal_dir)
    _ = reloaded
    arm_ledger.save(snapshot)
    reloaded = ledger.V3Ledger.load(snapshot, "M", journal_dir=journal_dir)
    assert reloaded.snapshot()["counters"]["requests"] == 1


def test_ledger_reload_preserves_category_and_elapsed(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path, "T")
    event = ledger.V3Event(
        event_id="d1",
        kind="request",
        file="g.parquet",
        category="data",
        requests=1,
        body_bytes=64,
        elapsed_seconds=3.0,
    )
    arm_ledger.apply(event)
    arm_ledger.save(snapshot)
    reloaded = ledger.V3Ledger.load(snapshot, "T", journal_dir=journal_dir)
    counters = reloaded.snapshot()["counters"]
    assert counters["requests"] == 1
    assert counters["transfer_by_category"]["data"] == 64
    assert counters["transfer_by_category"]["footer"] == 0
    assert counters["elapsed_seconds"] == 3.0


def test_ledger_crash_pending_conserves(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path, "T")
    event = ledger.V3Event(
        event_id="pending",
        kind="request",
        file="h.parquet",
        category="data",
        requests=1,
        body_bytes=64,
        elapsed_seconds=3.0,
    )
    token = arm_ledger.reserve_event(event)
    assert token
    arm_ledger.save(snapshot)  # crash with RESERVED open
    reloaded = ledger.V3Ledger.load(snapshot, "T", journal_dir=journal_dir)
    counters = reloaded.snapshot()["counters"]
    assert counters["requests"] == 1
    assert counters["transfer_by_category"]["data"] == 0  # body not yet observed: no bytes
    assert any(v.get("status") == "CRASH_RESERVED" for v in reloaded.in_flight.values())
    crash = next(v for v in reloaded.in_flight.values() if v.get("status") == "CRASH_RESERVED")
    assert crash["body_category"] == "data"  # category preserved, never defaulted


def test_ledger_conflicting_replay_refuses(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path)
    event = ledger.V3Event(
        event_id="e1", kind="request", file="f.parquet", category="footer", requests=1
    )
    arm_ledger.apply(event)
    arm_ledger.save(snapshot)
    reloaded = ledger.V3Ledger.load(snapshot, "M", journal_dir=journal_dir)
    assert reloaded.apply(event) is False
    with pytest.raises(ledger.LedgerError, match="conflicting"):
        reloaded.apply(
            ledger.V3Event(
                event_id="e1", kind="request", file="f.parquet", category="footer", requests=2
            )
        )


def test_ledger_truncated_journal_refuses(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path)
    _apply1(arm_ledger, "e1")
    _apply1(arm_ledger, "e2")
    arm_ledger.save(snapshot)
    journal = journal_dir / "journal.jsonl"
    lines = journal.read_bytes().split(b"\n")
    journal.write_bytes(b"\n".join(lines[:1]) + b"\n")  # drop the tail: rollback
    with pytest.raises(ledger.LedgerError, match="rollback/loss"):
        ledger.V3Ledger.load(snapshot, "M", journal_dir=journal_dir)


def test_ledger_corrupt_journal_refuses(tmp_path: Path) -> None:
    arm_ledger, journal_dir, snapshot = _journaled(tmp_path)
    _apply1(arm_ledger, "e1")
    arm_ledger.save(snapshot)
    journal = journal_dir / "journal.jsonl"
    journal.write_bytes(b"\x00not-json\xff\n")
    with pytest.raises(ledger.LedgerError, match="corrupt"):
        ledger.V3Ledger.load(snapshot, "M", journal_dir=journal_dir)


def test_ledger_v2_import_refuses(tmp_path: Path) -> None:
    arm_ledger, _, _ = _journaled(tmp_path)
    with pytest.raises(ledger.LedgerError):
        arm_ledger.import_v2_counters({"requests": 55})


# --------------------------------------------------------------------------
# TRANSPORT POLICY regressions.
# --------------------------------------------------------------------------


def test_transport_http_refuses() -> None:
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_url("http://huggingface.co/datasets/x/resolve/main/f.parquet")


def test_transport_loopback_refuses() -> None:
    for url in (
        "https://localhost:443/x",
        "https://127.0.0.1:443/x",
        "https://[::1]/x",
    ):
        with pytest.raises(transport.TransportPolicyError):
            transport.validate_url(url)


def test_transport_ip_literal_refuses() -> None:
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_url("https://93.184.216.34/datasets/x")


def test_transport_raw_githubusercontent_refuses() -> None:
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_url("https://raw.githubusercontent.com/org/repo/main/f.parquet")


def test_transport_arbitrary_hf_subdomain_refuses() -> None:
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_url("https://evil-hacker.hf.co/datasets/x")


def test_transport_port_444_refuses() -> None:
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_url("https://huggingface.co:444/datasets/x")


def test_transport_exact_allowed_hosts_pass() -> None:
    for url in (
        "https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/main/f.parquet",
        "https://cas-bridge.xethub.hf.co/xet-bridge/file/abc",
    ):
        assert transport.validate_url(url)["port"] == 443


def test_transport_redirect_revalidated_each_hop() -> None:
    good = "https://huggingface.co/datasets/x/resolve/main/f.parquet"
    evil = "https://raw.githubusercontent.com/org/repo/main/f.parquet"
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_redirect_chain([good, evil])
    with pytest.raises(transport.TransportPolicyError):
        transport.validate_redirect_chain([good] * 6)  # 5 transitions > 3
    assert transport.validate_redirect_chain([good, good])["redirect_transitions"] == 1


# --------------------------------------------------------------------------
# Executor harness.
# --------------------------------------------------------------------------


def _harness(
    tmp_path: Path,
    arm: str,
    operations: list[dict[str, Any]] | None = None,
    routes: dict[str, executor.FakeResponse] | None = None,
    monkeypatch: Any = None,
) -> dict[str, Any]:
    epoch_root = tmp_path / "epoch"
    if monkeypatch is not None:
        monkeypatch.setattr(frozen_v3, "EXECUTION_ROOT", epoch_root.as_posix())
    epoch_root.mkdir(exist_ok=True)
    claim = epoch.claim_genesis_exclusive(epoch_root, owner="harness")
    body = epoch.build_genesis_record(**_genesis_kwargs(epoch_root))
    epoch.publish_genesis_record(epoch_root, body, claim=claim)
    journal_dir = tmp_path / "journal"
    arm_ledger = ledger.V3Ledger(arm, path=journal_dir)
    memory = guards.FailClosedSupervisor(cap_bytes=268435456, reader=lambda: [(1, 8)])
    disk_root = tmp_path / "disk"
    disk_root.mkdir(exist_ok=True)
    disk = guards.PhysicalDiskInventory(
        disk_root, scratch_cap=1 << 30, final_cap=1 << 30, combined_cap=1 << 31
    )
    runtime = guards.ActiveRuntime()
    gate = epoch.PhaseGate(arm)
    artifact = _valid_auth(
        execution_root=epoch_root.as_posix(),
        child_manifest_digest="a" * 64,
        m_phase_p_plan_digest="b" * 64,
        t_phase_p_plan_digest="c" * 64,
    )
    validated = authz.validate_phase_p_authorization(artifact, **_expect())
    gate.authorize_p(artifact=artifact, expected_digest=epoch.authorization_digest(artifact))
    phone = executor.PhasePExecutor(
        arm,
        authorization=artifact,
        validated_auth=validated,
        epoch_root=epoch_root,
        phase_ledger=arm_ledger,
        memory=memory,
        disk=disk,
        runtime=runtime,
        gate=gate,
        transport_client=executor.FakeTransport(routes or {}),
    )
    return {
        "executor": phone,
        "ledger": arm_ledger,
        "memory": memory,
        "disk": disk,
        "runtime": runtime,
        "gate": gate,
        "operations": operations or [],
    }


def _op(url: str, **overrides: Any) -> dict[str, Any]:
    op: dict[str, Any] = {
        "file": "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet",
        "url": url,
        "range_start": 0,
        "range_end": 3,
        "remote_length": 100,
        "etag": '"abc"',
        "require_par1": True,
        "category": "footer",
        "phase": "P",
        "kind": "identity",
    }
    op.update(overrides)
    return op


GOOD_URL = (
    "https://huggingface.co/datasets/EssentialAI/essential-web-v1.0/resolve/"
    "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d/data/crawl=CC-MAIN-2014-15/"
    "train-01860-of-02772.parquet"
)


def _resp(status: int = 206, body: bytes = b"PAR1", etag: str = '"abc"') -> executor.FakeResponse:
    return executor.FakeResponse(status=status, content_range="bytes 0-3/100", etag=etag, body=body)


# --------------------------------------------------------------------------
# BODY ACCOUNTING regressions.
# --------------------------------------------------------------------------


def test_body_invalid_status_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(
        tmp_path,
        "M",
        routes={GOOD_URL: _resp(status=503, body=b"E" * 100)},
        monkeypatch=monkeypatch,
    )
    with pytest.raises(executor.ExecutorError):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])
    counters = ctx["ledger"].snapshot()["counters"]
    assert counters["requests"] == 1
    assert counters["response_body_bytes"] == 100  # rejected body still charged


def test_body_redirect_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hop = GOOD_URL + "?hop=1"
    ctx = _harness(
        tmp_path,
        "M",
        routes={GOOD_URL: _resp(body=b"PAR1"), hop: _resp(body=b"R" * 50)},
        monkeypatch=monkeypatch,
    )
    ctx["executor"].run_phase_p([_op(GOOD_URL, redirects=[hop])])
    counters = ctx["ledger"].snapshot()["counters"]
    assert counters["requests"] == 2  # logical + 1 redirect transition
    assert counters["response_body_bytes"] == 4 + 50


def test_body_partial_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp(body=b"PA")}, monkeypatch=monkeypatch)
    with pytest.raises(executor.ExecutorError):  # PAR1 check fails on short body
        ctx["executor"].run_phase_p([_op(GOOD_URL)])
    assert ctx["ledger"].snapshot()["counters"]["response_body_bytes"] == 2


def test_body_failed_retry_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(
        tmp_path, "M", routes={GOOD_URL: _resp(status=500, body=b"E" * 10)}, monkeypatch=monkeypatch
    )
    with pytest.raises(executor.ExecutorError):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])
    # A retry is a new authorized attempt under a fresh gate sharing the
    # same ledger: both failed bodies accumulate.
    gate2 = epoch.PhaseGate("M")
    artifact = ctx["executor"].authorization
    gate2.authorize_p(artifact=artifact, expected_digest=epoch.authorization_digest(artifact))
    retry = executor.PhasePExecutor(
        "M",
        authorization=artifact,
        validated_auth=ctx["executor"].validated_auth,
        epoch_root=ctx["executor"].epoch_root,
        phase_ledger=ctx["ledger"],
        memory=ctx["memory"],
        disk=ctx["disk"],
        runtime=ctx["runtime"],
        gate=gate2,
        transport_client=ctx["executor"].transport_client,
    )
    with pytest.raises(executor.ExecutorError):
        retry.run_phase_p([_op(GOOD_URL)])
    assert ctx["ledger"].snapshot()["counters"]["response_body_bytes"] == 20


# --------------------------------------------------------------------------
# MEMORY regressions.
# --------------------------------------------------------------------------


def test_memory_parent_child_and_dedup() -> None:
    registry = guards.OwnedProcessRegistry(is_alive=lambda pid: False)
    registry.register(200)
    supervisor = guards.FailClosedSupervisor(
        cap_bytes=1 << 28, reader=lambda: [(100, 60), (200, 40)], registry=registry
    )
    assert supervisor.check(context="t")["total_rss"] == 100


def test_memory_missing_owned_pid_refuses() -> None:
    registry = guards.OwnedProcessRegistry(is_alive=lambda pid: False)
    registry.register(999)
    supervisor = guards.FailClosedSupervisor(
        cap_bytes=1 << 28, reader=lambda: [(100, 10)], registry=registry
    )
    with pytest.raises(guards.GuardError, match="absent without authoritative release"):
        supervisor.check(context="t")


def test_memory_unreadable_refuses() -> None:
    def bad() -> Any:
        raise guards.GuardError("unreadable child process: boom")

    supervisor = guards.FailClosedSupervisor(cap_bytes=1 << 28, reader=bad)
    with pytest.raises(guards.GuardError, match="unreadable"):
        supervisor.check(context="t")


def test_memory_cap_exceed_refuses() -> None:
    supervisor = guards.FailClosedSupervisor(cap_bytes=100, reader=lambda: [(1, 101)])
    with pytest.raises(guards.GuardError, match="exceeds"):
        supervisor.check(context="t")


def test_memory_no_boolean_bypass() -> None:
    registry = guards.OwnedProcessRegistry(is_alive=lambda pid: True)
    registry.register(300)
    with pytest.raises(guards.GuardError, match="still alive"):
        registry.reap(300)
    with pytest.raises(guards.GuardError, match="never a boolean"):
        registry.release(True)  # type: ignore[arg-type]
    forged = guards.ExitProof(pid=300, nonce=999999)
    with pytest.raises(guards.GuardError, match="invalid exit proof"):
        registry.release(forged)


def test_memory_registry_reap_release_cycle() -> None:
    alive: dict[int, bool] = {400: True}
    registry = guards.OwnedProcessRegistry(is_alive=lambda pid: alive[pid])
    registry.register(400)
    alive[400] = False
    proof = registry.reap(400)
    registry.release(proof)
    assert registry.active() == ()


# --------------------------------------------------------------------------
# DISK regressions.
# --------------------------------------------------------------------------


def _disk(tmp_path: Path, cap: int = 10) -> guards.PhysicalDiskInventory:
    root = tmp_path / "disk"
    root.mkdir(parents=True, exist_ok=True)
    return guards.PhysicalDiskInventory(root, scratch_cap=cap, final_cap=cap, combined_cap=cap)


def test_disk_reserve1_write256_refuses(tmp_path: Path) -> None:
    inv = _disk(tmp_path, cap=1000)
    inv.reserve("tiny.bin", scratch=1, final=0)
    with pytest.raises(guards.GuardError, match="exceeds reservation"):
        inv.write_file("tiny.bin", b"x" * 256)
    assert inv.snapshot()["incomplete"] == ["tiny.bin"]
    # External bypass: a file that appears with more bytes than reserved
    # must fail reconciliation rather than report fits=true.
    (tmp_path / "disk" / "tiny.bin").write_bytes(b"x" * 256)
    with pytest.raises(guards.GuardError, match="exceeds reservation"):
        inv.reconcile()


def test_disk_old_temp_peak_counted(tmp_path: Path) -> None:
    inv = _disk(tmp_path, cap=10)
    inv.reserve("doc.bin", scratch=8, final=0)
    inv.write_file("doc.bin", b"x" * 8)
    with pytest.raises(guards.GuardError, match="exceed cap replacing"):
        inv.atomic_replace("doc.bin", b"y" * 8, scratch_tmp=8, final_new=0)
    root2 = tmp_path / "w2"
    root2.mkdir(exist_ok=True)
    inv2 = guards.PhysicalDiskInventory(root2, scratch_cap=100, final_cap=100, combined_cap=100)
    inv2.reserve("doc.bin", scratch=8, final=0)
    inv2.write_file("doc.bin", b"x" * 8)
    inv2.atomic_replace("doc.bin", b"y" * 8, scratch_tmp=8, final_new=0)
    assert inv2.snapshot()["peak_physical"] >= 16  # old + temp simultaneously


def test_disk_dotdot_escape_refuses(tmp_path: Path) -> None:
    inv = _disk(tmp_path)
    with pytest.raises(guards.GuardError, match="escape"):
        inv.reserve("../outside.bin", scratch=1, final=0)


def test_disk_absolute_escape_refuses(tmp_path: Path) -> None:
    inv = _disk(tmp_path)
    with pytest.raises(guards.GuardError, match="escape"):
        inv.reserve("C:/Windows/outside.bin", scratch=1, final=0)


def test_disk_symlink_escape_refuses(tmp_path: Path) -> None:
    target = tmp_path / "real-target.bin"
    target.write_bytes(b"secret")
    link = tmp_path / "disk" / "link.bin"
    link.parent.mkdir(exist_ok=True)
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unsupported on this volume")
    inv = _disk(tmp_path, cap=1000)
    with pytest.raises(guards.GuardError, match="escape"):
        inv.reconcile()


def test_disk_unknown_file_refuses(tmp_path: Path) -> None:
    inv = _disk(tmp_path, cap=1000)
    (tmp_path / "disk" / "stowaway.bin").write_bytes(b"?")
    with pytest.raises(guards.GuardError, match="unknown files"):
        inv.reconcile()


def test_disk_release_after_deletion_only(tmp_path: Path) -> None:
    inv = _disk(tmp_path, cap=1000)
    inv.reserve("c.bin", scratch=4, final=0)
    inv.write_file("c.bin", b"1234")
    with pytest.raises(guards.GuardError, match="still present"):
        inv.release("c.bin")
    inv.delete_verified("c.bin")
    assert "c.bin" not in inv.snapshot()["objects"]


def test_disk_import_allowlisted_hash_counted(tmp_path: Path) -> None:
    root = tmp_path / "disk"
    root.mkdir(exist_ok=True)
    payload = b'{"meta":1}'
    digest = hashlib.sha256(payload).hexdigest()
    inv = guards.PhysicalDiskInventory(
        root,
        scratch_cap=1000,
        final_cap=1000,
        combined_cap=1000,
        allowlist=("plan.json",),
    )
    with pytest.raises(guards.GuardError, match="non-allowlisted"):
        inv.import_allowlisted("evil.json", payload, expected_sha256=digest)
    with pytest.raises(guards.GuardError, match="hash mismatch"):
        inv.import_allowlisted("plan.json", payload, expected_sha256="0" * 64)
    inv.import_allowlisted("plan.json", payload, expected_sha256=digest, scratch=len(payload))
    assert inv.snapshot()["objects"]["plan.json"] == {"scratch": len(payload), "final": 0}


# --------------------------------------------------------------------------
# RUNTIME regressions.
# --------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000_000

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1_000_000_000)


def test_runtime_open31s_zero_budget_and_retained(tmp_path: Path) -> None:
    _ = tmp_path
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(clock=clock)
    runtime.start_segment(file="f.parquet")
    clock.advance(31.0)
    assert runtime.request_budget(file="f.parquet") == 0.0
    with pytest.raises(guards.GuardError):
        runtime.end_segment(file="f.parquet")
    assert runtime.anchor_monotonic_ns is not None  # anchor NOT cleared
    assert runtime.state()["arm_elapsed"] == 0.0  # elapsed retained


def test_runtime_crash_preserves_conservative_elapsed() -> None:
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(clock=clock)
    runtime.start_segment(file="f.parquet")
    clock.advance(5.0)
    state = runtime.state()
    assert state["anchor_monotonic_ns"] is not None
    restored = guards.ActiveRuntime.load(state, clock=clock)
    clock.advance(2.0)
    conserved = restored.conserve_restart_open_segment()
    assert conserved == pytest.approx(7.0)
    assert restored.state()["arm_elapsed"] == pytest.approx(7.0)


def test_runtime_pause_during_open_refuses() -> None:
    clock = _FakeClock()
    runtime = guards.ActiveRuntime(clock=clock)
    runtime.start_segment(file="f.parquet")
    with pytest.raises(guards.GuardError, match="open active segment"):
        runtime.seal_pause(sealed_quiescent=True)
    with pytest.raises(guards.GuardError, match="sealed quiescent"):
        runtime.seal_pause(sealed_quiescent=False)


def test_runtime_sealed_pause_works() -> None:
    runtime = guards.ActiveRuntime()
    runtime.seal_pause(sealed_quiescent=True)
    with pytest.raises(guards.GuardError, match="while paused"):
        runtime.start_segment(file="f.parquet")
    runtime.resume()
    runtime.start_segment(file="f.parquet")
    runtime.end_segment(file="f.parquet")


def test_runtime_earliest_wins() -> None:
    runtime = guards.ActiveRuntime(arm_cap=10.0, file_cap=5.0, per_request_cap=30.0)
    assert runtime.request_budget(file="f.parquet") == pytest.approx(5.0)
    runtime.charge(5.0, file="f.parquet")
    assert runtime.request_budget(file="f.parquet") == 0.0
    assert runtime.request_budget(file="g.parquet") == pytest.approx(5.0)


# --------------------------------------------------------------------------
# INTEGRATION regressions.
# --------------------------------------------------------------------------


def test_executor_refuses_without_epoch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frozen_v3, "EXECUTION_ROOT", tmp_path.as_posix())
    ledger_dir = tmp_path / "journal"
    arm_ledger = ledger.V3Ledger("M", path=ledger_dir)
    gate = epoch.PhaseGate("M")
    artifact = _valid_auth(execution_root=tmp_path.as_posix())
    validated = authz.validate_phase_p_authorization(artifact, **_expect())
    gate.authorize_p(artifact=artifact, expected_digest=epoch.authorization_digest(artifact))
    disk_root = tmp_path / "disk"
    disk_root.mkdir()
    phone = executor.PhasePExecutor(
        "M",
        authorization=artifact,
        validated_auth=validated,
        epoch_root=tmp_path / "missing-epoch",
        phase_ledger=arm_ledger,
        memory=guards.FailClosedSupervisor(cap_bytes=1 << 28, reader=lambda: [(1, 8)]),
        disk=guards.PhysicalDiskInventory(
            disk_root, scratch_cap=1 << 20, final_cap=1 << 20, combined_cap=1 << 21
        ),
        runtime=guards.ActiveRuntime(),
        gate=gate,
        transport_client=executor.FakeTransport({GOOD_URL: _resp()}),
    )
    with pytest.raises((epoch.EpochError, OSError)):
        phone.run_phase_p([_op(GOOD_URL)])


def test_executor_refuses_without_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    ctx["executor"].validated_auth = {}
    with pytest.raises(executor.ExecutorError, match="authorization"):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])


def test_executor_refuses_without_operator_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    ctx["executor"].validated_auth = {"authorization_digest": "a" * 64}
    with pytest.raises(executor.ExecutorError, match="operator approval"):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])


def test_executor_cannot_bypass_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    before = ctx["ledger"].snapshot()["counters"]["requests"]
    ctx["executor"].transport_client.fetch(GOOD_URL)  # raw call: ledger untouched
    assert ctx["ledger"].snapshot()["counters"]["requests"] == before
    ctx["executor"].run_phase_p([_op(GOOD_URL)])
    assert ctx["ledger"].snapshot()["counters"]["requests"] == before + 1


def test_executor_cannot_bypass_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    ctx["executor"].memory = guards.FailClosedSupervisor(cap_bytes=1, reader=lambda: [(1, 999)])
    with pytest.raises(guards.GuardError):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])
    assert ctx["executor"].transport_client.calls == []


def test_executor_no_t_text_in_p(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "T", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    with pytest.raises(executor.ExecutorError, match="cannot fetch"):
        ctx["executor"].run_phase_p([_op(GOOD_URL, kind="text")])
    assert ctx["executor"].transport_client.calls == []


def test_executor_no_m_data_in_p(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp()}, monkeypatch=monkeypatch)
    with pytest.raises(executor.ExecutorError, match="cannot fetch"):
        ctx["executor"].run_phase_p([_op(GOOD_URL, kind="data")])
    assert ctx["executor"].transport_client.calls == []


def test_executor_identity_mismatch_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _harness(tmp_path, "M", routes={GOOD_URL: _resp(etag='"wrong"')}, monkeypatch=monkeypatch)
    with pytest.raises(executor.ExecutorError, match="ETag mismatch"):
        ctx["executor"].run_phase_p([_op(GOOD_URL)])
    assert ctx["gate"].state == "INCOMPLETE"  # stops, never continues to D


# --------------------------------------------------------------------------
# READINESS regressions.
# --------------------------------------------------------------------------


def test_readiness_derived_false_when_broken() -> None:
    review = readiness.derive_arm_readiness(
        {
            name: (
                lambda n=name: (False, f"{n} broken") if n == "disk_schedule_ok" else (True, "ok")
            )
            for name in readiness.DERIVED_ITEMS
        }
    )
    assert review["verdict"] == "BLOCKED"
    assert review["failed"] == ["disk_schedule_ok"]
    assert review["items"]["authorization"] == "NONE"


def test_readiness_no_hardcoded_true() -> None:
    import inspect

    source = inspect.getsource(__import__("xlm.data.evidence_v3.readiness", fromlist=["x"]))
    assert source.count('"scientific_identity_ok": True') == 0
    assert "derive_arm_readiness" in source


# --------------------------------------------------------------------------
# Phase-P arithmetic regressions.
# --------------------------------------------------------------------------


def test_m_phase_p_accounting_exact() -> None:
    freeze = _freeze()
    accounting = schedules.m_phase_p_accounting(freeze["M_prospective_files"])
    assert accounting["P_logical"] == 16
    assert accounting["P_observed_physical"] == 24
    assert accounting["P_cold_no_retry"] == 40
    assert accounting["D_identity_allocation"] == 32
    assert accounting["controls_total"] == 72 <= 80
    assert accounting["P_arm_capacity_preserving_D"] == 48
    assert accounting["P_spare_preserving_D"] == 8
    assert accounting["P_payload_bytes"] == 1398416
    assert accounting["footer_remaining_before_redirect_error_retry"] == 32156016
    assert accounting["minimum_per_file_footer_headroom"] == 3991024


def test_t_phase_p_accounting_exact() -> None:
    accounting = schedules.t_phase_p_accounting()
    assert accounting["P_logical"] == 24
    assert accounting["P_observed_physical"] == 32
    assert accounting["P_cold_no_retry"] == 48
    assert accounting["D_identity_allocation"] == 32
    assert accounting["controls_combined"] == 80
    assert "not fabricated" in accounting["footer_headroom"]


# --------------------------------------------------------------------------
# Environment identity regressions.
# --------------------------------------------------------------------------


def test_env_identity_git_blob_method() -> None:
    identity = envidentity.collect_environment_identity(ROOT)
    assert identity["python_version"] == "3.12.13"
    assert identity["python_executable_class"] in ("project-venv", "conda-env", "system-python")
    assert identity["uv_version"].startswith("uv ")
    assert len(identity["uv_lock_blob_sha"]) == 40
    assert identity["pyarrow_version"] == "25.0.1"
    assert identity["torch_version"].startswith("2.14.0")
    assert identity["cpu_cuda_build"].endswith("+cpu")
    rep = envidentity.checkout_representation(ROOT, [ROOT / "pyproject.toml"])
    assert "core_autocrlf" in rep
    assert rep["working_tree_sha256"]["pyproject.toml"]


def test_no_cli_boolean_bypass_flags() -> None:
    import subprocess as _sp

    out = _sp.check_output(
        [
            "F:/Project/xlm-data-ultrax/.venv/Scripts/python.exe",
            "scripts/evidence_v3.py",
            "prepare-epoch",
            "--help",
        ],
        cwd=str(ROOT),
    ).decode()
    assert "--yes" not in out and "--force" not in out


def test_frozen_identities_unchanged() -> None:
    assert frozen_v3.PROTOCOL_SHA256 == (
        "c191aa49a7e35349e27eb077c7105ead1a15a7edbbb28ef0bc78f157b219fd79"
    )
    assert frozen_v3.FREEZE_DIGEST == (
        "aa977972883af209afffebae221683658b2637281ae8e72741150750534fd834"
    )
    assert frozen_v3.EPOCH_ID == "essential-web-evidence-v3.0:essential-web:epoch-0001"
    assert frozen_v3.SCIENTIFIC_NAMESPACE == "essential-web-evidence-v2.0"
    assert frozen_v3.SELECTION_DIGEST == (
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    )
    assert frozen_v3.SOURCE_REVISION == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"


def test_child_artifact_digest_json_serializable() -> None:
    manifest = json.loads(
        (
            ROOT
            / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD"
            / "artifact_manifest.json"
        ).read_bytes()
    )
    assert manifest["digest"]
