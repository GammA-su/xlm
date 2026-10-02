"""OFFLINE, read-only: classify every ``text``/``token_count`` value of a retained IFM shard.

Reports counts, row coordinates, lengths and whitespace code points only; it
never prints corpus text. Then reproduces the production adapter call on the
exact certified record stream (``selected_payloads`` -> ``json.loads`` ->
``IfmGeneralAdapter.adapt``) for rows ``[0, first_invalid]`` and for every
invalid row, recording the exception class, message and raising line.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/scan_retained_f00000.py \
        G:/XLM/acq-raw/ifm_general/source/general/general_full.chunk0-bdbff8a5c6-00069.parquet
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from xlm.data.acquisition.source_parquet import selected_payloads
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID

SOURCE_FILE = "general/general_full.chunk0-bdbff8a5c6-00069.parquet"
# Locator of General p02 (plan.json / identity.json); only lineage, never read by the adapter.
LOCATOR = {
    "source_id": "ifm_behaviors",
    "repository": "IFM/Pretrain-Behaviors",
    "revision": "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5",
    "selection_hash": "7b3cd854f223de992d28f9be67ecb874cf492157ac1681a160029b228ffc14f9",
}
ETAG = '"cf52d232509e6991424dc3e4a4c377c19e76b2e2b32e22f39cdd38f20b4fc8db"'
LIMITS = {"max_record_bytes": 8388608, "max_parser_bytes": 33554432}
MAX_DECODED = 8061452288


def classify(path: Path) -> dict[str, Any]:
    parquet = pq.ParquetFile(path, pre_buffer=False)
    schema = parquet.schema_arrow
    names = schema.names
    report: dict[str, Any] = {
        "rows": int(parquet.metadata.num_rows),
        "row_groups": int(parquet.num_row_groups),
        "schema": {name: str(schema.field(name).type) for name in names},
        "schema_nullable": {name: bool(schema.field(name).nullable) for name in names},
    }
    text_type = schema.field("text").type if "text" in names else None
    token_type = schema.field("token_count").type if "token_count" in names else None
    text: dict[str, Any] = {
        "column_exists": text_type is not None,
        "arrow_type": None if text_type is None else str(text_type),
        "null": 0,
        "non_string": 0,
        "non_string_note": "column is a string type: a non-string value is not representable",
        "empty": 0,
        "whitespace_only": 0,
        "non_empty_valid": 0,
        "valid_with_leading_whitespace": 0,
    }
    token: dict[str, Any] = {
        "column_exists": token_type is not None,
        "arrow_type": None if token_type is None else str(token_type),
        "null": 0,
        "wrong_type": 0,
        "wrong_type_note": "column is an integer type: a non-integer value is not representable",
        "negative": 0,
        "zero": 0,
        "positive": 0,
        "zero_rows": [],
        "negative_rows": [],
        "null_rows": [],
        "min": None,
        "max": None,
    }
    if text_type is not None and not (
        pa.types.is_string(text_type) or pa.types.is_large_string(text_type)
    ):
        text["non_string_note"] = "column type is not string: structural drift"
    invalid: list[dict[str, Any]] = []
    base = 0
    for group in range(parquet.num_row_groups):
        table = parquet.read_row_group(group, columns=["text", "token_count"], use_threads=False)
        texts = table.column("text").combine_chunks()
        tokens = table.column("token_count").combine_chunks()
        nulls = pc.is_null(texts).to_pylist()
        lengths = pc.utf8_length(texts).to_pylist()
        firsts = pc.utf8_slice_codeunits(texts, 0, 1).to_pylist()
        token_values = tokens.to_pylist()
        for local, (is_null, length, first, value) in enumerate(
            zip(nulls, lengths, firsts, token_values, strict=True)
        ):
            row = base + local
            if value is None:
                token["null"] += 1
                token["null_rows"].append(row)
            elif value < 0:
                token["negative"] += 1
                token["negative_rows"].append(row)
            elif value == 0:
                token["zero"] += 1
                token["zero_rows"].append(row)
            else:
                token["positive"] += 1
            if value is not None:
                token["min"] = value if token["min"] is None else min(token["min"], value)
                token["max"] = value if token["max"] is None else max(token["max"], value)
            category = None
            if is_null:
                category = "null"
            elif length == 0:
                category = "empty"
            elif first is not None and first.isspace():
                full = texts[local].as_py()
                if not full.strip():
                    category = "whitespace_only"
                    points = sorted({f"U+{ord(ch):04X}" for ch in full})
                else:
                    text["valid_with_leading_whitespace"] += 1
            if category is None:
                text["non_empty_valid"] += 1
                continue
            text[category] += 1
            entry: dict[str, Any] = {
                "row_index": row,
                "row_group": group,
                "row_in_group": local,
                "category": category,
                "chars": None if is_null else length,
                "utf8_bytes": None if is_null else len(texts[local].as_py().encode("utf-8")),
                "token_count": value,
            }
            if category == "whitespace_only":
                entry["whitespace_code_points"] = points
            invalid.append(entry)
        base += table.num_rows
    total = report["rows"]
    report["text"] = text
    report["token_count"] = token
    report["invalid_text_rows"] = invalid
    report["invalid_text_total"] = len(invalid)
    report["invalid_text_fraction"] = len(invalid) / total if total else None
    return report


def adapt_rows(path: Path, start: int, stop: int) -> dict[str, Any]:
    adapter = ADAPTERS_BY_ID["ifm_general"]()
    accepted = 0
    first_error: dict[str, Any] | None = None
    for row_index, payload in selected_payloads(
        path,
        source_file=SOURCE_FILE,
        locator=LOCATOR,
        etag=ETAG,
        columns=columns_for("ifm_general", "general"),
        max_record_bytes=LIMITS["max_record_bytes"],
        max_parser_bytes=LIMITS["max_parser_bytes"],
        max_decoded_bytes=MAX_DECODED,
        row_range=(start, stop),
    ):
        record = json.loads(payload)
        try:
            adapter.adapt(
                record,
                source_file=SOURCE_FILE,
                source_row=row_index,
                source_revision=LOCATOR["revision"],
            )
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            text_value = record.get("text")
            first_error = {
                "row_index": row_index,
                "exception": type(exc).__name__,
                "message": str(exc),
                "site": f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}",
                "record_keys": sorted(k for k in record if k != "_xlm_acquisition"),
                "text_python_type": type(text_value).__name__,
                "text_chars": len(text_value) if isinstance(text_value, str) else None,
            }
            break
        accepted += 1
    return {"row_range": [start, stop], "accepted_before_error": accepted, "error": first_error}


def main() -> None:
    path = Path(sys.argv[1])
    started = time.monotonic()
    report = classify(path)
    report["file"] = str(path)
    report["source_file"] = SOURCE_FILE
    invalid = report["invalid_text_rows"]
    if invalid:
        first = invalid[0]["row_index"]
        report["reproduction_first"] = adapt_rows(path, 0, first + 1)
        report["reproduction_each_invalid"] = [
            adapt_rows(path, row["row_index"], row["row_index"] + 1)["error"] for row in invalid
        ]
    report["seconds"] = round(time.monotonic() - started, 1)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
