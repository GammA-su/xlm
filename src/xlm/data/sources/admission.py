"""Admission gates, multi-criteria audit, and decision persistence adhering to C04."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xlm.artifacts.store import ArtifactStore
from xlm.data.acquisition.plan import AcquisitionPlan, AuthorizationRequiredError
from xlm.data.sources.catalog import CandidateSourceEntry, DatasetCatalogDraft
from xlm.data.sources.policy import (
    LEGAL_DISCLAIMER,
    BenchmarkContaminationRisk,
    LicenseReviewStatus,
    LicenseUsagePolicy,
    evaluate_license_review,
    is_denied_source,
)
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome


class AdmissionStatus(StrEnum):
    """Operational admission states for dataset source views."""

    ADMITTED = "admitted"
    UNADMITTED = "unadmitted"
    BLOCKED = "blocked"
    PENDING_REVIEW = "pending_review"


class AdmissionDecision(BaseModel):
    """Auditable record of operator review and admission decisions."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    source_id: str
    view_id: str = "default"
    provider: str
    repository: str
    immutable_revision: str
    adapter_id: str
    adapter_version: str = "1"
    selector_binding: dict[str, str] | None = None
    probe_fingerprint: str
    license_review: str = LicenseReviewStatus.PENDING
    provenance_review: str = "pending"
    benchmark_risk: str = BenchmarkContaminationRisk.CLEAN
    usage_policy: str = LicenseUsagePolicy.STRICT_RESEARCH
    operator_approved: bool = False
    operator_notes: str = ""
    decision_timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    legal_disclaimer: str = LEGAL_DISCLAIMER


class AdmissionGateResult(BaseModel):
    """Result of evaluating a source view against all mandatory admission criteria."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    admitted: bool
    status: AdmissionStatus
    source_id: str
    view_id: str
    reasons: list[str] = Field(default_factory=list)


class AdmissionGate:
    """Strict multi-criteria admission evaluator enforcing Contract C04 and A12/A13."""

    @classmethod
    def evaluate(
        cls,
        evidence: ProbeEvidenceRecord,
        decision: AdmissionDecision | None = None,
        usage_policy: LicenseUsagePolicy = LicenseUsagePolicy.STRICT_RESEARCH,
    ) -> AdmissionGateResult:
        reasons: list[str] = []

        # 1. Direct source denial check
        if is_denied_source(evidence.repository):
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.BLOCKED,
                source_id=evidence.source_id,
                view_id=evidence.view_id,
                reasons=[
                    f"Repository '{evidence.repository}' is denied by XLM policy "
                    "(FineWeb / FineWeb-Edu ban)."
                ],
            )

        # 2. Synthetic fixture rejection: synthetic evidence cannot satisfy production admission
        if evidence.evidence_type == EvidenceType.SYNTHETIC_FIXTURE:
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.UNADMITTED,
                source_id=evidence.source_id,
                view_id=evidence.view_id,
                reasons=[
                    "Synthetic fixture evidence is strictly prohibited from "
                    "satisfying production admission gates."
                ],
            )

        # 3. Probe outcome check
        if evidence.outcome != ProbeOutcome.ACCESSIBLE:
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.UNADMITTED,
                source_id=evidence.source_id,
                view_id=evidence.view_id,
                reasons=[
                    f"Discovery probe outcome is '{evidence.outcome}', not 'accessible'. "
                    f"Reason: {evidence.reason or 'Incomplete discovery.'}"
                ],
            )

        # 4. Mandatory immutable revision
        if not evidence.immutable_revision:
            reasons.append("Immutable commit revision / content digest is unresolved.")

        # 5. Mandatory schema verification
        if not evidence.verified_schema:
            reasons.append("Dataset view schema has not been verified against a tested adapter.")

        # 6. Operator decision presence
        if decision is None:
            reasons.append("No operator admission decision recorded.")
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.PENDING_REVIEW,
                source_id=evidence.source_id,
                view_id=evidence.view_id,
                reasons=reasons,
            )

        # 7. Stale evidence / fingerprint invalidation check
        for key in ("source_id", "view_id", "provider", "repository"):
            if getattr(decision, key) != getattr(evidence, key):
                reasons.append(f"Admission decision {key} differs from probe evidence.")
        if evidence.source_id == "essential_web":
            from xlm.data.adapters.essential_web_selector import (
                ADMITTED_COMPONENTS,
                SOURCE_REVISION,
                selector_identity,
            )

            if evidence.view_id in ADMITTED_COMPONENTS:
                if evidence.repository != "EssentialAI/essential-web-v1.0":
                    reasons.append("Essential-Web repository differs from the production source.")
                if evidence.immutable_revision != SOURCE_REVISION:
                    reasons.append("Essential-Web revision differs from the production pin.")
                if decision.adapter_id != "essential_web_bnormal":
                    reasons.append("Essential-Web production requires essential_web_bnormal.")
                if decision.selector_binding != selector_identity():
                    reasons.append(
                        "Essential-Web production selector binding differs from B-normal."
                    )

        if decision.probe_fingerprint != evidence.probe_fingerprint:
            reasons.append(
                "Admission decision probe fingerprint does not match current evidence fingerprint. "
                "Schema, files, or snapshot changed; revalidation required."
            )

        if decision.immutable_revision != evidence.immutable_revision:
            reasons.append(
                f"Admission decision revision '{decision.immutable_revision}' does not match "
                f"current evidence revision '{evidence.immutable_revision}'."
            )

        # 8. License & provenance review
        is_lic_ok, lic_msg = evaluate_license_review(
            declared_license=evidence.declared_license,
            review_status=decision.license_review,
            usage_policy=usage_policy,
        )
        if not is_lic_ok:
            reasons.append(lic_msg)

        # 9. Benchmark contamination check
        if decision.benchmark_risk != BenchmarkContaminationRisk.CLEAN:
            reasons.append(
                f"Benchmark risk status is '{decision.benchmark_risk}'. "
                "Benchmark-containing blends are disabled pending component audit."
            )

        # 10. Operator approval boolean
        # Note: Operator approval cannot override missing mandatory evidence or direct denial
        if not decision.operator_approved:
            reasons.append("Explicit operator approval is missing.")

        if reasons:
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.PENDING_REVIEW,
                source_id=evidence.source_id,
                view_id=evidence.view_id,
                reasons=reasons,
            )

        return AdmissionGateResult(
            admitted=True,
            status=AdmissionStatus.ADMITTED,
            source_id=evidence.source_id,
            view_id=evidence.view_id,
            reasons=["All admission criteria verified and operator approved."],
        )


class CatalogAuditor:
    """Read-only audit engine evaluating candidate catalog sources against admission gates."""

    def __init__(
        self, catalog: DatasetCatalogDraft, artifact_store: ArtifactStore | None = None
    ) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def audit_source(
        self, candidate: CandidateSourceEntry, view_id: str = "default"
    ) -> AdmissionGateResult:
        # Load evidence and decision from artifact store if available
        evidence: ProbeEvidenceRecord | None = None
        decision: AdmissionDecision | None = None

        if self.artifact_store is not None:
            evidence = load_probe_evidence(candidate.source_id, view_id, self.artifact_store)
            decision = load_admission_decision(candidate.source_id, view_id, self.artifact_store)

        if evidence is None:
            # Check direct denial first
            if is_denied_source(candidate.repository):
                return AdmissionGateResult(
                    admitted=False,
                    status=AdmissionStatus.BLOCKED,
                    source_id=candidate.source_id,
                    view_id=view_id,
                    reasons=[f"Repository '{candidate.repository}' is denied by XLM policy."],
                )

            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.UNADMITTED,
                source_id=candidate.source_id,
                view_id=view_id,
                reasons=["Source has not been probed yet (no probe evidence found)."],
            )

        return AdmissionGate.evaluate(evidence, decision)

    def audit_all(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        counts = {
            "total_candidates": len(self.catalog.sources),
            "admitted": 0,
            "pending_review": 0,
            "unadmitted": 0,
            "blocked": 0,
        }

        for candidate in self.catalog.sources:
            res = self.audit_source(candidate)
            status_str = res.status.value
            if res.admitted:
                counts["admitted"] += 1
            elif res.status == AdmissionStatus.BLOCKED:
                counts["blocked"] += 1
            elif res.status == AdmissionStatus.PENDING_REVIEW:
                counts["pending_review"] += 1
            else:
                counts["unadmitted"] += 1

            results.append(
                {
                    "candidate_number": candidate.candidate_number,
                    "source_id": candidate.source_id,
                    "repository": candidate.repository,
                    "status": status_str,
                    "admitted": res.admitted,
                    "reasons": res.reasons,
                }
            )

        return {
            "catalog_id": self.catalog.catalog_id,
            "counts": counts,
            "sources": results,
            "legal_disclaimer": LEGAL_DISCLAIMER,
        }


# -------------------------------------------------------------------------
# P01 Artifact Store Persistence Helpers
# -------------------------------------------------------------------------


def save_probe_evidence(
    evidence: ProbeEvidenceRecord,
    store: ArtifactStore,
    staging_dir: Path | None = None,
) -> str:
    """Persist probe evidence as an immutable P01 artifact."""
    scratch = staging_dir or (Path(".staging") / f"probe_{evidence.source_id}_{evidence.view_id}")
    scratch.mkdir(parents=True, exist_ok=True)

    evidence_path = scratch / "probe_evidence.json"
    evidence_path.write_text(json.dumps(evidence.to_canonical_dict(), indent=2), encoding="utf-8")

    dep_hash = hashlib.sha256(b"uv.lock").hexdigest()[:16]
    producer_hash = hashlib.sha256(b"xlm.data.sources.prober").hexdigest()[:16]
    artifact_id = f"probe_{evidence.source_id}_{evidence.view_id}"
    published_dir = store.publish_artifact(
        artifact_id=artifact_id,
        kind="probe_evidence",
        files={"probe_evidence.json": evidence_path},
        producer_code_hash=producer_hash,
        dependency_hash=dep_hash,
        resolved_config_hash=evidence.probe_fingerprint or "unverified_config",
        metadata={
            "source_id": evidence.source_id,
            "view_id": evidence.view_id,
            "outcome": evidence.outcome.value,
            "evidence_type": evidence.evidence_type.value,
        },
    )
    return str(published_dir)


def load_probe_evidence(
    source_id: str, view_id: str, store: ArtifactStore
) -> ProbeEvidenceRecord | None:
    """Load persisted probe evidence from P01 artifact store."""
    artifact_id = f"probe_{source_id}_{view_id}"
    artifact_dir = store.paths.root / "probe_evidence" / artifact_id
    if not artifact_dir.is_dir() or not (artifact_dir / "_COMPLETED").is_file():
        return None
    evidence_file = artifact_dir / "probe_evidence.json"
    if not evidence_file.is_file():
        return None
    try:
        store.verify_artifact(artifact_dir)
        raw = json.loads(evidence_file.read_text(encoding="utf-8"))
        return ProbeEvidenceRecord.model_validate(raw)
    except Exception:
        return None


def save_admission_decision(
    decision: AdmissionDecision,
    store: ArtifactStore,
    staging_dir: Path | None = None,
) -> str:
    """Persist an admission decision as an immutable P01 artifact."""
    scratch = staging_dir or (
        Path(".staging") / f"decision_{decision.source_id}_{decision.view_id}"
    )
    scratch.mkdir(parents=True, exist_ok=True)

    decision_path = scratch / "admission_decision.json"
    decision_path.write_text(json.dumps(decision.model_dump(), indent=2), encoding="utf-8")

    dep_hash = hashlib.sha256(b"uv.lock").hexdigest()[:16]
    producer_hash = hashlib.sha256(b"xlm.data.sources.admission").hexdigest()[:16]
    artifact_id = f"admission_{decision.source_id}_{decision.view_id}"
    published_dir = store.publish_artifact(
        artifact_id=artifact_id,
        kind="admission_decision",
        files={"admission_decision.json": decision_path},
        producer_code_hash=producer_hash,
        dependency_hash=dep_hash,
        resolved_config_hash=decision.probe_fingerprint or "unverified_config",
        metadata={
            "source_id": decision.source_id,
            "view_id": decision.view_id,
            "operator_approved": decision.operator_approved,
        },
    )
    return str(published_dir)


def load_admission_decision(
    source_id: str, view_id: str, store: ArtifactStore
) -> AdmissionDecision | None:
    """Load persisted admission decision from P01 artifact store."""
    artifact_id = f"admission_{source_id}_{view_id}"
    artifact_dir = store.paths.root / "admission_decision" / artifact_id
    if not artifact_dir.is_dir() or not (artifact_dir / "_COMPLETED").is_file():
        return None
    decision_file = artifact_dir / "admission_decision.json"
    if not decision_file.is_file():
        return None
    try:
        store.verify_artifact(artifact_dir)
        raw = json.loads(decision_file.read_text(encoding="utf-8"))
        return AdmissionDecision.model_validate(raw)
    except Exception:
        return None


def resolve_verified_production_admission(plan: AcquisitionPlan, store: ArtifactStore) -> None:
    """Resolve the stored admission decision authorizing a production plan.

    Binds the exact source/view/revision: loads current probe evidence and the
    recorded operator decision from the store, re-evaluates the full admission
    gate (denial, outcome, revision, schema, fingerprint match, license,
    benchmark risk, operator approval), and requires the decision's revision to
    equal the plan's revision. Anything missing, rejected, stale, or mismatched
    raises AuthorizationRequiredError with the specific reason; an admission
    reference string alone is never sufficient.
    """
    evidence = load_probe_evidence(plan.source_id, plan.view_id, store)
    if evidence is None:
        raise AuthorizationRequiredError(
            f"Production acquisition for '{plan.source_id}:{plan.view_id}' has no "
            "probe evidence in the store; re-probe the source before requesting "
            "production authorization."
        )
    decision = load_admission_decision(plan.source_id, plan.view_id, store)
    if decision is None:
        raise AuthorizationRequiredError(
            f"Production acquisition for '{plan.source_id}:{plan.view_id}' has no "
            "recorded operator admission decision; record one with 'data admit'."
        )
    gate = AdmissionGate.evaluate(evidence, decision)
    if not gate.admitted:
        raise AuthorizationRequiredError(
            f"Production acquisition for '{plan.source_id}:{plan.view_id}' is not "
            f"admitted ({gate.status.value}): " + "; ".join(gate.reasons)
        )
    if decision.immutable_revision != plan.revision:
        raise AuthorizationRequiredError(
            f"Admission decision revision '{decision.immutable_revision}' does not "
            f"match plan revision '{plan.revision}'; re-probe and re-admit."
        )
    if evidence.immutable_revision != plan.revision:
        raise AuthorizationRequiredError(
            f"Plan revision '{plan.revision}' does not match probed revision "
            f"'{evidence.immutable_revision}'; the plan does not bind the selected "
            "snapshot."
        )
