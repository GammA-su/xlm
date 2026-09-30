"""Essential-Web C04 admission bootstrap: bounded schema probe and review binding.

The shared production probe needs about 330 physical requests, so it is a
production plan and the fetch gate requires prior admission. Admission in
turn needs accessible, schema-verified probe evidence. This module supplies
that evidence with a small schema and source-identity probe: the dataset
card at the pinned revision and one Parquet footer of one frozen inventory
file. It decodes no row, never reads the text column and measures no yield
or cost; those stay with the production probe that runs after admission.

Expected values in the probe plan come from retained Phase-P footer bytes and
are planning evidence only. The live probe observes every value again and
refuses on any difference.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from xlm.data.acquisition.plan import PILOT_MAX_REQUESTS
from xlm.data.acquisition.projection import (
    ProjectionRefusal,
    parquet_field_leaves,
    resolve_projection,
)
from xlm.data.acquisition.sampling import canonical_range_url
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v2 import frozen as evidence_frozen
from xlm.data.evidence_v2.footer import FooterError, LiveFooterTransport, RangeEvidence
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import AdmissionDecision, essential_contamination_mitigation
from xlm.data.sources.policy import (
    BenchmarkContaminationRisk,
    LicenseReviewStatus,
    LicenseUsagePolicy,
)
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
from xlm.data.sources.schema import (
    RowExtractorContract,
    ViewSchema,
    arrow_schema_to_view_schema,
    compute_probe_fingerprint,
)
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    DeadlineExceededError,
    HostNotAllowlistedError,
    TransportBudget,
    validate_host,
)

PLAN_KIND = "essential_web_schema_probe_plan"
RECEIPT_KIND = "essential_web_schema_probe_receipt"
PROBE_PRODUCER = "xlm.data.sources.essential_web_bootstrap/1"
PROBE_ROLE = "c04_admission_bootstrap_schema_and_source_identity"
SOURCE_ID = "essential_web"
PROVIDER = "huggingface"
ADAPTER_ID = "essential_web_bnormal"
EXPECTED_LICENSE = "odc-by"
CARD_FILE = "README.md"
SCHEMA_VIEW = "essential_web_physical"
MIN_GROUP_ROWS = ready.ROWS_PER_CRAWL
MAX_PHYSICAL_REQUESTS = 24
MAX_RESPONSE_BODY_BYTES = 8 * ready.MIB
CARD_BYTES_MAX = ready.MIB
FOOTER_BYTES_MAX = 4 * ready.MIB - 8
FRONT_MATTER_BYTES_MAX = 65536
THRIFT_LIMIT_BYTES = 32 * ready.MIB
PER_REQUEST_TIMEOUT_SECONDS = 15.0
OVERALL_DEADLINE_SECONDS = 120.0
#: Card, magic, trailer and footer, each with one redirect.
NOMINAL_PHYSICAL_REQUESTS = 8
BENCHMARKS = ("BLiMP", "ARC-Easy", "HellaSwag", "PIQA")
REVIEW_FILES = {
    "source_rights": "source-rights-review.json",
    "attribution": "attribution-plan.json",
    "benchmark_risk": "benchmark-risk-review.json",
    "external_evidence": "external-source-evidence.json",
}
SOURCE_REVIEW_SECTIONS = (
    "source_identity",
    "dataset_license_metadata",
    "attribution_obligations",
    "underlying_content_caveat",
    "provenance",
    "privacy_and_third_party_risk",
    "intended_use",
    "mitigation",
)


class SchemaProbeRefusal(ValueError):
    """The probe refuses; ``outcome`` is what an honest evidence record would say."""

    def __init__(self, message: str, outcome: ProbeOutcome = ProbeOutcome.SCHEMA_MISMATCH) -> None:
        super().__init__(message)
        self.outcome = outcome


class ReviewRefusal(ValueError):
    """An admission review is missing, unbound or overclaims."""


@dataclass(frozen=True)
class CardEvidence:
    """The dataset card as served for the pinned revision."""

    body: bytes
    repo_commit: str | None = None
    etag: str | None = None


class SchemaProbeTransport(Protocol):
    def fetch_card(self, limit: int) -> CardEvidence: ...

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence: ...


class LiveSchemaProbeTransport:
    """Pinned, host-allowlisted transport for the card and three footer ranges."""

    def __init__(
        self,
        budget: TransportBudget,
        *,
        timeout_seconds: float = PER_REQUEST_TIMEOUT_SECONDS,
        opener_factory: Any = None,
    ) -> None:
        if (evidence_frozen.REPOSITORY, evidence_frozen.REVISION) != (
            ready.REPOSITORY,
            selector.SOURCE_REVISION,
        ):
            raise SchemaProbeRefusal(
                "range transport is not pinned to the production source",
                ProbeOutcome.BLOCKED_BY_POLICY,
            )
        self.budget = budget
        self.timeout_seconds = timeout_seconds
        self._ranges = LiveFooterTransport(
            budget, timeout_seconds=timeout_seconds, opener_factory=opener_factory
        )

    def fetch_card(self, limit: int) -> CardEvidence:
        url = canonical_range_url(PROVIDER, ready.REPOSITORY, selector.SOURCE_REVISION, CARD_FILE)
        validate_host(url)
        self._ranges.handler.reset_chain()
        self.budget.record_request()
        request = urllib.request.Request(
            url, headers={"User-Agent": "xlm-acquisition/2", "Accept-Encoding": "identity"}
        )
        with self._ranges.opener.open(request, timeout=self.timeout_seconds) as response:
            if getattr(response, "status", None) != 200:
                raise SchemaProbeRefusal(
                    "dataset card was not served with status 200",
                    ProbeOutcome.INACCESSIBLE_OR_NOT_FOUND,
                )
            headers = response.headers or {}
            body = self.budget.read_body(response, limit)
            return CardEvidence(body, headers.get("X-Repo-Commit"), headers.get("ETag"))

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        return self._ranges.fetch_range(source_file, start, end)


def probe_budget() -> TransportBudget:
    return TransportBudget(
        max_bytes=MAX_RESPONSE_BODY_BYTES,
        max_requests=MAX_PHYSICAL_REQUESTS,
        deadline_seconds=OVERALL_DEADLINE_SECONDS,
        per_request_timeout=PER_REQUEST_TIMEOUT_SECONDS,
    )


def adapter_contract(view: str) -> RowExtractorContract:
    from xlm.data.adapters.mix01_adapters import EssentialWebSelectedAdapter

    return EssentialWebSelectedAdapter(view).contract()


def card_license(card: bytes) -> str:
    """License declared in the card's YAML front matter; the card is untrusted data."""
    try:
        text = card.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise SchemaProbeRefusal("dataset card is not UTF-8") from exc
    end = text.find("\n---", 3)
    if not text.startswith("---\n") or end < 0 or end > FRONT_MATTER_BYTES_MAX:
        raise SchemaProbeRefusal("dataset card has no bounded YAML front matter")
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise SchemaProbeRefusal("dataset card front matter is not valid YAML") from exc
    value = meta.get("license") if isinstance(meta, dict) else None
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, str) or not value.strip():
        raise SchemaProbeRefusal("dataset card declares no single license")
    return value.strip().lower()


def footer_facts(payload: bytes) -> dict[str, Any]:
    """Schema and first-row-group facts from a footer plus its 8-byte trailer."""
    if (
        len(payload) < 12
        or payload[-4:] != b"PAR1"
        or int.from_bytes(payload[-8:-4], "little") != len(payload) - 8
    ):
        raise SchemaProbeRefusal("footer payload is not a Parquet footer with its trailer")
    try:
        parquet = pq.ParquetFile(
            pa.BufferReader(b"PAR1" + payload),
            thrift_string_size_limit=THRIFT_LIMIT_BYTES,
            thrift_container_size_limit=THRIFT_LIMIT_BYTES,
        )
        schema = arrow_schema_to_view_schema(parquet.schema_arrow, SCHEMA_VIEW)
        mapping = parquet_field_leaves(parquet)
        metadata = parquet.metadata
        if metadata.num_row_groups < 1:
            raise SchemaProbeRefusal("file has no row group")
        group = metadata.row_group(0)
        codecs = sorted({str(group.column(i).compression) for i in range(group.num_columns)})
    except SchemaProbeRefusal:
        raise
    except Exception as exc:
        raise SchemaProbeRefusal(f"cannot read Parquet footer: {type(exc).__name__}") from exc
    return {
        "schema": schema,
        "schema_digest": canonical.digest(schema.to_canonical_dict()),
        "field_leaves": mapping,
        "top_level_fields": list(schema.fields),
        "physical_leaves": len(parquet.schema),
        "file_rows": int(metadata.num_rows),
        "row_groups": int(metadata.num_row_groups),
        "group0_rows": int(group.num_rows),
        "group0_codecs": codecs,
        "created_by": str(metadata.created_by),
    }


def check_schema(facts: Mapping[str, Any], expected_digest: str) -> int:
    """Refuse a schema the production adapters cannot use; return projected leaf count."""
    schema: ViewSchema = facts["schema"]
    missing: list[str] = []
    for view in selector.ADMITTED_COMPONENTS:
        ok, absent = adapter_contract(view).evaluate_against_schema(schema)
        if not ok:
            missing = sorted(set(missing) | set(absent))
    if missing:
        raise SchemaProbeRefusal(f"schema lacks adapter fields: {missing}")
    if schema.fields["text"].type_name not in ("string", "large_string"):
        raise SchemaProbeRefusal("text field is not a string column")
    for name in ("eai_taxonomy", "quality_signals"):
        if not schema.fields[name].type_name.startswith("struct<"):
            raise SchemaProbeRefusal(f"{name} is not a struct column")
    if facts["schema_digest"] != expected_digest:
        raise SchemaProbeRefusal("schema differs from the schema the selector was frozen on")
    leaves = 0
    for view in selector.ADMITTED_COMPONENTS:
        try:
            resolved = resolve_projection(facts["field_leaves"], columns_for(ADAPTER_ID, view))
        except ProjectionRefusal as exc:
            raise SchemaProbeRefusal(f"production projection is not decodable: {exc}") from exc
        leaves = len(resolved.leaf_indices)
    if facts["group0_rows"] < MIN_GROUP_ROWS:
        raise SchemaProbeRefusal("first row group is shorter than the frozen calibration window")
    return leaves


def _with_digest(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("digest", None)
    body["digest"] = canonical.digest(body)
    return body


def build_probe_plan(
    window: Mapping[str, Any], footer_payload: bytes, calibration_digest: str
) -> dict[str, Any]:
    """Freeze the probe against one calibration file and its retained real footer."""
    if hashlib.sha256(footer_payload).hexdigest() != window["footer_sha256"]:
        raise SchemaProbeRefusal("retained footer differs from the frozen calibration footer")
    facts = footer_facts(footer_payload)
    leaves = check_schema(facts, facts["schema_digest"])
    length = int(window["remote_length"])
    footer_start = length - len(footer_payload)
    return _with_digest(
        {
            "kind": PLAN_KIND,
            "version": 1,
            "role": PROBE_ROLE,
            "scope": "schema and source identity only; no row decode, no text, no yield or cost",
            "binding": ready.source_binding(),
            "views": list(selector.ADMITTED_COMPONENTS),
            "file": {
                "file": window["file"],
                "remote_length": length,
                "strong_etag": window["strong_etag"],
                "footer_and_trailer_bytes": len(footer_payload),
                "footer_and_trailer_sha256": window["footer_sha256"],
            },
            "expected": {
                "license": EXPECTED_LICENSE,
                "schema_digest": facts["schema_digest"],
                "top_level_fields": facts["top_level_fields"],
                "physical_leaves": facts["physical_leaves"],
                "projected_physical_leaves": leaves,
                "min_group0_rows": MIN_GROUP_ROWS,
            },
            "planning_evidence": {
                "calibration_digest": calibration_digest,
                "footer": "retained v4.1 Phase-P M footer payload; real bytes, hash-checked",
                "status": "planning only; every value is observed again by the live probe",
            },
            "schedule": [
                {"op": "card", "file": CARD_FILE, "bytes_max": CARD_BYTES_MAX},
                {"op": "magic", "range": [0, 3]},
                {"op": "trailer", "range": [length - 8, length - 1]},
                {"op": "footer", "range": [footer_start, length - 9]},
            ],
            "limits": {
                "max_physical_requests": MAX_PHYSICAL_REQUESTS,
                "max_response_body_bytes": MAX_RESPONSE_BODY_BYTES,
                "card_bytes_max": CARD_BYTES_MAX,
                "footer_bytes_max": FOOTER_BYTES_MAX,
                "per_request_timeout_seconds": PER_REQUEST_TIMEOUT_SECONDS,
                "overall_deadline_seconds": OVERALL_DEADLINE_SECONDS,
                "workers": 1,
            },
            "nominal_physical_requests": NOMINAL_PHYSICAL_REQUESTS,
            "pilot_request_ceiling": PILOT_MAX_REQUESTS,
            "live_run": False,
        }
    )


def check_probe_plan(plan: Mapping[str, Any]) -> None:
    """Refuse a plan that is altered, unbound or wider than the frozen limits."""
    body = dict(plan)
    if body.pop("digest", None) != canonical.digest(body):
        raise SchemaProbeRefusal("probe plan digest mismatch", ProbeOutcome.BLOCKED_BY_POLICY)
    if plan.get("kind") != PLAN_KIND or plan.get("role") != PROBE_ROLE:
        raise SchemaProbeRefusal("not a schema probe plan", ProbeOutcome.BLOCKED_BY_POLICY)
    try:
        ready.check_binding(plan["binding"])
    except ValueError as exc:
        raise SchemaProbeRefusal(str(exc), ProbeOutcome.BLOCKED_BY_POLICY) from exc
    if list(plan["views"]) != list(selector.ADMITTED_COMPONENTS):
        raise SchemaProbeRefusal("plan views differ", ProbeOutcome.BLOCKED_BY_POLICY)
    limits = plan["limits"]
    if (
        limits["max_physical_requests"] != MAX_PHYSICAL_REQUESTS
        or limits["max_physical_requests"] > PILOT_MAX_REQUESTS
        or limits["max_response_body_bytes"] != MAX_RESPONSE_BODY_BYTES
        or plan["expected"]["license"] != EXPECTED_LICENSE
    ):
        raise SchemaProbeRefusal("plan limits differ", ProbeOutcome.BLOCKED_BY_POLICY)


def _range(transport: SchemaProbeTransport, file: Mapping[str, Any], start: int, end: int) -> bytes:
    evidence = transport.fetch_range(file["file"], start, end)
    if evidence.total_length != file["remote_length"]:
        raise SchemaProbeRefusal("remote file length differs from the frozen inventory file")
    if evidence.etag != file["strong_etag"]:
        raise SchemaProbeRefusal("remote ETag differs from the frozen inventory file")
    if len(evidence.body) != end - start + 1:
        raise SchemaProbeRefusal("short range body")
    return evidence.body


def run_schema_probe(
    plan: Mapping[str, Any],
    transport: SchemaProbeTransport,
    *,
    evidence_type: EvidenceType = EvidenceType.SYNTHETIC_FIXTURE,
    resource_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the four-operation probe; return a receipt and one evidence record per view."""
    check_probe_plan(plan)
    file, expected = plan["file"], plan["expected"]
    try:
        card = transport.fetch_card(plan["limits"]["card_bytes_max"])
        if card.repo_commit is not None and card.repo_commit != selector.SOURCE_REVISION:
            raise SchemaProbeRefusal(
                "provider served the card from a different commit", ProbeOutcome.REVISION_MISSING
            )
        license_tag = card_license(card.body)
        if license_tag != expected["license"]:
            raise SchemaProbeRefusal("declared license differs from the reviewed license")
        length = int(file["remote_length"])
        if _range(transport, file, 0, 3) != b"PAR1":
            raise SchemaProbeRefusal("file lacks the Parquet header")
        trailer = _range(transport, file, length - 8, length - 1)
        footer_bytes = int.from_bytes(trailer[:4], "little")
        if trailer[4:] != b"PAR1" or not 0 < footer_bytes <= min(FOOTER_BYTES_MAX, length - 12):
            raise SchemaProbeRefusal("file lacks a bounded Parquet footer")
        payload = _range(transport, file, length - 8 - footer_bytes, length - 9) + trailer
    except SchemaProbeRefusal:
        raise
    except BudgetExhaustedError as exc:
        raise SchemaProbeRefusal(str(exc), ProbeOutcome.BUDGET_EXHAUSTED) from exc
    except HostNotAllowlistedError as exc:
        raise SchemaProbeRefusal(str(exc), ProbeOutcome.BLOCKED_BY_POLICY) from exc
    except urllib.error.HTTPError as exc:
        outcome = (
            ProbeOutcome.GATED
            if exc.code in (401, 403)
            else ProbeOutcome.INACCESSIBLE_OR_NOT_FOUND
            if exc.code == 404
            else ProbeOutcome.TRANSIENT_ERROR
        )
        raise SchemaProbeRefusal(f"provider returned HTTP {exc.code}", outcome) from exc
    except (FooterError, DeadlineExceededError, OSError) as exc:
        raise SchemaProbeRefusal(
            f"transport failed: {type(exc).__name__}: {exc}", ProbeOutcome.TRANSIENT_ERROR
        ) from exc
    if hashlib.sha256(payload).hexdigest() != file["footer_and_trailer_sha256"]:
        raise SchemaProbeRefusal("footer bytes differ from the frozen calibration footer")
    facts = footer_facts(payload)
    leaves = check_schema(facts, expected["schema_digest"])
    inventory = [{"path": file["file"], "size": length}]
    observed = {
        "repository": ready.REPOSITORY,
        "revision": selector.SOURCE_REVISION,
        "card": {
            "file": CARD_FILE,
            "bytes": len(card.body),
            "sha256": hashlib.sha256(card.body).hexdigest(),
            "repo_commit_header": card.repo_commit,
            "etag": card.etag,
            "declared_license": license_tag,
        },
        "file": {
            "file": file["file"],
            "remote_length": length,
            "strong_etag": file["strong_etag"],
            "footer_and_trailer_bytes": len(payload),
            "footer_and_trailer_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "schema_digest": facts["schema_digest"],
        "top_level_fields": facts["top_level_fields"],
        "physical_leaves": facts["physical_leaves"],
        "projected_physical_leaves": leaves,
        "file_rows": facts["file_rows"],
        "row_groups": facts["row_groups"],
        "group0_rows": facts["group0_rows"],
        "group0_codecs": facts["group0_codecs"],
        "created_by": facts["created_by"],
    }
    receipt = _with_digest(
        {
            "kind": RECEIPT_KIND,
            "producer": PROBE_PRODUCER,
            "role": PROBE_ROLE,
            "status": "ACCESSIBLE",
            "evidence_type": evidence_type.value,
            "plan_digest": plan["digest"],
            "observed": observed,
            "rows_decoded": 0,
            "text_bytes_read": 0,
            "not_established": [
                "row decode through the production reader",
                "selector yield, retained bytes or tokens",
                "transfer cost per input row",
            ],
        }
    )
    records: dict[str, ProbeEvidenceRecord] = {}
    for view in plan["views"]:
        schema = facts["schema"].model_copy(
            update={
                "view_id": view,
                "row_count_estimate": facts["file_rows"],
                "byte_size_estimate": length,
            }
        )
        records[view] = ProbeEvidenceRecord(
            source_id=SOURCE_ID,
            view_id=view,
            provider=PROVIDER,
            repository=ready.REPOSITORY,
            immutable_revision=selector.SOURCE_REVISION,
            outcome=ProbeOutcome.ACCESSIBLE,
            evidence_type=evidence_type,
            probe_fingerprint=compute_probe_fingerprint(
                provider=PROVIDER,
                repository=ready.REPOSITORY,
                immutable_revision=selector.SOURCE_REVISION,
                view_id=view,
                schema_dict=schema.to_canonical_dict(),
                file_inventory=inventory,
            ),
            observed_files_count=1,
            verified_schema=schema,
            declared_license=license_tag,
            resource_metrics={
                **dict(resource_metrics or {}),
                "producer": PROBE_PRODUCER,
                "role": PROBE_ROLE,
                "plan_digest": plan["digest"],
                "receipt_digest": receipt["digest"],
                "rows_decoded": 0,
            },
            reason="bounded schema and source-identity probe of one frozen inventory file; "
            "no row decoded; not production yield or cost evidence",
        )
    return {"receipt": receipt, "records": records}


def check_reviews(reviews: Mapping[str, Mapping[str, Any]]) -> None:
    """Refuse an admission review package that is unbound, incomplete or overclaims."""
    if set(reviews) != set(REVIEW_FILES):
        raise ReviewRefusal(f"review package must hold exactly {sorted(REVIEW_FILES)}")
    for name, review in reviews.items():
        binding = review.get("binding", {})
        if (
            binding.get("repository") != ready.REPOSITORY
            or binding.get("revision") != selector.SOURCE_REVISION
        ):
            raise ReviewRefusal(f"{name} review is not bound to the pinned source revision")
        if review.get("legal_advice") is not False:
            raise ReviewRefusal(f"{name} review must state that it is not legal advice")
    source = reviews["source_rights"]
    absent = [key for key in SOURCE_REVIEW_SECTIONS if not source.get(key)]
    if absent:
        raise ReviewRefusal(f"source review lacks sections: {absent}")
    if source["dataset_license_metadata"].get("license") != EXPECTED_LICENSE:
        raise ReviewRefusal("source review license differs from the probed license")
    claims = source.get("claims", {})
    if (
        claims.get("every_source_document_is_odc_by") is not False
        or claims.get("underlying_content_rights_cleared") is not False
    ):
        raise ReviewRefusal("source review must not claim cleared underlying-content rights")
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
    ):
        raise ReviewRefusal("benchmark review must not claim an uncontaminated corpus")
    if (
        risk.get("gate_value") != BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION
        or not risk.get("mitigation")
        or risk.get("mitigation_binding")
        != essential_contamination_mitigation().model_dump(mode="json")
    ):
        raise ReviewRefusal("benchmark review lacks the gate value or its mitigation")
    if not reviews["external_evidence"].get("sources"):
        raise ReviewRefusal("external evidence manifest lists no source")


def decision_notes(review_sha256: Mapping[str, str], operator: str) -> str:
    """Operator notes that bind a decision to the exact review files."""
    if set(review_sha256) != set(REVIEW_FILES) or not operator.strip():
        raise ReviewRefusal("decision needs every review hash and a named operator")
    hashes = " ".join(f"{name}={review_sha256[name]}" for name in sorted(review_sha256))
    return (
        f"essential-web admission review v2; operator={operator.strip()}; "
        f"review sha256 {hashes}; "
        "license_review approves research pretraining use of the ODC-By database only and "
        "clears no underlying page rights; benchmark_risk=suspect_with_mitigation records "
        "possible contamination, not zero contamination; a C05 exclusion receipt over the "
        "frozen pool is required before any uncontaminated benchmark claim"
    )


def build_decision(
    evidence: ProbeEvidenceRecord, review_sha256: Mapping[str, str], operator: str
) -> AdmissionDecision:
    """Approved production decision for one view, bound to evidence, selector and reviews."""
    return AdmissionDecision(
        contract_version="c04-benchmark-risk-v2",
        source_id=evidence.source_id,
        view_id=evidence.view_id,
        provider=evidence.provider,
        repository=evidence.repository,
        immutable_revision=evidence.immutable_revision or "",
        adapter_id=ADAPTER_ID,
        selector_binding=selector.selector_identity(),
        probe_fingerprint=evidence.probe_fingerprint or "",
        license_review=LicenseReviewStatus.APPROVED,
        provenance_review="approved",
        benchmark_risk=BenchmarkContaminationRisk.SUSPECT_WITH_MITIGATION,
        reviews_sha256=dict(review_sha256),
        contamination_mitigation=essential_contamination_mitigation(),
        resource_contract="C04/C13; exact limits and matching authorization required",
        usage_policy=LicenseUsagePolicy.STRICT_RESEARCH,
        operator_approved=True,
        operator_notes=decision_notes(review_sha256, operator),
    )
