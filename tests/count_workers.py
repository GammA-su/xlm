"""Picklable authored worker tasks for the count-tokens failure tests (spawned workers)."""

from __future__ import annotations

import os
import time


def crash_task(code: int) -> int:
    """A worker that dies without a Python exception (abrupt process exit)."""
    time.sleep(0.2)
    os._exit(code)


def ok_task(value: int) -> int:
    return value
