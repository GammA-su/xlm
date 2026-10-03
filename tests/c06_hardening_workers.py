"""Picklable authored worker tasks for the C06 hardening tests (spawned workers)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def sleep_task(seconds: float) -> float:
    time.sleep(seconds)
    return seconds


def spawn_stubborn_grandchild(pid_file: str) -> int:
    """A worker that starts a long-lived descendant, then blocks."""
    child = subprocess.Popen(  # noqa: S603 - fixed interpreter, authored test only
        [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    Path(pid_file).write_text(str(child.pid), encoding="utf-8")
    time.sleep(120)
    return child.pid
