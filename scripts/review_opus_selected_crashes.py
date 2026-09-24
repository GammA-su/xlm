"""Authored selected-record process deaths; no external network or performance claims."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

from test_acquisition_leases import AuthoredHandler, plan_for
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan
from xlm.data.acquisition.records import StreamingJsonlWriter
from xlm.data.acquisition.selection import _BatchCommitter


def child(root: Path, phase: str) -> None:
    plan = AcquisitionPlan.model_validate_json((root / "plan.json").read_text())
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    scanned = 0
    staged = 0

    def die() -> None:
        with (root / "death.json").open("w") as stream:
            json.dump({"scanned": scanned, "staged": staged, "phase": phase}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(73)

    original_scan = _BatchCommitter.scanned
    original_add = _BatchCommitter.add_retained
    original_write = StreamingJsonlWriter.write_line
    original_close = _BatchCommitter.close
    original_link = os.link

    def scan(batcher: _BatchCommitter) -> None:
        nonlocal scanned
        original_scan(batcher)
        scanned += 1
        if phase == "scan" and scanned == 3:
            die()  # before any selected row: all three are skipped

    def add(batcher: _BatchCommitter, amount: int) -> None:
        original_add(batcher, amount)
        if phase == "staging_reserved":
            die()

    def write(writer: StreamingJsonlWriter, payload: bytes) -> None:
        nonlocal staged
        original_write(writer, payload)
        staged += len(payload)
        if phase == "staging_written":
            writer.flush()
            os.fsync(writer._stream.fileno())
            die()

    def close(batcher: _BatchCommitter) -> None:
        original_close(batcher)
        if phase == "settled":
            die()

    def link(*args: Any, **kwargs: Any) -> None:
        original_link(*args, **kwargs)
        if phase == "publication":
            die()

    with (
        patch.object(_BatchCommitter, "scanned", scan),
        patch.object(_BatchCommitter, "add_retained", add),
        patch.object(StreamingJsonlWriter, "write_line", write),
        patch.object(_BatchCommitter, "close", close),
        patch("os.link", link),
    ):
        fetcher.run()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path)
    parser.add_argument("--phase", default="none")
    parser.add_argument("--parallel-publication", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/opus-review/selected-crashes")
    )
    args = parser.parse_args()
    if args.child:
        child(args.child, args.phase)
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = args.output.resolve()
    if root.drive != "G:":
        raise ValueError("G: only")
    root.mkdir(parents=True, exist_ok=False)
    rows = [{"id": i, "text": f"row {i} " + "x" * 1000} for i in range(40)]
    plain = b"".join((json.dumps(row) + "\n").encode() for row in rows)
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf, row_group_size=20)
    AuthoredHandler.files = {
        "rows.jsonl": plain,
        "rows.jsonl.gz": gzip.compress(plain, mtime=0),
        "rows.parquet": buf.getvalue(),
    }
    AuthoredHandler.cut_first_response_at = None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AuthoredHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        for name, projected in (
            ("rows.jsonl", False),
            ("rows.jsonl.gz", False),
            ("rows.parquet", False),
            ("rows.parquet", True),
        ):
            names = [name]
            if args.parallel_publication:
                duplicate = "second-" + name
                AuthoredHandler.files[duplicate] = AuthoredHandler.files[name]
                names.append(duplicate)
            plan = plan_for(
                server.server_port,
                names,
                mode="selected_records",
                row_ranges={source: (10, 30) for source in names},
                projected_fields=["id", "text"] if projected else None,
                limits=AcquisitionLimits(
                    max_transferred_bytes=16 * 1024**2,
                    max_decompressed_bytes=16 * 1024**2,
                    max_temp_disk_bytes=16 * 1024**2,
                    max_output_disk_bytes=16 * 1024**2,
                    max_requests=100,
                    max_retries=0,
                    max_workers=len(names),
                    max_scanned_records=1000,
                    max_decompression_ratio=1000,
                    overall_deadline_seconds=120,
                ),
            )
            baseline = root / f"{name}-{projected}-baseline"
            baseline.mkdir()
            (baseline / "plan.json").write_text(plan.model_dump_json())
            child(baseline, "none")
            expected = (baseline / "output/selected_records.jsonl").read_bytes()
            phases = (
                ("publication",)
                if args.parallel_publication
                else ("scan", "staging_reserved", "staging_written", "settled", "publication")
            )
            for phase in phases:
                case = root / f"{name}-{projected}-{phase}"
                case.mkdir()
                (case / "plan.json").write_text(plan.model_dump_json())
                command = [sys.executable, __file__, "--child", str(case)]
                with (case / "kill.log").open("w") as log:
                    killed = subprocess.run(
                        command + ["--phase", phase],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=60,
                    )
                assert killed.returncode == 73, (name, phase, killed.returncode)
                witness = json.loads((case / "death.json").read_text())
                journal = case / "scratch/journals/authored_leases.progress.json"
                state = json.loads(journal.read_text())
                account = state["accounting"]
                allowance = {
                    key: account["occupancy" if key == "temp" else "consumed"].get(key, 0)
                    + sum(account["reservations"].get(key, {}).values())
                    for key in ("records_scanned", "temp", "transfer", "decompressed")
                }
                assert allowance["records_scanned"] >= witness["scanned"]
                assert allowance["temp"] >= witness["staged"]
                with (case / "restart.log").open("w") as log:
                    restart = subprocess.run(
                        command, stdout=log, stderr=subprocess.STDOUT, timeout=60
                    )
                final = json.loads(journal.read_text())
                assert final["accounting"]["deadline_at"] == account["deadline_at"]
                for key in ("records_scanned", "transfer", "decompressed"):
                    assert final["accounting"]["consumed"].get(key, 0) >= account["consumed"].get(
                        key, 0
                    )
                output = case / "output/selected_records.jsonl"
                actual = output.read_bytes() if output.exists() else None
                if restart.returncode == 0:
                    assert actual == expected
                    assert len(actual.splitlines()) == 20 * len(names)
                    if phase == "publication":
                        assert final["accounting"]["consumed"] == account["consumed"]
                        assert final["accounting"]["occupancy"]["output"] == len(expected)
                        assert not final["accounting"]["reservations"].get("output")
                assert restart.returncode == 0, (name, phase, "automatic recovery failed")
                results.append(
                    {
                        "name": name,
                        "projected": projected,
                        "phase": phase,
                        "witness": witness,
                        "durable_allowance": allowance,
                        "death_exit": killed.returncode,
                        "restart_exit": restart.returncode,
                        "valid_output_exists": actual == expected,
                        "expected_sha256": hashlib.sha256(expected).hexdigest(),
                        "workers": len(names),
                        "final_temp_bytes": final["accounting"]["occupancy"].get("temp", 0),
                    }
                )
                (root / "report.json").write_text(json.dumps(results, indent=2))
                print(name, projected, phase, restart.returncode, flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
