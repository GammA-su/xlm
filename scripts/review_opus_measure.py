"""Review-only adaptations of the frozen P31 measurement methodology.

Reconstruct ignored P31 methods from local commit 1edacbd before using this tool.
No package installation, external network or product tuning is performed here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/opus-review"
METHODS = OUT / "methods"


class SyncTimer:
    def __init__(self) -> None:
        self.original = os.fsync
        self.lock = threading.Lock()
        self.calls = 0
        self.seconds = 0.0

    def __call__(self, fd: int) -> None:
        import time

        start = time.perf_counter()
        self.original(fd)
        elapsed = time.perf_counter() - start
        with self.lock:
            self.calls += 1
            self.seconds += elapsed


def prepare() -> None:
    METHODS.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ("benchmark_systems.py", "benchmark_pipeline.py", "compare_pipeline.py"):
        original = subprocess.check_output(["git", "show", f"1edacbd:scripts/{name}"], cwd=ROOT)
        hashes[name] = hashlib.sha256(original).hexdigest()
        text = original.decode()
        if name == "benchmark_systems.py":
            text = text.replace(
                'plan, root / "scratch", root / "output", '
                "transfer_chunk_bytes=transfer_chunk_bytes",
                'plan, root / "scratch", root / "output", '
                "catalog_source_approved=len(payload) * files > 256 * MIB",
            )
            text = text.replace(
                'with measure() as row, patch("os.fsync", timed_fsync):',
                'with measure() as row, patch("os.fsync", timed_fsync), patch('
                '"xlm.data.acquisition.fetcher.WHOLE_FILE_READ_BYTES", '
                "transfer_chunk_bytes, create=True):",
            )
            text = text.replace(
                "min(256 * MIB, size * 4 + MIB)",
                "min((1024 if size > 256 * MIB else 256) * MIB, size * 4 + MIB)",
            )
            text = text.replace("args.mib * args.files > 256", "args.mib * args.files > 1024")
        (METHODS / name).write_text(text, encoding="utf8")
    (OUT / "methodology.json").write_text(
        json.dumps(
            {
                "source_commit": "1edacbd328d989aa44a5edd3d1af3ff4363ce8fe",
                "sha256": hashes,
                "adaptations": [
                    "lease read constant instead of absent P31 constructor option",
                    "whole-file ceiling raised only for separate 1 GiB control",
                ],
            },
            indent=2,
        ),
        encoding="utf8",
    )


def exact(label: str) -> None:
    if label == "green":
        sys.path.insert(0, str(OUT / "green/src"))
    sys.path.insert(0, str(METHODS))
    import pyarrow as pa
    import pyarrow.parquet as pq
    from benchmark_systems import fetch_case, make_plan, save

    from xlm.data.acquisition.progress import ProgressJournal
    from xlm.data.acquisition.verifier import AcquisitionVerifier

    rows = [{"id": i, "text": "row " + str(i) + " café 東京", "value": i % 7} for i in range(40)]
    plain = b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows)
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buffer, row_group_size=10)
    fixtures = [
        ("opaque.bin", bytes(range(256)) * 257),
        ("rows.jsonl", plain),
        ("rows.jsonl.gz", gzip.compress(plain, mtime=0)),
        ("rows.parquet", buffer.getvalue()),
    ]
    results = []
    for name, payload in fixtures:
        modes = ("whole_file",) if name.endswith("bin") else ("whole_file", "selected_records")
        for mode in modes:
            variants = (
                (False, True)
                if name.endswith("parquet") and mode == "selected_records"
                else (False,)
            )
            for projected in variants:
                changes: dict[str, Any] = {"mode": mode}
                if mode == "selected_records":
                    changes["row_ranges"] = {f"0-{name}": (10, 30)}
                    if projected:
                        changes.update(projected_fields=["id", "text"], range_coalesce_bytes=0)
                case = OUT / f"exact-{label}" / f"{mode}-{name}-{projected}"
                row = fetch_case(case, payload, 1, 1, name, **changes)
                plan = make_plan(
                    "http://127.0.0.1:47183/repo", [f"0-{name}"], 1, len(payload), **changes
                )
                journal = ProgressJournal(
                    case / "scratch/journals/authored_systems.progress.json",
                    plan.plan_id,
                    plan.compute_behavioral_hash(),
                )
                with journal.transaction() as state:
                    state.started_at = "authored-comparison"  # receipt timestamp only
                receipt = AcquisitionVerifier(plan, case / "output", journal).verify()
                results.append(
                    {
                        "name": case.name,
                        "hashes": row["output_hashes"],
                        "receipt": receipt.model_dump(),
                        "consumed": journal.state.accounting.consumed,
                        "occupancy": journal.state.accounting.occupancy,
                        "reservations": journal.state.accounting.reservations,
                        "plan_hash": plan.compute_behavioral_hash(),
                        "selection_hash": plan.compute_selection_hash(),
                        "bytes": row["bytes"],
                    }
                )
    save(OUT / f"exact-{label}.json", results)


def selected(profile: bool) -> None:
    import cProfile
    import pstats
    from unittest.mock import patch

    sys.path.insert(0, str(METHODS))
    from benchmark_acquisition_loopback import ServerProcess, build_parquet_corpus, make_plan
    from benchmark_systems import TimedLock, measure, save

    from xlm.data.acquisition.fetcher import BoundedFetcher

    corpus = OUT / "selected-corpus"
    names = build_parquet_corpus(corpus, 8, 20_000, 1000, 2000)
    total = sum((corpus / name).stat().st_size for name in names)
    destination = OUT / ("selected-profile" if profile else "selected")
    if destination.exists():
        raise ValueError("Fresh output required")
    server = ServerProcess(corpus, destination, rtt_ms=0, stream_mbps=0, redirect=True, port=47184)
    results = []
    try:
        for workers in (1,) if profile else (1, 2, 4, 8, 16):
            server.stats(reset=True)
            plan = make_plan(
                server.port,
                names,
                workers,
                total,
                mode="selected_records",
                row_ranges={name: (0, 20_000) for name in names},
                projected=["id", "text"],
                coalesce=1024**2,
                max_records=160_000,
            )
            case = destination / f"w{workers}"
            fetcher = BoundedFetcher(
                plan, case / "scratch", case / "output", catalog_source_approved=True
            )
            locks = {}
            for label, owner in (
                ("journal", fetcher.journal),
                ("capacity", fetcher.capacity_mgr),
                ("budget", fetcher.budget),
            ):
                lock = TimedLock(owner._lock)
                cast(Any, owner)._lock = lock
                locks[label] = lock
            sync = SyncTimer()

            profiler = cProfile.Profile()
            with measure() as row, patch("os.fsync", sync):
                state = profiler.runcall(fetcher.run) if profile else fetcher.run()
            assert state.status == "COMPLETED" and state.records_acquired == 160_000
            telemetry = json.loads(
                (case / "scratch/performance/loopback_bench.perf.json").read_text()
            )
            with (case / "output/selected_records.jsonl").open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            row.update(
                workers=workers,
                source_bytes=total,
                bytes=state.transferred_bytes,
                MBps=state.transferred_bytes / row["wall_seconds"] / 1e6,
                decompressed=state.decompressed_bytes,
                consumed=state.accounting.consumed,
                journal=fetcher.journal.io_stats(),
                fsync={"calls": sync.calls, "seconds": sync.seconds},
                telemetry=telemetry,
                output_sha256=digest,
                server=server.stats(),
                lock_timings={
                    name: {"wait": lock.wait_seconds, "hold": lock.hold_seconds}
                    for name, lock in locks.items()
                },
            )
            if profile:
                stats = cast(Any, pstats.Stats(profiler)).stats
                row["profile"] = [
                    {
                        "function": str(key),
                        "calls": value[1],
                        "own_seconds": value[2],
                        "cumulative_seconds": value[3],
                    }
                    for key, value in sorted(
                        stats.items(), key=lambda item: item[1][3], reverse=True
                    )[:100]
                ]
            results.append(row)
            assert len({item["output_sha256"] for item in results}) == 1
            save(destination / "report.json", results)
            print(workers, row["MBps"], digest, flush=True)
    finally:
        server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "exact", "compare", "whole", "selected"))
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--label", default="corrected")
    parser.add_argument("--mib", type=int, choices=(16, 64), default=16)
    parser.add_argument("--chunk", type=int, choices=(65536, 1048576, 8388608), default=65536)
    args = parser.parse_args()
    if ROOT.drive != "G:" or Path.cwd().resolve() != ROOT:
        raise ValueError("G: review worktree only")
    if args.mode == "prepare":
        prepare()
    elif args.mode == "exact":
        exact(args.label)
    elif args.mode == "compare":
        before = json.loads((OUT / "exact-green.json").read_text())
        after = json.loads((OUT / "exact-corrected.json").read_text())
        assert before == after, "Successful-run receipt/identity/consumption/bytes changed"
        print("Eight exact successful-run acquisition comparisons passed", flush=True)
    elif args.mode == "selected":
        selected(args.profile)
    else:
        sys.path.insert(0, str(METHODS))
        import random

        from benchmark_systems import MIB, fetch_case, save

        rng = random.Random(31)
        payload = b"".join(rng.randbytes(MIB) for _ in range(args.mib))
        report = []
        destination = OUT / f"whole-{args.mib}-{args.chunk}"
        if destination.exists():
            raise ValueError("Fresh output required")
        for workers in (1, 2, 4, 8, 16):
            row = fetch_case(
                destination / f"w{workers}", payload, workers, 16, transfer_chunk_bytes=args.chunk
            )
            assert row["status"] == "COMPLETED"
            assert row["bytes"] == 16 * len(payload)
            assert len(row["output_hashes"]) == 16
            assert set(row["output_hashes"].values()) == {hashlib.sha256(payload).hexdigest()}
            report.append(row)
            save(destination / "report.json", report)
            print(workers, row["MBps"], flush=True)


if __name__ == "__main__":
    main()
