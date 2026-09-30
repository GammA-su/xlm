"""Offline probe: os.replace onto a file another handle is reading (Windows semantics)."""

import ctypes
import json
import msvcrt
import os
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

GENERIC_READ = 0x80000000
SHARE_RW = 0x1 | 0x2
SHARE_RWD = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]


def attempt(root: Path, label: str, opener) -> dict:
    work = root / f"race-probe-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    target, tmp = work / "f.progress.json", work / "f.progress.tmp"
    target.write_text('{"rows": 1}')
    tmp.write_text('{"rows": 2}')
    reader = opener(target)
    try:
        os.replace(tmp, target)
        outcome = {"replaced": True}
    except OSError as exc:
        outcome = {
            "replaced": False,
            "type": type(exc).__name__,
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
            "strerror": exc.strerror,
            "tmp_left": tmp.exists(),
        }
    finally:
        reader.close()
    if not outcome["replaced"]:
        os.replace(tmp, target)  # after the reader closes the same replace succeeds
        outcome["replace_after_close"] = json.loads(target.read_text())["rows"] == 2
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    return {"volume": root.drive, "reader": label, **outcome}


def python_open(path: Path):
    return path.open("rb")  # what Path.read_bytes() holds while reading


def share_delete_open(path: Path):
    handle = k32.CreateFileW(str(path), GENERIC_READ, SHARE_RWD, None, OPEN_EXISTING, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")


results = []
for root in map(Path, sys.argv[1:]):
    results.append(attempt(root, "python open() / Path.read_bytes", python_open))
    results.append(attempt(root, "CreateFileW FILE_SHARE_READ|WRITE|DELETE", share_delete_open))
print(json.dumps({"python": sys.version.split()[0], "results": results}, indent=2))
