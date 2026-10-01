"""Explicit, verified locations for preserved benchmark scratch workspaces."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from filelock import FileLock

from xlm.artifacts.manifest import bounded_children, comparable_resolved, ensure_plain_path
from xlm.data.acquisition.source_parquet import file_sha256
from xlm.data.acquisition.source_run import (
    Roots,
    RunError,
    check_digest,
    read_json,
    self_digest,
    write_once,
)

RECEIPT = "scratch-archive.json"
INTENT = "scratch-archive.intent.json"
MAX_ENTRIES = 10_000


def inventory(root: Path, max_bytes: int) -> list[dict[str, Any]]:
    """Hash a bounded plain directory tree; no corpus content is returned."""
    ensure_plain_path(root)
    if not root.is_dir() or max_bytes < 0:
        raise RunError("archive needs an existing directory and a nonnegative byte bound")
    pending = [root]
    files: list[dict[str, Any]] = []
    count = total = 0
    while pending:
        for path in bounded_children(pending.pop()):
            count += 1
            if count > MAX_ENTRIES:
                raise RunError("archive tree exceeds entry bound")
            ensure_plain_path(path)
            if path.is_dir():
                pending.append(path)
                continue
            if not path.is_file():
                raise RunError("archive refuses nonregular files")
            before = path.stat()
            total += before.st_size
            if total > max_bytes:
                raise RunError("archive exceeds byte bound")
            sha256, size = file_sha256(path)
            after = path.stat()
            if (size, before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise RunError("archive file changed while hashing")
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": sha256,
                    "mtime_ns": after.st_mtime_ns,
                }
            )
    return sorted(files, key=lambda item: str(item["path"]))


def _paths(roots: Roots, label: str, destination: Path) -> tuple[Path, Path, Path]:
    if not label.isidentifier() or not destination.is_absolute():
        raise RunError("archive requires a plain benchmark label and absolute destination")
    original = roots.scratch(f"bench-{label}")
    for path in (original, destination, roots.plans):
        ensure_plain_path(path)
    original, destination = comparable_resolved(original), comparable_resolved(destination)
    for protected in (roots.scratch_root, roots.data_root):
        root = comparable_resolved(protected)
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            raise RunError("archive destination must be outside active scratch and durable data")
    return original, destination, roots.plans / "benchmarks" / label


def donor_directory(roots: Roots, label: str, record: dict[str, Any]) -> Path:
    """Resolve only a verified explicit archive; never guess a historical location."""
    directory = roots.plans / "benchmarks" / label
    if not (directory / RECEIPT).exists():
        if (directory / INTENT).exists():
            raise RunError("benchmark archive is pending verification; finish archival first")
        return roots.scratch(f"bench-{label}")
    receipt = read_json(directory / RECEIPT)
    check_digest(receipt, "scratch archive")
    original, archived, _ = _paths(roots, label, Path(receipt["archive_path"]))
    if (
        receipt.get("kind") != "mix01_benchmark_scratch_archive"
        or receipt.get("version") != 1
        or receipt.get("label") != label
        or receipt.get("benchmark_digest") != record["digest"]
        or receipt.get("source") != record["source"]
        or receipt.get("original_path") != str(original)
        or original.exists()
    ):
        raise RunError("scratch archive identity differs or original workspace reappeared")
    if inventory(archived, int(receipt["max_bytes"])) != receipt["files"]:
        raise RunError("scratch archive files no longer match their receipt")
    return archived


def archive_benchmark(
    roots: Roots, label: str, destination: Path, *, max_bytes: int
) -> dict[str, Any]:
    """Same-volume rename with durable intent, full post-check and a write-once receipt.

    An interrupted move is recoverable by repeating the identical call. No
    copy/delete fallback or overwrite is permitted. The caller authorizes the
    specific destination and an explicit hash-work byte bound.
    """
    original, archived, directory = _paths(roots, label, destination)
    record = read_json(directory / "benchmark.json")
    check_digest(record, "benchmark plan")
    with FileLock(str(roots.plans / "run.lock"), timeout=1):
        if (directory / RECEIPT).exists():
            receipt = read_json(directory / RECEIPT)
            if receipt["archive_path"] != str(archived) or receipt["max_bytes"] != max_bytes:
                raise RunError("archive already exists with another destination or byte bound")
            donor_directory(roots, label, record)
            return receipt
        if (directory / INTENT).exists():
            receipt = read_json(directory / INTENT)
            check_digest(receipt, "scratch archive intent")
            if (
                receipt["archive_path"],
                receipt["original_path"],
                receipt["benchmark_digest"],
                receipt["max_bytes"],
            ) != (str(archived), str(original), record["digest"], max_bytes):
                raise RunError("archive intent differs from the requested move")
        else:
            if archived.exists():
                raise RunError("archive destination already exists; refusing overwrite")
            receipt = self_digest(
                {
                    "kind": "mix01_benchmark_scratch_archive",
                    "version": 1,
                    "label": label,
                    "benchmark_digest": record["digest"],
                    "source": record["source"],
                    "original_path": str(original),
                    "archive_path": str(archived),
                    "max_bytes": max_bytes,
                    "files": inventory(original, max_bytes),
                }
            )
            write_once(directory / INTENT, receipt)
        if original.exists():
            if archived.exists():
                raise RunError("both original and archive exist; refusing overwrite")
            if inventory(original, max_bytes) != receipt["files"]:
                raise RunError("scratch changed after archive intent was recorded")
            archived.parent.mkdir(parents=True, exist_ok=True)
            if original.stat().st_dev != archived.parent.stat().st_dev:
                raise RunError("archive requires a same-volume directory rename")
            os.rename(original, archived)
        if inventory(archived, max_bytes) != receipt["files"]:
            raise RunError("archive verification failed; preserved intent requires investigation")
        write_once(directory / RECEIPT, receipt)
        return receipt
