"""Offline regression for the calibration driver's native wrapper (no network).

Runs the PowerShell harness that dot-sources
``scripts/operator_calibrate_remaining.ps1`` (functions only; stages never
execute) and asserts the exit-code contract: exit 0 with stderr succeeds,
nonzero exits fail-stop, both streams preserved. Skips where no PowerShell
host exists. The two end-to-end cases reuse the already-synced repo env
with ``--offline --no-sync`` flags and touch no network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS = REPO_ROOT / "tests" / "files" / "calibrate_driver_native.ps1"
DRIVER = REPO_ROOT / "scripts" / "operator_calibrate_remaining.ps1"


def _shell() -> str | None:
    for name in ("powershell", "pwsh"):
        exe = shutil.which(name)
        if exe is not None:
            return exe
    return None


def test_native_wrapper_contract(tmp_path: Path) -> None:
    exe = _shell()
    if exe is None:
        pytest.skip("no PowerShell host available")
    assert HARNESS.is_file() and DRIVER.is_file()
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
            "-WorkDir",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL NATIVE WRAPPER CASES PASSED" in proc.stdout
