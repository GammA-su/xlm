"""OFFLINE structural scan of one retained FinePDFs source Parquet. Emits no text.

Every row goes through the production projection and ``located_record``
serialization (the exact bytes the record bound compares), then through the
registered adapter, recording only lengths and non-text metadata.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pyarrow.parquet as pq

from xlm.data.acquisition.projection import parquet_field_leaves, resolve_projection
from xlm.data.acquisition.source_parquet import DECODE_BATCH_ROWS, file_sha256, located_record
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID, RecordRejectedError
from xlm.data.adapters.rejections import serialize_document

MIB = 1024 * 1024
PARSER = 32 * MIB

raw_path = Path(sys.argv[1])
plan_path = Path(sys.argv[2])
out = Path(sys.argv[3])
plan = json.loads(plan_path.read_text(encoding="utf-8"))
identity = json.loads(Path(str(raw_path) + ".identity.json").read_text(encoding="utf-8"))
started = time.monotonic()
sha, length = file_sha256(raw_path)
hash_seconds = time.monotonic() - started
pin = plan["source"]
locator = {
    "source_id": pin["source_id"],
    "repository": pin["repository"],
    "revision": pin["revision"],
    "selection_hash": plan["acquisition_plan"]["selection_hash"],
}
adapter = ADAPTERS_BY_ID[pin["adapter_id"]]()
parquet = pq.ParquetFile(
    raw_path, pre_buffer=False, thrift_string_size_limit=PARSER, thrift_container_size_limit=PARSER
)
logical = list(
    resolve_projection(
        parquet_field_leaves(parquet), tuple(columns_for(pin["adapter_id"], pin["view_id"]))
    ).logical_fields
)
meta = parquet.metadata
rows: list[tuple[int, int, int, int, int]] = []  # raw, payload, accepted, doc_line, text_bytes
big: list[dict[str, Any]] = []
groups: list[dict[str, Any]] = []
max_batch = 0
decoded_total = 0
peak = 0
process = psutil.Process()
base = 0
codes: dict[str, int] = {}
for group in range(parquet.num_row_groups):
    info = meta.row_group(group)
    group_rows = int(info.num_rows)
    local = 0
    group_decoded = 0
    group_max_raw = 0
    for batch in parquet.iter_batches(
        batch_size=DECODE_BATCH_ROWS, row_groups=[group], columns=logical, use_threads=False
    ):
        nbytes = int(batch.nbytes)
        group_decoded += nbytes
        decoded_total += nbytes
        max_batch = max(max_batch, nbytes)
        for offset, value in enumerate(batch.to_pylist()):
            row_index = base + local + offset
            raw, payload = located_record(
                value,
                {
                    "row_index": row_index,
                    "row_group": group,
                    "row_in_group": local + offset,
                    "format": "parquet",
                    "etag": identity["etag"],
                    "original_record_hash_convention": (
                        "canonical JSON serialization, not compressed bytes"
                    ),
                    **locator,
                    "source_file": identity["source_file"],
                },
            )
            record = json.loads(payload)
            try:
                document = adapter.adapt(
                    record,
                    source_file=identity["source_file"],
                    source_row=row_index,
                    source_revision=pin["revision"],
                )
                accepted, line, text_bytes = (
                    1,
                    len(serialize_document(document)) + 1,
                    document.utf8_byte_count,
                )
            except RecordRejectedError as exc:
                accepted, line, text_bytes = 0, 0, 0
                codes[type(exc).__name__] = codes.get(type(exc).__name__, 0) + 1
            rows.append((len(raw), len(payload), accepted, line, text_bytes))
            group_max_raw = max(group_max_raw, len(raw))
            if len(raw) > 4 * MIB:
                text = value.get("text")
                big.append(
                    {
                        "row": row_index,
                        "row_group": group,
                        "encoded_bytes": len(raw),
                        "payload_bytes": len(payload),
                        "text_utf8_bytes": len(text.encode("utf-8"))
                        if isinstance(text, str)
                        else None,
                        "extractor": value.get("extractor"),
                        "is_truncated": value.get("is_truncated"),
                        "token_count": value.get("token_count"),
                        "adapter": "accepted" if accepted else "rejected",
                    }
                )
            del record, raw, payload
        local += batch.num_rows
        peak = max(peak, process.memory_info().rss)
    groups.append(
        {
            "group": group,
            "rows": group_rows,
            "decoded_bytes": group_decoded,
            "compressed_bytes": int(info.total_byte_size),
            "max_encoded_bytes": group_max_raw,
        }
    )
    base += group_rows
parquet.close()


def pct(sorted_values: list[int], q: float) -> int:
    index = max(0, math.ceil(q * len(sorted_values)) - 1)
    return sorted_values[index]


raws = sorted(r[0] for r in rows)
top = max(range(len(rows)), key=lambda i: rows[i][0])
thresholds = [4, 8, 16, 24, 32, 40, 48, 64]
result = {
    "file": identity["source_file"],
    "path": str(raw_path),
    "sha256": sha,
    "bytes": length,
    "identity_sha256": identity["sha256"],
    "identity_length": identity["length"],
    "identity_match": sha == identity["sha256"] and length == identity["length"],
    "hash_seconds": hash_seconds,
    "footer_rows": int(meta.num_rows),
    "row_groups": parquet.num_row_groups,
    "rows_scanned": len(rows),
    "projection": logical,
    "max_encoded_bytes": raws[-1],
    "max_encoded_row": top,
    "max_payload_bytes": max(r[1] for r in rows),
    "percentiles_encoded_bytes": {
        q: pct(raws, float(q)) for q in ("0.5", "0.9", "0.99", "0.999", "0.9999", "0.99999")
    },
    "rows_above_mib": {f">{t} MiB": sum(1 for v in raws if v > t * MIB) for t in thresholds},
    "rows_above_4mib": big,
    "first_row_above_32mib": next((b["row"] for b in big if b["encoded_bytes"] > 32 * MIB), None),
    "documents": sum(r[2] for r in rows),
    "rejected": len(rows) - sum(r[2] for r in rows),
    "rejection_counts_by_code": codes,
    "canonical_bytes": sum(r[4] for r in rows),
    "estimated_tokens": sum(r[4] for r in rows) // 4,
    "documents_file_bytes": sum(r[3] for r in rows),
    "selected_records_bytes": sum(r[1] for r in rows),
    "decoded_bytes": decoded_total,
    "max_batch_decoded_bytes": max_batch,
    "max_group_decoded_bytes": max(g["decoded_bytes"] for g in groups),
    "groups_with_rows_above_8mib": [g for g in groups if g["max_encoded_bytes"] > 8 * MIB],
    "scan_peak_rss_bytes": peak,
    "scan_seconds": time.monotonic() - started,
}
out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            k: v
            for k, v in result.items()
            if k not in ("rows_above_4mib", "groups_with_rows_above_8mib")
        },
        indent=2,
    )
)
