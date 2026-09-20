"""One bounded fresh-process launcher for existing queue and direct smoke paths."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

import psutil

from xlm.artifacts.manifest import ensure_plain_path
from xlm.artifacts.store import compute_file_sha256
from xlm.experiments.execution import read_json, validate_envelope, write_json


def _observed_file_size(path: Path) -> int:
    """Sample live owned files while atomic publication retires private staging."""
    ensure_plain_path(path)
    try:
        return path.stat().st_size if path.is_file() else 0
    except FileNotFoundError:
        # A sampled file can disappear after is_file during atomic publication.
        # Other errors still fail closed; entry and aggregate caps remain active.
        return 0


def launch_worker(
    request: dict[str, Any],
    work: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Fail closed, spool bounded output, retain failures, never fall back to live code."""
    envelope = request["envelope"]
    snapshot_dir = Path(request["snapshot_dir"])
    validate_envelope(envelope, snapshot_dir, check_environment=False, resolve_components=False)
    python = Path(request["runtime_locations"]["python"])
    # uv may use a symlinked launcher on POSIX; verify its resolved executable.
    ensure_plain_path(python.resolve(strict=True))
    if compute_file_sha256(python) != envelope["environment"]["interpreter_sha256"]:
        raise ValueError("selected interpreter bytes differ from frozen environment")
    ensure_plain_path(work)
    work.mkdir(parents=True, exist_ok=False)
    request = {**request, "work": str(work.resolve())}
    descriptor = work / "request.json"
    write_json(descriptor, request)
    # Keep only OS essentials. Inherited Python/site/loader configuration is absent.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}
    }
    env.update(
        {
            "UV_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TMP": str(work),
            "TEMP": str(work),
            "TMPDIR": str(work),
            "TORCH_EXTENSIONS_DIR": str(work / "torch-cache"),
            "TORCHINDUCTOR_CACHE_DIR": str(work / "inductor-cache"),
            "XLM_HOME": request["artifact_home"],
            "HF_HOME": str(work / "hf-cache"),
            "PATH": os.pathsep.join(
                (
                    str(python.parent),
                    str(Path(os.environ["SYSTEMROOT"]) / "System32")
                    if os.name == "nt"
                    else "/usr/bin",
                )
            ),
        }
    )
    argv = [
        str(python),
        "-I",
        "-S",
        "-B",
        str(snapshot_dir / "code/src/xlm/experiments/bootstrap.py"),
        str(descriptor),
        compute_file_sha256(descriptor),
    ]
    limit = (
        float(
            envelope["config"].get("training", {}).get("budget", {}).get("max_train_seconds", 600)
        )
        + 120
    )
    started = time.monotonic()
    exceeded = threading.Event()
    guard = threading.Lock()
    output_bytes = 0
    peak_rss = 0
    peak_work_bytes = 0
    last_housekeeping = 0.0
    disk_cap = int(request.get("max_owned_disk_bytes", 2 * 1024**3))
    observed_pids: set[int] = set()

    def spool(source: BinaryIO, target: BinaryIO) -> None:
        nonlocal output_bytes
        try:
            while chunk := source.read(65536):
                with guard:
                    allowed = min(len(chunk), 16 * 1024**2 - output_bytes)
                    target.write(chunk[:allowed])
                    target.flush()
                    output_bytes += allowed
                    if allowed < len(chunk):
                        exceeded.set()
                        return
        finally:
            source.close()

    def kill_owned_tree(pid: int) -> None:
        try:
            for child in psutil.Process(pid).children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        process.kill()

    with (work / "stdout.log").open("xb") as stdout, (work / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(
            argv, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        write_json(
            work / "process_identity.json",
            {"pid": process.pid, "create_time": psutil.Process(process.pid).create_time()},
        )
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=spool, args=(source, target), daemon=True)
            for source, target in ((process.stdout, stdout), (process.stderr, stderr))
        ]
        for reader in readers:
            reader.start()
        reason: str | None = None
        try:
            while process.poll() is None:
                if should_cancel and should_cancel():
                    (work / "cancel.requested").touch()
                if time.monotonic() - last_housekeeping >= 1:
                    if heartbeat:
                        heartbeat()
                    count = used = 0
                    owned_root = Path(request.get("job", {}).get("work_dir", work))
                    for item in owned_root.rglob("*"):
                        count += 1
                        if count > 10000:
                            reason = "frozen worker scratch entry limit exceeded"
                            break
                        used += _observed_file_size(item)
                    peak_work_bytes = max(peak_work_bytes, used)
                    if used > disk_cap:
                        reason = "frozen worker scratch disk limit exceeded"
                    last_housekeeping = time.monotonic()
                try:
                    processes = [
                        psutil.Process(process.pid),
                        *psutil.Process(process.pid).children(recursive=True),
                    ]
                    observed_pids.update(p.pid for p in processes)
                    peak_rss = max(peak_rss, sum(p.memory_info().rss for p in processes))
                except psutil.Error:
                    pass
                if time.monotonic() - started > limit:
                    reason = "frozen worker wall-time limit exceeded"
                if exceeded.is_set():
                    reason = "frozen worker output byte limit exceeded"
                if reason:
                    kill_owned_tree(process.pid)
                    break
                time.sleep(0.1)
            exit_code = process.wait(timeout=10)
        finally:
            if process.poll() is None:
                kill_owned_tree(process.pid)
                process.wait(timeout=10)
            for reader in readers:
                reader.join(timeout=5)
    if exceeded.is_set():
        reason = "frozen worker output byte limit exceeded"
    write_json(
        work / "process.json",
        {
            "argv": argv,
            "pid": process.pid,
            "exit_code": exit_code,
            "elapsed_seconds": time.monotonic() - started,
            "reason": reason,
            "sampled_peak_tree_rss_bytes": peak_rss,
            "observed_pids": sorted(observed_pids),
            "output_bytes": output_bytes,
            "sampled_peak_work_bytes": peak_work_bytes,
            "max_owned_disk_bytes": disk_cap,
        },
    )
    if exit_code != 0 or reason:
        error = (work / "stderr.log").read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"frozen worker failed ({exit_code}): {reason or error}")
    result = read_json(work / "result.json", limit=2 * 1024**2)
    if result.get("execution_hash") != envelope["execution_hash"]:
        raise ValueError("worker result belongs to a different execution envelope")
    if result.get("worker_pid") not in observed_pids:
        raise ValueError("worker result PID was not observed in the launched process tree")
    return result
