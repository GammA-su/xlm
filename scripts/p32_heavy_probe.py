"""Fixed three-trial control/safe-diagnostic probe; no acquisition or model code."""

from __future__ import annotations

import argparse
import faulthandler
import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "artifacts/p32-heavy-crash"


def child(mode: str) -> None:
    faulthandler.enable()
    root = Path("G:/authored")
    paths = [root / str(index) / "payload.bin" for index in range(64)]
    deadline = time.monotonic() + 15
    iterations = dumps = 0
    if mode == "control":
        while time.monotonic() < deadline:
            for path in paths:
                str(path.relative_to(root))
            iterations += 1
    else:
        from thread_diagnostics import ThreadDump

        while time.monotonic() < deadline:
            timer = ThreadDump(0.001, sys.stderr.fileno())
            timer.start()
            while timer.thread.is_alive():
                for path in paths:
                    str(path.relative_to(root))
                iterations += 1
            timer.close()
            dumps += 1
    print(json.dumps({"iterations": iterations, "dumps": dumps}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("control", "safe"))
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        child(args.mode)
        return
    rows = []
    for trial in range(1, 4):
        log = OUT / f"{args.mode}-watchdog-{trial}.log"
        with log.open("xb") as stream:
            result = subprocess.run(
                [sys.executable, "-X", "dev", __file__, args.mode, "--child"],
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=25,
            )
        rows.append({"trial": trial, "exit": result.returncode, "bytes": log.stat().st_size})
        (OUT / f"{args.mode}-watchdog-results.json").write_text(json.dumps(rows, indent=2))
        print(rows[-1], flush=True)
    if any(row["exit"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
