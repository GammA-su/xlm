"""Bounded local stage-ceiling microbenchmarks for high-bandwidth ingest.

Measures, separately and on authored synthetic bytes only: SHA-256 by update
size, zlib/gzip decompression, Parquet (zstd) decode, Arrow->Python conversion,
canonical JSON serialization, and file writes (page-cache, flushed, fsynced per
window) plus sequential reads on a chosen directory. Each figure is the maximum
sustainable rate of that single stage on one thread; it is not an end-to-end
claim and never live-source evidence. Page-cache rates are labeled as such.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import random
import sys
import time
import zlib
from pathlib import Path
from typing import Any

MIB = 1024 * 1024


def _rate(nbytes: int, seconds: float) -> float:
    return round(nbytes / seconds / 1e6, 1) if seconds > 0 else 0.0


def bench_hash(total: int) -> dict[str, float]:
    data = random.Random(1).randbytes(16 * MIB)
    view = memoryview(data)
    out: dict[str, float] = {}
    for step in (8 * 1024, 64 * 1024, 256 * 1024, 1 * MIB, 4 * MIB, 16 * MIB):
        digest = hashlib.sha256()
        started = time.perf_counter()
        done = 0
        while done < total:
            for offset in range(0, len(view), step):
                digest.update(view[offset : offset + step])
            done += len(view)
        out[f"sha256_{step // 1024}KiB_MB_s"] = _rate(done, time.perf_counter() - started)
    return out


def _text_corpus(total: int) -> bytes:
    rng = random.Random(2)
    words = [
        "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9)))
        for _ in range(4096)
    ]
    lines = []
    size = 0
    while size < total:
        text = " ".join(words[rng.randrange(4096)] for _ in range(rng.randint(50, 600)))
        line = json.dumps({"id": f"d{len(lines)}", "text": text}) + "\n"
        lines.append(line)
        size += len(line)
    return "".join(lines).encode()


def bench_decompress(total: int) -> dict[str, float]:
    plain = _text_corpus(total)
    packed = gzip.compress(plain, compresslevel=6)
    out: dict[str, float] = {"gzip_ratio": round(len(plain) / len(packed), 2)}
    for chunk in (8 * 1024, 64 * 1024, 1 * MIB):
        decompressor = zlib.decompressobj(31)
        started = time.perf_counter()
        produced = 0
        for offset in range(0, len(packed), chunk):
            produced += len(decompressor.decompress(packed[offset : offset + chunk]))
        produced += len(decompressor.flush())
        elapsed = time.perf_counter() - started
        out[f"gunzip_in{chunk // 1024}KiB_out_MB_s"] = _rate(produced, elapsed)
        out[f"gunzip_in{chunk // 1024}KiB_in_MB_s"] = _rate(len(packed), elapsed)
    started = time.perf_counter()
    count = sum(1 for _ in io.BytesIO(plain))
    out["line_split_MB_s"] = _rate(len(plain), time.perf_counter() - started)
    started = time.perf_counter()
    for line in io.BytesIO(plain):
        json.loads(line)
    out["json_loads_MB_s"] = _rate(len(plain), time.perf_counter() - started)
    out["json_lines"] = count
    return out


def bench_parquet(rows: int, text_bytes: int) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = random.Random(3)
    words = [
        "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(2, 9)))
        for _ in range(4096)
    ]
    texts = []
    for _ in range(rows):
        parts: list[str] = []
        length = 0
        target = text_bytes // 2 + rng.randint(0, text_bytes)
        while length < target:
            word = words[rng.randrange(4096)]
            parts.append(word)
            length += len(word) + 1
        texts.append(" ".join(parts))
    table = pa.table({"id": [f"d{i}" for i in range(rows)], "text": texts})
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=1000, compression="zstd")
    blob = sink.getvalue()
    uncompressed = table.nbytes
    out: dict[str, Any] = {
        "parquet_compressed_bytes": len(blob),
        "parquet_arrow_bytes": uncompressed,
    }
    started = time.perf_counter()
    decoded = pq.ParquetFile(io.BytesIO(blob)).read(use_threads=False)
    out["parquet_decode_arrow_MB_s_arrow_out"] = _rate(uncompressed, time.perf_counter() - started)
    out["parquet_decode_arrow_MB_s_compressed_in"] = _rate(len(blob), time.perf_counter() - started)
    for batch_size in (1, 64, 512, 4096):
        parquet = pq.ParquetFile(io.BytesIO(blob))
        started = time.perf_counter()
        count = 0
        for batch in parquet.iter_batches(batch_size=batch_size, use_threads=False):
            count += len(batch.to_pylist())
            if batch_size == 1 and count >= 20_000:
                break
        elapsed = time.perf_counter() - started
        out[f"to_pylist_batch{batch_size}_rows_s"] = round(count / elapsed)
    rows_py = decoded.slice(0, min(rows, 20_000)).to_pylist()
    started = time.perf_counter()
    produced = 0
    for record in rows_py:
        produced += len(
            (
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        )
    elapsed = time.perf_counter() - started
    out["canonical_json_dumps_rows_s"] = round(len(rows_py) / elapsed)
    out["canonical_json_dumps_MB_s"] = _rate(produced, elapsed)
    return out


def bench_disk(directory: Path, total: int) -> dict[str, float]:
    directory.mkdir(parents=True, exist_ok=True)
    data = random.Random(4).randbytes(4 * MIB)
    out: dict[str, float] = {}

    def write(path: Path, step: int, window: int | None, final_fsync: bool) -> float:
        started = time.perf_counter()
        with path.open("wb", buffering=0) as stream:
            written = since = 0
            view = memoryview(data)
            while written < total:
                for offset in range(0, len(view), step):
                    stream.write(view[offset : offset + step])
                written += len(view)
                since += len(view)
                if window is not None and since >= window:
                    os.fsync(stream.fileno())
                    since = 0
            if final_fsync:
                os.fsync(stream.fileno())
        return time.perf_counter() - started

    cases = [
        ("cached_write_64KiB", 65536, None, False),
        ("cached_write_1MiB", MIB, None, False),
        ("write_fsync_end_1MiB", MIB, None, True),
        ("write_fsync_every_1MiB", 65536, MIB, True),
        ("write_fsync_every_16MiB", 65536, 16 * MIB, True),
        ("write_fsync_every_64MiB", MIB, 64 * MIB, True),
    ]
    for name, step, window, final in cases:
        path = directory / f"ceiling-{name}.bin"
        elapsed = write(path, step, window, final)
        out[f"{name}_MB_s"] = _rate(total, elapsed)
        path.unlink()
    path = directory / "ceiling-read.bin"
    write(path, MIB, None, True)
    started = time.perf_counter()
    with path.open("rb", buffering=0) as stream:
        while stream.read(MIB):
            pass
    out["sequential_read_after_write_MB_s_(page_cache_likely)"] = _rate(
        total, time.perf_counter() - started
    )
    path.unlink()
    started = time.perf_counter()
    for index in range(200):
        tmp = directory / f"ceiling-small-{index}.tmp"
        with tmp.open("xb") as stream:
            stream.write(b"x" * 16384)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, directory / "ceiling-small.json")
    out["small_atomic_replace_fsync_ms"] = round((time.perf_counter() - started) / 200 * 1000, 3)
    (directory / "ceiling-small.json").unlink()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk-dir", type=Path, action="append", default=[])
    parser.add_argument("--disk-mib", type=int, default=1024)
    parser.add_argument("--cpu-mib", type=int, default=256)
    parser.add_argument("--parquet-rows", type=int, default=50_000)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--skip-cpu", action="store_true")
    args = parser.parse_args()
    doc: dict[str, Any] = {
        "benchmark": "ingest-stage-ceilings",
        "evidence_class": "authored synthetic single-thread stage ceilings",
        "python": sys.version,
    }
    if not args.skip_cpu:
        doc["hash"] = bench_hash(args.cpu_mib * MIB)
        doc["decompress"] = bench_decompress(args.cpu_mib * MIB // 2)
        doc["parquet"] = bench_parquet(args.parquet_rows, 2000)
    for directory in args.disk_dir:
        doc[f"disk:{directory}"] = bench_disk(directory, args.disk_mib * MIB)
    text = json.dumps(doc, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text, "utf-8")


if __name__ == "__main__":
    main()
