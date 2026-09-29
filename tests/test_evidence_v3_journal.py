"""Adversarial tests: exclusive genesis and the authoritative journal.

All epochs are synthetic on disposable temporary roots.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from evidence_v3_support import block_network, build, execute, genesis, inspect
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import executor, journal, synthetic


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _journal_records(ep: synthetic.SyntheticEpoch) -> list[dict[str, Any]]:
    raw = (ep.root / "state/journal.jsonl").read_bytes()
    return [canonical.loads_bytes_strict(line) for line in raw.splitlines()]


# --------------------------------------------------------------------------
# Genesis
# --------------------------------------------------------------------------


def test_no_network_before_genesis(tmp_path: Path) -> None:
    ep = build(tmp_path)
    with pytest.raises(executor.PhasePError, match="execution root refused|no epoch genesis"):
        execute(ep)
    assert ep.transport.calls == []


def test_eight_concurrent_genesis_attempts_one_winner(tmp_path: Path) -> None:
    ep = build(tmp_path)
    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def attempt() -> None:
        barrier.wait()
        try:
            genesis(ep)
            outcome = "won"
        except executor.PhasePError:
            outcome = "refused"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    assert sorted(results) == ["refused"] * 7 + ["won"]
    assert execute(ep, max_operations=0)["executed_operations"] == 0


def test_second_genesis_refused_even_after_restart(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    with pytest.raises(executor.PhasePError, match="not empty|already claimed"):
        genesis(ep)


def test_dirty_root_refused(tmp_path: Path) -> None:
    ep = build(tmp_path)
    ep.root.mkdir()
    (ep.root / "leftover.bin").write_bytes(b"x")
    with pytest.raises(executor.PhasePError, match="not empty"):
        genesis(ep)


def test_crash_between_claim_and_epoch_start_blocks(tmp_path: Path) -> None:
    ep = build(tmp_path)
    ep.root.mkdir()
    (ep.root / "genesis.claim").write_bytes(b"{}")
    with pytest.raises(executor.PhasePError):
        genesis(ep)
    with pytest.raises(executor.PhasePError):
        execute(ep)
    assert ep.transport.calls == []


def test_claim_and_epoch_bound_to_physical_directory(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    moved = tmp_path / "moved-away"
    os.rename(ep.root, moved)
    shutil.copytree(moved, ep.root)  # same path, same bytes, different directory identity
    with pytest.raises(executor.PhasePError, match="root_identity|another physical directory"):
        execute(ep)
    assert ep.transport.calls == []


def _reseal_epoch_start(ep: synthetic.SyntheticEpoch, mutate: Any) -> None:
    path = ep.root / "epoch_start.json"
    body = canonical.loads_bytes_strict(path.read_bytes())
    mutate(body)
    body["digest"] = canonical.self_digest(body)
    os.chmod(path, 0o666)
    path.write_bytes(canonical.canonical_bytes(body))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.pop("environment_identity"),
        lambda b: b.pop("initial_ledgers"),
        lambda b: b.update(extra_field=1),
        lambda b: b["initial_ledgers"]["M"].update(requests=0.0),
        lambda b: b.update(m_phase_p_plan_digest="0" * 64),
        lambda b: b.update(authorization_digest="1" * 64),
        lambda b: b.update(operator_approval_digest="2" * 64),
        lambda b: b.update(child_manifest_digest="3" * 64),
        lambda b: b.update(implementation_commit="b" * 40),
        lambda b: b["code_hashes"].update({"src/xlm/extra.py": "4" * 64}),
        lambda b: b["resource_caps"]["M"].update(footer_requests_total=81),
        lambda b: b["supervision_identity"].update(memory_cap_bytes=1 << 40),
        lambda b: b["runtime_baseline"].update(active_ns={"M": 5, "T": 0}),
        lambda b: b.update(network_events_before_genesis=3),
        lambda b: b.update(genesis_utc="not-a-time"),
        lambda b: b["p_ceilings"]["M"]["arm"].update(requests=49),
        lambda b: b["initial_inventory"].pop("genesis.claim"),
    ],
)
def test_resealed_incomplete_or_altered_epoch_start_refused(tmp_path: Path, mutate: Any) -> None:
    ep = build(tmp_path)
    genesis(ep)
    _reseal_epoch_start(ep, mutate)
    with pytest.raises(executor.PhasePError, match="epoch_start"):
        execute(ep)
    assert ep.transport.calls == []


# --------------------------------------------------------------------------
# Journal: restart continuation, crash conservation, rollback detection
# --------------------------------------------------------------------------


def test_repeated_load_update_reload_cycles_are_exact(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    seen = []
    for _ in range(4):
        execute(ep, max_operations=1)
        state = inspect(ep)
        total = state["arms"]["M"]["requests"] + state["arms"]["T"]["requests"]
        seen.append(total)
        assert total == len(ep.transport.calls), "every physical request is durably counted"
        assert state["open_session"] is None
    assert seen == sorted(seen) and len(set(seen)) == 4
    records = _journal_records(ep)
    assert [r["seq"] for r in records] == list(range(len(records)))
    assert sum(1 for r in records if r["type"] == "SESSION_OPEN") == 4


def test_crash_mid_body_conserves_reservation_and_time(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)

    def crash(_: Any, response: synthetic.FakeResponse) -> synthetic.FakeResponse:
        response.chunk_limit = 2
        response.crash_after_bytes = 2
        return response

    ep.transport.overrides[1] = crash  # the signed-target 206 for M file 0 bytes 0-3
    with pytest.raises(synthetic.SimulatedCrash):
        execute(ep)
    before = inspect(ep)
    assert before["open_session"] is not None and before["open_attempt"] is not None
    open_attempt = [a for a in before["attempts"] if a["outcome"] is None][0]
    assert open_attempt["body_total"] == 2
    execute(ep, max_operations=0)
    after = inspect(ep)
    conserved = [a for a in after["attempts"] if a["attempt_id"] == open_attempt["attempt_id"]][0]
    assert conserved["outcome"] == "CRASH_RESERVED"
    assert conserved["charged"] == conserved["reservation"] >= 65537
    assert after["arms"]["M"]["requests"] == before["arms"]["M"]["requests"] == 2
    assert after["arms"]["M"]["time_ns"] >= 30_000_000_000, "open request hold charged"
    assert after["arms"]["M"]["body_held"] == 0


def test_crash_state_never_becomes_less_conservative(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    ep.transport.overrides[0] = lambda _r, resp: synthetic.FakeResponse(
        302, resp.headers, resp.body, crash_after_bytes=0
    )
    with pytest.raises(synthetic.SimulatedCrash):
        execute(ep)
    execute(ep, max_operations=0)
    first = inspect(ep)["arms"]["M"]
    execute(ep, max_operations=0)
    execute(ep, max_operations=0)
    again = inspect(ep)["arms"]["M"]
    for key in ("requests", "body_charged", "time_ns"):
        assert again[key] >= first[key]
    assert first["body_charged"] >= 65537


def test_journal_tail_truncation_refused(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    execute(ep, max_operations=2)
    path = ep.root / "state/journal.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:-3]))
    with pytest.raises(executor.PhasePError, match="truncated|rolled back"):
        execute(ep)


def test_journal_torn_tail_blocks(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    execute(ep, max_operations=1)
    path = ep.root / "state/journal.jsonl"
    path.write_bytes(path.read_bytes() + b'{"seq":')
    with pytest.raises(executor.PhasePError, match="torn"):
        execute(ep)


def test_journal_corruption_and_reordering_refused(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    execute(ep, max_operations=1)
    path = ep.root / "state/journal.jsonl"
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    tampered = lines[3].replace(b'"requests"', b'"requestz"') if b'"requests"' in lines[3] else None
    record = canonical.loads_bytes_strict(lines[2])
    record["body"]["x"] = 1
    lines[2] = canonical.canonical_bytes(record) + b"\n"
    path.write_bytes(b"".join(lines))
    with pytest.raises(executor.PhasePError, match="digest|chain"):
        execute(ep)
    swapped = original.splitlines(keepends=True)
    swapped[2], swapped[3] = swapped[3], swapped[2]
    path.write_bytes(b"".join(swapped))
    with pytest.raises(executor.PhasePError, match="chain"):
        execute(ep)
    _ = tampered


def test_journal_from_another_epoch_start_refused(tmp_path: Path) -> None:
    a = build(tmp_path / "a")
    genesis(a)
    execute(a, max_operations=1)
    b = build(tmp_path / "b")
    genesis(b)
    shutil.copyfile(a.root / "state/journal.jsonl", b.root / "state/journal.jsonl")
    shutil.copyfile(a.root / "state/journal.head", b.root / "state/journal.head")
    with pytest.raises(executor.PhasePError, match="journal"):
        execute(b)


def test_every_attempt_keeps_footer_category_and_plan_identity(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    execute(ep, max_operations=2)
    execute(ep)
    reserves = [r["body"] for r in _journal_records(ep) if r["type"] == "ATTEMPT_RESERVE"]
    assert reserves and all(r["category"] == "footer" for r in reserves)
    assert all(r["reservation"] >= 65537 for r in reserves)
    assert inspect(ep)["arms"]["T"]["phase"] == "P_COMPLETE_SEALED"


def test_reducer_rejects_replayed_conflicts() -> None:
    body = {
        "epoch_start_digest": "0" * 64,
        "initial_inventory": {},
        "plans": {},
        "disk_caps": {
            "M": {"scratch": 1, "final": 1, "combined": 1},
            "T": {"scratch": 1, "final": 1, "combined": 1},
        },
    }
    state = journal.new_state(body, "e")
    state.apply({"seq": 0, "type": "GENESIS", "body": body})
    with pytest.raises(journal.StateError):
        state.apply({"seq": 1, "type": "GENESIS", "body": body})
    with pytest.raises(journal.StateError):
        state.apply({"seq": 1, "type": "TIME", "body": {"arm": "M"}})
