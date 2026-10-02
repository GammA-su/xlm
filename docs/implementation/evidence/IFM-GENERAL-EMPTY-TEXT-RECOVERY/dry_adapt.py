"""OFFLINE, read-only: adapt one retained IFM General shard in memory under the registry adapter.

Replays the serial loop of ``source_local.adapt_source_file`` with the same
primitives (``selected_payloads`` -> ``json.loads`` -> registry adapter ->
``serialize_document`` / ``build_rejection_record``) but hashes instead of
writing: documents count/bytes/SHA-256, canonical bytes, rejection codes,
rejected rows and the uncompressed ledger SHA-256. Prints no corpus text
(ledger reasons are text-free templates).

    uv run --offline --locked --extra cpu --extra eval python \
        docs/implementation/evidence/IFM-GENERAL-EMPTY-TEXT-RECOVERY/dry_adapt.py <rank> <path>
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_parquet import selected_payloads
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import RecordRejectedError
from xlm.data.adapters.registry import ADAPTERS_BY_ID
from xlm.data.adapters.rejections import (
    build_rejection_record,
    serialize_document,
    serialize_rejection,
)

FILES = {
    0: (
        "general/general_full.chunk0-bdbff8a5c6-00069.parquet",
        '"cf52d232509e6991424dc3e4a4c377c19e76b2e2b32e22f39cdd38f20b4fc8db"',
    ),
    1: (
        "general/general_full.chunk0-bdbff8a5c6-00146.parquet",
        '"709cec97b14c4e419a98f039ce48247ad7f53155209f5d5cd73e8f72e58b5a22"',
    ),
}
REVISION = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"
# p02's selection identity; the documents never carry it (only the selected records do).
LOCATOR = {
    "source_id": "ifm_behaviors",
    "repository": "IFM/Pretrain-Behaviors",
    "revision": REVISION,
    "selection_hash": "7b3cd854f223de992d28f9be67ecb874cf492157ac1681a160029b228ffc14f9",
}


def main() -> None:
    rank, path = int(sys.argv[1]), Path(sys.argv[2])
    source_file, etag = FILES[rank]
    adapter = ADAPTERS_BY_ID["ifm_general"]()
    started = time.monotonic()
    documents = hashlib.sha256()
    ledger = hashlib.sha256()
    counts: dict[str, int] = {}
    rejected: list[dict[str, Any]] = []
    rows = accepted = canonical = document_bytes = ledger_bytes = 0
    for row_index, payload in selected_payloads(
        path,
        source_file=source_file,
        locator=LOCATOR,
        etag=etag,
        columns=columns_for("ifm_general", "general"),
        max_record_bytes=8388608,
        max_parser_bytes=33554432,
        max_decoded_bytes=8061452288,
    ):
        rows += 1
        record = json.loads(payload)
        try:
            document = adapter.adapt(
                record, source_file=source_file, source_row=row_index, source_revision=REVISION
            )
        except RecordRejectedError as exc:
            line = (
                serialize_rejection(
                    build_rejection_record(
                        input_line=rows,
                        source_id="ifm_behaviors",
                        source_revision=REVISION,
                        source_file=source_file,
                        source_row=row_index,
                        adapter_id="ifm_general",
                        error=exc,
                        original_record_sha256=record["_xlm_acquisition"]["original_record_sha256"],
                    )
                ).encode("utf-8")
                + b"\n"
            )
            ledger.update(line)
            ledger_bytes += len(line)
            counts[type(exc).__name__] = counts.get(type(exc).__name__, 0) + 1
            rejected.append(json.loads(line))
            continue
        line = serialize_document(document).encode("utf-8") + b"\n"
        documents.update(line)
        document_bytes += len(line)
        accepted += 1
        canonical += document.utf8_byte_count
    report = {
        "rank": rank,
        "file": str(path),
        "source_file": source_file,
        "adapter": f"{type(adapter).__module__}.{type(adapter).__qualname__}",
        "rows": rows,
        "documents": accepted,
        "rejected": rows - accepted,
        "rejection_counts_by_code": dict(sorted(counts.items())),
        "rejected_rows": rejected,
        "canonical_bytes": canonical,
        "estimated_tokens": canonical // 4,
        "documents_sha256": documents.hexdigest(),
        "documents_file_bytes": document_bytes,
        "rejections_sha256": ledger.hexdigest(),
        "rejections_uncompressed_bytes": ledger_bytes,
        "seconds": round(time.monotonic() - started, 1),
    }
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
