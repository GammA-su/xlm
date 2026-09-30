"""Offline probe 2: which concurrent handles block os.replace onto a target."""

import ctypes
import json
import os
import sys
import threading
import uuid
from ctypes import wintypes
from pathlib import Path

FILE_READ_ATTRIBUTES = 0x80
SHARE_RWD = 0x7
OPEN_EXISTING = 3
BACKUP = 0x02000000
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def fresh(root: Path) -> tuple[Path, Path, Path]:
    work = root / f"race-probe2-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    target, tmp = work / "p.json", work / "p.tmp"
    target.write_text("1")
    tmp.write_text("2")
    return work, target, tmp


def cleanup(work: Path) -> None:
    for p in work.iterdir():
        p.unlink()
    work.rmdir()


def replace_outcome(tmp: Path, target: Path) -> dict:
    try:
        os.replace(tmp, target)
        return {"replaced": True}
    except OSError as exc:
        return {"replaced": False, "winerror": getattr(exc, "winerror", None), "errno": exc.errno}


def attr_only(root: Path) -> dict:
    work, target, tmp = fresh(root)
    handle = k32.CreateFileW(str(target), FILE_READ_ATTRIBUTES, 0, None, OPEN_EXISTING, BACKUP, None)
    try:
        return {"case": "FILE_READ_ATTRIBUTES handle (os.stat fallback)", **replace_outcome(tmp, target)}
    finally:
        k32.CloseHandle(handle)
        cleanup(work)


def stat_storm(root: Path, rounds: int = 3000) -> dict:
    """os.stat / is_file in a tight loop while the target is replaced repeatedly."""
    work, target, _ = fresh(root)
    stop, failures = threading.Event(), []

    def reader() -> None:
        while not stop.is_set():
            try:
                target.is_file() and target.stat()
            except OSError:
                pass

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for index in range(rounds):
            tmp = work / "p.tmp"
            tmp.write_text(str(index))
            outcome = replace_outcome(tmp, target)
            if not outcome["replaced"]:
                failures.append(outcome["winerror"])
    finally:
        stop.set()
        thread.join()
        cleanup(work)
    return {"case": f"stat()/is_file() storm, {rounds} replaces", "failures": len(failures),
            "winerrors": sorted(set(failures))}


def read_storm(root: Path, rounds: int = 3000) -> dict:
    """read_bytes in a tight loop while the target is replaced repeatedly (the monitor race)."""
    work, target, _ = fresh(root)
    stop, failures, reader_errors = threading.Event(), [], []

    def reader() -> None:
        while not stop.is_set():
            try:
                target.read_bytes()
            except OSError as exc:
                reader_errors.append(getattr(exc, "winerror", None))

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for index in range(rounds):
            tmp = work / "p.tmp"
            tmp.write_text(str(index))
            outcome = replace_outcome(tmp, target)
            if not outcome["replaced"]:
                failures.append(outcome["winerror"])
    finally:
        stop.set()
        thread.join()
        leftover = (work / "p.tmp").exists()
        cleanup(work)
    return {"case": f"read_bytes() storm, {rounds} replaces", "failures": len(failures),
            "winerrors": sorted(set(failures)), "reader_errors": len(reader_errors),
            "reader_winerrors": sorted(set(reader_errors)), "tmp_left": leftover}


results = []
for root in map(Path, sys.argv[1:]):
    results.append({"volume": root.drive, **attr_only(root)})
    results.append({"volume": root.drive, **stat_storm(root)})
    results.append({"volume": root.drive, **read_storm(root)})
print(json.dumps(results, indent=2))
