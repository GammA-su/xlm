"""Copy bounded D06 evidence only; leave fixture originals and before evidence intact."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
DEST = ROOT / "docs/implementation/evidence/P23-D06/after05"
DEST.mkdir(exist_ok=True)

def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def copy(source: Path, relative: Path) -> None:
    target = DEST / relative
    assert target.resolve().is_relative_to(DEST.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert digest(target) == digest(source), f"Evidence conflict: {target}"
    else:
        shutil.copyfile(source, target)

groups = {}
group_bases = {name: HERE.parent / "after06" if name == "callers" else HERE
               for name in ("core", "callers", "regressions", "d01", "quality")}
for name in ("core", "callers", "regressions", "d01", "quality"):
    result = json.loads((group_bases[name] / f"{name}-results.json").read_text())
    assert result["source_unchanged"]
    assert all(c["exit_code"] == 0 and c["limit_reason"] is None for c in result["commands"])
    if name != "quality":
        suite = ET.parse(group_bases[name] / f"{name}.xml").getroot().find("testsuite")
        assert suite is not None
        result["junit"] = dict(suite.attrib)
    groups[name] = result

environment = json.loads((HERE / "environment.json").read_text())
assert environment["preserved_hashes_match"]
for relative, expected in environment["source_sha256"].items():
    assert digest(ROOT / relative) == expected, relative

processes = []
contexts = []
logical = {}
for group in ("core", "callers", "regressions", "d01"):
    base = group_bases[group] / (group + "-tmp")
    size = 0
    entries = 0
    for directory, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()
                   and not (Path(directory) / d).is_junction()]
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            entries += 1
            assert entries < 200_000
            length = path.stat().st_size
            size += length
            if name == "process.json" and path.parent.name.startswith(("worker", "rejected-worker")):
                assert length < 2 * 1024**2
                value = json.loads(path.read_text())
                processes.append({"path": str(path.relative_to(HERE.parent)), **value})
                copy(path, path.relative_to(HERE.parent))
                for output in ("stdout.log", "stderr.log"):
                    candidate = path.parent / output
                    if candidate.is_file() and candidate.stat().st_size <= 64 * 1024:
                        copy(candidate, candidate.relative_to(HERE.parent))
            if name == "execution.json" and path.parent.name.endswith("_final"):
                assert length < 2 * 1024**2
                value = json.loads(path.read_text())
                envelope = value.get("envelope")
                if envelope is None:
                    contexts.append({"path": str(path.relative_to(HERE.parent)),
                                     "unresolved_execution_evidence": value})
                else:
                    contexts.append({"path": str(path.relative_to(HERE.parent)), "plan_hash": value["plan_hash"],
                                     "execution_hash": envelope["execution_hash"], "code_hash": envelope["code_hash"],
                                     "environment": envelope["environment"], "observations": value["observations"]})
                for sidecar in ("execution.json", "runtime.json", "manifest.json"):
                    candidate = path.parent / sidecar
                    if candidate.is_file():
                        copy(candidate, candidate.relative_to(HERE.parent))
    logical[group] = {"files": entries, "logical_bytes": size}

summary = {"groups": groups, "logical_fixture_storage": logical, "worker_processes": processes,
           "final_checkpoint_contexts": contexts, "notes": [
               "Windows CPU authored fixtures only; no GPU/live/operator validation.",
               "Summed RSS may count shared pages repeatedly; logical sizes include hardlinks and do not measure physical allocation.",
               "Previous iterations and shared uv cache/environment excluded from fixture size totals.",
               "Complete platform offline rerun remains deferred."]}
(HERE / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
for path in HERE.iterdir():
    if path.is_file() and path.suffix in (".json", ".xml", ".log", ".py"):
        copy(path, Path(path.name))
for path in (HERE.parent / "after06").iterdir():
    if path.is_file() and path.suffix in (".json", ".xml", ".log", ".py"):
        copy(path, Path("after06") / path.name)
inventory = {p.relative_to(DEST).as_posix(): digest(p) for p in DEST.rglob("*") if p.is_file()}
(DEST / "sha256.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
print(json.dumps({"groups": {k: v.get("junit", "quality passed") for k, v in groups.items()},
                  "worker_records": len(processes), "final_contexts": len(contexts), "storage": logical}, indent=2))
