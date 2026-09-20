"""Record actual D02 imports, unchanged originals, and the locked environment."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import xlm
import xlm.data.acquisition.fetcher
import xlm.prepare.bounds

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).parent
preserved = json.loads((HERE.parent / "after01/preserved_hashes.json").read_text(encoding="utf-8-sig"))
checks = {entry["Path"]: hashlib.sha256(Path(entry["Path"]).read_bytes()).hexdigest() == entry["SHA256"] for entry in preserved}
assert all(checks.values()), checks
archive = ROOT / "docs/implementation/evidence/P23-D02"
original = json.loads((archive / "sha256.json").read_text(encoding="utf-8-sig"))
archive_checks = {name: hashlib.sha256((archive / name).read_bytes()).hexdigest() == digest for name, digest in original.items()}
assert all(archive_checks.values()), archive_checks
result = {
    "cwd": str(Path.cwd()), "source_root": str(ROOT / "src"),
    "imports": {module.__name__: module.__file__ for module in (xlm, xlm.data.acquisition.fetcher, xlm.prepare.bounds)},
    "interpreter": sys.executable, "python": sys.version, "platform": platform.platform(),
    "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
    "baseline_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "branch": subprocess.check_output(["git", "branch", "--show-current"], text=True).strip(),
    "packages": {name: importlib.metadata.version(name) for name in ("torch", "numpy", "pydantic", "pytest", "ruff", "mypy", "lm-eval", "tokenizers", "pyarrow", "filelock", "psutil")},
    "environment": {name: os.environ.get(name) for name in ("UV_PROJECT_ENVIRONMENT", "UV_OFFLINE", "PYTHONPATH", "HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE")},
    "pins": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ("pyproject.toml", "uv.lock", ".python-version")},
    "preserved_original_checks": checks, "archive_before_checks": archive_checks,
    "scope": "authored fixtures, Windows CPU, offline dependencies, bounded loopback HTTP only; live source/CUDA/Linux NOT RUN",
}
(HERE / "environment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({key:value for key,value in result.items() if key not in ("preserved_original_checks", "archive_before_checks")}, indent=2))
