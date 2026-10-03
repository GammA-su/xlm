"""Historical C05 membership/decision lines for a dense-id range (pure, read-only).

Rows are produced in ``ORDER BY id`` (ascending dense) with exactly the historical
entry dictionary, canonical bytes and decision rule: ``excluded`` when the
document's family has a benchmark hit, else ``kept`` for the duplicate-group
survivor, else ``duplicate``. Aggregates are order-independent sums.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from xlm.data.evidence_v2 import canonical

if TYPE_CHECKING:
    from xlm.data.exclusion.grouping import GroupRole

SPLITS = ("train", "diagnostic_val", "audit")


def _bucket() -> dict[str, int]:
    return {"kept": 0, "train_bytes": 0, "excluded": 0, "duplicate": 0}


def publish_rows(
    role: GroupRole, index: int, lo: int, hi: int
) -> tuple[int, bytes, bytes, dict[str, Any]]:
    arrays = role.arrays
    offsets = role.offsets
    where = np.asarray(arrays.get("where.u32", "<u4")[lo:hi], dtype=np.int64)
    dup = np.asarray(arrays.get("dup.u32", "<u4")[lo:hi])
    fam = np.asarray(arrays.get("fam.u32", "<u4")[lo:hi])
    survivor = arrays.get("survivor.u8", np.uint8)[lo:hi].tolist()
    families = np.asarray(arrays.get("families.u32", "<u4"))
    rank = np.searchsorted(families, fam)
    hits = np.asarray(arrays.get("fam_hit.u8", np.uint8))[rank].tolist()
    splits = np.asarray(arrays.get("fam_split.u8", np.uint8))[rank].tolist()
    quick = np.asarray(arrays.get("fam_quick.u8", np.uint8))[rank].tolist()
    unit_of = (np.searchsorted(offsets, where, side="right") - 1).tolist()
    rows = (where - offsets[np.asarray(unit_of, dtype=np.int64)]).tolist()
    ids = arrays.id_strings(np.arange(lo, hi))
    dup_ids = arrays.id_strings(dup)
    fam_ids = arrays.id_strings(fam)
    decisions: list[bytes] = []
    members: list[bytes] = []
    components: dict[str, dict[str, int]] = {}
    allocations: dict[str, dict[str, int]] = {}
    totals = {"documents": 0, "kept": 0, "excluded": 0, "duplicates": 0}
    records: dict[int, Any] = {}
    for position in range(hi - lo):
        unit, row = unit_of[position], rows[position]
        context = role.init.contexts[unit]
        record = records.get(unit)
        if record is None:
            record = records[unit] = role.units[unit].records()
        size = int(record["bytes"][row])
        hit, split = hits[position], SPLITS[splits[position]]
        decision = "excluded" if hit else "kept" if survivor[position] else "duplicate"
        entry = {
            "doc_id": ids[position],
            "source_id": context.source_id,
            "component": context.component,
            "view": context.view,
            "file": context.path,
            "row": row + 1,
            "content": bytes(record["content"][row]).hex(),
            "bytes": size,
            "duplicate_group": dup_ids[position],
            "lineage_group": fam_ids[position],
            "decision": decision,
            "split": split,
            "quick": bool(quick[position]),
            "upstream_component": context.upstream,
        }
        raw = canonical.canonical_bytes(entry) + b"\n"
        decisions.append(raw)
        if decision == "kept":
            members.append(raw)
        totals["documents"] += 1
        totals["kept"] += decision == "kept"
        totals["excluded"] += decision == "excluded"
        totals["duplicates"] += decision == "duplicate"
        counts = components.setdefault(context.component, _bucket())
        counts[decision] += 1
        allocation = canonical.canonical_bytes(
            [context.component, context.view, context.upstream]
        ).decode()
        subcounts = allocations.setdefault(allocation, _bucket())
        subcounts[decision] += 1
        if decision == "kept" and split == "train":
            counts["train_bytes"] += size
            subcounts["train_bytes"] += size
    return (
        index,
        b"".join(decisions),
        b"".join(members),
        {"totals": totals, "components": components, "allocations": allocations},
    )


def merge_counts(target: dict[str, dict[str, int]], part: dict[str, dict[str, int]]) -> None:
    for key, counts in part.items():
        bucket = target.setdefault(key, _bucket())
        for name, value in counts.items():
            bucket[name] += value
