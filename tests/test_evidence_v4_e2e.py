"""Evidence-v4 end-to-end SYNTHETIC Phase P with a real process death, then adversaries.

Scenario (authored fixture, 2 M + 2 T files, 10 logical operations):
one 3-transition redirect chain, one retryable 503, a separate process that
dies with ``os._exit`` in the middle of a >1 MiB T footer body, then a
restart in this process that reconciles the interrupted attempt and resumes
to COMPLETE. No network, no real source, no frozen root.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evidence_v4_support import (
    REDIRECT_BODY,
    SIGNED_PREFIX,
    FakeClock,
    FakeResponse,
    Fixture,
    Rule,
    SimulatedCrash,
    SyntheticTransport,
    at,
    block_network,
    build_fixture,
    redirect,
    run,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_p, state

SUPPORT = Path(__file__).resolve().parent / "evidence_v4_support.py"
CRASH_AT = 1310720
BUSY = b"synthetic busy"


def _rows(root: Path, sql: str) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def _range_bytes(fx: Fixture, op_id: str) -> int:
    op = fx.op(op_id)
    if op.range is not None:
        return op.range[1] - op.range[0] + 1
    return int.from_bytes(fx.data[op.source_file.file][-8:-4], "little")


def test_synthetic_phase_p_end_to_end_with_process_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_fixture(tmp_path / "fixture", t_pad=700000)
    root = tmp_path / "root"
    assert _range_bytes(fx, "T-01-footer") > frozen.FLUSH_INTERVAL_BYTES + CRASH_AT - 1048576
    env = dict(os.environ, OMP_NUM_THREADS="1", TOKENIZERS_PARALLELISM="false")
    crashed = subprocess.run(
        [sys.executable, str(SUPPORT), "crash-run", str(tmp_path / "fixture"), str(root)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert crashed.returncode == 17, crashed.stderr[-2000:]

    # The dead process left exactly one in-progress attempt with a durable interval count.
    before = _rows(root, "SELECT * FROM attempts ORDER BY attempt_id")
    (live,) = [a for a in before if a["outcome"] == state.IN_PROGRESS]
    assert live["op_id"] == "T-01-footer" and live["try_number"] == 1 and live["hop"] == 1
    assert live["response_bytes"] == frozen.FLUSH_INTERVAL_BYTES
    assert (root / live["temp_path"]).stat().st_size == CRASH_AT
    assert _rows(root, "SELECT status FROM run") == [{"status": "RUNNING"}]
    assert not (root / phase_p.RECEIPT).exists()

    # Restart in a new process image (this one), with the network blocked.
    block_network(monkeypatch)
    resumed = SyntheticTransport(fx)
    result = run(root, fx, resumed, FakeClock())
    assert result.status == "COMPLETE" and result.run_status == "COMPLETE"
    assert [(resumed.file_of(c), c.host) for c in resumed.calls] == [
        (fx.op("T-01-footer").source_file.file, "huggingface.co"),
        (fx.op("T-01-footer").source_file.file, "cas-bridge.xethub.hf.co"),
    ]

    table = _rows(root, "SELECT * FROM attempts ORDER BY attempt_id")
    per_op: dict[str, list[str]] = {}
    for a in table:
        per_op.setdefault(a["op_id"], []).append(a["outcome"])
    R, S = state.REDIRECT, state.SUCCESS
    assert per_op == {
        "M-00-head": [R, R, R, S],
        "M-00-footer": [R, S],
        "M-01-head": [R, S],
        "M-01-footer": [state.HTTP_ERROR, R, S],
        "T-00-head": [R, S],
        "T-00-trailer": [R, S],
        "T-00-footer": [R, S],
        "T-01-head": [R, S],
        "T-01-trailer": [R, S],
        "T-01-footer": [R, state.INTERRUPTED, R, S],
    }
    assert len(table) == 25
    assert [a["hop"] for a in table if a["op_id"] == "M-00-head"] == [0, 1, 2, 3]
    assert [a["try_number"] for a in table if a["op_id"] == "M-01-footer"] == [1, 2, 2]
    assert [a["try_number"] for a in table if a["op_id"] == "T-01-footer"] == [1, 1, 2, 2]
    assert [a["attempt_id"] for a in table] == list(range(1, 26))

    redirects = sum(1 for a in table if a["outcome"] == R)
    success_bytes = sum(_range_bytes(fx, op.op_id) for op in fx.plan.operations)
    expected_bytes = redirects * len(REDIRECT_BODY) + len(BUSY) + CRASH_AT + success_bytes
    assert sum(a["response_bytes"] for a in table) == expected_bytes
    interrupted = next(a for a in table if a["outcome"] == state.INTERRUPTED)
    assert interrupted["response_bytes"] == CRASH_AT

    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["status"] == "COMPLETE" and receipt["synthetic"] is True
    assert canonical.self_digest(receipt) == receipt["digest"]
    assert receipt["totals"]["all"]["physical_attempts"] == 25
    assert receipt["totals"]["all"]["response_body_bytes"] == expected_bytes
    assert receipt["totals"]["M"]["physical_attempts"] == 11
    assert receipt["totals"]["T"]["physical_attempts"] == 14
    assert [o["op_id"] for o in receipt["operations"]] == [o.op_id for o in fx.plan.operations]
    for entry in receipt["operations"]:
        output = entry["output"]
        raw = (root / output["retained_file"]).read_bytes()
        file = fx.op(entry["op_id"]).source_file.file
        start, end = entry["range"]
        assert raw == fx.data[file][start : end + 1]
        assert hashlib.sha256(raw).hexdigest() == output["sha256"]
    manifest = json.loads((root / phase_p.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["receipt_digest"] == receipt["digest"] and manifest["status"] == "COMPLETE"
    for rel, entry in manifest["artifacts"].items():
        raw = (root / rel).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (entry["bytes"], entry["sha256"])
    assert (root / live["temp_path"]).stat().st_size == CRASH_AT  # retained, never deleted
    assert _rows(root, "SELECT COUNT(*) AS n FROM operations WHERE status = 'COMPLETE'") == [
        {"n": 10}
    ]
    again = SyntheticTransport(fx)
    assert run(root, fx, again).status == "COMPLETE" and again.calls == []


# -- adversarial variants: every one stops safely --------------------------


def _adversary(fx: Fixture, name: str) -> tuple[list[Rule], FakeClock]:
    t = SyntheticTransport(fx)
    clock = FakeClock()
    signed = "cas-bridge.xethub.hf.co"
    key = fx.key(fx.op("M-01-head").source_file.file)

    def slow(call: Any) -> Any:
        def tick(_: int) -> None:
            clock.now += 121

        return t.ok(call, on_read=tick)

    rules = {
        "wrong-etag": [
            Rule(at(fx, "M-01-head", host=signed), lambda c: t.ok(c, headers={"ETag": '"x"'}))
        ],
        "wrong-range": [
            Rule(
                at(fx, "M-01-head", host=signed),
                lambda c: t.ok(
                    c, headers={"Content-Range": f"bytes 4-7/{len(fx.data[t.file_of(c)])}"}
                ),
            )
        ],
        "bad-redirect": [
            Rule(at(fx, "M-01-head"), lambda c: redirect("https://huggingface.co.evil.example/x"))
        ],
        "fourth-redirect": [
            Rule(at(fx, "M-01-head"), lambda c: redirect(f"{SIGNED_PREFIX}{key}?{c.index}"), 9)
        ],
        "overlarge": [
            Rule(
                at(fx, "M-01-head", host=signed),
                lambda c: t.ok(c, body=b"PAR1" * 1100000, headers={"Content-Length": None}),
            )
        ],
        "timeout": [Rule(at(fx, "M-01-head", host=signed), slow, 9)],
    }[name]
    return rules, clock


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("wrong-etag", "ETag differs"),
        ("wrong-range", "Content-Range 4-7"),
        ("bad-redirect", "not an exact allowlisted host"),
        ("fourth-redirect", "redirect transition 4"),
        ("overlarge", "exceeds 4194304"),
        ("timeout", "retries exhausted"),
    ],
)
def test_adversarial_responses_stop_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, fragment: str
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture")
    root = tmp_path / "root"
    rules, clock = _adversary(fx, name)
    result = run(root, fx, SyntheticTransport(fx, rules=rules), clock)
    _assert_safe_stop(result, root, fragment)
    assert _rows(root, "SELECT op_id FROM outputs ORDER BY op_id") == [
        {"op_id": "M-00-footer"},
        {"op_id": "M-00-head"},
    ]
    with pytest.raises(phase_p.RefusedError, match="STOPPED"):
        run(root, fx, SyntheticTransport(fx))


def _assert_safe_stop(
    result: phase_p.Result, root: Path, fragment: str, *, reconciled: bool = True
) -> None:
    assert result.status == "INCOMPLETE" and result.run_status == "STOPPED"
    assert result.stop_reason is not None and fragment in result.stop_reason
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["status"] == "INCOMPLETE" and receipt["layouts"] is None
    table = _rows(root, "SELECT * FROM attempts")
    assert receipt["totals"]["all"]["physical_attempts"] == len(table)
    assert receipt["totals"]["all"]["response_body_bytes"] == sum(
        a["response_bytes"] for a in table
    )
    if reconciled:  # a tampered store is refused before any reconciliation write
        assert all(a["outcome"] != state.IN_PROGRESS for a in table)
    assert all(a["response_bytes"] <= phase_p.RESPONSE_READ_LIMIT for a in table)


@pytest.mark.parametrize("target", ["temp", "output"])
def test_adversarial_corruption_stops_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture")
    root = tmp_path / "root"
    rule = Rule(at(fx, "M-00-head"), lambda c: FakeResponse(503, {}, BUSY))
    assert run(root, fx, SyntheticTransport(fx, rules=[rule])).status == "COMPLETE"
    if target == "temp":
        busy = _rows(root, "SELECT temp_path FROM attempts WHERE http_status = 503")[0]
        (root / busy["temp_path"]).write_bytes(b"X" * len(BUSY))
        fragment = "retained body was altered"
    else:
        path = root / "payload" / "T-00-footer.bin"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
        fragment = "corrupted"
    transport = SyntheticTransport(fx)
    _assert_safe_stop(run(root, fx, transport), root, fragment)
    assert transport.calls == []


def test_adversarial_operation_injection_stops_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    block_network(monkeypatch)
    fx = build_fixture(tmp_path / "fixture")
    root = tmp_path / "root"
    crash = Rule(at(fx, "T-00-head"), lambda c: SimulatedCrash("stop here"))
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash]))
    conn = sqlite3.connect(root / state.DB_NAME)
    conn.execute(
        "INSERT INTO operations VALUES ('T-01-text', 10, 'T', 1, ?, 'T_TEXT_PAGE', 4, 4194307, "
        "'PENDING')",
        (fx.op("T-01-head").source_file.file,),
    )
    conn.commit()
    conn.close()
    transport = SyntheticTransport(fx)
    _assert_safe_stop(run(root, fx, transport), root, "stored operations differ", reconciled=False)
    assert transport.calls == []
