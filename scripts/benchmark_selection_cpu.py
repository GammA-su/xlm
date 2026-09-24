"""CPU-only per-row cost of the selected-record path on authored Parquet bytes.

Decodes an in-memory authored Parquet file with the production batch size and
runs ``encode_record`` + ``selected_record`` per row exactly as acquisition
does, reporting process CPU seconds (robust to wall-clock contention from other
processes) and a digest of every produced payload for byte comparison across
trees. No network, no disk writes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import time

import pyarrow as pa
import pyarrow.parquet as pq

from xlm.data.acquisition.records import encode_record, selected_record


def build(rows: int, text_bytes: int) -> bytes:
    rng = random.Random(11)
    words = ["".join(rng.choice("abcdefghij") for _ in range(rng.randint(2, 9))) for _ in range(512)]
    texts = []
    for _ in range(rows):
        target = text_bytes // 2 + rng.randint(0, text_bytes)
        parts: list[str] = []
        size = 0
        while size < target:
            word = words[rng.randrange(512)]
            parts.append(word)
            size += len(word) + 1
        texts.append(" ".join(parts) + ' "quoted" é\n')
    table = pa.table({"id": [f"doc-{i}" for i in range(rows)], "text": texts})
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=1000, compression="zstd")
    return sink.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--text-bytes", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    blob = build(args.rows, args.text_bytes)
    results = []
    for _ in range(args.repeats):
        digest = hashlib.sha256()
        cpu = time.process_time()
        wall = time.perf_counter()
        parquet = pq.ParquetFile(io.BytesIO(blob))
        index = 0
        for batch in parquet.iter_batches(batch_size=512, use_threads=False):
            for record in batch.to_pylist():
                raw = encode_record(record)
                payload = selected_record(
                    record,
                    {"row_index": index, "format": "parquet", "source_file": "authored.parquet"},
                    raw,
                )
                digest.update(payload)
                index += 1
        results.append(
            {
                "cpu_s": round(time.process_time() - cpu, 3),
                "wall_s": round(time.perf_counter() - wall, 3),
                "rows": index,
                "payload_sha256": digest.hexdigest(),
            }
        )
    best = min(results, key=lambda item: item["cpu_s"])
    print(
        json.dumps(
            {
                "rows": args.rows,
                "best_cpu_s": best["cpu_s"],
                "rows_per_cpu_s": round(best["rows"] / best["cpu_s"]),
                "payload_sha256": best["payload_sha256"],
                "repeats": results,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
