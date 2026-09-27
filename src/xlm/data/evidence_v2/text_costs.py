"""Arm-T metadata-only cost planning (protocol section 7).

Reuses the footer transport, layout discovery, shared Arm-T ledger, and
sparse grouping from the Arm-M implementation. For each development file
holding frozen selected locators, inspects ONLY Parquet footer/chunk
metadata for the text column in row groups containing wanted rows:
offsets, compressed/uncompressed lengths, group sizes. Never reads text
page/range data, never decodes rows, never emits text. Cost uppers are
checked against the frozen T caps; exceeding them refuses the file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xlm.data.acquisition.projection import resolve_projection
from xlm.data.acquisition.sampling import SamplingRefusal, discover_layout_over_ranges
from xlm.data.evidence_v2 import budgets, canonical, frozen, sparse
from xlm.data.evidence_v2.footer import (
    FakeFooterTransport,
    LiveFooterTransport,
    _charging_closure,
)


class TextCostError(ValueError):
    """Any membership, metadata, cap, or budget violation: refuse."""


def wanted_by_file(manifest: Mapping[str, Any]) -> dict[str, list[int]]:
    """Group frozen selected source rows by development file (ascending)."""
    by_file: dict[str, list[int]] = {}
    for cell in manifest["cells"]:
        groups = [cell] if "identities" in cell else cell["crawls"]
        for group in groups:
            for locator in group["identities"]:
                if locator[0] != frozen.REPOSITORY or locator[1] != frozen.REVISION:
                    raise TextCostError(f"locator outside frozen repo/revision: {locator}")
                by_file.setdefault(str(locator[2]), []).append(int(locator[3]))
    canonical.check_ordered_paths(sorted(by_file), what="cost files")
    return {name: sorted(rows) for name, rows in sorted(by_file.items())}


def text_chunks_for_groups(layout: Any, wanted_groups: Mapping[int, list[int]]) -> dict[str, Any]:
    """Text-leaf chunk metadata for wanted groups; page data never touched."""
    try:
        resolved = resolve_projection(layout.field_leaves, ["text"])
    except Exception as exc:
        raise TextCostError(f"text leaf unresolvable: {exc}") from exc
    leaf_paths = [layout.leaf_paths[i] for i in resolved.leaf_indices]
    if len(leaf_paths) != 1:
        raise TextCostError(f"text must resolve to one leaf, got {leaf_paths}")
    text_path = leaf_paths[0]
    groups: list[dict[str, Any]] = []
    total_compressed = 0
    total_uncompressed = 0
    n_chunks = 0
    for group in layout.groups:
        if group.index not in wanted_groups:
            continue
        chunks = [
            {"path": c.path, "compressed_bytes": c.compressed, "uncompressed_bytes": c.uncompressed}
            for c in group.columns
            if c.path == text_path
        ]
        if not chunks:
            raise TextCostError(f"group {group.index} lacks the text chunk")
        compressed = sum(c["compressed_bytes"] for c in chunks)
        uncompressed = sum(c["uncompressed_bytes"] for c in chunks)
        total_compressed += compressed
        total_uncompressed += uncompressed
        n_chunks += len(chunks)
        groups.append(
            {
                "group_index": group.index,
                "group_rows": group.num_rows,
                "wanted_rows": wanted_groups[group.index],
                "text_chunks": chunks,
                "text_compressed_bytes": compressed,
                "text_uncompressed_bytes": uncompressed,
            }
        )
    return {
        "text_leaf": text_path,
        "groups": groups,
        "chunk_count": n_chunks,
        "text_compressed_bytes": total_compressed,
        "text_uncompressed_bytes": total_uncompressed,
    }


def cost_file(
    source_file: str,
    wanted_rows: Sequence[int],
    *,
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
) -> dict[str, Any]:
    """Cost one development file's wanted rows from footer metadata only."""
    state: dict[str, Any] = {}
    try:
        layout = discover_layout_over_ranges(
            source_file,
            _charging_closure(transport, ledger, source_file, state),
            max_parser_bytes=frozen.ARM_T_LIMITS["parser_bytes_max"],
            max_decompression_ratio=float(frozen.ARM_T_LIMITS["ratio_max"]),
        )
    except SamplingRefusal as exc:
        raise TextCostError(f"footer discovery refused for {source_file}: {exc}") from exc
    if layout.schema_refusal is not None:
        raise TextCostError(f"schema refused for {source_file}: {layout.schema_refusal}")
    bounds = [(g.start_row, g.start_row + g.num_rows) for g in layout.groups]
    wanted_groups = sparse.group_wanted_rows(list(wanted_rows), bounds)
    text = text_chunks_for_groups(layout, wanted_groups)
    # Conservative uppers: one range per chunk plus footer slack, whole
    # compressed text plus per-chunk buffer, full group rows scanned.
    requests_upper = text["chunk_count"] + len(wanted_groups) + 4
    transfer_upper = (
        text["text_compressed_bytes"]
        + text["chunk_count"] * frozen.ARM_T_LIMITS["range_buffer_bytes_max"]
    )
    by_index = {g.index: g for g in layout.groups}
    scan_rows = 0
    for entry in text["groups"]:
        group = by_index[entry["group_index"]]
        greatest = max(entry["wanted_rows"])
        batches = (greatest - group.start_row) // frozen.WINDOW_BATCH_ROWS + 1
        scan_rows += min(group.num_rows, batches * frozen.WINDOW_BATCH_ROWS)
    reasons: list[str] = []
    if requests_upper > frozen.ARM_T_LIMITS["requests_per_file_max"]:
        reasons.append(f"request upper {requests_upper} exceeds 100/file")
    if transfer_upper > frozen.ARM_T_LIMITS["data_bytes_per_file_max"]:
        reasons.append(f"transfer upper {transfer_upper} exceeds data/file cap")
    if text["text_uncompressed_bytes"] > frozen.ARM_T_LIMITS["decompressed_bytes_per_file_max"]:
        reasons.append("text uncompressed exceeds 64 MiB/file")
    if scan_rows > frozen.ARM_T_LIMITS["scan_rows_per_file_max"]:
        reasons.append(f"scan upper {scan_rows} exceeds 16384 rows/file")
    unit = {
        "file": source_file,
        "remote_length": state.get("total"),
        "etag": state.get("etag"),
        "wanted_rows": sorted(wanted_rows),
        "text_costs": text,
        "requests_upper": requests_upper,
        "transfer_upper_bytes": transfer_upper,
        "scan_rows_upper": scan_rows,
        "feasible": not reasons,
        "reasons": reasons,
    }
    if reasons:
        raise TextCostError(f"text costs infeasible for {source_file}: {reasons}")
    return unit


class TextCostsIncomplete(TextCostError):
    """Arm-T cost planning stopped: completed units, failed file, budget."""

    def __init__(
        self,
        units: list[dict[str, Any]],
        failed_file: str,
        reason: str,
        budget: dict[str, Any],
    ) -> None:
        super().__init__(f"Arm-T costs INCOMPLETE at {failed_file}: {reason}")
        self.units = units
        self.failed_file = failed_file
        self.reason = reason
        self.budget = budget


def plan_text_costs(
    manifest: Mapping[str, Any],
    *,
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
) -> dict[str, Any]:
    """Cost all files holding frozen locators; first infeasible file stops."""
    if manifest.get("digest") != canonical.self_digest(
        {k: v for k, v in manifest.items() if k != "digest"}
    ):
        raise TextCostError("selection manifest digest mismatch")
    if manifest.get("freeze_digest") != frozen.FREEZE_DIGEST:
        raise TextCostError("selection manifest is not evidence-v2.0")
    if int(manifest.get("total_selected", 0)) > frozen.TEXT_SELECTION_MAX:
        raise TextCostError("selection exceeds 118 rows")
    wanted = wanted_by_file(manifest)
    units: list[dict[str, Any]] = []
    for name, rows in wanted.items():
        try:
            units.append(cost_file(name, rows, transport=transport, ledger=ledger))
        except (TextCostError, budgets.BudgetRefusal) as exc:
            raise TextCostsIncomplete(units, name, str(exc), ledger.snapshot()) from exc
    return {
        "kind": "essential_web_evidence_v2_text_cost_evidence",
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "selection_digest": manifest["digest"],
        "total_selected": manifest["total_selected"],
        "files": units,
        "budget": ledger.snapshot(),
        "status": "COMPLETE",
    }
