# Requires: offline only. Authored data, local files, no network.
"""Local resource benchmark for scan-bounded Parquet window acquisition.

Builds an AUTHORED shard shaped like the observed SYNTH layout (one row
group of 155,736 rows, 14 top-level columns, ~800 MB logical size) from
seeded random letters, then measures one window acquisition through the
real ``BoundedFetcher`` in a fresh child process. Bytes are served from the
local file by an in-process range opener; nothing touches the network.

Profiles (per-column byte split is an authored assumption, not SYNTH data):
- ``typical``: ~48% of logical bytes in projected columns (reasoning text
  unprojected, as in the certified ``synth_en`` projection);
- ``worst``: ~97% of logical bytes in projected columns.

Usage::

    uv run --offline --locked --extra cpu --extra eval python \\
        scripts/bench_parquet_window.py run --workdir <scratch> --profile typical

Prints one JSON document per measured window; numbers are local and
authored, never production-fetch throughput.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROWS = 155_736
SEED = 20260918
REVISION = "authored-bench"
#: Mean bytes per row by column; projected columns follow ``synth_en``.
PROFILES: dict[str, dict[str, int]] = {
    "typical": {
        "query": 200,
        "query_seed_text": 1300,
        "synthetic_answer": 800,
        "query_seed_url": 60,
        "synthetic_reasoning": 2600,
        "authored_extra_a": 100,
    },
    "worst": {
        "query": 200,
        "query_seed_text": 3900,
        "synthetic_answer": 800,
        "query_seed_url": 60,
        "synthetic_reasoning": 60,
        "authored_extra_a": 40,
    },
}
ALPHABET = np.frombuffer(b"abcdefghijklmnopqrstuvwxyz ", dtype=np.uint8)


def _text(rng: np.random.Generator, rows: int, mean: int) -> pa.Array:
    lengths = rng.integers(mean // 2, mean + mean // 2 + 1, size=rows, dtype=np.int64)
    offsets = np.zeros(rows + 1, dtype=np.int64)
    np.cumsum(lengths, out=offsets[1:])
    data = ALPHABET[rng.integers(0, len(ALPHABET), size=int(offsets[-1]), dtype=np.uint8)]
    return pa.LargeStringArray.from_buffers(
        rows, pa.py_buffer(offsets.tobytes()), pa.py_buffer(data.tobytes())
    ).cast(pa.string())


def build(path: Path, profile: str) -> None:
    rng = np.random.default_rng(SEED)
    sizes = PROFILES[profile]
    languages = np.array(["en", "fr", "de", "es", "it", "pl", "nl"])
    columns: dict[str, Any] = {
        "synth_id": pa.array([f"synth_{i:09d}" for i in range(ROWS)]),
        "language": pa.array(languages[rng.integers(0, len(languages), size=ROWS)]),
        "query": _text(rng, ROWS, sizes["query"]),
        "query_seed_text": _text(rng, ROWS, sizes["query_seed_text"]),
        "synthetic_answer": _text(rng, ROWS, sizes["synthetic_answer"]),
        "seed_license": pa.array(["CC-By-SA (4.0)"] * ROWS),
        "exercise": pa.array(["memorization"] * ROWS),
        "model": pa.array(["authored-model"] * ROWS),
        "words": pa.array(rng.integers(20, 2000, size=ROWS, dtype=np.int64)),
        "query_seed_url": _text(rng, ROWS, sizes["query_seed_url"]),
        "additional_seed_url": pa.nulls(ROWS, pa.string()),
        "synthetic_reasoning": _text(rng, ROWS, sizes["synthetic_reasoning"]),
        "authored_extra_a": _text(rng, ROWS, sizes["authored_extra_a"]),
        "authored_extra_b": pa.array(rng.integers(0, 2**40, size=ROWS, dtype=np.int64)),
    }
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(pa.table(columns), temporary, row_group_size=ROWS, compression="zstd")
    temporary.replace(path)


class FileRangeOpener:
    """Serve HTTP-like Range responses straight from one local file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.total = path.stat().st_size
        self.requests = 0

    def open(self, request: Any, *, timeout: float) -> Any:
        headers = dict(request.header_items())
        if "Range" not in headers:
            raise RuntimeError("benchmark refuses non-range requests")
        start_s, end_s = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start_s), int(end_s)
        with self.path.open("rb") as stream:
            stream.seek(start)
            body = stream.read(end - start + 1)
        self.requests += 1
        total = self.total

        class Part(io.BytesIO):
            status = 206
            headers = {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(end - start + 1),
                "ETag": '"authored-bench"',
            }

        return Part(body)


def _peak_rss() -> int:
    import psutil

    info = psutil.Process().memory_info()
    return int(getattr(info, "peak_wset", 0) or info.rss)


def measure(path: Path, start: int, stop: int, scratch: Path) -> dict[str, Any]:
    """Child-process body: one window acquisition, resource numbers as JSON."""
    from xlm.data.acquisition.fetcher import BoundedFetcher
    from xlm.data.acquisition.plan import (
        AcquisitionPlan,
        ParquetWindowDecode,
        PlanAuthorization,
    )
    from xlm.data.adapters.columns import columns_for

    baseline = _peak_rss()
    window = ParquetWindowDecode(
        stream_buffer_bytes=4 * 1024 * 1024, max_window_scan_rows=16_384, batch_rows=256
    )
    plan = AcquisitionPlan(
        plan_id=f"bench_{start}",
        source_id="authored",
        provider="https",
        repository="http://127.0.0.1:9/bench",
        revision=REVISION,
        mode="selected_records",
        selected_files=[path.name],
        row_ranges={path.name: (start, stop)},
        output_artifact_id=f"bench_{start}",
        projected_fields=list(columns_for("synth_en")),
        parquet_window=window,
    )
    plan = plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.compute_behavioral_hash(),
                authorized_by="offline benchmark",
                authorized_at="bench",
                is_pilot_approved=True,
            )
        }
    )
    fetcher = BoundedFetcher(plan, scratch / "scratch", scratch / "output")
    opener = FileRangeOpener(path)
    fetcher.opener = opener  # type: ignore[assignment]
    started = time.perf_counter()
    try:
        state = fetcher.run()
    finally:
        fetcher.close()
    wall = time.perf_counter() - started
    consumed = state.accounting.consumed
    return {
        "status": state.status,
        "window": [start, stop],
        "limits": {
            "max_transferred_bytes": plan.limits.max_transferred_bytes,
            "max_decompressed_bytes": plan.limits.max_decompressed_bytes,
            "max_scanned_records": plan.limits.max_scanned_records,
            "max_requests": plan.limits.max_requests,
        },
        "transferred_bytes": state.transferred_bytes,
        "decoded_bytes_charged": consumed.get("decompressed", 0),
        "rows_scanned": consumed.get("records_scanned", 0),
        "rows_retained": state.records_acquired,
        "requests": opener.requests,
        "wall_seconds": round(wall, 3),
        "baseline_peak_rss_bytes": baseline,
        "peak_rss_bytes": _peak_rss(),
    }


def run(workdir: Path, profile: str) -> list[dict[str, Any]]:
    from xlm.data.acquisition.plan import ParquetWindowDecode
    from xlm.data.acquisition.sampling import (
        SamplingRequest,
        discover_layout_local,
        plan_sample_blocks,
    )
    from xlm.data.adapters.columns import columns_for

    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"synth_shaped_{profile}.parquet"
    if not path.is_file():
        build(path, profile)
    layout = discover_layout_local(
        path, name=path.name, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
    )
    window = ParquetWindowDecode(
        stream_buffer_bytes=4 * 1024 * 1024, max_window_scan_rows=16_384, batch_rows=256
    )
    sampled = plan_sample_blocks(
        {path.name: layout},
        SamplingRequest(
            source_id="authored",
            view_id="default",
            revision=REVISION,
            files=(path.name,),
            seed=SEED,
            mode="window",
            projected_fields=tuple(columns_for("synth_en")),
            window=window,
        ),
    )
    (chosen,) = sampled.windows
    results: list[dict[str, Any]] = []
    # Sampled window, then the worst admissible window (ending at the domain).
    for label, (start, stop) in (
        ("sampled", (chosen.start_row, chosen.stop_row)),
        ("worst_admissible", (chosen.start_domain_rows - 1000, chosen.start_domain_rows)),
    ):
        scratch = workdir / f"run_{profile}_{label}_{time.time_ns()}"
        proc = subprocess.run(
            [
                sys.executable,
                __file__,
                "measure",
                "--file",
                str(path),
                "--start",
                str(start),
                "--stop",
                str(stop),
                "--scratch",
                str(scratch),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-2000:])
        doc = json.loads(proc.stdout.strip().splitlines()[-1])
        doc.update(
            {
                "profile": profile,
                "label": label,
                "file_bytes": path.stat().st_size,
                "group_rows": chosen.group_rows,
                "group_total_byte_size": chosen.group_total_byte_size,
                "selected_compressed_bytes": chosen.selected_compressed_bytes,
                "selected_uncompressed_bytes": chosen.selected_uncompressed_bytes,
                "largest_selected_chunk": max(c.compressed for c in chosen.selected_columns),
                "estimated_transfer_upper_bytes": chosen.estimated_transfer_upper_bytes,
                "estimated_requests": chosen.estimated_requests,
                "domain_estimated_transfer_upper_bytes": (
                    chosen.domain_estimated_transfer_upper_bytes
                ),
                "domain_estimated_requests": chosen.domain_estimated_requests,
            }
        )
        results.append(doc)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--workdir", type=Path, required=True)
    run_parser.add_argument("--profile", choices=sorted(PROFILES), default="typical")
    measure_parser = sub.add_parser("measure")
    measure_parser.add_argument("--file", type=Path, required=True)
    measure_parser.add_argument("--start", type=int, required=True)
    measure_parser.add_argument("--stop", type=int, required=True)
    measure_parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "measure":
        print(json.dumps(measure(args.file, args.start, args.stop, args.scratch)))
        return 0
    for doc in run(args.workdir, args.profile):
        print(json.dumps(doc, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
