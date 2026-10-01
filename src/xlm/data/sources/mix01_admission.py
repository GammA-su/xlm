"""Operator review input and C04 admission decisions for non-Essential Mix-01 sources.

The software never makes a legal determination. It assembles the facts the
certified evidence establishes, lists what remains unknown, and turns an
operator's explicit decisions into four review files plus one admission
decision. The decision is mitigated (``suspect_with_mitigation`` under
``c04-benchmark-risk-v3``): large derived corpora cannot be shown benchmark
clean, so the C05 exclusion obligation over the frozen Mix-01 pool is bound
instead of claiming ``clean``. It also binds the certified bridge receipt, so
a decision can never outlive the evidence, adapter or revision it reviewed.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.artifacts.store import ArtifactStore
from xlm.data.sources.admission import (
    MIX01_REVIEW_NAMES,
    MIX01_RISK_CONTRACT,
    RESOURCE_CONTRACT,
    AdmissionDecision,
    load_admission_decision,
    mix01_contamination_mitigation,
)
from xlm.data.sources.catalog import DatasetCatalogDraft
from xlm.data.sources.certified_evidence import (
    BridgeRefusal,
    SourcePin,
    check_receipt,
    resolve_pin,
    stored_bridge,
    verify_current,
)
from xlm.data.sources.mix01 import (
    LiveVerification,
    Mix01ViewRegistry,
    Mix01ViewSpec,
    component_admission_views,
)
from xlm.data.sources.policy import (
    BenchmarkContaminationRisk,
    LicenseReviewStatus,
    LicenseUsagePolicy,
)
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord

REVIEW_VERSION = "mix01-source-review-v1"
REVIEW_FILES = {
    "source_rights": "source-rights-review.json",
    "attribution": "attribution-plan.json",
    "benchmark_risk": "benchmark-risk-review.json",
    "external_evidence": "external-source-evidence.json",
}
BENCHMARKS = ("BLiMP", "ARC-Easy", "HellaSwag", "PIQA")
LICENSE_DECISIONS = ("approve_research_pretraining", "reject")
PROVENANCE_DECISIONS = ("approved", "rejected")
#: The bridge receipt binding a decision carries next to the four reviews.
EVIDENCE_BINDING = "certified_evidence"
#: Facts the operator must establish outside this software for derived web corpora.
UNKNOWN_FOR_DERIVED_CORPORA = (
    "per-document license or terms of the underlying web pages",
    "whether any underlying content rights are cleared (never assumed)",
    "the exact upstream corpus lineage beyond what the dataset card and row labels state",
    "benchmark contamination: unknown until C05 screening runs on the frozen pool",
)


class ReviewRefusal(ValueError):
    """A review package or decision is missing, unbound or overclaims."""


@dataclass(frozen=True)
class OperatorDecisions:
    """What only the operator can decide; the tool never fills these."""

    operator: str
    license_decision: str
    provenance_decision: str
    benchmark_risk: str
    rationale: str

    def check(self) -> None:
        if not self.operator.strip() or not self.rationale.strip():
            raise ReviewRefusal("a named operator and a written rationale are required")
        if self.license_decision not in LICENSE_DECISIONS:
            raise ReviewRefusal(f"license decision must be one of {LICENSE_DECISIONS}")
        if self.provenance_decision not in PROVENANCE_DECISIONS:
            raise ReviewRefusal(f"provenance decision must be one of {PROVENANCE_DECISIONS}")
        if self.benchmark_risk != BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION:
            raise ReviewRefusal(
                "this workflow records only suspect_with_mitigation; a derived corpus is "
                "never declared benchmark-clean here"
            )


def review_facts(
    pin: SourcePin, receipt: Mapping[str, Any], registry_notes: list[str]
) -> dict[str, Any]:
    """Known facts for the operator, each traced to the certified evidence."""
    check_receipt(receipt)
    if receipt["source"] != pin.as_dict():
        raise ReviewRefusal("bridge receipt binds another source, view or revision")
    probe = receipt["certification"]["probe"]
    sets = receipt["adapter"]["row_sets"]
    return {
        "source": pin.as_dict(),
        "bridge_receipt_digest": receipt["digest"],
        "declared_repository_license": receipt["declared_license"],
        "declared_license_scope": "the dataset repository's metadata declaration only; it does "
        "not establish the license of every underlying document",
        "license_caveat_from_evidence": receipt["license_caveat"],
        "configs_observed": probe.get("configs_observed"),
        "row_source_labels": probe.get("row_source_labels"),
        "schema_fields": sorted(receipt["schema"]["fields"]),
        "schema_basis": receipt["schema_basis"],
        "observed_files": [
            {"path": f["path"], "length": f["length"]} for f in receipt["observed_files"]
        ],
        "adapter_certification": {
            name: {"rows": s["rows"], "accepted": s["accepted"], "rejected": s["rejected"]}
            for name, s in sets.items()
        },
        "registry_notes": list(registry_notes),
        "unknown": list(UNKNOWN_FOR_DERIVED_CORPORA),
        "decision_fields_required": {
            "license_decision": list(LICENSE_DECISIONS),
            "provenance_decision": list(PROVENANCE_DECISIONS),
            "benchmark_risk": [BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION.value],
            "operator": "named person recording the decision",
            "rationale": "the operator's own written reasoning",
        },
        "legal_advice": False,
    }


def build_reviews(
    pin: SourcePin,
    receipt: Mapping[str, Any],
    decisions: OperatorDecisions,
    registry_notes: list[str],
) -> dict[str, dict[str, Any]]:
    """Four review documents from the evidence facts plus the operator's decisions."""
    decisions.check()
    facts = review_facts(pin, receipt, registry_notes)
    binding = {
        "source_id": pin.source_id,
        "view_id": pin.view_id,
        "repository": pin.repository,
        "revision": pin.revision,
        "bridge_receipt_digest": receipt["digest"],
    }
    common = {"version": REVIEW_VERSION, "binding": binding, "legal_advice": False}
    return {
        "source_rights": {
            **common,
            "facts": facts,
            "operator": decisions.operator,
            "license_decision": decisions.license_decision,
            "provenance_decision": decisions.provenance_decision,
            "rationale": decisions.rationale,
            "claims": {
                "all_underlying_text_under_declared_license": False,
                "underlying_content_rights_cleared": False,
            },
        },
        "attribution": {
            **common,
            "attributions": [
                {
                    "dataset": pin.repository,
                    "revision": pin.revision,
                    "view": pin.view_id,
                    "declared_license": receipt["declared_license"],
                    "caveat": receipt["license_caveat"],
                }
            ],
        },
        "benchmark_risk": {
            **common,
            "benchmarks": list(BENCHMARKS),
            "gate_value": decisions.benchmark_risk,
            "zero_contamination_claimed": False,
            "contamination_possible": True,
            "permission_to_acquire_and_pretrain": True,
            "permission_to_claim_uncontaminated_benchmark_results": False,
            "mitigation": "C05 xlm.data.exclusion over the eventually frozen Mix-01 canonical "
            "training pool before tokenizer fitting and training",
            "mitigation_binding": mix01_contamination_mitigation().model_dump(mode="json"),
        },
        "external_evidence": {
            **common,
            "sources": [
                {
                    "kind": "dataset_repository",
                    "reference": f"https://huggingface.co/datasets/{pin.repository}",
                    "revision": pin.revision,
                },
                {"kind": "certified_bridge_receipt", "digest": receipt["digest"]},
                *[
                    {"kind": "certified_input", "name": name, "sha256": value.get("sha256")}
                    for name, value in sorted(receipt["certification"]["inputs"].items())
                ],
            ],
        },
    }


def check_reviews(
    pin: SourcePin, receipt: Mapping[str, Any], reviews: Mapping[str, Mapping[str, Any]]
) -> None:
    """Refuse a package that is incomplete, bound elsewhere, or overclaims."""
    if set(reviews) != set(REVIEW_FILES):
        raise ReviewRefusal(f"review package must hold exactly {sorted(REVIEW_FILES)}")
    for name, review in reviews.items():
        binding = review.get("binding", {})
        if (
            review.get("version") != REVIEW_VERSION
            or binding.get("source_id") != pin.source_id
            or binding.get("view_id") != pin.view_id
            or binding.get("repository") != pin.repository
            or binding.get("revision") != pin.revision
            or binding.get("bridge_receipt_digest") != receipt["digest"]
        ):
            raise ReviewRefusal(f"{name} review is not bound to this source and evidence")
        if review.get("legal_advice") is not False:
            raise ReviewRefusal(f"{name} review must state that it is not legal advice")
    rights = reviews["source_rights"]
    claims = rights.get("claims", {})
    if (
        claims.get("all_underlying_text_under_declared_license") is not False
        or claims.get("underlying_content_rights_cleared") is not False
    ):
        raise ReviewRefusal("source review must not claim cleared underlying-content rights")
    if (
        rights.get("license_decision") != "approve_research_pretraining"
        or rights.get("provenance_decision") != "approved"
    ):
        raise ReviewRefusal("the operator did not approve license and provenance")
    if not str(rights.get("operator", "")).strip() or not str(rights.get("rationale", "")).strip():
        raise ReviewRefusal("source review lacks a named operator or rationale")
    if not reviews["attribution"].get("attributions"):
        raise ReviewRefusal("attribution plan lists no attribution")
    risk = reviews["benchmark_risk"]
    if sorted(risk.get("benchmarks", [])) != sorted(BENCHMARKS):
        raise ReviewRefusal("benchmark review does not cover the four core benchmarks")
    if (
        risk.get("zero_contamination_claimed") is not False
        or risk.get("contamination_possible") is not True
        or risk.get("permission_to_claim_uncontaminated_benchmark_results") is not False
        or risk.get("permission_to_acquire_and_pretrain") is not True
        or risk.get("gate_value") != BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION
        or risk.get("mitigation_binding")
        != mix01_contamination_mitigation().model_dump(mode="json")
    ):
        raise ReviewRefusal("benchmark review overclaims or lacks its C05 mitigation")
    if not reviews["external_evidence"].get("sources"):
        raise ReviewRefusal("external evidence manifest lists no source")


def review_bytes(review: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(review), indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_reviews(directory: Path, reviews: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Write-once review files; an identical rerun is a no-op, a different one refuses."""
    directory.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for name, filename in REVIEW_FILES.items():
        data = review_bytes(reviews[name])
        path = directory / filename
        if path.exists():
            if path.read_bytes() != data:
                raise ReviewRefusal(f"{filename} already exists with different content")
        else:
            temporary = path.with_name(f"{filename}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        digests[name] = hashlib.sha256(data).hexdigest()
    return digests


def read_reviews(directory: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    reviews: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for name, filename in REVIEW_FILES.items():
        path = directory / filename
        if not path.is_file() or path.stat().st_size > 1024**2:
            raise ReviewRefusal(f"review file {filename} is missing or oversized")
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ReviewRefusal(f"{filename} is not a JSON object")
        reviews[name] = value
        digests[name] = hashlib.sha256(data).hexdigest()
    return reviews, digests


def build_decision(
    pin: SourcePin,
    evidence: ProbeEvidenceRecord,
    receipt: Mapping[str, Any],
    reviews: Mapping[str, Mapping[str, Any]],
    review_sha256: Mapping[str, str],
) -> AdmissionDecision:
    """Approved mitigated decision bound to evidence, reviews and the bridge receipt."""
    check_receipt(receipt)
    check_reviews(pin, receipt, reviews)
    if set(review_sha256) != set(MIX01_REVIEW_NAMES):
        raise ReviewRefusal("decision needs the SHA-256 of every review")
    if (
        evidence.probe_fingerprint != receipt["probe_fingerprint"]
        or evidence.immutable_revision != pin.revision
        or evidence.resource_metrics.get("bridge_receipt_digest") != receipt["digest"]
    ):
        raise ReviewRefusal("current store evidence is not the reviewed bridge publication")
    operator = str(reviews["source_rights"]["operator"]).strip()
    hashes = " ".join(f"{k}={review_sha256[k]}" for k in sorted(review_sha256))
    return AdmissionDecision(
        contract_version=MIX01_RISK_CONTRACT,
        source_id=pin.source_id,
        view_id=pin.view_id,
        provider=pin.provider,
        repository=pin.repository,
        immutable_revision=pin.revision,
        adapter_id=pin.adapter_id,
        probe_fingerprint=receipt["probe_fingerprint"],
        license_review=LicenseReviewStatus.APPROVED,
        provenance_review="approved",
        benchmark_risk=BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION,
        reviews_sha256={**dict(review_sha256), EVIDENCE_BINDING: str(receipt["digest"])},
        contamination_mitigation=mix01_contamination_mitigation(),
        resource_contract=RESOURCE_CONTRACT,
        usage_policy=LicenseUsagePolicy.STRICT_RESEARCH,
        operator_approved=True,
        operator_notes=(
            f"mix01 source review {REVIEW_VERSION}; operator={operator}; review sha256 {hashes}; "
            f"certified evidence {receipt['digest']}; license approval covers research "
            "pretraining under the declared repository license only and clears no underlying "
            "content rights; suspect_with_mitigation records possible contamination, not zero "
            "contamination; a C05 exclusion receipt over the frozen Mix-01 pool is required "
            "before any uncontaminated benchmark claim"
        ),
    )


def decision_binds_bridge(decision: AdmissionDecision, receipt: Mapping[str, Any]) -> bool:
    return decision.reviews_sha256.get(EVIDENCE_BINDING) == receipt.get("digest")


def _view_live_verification(
    store: ArtifactStore,
    catalog: DatasetCatalogDraft,
    registry: Mix01ViewRegistry,
    spec: Mix01ViewSpec,
    view_id: str,
) -> str:
    """The bridge digest proving live rows for one view; refuses on any gap or drift."""
    receipt = stored_bridge(store, spec.source_id, view_id)
    if receipt is None:
        raise BridgeRefusal(f"view '{view_id}' has no certified bridge evidence")
    if receipt.get("evidence_type") != EvidenceType.REAL_OBSERVED.value:
        raise BridgeRefusal(f"view '{view_id}' bridge evidence is not real observed rows")
    source = receipt.get("source")
    adapter_id = source.get("adapter_id") if isinstance(source, Mapping) else None
    if not isinstance(adapter_id, str):
        raise BridgeRefusal(f"view '{view_id}' bridge receipt names no adapter")
    # The pin is the registry's exact source, view, component, revision and adapter;
    # verify_current also binds the current adapter code and the stored record.
    pin = resolve_pin(catalog, registry, spec.source_id, view_id, adapter_id)
    if pin.component_id != spec.component_id:
        raise BridgeRefusal(f"view '{view_id}' bridge binds another component")
    receipt = verify_current(store, pin)
    adapter = receipt.get("adapter")
    row_sets = adapter.get("row_sets") if isinstance(adapter, Mapping) else None
    if not isinstance(row_sets, Mapping) or not any(
        isinstance(rows, Mapping) and int(rows.get("accepted", 0)) > 0 for rows in row_sets.values()
    ):
        raise BridgeRefusal(f"view '{view_id}' bridge certified no accepted real row")
    decision = load_admission_decision(spec.source_id, view_id, store)
    if decision is None:
        raise BridgeRefusal(f"view '{view_id}' has no admission decision")
    if (
        not decision_binds_bridge(decision, receipt)
        or decision.immutable_revision != pin.revision
        or decision.adapter_id != pin.adapter_id
        or decision.repository != pin.repository
        or decision.probe_fingerprint != receipt.get("probe_fingerprint")
    ):
        raise BridgeRefusal(
            f"view '{view_id}' admission decision does not bind its certified bridge evidence"
        )
    return str(receipt["digest"])


def live_verification(
    store: ArtifactStore,
    catalog: DatasetCatalogDraft,
    registry: Mix01ViewRegistry,
    spec: Mix01ViewSpec,
) -> LiveVerification:
    """Derive a component's live-row verification from immutable certified evidence.

    Every view the component needs must carry a verified, real-observed bridge
    receipt that still binds the registry pin (source, view, component, exact
    revision, adapter) and the current adapter code, whose adapter accepted real
    rows, and that its admission decision binds by digest, revision, adapter and
    fingerprint. Admission state itself is judged separately by the gate; this
    never makes an admitted source live-verified without such evidence.
    """
    digests: list[str] = []
    try:
        for view_id in component_admission_views(spec):
            digests.append(
                f"{view_id}={_view_live_verification(store, catalog, registry, spec, view_id)}"
            )
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return LiveVerification(verified=False, reason=str(exc))
    return LiveVerification(
        verified=True,
        reason="real-row adapter certification bound by admission (bridge "
        + ", ".join(digests)
        + ").",
    )
