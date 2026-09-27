"""Authored synthetic calibration unit for offline driver/helper tests.

Builds, under one temporary data root, the on-disk layout the Mix-01
calibration driver leaves for the ``simple_stories`` unit after probe,
sample-blocks, plan and fetch: persisted probe evidence in the store, row
ranges plus sampling report, a saved plan, a selected-records file with
real plan-bound locators, and a COMPLETED fetch journal. Nothing here
touches the network; every story and counter is authored (the journal's
transfer counter is a synthetic value, not a measurement). The pinned
source identifiers are used so the driver's unit table applies unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCE = "simple_stories"
VIEW = "default"
REPOSITORY = "SimpleStories/SimpleStories"
REVISION = "e63b8adc3b1a1bdc7cac5b500d150b71346b0628"
SOURCE_FILE = "data/train-00003-of-00007.parquet"
SEED = 20260918
FIRST_ROW = 100
SYNTHETIC_TRANSFERRED_BYTES = 4096

STORIES = [
    "Mia found a small red kite in the park and flew it until sunset.",
    "The old lighthouse keeper counted ships; café lights blinked below.",
    "A naïve robot learned to bake bread, one burnt loaf at a time.",
    "Under the bridge, two otters shared a smooth grey stone — 日本 style.",
]

# UltraX reference entry as documented in MIX01-CALIBRATION-REMAINING.md §5.
ULTRAX_ENTRY = {
    "accepted_records": 994,
    "canonical_bytes": 3845430,
    "extra_survival": 1.0,
    "records_sampled": 1000,
    "rejected_records": 6,
    "transferred_bytes": 3326258,
}


def render_json(payload: Any) -> bytes:
    """Byte form written by mix01_inventory (_atomic_write_json)."""
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@dataclass(frozen=True)
class CalibrationUnit:
    data_root: Path
    home: Path
    unit: Path
    plan_path: Path
    rows: Path
    report: Path
    raw: Path
    scratch: Path
    canonical: Path
    calibration: Path
    measurement: Path
    plan_id: str
    plan_hash: str

    @property
    def journal(self) -> Path:
        return self.scratch / "journals" / f"{self.plan_id}.progress.json"


def expected_entry() -> dict[str, Any]:
    return {
        "accepted_records": len(STORIES),
        "canonical_bytes": sum(len(story.encode("utf-8")) for story in STORIES),
        "extra_survival": 1.0,
        "records_sampled": len(STORIES),
        "rejected_records": 0,
        "transferred_bytes": SYNTHETIC_TRANSFERRED_BYTES,
    }


def _publish_probe(home: Path) -> None:
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sources.admission import save_probe_evidence
    from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome

    record = ProbeEvidenceRecord.model_validate(
        {
            "source_id": SOURCE,
            "view_id": VIEW,
            "provider": "huggingface",
            "repository": REPOSITORY,
            "immutable_revision": REVISION,
            "outcome": ProbeOutcome.PARTIAL,
            "evidence_type": EvidenceType.REAL_OBSERVED,
            "observed_files_count": 7,
            "declared_license": "mit",
        }
    )
    save_probe_evidence(
        record, ArtifactStore(ArtifactPaths(root=home)), staging_dir=home / "staging"
    )


def save_plan(path: Path, *, revision: str = REVISION) -> tuple[str, str, str]:
    """Save the synthetic plan; return (plan_id, plan_hash, selection_hash)."""
    from xlm.data.acquisition.plan import (
        AcquisitionLimits,
        AcquisitionMode,
        AcquisitionPlan,
        SamplingFrame,
        load_acquisition_plan,
        save_acquisition_plan,
    )

    plan = AcquisitionPlan(
        plan_id="plan_simple_stories_default_synthetic",
        source_id=SOURCE,
        view_id=VIEW,
        provider="huggingface",
        repository=REPOSITORY,
        revision=revision,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[SOURCE_FILE],
        row_ranges={SOURCE_FILE: (FIRST_ROW, FIRST_ROW + len(STORIES))},
        sampling_frame=SamplingFrame(selection_seed=SEED),
        limits=AcquisitionLimits(),
        output_artifact_id="raw_simple_stories_default_synthetic",
        is_pilot=True,
    )
    save_acquisition_plan(plan, path)
    loaded = load_acquisition_plan(path)
    return loaded.plan_id, loaded.compute_behavioral_hash(), loaded.compute_selection_hash()


def build_unit(data_root: Path) -> CalibrationUnit:
    """Author the post-fetch state of the synthetic unit under ``data_root``."""
    from xlm.data.acquisition.progress import AcquisitionState, FileProgress, ResourceAccount

    data_root = data_root.resolve()
    home = data_root / "xlm-home"
    unit = data_root / "calib" / SOURCE
    raw, scratch = unit / "raw", unit / "scratch"
    for directory in (home, raw, scratch / "journals"):
        directory.mkdir(parents=True, exist_ok=True)
    _publish_probe(home)

    rows = unit / "rows.json"
    rows.write_text(
        json.dumps({SOURCE_FILE: [FIRST_ROW, FIRST_ROW + len(STORIES)]}), encoding="utf-8"
    )
    report = unit / "rows.evidence.json"
    report.write_text(
        json.dumps(
            {
                "source_id": SOURCE,
                "view_id": VIEW,
                "revision": REVISION,
                "seed": SEED,
                "note": "authored synthetic fixture",
            }
        ),
        encoding="utf-8",
    )
    plan_path = unit / "plan.json"
    plan_id, plan_hash, selection_hash = save_plan(plan_path)

    lines = []
    for offset, story in enumerate(STORIES):
        locator = {
            "source_id": SOURCE,
            "repository": REPOSITORY,
            "revision": REVISION,
            "source_file": SOURCE_FILE,
            "row_index": FIRST_ROW + offset,
            "selection_hash": selection_hash,
        }
        lines.append(json.dumps({"story": story, "_xlm_acquisition": locator}))
    selected = raw / "selected_records.jsonl"
    selected.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    size = selected.stat().st_size
    digest = hashlib.sha256(selected.read_bytes()).hexdigest()

    state = AcquisitionState(
        plan_id=plan_id,
        plan_hash=plan_hash,
        status="COMPLETED",
        transferred_bytes=SYNTHETIC_TRANSFERRED_BYTES,
        requests_made=1,
        records_acquired=len(STORIES),
        file_progress={
            "selected_records.jsonl": FileProgress(
                file_path="selected_records.jsonl",
                bytes_downloaded=size,
                verified_prefix_bytes=size,
                prefix_sha256=digest,
                content_sha256=digest,
                record_count=len(STORIES),
                status="completed",
            )
        },
        accounting=ResourceAccount(
            consumed={"transfer": SYNTHETIC_TRANSFERRED_BYTES, "requests": 1}
        ),
        storage_roots={"scratch": str(scratch.resolve()), "output": str(raw.resolve())},
    )
    journal = scratch / "journals" / f"{plan_id}.progress.json"
    journal.write_bytes(state.model_dump_json().encode("utf-8"))

    calibration = data_root / "calib" / "calibration.json"
    calibration.write_bytes(render_json({"sources": {"ultrax_ultrafineweb": ULTRAX_ENTRY}}))
    return CalibrationUnit(
        data_root=data_root,
        home=home,
        unit=unit,
        plan_path=plan_path,
        rows=rows,
        report=report,
        raw=raw,
        scratch=scratch,
        canonical=unit / "canonical",
        calibration=calibration,
        measurement=unit / "record_inputs.json",
        plan_id=plan_id,
        plan_hash=plan_hash,
    )


def adapt_in_process(unit: CalibrationUnit) -> None:
    """Run the real ``data adapt`` (record mode) over the synthetic selection."""
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app as data_app

    result = CliRunner().invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(unit.plan_path),
            "--adapter",
            "simple_stories",
            "--input",
            str(unit.raw / "selected_records.jsonl"),
            "--output-dir",
            str(unit.canonical),
            "--on-reject",
            "record",
        ],
    )
    if result.exit_code != 0:
        raise RuntimeError(f"synthetic adapt failed: {result.output}")


def rebind_documents_digest(unit: CalibrationUnit) -> None:
    """Re-point the summary at the current documents.jsonl bytes (mutation tests)."""
    summary_path = unit.canonical / "adaptation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    docs = (unit.canonical / "documents.jsonl").read_bytes()
    summary["documents"]["sha256"] = hashlib.sha256(docs).hexdigest()
    summary_path.write_bytes(render_json(summary))
