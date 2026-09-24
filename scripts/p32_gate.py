"""Sequential six-leg offline gate, eligible only after focused/crash certification."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-recovery"
LEGS = {
    "tier-a": (
        "not serial and not performance and not scale and not cuda and not network "
        "and not operator and not optional_dependency and not environment_setup",
        16,
        "worksteal",
    ),
    "core": ("serial_core and not serial_exclusive", 4, "loadgroup"),
    "exclusive": ("serial_core and serial_exclusive", 0, None),
    "heavy": ("serial_heavy", 4, "loadgroup"),
    "scale": (
        "scale and not network and not cuda and not operator and not environment_setup",
        0,
        None,
    ),
    "optional": (
        "optional_dependency and not network and not cuda and not operator "
        "and not environment_setup",
        4,
        "loadgroup",
    ),
}


def eligible() -> None:
    if (OUT / "focused.exit").read_text().strip() != "0":
        raise ValueError("focused gate must pass first")
    assert (OUT / "orphans-final.exit").read_text().strip() == "0"
    assert (OUT / "bootstrap.exit").read_text().strip() == "0"
    exact = json.loads((OUT / "exact-comparison.json").read_text())
    assert exact["equal"] and exact["cases"] == 8
    for name, count in (("early", 44), ("mature", 44), ("selected", 20)):
        rows = json.loads((OUT / f"crashes-{name}/report.json").read_text())
        assert len(rows) == count and all(row["restart_exit"] == 0 for row in rows)
        assert not list((OUT / f"crashes-{name}").rglob("*.tmp")), "orphan survived recovery"


def run(name: str) -> int:
    expression, workers, distribution = LEGS[name]
    destination = OUT / "gate"
    destination.mkdir(exist_ok=True)
    report = destination / f"{name}.json"
    if report.exists() or (destination / f"{name}.log").exists():
        raise ValueError("gate output must be fresh; no silent retry")
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--strict-markers",
        "-rs",
        "-m",
        expression,
        "-n",
        str(workers),
        "--durations=20",
        "-o",
        "faulthandler_timeout=120",
        "--basetemp",
        str(destination / f"pytest-{name}"),
        "-p",
        "pytest_evidence",
        "--evidence-json",
        str(report),
    ]
    if distribution:
        argv += ["--dist", distribution, "--max-worker-restart=0"]
    started = time.monotonic()
    peak = 0
    failure: str | None = None
    with (destination / f"{name}.log").open("wb") as log:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        root = psutil.Process(process.pid)
        while process.poll() is None:
            try:
                children = root.children(recursive=True)
                rss = sum(p.memory_info().rss for p in [root, *children] if p.is_running())
                peak = max(peak, rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                children = []
            if time.monotonic() - started > 1800 or peak > 24 * 1024**3:
                failure = "gate exceeded 1800 seconds or 24 GiB sampled tree RSS"
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                break
            time.sleep(0.5)
        code = process.wait()
    result: dict[str, Any] = {
        "argv": argv,
        "exit": code,
        "wall_seconds": time.monotonic() - started,
        "peak_tree_rss_bytes": peak,
        "failure": failure,
    }
    (destination / f"{name}.run.json").write_text(json.dumps(result, indent=2))
    (destination / f"{name}.exit").write_text(str(code))
    if not report.exists():
        raise ValueError(f"{name}: missing pytest evidence")
    evidence = json.loads(report.read_text())
    assert evidence["exit_code"] == code
    assert evidence["reports"], "no test groups completed"
    collections = list(evidence["worker_collections"].values())
    expected = set(collections[0] if collections else evidence["selected"])
    assert expected, "required leg collected no tests"
    if collections:
        assert all(set(nodes) == expected for nodes in collections), "worker collection mismatch"
    if code == 0:
        assert {row["nodeid"] for row in evidence["reports"]} == expected, "missing test groups"
    print(name, code, result["wall_seconds"], flush=True)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("tier-a", "remaining", "finish-unrun"))
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT or ROOT != Path("G:/Project/xlm-p32-recovery"):
        raise ValueError("P32 worktree only")
    eligible()
    if args.mode != "tier-a":
        assert (OUT / "gate/tier-a.exit").read_text().strip() == "0"
    names: tuple[str, ...]
    if args.mode == "finish-unrun":
        # Complete coverage after the preserved heavy worker crash. Never rerun
        # a completed leg or turn its failed aggregate into a successful one.
        assert (OUT / "gate/heavy.exit").read_text().strip() != "0"
        names = ("scale", "optional")
    else:
        names = ("tier-a",) if args.mode == "tier-a" else tuple(LEGS)[1:]
    for name in names:
        code = run(name)
        if code:
            raise SystemExit(code)


if __name__ == "__main__":
    main()
