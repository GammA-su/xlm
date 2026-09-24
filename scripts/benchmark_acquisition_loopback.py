"""Bounded loopback acquisition benchmark; authored synthetic bytes, never live evidence.

A separate server process serves deterministic in-memory files over plain HTTP on
127.0.0.1 with strong ETags, exact Range support, optional HF-style 302 redirects
(canonical ``/repo/`` path -> signed-looking ``/cdn/`` target), injected per-request
and per-connection latency, and optional per-stream pacing. The client drives the
real ``BoundedFetcher`` (whole-file or selected Parquet records) or a raw stdlib
``http.client`` ceiling. Simulated latency/bandwidth are local models: results are
never claims about Hugging Face or any CDN. No network beyond loopback is used.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import http.server
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

MIB = 1024 * 1024
MAX_SERVED_BYTES = 6 * 1024 * MIB


# --------------------------------------------------------------------------- server


class _Stats:
    lock = threading.Lock()
    requests = 0
    range_requests = 0
    redirects = 0
    bytes_sent = 0
    connections = 0
    active = 0
    max_active = 0


class LoopbackHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    files: dict[str, bytes] = {}
    etags: dict[str, str] = {}
    rtt = 0.0
    rate = 0.0  # bytes/second per response stream; 0 = unpaced
    redirect = False

    def setup(self) -> None:
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with _Stats.lock:
            _Stats.connections += 1
            _Stats.active += 1
            _Stats.max_active = max(_Stats.max_active, _Stats.active)
        if self.rtt:
            time.sleep(self.rtt)  # models the connection handshake round trip

    def finish(self) -> None:
        with _Stats.lock:
            _Stats.active -= 1
        super().finish()

    def log_message(self, *args: Any) -> None:
        return

    def _stats(self) -> None:
        with _Stats.lock:
            body = json.dumps(
                {
                    key: getattr(_Stats, key)
                    for key in (
                        "requests",
                        "range_requests",
                        "redirects",
                        "bytes_sent",
                        "connections",
                        "max_active",
                    )
                }
            ).encode()
            if self.path == "/__reset":
                for key in (
                    "requests",
                    "range_requests",
                    "redirects",
                    "bytes_sent",
                    "connections",
                ):
                    setattr(_Stats, key, 0)
                _Stats.max_active = _Stats.active
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path in ("/__stats", "/__reset"):
            self._stats()
            return
        path = self.path.split("?", 1)[0]
        if self.rtt:
            time.sleep(self.rtt)  # request/response round trip before first byte
        with _Stats.lock:
            _Stats.requests += 1
        if path.startswith("/repo/") and self.redirect:
            name = path[len("/repo/") :]
            with _Stats.lock:
                _Stats.redirects += 1
            self.send_response(302)
            self.send_header("Location", f"/cdn/{name}?X-Amz-Signature=authored")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        for prefix in ("/repo/", "/cdn/"):
            if path.startswith(prefix):
                name = path[len(prefix) :]
                break
        else:
            name = ""
        payload = self.files.get(name)
        if payload is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        view = memoryview(payload)
        header = self.headers.get("Range")
        if header:
            first, _, last = header.removeprefix("bytes=").partition("-")
            start = int(first)
            end = int(last) if last else len(payload) - 1
            end = min(end, len(payload) - 1)
            with _Stats.lock:
                _Stats.range_requests += 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            view = view[start : end + 1]
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(view)))
        self.send_header("ETag", self.etags[name])
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._send_paced(view)

    def _send_paced(self, view: memoryview) -> None:
        sent = 0
        if not self.rate:
            step = 4 * MIB
            while sent < len(view):
                piece = view[sent : sent + step]
                self.wfile.write(piece)
                sent += len(piece)
            with _Stats.lock:
                _Stats.bytes_sent += sent
            return
        step = max(16384, int(self.rate * 0.004))
        began = time.perf_counter()
        while sent < len(view):
            piece = view[sent : sent + step]
            self.wfile.write(piece)
            sent += len(piece)
            due = began + sent / self.rate
            delay = due - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        with _Stats.lock:
            _Stats.bytes_sent += sent


def serve(root: Path, port_file: Path, rtt_ms: float, stream_mbps: float, redirect: bool) -> None:
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            total += path.stat().st_size
            if total > MAX_SERVED_BYTES:
                raise SystemExit("served corpus exceeds bounded in-memory cap")
            name = path.relative_to(root).as_posix()
            files[name] = path.read_bytes()
    LoopbackHandler.files = files
    LoopbackHandler.etags = {
        name: '"' + hashlib.sha256(data).hexdigest()[:32] + '"' for name, data in files.items()
    }
    LoopbackHandler.rtt = rtt_ms / 1000.0
    LoopbackHandler.rate = stream_mbps * 1e6
    LoopbackHandler.redirect = redirect
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LoopbackHandler)
    server.daemon_threads = True
    server.request_queue_size = 512
    port_file.write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever(poll_interval=0.05)


class ServerProcess:
    def __init__(
        self, root: Path, work: Path, *, rtt_ms: float, stream_mbps: float, redirect: bool
    ) -> None:
        work.mkdir(parents=True, exist_ok=True)
        port_file = work / f"port-{os.getpid()}-{time.monotonic_ns()}.txt"
        self.proc = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "serve",
                "--root",
                str(root),
                "--port-file",
                str(port_file),
                "--rtt-ms",
                str(rtt_ms),
                "--stream-mbps",
                str(stream_mbps),
                *(["--redirect"] if redirect else []),
            ]
        )
        deadline = time.monotonic() + 120
        while not port_file.exists() or not port_file.read_text(encoding="utf-8"):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("loopback server did not start")
            time.sleep(0.05)
        self.port = int(port_file.read_text(encoding="utf-8"))
        port_file.unlink()

    def stats(self, reset: bool = False) -> dict[str, int]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/__reset" if reset else "/__stats")
        value: dict[str, int] = json.loads(conn.getresponse().read())
        conn.close()
        return value

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=30)


# --------------------------------------------------------------------------- corpus


def build_blob_corpus(root: Path, files: int, file_mib: float, seed: int = 20260924) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    size = int(file_mib * MIB)
    block = rng.randbytes(min(size, 8 * MIB))
    for index in range(files):
        path = root / f"blob-{index:04d}.bin"
        if path.exists() and path.stat().st_size == size:
            continue
        salt = hashlib.sha256(f"{seed}:{index}".encode()).digest()
        with path.open("wb") as stream:
            written = 0
            while written < size:
                piece = (salt + block)[: size - written]
                stream.write(piece)
                written += len(piece)


def build_parquet_corpus(
    root: Path, files: int, rows: int, row_group_rows: int, text_bytes: int, seed: int = 7
) -> list[str]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    words = [
        "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9)))
        for _ in range(4096)
    ]
    names = []
    for index in range(files):
        name = f"shard-{index:04d}.parquet"
        names.append(name)
        path = root / name
        if path.exists():
            continue
        texts, ids, langs = [], [], []
        for row in range(rows):
            parts: list[str] = []
            length = 0
            target = text_bytes // 2 + rng.randint(0, text_bytes)
            while length < target:
                word = words[rng.randrange(len(words))]
                parts.append(word)
                length += len(word) + 1
            texts.append(" ".join(parts))
            ids.append(f"doc-{index}-{row}")
            langs.append("en")
        table = pa.table({"id": ids, "text": texts, "language": langs})
        pq.write_table(table, path, row_group_size=row_group_rows, compression="zstd")
    return names


# --------------------------------------------------------------------------- clients


class ResourceSampler:
    def __init__(self, interval: float = 0.05) -> None:
        self.process = psutil.Process()
        self.peak_rss = self.process.memory_info().rss
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.interval = interval

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
            except psutil.Error:
                return

    def __enter__(self) -> ResourceSampler:
        self.cpu0 = self.process.cpu_times()
        self.io0 = psutil.disk_io_counters(perdisk=False)
        self.t0 = time.perf_counter()
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.wall = time.perf_counter() - self.t0
        self._stop.set()
        self._thread.join()
        cpu1 = self.process.cpu_times()
        self.cpu_user = cpu1.user - self.cpu0.user
        self.cpu_system = cpu1.system - self.cpu0.system
        io1 = psutil.disk_io_counters(perdisk=False)
        self.disk_write_bytes = io1.write_bytes - self.io0.write_bytes if io1 and self.io0 else 0


def raw_client(port: int, names: list[str], workers: int, chunk: int, redirect: bool) -> int:
    """Stdlib ceiling: keep-alive per worker, ``readinto`` a reused buffer, no accounting."""
    lock = threading.Lock()
    queue = list(names)
    total = [0]

    def worker() -> None:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        buffer = bytearray(chunk)
        view = memoryview(buffer)
        while True:
            with lock:
                if not queue:
                    break
                name = queue.pop(0)
            path = f"/repo/{name}"
            conn.request("GET", path)
            resp = conn.getresponse()
            if resp.status == 302:
                resp.read()
                conn.request("GET", resp.getheader("Location") or "")
                resp = conn.getresponse()
            got = 0
            while True:
                n = resp.readinto(view)
                if not n:
                    break
                got += n
            with lock:
                total[0] += got
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return total[0]


def make_plan(
    port: int,
    names: list[str],
    workers: int,
    total_bytes: int,
    *,
    mode: str = "whole_file",
    row_ranges: dict[str, tuple[int, int]] | None = None,
    projected: list[str] | None = None,
    coalesce: int | None = None,
    max_records: int = 25_000,
) -> Any:
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        PlanAuthorization,
    )

    headroom = total_bytes * 2 + 64 * MIB
    plan = AcquisitionPlan(
        plan_id="loopback_bench",
        source_id="authored_loopback",
        provider="https",
        repository=f"http://127.0.0.1:{port}/repo",
        revision="authored-loopback-v1",
        mode=AcquisitionMode(mode),
        selected_files=names,
        row_ranges=row_ranges,
        projected_fields=projected,
        range_coalesce_bytes=coalesce,
        output_artifact_id="loopback_bench",
        is_pilot=False,
        limits=AcquisitionLimits(
            max_transferred_bytes=headroom,
            max_decompressed_bytes=max(headroom * 16, 512 * MIB),
            max_temp_disk_bytes=headroom,
            max_output_disk_bytes=headroom,
            max_records=max_records,
            max_scanned_records=max(100_000, max_records * 4),
            max_requests=100_000,
            max_retries=0,
            max_workers=min(16, workers),
            per_request_timeout_seconds=120,
            overall_deadline_seconds=3600,
        ),
    )
    return plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="authored loopback benchmark",
                authorized_at="fixture",
                scope="production",
            )
        }
    )


def run_fetch(plan: Any, work: Path) -> dict[str, Any]:
    from xlm.data.acquisition.fetcher import BoundedFetcher

    if work.exists():
        shutil.rmtree(work)
    # Authored loopback source: this harness is the admitting caller for plans above
    # pilot thresholds (C13); the authorization hash still binds the full limits.
    fetcher = BoundedFetcher(plan, work / "scratch", work / "output", catalog_source_approved=True)
    fetch_started = time.perf_counter()
    if os.environ.get("XLM_BENCH_PROFILE"):
        import cProfile
        import pstats

        profiler = cProfile.Profile()
        state = profiler.runcall(fetcher.run)
        pstats.Stats(profiler).sort_stats("tottime").print_stats(18)
    else:
        state = fetcher.run()
    fetch_wall = time.perf_counter() - fetch_started
    sidecar = json.loads(
        (work / "scratch" / "performance" / f"{plan.plan_id}.perf.json").read_text("utf-8")
    )
    telemetry = sidecar.get("telemetry") or {}
    output_digest = hashlib.sha256()
    for path in sorted((work / "output").rglob("*")):
        if path.is_file():
            output_digest.update(path.name.encode())
            output_digest.update(hashlib.sha256(path.read_bytes()).digest())
    return {
        "status": state.status,
        "fetch_wall_s": round(fetch_wall, 3),
        "fetch_mb_s": round(state.transferred_bytes / fetch_wall / 1e6, 2),
        "transferred_bytes": state.transferred_bytes,
        "decompressed_bytes": state.decompressed_bytes,
        "requests_made": state.requests_made,
        "records": state.records_acquired,
        "output_digest": output_digest.hexdigest(),
        "journal": sidecar.get("journal"),
        "average_concurrency": round(float(sidecar.get("average_concurrency") or 0), 2),
        "max_active_workers": telemetry.get("max_active_workers"),
        "connection_reuses": telemetry.get("connection_reuses"),
        "connection_creations": telemetry.get("connection_creations"),
        "redirect_cache_hits": telemetry.get("redirect_target_cache_hits"),
        "stage_seconds": {
            key: round(float(telemetry.get(f"{key}_seconds") or 0), 3)
            for key in ("open", "body", "metadata", "decode", "serialize", "write", "accounting")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    srv = sub.add_parser("serve")
    srv.add_argument("--root", type=Path, required=True)
    srv.add_argument("--port-file", type=Path, required=True)
    srv.add_argument("--rtt-ms", type=float, default=0.0)
    srv.add_argument("--stream-mbps", type=float, default=0.0)
    srv.add_argument("--redirect", action="store_true")

    bench = sub.add_parser("bench")
    bench.add_argument("--work", type=Path, required=True, help="scratch/output location")
    bench.add_argument("--corpus", type=Path, required=True)
    bench.add_argument("--client", choices=("raw", "whole", "select"), default="whole")
    bench.add_argument("--files", type=int, default=16)
    bench.add_argument("--file-mib", type=float, default=16.0)
    bench.add_argument("--workers", type=str, default="1,2,4,8,16")
    bench.add_argument("--chunk", type=int, default=65536)
    bench.add_argument("--rtt-ms", type=float, default=0.0)
    bench.add_argument("--stream-mbps", type=float, default=0.0)
    bench.add_argument("--redirect", action="store_true")
    bench.add_argument("--rows", type=int, default=20_000)
    bench.add_argument("--row-group-rows", type=int, default=1000)
    bench.add_argument("--text-bytes", type=int, default=2000)
    bench.add_argument("--select-rows", type=int, default=0, help="0 = every row")
    bench.add_argument("--projected", type=str, default="id,text")
    bench.add_argument("--coalesce", type=int, default=1 * MIB)
    bench.add_argument("--max-seconds", type=float, default=900)
    bench.add_argument("--json-out", type=Path)
    bench.add_argument(
        "--diagnostic-no-fsync",
        action="store_true",
        help="DIAGNOSTIC ONLY: make os.fsync a no-op in this benchmark process to expose the "
        "software ceiling independent of storage flush cost. Results are non-durable and never "
        "evidence of crash safety.",
    )
    args = parser.parse_args()
    if getattr(args, "diagnostic_no_fsync", False):
        os.fsync = lambda fd: None  # noqa: E731 - diagnostic-only, process-local

    if args.command == "serve":
        serve(args.root, args.port_file, args.rtt_ms, args.stream_mbps, args.redirect)
        return

    if args.client == "select":
        names = build_parquet_corpus(
            args.corpus, args.files, args.rows, args.row_group_rows, args.text_bytes
        )
    else:
        build_blob_corpus(args.corpus, args.files, args.file_mib)
        names = sorted(p.name for p in args.corpus.glob("blob-*.bin"))[: args.files]
    corpus_bytes = sum((args.corpus / name).stat().st_size for name in names)
    server = ServerProcess(
        args.corpus,
        args.work,
        rtt_ms=args.rtt_ms,
        stream_mbps=args.stream_mbps,
        redirect=args.redirect,
    )
    results = []
    started = time.monotonic()
    try:
        for workers in [int(value) for value in args.workers.split(",")]:
            if time.monotonic() - started > args.max_seconds:
                results.append({"workers": workers, "status": "NOT RUN (time cap)"})
                continue
            server.stats(reset=True)
            row: dict[str, Any] = {"workers": workers, "client": args.client}
            with ResourceSampler() as sample:
                if args.client == "raw":
                    row["bytes"] = raw_client(
                        server.port, names, workers, args.chunk, args.redirect
                    )
                else:
                    ranges = None
                    projected = None
                    coalesce = None
                    mode = "whole_file"
                    if args.client == "select":
                        mode = "selected_records"
                        stop = args.select_rows or args.rows
                        ranges = {name: (0, stop) for name in names}
                        projected = [f for f in args.projected.split(",") if f]
                        coalesce = args.coalesce
                    plan = make_plan(
                        server.port,
                        names,
                        workers,
                        corpus_bytes,
                        mode=mode,
                        row_ranges=ranges,
                        projected=projected,
                        coalesce=coalesce,
                        max_records=max(25_000, len(names) * (args.select_rows or args.rows)),
                    )
                    row.update(run_fetch(plan, args.work / f"run-w{workers}"))
                    row["bytes"] = row["transferred_bytes"]
            server_stats = server.stats()
            row.update(
                {
                    "wall_s": round(sample.wall, 3),
                    "cpu_user_s": round(sample.cpu_user, 3),
                    "cpu_system_s": round(sample.cpu_system, 3),
                    "cpu_util_cores": round((sample.cpu_user + sample.cpu_system) / sample.wall, 2),
                    "peak_rss_mib": round(sample.peak_rss / MIB, 1),
                    "disk_write_mib_all_disks": round(sample.disk_write_bytes / MIB, 1),
                    "aggregate_mb_s": round(row["bytes"] / sample.wall / 1e6, 2),
                    "per_stream_mb_s": round(row["bytes"] / sample.wall / 1e6 / workers, 2),
                    "server": server_stats,
                }
            )
            if args.client != "raw":
                shutil.rmtree(args.work / f"run-w{workers}", ignore_errors=True)
            results.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)
    finally:
        server.close()
    document = {
        "benchmark": "loopback-acquisition",
        "evidence_class": "authored loopback simulation; not live source evidence",
        "python": sys.version,
        "cpu_logical": psutil.cpu_count(),
        "corpus_bytes": corpus_bytes,
        "files": len(names),
        "rtt_ms": args.rtt_ms,
        "stream_mbps": args.stream_mbps,
        "redirect": args.redirect,
        "chunk": args.chunk,
        "work": str(args.work),
        "diagnostic_no_fsync": bool(args.diagnostic_no_fsync),
        "results": results,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")


if __name__ == "__main__":
    main()
