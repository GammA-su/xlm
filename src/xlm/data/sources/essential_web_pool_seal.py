"""Additive post-acquisition seal of the Essential-Web first-pass canonical pool.

The fast campaign seals every file unit with a write-once receipt, but its
cumulative files (``cumulative.json``, ``campaign-status.json``) are summaries
rewritten on every accounting call and carry no digest. Nothing yet binds the
completed first pass as one immutable input that later C05 screening, tokenizer
fitting and exact counting can name. This module builds that binding.

The seal records **first-pass canonical availability**, never final training
exposure: nothing is down-selected, the oversupplied views are kept whole, and
C05, the tokenizer, exact XLM-token counts, any deterministic top-up and the
final 6B freeze all remain pending. It is not a :class:`xlm.data.pools.PoolFreeze`
or a ``FrozenPoolManifest``: those bind dedup and split identities that exist only
after the global Mix-01 C05 step.

Every value is derived from the sealed unit receipts and the verified campaign;
the seal is deterministic (no timestamps), so rebuilding it from an unchanged
store reproduces the same bytes and the same digest.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources import essential_web_fast as fast

SEAL_KIND = "essential_web_first_pass_pool_seal"
SEAL_VERSION = 1
STAGE = "first_pass_canonical_availability"
SEAL_FILENAME = "first-pass-seal.json"
#: Fixed so that a rebuilt seal is byte-identical; any change is a new seal version.
VERIFICATION_POLICY = {
    "receipts": "every unit receipt re-read and checked against its own digest and campaign",
    "accounting": "cumulative yield recomputed from the receipts (duplicate, order and plan "
    "checks of the campaign accounting)",
    "raw": "every retained source Parquet: size and SHA-256 of every byte equal to the "
    "receipt; identity sidecar equal to the receipt source record",
    "canonical": "every documents.jsonl, compressed rejection ledger and adaptation "
    "summary: size and SHA-256 of every byte equal to the receipt",
    "staging": "no unpublished staging unit and no sealed unit outside a planned batch",
}
PENDING = {
    "c05": "NOT RUN: global across all Mix-01 source families (dedup before splits, "
    "benchmark exclusion over the frozen Mix-01 training pool); deferred until every "
    "Mix-01 component has first-pass canonical availability",
    "tokenizer": "NOT TRAINED: the 32,768-token tokenizer is fitted only after C05, on the "
    "training partition",
    "exact_xlm_token_count": "PENDING: estimated tokens only until the tokenizer is frozen",
    "deterministic_top_up": "MAY STILL BE REQUIRED after exact counting: next inventory "
    "batches in frozen order under a new reviewed authorization",
    "final_6b_freeze": "PENDING",
    "training_permitted": False,
}
EXPOSURE_POLICY = {
    "availability_is_not_exposure": True,
    "mixture_weights_changed": False,
    "quotas_changed": False,
    "oversupply_preserved": True,
    "down_selection": "none; final exposure is chosen later by the exact-token quota "
    "scheduler (C07) under the frozen tokenizer and the unchanged Mix-01 weights",
    "reason": "oversupply is headroom for C05 drops and exact-count uncertainty; discarding "
    "it now would decide exposure before the tokenizer and C05 exist",
}


class SealError(ValueError):
    """The store or the campaign does not support a first-pass seal."""


def unit_entry(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Compact, receipt-derived record of one sealed file unit."""
    entry: dict[str, Any] = {
        "rank": int(receipt["inventory_rank"]),
        "batch": int(receipt["batch"]),
        "file": str(receipt["file"]),
        "receipt_digest": str(receipt["digest"]),
        "raw": {"bytes": int(receipt["raw"]["bytes"]), "sha256": str(receipt["raw"]["sha256"])},
        "rows": int(receipt["rows"]),
        "malformed_rows": int(receipt["malformed_rows"]),
        "views": {
            view: {
                "documents": int(receipt["views"][view]["documents"]),
                "canonical_bytes": int(receipt["views"][view]["canonical_bytes"]),
                "documents_sha256": str(receipt["views"][view]["documents_sha256"]),
            }
            for view in calibration.VIEWS
        },
    }
    if "recovery" in receipt:
        entry["recovery_digest"] = str(receipt["recovery"]["digest"])
    return entry


def membership(units: Sequence[Mapping[str, Any]], view: str) -> dict[str, Any]:
    """Deterministic membership of one view: its document files in inventory order.

    Each ``documents_sha256`` binds that file's document IDs and text, so the
    ordered list binds every document of the view transitively.
    """
    lines = [
        f"{u['rank']}|{u['file']}|{u['views'][view]['documents_sha256']}|"
        f"{u['views'][view]['documents']}|{u['views'][view]['canonical_bytes']}"
        for u in units
    ]
    return {
        "order": "inventory rank ascending",
        "units": len(units),
        "documents": sum(int(u["views"][view]["documents"]) for u in units),
        "canonical_bytes": sum(int(u["views"][view]["canonical_bytes"]) for u in units),
        "digest": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
    }


def _totals(units: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "files": len(units),
        "rows": sum(int(u["rows"]) for u in units),
        "malformed_rows": sum(int(u["malformed_rows"]) for u in units),
        "raw_bytes": sum(int(u["raw"]["bytes"]) for u in units),
    }


def sufficiency(
    members: Mapping[str, Mapping[str, Any]], targets: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """First-pass status per view, in the campaign's own estimated-token terms."""
    result: dict[str, Any] = {}
    for view in calibration.VIEWS:
        have = int(members[view]["canonical_bytes"])
        target = targets[view]
        need = int(target["required_canonical_bytes"])
        result[view] = {
            "final_exact_token_quota": int(target["final_exact_token_quota"]),
            "first_pass_estimated_token_target": int(target["first_pass_estimated_token_target"]),
            "required_canonical_bytes": need,
            "acquired_canonical_bytes": have,
            "estimated_tokens": have / calibration.BYTES_PER_TOKEN["central"],
            "availability_over_first_pass_target": have / need,
            "status": "SUFFICIENT" if have >= need else "TOP_UP",
        }
    return result


def build_seal(
    *,
    config: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    batches: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    amendments: Mapping[str, Any],
    admissions: Mapping[str, Any],
) -> dict[str, Any]:
    """The first-pass seal; refuses anything short of a complete, sufficient pass."""
    units = sorted((unit_entry(r) for r in receipts), key=lambda u: int(u["rank"]))
    if [u["rank"] for u in units] != list(range(len(units))):
        raise SealError("sealed units are not a contiguous inventory prefix from rank 0")
    size = int(config["batch"]["files"])
    indices = [int(b["index"]) for b in batches]
    if indices != list(range(len(batches))) or not batches:
        raise SealError("planned batches are not a contiguous prefix from batch 0")
    if len(units) != size * len(batches):
        raise SealError("not every planned batch is complete")
    for unit in units:
        if unit["batch"] != unit["rank"] // size:
            raise SealError(f"{unit['file']} is sealed under the wrong batch")
    if (
        int(state["complete_batches"]) != len(batches)
        or state["counted"] != (state["sealed_including_incomplete_batch"])
    ):
        raise SealError("campaign accounting disagrees with the planned batches")
    members = {view: membership(units, view) for view in calibration.VIEWS}
    totals = _totals(units)
    counted = state["counted"]
    for key in ("files", "rows", "malformed_rows", "raw_bytes"):
        if int(counted[key]) != totals[key]:
            raise SealError(f"{key} differs from the campaign accounting")
    for view in calibration.VIEWS:
        for key in ("documents", "canonical_bytes"):
            if int(counted["views"][view][key]) != members[view][key]:
                raise SealError(f"{view} {key} differs from the campaign accounting")
    status = sufficiency(members, config["stop"]["targets"])
    if any(entry["status"] != "SUFFICIENT" for entry in status.values()):
        raise SealError("a first-pass target is not met; the first pass is not complete")
    if set(admissions) != set(calibration.VIEWS):
        raise SealError("every view needs its bound production admission")
    binding = config["binding"]
    seal: dict[str, Any] = {
        "kind": SEAL_KIND,
        "version": SEAL_VERSION,
        "stage": STAGE,
        "is_not": [
            "final training exposure",
            "a C05-deduplicated or benchmark-screened pool",
            "a tokenizer-fit corpus",
            "an exact XLM-token count",
            "the final 6B freeze",
        ],
        "source": {
            "source_id": binding["source_id"],
            "repository": binding["repository"],
            "revision": binding["revision"],
            "adapter_id": binding["adapter_id"],
            "canonicalization": binding["canonicalization"],
            "selector": dict(binding["selector"]),
            "views": list(calibration.VIEWS),
        },
        "campaign": {
            "digest": config["digest"],
            "kind": config["kind"],
            "version": config["version"],
            "supersedes": dict(config["supersedes"]),
            "inventory": dict(config["inventory"]),
            "batch": dict(config["batch"]),
            "roots": dict(config["roots"]),
            "quotas": dict(config["quotas"]),
            "adapter_code_sha256": dict(config["adapter_code_sha256"]),
            "transport_code_sha256": dict(config["transport_code_sha256"]),
            "calibration_freeze_digest": config["calibration_freeze_digest"],
            "calibration_seal_digest": config["calibration_seal_digest"],
            "raw_contract_id": fast.RAW_CONTRACT_ID,
            "c05_obligation": dict(config["c05"]["obligation"]),
        },
        "amendments": dict(amendments),
        "admissions": dict(admissions),
        "batches": [dict(b) for b in batches],
        "units": units,
        "membership": members,
        "views_disjoint": "each source row reaches at most one view (row conservation is "
        "checked per unit receipt)",
        "totals": totals,
        "token_method": calibration.TOKEN_METHOD,
        "sufficiency": status,
        "exposure_policy": dict(EXPOSURE_POLICY),
        "pending": dict(PENDING),
        "verification": dict(VERIFICATION_POLICY),
    }
    seal["digest"] = canonical.digest(seal)
    return seal


def check_seal(seal: Mapping[str, Any]) -> None:
    """Refuse an altered seal or one whose summaries disagree with its own units."""
    body = {key: value for key, value in seal.items() if key != "digest"}
    if canonical.digest(body) != seal.get("digest"):
        raise SealError("seal does not match its own digest")
    if seal.get("kind") != SEAL_KIND or seal.get("version") != SEAL_VERSION:
        raise SealError("not an Essential-Web first-pass seal")
    units = seal["units"]
    for view in calibration.VIEWS:
        if membership(units, view) != seal["membership"][view]:
            raise SealError(f"{view} membership differs from the sealed units")
    if _totals(units) != seal["totals"]:
        raise SealError("totals differ from the sealed units")
    if seal["pending"]["training_permitted"] is not False:
        raise SealError("a first-pass seal never permits training")
