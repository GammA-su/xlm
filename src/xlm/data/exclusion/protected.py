"""Offline operator-only preparation of exact, reviewed JSONL benchmark material.

Publisher coverage is an explicit signed operator assertion over a frozen listing;
the builder cannot infer unpublished/missing configs from whichever files happen
to be local. No network, dataset execution or automatic license acceptance.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psutil
from pydantic import Field

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import BenchmarkReceipt, MaterialFile, Sha, signed
from xlm.data.exclusion.inputs import contained
from xlm.data.exclusion.isolation import (
    AnyIsolation,
    DetachedVolumeIsolation,
    VolumeInspector,
    checkout_root,
    overlaps,
    require_operator,
    verify_protected_root,
    verify_separation,
)
from xlm.data.exclusion.policy import C05Error, FrozenModel, MatcherPolicy, Resources, ReviewPolicy
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.storage import connect
from xlm.data.exclusion.streaming import patterns, render


class MaterialSpec(FrozenModel):
    files: tuple[MaterialFile, ...]
    publisher_inventory_sha256: Sha
    all_published_configs_splits_reviewed: bool
    isolation: AnyIsolation
    max_record_bytes: int = Field(default=1024 * 1024, ge=1)


def inspect(spec: MaterialSpec, root: Path) -> dict[str, Any]:
    """Only file existence and size; no protected text is opened."""
    return {
        "spec_digest": canonical.digest(spec.model_dump(mode="json")),
        "complete_local_sizes": all(
            contained(root, f.path).is_file() and contained(root, f.path).stat().st_size == f.bytes
            for f in spec.files
        ),
        "missing_material": [
            {
                "task": f.task,
                "repository": f.repository,
                "revision": f.revision,
                "config": f.config,
                "split": f.split,
                "path": f.path,
                "expected_bytes": f.bytes,
            }
            for f in sorted(spec.files, key=lambda f: (f.task, f.config, f.split, f.path))
            if not contained(root, f.path).is_file()
        ],
        "files": [
            {
                "path": f.path,
                "present": contained(root, f.path).is_file(),
                "size_matches": contained(root, f.path).is_file()
                and contained(root, f.path).stat().st_size == f.bytes,
            }
            for f in spec.files
        ],
    }


def material_rows(
    path: Path, entry: MaterialFile, max_record: int, check: Callable[[], None]
) -> Iterator[dict[str, Any]]:
    if entry.format == "jsonl":
        with path.open("rb") as stream:
            while raw := stream.readline(max_record + 1):
                check()
                if len(raw) > max_record:
                    raise C05Error("benchmark material record ceiling")
                row = canonical.loads_bytes_strict(raw)
                if not isinstance(row, dict):
                    raise C05Error("benchmark row must be an object")
                yield row
        return
    import pyarrow.parquet as pq

    # Validate decompressed row-group size before decoding. This intentionally
    # refuses large row groups rather than silently widening memory bounds.
    with pq.ParquetFile(path) as parquet:
        if parquet.metadata.num_rows != entry.items:
            raise C05Error("benchmark parquet item count mismatch")
        for number in range(parquet.metadata.num_row_groups):
            check()
            if parquet.metadata.row_group(number).total_byte_size > max_record:
                raise C05Error("benchmark parquet decompressed row-group ceiling")
            for batch in parquet.iter_batches(batch_size=1, row_groups=[number], use_threads=False):
                check()
                row = batch.to_pylist()[0]
                if len(canonical.canonical_bytes(row)) > max_record:
                    raise C05Error("benchmark material record ceiling")
                yield row


def detached_preparation(
    isolation: DetachedVolumeIsolation,
    material: Path,
    destination: Path,
    receipt_export: Path | None,
    inspector: VolumeInspector | None,
) -> None:
    """Material, index and scratch stay inside the verified detached protected root."""
    protected = verify_protected_root(isolation.protected_root, inspector)
    root = Path(protected.path).resolve()
    for name, path in (("benchmark material", material), ("protected index", destination)):
        if not path.resolve().is_relative_to(root) or path.resolve() == root:
            raise C05Error(f"{name} must stay inside the protected benchmark root")
    if receipt_export is not None and overlaps(receipt_export, root):
        raise C05Error("receipt export is the content-free copy outside the protected root")
    if overlaps(checkout_root(), root):
        raise C05Error("protected benchmark root overlaps the running repository checkout")
    # Preparation-time isolation attestation: every declared root is re-measured now.
    measured = verify_separation(
        protected, ((r.role, r.path) for r in isolation.separated_roots), inspector
    )
    if [m.volume for m in measured] != [r.volume for r in isolation.separated_roots]:
        raise C05Error("declared separated-root filesystem identity changed")


def build(
    spec: MaterialSpec,
    root: Path,
    destination: Path,
    *,
    policy: MatcherPolicy,
    resources: Resources,
    issuer: str,
    key: bytes,
    code_commit: str,
    code_identity: str,
    dependency_sha256: str,
    review_policy: ReviewPolicy | None = None,
    receipt_export: Path | None = None,
    inspector: VolumeInspector | None = None,
) -> dict[str, Any]:
    """Write a protected index and a content-free signed receipt, never snippets.

    ``receipt_export`` is the only artifact that may be written outside a detached
    protected root: a byte-identical copy of the content-free signed receipt.
    """
    if not spec.all_published_configs_splits_reviewed:
        raise C05Error("publisher config/split coverage has not been reviewed")
    if isinstance(spec.isolation, DetachedVolumeIsolation):
        detached_preparation(spec.isolation, root, destination, receipt_export, inspector)
    elif receipt_export is not None:
        raise C05Error("receipt export is defined only for detached-volume preparation")
    if spec.isolation.mode == "protected":
        from xlm.data.exclusion.identity import implementation_identity

        require_operator(spec.isolation)
        actual = implementation_identity()
        if (code_commit, code_identity, dependency_sha256) != (
            actual["code_commit"],
            actual["code_identity"],
            actual["dependency_sha256"],
        ):
            raise C05Error("protected preparation code/dependencies differ")
    if not spec.files or len({f.path for f in spec.files}) != len(spec.files):
        raise C05Error("empty/duplicate material listing")
    if (
        len(spec.files) > resources.files
        or sum(f.bytes for f in spec.files) > resources.benchmark_bytes
        or sum(f.items for f in spec.files) > resources.records
    ):
        raise C05Error("protected material aggregate ceiling")
    if destination.exists():
        raise C05Error("benchmark destination is write-once")
    destination.mkdir(parents=True)
    db = connect(destination / "preparation.sqlite", resources.index_bytes)
    db.executescript(
        "CREATE TABLE patterns(hash TEXT PRIMARY KEY,tokens TEXT);"
        "CREATE TABLE refs(hash TEXT,ref TEXT,PRIMARY KEY(hash,ref));"
        "CREATE TABLE items(hash TEXT PRIMARY KEY);"
    )
    items = variants = duplicate_items = empty = pattern_count = 0
    started = time.monotonic()

    def check() -> None:
        if time.monotonic() - started > resources.stage_seconds:
            raise C05Error("protected preparation deadline")
        if psutil.Process().memory_info().rss > resources.ram_bytes:
            raise C05Error("protected preparation RAM ceiling")
        if shutil.disk_usage(destination).free < resources.free_bytes:
            raise C05Error("protected preparation free-space reserve")
        used = sum(p.stat().st_size for p in destination.iterdir() if p.is_file())
        if used > resources.scratch_bytes:
            raise C05Error("protected preparation aggregate disk ceiling")

    db.check = check

    try:
        with db:
            for entry in spec.files:
                check()
                path = contained(root, entry.path)
                if path.stat().st_size != entry.bytes or file_sha(path) != entry.sha256:
                    raise C05Error("protected material file identity mismatch")
                count = 0
                for row in material_rows(path, entry, spec.max_record_bytes, check):
                    check()
                    count += 1
                    if count > entry.items:
                        raise C05Error("benchmark material item ceiling")
                    rendered = render(entry.task, row)
                    item_hash = canonical.digest([entry.task, rendered])
                    duplicate_items += int(
                        db.execute("SELECT 1 FROM items WHERE hash=?", (item_hash,)).fetchone()
                        is not None
                    )
                    db.execute("INSERT OR IGNORE INTO items VALUES(?)", (item_hash,))
                    reference = canonical.digest([entry.model_dump(mode="json"), count])
                    found = False
                    for pattern in patterns(rendered, reference, policy):
                        found = True
                        pattern_count += 1
                        if pattern_count > resources.benchmark_patterns:
                            raise C05Error("protected pattern generation ceiling")
                        identity = canonical.digest(pattern.tokens)
                        db.execute(
                            "INSERT OR IGNORE INTO patterns VALUES(?,?)",
                            (identity, canonical.canonical_bytes(pattern.tokens).decode()),
                        )
                        db.executemany(
                            "INSERT INTO refs VALUES(?,?) ON CONFLICT DO NOTHING",
                            ((identity, ref) for ref in pattern.provenance),
                        )
                    empty += int(not found)
                    variants += len(rendered)
                    items += 1
                if count != entry.items or file_sha(path) != entry.sha256:
                    raise C05Error("benchmark material changed during preparation")
        index = destination / "index.jsonl"
        written = unique = 0
        with index.open("xb") as output_stream:
            # One provenance per line keeps an extreme duplicate bucket bounded.
            for tokens, ref in db.execute(
                "SELECT p.tokens,r.ref FROM refs r JOIN patterns p ON p.hash=r.hash "
                "ORDER BY r.hash,r.ref"
            ):
                raw = (
                    canonical.canonical_bytes(
                        {"tokens": canonical.loads_strict(tokens), "provenance": [ref]}
                    )
                    + b"\n"
                )
                written += len(raw)
                if written > resources.benchmark_bytes:
                    raise C05Error("protected index byte ceiling")
                output_stream.write(raw)
                unique += 1
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if not unique:
            raise C05Error("empty benchmark index")
        receipt = BenchmarkReceipt(
            files=spec.files,
            publisher_inventory_sha256=spec.publisher_inventory_sha256,
            all_published_configs_splits_reviewed=True,
            policy_digest=policy.identity(),
            fuzzy_policy_digest=(review_policy or ReviewPolicy()).identity(),
            index_sha256=file_sha(index),
            index_bytes=written,
            items=items,
            duplicate_items=duplicate_items,
            variants=variants,
            patterns=unique,
            items_without_patterns=empty,
            code_commit=code_commit,
            code_identity=code_identity,
            dependency_sha256=dependency_sha256,
            isolation=spec.isolation,
            issuer=issuer,
        )
        envelope = signed(receipt.model_dump(mode="json"), issuer, key)
        canonical.write_canonical_json(destination / "benchmark-preparation.receipt.json", envelope)
        if receipt_export is not None:
            # Content-free: aggregate counts, digests and isolation identity only.
            canonical.write_canonical_json(receipt_export, envelope)
        return envelope
    finally:
        db.close()
