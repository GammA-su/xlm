"""Bounded D06 verification; exact commands, exits, source and resource evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / "data/audit/p23-remediation/stage01/after01/.venv"
prefix = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval"]
env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(ENVIRONMENT), "UV_OFFLINE": "1",
       "PYTHONPATH": str(ROOT / "src"), "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
       "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "XLM_HOME": str(HERE / "check-home")}
groups = {
    "core": ["tests/test_frozen_execution.py", "tests/test_queue.py", "tests/test_experiment_plans.py"],
    "callers": ["tests/test_checkpoint.py", "tests/test_cli_train_demo.py", "tests/test_cli_eval_gen.py",
                "tests/test_offline_workflow.py", "tests/test_final_acceptance.py"],
    "d01": ["tests/test_artifact_identity.py", "tests/test_artifacts.py", "tests/test_ledger.py",
            "data/audit/p23-remediation/stage01/test_d01_before.py"],
}
group = sys.argv[1]
if group == "quality":
    commands = [["ruff", "format", "--check", "src", "tests"], ["ruff", "check", "src", "tests"], ["mypy", "src"]]
elif group in groups:
    commands = [["pytest", *groups[group], "-vv", "-m", "not network and not cuda and not operator",
                 "-o", "faulthandler_timeout=240", "--basetemp=" + str(HERE / (group + "-tmp")),
                 "--junitxml=" + str(HERE / (group + ".xml"))]]
else:
    commands = [["pytest", *sys.argv[2:], "-vv", "-m", "not network and not cuda and not operator",
                 "--basetemp=" + str(HERE / (group + "-tmp")), "--junitxml=" + str(HERE / (group + ".xml"))]]

def source_hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "tests") for p in (ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts}

before = source_hashes()
(HERE / f"{group}-source-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
results = []
for index, tail in enumerate(commands):
    log = HERE / f"{group}-{index}.log"
    started = time.monotonic()
    peak = 0
    reason = None
    with log.open("xb") as output:
        proc = subprocess.Popen(prefix + tail, cwd=ROOT, env=env, stdout=output, stderr=output)
        while proc.poll() is None:
            try:
                tree = [psutil.Process(proc.pid), *psutil.Process(proc.pid).children(recursive=True)]
                peak = max(peak, sum(p.memory_info().rss for p in tree))
            except psutil.Error:
                pass
            if time.monotonic() - started > 1800 or log.stat().st_size > 16 * 1024**2:
                reason = "1800-second or 16-MiB command log limit"
                for child in psutil.Process(proc.pid).children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                proc.kill()
                break
            time.sleep(0.2)
        code = proc.wait(timeout=10)
    result = dict(argv=prefix + tail, cwd=str(ROOT), exit_code=code, elapsed_seconds=time.monotonic()-started,
                  sampled_peak_tree_rss_bytes=peak, log=str(log), limit_reason=reason)
    results.append(result)
    print(json.dumps(result), flush=True)
after = source_hashes()
(HERE / f"{group}-source-after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
(HERE / f"{group}-results.json").write_text(json.dumps({"commands": results, "source_unchanged": before == after}, indent=2), encoding="utf-8")
raise SystemExit(0 if before == after and all(r["exit_code"] == 0 for r in results) else 1)
