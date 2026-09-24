"""Bounded CPU comparisons on authored data, with exact output checks."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/opus-review"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", choices=("green", "corrected"), required=True)
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT or ROOT.drive != "G:":
        raise ValueError("review worktree only")
    if args.label == "green":
        sys.path.insert(0, str(OUT / "green/src"))
    import pyarrow.parquet as pq
    from benchmark_selection_cpu import build

    from xlm.data.acquisition.records import encode_record, selected_record

    blob = build(50_000, 2000)
    results: list[dict[str, Any]] = []
    for _ in range(3):
        clocks = dict.fromkeys(("arrow_decode", "to_pylist", "encode", "locator"), 0.0)
        digest = hashlib.sha256()
        start = time.process_time()
        parquet = pq.ParquetFile(io.BytesIO(blob))
        batches = iter(parquet.iter_batches(batch_size=512, use_threads=False))
        index = 0
        while True:
            tick = time.perf_counter()
            try:
                batch = next(batches)
            except StopIteration:
                break
            clocks["arrow_decode"] += time.perf_counter() - tick
            tick = time.perf_counter()
            records = batch.to_pylist()
            clocks["to_pylist"] += time.perf_counter() - tick
            for record in records:
                tick = time.perf_counter()
                raw = encode_record(record)
                clocks["encode"] += time.perf_counter() - tick
                tick = time.perf_counter()
                payload = selected_record(
                    record,
                    {"row_index": index, "format": "parquet", "source_file": "authored.parquet"},
                    raw,
                )
                clocks["locator"] += time.perf_counter() - tick
                digest.update(payload)
                index += 1
        results.append(
            {
                "components_wall_seconds": clocks,
                "cpu_seconds": time.process_time() - start,
                "records": index,
                "sha256": digest.hexdigest(),
            }
        )
    report: dict[str, Any] = {
        "label": args.label,
        "serializer": results,
        "timing_note": "Component wall times; total process CPU; authored in-memory bytes",
    }
    if args.label == "corrected":
        from xlm.data.dedup import minhash

        rng = random.Random(991)
        params = minhash._permutation_params(minhash.MinHashConfig())
        rows = []
        for size in (16, 64, 512, 4097):
            values = {rng.getrandbits(64) for _ in range(size)}
            expected = minhash._signature_python(values, params)
            for name, kernel in (
                ("python", minhash._signature_python),
                ("numpy", minhash._signature_vectorized),
                ("arrow", minhash._signature_arrow),
            ):
                assert kernel(values, params) == expected
                timings = []
                for _ in range(5):
                    tick = time.perf_counter()
                    for _ in range(10):
                        actual = kernel(values, params)
                    timings.append((time.perf_counter() - tick) / 10)
                    assert actual == expected
                rows.append(
                    {
                        "shingles": size,
                        "backend": name,
                        "median_seconds": statistics.median(timings),
                        "samples": timings,
                    }
                )
        report["minhash"] = rows
    (OUT / f"cpu-{args.label}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
