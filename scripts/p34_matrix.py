"""Run named P34 benchmark cases sequentially, one fresh CUDA process each.

Contenders are interleaved across repetitions. A case refused because the GPU
is shared waits and is retried (scheduling only); a run that observed
contention keeps its record under an ``_invalid`` name and is repeated once.
Every command, exit status and duration is appended to the execution record.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/implementation/evidence/p34"
CASES = {
    "p33_sync": ["--mode", "end_to_end", "--loader", "p33"],
    "fast_sync": ["--mode", "end_to_end"],
    "prefetch": ["--mode", "prefetch"],
    "resident": ["--mode", "resident"],
    "p33_producer": ["--mode", "prefetch", "--loader", "p33"],
    "prefetch_d2": ["--mode", "prefetch", "--depth", "2"],
}


def run_case(name: str, arguments: list[str], log: list[dict[str, object]]) -> None:
    command = [sys.executable, "scripts/benchmark_p34.py", "--name", name, *arguments]
    for attempt in range(40):
        begin = time.monotonic()
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1200)
        entry = {
            "name": name,
            "command": command[1:],
            "exit": completed.returncode,
            "seconds": time.monotonic() - begin,
            "attempt": attempt,
            "stdout_tail": completed.stdout[-400:],
            "stderr_tail": completed.stderr[-400:],
        }
        log.append(entry)
        print(json.dumps({k: entry[k] for k in ("name", "exit", "seconds")}), flush=True)
        if "GPU not exclusive" in completed.stderr:
            (EVIDENCE / f"{name}.json").unlink(missing_ok=True)  # refused before measuring
            time.sleep(90)
            continue
        if completed.returncode == 3 and not name.endswith("_retry"):
            (EVIDENCE / f"{name}.json").rename(EVIDENCE / f"{name}_invalid.json")
            name = f"{name}_retry"
            command[command.index("--name") + 1] = name
            continue
        return
    raise RuntimeError(f"{name}: GPU never became exclusive")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=sorted(CASES), required=True)
    parser.add_argument("--reps", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--sections-rep", type=int, default=1)
    parser.add_argument("--extra", default="", help="extra benchmark arguments, one string")
    parser.add_argument("--prefix", default="final")
    parser.add_argument("--record", default="matrix_execution.json")
    args = parser.parse_args()
    record = EVIDENCE / args.record
    log: list[dict[str, object]] = json.loads(record.read_text()) if record.exists() else []
    for rep in args.reps:
        for case in args.cases:
            extra = shlex.split(args.extra) + (["--sections"] if rep == args.sections_rep else [])
            run_case(f"{args.prefix}_{case}_r{rep}", CASES[case] + extra, log)
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
