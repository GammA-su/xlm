"""NETWORK (bounded, metadata only): Parquet footers of the four IFM p01 selected files.

Exactly two ranged GETs per file (8-byte trailer, then the footer), pinned
revision, host allowlist, 30 s timeout, no retries, 4 MiB per-file and 16 MiB
total caps, and the Content-Range total must equal the frozen inventory size.
No document payload is requested. Text-column statistics (which can hold corpus
text) are never read; only integer ``token_count`` statistics are reported.
Raw footers are kept outside the repository (``--keep-dir``) for offline reuse.
This uses plain urllib, not the Hugging Face client: HF_HUB_OFFLINE stays as set.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-PRODUCTION-BOUND-RECOVERY/footer_metadata_fetch.py \
        --keep-dir <scratchpad>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

REPOSITORY = "IFM/Pretrain-Behaviors"
REVISION = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"
#: (view, rank, file, frozen inventory size) of the four p01 selections.
TARGETS = (
    ("general", 0, "general/general_full.chunk0-bdbff8a5c6-00069.parquet", 2013330256),
    ("general", 1, "general/general_full.chunk0-bdbff8a5c6-00146.parquet", 2014409401),
    ("planning", 0, "planning/planning.chunk0-160f3594ed-00058.parquet", 2007884297),
    ("planning", 1, "planning/planning.chunk1-6d580bf230-00295.parquet", 2007493650),
)
HOSTS = {"huggingface.co", "cas-bridge.xethub.hf.co", "us.aws.cdn.hf.co"}
FILE_CAP = 4 * 1024 * 1024
TOTAL_CAP = 16 * 1024 * 1024
TIMEOUT = 30.0
log: dict[str, Any] = {"requests": 0, "redirects": 0, "bytes": 0, "hosts": [], "responses": []}


class Guard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        host = urllib.parse.urlparse(newurl).hostname
        if host not in HOSTS:
            raise RuntimeError(f"redirect host not allowlisted: {host}")
        log["redirects"] += 1
        log["hosts"].append(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(Guard)


def url_of(name: str) -> str:
    quoted = urllib.parse.quote(name)
    return f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}/{quoted}"


def get(name: str, size: int, start: int, stop: int, spent: list[int]) -> bytes:
    length = stop - start
    if spent[0] + length > FILE_CAP or log["bytes"] + length > TOTAL_CAP:
        raise RuntimeError("metadata byte cap reached")
    request = urllib.request.Request(url_of(name), headers={"Range": f"bytes={start}-{stop - 1}"})
    with OPENER.open(request, timeout=TIMEOUT) as response:
        log["requests"] += 1
        if response.status != 206:
            raise RuntimeError(f"status {response.status}")
        total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        if total != size:
            raise RuntimeError(f"{name}: remote size {total} differs from inventory {size}")
        data = response.read(length + 1)
        log["responses"].append({"file": name, "range": [start, stop - 1], "bytes": len(data)})
    if len(data) != length:
        raise RuntimeError("short or long ranged read")
    spent[0] += length
    log["bytes"] += length
    return data


def summarize(view: str, rank: int, name: str, size: int, footer: bytes) -> dict[str, Any]:
    md = pq.read_metadata(io.BytesIO(footer))
    schema = md.schema.to_arrow_schema()
    groups = [md.row_group(i) for i in range(md.num_row_groups)]
    columns = [groups[0].column(c).path_in_schema for c in range(md.num_columns)]
    per_column: dict[str, dict[str, int]] = {
        c: {"compressed": 0, "uncompressed": 0, "max_rg_uncompressed": 0} for c in columns
    }
    projected = ("text", "token_count")
    max_ratio = 0.0
    token_max = None
    token_stats = True
    max_projected_rg = 0
    encodings: dict[str, list[str]] = {}
    codecs: set[str] = set()
    for group in groups:
        rg_projected = 0
        rg_projected_compressed = 0
        for c in range(md.num_columns):
            chunk = group.column(c)
            path = chunk.path_in_schema
            entry = per_column[path]
            entry["compressed"] += chunk.total_compressed_size
            entry["uncompressed"] += chunk.total_uncompressed_size
            entry["max_rg_uncompressed"] = max(
                entry["max_rg_uncompressed"], chunk.total_uncompressed_size
            )
            codecs.add(str(chunk.compression))
            encodings.setdefault(path, sorted({str(e) for e in chunk.encodings}))
            if path in projected:
                rg_projected += chunk.total_uncompressed_size
                rg_projected_compressed += chunk.total_compressed_size
                ratio = chunk.total_uncompressed_size / max(1, chunk.total_compressed_size)
                max_ratio = max(max_ratio, ratio)
            if path == "token_count":
                stats = chunk.statistics
                if stats is None or not stats.has_min_max or stats.null_count is None:
                    token_stats = False
                else:
                    token_max = stats.max if token_max is None else max(token_max, stats.max)
        max_projected_rg = max(max_projected_rg, rg_projected)
    rows = [g.num_rows for g in groups]
    return {
        "view": view,
        "rank": rank,
        "unit": f"f{rank:05d}",
        "file": name,
        "file_bytes": size,
        "footer_bytes": len(footer),
        "footer_sha256": hashlib.sha256(footer).hexdigest(),
        "created_by": md.created_by,
        "format_version": md.format_version,
        "rows": md.num_rows,
        "row_groups": md.num_row_groups,
        "max_row_group_rows": max(rows),
        "min_row_group_rows": min(rows),
        "columns": columns,
        "arrow_types": {f.name: str(f.type) for f in schema},
        "codecs": sorted(codecs),
        "encodings": encodings,
        "total_byte_size": sum(g.total_byte_size for g in groups),
        "max_row_group_total_byte_size": max(g.total_byte_size for g in groups),
        "column_chunk_compressed_bytes": sum(v["compressed"] for v in per_column.values()),
        "per_column": per_column,
        "projected_uncompressed_bytes": sum(per_column[c]["uncompressed"] for c in projected),
        "projected_compressed_bytes": sum(per_column[c]["compressed"] for c in projected),
        "max_row_group_projected_uncompressed_bytes": max_projected_rg,
        "max_projected_column_ratio": round(max_ratio, 4),
        "token_count_max": token_max if token_stats else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-dir", required=True, help="where raw footers are kept (not git)")
    args = parser.parse_args()
    keep = Path(args.keep_dir)
    keep.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    results = []
    for view, rank, name, size in TARGETS:
        spent = [0]
        tail = get(name, size, size - 8, size, spent)
        footer_length, magic = struct.unpack("<I4s", tail)
        if magic != b"PAR1" or footer_length + 8 > FILE_CAP:
            raise RuntimeError(f"{name}: bad trailer")
        footer = get(name, size, size - 8 - footer_length, size - 8, spent) + tail
        (keep / f"{view}-f{rank:05d}.footer.bin").write_bytes(footer)
        results.append(summarize(view, rank, name, size, footer))
    log["wall_seconds"] = round(time.monotonic() - started, 3)
    json.dump(
        {"repository": REPOSITORY, "revision": REVISION, "network": log, "files": results},
        sys.stdout,
        indent=2,
        sort_keys=True,
        default=str,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
