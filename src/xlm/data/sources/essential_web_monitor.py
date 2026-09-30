"""Connect parent-process telemetry and atomic worker counters to the dashboard."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, TextIO

import pyarrow.parquet as pq

from xlm.data.acquisition.source_parquet import ScratchBudget, TransferMeter, identity_path
from xlm.data.sources.essential_web_local import Unit, read_progress
from xlm.data.sources.essential_web_progress import Dashboard, Snapshot, percentage

LOCAL, RESUMABLE, NETWORK = "local", "resumable", "network"


class ObservedScratch(ScratchBudget):
    """A scratch budget that also tells the monitor which download streams exist.

    A download thread replaces its ``state.json`` at every checkpoint; on
    Windows that replace fails while any reader holds the file open. Every
    stream reserves its key here, in the calling thread, before it starts, so a
    key never reserved in this run has no writer and its state file is safe to
    read. The declared length of a live stream arrives through ``shrink``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.streamed: set[str] = set()
        self.declared: dict[str, int] = {}

    def reserve(self, key: str, amount: int, path: Path) -> bool:
        reserved = super().reserve(key, amount, path)
        if reserved:
            self.streamed.add(key)
        return reserved

    def shrink(self, key: str, amount: int) -> None:
        super().shrink(key, amount)
        self.declared[key] = amount


def restart_class(state: dict[str, Any] | None, durable: bool) -> str:
    """What a restart needs for one unsealed unit, from its retained evidence."""
    if durable or (state is not None and state.get("complete")):
        return LOCAL
    if state is not None and int(state.get("verified_bytes", 0)) > 0:
        return RESUMABLE
    return NETWORK


class Monitor:
    def __init__(
        self,
        campaign: Any,
        batch: int,
        resume: dict[str, Any],
        units: list[Unit],
        scratch: ObservedScratch,
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
        self.progress: dict[str, dict[str, Any]] = {}
        self.failed: set[str] = set()
        self.failures: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
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

    def _length(self, unit: Unit) -> None:
        """Learn a unit's declared length without opening a state file a stream replaces."""
        if unit.key in self.scratch.streamed:
            declared = self.scratch.declared.get(unit.key)
            if declared is not None:
                self.lengths[unit.key] = declared
        elif unit.state.is_file():
            state = json.loads(unit.state.read_bytes())
            if state.get("length") is not None:
                self.lengths[unit.key] = int(state["length"])

    def update(self, status: dict[str, Any], *, force: bool = False) -> None:
        if "failed" in status:
            key = str(status["failed"])
            if key not in self.failed:
                self.failed.add(key)
                detail = {k: v for k, v in status.items() if k != "failed" and v is not None}
                self.failures.append({"key": key, **detail})
                self.dashboard.event("failed", key=key, **detail)
            force = True
        elif "cancelled" in status:
            key = str(status["cancelled"])
            if key not in self.cancelled:
                self.cancelled.append(key)
                root = self.failures[0]["key"] if self.failures else None
                self.dashboard.event("cancelled", key=key, cause=root)
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
                self._length(unit)
                if unit.identity_record is not None:
                    snapshot.downloaded += int(unit.identity_record["length"])
                elif unit.partial.is_file():
                    snapshot.downloaded += unit.partial.stat().st_size
                if unit.job is not None:
                    latest = read_progress(Path(unit.job["progress_path"]))
                    if latest is not None:
                        self.progress[unit.key] = latest
                    # A snapshot being replaced this instant keeps its previous value.
                    value = self.progress.get(unit.key, {})
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
        snapshot.cancelled = len(self.cancelled)
        snapshot.scratch, snapshot.scratch_cap = self.scratch.occupied(), self.scratch.cap_bytes
        snapshot.free = shutil.disk_usage(self.campaign.root).free
        for path in self.campaign.staging(self.batch).rglob("*"):
            if path.is_file():
                snapshot.durable += path.stat().st_size
        self.dashboard.update(snapshot, now=now, force=force)

    def restart(self) -> dict[str, Any]:
        """Restart classes of the unsealed units, from files every stream has stopped writing.

        Called only after the pipeline returned or raised, when no stream or
        worker is left. The restart re-verifies each class before using it.
        """
        classes: dict[str, list[str]] = {LOCAL: [], RESUMABLE: [], NETWORK: []}
        kept = 0
        for unit in self.units:
            if unit.key in self.receipts:
                continue
            state = json.loads(unit.state.read_bytes()) if unit.state.is_file() else None
            durable = unit.identity_record is not None or (
                identity_path(self.campaign.raw_path(unit.source_file)).is_file()
                and self.campaign.raw_path(unit.source_file).is_file()
            )
            kind = restart_class(state, durable)
            classes[kind].append(unit.key)
            if kind == RESUMABLE and state is not None:
                kept += int(state["verified_bytes"])
        return {**classes, "resumable_verified_bytes": kept}

    def fatal(self, error: BaseException) -> list[str]:
        """Separate the root failure from its consequences; say what a restart will do."""
        sealed = len(self.resume["receipts"]) + len(self.receipts)
        total = int(self.resume["total"])
        restart = self.restart()
        root = self.failures[0] if self.failures else {"key": "pipeline", "exception": None}
        exception = root.get("exception") or type(error).__name__
        codes = " ".join(
            f"{name}={root[name]}" for name in ("errno", "winerror") if root.get(name) is not None
        )
        lines = ["ROOT FAILURE", f"  {root['key']} {exception} {codes}".rstrip()]
        if root.get("site"):
            lines.append(f"  at {root['site']}")
        if root.get("path2"):
            lines.append(f"  operation: replace {root.get('path')} -> {root['path2']}")
        elif root.get("path"):
            lines.append(f"  file: {root['path']}")
        if root.get("reason"):
            lines.append(f"  {root['reason']}")
        lines += [
            "CANCELLED",
            f"  {len(self.cancelled)} in-flight units cancelled because of the root failure"
            + (f": {', '.join(self.cancelled)}" if self.cancelled else ""),
        ]
        others = [f"{f['key']} {f.get('exception')}" for f in self.failures[1:]]
        if others:
            lines += ["OTHER FAILURES", f"  {len(others)}: {', '.join(others)}"]
        lines += [
            "PRESERVED",
            f"  {sealed} / {total} sealed ({percentage(sealed, total)}); "
            f"{len(self.receipts)} sealed in this run",
            "RESTART",
            f"  {total - sealed} remaining",
            f"  {len(restart[LOCAL])} reusable locally (retained source or complete scratch)",
            f"  {len(restart[RESUMABLE])} resumable "
            f"({restart['resumable_verified_bytes']:,} verified bytes kept)",
            f"  {len(restart[NETWORK])} require a fresh download",
            "  resume-check verifies these before any run; sealed units are never redone",
        ]
        self.dashboard.event(
            "fatal",
            root={k: v for k, v in root.items()},
            cancelled=self.cancelled,
            other_failures=[f["key"] for f in self.failures[1:]],
            sealed=sealed,
            total=total,
            restart=restart,
        )
        self.dashboard.clear()
        self.dashboard.stream.write("\n".join(lines) + "\n")
        self.dashboard.stream.flush()
        return lines
