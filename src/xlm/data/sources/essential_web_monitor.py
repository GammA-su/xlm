"""Connect parent-process telemetry and atomic worker counters to the dashboard."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, TextIO

import pyarrow.parquet as pq

from xlm.data.acquisition.source_parquet import ScratchBudget, TransferMeter
from xlm.data.sources.essential_web_local import Unit
from xlm.data.sources.essential_web_progress import Dashboard, Snapshot


class Monitor:
    def __init__(
        self,
        campaign: Any,
        batch: int,
        resume: dict[str, Any],
        units: list[Unit],
        scratch: ScratchBudget,
        meter: TransferMeter,
        stream: TextIO,
        cumulative_views: dict[str, Any],
    ) -> None:
        self.campaign, self.batch = campaign, batch
        self.resume, self.units = resume, units
        self.scratch, self.meter = scratch, meter
        self.receipts: dict[str, dict[str, Any]] = {}
        self.views = {k: dict(v) for k, v in cumulative_views.items()}
        self.dashboard = Dashboard(stream, campaign.batch_dir(batch) / "events.jsonl")
        self.status: dict[str, Any] = {}
        self.rows: dict[str, int] = {}
        self.lengths: dict[str, int] = {}
        self.failed: set[str] = set()
        self.warned: set[str] = set()
        self.retries_seen = 0
        self.last = -1.0
        for unit in units:
            if unit.identity_record is not None and unit.job is not None:
                self.lengths[unit.key] = int(unit.identity_record["length"])
                with pq.ParquetFile(unit.job["source_path"]) as parquet:
                    self.rows[unit.key] = int(parquet.metadata.num_rows)
        self.dashboard.event(
            "resume" if resume["sealed"] else "run",
            batch=batch,
            sealed=resume["sealed"],
            total=resume["total"],
            remaining=resume["scheduled"],
            completion_percent=resume["completion_percent"],
        )
        self.update({}, force=True)

    def sealed(self, key: str, receipt: dict[str, Any]) -> None:
        self.receipts[key] = receipt
        self.dashboard.event(
            "sealed",
            key=key,
            rows=receipt["rows"],
            views={
                k: {"documents": v["documents"], "estimated_tokens": v["canonical_bytes"] / 4}
                for k, v in receipt["views"].items()
            },
        )
        self.update({}, force=True)

    def update(self, status: dict[str, Any], *, force: bool = False) -> None:
        if "failed" in status:
            key = str(status["failed"])
            if key not in self.failed:
                self.failed.add(key)
                self.dashboard.event("failed", key=key, exception=status["exception"])
            force = True
        else:
            self.status.update(status)
        now = time.monotonic()
        if not force and now - self.last < 1:
            return
        self.last = now
        received, retries = self.meter.telemetry()
        for name, attempt in retries[self.retries_seen :]:
            self.dashboard.event("retry", file=name, attempt=attempt)
        self.retries_seen = len(retries)
        old = self.resume["receipts"]
        snapshot = Snapshot(
            self.batch,
            self.campaign.config["digest"],
            "RESUME" if old else "RUN",
            self.resume["total"],
            len(old) + len(self.receipts),
        )
        snapshot.views = {k: dict(v) for k, v in self.views.items()}
        snapshot.targets = {
            k: v["first_pass_estimated_token_target"]
            for k, v in self.campaign.config["stop"]["targets"].items()
        }
        snapshot.processed = sum(r["rows"] for r in old)
        snapshot.downloaded = sum(r["raw_bytes"] for r in old)
        snapshot.durable = sum(r["footprint_bytes"] for r in old)
        snapshot.malformed = sum(r["malformed_rows"] for r in old)
        for unit in self.units:
            value: dict[str, Any] = {}
            if unit.key in self.receipts:
                receipt = self.receipts[unit.key]
                value = {
                    "rows": receipt["rows"],
                    "total": receipt["rows"],
                    "malformed": receipt["malformed_rows"],
                    "views": receipt["views"],
                }
                snapshot.durable += receipt["footprint_bytes"]
                snapshot.downloaded += receipt["raw_bytes"]
                self.lengths[unit.key] = receipt["raw_bytes"]
            else:
                if unit.state.is_file():
                    state = json.loads(unit.state.read_bytes())
                    if state.get("length") is not None:
                        self.lengths[unit.key] = int(state["length"])
                if unit.identity_record is not None:
                    snapshot.downloaded += int(unit.identity_record["length"])
                elif unit.partial.is_file():
                    snapshot.downloaded += unit.partial.stat().st_size
                if unit.job is not None:
                    progress = Path(unit.job["progress_path"])
                    if progress.is_file():
                        value = json.loads(progress.read_bytes())
                    durable = self.campaign.raw_path(unit.source_file)
                    if durable.is_file():
                        snapshot.durable += durable.stat().st_size
            if value:
                self.rows[unit.key] = int(value["total"])
                snapshot.processed += int(value["rows"])
                snapshot.work_rows += int(value["rows"])
                snapshot.malformed += int(value["malformed"])
                if value["malformed"] and unit.key not in self.warned:
                    self.warned.add(unit.key)
                    self.dashboard.event(
                        "warning",
                        key=unit.key,
                        reason="malformed rows quarantined",
                        count=value["malformed"],
                    )
                if unit.key in self.receipts:
                    for name, view in value["views"].items():
                        for field in ("documents", "canonical_bytes"):
                            snapshot.views[name][field] += int(view[field])
        if len(self.lengths) == len(self.units):
            snapshot.expected_bytes = sum(r["raw_bytes"] for r in old) + sum(self.lengths.values())
        if len(self.rows) == len(self.units):
            snapshot.known_rows = sum(r["rows"] for r in old) + sum(self.rows.values())
        snapshot.transfer_bytes = received
        snapshot.downloading = list(self.status.get("downloading", []))
        snapshot.processing = list(self.status.get("processing", []))
        snapshot.process_workers = int(self.status.get("process_workers", 0))
        snapshot.backlog = int(self.status.get("backlog", 0))
        snapshot.retries, snapshot.failed = len(retries), len(self.failed)
        snapshot.scratch, snapshot.scratch_cap = self.scratch.occupied(), self.scratch.cap_bytes
        snapshot.free = shutil.disk_usage(self.campaign.root).free
        for path in self.campaign.staging(self.batch).rglob("*"):
            if path.is_file():
                snapshot.durable += path.stat().st_size
        self.dashboard.update(snapshot, now=now, force=force)
