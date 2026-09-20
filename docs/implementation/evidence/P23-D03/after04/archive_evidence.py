"""Archive final bounded evidence, verify source stability, preserve historical failures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
GROUPS = ("focused", "core", "callers", "d01", "regressions", "prepare-config", "quality", "vocabulary")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


current = {p.relative_to(ROOT).as_posix(): digest(p)
           for folder in ("src", "tests") for p in (ROOT / folder).rglob("*")
           if p.is_file() and p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts}
preserved = json.loads((HERE.parent / "after01/preserved_hashes.json").read_text())
assert all(digest(ROOT / name) == value for name, value in preserved.items())
summary = {}
unique = set()
for group in GROUPS:
    folder = HERE.parent / "after05" if group in ("callers", "quality", "vocabulary") else HERE
    result = json.loads((folder / f"{group}-results.json").read_text())
    assert all(c["exit_code"] == 0 and c["limit_reason"] is None for c in result["commands"]), group
    for when in ("before", "after"):
        observed = json.loads((folder / f"{group}-source-{when}.json").read_text())
        # Product source never changed after after04 began. Only the unrelated
        # caller fixture and its new negative test changed; their own suites
        # are rerun in after05. Every executed test in the other batches is intact.
        changed = {name for name in set(current) | set(observed) if current.get(name) != observed.get(name)}
        permitted = {"tests/test_cli_train_demo.py", "tests/test_training_vocabulary.py"} if folder == HERE else set()
        assert all(permitted.isdisjoint(command["argv"]) for command in result["commands"])
        assert changed <= permitted, (group, when, changed)
        assert all(not name.startswith("src/") for name in changed)
        result[f"out_of_scope_test_only_changes_{when}"] = sorted(changed)
    result["product_and_executed_test_scope_unchanged"] = True
    result["evidence_directory"] = str(folder)
    if group != "quality":
        cases = list(ET.parse(folder / f"{group}.xml").iter("testcase"))
        assert all(not any(c.tag in ("failure", "error", "skipped") for c in case) for case in cases)
        unique.update((c.get("classname"), c.get("name")) for c in cases)
        result["passed"] = len(cases)
    summary[group] = result
summary["distinct_test_cases"] = len(unique)
summary["test_case_executions"] = sum(v.get("passed", 0) for v in summary.values() if isinstance(v, dict))
summary["preserved_originals"] = len(preserved)
summary["final_source_entries"] = len(current)
initial = json.loads((HERE.parent / "after01/before_source_hashes.json").read_text())
summary["changed_source_and_tests"] = {name: {"before": initial.get(name), "after": sha}
    for name, sha in current.items() if initial.get(name) != sha}
(HERE / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
(HERE / "final_source_hashes.json").write_text(json.dumps(current, indent=2), encoding="utf-8")


def archive(source: Path, destination: Path, paths: list[Path]) -> dict:
    assert not destination.exists(), destination
    assert len(paths) < 4096 and sum(p.stat().st_size for p in paths) < 64 * 1024**2
    destination.mkdir(parents=True)
    entries = {}
    for path in paths:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        entries[relative.as_posix()] = digest(target)
        assert entries[relative.as_posix()] == digest(path)
    (destination / "sha256.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return {"entries": len(entries), "bytes": sum(p.stat().st_size for p in paths)}


paths = [p for p in HERE.iterdir() if p.is_file() and p.suffix in (".json", ".log", ".xml", ".py")]
for fixture in (HERE / "focused-tmp").glob("test_public_two_source_prepare*"):
    if not fixture.is_dir():
        continue
    paths.extend((fixture / "commands").glob("*.json"))
    for name in ("data_state.json", "checkpoint_meta.json", "execution.json"):
        paths.extend(p for p in fixture.rglob(name) if "checkpoints" in p.parts)
    paths.extend(p for p in (fixture / "tree").iterdir() if p.suffix in (".yaml", ".json"))
report = {"d03": archive(HERE, ROOT / "docs/implementation/evidence/P23-D03/after04", sorted(set(paths)))}
after05 = HERE.parent / "after05"
paths = [p for p in after05.iterdir() if p.is_file() and p.suffix in (".json", ".log", ".xml", ".py")]
report["d03_caller_correction"] = archive(after05, ROOT / "docs/implementation/evidence/P23-D03/after05", sorted(set(paths)))
d02 = ROOT / "data/audit/p23-remediation/stage04"
paths = [p for p in d02.iterdir() if p.is_file() and p.suffix in (".md", ".json", ".log", ".xml", ".py")]
paths += list((d02 / "before-tmp").glob("*/observed.json"))
report["d02"] = archive(d02, ROOT / "docs/implementation/evidence/P23-D02", sorted(set(paths)))
(HERE / "archive_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"passed_executions": summary["test_case_executions"], "distinct": len(unique), **report}))

