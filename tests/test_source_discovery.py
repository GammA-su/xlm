"""Tests for source discovery prober, metered transports, schema inspection, and redaction."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.sources.catalog import CandidateSourceEntry
from xlm.data.sources.prober import (
    EvidenceType,
    ProbeOutcome,
    SourceProber,
)
from xlm.data.sources.schema import (
    RedactionUtility,
    RowExtractorContract,
    arrow_schema_to_view_schema,
    compute_probe_fingerprint,
)
from xlm.data.sources.transport import (
    BudgetExhaustedError,
    DeadlineExceededError,
    HostNotAllowlistedError,
    InsecurePathError,
    LocalManifestTransport,
    MockStreamingTransport,
    SnapshotInfo,
    TransportBudget,
    is_allowlisted_host,
    validate_host,
)


def test_source_prober_missing_repo_outcome() -> None:
    """Verify prober returns inaccessible_or_not_found for 404 missing repositories."""
    candidate = CandidateSourceEntry(
        candidate_number=1,
        source_id="missing_source",
        provider="huggingface",
        repository="org/missing-repo",
    )
    snapshot = SnapshotInfo(
        provider="huggingface",
        target="org/missing-repo",
        immutable_revision="",
        is_not_found=True,
        error_reason="Repository 'org/missing-repo' not found (HTTP 404).",
    )
    transport = MockStreamingTransport(snapshot_info=snapshot)
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )

    evidence = prober.probe()
    assert evidence.outcome == ProbeOutcome.INACCESSIBLE_OR_NOT_FOUND
    assert "not found" in (evidence.reason or "").lower()
    assert "repository_not_found" in evidence.unresolved_requirements


def test_source_prober_gated_repo_outcome() -> None:
    """Verify prober returns gated and notes terms agreement required for 401/403 repos."""
    candidate = CandidateSourceEntry(
        candidate_number=2,
        source_id="gated_source",
        provider="huggingface",
        repository="org/gated-repo",
    )
    snapshot = SnapshotInfo(
        provider="huggingface",
        target="org/gated-repo",
        immutable_revision="",
        is_gated=True,
        error_reason="Access denied (403): repository is gated.",
    )
    transport = MockStreamingTransport(snapshot_info=snapshot)
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )

    evidence = prober.probe()
    assert evidence.outcome == ProbeOutcome.GATED
    assert evidence.is_gated is True
    assert "operator_terms_agreement_required" in evidence.unresolved_requirements


def test_source_prober_denied_fineweb_policy_block() -> None:
    """Verify prober immediately blocks denied direct FineWeb repositories."""
    candidate = CandidateSourceEntry(
        candidate_number=3,
        source_id="fineweb_bad",
        provider="huggingface",
        repository="HuggingFaceFW/fineweb",
    )
    transport = MockStreamingTransport(
        snapshot_info=SnapshotInfo(
            provider="huggingface", target="HuggingFaceFW/fineweb", immutable_revision="abc"
        )
    )
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )

    evidence = prober.probe()
    assert evidence.outcome == ProbeOutcome.BLOCKED_BY_POLICY
    assert "denied by xlm policy" in (evidence.reason or "").lower()
    assert "denied_by_policy" in evidence.unresolved_requirements


@pytest.fixture(scope="session")
def nested_parquet_samples(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Mapping[str, Path]]:
    """Session-only authored schemas; immutable files, never a checkout prerequisite."""
    records = {
        "nested": [
            {
                "doc_id": "authored-1",
                "text": "Authored schema fixture.",
                "metadata": {"taxonomy": "science", "quality_score": 0.75},
            }
        ],
        "mismatched": [{"doc_id": "authored-2", "body": "No text or QA fields."}],
    }
    key = hashlib.sha256(
        json.dumps({"generator": "nested-parquet-v1", "records": records}, sort_keys=True).encode()
    ).hexdigest()
    root = tmp_path_factory.mktemp(f"nested-{key[:12]}")
    paths = {}
    digests = {}
    for name, rows in records.items():
        path = root / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path, compression="NONE")
        paths[name] = path
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        path.chmod(stat.S_IREAD)
    yield MappingProxyType(paths)
    for name, path in paths.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digests[name]


def test_nested_field_schema_inspection(nested_parquet_samples: Mapping[str, Path]) -> None:
    """Verify Arrow schema inspection parses nested struct fields from real Parquet fixture."""
    parquet_path = nested_parquet_samples["nested"]
    assert parquet_path.is_file(), f"Fixture missing: {parquet_path}"

    pq_file = pq.ParquetFile(parquet_path)
    arrow_schema = pq_file.schema_arrow
    view_schema = arrow_schema_to_view_schema(
        schema=arrow_schema,
        view_id="nested_view",
        row_count_estimate=pq_file.metadata.num_rows,
    )

    assert view_schema.view_id == "nested_view"
    assert view_schema.is_nested is True
    assert "doc_id" in view_schema.fields
    assert "text" in view_schema.fields
    assert "metadata" in view_schema.fields

    meta_field = view_schema.fields["metadata"]
    assert "taxonomy" in meta_field.nested_fields
    assert "quality_score" in meta_field.nested_fields
    assert "string" in meta_field.nested_fields["taxonomy"].type_name.lower()


def test_missing_text_field_explicit_handling(nested_parquet_samples: Mapping[str, Path]) -> None:
    """Verify that schema mismatch flags missing text column against contract without guessing."""
    parquet_path = nested_parquet_samples["mismatched"]
    assert parquet_path.is_file(), f"Fixture missing: {parquet_path}"

    pq_file = pq.ParquetFile(parquet_path)
    arrow_schema = pq_file.schema_arrow
    view_schema = arrow_schema_to_view_schema(schema=arrow_schema, view_id="mismatched_view")

    # Adapter expecting a literal text column
    contract = RowExtractorContract(adapter_id="text_plain", text_field="text")
    is_valid, missing = contract.evaluate_against_schema(view_schema)

    assert is_valid is False
    assert missing == ["text"]

    # Adapter expecting structured fields
    structured_contract = RowExtractorContract(
        adapter_id="qa_structured",
        structured_renderer="qa_dialogue_v1",
        required_fields=["question", "answer"],
    )
    is_valid_struct, missing_struct = structured_contract.evaluate_against_schema(view_schema)
    assert is_valid_struct is False
    assert missing_struct == ["question", "answer"]


def test_secret_redaction_and_preview_bounding() -> None:
    """Verify that secrets, tokens, credentials, and control characters are scrubbed."""
    raw_sample = {
        "doc_id": "test_01",
        "token": "secret_token_value_12345",
        "api_key": "my-fake-api-key-9999",
        "text": (
            "Check token hf_abcdefghijklmnopqrstuvwxyz0123456789 and Bearer "
            "my-auth-bearer-token-123456."
        ),
        "remote_url": "https://user:mypassword@huggingface.co/api/datasets/test",
        "long_field": "x" * 300,
        "control_chars": "Hello\x00\x07World\t\n",
    }

    clean = RedactionUtility.redact_data(raw_sample, max_preview_len=200)

    assert clean["token"] == "[REDACTED]"
    assert clean["api_key"] == "[REDACTED]"
    assert "[REDACTED_HF_TOKEN]" in clean["text"]
    assert "Bearer [REDACTED]" in clean["text"]
    assert "https://***:***@huggingface.co" in clean["remote_url"]
    assert len(clean["long_field"]) <= 220
    assert "...[truncated]" in clean["long_field"]
    assert "\\x00\\x07" in clean["control_chars"]
    assert "\t\n" in clean["control_chars"]  # tabs and newlines preserved


def test_probe_fingerprint_stability_and_invalidation() -> None:
    """Verify fingerprint is stable across runs but invalidates on revision/schema changes."""
    base_schema = {"fields": {"text": "string", "id": "int64"}}
    base_files = [{"path": "data/train.parquet", "size": 1024}]

    fp1 = compute_probe_fingerprint(
        "huggingface", "org/repo", "sha_111", "view_a", base_schema, base_files
    )
    fp2 = compute_probe_fingerprint(
        "huggingface", "org/repo", "sha_111", "view_a", base_schema, base_files
    )
    assert fp1 == fp2, "Fingerprint must be deterministic and stable for identical inputs"

    # Revision change
    fp_rev = compute_probe_fingerprint(
        "huggingface", "org/repo", "sha_222", "view_a", base_schema, base_files
    )
    assert fp_rev != fp1

    # Schema change
    modified_schema = {"fields": {"text": "string", "id": "int64", "tag": "string"}}
    fp_schema = compute_probe_fingerprint(
        "huggingface", "org/repo", "sha_111", "view_a", modified_schema, base_files
    )
    assert fp_schema != fp1

    # File inventory change
    modified_files = [{"path": "data/train.parquet", "size": 2048}]
    fp_files = compute_probe_fingerprint(
        "huggingface", "org/repo", "sha_111", "view_a", base_schema, modified_files
    )
    assert fp_files != fp1


def test_transport_budget_limits_and_deadlines() -> None:
    """Verify shared TransportBudget enforces byte ceilings, request limits, and deadlines."""
    budget = TransportBudget(max_bytes=100, max_requests=2, deadline_seconds=10.0)

    # 1. Request counting
    budget.record_request()
    assert budget.requests_made == 1
    budget.record_request()
    assert budget.requests_made == 2

    with pytest.raises(BudgetExhaustedError, match="maximum discovery request allowance"):
        budget.record_request()

    # 2. Byte consumption
    byte_budget = TransportBudget(max_bytes=100, max_requests=10)
    byte_budget.record_bytes(60)
    assert byte_budget.bytes_transferred == 60

    with pytest.raises(BudgetExhaustedError, match="discovery transferred bytes limit"):
        byte_budget.record_bytes(50)  # 60 + 50 = 110 > 100

    # 3. Deadline exceeded
    deadline_budget = TransportBudget(deadline_seconds=0.01)
    import time

    time.sleep(0.02)
    with pytest.raises(DeadlineExceededError, match="total deadline"):
        deadline_budget.check_deadline()


def test_security_host_allowlist_and_local_traversal() -> None:
    """Verify host allowlist security and local path traversal prevention."""
    # Allowlisted hosts
    validate_host("https://huggingface.co/api/datasets/test")
    validate_host("https://raw.githubusercontent.com/org/repo/main/manifest.yaml")

    # Unauthorized host raises HostNotAllowlistedError
    with pytest.raises(HostNotAllowlistedError, match="not in the allowlist"):
        validate_host("https://malicious-site.example.com/exploit.json")

    # Local transport path traversal rejection
    root_dir = Path("fixtures/sources")
    local_transport = LocalManifestTransport(allowed_roots=[root_dir])

    with pytest.raises(InsecurePathError, match="escapes allowed root"):
        local_transport._validate_path(Path("../../../Windows/System32/cmd.exe"))


def test_ambiguous_provider_error_does_not_infer_nonexistence() -> None:
    """Verify provider 500/503 errors produce TRANSIENT_ERROR, not INACCESSIBLE_OR_NOT_FOUND."""
    candidate = CandidateSourceEntry(
        candidate_number=5,
        source_id="ambiguous_source",
        provider="huggingface",
        repository="org/repo-500",
    )
    snapshot = SnapshotInfo(
        provider="huggingface",
        target="org/repo-500",
        immutable_revision="",
        error_reason="Provider returned HTTP error 500: Internal Server Error",
    )
    transport = MockStreamingTransport(snapshot_info=snapshot)
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )
    evidence = prober.probe()

    # Must NOT infer 404/not_found from a 500 error!
    assert evidence.outcome != ProbeOutcome.INACCESSIBLE_OR_NOT_FOUND
    assert evidence.outcome in (ProbeOutcome.REVISION_MISSING, ProbeOutcome.TRANSIENT_ERROR)


def test_malicious_redirect_blocked_by_safe_redirect_handler() -> None:
    """Verify that redirects to un-allowlisted hosts are intercepted and blocked."""
    candidate = CandidateSourceEntry(
        candidate_number=6,
        source_id="redirect_source",
        provider="huggingface",
        repository="org/redirect-repo",
    )
    # Mock transport configured with a malicious redirect target
    transport = MockStreamingTransport(
        snapshot_info=SnapshotInfo(
            provider="huggingface", target="org/redirect-repo", immutable_revision="sha"
        ),
        redirect_target="https://attacker-controlled.site/steal",
    )
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )
    evidence = prober.probe()

    assert evidence.outcome == ProbeOutcome.BLOCKED_BY_POLICY
    assert "not in the allowlist" in (evidence.reason or "").lower()


# ----------------- Hugging Face storage/CDN redirect compatibility (offline)


def test_huggingface_cdn_endpoints_accepted() -> None:
    """Authored offline check: official HF storage/CDN hosts validate.

    Covers the operator-hit redirect target (us.aws.cdn.hf.co) and the other
    documented endpoint forms. No network is performed; only the host
    predicate is exercised.
    """
    for host in [
        "huggingface.co",
        "hf.co",
        "cdn-lfs.huggingface.co",
        "cas-server.xethub.hf.co",
        "cas-server.xethub-eu.hf.co",
        "transfer.xethub.hf.co",
        "transfer.xethub-eu.hf.co",
        "us.aws.cdn.hf.co",
        "us.gcp.cdn.hf.co",
        "cdn-lfs-us-1.hf.co",
        "cdn-lfs-eu-1.hf.co",
    ]:
        assert is_allowlisted_host(host) is True
        validate_host(f"https://{host}/xet-bridge-us/abc123/synth_001.parquet")


def test_huggingface_cdn_redirect_accepted_through_prober() -> None:
    """A redirect onto HF CDN infrastructure is not a policy block."""
    candidate = CandidateSourceEntry(
        candidate_number=8,
        source_id="cdn_source",
        provider="huggingface",
        repository="org/cdn-repo",
    )
    transport = MockStreamingTransport(
        snapshot_info=SnapshotInfo(
            provider="huggingface", target="org/cdn-repo", immutable_revision="sha"
        ),
        redirect_target="https://us.aws.cdn.hf.co/xet-bridge-us/abc123/synth_001.parquet",
    )
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )
    evidence = prober.probe()
    assert evidence.outcome != ProbeOutcome.BLOCKED_BY_POLICY


@pytest.mark.parametrize(
    "host",
    [
        "hf.co.evil.example",
        "evilhf.co",
        "huggingface.co.attacker.example",
        "us.aws.cdn.hf.co.evil.example",
        "attacker-controlled.site",
        "10.0.0.1",
    ],
)
def test_lookalike_and_non_hf_hosts_refused(host: str) -> None:
    """Lookalikes, unrelated hosts, and raw IPs never match the HF suffix rule."""
    assert is_allowlisted_host(host) is False
    with pytest.raises(HostNotAllowlistedError, match="not in the allowlist"):
        validate_host(f"https://{host}/payload")


def test_cdn_security_boundaries_intact() -> None:
    """HTTPS/credential/private-network rules still apply on HF-suffixed hosts."""
    # Cleartext to a CDN host is refused (loopback-only HTTP rule).
    with pytest.raises(HostNotAllowlistedError, match="cleartext HTTP"):
        validate_host("http://us.aws.cdn.hf.co/xet-bridge-us/abc123/file.parquet")
    # Embedded credentials are refused even on a trusted host.
    with pytest.raises(HostNotAllowlistedError, match="without credentials"):
        validate_host("https://operator:secret@us.aws.cdn.hf.co/file.parquet")
    # Non-HTTP(S) schemes are refused even on a trusted host.
    with pytest.raises(HostNotAllowlistedError, match="only explicit HTTP"):
        validate_host("file://us.aws.cdn.hf.co/file.parquet")


def test_prober_budget_exhaustion_outcome() -> None:
    """Verify prober returns BUDGET_EXHAUSTED when budget bytes ceiling is breached."""
    candidate = CandidateSourceEntry(
        candidate_number=7,
        source_id="heavy_source",
        provider="huggingface",
        repository="org/heavy-repo",
    )
    snapshot = SnapshotInfo(
        provider="huggingface", target="org/heavy-repo", immutable_revision="sha123"
    )
    # Budget with only 1 request allowance: get_snapshot_info takes 1, list_files will breach it
    exhausted_budget = TransportBudget(max_requests=1)
    transport = MockStreamingTransport(
        snapshot_info=snapshot,
        budget=exhausted_budget,
    )

    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=exhausted_budget,
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
    )
    evidence = prober.probe()

    assert evidence.outcome == ProbeOutcome.BUDGET_EXHAUSTED
    assert "budget_exhausted" in evidence.unresolved_requirements


def test_partial_inventory_and_unresolved_requirements() -> None:
    """Verify prober returns PARTIAL when files exist but adapter schema is unassigned."""
    candidate = CandidateSourceEntry(
        candidate_number=8,
        source_id="partial_source",
        provider="huggingface",
        repository="org/partial-repo",
    )
    snapshot = SnapshotInfo(
        provider="huggingface",
        target="org/partial-repo",
        immutable_revision="sha_part",
    )
    files = [{"path": "data/train-00000.parquet", "size": 1048576}]
    transport = MockStreamingTransport(snapshot_info=snapshot, files=files)
    prober = SourceProber(
        candidate=candidate,
        transport=transport,
        budget=TransportBudget(),
        evidence_type=EvidenceType.SYNTHETIC_FIXTURE,
        extractor_contract=None,  # No extractor assigned yet
    )
    evidence = prober.probe()

    assert evidence.outcome == ProbeOutcome.PARTIAL
    assert "adapter_contract_unassigned" in evidence.unresolved_requirements
    assert evidence.observed_files_count == 1
