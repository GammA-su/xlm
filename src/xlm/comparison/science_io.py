"""File-level entry points for science-v1 comparisons (P35 M4 CLI backend).

``run_comparison`` reads a comparison manifest, a run roster naming frozen
checkpoints (and failed attempts), extracts evidence from the checkpoints'
receipts, computes the comparison record and writes JSON/Markdown/CSV. It
never trains or schedules anything. ``render_record`` re-renders an existing
record after verifying its hash; it cannot change the decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.comparison import science_evidence
from xlm.comparison.science_compare import (
    RUN_ROSTER_VERSION,
    ComparisonError,
    compare_science,
    load_run_roster,
    verify_comparison_record,
)
from xlm.comparison.science_evidence import EvidenceError, extract_run_evidence

MAX_INPUT_BYTES = 8 * 1024**2
ROSTER_RUN_KEYS = frozenset(
    {"label", "arm_id", "status", "failure", "checkpoint", "measurements", "synthetic"}
)


def read_json_document(path: Path, label: str) -> dict[str, Any]:
    """A bounded, canonical-JSON mapping (the execution-record reader)."""
    from xlm.experiments.execution import read_json

    try:
        return read_json(Path(path), limit=MAX_INPUT_BYTES)
    except (OSError, ValueError) as exc:
        raise ComparisonError(f"cannot read {label} '{path}': {exc}") from exc


def _roster_with_evidence(
    roster: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    if roster.get("roster_version") != RUN_ROSTER_VERSION:
        raise ComparisonError(f"run roster must declare roster_version '{RUN_ROSTER_VERSION}'")
    runs = roster.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ComparisonError("run roster lists no runs")
    evidence: dict[str, Mapping[str, Any]] = {}
    rewritten: list[dict[str, Any]] = []
    for item in runs:
        if not isinstance(item, Mapping) or not set(item) <= ROSTER_RUN_KEYS:
            raise ComparisonError(f"roster run keys must be within {sorted(ROSTER_RUN_KEYS)}")
        entry = dict(item)
        checkpoint = entry.get("checkpoint")
        if checkpoint is not None:
            path = Path(str(checkpoint))
            if not path.is_absolute():
                raise ComparisonError(
                    f"run '{entry.get('label')}': checkpoint path must be absolute"
                )
            try:
                evidence[str(entry["label"])] = extract_run_evidence(
                    path, parameter_counter=science_evidence.meta_parameter_counter
                )
            except EvidenceError as exc:
                # Kept visible as a failed attempt with the exact reason; never dropped.
                entry["status"] = "failed"
                entry["failure"] = f"evidence refused: {exc}"
        rewritten.append(entry)
    return {**roster, "runs": rewritten}, evidence


def run_comparison(
    manifest_path: Path,
    roster_path: Path,
    output_dir: Path,
    *,
    prerequisite_paths: Sequence[Path] = (),
) -> tuple[dict[str, Any], dict[str, Path]]:
    from xlm.reports.science import write_science_report

    manifest = read_json_document(manifest_path, "comparison manifest")
    roster = read_json_document(roster_path, "run roster")
    prerequisites = [read_json_document(p, "prerequisite record") for p in prerequisite_paths]
    for record in prerequisites:
        verify_comparison_record(record)
    roster, evidence = _roster_with_evidence(roster)
    entries = load_run_roster(roster, evidence)
    record = compare_science(manifest, entries, prerequisites=prerequisites)
    return record, write_science_report(record, output_dir)


def render_record(record_path: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    from xlm.reports.science import write_science_report

    record = read_json_document(record_path, "comparison record")
    verify_comparison_record(record)
    return record, write_science_report(record, output_dir)
