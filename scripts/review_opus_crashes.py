"""Deterministic process-death matrix for actual whole-file lease acquisition.

Authored loopback only, private G: roots, 44 crash/restart pairs, no retries.
This is correctness instrumentation, never throughput evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from test_acquisition_leases import AuthoredHandler, blob, plan_for
from xlm.data.acquisition import fetcher as implementation
from xlm.data.acquisition.fetcher import BoundedFetcher
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan

PHASES = (
    "before_reservation",
    "after_reservation",
    "during_read",
    "after_read",
    "after_write",
    "after_settlement",
    "before_checkpoint",
    "after_checkpoint",
    "extension",
    "final_settlement",
    "publication",
)


def child(root: Path, phase: str, window: int, mature: bool) -> None:
    plan = AcquisitionPlan.model_validate_json((root / "plan.json").read_text())
    implementation.WHOLE_FILE_WINDOW_BYTES = window
    implementation.WHOLE_FILE_READ_BYTES = min(window, 8 * 1024**2)
    fetcher = BoundedFetcher(plan, root / "scratch", root / "output")
    received = 0
    reservations = 0
    finalizing = False
    active_window = 0

    def die() -> None:
        with (root / "death.json").open("w") as stream:
            json.dump(
                {
                    "phase": phase,
                    "received": received,
                    "window": window,
                    "active_window": active_window,
                    "mature": mature,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(73)

    original_exchange = fetcher.capacity_mgr.exchange

    def exchange(**kwargs: Any) -> Any:
        nonlocal reservations, finalizing, active_window
        reserve = kwargs.get("reserve", ())
        progress = kwargs.get("progress")
        if reserve:
            reservations += 1
            if phase == "before_reservation" and (not mature or reserve[0][1] >= window):
                die()
        old_window = active_window
        if phase == "before_checkpoint" and progress and (not mature or old_window >= window):
            die()
        finalizing = bool(kwargs.get("settle") and not reserve)
        result = original_exchange(**kwargs)
        finalizing = False
        if reserve:
            active_window = result[0][0][1]
        ready = not mature or active_window >= window
        if phase == "after_reservation" and reserve and ready:
            die()
        if phase == "extension" and reserve and reservations >= 2 and ready:
            die()
        if (
            phase == "after_settlement"
            and kwargs.get("settle")
            and (not mature or old_window >= window)
        ):
            die()
        if phase == "after_checkpoint" and progress and (not mature or old_window >= window):
            die()
        return result

    original_open = fetcher._open

    class Response:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def __enter__(self) -> Response:
            self.inner.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self.inner.__exit__(*args)

        def read(self, amount: int) -> bytes:
            nonlocal received
            ready = not mature or active_window >= window
            value = self.inner.read(
                max(1, amount // 2) if phase == "during_read" and ready else amount
            )
            received += len(value)
            if phase == "during_read" and ready:
                die()
            return bytes(value)

    original_read = fetcher.budget.read_leased

    def read(*args: Any, **kwargs: Any) -> bytes:
        value = original_read(*args, **kwargs)
        if phase == "after_read" and (not mature or active_window >= window):
            die()
        return value

    original_stream = fetcher._stream_body

    class Output:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def write(self, value: bytes) -> int:
            result = self.inner.write(value)
            if phase == "after_write" and (not mature or active_window >= window):
                self.inner.flush()
                die()
            return cast(int, result)

    def body(name: str, response: Any, output: Any, *args: Any) -> int:
        return original_stream(name, response, Output(output), *args)

    original_link = os.link
    original_replace = os.replace

    def replace(source: Any, destination: Any) -> None:
        if phase == "final_settlement" and finalizing and Path(destination) == fetcher.journal_path:
            die()  # fsynced journal replacement exists, but is not yet authoritative
        original_replace(source, destination)

    def link(*args: Any, **kwargs: Any) -> None:
        original_link(*args, **kwargs)
        if phase == "publication":
            die()

    with (
        patch.object(fetcher.capacity_mgr, "exchange", exchange),
        patch.object(fetcher, "_open", lambda *a, **k: Response(original_open(*a, **k))),
        patch.object(fetcher.budget, "read_leased", read),
        patch.object(fetcher, "_stream_body", body),
        patch("os.link", link),
        patch("os.replace", replace),
    ):
        state = fetcher.run()
    (root / "success.json").write_text(state.model_dump_json(), encoding="utf8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path)
    parser.add_argument("--phase", default="none")
    parser.add_argument("--window", type=int, default=65536)
    parser.add_argument("--output", type=Path, default=Path("artifacts/opus-review/crashes"))
    parser.add_argument("--mature", action="store_true")
    args = parser.parse_args()
    if args.child:
        child(args.child, args.phase, args.window, args.mature)
        return
    root = args.output.resolve()
    if root.drive != "G:":
        raise ValueError("G: only")
    root.mkdir(parents=True, exist_ok=False)
    results = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AuthoredHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for window in (65536, 1024**2, 8 * 1024**2, 16 * 1024**2):
            payload = blob(2 * window + 17, 719)
            AuthoredHandler.files = {"payload.bin": payload}
            AuthoredHandler.cut_first_response_at = None
            digest = hashlib.sha256(payload).hexdigest()
            for phase in PHASES:
                case = root / f"{window}-{phase}"
                case.mkdir()
                plan = plan_for(
                    server.server_port,
                    ["payload.bin"],
                    expected_file_digests={"payload.bin": digest},
                    limits=AcquisitionLimits(
                        max_transferred_bytes=128 * 1024**2,
                        max_decompressed_bytes=128 * 1024**2,
                        max_temp_disk_bytes=128 * 1024**2,
                        max_output_disk_bytes=128 * 1024**2,
                        max_requests=20,
                        max_retries=0,
                        max_workers=1,
                        overall_deadline_seconds=120,
                    ),
                )
                (case / "plan.json").write_text(plan.model_dump_json(), encoding="utf8")
                command = [
                    sys.executable,
                    __file__,
                    "--child",
                    str(case),
                    "--window",
                    str(window),
                    *(["--mature"] if args.mature else []),
                ]
                with (case / "kill.log").open("w") as log:
                    killed = subprocess.run(
                        command + ["--phase", phase],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=60,
                    )
                assert killed.returncode == 73, (phase, killed.returncode)
                death = json.loads((case / "death.json").read_text())
                journal = case / "scratch/journals/authored_leases.progress.json"
                state = json.loads(journal.read_text())
                account = state["accounting"]
                charged = account["consumed"].get("transfer", 0)
                reserved = sum(account["reservations"].get("transfer", {}).values())
                assert charged + reserved >= death["received"]
                fp = state["file_progress"].get("payload.bin", {})
                prefix = fp.get("verified_prefix_bytes", 0)
                if prefix:
                    assert fp["prefix_sha256"] == hashlib.sha256(payload[:prefix]).hexdigest()
                with (case / "restart.log").open("w") as log:
                    resumed = subprocess.run(
                        command, stdout=log, stderr=subprocess.STDOUT, timeout=60
                    )
                output = case / "output/payload.bin"
                if resumed.returncode == 0:
                    assert output.read_bytes() == payload
                    final = json.loads(journal.read_text())
                    assert final["accounting"]["deadline_at"] == account["deadline_at"]
                    assert final["requests_made"] >= state["requests_made"]
                    assert final["transferred_bytes"] >= len(payload)
                    if phase == "publication":
                        assert final["accounting"]["consumed"] == account["consumed"]
                        assert final["accounting"]["occupancy"]["output"] == len(payload)
                        assert not final["accounting"]["reservations"].get("output")
                assert resumed.returncode == 0, (window, phase, "automatic recovery failed")
                row = {
                    "window": window,
                    "phase": phase,
                    "death_exit": killed.returncode,
                    "received": death["received"],
                    "consumed": charged,
                    "reserved": reserved,
                    "verified_prefix": prefix,
                    "active_window": death["active_window"],
                    "mature": args.mature,
                    "restart_exit": resumed.returncode,
                    "valid_output_exists": output.exists() and output.read_bytes() == payload,
                }
                results.append(row)
                (root / "report.json").write_text(json.dumps(results, indent=2), encoding="utf8")
                print(window, phase, "restart", resumed.returncode, flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
