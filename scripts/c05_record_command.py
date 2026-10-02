"""Record exact bounded preparation/check commands and sampled process-tree RSS."""

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
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or Path(args.name).name != args.name:
        parser.error("supply a command and a plain evidence name")
    args.evidence.mkdir(parents=True, exist_ok=True)
    log = args.evidence / f"{args.name}.log"
    record = args.evidence / f"{args.name}.json"
    if record.exists() or log.exists():
        parser.error("evidence names are write-once")
    started = time.monotonic()
    peak = 0
    with log.open("x", encoding="utf-8") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        while process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                rss = 0
                for child in [parent, *parent.children(recursive=True)]:
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                peak = max(peak, rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(0.05)
    raw_log = log.read_bytes()
    log.write_bytes(
        (
            "\n".join(line.rstrip() for line in raw_log.decode("utf-8").splitlines()).rstrip()
            + "\n"
        ).encode("utf-8")
        if raw_log
        else b""
    )
    payload = {
        "raw_stdout_sha256": hashlib.sha256(raw_log).hexdigest(),
        "log_storage": "UTF-8, LF, trailing whitespace removed; command output content preserved",
        "command": command,
        "cwd": str(Path.cwd()),
        "exit_status": process.returncode,
        "wall_seconds": time.monotonic() - started,
        "sampled_peak_process_tree_rss_bytes": peak,
        "sampling_interval_seconds": 0.05,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "environment": {
            key: os.environ.get(key)
            for key in (
                "UV_PROJECT_ENVIRONMENT",
                "PYTHONPATH",
                "UV_OFFLINE",
                "HF_HUB_OFFLINE",
                "HF_DATASETS_OFFLINE",
                "TRANSFORMERS_OFFLINE",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "TOKENIZERS_PARALLELISM",
            )
        },
    }
    with record.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload))
    print(log.read_text(encoding="utf-8")[-12000:])
    return int(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
