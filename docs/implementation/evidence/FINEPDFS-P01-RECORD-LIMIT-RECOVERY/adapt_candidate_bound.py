"""OFFLINE: adapt the retained failed source under a candidate record bound. No text output.

Runs the production ``adapt_source_file`` with the p01 limits except
``max_record_bytes``, serial and with the plan's row-group parallelism, into
private scratch directories, and reports only counts, hashes and resources.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pyarrow as pa

from xlm.data.acquisition.source_local import adapt_source_file
from xlm.data.acquisition.source_parquet import file_sha256


def main() -> None:
    raw = Path(sys.argv[1])
    plan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    work = Path(sys.argv[3])
    bound = int(sys.argv[4])
    modes = sys.argv[5].split(",")
    identity = json.loads(Path(str(raw) + ".identity.json").read_text(encoding="utf-8"))
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    keys = (
        "max_decompression_ratio",
        "max_parser_bytes",
        "max_rows_per_file",
        "max_record_bytes",
        "max_decoded_bytes_per_file",
        "max_ledger_bytes",
        "max_canonical_bytes_per_file",
        "max_durable_bytes_per_file",
        "scratch_min_free_bytes",
        "processing_growth",
        "row_group_parallel",
    )
    base = {key: plan["limits"][key] for key in keys}
    base["max_record_bytes"] = bound
    pin = plan["source"]
    results = {}
    for mode in modes:
        limits = dict(base)
        if mode == "serial":
            limits.pop("row_group_parallel")
        output = work / mode
        shutil.rmtree(output, ignore_errors=True)
        started = time.monotonic()
        error = None
        try:
            result = adapt_source_file(
                raw,
                output,
                source_file=identity["source_file"],
                source_id=pin["source_id"],
                view_id=pin["view_id"],
                adapter_id=pin["adapter_id"],
                repository=pin["repository"],
                revision=pin["revision"],
                plan_id=plan["acquisition_plan"]["plan_id"],
                plan_hash=plan["acquisition_plan"]["plan_hash"],
                selection_hash=plan["acquisition_plan"]["selection_hash"],
                identity=identity,
                limits=limits,
            )
        except Exception as exc:  # report the class and message only (no record text is in it)
            error = f"{type(exc).__name__}: {exc}"
            result = {}
        keep = {k: v for k, v in result.items() if k not in ("row_group_pool",)}
        if result.get("row_group_pool"):
            pool = result["row_group_pool"]
            keep["row_group_pool"] = {
                k: pool[k] for k in sorted(pool) if not isinstance(pool[k], list)
            }
        if not error:
            keep["documents_file_sha256"] = file_sha256(output / "documents.jsonl")[0]
            keep["ledger_file_sha256"] = file_sha256(output / "adaptation_rejections.jsonl.zst")[0]
        keep["error"] = error
        keep["wall_seconds"] = time.monotonic() - started
        results[mode] = keep
        shutil.rmtree(output, ignore_errors=True)
    (work / f"adapt-{bound}.json").write_text(
        json.dumps({"record_bound": bound, "results": results}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
