"""Bounded proposal reproduction only; does not implement D03."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psutil

ROOT = Path(r"D:\Project\xlm")
HERE = Path(__file__).resolve().parent
argv = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval", "pytest",
        str(HERE / "test_d03_before.py"), "-vv", "--basetemp=" + str(HERE / "before-tmp"),
        "--junitxml=" + str(HERE / "before.xml")]
env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(ROOT / "data/audit/p23-remediation/stage01/after01/.venv"),
       "UV_OFFLINE": "1", "PYTHONPATH": str(ROOT / "src"), "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
       "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}

def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "src").rglob("*.py") if "__pycache__" not in p.parts}

before = hashes()
started = time.monotonic()
peak = 0
reason = None
with (HERE / "before.log").open("xb") as output:
    process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=output, stderr=output)
    while process.poll() is None:
        try:
            parent = psutil.Process(process.pid)
            peak = max(peak, sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)]))
        except psutil.Error:
            pass
        if time.monotonic() - started > 240 or (HERE / "before.log").stat().st_size > 2 * 1024**2:
            reason = "240-second / 2-MiB output limit"
            for owned in [*psutil.Process(process.pid).children(recursive=True), psutil.Process(process.pid)]:
                try:
                    owned.kill()
                except psutil.NoSuchProcess:
                    pass
            break
        time.sleep(0.2)
    code = process.wait(timeout=10)
result = dict(argv=argv, cwd=str(ROOT), exit_code=code, elapsed_seconds=time.monotonic() - started,
              sampled_peak_tree_rss_bytes=peak, limit_reason=reason,
              source_unchanged=hashes() == before, source_sha256=before)
(HERE / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in result.items() if k != "source_sha256"}, indent=2))
raise SystemExit(code)
