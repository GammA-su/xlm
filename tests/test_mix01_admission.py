"""Mitigated admission of non-Essential sources and authoritative admission reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mix01_source_fixtures import (
    CONFIG,
    REVISION,
    SOURCE,
    bridge_facts,
    metadata_probe,
    ultrax_pin,
    ultrax_row,
)
from xlm.artifacts.store import ArtifactStore
from xlm.cli.data_cmd import app
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, AuthorizationRequiredError
from xlm.data.adapters.essential_web_selector import SOURCE_REVISION, selector_identity
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources import mix01_admission as review
from xlm.data.sources.admission import (
    MIX01_RISK_CONTRACT,
    AdmissionDecision,
    AdmissionGate,
    CatalogAuditor,
    ContaminationMitigation,
    essential_contamination_mitigation,
    load_probe_evidence,
    mix01_contamination_mitigation,
    resolve_verified_production_admission,
    save_admission_decision,
    save_probe_evidence,
    stored_view_ids,
)
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.mix01 import ComponentAdmission, load_mix01_views, mix01_status
from xlm.data.sources.policy import BenchmarkContaminationRisk
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
from xlm.data.sources.schema import FieldDescriptor, ViewSchema

ROWS = [ultrax_row(i) for i in range(12)]
DECISIONS = review.OperatorDecisions(
    operator="test-operator",
    license_decision="approve_research_pretraining",
    provenance_decision="approved",
    benchmark_risk="suspect_with_mitigation",
    rationale="authored fixture decision for an offline test",
)


@pytest.fixture
def store(isolated_xlm_home: Path) -> ArtifactStore:
    target = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    metadata_probe(target)
    return target


def _published(store: ArtifactStore) -> dict[str, object]:
    receipt, record = ce.build_bridge(
        ultrax_pin(), bridge_facts(store, ROWS), evidence_type=EvidenceType.REAL_OBSERVED
    )
    ce.publish_bridge(store, receipt, record)
    return receipt


def _admit(store: ArtifactStore, tmp_path: Path) -> AdmissionDecision:
    receipt = _published(store)
    pin = ultrax_pin()
    reviews = review.build_reviews(pin, receipt, DECISIONS, ["registry note"])
    hashes = review.write_reviews(tmp_path / "reviews", reviews)
    read, read_hashes = review.read_reviews(tmp_path / "reviews")
    assert read_hashes == hashes
    evidence = load_probe_evidence(SOURCE, CONFIG, store)
    assert evidence is not None
    decision = review.build_decision(pin, evidence, receipt, read, read_hashes)
    save_admission_decision(decision, store, tmp_path / "staging")
    return decision


def _plan(view: str = CONFIG, revision: str = REVISION) -> AcquisitionPlan:
    return AcquisitionPlan(
        plan_id="plan_fixture",
        source_id=SOURCE,
        view_id=view,
        provider="huggingface",
        repository="openbmb/UltraX-Preview",
        revision=revision,
        selected_files=["data/x.parquet"],
        limits=AcquisitionLimits(max_transferred_bytes=10 * 1024**3),
        output_artifact_id="raw_fixture",
        is_pilot=False,
    )


def test_mitigated_admission_of_bridged_evidence(store: ArtifactStore, tmp_path: Path) -> None:
    decision = _admit(store, tmp_path)
    assert decision.contract_version == MIX01_RISK_CONTRACT
    assert decision.benchmark_risk == BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION
    assert decision.contamination_mitigation == mix01_contamination_mitigation()
    evidence = load_probe_evidence(SOURCE, CONFIG, store)
    assert evidence is not None
    assert AdmissionGate.evaluate(evidence, decision).admitted
    resolve_verified_production_admission(_plan(), store)
    with pytest.raises(AuthorizationRequiredError):
        resolve_verified_production_admission(_plan(revision="d" * 40), store)
    with pytest.raises(AuthorizationRequiredError):
        resolve_verified_production_admission(_plan(view="UltraX-FineWeb"), store)


def test_non_essential_mitigation_requires_v3_bindings(
    store: ArtifactStore, tmp_path: Path
) -> None:
    decision = _admit(store, tmp_path)
    evidence = load_probe_evidence(SOURCE, CONFIG, store)
    assert evidence is not None
    cases = {
        "contract": decision.model_copy(update={"contract_version": "c04-benchmark-risk-v2"}),
        "scope": decision.model_copy(
            update={"contamination_mitigation": essential_contamination_mitigation()}
        ),
        "reviews": decision.model_copy(update={"reviews_sha256": {"benchmark_risk": "a" * 64}}),
        "provenance": decision.model_copy(update={"provenance_review": "pending"}),
        "resource": decision.model_copy(update={"resource_contract": None}),
        "clean_is_not_mitigated": decision.model_copy(
            update={"benchmark_risk": BenchmarkContaminationRisk.SUSPECT}
        ),
    }
    for name, changed in cases.items():
        assert not AdmissionGate.evaluate(evidence, changed).admitted, name


def test_mitigation_versions_bind_their_scopes() -> None:
    with pytest.raises(ValueError, match="must bind scope"):
        ContaminationMitigation(
            **{**mix01_contamination_mitigation().model_dump(), "version": "c04-benchmark-risk-v2"}
        )
    assert essential_contamination_mitigation().scope.startswith("eventually_frozen_essential")


def test_review_package_refuses_overclaims(store: ArtifactStore) -> None:
    receipt = _published(store)
    pin = ultrax_pin()
    reviews = review.build_reviews(pin, receipt, DECISIONS, [])
    review.check_reviews(pin, receipt, reviews)
    facts = reviews["source_rights"]["facts"]
    assert facts["declared_repository_license"] == "apache-2.0"
    assert "does not establish" in facts["declared_license_scope"]
    assert facts["unknown"] and facts["legal_advice"] is False
    for name, key, value in (
        (
            "source_rights",
            "claims",
            {
                "all_underlying_text_under_declared_license": True,
                "underlying_content_rights_cleared": False,
            },
        ),
        ("benchmark_risk", "zero_contamination_claimed", True),
        ("benchmark_risk", "gate_value", "clean"),
        ("source_rights", "license_decision", "reject"),
        ("attribution", "binding", {**reviews["attribution"]["binding"], "revision": "e" * 40}),
    ):
        changed = json.loads(json.dumps(reviews))
        changed[name][key] = value
        with pytest.raises(review.ReviewRefusal):
            review.check_reviews(pin, receipt, changed)
    with pytest.raises(review.ReviewRefusal, match="never declared benchmark-clean"):
        review.OperatorDecisions(
            "op", "approve_research_pretraining", "approved", "clean", "r"
        ).check()


def test_reviews_are_write_once(store: ArtifactStore, tmp_path: Path) -> None:
    receipt = _published(store)
    reviews = review.build_reviews(ultrax_pin(), receipt, DECISIONS, [])
    review.write_reviews(tmp_path, reviews)
    review.write_reviews(tmp_path, reviews)
    other = review.build_reviews(
        ultrax_pin(),
        receipt,
        review.OperatorDecisions(**{**DECISIONS.__dict__, "operator": "x"}),
        [],
    )
    with pytest.raises(review.ReviewRefusal, match="different content"):
        review.write_reviews(tmp_path, other)


# ------------------------------------------------------------ reporting


def _essential_records(store: ArtifactStore, tmp_path: Path, view: str) -> None:
    schema = ViewSchema(
        view_id=view, fields={"text": FieldDescriptor(name="text", type_name="string")}
    )
    evidence = ProbeEvidenceRecord(
        source_id="essential_web",
        view_id=view,
        provider="huggingface",
        repository="EssentialAI/essential-web-v1.0",
        immutable_revision=SOURCE_REVISION,
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="f" * 64,
        verified_schema=schema,
        declared_license="odc-by",
    )
    save_probe_evidence(evidence, store, tmp_path / f"p-{view}")
    decision = AdmissionDecision(
        contract_version="c04-benchmark-risk-v2",
        source_id="essential_web",
        view_id=view,
        provider="huggingface",
        repository="EssentialAI/essential-web-v1.0",
        immutable_revision=SOURCE_REVISION,
        adapter_id="essential_web_bnormal",
        selector_binding=selector_identity(),
        probe_fingerprint="f" * 64,
        license_review="approved",
        provenance_review="approved",
        benchmark_risk=BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION,
        reviews_sha256={
            k: "a" * 64
            for k in ("source_rights", "attribution", "benchmark_risk", "external_evidence")
        },
        contamination_mitigation=essential_contamination_mitigation(),
        resource_contract="C04/C13; exact limits and matching authorization required",
        operator_approved=True,
    )
    save_admission_decision(decision, store, tmp_path / f"d-{view}")


def test_audit_and_mix01_status_read_per_view_store_admission(
    isolated_xlm_home: Path, tmp_path: Path
) -> None:
    store = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    for view in ("essential_science", "essential_practical", "essential_prose"):
        _essential_records(store, tmp_path, view)
    assert stored_view_ids(store, "essential_web") == [
        "essential_practical",
        "essential_prose",
        "essential_science",
    ]
    catalog = load_catalog("manifests/datasets.catalog.yaml")
    report = CatalogAuditor(catalog, store).audit_all()
    row = next(s for s in report["sources"] if s["source_id"] == "essential_web")
    assert row["status"] == "admitted" and len(row["admitted_views"]) == 3
    assert report["counts"]["admitted"] == 1
    # The old behavior looked only at view 'default', which has no record.
    assert (
        not CatalogAuditor(catalog, store)
        .audit_source(catalog.get_source("essential_web"))
        .admitted
    )

    result = CliRunner().invoke(app, ["mix01-status", "--json"])
    assert result.exit_code == 0, result.output
    status = {entry["component_id"]: entry["readiness"] for entry in json.loads(result.output)}
    assert status["essential_science"] == "ready"
    assert status["essential_practical"] == "ready"
    assert status["essential_prose"] == "ready"
    assert status["ultrax_ultrafineweb"] == "not_live_verified"
    audit = CliRunner().invoke(app, ["audit"])
    assert audit.exit_code == 0 and "essential_science=admitted" in audit.output


def test_gating_is_per_view(isolated_xlm_home: Path, tmp_path: Path) -> None:
    """Production gating already resolved the exact (source, view): unaffected by the bug."""
    store = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    _essential_records(store, tmp_path, "essential_science")
    plan = AcquisitionPlan(
        plan_id="plan_ew",
        source_id="essential_web",
        view_id="essential_science",
        provider="huggingface",
        repository="EssentialAI/essential-web-v1.0",
        revision=SOURCE_REVISION,
        selected_files=["data/x.parquet"],
        limits=AcquisitionLimits(max_transferred_bytes=10 * 1024**3),
        output_artifact_id="raw_ew",
        is_pilot=False,
    )
    resolve_verified_production_admission(plan, store)
    with pytest.raises(AuthorizationRequiredError, match="no probe evidence"):
        resolve_verified_production_admission(plan.model_copy(update={"view_id": "default"}), store)


def test_component_admission_needs_every_view() -> None:
    registry = load_mix01_views("recipes/mixtures/mix01_views.yaml")
    partial = ComponentAdmission({"general": "admitted", "planning": "pending_review"})
    assert partial.state == "pending_review"
    report = mix01_status(
        registry,
        component_admission={"ifm_behaviors_general_planning": partial},
    )
    ifm = next(s for s in report if s.component_id == "ifm_behaviors_general_planning")
    assert ifm.readiness.value == "not_live_verified"
    assert any("planning=pending_review" in reason for reason in ifm.reasons)
    assert ComponentAdmission({"a": "admitted"}).state == "admitted"
    assert ComponentAdmission({"a": "admitted", "b": "blocked"}).state == "blocked"
