"""Preserve stage evidence and verify final product code has not changed."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import xlm.artifacts.store

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
DURABLE = ROOT / "docs/implementation/evidence/P23-D01"
D06 = ROOT / "data/audit/p23-remediation/stage02"

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

env = json.loads((DURABLE / "environment.json").read_text())
current = {str(p.relative_to(ROOT)): sha(p)
           for directory in ("src", "tests") for p in (ROOT / directory).rglob("*.py")}
changed = [p for p, h in env["source_sha256"].items() if current[p] != h]
assert changed == [str(Path("tests/test_artifact_identity.py"))], changed
assert Path(xlm.artifacts.store.__file__).resolve() == ROOT / "src/xlm/artifacts/store.py"
for item in env["preserved"]:
    assert sha(Path(item["Path"])) == item["Hash"].lower(), item["Path"]
final = dict(source_sha256=current, executable=sys.executable,
             artifact_store_origin=xlm.artifacts.store.__file__,
             changed_since_main_verification=changed,
             reason="Additional actual public CLI legacy-publication refusal assertion; product unchanged",
             preserved_before_and_contract_hashes_match=True)
(DURABLE / "final_source.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
for name in ("legacy-cli.log", "legacy-cli.xml"):
    shutil.copyfile(HERE / name, DURABLE / name)

quality = []
for index, command in enumerate((
    ["ruff", "format", "--check", "src", "tests"], ["ruff", "check", "src", "tests"]
)):
    argv = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval", *command]
    start = time.monotonic()
    with (DURABLE / f"post-cli-quality-{index}.log").open("xb") as output:
        result = subprocess.run(argv, cwd=ROOT, stdout=output, stderr=output, timeout=30)
    quality.append(dict(argv=argv, exit_code=result.returncode, elapsed_seconds=time.monotonic() - start))
assert all(r["exit_code"] == 0 for r in quality), quality
(DURABLE / "post-cli-quality-results.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")

resources = {}
for directory in (HERE / ".venv", HERE / "focused-final-tmp", HERE / "callers-final-tmp",
                  HERE / "legacy-cli-tmp", D06 / "before-tmp"):
    sizes = [p.stat().st_size for p in directory.rglob("*") if p.is_file()]
    resources[str(directory)] = dict(files=len(sizes), logical_bytes=sum(sizes))
(DURABLE / "resources.json").write_text(json.dumps({
    "measurement": "Logical retained file sizes, including hardlinks; not allocated space or full job/cache cost",
    "paths": resources,
}, indent=2), encoding="utf-8")
d06_receipts = {}
for path in sorted((D06 / "before-tmp").glob("*/observed.json")):
    data = json.loads(path.read_text())
    d06_receipts[path.parent.name] = dict(
        state=data["result"].get("state"),
        committed_valid_targets=data["result"].get("committed_valid_targets", 0),
        origins=data["observed_train_step_origins"],
    )
d06_result = dict(exit_code=1, pytest="3 failed, 1 passed in 21.49s", fixtures_only=True,
                 implementation_changes=False, observations=d06_receipts,
                 sha256={name: sha(D06 / name) for name in ("test_d06_before.py", "before.log", "before.xml", "PLAN.md")})
(ROOT / "docs/implementation/evidence/P23-D06/results.json").write_text(json.dumps(d06_result, indent=2), encoding="utf-8")
shutil.copyfile(__file__, DURABLE / "finish_evidence.py")
print(json.dumps(dict(final_source_verified=True, quality=quality, resources=resources), indent=2))
