"""Read-only status of an audit output, safe while the audit is running.

``python -m xlm.data.quality status --manifest M --output O [--progress-log L]
[--watch --interval-seconds S]`` never writes, renames, deletes, locks or creates
anything. It reads the manifest (bounded), the audit binding (bounded), ``stat``s the
committed unit files (it never opens them, so the running audit's atomic renames are
never blocked on Windows), checks whether the COMPLETE receipt exists, optionally
reads the last line of the operational progress log, and inspects the audit process
tree through psutil (command line, CPU times, RSS). Units are committed per file after
the second source hash, so committed progress lags live progress by the files in
flight; the progress log shows chunk-level progress.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.progress import PREFIX, duration
from xlm.data.quality.scan import (
    BINDING_FILE,
    MAX_MANIFEST_BYTES,
    RECEIPT_FILE,
    QualityError,
    load_manifest,
    read_bounded,
    unit_path,
)

RECENT_SECONDS = 300.0
LOG_TAIL_BYTES = 64 * 1024
MAX_WATCH_SECONDS = 7 * 86400.0
CPU_SAMPLE_SECONDS = 1.0


def _binding_state(output: Path, manifest_digest: str) -> str:
    path = output / BINDING_FILE
    if not path.is_file():
        return "absent"
    try:
        body = canonical.loads_bytes_strict(read_bounded(path, MAX_MANIFEST_BYTES, "binding"))
    except (ValueError, OSError):
        return "unreadable"
    if not isinstance(body, dict) or body.get("digest") != canonical.self_digest(body):
        return "invalid"
    if body.get("input_manifest", {}).get("digest") != manifest_digest:
        return "different-manifest"
    return "matches-manifest"


def last_progress_line(log: Path) -> str | None:
    """The last complete ``[quality-audit]`` line of a progress log (read-only)."""
    try:
        with log.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - LOG_TAIL_BYTES))
            tail = stream.read(LOG_TAIL_BYTES)
    except OSError:
        return None
    for raw in reversed(tail.split(b"\n")):
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith(PREFIX):
            return line
    return None


def _matches(cmdline: list[str], output: Path, cwd: str | None) -> bool:
    if "xlm.data.quality" not in cmdline or "audit" not in cmdline:
        return False
    for flag, value in zip(cmdline, cmdline[1:], strict=False):
        if flag == "--output":
            candidate = Path(value)
            if not candidate.is_absolute() and cwd is not None:
                candidate = Path(cwd) / candidate
            try:
                return candidate.resolve() == output.resolve()
            except OSError:
                return False
    return False


def find_audit_process(output: Path) -> dict[str, Any] | None:
    """The running audit process for ``output`` and its process tree (read-only)."""
    import psutil

    found: list[Any] = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = process.info["cmdline"] or []
            if "xlm.data.quality" not in cmdline:
                continue
            try:
                cwd: str | None = process.cwd()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cwd = None
            if _matches(cmdline, output, cwd):
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if not found:
        return None
    pids = {p.pid for p in found}
    # A launcher (e.g. ``uv run``) carries the same command line: report the innermost.
    leaves = []
    for process in found:
        try:
            if not any(c.pid in pids for c in process.children(recursive=True)):
                leaves.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not leaves:
        return None
    audit = leaves[0]

    def tree() -> tuple[float, dict[int, float], int]:
        parent_times = audit.cpu_times()
        rss = int(audit.memory_info().rss)
        children: dict[int, float] = {}
        for child in audit.children(recursive=True):
            try:
                times = child.cpu_times()
                children[child.pid] = float(times.user + times.system)
                rss += int(child.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return float(parent_times.user + parent_times.system), children, rss

    try:
        first_parent, first_children, _ = tree()
        started = time.monotonic()
        time.sleep(CPU_SAMPLE_SECONDS)
        parent_cpu, children, rss = tree()
        dt = time.monotonic() - started
        busy = {pid: (cpu - first_children.get(pid, 0.0)) / dt for pid, cpu in children.items()}
        return {
            "pid": audit.pid,
            "matching_processes": len(found),
            "elapsed_seconds": round(time.time() - audit.create_time(), 1),
            "child_processes": len(children),
            "active_children": sum(1 for v in busy.values() if v >= 0.5),
            "parent_cores": round((parent_cpu - first_parent) / dt, 2),
            "children_cores": round(sum(max(v, 0.0) for v in busy.values()), 2),
            "parent_cpu_seconds": round(parent_cpu, 1),
            "children_cpu_seconds": round(sum(children.values()), 1),
            "process_tree_rss_bytes": rss,
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _span_rate(commits: list[tuple[float, int]]) -> float | None:
    """Bytes per second between the first and last of time-ordered unit commits."""
    if len(commits) < 2 or commits[-1][0] <= commits[0][0]:
        return None
    return sum(nbytes for _, nbytes in commits[1:]) / (commits[-1][0] - commits[0][0])


def audit_status(
    manifest_path: Path,
    output: Path,
    *,
    data_root: Path | None = None,
    progress_log: Path | None = None,
    processes: bool = True,
) -> dict[str, Any]:
    """Committed/staging-derived state of one audit output (never modifies anything)."""
    manifest = load_manifest(manifest_path, data_root)
    totals = manifest.totals
    now = time.time()
    state: dict[str, Any] = {"output_exists": output.is_dir()}
    files = bytes_done = docs_done = 0
    commits: list[tuple[float, int]] = []
    if output.is_dir():
        for item in manifest.files:
            try:
                info = unit_path(output, item.ordinal).stat()
            except FileNotFoundError:
                continue
            files += 1
            bytes_done += item.file_bytes
            docs_done += item.documents
            commits.append((info.st_mtime, item.file_bytes))
    commits.sort()
    complete = (output / RECEIPT_FILE).is_file()
    span = (commits[-1][0] - commits[0][0]) if len(commits) > 1 else 0.0
    overall_rate = _span_rate(commits)
    recent_rate = _span_rate([c for c in commits if c[0] >= commits[-1][0] - RECENT_SECONDS])
    rate = recent_rate or overall_rate
    remaining = totals["file_bytes"] - bytes_done
    state.update(
        {
            "state": "COMPLETE" if complete else "INCOMPLETE",
            "receipt_present": complete,
            "binding": _binding_state(output, manifest.digest),
            "files_complete": files,
            "files_total": totals["files"],
            "documents_complete": docs_done,
            "documents_total": totals["documents"],
            "file_bytes_complete": bytes_done,
            "file_bytes_total": totals["file_bytes"],
            "percent_bytes": round(100.0 * bytes_done / totals["file_bytes"], 2)
            if totals["file_bytes"]
            else 100.0,
            "committed_rate_mb_per_s_recent": None
            if recent_rate is None
            else round(recent_rate / 1e6, 2),
            "committed_rate_mb_per_s_overall": None
            if overall_rate is None
            else round(overall_rate / 1e6, 2),
            "eta_seconds": None if complete or not rate else round(remaining / rate, 0),
            "seconds_since_last_commit": round(now - commits[-1][0], 1) if commits else None,
            "first_to_last_commit_seconds": round(span, 1),
            "note": "committed = files whose unit is written (after the second source hash)",
        }
    )
    if progress_log is not None:
        state["last_progress_line"] = last_progress_line(progress_log)
    if processes:
        state["process"] = find_audit_process(output)
    return state


def status_line(state: dict[str, Any]) -> str:
    gb = 1e9
    parts = [
        f"{PREFIX} status {state['state']} {state['percent_bytes']:.1f}%",
        f"{state['files_complete']}/{state['files_total']} files",
        f"{state['documents_complete'] / 1e6:.2f}M/{state['documents_total'] / 1e6:.2f}M docs",
        f"{state['file_bytes_complete'] / gb:.1f}/{state['file_bytes_total'] / gb:.1f} GB",
    ]
    rate = state["committed_rate_mb_per_s_recent"] or state["committed_rate_mb_per_s_overall"]
    parts.append(f"{'--' if rate is None else rate} MB/s committed")
    parts.append(f"ETA {duration(state['eta_seconds'])}")
    process = state.get("process")
    if process:
        parts += [
            f"pid {process['pid']}",
            f"elapsed {duration(process['elapsed_seconds'])}",
            f"children {process['active_children']}/{process['child_processes']} active",
            f"CPU {process['parent_cores']}+{process['children_cores']} cores",
            f"RSS {process['process_tree_rss_bytes'] / 2**30:.2f} GiB",
        ]
    elif "process" in state:
        parts.append("no running audit process found")
    return " | ".join(parts)


def watch(
    produce: Callable[[], dict[str, Any]],
    interval: float,
    max_seconds: float = MAX_WATCH_SECONDS,
) -> dict[str, Any]:
    """Print a status line every ``interval`` until COMPLETE, no audit process remains,
    or ``max_seconds`` (bounded); return the last state."""
    if not 0 < interval <= 3600 or not 0 < max_seconds <= MAX_WATCH_SECONDS:
        raise QualityError("--interval-seconds or watch bound outside its range")
    stop = time.monotonic() + max_seconds
    while True:
        state = produce()
        print(status_line(state), file=sys.stderr, flush=True)
        line = state.get("last_progress_line")
        if line:
            print(f"  last progress: {line}", file=sys.stderr, flush=True)
        if state["state"] == "COMPLETE" or time.monotonic() >= stop:
            return state
        if "process" in state and state["process"] is None and state["files_complete"]:
            return state
        time.sleep(interval)
