"""Evidence-v4 Phase-P engine: accounting, restart, caps, isolation and outputs.

All runs are SYNTHETIC: authored Parquet files, a scripted transport and
disposable roots. No network, no real source, no frozen execution root.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
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
from xlm.data.evidence_v4 import transport as tp

CANONICAL_HOST = "huggingface.co"
SIGNED_HOST = "cas-bridge.xethub.hf.co"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return build_fixture(tmp_path / "fixture")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def rows(root: Path, sql: str, *params: Any) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def attempts(root: Path, op_id: str | None = None) -> list[dict[str, Any]]:
    everything = rows(root, "SELECT * FROM attempts ORDER BY attempt_id")
    return [a for a in everything if op_id is None or a["op_id"] == op_id]


def edit(root: Path, sql: str, *params: Any) -> None:
    conn = sqlite3.connect(root / state.DB_NAME)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def crash_on(fixture: Fixture, op_id: str, host: str = CANONICAL_HOST) -> Rule:
    return Rule(at(fixture, op_id, host=host), lambda c: SimulatedCrash(op_id))


def assert_stopped(result: phase_p.Result, root: Path, fragment: str) -> None:
    assert result.status == "INCOMPLETE" and result.run_status == "STOPPED"
    assert result.stop_reason is not None and fragment in result.stop_reason
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    assert receipt["status"] == "INCOMPLETE" and receipt["layouts"] is None
    assert canonical.self_digest(receipt) == receipt["digest"]
    assert not (root / phase_p.M_LAYOUT).exists() and not (root / phase_p.T_LAYOUT).exists()
    assert receipt["totals"]["all"]["physical_attempts"] == len(attempts(root))


def slice_of(fixture: Fixture, op_id: str, start: int, end: int) -> bytes:
    return fixture.data[fixture.op(op_id).source_file.file][start : end + 1]


# -- ACCOUNTING ----------------------------------------------------------


def test_attempt_is_durable_before_the_transport_is_called(fx: Fixture, root: Path) -> None:
    transport = SyntheticTransport(fx, rules=[crash_on(fx, "M-00-head")])
    with pytest.raises(SimulatedCrash):
        run(root, fx, transport)
    (only,) = attempts(root)
    assert (only["op_id"], only["outcome"], only["hop"], only["try_number"]) == (
        "M-00-head",
        state.IN_PROGRESS,
        0,
        1,
    )
    assert (only["url_host"], only["range_start"], only["range_end"]) == (CANONICAL_HOST, 0, 3)
    assert (root / only["temp_path"]).stat().st_size == 0
    assert rows(root, "SELECT status FROM run")[0]["status"] == "RUNNING"


def test_success_and_redirect_bytes_are_recorded(fx: Fixture, root: Path) -> None:
    transport = SyntheticTransport(fx)
    result = run(root, fx, transport)
    assert result.status == "COMPLETE"
    table = attempts(root)
    assert len(table) == 20 == len(transport.calls)
    for row in table:
        if row["outcome"] == state.REDIRECT:
            assert row["http_status"] == 302 and row["url_host"] == CANONICAL_HOST
            assert row["response_bytes"] == len(REDIRECT_BODY)
            assert row["response_sha256"] == hashlib.sha256(REDIRECT_BODY).hexdigest()
        else:
            assert row["outcome"] == state.SUCCESS and row["url_host"] == SIGNED_HOST
            assert row["response_bytes"] == row["range_end"] - row["range_start"] + 1
    assert result.totals["all"]["response_body_bytes"] == sum(r["response_bytes"] for r in table)


def test_error_body_bytes_are_recorded_and_retried(fx: Fixture, root: Path) -> None:
    body = b"synthetic upstream busy"
    rule = Rule(at(fx, "M-00-head", host=CANONICAL_HOST), lambda c: FakeResponse(503, {}, body))
    sleeps: list[float] = []
    result = phase_p.run_offline(
        root, plan=fx.plan, transport=SyntheticTransport(fx, rules=[rule]), sleep=sleeps.append
    )
    assert result.status == "COMPLETE" and sleeps == [1]
    first, *rest = attempts(root, "M-00-head")
    assert (first["outcome"], first["http_status"], first["response_bytes"]) == (
        state.HTTP_ERROR,
        503,
        len(body),
    )
    assert (root / first["temp_path"]).read_bytes() == body
    assert [r["try_number"] for r in rest] == [2, 2]


def test_partial_response_bytes_are_recorded(fx: Fixture, root: Path) -> None:
    t = SyntheticTransport(fx)
    t.rules = [Rule(at(fx, "M-00-footer", host=SIGNED_HOST), lambda c: t.ok(c, fail_after=1000))]
    assert run(root, fx, t).status == "COMPLETE"
    failed = attempts(root, "M-00-footer")[1]
    assert (failed["outcome"], failed["response_bytes"]) == (state.TRANSPORT_ERROR, 1000)
    op = fx.op("M-00-footer")
    assert op.range is not None
    assert (root / failed["temp_path"]).read_bytes() == slice_of(fx, op.op_id, *op.range)[:1000]


def test_interrupted_temp_file_is_reconciled_on_restart(tmp_path: Path, root: Path) -> None:
    fixture = build_fixture(tmp_path / "fixture", t_pad=700000)
    t = SyntheticTransport(fixture)
    t.rules = [
        Rule(at(fixture, "T-01-footer", host=SIGNED_HOST), lambda c: t.ok(c, crash_after=1310720))
    ]
    with pytest.raises(SimulatedCrash):
        run(root, fixture, t)
    (crashed,) = [a for a in attempts(root) if a["outcome"] == state.IN_PROGRESS]
    assert crashed["op_id"] == "T-01-footer"
    assert crashed["response_bytes"] == frozen.FLUSH_INTERVAL_BYTES  # durable interval count
    assert (root / crashed["temp_path"]).stat().st_size == 1310720
    resumed = SyntheticTransport(fixture)
    assert run(root, fixture, resumed).status == "COMPLETE"
    after = next(a for a in attempts(root) if a["attempt_id"] == crashed["attempt_id"])
    assert (after["outcome"], after["response_bytes"]) == (state.INTERRUPTED, 1310720)
    new = [a for a in attempts(root, "T-01-footer") if a["try_number"] == 2]
    assert [a["outcome"] for a in new] == [state.REDIRECT, state.SUCCESS]
    assert all(a["attempt_id"] > crashed["attempt_id"] for a in new)


# -- NETWORK -------------------------------------------------------------


def test_every_attempt_passes_the_120_second_timeout(fx: Fixture, root: Path) -> None:
    clock = FakeClock()
    transport = SyntheticTransport(fx)
    run(root, fx, transport, clock)
    assert {c.timeout_seconds for c in transport.calls} == {120}
    assert {c.deadline for c in transport.calls} == {clock.now + 120}


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        "http://huggingface.co/x",
        "https://127.0.0.1/x",
        "https://cas-bridge.xethub.hf.co:8443/x",
    ],
)
def test_bad_redirect_stops(fx: Fixture, root: Path, location: str) -> None:
    rule = Rule(at(fx, "M-00-head", host=CANONICAL_HOST), lambda c: redirect(location))
    transport = SyntheticTransport(fx, rules=[rule])
    assert_stopped(run(root, fx, transport), root, "M-00-head")
    (row,) = attempts(root)
    assert row["outcome"] == state.POLICY_REFUSED and row["response_bytes"] == len(REDIRECT_BODY)
    assert len(transport.calls) == 1


def test_fourth_redirect_stops(fx: Fixture, root: Path) -> None:
    key = fx.key(fx.op("M-00-head").source_file.file)
    chain = Rule(
        at(fx, "M-00-head"), lambda c: redirect(f"{SIGNED_PREFIX}{key}?n={c.index}"), times=99
    )
    transport = SyntheticTransport(fx, rules=[chain])
    assert_stopped(run(root, fx, transport), root, "redirect transition 4")
    table = attempts(root)
    assert [r["hop"] for r in table] == [0, 1, 2, 3]
    assert [r["outcome"] for r in table] == [state.REDIRECT] * 3 + [state.POLICY_REFUSED]


def test_three_redirects_are_allowed(fx: Fixture, root: Path) -> None:
    key = fx.key(fx.op("M-00-head").source_file.file)
    chain = Rule(at(fx, "M-00-head"), lambda c: redirect(f"{SIGNED_PREFIX}{key}?n={c.index}"), 3)
    assert run(root, fx, SyntheticTransport(fx, rules=[chain])).status == "COMPLETE"
    assert [r["hop"] for r in attempts(root, "M-00-head")] == [0, 1, 2, 3]


def test_retry_maximum_is_enforced(fx: Fixture, root: Path) -> None:
    busy = Rule(at(fx, "M-00-head"), lambda c: FakeResponse(503, {}, b"busy"), times=99)
    sleeps: list[float] = []
    transport = SyntheticTransport(fx, rules=[busy])
    result = phase_p.run_offline(root, plan=fx.plan, transport=transport, sleep=sleeps.append)
    assert_stopped(result, root, "retries exhausted after 3 tries")
    assert [r["try_number"] for r in attempts(root)] == [1, 2, 3]
    assert sleeps == [1, 2] and len(transport.calls) == 3


def test_transport_timeout_is_retryable_then_stops(fx: Fixture, root: Path) -> None:
    clock = FakeClock()

    def slow(call: Any) -> FakeResponse:
        def advance(_: int) -> None:
            clock.now += 121

        return SyntheticTransport(fx).ok(call, on_read=advance)

    rule = Rule(at(fx, "M-00-head", host=SIGNED_HOST), slow, times=99)
    result = run(root, fx, SyntheticTransport(fx, rules=[rule]), clock)
    assert_stopped(result, root, "retries exhausted")
    outcomes = [r["outcome"] for r in attempts(root)]
    assert outcomes == [state.REDIRECT, state.TIMEOUT] * 3


def test_socket_level_transport_error_is_retryable(fx: Fixture, root: Path) -> None:
    rule = Rule(at(fx, "M-00-head"), lambda c: tp.TransportError("connection refused"))
    assert run(root, fx, SyntheticTransport(fx, rules=[rule])).status == "COMPLETE"
    first = attempts(root, "M-00-head")[0]
    assert (first["outcome"], first["response_bytes"]) == (state.TRANSPORT_ERROR, 0)


# -- IDENTITY (engine level) ---------------------------------------------


def _signed_fault(fixture: Fixture, op_id: str, **overrides: Any) -> Rule:
    t = SyntheticTransport(fixture)
    return Rule(at(fixture, op_id, host=SIGNED_HOST), lambda c: t.ok(c, **overrides))


@pytest.mark.parametrize(
    ("overrides", "outcome", "fragment"),
    [
        ({"status": 200}, state.HTTP_ERROR, "non-retryable HTTP status 200"),
        ({"status": 404}, state.HTTP_ERROR, "non-retryable HTTP status 404"),
        (
            {"headers": {"Content-Range": "bytes 1-4/8391"}},
            state.IDENTITY_MISMATCH,
            "Content-Range",
        ),
        ({"headers": {"Content-Range": "bytes 0-3/9999"}}, state.IDENTITY_MISMATCH, "total"),
        ({"headers": {"ETag": 'W/"abc"'}}, state.IDENTITY_MISMATCH, "weak"),
        ({"headers": {"ETag": '"0123"'}}, state.IDENTITY_MISMATCH, "differs"),
        ({"body": b"PAR", "headers": {"Content-Length": None}}, state.IDENTITY_MISMATCH, "exactly"),
        (
            {"body": b"PAR1PAR1", "headers": {"Content-Length": None}},
            state.IDENTITY_MISMATCH,
            "exactly",
        ),
        ({"body": b"PARX"}, state.IDENTITY_MISMATCH, "PAR1"),
    ],
)
def test_identity_mismatch_stops(
    fx: Fixture, root: Path, overrides: dict[str, Any], outcome: str, fragment: str
) -> None:
    transport = SyntheticTransport(fx, rules=[_signed_fault(fx, "M-00-head", **overrides)])
    assert_stopped(run(root, fx, transport), root, fragment)
    final = attempts(root)[-1]
    assert final["outcome"] == outcome and final["op_id"] == "M-00-head"
    assert len(transport.calls) == 2  # no retry, no other file, no refresh
    with pytest.raises(phase_p.RefusedError, match="STOPPED"):
        run(root, fx, SyntheticTransport(fx))


@pytest.mark.parametrize(
    "location",
    [
        "https://huggingface.co/datasets/synthetic/evidence-v4-fixture/resolve/"
        "0123456789abcdef0123456789abcdef01234567/data/crawl=SYN-M/train-00001.parquet",
        "https://huggingface.co/datasets/synthetic/evidence-v4-fixture/resolve/main/"
        "data/crawl=SYN-M/train-00000.parquet?revision=0123456789abcdef0123456789abcdef01234567",
    ],
)
def test_wrong_path_or_query_only_revision_stops(fx: Fixture, root: Path, location: str) -> None:
    rule = Rule(at(fx, "M-00-head", host=CANONICAL_HOST), lambda c: redirect(location))
    assert_stopped(run(root, fx, SyntheticTransport(fx, rules=[rule])), root, "not the frozen")


def test_bad_par1_trailer_stops(fx: Fixture, root: Path) -> None:
    op = fx.op("T-00-trailer")
    assert op.range is not None
    raw = slice_of(fx, op.op_id, *op.range)
    rule = _signed_fault(fx, "T-00-trailer", body=raw[:4] + b"PAR2")
    assert_stopped(run(root, fx, SyntheticTransport(fx, rules=[rule])), root, "PAR1")


# -- RESTART -------------------------------------------------------------


def test_completed_operations_are_verified_and_never_rerun(fx: Fixture, root: Path) -> None:
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "T-00-trailer")]))
    done = {r["op_id"] for r in rows(root, "SELECT op_id FROM outputs")}
    assert done == {"M-00-head", "M-00-footer", "M-01-head", "M-01-footer", "T-00-head"}
    resumed = SyntheticTransport(fx)
    assert run(root, fx, resumed).status == "COMPLETE"
    requested = {(resumed.file_of(c), c.start) for c in resumed.calls}
    for op_id in done:
        op = fx.op(op_id)
        assert op.range is not None and (op.source_file.file, op.range[0]) not in requested
    assert len(resumed.calls) == 2 * 5
    interrupted = [a for a in attempts(root) if a["outcome"] == state.INTERRUPTED]
    assert [(a["op_id"], a["response_bytes"]) for a in interrupted] == [("T-00-trailer", 0)]


def test_crash_before_temp_creation_is_reconciled_across_restarts(fx: Fixture, root: Path) -> None:
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "M-01-head")]))
    (live,) = [a for a in attempts(root) if a["outcome"] == state.IN_PROGRESS]
    (root / live["temp_path"]).unlink()  # the process died before creating the temp file
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "T-00-head")]))
    after = next(a for a in attempts(root) if a["attempt_id"] == live["attempt_id"])
    assert (after["outcome"], after["response_bytes"]) == (state.INTERRUPTED, 0)
    assert (root / live["temp_path"]).read_bytes() == b""
    assert run(root, fx, SyntheticTransport(fx)).status == "COMPLETE"


def test_crash_between_commit_and_rename_is_completed(fx: Fixture, root: Path) -> None:
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "M-01-head")]))
    (output,) = rows(root, "SELECT * FROM outputs WHERE op_id = 'M-00-footer'")
    (attempt,) = rows(root, "SELECT * FROM attempts WHERE attempt_id = ?", output["attempt_id"])
    (root / output["retained_file"]).rename(root / attempt["temp_path"])
    resumed = SyntheticTransport(fx)
    assert run(root, fx, resumed).status == "COMPLETE"
    assert (root / output["retained_file"]).exists() and not (root / attempt["temp_path"]).exists()
    assert all(resumed.file_of(c) != fx.op("M-00-footer").source_file.file for c in resumed.calls)


def _partial_root(fx: Fixture, root: Path) -> None:
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "T-00-head")]))


def _corrupt_payload(fx: Fixture, root: Path) -> str:
    path = root / "payload" / "M-00-footer.bin"
    raw = bytearray(path.read_bytes())
    raw[10] ^= 0xFF
    path.write_bytes(bytes(raw))
    return "corrupted"


def _delete_retained_body(fx: Fixture, root: Path) -> str:
    row = next(a for a in attempts(root) if a["outcome"] == state.REDIRECT)
    (root / row["temp_path"]).unlink()
    return "retained body is missing"


def _alter_retained_body(fx: Fixture, root: Path) -> str:
    row = next(a for a in attempts(root) if a["outcome"] == state.REDIRECT)
    (root / row["temp_path"]).write_bytes(b"x" * row["response_bytes"])
    return "retained body was altered"


def _stray_payload(fx: Fixture, root: Path) -> str:
    (root / "payload" / "T-07-footer.bin").write_bytes(b"planted")
    return "unexpected file payload/T-07-footer.bin"


def _stray_temp(fx: Fixture, root: Path) -> str:
    (root / "tmp" / "extra.part").write_bytes(b"planted")
    return "unexpected file tmp/extra.part"


def _drop_output_row(fx: Fixture, root: Path) -> str:
    edit(root, "DELETE FROM outputs WHERE op_id = 'M-00-head'")
    return "disagree"


def _shrink_in_progress_temp(fx: Fixture, root: Path) -> str:
    edit(root, "UPDATE attempts SET response_bytes = 5 WHERE outcome = 'IN_PROGRESS'")
    return "smaller than its durably recorded byte count"


def _payload_missing(fx: Fixture, root: Path) -> str:
    (root / "payload" / "M-01-head.bin").unlink()
    return "retained output is missing"


@pytest.mark.parametrize(
    "damage",
    [
        _corrupt_payload,
        _delete_retained_body,
        _alter_retained_body,
        _stray_payload,
        _stray_temp,
        _drop_output_row,
        _shrink_in_progress_temp,
        _payload_missing,
    ],
)
def test_inconsistent_or_corrupted_state_stops_for_review(
    fx: Fixture, root: Path, damage: Any
) -> None:
    _partial_root(fx, root)
    fragment = damage(fx, root)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, fragment)
    assert transport.calls == []


def test_corrupted_output_of_a_complete_root_stops(fx: Fixture, root: Path) -> None:
    assert run(root, fx, SyntheticTransport(fx)).status == "COMPLETE"
    again = SyntheticTransport(fx)
    assert run(root, fx, again).status == "COMPLETE" and again.calls == []
    _corrupt_payload(fx, root)
    assert_stopped(run(root, fx, SyntheticTransport(fx)), root, "corrupted")
    manifest = json.loads((root / phase_p.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["status"] == "INCOMPLETE"
    assert {f"{phase_p.M_LAYOUT}.superseded", f"{phase_p.T_LAYOUT}.superseded"} <= set(
        manifest["artifacts"]
    )


def test_second_process_is_refused(fx: Fixture, root: Path) -> None:
    _partial_root(fx, root)
    holder = state.Store(root / state.DB_NAME, create=False)
    try:
        with pytest.raises(phase_p.RefusedError, match="locked"):
            run(root, fx, SyntheticTransport(fx))
        with pytest.raises(phase_p.RefusedError, match="locked"):
            phase_p.inspect(root)
    finally:
        holder.close()
    assert phase_p.inspect(root)["status"] == "RUNNING"


def test_non_empty_foreign_root_is_refused(fx: Fixture, root: Path) -> None:
    root.mkdir()
    (root / "notes.txt").write_text("foreign", encoding="utf-8")
    with pytest.raises(phase_p.RefusedError, match="non-empty"):
        run(root, fx, SyntheticTransport(fx))


def test_root_bound_to_another_plan_is_refused(tmp_path: Path, fx: Fixture, root: Path) -> None:
    _partial_root(fx, root)
    other = build_fixture(tmp_path / "other", m_files=1, t_files=1)
    with pytest.raises(phase_p.RefusedError, match="different"):
        run(root, other, SyntheticTransport(other))


# -- ISOLATION -----------------------------------------------------------


def _chunk_ranges(raw: bytes, column_prefixes: tuple[str, ...]) -> list[tuple[int, int]]:
    meta = pq.ParquetFile(io.BytesIO(raw)).metadata
    ranges = []
    for g in range(meta.num_row_groups):
        group = meta.row_group(g)
        for i in range(group.num_columns):
            column = group.column(i)
            if str(column.path_in_schema).split(".")[0] in column_prefixes:
                start = int(column.data_page_offset)
                if column.dictionary_page_offset:
                    start = min(start, int(column.dictionary_page_offset))
                ranges.append((start, start + int(column.total_compressed_size) - 1))
    return ranges


def test_phase_p_never_requests_data_chunks_or_text_pages(fx: Fixture, root: Path) -> None:
    transport = SyntheticTransport(fx)
    assert run(root, fx, transport).status == "COMPLETE"
    for f in fx.plan.files:
        requested = {(c.start, c.end) for c in transport.calls if transport.file_of(c) == f.file}
        n = f.remote_length
        raw = fx.data[f.file]
        if f.arm == "M":
            footer = fx.op(f"M-{f.ordinal:02d}-footer").range
            assert requested == {(0, 3), footer}
            forbidden = _chunk_ranges(raw, frozen.PROJECTION)
        else:
            length = int.from_bytes(raw[-8:-4], "little")
            assert requested == {(0, 3), (n - 8, n - 1), (n - 8 - length, n - 9)}
            forbidden = _chunk_ranges(raw, ("text",))
        for start, end in requested:
            assert all(end < lo or start > hi for lo, hi in forbidden)


def test_forged_trailer_reaching_text_stops_before_any_footer_request(
    fx: Fixture, root: Path
) -> None:
    op = fx.op("T-00-footer")
    assert op.source_file.t is not None
    n = op.source_file.remote_length
    forged = (n - 8 - op.source_file.t.span_start).to_bytes(4, "little") + b"PAR1"
    transport = SyntheticTransport(fx, rules=[_signed_fault(fx, "T-00-trailer", body=forged)])
    assert_stopped(run(root, fx, transport), root, "text chunk")
    assert attempts(root)[-1]["outcome"] == state.VERIFICATION_FAILED
    assert not any(c.end == n - 9 for c in transport.calls)
    assert rows(root, "SELECT range_start FROM operations WHERE op_id = 'T-00-footer'") == [
        {"range_start": None}
    ]


def test_m_footer_whose_trailer_differs_from_frozen_length_stops(fx: Fixture, root: Path) -> None:
    op = fx.op("M-00-footer")
    assert op.range is not None
    raw = bytearray(slice_of(fx, op.op_id, *op.range))
    raw[-8:-4] = (int.from_bytes(raw[-8:-4], "little") - 1).to_bytes(4, "little")
    rule = _signed_fault(fx, "M-00-footer", body=bytes(raw))
    assert_stopped(run(root, fx, SyntheticTransport(fx, rules=[rule])), root, "frozen L")


def test_m_binding_mismatch_stops(tmp_path: Path, root: Path) -> None:
    fixture = build_fixture(tmp_path / "fixture")
    obj = json.loads(json.dumps(fixture.plan_obj))
    obj["files"][0]["bindings"]["data_payload_bytes"] += 1
    obj["digest"] = canonical.self_digest(obj)
    fixture.plan = frozen.validate_plan(obj, synthetic=True)
    fixture.plan_obj = obj
    result = run(root, fixture, SyntheticTransport(fixture))
    assert_stopped(result, root, "compressed bytes differ")


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        (
            "INSERT INTO operations VALUES ('T-09-footer', 99, 'T', 9, 'data/x.parquet', "
            "'T_FOOTER_FROM_TRAILER', 0, 1000, 'PENDING')",
            "stored operations differ from the frozen plan",
        ),
        (
            "UPDATE operations SET range_start = 4 WHERE op_id = 'M-01-footer'",
            "stored range differs from the frozen range",
        ),
        (
            "UPDATE operations SET range_start = 100, range_end = 200 WHERE op_id = 'T-01-footer'",
            "range present before its trailer completed",
        ),
        (
            "UPDATE operations SET kind = 'HEAD_0_3' WHERE op_id = 'T-01-trailer'",
            "stored operation differs from the frozen plan",
        ),
        (
            "UPDATE operations SET file = 'data/other.parquet' WHERE op_id = 'T-01-head'",
            "stored operation differs from the frozen plan",
        ),
    ],
)
def test_injected_or_altered_operations_are_refused(
    fx: Fixture, root: Path, sql: str, fragment: str
) -> None:
    _partial_root(fx, root)
    edit(root, sql)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, fragment)
    assert transport.calls == []


def test_offline_entry_refuses_real_plan_live_transport_and_frozen_roots(
    fx: Fixture, root: Path, tmp_path: Path
) -> None:
    real = frozen.load_committed_plan()
    with pytest.raises(phase_p.RefusedError):
        phase_p.run_offline(root, plan=real, transport=SyntheticTransport(fx))
    with pytest.raises(phase_p.RefusedError):
        phase_p.run_offline(root, plan=fx.plan, transport=tp.LiveHttpsTransport())
    for frozen_root in (frozen.EXECUTION_ROOT, phase_p.V3_ROOT):
        with pytest.raises(phase_p.RefusedError):
            phase_p.run_offline(Path(frozen_root), plan=fx.plan, transport=SyntheticTransport(fx))
        with pytest.raises(phase_p.RefusedError):
            phase_p.run_offline(
                Path(frozen_root) / "sub", plan=fx.plan, transport=SyntheticTransport(fx)
            )
    assert not root.exists() and not Path(frozen.EXECUTION_ROOT).exists()


# -- CAPS ----------------------------------------------------------------


def _partial_m_root(fx: Fixture, root: Path) -> int:
    """Crash inside arm M (M-00-* complete, M-01-head interrupted); returns M attempts."""
    with pytest.raises(SimulatedCrash):
        run(root, fx, SyntheticTransport(fx, rules=[crash_on(fx, "M-01-head")]))
    return len([a for a in attempts(root) if a["arm"] == "M"])


def _seed_attempts(fx: Fixture, root: Path, count: int, body_bytes: int = 0) -> None:
    """Record ``count`` finished M attempts (each retaining a body of ``body_bytes``)."""
    op = fx.op("M-00-head")
    store = state.Store(root / state.DB_NAME, create=False)
    try:
        for _ in range(count):
            attempt_id, temp = store.begin_attempt(
                op=op,
                try_number=1,
                hop=0,
                url=tp.start_url(fx.plan.source, op.source_file.file).identity(),
                start=0,
                end=3,
                now="2026-09-29T00:00:00Z",
            )
            with open(root / temp, "wb") as sink:
                sink.truncate(body_bytes)
            digest = hashlib.sha256(b"\x00" * body_bytes).hexdigest()
            store.finish_attempt(
                attempt_id,
                outcome=state.HTTP_ERROR,
                http_status=503,
                received=body_bytes,
                sha256=digest,
                error="seeded",
                now="2026-09-29T00:00:00Z",
            )
    finally:
        store.close()


def test_attempt_cap_refuses_the_201st_attempt(fx: Fixture, root: Path) -> None:
    existing = _partial_m_root(fx, root)
    _seed_attempts(fx, root, frozen.ATTEMPTS_PER_ARM_MAX - existing)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, "physical attempt cap 200")
    assert transport.calls == []
    assert len([a for a in attempts(root) if a["arm"] == "M"]) == 200


def test_attempt_cap_allows_exactly_the_200th_attempt(fx: Fixture, root: Path) -> None:
    existing = _partial_m_root(fx, root)
    _seed_attempts(fx, root, frozen.ATTEMPTS_PER_ARM_MAX - 1 - existing)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, "physical attempt cap 200")
    assert len(transport.calls) == 1  # hop 0 of M-01-head; its redirect hop is refused
    assert len([a for a in attempts(root) if a["arm"] == "M"]) == 200


@pytest.mark.parametrize(("slack", "calls"), [(0, 1), (1, 0)])
def test_arm_body_cap_is_never_exceeded(fx: Fixture, root: Path, slack: int, calls: int) -> None:
    _partial_m_root(fx, root)
    used = sum(a["response_bytes"] for a in attempts(root) if a["arm"] == "M")
    room = frozen.BODY_BYTES_PER_ARM_MAX - phase_p.RESPONSE_READ_LIMIT
    _seed_attempts(fx, root, 1, room - used + slack)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, "arm cap")
    assert len(transport.calls) == calls
    total = sum(a["response_bytes"] for a in attempts(root) if a["arm"] == "M")
    assert total <= frozen.BODY_BYTES_PER_ARM_MAX


def test_single_response_over_4_mib_stops(fx: Fixture, root: Path) -> None:
    op = fx.op("M-00-footer")
    huge = b"\x00" * (frozen.BODY_BYTES_PER_RESPONSE_MAX + 4096)
    rule = _signed_fault(fx, op.op_id, body=huge, headers={"Content-Length": None})
    assert_stopped(run(root, fx, SyntheticTransport(fx, rules=[rule])), root, "exceeds 4194304")
    last = attempts(root)[-1]
    assert (last["outcome"], last["response_bytes"]) == (
        state.CAP_EXCEEDED,
        frozen.BODY_BYTES_PER_RESPONSE_MAX + 1,
    )


def test_root_size_cap_refuses(fx: Fixture, root: Path) -> None:
    _partial_m_root(fx, root)
    with open(root / "ballast.bin", "wb") as sink:
        sink.truncate(frozen.ROOT_BYTES_MAX - phase_p.RESPONSE_READ_LIMIT)
    transport = SyntheticTransport(fx)
    assert_stopped(run(root, fx, transport), root, "root cap")
    assert transport.calls == []


def test_insufficient_free_disk_refuses_before_creating_the_root(
    fx: Fixture, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phase_p, "_free_bytes", lambda _: frozen.FREE_DISK_BYTES_MIN - 1)
    with pytest.raises(phase_p.RefusedError, match="1 GiB"):
        run(root, fx, SyntheticTransport(fx))
    assert not root.exists()


# -- OUTPUT --------------------------------------------------------------


def test_complete_outputs_are_hash_bound(fx: Fixture, root: Path) -> None:
    assert run(root, fx, SyntheticTransport(fx)).status == "COMPLETE"
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    manifest = json.loads((root / phase_p.MANIFEST).read_text(encoding="utf-8"))
    for doc in (receipt, manifest):
        assert canonical.self_digest(doc) == doc["digest"]
        assert (doc["plan_digest"], doc["protocol_sha256"], doc["selection_digest"]) == (
            fx.plan.digest,
            frozen.PROTOCOL_SHA256,
            frozen.SELECTION_DIGEST,
        )
    assert receipt["status"] == "COMPLETE" and manifest["receipt_digest"] == receipt["digest"]
    for rel, entry in manifest["artifacts"].items():
        raw = (root / rel).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (entry["bytes"], entry["sha256"])
    assert {k for k in manifest["artifacts"] if k.startswith("payload/")} == {
        f"payload/{op.op_id}.bin" for op in fx.plan.operations
    }
    table = attempts(root)
    lines = (root / phase_p.REQUESTS).read_bytes().splitlines()
    assert [json.loads(line) for line in lines] == table
    assert (
        receipt["request_receipt"]["sha256"]
        == hashlib.sha256((root / phase_p.REQUESTS).read_bytes()).hexdigest()
    )
    for arm in ("M", "T"):
        arm_rows = [a for a in table if a["arm"] == arm]
        totals = receipt["totals"][arm]
        assert totals["physical_attempts"] == len(arm_rows)
        assert totals["response_body_bytes"] == sum(a["response_bytes"] for a in arm_rows)
        assert totals["logical_complete"] == totals["logical_operations"]
    for op in receipt["operations"]:
        output = op["output"]
        raw = (root / output["retained_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == output["sha256"] and op["status"] == "COMPLETE"
        plan_op = fx.op(op["op_id"])
        n = plan_op.source_file.remote_length
        assert raw == slice_of(fx, op["op_id"], op["range"][0], op["range"][1])
        if plan_op.range is None:
            assert op["range"][1] == n - 9


def test_layouts_freeze_the_structural_metadata(fx: Fixture, root: Path) -> None:
    assert run(root, fx, SyntheticTransport(fx)).status == "COMPLETE"
    receipt = json.loads((root / phase_p.RECEIPT).read_text(encoding="utf-8"))
    m_doc = json.loads((root / phase_p.M_LAYOUT).read_text(encoding="utf-8"))
    t_doc = json.loads((root / phase_p.T_LAYOUT).read_text(encoding="utf-8"))
    for name, doc in ((phase_p.M_LAYOUT, m_doc), (phase_p.T_LAYOUT, t_doc)):
        assert canonical.self_digest(doc) == doc["digest"] == receipt["layouts"][name]["digest"]
    for entry in m_doc["files"]:
        f = next(x for x in fx.plan.files if x.arm == "M" and x.file == entry["file"])
        assert f.m is not None
        chunks = entry["layout"]["projected_chunks"]
        assert len(chunks) == f.m.data_chunk_count
        assert sum(c["compressed_bytes"] for c in chunks) == f.m.data_payload_bytes
        assert entry["layout"]["window_row_group"] == 1
    for entry in t_doc["files"]:
        raw = fx.data[entry["file"]]
        assert entry["validated_footer_length"] == int.from_bytes(raw[-8:-4], "little")
        chunk = entry["layout"]["selected_text_chunk"]
        bindings = entry["bindings"]
        assert (chunk["start"], chunk["end_exclusive"]) == (
            bindings["span_start"],
            bindings["span_end"],
        )
        assert entry["footer_range"][0] >= bindings["span_end"]
        assert "statistics" not in json.dumps(entry)
