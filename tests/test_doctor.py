"""Test doctor diagnostic inspection, failure paths, and edge cases."""

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from xlm.cli.doctor import collect_doctor_report, get_nvidia_smi_info, inspect_torch
from xlm.cli.main import app

runner = CliRunner()


def test_doctor_with_torch_absent() -> None:
    """Verify doctor reports torch as not installed when torch import fails with ImportError."""
    with mock.patch.dict("sys.modules", {"torch": None}):
        with mock.patch("builtins.__import__", side_effect=ImportError("No module named 'torch'")):
            installed, ver, cuda_ver, cuda_avail, bf16, kernels, devices = inspect_torch()
            assert not installed
            assert ver is None
            assert not cuda_avail
            assert not bf16
            assert kernels == {}
            assert devices == []

            report = collect_doctor_report()
            assert not report.torch_installed
            assert "disabled (torch not installed)" in report.capabilities["cuda_acceleration"]


def test_doctor_with_torch_broken_import() -> None:
    """Verify doctor handles broken torch import (e.g. DLL failure or OSError) gracefully."""
    with mock.patch(
        "builtins.__import__", side_effect=OSError("DLL load failed while importing _C")
    ):
        installed, ver, cuda_ver, cuda_avail, bf16, kernels, devices = inspect_torch()
        assert not installed
        assert ver is not None and "DLL load failed" in ver
        assert not cuda_avail


def test_doctor_missing_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify doctor handles systems where nvidia-smi is not in PATH."""
    monkeypatch.setattr("shutil.which", lambda cmd: None if cmd == "nvidia-smi" else "/bin/" + cmd)
    driver_ver, cuda_ver = get_nvidia_smi_info()
    assert driver_ver is None
    assert cuda_ver is None


def test_doctor_nvidia_smi_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify doctor handles nvidia-smi timeout gracefully without hanging."""

    def mock_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["nvidia-smi"], timeout=3)

    monkeypatch.setattr("subprocess.run", mock_run)
    driver_ver, cuda_ver = get_nvidia_smi_info()
    assert driver_ver is None
    assert cuda_ver is None


def test_doctor_nonexistent_xlm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify doctor handles nonexistent XLM_HOME path without failing."""
    nonexistent = tmp_path / "does" / "not" / "exist"
    monkeypatch.setenv("XLM_HOME", str(nonexistent))

    report = collect_doctor_report()
    assert report.artifact_root == str(nonexistent.resolve())
    assert not report.artifact_root_exists
    # Should still resolve disk space from parent
    assert report.disk_free_gb is not None

    # Verify JSON mode output in this state is still valid JSON
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output.strip())
    assert not data["artifact_root_exists"]
