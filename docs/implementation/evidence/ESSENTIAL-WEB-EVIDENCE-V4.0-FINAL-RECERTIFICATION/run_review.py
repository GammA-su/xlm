"""Offline review command recorder; no implementation changes or live execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main() -> None:
    os.chdir(ROOT)
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[name] = "1"
    env.update(TOKENIZERS_PARALLELISM="false", UV_OFFLINE="1", HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1")
    records = json.loads((HERE / "commands.json").read_text()) if len(sys.argv) > 1 else []

    def run(name: str, argv: list[str]) -> None:
        start = time.perf_counter()
        result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=180)
        elapsed = time.perf_counter() - start
        (HERE / f"{name}.txt").write_bytes(result.stdout + result.stderr)
        records.append(dict(name=name, argv=argv, exit=result.returncode, seconds=elapsed))
        (HERE / "commands.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(name, result.returncode, round(elapsed, 3), flush=True)
        if result.returncode:
            raise RuntimeError(f"{name} failed: inspect retained output")

    if "--independent-only" in sys.argv:
        run("independent-corrected", ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval", "python", str(HERE / "independent_check.py")])
        return
    if "--injection-only" in sys.argv:
        run("injection", ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval", "python", "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/probe_injection.py"])
        results = json.loads((HERE / "injection.txt").read_text())
        assert set(results) == {"M_data_chunk", "T_text_page"}
        for case in results.values():
            assert case["status"] == "INCOMPLETE" and case["run_status"] == "STOPPED"
            assert case["requests_after_injection"] == 0
            assert "stored operations differ from the frozen plan" in case["stop_reason"]
        print("injection assertions PASS")
        return
    status = subprocess.check_output(["git", "status", "--porcelain=v1", "-uall"], text=True)
    prior = {}
    for line in status.splitlines():
        path = line[3:]
        if "FINAL-RECERTIFICATION" not in path and Path(path).is_file():
            prior[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    baseline = dict(head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), platform=platform.platform(), python=sys.version, executable=sys.executable, prior_files_sha256=prior, root_absent=not Path("G:/Project/xlm-evidence-v4/essential-web").exists())
    assert baseline["head"] == "a9f89c0d13ef01e9ab387104c4acd4da948d2366"
    assert baseline["root_absent"]
    (HERE / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    prefix = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval", "python"]
    run("verify", prefix + ["scripts/evidence_v4.py", "verify"])
    run("focused", prefix + ["-m", "pytest", "-n", "0", "-q", "-p", "no:cacheprovider", "tests/test_evidence_v4_plan.py", "tests/test_evidence_v4_transport.py", "tests/test_evidence_v4_engine.py", "tests/test_evidence_v4_e2e.py", "tests/test_evidence_v4_wire.py", f"--junitxml={HERE / 'focused.xml'}"])
    run("repair-probe", prefix + ["docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/probe_b01_b02.py", str(ROOT)])
    run("independent", prefix + [str(HERE / "independent_check.py")])


if __name__ == "__main__":
    main()
