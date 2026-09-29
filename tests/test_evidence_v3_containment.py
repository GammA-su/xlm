"""Adversarial tests: Windows-safe root containment, unified disk accounting,
and registry-owned process lifecycle for memory supervision.

Junction tests create REAL NTFS junctions with ``mklink /J`` (no privilege
needed). On Windows a failure to create a junction FAILS the test; it is
never skipped. Symlink tests may skip where the OS forbids symlinks, and a
symlink skip is never counted as junction evidence.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from evidence_v3_support import block_network, build, execute, genesis, inspect
from xlm.data.evidence_v3 import executor, fsroot, journal, memory, synthetic, transport


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("NTFS junctions exist only on Windows")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
    )
    assert completed.returncode == 0, "creating a real junction must succeed on Windows"
    st = os.lstat(link)
    assert fsroot.is_reparse(st)


# --------------------------------------------------------------------------
# Path grammar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.bin",
        "a/../../b",
        "./a",
        "/abs.bin",
        "C:/x.bin",
        "C:x.bin",
        "d:stream",
        "file.bin:ads",
        "a\\b",
        "NUL",
        "nul",
        "con.txt",
        "com1",
        "trailing.",
        "Upper.bin",
        "",
        "a//b",
        "a/b/c/d/e/f/g",
    ],
)
def test_relative_path_grammar_refuses(rel: str) -> None:
    with pytest.raises(fsroot.ContainmentError):
        fsroot.check_rel(rel)


def test_absolute_external_path_cannot_be_written(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fs = fsroot.RootFS(fsroot.observe_root(root))
    for rel in (str(tmp_path / "outside.bin"), (tmp_path / "outside.bin").as_posix()):
        with pytest.raises(fsroot.ContainmentError):
            fs.create_exclusive(rel, b"x")
    assert not (tmp_path / "outside.bin").exists()


# --------------------------------------------------------------------------
# Real junctions / reparse points
# --------------------------------------------------------------------------


def test_real_junction_child_escape_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _junction(root / "escape", outside)
    fs = fsroot.RootFS(fsroot.observe_root(root))
    with pytest.raises(fsroot.ContainmentError, match="not a plain dir"):
        fs.create_exclusive("escape/x.bin", b"x")
    with pytest.raises(fsroot.ContainmentError):
        fs.mkdirs("escape/deeper")
    with pytest.raises(fsroot.ContainmentError, match="reparse"):
        fs.scan()
    assert list(outside.iterdir()) == []


def test_ancestor_junction_refused_at_genesis(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    _junction(tmp_path / "via-junction", real)
    ep = build(tmp_path / "work")
    moved = synthetic.SyntheticEpoch(**{**ep.__dict__, "root": tmp_path / "via-junction" / "root"})
    body = synthetic.authorization_body(
        repo=ep.repo,
        root=moved.root,
        plan_m=ep.plan_m,
        plan_t=ep.plan_t,
    )
    from evidence_v3_support import write_json

    ep.review_path.write_bytes(synthetic.authorization_review_bytes(body))
    write_json(ep.auth_path, body)
    write_json(ep.approval_path, synthetic.approval_body(body["digest"]))
    with pytest.raises(executor.PhasePError, match="reparse|junction"):
        executor.perform_genesis(**moved.paths(), harness=moved.harness())
    assert list(real.iterdir()) == []


def test_junction_planted_after_genesis_refuses_before_network(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(ep.root / "phase_p", outside)
    with pytest.raises(executor.PhasePError, match="reparse"):
        execute(ep)
    assert ep.transport.calls == []
    assert list(outside.iterdir()) == []


def test_junction_planted_mid_run_stops_without_escape(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    outside = tmp_path / "outside"
    outside.mkdir()

    def plant(index: int, _r: transport.TransportRequest) -> None:
        if index == 0:
            _junction(ep.root / "phase_p", outside)

    ep.transport.before_open = plant
    with pytest.raises(executor.PhasePError):
        execute(ep)
    assert list(outside.iterdir()) == []
    state = inspect(ep)
    assert state["arms"]["M"]["requests"] == 2
    assert state["arms"]["M"]["body_charged"] == 18 + 4


def test_symlink_escape_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted here (NOT junction evidence)")
    fs = fsroot.RootFS(fsroot.observe_root(root))
    with pytest.raises(fsroot.ContainmentError):
        fs.create_exclusive("link/x.bin", b"x")
    assert list(outside.iterdir()) == []


def test_root_replaced_by_other_directory_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fs = fsroot.RootFS(fsroot.observe_root(root))
    os.rename(root, tmp_path / "old")
    root.mkdir()
    with pytest.raises(fsroot.ContainmentError, match="identity changed"):
        fs.create_exclusive("x.bin", b"x")


# --------------------------------------------------------------------------
# Unified disk accounting
# --------------------------------------------------------------------------


def test_unknown_physical_file_stops(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    (ep.root / "state" / "stray.bin").write_bytes(b"x")
    with pytest.raises(executor.PhasePError, match="unknown physical file"):
        execute(ep)
    assert ep.transport.calls == []


def test_unknown_file_mid_run_stops_and_keeps_accounting(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)

    def plant(index: int, _r: transport.TransportRequest) -> None:
        if index == 1:
            (ep.root / "intruder.bin").write_bytes(b"zz")

    ep.transport.before_open = plant
    with pytest.raises(executor.PhasePStop):
        execute(ep)
    state = inspect(ep)
    assert state["arms"]["M"]["phase"] == "P_INCOMPLETE"
    assert "unknown physical file" in state["arms"]["M"]["incomplete_reason"]
    assert state["arms"]["M"]["requests"] == 2 and state["pending_disk"] == []


def test_inventory_survives_reload_and_later_mutation(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    execute(ep, max_operations=1)
    first = inspect(ep)["disk"]
    assert "phase_p/m/f00-identity-head-0-3.bin" in first
    execute(ep, max_operations=2)
    second = inspect(ep)["disk"]
    assert set(first) < set(second)
    physical = {
        p.relative_to(ep.root).as_posix(): p.stat().st_size
        for p in ep.root.rglob("*")
        if p.is_file()
    }
    for rel, cell in second.items():
        if rel.startswith("phase_p/"):
            assert physical[rel] == cell["size"]
            assert cell["arms"] == ["M"] and cell["category"] == "scratch"
    assert all(rel in second for rel in physical)


def test_actual_bytes_exceeding_reservation_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ep = build(tmp_path)
    genesis(ep)
    original = fsroot.RootFS.rename

    def grow_then_rename(self: fsroot.RootFS, src: str, dst: str, *, replace: bool) -> None:
        if src.endswith(".bin.tmp"):
            with open(self._resolve(src), "ab") as stream:
                stream.write(b"\0" * 252)  # 4 declared bytes become 256 on disk
        original(self, src, dst, replace=replace)

    monkeypatch.setattr(fsroot.RootFS, "rename", grow_then_rename)
    with pytest.raises(executor.PhasePError):
        execute(ep)
    monkeypatch.setattr(fsroot.RootFS, "rename", original)
    with pytest.raises(executor.PhasePError, match="unexpected bytes|BLOCKED"):
        execute(ep)


def _disk_state(
    caps: dict[str, int], inventory: dict[str, Any] | None = None
) -> journal.EpochState:
    body = {
        "epoch_start_digest": "0" * 64,
        "initial_inventory": inventory or {},
        "plans": {},
        "disk_caps": {"M": dict(caps), "T": dict(caps)},
    }
    state = journal.new_state(body, "e")
    state.apply({"seq": 0, "type": "GENESIS", "body": body})
    state.apply({"seq": 1, "type": "SESSION_OPEN", "body": {"session": "s"}})
    return state


def _reserve(state: journal.EpochState, rel: str, size: int, **extra: Any) -> None:
    body = {
        "rel": rel,
        "temp_rel": rel + ".tmp",
        "category": "scratch",
        "arms": ["M"],
        "size": size,
        "sha256": "a" * 64,
        "replaces": False,
        "op_complete": None,
        **extra,
    }
    state.apply({"seq": state.seq + 1, "type": "DISK_RESERVE", "body": body})


def test_disk_reservation_refuses_over_subcap_and_combined() -> None:
    state = _disk_state({"scratch": 100, "final": 50, "combined": 120})
    with pytest.raises(journal.StateError, match="scratch subcap"):
        _reserve(state, "a.bin", 256)
    with pytest.raises(journal.StateError, match="final subcap"):
        _reserve(state, "b.bin", 60, category="final")
    _reserve(state, "c.bin", 90)
    state.apply(
        {
            "seq": state.seq + 1,
            "type": "DISK_COMMIT",
            "body": {"rel": "c.bin", "size": 90, "sha256": "a" * 64},
        }
    )
    with pytest.raises(journal.StateError, match="combined"):
        _reserve(state, "d.bin", 40, category="final")


def test_replacement_counts_old_plus_new_peak() -> None:
    state = _disk_state({"scratch": 150, "final": 50, "combined": 200})
    _reserve(state, "x.bin", 100)
    state.apply(
        {
            "seq": state.seq + 1,
            "type": "DISK_COMMIT",
            "body": {"rel": "x.bin", "size": 100, "sha256": "a" * 64},
        }
    )
    with pytest.raises(journal.StateError, match="scratch subcap"):
        _reserve(state, "x.bin", 100, replaces=True)  # final 100 fits, peak 200 does not


def test_control_files_count_against_both_arms() -> None:
    control = {
        "state/journal.jsonl": {
            "size": 90,
            "sha256": None,
            "category": "scratch",
            "arms": ["M", "T"],
            "control_allowance": True,
        }
    }
    state = _disk_state({"scratch": 100, "final": 50, "combined": 150}, control)
    with pytest.raises(journal.StateError):
        _reserve(state, "m.bin", 20, arms=["M"])
    with pytest.raises(journal.StateError):
        _reserve(state, "t.bin", 20, arms=["T"])
    assert state.occupancy("M") == state.occupancy("T") == 90


def test_real_epoch_reserves_control_allowances_at_genesis(tmp_path: Path) -> None:
    ep = build(tmp_path)
    genesis(ep)
    disk = inspect(ep)["disk"]
    assert disk["state/journal.jsonl"]["size"] == journal.JOURNAL_ALLOWANCE
    assert disk["state/journal.jsonl"]["arms"] == ["M", "T"]
    for rel in ("genesis.claim", "epoch_start.json", "state/lock"):
        assert rel in disk and disk[rel]["arms"] == ["M", "T"]


# --------------------------------------------------------------------------
# Registry-owned process lifecycle
# --------------------------------------------------------------------------


def test_registry_has_no_boolean_or_caller_proof_release() -> None:
    registry = memory.OwnedProcessRegistry()
    assert not hasattr(registry, "release")
    assert not hasattr(memory, "ExitProof")
    assert set(vars(memory.OwnedProcessRegistry)) >= {"register", "reap", "owned"}


def test_live_child_cannot_be_released_and_reaps_after_exit() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        registry = memory.OwnedProcessRegistry()
        handle = registry.register(child.pid)
        with pytest.raises(memory.MemoryGuardError, match="still running"):
            registry.reap(handle)
        forged = memory.Registration(token="0" * 32, pid=child.pid)
        with pytest.raises(memory.MemoryGuardError, match="unknown registration"):
            registry.reap(forged)
    finally:
        child.kill()
        child.wait(timeout=30)
    registry.reap(handle)
    assert registry.owned() == ()


def test_sampler_includes_descendants(tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; x = bytes(range(256)) * 160_000; time.sleep(30)"]
    )
    try:
        time.sleep(1.5)
        sampler = memory.TreeSampler(memory.OwnedProcessRegistry())
        main_only = psutil.Process().memory_info().rss
        assert sampler() >= main_only + 30_000_000
    finally:
        child.kill()
        child.wait(timeout=30)


def test_registered_external_non_descendant_is_measured() -> None:
    if sys.platform != "win32":
        pytest.skip("DETACHED_PROCESS orphaning is the Windows method used here")
    launcher = (
        "import subprocess, sys\n"
        "p = subprocess.Popen([sys.executable, '-c', "
        "'import time; x = bytes(range(256)) * 200_000; time.sleep(60)'], "
        "creationflags=0x00000008 | 0x00000200)\n"
        "print(p.pid)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", launcher], capture_output=True, text=True, check=True
    )
    pid = int(out.stdout.strip())
    try:
        assert pid not in {c.pid for c in psutil.Process().children(recursive=True)}
        time.sleep(1.5)
        registry = memory.OwnedProcessRegistry()
        handle = registry.register(pid)
        without = memory.TreeSampler(memory.OwnedProcessRegistry())()
        with_external = memory.TreeSampler(registry)()
        assert with_external >= without + 40_000_000
    finally:
        psutil.Process(pid).kill()
        psutil.wait_procs([psutil.Process(pid)], timeout=30) if psutil.pid_exists(pid) else None
    deadline = time.time() + 30
    while psutil.pid_exists(pid) and time.time() < deadline:
        time.sleep(0.1)
    with pytest.raises(memory.MemoryGuardError, match="vanished without reap"):
        memory.TreeSampler(registry)()
    registry.reap(handle)
    memory.TreeSampler(registry)()


def test_supervisor_latches_breach_and_refuses_bad_readings() -> None:
    clock = synthetic.FakeClock()
    readings = iter([10, memory_cap_plus(), 10])
    sup = memory.Supervisor(
        reader=lambda: next(readings),
        cap_bytes=100,
        interval_s=0.5,
        monotonic_ns=clock.monotonic_ns,
    )
    sup.sample_once()
    sup.checkpoint()
    sup.sample_once()
    with pytest.raises(memory.MemoryGuardError, match="exceeds cap"):
        sup.checkpoint()
    sup.sample_once()  # a later good reading never clears a latched breach
    with pytest.raises(memory.MemoryGuardError):
        sup.checkpoint()
    bad = memory.Supervisor(
        reader=lambda: True, cap_bytes=100, interval_s=0.5, monotonic_ns=clock.monotonic_ns
    )
    bad.sample_once()
    with pytest.raises(memory.MemoryGuardError, match="non-integer"):
        bad.checkpoint()


def memory_cap_plus() -> int:
    return 101
