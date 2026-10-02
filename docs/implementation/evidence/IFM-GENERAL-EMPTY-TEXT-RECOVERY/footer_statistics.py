"""OFFLINE, metadata only: per-row-group statistics of saved IFM Parquet footers.

Reads raw footers (``<footer><len:u32 LE>PAR1``) saved by the earlier bounded
footer audit (IFM-PRODUCTION-BOUND-RECOVERY) and reports, per file, the row
groups whose ``token_count`` minimum is 0 and whether any ``text`` minimum
statistic is the empty string. It prints no statistic value of ``text``: only
booleans and counts. No network, no corpus text.

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/footer_statistics.py \
        <footer-dir>
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def facts(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if data[-4:] != b"PAR1":
        raise ValueError(f"{path.name}: not a raw Parquet footer with trailer")
    metadata = pq.read_metadata(pa.BufferReader(b"PAR1" + data))
    names = [metadata.schema.column(i).name for i in range(metadata.num_columns)]
    text_index, token_index = names.index("text"), names.index("token_count")
    zero_token_groups: list[int] = []
    empty_text_min_groups: list[int] = []
    text_nulls = token_nulls = 0
    missing_statistics = 0
    for group in range(metadata.num_row_groups):
        row_group = metadata.row_group(group)
        text = row_group.column(text_index).statistics
        token = row_group.column(token_index).statistics
        if text is None or token is None or not token.has_min_max:
            missing_statistics += 1
            continue
        text_nulls += int(text.null_count)
        token_nulls += int(token.null_count)
        if int(token.min) == 0:
            zero_token_groups.append(group)
        if text.has_min_max and text.min in ("", b""):
            empty_text_min_groups.append(group)
    return {
        "footer": path.name,
        "footer_sha256": hashlib.sha256(data).hexdigest(),
        "rows": int(metadata.num_rows),
        "row_groups": int(metadata.num_row_groups),
        "groups_without_statistics": missing_statistics,
        "text_null_count": text_nulls,
        "token_count_null_count": token_nulls,
        "groups_with_token_count_min_0": zero_token_groups,
        "groups_with_empty_text_min": empty_text_min_groups,
    }


def main() -> None:
    directory = Path(sys.argv[1])
    report = [facts(path) for path in sorted(directory.glob("*.footer.bin"))]
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
