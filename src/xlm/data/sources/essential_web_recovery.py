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
SCOPE_FIX = "docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/code-compatibility.json"
WINDOWS_FIX = "docs/implementation/evidence/ESSENTIAL-WEB-BATCH1-WINDOWS/code-compatibility.json"
MALFORMED_FIX = (
    "docs/implementation/evidence/ESSENTIAL-WEB-BATCH3-MALFORMED/code-compatibility.json"
)
_DRIVER = "scripts/essential_web_fast.py"
_DISPATCHER = "src/xlm/data/sources/essential_web_recovery.py"
SOURCE_GROWTH_FIX = (
    "docs/implementation/evidence/FINEPDFS-PROCESSING-GROWTH/code-compatibility.json"
)
#: Additive code-compatibility records in order: path, kind, and the exact files each changes.
COMPATIBILITY = (
    (SCOPE_FIX, "essential_web_recovery_scope_fix_v1", frozenset({_DRIVER, _DISPATCHER})),
    (
        WINDOWS_FIX,
        "essential_web_windows_publication_fix_v1",
        frozenset(
            {
                _DRIVER,
                _DISPATCHER,
                "src/xlm/data/sources/essential_web_local.py",
                "src/xlm/data/sources/essential_web_monitor.py",
                "src/xlm/data/sources/essential_web_progress.py",
            }
        ),
    ),
    (
        MALFORMED_FIX,
        "essential_web_malformed_whole_pass_v1",
        frozenset({_DISPATCHER, "src/xlm/data/sources/essential_web_local.py"}),
    ),
    (
        SOURCE_GROWTH_FIX,
        "source_processing_growth_v1",
        frozenset(
            {
                _DISPATCHER,
                "src/xlm/data/sources/essential_web_local.py",
                "src/xlm/data/acquisition/source_parquet.py",
            }
        ),
    ),
)
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
        or not compatible_code(repo, value)
    ):
        raise BulkError("recovery code changed since its freeze")
    bound = value.get("max_record_bytes")
    if (
        not isinstance(bound, int)
        or not config["limits"]["max_record_bytes"] < bound <= config["limits"]["max_parser_bytes"]
    ):
        raise BulkError("recovery record bound is invalid")
    return value


def compatible_code(repo: Path, manifest: Mapping[str, Any]) -> bool:
    """Preserve the historical amendment; bind only the reviewed repairs, in order.

    Each record must continue exactly from the code the previous one froze and
    may change only its own files; the running code must be one of the frozen
    states. Nothing is rewritten: a later repair only appends a record.
    """
    current = code_identity(repo)
    code = manifest.get("code")
    if code == current:
        return True
    for relative, kind, allowed in COMPATIBILITY:
        path = repo / relative
        if not path.is_file() or not isinstance(code, dict):
            return False
        fix = json.loads(path.read_bytes())
        frozen = fix.get("code")
        if not isinstance(frozen, dict) or set(frozen) != set(code):
            return False
        changed = {name for name in frozen if frozen[name] != code[name]}
        if not (
            fix.get("kind") == kind
            and fix.get("digest")
            == canonical.digest({k: v for k, v in fix.items() if k != "digest"})
            and fix.get("recovery_digest") == manifest["digest"]
            and fix.get("campaign") == manifest["campaign"]
            and fix.get("previous_code") == code
            and changed == allowed
        ):
            return False
        code = frozen
        if code == current:
            return True
    return False


def unit_scope(campaign: Any, batch: int, rank: int, name: str) -> dict[str, Any] | None:
    """Scope comes from immutable campaign membership, never a path alone."""
    amendment: dict[str, Any] | None = campaign.recovery
    if (
        amendment is None
        or amendment["campaign"] != campaign.config["digest"]
        or batch != amendment["batch"]
        or name != amendment["file"]
    ):
        return None
    members = campaign.members(batch)
    if name not in members or rank != campaign.rank(batch, members.index(name)):
        return None
    return amendment


def lookup_matching_recovery(
    campaign: Any, batch: int, rank: int, name: str, source: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    amendment = unit_scope(campaign, batch, rank, name)
    if (
        amendment is None
        or source is None
        or source.get("source_file") != name
        or source.get("sha256") != amendment["source_sha256"]
        or source.get("revision") != campaign.config["binding"]["revision"]
        or source.get("repository") != campaign.config["binding"]["repository"]
    ):
        return None
    return amendment


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
    if campaign.recovery is not None and record["batch"] == campaign.recovery["batch"]:
        expected = campaign.recovery["sealed_receipts"].get(f"f{rank:05d}")
        if expected is not None and receipt["digest"] != expected:
            raise BulkError(f"unit f{rank:05d} differs from the preserved recovery seal")
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
    amendment = lookup_matching_recovery(campaign, int(record["batch"]), rank, name, source)
    if amendment is not None:
        if receipt.get("recovery") != {
            "digest": amendment["digest"],
            "effective_max_record_bytes": amendment["max_record_bytes"],
        }:
            raise BulkError(f"unit f{rank:05d} lacks its exact recovery lineage")
    elif "recovery" in receipt:
        raise BulkError(f"unit f{rank:05d} has recovery lineage outside its scope")
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
