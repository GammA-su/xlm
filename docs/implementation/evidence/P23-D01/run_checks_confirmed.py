"""Bounded D01 verification only; retained exact argv and process evidence."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
assert ROOT == Path(r"D:\Project\xlm")
env = dict(os.environ)
env.update(UV_OFFLINE="1", HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1",
           PYTHONPATH=str(ROOT / "src"), OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
           UV_PROJECT_ENVIRONMENT=str(HERE / ".venv"), XLM_HOME=str(HERE / "check-home"))
prefix = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval"]
group = sys.argv[1]
commands = {
    "focused": [["pytest", "tests/test_artifact_identity.py", "tests/test_artifacts.py",
                  "tests/test_ledger.py", "data/audit/p23-remediation/stage01/test_d01_before.py",
                  "-q", "--basetemp=" + str(HERE / "focused-final-tmp"),
                  "--junitxml=" + str(HERE / "focused-final.xml")]],
    "callers": [["pytest", "tests/test_checkpoint.py", "tests/test_models.py",
                  "tests/test_cli_train_demo.py", "tests/test_cli_eval_gen.py",
                  "tests/test_export.py", "tests/test_source_admission.py",
                  "tests/test_acquisition_verifier.py", "tests/test_tokenizers.py",
                  "tests/test_data_adapters.py", "tests/test_offline_workflow.py",
                  "tests/test_final_acceptance.py", "-q", "-m",
                  "not network and not cuda and not operator",
                  "--basetemp=" + str(HERE / "callers-final-tmp"),
                  "--junitxml=" + str(HERE / "callers-final.xml")]],
    "quality": [["ruff", "format", "--check", "src", "tests"],
                ["ruff", "check", "src", "tests"], ["mypy", "src"]],
}
commands["focused-confirmed"] = [
    [arg.replace("focused-final", "focused-confirmed") for arg in command]
    for command in commands["focused"]
]
commands["quality-confirmed"] = commands["quality"]
results = []
for index, tail in enumerate(commands[group]):
    log = HERE / f"{group}-final-{index}.log"
    start = time.monotonic()
    peak = 0
    reason = None
    with log.open("xb") as output:
        proc = subprocess.Popen(prefix + tail, cwd=ROOT, env=env, stdout=output, stderr=output)
        while proc.poll() is None:
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
                peak = max(peak, sum(p.memory_info().rss for p in [psutil.Process(proc.pid), *children]))
            except psutil.Error:
                pass
            if time.monotonic() - start > 300 or log.stat().st_size > 16 * 1024**2:
                reason = "300-second or 16-MiB log limit"
                for child in psutil.Process(proc.pid).children(recursive=True):
                    child.kill()
                proc.kill()
                break
            time.sleep(0.2)
        code = proc.wait(timeout=10)
    result = dict(argv=prefix + tail, cwd=str(ROOT), exit_code=code,
                  elapsed_seconds=time.monotonic() - start, sampled_peak_tree_rss_bytes=peak,
                  log=str(log), limit_reason=reason)
    results.append(result)
    print(json.dumps(result), flush=True)
(HERE / f"{group}-final-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
raise SystemExit(0 if all(r["exit_code"] == 0 for r in results) else 1)
