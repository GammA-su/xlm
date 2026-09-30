"""Additive recovery authorization; historical campaigns and seals stay immutable."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_parquet import file_sha256, load_durable_source
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_fast as fast
from xlm.data.sources import essential_web_local as local
from xlm.data.sources.essential_web_bulk import BulkError

MANIFEST = "docs/implementation/evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/recovery.json"
CODE_FILES = (
    *fast.TRANSPORT_CODE_FILES,
    "src/xlm/data/sources/essential_web_progress.py",
    "src/xlm/data/sources/essential_web_monitor.py",
    "src/xlm/data/sources/essential_web_recovery.py",
    "scripts/essential_web_fast.py",
)


def code_identity(repo: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((repo / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in CODE_FILES
    }


def load_manifest(repo: Path, config: Mapping[str, Any]) -> dict[str, Any] | None:
    path = repo / MANIFEST
    if not path.is_file():
        return None
    value: dict[str, Any] = json.loads(path.read_bytes())
    if value.get("campaign") != config["digest"]:
        return None
    if value.get("digest") != canonical.digest({k: v for k, v in value.items() if k != "digest"}):
        raise BulkError("recovery amendment was altered")
    if (
        value.get("kind") != "essential_web_batch_recovery_v1"
        or value.get("original_code") != config["transport_code_sha256"]
        or value.get("code") != code_identity(repo)
    ):
        raise BulkError("recovery code changed since its freeze")
    bound = value.get("max_record_bytes")
    if (
        not isinstance(bound, int)
        or not config["limits"]["max_record_bytes"] < bound <= config["limits"]["max_parser_bytes"]
    ):
        raise BulkError("recovery record bound is invalid")
    return value


def check_authorization(manifest: Mapping[str, Any], directory: Path) -> None:
    path = directory / "recovery-authorization.json"
    if not path.is_file():
        raise BulkError(f"recovery needs operator authorization: {manifest['digest']}")
    value = json.loads(path.read_bytes())
    if value.get("digest") != manifest["digest"] or not value.get("operator"):
        raise BulkError("recovery authorization does not match its frozen amendment")


def verify_unit(campaign: Any, record: Mapping[str, Any], rank: int, name: str) -> dict[str, Any]:
    """Hash existing artifacts without adapting, downloading or replacing any of them."""
    directory = campaign.unit_dir(int(record["batch"]), rank)
    receipt: dict[str, Any] = json.loads((directory / local.RECEIPT_FILENAME).read_bytes())
    if receipt.get("digest") != canonical.digest(
        {k: v for k, v in receipt.items() if k != "digest"}
    ):
        raise BulkError(f"unit f{rank:05d} receipt digest differs")
    if campaign.recovery is not None:
        expected = campaign.recovery["sealed_receipts"].get(f"f{rank:05d}")
        if expected is not None and receipt["digest"] != expected:
            raise BulkError(f"unit f{rank:05d} differs from the preserved recovery seal")
        if (
            expected is None
            and receipt.get("recovery", {}).get("digest") != campaign.recovery["digest"]
        ):
            raise BulkError(f"unit f{rank:05d} lacks its recovery lineage")
    if (
        receipt.get("kind") != fast.RECEIPT_KIND
        or receipt.get("campaign") != campaign.config["digest"]
        or receipt.get("file") != name
        or receipt.get("batch") != record["batch"]
        or receipt.get("inventory_rank") != rank
        or receipt["plan"]["plan_hash"] != record["plan_hash"]
        or receipt["plan"]["selection_hash"] != record["selection_hash"]
    ):
        raise BulkError(f"unit f{rank:05d} identity differs from the batch")
    source = load_durable_source(campaign.raw_path(name))
    if (
        source is None
        or source != receipt["source"]
        or source["sha256"] != receipt["raw"]["sha256"]
        or source["source_file"] != name
        or source["revision"] != record["revision"]
        or source["repository"] != campaign.config["binding"]["repository"]
        or receipt["raw"]["path"] != campaign.raw_path(name).relative_to(campaign.root).as_posix()
    ):
        raise BulkError(f"unit f{rank:05d} raw identity differs")
    for view in campaign.config["views"]:
        info = receipt["views"][view]
        for filename, hash_key in (
            ("documents.jsonl", "documents_sha256"),
            (local.LEDGER_FILENAME, "rejections_file_sha256"),
            ("adaptation_summary.json", "adaptation_summary_sha256"),
        ):
            if file_sha256(directory / view / filename)[0] != info[hash_key]:
                raise BulkError(f"unit f{rank:05d} {view} {filename} hash differs")
    return receipt


def resume_state(campaign: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    sealed, remaining = [], []
    for position, name in enumerate(record["files"]):
        rank = campaign.rank(int(record["batch"]), position)
        directory = campaign.unit_dir(int(record["batch"]), rank)
        if (directory / local.RECEIPT_FILENAME).is_file():
            sealed.append(verify_unit(campaign, record, rank, name))
        elif directory.exists():
            raise BulkError(f"unit f{rank:05d} exists without a receipt; review before rerunning")
        else:
            remaining.append({"key": f"f{rank:05d}", "rank": rank, "file": name})
    return {
        "total": len(record["files"]),
        "sealed": len(sealed),
        "scheduled": len(remaining),
        "already_sealed_scheduled": 0,
        "completion_percent": 100 * len(sealed) / len(record["files"]),
        "remaining": remaining,
        "receipts": sealed,
    }
