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
    FooterError,
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


PAGE_INDEX_NOTE = (
    "not exposed by the current stack: pyarrow offers no page/offset/column "
    "index API and the layout carries no index section, so page-subset "
    "bounds are uncertifiable here"
)


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
        wanted = sorted(wanted_groups[group.index])
        chunks = [
            {
                "path": c.path,
                "offset": c.data_page_offset,
                "compressed_bytes": c.compressed,
                "uncompressed_bytes": c.uncompressed,
                "dictionary_page_offset": c.dictionary_page_offset,
            }
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
        buffer = frozen.ARM_T_LIMITS["range_buffer_bytes_max"]
        groups.append(
            {
                "group_index": group.index,
                "group_rows": group.num_rows,
                "group_start_row": group.start_row,
                "selected_count": len(wanted),
                "selected_min": wanted[0],
                "selected_max": wanted[-1],
                "wanted_rows": wanted,
                "text_chunks": chunks,
                "chunk_count": len(chunks),
                "dictionary_required": any(c["dictionary_page_offset"] is not None for c in chunks),
                "page_index": PAGE_INDEX_NOTE,
                "offset_index": PAGE_INDEX_NOTE,
                "text_compressed_bytes": compressed,
                "text_uncompressed_bytes": uncompressed,
                "scan_upper_rows": _scan_upper(group, wanted),
                "request_upper": len(chunks) + 1,
                "transfer_upper_bytes": compressed + len(chunks) * buffer,
                "decompressed_upper_bytes": uncompressed,
            }
        )
    return {
        "text_leaf": text_path,
        "groups": groups,
        "chunk_count": n_chunks,
        "text_compressed_bytes": total_compressed,
        "text_uncompressed_bytes": total_uncompressed,
    }


def _scan_upper(group: Any, wanted: list[int]) -> int:
    """256-row batches from the group start covering the greatest wanted row."""
    greatest = int(max(wanted))
    batches = (greatest - int(group.start_row)) // frozen.WINDOW_BATCH_ROWS + 1
    return min(int(group.num_rows), batches * frozen.WINDOW_BATCH_ROWS)


FORMULAS: dict[str, str] = {
    "transfer_upper_bytes": (
        "sum(text compressed chunk bytes over wanted groups) + chunk_count * 4194304"
    ),
    "decompressed_upper_bytes": "sum(text uncompressed chunk bytes over wanted groups)",
    "requests_upper": "chunk_count + wanted_group_count + 4",
    "scan_rows_upper": (
        "sum over wanted groups of min(group_rows, batches covering the greatest wanted row * 256)"
    ),
    "workspace_upper_bytes": (
        "decompressed_upper_bytes + 33554432 parser headroom; "
        "per-group workspace shares one parser allowance"
    ),
}


def _fits(value: int, cap: int) -> dict[str, Any]:
    return {"value": value, "cap": cap, "fits": value <= cap}


def cost_file(
    source_file: str,
    wanted_rows: Sequence[int],
    *,
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
    caps: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Cost one development file's wanted rows from footer metadata only.

    Integrity/safety failures (transport, drift, schema, unresolvable
    text leaf, locator outside every group) raise ``TextCostError`` and
    stop planning immediately. Pure future-acquisition cap overruns raise
    ``TextCostInfeasible`` carrying the computed unit, which the caller
    may collect across files without altering the INCOMPLETE verdict.
    ``caps`` defaults to the frozen v2.0 limits; v2.1 callers pass the
    amended transfer ceilings explicitly (same formulas, new bounds).
    """
    limits = dict(frozen.ARM_T_LIMITS) if caps is None else dict(caps)
    state: dict[str, Any] = {}
    try:
        layout = discover_layout_over_ranges(
            source_file,
            _charging_closure(transport, ledger, source_file, state),
            max_parser_bytes=limits["parser_bytes_max"],
            max_decompression_ratio=float(limits["ratio_max"]),
        )
    except SamplingRefusal as exc:
        raise TextCostError(f"footer discovery refused for {source_file}: {exc}") from exc
    except FooterError as exc:
        raise TextCostError(f"footer transport refused for {source_file}: {exc}") from exc
    if layout.schema_refusal is not None:
        raise TextCostError(f"schema refused for {source_file}: {layout.schema_refusal}")
    bounds = [(g.start_row, g.start_row + g.num_rows) for g in layout.groups]
    try:
        wanted_groups = sparse.group_wanted_rows(list(wanted_rows), bounds)
    except sparse.SparseError as exc:
        raise TextCostError(f"locator/group integrity failure for {source_file}: {exc}") from exc
    text = text_chunks_for_groups(layout, wanted_groups)
    # Conservative uppers: one range per chunk plus footer slack, whole
    # compressed text plus per-chunk buffer, full group rows scanned.
    requests_upper = text["chunk_count"] + len(wanted_groups) + 4
    transfer_upper = (
        text["text_compressed_bytes"] + text["chunk_count"] * limits["range_buffer_bytes_max"]
    )
    scan_rows = sum(entry["scan_upper_rows"] for entry in text["groups"])
    workspace_upper = text["text_uncompressed_bytes"] + limits["parser_bytes_max"]
    fits = {
        "transfer": _fits(transfer_upper, limits["data_bytes_per_file_max"]),
        "decompressed": _fits(
            text["text_uncompressed_bytes"], limits["decompressed_bytes_per_file_max"]
        ),
        "requests": _fits(requests_upper, limits["requests_per_file_max"]),
        "scan": _fits(scan_rows, limits["scan_rows_per_file_max"]),
        "workspace": _fits(workspace_upper, limits["memory_resident_bytes"]),
    }
    reasons: list[str] = []
    if not fits["requests"]["fits"]:
        reasons.append(f"request upper {requests_upper} exceeds 100/file")
    if not fits["transfer"]["fits"]:
        reasons.append(f"transfer upper {transfer_upper} exceeds data/file cap")
    if not fits["decompressed"]["fits"]:
        reasons.append("text uncompressed exceeds 64 MiB/file")
    if not fits["scan"]["fits"]:
        reasons.append(f"scan upper {scan_rows} exceeds 16384 rows/file")
    if not fits["workspace"]["fits"]:
        reasons.append(f"workspace upper {workspace_upper} exceeds 256 MiB")
    unit = {
        "file": source_file,
        "remote_length": state.get("total"),
        "etag": state.get("etag"),
        "selected_count": len(wanted_rows),
        "selected_min": min(wanted_rows),
        "selected_max": max(wanted_rows),
        "wanted_rows": sorted(wanted_rows),
        "text_costs": text,
        "requests_upper": requests_upper,
        "transfer_upper_bytes": transfer_upper,
        "decompressed_upper_bytes": text["text_uncompressed_bytes"],
        "scan_rows_upper": scan_rows,
        "workspace_upper_bytes": workspace_upper,
        "fits": fits,
        "feasible": not reasons,
        "reasons": reasons,
    }
    if reasons:
        raise TextCostInfeasible(source_file, reasons, unit)
    return unit


class TextCostInfeasible(TextCostError):
    """One file's computed cost evidence exceeds caps; carries that evidence.

    The partial unit holds footer-derived numbers only (wanted rows,
    groups, chunk sizes, uppers, reasons) — never text content — so a
    future authorized audit can see exactly which bound failed without
    re-fetching footers. The refusal decision itself is unchanged.
    """

    def __init__(self, source_file: str, reasons: list[str], partial_unit: dict[str, Any]) -> None:
        super().__init__(f"text costs infeasible for {source_file}: {reasons}")
        self.partial_unit = partial_unit


class TextCostsIncomplete(TextCostError):
    """Arm-T cost planning stopped: collected units, infeasibility map, budget.

    ``units`` holds every safely reached file unit (feasible or not);
    ``infeasible`` lists each future-infeasible file with its reasons;
    ``stopped_early`` marks an integrity/safety stop that truncated
    collection (as opposed to full collection with cap overruns).
    """

    def __init__(
        self,
        units: list[dict[str, Any]],
        failed_file: str,
        reason: str,
        budget: dict[str, Any],
        *,
        failed_unit: dict[str, Any] | None = None,
        infeasible: list[dict[str, Any]] | None = None,
        stopped_early: bool = False,
    ) -> None:
        super().__init__(f"Arm-T costs INCOMPLETE at {failed_file}: {reason}")
        self.units = units
        self.failed_file = failed_file
        self.reason = reason
        self.budget = budget
        self.failed_unit = failed_unit
        self.infeasible: list[dict[str, Any]] = list(infeasible) if infeasible else []
        self.stopped_early = stopped_early


def plan_text_costs(
    manifest: Mapping[str, Any],
    *,
    transport: LiveFooterTransport | FakeFooterTransport,
    ledger: budgets.ArmLedger,
    caps: Mapping[str, int] | None = None,
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
    infeasible: list[dict[str, Any]] = []
    first_failure: tuple[str, str, dict[str, Any] | None] | None = None
    for name, rows in wanted.items():
        try:
            units.append(cost_file(name, rows, transport=transport, ledger=ledger, caps=caps))
            continue
        except TextCostInfeasible as exc:
            # Collectable: future-acquisition cap overrun on this file only.
            # Planning continues to the next frozen file; the arm stays
            # INCOMPLETE and no success artifact may be published.
            units.append(exc.partial_unit)
            infeasible.append({"file": name, "reasons": exc.partial_unit["reasons"]})
            if first_failure is None:
                first_failure = (name, str(exc), exc.partial_unit)
            continue
        except (TextCostError, budgets.BudgetRefusal, sparse.SparseError) as exc:
            # Immediate stop: integrity/safety failures (digest, repo,
            # foreign locator, malformed footer/schema, budget, drift,
            # unresolvable bounds) truncate collection at this file.
            raise TextCostsIncomplete(
                units,
                name,
                str(exc),
                ledger.snapshot(),
                infeasible=infeasible,
                stopped_early=True,
            ) from exc
    if infeasible:
        assert first_failure is not None
        raise TextCostsIncomplete(
            units,
            first_failure[0],
            first_failure[1],
            ledger.snapshot(),
            failed_unit=first_failure[2],
            infeasible=infeasible,
            stopped_early=False,
        )
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


def build_incomplete_aggregate(
    error: TextCostsIncomplete,
    manifest: Mapping[str, Any],
    *,
    command: str,
    exit_status: int = 1,
) -> dict[str, Any]:
    """Bounded INCOMPLETE cost map over every safely reached file.

    One unit per file reached without an integrity/safety failure, each
    carrying per-group cost detail, exact formulas, and per-limit fits;
    aggregate sums/maxima; every refusal reason. Status stays INCOMPLETE
    whenever any file is future-infeasible or collection stopped early,
    and no success artifact may be published alongside it.
    """
    limits = frozen.ARM_T_LIMITS
    transfer_sum = sum(u["transfer_upper_bytes"] for u in error.units)
    transfer_max = max((u["transfer_upper_bytes"] for u in error.units), default=0)
    decomp_sum = sum(u["decompressed_upper_bytes"] for u in error.units)
    decomp_max = max((u["decompressed_upper_bytes"] for u in error.units), default=0)
    requests_future = sum(u["requests_upper"] for u in error.units)
    scan_sum = sum(u["scan_rows_upper"] for u in error.units)
    refusal_reasons = sorted({reason for item in error.infeasible for reason in item["reasons"]})
    if error.stopped_early:
        refusal_reasons = sorted(set(refusal_reasons) | {error.reason})
    budget = error.budget
    aggregate = {
        "files_total": len(error.units),
        "files_feasible": sum(1 for u in error.units if u["feasible"]),
        "files_infeasible": len(error.infeasible),
        "transfer_upper_sum_bytes": transfer_sum,
        "transfer_upper_max_bytes": transfer_max,
        "decompressed_upper_sum_bytes": decomp_sum,
        "decompressed_upper_max_bytes": decomp_max,
        "requests_upper_future_total": requests_future,
        "requests_planning_consumed": budget.get("requests", 0),
        "scan_upper_total_rows": scan_sum,
        "refusal_reasons": refusal_reasons,
        "fits": {
            "transfer_arm": _fits(transfer_sum, limits["transfer_bytes_arm_max"]),
            "decompressed_arm": _fits(decomp_sum, limits["decompressed_bytes_arm_max"]),
            "requests_arm": _fits(
                requests_future + int(budget.get("requests", 0)),
                limits["requests_arm_max"],
            ),
            "scan_arm": _fits(scan_sum, limits["scan_rows_arm_max"]),
        },
    }
    return {
        "kind": "essential_web_evidence_v2_text_costs_incomplete",
        "receipt_schema_version": 2,
        "protocol_version": frozen.PROTOCOL_VERSION,
        "freeze_digest": frozen.FREEZE_DIGEST,
        "selection_digest": manifest["digest"],
        "total_selected": manifest["total_selected"],
        "files_planned": len(error.units),
        "files_feasible": aggregate["files_feasible"],
        "files_infeasible": aggregate["files_infeasible"],
        "units": error.units,
        "infeasible_files": error.infeasible,
        "formulas": dict(FORMULAS),
        "aggregate": aggregate,
        "completed_units": [u for u in error.units if u["feasible"]],
        "failed_file": error.failed_file,
        "failed_unit": error.failed_unit,
        "reason": error.reason,
        "stopped_early": error.stopped_early,
        "budget": budget,
        "command": command,
        "exit_status": exit_status,
        "status": "INCOMPLETE",
    }
