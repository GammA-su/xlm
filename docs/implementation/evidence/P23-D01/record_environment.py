"""Read-only source/environment evidence for the D01 repaired workspace."""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

preserved = json.loads((HERE / "preserved_hashes.json").read_text(encoding="utf-8-sig"))
for item in preserved:
    assert sha(Path(item["Path"])) == item["Hash"].lower(), item["Path"]
before = json.loads((HERE / "before_source_hashes.json").read_text(encoding="utf-8-sig"))
old = {str(Path(item["Path"]).relative_to(ROOT)): item["Hash"].lower() for item in before}
current = {str(p.relative_to(ROOT)): sha(p)
           for folder in ("src", "tests") for p in (ROOT / folder).rglob("*.py")}
origins = {}
for name in ("xlm", "xlm.artifacts.store", "xlm.artifacts.manifest", "xlm.artifacts.ledger",
             "xlm.cli.artifact_cmd", "xlm.cli.demo_cmd", "xlm.training.checkpoint"):
    origin = Path(importlib.import_module(name).__file__).resolve()
    assert origin.is_relative_to(ROOT / "src") and "data/audit" not in origin.as_posix()
    origins[name] = str(origin)
result = dict(cwd=str(Path.cwd()), repository=str(ROOT), executable=sys.executable,
              python=sys.version, platform=platform.platform(), machine=platform.machine(),
              uv=subprocess.check_output(["uv", "--version"], text=True).strip(),
              versions={name: importlib.metadata.version(name)
                        for name in ("torch", "lm-eval", "pydantic", "pytest", "filelock", "ruff", "mypy")},
              imports=origins, sys_path=sys.path, preserved=preserved,
              preserved_hashes_match=True, source_sha256=current,
              changed_existing=[p for p, h in old.items() if current.get(p) != h],
              added=[p for p in current if p not in old])
(HERE / "environment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k not in ("source_sha256", "preserved", "sys_path")}, indent=2))
