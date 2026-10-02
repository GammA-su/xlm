"""Bounded related offline selection and static checks, with command/resource receipts."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
PREFIX = [
    "uv",
    "run",
    "--offline",
    "--locked",
    "--no-sync",
    "--extra",
    "cpu",
    "--extra",
    "eval",
    "python",
]
TESTS = [
    "component_allowlist",
    "component_calibration",
    "component_policy",
    "jsonl_gz",
    "source_jsonl_gz_run",
    "source_plan",
    "source_run",
    "source_repair",
    "source_file_bounds",
    "source_record_bound",
    "source_growth",
    "source_growth_integration",
    "source_rowgroups",
    "mix01_source_cli",
    "mix01_inventory",
    "source_admission",
    "mix01_admission",
    "source_discovery",
    "mix01_views",
    "production_ingest",
    "ifm_requirement_split",
    "ifm_production_bounds",
    "ifm_empty_text_recovery",
    "synth_adapter_semantics",
    "certified_evidence",
    "essential_web_bulk",
]


def run(name: str, command: list[str]) -> dict[str, Any]:
    env = os.environ | {
        k: "1"
        for k in (
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "HF_HUB_OFFLINE",
            "HF_DATASETS_OFFLINE",
        )
    }
    env.update(TOKENIZERS_PARALLELISM="false", PYTHONUTF8="1", XLM_HOME="G:/XLM/xlm-home")
    start = time.monotonic()
    peak = 0
    with (OUT / f"{name}.log").open("wb") as handle:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT
        )
        monitored = psutil.Process(process.pid)
        while process.poll() is None:
            try:
                peak = max(
                    peak,
                    sum(
                        p.memory_info().rss
                        for p in [monitored, *monitored.children(recursive=True)]
                    ),
                )
            except psutil.Error:
                pass
            if time.monotonic() - start > 300:
                for child in monitored.children(recursive=True):
                    child.kill()
                process.kill()
                raise RuntimeError(f"{name} exceeded its five-minute check deadline")
            time.sleep(0.1)
    log_path = OUT / f"{name}.log"
    log_path.write_bytes(
        b"".join(line.rstrip() + b"\n" for line in log_path.read_bytes().splitlines())
    )
    result = {
        "command": subprocess.list2cmdline(command),
        "exit": process.returncode,
        "seconds": round(time.monotonic() - start, 3),
        "sampled_tree_peak_rss_bytes": peak,
    }
    print(name, json.dumps(result), flush=True)
    return result


def main() -> None:
    changed = (
        subprocess.check_output(["git", "diff", "--name-only", "3473324", "--", "*.py"], cwd=ROOT)
        .decode()
        .splitlines()
    )
    added = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "*.py"], cwd=ROOT
        )
        .decode()
        .splitlines()
    )
    files = sorted(set(changed + added))
    tests = [f"tests/test_{name}.py" for name in TESTS]
    results = {}
    results["related-final"] = run(
        "related-final",
        [
            *PREFIX,
            "-m",
            "pytest",
            *tests,
            "-n",
            "16",
            "--dist=worksteal",
            "--max-worker-restart=0",
            "-m",
            "not serial",
            "-q",
        ],
    )
    results["serial-final"] = run(
        "serial-final", [*PREFIX, "-m", "pytest", *tests, "-n", "0", "-m", "serial", "-q"]
    )
    for name, options in (
        ("ruff-final", ["ruff", "check"]),
        ("format-final", ["ruff", "format", "--check"]),
        ("mypy-final", ["mypy", "--strict"]),
    ):
        results[name] = run(name, [*PREFIX, "-m", *options, *files])
    results["diff-final"] = run("diff-final", ["git", "diff", "--check"])
    for name, tail in (("evidence-show", ["evidence", "show"]), ("plan-refusal", ["plan"])):
        results[name] = run(
            name,
            [
                *PREFIX,
                "scripts/mix01_source.py",
                *tail,
                "--source-key",
                "common_pile",
                "--data-root",
                "G:/XLM",
                "--scratch-root",
                "C:/XLM-scratch",
            ],
        )
    result = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "test_basis": "authored offline fixtures; related selection, not whole repository",
        "network_acquisition": False,
        "cuda": False,
        "changed_python_files": files,
        "results": results,
    }
    (OUT / "validation-results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    failed = [
        name
        for name, value in results.items()
        if value["exit"] != (1 if name in ("evidence-show", "plan-refusal") else 0)
        and not (name == "serial-final" and value["exit"] == 5)
    ]
    if failed:
        raise SystemExit(f"checks failed: {failed}")


if __name__ == "__main__":
    main()
