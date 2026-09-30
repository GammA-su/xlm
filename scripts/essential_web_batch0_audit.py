"""Offline, metadata-only audit of the retained Batch-0 recovery evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import essential_web_fast as driver
import psutil
import pyarrow.parquet as pq

from xlm.data.acquisition.projection import parquet_field_leaves, resolve_projection
from xlm.data.acquisition.records import encode_record
from xlm.data.acquisition.source_parquet import file_sha256, load_durable_source, located_record
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.essential_web_selector import FrozenEssentialWebSelector
from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID, RecordRejectedError
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_local as local


def audit(campaign: driver.Campaign) -> dict[str, Any]:
    started = time.monotonic()
    record = driver.batch_record(campaign, 0)
    state, _ = driver.campaign_state(campaign)
    sealed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for rank, name in enumerate(record["files"]):
        directory = campaign.unit_dir(0, rank)
        path = directory / local.RECEIPT_FILENAME
        if not path.exists():
            remaining.append({"key": f"f{rank:05d}", "file": name})
            continue
        receipt = driver.load_receipt(path, campaign)
        if receipt["file"] != name or receipt["inventory_rank"] != rank:
            raise RuntimeError("receipt membership differs")
        source = load_durable_source(campaign.raw_path(name))
        if source != receipt["source"]:
            raise RuntimeError("receipt source identity differs")
        checked = [path, campaign.raw_path(name)]
        for view, info in receipt["views"].items():
            for filename, hash_key in (
                ("documents.jsonl", "documents_sha256"),
                (local.LEDGER_FILENAME, "rejections_file_sha256"),
                ("adaptation_summary.json", "adaptation_summary_sha256"),
            ):
                artifact = directory / view / filename
                if file_sha256(artifact)[0] != info[hash_key]:
                    raise RuntimeError(f"artifact hash differs: {artifact.name}")
                checked.append(artifact)
            ledger = local.read_ledger(
                directory / view / local.LEDGER_FILENAME, info["rejections_uncompressed_bytes"]
            )
            if hashlib.sha256(ledger).hexdigest() != info["rejections_sha256"]:
                raise RuntimeError("uncompressed ledger hash differs")
        sealed.append(
            {
                "key": f"f{rank:05d}",
                "receipt": receipt["digest"],
                "artifacts": [
                    {"path": str(p), "bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                    for p in checked
                ],
            }
        )
        print(f"verified f{rank:05d}", flush=True)
    diagnostics = []
    for unit in remaining:
        name = unit["file"]
        path = campaign.raw_path(name)
        source = load_durable_source(path)
        if source is None:
            raise RuntimeError("remaining source is unavailable offline")
        limits = campaign.process_limits()
        layout = local.check_layout(path, name, limits)
        evaluator = FrozenEssentialWebSelector.load()
        adapters = {v: ADAPTERS_BY_ID[local.ADAPTER_ID](v) for v in campaign.config["views"]}
        largest = count = decoded = peak_rss = 0
        oversized = []
        with pq.ParquetFile(path, pre_buffer=False) as parquet:
            projection = list(
                resolve_projection(
                    parquet_field_leaves(parquet),
                    tuple(columns_for(local.ADAPTER_ID, bulk.PLAN_VIEW)),
                ).logical_fields
            )
            for batch in parquet.iter_batches(batch_size=32, columns=projection, use_threads=False):
                decoded += int(batch.nbytes)
                peak_rss = max(peak_rss, psutil.Process().memory_info().rss)
                if (
                    decoded > limits["max_decoded_bytes_per_file"]
                    or time.monotonic() - started > 300
                ):
                    raise RuntimeError("offline diagnostic resource bound exceeded")
                for row in batch.to_pylist():
                    raw, _ = located_record(row, {})
                    # Independent certified encoder: no whole-file accounting assumption.
                    if encode_record(row) != raw:
                        raise RuntimeError("whole-file and certified record encoders differ")
                    largest = max(largest, len(raw))
                    if len(raw) > limits["max_record_bytes"]:
                        decision = evaluator.decide(row)
                        outcomes = {}
                        for view, adapter in adapters.items():
                            try:
                                adapter.adapt(
                                    row,
                                    source_file=name,
                                    source_row=count,
                                    source_revision=source["revision"],
                                )
                                outcomes[view] = "accepted"
                            except RecordRejectedError as exc:
                                outcomes[view] = type(exc).__name__
                        fields = {
                            k: {
                                "type": type(v).__name__,
                                "encoded_bytes": len(
                                    json.dumps(
                                        v,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                        allow_nan=False,
                                    ).encode("utf-8")
                                ),
                            }
                            for k, v in row.items()
                        }
                        oversized.append(
                            {
                                "row_index_zero_based": count,
                                "raw_bytes": len(raw),
                                "limit": limits["max_record_bytes"],
                                "fields": fields,
                                "text_utf8_bytes": len(row["text"].encode("utf-8")),
                                "selector": asdict(decision),
                                "adapter_outcomes": outcomes,
                            }
                        )
                    count += 1
        diagnostics.append(
            {
                **unit,
                "source_sha256": source["sha256"],
                "rows": count,
                "footer_rows": layout["rows"],
                "maximum_raw_bytes": largest,
                "decoded_bytes": decoded,
                "sampled_peak_rss_bytes": peak_rss,
                "oversized": oversized,
            }
        )
    return {
        "campaign": campaign.config["digest"],
        "total": len(record["files"]),
        "sealed": len(sealed),
        "remaining": remaining,
        "failed_incomplete": len(remaining),
        "completion_percent": 100 * len(sealed) / len(record["files"]),
        "verified_units": sealed,
        "diagnostics": diagnostics,
        "yield": state["sealed_including_incomplete_batch"],
        "seconds": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--verify-immutability",
        type=Path,
        help="Compare an earlier audit's retained artifact sizes and mtimes.",
    )
    args = parser.parse_args()
    if args.verify_immutability is not None:
        earlier = driver.read_json(args.verify_immutability)
        artifacts = [item for unit in earlier["verified_units"] for item in unit["artifacts"]]
        unchanged = all(
            Path(item["path"]).stat().st_size == item["bytes"]
            and Path(item["path"]).stat().st_mtime_ns == item["mtime_ns"]
            for item in artifacts
        )
        if not unchanged:
            raise RuntimeError("sealed artifact stat changed since the full hash audit")
        result = {
            "sealed_units": len(earlier["verified_units"]),
            "artifacts": len(artifacts),
            "sizes_and_mtimes_unchanged": True,
            "hashes": "reverified by resume-check.json against the immutable receipts",
        }
        driver.write_json(args.output, result)
        print(json.dumps(result, indent=2))
        return
    campaign = driver.load_campaign(
        driver.REPO / driver.FAST_DIR / "campaign.json", Path("G:/XLM"), Path("C:/XLM-scratch")
    )
    result = audit(campaign)
    driver.write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "verified_units"}, indent=2))


if __name__ == "__main__":
    main()
