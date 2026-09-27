"""Sparse exact-locator text retention layer (Arm T, protocol section 7).

The existing contiguous parquet window writer is insufficient: it would
emit the hull interval including gap rows. This layer plans per-file
decoding by (file, row group), decodes each required group once up to
the batch covering the greatest selected row, and membership-filters
before retention/serialization so gap rows are decoded but never
emitted, displayed, logged, or inspected. Absolute source rows are
preserved. Statuses: ``full_text_available``,
``unreviewable_full_document_due_to_size`` (>65536 UTF-8 bytes, no
excerpt, no truncation), ``unreviewable_missing_or_invalid_text``, and
``acquisition_incomplete`` with an explicit reason. Exact-text aliases
consolidate only review multiplicity after byte equality is confirmed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.evidence_v2 import frozen


class SparseError(ValueError):
    """Any membership, bound, alias, or substitution violation: refuse."""


def group_wanted_rows(
    wanted_rows: Sequence[int], group_bounds: Sequence[tuple[int, int]]
) -> dict[int, list[int]]:
    """Assign wanted absolute rows to row-group indices (ascending)."""
    if not wanted_rows:
        raise SparseError("no wanted rows for this file")
    plan: dict[int, list[int]] = {}
    for row in sorted(wanted_rows):
        placed = False
        for index, (start, stop) in enumerate(group_bounds):
            if start <= row < stop:
                plan.setdefault(index, []).append(row)
                placed = True
                break
        if not placed:
            raise SparseError(f"wanted row {row} is in no known row group")
    return plan


def decode_batches(group_start: int, greatest_row: int) -> list[tuple[int, int]]:
    """256-row batches from the group start covering the greatest row."""
    if greatest_row < group_start:
        raise SparseError("greatest wanted row precedes its group start")
    batches: list[tuple[int, int]] = []
    stop = ((greatest_row - group_start) // frozen.WINDOW_BATCH_ROWS + 1) * frozen.WINDOW_BATCH_ROWS
    cursor = group_start
    while cursor < group_start + stop:
        batches.append((cursor, cursor + frozen.WINDOW_BATCH_ROWS))
        cursor += frozen.WINDOW_BATCH_ROWS
    return batches


def retain_selected(
    decoded: Sequence[tuple[int, Any]], wanted: frozenset[int]
) -> tuple[list[tuple[int, Any]], int]:
    """Emit only wanted (absolute row, value) pairs; count decoded gaps."""
    kept = [(row, value) for row, value in decoded if row in wanted]
    if len({row for row, _ in kept}) != len(kept):
        raise SparseError("duplicate decoded row inside one group")
    return kept, len(decoded) - len(kept)


def utf8_length(text: str) -> int:
    return len(text.encode("utf-8"))


def classify_retained(value: Any, *, locator: Sequence[Any], reason: str = "") -> dict[str, Any]:
    """Exactly one acquisition status per selected locator; no coercion."""
    base = {"locator": list(locator)}
    if reason:
        return {**base, "status": "acquisition_incomplete", "reason": reason}
    if not isinstance(value, str) or not value:
        return {**base, "status": "unreviewable_missing_or_invalid_text"}
    try:
        size = utf8_length(value)
    except (UnicodeEncodeError, ValueError):
        return {**base, "status": "unreviewable_missing_or_invalid_text"}
    if size > frozen.ARM_T_LIMITS["document_bytes_max"]:
        return {
            **base,
            "status": "unreviewable_full_document_due_to_size",
            "utf8_bytes": size,
            "excerpt": None,
        }
    return {
        **base,
        "status": "full_text_available",
        "utf8_bytes": size,
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def check_retained_budget(items: Sequence[Mapping[str, Any]]) -> int:
    """Unique full-text bytes must fit the retained-text ceiling; shared once."""
    seen: set[str] = set()
    total = 0
    for item in items:
        if item.get("status") != "full_text_available":
            continue
        digest = str(item["sha256"])
        if digest in seen:
            continue
        seen.add(digest)
        total += int(item["utf8_bytes"])
    if total > frozen.ARM_T_LIMITS["retained_text_bytes_max"]:
        raise SparseError("retained unique text exceeds the arm ceiling")
    return total


def consolidate_aliases(
    items: Sequence[Mapping[str, Any]],
    stratum_rank: Mapping[str, int],
    crawl_rank: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Group byte-identical texts; representative by precedence/crawl/locator.

    Changes review multiplicity only: acquisition membership (the frozen
    selection) is never altered, aliases are never backfilled, and no row
    is ever substituted.
    """
    by_text: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        if item.get("status") != "full_text_available":
            continue
        by_text.setdefault(str(item["sha256"]), []).append(item)
    consolidated: list[dict[str, Any]] = []
    for digest, group in sorted(by_text.items()):
        ordered = sorted(
            group,
            key=lambda g: (
                stratum_rank[str(g["stratum"])],
                crawl_rank[str(g["crawl"])],
                str(g["locator"][2]).encode("utf-8"),
                int(g["locator"][3]),
            ),
        )
        consolidated.append(
            {
                "sha256": digest,
                "representative": list(ordered[0]["locator"]),
                "aliases": [list(g["locator"]) for g in ordered],
                "strata": sorted({str(g["stratum"]) for g in group}),
            }
        )
    return consolidated
