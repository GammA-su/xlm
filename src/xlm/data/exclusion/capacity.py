"""Deterministic C05 storage admission: hard, derived and monitored limits.

Every file a C05 run may create has an upper bound fixed by the plan:

* HARD: enforced by the writer before bytes land -- the working-index ledger
  (``index_bytes``) over every fact unit, unit staging, grouping artifact and
  sort spill of the compact engine; byte-counted writers for private decisions,
  kept membership, completion envelopes, the group seal and signed state; and,
  only with heuristic review enabled, SQLite ``max_page_count`` for the review
  store.
* DERIVED: the compiled exact matcher (``compact.compiled_bound`` of the reviewed
  ``benchmark_bytes``/``benchmark_patterns``; staging and published copies never
  coexist), the review store's rollback journal (``journal_mode=DELETE``
  journals each original page at most once plus sector headers, bounded by
  ``header + pages * (record + 2 * header)`` with geometry measured on the scratch
  volume), and per-file allocation slack.
* MONITORED: free space consumed by *other* processes, filesystem metadata and
  process RSS. These are sampled; a sample can miss a short peak.

The plan refuses when the worst-case sum exceeds the reviewed aggregate scratch cap,
and a run refuses before starting when any volume cannot hold the remaining growth
plus the reviewed free-space reserve. Nothing here widens a bound automatically.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

from pydantic import Field

from xlm.data.exclusion.compact import COMPILED_ENTRIES, MATCHER_DIR, STAGING_DIR, compiled_bound
from xlm.data.exclusion.policy import C05Error, FrozenModel, Resources

PAGE_SIZE: Final = 4096
STATE_BYTES = 256 * 1024
COMPLETION_BYTES = 4 * 1024**2
SEAL_BYTES = 256 * 1024
LOCK_BYTES = 4096
# Allocation rounding per file: NTFS/ext4 clusters are at most 2 MiB.
FILE_SLACK_BYTES = 2 * 1024**2
#: External-sort spill runs a grouping pass may hold at once (refused beyond).
MAX_SORT_RUNS: Final = 16
FACTS_DIR: Final = "facts"
GROUP_DIR: Final = "group"
REVIEW_DB: Final = "review.sqlite"
SCRATCH_FILES = frozenset(
    {
        "state.json",
        "state.json.tmp",
        "decisions.jsonl",
        "seal.json",
        "seal.json.tmp",
        REVIEW_DB,
        REVIEW_DB + "-journal",
    }
)
SCRATCH_DIRS = frozenset({FACTS_DIR, GROUP_DIR})
LOCK_FILES = frozenset({"run.lock"})
STAGED_FILES = frozenset({"membership.jsonl", "completion.json", "completion.json.tmp"})
COMPILED_DIRS = frozenset({MATCHER_DIR, STAGING_DIR})
#: Group directory: sealed arrays plus the transient near/sort work directories.
GROUP_FILES = frozenset(
    {
        "ids.bin",
        "ids.off",
        "where.u32",
        "dup.u32",
        "fam.u32",
        "survivor.u8",
        "families.u32",
        "fam_ordering.b32",
        "fam_bytes.i64",
        "fam_hit.u8",
        "fam_split.u8",
        "fam_quick.u8",
    }
)
GROUP_WORK_DIRS = frozenset({"near", "sort"})
NEAR_FILES = frozenset({"postings.bin", "members.bin"})
UNIT_SECTIONS: Final = 18  # 17 sections + optional review, staged before assembly
_UNIT = re.compile(r"^\d{5}\.unit$")
_UNIT_STAGING = re.compile(r"^\d{5}\.(staging|unit\.staging)$")
_RUN = re.compile(r"^run-\d{5}\.bin$")
#: Files that may coexist besides one unit per plan file: scratch, group, near,
#: staged unit sections, one assembled unit, publication and the lock.
FIXED_FILES: Final = (
    len(SCRATCH_FILES)
    + len(GROUP_FILES)
    + len(NEAR_FILES)
    + UNIT_SECTIONS
    + 1
    + len(STAGED_FILES)
    + len(LOCK_FILES)
)


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


def storage_bounds(
    resources: Resources,
    geometry: StorageGeometry,
    *,
    review: bool = True,
    files: int | None = None,
) -> dict[str, int]:
    """Worst-case simultaneous bytes of every C05 artifact, crash leftovers included.

    ``files`` is the number of plan input files (one fact unit each; the reviewed
    ``resources.files`` ceiling when unknown). ``review`` is whether heuristic
    review (the only SQLite store) is enabled; unknown counts it. Staged membership
    is published by a same-directory rename, so it is counted once; atomic JSON
    replacement briefly holds the old file and its ``.tmp`` together.
    """
    units = resources.files if files is None else files
    review_db = (resources.index_bytes // geometry.page_size) * geometry.page_size if review else 0
    return {
        "facts_working_index": resources.index_bytes,
        "review_store": review_db,
        "review_rollback_journal": journal_bound(geometry, review_db) if review else 0,
        "private_decisions": resources.decision_bytes,
        "membership_staging_and_publication": resources.output_bytes,
        "completion_envelopes": 2 * COMPLETION_BYTES,
        "signed_state": 2 * STATE_BYTES,
        "group_seal": 2 * SEAL_BYTES,
        "locks": len(LOCK_FILES) * LOCK_BYTES,
        "benchmark_index": resources.benchmark_bytes,
        "compiled_matcher": compiled_bound(
            resources.benchmark_bytes, resources.benchmark_patterns, FILE_SLACK_BYTES
        ),
        "allocation_slack": (units + FIXED_FILES + MAX_SORT_RUNS) * FILE_SLACK_BYTES,
    }


def admit_plan(
    resources: Resources,
    geometry: StorageGeometry,
    *,
    review: bool = True,
    files: int | None = None,
) -> dict[str, Any]:
    """Refuse a plan whose reviewed ceilings cannot contain its own worst case."""
    bounds = storage_bounds(resources, geometry, review=review, files=files)
    if bounds["review_rollback_journal"] > resources.journal_bytes:
        raise C05Error(
            "reviewed journal ceiling is below the derived rollback-journal bound "
            f"({bounds['review_rollback_journal']} B); no automatic widening"
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


def tree_size(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    total = 0
    for root, _dirs, names in os.walk(directory):
        for name in names:
            total += os.stat(os.path.join(root, name)).st_size
    return total


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
    *,
    review: bool = True,
    files: int | None = None,
    index_used: int | None = None,
) -> list[Component]:
    """Current size of every bounded component (``index_used`` from the live ledger)."""
    bounds = storage_bounds(resources, geometry, review=review, files=files)
    staged = output / (identity + ".partial")
    final = output / identity
    published = staged if staged.exists() else final
    working = (
        index_used
        if index_used is not None
        else tree_size(work / FACTS_DIR) + tree_size(work / GROUP_DIR)
    )
    return [
        Component("facts_working_index", work, bounds["facts_working_index"], working),
        Component("review_store", work, bounds["review_store"], _size(work / REVIEW_DB)),
        Component(
            "review_rollback_journal",
            work,
            bounds["review_rollback_journal"],
            _size(work / (REVIEW_DB + "-journal")),
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
        Component(
            "group_seal",
            work,
            bounds["group_seal"],
            _size(work / "seal.json") + _size(work / "seal.json.tmp"),
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
        Component(
            "compiled_matcher",
            work,
            bounds["compiled_matcher"],
            tree_size(work / MATCHER_DIR) + tree_size(work / STAGING_DIR),
        ),
        Component("allocation_slack", work, bounds["allocation_slack"], 0),
    ]


def _plain_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def unexpected_entries(work: Path, output: Path, identity: str) -> list[str]:
    """Names outside the accounted set: WAL/SHM, foreign files, a historical SQLite DB."""
    found: list[str] = []
    if work.is_dir():
        for entry in work.iterdir():
            name = entry.name
            if name in COMPILED_DIRS and _plain_dir(entry):
                found.extend(
                    name + "/" + child.name
                    for child in entry.iterdir()
                    if child.name not in COMPILED_ENTRIES or not _plain_file(child)
                )
            elif name == FACTS_DIR and _plain_dir(entry):
                for child in entry.iterdir():
                    if _UNIT.match(child.name) and _plain_file(child):
                        continue
                    if _UNIT_STAGING.match(child.name) and not child.is_symlink():
                        continue
                    found.append(name + "/" + child.name)
            elif name == GROUP_DIR and _plain_dir(entry):
                for child in entry.iterdir():
                    if child.name in GROUP_FILES and _plain_file(child):
                        continue
                    if child.name in GROUP_WORK_DIRS and _plain_dir(child):
                        found.extend(
                            f"{name}/{child.name}/{leaf.name}"
                            for leaf in child.iterdir()
                            if not _plain_file(leaf)
                            or not (leaf.name in NEAR_FILES or _RUN.match(leaf.name))
                        )
                        continue
                    found.append(name + "/" + child.name)
            elif name not in SCRATCH_FILES | LOCK_FILES or not _plain_file(entry):
                found.append(name)
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
    review: bool = True,
    files: int | None = None,
    disk_usage: Callable[[Path], Any] | None = None,
    probe: Callable[[Path], StorageGeometry] | None = None,
) -> dict[str, Any]:
    """Fail closed before work if any volume cannot hold the remaining growth.

    Existing bytes (a resumed run's fact units, staging leftovers or partial
    publication) are already allocated; only ``bound - current`` must still be
    free, plus the reviewed reserve on every volume. The geometry is re-measured
    beside the scratch root.
    """
    admit_plan(resources, geometry, review=review, files=files)
    usage = disk_usage or shutil.disk_usage
    measure = probe or probe_geometry
    # The probe file lives in the scratch root, never inside the accounted job directory.
    if measure(work.parent) != geometry:
        raise C05Error("scratch SQLite journal geometry differs from the reviewed plan")
    return physical_reserve(
        resources,
        geometry,
        work,
        output,
        identity,
        index,
        review=review,
        files=files,
        disk_usage=usage,
        when="starting",
    )


def physical_reserve(
    resources: Resources,
    geometry: StorageGeometry,
    work: Path,
    output: Path,
    identity: str,
    index: Path,
    *,
    review: bool = True,
    files: int | None = None,
    index_used: int | None = None,
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
    components = runtime_components(
        resources,
        geometry,
        work,
        output,
        identity,
        index,
        review=review,
        files=files,
        index_used=index_used,
    )
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
            "facts_working_index",
            "review_store",
            "private_decisions",
            "membership_staging_and_publication",
            "completion_envelopes",
            "signed_state",
            "group_seal",
        ],
        "derived": ["review_rollback_journal", "compiled_matcher", "allocation_slack"],
        "fixed_input": ["benchmark_index"],
        "reserved": ["locks"],
        "monitored": ["other-process free-space consumption", "process-tree RSS"],
        "bounds": dict(bounds),
    }
