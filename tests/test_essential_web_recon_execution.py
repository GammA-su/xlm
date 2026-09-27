"""Essential-Web recon execution-v2 + combine (offline, authored fixtures only).

Plans are produced by the real ``xlm data plan`` CLI (offline); fetch journals
and ``selected_records.jsonl`` parts are authored in the fetcher's formats so
the combine validation logic is exercised without any network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.plan import AcquisitionPlan
from xlm.data.acquisition.progress import AcquisitionState, FileProgress
from xlm.data.acquisition.records import LOCATOR_FIELD

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "essential_web_recon.py"
_SPEC = importlib.util.spec_from_file_location("essential_web_recon_exec", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
recon = importlib.util.module_from_spec(_SPEC)
sys.modules["essential_web_recon_exec"] = recon
_SPEC.loader.exec_module(recon)

REV = recon.PINNED_REVISION
CRAWLS = [f"crawl=CC-MAIN-{y}-{w:02d}" for y in range(2013, 2025) for w in (10, 22, 40)]
FIELDS = "eai_taxonomy,quality_signals"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _listing(path: str) -> list[dict[str, Any]]:
    if path == "data":
        return [{"type": "directory", "path": f"data/{c}"} for c in CRAWLS]
    return [{"type": "file", "path": f"{path}/train-{i:05d}.parquet", "size": 9} for i in range(4)]


def _evidence(file: str, start: int) -> dict[str, Any]:
    return {
        "source_id": "essential_web",
        "view_id": "selector_recon",
        "revision": REV,
        "seed": 20260918,
        "mode": "window",
        "selected_files": [file],
        "projected_fields": ["eai_taxonomy", "quality_signals"],
        "projected_logical_fields": ["eai_taxonomy", "quality_signals"],
        "planned_records": 512,
        "row_ranges": {file: [start, start + 512]},
        "window_policy": {**recon.EXECUTION_WINDOW, "ratio_exempt_bytes": 16777216},
        "windows": [
            {
                "file": file,
                "start_row": start,
                "stop_row": start + 512,
                "row_group": 1,
                "expected_scan_rows": 4096,
                "estimated_requests": 85,
                "estimated_transfer_upper_bytes": 1_640_000,
            }
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _footers(root: Path, manifest: dict[str, Any]) -> None:
    for sel in manifest["selections"]:
        unit, start = f"{sel['stratum']:02d}", 1000 * (sel["stratum"] + 1)
        _write_json(root / f"split/{unit}/rows.json", {sel["file"]: [start, start + 512]})
        _write_json(root / f"split/{unit}/rows.evidence.json", _evidence(sel["file"], start))


@pytest.fixture()
def world(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "recon"
    manifest = recon.build_manifest(_listing, revision=REV, seed=20260918, strata=8)
    _write_json(root / "discovery.json", manifest)
    _footers(root, manifest)
    return {"root": root, "manifest": manifest}


def _execution(world: dict[str, Any]) -> dict[str, Any]:
    return recon.build_execution(world["manifest"], world["root"], world["manifest"]["digest"])


def _record(file: str, row: int) -> bytes:
    record = {
        "eai_taxonomy": {"free_decimal_correspondence": {"primary": {"code": "512.1"}}},
        "quality_signals": {"fasttext": {"english": 0.9}},
        LOCATOR_FIELD: {
            "source_id": "essential_web",
            "repository": recon.REPOSITORY,
            "revision": REV,
            "source_file": file,
            "row_index": row,
            "selection_hash": "s",
        },
    }
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_part(root: Path, unit: dict[str, Any], plan: AcquisitionPlan, data: bytes) -> None:
    raw = root / unit["paths"]["raw"] / "selected_records.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    progress = FileProgress(
        file_path="selected_records.jsonl",
        bytes_downloaded=len(data),
        content_sha256=hashlib.sha256(data).hexdigest(),
        record_count=data.count(b"\n"),
        status="completed",
    )
    state = AcquisitionState(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        status="COMPLETED",
        records_acquired=data.count(b"\n"),
        file_progress={"selected_records.jsonl": progress},
    )
    journal = root / unit["paths"]["scratch"] / "journals" / f"{plan.plan_id}.progress.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(state.model_dump_json(), encoding="utf-8")


def _plan_cli(root: Path, catalog: Path, unit: dict[str, Any]) -> list[str]:
    w = recon.EXECUTION_WINDOW
    return [
        "plan", "--source", "essential_web", "--view", "selector_recon", "--catalog", str(catalog),
        "--files", unit["file"], "--mode", "selected_records",
        "--row-ranges", str(root / unit["paths"]["rows"]), "--project-fields", FIELDS,
        "--seed", "20260918", "--attempt", "1",
        "--parquet-window-scan-rows", str(w["max_window_scan_rows"]),
        "--parquet-window-buffer-bytes", str(w["stream_buffer_bytes"]),
        "--parquet-window-batch-rows", str(w["batch_rows"]),
        "--parquet-window-policy-version", str(w["policy_version"]),
        "--max-records", "512", "--pilot-approved",
        "--output", str(root / unit["paths"]["plan"]),
    ]  # fmt: skip


@pytest.fixture()
def fetched(
    world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "xlm-home"))
    root = world["root"]
    execution = _execution(world)
    catalog = tmp_path / "catalog.json"
    source = {"candidate_number": 1, "source_id": "essential_web", "provider": "huggingface",
              "repository": recon.REPOSITORY, "revision": REV}  # fmt: skip
    catalog.write_text(json.dumps({"catalog_id": "authored", "sources": [source]}), "utf-8")
    runner = CliRunner()
    for unit in execution["units"]:
        (root / unit["paths"]["plan"]).parent.mkdir(parents=True, exist_ok=True)
        result = runner.invoke(data_app, _plan_cli(root, catalog, unit))
        assert result.exit_code == 0, result.output
        plan = AcquisitionPlan.model_validate_json((root / unit["paths"]["plan"]).read_bytes())
        start, stop = unit["row_range"]
        _write_part(
            root, unit, plan, b"".join(_record(unit["file"], r) for r in range(start, stop))
        )
    return {**world, "execution": execution}


def _unit_plan(fx: dict[str, Any], index: int) -> tuple[dict[str, Any], Path, AcquisitionPlan]:
    unit = fx["execution"]["units"][index]
    path = fx["root"] / unit["paths"]["plan"]
    return unit, path, AcquisitionPlan.model_validate_json(path.read_bytes())


def _rewrite_plan(fx: dict[str, Any], index: int, **changes: Any) -> None:
    """Tamper one plan field and re-seal its hash, so the specific check fires."""
    unit, path, _ = _unit_plan(fx, index)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    plan = AcquisitionPlan.model_validate(data)
    plan = plan.model_copy(update={"plan_hash": plan.compute_behavioral_hash()})
    path.write_text(plan.model_dump_json(), encoding="utf-8")
    raw = fx["root"] / unit["paths"]["raw"] / "selected_records.jsonl"
    _write_part(fx["root"], unit, plan, raw.read_bytes())


def _replace_part(fx: dict[str, Any], index: int, data: bytes) -> None:
    unit, _, plan = _unit_plan(fx, index)
    _write_part(fx["root"], unit, plan, data)


# ------------------------------------------------------------- execution


def test_execution_binds_discovery_without_mutating_it(world: dict[str, Any]) -> None:
    before = (world["root"] / "discovery.json").read_bytes()
    execution = _execution(world)
    assert (world["root"] / "discovery.json").read_bytes() == before
    assert recon.load_manifest(world["root"] / "discovery.json")["projection"] == [
        "eai_taxonomy", "quality_signals", "id", "pid", "metadata"
    ]  # fmt: skip
    assert execution["parent_discovery_digest"] == world["manifest"]["digest"]
    assert execution["execution_version"] == "essential-recon-execution-v2"
    assert execution["projection"] == ["eai_taxonomy", "quality_signals"]
    assert execution["digest"] == recon.manifest_digest(execution)
    assert "104" in execution["narrowing_rationale"] and "85" in execution["narrowing_rationale"]
    assert "not production" in execution["probe_limitation"].lower()
    with pytest.raises(recon.ReconError, match="expected"):
        recon.build_execution(world["manifest"], world["root"], "0" * 64)


def test_eight_independent_units_in_stratum_order(world: dict[str, Any]) -> None:
    execution = _execution(world)
    units = execution["units"]
    assert len(units) == 8 and execution["total_target_records"] == 4096
    assert sum(u["records"] for u in units) == 4096
    assert execution["plan_shape"].startswith("one independent")
    assert execution["pilot_max_requests_per_plan"] == 100
    for sel, unit in zip(world["manifest"]["selections"], units, strict=True):
        assert (unit["stratum"], unit["crawl"], unit["file"]) == (
            sel["stratum"], sel["crawl"], sel["file"]
        )  # fmt: skip
        assert unit["stratum_first"] == sel["stratum_first"]
        assert len({unit["paths"]["plan"] for unit in units}) == 8  # one plan per unit


def test_execution_write_is_restart_safe(world: dict[str, Any], tmp_path: Path) -> None:
    root, out = world["root"], world["root"] / "execution.json"
    args = ["execution", "--discovery", str(root / "discovery.json"),
            "--expect-discovery-digest", world["manifest"]["digest"],
            "--root", str(root), "--output", str(out)]  # fmt: skip
    assert recon.main(args) == 0
    first = out.read_bytes()
    assert recon.main(args) == 0 and out.read_bytes() == first  # adopted
    out.write_bytes(first.replace(b'"records_per_unit": 512', b'"records_per_unit": 513'))
    assert recon.main(args) == 2  # mismatched existing artifact refuses, untouched
    assert b"513" in out.read_bytes()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("projected_logical_fields", ["eai_taxonomy", "quality_signals", "id", "pid", "metadata"]),
        ("revision", "f" * 40),
        ("planned_records", 4096),
        ("window_policy", {**recon.EXECUTION_WINDOW, "policy_version": 1}),
    ],
)
def test_incompatible_footer_refuses(world: dict[str, Any], key: str, value: Any) -> None:
    path = world["root"] / "split/03/rows.evidence.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence[key] = value
    _write_json(path, evidence)
    with pytest.raises(recon.ReconError, match="footer"):
        _execution(world)


def test_footer_rows_mismatch_and_missing_refuse(world: dict[str, Any]) -> None:
    sel = world["manifest"]["selections"][2]
    _write_json(world["root"] / "split/02/rows.json", {sel["file"]: [0, 512]})
    with pytest.raises(recon.ReconError):
        _execution(world)
    (world["root"] / "split/02/rows.json").unlink()
    with pytest.raises(recon.ReconError, match="missing"):
        _execution(world)


# ------------------------------------------------------------- combine


def test_combine_preserves_bytes_and_order(fetched: dict[str, Any]) -> None:
    combined, receipt = recon.combine(fetched["execution"], fetched["root"])
    parts = [
        (fetched["root"] / u["paths"]["raw"] / "selected_records.jsonl").read_bytes()
        for u in fetched["execution"]["units"]
    ]
    assert combined == b"".join(parts)
    assert combined.count(b"\n") == 4096 and receipt["total_records"] == 4096
    assert receipt["part_count"] == 8 and [p["stratum"] for p in receipt["parts"]] == list(range(8))
    assert receipt["execution_digest"] == fetched["execution"]["digest"]
    assert receipt["combined_sha256"] == hashlib.sha256(combined).hexdigest()
    assert len({p["plan_hash"] for p in receipt["parts"]}) == 8


def test_combine_cli_writes_and_adopts(fetched: dict[str, Any]) -> None:
    root = fetched["root"]
    exec_path = root / "execution.json"
    exec_path.write_text(json.dumps(fetched["execution"]), encoding="utf-8")
    args = ["combine", "--execution", str(exec_path), "--root", str(root),
            "--output", str(root / "raw/selected_records.jsonl"),
            "--receipt", str(root / "bundle.json")]  # fmt: skip
    assert recon.main(args) == 0
    assert recon.main(args) == 0  # restart adopts identical outputs
    (root / "raw/selected_records.jsonl").write_bytes(b"tampered\n")
    assert recon.main(args) == 2


def test_combine_refuses_wrong_source_file(fetched: dict[str, Any]) -> None:
    other = fetched["execution"]["units"][1]["file"]
    bound = fetched["execution"]["units"][0]["row_range"]
    _rewrite_plan(fetched, 0, selected_files=[other], row_ranges={other: bound})
    with pytest.raises(recon.ReconError, match="selected_files"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_record_from_wrong_file(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][0]
    start, stop = unit["row_range"]
    rows = [_record(unit["file"], r) for r in range(start, stop - 1)]
    _replace_part(fetched, 0, b"".join(rows) + _record("data/x.parquet", stop - 1))
    with pytest.raises(recon.ReconError, match="wrong source file"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_revision(fetched: dict[str, Any]) -> None:
    _rewrite_plan(fetched, 4, revision="f" * 40)
    with pytest.raises(recon.ReconError, match="revision"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_plan_hash(fetched: dict[str, Any]) -> None:
    _, path, _ = _unit_plan(fetched, 2)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["plan_hash"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(recon.ReconError, match="plan"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_projection(fetched: dict[str, Any]) -> None:
    _rewrite_plan(fetched, 1, projected_fields=["eai_taxonomy", "quality_signals", "text"])
    with pytest.raises(recon.ReconError, match="projected_fields"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_window_policy(fetched: dict[str, Any]) -> None:
    _, path, _ = _unit_plan(fetched, 5)
    window = json.loads(path.read_text(encoding="utf-8"))["parquet_window"]
    _rewrite_plan(fetched, 5, parquet_window={**window, "policy_version": 1})
    with pytest.raises(recon.ReconError, match="window policy"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_row_range(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][6]
    start, stop = unit["row_range"]
    _rewrite_plan(fetched, 6, row_ranges={unit["file"]: [start + 1, stop + 1]})
    with pytest.raises(recon.ReconError, match="row_ranges"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_row_outside_range(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][6]
    start, stop = unit["row_range"]
    _replace_part(fetched, 6, b"".join(_record(unit["file"], r + 1) for r in range(start, stop)))
    with pytest.raises(recon.ReconError, match="outside range"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_wrong_count(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][7]
    start, stop = unit["row_range"]
    _replace_part(fetched, 7, b"".join(_record(unit["file"], r) for r in range(start, stop - 1)))
    with pytest.raises(recon.ReconError, match="records"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_duplicate_locator(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][3]
    start, stop = unit["row_range"]
    rows = [_record(unit["file"], r) for r in range(start, stop - 1)]
    _replace_part(fetched, 3, b"".join(rows) + _record(unit["file"], start))
    with pytest.raises(recon.ReconError, match="duplicate locator"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_corrupt_or_drifted_part(fetched: dict[str, Any]) -> None:
    unit = fetched["execution"]["units"][0]
    raw = fetched["root"] / unit["paths"]["raw"] / "selected_records.jsonl"
    data = raw.read_bytes()
    raw.write_bytes(data[:-3] + b"}x\n")  # drifts from the journal digest
    with pytest.raises(recon.ReconError, match="journal digest"):
        recon.combine(fetched["execution"], fetched["root"])
    _replace_part(fetched, 0, data[:-3] + b"}x\n")  # journal re-sealed, JSON still corrupt
    with pytest.raises(recon.ReconError, match="corrupt JSONL"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combine_refuses_missing_or_duplicated_part(fetched: dict[str, Any]) -> None:
    execution = dict(fetched["execution"])
    execution["units"] = [*execution["units"][:7], execution["units"][6]]
    with pytest.raises(recon.ReconError, match="stratum"):
        recon.combine(execution, fetched["root"])
    unit = fetched["execution"]["units"][4]
    (fetched["root"] / unit["paths"]["raw"] / "selected_records.jsonl").unlink()
    with pytest.raises(recon.ReconError, match="missing"):
        recon.combine(fetched["execution"], fetched["root"])


def test_combined_bundle_analyzes_without_id_pid_metadata_or_text(fetched: dict[str, Any]) -> None:
    combined, _ = recon.combine(fetched["execution"], fetched["root"])
    path = fetched["root"] / "bundle.jsonl"
    path.write_bytes(combined)
    rows = recon.read_records(path, fetched["manifest"])
    assert all(set(r) == {"eai_taxonomy", "quality_signals"} for _, r in rows)
    result = recon.analyze(rows, fetched["manifest"])
    assert result["records"] == 4096 and result["text_analyzed"] is False
    assert not any(p.startswith(("id", "pid", "metadata")) for p in result["path_accounting"])
    assert all(v == {"ok": 4096} for k, v in result["path_accounting"].items() if "fdc" in k)
    assert result["approved_selectors"] == []
    assert all(c["status"] == recon.CANDIDATE_STATUS for c in result["candidates"].values())
    assert (
        sum(result["records_per_crawl"].values()) == 4096 and len(result["records_per_crawl"]) == 8
    )
