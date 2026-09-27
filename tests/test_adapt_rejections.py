"""Explicit recorded adapter rejections: authored fixtures, offline only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AcquisitionMode,
    AcquisitionPlan,
    save_acquisition_plan,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

SOURCE_ID = "finepdfs_edu"
REVISION = "authored-rev-finepdfs"
SOURCE_FILE = "data/eng_Latn/train/000_00083.parquet"
DOC_TEXT = "Docling-extracted section on river deltas and sediment transport Alpha."
OCR_TEXT = "RolmOCR image-OCR section on river deltas and sediment transport Beta."


def _finepdf_row(extractor: str, text: str) -> dict[str, Any]:
    return {"text": text, "language": "eng_Latn", "extractor": extractor}


def _plan(tmp_path: Path, plan_id: str = "plan_adapt_rej") -> tuple[Path, str]:
    plan = AcquisitionPlan(
        plan_id=plan_id,
        source_id=SOURCE_ID,
        view_id="eng_Latn",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[SOURCE_FILE],
        row_ranges={SOURCE_FILE: (0, 4)},
        output_artifact_id="raw_adapt_rej",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=1024**2, max_records=100),
    )
    plan_path = tmp_path / f"{plan_id}.json"
    save_acquisition_plan(plan, plan_path)
    from xlm.data.acquisition.plan import load_acquisition_plan

    loaded = load_acquisition_plan(plan_path)
    return plan_path, loaded.compute_behavioral_hash()


def _line(record: dict[str, Any], row_index: int, sel_hash: str, sha: str | None = None) -> str:
    locator: dict[str, Any] = {
        "source_id": SOURCE_ID,
        "repository": "http://127.0.0.1:9/unused",
        "revision": REVISION,
        "source_file": SOURCE_FILE,
        "row_index": row_index,
        "selection_hash": sel_hash,
    }
    if sha is not None:
        locator["original_record_sha256"] = sha
    return json.dumps({**record, "_xlm_acquisition": locator}, ensure_ascii=False)


def _selected(rows: list[dict[str, Any]], sel_hash: str) -> str:
    lines = []
    for index, record in enumerate(rows):
        raw = json.dumps(record, ensure_ascii=False).encode("utf-8")
        lines.append(_line(record, index, sel_hash, hashlib.sha256(raw).hexdigest()))
    return "\n".join(lines) + "\n"


def _mixed_rows() -> list[dict[str, Any]]:
    return [
        _finepdf_row("rolmOCR", OCR_TEXT),
        _finepdf_row("docling", DOC_TEXT),
        _finepdf_row("rolmOCR", OCR_TEXT),
        _finepdf_row("docling", DOC_TEXT + " Second."),
    ]


def _run(*args: str) -> Any:
    return CliRunner().invoke(data_app, ["adapt", *args])


def test_default_fail_mode_aborts_first_reject(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(_mixed_rows(), sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
    )
    assert result.exit_code == 1, result.output
    assert "refused line 1" in result.output
    assert not (out_dir / "documents.jsonl").exists()
    assert not (out_dir / "adaptation_rejections.jsonl").exists()
    assert not (out_dir / "adaptation_summary.json").exists()


def test_record_mode_mixed_finepdf(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(_mixed_rows(), sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    docs = [
        json.loads(line)
        for line in (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(docs) == 2
    assert docs[0]["doc_id"] == canonical_source_doc_id("finepdfs", SOURCE_FILE, 1)
    assert docs[1]["doc_id"] == canonical_source_doc_id("finepdfs", SOURCE_FILE, 3)
    ledger = [
        json.loads(line)
        for line in (out_dir / "adaptation_rejections.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(ledger) == 2
    assert [entry["input_line"] for entry in ledger] == [1, 3]
    for entry in ledger:
        assert entry["rejection_code"] == "RecordRejectedError"
        assert entry["rejection_category"] == "policy"
        assert entry["adapter_id"] == "finepdfs_en"
        assert entry["source_id"] == SOURCE_ID
        assert entry["source_revision"] == REVISION
        assert entry["source_file"] == SOURCE_FILE
        assert len(entry["original_record_sha256"]) == 64
    assert OCR_TEXT not in (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8")
    assert DOC_TEXT not in (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8")
    summary = json.loads((out_dir / "adaptation_summary.json").read_text(encoding="utf-8"))
    assert summary["total_input_records"] == 4
    assert summary["accepted_records"] == 2
    assert summary["rejected_records"] == 2
    assert summary["rejection_counts_by_code"] == {"RecordRejectedError": 2}
    assert summary["on_reject"] == "record"
    assert summary["adaptation_summary_version"] == 1


def test_record_mode_all_accepted(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    rows = [_finepdf_row("docling", DOC_TEXT), _finepdf_row("docling", DOC_TEXT + " B.")]
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(rows, sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    assert len((out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8") == ""
    summary = json.loads((out_dir / "adaptation_summary.json").read_text(encoding="utf-8"))
    assert (summary["accepted_records"], summary["rejected_records"]) == (2, 0)
    assert summary["rejection_counts_by_code"] == {}
    # No staging temp survives publication (a summary writer used to leak an
    # empty adaptation_summary.json.<uuid>.tmp beside the published triple).
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "adaptation_rejections.jsonl",
        "adaptation_summary.json",
        "documents.jsonl",
    ]


def test_record_mode_all_rejected(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    rows = [_finepdf_row("rolmOCR", OCR_TEXT), _finepdf_row("rolmOCR", OCR_TEXT)]
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(rows, sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "documents.jsonl").read_text(encoding="utf-8") == ""
    ledger = (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 2
    summary = json.loads((out_dir / "adaptation_summary.json").read_text(encoding="utf-8"))
    assert (summary["accepted_records"], summary["rejected_records"]) == (0, 2)


def test_malformed_row_fatal_in_record_mode(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    good = _selected([_finepdf_row("docling", DOC_TEXT)], sel_hash).rstrip("\n")
    selected = tmp_path / "selected.jsonl"
    selected.write_text(good + "\n{not json\n", encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert not (out_dir / "documents.jsonl").exists()
    assert not (out_dir / "adaptation_rejections.jsonl").exists()
    assert not (out_dir / "adaptation_summary.json").exists()


def test_bad_provenance_fatal_in_record_mode(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    record = _finepdf_row("docling", DOC_TEXT)
    locator = {
        "source_id": SOURCE_ID,
        "repository": "http://127.0.0.1:9/unused",
        "revision": "wrong-revision",
        "source_file": SOURCE_FILE,
        "row_index": 0,
        "selection_hash": sel_hash,
    }
    selected = tmp_path / "selected.jsonl"
    selected.write_text(
        json.dumps({**record, "_xlm_acquisition": locator}) + "\n", encoding="utf-8"
    )
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert not (out_dir / "documents.jsonl").exists()


def test_selection_hash_mismatch_fatal_in_record_mode(tmp_path: Path) -> None:
    plan_path, _sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected([_finepdf_row("docling", DOC_TEXT)], "0" * 64), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert "selection_hash" in result.output
    assert not (out_dir / "documents.jsonl").exists()


def test_missing_field_fatal_in_record_mode(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    record = {"text": DOC_TEXT, "language": "eng_Latn"}
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected([record], sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert "refused line 1" in result.output
    assert not (out_dir / "documents.jsonl").exists()
    assert not (out_dir / "adaptation_rejections.jsonl").exists()


def test_no_partial_output_after_midstream_fatal(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    good = _selected(
        [_finepdf_row("docling", DOC_TEXT), _finepdf_row("docling", DOC_TEXT + " B.")],
        sel_hash,
    ).rstrip("\n")
    selected = tmp_path / "selected.jsonl"
    selected.write_text(good + "\n[1, 2]\n", encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert not (out_dir / "documents.jsonl").exists()
    assert not (out_dir / "adaptation_rejections.jsonl").exists()
    assert not (out_dir / "adaptation_summary.json").exists()


def test_deterministic_outputs_across_reruns(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(_mixed_rows(), sel_hash), encoding="utf-8")
    first, second = tmp_path / "first", tmp_path / "second"
    for out_dir in (first, second):
        result = _run(
            "--plan",
            str(plan_path),
            "--adapter",
            "finepdfs_en",
            "--input",
            str(selected),
            "--output-dir",
            str(out_dir),
            "--on-reject",
            "record",
        )
        assert result.exit_code == 0, result.output
    for name in ("documents.jsonl", "adaptation_rejections.jsonl", "adaptation_summary.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_overwrite_refusal_covers_new_files(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected([_finepdf_row("docling", DOC_TEXT)], sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    (out_dir / "adaptation_summary.json").parent.mkdir(parents=True, exist_ok=True)
    (out_dir / "adaptation_summary.json").write_text("{}", encoding="utf-8")
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 1, result.output
    assert "refusing to overwrite" in result.output


def test_txt360_second_adapter_record_mode(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan = AcquisitionPlan(
        plan_id="plan_adapt_txt",
        source_id="txt360_v2",
        view_id="default",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision="rev-txt",
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=["web.jsonl"],
        row_ranges={"web.jsonl": (0, 2)},
        output_artifact_id="raw_adapt_txt",
        is_pilot=True,
        limits=AcquisitionLimits(max_transferred_bytes=1024**2, max_records=100),
    )
    plan_path = tmp_path / "plan_txt.json"
    save_acquisition_plan(plan, plan_path)
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    good = {"text": "t", "quality_band": "web-high-medium"}
    bad = {"text": "t", "quality_band": "web-low"}

    def line(record: dict[str, object], index: int) -> str:
        return json.dumps(
            {
                **record,
                "_xlm_acquisition": {
                    "source_id": "txt360_v2",
                    "repository": "http://127.0.0.1:9/unused",
                    "revision": "rev-txt",
                    "source_file": "web.jsonl",
                    "row_index": index,
                    "selection_hash": sel_hash,
                },
            }
        )

    selected = tmp_path / "selected.jsonl"
    selected.write_text(line(good, 0) + "\n" + line(bad, 1) + "\n", encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "txt360_web",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    docs = (out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(docs) == 1
    assert json.loads(docs[0])["doc_id"] == canonical_source_doc_id("txt360", "web.jsonl", 0)
    ledger = (out_dir / "adaptation_rejections.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1
    entry = json.loads(ledger[0])
    assert entry["rejection_code"] == "RecordRejectedError"
    assert entry["input_line"] == 2


def test_logical_selection_hash_accepted(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import load_acquisition_plan

    plan_path, _sel_hash = _plan(tmp_path)
    logical = load_acquisition_plan(plan_path).compute_selection_hash()
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected([_finepdf_row("docling", DOC_TEXT)], logical), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    assert len((out_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_invalid_on_reject_value(tmp_path: Path) -> None:
    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected([_finepdf_row("docling", DOC_TEXT)], sel_hash), encoding="utf-8")
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(tmp_path / "o"),
        "--on-reject",
        "skip",
    )
    assert result.exit_code == 1, result.output
    assert "--on-reject" in result.output


def test_summary_schema_and_hashes(tmp_path: Path) -> None:
    import hashlib as _hashlib

    plan_path, sel_hash = _plan(tmp_path)
    selected = tmp_path / "selected.jsonl"
    selected.write_text(_selected(_mixed_rows(), sel_hash), encoding="utf-8")
    out_dir = tmp_path / "canonical"
    result = _run(
        "--plan",
        str(plan_path),
        "--adapter",
        "finepdfs_en",
        "--input",
        str(selected),
        "--output-dir",
        str(out_dir),
        "--on-reject",
        "record",
    )
    assert result.exit_code == 0, result.output
    summary = json.loads((out_dir / "adaptation_summary.json").read_text(encoding="utf-8"))
    for key in (
        "adaptation_summary_version",
        "adapter_id",
        "source_id",
        "source_revision",
        "plan_id",
        "plan_hash",
        "on_reject",
        "total_input_records",
        "accepted_records",
        "rejected_records",
        "rejection_counts_by_code",
        "documents",
        "rejections",
    ):
        assert key in summary, key
    assert summary["total_input_records"] == (
        summary["accepted_records"] + summary["rejected_records"]
    )
    for section in ("documents", "rejections"):
        payload = (out_dir / summary[section]["file"]).read_bytes()
        digest = _hashlib.sha256()
        for line in payload.decode("utf-8").splitlines():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        assert digest.hexdigest() == summary[section]["sha256"]
        assert summary[section]["count"] == len(payload.decode("utf-8").splitlines())
