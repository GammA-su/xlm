"""Run the bounded offline P23 checks in a supplied separate repository copy.

Invoke with uv and an environment containing psutil. This script never downloads
dependencies or data, and never invokes CUDA, network, or operator-marked tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import psutil


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    repo, evidence = args.repo.resolve(), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    env.update(
        UV_OFFLINE="1",
        HF_HUB_OFFLINE="1",
        HF_DATASETS_OFFLINE="1",
        XLM_HOME=str(evidence / "home"),
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        PYTHONUTF8="1",
    )
    prefix = ["uv", "run", "--offline", "--locked", "--extra", "cpu", "--extra", "eval"]
    commands = [
        ("sync", ["uv", "sync", "--offline", "--locked", "--extra", "cpu", "--extra", "eval"]),
        ("ruff", [*prefix, "ruff", "check", "src", "tests", "scripts"]),
        ("format", [*prefix, "ruff", "format", "--check", "src", "tests", "scripts"]),
        ("mypy", [*prefix, "mypy", "src"]),
        (
            "tests",
            [
                *prefix,
                "pytest",
                "-q",
                "-rs",
                "-m",
                "not network and not cuda and not operator",
                "--durations=10",
                f"--junitxml={evidence / 'tests.xml'}",
                f"--basetemp={evidence / 'pytest'}",
            ],
        ),
        ("doctor", [*prefix, "xlm", "doctor", "--json"]),
        ("demo", [*prefix, "xlm", "demo"]),
        ("wheel", ["uv", "build", "--offline", "--wheel", "--out-dir", str(evidence / "dist")]),
    ]
    report: dict = {
        "platform": platform.platform(),
        "cwd": str(repo),
        "commands": [],
        "environment": {
            k: env[k]
            for k in (
                "UV_OFFLINE",
                "HF_HUB_OFFLINE",
                "HF_DATASETS_OFFLINE",
                "XLM_HOME",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
            )
        },
        "install_scope": "fresh environment; pre-existing uv package cache; offline",
        "inputs": {},
    }
    for root in (repo / "src", repo / "tests", repo / "scripts"):
        for file in sorted(root.rglob("*.py")):
            report["inputs"][file.relative_to(repo).as_posix()] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    for name in ("uv.lock", "pyproject.toml", ".python-version"):
        report["inputs"][name] = hashlib.sha256((repo / name).read_bytes()).hexdigest()
    for name, command in commands:
        start, peak, timed_out = time.monotonic(), 0, False
        print(f"Running {name}: {subprocess.list2cmdline(command)}", flush=True)
        with (evidence / f"{name}.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(command, cwd=repo, env=env, stdout=log, stderr=log)
            while proc.poll() is None:
                try:
                    root_process = psutil.Process(proc.pid)
                    processes = [root_process, *root_process.children(recursive=True)]
                    rss = 0
                    for process in processes:
                        try:
                            rss += process.memory_info().rss
                        except psutil.Error:
                            pass
                    peak = max(peak, rss)
                    if time.monotonic() - start > 1800:
                        timed_out = True
                        for process in reversed(processes):
                            try:
                                process.kill()
                            except psutil.Error:
                                pass
                        proc.wait(timeout=30)
                except psutil.Error:
                    pass
                time.sleep(0.2)
            code = proc.wait()
        report["commands"].append(
            {
                "name": name,
                "argv": command,
                "exit_code": code,
                "elapsed_seconds": round(time.monotonic() - start, 3),
                "peak_sampled_process_tree_rss_bytes": peak,
                "timed_out": timed_out,
            }
        )
        (evidence / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{name}: exit {code}", flush=True)
        if name == "sync" and code:
            break
    return int(any(c["exit_code"] != 0 for c in report["commands"]))


if __name__ == "__main__":
    raise SystemExit(main())
