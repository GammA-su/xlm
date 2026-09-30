"""Offline operational models for the frozen Essential-Web production path.

No network, approvals, source text, selector changes or assumed token counts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.adapters import essential_web_selector as selector
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v2.inventory import verify_inventory_freeze

REPOSITORY = "EssentialAI/essential-web-v1.0"
SEED = 20260930
ROWS_PER_CRAWL = 2048
MIB = 1024**2


def check_binding(binding: Mapping[str, Any]) -> None:
    """Refuse source, adapter, or selector substitution before operational planning."""
    expected = {
        "repository": REPOSITORY,
        "revision": selector.SOURCE_REVISION,
        "adapter_id": "essential_web_bnormal",
        "selector": selector.selector_identity(),
    }
    if any(binding.get(k) != v for k, v in expected.items()):
        raise ValueError("Essential-Web production binding mismatch")


def source_binding() -> dict[str, Any]:
    return {
        "source_id": "essential_web",
        "repository": REPOSITORY,
        "revision": selector.SOURCE_REVISION,
        "adapter_id": "essential_web_bnormal",
        "selector": selector.selector_identity(),
        "canonicalization": (
            "EssentialWebSelectedAdapter -> EssentialWebAdapter -> CanonicalDocument"
        ),
    }


def adopt_paths(freeze: Mapping[str, Any]) -> list[str]:
    """Reconstruct only the eight fully observed strata, with original hash checks."""
    verified = verify_inventory_freeze(freeze)
    paths = [path for stratum in verified["strata"] for path in stratum["files"]]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate production inventory paths")
    return paths


def transfer_model(files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cost of one full projected row-group read versus two passes.

    Independence is a planning approximation, not a corpus guarantee. Without
    usable row/page indexes the simple two-pass implementation reads the text
    chunk whenever any row in the group is admitted. All three views share it.
    """
    if not files:
        raise ValueError("physical evidence is required")
    probability = (24 + 117 + 372) / 4096
    full = metadata_first = 0.0
    groups: list[dict[str, Any]] = []
    for file in files:
        rows = int(file["group_rows"])
        metadata = int(file["selector_compressed_bytes"])
        rest = int(file["projected_compressed_bytes"]) - metadata
        overhead = int(file["footer_bytes"]) + 65536 + 4
        if rows <= 0 or metadata <= 0 or rest <= 0:
            raise ValueError("invalid physical model quantities")
        needed = -math.expm1(rows * math.log1p(-probability))
        full += metadata + rest + overhead
        metadata_first += metadata + needed * rest + overhead
        groups.append({"file": file["file"], "rows": rows, "needed_text_chunk_fraction": needed})
    saving = 1 - metadata_first / full
    return {
        "basis": "observed compressed row-group chunks; no retries; windows may cost less",
        "assumption": "independent admissions within a row group; clustering unmeasured",
        "admission_probability": probability,
        "full_text_first_bytes": math.ceil(full),
        "metadata_first_bytes": math.ceil(metadata_first),
        "expected_saving_fraction": saving,
        "implementation_threshold": 0.25,
        "strategy": "metadata_first" if saving >= 0.25 else "full_text_first",
        "granularity": "column chunk within a row group; sequential pages, no row seek",
        "groups": groups,
    }


def calibration_plan(files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Freeze eight new prefix windows on footer-certified files; no replacement."""
    if len(files) != 8 or len({f["crawl"] for f in files}) != 8:
        raise ValueError("calibration needs exactly eight distinct crawls")
    windows = []
    for file in files:
        if int(file["group_rows"]) < ROWS_PER_CRAWL:
            raise ValueError("first row group is too short; no adaptive replacement")
        m_start, m_stop = file["confirmation_window"]
        if m_start < ROWS_PER_CRAWL and m_stop > 0:
            raise ValueError("calibration overlaps confirmation")
        windows.append(
            {
                "file": file["file"],
                "crawl": file["crawl"],
                "row_group": 0,
                "range": [0, ROWS_PER_CRAWL],
                "rows": ROWS_PER_CRAWL,
                "remote_length": file["remote_length"],
                "strong_etag": file["strong_etag"],
                "footer_sha256": file["footer_sha256"],
            }
        )
    result = {
        "kind": "essential_web_operational_calibration",
        "binding": source_binding(),
        "seed": SEED,
        "files_selection": "reuse eight footer-certified M files, historical frozen hash rank",
        "window_rule": (
            "first 2048 rows of group 0; disjoint from development files and confirmation windows"
        ),
        "replacement": "NONE; missing file, changed footer or short window stops",
        "expected_rows": 16384,
        "windows": windows,
        "expected_yields_not_required": {
            "essential_science": 96,
            "essential_practical": 468,
            "essential_prose": 1488,
        },
        "selector": selector.selector_identity(),
        "live_run": False,
        "bias": "eight clustered crawl/file/prefix windows; not unbiased corpus estimates",
    }
    result["digest"] = canonical.digest(result)
    return result


def science_capacity(
    quotas: Mapping[str, Any],
    file_count: int,
    files: Sequence[Mapping[str, Any]],
    measurement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Quota-preserving capacity model; missing yield stays unknown, never zero."""
    target = int(quotas["final_quotas"]["essential_science"])
    headroom = int(quotas["first_pass_headroom_quotas"]["essential_science"])
    mean_rows = sum(int(f["file_rows"]) for f in files) / len(files)
    mean_bytes = sum(int(f["remote_length"]) for f in files) / len(files)
    result: dict[str, Any] = {
        "final_science_exact_token_quota": target,
        "first_pass_science_estimated_token_target": headroom,
        "headroom_ratio": headroom / target,
        "science_admission_per_input_row": 24 / 4096,
        "scanned_rows_per_admitted_science_row": 4096 / 24,
        "inventory_files": file_count,
        "mean_observed_file_rows": mean_rows,
        "mean_observed_file_bytes": mean_bytes,
        "extrapolated_inventory_rows": mean_rows * file_count,
        "extrapolated_inventory_file_bytes": mean_bytes * file_count,
        "minimum_estimated_tokens_per_science_document_for_headroom": (
            headroom / (mean_rows * file_count * (24 / 4096))
        ),
        "extrapolation_is_capacity_proof": False,
        "required_rows": None,
        "required_files": None,
        "expected_transferred_bytes": None,
        "source_capacity_sufficient": None,
        "formula": (
            f"rows = {headroom} / measured(science retained bytes / 4 / input rows); "
            "files = ceil(rows / mean file rows)"
        ),
        "token_sizing": "mix01_inventory: assumed bytes/token 3/4/5; estimated tokens only",
        "blocker": "B-normal retained science bytes and physical bytes/input row not measured",
    }
    if measurement is not None:
        scanned = int(measurement["input_rows"])
        canonical_bytes = int(measurement["science_canonical_bytes"])
        transfer = int(measurement["transferred_bytes"])
        if min(scanned, canonical_bytes, transfer) <= 0:
            raise ValueError("positive observed science yield and transfer required")
        rows = math.ceil(headroom / (canonical_bytes / 4 / scanned))
        result.update(
            required_rows=rows,
            required_files=math.ceil(rows / mean_rows),
            expected_transferred_bytes=math.ceil(rows * transfer / scanned),
            estimated_inventory_sufficient=rows <= mean_rows * file_count,
            estimated_capacity_deficit_files=max(0, math.ceil(rows / mean_rows) - file_count),
            blocker="capacity needs live breadth validation; clustered calibration is not proof",
        )
    return result
