"""Test CLI commands, options, and installed entrypoint execution."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm import __version__
from xlm.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    """Verify xlm --help succeeds and lists available commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "XLM: Research platform" in result.output
    assert "doctor" in result.output


def test_cli_version() -> None:
    """Verify xlm --version reports correct package version."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"xlm {__version__}" in result.output


def test_cli_unknown_command() -> None:
    """Verify unknown commands exit nonzero."""
    result = runner.invoke(app, ["train"])
    assert result.exit_code != 0


def test_cli_doctor_text() -> None:
    """Verify xlm doctor produces text diagnostics."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "=== XLM Environment & Diagnostic Report ===" in result.output
    assert "Operating System:" in result.output
    assert "Active Capabilities" in result.output


def test_cli_doctor_json() -> None:
    """Verify xlm doctor --json outputs strictly valid JSON."""
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output.strip())
    assert data["package_version"] == __version__
    assert "python_version" in data
    assert "os_name" in data
    assert "capabilities" in data
    assert "artifact_root" in data


def test_installed_entrypoint_subprocess(tmp_path: Path) -> None:
    """Verify the installed 'xlm' console script runs outside the repo directory."""
    # Locate the xlm executable in the current virtualenv
    bin_dir = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    xlm_exe = bin_dir / ("xlm.exe" if sys.platform == "win32" else "xlm")

    if not xlm_exe.exists():
        found = shutil.which("xlm")
        if found:
            xlm_exe = Path(found)
        else:
            pytest.skip(f"Installed entrypoint '{xlm_exe}' not found in virtualenv")

    # Execute outside the repo from tmp_path
    res_ver = subprocess.run(
        [str(xlm_exe), "--version"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_ver.returncode == 0
    assert f"xlm {__version__}" in res_ver.stdout

    res_help = subprocess.run(
        [str(xlm_exe), "--help"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_help.returncode == 0
    assert "doctor" in res_help.stdout
