"""Record actual imports, pins and preserved originals for the D03 handoff."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import xlm
import xlm.training.components

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
preserved = json.loads((HERE.parent / "after01/preserved_hashes.json").read_text())
checks = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
          for name, expected in preserved.items()}
assert all(checks.values()), checks
packages = ("torch", "numpy", "pydantic", "pytest", "ruff", "mypy", "lm-eval", "tokenizers")
record = {
    "cwd": str(Path.cwd()), "source_root": str(ROOT / "src"),
    "xlm_import": xlm.__file__, "components_import": xlm.training.components.__file__,
    "interpreter": sys.executable, "python": sys.version, "platform": platform.platform(),
    "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
    "packages": {name: importlib.metadata.version(name) for name in packages},
    "environment": {name: os.environ.get(name) for name in (
        "UV_PROJECT_ENVIRONMENT", "UV_OFFLINE", "PYTHONPATH", "HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE")},
    "pins": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
             for name in ("pyproject.toml", "uv.lock", ".python-version")},
    "preservation_checks": checks,
    "gpu_execution": "NOT RUN; CPU authored fixtures only",
}
(HERE / "environment.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
