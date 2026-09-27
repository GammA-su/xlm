"""Offline eight-file inventory reconstruction and hash ranking (Arm M).

Reproduces the frozen inventory from ``inventory-freeze.json`` templates
with no network enumeration or footer reads: sequential filename lists
are hash-verified against the original listing hashes, development files
are excluded, and the winner per crawl is the lowest
``H([protocol, "metadata-file", seed, repository, revision, crawl, path])``
with ties broken by ascending UTF-8 path bytes. An unavailable winner is
a STOP, never permission to take the next rank.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import canonical, frozen


class InventoryError(ValueError):
    """Any inventory reconstruction, digest, or ranking mismatch: STOP."""


def expand_listing(crawl: str, file_count: int) -> list[str]:
    """Offline sequential filename list for one crawl (no enumeration)."""
    return [f"data/{crawl}/train-{i:05d}-of-{file_count:05d}.parquet" for i in range(file_count)]


def check_listing(paths: Sequence[str], expected_sha256: str, *, crawl: str) -> None:
    """The canonical hash must reproduce the original listing hash exactly."""
    if canonical.digest(list(paths)) != expected_sha256:
        raise InventoryError(f"listing hash mismatch for {crawl}: not the frozen inventory")


def eligible_paths(paths: Sequence[str], excluded: str, *, crawl: str) -> list[str]:
    """All listed paths except the development file; sorted by construction."""
    if excluded not in paths:
        raise InventoryError(f"development file {excluded} absent from {crawl} listing")
    eligible = [p for p in paths if p != excluded]
    canonical.check_ordered_paths(eligible, what=f"{crawl} eligible paths")
    return eligible


def rank_digest(crawl: str, source_file: str) -> str:
    """Frozen file-ranking hash for one candidate path."""
    return canonical.digest(
        [
            frozen.PROTOCOL_VERSION,
            frozen.METADATA_FILE_RANK_PREFIX,
            frozen.METADATA_SEED,
            frozen.REPOSITORY,
            frozen.REVISION,
            crawl,
            source_file,
        ]
    )


def select_winner(eligible: Sequence[str], crawl: str) -> tuple[str, str]:
    """Lowest rank digest wins; ties break by ascending UTF-8 path bytes."""
    if not eligible:
        raise InventoryError(f"no eligible files in {crawl}")
    winner = min(eligible, key=lambda p: (rank_digest(crawl, p), p.encode("utf-8")))
    return winner, rank_digest(crawl, winner)


def expand_stratum(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct and verify one frozen stratum; returns the verified record."""
    crawl = str(entry["crawl"])
    count = canonical.check_exact_int(entry["file_count"], what=f"{crawl} file_count")
    excluded = str(entry["excluded_file"])
    paths = expand_listing(crawl, count)
    check_listing(paths, str(entry["original_listing_sha256"]), crawl=crawl)
    eligible = eligible_paths(paths, excluded, crawl=crawl)
    if len(eligible) != canonical.check_exact_int(
        entry["eligible_count"], what=f"{crawl} eligible_count"
    ):
        raise InventoryError(f"eligible count mismatch for {crawl}")
    if canonical.digest(eligible) != entry["eligible_paths_digest"]:
        raise InventoryError(f"eligible paths digest mismatch for {crawl}")
    winner, rank = select_winner(eligible, crawl)
    if winner != entry["selected_file"]:
        raise InventoryError(f"winner mismatch for {crawl}: {winner} is not frozen")
    if rank != entry["selection_rank_sha256"]:
        raise InventoryError(f"ranking hash mismatch for {crawl}")
    if winner == excluded:
        raise InventoryError(f"development file selected in {crawl}")
    return {"crawl": crawl, "files": eligible, "selected_file": winner}


def verify_inventory_freeze(inventory_freeze: Mapping[str, Any]) -> dict[str, Any]:
    """Full offline verification; returns crawl/file identities on success."""
    canonical.check_known_fields(
        inventory_freeze,
        frozenset(
            {
                "kind",
                "protocol_version",
                "metadata_seed",
                "repository",
                "revision",
                "parent_discovery_digest",
                "complete_eligible_inventory_digest",
                "complete_eligible_inventory_canonical_shape",
                "reconstruction",
                "strata",
                "digest",
            }
        ),
        what="inventory-freeze",
    )
    if inventory_freeze["protocol_version"] != frozen.PROTOCOL_VERSION:
        raise InventoryError("inventory freeze is not evidence-v2.0")
    if inventory_freeze["metadata_seed"] != frozen.METADATA_SEED:
        raise InventoryError("inventory freeze seed mismatch")
    if inventory_freeze["repository"] != frozen.REPOSITORY:
        raise InventoryError("inventory freeze repository mismatch")
    if inventory_freeze["revision"] != frozen.REVISION:
        raise InventoryError("inventory freeze revision mismatch")
    if inventory_freeze["parent_discovery_digest"] != frozen.DEV_DISCOVERY_DIGEST:
        raise InventoryError("inventory freeze parent discovery mismatch")
    if canonical.self_digest(inventory_freeze) != inventory_freeze["digest"]:
        raise InventoryError("inventory freeze self-digest mismatch")
    if inventory_freeze["digest"] != frozen.INVENTORY_DIGEST:
        raise InventoryError("inventory freeze digest is not the frozen value")
    strata = inventory_freeze["strata"]
    if not isinstance(strata, list) or len(strata) != 8:
        raise InventoryError("inventory freeze must carry exactly eight strata")
    expanded = [expand_stratum(dict(s)) for s in strata]
    winners = [e["selected_file"] for e in expanded]
    if len(set(winners)) != 8:
        raise InventoryError("selected files are not distinct")
    body = {
        "repository": inventory_freeze["repository"],
        "revision": inventory_freeze["revision"],
        "strata": [{"crawl": e["crawl"], "files": e["files"]} for e in expanded],
    }
    if canonical.digest(body) != inventory_freeze["complete_eligible_inventory_digest"]:
        raise InventoryError("complete eligible inventory digest mismatch")
    if inventory_freeze["complete_eligible_inventory_digest"] != frozen.COMPLETE_INVENTORY_DIGEST:
        raise InventoryError("complete inventory digest is not the frozen value")
    total = sum(len(e["files"]) for e in expanded)
    return {"winners": winners, "eligible_total": total, "strata": expanded}


def verify_freeze_binding(freeze: Mapping[str, Any], root_files: Mapping[str, bytes]) -> None:
    """Verify freeze self-digest, expected value, and every bound artifact."""
    if freeze.get("digest") != frozen.FREEZE_DIGEST:
        raise InventoryError("freeze digest is not the expected canonical value")
    if canonical.self_digest(freeze) != freeze["digest"]:
        raise InventoryError("freeze self-digest recomputation mismatch")
    artifacts = freeze.get("artifacts")
    if not isinstance(artifacts, list):
        raise InventoryError("freeze carries no artifact list")
    for artifact in artifacts:
        raw = root_files.get(str(artifact["relative_path"]))
        if raw is None:
            raise InventoryError(f"freeze artifact missing: {artifact['relative_path']}")
        if len(raw) != artifact["bytes"]:
            raise InventoryError(f"freeze artifact byte drift: {artifact['relative_path']}")
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise InventoryError(f"freeze artifact hash drift: {artifact['relative_path']}")
        descriptor = {k: v for k, v in artifact.items() if k != "descriptor_digest"}
        if canonical.digest(descriptor) != artifact["descriptor_digest"]:
            raise InventoryError(f"freeze descriptor digest mismatch: {artifact['relative_path']}")
