"""Independent offline review probes. Only authored synthetic data are decoded."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import socket
import sys
from dataclasses import replace
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "tests"))

from evidence_v4_support import SimulatedCrash, SyntheticTransport
from evidence_v41_phase_d_support import SENTINEL, build_dfixture, run_d, tree
from xlm.data.evidence_v4 import phase_d, phase_p
from xlm.data.evidence_v4 import phase_d_decode as dec
from xlm.data.evidence_v4 import phase_d_plan as pd


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical({k: v for k, v in value.items() if k != "digest"})).hexdigest()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network forbidden by authorization review")
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    return build_dfixture(tmp_path_factory.mktemp("independent-phase-d-fixture"))


def test_independent_frozen_hashes_ranges_and_scientific_identity():
    base = REPO / pd.EVIDENCE_DIR
    hashes = {}
    for path in [*sorted(base.glob("*.json")), REPO / pd.DRY_PLAN_PATH]:
        raw = path.read_bytes()
        obj = json.loads(raw)
        if path.name == "phase_d_plan.json":
            assert canonical(obj) == raw
        assert digest(obj) == obj["digest"]
        hashes[path.name] = {"digest": digest(obj), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    assert hashes["freeze.json"]["digest"] == "9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228"
    assert hashes["phase_d_plan.json"]["digest"] == "23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7"
    assert hashes["phase_d_dry_plan.json"]["digest"] == "ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356"
    protocol = (REPO / pd.PROTOCOL_PATH).read_bytes()
    assert hashlib.sha256(protocol).hexdigest() == "bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f"
    obj = json.loads((base / "phase_d_plan.json").read_bytes())
    dry = json.loads((REPO / pd.DRY_PLAN_PATH).read_bytes())
    assert obj["scientific_namespace"] == "essential-web-evidence-v2.0"
    assert obj["selection_digest"] == "975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474"
    assert obj["policy_digest"] == "f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07"
    assert obj["source"]["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    for arm, count, size in [("M", 8, 11692530), ("T", 47, 179963169)]:
        files = [f for f in obj["files"] if f["arm"] == arm]
        layout = json.loads((Path(pd.PHASE_P_ROOT) / f"{arm.lower()}_phase_p_layout.json").read_bytes())
        ops = [o for o in obj["operations"] if o["arm"] == arm]
        assert len(ops) == count
        assert sum(o["range"][1] + 1 - o["range"][0] for o in ops) == size
        for f, parent, previous in zip(files, layout["files"], dry["files"][arm], strict=True):
            assert f["file"] == parent["file"] == previous["file"]
            ranges = [o["range"] for o in ops if o["ordinal"] == f["ordinal"]]
            assert ranges == [[r["start"], r["end_exclusive"] - 1] for r in previous["request_ranges"]]
            if arm == "M":
                chunks = parent["layout"]["projected_chunks"]
                assert len(chunks) == 81
                assert all(a["end_exclusive"] == b["start"] for a, b in zip(chunks, chunks[1:]))
                assert ranges == [[chunks[0]["start"], chunks[-1]["end_exclusive"] - 1]]
                assert f["projection"] == ["eai_taxonomy", "quality_signals"]
                assert f["window"] == parent["layout"]["window"] == previous["window"]
                assert f["window"][1] - f["window"][0] == 512
            else:
                chunk = parent["layout"]["selected_text_chunk"]
                assert ranges[0][0] == f["dictionary_page_offset"] == chunk["start"]
                assert f["dictionary_page_offset"] < f["data_page_offset"]
                assert ranges[-1][1] + 1 == chunk["end_exclusive"]
                assert all(a[1] + 1 == b[0] for a, b in zip(ranges, ranges[1:]))
                assert len(ranges) == [6, 6, 5, 6, 6, 6, 6, 6][f["ordinal"]]
                assert f["locators"] == previous["locators"]
    selected = [tuple(loc["identity"]) for f in obj["files"] if f["arm"] == "T" for loc in f["locators"]]
    assert len(selected) == len(set(selected)) == 118
    selection_raw = Path("F:/Project/xlm-evidence-v2/essential-web/text_selection_manifest.json").read_bytes()
    selection = json.loads(selection_raw)
    assert digest(selection) == obj["selection_digest"] == selection["digest"]
    units = [u for cell in selection["cells"] for u in ([cell] if "identities" in cell else cell["crawls"])]
    identities = [tuple(i) for u in units for i in u["identities"]]
    assert len(identities) == 118 and set(identities) == set(selected)
    parent = obj["parents"]["phase_p"]
    for name, binding in parent["artifacts"].items():
        raw = (Path(pd.PHASE_P_ROOT) / name).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (binding["bytes"], binding["sha256"])
        if "digest" in binding:
            assert digest(json.loads(raw)) == binding["digest"]
    receipt = json.loads((Path(pd.PHASE_P_ROOT) / "phase_p_receipt.json").read_bytes())
    assert receipt["status"] == receipt["run_status"] == "COMPLETE"
    assert len(receipt["operations"]) == 40
    assert all(o["status"] == "COMPLETE" for o in receipt["operations"])
    (HERE / "independent-bindings.json").write_text(json.dumps(hashes, indent=2) + "\n")


@pytest.mark.parametrize("arm", ["M", "T"])
@pytest.mark.parametrize("change", ["widen", "inject"])
def test_arbitrary_range_injection_refused(fixture, arm, change):
    obj = copy.deepcopy(fixture.plan_obj)
    op = next(o for o in obj["operations"] if o["arm"] == arm)
    if change == "widen":
        op["range"][0] -= 1
    else:
        obj["operations"].append(copy.deepcopy(op))
    obj["digest"] = digest(obj)
    with pytest.raises(pd.PlanError, match="operations differ"):
        pd.validate_plan(obj, synthetic=True)


def test_real_parent_footer_offsets_metadata_only():
    """Structural metadata only: no statistics, key-value metadata or column reads."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    plan = pd.load_committed_plan()
    parent = Path(pd.PHASE_P_ROOT)
    for f in [*plan.m_files, *plan.t_files]:
        footer = (parent / f.footer_payload).read_bytes()
        if isinstance(f, pd.TFile):
            trailer = (parent / f.trailer_payload).read_bytes()
        else:
            footer, trailer = footer[:-8], footer[-8:]
        meta = pq.ParquetFile(pa.BufferReader(b"PAR1" + footer + trailer),
                              pre_buffer=False, thrift_string_size_limit=33554432,
                              thrift_container_size_limit=33554432).metadata
        first = sum(meta.row_group(i).num_rows for i in range(f.row_group))
        group = meta.row_group(f.row_group)
        assert (first, group.num_rows) == (f.row_group_first_row, f.row_group_rows)
        columns = [group.column(i) for i in range(group.num_columns)]
        if isinstance(f, pd.TFile):
            text = [c for c in columns if c.path_in_schema == "text"]
            assert len(text) == 1
            column = text[0]
            assert column.dictionary_page_offset == f.dictionary_page_offset
            assert column.data_page_offset == f.data_page_offset
            assert (column.dictionary_page_offset, column.dictionary_page_offset + column.total_compressed_size) == f.span
        else:
            projected = [c for c in columns if c.path_in_schema.split(".")[0] in ("eai_taxonomy", "quality_signals")]
            actual = sorted((min(c.data_page_offset, c.dictionary_page_offset)
                             if c.dictionary_page_offset and c.dictionary_page_offset > 0
                             else c.data_page_offset, c.path_in_schema, c.total_compressed_size)
                            for c in projected)
            assert actual == [(c.start, c.path, c.compressed_bytes) for c in f.chunks]
            assert len(actual) == 81


@pytest.mark.parametrize("kind", ["missing", "duplicate", "reordered", "misaligned"])
def test_m_identity_refusal(fixture, kind):
    f = fixture.plan.m_files[0]
    raw = fixture.fx.data[f.file]
    good = dec.decode_m_file(f, raw[slice(*f.range_half_open)],
                             (fixture.parent / f.footer_payload).read_bytes(), fixture.plan.fetch.source)
    changed = {"missing": good.rows[:-1], "duplicate": (*good.rows[:-1], good.rows[0]),
               "reordered": good.rows[::-1], "misaligned": tuple(r + 1 for r in good.rows)}[kind]
    with pytest.raises(dec.DecodeError):
        dec.assemble_m([f], [replace(good, rows=changed)])


@pytest.mark.parametrize("kind", ["missing", "duplicate", "reordered"])
def test_t_locator_refusal(fixture, kind):
    f = fixture.plan.t_files[0]
    good = tuple(dec.classify(b"authored", loc, f.ordinal, f.row_group) for loc in f.locators)
    changed = {"missing": good[:-1], "duplicate": (*good[:-1], good[0]), "reordered": good[::-1]}[kind]
    with pytest.raises(dec.DecodeError):
        dec.assemble_t([f], [dec.TResult(f.ordinal, changed, f.row_group_rows, 1)])


def test_wrong_parent_manifest_refuses_before_root(fixture, tmp_path):
    parent = tmp_path / "synthetic-parent"
    shutil.copytree(fixture.parent, parent)
    manifest = parent / phase_d.MANIFEST
    manifest.write_bytes(manifest.read_bytes() + b" ")
    transport = SyntheticTransport(fixture.fx)
    with pytest.raises(phase_d.RefusedError, match="artifact_manifest.json"):
        run_d(tmp_path / "d", fixture, transport, parent=parent)
    assert not (tmp_path / "d").exists() and not transport.calls


def test_wrong_plan_digest_refuses():
    with pytest.raises(phase_d.RefusedError, match="plan digest"):
        phase_d.run_live(confirm_plan_digest="0" * 64)
    assert not Path(pd.EXECUTION_ROOT).exists()


@pytest.mark.parametrize("arm", ["M", "T"])
def test_pyarrow_out_of_range_stops_without_extra_acquisition(fixture, tmp_path, monkeypatch, arm):
    original = dec._open
    def force_padding(source, meta):
        reader = original(source, meta)
        # Simulate a PyArrow old-writer padding read at the actual sparse boundary.
        source.seek(source._segments[0][0] - 1)
        source.read(1)
        return reader
    target = dec.decode_m_file if arm == "M" else dec.decode_t_file
    def padded(*args, **kwargs):
        with monkeypatch.context() as local:
            local.setattr(dec, "_open", force_padding)
            return target(*args, **kwargs)
    monkeypatch.setattr(dec, "decode_m_file" if arm == "M" else "decode_t_file", padded)
    transport = SyntheticTransport(fixture.fx)
    result = run_d(tmp_path / "d", fixture, transport)
    assert result.status == "INCOMPLETE" and result.run_status == "STOPPED"
    assert "outside the acquired ranges" in result.arms[arm]["reason"]
    assert len(transport.calls) == len(fixture.plan.fetch.operations) * 2
    expected = {op.range for op in fixture.plan.fetch.operations}
    assert all((call.start, call.end) in expected for call in transport.calls)


def test_crash_decode_restart_exact_outputs_no_leakage(fixture, tmp_path, monkeypatch, capsys, caplog):
    root = tmp_path / "d"
    before = tree(fixture.parent)
    def crash(*args):
        raise SimulatedCrash("review decode crash")
    with monkeypatch.context() as local:
        local.setattr(dec, "decode_m_file", crash)
        with pytest.raises(SimulatedCrash):
            run_d(root, fixture, SyntheticTransport(fixture.fx))
    transport = SyntheticTransport(fixture.fx)
    result = run_d(root, fixture, transport)
    assert result.status == "COMPLETE" and not transport.calls
    for path in root.rglob("*"):
        if path.is_file() and path.relative_to(root).parts[0] not in ("tmp", "payload"):
            assert SENTINEL.encode() not in path.read_bytes()
    assert SENTINEL not in capsys.readouterr().out + caplog.text
    assert len((root / phase_d.M_OUTPUT).read_bytes().splitlines()) == 30
    documents = [json.loads(line) for line in (root / phase_d.T_DOCUMENTS).read_bytes().splitlines()]
    assert [tuple(d["locator"]) for d in documents] == [loc.identity for f in fixture.plan.t_files for loc in f.locators]
    assert tree(fixture.parent) == before
    again = SyntheticTransport(fixture.fx)
    assert run_d(root, fixture, again).status == "COMPLETE" and not again.calls


@pytest.mark.parametrize("free", [pd.FREE_DISK_BYTES_MIN - 1, pd.FREE_DISK_BYTES_MIN])
def test_free_space_boundary(fixture, tmp_path, monkeypatch, free):
    monkeypatch.setattr(phase_d, "_free_bytes", lambda _: free)
    transport = SyntheticTransport(fixture.fx)
    if free < pd.FREE_DISK_BYTES_MIN:
        with pytest.raises(phase_d.RefusedError):
            run_d(tmp_path / "d", fixture, transport)
        assert not transport.calls and not (tmp_path / "d").exists()
    else:
        assert run_d(tmp_path / "d", fixture, transport).status == "COMPLETE"


def test_root_cap_includes_exports_at_decode_boundary(fixture, tmp_path, monkeypatch):
    """Simulate existing storage so decoded bundles fit exactly; include later exports."""
    root = tmp_path / "d"
    original_decode = phase_d._PhaseDEngine._decode
    original_size = phase_d._root_bytes
    outputs = {}
    occupancy = {}
    original_exports = phase_d._PhaseDEngine._write_exports
    def decode(engine, arm):
        outcome = original_decode(engine, arm)
        outputs[arm] = outcome
        if arm == "T":
            output_size = sum(len(raw) for a in outputs.values() for raw in a.files.values())
            occupancy["base"] = pd.ROOT_BYTES_MAX - output_size - original_size(root)
            occupancy["output_size"] = output_size
        return outcome
    def size(path):
        return original_size(path) + occupancy.get("base", 0)
    def exports(engine):
        original_exports(engine)
        occupancy["after_exports_before_store_close"] = size(root)
    monkeypatch.setattr(phase_d._PhaseDEngine, "_decode", decode)
    monkeypatch.setattr(phase_d, "_root_bytes", size)
    monkeypatch.setattr(phase_d._PhaseDEngine, "_write_exports", exports)
    result = run_d(root, fixture, SyntheticTransport(fixture.fx))
    final = size(root)
    record = {"simulation": "constant pre-existing occupancy added to measured root bytes at decode boundary",
              "cap": pd.ROOT_BYTES_MAX, "final_accounted_bytes": final,
              "excess_bytes": final - pd.ROOT_BYTES_MAX, "result": result.status,
              "run_status": result.run_status, "decoded_output_bytes": occupancy["output_size"],
              "after_exports_before_store_close": occupancy["after_exports_before_store_close"]}
    (HERE / "cap-boundary-observation.json").write_text(json.dumps(record, indent=2) + "\n")
    assert max(final, occupancy["after_exports_before_store_close"]) <= pd.ROOT_BYTES_MAX, record


def test_parent_manifest_drift_during_run_stops(fixture, tmp_path, monkeypatch):
    """Protocol section 1: a parent change during the run must STOP."""
    parent = tmp_path / "synthetic-parent"
    shutil.copytree(fixture.parent, parent)
    original = phase_d._PhaseDEngine._acquire
    def acquire(engine):
        result = original(engine)
        manifest = parent / phase_d.MANIFEST
        manifest.write_bytes(manifest.read_bytes() + b" ")
        return result
    monkeypatch.setattr(phase_d._PhaseDEngine, "_acquire", acquire)
    result = run_d(tmp_path / "d", fixture, SyntheticTransport(fixture.fx), parent=parent)
    (HERE / "parent-drift-observation.json").write_text(json.dumps({
        "synthetic_only": True, "mutation": "append one space to copied parent manifest after acquisition before decoding",
        "result": result.status, "run_status": result.run_status,
        "M_status": result.arms["M"]["status"], "T_status": result.arms["T"]["status"],
        "expected": "STOPPED / INCOMPLETE"}, indent=2) + "\n")
    assert result.run_status == "STOPPED", result
