"""Run one Essential-Web driver script with every socket connection refused.

Usage: ``no_network.py <essential_web_fast|essential_web_campaign|<path.py>> ARGS...``.
Any connection attempt raises, is counted and turns the exit code into 99.
"""

from __future__ import annotations

import importlib
import runpy
import socket
import sys
from pathlib import Path
from typing import Any

attempts: list[str] = []


def refuse(*args: Any, **kwargs: Any) -> None:
    attempts.append(repr(args[1:2]))
    raise AssertionError("network attempt during an offline command")


socket.socket.connect = refuse  # type: ignore[method-assign]
socket.socket.connect_ex = refuse  # type: ignore[method-assign,assignment]
socket.create_connection = refuse  # type: ignore[assignment]
repository = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repository / "scripts"))

target, arguments = sys.argv[1], sys.argv[2:]
if target.endswith(".py"):
    sys.argv = [target, *arguments]
    try:
        runpy.run_path(target, run_name="__main__")
        code = 0
    except SystemExit as exc:
        code = int(exc.code or 0) if not isinstance(exc.code, str) else 1
else:
    code = int(importlib.import_module(target).main(arguments))
print(f"NETWORK ATTEMPTS: {len(attempts)}", file=sys.stderr)
raise SystemExit(99 if attempts else code)
