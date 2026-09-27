"""Offline: only the SYNTH driver unit requests window sampling (no execution).

Runs ``tests/files/calibrate_driver_argv.ps1``, which dot-sources the driver
and records (never executes) the SampleBlocks/Plan argv. SimpleStories'
argv must stay exactly the pre-window shape; SYNTH must bind one identical
window policy into sample-blocks, plan and both adoption checks. Skips where
no PowerShell host exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "files" / "calibrate_driver_argv.ps1"
DRIVER = REPO_ROOT / "scripts" / "operator_calibrate_remaining.ps1"


def _capture(unit: str, data_root: Path) -> list[tuple[str, list[str]]]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if exe is None:
        pytest.skip("no PowerShell host available")
    proc = subprocess.run(
        [
            exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HARNESS),
            "-DriverPath",
            str(DRIVER),
            "-Unit",
            unit,
            "-DataRoot",
            str(data_root),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0 and "ARGV OK" in proc.stdout, proc.stdout + proc.stderr
    calls: list[tuple[str, list[str]]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("ARGV ") and line not in ("ARGV OK",):
            _, name, payload = line.split(" ", 2)
            calls.append((name, json.loads(payload)))
    return calls


def _after(values: list[str], flag: str) -> str:
    return values[values.index(flag) + 1]


def test_simple_stories_argv_is_unchanged(tmp_path: Path) -> None:
    calls = _capture("simple_stories", tmp_path)
    unit = tmp_path / "calib" / "simple_stories"
    rows, report, plan = (
        str(unit / name) for name in ("rows.json", "rows.evidence.json", "plan.json")
    )
    identity = ["--source", "simple_stories", "--view", "default"]
    revision = ["--revision", "e63b8adc3b1a1bdc7cac5b500d150b71346b0628"]
    files = "data/train-00003-of-00007.parquet"
    # Exact pre-window argv (60a14e4) for every SampleBlocks/Plan call.
    assert [args for _, args in calls][:4] == [
        ["sample-blocks", "--rows", rows, "--report", report, *identity, *revision]
        + ["--seed", "20260918", "--files-csv", files],
        ["data", "sample-blocks", *identity, *revision, "--files", files, "--seed", "20260918"]
        + ["--mode", "rowgroup", "--target-records", "1000", "--output", rows, "--report", report],
        ["plan", "--plan", plan, "--rows", rows, *identity, *revision, "--seed", "20260918"]
        + ["--files-csv", files, "--mode", "selected_records"],
        ["data", "plan", *identity, "--catalog", "manifests/datasets.catalog.yaml"]
        + ["--files", files, "--mode", "selected_records", "--row-ranges", rows]
        + ["--adapter-spec", "simple_stories", "--seed", "20260918", "--attempt", "1"]
        + ["--pilot-approved", "--output", plan],
    ]


def test_synth_unit_binds_one_window_policy_everywhere(tmp_path: Path) -> None:
    calls = _capture("synth_en_explanations", tmp_path)
    named = dict(calls)
    sample, plan = named["sample-blocks"], named["plan"]
    assert _after(sample, "--mode") == "window"
    assert _after(sample, "--adapter-spec") == "synth_en"
    policy = (
        _after(sample, "--window-max-scan-rows"),
        _after(sample, "--window-buffer-bytes"),
        _after(sample, "--window-batch-rows"),
    )
    assert policy == ("16384", "4194304", "256")
    assert (
        _after(plan, "--parquet-window-scan-rows"),
        _after(plan, "--parquet-window-buffer-bytes"),
        _after(plan, "--parquet-window-batch-rows"),
    ) == policy
    assert "--pilot-approved" in plan
    adopt_sample, adopt_plan = [args for name, args in calls if name == "adopt"][:2]
    assert adopt_sample[0] == "sample-blocks" and adopt_plan[0] == "plan"
    assert _after(adopt_sample, "--sample-mode") == "window"
    for args in (adopt_sample, adopt_plan):
        assert (
            _after(args, "--window-scan-rows"),
            _after(args, "--window-buffer-bytes"),
            _after(args, "--window-batch-rows"),
        ) == policy
    # No limit-raising or production flags ever reach the plan.
    assert not any(
        flag in plan
        for flag in ("--limits", "--max-bytes", "--max-records", "--authorization-hash")
    )
