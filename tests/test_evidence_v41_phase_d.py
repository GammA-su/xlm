"""Evidence-v4.1 Phase D: plan, M/T decode, blinding, restart, caps, outputs, CLI, E2E.

Every run is SYNTHETIC and offline: authored Parquet files, a synthetic
COMPLETE Phase-P parent produced by the reviewed Phase-P engine, a scripted
transport and disposable roots. Sockets are patched to refuse. The committed
real plan is only loaded and validated; it is never executed.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from evidence_v4_support import (
    REDIRECT_BODY,
    FakeClock,
    FakeResponse,
    Rule,
    SimulatedCrash,
    SyntheticTransport,
    WireRule,
    WireTransport,
    block_network,
    chunk,
    response_head,
)
from evidence_v41_phase_d_support import (
    BIG_ROWS,
    SENTINEL,
    T0_SELECTED,
    T1_NULL_ROW,
    T1_OVERSIZE_ROW,
    T1_SELECTED,
    DFixture,
    at_op,
    build_dfixture,
    reseal,
    run_d,
    selected_text,
    tree,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v4 import frozen, phase_d, phase_p, state, v41
from xlm.data.evidence_v4 import phase_d_decode as dec
from xlm.data.evidence_v4 import phase_d_plan as pd
from xlm.data.evidence_v4 import transport as tp

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SIGNED = "cas-bridge.xethub.hf.co"
ORIGIN = "huggingface.co"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.fixture(scope="module")
def dfx(tmp_path_factory: pytest.TempPathFactory) -> DFixture:
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        return build_dfixture(tmp_path_factory.mktemp("phase-d-fixture"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "phase-d-root"


def rows(root: Path, sql: str) -> list[dict[str, Any]]:
    conn = state.open_read_only(root / state.DB_NAME)
    try:
        return [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def attempts(root: Path) -> list[dict[str, Any]]:
    return rows(root, "SELECT * FROM attempts ORDER BY attempt_id")


def load(root: Path, name: str) -> dict[str, Any]:
    obj = canonical.loads_bytes_strict((root / name).read_bytes())
    assert isinstance(obj, dict) and canonical.self_digest(obj) == obj["digest"]
    return obj


def jsonl(root: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / name).read_bytes().splitlines()]


def transport(dfx: DFixture, *rules: Rule) -> SyntheticTransport:
    return SyntheticTransport(dfx.fx, rules=list(rules))


def signed_ok(dfx: DFixture, op_id: str, times: int = 1, **overrides: Any) -> Rule:
    t = SyntheticTransport(dfx.fx)
    return Rule(at_op(dfx, op_id, host=SIGNED), lambda c: t.ok(c, **overrides), times)


def m_oracle(dfx: DFixture) -> list[dict[str, Any]]:
    """Independent oracle: the frozen window rows read directly from the authored files."""
    out = []
    for f in dfx.plan.m_files:
        table = pq.read_table(io.BytesIO(dfx.fx.data[f.file]), columns=list(frozen.PROJECTION))
        out += table.slice(f.window[0], f.window[1] - f.window[0]).to_pylist()
    return out


def assert_complete(result: phase_d.Result) -> None:
    assert (result.status, result.run_status, result.stop_reason) == (
        "COMPLETE",
        "COMPLETE",
        None,
    ), result
    assert {a: result.arms[a]["status"] for a in "MT"} == {"M": "COMPLETE", "T": "COMPLETE"}


def assert_stopped(result: phase_d.Result, fragment: str) -> None:
    assert result.status == "INCOMPLETE" and result.run_status == "STOPPED"
    assert result.stop_reason is not None and fragment in result.stop_reason, result.stop_reason


# -- PLAN: the committed real plan -------------------------------------------------


def test_committed_plan_is_exactly_8_m_and_47_t_operations() -> None:
    plan = pd.load_committed_plan()
    ops = plan.fetch.operations
    m_ops = [o for o in ops if o.arm == "M"]
    t_ops = [o for o in ops if o.arm == "T"]
    assert (len(m_ops), len(t_ops)) == (8, 47)
    assert [o.op_id for o in m_ops] == [f"M-{i:02d}-d00" for i in range(8)]

    def size(op: frozen.Operation) -> int:
        assert op.range is not None
        return op.range[1] - op.range[0] + 1

    assert sum(size(o) for o in m_ops) == 11692530
    assert sum(size(o) for o in t_ops) == 179963169
    assert [len(plan.ops_of("T", i)) for i in range(8)] == [6, 6, 5, 6, 6, 6, 6, 6]
    assert all(size(o) <= 4194304 for o in ops)
    assert sum(f.window[1] - f.window[0] for f in plan.m_files) == 4096
    assert all(f.window[1] - f.window[0] == 512 for f in plan.m_files)
    assert sum(len(g.locators) for g in plan.t_files) == 118
    assert [o.seq for o in ops] == list(range(55))
    for g in plan.t_files:
        pieces = [(o.range[0], o.range[1] + 1) for o in plan.ops_of("T", g.ordinal) if o.range]
        assert pieces[0][0] == g.span[0] == g.dictionary_page_offset < g.data_page_offset
        assert pieces[-1][1] == g.span[1]
        assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:], strict=False))


def test_committed_plan_binds_the_reviewed_dry_plan_and_complete_phase_p_parent() -> None:
    plan = pd.load_committed_plan()
    parents = plan.parents
    assert (
        parents.dry_plan_digest
        == pd.DRY_PLAN_DIGEST
        == ("ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356")
    )
    assert parents.result_review_commit == "ed8efcf3c85c265828a88579a264f08a2048640f"
    assert parents.phase_p_root == v41.EXECUTION_ROOT == pd.PHASE_P_ROOT
    assert parents.phase_p_plan_digest == v41.PLAN_DIGEST
    digests = {a.rel: a.digest for a in parents.artifacts if a.digest}
    assert digests == {  # the reviewed COMPLETE Phase-P exports (result review, ed8efcf)
        "phase_p_receipt.json": "ebd5704be8a871034ef8048c0641e9d735d017f458003b4efc50ab124c8bd965",
        "artifact_manifest.json": (
            "1bb6bad6e3690758c005e260c272eac3c0adcb6d040fde960a502d0ced90d7fe"
        ),
        "m_phase_p_layout.json": "2c9f2f79448e1bb1d59b57f1ddec276dcd05bc79dddaea4014c9942a340d340a",
        "t_phase_p_layout.json": "364bda46c766e8f9fdebd5732408743420cbe5c6f0b00fbd6a10d09a8bd53274",
    }
    assert len(parents.artifacts) == 4 + 2 + 8 + 16
    assert plan.fetch.execution_root == pd.EXECUTION_ROOT
    assert pd.EXECUTION_ROOT not in (v41.EXECUTION_ROOT, frozen.EXECUTION_ROOT, phase_p.V3_ROOT)
    assert plan.fetch.profile.hosts is v41.HOSTS and plan.fetch.profile.network == v41.NETWORK


def test_scientific_identity_is_preserved() -> None:
    plan = pd.load_committed_plan()
    assert (frozen.SCIENTIFIC_NAMESPACE, frozen.SELECTION_DIGEST, frozen.SOURCE_REVISION) == (
        "essential-web-evidence-v2.0",
        "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474",
        "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d",
    )
    assert frozen.POLICY_DIGEST == (
        "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    )
    v41_plan = v41.load_committed_plan()
    for f, p in zip(plan.m_files, [f for f in v41_plan.files if f.arm == "M"], strict=True):
        assert p.m is not None
        assert (f.file, f.remote_length, f.strong_etag, f.window) == (
            p.file,
            p.remote_length,
            p.strong_etag,
            p.m.window,
        )
        assert (len(f.chunks), f.range_half_open[1] - f.range_half_open[0]) == (
            p.m.data_chunk_count,
            p.m.data_payload_bytes,
        )
    for g, p in zip(plan.t_files, [f for f in v41_plan.files if f.arm == "T"], strict=True):
        assert p.t is not None
        assert (g.file, g.remote_length, g.strong_etag) == (p.file, p.remote_length, p.strong_etag)
        assert (g.span, len(plan.ops_of("T", g.ordinal))) == (
            (p.t.span_start, p.t.span_end),
            p.t.data_range_count,
        )


def test_verify_reproduces_every_committed_phase_d_binding() -> None:
    summary = phase_d.verify_repository()
    assert summary["plan_digest"] == pd.PLAN_DIGEST
    assert summary["freeze_digest"] == pd.FREEZE_DIGEST
    assert summary["protocol_sha256"] == pd.PROTOCOL_SHA256
    assert summary["dry_plan_digest"] == pd.DRY_PLAN_DIGEST
    assert (summary["M_operations"], summary["T_operations"]) == (8, 47)
    assert (summary["M_success_payload_bytes"], summary["T_success_payload_bytes"]) == (
        11692530,
        179963169,
    )
    assert (summary["M_rows"], summary["T_locators"]) == (4096, 118)


def _real_obj() -> dict[str, Any]:
    obj = canonical.loads_bytes_strict((frozen.REPO_ROOT / pd.PLAN_PATH).read_bytes())
    assert isinstance(obj, dict)
    return obj


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o["operations"].append({**o["operations"][-1], "op_id": "T-07-d06"}),
        lambda o: o["operations"][0]["range"].__setitem__(1, o["operations"][0]["range"][1] + 1),
        lambda o: o["operations"].pop(9),
        lambda o: o["files"][8]["locators"].pop(),
        lambda o: o["parents"]["dry_plan"].__setitem__("digest", "0" * 64),
        lambda o: o["limits"]["physical_attempts_per_arm_max"].__setitem__("T", 321),
        lambda o: o["network"]["hosts"].append("evil.hf.co"),
        lambda o: o.__setitem__("execution_root", v41.EXECUTION_ROOT),
    ],
)
def test_real_plan_refuses_any_edit_even_when_resealed(
    monkeypatch: pytest.MonkeyPatch, mutate: Any
) -> None:
    obj = _real_obj()
    mutate(obj)
    sealed = reseal(obj)
    with pytest.raises(pd.PlanError):
        pd.validate_plan(sealed, synthetic=False)
    monkeypatch.setattr(pd, "PLAN_DIGEST", sealed["digest"])  # isolate the structural check
    with pytest.raises(pd.PlanError):
        pd.validate_plan(sealed, synthetic=False)


# -- PLAN: synthetic grammar -------------------------------------------------------


def test_synthetic_plan_shape(dfx: DFixture) -> None:
    ops = dfx.plan.fetch.operations
    assert [o.op_id for o in ops] == ["M-00-d00", "M-01-d00", "T-00-d00", "T-00-d01", "T-01-d00"]
    first, second = dfx.plan.ops_of("T", 0)
    assert first.range is not None and second.range is not None
    assert first.range[1] - first.range[0] + 1 == pd.PIECE_BYTES
    assert second.range[0] == first.range[1] + 1
    with pytest.raises(pd.PlanError):
        pd.validate_plan(dfx.plan_obj, synthetic=False)  # synthetic and real never mix


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda o: o["operations"].append({**o["operations"][0], "seq": 5}), "operations differ"),
        (lambda o: o["operations"][2]["range"].__setitem__(0, 0), "operations differ"),
        (lambda o: o["operations"].reverse(), "operations differ"),
        (lambda o: o["operations"][4].__setitem__("kind", "HEAD_0_3"), "operations differ"),
        (lambda o: o["files"][0]["projected_chunks"].pop(), "union of the projected"),
        (lambda o: _shift(o["files"][0]["projected_chunks"][1]), "exactly adjacent"),
        (lambda o: o["files"][2].__setitem__("dictionary_page_offset", 5), "dictionary"),
        (lambda o: o["files"][2]["locators"][0].__setitem__("row_in_group", 4), "identity"),
        (lambda o: o["files"][3]["locators"].reverse(), "ascending"),
        (lambda o: o["files"][0].__setitem__("window", [0, 10]), "inside its row group"),
        (lambda o: o["files"][0].__setitem__("projection", ["eai_taxonomy"]), "projection"),
        (lambda o: o["files"][2]["phase_p_payloads"].__setitem__("footer", "x.bin"), "payload"),
        (lambda o: o["parents"]["phase_p"]["artifacts"].pop("state.sqlite"), "artifacts"),
        (lambda o: o["parents"]["phase_p"].__setitem__("run_status", "STOPPED"), "COMPLETE"),
        (lambda o: o["limits"].__setitem__("execution_root_bytes_max", 2**31), "limits"),
        (lambda o: o["source"].__setitem__("revision", frozen.SOURCE_REVISION), "synthetic"),
    ],
)
def test_arbitrary_operation_or_binding_injection_refuses(
    dfx: DFixture, mutate: Any, message: str
) -> None:
    obj = json.loads(json.dumps(dfx.plan_obj))
    mutate(obj)
    with pytest.raises(pd.PlanError, match=message):
        pd.validate_plan(reseal(obj), synthetic=True)


def _shift(chunk_: dict[str, Any]) -> None:
    chunk_["start"] += 1
    chunk_["end_exclusive"] += 1


def test_unsealed_edit_refuses(dfx: DFixture) -> None:
    obj = json.loads(json.dumps(dfx.plan_obj))
    obj["operations"][0]["range"][1] += 1
    with pytest.raises(pd.PlanError, match="self-digest"):
        pd.validate_plan(obj, synthetic=True)


def test_wrong_confirm_digest_refuses_without_creating_any_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(phase_d, "live_root", lambda: tmp_path / "live")
    for digest in (v41.PLAN_DIGEST, pd.DRY_PLAN_DIGEST, pd.PLAN_DIGEST.upper(), "0" * 64, ""):
        with pytest.raises(phase_d.RefusedError, match="does not equal"):
            phase_d.run_live(confirm_plan_digest=digest)
    assert not (tmp_path / "live").exists() and not Path(pd.EXECUTION_ROOT).exists()


def test_live_entry_wires_only_the_frozen_plan_roots_and_host_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class Recorder:
        def __init__(self, plan: Any, root: Path, parent: Path, transport: Any, **_: Any) -> None:
            seen.update(plan=plan, root=root, parent=parent, transport=transport)

        def run(self) -> str:
            return "recorded"

    monkeypatch.setattr(phase_d, "_PhaseDEngine", Recorder)
    assert phase_d.run_live(confirm_plan_digest=pd.PLAN_DIGEST) == "recorded"  # type: ignore[comparison-overlap]
    assert seen["plan"].digest == pd.PLAN_DIGEST and not seen["plan"].synthetic
    assert seen["root"] == Path("G:\\Project\\xlm-evidence-v4.1\\essential-web-phase-d")
    assert seen["parent"] == Path("G:\\Project\\xlm-evidence-v4.1\\essential-web")
    assert isinstance(seen["transport"], tp.LiveHttpsTransport)
    assert seen["transport"]._policy is v41.HOSTS
    assert not Path(pd.EXECUTION_ROOT).exists()


def test_offline_entry_refuses_real_plan_live_transport_and_frozen_roots(
    dfx: DFixture, root: Path
) -> None:
    real = pd.load_committed_plan()
    with pytest.raises(phase_d.RefusedError):
        run_d(root, dfx, transport(dfx), plan=real)
    with pytest.raises(phase_d.RefusedError):
        run_d(root, dfx, tp.LiveHttpsTransport(policy=v41.HOSTS))
    for fixed in phase_d.FROZEN_ROOTS:
        with pytest.raises(phase_d.RefusedError):
            run_d(Path(fixed), dfx, transport(dfx))
        with pytest.raises(phase_d.RefusedError):
            run_d(root, dfx, transport(dfx), parent=Path(fixed) / "sub")
    assert not root.exists() and not Path(pd.EXECUTION_ROOT).exists()


def test_stored_operation_rows_differing_from_the_plan_stop(dfx: DFixture, root: Path) -> None:
    crash = Rule(at_op(dfx, "T-00-d00"), lambda c: SimulatedCrash("x"))
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, transport(dfx, crash))
    conn = sqlite3.connect(root / state.DB_NAME)
    conn.execute("UPDATE operations SET range_end = range_end + 1 WHERE op_id = 'T-01-d00'")
    conn.commit()
    conn.close()
    t = transport(dfx)
    assert_stopped(run_d(root, dfx, t), "stored range differs from the frozen range")
    assert t.calls == []


# -- M -----------------------------------------------------------------------------


def test_m_decodes_exactly_the_frozen_window_rows_of_the_two_projected_columns(
    dfx: DFixture, root: Path
) -> None:
    assert_complete(run_d(root, dfx, transport(dfx)))
    records = jsonl(root, phase_d.M_OUTPUT)
    assert len(records) == sum(f.window[1] - f.window[0] for f in dfx.plan.m_files) == 30
    for record in records:
        assert set(record) == {dec.LOCATOR_FIELD, "eai_taxonomy", "quality_signals"}
    locators = [r.pop(dec.LOCATOR_FIELD) for r in records]
    assert records == m_oracle(dfx)
    expected = [(f.file, row, f.ordinal) for f in dfx.plan.m_files for row in range(*f.window)]
    assert [(loc["source_file"], loc["row"], loc["m_ordinal"]) for loc in locators] == expected
    for loc in locators:
        assert loc["row_in_group"] == loc["row"] - 40 and loc["row_group"] == 1
        assert (loc["repository"], loc["revision"]) == (
            frozen.SYNTHETIC_REPOSITORY,
            dfx.plan.fetch.source.revision,
        )
    manifest = load(root, phase_d.M_MANIFEST)
    raw = (root / phase_d.M_OUTPUT).read_bytes()
    assert manifest["output"][phase_d.M_OUTPUT] == {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert manifest["records"] == 30 and manifest["projection"] == list(frozen.PROJECTION)
    assert all(f["decoder_reads"] == len(dfx.plan.m_files[0].chunks) for f in manifest["files"])


def test_m_decode_reads_only_acquired_bytes(dfx: DFixture) -> None:
    f = dfx.plan.m_files[0]
    raw = dfx.fx.data[f.file]
    footer = (dfx.parent / f.footer_payload).read_bytes()
    payload = raw[f.range_half_open[0] : f.range_half_open[1]]
    result = dec.decode_m_file(f, payload, footer, dfx.plan.fetch.source)
    assert result.reads == len(f.chunks) and result.rows == tuple(range(*f.window))
    last = f.chunks[-1]
    short = pd.MFile(**{**f.__dict__, "range_half_open": (f.range_half_open[0], last.start)})
    with pytest.raises(dec.DecodeError, match="outside the acquired ranges"):
        dec.decode_m_file(
            short, payload[: last.start - f.range_half_open[0]], footer, dfx.plan.fetch.source
        )
    source = dec.SparseSource(100, [(10, b"x" * 20)])
    source.seek(25)
    with pytest.raises(dec.SparseReadError):
        source.read(10)
    with pytest.raises(dec.DecodeError, match="wrong length"):
        dec.decode_m_file(f, payload[:-1], footer, dfx.plan.fetch.source)


def test_m_assembly_refuses_duplicate_missing_or_reordered_rows(dfx: DFixture) -> None:
    files = dfx.plan.m_files
    good = [
        dec.MResult(
            f.ordinal, tuple(range(*f.window)), tuple(b"{}\n" for _ in range(*f.window)), 40, 1
        )
        for f in files
    ]
    assert dec.assemble_m(files, good).count(b"\n") == 30
    first = good[0]
    for rows_ in (
        (first.rows[0], *first.rows[:-1]),  # duplicate
        first.rows[1:],  # missing
        tuple(reversed(first.rows)),  # reordered
    ):
        bad = dec.MResult(0, rows_, tuple(b"{}\n" for _ in rows_), 40, 1)
        with pytest.raises(dec.DecodeError, match="duplicate, missing or reordered"):
            dec.assemble_m(files, [bad, good[1]])
    with pytest.raises(dec.DecodeError, match="exactly one per frozen M file"):
        dec.assemble_m(files, [good[0], good[0]])


def test_committed_m_windows_are_exactly_512_rows_per_file_4096_total() -> None:
    plan = pd.load_committed_plan()
    assert [f.window[1] - f.window[0] for f in plan.m_files] == [512] * 8
    for f in plan.m_files:
        assert f.row_group_first_row <= f.window[0] and f.window[1] <= (
            f.row_group_first_row + f.row_group_rows
        )
        assert len(f.chunks) == 81


# -- T -----------------------------------------------------------------------------


def test_t_dictionary_inclusive_multi_piece_decode_retains_exact_selected_documents(
    dfx: DFixture, root: Path
) -> None:
    assert_complete(run_d(root, dfx, transport(dfx)))
    g = dfx.plan.t_files[0]
    assert g.dictionary_page_offset < g.data_page_offset and len(dfx.plan.ops_of("T", 0)) == 2
    docs = jsonl(root, phase_d.T_DOCUMENTS)
    expected = [(0, r) for r in T0_SELECTED] + [(1, r) for r in T1_SELECTED]
    assert [(d["t_ordinal"], d["row_in_group"]) for d in docs] == expected
    for d in docs:
        text = selected_text(d["t_ordinal"], d["row_in_group"])
        g = dfx.plan.t_files[d["t_ordinal"]]
        assert d["locator"] == [
            frozen.SYNTHETIC_REPOSITORY,
            dfx.plan.fetch.source.revision,
            g.file,
            g.row_group_first_row + d["row_in_group"],
        ]
        if text is None:
            assert (d["status"], d["text"], d["sha256"]) == (
                "unreviewable_missing_or_invalid_text",
                None,
                None,
            )
        elif len(text.encode()) > 65536:
            assert (d["status"], d["text"], d["sha256"], d["utf8_bytes"]) == (
                "unreviewable_full_document_due_to_size",
                None,
                None,
                len(text.encode()),
            )
        else:
            assert (d["status"], d["text"]) == ("full_text_available", text)
            assert d["utf8_bytes"] == len(text.encode("utf-8"))
            assert d["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest = load(root, phase_d.T_MANIFEST)
    assert manifest["selected_locators"] == 7
    assert manifest["status_counts"] == {
        "full_text_available": 5,
        "unreviewable_full_document_due_to_size": 1,
        "unreviewable_missing_or_invalid_text": 1,
    }
    big = manifest["files"][0]
    assert big["decoded_rows"] == BIG_ROWS and big["unselected_rows_decoded_not_retained"] == (
        BIG_ROWS - len(T0_SELECTED)
    )
    assert big["decoder_reads"] == 1  # one read of the reassembled dictionary-inclusive chunk
    small = manifest["files"][1]
    assert (
        small["decoded_rows"] == 40
        and small["status_counts"]["unreviewable_full_document_due_to_size"] == 1
    )


def test_unselected_text_is_never_exported_logged_or_rendered(
    dfx: DFixture, root: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    result = run_d(root, dfx, transport(dfx))
    assert_complete(result)
    assert SENTINEL not in json.dumps(result.__dict__)
    for rel, raw in (
        (p.relative_to(root).as_posix(), p.read_bytes()) for p in root.rglob("*") if p.is_file()
    ):
        if rel.startswith(("payload/", "tmp/")):
            continue  # raw compressed acquisition bytes: custodian evidence, never decoded out
        assert SENTINEL.encode() not in raw, rel
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err + caplog.text
    oversized = selected_text(1, T1_OVERSIZE_ROW)
    assert oversized is not None
    assert oversized[:40].encode() not in (root / phase_d.T_DOCUMENTS).read_bytes()


def test_sealed_provenance_and_no_selection_category_or_blinding_secret(
    dfx: DFixture, root: Path
) -> None:
    assert_complete(run_d(root, dfx, transport(dfx)))
    provenance = load(root, phase_d.T_PROVENANCE)
    assert provenance["access"].startswith("SEALED")
    assert [p["locator"] for p in provenance["locators"]] == [
        list(loc.identity) for g in dfx.plan.t_files for loc in g.locators
    ]
    assert provenance["locators"][0]["operations"] == ["T-00-d00", "T-00-d01"]
    forbidden = ("stratum", "strata", "review_id", "ew2-", "key_commitment", "census", "policy_a")
    for p in root.rglob("*"):
        if p.is_file() and not p.relative_to(root).as_posix().startswith(("payload/", "tmp/")):
            text = p.read_bytes().decode("utf-8", "replace").lower()
            for word in forbidden:
                assert word not in text, (p.name, word)
    manifest = load(root, phase_d.T_MANIFEST)
    assert "locators" not in manifest  # no per-locator identity in the unsealed manifest
    assert all({"text", "locators", "locator"}.isdisjoint(f) for f in manifest["files"])


def test_t_assembly_refuses_duplicate_missing_or_foreign_locators(dfx: DFixture) -> None:
    files = dfx.plan.t_files
    results = [
        dec.TResult(
            g.ordinal,
            tuple(dec.classify(b"ok", loc, g.ordinal, g.row_group) for loc in g.locators),
            g.row_group_rows,
            1,
        )
        for g in files
    ]
    bundle, total, unique = dec.assemble_t(files, results)
    assert bundle.count(b"\n") == 7 and total == 14 and unique == 2
    docs = results[1].documents
    for bad_docs in (
        docs[:-1],
        (*docs, docs[-1]),
        (docs[0], *docs[:-1]),
        (*docs[:-1], results[0].documents[0]),
    ):
        bad = dec.TResult(1, tuple(bad_docs), 40, 1)
        with pytest.raises(dec.DecodeError, match="duplicate, missing or foreign"):
            dec.assemble_t(files, [results[0], bad])
    with pytest.raises(dec.DecodeError, match="exactly one per frozen T file"):
        dec.assemble_t(files, results[:1])


def test_t_document_size_rule_and_invalid_text_are_frozen_statuses(dfx: DFixture) -> None:
    loc = dfx.plan.t_files[1].locators[0]
    cases = {
        b"x" * 65536: ("full_text_available", 65536),
        b"x" * 65537: ("unreviewable_full_document_due_to_size", 65537),
        "é".encode() * 32769: ("unreviewable_full_document_due_to_size", 65538),
        b"\xff\xfe invalid": ("unreviewable_missing_or_invalid_text", None),
        b"\xed\xa0\x80": ("unreviewable_missing_or_invalid_text", None),  # encoded surrogate
        b"": ("unreviewable_missing_or_invalid_text", None),
        None: ("unreviewable_missing_or_invalid_text", None),
    }
    for raw, (status, size) in cases.items():
        doc = dec.classify(raw, loc, 1, 1)
        assert (doc.status, doc.utf8_bytes) == (status, size), raw
        assert (doc.text is not None) == (status == "full_text_available")  # never truncated


def test_t_retained_text_ceiling_is_enforced(dfx: DFixture) -> None:
    g = dfx.plan.t_files[1]
    locators = tuple(
        pd.Locator((g.locators[0].identity[0], g.locators[0].identity[1], g.file, r), r)
        for r in range(129)
    )
    big = pd.TFile(**{**g.__dict__, "row_group_rows": 200, "locators": locators})
    docs = tuple(
        dec.classify(f"{i:05d}".encode() + b"y" * 65531, loc, 1, 1)
        for i, loc in enumerate(locators)
    )
    assert all(d.status == "full_text_available" for d in docs)
    with pytest.raises(dec.DecodeError, match="retained unique text exceeds"):
        dec.assemble_t([big], [dec.TResult(1, docs, 129, 1)])


def test_committed_t_locators_are_exactly_118_unique() -> None:
    plan = pd.load_committed_plan()
    identities = [loc.identity for g in plan.t_files for loc in g.locators]
    assert len(identities) == len(set(identities)) == 118
    assert [len(g.locators) for g in plan.t_files] == [16, 14, 11, 11, 14, 13, 18, 21]


# -- TRANSPORT (reused v4.1 engine: redirect, B01 over the production parser) -------


def test_b01_partial_bytes_on_a_phase_d_piece_are_accounted_through_the_production_parser(
    dfx: DFixture, root: Path
) -> None:
    clock = FakeClock()
    op = dfx.plan.fetch.operation("T-01-d00")
    assert op.range is not None

    def partial(call: Any) -> list[tuple[float, bytes]]:
        ok = SyntheticTransport(dfx.fx).ok(call)
        headers = {k: v for k, v in ok._headers.items() if k != "content-length"}
        headers["transfer-encoding"] = "chunked"
        return [(0.0, response_head(206, headers) + chunk(b"PARTIAL"))]

    rule = WireRule(at_op(dfx, "T-01-d00", host=SIGNED), partial)
    wire = WireTransport(dfx.fx, clock, rules=[rule], policy=v41.HOSTS)
    assert_complete(run_d(root, dfx, wire, clock))
    t_rows = [a for a in attempts(root) if a["op_id"] == "T-01-d00"]
    assert [(a["try_number"], a["hop"], a["outcome"]) for a in t_rows] == [
        (1, 0, state.REDIRECT),
        (1, 1, state.TRANSPORT_ERROR),
        (2, 0, state.REDIRECT),
        (2, 1, state.SUCCESS),
    ]
    failed = t_rows[1]
    assert "IncompleteRead" in failed["error"] and failed["response_bytes"] == 7
    assert (root / failed["temp_path"]).read_bytes() == b"PARTIAL"
    success = t_rows[3]
    assert success["response_bytes"] == op.range[1] - op.range[0] + 1


# -- RESTART -------------------------------------------------------------------------


@pytest.mark.parametrize(("op_id", "arm"), [("M-01-d00", "M"), ("T-00-d01", "T")])
def test_crash_mid_range_restarts_as_a_new_attempt_and_completes(
    dfx: DFixture, root: Path, op_id: str, arm: str
) -> None:
    first = transport(dfx, signed_ok(dfx, op_id, crash_after=500))
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, first)
    before = attempts(root)
    [live] = [a for a in before if a["outcome"] == state.IN_PROGRESS]
    assert (live["op_id"], live["hop"], live["response_bytes"]) == (op_id, 1, 0)
    assert (root / live["temp_path"]).stat().st_size == 500
    completed = {a["op_id"] for a in before if a["outcome"] == state.SUCCESS}
    second = transport(dfx)
    assert_complete(run_d(root, dfx, second))
    after = attempts(root)
    interrupted = next(a for a in after if a["attempt_id"] == live["attempt_id"])
    assert (interrupted["outcome"], interrupted["response_bytes"]) == (state.INTERRUPTED, 500)
    retried = [a for a in after if a["op_id"] == op_id and a["attempt_id"] > live["attempt_id"]]
    assert [(a["try_number"], a["outcome"]) for a in retried] == [
        (2, state.REDIRECT),
        (2, state.SUCCESS),
    ]
    requested = {(c.start, c.end) for c in second.calls}
    for done in completed:  # completed ranges are verified and skipped, never re-requested
        assert dfx.plan.fetch.operation(done).range not in requested
    assert all(a["arm"] == arm for a in retried)
    assert (root / live["temp_path"]).stat().st_size == 500  # interrupted body retained


def test_crash_during_decode_resumes_without_requests_and_without_silent_completion(
    dfx: DFixture, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = dec.decode_t_file
    calls = {"n": 0}

    def crash_once(*args: Any) -> dec.TResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise SimulatedCrash("died while decoding T")
        return real(*args)

    monkeypatch.setattr(dec, "decode_t_file", crash_once)
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, transport(dfx))
    assert rows(root, "SELECT status FROM run") == [{"status": "RUNNING"}]
    assert not (root / phase_d.RECEIPT).exists()
    again = transport(dfx)
    assert_complete(run_d(root, dfx, again))
    assert again.calls == []


def test_complete_root_reverifies_and_makes_no_request(dfx: DFixture, root: Path) -> None:
    assert_complete(run_d(root, dfx, transport(dfx)))
    again = transport(dfx)
    assert_complete(run_d(root, dfx, again))
    assert again.calls == []
    with open(root / phase_d.M_OUTPUT, "ab") as sink:
        sink.write(b"{}\n")
    result = run_d(root, dfx, transport(dfx))
    assert_stopped(result, "drifted from its manifest")
    with pytest.raises(phase_d.RefusedError, match="STOPPED"):
        run_d(root, dfx, transport(dfx))


# -- CAPS ------------------------------------------------------------------------------


def _partial_root(dfx: DFixture, root: Path, op_id: str) -> None:
    """A Phase-D root whose run died at hop 0 of ``op_id`` (earlier operations complete)."""
    crash = Rule(at_op(dfx, op_id, host=ORIGIN), lambda c: SimulatedCrash(op_id))
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, transport(dfx, crash))


def _seed(dfx: DFixture, root: Path, op_id: str, count: int, body: int = 0) -> None:
    """Record ``count`` finished retryable attempts of ``op_id``, each retaining ``body`` bytes."""
    op = dfx.plan.fetch.operation(op_id)
    assert op.range is not None
    digest = hashlib.sha256(b"\x00" * body).hexdigest()
    store = phase_d.PhaseDStore(root / state.DB_NAME, create=False)
    try:
        for _ in range(count):
            attempt_id, temp = store.begin_attempt(
                op=op,
                try_number=1,
                hop=0,
                url=tp.start_url(dfx.plan.fetch.source, op.source_file.file, v41.HOSTS).identity(),
                start=op.range[0],
                end=op.range[1],
                now="2026-09-29T00:00:00Z",
            )
            with open(root / temp, "wb") as sink:
                sink.truncate(body)
            store.finish_attempt(
                attempt_id,
                outcome=state.HTTP_ERROR,
                http_status=503,
                received=body,
                sha256=digest,
                error="seeded",
                now="2026-09-29T00:00:00Z",
            )
    finally:
        store.close()


def _arm_attempts(root: Path, arm: str) -> list[dict[str, Any]]:
    return [a for a in attempts(root) if a["arm"] == arm]


@pytest.mark.parametrize(("arm", "op_id", "cap"), [("M", "M-00-d00", 64), ("T", "T-00-d00", 320)])
@pytest.mark.parametrize(("room", "calls"), [(0, 0), (1, 1)])
def test_arm_attempt_caps(
    dfx: DFixture, root: Path, arm: str, op_id: str, cap: int, room: int, calls: int
) -> None:
    assert pd.ATTEMPTS_PER_ARM_MAX[arm] == cap
    _partial_root(dfx, root, op_id)
    _seed(dfx, root, op_id, cap - room - len(_arm_attempts(root, arm)))
    t = transport(dfx)
    assert_stopped(run_d(root, dfx, t), f"arm {arm}: physical attempt cap {cap} reached")
    assert len(t.calls) == calls  # with one attempt of room: hop 0 only, its redirect is refused
    assert len(_arm_attempts(root, arm)) == cap
    assert (root / phase_d.RECEIPT).exists()


@pytest.mark.parametrize(
    ("arm", "op_id", "cap"), [("M", "M-00-d00", 67108864), ("T", "T-00-d00", 536870912)]
)
@pytest.mark.parametrize(("slack", "calls"), [(0, 1), (1, 0)])
def test_arm_response_byte_caps_are_never_exceeded(
    dfx: DFixture, root: Path, arm: str, op_id: str, cap: int, slack: int, calls: int
) -> None:
    assert pd.BODY_BYTES_PER_ARM_MAX[arm] == cap
    _partial_root(dfx, root, op_id)
    used = sum(a["response_bytes"] for a in _arm_attempts(root, arm))
    remaining = cap - phase_p.RESPONSE_READ_LIMIT - used + slack
    full, rest = divmod(remaining, pd.PIECE_BYTES)  # realistic <=4 MiB retained bodies
    _seed(dfx, root, op_id, full, pd.PIECE_BYTES)
    _seed(dfx, root, op_id, 1, rest)
    t = transport(dfx)
    try:
        assert_stopped(run_d(root, dfx, t), f"under the {cap}-byte arm cap")
        assert len(t.calls) == calls
        assert sum(a["response_bytes"] for a in _arm_attempts(root, arm)) <= cap
    finally:
        shutil.rmtree(root / "tmp", ignore_errors=True)  # release the seeded ballast


def test_single_response_over_4_mib_stops(dfx: DFixture, root: Path) -> None:
    huge = b"\x00" * (pd.PIECE_BYTES + 4096)
    rule = signed_ok(dfx, "T-01-d00", body=huge, headers={"Content-Length": None})
    assert_stopped(run_d(root, dfx, transport(dfx, rule)), "exceeds 4194304")
    last = attempts(root)[-1]
    assert (last["outcome"], last["response_bytes"]) == (
        state.CAP_EXCEEDED,
        pd.PIECE_BYTES + 1,
    )


@pytest.mark.parametrize(("over", "calls"), [(0, 5 * 2), (1, 0)])
def test_execution_root_cap_of_1_gib(
    dfx: DFixture, root: Path, monkeypatch: pytest.MonkeyPatch, over: int, calls: int
) -> None:
    limit = pd.ROOT_BYTES_MAX - phase_p.RESPONSE_READ_LIMIT + over
    monkeypatch.setattr(phase_d, "_root_bytes", lambda _: limit)
    t = transport(dfx)
    result = run_d(root, dfx, t)
    if over:
        assert_stopped(result, f"the {pd.ROOT_BYTES_MAX}-byte root cap")
    else:
        assert_complete(result)  # exactly at the boundary: allowed; outputs still fit
    assert len(t.calls) == calls


def test_decoded_outputs_also_respect_the_root_cap(
    dfx: DFixture, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = phase_d._root_bytes

    def measured(path: Path) -> int:
        caller = sys._getframe(1).f_code.co_name
        return pd.ROOT_BYTES_MAX - 1024 if caller == "_decode_arms" else real(path)

    monkeypatch.setattr(phase_d, "_root_bytes", measured)
    result = run_d(root, dfx, transport(dfx))
    assert_stopped(result, "decoded outputs would exceed the 1073741824-byte root cap")
    assert {result.arms[a]["status"] for a in "MT"} == {"INCOMPLETE"}
    assert not (root / phase_d.M_OUTPUT).exists() and not (root / phase_d.T_DOCUMENTS).exists()


def test_insufficient_free_disk_refuses_before_creating_the_root(
    dfx: DFixture, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(phase_d, "_free_bytes", lambda _: pd.FREE_DISK_BYTES_MIN - 1)
    with pytest.raises(phase_d.RefusedError, match="2 GiB"):
        run_d(root, dfx, transport(dfx))
    assert not root.exists()
    assert pd.FREE_DISK_BYTES_MIN == 2 * 1024**3


# -- PARENT ----------------------------------------------------------------------------


def _flip(path: Path) -> None:
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0x01]))


@pytest.mark.parametrize(
    "tamper",
    [
        lambda p: _flip(p / "payload/T-00-footer.bin"),
        lambda p: (p / "phase_p_receipt.json").write_bytes(
            (p / "phase_p_receipt.json").read_bytes().replace(b"COMPLETE", b"COMPLETX", 1)
        ),
        lambda p: (p / "payload/M-01-footer.bin").unlink(),
        lambda p: _flip(p / "state.sqlite"),
    ],
)
def test_phase_d_refuses_a_different_phase_p_parent(
    dfx: DFixture, root: Path, tmp_path: Path, tamper: Any
) -> None:
    parent = tmp_path / "parent-copy"
    shutil.copytree(dfx.parent, parent)
    tamper(parent)
    t = transport(dfx)
    with pytest.raises(phase_d.RefusedError, match="Phase-P parent differs"):
        run_d(root, dfx, t, parent=parent)
    assert t.calls == [] and not root.exists()


def test_parent_drift_during_the_run_stops_before_decode(
    dfx: DFixture, root: Path, tmp_path: Path
) -> None:
    parent = tmp_path / "parent-copy"
    shutil.copytree(dfx.parent, parent)
    footer = parent / "payload/T-01-footer.bin"

    def drift(position: int) -> None:
        if position == 0:
            footer.write_bytes(b"\x00" + footer.read_bytes()[1:])

    rule = signed_ok(dfx, "T-01-d00", on_read=drift)
    assert_stopped(run_d(root, dfx, transport(dfx, rule), parent=parent), "Phase-P parent changed")


def test_the_phase_p_parent_is_never_mutated(dfx: DFixture, root: Path) -> None:
    before = tree(dfx.parent)
    first = transport(dfx, signed_ok(dfx, "T-00-d00", crash_after=1000))
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, first)
    assert_complete(run_d(root, dfx, transport(dfx)))
    assert tree(dfx.parent) == before


# -- OUTPUT ----------------------------------------------------------------------------


def test_complete_outputs_are_hash_bound_to_plan_parents_and_operations(
    dfx: DFixture, root: Path
) -> None:
    result = run_d(root, dfx, transport(dfx))
    assert_complete(result)
    receipt = load(root, phase_d.RECEIPT)
    manifest = load(root, phase_d.MANIFEST)
    for doc in (
        receipt,
        manifest,
        load(root, phase_d.M_MANIFEST),
        load(root, phase_d.T_MANIFEST),
        load(root, phase_d.T_PROVENANCE),
    ):
        assert (doc["plan_digest"], doc["dry_plan_digest"], doc["protocol_version"]) == (
            dfx.plan.digest,
            dfx.plan.parents.dry_plan_digest,
            pd.PROTOCOL_VERSION,
        )
        assert doc["phase_p_parent"]["plan_digest"] == dfx.fx.plan.digest
        assert (
            doc["phase_p_parent"]["receipt_digest"]
            == json.loads((dfx.parent / "phase_p_receipt.json").read_bytes())["digest"]
        )
        assert (doc["selection_digest"], doc["scientific_namespace"]) == (
            frozen.SELECTION_DIGEST,
            frozen.SCIENTIFIC_NAMESPACE,
        )
    assert receipt["status"] == "COMPLETE" and manifest["receipt_digest"] == receipt["digest"]
    for rel, entry in manifest["artifacts"].items():
        raw = (root / rel).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (entry["bytes"], entry["sha256"])
    assert {k for k in manifest["artifacts"] if k.startswith("payload/")} == {
        f"payload/{op.op_id}.bin" for op in dfx.plan.fetch.operations
    }
    assert (
        set(receipt["outputs"])
        == {
            phase_d.M_OUTPUT,
            phase_d.M_MANIFEST,
            phase_d.T_DOCUMENTS,
            phase_d.T_PROVENANCE,
            phase_d.T_MANIFEST,
        }
        <= set(manifest["artifacts"])
    )
    table = attempts(root)
    lines = (root / phase_d.REQUESTS).read_bytes().splitlines()
    assert [json.loads(line) for line in lines] == table
    decoded = rows(root, "SELECT name, arm, bytes, sha256 FROM decoded_outputs")
    assert {r["name"]: {k: r[k] for k in ("arm", "bytes", "sha256")} for r in decoded} == receipt[
        "outputs"
    ]
    for arm in "MT":
        arm_rows = [a for a in table if a["arm"] == arm]
        assert receipt["totals"][arm]["physical_attempts"] == len(arm_rows)
        assert receipt["totals"][arm]["response_body_bytes"] == sum(
            a["response_bytes"] for a in arm_rows
        )
    ranges = [op["range"] for op in receipt["operations"]]
    assert ranges == [list(op.range or ()) for op in dfx.plan.fetch.operations]
    assert receipt["human_review"] == receipt["scientific_scoring"] == "NOT PERFORMED"


def test_t_acquisition_stop_leaves_m_complete_and_overall_incomplete(
    dfx: DFixture, root: Path
) -> None:
    bad = signed_ok(dfx, "T-01-d00", headers={"ETag": '"not-the-frozen-etag"'})
    result = run_d(root, dfx, transport(dfx, bad))
    assert_stopped(result, "T-01-d00: response ETag differs")
    assert result.arms["M"]["status"] == "COMPLETE"
    assert result.arms["T"]["status"] == "INCOMPLETE"
    assert "acquisition incomplete: 2/3" in result.arms["T"]["reason"]
    assert (root / phase_d.M_OUTPUT).exists() and not (root / phase_d.T_DOCUMENTS).exists()
    receipt = load(root, phase_d.RECEIPT)
    assert receipt["status"] == "INCOMPLETE" and set(receipt["outputs"]) == {
        phase_d.M_OUTPUT,
        phase_d.M_MANIFEST,
    }
    with pytest.raises(phase_d.RefusedError, match="STOPPED"):
        run_d(root, dfx, transport(dfx))


def test_m_decode_failure_makes_the_run_incomplete(
    dfx: DFixture, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any) -> dec.MResult:
        raise dec.DecodeError("M-00: synthetic decode failure")

    monkeypatch.setattr(dec, "decode_m_file", fail)
    result = run_d(root, dfx, transport(dfx))
    assert_stopped(result, "decode: M-00: synthetic decode failure")
    assert (result.arms["M"]["status"], result.arms["T"]["status"]) == ("INCOMPLETE", "COMPLETE")
    assert not (root / phase_d.M_OUTPUT).exists() and (root / phase_d.T_DOCUMENTS).exists()
    assert load(root, phase_d.RECEIPT)["status"] == "INCOMPLETE"


# -- END-TO-END -------------------------------------------------------------------------


def test_synthetic_phase_d_end_to_end_redirect_retry_interruption_restart_complete(
    dfx: DFixture, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parent_before = tree(dfx.parent)
    retry = Rule(
        at_op(dfx, "T-00-d01", host=SIGNED),
        lambda c: FakeResponse(503, {"Retry-After": "1"}, b"busy"),
    )
    crash = signed_ok(dfx, "T-00-d00", crash_after=100000)
    first = transport(dfx, retry, crash)
    with pytest.raises(SimulatedCrash):
        run_d(root, dfx, first)
    interrupted = attempts(root)[-1]
    assert (interrupted["op_id"], interrupted["outcome"]) == ("T-00-d00", state.IN_PROGRESS)
    second = transport(dfx, retry)
    result = run_d(root, dfx, second)
    assert_complete(result)

    planned = {op.range for op in dfx.plan.fetch.operations}
    for call in [*first.calls, *second.calls]:
        assert (call.start, call.end) in planned  # exact planned ranges only
        assert call.host in (ORIGIN, SIGNED) and call.timeout_seconds == 120
    assert {(c.start, c.end) for c in second.calls} == {
        dfx.plan.fetch.operation(o).range for o in ("T-00-d00", "T-00-d01", "T-01-d00")
    }

    table = attempts(root)
    by_outcome = {o: [a for a in table if a["outcome"] == o] for o in {a["outcome"] for a in table}}
    assert len(by_outcome[state.SUCCESS]) == 5
    assert [a["op_id"] for a in by_outcome[state.INTERRUPTED]] == ["T-00-d00"]
    assert by_outcome[state.INTERRUPTED][0]["response_bytes"] == 100000
    assert [(a["op_id"], a["http_status"]) for a in by_outcome[state.HTTP_ERROR]] == [
        ("T-00-d01", 503)
    ]
    payload = sum(op.range[1] - op.range[0] + 1 for op in dfx.plan.fetch.operations if op.range)
    redirects = len(by_outcome[state.REDIRECT])
    assert redirects == 5 + 1 + 1  # one per logical request (+ the retried and resumed tries)
    assert sum(a["response_bytes"] for a in table) == (
        payload + redirects * len(REDIRECT_BODY) + 100000 + len(b"busy")
    )
    receipt = load(root, phase_d.RECEIPT)
    assert receipt["totals"]["all"]["response_body_bytes"] == sum(
        a["response_bytes"] for a in table
    )
    assert receipt["totals"]["all"]["retained_payload_bytes"] == payload
    assert receipt["totals"]["T"]["attempts_by_outcome"] == {
        state.HTTP_ERROR: 1,
        state.INTERRUPTED: 1,
        state.REDIRECT: 5,
        state.SUCCESS: 3,
    }

    records = jsonl(root, phase_d.M_OUTPUT)
    for record in records:
        record.pop(dec.LOCATOR_FIELD)
    assert records == m_oracle(dfx)
    docs = jsonl(root, phase_d.T_DOCUMENTS)
    assert [(d["t_ordinal"], d["row_in_group"]) for d in docs] == [(0, r) for r in T0_SELECTED] + [
        (1, r) for r in T1_SELECTED
    ]
    full = [d for d in docs if d["status"] == "full_text_available"]
    assert [d["text"] for d in full] == [
        selected_text(d["t_ordinal"], d["row_in_group"]) for d in full
    ]
    assert (
        next(d for d in docs if d["row_in_group"] == T1_NULL_ROW and d["t_ordinal"] == 1)["status"]
        == "unreviewable_missing_or_invalid_text"
    )

    manifest = load(root, phase_d.MANIFEST)
    for rel, entry in manifest["artifacts"].items():
        raw = (root / rel).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (entry["bytes"], entry["sha256"])
        if not rel.startswith(("payload/", "tmp/")):
            assert SENTINEL.encode() not in raw
    assert f"tmp/{interrupted['op_id']}.a{interrupted['attempt_id']}.part" in manifest["artifacts"]
    assert SENTINEL not in "".join(capsys.readouterr())
    assert tree(dfx.parent) == parent_before

    third = transport(dfx)
    assert_complete(run_d(root, dfx, third))
    assert third.calls == []


# -- CLI --------------------------------------------------------------------------------


def _cli() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "evidence_v41_phase_d", SCRIPTS / "evidence_v41_phase_d.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_exposes_no_arbitrary_url_file_range_host_root_or_locator_option() -> None:
    cli = _cli()
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert set(sub.choices) == {"verify", "show-plan", "phase-d-status", "phase-d"}
    live = sub.choices["phase-d"]
    options = {o for a in live._actions for o in a.option_strings}
    assert options == {"-h", "--help", "--confirm-plan-digest"}
    for command in ("verify", "show-plan", "phase-d-status"):
        assert {o for a in sub.choices[command]._actions for o in a.option_strings} == {
            "-h",
            "--help",
        }
    digest = ["--confirm-plan-digest", pd.PLAN_DIGEST]
    for extra in (
        ["--url", "https://huggingface.co/x"],
        ["--file", "data/x.parquet"],
        ["--range", "0-3"],
        ["--etag", '"x"'],
        ["--host", "evil.hf.co"],
        ["--root", "C:\\x"],
        ["--locator", "x"],
        ["--plan", "p.json"],
        ["--force"],
        ["--confirm"],  # no abbreviation of the confirmation option
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(["phase-d", *digest, *extra])
    with pytest.raises(SystemExit):
        parser.parse_args(["phase-d"])


def test_cli_refuses_a_wrong_digest_and_shows_the_exact_plan(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cli = _cli()
    monkeypatch.setattr(phase_d, "live_root", lambda: tmp_path / "live")
    assert cli.main(["phase-d", "--confirm-plan-digest", v41.PLAN_DIGEST]) == 1
    assert "refused" in capsys.readouterr().err and not (tmp_path / "live").exists()
    assert cli.main(["show-plan"]) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "_RANGE" in line]
    assert len(lines) == 55
    assert sum("M_PROJECTED_RANGE" in line for line in lines) == 8
    assert "M rows 4096; T locators 118" in out
    assert cli.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["plan_digest"] == pd.PLAN_DIGEST
    assert cli.main(["phase-d-status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "NOT_STARTED" and not Path(pd.EXECUTION_ROOT).exists()
