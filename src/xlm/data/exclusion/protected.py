"""Offline operator-only preparation of exact, reviewed JSONL benchmark material.

Publisher coverage is an explicit signed operator assertion over a frozen listing;
the builder cannot infer unpublished/missing configs from whichever files happen
to be local. No network, dataset execution or automatic license acceptance.
"""

from __future__ import annotations

import getpass
import os
import shutil
import time
from pathlib import Path
from typing import Any

import psutil
from pydantic import Field

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import BenchmarkReceipt, Isolation, MaterialFile, Sha, signed
from xlm.data.exclusion.inputs import contained
from xlm.data.exclusion.policy import C05Error, FrozenModel, MatcherPolicy, Resources
from xlm.data.exclusion.runner import file_sha
from xlm.data.exclusion.storage import connect
from xlm.data.exclusion.streaming import patterns, render


class MaterialSpec(FrozenModel):
    files: tuple[MaterialFile, ...]
    publisher_inventory_sha256: Sha
    all_published_configs_splits_reviewed: bool
    isolation: Isolation
    max_record_bytes: int = Field(default=1024 * 1024, ge=1)


def inspect(spec: MaterialSpec, root: Path) -> dict[str, Any]:
    """Only file existence and size; no protected text is opened."""
    return {
        "spec_digest": canonical.digest(spec.model_dump(mode="json")),
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
) -> dict[str, Any]:
    """Write a protected index and a content-free signed receipt, never snippets."""
    if not spec.all_published_configs_splits_reviewed:
        raise C05Error("publisher config/split coverage has not been reviewed")
    if spec.isolation.mode == "protected":
        from xlm.data.exclusion.identity import implementation_identity

        if (
            getpass.getuser().casefold() != spec.isolation.operator_principal.casefold()
            or spec.isolation.operator_principal.casefold()
            == spec.isolation.denied_agent_principal.casefold()
        ):
            raise C05Error("protected preparation requires a separate operator identity")
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
                with path.open("rb") as stream:
                    while raw := stream.readline(spec.max_record_bytes + 1):
                        check()
                        if len(raw) > spec.max_record_bytes:
                            raise C05Error("benchmark material record ceiling")
                        count += 1
                        if count > entry.items:
                            raise C05Error("benchmark material item ceiling")
                        row = canonical.loads_bytes_strict(raw)
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
        return envelope
    finally:
        db.close()
