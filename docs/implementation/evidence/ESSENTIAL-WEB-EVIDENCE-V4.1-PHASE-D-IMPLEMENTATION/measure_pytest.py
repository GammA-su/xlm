"""Run one pytest selection in-process; report exit, wall time and peak working set.

Windows only (GetProcessMemoryInfo). Measures the interpreter that runs the
tests, not a launcher. Usage: python measure_pytest.py <pytest args...>
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes

import pytest


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_working_set() -> int:
    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = wintypes.HANDLE
    info = ctypes.windll.psapi.GetProcessMemoryInfo
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    info.restype = wintypes.BOOL
    if not info(current(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def main() -> int:
    start = time.perf_counter()
    code = int(pytest.main(sys.argv[1:]))
    elapsed = time.perf_counter() - start
    peak = peak_working_set()
    print(json.dumps({"exit": code, "wall_seconds": round(elapsed, 3), "peak_working_set": peak}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
