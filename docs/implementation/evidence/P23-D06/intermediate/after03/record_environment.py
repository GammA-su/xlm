"""Record actual repaired source/import locations without using an old audit copy."""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
BEFORE = HERE.parent / "after01"

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

preserved = json.loads((BEFORE / "preserved_hashes.json").read_text(encoding="utf-8-sig"))
assert all(sha(Path(item["Path"])) == item["Hash"].lower() for item in preserved)
before = json.loads((BEFORE / "before_source_hashes.json").read_text(encoding="utf-8-sig"))
old = {Path(item["Path"]).relative_to(ROOT).as_posix(): item["Hash"].lower() for item in before}
current = {p.relative_to(ROOT).as_posix(): sha(p) for folder in ("src", "tests")
           for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts}
origins = {}
for name in ("xlm", "xlm.experiments.environment", "xlm.experiments.execution", "xlm.experiments.queue",
             "xlm.experiments.launcher", "xlm.training.trainer", "xlm.training.checkpoint",
             "xlm.cli.train_cmd", "xlm.cli.eval_cmd", "xlm.artifacts.store"):
    origin = Path(importlib.import_module(name).__file__).resolve()
    assert origin.is_relative_to(ROOT / "src")
    origins[name] = str(origin)
result = dict(cwd=str(Path.cwd()), repository=str(ROOT), executable=sys.executable, python=sys.version,
    platform=platform.platform(), machine=platform.machine(), uv=subprocess.check_output(["uv", "--version"], text=True).strip(),
    versions={name: importlib.metadata.version(name) for name in ("torch", "lm-eval", "pydantic", "pytest", "filelock", "ruff", "mypy")},
    imports=origins, preserved=preserved, preserved_hashes_match=True, source_sha256=current,
    changed_existing=[p for p, h in old.items() if current.get(p) != h], added=[p for p in current if p not in old])
(HERE / "environment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k not in ("source_sha256", "preserved")}, indent=2))
