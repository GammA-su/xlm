"""Bounded, fresh-output P32 diagnosis; one controller, no automatic retries."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-heavy-crash"
NODE = "tests/test_queue.py::test_toy_campaign_runs_sequentially_to_success"


def run(name: str, args: list[str]) -> int:
    log = OUT / f"{name}.log"
    if log.exists():
        raise ValueError("Fresh evidence name required; no overwrite/retry")
    argv = [
        sys.executable,
        *(["-X", "dev"] if name.startswith("before-") else []),
        "-m",
        "pytest",
        *args,
        "-q",
        "-rs",
        "--strict-markers",
        "-o",
        "faulthandler_timeout=120",
        "--durations=20",
        "--basetemp",
        str(OUT / f"pytest-{name}"),
        "-p",
        "pytest_evidence",
        "--evidence-json",
        str(OUT / f"{name}.json"),
    ]
    started = time.monotonic()
    peak = 0
    with log.open("wb") as stream:
        child = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        root = psutil.Process(child.pid)
        while child.poll() is None:
            try:
                owned = [root, *root.children(recursive=True)]
                peak = max(peak, sum(p.memory_info().rss for p in owned))
            except psutil.Error:
                owned = [root]
            if time.monotonic() - started > 1800 or peak > 24 * 1024**3:
                for process in reversed(owned):
                    try:
                        process.kill()
                    except psutil.NoSuchProcess:
                        pass
                raise RuntimeError("1800 second / 24 GiB experiment bound exceeded")
            time.sleep(0.5)
        code = child.wait()
    result = {
        "argv": argv,
        "exit": code,
        "wall_seconds": time.monotonic() - started,
        "peak_tree_rss_bytes": peak,
    }
    (OUT / f"{name}.run.json").write_text(json.dumps(result, indent=2) + "\n")
    evidence = json.loads((OUT / f"{name}.json").read_text())
    assert evidence["exit_code"] == code
    collections = list(evidence["worker_collections"].values())
    selected = set(collections[0] if collections else evidence["selected"])
    assert selected, "experiment selected no tests"
    if collections:
        assert all(set(nodes) == selected for nodes in collections), "worker collection mismatch"
    if code == 0:
        assert {row["nodeid"] for row in evidence["reports"]} == selected, "missing outcomes"
    print(name, json.dumps(result), flush=True)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("before", "stress", "heavy", "gate"))
    args = parser.parse_args()
    if Path.cwd().resolve() != Path("G:/Project/xlm-p32-heavy-crash"):
        raise ValueError("Heavy-crash worktree only")
    if args.mode == "stress":
        # Five repetitions declared before execution; stop on failure, never
        # retry a failed run or reuse its output. Normal group scheduling.
        for index in range(1, 6):
            code = run(
                f"stress-{index}", [NODE, "-n", "4", "--dist=loadgroup", "--max-worker-restart=0"]
            )
            if code:
                raise SystemExit(code)
        return
    if args.mode == "heavy":
        assert all(
            json.loads((OUT / f"stress-{i}.run.json").read_text())["exit"] == 0 for i in range(1, 6)
        )
        raise SystemExit(
            run(
                "heavy",
                ["-m", "serial_heavy", "-n", "4", "--dist=loadgroup", "--max-worker-restart=0"],
            )
        )
    if args.mode == "gate":
        from p32_gate import LEGS

        assert json.loads((OUT / "heavy.run.json").read_text())["exit"] == 0
        assert (OUT / "focused.exit").read_text().strip() == "0"
        assert (OUT / "final-unit.exit").read_text().strip() == "0"
        assert all(
            row["exit"] == 0 for row in json.loads((OUT / "safe-watchdog-results.json").read_text())
        )
        for name, (expression, workers, distribution) in LEGS.items():
            selection = ["-m", expression, "-n", str(workers)]
            if distribution:
                selection += ["--dist", distribution, "--max-worker-restart=0"]
            code = run(f"gate-{name}", selection)
            if code:
                raise SystemExit(code)
        return
    # Declared before execution: one serial node and one normal four-worker
    # loadgroup node. That node is the entire p30b-queue-campaign domain.
    for name, pytest_args in (
        ("before-serial", [NODE, "-n", "0"]),
        ("before-group", [NODE, "-n", "4", "--dist=loadgroup", "--max-worker-restart=0"]),
    ):
        run(name, pytest_args)


if __name__ == "__main__":
    main()
