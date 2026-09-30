"""Run the production local worker on one retained Batch-3 source, privately and offline.

Calls ``essential_web_local.adapt_source_file`` with exactly the job the fast
executor builds for this unit (views, identity, plan, selection hash and
limits) on the verified local file, into a fresh private directory given by
``--work`` that is outside the operator store. Nothing is promoted, sealed or
published and no receipt is written: the executor does that on the resumed
run. The report holds counts and hashes only; ``--work`` is removed afterwards
unless ``--keep`` is given.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

fast_driver: Any = importlib.import_module("essential_web_fast")

from xlm.data.acquisition.source_parquet import file_sha256  # noqa: E402
from xlm.data.adapters.malformed import MalformedLimitError  # noqa: E402
from xlm.data.sources import essential_web_bulk as bulk  # noqa: E402
from xlm.data.sources import essential_web_local as local  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--rank", type=int, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    data_root = Path(os.environ["XLM_DATA_ROOT"])
    if args.work.resolve().is_relative_to(data_root.resolve()):
        raise SystemExit("--work must be outside the operator store")
    campaign = fast_driver.load_campaign(
        fast_driver.REPO / fast_driver.FAST_DIR / "campaign.json",
        data_root,
        Path(os.environ["XLM_SCRATCH_ROOT"]),
    )
    record = fast_driver.batch_record(campaign, args.batch)
    plan = fast_driver.load_acquisition_plan(campaign.batch_dir(args.batch) / "batch.plan.json")
    if plan.plan_hash != record["plan_hash"] or plan.selected_files != record["files"]:
        raise SystemExit("authorized plan differs from batch")
    resume = fast_driver.recovery.resume_state(campaign, record)
    pending = [p for p in resume["remaining"] if int(p["rank"]) == args.rank]
    if len(pending) != 1:
        raise SystemExit(f"rank {args.rank} is not an unsealed unit of batch {args.batch}")
    name = str(pending[0]["file"])
    key = f"f{args.rank:05d}"
    retained = fast_driver.load_durable_source(campaign.raw_path(name))
    if retained is not None:
        path, identity, location = campaign.raw_path(name), retained, "durable"
    else:
        scratch = campaign.scratch(f"b{args.batch:04d}")
        state = json.loads((scratch / f"{key}.state.json").read_text(encoding="utf-8"))
        if not state.get("complete"):
            raise SystemExit(f"{key} has no complete local copy")
        path, identity, location = scratch / f"{key}.parquet.part", state, "scratch"
    if file_sha256(path) != (identity["sha256"], identity["length"]):
        raise SystemExit(f"{key}: local file does not rehash to its identity")
    job = fast_driver.unit_job(
        campaign, plan, name, args.work, batch=args.batch, rank=args.rank, source=retained
    )
    out = args.work / key
    report: dict[str, object] = {
        "key": key,
        "source_file": name,
        "local_location": location,
        "sha256": identity["sha256"],
        "malformed_policy": getattr(local, "MALFORMED_POLICY", "frozen per-row rule, no amendment"),
    }
    started = time.monotonic()
    try:
        result = local.adapt_source_file(
            path,
            out,
            source_file=name,
            views=list(job["views"]),
            source_id=str(job["source_id"]),
            repository=str(job["repository"]),
            revision=str(job["revision"]),
            plan_id=str(job["plan_id"]),
            plan_hash=str(job["plan_hash"]),
            selection_hash=str(job["selection_hash"]),
            identity={
                "etag": identity["etag"],
                "sha256": identity["sha256"],
                "length": identity["length"],
            },
            limits=job["limits"],
        )
    except MalformedLimitError as exc:
        report.update(outcome="MalformedLimitError", reason=str(exc))
    else:
        malformed = bulk.check_conservation(int(result["rows"]), result["views"])
        report.update(
            outcome="completed",
            rows=result["rows"],
            malformed_rows=malformed,
            malformed_fraction=malformed / int(result["rows"]),
            selected_records_sha256=result["selected_records_sha256"],
            views={
                view: {
                    "documents": entry["documents"],
                    "canonical_bytes": entry["canonical_bytes"],
                    "estimated_tokens_4_bytes": entry["canonical_bytes"] / 4,
                    "documents_sha256": entry["documents_sha256"],
                    "rejections_sha256": entry["rejections_sha256"],
                    "rejection_counts_by_code": entry["rejection_counts_by_code"],
                }
                for view, entry in result["views"].items()
            },
        )
    report["seconds"] = round(time.monotonic() - started, 3)
    if not args.keep:
        shutil.rmtree(args.work, ignore_errors=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["outcome"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
