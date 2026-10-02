"""Write-once reviewed bounds and upstream-component requirements; no default decisions."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition import component_calibration as cc
from xlm.data.acquisition.source_growth import ProcessingGrowth
from xlm.data.evidence_v2 import canonical

BOUNDS_KIND = "mix01-component-reviewed-bounds-v1"
SPLIT_KIND = "mix01-upstream-component-requirements-v1"
BOUND_FIELDS = (
    "max_file_bytes",
    "max_decoded_bytes_per_file",
    "max_decompression_ratio",
    "max_rows_per_file",
    "max_record_bytes",
    "max_canonical_bytes_per_file",
    "max_durable_bytes_per_file",
    "max_ledger_bytes",
    "scratch_cap_bytes",
    "file_deadline_seconds",
    "plan_deadline_seconds",
)


class ComponentPolicyError(ValueError):
    """Missing or inconsistent reviewed policy; never infer an operator decision."""


def binding(calibration: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: calibration[k]
        for k in (
            "source_id",
            "component_id",
            "repository",
            "revision",
            "component_allowlist_digest",
            "production_inventory_digest",
        )
    } | {"view_id": calibration["component_id"], "calibration_digest": calibration["digest"]}


def seal(body: dict[str, Any]) -> dict[str, Any]:
    body["digest"] = canonical.digest(body)
    return body


def build_bounds(
    calibration: Mapping[str, Any], bounds: Mapping[str, Any], *, operator: str, rationale: str
) -> dict[str, Any]:
    cc.check_calibration(
        calibration, allowlist=calibration["allowlist"], inventory=calibration["inventory_snapshot"]
    )
    if not operator.strip() or not rationale.strip():
        raise ComponentPolicyError("reviewed bounds need an operator and rationale")
    if set(bounds) != {*BOUND_FIELDS, "processing_growth"}:
        raise ComponentPolicyError(
            "reviewed bounds must specify every ceiling and no unknown field"
        )
    for key in BOUND_FIELDS:
        value = bounds[key]
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ComponentPolicyError(f"{key} must be numeric")
        if not math.isfinite(value) or value <= 0:
            raise ComponentPolicyError(f"{key} must be finite and positive")
        if key not in {"max_decompression_ratio", "file_deadline_seconds", "plan_deadline_seconds"}:
            if type(value) is not int:
                raise ComponentPolicyError(f"{key} must be an integer")
    growth = ProcessingGrowth.model_validate(bounds["processing_growth"])
    if bounds["max_file_bytes"] < max(
        e["size_bytes"] for e in calibration["inventory_snapshot"]["files"]
    ):
        raise ComponentPolicyError("reviewed file ceiling does not cover the production inventory")
    if growth.source_max_bytes != bounds["max_file_bytes"]:
        raise ComponentPolicyError("growth source ceiling must equal max_file_bytes")
    if growth.output_bytes + growth.source_max_bytes > bounds["max_durable_bytes_per_file"]:
        raise ComponentPolicyError("durable ceiling does not cover source plus processing outputs")
    if (
        growth.source_max_bytes + growth.processing_peak + growth.state_peak
        > bounds["scratch_cap_bytes"]
    ):
        raise ComponentPolicyError("scratch ceiling does not cover one processing envelope")
    if bounds["max_canonical_bytes_per_file"] > growth.output_bytes:
        raise ComponentPolicyError("processing growth does not cover canonical bytes")
    if bounds["file_deadline_seconds"] > bounds["plan_deadline_seconds"]:
        raise ComponentPolicyError("file deadline exceeds plan deadline")
    return seal(
        {
            "kind": BOUNDS_KIND,
            "binding": binding(calibration),
            "bounds": dict(bounds),
            "operator": operator.strip(),
            "rationale": rationale.strip(),
        }
    )


def check_bounds(record: Mapping[str, Any], calibration: Mapping[str, Any]) -> dict[str, Any]:
    rebuilt = build_bounds(
        calibration, record["bounds"], operator=record["operator"], rationale=record["rationale"]
    )
    if dict(record) != rebuilt:
        raise ComponentPolicyError("reviewed bounds identity/digest is stale or tampered")
    return rebuilt


def build_split(
    calibration: Mapping[str, Any], shares: Mapping[str, Any], *, operator: str, rationale: str
) -> dict[str, Any]:
    cc.check_calibration(
        calibration, allowlist=calibration["allowlist"], inventory=calibration["inventory_snapshot"]
    )
    if not operator.strip() or not rationale.strip():
        raise ComponentPolicyError("component split needs an operator and rationale")
    if shares == {"strategy": "hash_prefix"}:
        return seal(
            {
                "kind": SPLIT_KIND,
                "binding": binding(calibration),
                "strategy": "hash_prefix",
                "components": {},
                "total_final_tokens": 300_000_000,
                "total_first_pass_tokens": 330_000_000,
                "operator": operator.strip(),
                "rationale": rationale.strip(),
            }
        )
    if set(shares) != set(calibration["components"]):
        raise ComponentPolicyError("split must cover exactly all allowlisted components")
    entries = {}
    for component, share in sorted(shares.items()):
        if set(share) != {"final_tokens", "first_pass_tokens"}:
            raise ComponentPolicyError("each split share needs final_tokens and first_pass_tokens")
        if any(type(v) is not int or v <= 0 for v in share.values()):
            raise ComponentPolicyError("split token shares must be positive integers")
        if share["first_pass_tokens"] < share["final_tokens"]:
            raise ComponentPolicyError("first-pass share is below final share")
        entries[component] = dict(share) | {
            "required_canonical_bytes": 4 * share["first_pass_tokens"]
        }
    if sum(e["final_tokens"] for e in entries.values()) != 300_000_000:
        raise ComponentPolicyError("split final tokens must total 300000000")
    if sum(e["first_pass_tokens"] for e in entries.values()) != 330_000_000:
        raise ComponentPolicyError("split first-pass tokens must total 330000000")
    return seal(
        {
            "kind": SPLIT_KIND,
            "binding": binding(calibration),
            "components": entries,
            "total_final_tokens": 300_000_000,
            "total_first_pass_tokens": 330_000_000,
            "operator": operator.strip(),
            "rationale": rationale.strip(),
        }
    )


def check_split(record: Mapping[str, Any], calibration: Mapping[str, Any]) -> dict[str, Any]:
    shares: dict[str, Any] = {
        c: {k: e[k] for k in ("final_tokens", "first_pass_tokens")}
        for c, e in record["components"].items()
    }
    if record.get("strategy") == "hash_prefix":
        shares = {"strategy": "hash_prefix"}
    rebuilt = build_split(
        calibration, shares, operator=record["operator"], rationale=record["rationale"]
    )
    if dict(record) != rebuilt:
        raise ComponentPolicyError("component split identity/digest is stale or tampered")
    return rebuilt


def select_files(
    policy: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    acquired: Mapping[str, int],
    cursors: Mapping[str, int],
    safety: float,
    eligible: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Independent deterministic component prefixes; excess in one never fills another."""
    calibration = policy["calibration"]
    split = check_split(policy["component_split"], calibration)
    if inventory != calibration["inventory_snapshot"]:
        raise ComponentPolicyError("component selection inventory differs from calibration")
    if split.get("strategy") == "hash_prefix":
        start = cursors.get("__hash__", 0)
        deficit = 1_320_000_000 - sum(acquired.values())
        if deficit <= 0:
            raise ComponentPolicyError("hash-prefix first-pass requirement is already met")
        wanted = math.ceil(deficit * safety / calibration["combined"]["canonical_bytes_per_file"])
        stop = start + wanted
        if stop > eligible:
            raise ComponentPolicyError("hash-prefix inventory capacity exhausted")
        return (
            [
                {"rank": rank, "file": inventory["files"][rank]["file"]}
                for rank in range(start, stop)
            ],
            {"__hash__": stop},
        )
    selected = []
    following = dict(cursors)
    for component, share in split["components"].items():
        deficit = max(0, share["required_canonical_bytes"] - acquired.get(component, 0))
        following.setdefault(component, 0)
        if not deficit:
            continue
        ratio = calibration["components"][component]["measured"][
            "canonical_bytes_per_compressed_byte"
        ]
        estimate = 0.0
        for rank, entry in enumerate(inventory["files"][:eligible]):
            if rank < following[component] or entry["file"].split("/")[0] != component:
                continue
            selected.append({"rank": rank, "file": entry["file"]})
            following[component] = rank + 1
            estimate += entry["size_bytes"] * ratio
            if estimate >= deficit * safety:
                break
        if estimate < deficit * safety:
            raise ComponentPolicyError(
                f"component {component} lacks estimated capacity; "
                "no substitution or renormalization is allowed"
            )
    if not selected:
        raise ComponentPolicyError("all component first-pass requirements are already met")
    return sorted(selected, key=lambda e: e["rank"]), following


def expected_selection(
    calibration: Mapping[str, Any], files: Sequence[str], downloads: Sequence[str]
) -> dict[str, Any]:
    """Exact inventory transfer; each component retains its own sampled density."""
    inventory = {e["file"]: e for e in calibration["inventory_snapshot"]["files"]}
    if len(set(files)) != len(files) or not set(downloads) <= set(files):
        raise ComponentPolicyError("duplicate selected files or foreign downloads")
    if len(set(downloads)) != len(downloads) or not set(files) <= inventory.keys():
        raise ComponentPolicyError("duplicate downloads or files outside calibrated inventory")
    components: dict[str, Any] = {}
    for name in files:
        component = name.split("/")[0]
        measured = calibration["components"][component]["measured"]
        entry = components.setdefault(
            component, {"files": 0, "compressed_bytes": 0, "canonical_bytes": 0.0, "rows": 0.0}
        )
        size = inventory[name]["size_bytes"]
        entry["files"] += 1
        entry["compressed_bytes"] += size
        entry["canonical_bytes"] += size * measured["canonical_bytes_per_compressed_byte"]
        entry["rows"] += size / measured["compressed_bytes_per_row"]
    canonical_bytes = math.floor(sum(e["canonical_bytes"] for e in components.values()))
    return {
        "files": len(files),
        "rows": math.ceil(sum(e["rows"] for e in components.values())),
        "canonical_bytes": canonical_bytes,
        "estimated_tokens": canonical_bytes // 4,
        "transfer_bytes": sum(inventory[name]["size_bytes"] for name in downloads),
        "requests": 2 * len(downloads),
        "components": components,
        "basis": "exact selected inventory sizes; component-specific biased prefix densities; "
        "rows rounded up, canonical bytes rounded down; /4 token estimates; "
        "actual yields and costs require production receipts",
    }
