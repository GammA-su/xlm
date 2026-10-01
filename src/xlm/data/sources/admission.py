"""Admission gates, multi-criteria audit, and decision persistence adhering to C04."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


ReviewDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

#: The Essential-Web amendment (three Essential views only).
ESSENTIAL_RISK_CONTRACT: Final = "c04-benchmark-risk-v2"
#: The same mitigation extended to every other Mix-01 source (CONTRACTS.md C04).
MIX01_RISK_CONTRACT: Final = "c04-benchmark-risk-v3"
ESSENTIAL_MITIGATION_SCOPE: Final = "eventually_frozen_essential_mix01_canonical_training_pool"
MIX01_MITIGATION_SCOPE: Final = "eventually_frozen_mix01_canonical_training_pool"
#: Review files every mitigated non-Essential decision binds by SHA-256.
MIX01_REVIEW_NAMES = ("source_rights", "attribution", "benchmark_risk", "external_evidence")
RESOURCE_CONTRACT: Final = "C04/C13; exact limits and matching authorization required"


class ContaminationMitigation(BaseModel):
    """Data-only C05 obligation, not an executed exclusion receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["c04-benchmark-risk-v2", "c04-benchmark-risk-v3"]
    mechanism: Literal["xlm.data.exclusion"]
    scope: Literal[
        "eventually_frozen_essential_mix01_canonical_training_pool",
        "eventually_frozen_mix01_canonical_training_pool",
    ]
    benchmarks: tuple[Literal["BLiMP"], Literal["ARC-Easy"], Literal["HellaSwag"], Literal["PIQA"]]
    before_training: Literal[True]
    before_official_benchmark_claims: Literal[True]
    receipt_verifier: Literal["xlm.data.exclusion.receipt.verify_benchmark_claim"]

    @model_validator(mode="after")
    def scope_matches_version(self) -> ContaminationMitigation:
        expected = {
            ESSENTIAL_RISK_CONTRACT: ESSENTIAL_MITIGATION_SCOPE,
            MIX01_RISK_CONTRACT: MIX01_MITIGATION_SCOPE,
        }[self.version]
        if self.scope != expected:
            raise ValueError(f"mitigation {self.version} must bind scope {expected}")
        return self


def essential_contamination_mitigation() -> ContaminationMitigation:
    """The versioned obligation; does not assert that screening has run."""
    return ContaminationMitigation(
        version="c04-benchmark-risk-v2",
        mechanism="xlm.data.exclusion",
        scope="eventually_frozen_essential_mix01_canonical_training_pool",
        benchmarks=("BLiMP", "ARC-Easy", "HellaSwag", "PIQA"),
        before_training=True,
        before_official_benchmark_claims=True,
        receipt_verifier="xlm.data.exclusion.receipt.verify_benchmark_claim",
    )


def mix01_contamination_mitigation() -> ContaminationMitigation:
    """The same C05 obligation for any other Mix-01 source; never a screening result."""
    return ContaminationMitigation(
        version="c04-benchmark-risk-v3",
        mechanism="xlm.data.exclusion",
        scope="eventually_frozen_mix01_canonical_training_pool",
        benchmarks=("BLiMP", "ARC-Easy", "HellaSwag", "PIQA"),
        before_training=True,
        before_official_benchmark_claims=True,
        receipt_verifier="xlm.data.exclusion.receipt.verify_benchmark_claim",
    )


class AdmissionDecision(BaseModel):
    """Auditable record of operator review and admission decisions."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    contract_version: Literal["1", "c04-benchmark-risk-v2", "c04-benchmark-risk-v3"] = "1"
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
    benchmark_risk: BenchmarkContaminationRisk = BenchmarkContaminationRisk.CLEAN
    reviews_sha256: dict[str, ReviewDigest] = Field(default_factory=dict)
    contamination_mitigation: ContaminationMitigation | None = None
    resource_contract: (
        Literal["C04/C13; exact limits and matching authorization required"] | None
    ) = None
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


def _mix01_mitigation_reasons(decision: AdmissionDecision) -> list[str]:
    """What a mitigated decision for a non-Essential Mix-01 source still lacks (C04 risk v3).

    The Essential-scoped v2 obligation can never be reused for another source:
    the contract version, the Mix-01 pool scope, the four review bindings, an
    approved provenance review and the resource contract are all required.
    """
    reasons: list[str] = []
    if decision.contract_version != MIX01_RISK_CONTRACT:
        reasons.append(
            "Mitigated risk for a non-Essential source requires the "
            f"'{MIX01_RISK_CONTRACT}' benchmark-risk contract."
        )
    mitigation = decision.contamination_mitigation
    if mitigation is not None and mitigation != mix01_contamination_mitigation():
        reasons.append("C05 mitigation binding is not the Mix-01 pool obligation.")
    missing = [name for name in MIX01_REVIEW_NAMES if name not in decision.reviews_sha256]
    if missing:
        reasons.append(f"Source/license/provenance review bindings are missing: {missing}.")
    if decision.provenance_review != "approved":
        reasons.append("Provenance review is not approved.")
    if decision.resource_contract is None:
        reasons.append("Resource contract binding is missing.")
    return reasons


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
                if decision.benchmark_risk != BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION:
                    reasons.append("Essential-Web possible contamination requires mitigation.")
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
        if decision.benchmark_risk == BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION:
            if evidence.source_id == "essential_web":
                if decision.contract_version != ESSENTIAL_RISK_CONTRACT:
                    reasons.append(
                        "Mitigated risk requires the versioned C04 benchmark-risk contract."
                    )
            else:
                reasons.extend(_mix01_mitigation_reasons(decision))
            if not decision.reviews_sha256.get("benchmark_risk"):
                reasons.append("Benchmark-risk review binding is missing.")
            if decision.contamination_mitigation is None:
                reasons.append("C05 contamination mitigation binding is missing.")
            if evidence.source_id == "essential_web":
                if not {"source_rights", "attribution", "external_evidence"}.issubset(
                    decision.reviews_sha256
                ):
                    reasons.append("Essential-Web source/license/provenance review is missing.")
                if decision.provenance_review != "approved":
                    reasons.append("Essential-Web provenance review is not approved.")
                if decision.resource_contract is None:
                    reasons.append("Essential-Web resource contract binding is missing.")
        elif decision.benchmark_risk != BenchmarkContaminationRisk.CLEAN:
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
    """Read-only audit engine evaluating candidate catalog sources against admission gates.

    Admission is recorded per (source, view) in the operator artifact store,
    never in the static catalog. Every view the store holds evidence or a
    decision for is re-evaluated through the full gate; a source without any
    stored view is reported for its ``default`` view, as before.
    """

    def __init__(
        self, catalog: DatasetCatalogDraft, artifact_store: ArtifactStore | None = None
    ) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def audit_source(
        self, candidate: CandidateSourceEntry, view_id: str = "default"
    ) -> AdmissionGateResult:
        return evaluate_stored_view(
            self.artifact_store, candidate.source_id, view_id, candidate.repository
        )

    def audit_source_views(self, candidate: CandidateSourceEntry) -> list[AdmissionGateResult]:
        """One full gate evaluation per view the operator store records for this source."""
        views = (
            stored_view_ids(self.artifact_store, candidate.source_id)
            if self.artifact_store is not None
            else []
        )
        return [self.audit_source(candidate, view) for view in views or ["default"]]

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
            views = self.audit_source_views(candidate)
            status = source_status(views)
            counts[status.value] += 1

            results.append(
                {
                    "candidate_number": candidate.candidate_number,
                    "source_id": candidate.source_id,
                    "repository": candidate.repository,
                    "status": status.value,
                    "admitted": status == AdmissionStatus.ADMITTED,
                    "admitted_views": [v.view_id for v in views if v.admitted],
                    "views": [
                        {
                            "view_id": v.view_id,
                            "status": v.status.value,
                            "admitted": v.admitted,
                            "reasons": v.reasons,
                        }
                        for v in views
                    ],
                    "reasons": [reason for v in views for reason in v.reasons],
                }
            )

        return {
            "catalog_id": self.catalog.catalog_id,
            "admission_source": "operator artifact store, per (source, view); never the catalog",
            "counts": counts,
            "sources": results,
            "legal_disclaimer": LEGAL_DISCLAIMER,
        }


def evaluate_stored_view(
    store: ArtifactStore | None, source_id: str, view_id: str, repository: str
) -> AdmissionGateResult:
    """The authoritative gate result of one (source, view) pair in the operator store.

    The same evaluation the production fetch gate performs
    (:func:`resolve_verified_production_admission`), minus the plan binding.
    """
    evidence: ProbeEvidenceRecord | None = None
    decision: AdmissionDecision | None = None
    if store is not None:
        evidence = load_probe_evidence(source_id, view_id, store)
        decision = load_admission_decision(source_id, view_id, store)
    if evidence is None:
        if is_denied_source(repository):
            return AdmissionGateResult(
                admitted=False,
                status=AdmissionStatus.BLOCKED,
                source_id=source_id,
                view_id=view_id,
                reasons=[f"Repository '{repository}' is denied by XLM policy."],
            )
        return AdmissionGateResult(
            admitted=False,
            status=AdmissionStatus.UNADMITTED,
            source_id=source_id,
            view_id=view_id,
            reasons=["Source has not been probed yet (no probe evidence found)."],
        )
    return AdmissionGate.evaluate(evidence, decision)


def source_status(views: list[AdmissionGateResult]) -> AdmissionStatus:
    """Source-level summary of its views: admitted when at least one view is admitted.

    The per-view results stay in the report; a component that needs several
    views must check each of them (see ``xlm.data.sources.mix01``).
    """
    statuses = {v.status for v in views}
    for status in (
        AdmissionStatus.ADMITTED,
        AdmissionStatus.PENDING_REVIEW,
        AdmissionStatus.UNADMITTED,
    ):
        if status in statuses:
            return status
    return AdmissionStatus.BLOCKED


def stored_view_ids(store: ArtifactStore, source_id: str) -> list[str]:
    """Views with a completed probe-evidence or admission artifact for ``source_id``.

    Discovery reads only each artifact's manifest metadata; the evaluation that
    follows loads and verifies the records themselves. Artifact names are never
    parsed, because source and view ids may both contain underscores.
    """
    views: set[str] = set()
    for kind in ("probe_evidence", "admission_decision"):
        root = store.paths.root / kind
        if not root.is_dir():
            continue
        for directory in root.iterdir():
            manifest = directory / "manifest.json"
            if not (directory / "_COMPLETED").is_file() or not manifest.is_file():
                continue
            try:
                metadata = json.loads(manifest.read_text(encoding="utf-8")).get("metadata", {})
            except (OSError, ValueError, AttributeError):
                continue
            if not isinstance(metadata, dict):
                continue
            view = metadata.get("view_id")
            if metadata.get("source_id") == source_id and isinstance(view, str) and view:
                views.add(view)
    return sorted(views)


# -------------------------------------------------------------------------
# P01 Artifact Store Persistence Helpers
# -------------------------------------------------------------------------


#: Later attempts supersede earlier ones; every attempt stays in the store.
MAX_EVIDENCE_ATTEMPTS = 16


def attempt_artifact_id(base_id: str, attempt: int) -> str:
    """Artifact id of one attempt. Attempt 1 keeps the original id."""
    if not 1 <= attempt <= MAX_EVIDENCE_ATTEMPTS:
        raise ValueError(f"attempt must be in [1, {MAX_EVIDENCE_ATTEMPTS}]")
    return base_id if attempt == 1 else f"{base_id}.attempt{attempt:02d}"


def latest_attempt(store: ArtifactStore, kind: str, base_id: str) -> int:
    """Highest attempt with a directory in the store; 0 when there is none.

    The store never replaces a published artifact, so a renewed probe or
    decision is published under the next attempt and the older record is
    preserved. Existence alone selects the attempt: an incomplete or corrupt
    newest attempt is never skipped in favor of older evidence.
    """
    for attempt in range(MAX_EVIDENCE_ATTEMPTS, 0, -1):
        if (store.paths.root / kind / attempt_artifact_id(base_id, attempt)).exists():
            return attempt
    return 0


def next_attempt(store: ArtifactStore, kind: str, base_id: str) -> int:
    """First unused attempt for a renewed record."""
    attempt = latest_attempt(store, kind, base_id) + 1
    if attempt > MAX_EVIDENCE_ATTEMPTS:
        raise ValueError(f"'{base_id}' already has {MAX_EVIDENCE_ATTEMPTS} attempts")
    return attempt


def _load_latest(store: ArtifactStore, kind: str, base_id: str, filename: str) -> Any | None:
    attempt = latest_attempt(store, kind, base_id)
    if attempt == 0:
        return None
    artifact_dir = store.paths.root / kind / attempt_artifact_id(base_id, attempt)
    if not artifact_dir.is_dir() or not (artifact_dir / "_COMPLETED").is_file():
        return None
    payload = artifact_dir / filename
    if not payload.is_file():
        return None
    try:
        store.verify_artifact(artifact_dir)
        return json.loads(payload.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_probe_evidence(
    evidence: ProbeEvidenceRecord,
    store: ArtifactStore,
    staging_dir: Path | None = None,
    *,
    attempt: int = 1,
) -> str:
    """Persist probe evidence as an immutable P01 artifact."""
    scratch = staging_dir or (Path(".staging") / f"probe_{evidence.source_id}_{evidence.view_id}")
    scratch.mkdir(parents=True, exist_ok=True)

    evidence_path = scratch / "probe_evidence.json"
    evidence_path.write_text(json.dumps(evidence.to_canonical_dict(), indent=2), encoding="utf-8")

    dep_hash = hashlib.sha256(b"uv.lock").hexdigest()[:16]
    producer_hash = hashlib.sha256(b"xlm.data.sources.prober").hexdigest()[:16]
    artifact_id = attempt_artifact_id(f"probe_{evidence.source_id}_{evidence.view_id}", attempt)
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
    """Load the latest persisted probe evidence attempt from the P01 artifact store."""
    raw = _load_latest(
        store, "probe_evidence", f"probe_{source_id}_{view_id}", "probe_evidence.json"
    )
    if raw is None:
        return None
    try:
        record = ProbeEvidenceRecord.model_validate(raw)
    except Exception:
        return None
    if (record.source_id, record.view_id) != (source_id, view_id):
        return None
    return record


def save_admission_decision(
    decision: AdmissionDecision,
    store: ArtifactStore,
    staging_dir: Path | None = None,
    *,
    attempt: int = 1,
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
    artifact_id = attempt_artifact_id(f"admission_{decision.source_id}_{decision.view_id}", attempt)
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
    """Load the latest persisted admission decision attempt from the P01 artifact store."""
    raw = _load_latest(
        store, "admission_decision", f"admission_{source_id}_{view_id}", "admission_decision.json"
    )
    if raw is None:
        return None
    try:
        decision = AdmissionDecision.model_validate(raw)
    except Exception:
        return None
    if (decision.source_id, decision.view_id) != (source_id, view_id):
        return None
    return decision


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
