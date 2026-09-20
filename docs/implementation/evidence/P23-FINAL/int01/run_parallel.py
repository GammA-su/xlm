"""Process-level parallel pytest scheduler (stdlib only; max_workers configurable).

Each named group runs as one independent pytest subprocess with its own
XLM_HOME, HF_HOME, basetemp, JUnit XML, and bounded log. Groups are disjoint:
every intended test runs exactly once apart from recorded diagnostic reruns.
The aggregate fails (nonzero exit) when any group fails, any JUnit XML is
missing, or any scheduled group never completes.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(r"D:\Project\xlm-final-integration")
HERE = Path(__file__).resolve().parent
ENVIRONMENT = ROOT / ".venv-final"
BASE_FLAGS = [
    "-vv",
    "-m",
    "not network and not cuda and not operator and not serial",
    "-o",
    "faulthandler_timeout=0",
    "--durations=10",
]
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
    "-m",
    "pytest",
]
LOG_BYTE_LIMIT = 16 * 1024**2
COMMAND_TIMEOUT = 1800.0


def worker_env(name: str) -> dict[str, str]:
    home = HERE / f"{name}-home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "UV_PROJECT_ENVIRONMENT": str(ENVIRONMENT),
        "UV_OFFLINE": "1",
        "PYTHONPATH": "",
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "XLM_HOME": str(home),
        "HF_HOME": str(home / "hf_home"),
    }


def run_group(name: str, tests: list[str]) -> dict[str, object]:
    started = time.monotonic()
    log = HERE / f"{name}-0.log"
    xml = HERE / f"{name}.xml"
    tmp = HERE / f"{name}-tmp"
    argv = PREFIX + tests + BASE_FLAGS + [f"--basetemp={tmp}", f"--junitxml={xml}"]
    peak, reason = 0, None
    try:
        import psutil  # noqa: E402
    except ImportError:
        psutil = None  # type: ignore[assignment]
    with log.open("xb") as output:
        proc = subprocess.Popen(argv, cwd=ROOT, env=worker_env(name), stdout=output, stderr=output)
        assert psutil is not None, "psutil is required for resource observation"
        while proc.poll() is None:
            try:
                tree = [
                    psutil.Process(proc.pid),
                    *psutil.Process(proc.pid).children(recursive=True),
                ]
                peak = max(peak, sum(p.memory_info().rss for p in tree))
            except psutil.Error:
                pass
            if time.monotonic() - started > COMMAND_TIMEOUT or log.stat().st_size > LOG_BYTE_LIMIT:
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
    elapsed = time.monotonic() - started
    junit: dict[str, object] = {"present": xml.exists()}
    if xml.exists():
        try:
            root = ET.parse(str(xml)).getroot()
            suite = root if root.tag == "testsuite" else root.find("testsuite")
            assert suite is not None
            junit.update({k: suite.get(k) for k in ("tests", "failures", "errors", "skipped")})
        except Exception as exc:  # noqa: BLE001 - record parse failure explicitly
            junit["parse_error"] = str(exc)[:200]
    return {
        "group": name,
        "argv": argv,
        "exit_code": code,
        "elapsed_seconds": elapsed,
        "sampled_peak_tree_rss_bytes": peak,
        "log": str(log),
        "limit_reason": reason,
        "junit": junit,
    }


def run_xdist(name: str, tests: list[str], workers: int) -> dict[str, object]:
    """One xdist controller for the selected parallel-safe tests."""
    started = time.monotonic()
    log = HERE / f"{name}-0.log"
    xml = HERE / f"{name}.xml"
    tmp = HERE / f"{name}-tmp"
    argv = (
        PREFIX
        + tests
        + ["-n", str(workers), "--dist=worksteal", "--max-worker-restart=0"]
        + BASE_FLAGS
        + [f"--basetemp={tmp}", f"--junitxml={xml}"]
    )
    peak, reason = 0, None
    observed_pids: set[int] = set()
    import psutil

    with log.open("xb") as output:
        proc = subprocess.Popen(argv, cwd=ROOT, env=worker_env(name), stdout=output, stderr=output)
        while proc.poll() is None:
            try:
                tree = [psutil.Process(proc.pid), *psutil.Process(proc.pid).children(recursive=True)]
                peak = max(peak, sum(p.memory_info().rss for p in tree))
                observed_pids.update(p.pid for p in tree[1:])
            except psutil.Error:
                pass
            if time.monotonic() - started > COMMAND_TIMEOUT or log.stat().st_size > LOG_BYTE_LIMIT:
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
    elapsed = time.monotonic() - started
    junit: dict[str, object] = {"present": xml.exists(), "observed_worker_pids": sorted(observed_pids)}
    if xml.exists():
        try:
            root = ET.parse(str(xml)).getroot()
            suite = root if root.tag == "testsuite" else root.find("testsuite")
            assert suite is not None
            junit.update({k: suite.get(k) for k in ("tests", "failures", "errors", "skipped")})
        except Exception as exc:  # noqa: BLE001 - record parse failure explicitly
            junit["parse_error"] = str(exc)[:200]
    return {
        "group": name,
        "argv": argv,
        "exit_code": code,
        "elapsed_seconds": elapsed,
        "sampled_peak_tree_rss_bytes": peak,
        "log": str(log),
        "limit_reason": reason,
        "junit": junit,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--partitions", required=False, help="JSON file mapping group name -> list of test node IDs"
    )
    parser.add_argument(
        "--xdist",
        required=False,
        help="Run one xdist controller named by this group over --tests-file node IDs",
    )
    parser.add_argument("--tests-file", required=False, help="File with one test node ID per line")
    options = parser.parse_args()
    if options.workers < 1 or options.workers > 16:
        raise SystemExit("--workers must be within 1..16 (combined allowance)")
    if options.xdist:
        if not options.tests_file:
            raise SystemExit("--xdist requires --tests-file")
        tests = [
            line.strip()
            for line in Path(options.tests_file).read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        if len(tests) != len(set(tests)):
            raise SystemExit("xdist selection error: duplicate test node IDs")
        print(f"xdist controller '{options.xdist}' over {options.workers} workers", flush=True)
        started = time.monotonic()
        result = run_xdist(options.xdist, tests, options.workers)
        summary = {
            "workers": options.workers,
            "elapsed_seconds": time.monotonic() - started,
            "groups": {options.xdist: result},
            "missing_groups": [],
            "failed_groups": []
            if result["exit_code"] == 0 and result["junit"].get("present")
            else [options.xdist],
        }
        (HERE / "parallel-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"failed={summary['failed_groups']} elapsed={summary['elapsed_seconds']:.1f}s",
            flush=True,
        )
        raise SystemExit(1 if summary["failed_groups"] else 0)
    if not options.partitions:
        raise SystemExit("provide --partitions or --xdist/--tests-file")
    spec = json.loads(Path(options.partitions).read_text(encoding="utf-8-sig"))
    groups: dict[str, list[str]] = spec["groups"]
    seen: list[str] = [t for tests in groups.values() for t in tests]
    duplicates = sorted({t for t in seen if seen.count(t) > 1})
    if duplicates:
        raise SystemExit(f"partition error: tests scheduled more than once: {duplicates[:5]}")
    print(f"scheduling {len(groups)} groups over {options.workers} workers", flush=True)
    started = time.monotonic()
    results: dict[str, dict[str, object]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=options.workers) as pool:
        future_to_name = {
            pool.submit(run_group, name, tests): name for name, tests in groups.items()
        }
        pending = set(future_to_name)
        while pending:
            done, pending = concurrent.futures.wait(
                pending, timeout=30, return_when=concurrent.futures.FIRST_COMPLETED
            )
            running = sorted(future_to_name[f] for f in pending)
            finished = sorted(future_to_name[f] for f in done)
            print(
                f"[{time.monotonic() - started:7.1f}s] finished={finished} running={running}",
                flush=True,
            )
            for future in done:
                try:
                    results[future_to_name[future]] = future.result()
                except Exception as exc:  # noqa: BLE001 - scheduler must report, not crash
                    name = future_to_name[future]
                    results[name] = {
                        "group": name,
                        "argv": [],
                        "exit_code": 99,
                        "elapsed_seconds": 0.0,
                        "sampled_peak_tree_rss_bytes": 0,
                        "log": str(HERE / f"{name}-0.log"),
                        "limit_reason": None,
                        "scheduler_error": str(exc)[:300],
                        "junit": {"present": False},
                    }
    missing = sorted(set(groups) - set(results))
    failed = sorted(
        n for n, r in results.items() if r["exit_code"] != 0 or not r["junit"].get("present")
    )
    summary = {
        "workers": options.workers,
        "elapsed_seconds": time.monotonic() - started,
        "groups": results,
        "missing_groups": missing,
        "failed_groups": failed,
    }
    (HERE / "parallel-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"missing={missing} failed={failed} elapsed={summary['elapsed_seconds']:.1f}s", flush=True
    )
    raise SystemExit(1 if missing or failed else 0)


if __name__ == "__main__":
    main()
