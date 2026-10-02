"""Offline operator-only preparation of exact, reviewed JSONL benchmark material.

Publisher coverage is an explicit signed operator assertion over a frozen listing;
the builder cannot infer unpublished/missing configs from whichever files happen
to be local. No network, dataset execution or automatic license acceptance.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

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
from xlm.data.exclusion.policy import (
    C05Error,
    FrozenModel,
    MatcherPolicy,
    MatcherPolicyV4,
    Resources,
    ReviewPolicy,
)
from xlm.data.exclusion.prepare_workers import (
    ProcessTree,
    RowResult,
    Task,
    plan_tasks,
    run_tasks,
)
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.storage import connect

# Present from directory creation until the signed receipt (and export) exist.
INCOMPLETE_MARKER = "PREPARATION-INCOMPLETE"


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


def _admit(spec: MaterialSpec, root: Path, resources: Resources) -> list[Path]:
    if not spec.files or len({f.path for f in spec.files}) != len(spec.files):
        raise C05Error("empty/duplicate material listing")
    if (
        len(spec.files) > resources.files
        or sum(f.bytes for f in spec.files) > resources.benchmark_bytes
        or sum(f.items for f in spec.files) > resources.records
    ):
        raise C05Error("protected material aggregate ceiling")
    return [contained(root, entry.path) for entry in spec.files]


def _verify_and_plan(
    spec: MaterialSpec, paths: Sequence[Path], check: Callable[[], None]
) -> list[Task]:
    # Identity before any row is read; re-hashed after each file's last row.
    for entry, path in zip(spec.files, paths, strict=True):
        check()
        if path.stat().st_size != entry.bytes or file_sha(path, check) != entry.sha256:
            raise C05Error("protected material file identity mismatch")
    return plan_tasks(paths, spec.files, spec.max_record_bytes)


def _stream(
    spec: MaterialSpec,
    paths: Sequence[Path],
    tasks: Sequence[Task],
    *,
    policy: MatcherPolicy | MatcherPolicyV4,
    workers: int,
    check: Callable[[], None],
    process_tree: ProcessTree,
) -> Iterator[tuple[int, list[RowResult] | None, int]]:
    """Yield (file, batch, active) per batch and (file, None, active) per verified file."""
    remaining = [0] * len(spec.files)
    file_rows = [0] * len(spec.files)
    for task in tasks:
        remaining[task.file] += 1
    live: set[int] = set()
    with closing(
        run_tasks(
            tasks, workers=workers, policy=policy, max_record=spec.max_record_bytes, check=check
        )
    ) as events:
        for event in events:
            if event[0] == "ready":
                live.add(event[1])
                process_tree.watch(event[1])
                continue
            task = tasks[event[1]]
            if event[0] == "batch":
                # Single consumer; every ceiling is checked against global totals.
                check()
                batch: list[RowResult] = event[2]
                file_rows[task.file] += len(batch)
                if file_rows[task.file] > spec.files[task.file].items:
                    raise C05Error("benchmark material item ceiling")
                yield task.file, batch, len(live) or 1
                continue
            remaining[task.file] -= 1
            if remaining[task.file]:
                continue
            entry, path = spec.files[task.file], paths[task.file]
            if (
                file_rows[task.file] != entry.items
                or path.stat().st_size != entry.bytes
                or file_sha(path, check) != entry.sha256
            ):
                raise C05Error("benchmark material changed during preparation")
            yield task.file, None, len(live) or 1


AUDIT_KEYS = (
    "files",
    "items",
    "normal_signed_items",
    "fallback_signed_items",
    "items_without_patterns",
    "raw_candidate_patterns",
    "emitted_patterns_after_lossless_dedup",
)


def audit(
    spec: MaterialSpec,
    root: Path,
    *,
    policy: MatcherPolicy | MatcherPolicyV4,
    resources: Resources,
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Content-free signature-coverage audit; writes nothing, needs no signing key.

    Reads protected material through the same bounded workers as ``build`` and
    returns aggregate counts only: no text, labels, tokens, signatures, item
    hashes, provenance or row numbers. ``benchmark_patterns`` is reported, not
    enforced, so the operator can size that ceiling; time/RAM/record ceilings hold.
    """
    if spec.isolation.mode == "protected":
        require_operator(spec.isolation)
    paths = _admit(spec, root, resources)
    started = time.monotonic()
    report = progress or (lambda *_args, **_kwargs: None)
    process_tree = ProcessTree()

    def check() -> None:
        if time.monotonic() - started > resources.stage_seconds:
            raise C05Error("benchmark audit deadline")
        if process_tree.rss() > resources.ram_bytes:
            raise C05Error("benchmark audit RAM ceiling")

    by_task: dict[str, dict[str, int]] = {}
    for entry in spec.files:
        by_task.setdefault(entry.task, dict.fromkeys(AUDIT_KEYS, 0))["files"] += 1
    unsigned: dict[tuple[str, str, str], int] = {}
    report("verify", force=True)
    tasks = _verify_and_plan(spec, paths, check)
    files_done = rows = emitted = fallback_items = 0
    for file, batch, active in _stream(
        spec,
        paths,
        tasks,
        policy=policy,
        workers=resources.workers,
        check=check,
        process_tree=process_tree,
    ):
        if batch is None:
            files_done += 1
        else:
            entry = spec.files[file]
            counts = by_task[entry.task]
            for _item, _variants, raw, fallback, found in batch:
                counts["items"] += 1
                counts["raw_candidate_patterns"] += raw
                counts["emitted_patterns_after_lossless_dedup"] += len(found)
                if fallback:
                    counts["fallback_signed_items"] += 1
                elif found:
                    counts["normal_signed_items"] += 1
                else:
                    counts["items_without_patterns"] += 1
                    key = (entry.task, entry.config, entry.split)
                    unsigned[key] = unsigned.get(key, 0) + 1
            rows += len(batch)
            emitted += sum(len(row[4]) for row in batch)
            fallback_items += sum(row[3] for row in batch)
        report(
            "audit",
            files=files_done,
            rows=rows,
            patterns=emitted,
            fallback=fallback_items,
            active=active,
            force=batch is None,
        )
    if files_done != len(spec.files) or rows != sum(f.items for f in spec.files):
        raise C05Error("benchmark audit did not complete every material file")
    for counts in by_task.values():
        counts["deduplicated_pattern_emissions"] = (
            counts["raw_candidate_patterns"] - counts["emitted_patterns_after_lossless_dedup"]
        )
    totals = {
        k: sum(c[k] for c in by_task.values())
        for k in (*AUDIT_KEYS, "deduplicated_pattern_emissions")
    }
    report(
        "done", files=files_done, rows=rows, patterns=emitted, fallback=fallback_items, force=True
    )
    return {
        "audit": "c05-benchmark-signature-coverage-v1",
        "content_free": True,
        "mode": spec.isolation.mode,
        "matcher_version": policy.version,
        "policy_digest": policy.identity(),
        "workers": resources.workers,
        "benchmark_patterns_ceiling": resources.benchmark_patterns,
        "within_benchmark_patterns_ceiling": totals["emitted_patterns_after_lossless_dedup"]
        <= resources.benchmark_patterns,
        "totals": totals,
        "by_task": {task: by_task[task] for task in sorted(by_task)},
        "unsigned_by_task_config_split": [
            {"task": t, "config": c, "split": sp, "items_without_patterns": n}
            for (t, c, sp), n in sorted(unsigned.items())
        ],
    }


def build(
    spec: MaterialSpec,
    root: Path,
    destination: Path,
    *,
    policy: MatcherPolicy | MatcherPolicyV4,
    resources: Resources,
    issuer: str,
    key: bytes,
    code_commit: str,
    code_identity: str,
    dependency_sha256: str,
    review_policy: ReviewPolicy | None = None,
    receipt_export: Path | None = None,
    inspector: VolumeInspector | None = None,
    progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Write a protected index and a content-free signed receipt, never snippets.

    ``receipt_export`` is the only artifact that may be written outside a detached
    protected root: a byte-identical copy of the content-free signed receipt.
    ``resources.workers`` bounds the decode/render/pattern processes; one writer
    owns SQLite. ``progress`` is operational display only (content-free counts).
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
    paths = _admit(spec, root, resources)
    if destination.exists():
        raise C05Error("benchmark destination is write-once")
    started = time.monotonic()
    report = progress or (lambda *_args, **_kwargs: None)
    total_rows = sum(f.items for f in spec.files)
    report("verify", force=True)
    destination.mkdir(parents=True)
    # Removed only after the signed receipt (and export) exist: a crash leaves it.
    incomplete = destination / INCOMPLETE_MARKER
    incomplete.write_bytes(b"")
    db = connect(destination / "preparation.sqlite", resources.index_bytes)
    db.executescript(
        "CREATE TABLE patterns(hash TEXT PRIMARY KEY,tokens TEXT);"
        "CREATE TABLE refs(hash TEXT,ref TEXT,PRIMARY KEY(hash,ref));"
        "CREATE TABLE items(hash TEXT PRIMARY KEY);"
    )
    items = variants = duplicate_items = empty = pattern_count = files_done = fallback = 0
    process_tree = ProcessTree()

    def check() -> None:
        if time.monotonic() - started > resources.stage_seconds:
            raise C05Error("protected preparation deadline")
        # Parent plus worker children: the RAM ceiling is global to the build.
        if process_tree.rss() > resources.ram_bytes:
            raise C05Error("protected preparation RAM ceiling")
        if shutil.disk_usage(destination).free < resources.free_bytes:
            raise C05Error("protected preparation free-space reserve")
        used = sum(p.stat().st_size for p in destination.iterdir() if p.is_file())
        if used > resources.scratch_bytes:
            raise C05Error("protected preparation aggregate disk ceiling")

    db.check = check

    try:
        tasks = _verify_and_plan(spec, paths, check)
        report("process", files=0, rows=0, patterns=0, active=0, force=True)
        with db:
            for _file, batch, active in _stream(
                spec,
                paths,
                tasks,
                policy=policy,
                workers=resources.workers,
                check=check,
                process_tree=process_tree,
            ):
                if batch is None:
                    files_done += 1
                    report(
                        "process",
                        files=files_done,
                        rows=items,
                        patterns=pattern_count,
                        fallback=fallback,
                        active=active,
                        force=True,
                    )
                    continue
                # Emitted = exactly the (pattern, provenance) records index.jsonl holds.
                pattern_count += sum(len(row[4]) for row in batch)
                if pattern_count > resources.benchmark_patterns:
                    raise C05Error("protected pattern generation ceiling")
                before = db.total_changes
                db.executemany(
                    "INSERT OR IGNORE INTO items VALUES(?)", ((row[0],) for row in batch)
                )
                # An ignored insert is exactly an item hash seen before: the
                # total is items minus distinct hashes, independent of order.
                duplicate_items += len(batch) - (db.total_changes - before)
                db.executemany(
                    "INSERT OR IGNORE INTO patterns VALUES(?,?)",
                    ((p[0], p[1]) for row in batch for p in row[4]),
                )
                db.executemany(
                    "INSERT INTO refs VALUES(?,?) ON CONFLICT DO NOTHING",
                    ((p[0], ref) for row in batch for p in row[4] for ref in p[2]),
                )
                items += len(batch)
                variants += sum(row[1] for row in batch)
                fallback += sum(row[3] for row in batch)
                empty += sum(not row[4] for row in batch)
                report(
                    "process",
                    files=files_done,
                    rows=items,
                    patterns=pattern_count,
                    fallback=fallback,
                    active=active,
                )
        if files_done != len(spec.files) or items != total_rows:
            raise C05Error("protected preparation did not complete every material file")
        if spec.isolation.mode == "protected" and empty:
            # verify_benchmark() would reject this receipt: refuse before publishing.
            raise C05Error("protected benchmark items without frozen signatures")
        report("index", files=files_done, rows=items, patterns=pattern_count, force=True)
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
        # Every provenance embeds a globally unique item reference and emissions are
        # deduplicated per item, so emissions and index lines coincide exactly.
        if unique != pattern_count:
            raise C05Error("protected index/emission count invariant")
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
        incomplete.unlink()
        report("done", files=files_done, rows=items, patterns=pattern_count, force=True)
        return envelope
    finally:
        db.close()
