"""Seal the executed Essential-Web production calibration as Git-safe evidence.

Offline and read-only over the calibration root. Every number is recomputed
from the executed artifacts (plans, journals, raw records, adaptation outputs)
and compared with the recorded measurement. Only hashes, counts and sizes are
written: never document text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import essential_web_measure
import mix01_inventory
import yaml

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import load_acquisition_plan
from xlm.data.acquisition.progress import AcquisitionState
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    attempt_artifact_id,
    latest_attempt,
)
from xlm.data.sources.prober import ProbeEvidenceRecord

REPO = Path(__file__).resolve().parents[1]
ADAPTER_ID = "essential_web_bnormal"
MALFORMED_CODE = "EssentialWebMalformedRowError"
REASON_CODE = re.compile(r"^[a-z0-9_]{1,64}(:[a-z0-9_]{1,32}){0,2}$")
CODE_FILES = (
    "src/xlm/data/adapters/mix01_adapters.py",
    "src/xlm/data/adapters/essential_web_selector.py",
    "src/xlm/data/adapters/malformed.py",
    "src/xlm/data/adapters/rejections.py",
)
MAX_FILE_BYTES = 256 * ready.MIB


class SealError(ValueError):
    """A calibration artifact is missing or disagrees with its recorded identity."""


def file_sha256(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise SealError(f"'{path.name}' exceeds the bounded seal input size")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path: Path) -> Any:
    return mix01_inventory._read_bounded_json(path, 8 * ready.MIB, "calibration evidence")


def code_identity() -> dict[str, str]:
    return {name: file_sha256(REPO / name) for name in CODE_FILES}


def max_line_bytes(path: Path) -> int:
    """Largest serialized record of one raw file (a size, never its content)."""
    largest = 0
    with path.open("rb") as handle:
        for line in handle:
            largest = max(largest, len(line))
    return largest


def reason_codes(reason: Any) -> tuple[str, ...]:
    """Deterministic reason codes of one malformed row; never free text.

    The adapter writes either one code or ``prefix: code, code``. Anything that
    is not code-shaped is reported as ``other`` so no record content can reach
    the sealed evidence.
    """
    if not isinstance(reason, str):
        return ("other",)
    prefix, separator, rest = reason.partition(": ")
    tokens = [prefix] if not separator else [f"{prefix}:{part}" for part in rest.split(", ")]
    return tuple(token if REASON_CODE.fullmatch(token) else "other" for token in tokens)


def malformed_reasons(path: Path) -> Counter[str]:
    """Rows per sorted set of malformed reason codes (counts only)."""
    reasons: Counter[str] = Counter()
    for record in essential_web_measure.lines(path, MAX_FILE_BYTES):
        if record.get("rejection_code") == MALFORMED_CODE:
            reasons[" + ".join(sorted(set(reason_codes(record.get("reason")))))] += 1
    return reasons


def readapt(plan_path: Path, raw: Path, view: str, work: Path) -> dict[str, Any]:
    """Re-run the CURRENT production adapter offline on the executed raw records."""
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app

    output = work / view
    result = CliRunner().invoke(
        app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            ADAPTER_ID,
            "--adapter-config",
            view,
            "--input",
            str(raw),
            "--output-dir",
            str(output),
            "--on-reject",
            "record",
            "--max-input-bytes",
            str(MAX_FILE_BYTES),
        ],
    )
    if result.exit_code != 0:
        raise SealError(f"offline re-adaptation failed for {view}: {result.output}")
    summary: dict[str, Any] = read_json(output / "adaptation_summary.json")
    return summary


def admission_identity(xlm_home: Path) -> dict[str, Any]:
    """Stored probe evidence and operator decision per view, re-evaluated by the C04 gate."""
    store = ArtifactStore(ArtifactPaths(root=xlm_home))
    views: dict[str, Any] = {}
    for view in calibration.VIEWS:
        record: dict[str, Any] = {}
        loaded: dict[str, Any] = {}
        for kind, prefix in (("probe_evidence", "probe"), ("admission_decision", "admission")):
            base = f"{prefix}_essential_web_{view}"
            attempt = max(1, latest_attempt(store, kind, base))
            artifact = attempt_artifact_id(base, attempt)
            path = xlm_home / kind / artifact / f"{kind}.json"
            manifest = read_json(path.with_name("manifest.json"))
            loaded[kind] = read_json(path)
            record[kind] = {
                "artifact_id": artifact,
                "attempt": attempt,
                "record_sha256": file_sha256(path),
                "manifest_content_hash": manifest["content_hash"],
            }
        evidence = ProbeEvidenceRecord.model_validate(loaded["probe_evidence"])
        decision = AdmissionDecision.model_validate(loaded["admission_decision"])
        gate = AdmissionGate.evaluate(evidence, decision)
        if not gate.admitted:
            raise SealError(f"{view}: stored admission does not admit: {gate.reasons}")
        if decision.selector_binding != selector.selector_identity():
            raise SealError(f"{view}: admission binds a different selector")
        mitigation = decision.contamination_mitigation
        record.update(
            admitted=True,
            contract_version=decision.contract_version,
            adapter_id=decision.adapter_id,
            immutable_revision=decision.immutable_revision,
            probe_fingerprint=decision.probe_fingerprint,
            benchmark_risk=str(decision.benchmark_risk.value),
            contamination_mitigation=None if mitigation is None else mitigation.model_dump(),
            reviews_sha256=dict(decision.reviews_sha256),
            decision_timestamp=decision.decision_timestamp,
        )
        views[view] = record
    return views


def build(root: Path, freeze: Path, xlm_home: Path, work: Path) -> dict[str, Any]:
    """Recompute the calibration from its artifacts; returns seal, yields and dispersion."""
    measurement_path = root / "measurement.json"
    recorded = read_json(measurement_path)
    recomputed = essential_web_measure.measure(root, freeze, "calibration")
    if recomputed != recorded:
        raise SealError("recorded measurement differs from the artifacts it describes")
    ready.check_binding(recorded["binding"])
    frozen = read_json(freeze / "calibration-plan.json")
    body = {key: value for key, value in frozen.items() if key != "digest"}
    if canonical.digest(body) != frozen["digest"]:
        raise SealError("calibration freeze digest does not reproduce")
    if frozen["selector"] != selector.selector_identity():
        raise SealError("calibration freeze binds a different selector")
    if work.exists():
        raise SealError(f"work directory '{work}' already exists; use a fresh one")
    units: list[dict[str, Any]] = []
    crawl_units: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    try:
        for index, (window, measured) in enumerate(
            zip(frozen["windows"], recorded["units"], strict=True)
        ):
            tag = f"calibration-{index:02d}"
            plan_path = root / f"{tag}.plan.json"
            plan = load_acquisition_plan(plan_path)
            unit_root = root / tag
            journal_path = unit_root / "scratch/journals" / f"{plan.plan_id}.progress.json"
            state = AcquisitionState.model_validate(read_json(journal_path))
            raw = unit_root / "raw/selected_records.jsonl"
            artifact = xlm_home / "raw_dataset" / plan.output_artifact_id
            manifest = read_json(artifact / "manifest.json")
            stored = {entry["path"]: entry for entry in manifest["files"]}
            if (
                stored["selected_records.jsonl"]["sha256"] != measured["raw_sha256"]
                or file_sha256(artifact / "selected_records.jsonl") != measured["raw_sha256"]
                or file_sha256(artifact / "acquisition_receipt.json")
                != stored["acquisition_receipt.json"]["sha256"]
                or manifest["resolved_config_hash"] != plan.plan_hash
            ):
                raise SealError(f"{tag}: published raw dataset differs from the executed fetch")
            views: dict[str, Any] = {}
            crawl: dict[str, Any] = {
                "crawl": window["crawl"],
                "file": window["file"],
                "input_rows": state.records_acquired,
                "transferred_bytes": state.transferred_bytes,
                "decompressed_bytes": state.decompressed_bytes,
                "requests": state.requests_made,
                "raw_bytes": raw.stat().st_size,
                "max_raw_record_bytes": max_line_bytes(raw),
                "elapsed_seconds": (
                    datetime.fromisoformat(state.updated_at)
                    - datetime.fromisoformat(state.started_at)
                ).total_seconds(),
            }
            performance = read_json(unit_root / "scratch/performance" / f"{plan.plan_id}.perf.json")
            telemetry = performance["telemetry"]
            crawl["performance"] = {
                "wall_seconds": performance["wall_seconds"],
                "cpu_process_seconds": performance["cpu_process_seconds"],
                "logical_requests": telemetry["logical_requests"],
                "open_seconds": telemetry["open_seconds"],
                "body_seconds": telemetry["body_seconds"],
                "peak_rss_bytes": telemetry["peak_rss_bytes"],
                "max_workers": performance["max_workers_configured"],
            }
            malformed: set[int] = set()
            for view in calibration.VIEWS:
                directory = unit_root / view
                summary = read_json(directory / "adaptation_summary.json")
                documents, canonical_bytes, digest = mix01_inventory.scan_canonical(
                    directory / "documents.jsonl", require_text=True
                )
                rejections = directory / "adaptation_rejections.jsonl"
                if (
                    summary["adapter_id"] != ADAPTER_ID
                    or summary["plan_hash"] != plan.plan_hash
                    or summary["documents"]["sha256"] != digest
                    or summary["rejections"]["sha256"] != file_sha256(rejections)
                    or summary["accepted_records"] != documents
                ):
                    raise SealError(f"{tag}/{view}: adaptation summary does not bind its outputs")
                again = readapt(plan_path, raw, view, work / tag)
                if any(
                    again[key] != summary[key]
                    for key in (
                        "accepted_records",
                        "rejected_records",
                        "rejection_counts_by_code",
                        "documents",
                        "rejections",
                    )
                ):
                    raise SealError(f"{tag}/{view}: current adapter does not reproduce the output")
                codes = summary["rejection_counts_by_code"]
                malformed.add(int(codes.get(MALFORMED_CODE, 0)))
                views[view] = {
                    "adaptation_summary_sha256": file_sha256(directory / "adaptation_summary.json"),
                    "documents_sha256": digest,
                    "documents_file_bytes": (directory / "documents.jsonl").stat().st_size,
                    "rejections_sha256": summary["rejections"]["sha256"],
                    "rejections_file_bytes": rejections.stat().st_size,
                    "accepted_records": documents,
                    "rejected_records": summary["rejected_records"],
                    "rejection_counts_by_code": dict(sorted(codes.items())),
                    "canonical_bytes": canonical_bytes,
                    "reproduced_by_current_adapter": True,
                }
                crawl[view] = {"documents": documents, "canonical_bytes": canonical_bytes}
            if len(malformed) != 1:
                raise SealError(f"{tag}: views disagree on malformed rows")
            crawl["malformed_rows"] = malformed.pop()
            reasons.update(
                malformed_reasons(unit_root / "essential_science/adaptation_rejections.jsonl")
            )
            units.append(
                {
                    "unit": tag,
                    "crawl": window["crawl"],
                    "file": window["file"],
                    "row_range": list(plan.row_ranges[window["file"]]) if plan.row_ranges else [],
                    "plan_id": plan.plan_id,
                    "plan_hash": plan.plan_hash,
                    "plan_file_sha256": file_sha256(plan_path),
                    "frozen_plan_file_sha256": file_sha256(freeze / f"{tag}.plan.json"),
                    "selection_hash": plan.compute_selection_hash(),
                    "authorization": None
                    if plan.authorization is None
                    else plan.authorization.model_dump(),
                    "journal_sha256": file_sha256(journal_path),
                    "source_validator": state.source_validators[window["file"]],
                    "raw_sha256": measured["raw_sha256"],
                    "raw_bytes": raw.stat().st_size,
                    "raw_rows": state.records_acquired,
                    "raw_dataset_artifact": {
                        "artifact_id": plan.output_artifact_id,
                        "manifest_sha256": file_sha256(artifact / "manifest.json"),
                        "content_hash": manifest["content_hash"],
                        "receipt_sha256": stored["acquisition_receipt.json"]["sha256"],
                    },
                    "views": views,
                }
            )
            crawl_units.append(crawl)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    totals = {
        "crawls": recorded["crawls"],
        "files": recorded["files"],
        "input_rows": recorded["input_rows"],
        "scanned_rows": recorded["scanned_rows"],
        "physical_response_body_bytes": recorded["physical_response_body_bytes"],
        "decompressed_bytes": recorded["decompressed_bytes"],
        "requests": sum(unit["requests"] for unit in crawl_units),
        "raw_bytes": sum(unit["raw_bytes"] for unit in crawl_units),
        "max_raw_record_bytes": max(unit["max_raw_record_bytes"] for unit in crawl_units),
        "elapsed_wall_seconds_including_restart_downtime": recorded[
            "elapsed_wall_seconds_including_restart_downtime"
        ],
        "malformed_rows": recorded["malformed_rows"],
        "malformed_rows_by_reason_codes": dict(sorted(reasons.items())),
        "malformed_reason_code_occurrences": dict(
            sorted(
                Counter(
                    code for key, rows in reasons.items() for code in key.split(" + ") * rows
                ).items()
            )
        ),
        "selector_counts": recorded["selector_counts"],
        "retained": {
            view: {
                key: recorded["retention_and_cost"][view][key]
                for key in ("documents", "canonical_bytes", "characters")
            }
            for view in calibration.VIEWS
        },
        "total_retained_documents": recorded["total_retained_documents"],
        "total_canonical_bytes": recorded["total_canonical_bytes"],
        "documents_file_bytes": sum(
            view["documents_file_bytes"] for unit in units for view in unit["views"].values()
        ),
        "rejections_file_bytes": sum(
            view["rejections_file_bytes"] for unit in units for view in unit["views"].values()
        ),
    }
    if totals["malformed_rows"] != sum(unit["malformed_rows"] for unit in crawl_units) or totals[
        "malformed_rows"
    ] != sum(reasons.values()):
        raise SealError("malformed rows do not reconcile across units and ledgers")
    seal: dict[str, Any] = {
        "kind": "essential_web_production_calibration_seal",
        "version": 1,
        "evidence_class": "REAL live production calibration; recomputed offline from artifacts",
        "calibration_root": str(root),
        "binding": recorded["binding"],
        "selector": selector.selector_identity(),
        "source_revision": selector.SOURCE_REVISION,
        "adapter": {"adapter_id": ADAPTER_ID, "code_sha256": code_identity()},
        "admission": admission_identity(xlm_home),
        "calibration_freeze_digest": frozen["digest"],
        "measurement": {
            "file": "measurement.json",
            "sha256": file_sha256(measurement_path),
            "bytes": measurement_path.stat().st_size,
            "reproduced_from_artifacts": True,
        },
        "units": units,
        "totals": totals,
        "token_method": calibration.TOKEN_METHOD,
        "contains_document_text": False,
    }
    seal["seal_digest"] = canonical.digest(seal)
    quotas = yaml.safe_load((REPO / "recipes/mixtures/mix01_quotas_6b.yaml").read_bytes())
    yields = calibration.component_yields(recorded)
    yields["requirements"] = calibration.component_requirements(recorded, quotas)
    yields["seal_digest"] = seal["seal_digest"]
    dispersion = calibration.crawl_dispersion(
        crawl_units, int(quotas["first_pass_headroom_quotas"]["essential_science"])
    )
    dispersion["seal_digest"] = seal["seal_digest"]
    return {"seal": seal, "yields": yields, "crawls": dispersion}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--xlm-home", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare with the existing seal instead of writing; nonzero on any difference.",
    )
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        result = build(args.root, args.freeze, args.xlm_home, args.work_dir)
    except (ValueError, OSError, KeyError) as exc:
        print(f"essential_web_calibration_seal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    names = {
        "seal": "calibration-seal.json",
        "yields": "calibration-yields.json",
        "crawls": "crawl-yields.json",
    }
    for key, name in names.items():
        path = args.output_dir / name
        if args.verify:
            if read_json(path) != json.loads(json.dumps(result[key])):
                print(f"essential_web_calibration_seal: {name} differs", file=sys.stderr)
                return 1
        else:
            mix01_inventory._atomic_write_json(path, result[key])
    verb = "verified" if args.verify else "sealed"
    print(f"{verb}: {result['seal']['seal_digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
