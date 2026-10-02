"""Deterministic C05 storage admission: hard, derived and monitored limits.

Every file a C05 run may create has an upper bound fixed by the plan:

* HARD: enforced by the writing mechanism itself before bytes land -- SQLite
  ``max_page_count`` for the facts database, and byte-counted writers for private
  decisions, kept membership, completion envelopes and signed state.
* DERIVED: the rollback journal. In ``journal_mode=DELETE`` SQLite journals each
  page that existed when the transaction began at most once, plus one sector-sized
  header (and at most one sector of alignment padding) per journal sync. With the
  page cap fixed, ``header + pages * (record + 2 * header)`` bounds it; the header
  and record sizes are measured on the actual scratch volume and bound in the plan.
* MONITORED: free space consumed by *other* processes, filesystem metadata and
  process RSS. These are sampled; a sample can miss a short peak.

The plan refuses when the worst-case sum exceeds the reviewed aggregate scratch cap,
and a run refuses before starting when any volume cannot hold the remaining growth
plus the reviewed free-space reserve. Nothing here widens a bound automatically.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

from pydantic import Field

from xlm.data.exclusion.policy import C05Error, FrozenModel, Resources

PAGE_SIZE: Final = 4096
STATE_BYTES = 256 * 1024
COMPLETION_BYTES = 4 * 1024**2
LOCK_BYTES = 4096
# Allocation rounding per file: NTFS/ext4 clusters are at most 2 MiB.
FILE_SLACK_BYTES = 2 * 1024**2
SCRATCH_FILES = frozenset(
    {"facts.sqlite", "facts.sqlite-journal", "state.json", "state.json.tmp", "decisions.jsonl"}
)
LOCK_FILES = frozenset({"run.lock"})
STAGED_FILES = frozenset({"membership.jsonl", "completion.json", "completion.json.tmp"})


class StorageGeometry(FrozenModel):
    """Measured SQLite rollback-journal geometry of the scratch volume."""

    kind: Literal["c05-sqlite-geometry-v1"] = "c05-sqlite-geometry-v1"
    sqlite_version: str = Field(min_length=1)
    page_size: Literal[4096] = PAGE_SIZE
    journal_mode: Literal["delete"] = "delete"
    journal_header_bytes: int = Field(ge=32, le=65536)
    journal_record_bytes: int = Field(ge=PAGE_SIZE + 8, le=PAGE_SIZE + 8)


def probe_geometry(directory: Path) -> StorageGeometry:
    """Measure the journal header/record sizes with a private four-page fixture.

    The header is the journal size after one journaled page minus one record; the
    record is the growth from each further page. Unexpected values refuse.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".c05-geometry-{uuid.uuid4().hex}.sqlite"
    journal = Path(str(path) + "-journal")
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute(f"PRAGMA page_size={PAGE_SIZE}")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE fixture(value BLOB)")
        db.execute("BEGIN")
        for _ in range(8):
            db.execute("INSERT INTO fixture VALUES(?)", (b"a" * 3000,))
        db.execute("COMMIT")
        sizes = []
        db.execute("BEGIN")
        for row in (1, 3, 5, 7):
            db.execute("UPDATE fixture SET value=? WHERE rowid=?", (b"b" * 3000, row))
            sizes.append(journal.stat().st_size)
        db.execute("ROLLBACK")
        version = sqlite3.sqlite_version
    finally:
        db.close()
        path.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)
    steps = {b - a for a, b in zip(sizes, sizes[1:], strict=False)}
    if len(steps) != 1:
        raise C05Error("SQLite journal growth is not one record per page")
    record = steps.pop()
    return StorageGeometry(
        sqlite_version=version,
        journal_header_bytes=sizes[0] - record,
        journal_record_bytes=record,
    )


def journal_bound(geometry: StorageGeometry, database_bytes: int) -> int:
    pages = database_bytes // geometry.page_size
    header = geometry.journal_header_bytes
    return header + pages * (geometry.journal_record_bytes + 2 * header)


def storage_bounds(resources: Resources, geometry: StorageGeometry) -> dict[str, int]:
    """Worst-case simultaneous bytes of every C05 artifact, crash leftovers included.

    Staged membership is published by a same-directory rename, so it is counted once.
    Atomic JSON replacement briefly holds the old file and its ``.tmp`` together.
    A hot journal or partial staging left by a crash is within the same bounds.
    """
    database = (resources.index_bytes // geometry.page_size) * geometry.page_size
    files = len(SCRATCH_FILES) + len(LOCK_FILES) + len(STAGED_FILES) + 1
    return {
        "facts_sqlite": database,
        "facts_rollback_journal": journal_bound(geometry, database),
        "private_decisions": resources.decision_bytes,
        "membership_staging_and_publication": resources.output_bytes,
        "completion_envelopes": 2 * COMPLETION_BYTES,
        "signed_state": 2 * STATE_BYTES,
        "locks": len(LOCK_FILES) * LOCK_BYTES,
        "benchmark_index": resources.benchmark_bytes,
        "allocation_slack": files * FILE_SLACK_BYTES,
    }


def admit_plan(resources: Resources, geometry: StorageGeometry) -> dict[str, Any]:
    """Refuse a plan whose reviewed ceilings cannot contain its own worst case."""
    bounds = storage_bounds(resources, geometry)
    if bounds["facts_rollback_journal"] > resources.journal_bytes:
        raise C05Error(
            "reviewed journal ceiling is below the derived rollback-journal bound "
            f"({bounds['facts_rollback_journal']} B); no automatic widening"
        )
    total = sum(bounds.values())
    if total > resources.scratch_bytes:
        raise C05Error(
            f"worst-case aggregate storage {total} B exceeds the reviewed scratch ceiling; "
            "no automatic widening"
        )
    return {"bounds": bounds, "worst_case_bytes": total, "geometry": geometry.model_dump()}


class Component(NamedTuple):
    name: str
    path: Path
    bound: int
    current: int


def _size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def _volume(path: Path) -> Path:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return probe


def runtime_components(
    resources: Resources,
    geometry: StorageGeometry,
    work: Path,
    output: Path,
    identity: str,
    index: Path,
) -> list[Component]:
    bounds = storage_bounds(resources, geometry)
    staged = output / (identity + ".partial")
    final = output / identity
    published = staged if staged.exists() else final
    return [
        Component("facts_sqlite", work, bounds["facts_sqlite"], _size(work / "facts.sqlite")),
        Component(
            "facts_rollback_journal",
            work,
            bounds["facts_rollback_journal"],
            _size(work / "facts.sqlite-journal"),
        ),
        Component(
            "private_decisions",
            work,
            bounds["private_decisions"],
            _size(work / "decisions.jsonl"),
        ),
        Component(
            "signed_state",
            work,
            bounds["signed_state"],
            _size(work / "state.json") + _size(work / "state.json.tmp"),
        ),
        Component("locks", work, bounds["locks"], _size(work / "run.lock")),
        Component(
            "membership_staging_and_publication",
            output,
            bounds["membership_staging_and_publication"],
            _size(published / "membership.jsonl"),
        ),
        Component(
            "completion_envelopes",
            output,
            bounds["completion_envelopes"],
            _size(published / "completion.json") + _size(published / "completion.json.tmp"),
        ),
        Component("benchmark_index", index, bounds["benchmark_index"], bounds["benchmark_index"]),
        Component("allocation_slack", work, bounds["allocation_slack"], 0),
    ]


def unexpected_entries(work: Path, output: Path, identity: str) -> list[str]:
    """Names outside the accounted set: WAL/SHM, sort spills, foreign files."""
    found: list[str] = []
    if work.is_dir():
        for entry in work.iterdir():
            if entry.name not in SCRATCH_FILES | LOCK_FILES or not entry.is_file():
                found.append(entry.name)
    for directory in (output / (identity + ".partial"), output / identity):
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.name not in STAGED_FILES or not entry.is_file():
                    found.append(directory.name + "/" + entry.name)
    return sorted(found)


def admit_runtime(
    resources: Resources,
    geometry: StorageGeometry,
    work: Path,
    output: Path,
    identity: str,
    index: Path,
    *,
    disk_usage: Callable[[Path], Any] | None = None,
    probe: Callable[[Path], StorageGeometry] | None = None,
) -> dict[str, Any]:
    """Fail closed before work if any volume cannot hold the remaining growth.

    Existing bytes (a resumed run's database, hot journal or partial staging) are
    already allocated; only ``bound - current`` must still be free, plus the reviewed
    reserve on every volume. The geometry is re-measured beside the scratch root.
    """
    admit_plan(resources, geometry)
    usage = disk_usage or shutil.disk_usage
    measure = probe or probe_geometry
    # The probe file lives in the scratch root, never inside the accounted job directory.
    if measure(work.parent) != geometry:
        raise C05Error("scratch SQLite journal geometry differs from the reviewed plan")
    return physical_reserve(
        resources, geometry, work, output, identity, index, disk_usage=usage, when="starting"
    )


def physical_reserve(
    resources: Resources,
    geometry: StorageGeometry,
    work: Path,
    output: Path,
    identity: str,
    index: Path,
    *,
    disk_usage: Callable[[Path], Any] | None = None,
    when: str = "continuing",
) -> dict[str, Any]:
    """Every volume must hold its remaining worst-case growth plus the reserve.

    Used at admission and, sampled, during the run: space consumed by another
    process then refuses before this job could exhaust the volume.
    """
    usage = disk_usage or shutil.disk_usage
    foreign = unexpected_entries(work, output, identity)
    if foreign:
        raise C05Error("unaccounted C05 scratch/staging entries: " + ", ".join(foreign))
    components = runtime_components(resources, geometry, work, output, identity, index)
    volumes: dict[str, dict[str, Any]] = {}
    for item in components:
        if item.current > item.bound:
            raise C05Error(f"existing {item.name} exceeds its hard bound")
        root = _volume(item.path)
        # Group by filesystem device, not path anchor: a mount point (POSIX, or a
        # Windows folder mount) shares the anchor but has its own free space.
        anchor = f"{Path(root.anchor or root).resolve()} (device {os.stat(root).st_dev})"
        volume = volumes.setdefault(anchor, {"path": root, "growth": 0, "present": 0})
        volume["growth"] += item.bound - item.current
        volume["present"] += item.current
    report: dict[str, Any] = {}
    for anchor, volume in sorted(volumes.items()):
        free = int(usage(volume["path"]).free)
        required = volume["growth"] + resources.free_bytes
        if free < required:
            raise C05Error(
                f"insufficient physical reserve on {anchor}: {free} B free, "
                f"{required} B required before {when}"
            )
        report[anchor] = {
            "free_bytes": free,
            "required_bytes": required,
            "remaining_growth_bytes": volume["growth"],
            "present_bytes": volume["present"],
        }
    return {
        "volumes": report,
        "worst_case_bytes": sum(c.bound for c in components),
        "present_bytes": sum(c.current for c in components),
    }


def bounded_bytes(raw: bytes, ceiling: int, what: str) -> bytes:
    if len(raw) > ceiling:
        raise C05Error(f"{what} exceeds its hard byte ceiling")
    return raw


def summary(bounds: Mapping[str, int]) -> dict[str, Any]:
    return {
        "hard": [
            "facts_sqlite",
            "private_decisions",
            "membership_staging_and_publication",
            "completion_envelopes",
            "signed_state",
        ],
        "derived": ["facts_rollback_journal"],
        "fixed_input": ["benchmark_index"],
        "reserved": ["locks", "allocation_slack"],
        "monitored": ["other-process free-space consumption", "process-tree RSS"],
        "bounds": dict(bounds),
    }
