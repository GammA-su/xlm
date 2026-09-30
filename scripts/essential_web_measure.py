"""Offline operational measurement over the frozen shared probe/calibration outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import mix01_inventory

from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.progress import AcquisitionState
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.sources import essential_web_readiness as ready


def bounded_json(path: Path) -> Any:
    return mix01_inventory._read_bounded_json(path, 8 * ready.MIB, "operational evidence")


def lines(path: Path, limit: int) -> Any:
    if path.stat().st_size > limit:
        raise ValueError("operational input exceeds frozen byte cap")
    with path.open("rb") as stream:
        while raw := stream.readline(ready.MIB + 1):
            if len(raw) > ready.MIB:
                raise ValueError("operational row exceeds line cap")
            yield json.loads(raw)


def measure(root: Path, freeze: Path, stage: str) -> dict[str, Any]:
    windows = bounded_json(freeze / "calibration-plan.json")["windows"]
    if stage == "probe":
        windows = windows[:1]
    frozen_selector = selector.FrozenEssentialWebSelector.load()
    totals: Counter[str] = Counter()
    retained: dict[str, Counter[str]] = {v: Counter() for v in selector.ADMITTED_COMPONENTS}
    rows = scanned = transferred = decompressed = malformed = 0
    elapsed = 0.0
    units = []
    for i, window in enumerate(windows):
        tag = f"{stage}-{i:02d}"
        plan = load_acquisition_plan(root / f"{tag}.plan.json")
        frozen_plan = load_acquisition_plan(freeze / f"{tag}.plan.json")
        if plan.compute_behavioral_hash() != frozen_plan.compute_behavioral_hash():
            raise ValueError("executed plan differs from frozen operational window")
        unit_root = root / tag
        state = AcquisitionState.model_validate(
            bounded_json(unit_root / "scratch/journals" / f"{plan.plan_id}.progress.json")
        )
        if state.plan_hash != plan.plan_hash or state.status != "COMPLETED":
            raise ValueError("operational fetch is incomplete or has wrong identity")
        if state.source_validators.get(window["file"]) != {
            "etag": window["strong_etag"],
            "length": window["remote_length"],
        }:
            raise ValueError("operational source identity differs from frozen footer evidence")
        raw_path = unit_root / "raw/selected_records.jsonl"
        if raw_path.stat().st_size > 256 * ready.MIB:
            raise ValueError("raw calibration exceeds adaptation cap")
        progress = state.file_progress["selected_records.jsonl"]
        with raw_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != progress.content_sha256:
            raise ValueError("operational raw payload hash mismatch")
        n = 0
        for record in lines(raw_path, 256 * ready.MIB):
            loc = record["_xlm_acquisition"]
            if (
                loc["source_file"] != window["file"]
                or loc["row_index"] != n
                or loc["revision"] != selector.SOURCE_REVISION
                or loc["selection_hash"] not in plan.accepted_selection_hashes()
            ):
                raise ValueError("operational row identity/accounting mismatch")
            totals[frozen_selector.decide(record).final] += 1
            n += 1
        expected = 256 if stage == "probe" else 2048
        if n != expected or state.records_acquired != n:
            raise ValueError("operational row count differs from frozen window")
        shared_bad: int | None = None
        for view in selector.ADMITTED_COMPONENTS:
            directory = unit_root / view
            measured = mix01_inventory.measure_unit(
                plan_path=root / f"{tag}.plan.json",
                scratch_dir=unit_root / "scratch",
                canonical_dir=directory,
                source="essential_web",
                view="essential_science",
                revision=selector.SOURCE_REVISION,
                allow_empty=True,
            )
            summary = bounded_json(directory / "adaptation_summary.json")
            if summary["adapter_id"] != "essential_web_bnormal":
                raise ValueError("operational canonical data has wrong adapter")
            count = summary["rejection_counts_by_code"].get("EssentialWebMalformedRowError", 0)
            if shared_bad is not None and count != shared_bad:
                raise ValueError("views disagree on malformed rows")
            shared_bad = count
            characters = 0
            for document in lines(directory / "documents.jsonl", 256 * ready.MIB):
                metadata = document["source_metadata"]
                if (
                    metadata["mix01_component"] != view
                    or metadata["essential_web_selector_policy_digest"] != selector.POLICY_DIGEST
                ):
                    raise ValueError("retained document has wrong component/selector")
                characters += len(document["text"])
            retained[view].update(
                documents=measured["accepted_records"],
                canonical_bytes=measured["canonical_bytes"],
                characters=characters,
            )
        malformed += shared_bad or 0
        rows += n
        scanned += state.accounting.consumed["records_scanned"]
        transferred += state.transferred_bytes
        decompressed += state.decompressed_bytes
        # Journal elapsed includes downtime/restarts, explicitly disclosed below.
        elapsed += (
            datetime.fromisoformat(state.updated_at) - datetime.fromisoformat(state.started_at)
        ).total_seconds()
        units.append(
            {
                "file": window["file"],
                "crawl": window["crawl"],
                "raw_sha256": digest,
                "plan_hash": plan.plan_hash,
                "resource_account": state.accounting.model_dump(),
            }
        )
    total_docs = sum(c["documents"] for c in retained.values())
    total_bytes = sum(c["canonical_bytes"] for c in retained.values())
    costs = {}
    for view, values in retained.items():
        docs, canonical_bytes = values["documents"], values["canonical_bytes"]
        costs[view] = {
            **dict(values),
            "estimated_tokens": canonical_bytes / 4,
            "estimated_tokens_low_high": [canonical_bytes / 5, canonical_bytes / 3],
            "scanned_rows_per_retained_document": scanned / docs if docs else None,
            "transfer_bytes_per_retained_document": transferred / docs if docs else None,
            "transfer_bytes_per_canonical_byte": transferred / canonical_bytes
            if canonical_bytes
            else None,
            "transfer_bytes_per_estimated_token": transferred / (canonical_bytes / 4)
            if canonical_bytes
            else None,
        }
    return {
        "binding": ready.source_binding(),
        "stage": stage,
        "input_rows": rows,
        "scanned_rows": scanned,
        "files": len(windows),
        "crawls": len(windows),
        "physical_response_body_bytes": transferred,
        "decompressed_bytes": decompressed,
        "elapsed_wall_seconds_including_restart_downtime": elapsed,
        "selector_counts": {key: totals[key] for key in selector.FINAL_COMPONENTS},
        "malformed_rows": malformed,
        "retention_and_cost": costs,
        "total_retained_documents": total_docs,
        "total_canonical_bytes": total_bytes,
        "units": units,
        "shared_transfer_counted_once": True,
        "cost_per_component": "each component bears full shared transfer for bottleneck sizing",
        "token_method": "mix01_inventory: assumed UTF-8 bytes/token 3/4/5; estimated tokens only",
        "peak_process_memory": None,
        "peak_process_memory_status": "NOT MEASURED by journal; use runtime performance artifact",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--stage", choices=("probe", "calibration"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = measure(args.root, args.freeze, args.stage)
    mix01_inventory._atomic_write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
