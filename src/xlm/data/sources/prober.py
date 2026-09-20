"""Dataset source prober executing bounded discovery and emitting typed evidence records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xlm.data.sources.catalog import CandidateSourceEntry
from xlm.data.sources.policy import (
    is_denied_source,
)
from xlm.data.sources.schema import (
    RedactionUtility,
    RowExtractorContract,
    ViewSchema,
    compute_probe_fingerprint,
)
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    DeadlineExceededError,
    DiscoveryTransport,
    HostNotAllowlistedError,
    TransportBudget,
)


class ProbeOutcome(StrEnum):
    """Honest outcomes for candidate source discovery probing."""

    ACCESSIBLE = "accessible"
    GATED = "gated"
    INACCESSIBLE_OR_NOT_FOUND = "inaccessible_or_not_found"
    REVISION_MISSING = "revision_missing"
    SCHEMA_MISMATCH = "schema_mismatch"
    PARTIAL = "partial"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNSUPPORTED = "unsupported"
    TRANSIENT_ERROR = "transient_error"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class EvidenceType(StrEnum):
    """Distinction between real observed evidence and synthetic testing fixtures."""

    REAL_OBSERVED = "real_observed"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class ProbeEvidenceRecord(BaseModel):
    """Immutable auditable record of discovery probing results adhering to Contract C04."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    source_id: str
    view_id: str = "default"
    provider: str
    repository: str
    immutable_revision: str | None = None
    outcome: ProbeOutcome
    evidence_type: EvidenceType = EvidenceType.REAL_OBSERVED
    probe_fingerprint: str | None = None
    observed_timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    observed_files_count: int = 0
    total_files_count_declared: int | None = None
    verified_schema: ViewSchema | None = None
    sample_preview: list[dict[str, Any]] = Field(default_factory=list)
    declared_license: str | None = None
    is_gated: bool = False
    conversion_status: dict[str, Any] | None = None
    resource_metrics: dict[str, Any] = Field(default_factory=dict)
    unresolved_requirements: list[str] = Field(default_factory=list)
    reason: str | None = None

    def to_canonical_dict(self) -> dict[str, Any]:
        """Convert record to a canonical JSON-serializable dictionary."""
        d = self.model_dump()
        return d


class SourceProber:
    """Discovery prober inspecting candidate repository snapshots under bounded resource budgets."""

    def __init__(
        self,
        candidate: CandidateSourceEntry,
        transport: DiscoveryTransport,
        budget: TransportBudget,
        view_id: str = "default",
        extractor_contract: RowExtractorContract | None = None,
        evidence_type: EvidenceType = EvidenceType.REAL_OBSERVED,
    ) -> None:
        self.candidate = candidate
        self.transport = transport
        self.budget = budget
        self.view_id = view_id
        self.extractor_contract = extractor_contract
        self.evidence_type = evidence_type

    def probe(self) -> ProbeEvidenceRecord:
        """Execute discovery probe and return typed evidence record."""
        # 1. Check direct denial policy
        if is_denied_source(self.candidate.repository):
            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                outcome=ProbeOutcome.BLOCKED_BY_POLICY,
                evidence_type=self.evidence_type,
                reason=(
                    f"Repository '{self.candidate.repository}' is denied by XLM policy "
                    "(FineWeb / FineWeb-Edu denial)."
                ),
                unresolved_requirements=["denied_by_policy"],
                resource_metrics=self.budget.to_metrics(),
            )

        try:
            # 2. Get repository snapshot metadata
            snapshot = self.transport.get_snapshot_info(
                self.candidate.repository, pinned_revision=self.candidate.revision
            )

            if snapshot.is_not_found:
                return ProbeEvidenceRecord(
                    source_id=self.candidate.source_id,
                    view_id=self.view_id,
                    provider=self.candidate.provider,
                    repository=self.candidate.repository,
                    outcome=ProbeOutcome.INACCESSIBLE_OR_NOT_FOUND,
                    evidence_type=self.evidence_type,
                    reason=snapshot.error_reason or "Repository not found at provider.",
                    unresolved_requirements=["repository_not_found"],
                    resource_metrics=self.budget.to_metrics(),
                )

            if snapshot.is_gated:
                return ProbeEvidenceRecord(
                    source_id=self.candidate.source_id,
                    view_id=self.view_id,
                    provider=self.candidate.provider,
                    repository=self.candidate.repository,
                    outcome=ProbeOutcome.GATED,
                    evidence_type=self.evidence_type,
                    is_gated=True,
                    reason=snapshot.error_reason
                    or "Repository is gated/private; operator agreement required.",
                    unresolved_requirements=["operator_terms_agreement_required"],
                    resource_metrics=self.budget.to_metrics(),
                )

            rev = snapshot.immutable_revision
            if not rev:
                return ProbeEvidenceRecord(
                    source_id=self.candidate.source_id,
                    view_id=self.view_id,
                    provider=self.candidate.provider,
                    repository=self.candidate.repository,
                    outcome=ProbeOutcome.REVISION_MISSING,
                    evidence_type=self.evidence_type,
                    declared_license=snapshot.declared_license,
                    reason=snapshot.error_reason
                    or "Provider did not provide an immutable commit SHA / content digest.",
                    unresolved_requirements=["immutable_revision_unresolved"],
                    resource_metrics=self.budget.to_metrics(),
                )

            # 3. File inventory (respecting pagination within budget)
            files, _, total_declared = self.transport.list_files(
                self.candidate.repository, pinned_revision=rev
            )

            # 4. Schema and sample verification
            verified_schema: ViewSchema | None = None
            sample_preview: list[dict[str, Any]] = []
            unresolved: list[str] = []

            # If we have files, check for schema inspection
            if self.extractor_contract is not None:
                # If a mock or transport provides a verified schema through card data or metadata
                raw_schema = snapshot.card_data.get("schema")
                if isinstance(raw_schema, ViewSchema):
                    verified_schema = raw_schema
                elif isinstance(raw_schema, dict):
                    verified_schema = ViewSchema.model_validate(raw_schema)

                if verified_schema is not None:
                    is_valid, missing_fields = self.extractor_contract.evaluate_against_schema(
                        verified_schema
                    )
                    if not is_valid:
                        return ProbeEvidenceRecord(
                            source_id=self.candidate.source_id,
                            view_id=self.view_id,
                            provider=self.candidate.provider,
                            repository=self.candidate.repository,
                            immutable_revision=rev,
                            outcome=ProbeOutcome.SCHEMA_MISMATCH,
                            evidence_type=self.evidence_type,
                            observed_files_count=len(files),
                            total_files_count_declared=total_declared,
                            verified_schema=verified_schema,
                            declared_license=snapshot.declared_license,
                            reason=(
                                f"Schema mismatch against adapter "
                                f"'{self.extractor_contract.adapter_id}'. "
                                f"Missing fields: {missing_fields}"
                            ),
                            unresolved_requirements=[f"missing_fields:{','.join(missing_fields)}"],
                            resource_metrics=self.budget.to_metrics(),
                        )
                else:
                    unresolved.append("schema_unverified")
            else:
                unresolved.append("adapter_contract_unassigned")

            # Redacted sample preview
            raw_samples = snapshot.card_data.get("sample_rows", [])
            if isinstance(raw_samples, list):
                for row in raw_samples[: self.budget.max_sample_rows]:
                    if isinstance(row, dict):
                        clean_row = RedactionUtility.redact_data(row)
                        sample_preview.append(clean_row)

            # Compute probe fingerprint if schema and revision are verified
            fingerprint: str | None = None
            if rev and verified_schema is not None:
                fingerprint = compute_probe_fingerprint(
                    provider=self.candidate.provider,
                    repository=self.candidate.repository,
                    immutable_revision=rev,
                    view_id=self.view_id,
                    schema_dict=verified_schema.to_canonical_dict(),
                    file_inventory=files,
                )

            # Determine final outcome
            outcome = ProbeOutcome.ACCESSIBLE
            if unresolved:
                outcome = ProbeOutcome.PARTIAL

            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                immutable_revision=rev,
                outcome=outcome,
                evidence_type=self.evidence_type,
                probe_fingerprint=fingerprint,
                observed_files_count=len(files),
                total_files_count_declared=total_declared,
                verified_schema=verified_schema,
                sample_preview=sample_preview,
                declared_license=snapshot.declared_license,
                is_gated=False,
                conversion_status=snapshot.conversion_status,
                resource_metrics=self.budget.to_metrics(),
                unresolved_requirements=unresolved,
            )

        except BudgetExhaustedError as e:
            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                outcome=ProbeOutcome.BUDGET_EXHAUSTED,
                evidence_type=self.evidence_type,
                reason=str(e),
                unresolved_requirements=["budget_exhausted"],
                resource_metrics=self.budget.to_metrics(),
            )
        except DeadlineExceededError as e:
            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                outcome=ProbeOutcome.TRANSIENT_ERROR,
                evidence_type=self.evidence_type,
                reason=f"Discovery deadline exceeded: {e}",
                unresolved_requirements=["timeout"],
                resource_metrics=self.budget.to_metrics(),
            )
        except HostNotAllowlistedError as e:
            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                outcome=ProbeOutcome.BLOCKED_BY_POLICY,
                evidence_type=self.evidence_type,
                reason=str(e),
                unresolved_requirements=["host_not_allowlisted"],
                resource_metrics=self.budget.to_metrics(),
            )
        except Exception as e:
            return ProbeEvidenceRecord(
                source_id=self.candidate.source_id,
                view_id=self.view_id,
                provider=self.candidate.provider,
                repository=self.candidate.repository,
                outcome=ProbeOutcome.TRANSIENT_ERROR,
                evidence_type=self.evidence_type,
                reason=f"Unexpected error during discovery probe: {e}",
                unresolved_requirements=["probing_error"],
                resource_metrics=self.budget.to_metrics(),
            )
