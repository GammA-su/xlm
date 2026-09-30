"""Offline acceptance replay of ONLY the unsealed file; publishes no campaign unit."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import essential_web_bulk as historical_tool
import essential_web_fast as driver
import mix01_inventory
import psutil

from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.source_parquet import load_durable_source
from xlm.data.sources import essential_web_local as local


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("offline replay forbids socket connections")

    socket.socket.connect = refused  # type: ignore[method-assign]
    campaign = driver.load_campaign(
        driver.REPO / driver.FAST_DIR / "campaign.json", Path("G:/XLM"), Path("C:/XLM-scratch")
    )
    amendment = campaign.recovery
    if amendment is None or args.work.exists():
        raise RuntimeError("replay requires a frozen amendment and a fresh private directory")
    name = amendment["file"]
    source = campaign.raw_path(name)
    identity = load_durable_source(source)
    if identity is None or identity["sha256"] != amendment["source_sha256"]:
        raise RuntimeError("replay source differs")
    if (campaign.unit_dir(0, 26) / local.RECEIPT_FILENAME).exists():
        raise RuntimeError("replay refuses to process an already sealed unit")
    plan = load_acquisition_plan(campaign.batch_dir(0) / "batch.plan.json")
    job = driver.unit_job(campaign, plan, name, args.work)
    stop = threading.Event()
    samples: list[int] = []

    def sample() -> None:
        while not stop.wait(0.1):
            samples.append(psutil.Process().memory_info().rss)

    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    started = time.monotonic()
    try:
        result = local.adapt_source_file(
            source,
            args.work,
            source_file=name,
            views=job["views"],
            source_id=plan.source_id,
            repository=plan.repository,
            revision=plan.revision,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            selection_hash=plan.compute_selection_hash(),
            identity=identity,
            limits=job["limits"],
        )
        for view, value in result["views"].items():
            documents = args.work / view / "documents.jsonl"
            if mix01_inventory.scan_canonical(documents, require_text=True) != (
                value["documents"],
                value["canonical_bytes"],
                value["documents_sha256"],
            ):
                raise RuntimeError("canonical reconciliation failed")
            historical_tool.check_documents(documents, view, amendment["max_record_bytes"] + 65536)
        result.update(
            wall_seconds=time.monotonic() - started,
            sampled_peak_rss_bytes=max(samples, default=0),
            output_bytes=sum(p.stat().st_size for p in args.work.rglob("*") if p.is_file()),
            published=False,
            network_connections=0,
        )
        driver.write_json(args.output, result)
        print(json.dumps(result, indent=2))
    finally:
        stop.set()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
