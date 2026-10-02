"""Certified real-source evidence -> C04 admission evidence (offline, authored fixtures)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mix01_source_fixtures import (
    CONFIG,
    REPO,
    REVISION,
    SOURCE,
    bridge_facts,
    calibration_files,
    metadata_probe,
    probe_files,
    ultrax_pin,
    ultrax_row,
)
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources.admission import AdmissionGate, load_probe_evidence
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.mix01 import load_mix01_views
from xlm.data.sources.prober import EvidenceType, ProbeOutcome

ROWS = [ultrax_row(i, empty=i == 7) for i in range(20)]


@pytest.fixture
def store(isolated_xlm_home: Path) -> ArtifactStore:
    target = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    metadata_probe(target)
    return target


def _bridge(
    store: ArtifactStore, kind: EvidenceType = EvidenceType.REAL_OBSERVED
) -> tuple[Any, Any]:
    return ce.build_bridge(ultrax_pin(), bridge_facts(store, ROWS), evidence_type=kind)


def test_pin_comes_from_catalog_and_registry() -> None:
    pin = ultrax_pin()
    assert (pin.repository, pin.revision, pin.view_id, pin.adapter_id) == (
        "openbmb/UltraX-Preview",
        REVISION,
        CONFIG,
        SOURCE,
    )
    catalog = load_catalog(REPO / "manifests/datasets.catalog.yaml")
    registry = load_mix01_views(REPO / "recipes/mixtures/mix01_views.yaml")
    with pytest.raises(ce.BridgeRefusal, match="not an adapter"):
        ce.resolve_pin(catalog, registry, SOURCE, CONFIG, "finewiki_en")
    with pytest.raises(ce.BridgeRefusal, match="exactly one Mix-01 component"):
        ce.resolve_pin(catalog, registry, SOURCE, "UltraX-FineWeb", SOURCE)
    with pytest.raises(ce.BridgeRefusal, match="own bootstrap"):
        ce.resolve_pin(
            catalog, registry, "essential_web", "essential_science", "essential_web_bnormal"
        )
    # No source inherits another source's pin: finewiki resolves to its own revision.
    other = ce.resolve_pin(catalog, registry, "finewiki", "en", "finewiki_en")
    assert other.revision != REVISION and other.repository == "HuggingFaceFW/finewiki"


def test_registry_revision_must_agree_with_catalog() -> None:
    catalog = load_catalog(REPO / "manifests/datasets.catalog.yaml")
    registry = load_mix01_views(REPO / "recipes/mixtures/mix01_views.yaml")
    changed = registry.model_copy(
        update={
            "views": [
                v.model_copy(update={"observed_revision": "b" * 40})
                if v.component_id == SOURCE
                else v
                for v in registry.views
            ]
        }
    )
    with pytest.raises(ce.BridgeRefusal, match="revision pins differ"):
        ce.resolve_pin(catalog, changed, SOURCE, CONFIG, SOURCE)


def test_real_probe_evidence_translates_into_admissible_evidence(store: ArtifactStore) -> None:
    receipt, record = _bridge(store)
    assert record.outcome == ProbeOutcome.ACCESSIBLE
    assert record.evidence_type == EvidenceType.REAL_OBSERVED
    assert record.immutable_revision == REVISION
    assert record.verified_schema is not None
    assert record.verified_schema.raw_schema_type == ce.BASIS_FEATURES
    assert set(record.verified_schema.fields) == {
        "uid",
        "raw_content",
        "cleaned_content",
        "processed_functions",
        "source",
    }
    assert record.probe_fingerprint == receipt["probe_fingerprint"]
    assert record.declared_license == "apache-2.0"
    assert receipt["observed_files"][0]["length"] == 2_000_000
    sets = receipt["adapter"]["row_sets"]
    assert sets["probe_sample"]["accepted"] == 5
    assert sets["calibration_records"] == {
        **sets["calibration_records"],
        "rows": 20,
        "accepted": 19,
        "rejected": 1,
        "reproduces_recorded_documents": True,
    }
    ce.check_receipt(receipt)
    assert ce.record_from_receipt(receipt).model_dump() == record.model_dump()
    # Deterministic: the same inputs give the same receipt digest and fingerprint.
    again, _ = _bridge(store)
    assert again["digest"] == receipt["digest"]
    # The gate now sees real accessible evidence; only the operator decision is missing.
    gate = AdmissionGate.evaluate(record, None)
    assert gate.status.value == "pending_review"
    assert gate.reasons == ["No operator admission decision recorded."]


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"revision_sha": "b" * 40}, "repository/revision differs"),
        ({"config": "UltraX-FineWeb"}, "config differs"),
        ({"field_types": {"uid": "Value('string')"}}, "field names differ"),
        ({"schema_evidence": {"source": "observed_rows", "rows": 5}}, "not metadata"),
        ({"schema_match": False}, "did not verify"),
        ({"tested_aliases": {}}, "live pinned resolution"),
        ({"rows_sampled": 6}, "row count differs"),
        ({"source_values": {"Ultra-FineWeb": 4}}, "source labels differ"),
        ({"probe": "something-else"}, "not an ultrax-schema-probe-v1"),
    ],
)
def test_probe_receipt_refusals(override: dict[str, Any], match: str) -> None:
    receipt, sample = probe_files(ROWS[:5], **override)
    with pytest.raises(ce.BridgeRefusal, match=match):
        ce.translate_ultrax_probe(ultrax_pin(), receipt, sample)


def test_sample_must_be_the_receipts_own_rows() -> None:
    receipt, sample = probe_files(ROWS[:5])
    with pytest.raises(ce.BridgeRefusal, match="BOM or CR"):
        ce.translate_ultrax_probe(ultrax_pin(), receipt, b"\xef\xbb\xbf" + sample)
    swapped = sample.replace(ROWS[0]["uid"].encode(), b"0" * 32)
    with pytest.raises(ce.BridgeRefusal, match="uid digest"):
        ce.translate_ultrax_probe(ultrax_pin(), receipt, swapped)
    with pytest.raises(ce.BridgeRefusal, match="locator differs"):
        ce.translate_ultrax_probe(
            ultrax_pin(), receipt, sample.replace(REVISION.encode(), b"c" * 40)
        )
    shorter = sample.replace(b"measurement", b"measure")
    with pytest.raises(ce.BridgeRefusal, match="statistics differ"):
        ce.translate_ultrax_probe(ultrax_pin(), receipt, shorter)


def test_calibration_refusals() -> None:
    pin = ultrax_pin()
    good = calibration_files(ROWS)
    summary = json.loads(good.summary)
    summary["documents"]["sha256"] = "0" * 64
    with pytest.raises(ce.BridgeRefusal, match="no longer reproduces"):
        facts = ce.translate_calibration(
            pin,
            ce.CalibrationEvidence(
                good.plan, good.journal, good.records, json.dumps(summary).encode()
            ),
        )
        ce.certify_adapter(pin, facts, ce.view_schema(pin, facts))
    journal = json.loads(good.journal)
    journal["status"] = "RUNNING"
    with pytest.raises(ce.BridgeRefusal, match="did not complete"):
        ce.translate_calibration(
            pin,
            ce.CalibrationEvidence(
                good.plan, json.dumps(journal).encode(), good.records, good.summary
            ),
        )
    with pytest.raises(ce.BridgeRefusal, match="not the bytes"):
        ce.translate_calibration(
            pin, ce.CalibrationEvidence(good.plan, good.journal, good.records + b"\n", good.summary)
        )
    weak = json.loads(good.journal)
    weak["source_validators"] = {
        k: {**v, "etag": 'W/"x"'} for k, v in weak["source_validators"].items()
    }
    with pytest.raises(ce.BridgeRefusal, match="strong ETag"):
        ce.calibration_files(
            pin,
            ce.CalibrationEvidence(
                good.plan, json.dumps(weak).encode(), good.records, good.summary
            ),
        )


def test_schema_and_adapter_mismatch_refused(store: ArtifactStore) -> None:
    pin = ultrax_pin()
    facts = bridge_facts(store, ROWS)
    del facts.schema["cleaned_content"]
    with pytest.raises(ce.BridgeRefusal, match="lacks adapter fields"):
        ce.build_bridge(pin, facts, evidence_type=EvidenceType.REAL_OBSERVED)
    facts = bridge_facts(store, ROWS)
    other = ce.SourcePin(**{**pin.as_dict(), "adapter_id": "finewiki_en"})
    with pytest.raises(ce.BridgeRefusal):
        ce.build_bridge(other, facts, evidence_type=EvidenceType.REAL_OBSERVED)


def test_metadata_probe_must_agree(isolated_xlm_home: Path) -> None:
    target = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    metadata_probe(target, license_tag="mit")
    with pytest.raises(ce.BridgeRefusal, match="different licenses"):
        bridge_facts(target, ROWS)


def test_authored_or_in_checkout_evidence_is_never_published(store: ArtifactStore) -> None:
    receipt, record = _bridge(store, EvidenceType.SYNTHETIC_FIXTURE)
    with pytest.raises(ce.BridgeRefusal, match="authored or synthetic"):
        ce.publish_bridge(store, receipt, record)
    assert AdmissionGate.evaluate(record, None).reasons[0].startswith("Synthetic fixture")
    with pytest.raises(ce.BridgeRefusal, match="inside the code checkout"):
        ce.inputs_outside([REPO / "fixtures" / "anything.json"], REPO)
    ce.inputs_outside([Path("D:/Project/xlm-operator-ultrax/x.json")], REPO)


def test_publication_is_immutable_idempotent_and_verifiable(
    store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    pin = ultrax_pin()
    receipt, record = _bridge(store)
    directory, published = ce.publish_bridge(store, receipt, record)
    assert published and directory.endswith(
        "probe_ultrax_ultrafineweb_UltraX-Ultra-FineWeb.attempt02"
    )
    # The earlier partial attempt is preserved, the bridge supersedes it.
    root = store.paths.root / "probe_evidence"
    assert (root / "probe_ultrax_ultrafineweb_UltraX-Ultra-FineWeb" / "_COMPLETED").is_file()
    loaded = load_probe_evidence(SOURCE, CONFIG, store)
    assert loaded is not None and loaded.outcome == ProbeOutcome.ACCESSIBLE
    assert ce.publish_bridge(store, receipt, record) == (directory, False)
    assert (
        ce.verify_current(store, pin, rebuild=lambda: _bridge(store))["digest"] == receipt["digest"]
    )
    # The generic metadata probe stays discoverable behind the bridge attempt.
    found = ce.generic_probe_record(store, SOURCE, CONFIG)
    assert found is not None and found[1] == "probe_ultrax_ultrafineweb_UltraX-Ultra-FineWeb"
    # Changed adapter code or changed inputs fail closed.
    monkeypatch.setattr(ce, "adapter_code_identity", lambda _adapter_id: {"changed": "0" * 64})
    with pytest.raises(ce.BridgeRefusal, match="adapter code changed"):
        ce.verify_current(store, pin)
    monkeypatch.undo()
    altered = dict(receipt)
    altered["digest"] = "0" * 64
    with pytest.raises(ce.BridgeRefusal, match="no longer reproduce"):
        ce.verify_current(store, pin, rebuild=lambda: (altered, record))
    with pytest.raises(ce.BridgeRefusal, match="another source"):
        ce.verify_current(store, ce.SourcePin(**{**pin.as_dict(), "revision": "c" * 40}))


def test_receipt_tampering_refused(store: ArtifactStore) -> None:
    receipt, record = _bridge(store)
    tampered = {**receipt, "declared_license": "mit"}
    with pytest.raises(ce.BridgeRefusal, match="digest does not verify"):
        ce.publish_bridge(store, tampered, record)
    with pytest.raises(ce.BridgeRefusal, match="does not match"):
        ce.publish_bridge(store, receipt, record.model_copy(update={"declared_license": "mit"}))


def test_generic_calibration_kind_for_another_source(store: ArtifactStore) -> None:
    """The same mechanism serves sources with only a real calibration fetch."""
    pin = ultrax_pin()
    facts = ce.translate_calibration(pin, calibration_files(ROWS))
    assert facts.kind == ce.CALIBRATION_FETCH
    assert facts.schema_basis == ce.BASIS_OBSERVED
    assert set(facts.schema) == {"uid", "cleaned_content", "source", "processed_functions"}
    found = ce.generic_probe_record(store, SOURCE, CONFIG)
    assert found is not None
    facts = ce.translate_store_probe(pin, facts, *found)
    receipt, record = ce.build_bridge(pin, facts, evidence_type=EvidenceType.REAL_OBSERVED)
    assert record.verified_schema is not None
    assert record.verified_schema.raw_schema_type == ce.BASIS_OBSERVED
    assert receipt["adapter"]["row_sets"]["calibration_records"]["reproduces_recorded_documents"]
