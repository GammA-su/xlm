"""Offline Stage-All semantics of the calibration driver (authored fixtures only).

Runs ``scripts/operator_calibrate_remaining.ps1`` stage logic through
``tests/files/calibrate_driver_stages.ps1`` against the synthetic unit from
``calibration_fixture`` under a temporary data root. The harness refuses any
live step, so these runs prove adoption rather than touching the network.
Env (``uv sync``) is the one stage of ``All`` not exercised: it would mutate
the shared checkout's environment. Skips where no PowerShell host exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from calibration_fixture import (
    ULTRAX_ENTRY,
    CalibrationUnit,
    adapt_in_process,
    build_unit,
    expected_entry,
    rebind_documents_digest,
    render_json,
    save_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "files" / "calibrate_driver_stages.ps1"
DRIVER = REPO_ROOT / "scripts" / "operator_calibrate_remaining.ps1"
ALL_BUT_ENV = [
    "Probe",
    "SampleBlocks",
    "Plan",
    "Fetch",
    "Status",
    "Verify",
    "Adapt",
    "Summary",
    "Record",
]


def _shell() -> str:
    for name in ("powershell", "pwsh"):
        exe = shutil.which(name)
        if exe is not None:
            return exe
    pytest.skip("no PowerShell host available")


def _run(unit: CalibrationUnit, stages: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[name] = "1"
    env["TOKENIZERS_PARALLELISM"] = "false"
    return subprocess.run(
        [
            _shell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HARNESS),
            "-DriverPath",
            str(DRIVER),
            "-Repo",
            str(REPO_ROOT),
            "-DataRoot",
            str(unit.data_root),
            "-Stages",
            ",".join(stages),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
        cwd=str(unit.data_root),
        env=env,
    )


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under root except driver logs."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "logs" not in path.relative_to(root).parts
    }


def test_stage_all_retry_then_restart_reuses_everything(tmp_path: Path) -> None:
    unit = build_unit(tmp_path / "data")
    # Pass A: the interrupted canary's progress (through adapt, no Record).
    first = _run(unit, ALL_BUT_ENV[:-1])
    assert first.returncode == 0, first.stdout + first.stderr
    assert "live step" not in first.stdout
    published = unit.home / "raw_dataset" / "raw_simple_stories_default_synthetic"
    assert (published / "_COMPLETED").is_file()
    assert sorted(p.name for p in unit.canonical.iterdir()) == [
        "adaptation_rejections.jsonl",
        "adaptation_summary.json",
        "documents.jsonl",
    ]
    before = _snapshot(unit.data_root)

    # Pass B: the operator's retry. Everything is adopted; only Record acts.
    retry = _run(unit, ALL_BUT_ENV)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    for line in (
        "existing compatible probe evidence reused",
        "existing compatible row ranges reused",
        "existing compatible plan reused",
        "existing completed fetch reused; no fetch executed",
        "existing verified publication reused; re-confirming without republishing",
        "existing compatible adapted outputs reused",
        "measurement written",
        "source: simple_stories recorded accepted: 4/4",
    ):
        assert line in retry.stdout, line
    assert "Successfully published" not in retry.stdout
    after = _snapshot(unit.data_root)
    changed = {key for key in before if before[key] != after.get(key)}
    assert changed == {str(unit.calibration.relative_to(unit.data_root))}
    assert set(after) - set(before) == {str(unit.measurement.relative_to(unit.data_root))}
    payload = json.loads(unit.calibration.read_text(encoding="utf-8"))
    assert payload["sources"]["ultrax_ultrafineweb"] == ULTRAX_ENTRY
    assert payload["sources"]["simple_stories"] == expected_entry()
    measured = json.loads(unit.measurement.read_text(encoding="utf-8"))
    assert measured["plan_hash"] == unit.plan_hash
    assert measured["fetch_status"] == "COMPLETED"

    # Pass C: restart of the same Stage All is a complete no-op.
    restart = _run(unit, ALL_BUT_ENV)
    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert "existing identical measurement reused" in restart.stdout
    assert "source: simple_stories existing identical entry reused" in restart.stdout
    assert _snapshot(unit.data_root) == after


def _break_metric(unit: CalibrationUnit) -> None:
    docs = unit.canonical / "documents.jsonl"
    lines = docs.read_bytes().decode("utf-8").splitlines(keepends=True)
    record = json.loads(lines[0])
    record["utf8_byte_count"] += 1
    lines[0] = json.dumps(record, ensure_ascii=False) + "\n"
    docs.write_bytes("".join(lines).encode("utf-8"))
    rebind_documents_digest(unit)


def _journal_field(field: str, value: object) -> Callable[[CalibrationUnit], None]:
    def mutate(unit: CalibrationUnit) -> None:
        state = json.loads(unit.journal.read_text(encoding="utf-8"))
        state[field] = value
        unit.journal.write_text(json.dumps(state), encoding="utf-8")

    return mutate


def _summary_field(field: str, value: object) -> Callable[[CalibrationUnit], None]:
    def mutate(unit: CalibrationUnit) -> None:
        path = unit.canonical / "adaptation_summary.json"
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary[field] = value
        path.write_bytes(render_json(summary))

    return mutate


def _wrong_revision(unit: CalibrationUnit) -> None:
    unit.plan_path.unlink()
    save_plan(unit.plan_path, revision="f" * 40)


def _divergent_record(unit: CalibrationUnit) -> None:
    entry = {**expected_entry(), "canonical_bytes": expected_entry()["canonical_bytes"] + 1}
    unit.calibration.write_bytes(
        render_json({"sources": {"simple_stories": entry, "ultrax_ultrafineweb": ULTRAX_ENTRY}})
    )


def _divergent_measurement(unit: CalibrationUnit) -> None:
    unit.measurement.write_bytes(render_json({"measurement_version": 1, "stale": True}))


def _drift_raw(unit: CalibrationUnit) -> None:
    path = unit.raw / "selected_records.jsonl"
    path.write_bytes(path.read_bytes().replace(b"kite", b"kits"))


MUTATIONS = {
    "malformed-canonical-metric": (_break_metric, ["Record"], "utf8_byte_count"),
    "wrong-journal-plan-hash": (
        _journal_field("plan_hash", "0" * 64),
        ["Record"],
        "journal plan_hash",
    ),
    "wrong-summary-plan-hash": (
        _summary_field("plan_hash", "0" * 64),
        ["Record"],
        "adaptation summary plan_hash",
    ),
    "wrong-plan-hash-fetch-adopt": (
        _journal_field("plan_hash", "0" * 64),
        ["Fetch"],
        "fetch adoption refused",
    ),
    "wrong-revision": (_wrong_revision, ["Record"], "plan revision"),
    "wrong-summary-revision": (
        _summary_field("source_revision", "f" * 40),
        ["Record"],
        "source_revision",
    ),
    "divergent-calibration-record": (_divergent_record, ["Record"], "already recorded"),
    "divergent-measurement": (_divergent_measurement, ["Record"], "differs from the artifacts"),
    "missing-canonical-record": (
        lambda unit: (unit.canonical / "documents.jsonl").unlink(),
        ["Record"],
        "measurement refused",
    ),
    "missing-canonical-adopt": (
        lambda unit: (unit.canonical / "documents.jsonl").unlink(),
        ["Adapt"],
        "adapt adoption refused",
    ),
    "fetch-not-completed-record": (
        _journal_field("status", "INTERRUPTED"),
        ["Record"],
        "journal status",
    ),
    "fetch-not-completed-resumes": (
        _journal_field("status", "INTERRUPTED"),
        ["Fetch"],
        "live step 'fetch' refused",
    ),
    "drifted-raw-output": (_drift_raw, ["Fetch"], "fetch adoption refused"),
}


@pytest.mark.parametrize("case", sorted(MUTATIONS))
def test_stage_mutations_fail_closed(tmp_path: Path, case: str) -> None:
    mutate, stages, reason = MUTATIONS[case]
    unit = build_unit(tmp_path / "data")
    adapt_in_process(unit)
    mutate(unit)
    calibration_before = unit.calibration.read_bytes()
    measurement_before = unit.measurement.read_bytes() if unit.measurement.exists() else None
    result = _run(unit, stages)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "STAGE FAILED" in result.stdout
    assert reason in result.stdout, result.stdout
    assert unit.calibration.read_bytes() == calibration_before
    if case == "divergent-calibration-record":
        return  # the (correct) measurement is written; only the record refuses
    after = unit.measurement.read_bytes() if unit.measurement.exists() else None
    assert after == measurement_before
