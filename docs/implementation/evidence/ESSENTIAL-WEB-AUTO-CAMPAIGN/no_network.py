"""Run ``scripts/essential_web_campaign.py`` with every socket connection refused.

Evidence tool for the offline stages (``status``, ``prepare-auto``): any
connection attempt raises, is counted and turns the exit code into 99.
"""

from __future__ import annotations

import importlib
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
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))

code = int(importlib.import_module("essential_web_campaign").main(sys.argv[1:]))
print(f"NETWORK ATTEMPTS: {len(attempts)}", file=sys.stderr)
raise SystemExit(99 if attempts else code)
