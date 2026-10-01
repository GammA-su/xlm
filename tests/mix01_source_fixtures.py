"""Authored offline fixtures for the Mix-01 source bridge, planner and engine tests.

Everything here is synthetic and shaped like the real artifacts (probe
receipt, saved sample, calibration plan/journal/records/summary, Parquet
files). None of it is real source evidence: real evidence never lives in the
checkout or in pytest's temporary tree.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from xlm.artifacts.store import ArtifactStore
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionMode, AcquisitionPlan
from xlm.data.acquisition.source_parquet import located_record
from xlm.data.adapters.mix01_adapters import RecordRejectedError, UltraXUltraFineWebAdapter
from xlm.data.adapters.rejections import serialize_document
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources.admission import save_probe_evidence
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.mix01 import load_mix01_views
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome

REPO = Path(__file__).resolve().parents[1]
REPOSITORY = "openbmb/UltraX-Preview"
REVISION = "a88527587389fd4ab352e9ad1273f4c0a234d8df"
CONFIG = "UltraX-Ultra-FineWeb"
SOURCE = "ultrax_ultrafineweb"
FIELDS = ("uid", "raw_content", "cleaned_content", "processed_functions", "source")
CAL_FILE = "data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0039-of-0104.parquet"
ETAG = '"' + "e" * 64 + '"'


def ultrax_pin() -> ce.SourcePin:
    return ce.resolve_pin(
        load_catalog(REPO / "manifests/datasets.catalog.yaml"),
        load_mix01_views(REPO / "recipes/mixtures/mix01_views.yaml"),
        SOURCE,
        CONFIG,
        SOURCE,
    )


def ultrax_row(index: int, *, empty: bool = False) -> dict[str, Any]:
    text = "" if empty else f"Authored web paragraph {index} about measurement and tides."
    return {
        "uid": hashlib.md5(f"uid-{index}".encode()).hexdigest(),  # noqa: S324 - fixture ids
        "raw_content": f"RAW {index} {text}",
        "cleaned_content": text,
        "processed_functions": "keep_all()",
        "source": "Ultra-FineWeb",
    }


def _stats(values: list[int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def probe_files(rows: list[dict[str, Any]], **overrides: Any) -> tuple[bytes, bytes]:
    """An ``ultrax-schema-probe-v1`` receipt and its sample, internally consistent."""
    locator = f"hf-stream://{REPOSITORY}@{REVISION}/{CONFIG}/train"
    sample = b"".join(
        (
            json.dumps(
                {
                    **row,
                    "_cert_source_file": locator,
                    "_cert_source_row": index,
                    "_cert_revision": REVISION,
                },
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        for index, row in enumerate(rows)
    )
    labels: dict[str, int] = {}
    for row in rows:
        labels[row["source"]] = labels.get(row["source"], 0) + 1
    receipt: dict[str, Any] = {
        "probe": "ultrax-schema-probe-v1",
        "probe_version": 1,
        "repository": REPOSITORY,
        "revision_sha": REVISION,
        "config": CONFIG,
        "expected_config": CONFIG,
        "config_verified": True,
        "schema_match": True,
        "split": "train",
        "field_names": sorted(FIELDS),
        "expected_fields": list(FIELDS),
        "extra_fields": [],
        "field_types": {name: "Value('string')" for name in FIELDS},
        "schema_evidence": {"source": "streaming_features", "rows": 0},
        "rows_sampled": len(rows),
        "observed_field_presence": {name: len(rows) for name in FIELDS},
        "uid_format": {
            "sorted_sample_sha256": hashlib.sha256(
                "|".join(sorted(r["uid"] for r in rows)).encode("utf-8")
            ).hexdigest()
        },
        "cleaned_content_lengths": _stats([len(r["cleaned_content"]) for r in rows]),
        "raw_content_lengths": _stats([len(r["raw_content"]) for r in rows]),
        "cleaned_content_empty_count": sum(1 for r in rows if not r["cleaned_content"].strip()),
        "source_values": labels,
        "declared_license": "apache-2.0",
        "license_caveat": "UltraX is derived from source corpora.",
        "tested_aliases": {
            REPOSITORY: {"accessible": True, "sha": REVISION, "license": "apache-2.0"}
        },
        "budgets": {"full_download": False, "max_rows": len(rows), "elapsed_seconds": 1.0},
        "observed_at": "2026-09-27T00:00:00+00:00",
        "configs_observed": [CONFIG],
    }
    receipt.update(overrides)
    return json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8"), sample


def calibration_files(
    rows: list[dict[str, Any]], *, source_file: str = CAL_FILE, start: int = 1000
) -> ce.CalibrationEvidence:
    """A completed pilot calibration fetch + adaptation of ``rows`` (projected columns)."""
    plan = AcquisitionPlan(
        plan_id="plan_ultrax_ultrafineweb_UltraX-Ultra-FineWeb_huggingface_fixture",
        source_id=SOURCE,
        view_id=CONFIG,
        provider="huggingface",
        repository=REPOSITORY,
        revision=REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[source_file],
        row_ranges={source_file: (start, start + len(rows))},
        projected_fields=["uid", "cleaned_content", "source", "processed_functions"],
        limits=AcquisitionLimits(),
        output_artifact_id="raw_fixture",
    ).with_computed_hash()
    selection = plan.compute_selection_hash()
    lines = []
    adapter = UltraXUltraFineWebAdapter()
    documents = hashlib.sha256()
    accepted = rejected = 0
    for offset, row in enumerate(rows):
        projected = {k: row[k] for k in ("uid", "cleaned_content", "source", "processed_functions")}
        _, payload = located_record(
            projected,
            {
                "row_index": start + offset,
                "format": "parquet",
                "etag": ETAG,
                "source_id": SOURCE,
                "repository": REPOSITORY,
                "revision": REVISION,
                "selection_hash": selection,
                "source_file": source_file,
            },
        )
        lines.append(payload)
        record = json.loads(payload)
        try:
            document = adapter.adapt(
                record, source_file=source_file, source_row=start + offset, source_revision=REVISION
            )
        except RecordRejectedError:
            rejected += 1
            continue
        documents.update(serialize_document(document).encode("utf-8") + b"\n")
        accepted += 1
    records = b"".join(lines)
    journal = {
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "status": "COMPLETED",
        "records_acquired": len(rows),
        "source_validators": {source_file: {"etag": ETAG, "length": 2_000_000}},
        "file_progress": {
            "selected_records.jsonl": {
                "content_sha256": hashlib.sha256(records).hexdigest(),
                "record_count": len(rows),
                "bytes_downloaded": len(records),
            }
        },
        "updated_at": "2026-09-27T00:00:01+00:00",
    }
    summary = {
        "adapter_id": SOURCE,
        "plan_hash": plan.plan_hash,
        "source_revision": REVISION,
        "total_input_records": len(rows),
        "accepted_records": accepted,
        "rejected_records": rejected,
        "documents": {"sha256": documents.hexdigest()},
    }
    return ce.CalibrationEvidence(
        plan=json.dumps(plan.model_dump()).encode("utf-8"),
        journal=json.dumps(journal).encode("utf-8"),
        records=records,
        summary=json.dumps(summary).encode("utf-8"),
    )


def metadata_probe(store: ArtifactStore, license_tag: str = "apache-2.0") -> None:
    """The earlier real-shaped ``partial`` metadata probe the bridge binds for the license."""
    save_probe_evidence(
        ProbeEvidenceRecord(
            source_id=SOURCE,
            view_id=CONFIG,
            provider="huggingface",
            repository=REPOSITORY,
            immutable_revision=REVISION,
            outcome=ProbeOutcome.PARTIAL,
            evidence_type=EvidenceType.REAL_OBSERVED,
            declared_license=license_tag,
            observed_timestamp="2026-09-27T00:00:02+00:00",
            unresolved_requirements=["adapter_contract_unassigned"],
        ),
        store,
        store.paths.root / ".staging-fixture",
    )


def bridge_facts(store: ArtifactStore, rows: list[dict[str, Any]]) -> ce.CertifiedFacts:
    pin = ultrax_pin()
    receipt, sample = probe_files(rows[:5])
    facts = ce.translate_ultrax_probe(pin, receipt, sample)
    facts = ce.translate_calibration(pin, calibration_files(rows), facts)
    found = ce.generic_probe_record(store, SOURCE, CONFIG)
    assert found is not None
    return ce.translate_store_probe(pin, facts, *found)


def parquet_bytes(rows: list[dict[str, Any]], group_rows: int) -> bytes:
    table = pa.Table.from_pylist(rows, schema=pa.schema([(name, pa.string()) for name in FIELDS]))
    buffer = io.BytesIO()
    pq.write_table(table, buffer, row_group_size=group_rows)
    return buffer.getvalue()
