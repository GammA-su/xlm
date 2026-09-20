"""Verify clean imports, lazy loading, and package metadata identity."""

import importlib
import sys
from pathlib import Path


def test_import_xlm_without_heavy_deps() -> None:
    """Verify importing xlm in a fresh process does not eagerly load torch."""
    import subprocess

    code = "import sys, xlm; assert 'torch' not in sys.modules, 'torch was eagerly loaded'"
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Import isolation check failed: {res.stderr}"

    import xlm

    assert hasattr(xlm, "__version__")
    assert xlm.__version__ == "0.1.0"


def test_package_metadata_and_location() -> None:
    """Verify package distribution metadata and ensure it does not shadow another package."""
    dist_version = importlib.metadata.version("xlm")
    assert dist_version == "0.1.0"

    import xlm

    package_path = Path(xlm.__file__).resolve()
    # Should resolve to the src/xlm directory
    assert "src" in package_path.parts or "site-packages" in package_path.parts
    assert package_path.name == "__init__.py"
