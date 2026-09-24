"""Frozen authored G: measurements; profiling is separate from throughput trials."""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/p32-perf-closeout"


class Profile:
    """Inclusive summed thread timings, not additive wall-time fractions."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, float]] = {}
        self.lock = threading.Lock()
        self.local = threading.local()

    def record(self, name: str, start: float) -> None:
        elapsed = time.perf_counter() - start
        with self.lock:
            row = self.rows.setdefault(name, {"calls": 0, "seconds": 0.0})
            row["calls"] += 1
            row["seconds"] += elapsed

    def wrap(self, function: Callable[..., Any], name: str) -> Callable[..., Any]:
        @functools.wraps(function)
        def timed(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                label = (
                    "publication_intent"
                    if name == "disk_reservation_intent" and kwargs.get("publication")
                    else name
                )
                self.record(label, start)

        return timed

    def trace(self, frame: FrameType, event: str, arg: Any) -> None:
        if event not in ("c_call", "c_return", "c_exception"):
            return
        name = getattr(arg, "__name__", "")
        filename = frame.f_code.co_filename.replace("\\", "/")
        label = None
        if filename.endswith("acquisition/fetcher.py"):
            label = {"write": "payload_write", "update": "incremental_hash"}.get(name)
        if label:
            if event == "c_call":
                self.local.start = time.perf_counter()
            else:
                self.record(label, self.local.start)

    @contextlib.contextmanager
    def active(self) -> Iterator[None]:
        import filelock

        from xlm.data.acquisition import publication
        from xlm.data.acquisition.disk import AtomicFileWriter, StorageCapacityManager
        from xlm.data.acquisition.progress import ProgressJournal
        from xlm.data.sources.transport import TransportBudget

        with contextlib.ExitStack() as stack:
            for owner, name, label in (
                (ProgressJournal, "_load", "journal_read"),
                (ProgressJournal, "_write", "journal_write"),
                (ProgressJournal, "_retire_replacements", "orphan_inventory"),
                (ProgressJournal, "control_bytes", "control_inventory"),
                (ProgressJournal, "set_status", "final_status"),
                (publication, "_verify", "payload_verification"),
                (publication, "complete_publication", "settlement_completion"),
                (publication, "publish_output", "publication_total"),
                (StorageCapacityManager, "reserve_disk_space", "disk_reservation_intent"),
                (AtomicFileWriter, "atomic_complete", "hardlink_publication"),
                (TransportBudget, "read_leased", "download_read"),
                (filelock.FileLock, "acquire", "filelock_wait"),
            ):
                original = getattr(owner, name)
                replacement: Any = self.wrap(original, label)
                if owner is AtomicFileWriter:
                    replacement = staticmethod(replacement)
                stack.enter_context(patch.object(owner, name, replacement))
            original_sync = os.fsync

            def sync(fd: int) -> None:
                frame = sys._getframe(1)
                if frame.f_code.co_name == "timed_fsync" and frame.f_back is not None:
                    frame = frame.f_back
                caller = frame.f_code.co_name
                kind = (
                    "journal"
                    if caller == "_write"
                    else "control"
                    if caller == "write_diagnostic"
                    else "data"
                )
                self.wrap(original_sync, f"fsync_{kind}")(fd)

            stack.enter_context(patch("os.fsync", sync))
            sys.setprofile(self.trace)
            threading.setprofile(self.trace)
            try:
                yield
            finally:
                sys.setprofile(None)
                threading.setprofile(None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("whole", "profile", "exact", "compare"))
    parser.add_argument("--label", choices=("baseline", "current"), default="current")
    args = parser.parse_args()
    if Path.cwd().resolve() != Path("G:/Project/xlm-p32-perf-closeout"):
        raise ValueError("Closeout worktree only")
    import review_opus_measure as method

    method.OUT, method.METHODS = OUT, OUT / "methods"
    if args.mode == "exact":
        method.exact(args.label)
        return
    if args.mode == "compare":
        before = json.loads((OUT / "exact-baseline.json").read_text())
        after = json.loads((OUT / "exact-current.json").read_text())
        assert before == after
        (OUT / "exact-comparison.json").write_text(json.dumps({"equal": True, "cases": len(after)}))
        return
    sys.path.insert(0, str(method.METHODS))
    from benchmark_systems import MIB, fetch_case, save

    rng = random.Random(31)
    payload = b"".join(rng.randbytes(MIB) for _ in range(64))
    digest = hashlib.sha256(payload).hexdigest()
    destination = OUT / f"{args.mode}-{args.label}"
    destination.mkdir(exist_ok=False)
    rows = []
    for repetition in range(1, 2 if args.mode == "profile" else 4):
        for workers in (1, 8, 16):
            profiler = Profile()
            with profiler.active() if args.mode == "profile" else contextlib.nullcontext():
                row = fetch_case(
                    destination / f"r{repetition}-w{workers}",
                    payload,
                    workers,
                    16,
                    transfer_chunk_bytes=65536,
                )
            assert row["status"] == "COMPLETED" and row["bytes"] == 16 * len(payload)
            assert len(row["output_hashes"]) == 16 and set(row["output_hashes"].values()) == {
                digest
            }
            row.update(repetition=repetition, profile=profiler.rows)
            rows.append(row)
            save(destination / "report.json", rows)
            print(args.mode, args.label, repetition, workers, row["MBps"], flush=True)


if __name__ == "__main__":
    main()
