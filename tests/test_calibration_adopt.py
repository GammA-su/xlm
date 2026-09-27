"""Offline adoption decisions for calibration reruns (no network).

Exercises scripts/calibration_adopt.py against real repository APIs
(ArtifactStore, acquisition plans) in tmp stores: absent outputs run,
compatible outputs are reused, incompatible/corrupt/incomplete outputs
fail closed without deletion, overwrite, or fresh-identity bypass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.sources.admission import save_probe_evidence
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome


def _load_script() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "calibration_adopt.py"
    spec = importlib.util.spec_from_file_location("calibration_adopt", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_adopt = _load_script()

SOURCE = "simple_stories"
VIEW = "default"
REVISION = "e63b8adc3b1a1bdc7cac5b500d150b71346b0628"
REPOSITORY = "SimpleStories/SimpleStories"
SHA_OTHER = "c" * 40


def _store(home: Path) -> ArtifactStore:
    return ArtifactStore(ArtifactPaths(root=home))


def _evidence(**overrides: Any) -> ProbeEvidenceRecord:
    payload: dict[str, Any] = {
        "source_id": SOURCE,
        "view_id": VIEW,
        "provider": "huggingface",
        "repository": REPOSITORY,
        "immutable_revision": REVISION,
        "outcome": ProbeOutcome.PARTIAL,
        "evidence_type": EvidenceType.REAL_OBSERVED,
        "observed_files_count": 3,
        "declared_license": "mit",
    }
    payload.update(overrides)
    return ProbeEvidenceRecord.model_validate(payload)


def _publish(home: Path, record: ProbeEvidenceRecord) -> None:
    save_probe_evidence(record, _store(home), staging_dir=home / "staging")


def _probe_main(
    home: Path, source: str = SOURCE, view: str = VIEW, revision: str = REVISION
) -> int:
    return _adopt.main(
        [
            "probe",
            "--store",
            str(home),
            "--source",
            source,
            "--view",
            view,
            "--revision",
            revision,
            "--repository",
            REPOSITORY,
        ]
    )


def test_1_no_evidence_runs(tmp_path: Path) -> None:
    assert _probe_main(tmp_path) == 0


def test_2_compatible_evidence_reused(tmp_path: Path) -> None:
    _publish(tmp_path, _evidence())
    assert _probe_main(tmp_path) == 2


def test_3_wrong_revision_fails_closed() -> None:
    record = _evidence()
    with pytest.raises(_adopt.AdoptionRefused, match="revision"):
        _adopt.check_probe_evidence(
            record, source=SOURCE, view=VIEW, revision=SHA_OTHER, repository=REPOSITORY
        )


def test_4_wrong_source_view_fails_closed() -> None:
    record = _evidence()
    with pytest.raises(_adopt.AdoptionRefused, match="not 'other:default'"):
        _adopt.check_probe_evidence(
            record, source="other", view=VIEW, revision=REVISION, repository=REPOSITORY
        )
    with pytest.raises(_adopt.AdoptionRefused, match="not 'simple_stories:other'"):
        _adopt.check_probe_evidence(
            record, source=SOURCE, view="other", revision=REVISION, repository=REPOSITORY
        )


def test_5_incomplete_evidence_fails_closed(tmp_path: Path) -> None:
    slot = tmp_path / "probe_evidence" / f"probe_{SOURCE}_{VIEW}"
    slot.mkdir(parents=True)
    (slot / "probe_evidence.json").write_text('{"source_id": "x"}', encoding="utf-8")
    assert _probe_main(tmp_path) == 1
    assert slot.is_dir()


def test_6_corrupt_evidence_fails_closed(tmp_path: Path) -> None:
    slot = tmp_path / "probe_evidence" / f"probe_{SOURCE}_{VIEW}"
    slot.mkdir(parents=True)
    (slot / "_COMPLETED").write_text("COMPLETED\n", encoding="utf-8")
    (slot / "probe_evidence.json").write_text("not json{{{", encoding="utf-8")
    assert _probe_main(tmp_path) == 1
    assert (slot / "probe_evidence.json").is_file()


def _blocks_paths(tmp_path: Path) -> tuple[Path, Path]:
    rows = tmp_path / "rows.json"
    report = tmp_path / "report.json"
    rows.write_text(json.dumps({"a.parquet": [0, 100]}), encoding="utf-8")
    report.write_text(
        json.dumps(
            {
                "source_id": SOURCE,
                "view_id": VIEW,
                "revision": REVISION,
                "seed": 20260918,
            }
        ),
        encoding="utf-8",
    )
    return rows, report


def _blocks_main(tmp_path: Path, rows: Path, report: Path) -> int:
    return _adopt.main(
        [
            "sample-blocks",
            "--rows",
            str(rows),
            "--report",
            str(report),
            "--source",
            SOURCE,
            "--view",
            VIEW,
            "--revision",
            REVISION,
            "--seed",
            "20260918",
            "--files-csv",
            "a.parquet",
        ]
    )


def test_7_restart_after_probe_proceeds(tmp_path: Path) -> None:
    _publish(tmp_path, _evidence())
    assert _probe_main(tmp_path) == 2
    rows, report = _blocks_paths(tmp_path)
    assert _blocks_main(tmp_path, rows, report) == 2
    assert (
        _adopt.main(
            ["sample-blocks"]
            + ["--rows", str(tmp_path / "missing.json")]
            + [
                "--report",
                str(report),
                "--source",
                SOURCE,
                "--view",
                VIEW,
                "--revision",
                REVISION,
                "--seed",
                "20260918",
                "--files-csv",
                "a.parquet",
            ]
        )
        == 0
    )


def _saved_plan(tmp_path: Path):
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        SamplingFrame,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_simple_stories_default_hf",
        source_id="simple_stories",
        view_id="default",
        provider="huggingface",
        repository="SimpleStories/SimpleStories",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=["a.parquet"],
        row_ranges={"a.parquet": (0, 100)},
        sampling_frame=SamplingFrame(selection_seed=20260918),
        limits=AcquisitionLimits(),
        output_artifact_id="raw_simple_stories_default",
        is_pilot=True,
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(plan, plan_path)
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps({"a.parquet": [0, 100]}), encoding="utf-8")
    return plan, plan_path, rows


def test_plan_identity_loads_path_and_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """plan-identity uses the Path API and prints id/hash/revision."""
    plan, plan_path, _ = _saved_plan(tmp_path)
    assert _adopt.main(["plan-identity", "--plan", str(plan_path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == plan.plan_id
    assert out[1] == plan.compute_behavioral_hash()
    assert out[2] == REVISION
    spaced = tmp_path / "dir with spaces"
    spaced.mkdir()
    moved = spaced / "plan.json"
    moved.write_bytes(plan_path.read_bytes())
    assert _adopt.main(["plan-identity", "--plan", str(moved)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == plan.plan_id


def test_plan_identity_refuses_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "plan.json"
    bad.write_text("not json{{{", encoding="utf-8")
    assert _adopt.main(["plan-identity", "--plan", str(bad)]) == 1


def test_restart_reuses_plan_without_regeneration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Compatible plan: adopt skips regeneration, identity display proceeds."""
    plan, plan_path, rows = _saved_plan(tmp_path)
    before = plan_path.read_bytes()
    base = [
        "--plan",
        str(plan_path),
        "--rows",
        str(rows),
        "--source",
        "simple_stories",
        "--view",
        "default",
        "--revision",
        REVISION,
        "--seed",
        "20260918",
        "--files-csv",
        "a.parquet",
        "--mode",
        "selected_records",
    ]
    assert _adopt.main(["plan"] + base) == 2
    assert _adopt.main(["plan-identity", "--plan", str(plan_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-3:] == [plan.plan_id, plan.compute_behavioral_hash(), REVISION]
    assert plan_path.read_bytes() == before


def test_adopt_plan_mismatch_fails_closed(tmp_path: Path) -> None:
    plan, plan_path, rows = _saved_plan(tmp_path)
    base = [
        "--plan",
        str(plan_path),
        "--rows",
        str(rows),
        "--source",
        "simple_stories",
        "--view",
        "default",
        "--revision",
        REVISION,
        "--seed",
        "20260918",
        "--files-csv",
        "a.parquet",
        "--mode",
        "selected_records",
    ]
    assert _adopt.main(["plan"] + base) == 2
    before = plan_path.read_bytes()
    rows.write_text(json.dumps({"a.parquet": [0, 50]}), encoding="utf-8")
    assert _adopt.main(["plan"] + base) == 1
    assert plan_path.read_bytes() == before
    bad_plan = tmp_path / "bad.json"
    bad_plan.write_text("garbage", encoding="utf-8")
    assert _adopt.main(["plan", "--plan", str(bad_plan)] + base[2:]) == 1


def test_8_no_republish_of_completed_outputs(tmp_path: Path) -> None:
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        SamplingFrame,
        save_acquisition_plan,
    )

    files = ["a.parquet"]
    plan = AcquisitionPlan(
        plan_id="plan_simple_stories_default_hf",
        source_id="simple_stories",
        view_id="default",
        provider="huggingface",
        repository="SimpleStories/SimpleStories",
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=files,
        row_ranges={"a.parquet": (0, 100)},
        sampling_frame=SamplingFrame(selection_seed=20260918),
        limits=AcquisitionLimits(),
        output_artifact_id="raw_simple_stories_default",
        is_pilot=True,
    )
    plan_path = tmp_path / "plan.json"
    save_acquisition_plan(plan, plan_path)
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps({"a.parquet": [0, 100]}), encoding="utf-8")
    base = [
        "--plan",
        str(plan_path),
        "--rows",
        str(rows),
        "--source",
        "simple_stories",
        "--view",
        "default",
        "--revision",
        REVISION,
        "--seed",
        "20260918",
        "--files-csv",
        "a.parquet",
        "--mode",
        "selected_records",
    ]
    assert _adopt.main(["plan"] + base) == 2

    out = tmp_path / "canonical"
    out.mkdir()
    (out / "documents.jsonl").write_text('{"doc_id": "d"}\n', encoding="utf-8")
    summary = {
        "plan_id": plan.plan_id,
        "plan_hash": plan.compute_behavioral_hash(),
        "total_input_records": 100,
        "accepted_records": 99,
        "rejected_records": 1,
    }
    (out / "adaptation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    assert _adopt.main(["adapt", "--output-dir", str(out), "--plan", str(plan_path)]) == 2
    summary["plan_hash"] = "0" * 64
    (out / "adaptation_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    assert _adopt.main(["adapt", "--output-dir", str(out), "--plan", str(plan_path)]) == 1
    assert (out / "documents.jsonl").is_file()

    raw = tmp_path / "raw"
    raw.mkdir()
    payload_file = raw / "selected_records.jsonl"
    payload_file.write_text('{"row": 1}\n', encoding="utf-8")
    store = _store(tmp_path)
    manifest_meta = {
        "plan_hash": plan.compute_behavioral_hash(),
        "source_id": "simple_stories",
        "view_id": "default",
        "revision": REVISION,
    }
    store.publish_artifact(
        artifact_id=plan.output_artifact_id,
        kind="raw_dataset",
        files={"selected_records.jsonl": payload_file},
        producer_code_hash="p" * 16,
        dependency_hash="d" * 16,
        resolved_config_hash="r" * 16,
        metadata=manifest_meta,
    )
    assert (
        _adopt.main(
            ["verify", "--store", str(tmp_path), "--plan", str(plan_path), "--output-dir", str(raw)]
        )
        == 2
    )
    payload_file.write_text('{"row": 2}\n', encoding="utf-8")
    assert (
        _adopt.main(
            ["verify", "--store", str(tmp_path), "--plan", str(plan_path), "--output-dir", str(raw)]
        )
        == 1
    )
