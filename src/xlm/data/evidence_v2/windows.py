"""Frozen window-v2 identities and dry window plans (Arm M).

Protocol section 4: ``I(parts,m) = int(SHA256(UTF8("|".join(["20260927",
*parts]))).hexdigest(),16) % m``. Eligible group number is
``I([source,view,revision,file,"window-v2","group"], n_eligible)``;
with ``K=min(512,D)`` the start is
``I([source,view,revision,file,str(g),"window-v2","start"],D-K+1)``
and the absolute half-open window is ``[group_start+s, group_start+s+K)``.
``K != 512`` refuses the file/arm: no group rerank to fill it.

The index construction delegates to the existing tested
``sampling._det_index`` with the frozen string seed, so there is exactly
one implementation of the hash. Full ``plan_sample_windows`` execution
happens only after authorized footer inspection; until then this module
emits dry plans with explicitly unresolved physical information.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition.sampling import _det_index
from xlm.data.evidence_v2 import frozen


class WindowError(ValueError):
    """Any window-identity or eligibility violation: refuse, never resample."""


def index(parts: Sequence[str], modulus: int) -> int:
    """Protocol ``I(parts, m)`` with the frozen metadata seed."""
    if modulus <= 0:
        raise WindowError("window index requires a positive modulus")
    return _det_index(frozen.METADATA_SEED, tuple(parts), modulus)


def window_parts(*parts: str) -> tuple[str, ...]:
    """Exact identity parts: source, view, revision lead every construction."""
    return (
        frozen.WINDOW_SOURCE_ID,
        frozen.WINDOW_VIEW_ID,
        frozen.REVISION,
        *parts,
    )


def choose_group(source_file: str, n_eligible_groups: int) -> int:
    """Eligible group number; refusal when no group is eligible."""
    if n_eligible_groups <= 0:
        raise WindowError(f"no eligible row groups in {source_file}")
    return index(
        window_parts(source_file, frozen.WINDOW_VERSION_LABEL, "group"),
        n_eligible_groups,
    )


def start_domain(group_rows: int, max_scan_rows: int = frozen.MAX_SCAN_ROWS_PER_FILE) -> int:
    """Start domain ``D``: ``N`` when inside the scan bound else whole batches."""
    if group_rows <= 0:
        raise WindowError("row group has no rows")
    if group_rows <= max_scan_rows:
        return group_rows
    return (max_scan_rows // frozen.WINDOW_BATCH_ROWS) * frozen.WINDOW_BATCH_ROWS


def choose_start(source_file: str, group_index: int, domain: int, keep: int) -> int:
    """Window start ``s`` in ``[0, D-K]``; ``K != 512`` is refused upstream."""
    if keep != frozen.WINDOW_ROWS_PER_FILE:
        raise WindowError(
            f"K={keep} is not the frozen 512-row window for {source_file}: refuse, no rerank"
        )
    if domain < keep:
        raise WindowError(f"start domain {domain} < 512 in {source_file}: refuse")
    return index(
        window_parts(source_file, str(group_index), frozen.WINDOW_VERSION_LABEL, "start"),
        domain - keep + 1,
    )


def expected_scan_rows(group_rows: int, stop_in_group: int) -> int:
    """Decoded scan rows: batches of 256 covering the window stop."""
    if stop_in_group <= 0 or stop_in_group > group_rows:
        raise WindowError("window stop is outside its row group")
    return min(
        group_rows, math.ceil(stop_in_group / frozen.WINDOW_BATCH_ROWS) * frozen.WINDOW_BATCH_ROWS
    )


def freeze_window(
    source_file: str,
    eligible_group_indices: Sequence[int],
    group_rows: Sequence[int],
    group_starts: Sequence[int],
) -> dict[str, Any]:
    """Freeze absolute and group-relative window indices for one file.

    ``eligible_group_indices`` are physical group numbers in ascending
    order; ``group_rows``/``group_starts`` are parallel arrays. The chosen
    eligible group number indexes into the eligible list (protocol: group
    *number* among eligible groups).
    """
    if not (len(eligible_group_indices) == len(group_rows) == len(group_starts)):
        raise WindowError(f"parallel group arrays disagree for {source_file}")
    slot = choose_group(source_file, len(eligible_group_indices))
    group_index = int(eligible_group_indices[slot])
    rows = int(group_rows[slot])
    domain = start_domain(rows)
    keep = min(frozen.WINDOW_ROWS_PER_FILE, domain)
    start = choose_start(source_file, group_index, domain, keep)
    absolute_start = int(group_starts[slot]) + start
    return {
        "source_file": source_file,
        "eligible_group_slot": slot,
        "group_index": group_index,
        "group_rows": rows,
        "group_start": int(group_starts[slot]),
        "start_domain": domain,
        "keep": keep,
        "start_in_group": start,
        "absolute_window": [absolute_start, absolute_start + keep],
        "expected_scan_rows": expected_scan_rows(rows, start + keep),
    }


def sampling_request(
    source_file: str,
) -> dict[str, Any]:
    """Pinned single-file window-v2 sampling request for authorized planning.

    Data-only: layouts/footers are unresolved until the authorized
    footer-planning stage supplies them. Executing ``plan_sample_windows``
    with these exact fields reproduces the frozen identities above.
    """
    return {
        "source_id": frozen.WINDOW_SOURCE_ID,
        "view_id": frozen.WINDOW_VIEW_ID,
        "revision": frozen.REVISION,
        "seed": frozen.METADATA_SEED,
        "mode": "window",
        "window_policy_version": 2,
        "files": [source_file],
        "block_records": frozen.WINDOW_ROWS_PER_FILE,
        "target_records": frozen.WINDOW_ROWS_PER_FILE,
        "projected_fields": list(frozen.PROJECTION_METADATA),
        "physical_layout": "UNRESOLVED_requires_authorized_footer",
        "window_indices": "UNRESOLVED_until_layouts_observed",
    }


def dry_arm_plan(winners: Sequence[str]) -> dict[str, Any]:
    """Eight dry single-file plans; physical costs explicitly unresolved."""
    if len(winners) != 8:
        raise WindowError("Arm M needs exactly eight frozen files")
    return {
        "kind": "essential_web_evidence_v2_dry_arm_m_plan",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "plans": [sampling_request(w) for w in winners],
        "physical_unknowns": [
            "footer bytes per file",
            "row-group layouts",
            "compressed/uncompressed chunk lengths",
            "decoder workspace upper bounds",
            "request/range counts",
        ],
        "executable": False,
        "note": (
            "Not executable: no footer/cost evidence exists. Executable plans "
            "require separately authorized footer inspection first."
        ),
    }


def check_projection(order: Sequence[str]) -> None:
    """Projection must be exactly ``["eai_taxonomy", "quality_signals"]``."""
    if list(order) != list(frozen.PROJECTION_METADATA):
        raise WindowError(f"projection is not the frozen metadata pair: {list(order)}")


def check_plan_shape(plans: Mapping[str, Any]) -> None:
    """Eight separate 512-row plans; never one 4096-record plan."""
    items = plans.get("plans")
    if not isinstance(items, list) or len(items) != 8:
        raise WindowError("Arm M must hold eight independent plans")
    for item in items:
        if item.get("block_records") != 512 or item.get("target_records") != 512:
            raise WindowError("each Arm M plan retains exactly 512 rows")
