"""Integrated end-to-end synthetic Phase-P experiment (mandatory evidence).

authorize -> approve -> genesis -> execute exact plan operations -> REAL
process death mid-body (``os._exit`` in a subprocess) -> resume in a new
process with the real psutil process-tree sampler -> finish -> seal P.
Then adversarial variants, each of which must fail closed while
preserving every consumed resource. No network; disposable roots only.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evidence_v3_support import REPO, block_network, build, execute, genesis, inspect
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import executor, frozen_v3, netpolicy, synthetic, transport
from xlm.data.evidence_v3.synthetic import FakeResponse


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


_RUNNER = r"""
import json, os, sys
from pathlib import Path
from xlm.data.evidence_v3 import executor, synthetic
repo, work, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ep = synthetic.reload_epoch(repo, work, use_real_sampler=True)
log = work / "calls.log"
def record(index, request):
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(f"{os.getpid()} {request.host} {request.range_header}\n")
ep.transport.before_open = record
ep.transport.clock_advance = None
def tick(index, request, response):
    ep.clock.advance(1.0)
    if mode == "crash" and index == 2:
        def die(pos):
            if pos >= 1:
                os._exit(137)
        response.chunk_limit = 1
        response.on_chunk = die
    return response
ep.transport.rule = tick
result = executor.execute_phase_p(**ep.paths(), harness=ep.harness())
print("RESULT " + json.dumps(result))
"""


def _run_process(work: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, str(REPO), str(work), mode],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env={**os.environ, "OMP_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"},
    )


def _journal(root: Path) -> list[dict[str, Any]]:
    raw = (root / "state/journal.jsonl").read_bytes()
    return [canonical.loads_bytes_strict(line) for line in raw.splitlines()]


def _allowed_ranges(ep: synthetic.SyntheticEpoch) -> set[tuple[int, int]]:
    allowed: set[tuple[int, int]] = set()
    for f in ep.m_files:
        allowed |= {(0, 3), (f.length - 8 - f.footer_length, f.length - 1)}
    for f in ep.t_files:
        allowed |= {
            (0, 3),
            (f.length - 8, f.length - 1),
            (f.length - 8 - f.footer_length, f.length - 9),
        }
    return allowed


def _assert_conserved(state: dict[str, Any]) -> None:
    for arm in ("M", "T"):
        mine = [a for a in state["attempts"] if a["arm"] == arm]
        cell = state["arms"][arm]
        assert cell["requests"] == len(mine)
        settled = [a for a in mine if a["charged"] is not None]
        assert cell["body_charged"] == sum(a["charged"] for a in settled)
        assert all(a["charged"] >= a["body_total"] for a in settled)
        assert cell["body_held"] == sum(a["reservation"] for a in mine if a["charged"] is None)


def test_integrated_phase_p_across_real_process_death(tmp_path: Path) -> None:
    work = tmp_path / "epoch"
    ep = build(work)
    log = work / "calls.log"

    def record(_index: int, request: transport.TransportRequest) -> None:
        with open(log, "a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()} {request.host} {request.range_header}\n")

    ep.transport.before_open = record

    def tick(_i: int, _r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
        ep.clock.advance(1.0)
        return resp

    ep.transport.rule = tick

    # authorize + approve (offline validation of both artifacts)
    checked = executor.check_authorization(**ep.paths(), harness=ep.harness())
    assert checked["mode"] == "SYNTHETIC"
    receipt = genesis(ep)
    start_sha = hashlib.sha256((ep.root / "epoch_start.json").read_bytes()).hexdigest()
    assert receipt["epoch_start_sha256"] == start_sha

    # session 1 (this process): three plan operations, then a sealed quiescent pause
    first = execute(ep, max_operations=3)
    assert first["executed_operations"] == 3 and first["stopped_early"]
    staged_before = {
        p.relative_to(ep.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ep.root / "phase_p").rglob("*.bin")
    }
    assert len(staged_before) == 3

    # session 2 (new process): real process death mid-body after 1 journalled byte
    crashed = _run_process(work, "crash")
    assert crashed.returncode == 137, crashed.stderr[-2000:]
    mid = inspect(ep)
    assert mid["open_session"] is not None and mid["open_attempt"] is not None
    open_attempt = [a for a in mid["attempts"] if a["charged"] is None][0]
    assert open_attempt["body_total"] == 1

    # session 3 (new process, real psutil sampler): conserve, resume, finish, seal
    resumed = _run_process(work, "resume")
    assert resumed.returncode == 0, resumed.stderr[-3000:]
    final = inspect(ep)
    assert final["arms"]["M"]["phase"] == final["arms"]["T"]["phase"] == "P_COMPLETE_SEALED"
    assert final["open_session"] is None and final["open_attempt"] is None
    _assert_conserved(final)

    # request counters exact: every physical request in every process is journalled
    calls = log.read_text(encoding="utf-8").splitlines()
    assert final["arms"]["M"]["requests"] + final["arms"]["T"]["requests"] == len(calls)
    assert len({line.split()[0] for line in calls}) == 3, "three distinct processes issued requests"

    # the crashed attempt is charged its full reservation, never refunded
    conserved = [a for a in final["attempts"] if a["attempt_id"] == open_attempt["attempt_id"]][0]
    assert conserved["outcome"] == "CRASH_RESERVED"
    assert conserved["charged"] == conserved["reservation"]

    # runtime conserved: every issued request advanced 1 s; crash hold (30 s) charged
    total_time = final["arms"]["M"]["time_ns"] + final["arms"]["T"]["time_ns"]
    assert total_time >= (len(calls) - 1) * 1_000_000_000 + 30_000_000_000

    # disk inventory conserved: session-1 staged bytes untouched; physical == inventory
    for rel, sha in staged_before.items():
        assert hashlib.sha256((ep.root / rel).read_bytes()).hexdigest() == sha
        assert rel in final["disk"]
    physical = {p.relative_to(ep.root).as_posix() for p in ep.root.rglob("*") if p.is_file()}
    assert physical <= set(final["disk"])

    records = _journal(ep.root)
    # no duplicate operation: each plan op completed exactly once, in plan order
    completions = [
        (r["body"]["op_complete"]["arm"], r["body"]["op_complete"]["op_index"])
        for r in records
        if r["type"] == "DISK_RESERVE" and r["body"]["op_complete"] is not None
    ]
    assert completions == [("M", i) for i in range(4)] + [("T", i) for i in range(6)]
    # same authorization + epoch: one genesis, epoch_start unchanged
    assert sum(r["type"] == "GENESIS" for r in records) == 1
    assert hashlib.sha256((ep.root / "epoch_start.json").read_bytes()).hexdigest() == start_sha
    assert sum(r["type"] == "SESSION_OPEN" for r in records) == 3
    assert any(r["type"] == "CRASH_ATTEMPT" for r in records)
    assert any(r["type"] == "CRASH_TIME" for r in records)
    # exact plan only
    allowed = _allowed_ranges(ep)
    for r in records:
        if r["type"] == "ATTEMPT_RESERVE":
            assert tuple(r["body"]["range"]) in allowed
            assert r["body"]["host"] in frozen_v3.ALLOWED_HOSTS
    # sealed results exist and bind the plan + authorization
    for arm in ("m", "t"):
        result = canonical.loads_bytes_strict((ep.root / f"phase_p/{arm}/result.json").read_bytes())
        assert result["authorization_digest"] == checked["authorization_digest"]
        assert canonical.self_digest(result) == result["digest"]
    assert "RESULT" in resumed.stdout
    # sealed phase cannot be re-run
    again = execute(ep)
    assert again["executed_operations"] == 0


# --------------------------------------------------------------------------
# Adversarial variants: each fails closed while preserving resources
# --------------------------------------------------------------------------


def _variant(ep: synthetic.SyntheticEpoch, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    f0 = ep.m_files[0]
    if name == "bad_redirect":
        ep.transport.overrides[0] = lambda _r, _x: FakeResponse(
            302, {"location": "https://huggingface.co.evil.example/x"}, b"moved"
        )
    elif name == "body_too_large":
        ep.transport.overrides[1] = lambda _r, resp: FakeResponse(
            206, resp.headers, b"P" * 5_000_000
        )
    elif name == "timeout":

        def slow(_i: int, _r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
            if resp.status == 206:
                resp.on_chunk = lambda _pos: ep.clock.advance(31)
            return resp

        ep.transport.rule = slow
    elif name == "wrong_etag":
        ep.transport.overrides[1] = lambda _r, resp: FakeResponse(
            206, {**resp.headers, "etag": '"' + "0" * 64 + '"'}, resp.body
        )
    elif name == "wrong_range":
        ep.transport.overrides[1] = lambda _r, _resp: FakeResponse(
            206, {"content-range": f"bytes 0-4/{f0.length}", "etag": f0.etag}, f0.content[:5]
        )
    elif name == "memory_breach":
        pass  # configured through the reader in the test body
    elif name == "disk_breach":

        def plant(index: int, _r: transport.TransportRequest) -> None:
            if index == 2:
                (ep.root / "phase_p" / "m" / "stray.bin").write_bytes(b"x")

        ep.transport.before_open = plant
    elif name == "journal_crash":
        calls = {"n": 0}
        real_fsync = os.fsync

        def flaky(fd: int) -> None:
            calls["n"] += 1
            if calls["n"] == 12:
                raise OSError(5, "simulated I/O error during journal fsync")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", flaky)
    else:
        raise AssertionError(name)


VARIANTS = [
    "bad_redirect",
    "body_too_large",
    "timeout",
    "wrong_etag",
    "wrong_range",
    "memory_breach",
    "disk_breach",
    "journal_crash",
]


@pytest.mark.parametrize("name", VARIANTS)
def test_adversarial_variant_fails_closed_and_conserves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    import threading

    breach = threading.Event()

    def reader() -> int:
        return frozen_v3.MEMORY_RESIDENT_BYTES_MAX + 1 if breach.is_set() else 8 * 1024 * 1024

    ep = build(tmp_path, memory_reader=reader)
    genesis(ep)
    if name == "memory_breach":
        seen = threading.Event()

        def reader2() -> int:
            if breach.is_set():
                seen.set()
                return frozen_v3.MEMORY_RESIDENT_BYTES_MAX + 1
            return 8 * 1024 * 1024

        ep = synthetic.SyntheticEpoch(**{**ep.__dict__, "memory_reader": reader2})

        def trip(_r: transport.TransportRequest, resp: FakeResponse) -> FakeResponse:
            def on_chunk(pos: int) -> None:
                if pos >= 1:
                    breach.set()
                    assert seen.wait(10)

            resp.chunk_limit = 1
            resp.on_chunk = on_chunk
            return resp

        ep.transport.overrides[1] = trip
    _variant(ep, name, monkeypatch)
    with pytest.raises(executor.PhasePError):
        execute(ep)
    monkeypatch.undo()
    block_network(monkeypatch)
    before = inspect(ep)
    issued = len(ep.transport.calls)
    assert before["arms"]["M"]["requests"] >= issued
    if name == "journal_crash":
        assert before["open_session"] is not None
        with pytest.raises(executor.PhasePError):
            # recovery conserves; the arm then continues or stops, never refunds
            ep.transport.overrides.clear()
            ep.transport.overrides[len(ep.transport.calls)] = lambda _r, _x: FakeResponse(
                302, {"location": "http://refused.example/"}, b""
            )
            execute(ep)
    state = inspect(ep)
    _assert_conserved(state)
    assert state["arms"]["M"]["phase"] == "P_INCOMPLETE"
    assert state["arms"]["T"]["phase"] == "NOT_STARTED"
    assert state["arms"]["M"]["requests"] >= before["arms"]["M"]["requests"]
    assert state["arms"]["M"]["body_charged"] >= before["arms"]["M"]["body_charged"]
    calls_before_rerun = len(ep.transport.calls)
    # No automatic rerun after a terminal stop. Depending on the variant the
    # refusal comes at session open (breach still present) or from the
    # INCOMPLETE arm state; either way no new physical request is issued.
    with pytest.raises(executor.PhasePError, match="INCOMPLETE|unknown physical|memory"):
        execute(ep)
    assert len(ep.transport.calls) == calls_before_rerun
    assert inspect(ep)["arms"]["M"]["requests"] == state["arms"]["M"]["requests"]
    for record in _journal(ep.root):
        if record["type"] == "ATTEMPT_RESERVE":
            assert tuple(record["body"]["range"]) in _allowed_ranges(ep)
    _ = netpolicy
