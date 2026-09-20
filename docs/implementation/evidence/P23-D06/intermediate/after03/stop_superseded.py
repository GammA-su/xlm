"""Stop only this task's two superseded fixture-check process trees; retain evidence."""
import json
from pathlib import Path
import psutil

ROOT = Path(r"D:\Project\xlm")
target = ROOT / "data/audit/p23-remediation/stage02/after03/run_checks.py"
selected = []
for process in psutil.process_iter(["pid", "cmdline"]):
    argv = process.info["cmdline"] or []
    if len(argv) >= 3 and argv[-1] in ("core", "callers"):
        try:
            matches = (ROOT / argv[-2]).resolve() == target
        except (OSError, ValueError):
            matches = False
        if matches:
            selected.append(process)
owned = {p.pid: p for parent in selected for p in [parent, *parent.children(recursive=True)]}
evidence = {"reason": "Superseded by settled-source D06 verification after descriptor-boundary repair",
            "roots": [p.pid for p in selected], "owned_tree_pids": sorted(owned)}
(target.parent / "superseded.json").write_text(json.dumps(evidence, indent=2))
for process in [*selected, *owned.values()]:
    try:
        process.kill()
    except psutil.NoSuchProcess:
        pass
psutil.wait_procs(list(owned.values()), timeout=10)
print(json.dumps(evidence))
