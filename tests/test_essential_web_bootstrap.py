"""Authored offline tests of the Essential-Web admission bootstrap. No network.

Parquet images and cards are authored fixtures. Records built from them prove
gate and probe logic, not live dataset compatibility.
"""

from __future__ import annotations

import copy
import importlib
import io
import json
import socket
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from xlm.artifacts.store import ArtifactConflictError, ArtifactStore
from xlm.cli.data_cmd import app
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import (
    PILOT_MAX_REQUESTS,
    AuthorizationRequiredError,
    PlanAuthorization,
    load_acquisition_plan,
    plan_requires_production_admission,
    validate_plan_authorization,
)
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v2.footer import RangeEvidence
from xlm.data.sources import essential_web_bootstrap as boot
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    attempt_artifact_id,
    latest_attempt,
    load_admission_decision,
    load_probe_evidence,
    next_attempt,
    resolve_verified_production_admission,
    save_admission_decision,
    save_probe_evidence,
)
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP"
READINESS = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS"
FILE = "data/crawl=AUTHORED/train-00000-of-00001.parquet"
ETAG = '"authored-etag"'
CARD = b"---\nlicense: odc-by\n---\n# authored card\n"
HASHES = {name: "0" * 64 for name in boot.REVIEW_FILES}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline test attempted network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    return importlib.import_module("essential_web_bootstrap")


def image(drop: str | None = None, extra: bool = False, rows: int = 2048) -> bytes:
    """Authored Parquet file with the production top-level fields."""
    columns: dict[str, Any] = {
        "id": pa.array(range(rows), pa.int64()),
        "text": pa.array(["authored"] * rows, pa.string()),
        "metadata": pa.array([{"url": "authored"}] * rows),
        "quality_signals": pa.array([{"fasttext": {"english": 0.9}}] * rows),
        "eai_taxonomy": pa.array([{"document_type_v2": {"primary": {"label": "x"}}}] * rows),
        "pid": pa.array(["authored"] * rows, pa.string()),
    }
    if drop:
        del columns[drop]
    if extra:
        columns["extra"] = pa.array([1] * rows, pa.int32())
    sink = io.BytesIO()
    pq.write_table(pa.table(columns), sink)
    return sink.getvalue()


def footer_of(content: bytes) -> bytes:
    return content[len(content) - 8 - int.from_bytes(content[-8:-4], "little") :]


def window(content: bytes) -> dict[str, Any]:
    import hashlib

    return {
        "file": FILE,
        "remote_length": len(content),
        "strong_etag": ETAG,
        "footer_sha256": hashlib.sha256(footer_of(content)).hexdigest(),
    }


def plan_for(content: bytes) -> dict[str, Any]:
    return boot.build_probe_plan(window(content), footer_of(content), "authored")


def redigest(plan: dict[str, Any]) -> dict[str, Any]:
    plan.pop("digest", None)
    plan["digest"] = canonical.digest(plan)
    return plan


def retarget(plan: dict[str, Any], content: bytes) -> dict[str, Any]:
    """Point the plan's file identity at another image, keeping the expected schema."""
    changed = copy.deepcopy(plan)
    target = window(content)
    changed["file"].update(
        remote_length=target["remote_length"],
        footer_and_trailer_bytes=len(footer_of(content)),
        footer_and_trailer_sha256=target["footer_sha256"],
    )
    return redigest(changed)


class FakeTransport:
    def __init__(
        self,
        content: bytes,
        card: bytes = CARD,
        commit: str | None = selector.SOURCE_REVISION,
        etag: str = ETAG,
    ) -> None:
        self.content, self.card, self.commit, self.etag = content, card, commit, etag
        self.ranges: list[tuple[int, int]] = []
        self.cards = 0

    def fetch_card(self, limit: int) -> boot.CardEvidence:
        self.cards += 1
        return boot.CardEvidence(self.card[:limit], self.commit, '"card"')

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        assert source_file == FILE
        self.ranges.append((start, end))
        return RangeEvidence(self.content[start : end + 1], len(self.content), self.etag)


def test_probe_reads_card_and_footer_only() -> None:
    content = image()
    transport = FakeTransport(content)
    result = boot.run_schema_probe(plan_for(content), transport)
    footer_start = len(content) - len(footer_of(content))
    assert transport.cards == 1 and len(transport.ranges) == 3
    assert all(end <= 3 or start >= footer_start for start, end in transport.ranges)
    receipt = result["receipt"]
    assert receipt["rows_decoded"] == 0 and receipt["text_bytes_read"] == 0
    assert receipt["producer"] == boot.PROBE_PRODUCER and receipt["role"] == boot.PROBE_ROLE
    assert receipt["observed"]["card"]["declared_license"] == "odc-by"
    assert list(result["records"]) == list(selector.ADMITTED_COMPONENTS)
    fingerprints = set()
    for view, record in result["records"].items():
        assert record.outcome == ProbeOutcome.ACCESSIBLE
        assert record.evidence_type == EvidenceType.SYNTHETIC_FIXTURE
        assert (record.source_id, record.view_id) == ("essential_web", view)
        assert record.repository == ready.REPOSITORY
        assert record.immutable_revision == selector.SOURCE_REVISION
        assert record.verified_schema is not None and record.verified_schema.view_id == view
        assert record.resource_metrics["role"] == boot.PROBE_ROLE
        assert record.resource_metrics["producer"] == boot.PROBE_PRODUCER
        assert record.sample_preview == []
        fingerprints.add(record.probe_fingerprint)
    assert len(fingerprints) == 3 and None not in fingerprints


def test_request_schedule_is_far_below_the_pilot_ceiling() -> None:
    plan = plan_for(image())
    assert len(plan["schedule"]) == 4
    assert plan["nominal_physical_requests"] == 8
    assert plan["limits"]["max_physical_requests"] == 24 < PILOT_MAX_REQUESTS == 100
    assert boot.probe_budget().max_requests == 24


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status, self.headers, self._body = status, headers, io.BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def geturl(self) -> None:
        return None

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeOpener:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.urls: list[str] = []

    def open(self, request: Any, timeout: float) -> FakeResponse:
        self.urls.append(request.full_url)
        span = request.get_header("Range")
        if span is None:
            headers = {"Content-Length": str(len(CARD)), "X-Repo-Commit": selector.SOURCE_REVISION}
            return FakeResponse(200, headers, CARD)
        start, end = (int(part) for part in span.removeprefix("bytes=").split("-"))
        body = self.content[start : end + 1]
        headers = {
            "Content-Range": f"bytes {start}-{end}/{len(self.content)}",
            "Content-Length": str(len(body)),
            "ETag": ETAG,
        }
        return FakeResponse(206, headers, body)


def test_live_transport_is_pinned_metered_and_bounded() -> None:
    content = image()
    opener = FakeOpener(content)
    budget = boot.probe_budget()
    transport = boot.LiveSchemaProbeTransport(budget, opener_factory=lambda handler: opener)
    result = boot.run_schema_probe(plan_for(content), transport)
    assert result["receipt"]["status"] == "ACCESSIBLE"
    assert budget.requests_made == 4 <= boot.MAX_PHYSICAL_REQUESTS
    assert budget.bytes_transferred == len(CARD) + 4 + len(footer_of(content))
    prefix = (
        f"https://huggingface.co/datasets/{ready.REPOSITORY}/resolve/{selector.SOURCE_REVISION}/"
    )
    assert opener.urls[0] == prefix + "README.md"
    assert all(url.startswith(prefix) for url in opener.urls)
    tight = boot.probe_budget()
    tight.max_requests = 2
    starved = boot.LiveSchemaProbeTransport(tight, opener_factory=lambda handler: opener)
    with pytest.raises(boot.SchemaProbeRefusal):
        boot.run_schema_probe(plan_for(content), starved)
    assert tight.requests_made == 2


@pytest.mark.parametrize(
    "key,value",
    [("repository", "other/repo"), ("revision", "0" * 40), ("adapter_id", "essential_web")],
)
def test_wrong_source_revision_or_adapter_refuses(key: str, value: str) -> None:
    content = image()
    plan = plan_for(content)
    plan["binding"][key] = value
    transport = FakeTransport(content)
    with pytest.raises(boot.SchemaProbeRefusal, match="digest mismatch"):
        boot.run_schema_probe(plan, transport)
    with pytest.raises(boot.SchemaProbeRefusal, match="binding mismatch"):
        boot.run_schema_probe(redigest(plan), transport)
    assert transport.cards == 0 and not transport.ranges


def test_card_from_another_commit_or_license_refuses() -> None:
    content = image()
    plan = plan_for(content)
    with pytest.raises(boot.SchemaProbeRefusal, match="different commit") as caught:
        boot.run_schema_probe(plan, FakeTransport(content, commit="0" * 40))
    assert caught.value.outcome == ProbeOutcome.REVISION_MISSING
    for card, reason in (
        (b"---\nlicense: mit\n---\n", "license differs"),
        (b"# no front matter\n", "front matter"),
        (b"---\ntags: [x]\n---\n", "no single license"),
    ):
        with pytest.raises(boot.SchemaProbeRefusal, match=reason):
            boot.run_schema_probe(plan, FakeTransport(content, card=card))
    # The provider may omit the commit header; the URL pin still binds the revision.
    assert boot.run_schema_probe(plan, FakeTransport(content, commit=None))["records"]


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"extra": True}, "schema differs"),
        ({"drop": "text"}, r"lacks adapter fields: \['text'\]"),
        ({"drop": "eai_taxonomy"}, r"lacks adapter fields: \['eai_taxonomy'\]"),
        ({"drop": "quality_signals"}, r"lacks adapter fields: \['quality_signals'\]"),
        ({"rows": 100}, "first row group is shorter"),
    ],
)
def test_wrong_schema_refuses(kwargs: dict[str, Any], reason: str) -> None:
    other = image(**kwargs)
    plan = retarget(plan_for(image()), other)
    with pytest.raises(boot.SchemaProbeRefusal, match=reason) as caught:
        boot.run_schema_probe(plan, FakeTransport(other))
    assert caught.value.outcome == ProbeOutcome.SCHEMA_MISMATCH
    if "extra" not in kwargs:
        # An unusable schema cannot be frozen as the expected one either.
        with pytest.raises(boot.SchemaProbeRefusal):
            boot.build_probe_plan(window(other), footer_of(other), "authored")


def test_file_identity_mismatch_refuses() -> None:
    content = image()
    plan = plan_for(content)
    with pytest.raises(boot.SchemaProbeRefusal, match="ETag differs"):
        boot.run_schema_probe(plan, FakeTransport(content, etag='"other"'))
    with pytest.raises(boot.SchemaProbeRefusal, match="length differs"):
        boot.run_schema_probe(plan, FakeTransport(content + b"x"))
    stale = copy.deepcopy(plan)
    stale["file"]["footer_and_trailer_sha256"] = "0" * 64
    with pytest.raises(boot.SchemaProbeRefusal, match="footer bytes differ"):
        boot.run_schema_probe(redigest(stale), FakeTransport(content))
    with pytest.raises(boot.SchemaProbeRefusal, match="retained footer differs"):
        boot.build_probe_plan(
            {**window(content), "footer_sha256": "0" * 64}, footer_of(content), ""
        )


def real_shaped(record: ProbeEvidenceRecord) -> ProbeEvidenceRecord:
    # Authored record relabeled to exercise gate logic; never live evidence.
    return record.model_copy(update={"evidence_type": EvidenceType.REAL_OBSERVED})


def test_decision_binds_fingerprint_selector_and_reviews() -> None:
    content = image()
    records = boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"]
    other = image(rows=4096)
    drifted = boot.run_schema_probe(plan_for(other), FakeTransport(other))["records"]
    for view, record in records.items():
        decision = boot.build_decision(record, HASHES, "authored operator")
        assert not AdmissionGate.evaluate(record, decision).admitted
        assert AdmissionGate.evaluate(real_shaped(record), decision).admitted
        assert decision.adapter_id == "essential_web_bnormal"
        assert decision.selector_binding == selector.selector_identity()
        assert all(digest in decision.operator_notes for digest in HASHES.values())
        assert "not zero contamination" in decision.operator_notes
        gate = AdmissionGate.evaluate(real_shaped(drifted[view]), decision)
        assert not gate.admitted and any("fingerprint" in reason for reason in gate.reasons)
        foreign = next(name for name in records if name != view)
        assert not AdmissionGate.evaluate(real_shaped(records[foreign]), decision).admitted
    with pytest.raises(boot.ReviewRefusal):
        boot.build_decision(records["essential_science"], HASHES, " ")
    with pytest.raises(boot.ReviewRefusal):
        boot.build_decision(records["essential_science"], {"source_rights": "0" * 64}, "x")


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"contamination_mitigation": None}, "mitigation"),
        ({"reviews_sha256": {"source_rights": "0" * 64}}, "Benchmark-risk review"),
        ({"reviews_sha256": {"benchmark_risk": "0" * 64}}, "provenance review"),
        ({"contract_version": "1"}, "versioned"),
        ({"resource_contract": None}, "resource contract"),
        ({"benchmark_risk": "clean"}, "possible contamination"),
        ({"benchmark_risk": "suspect"}, "risk status"),
        ({"probe_fingerprint": "f" * 64}, "fingerprint"),
        ({"immutable_revision": "wrong"}, "revision"),
    ],
)
def test_mitigated_admission_fails_closed(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    content = image()
    record = real_shaped(
        boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"][
            "essential_science"
        ]
    )
    payload = boot.build_decision(record, HASHES, "authored").model_dump()
    decision = AdmissionDecision.model_validate({**payload, **changes})
    gate = AdmissionGate.evaluate(record, decision)
    assert not gate.admitted and any(reason in item for item in gate.reasons)
    store = store_at(tmp_path / "store")
    save_probe_evidence(record, store, tmp_path / "probe")
    save_admission_decision(decision, store, tmp_path / "decision")
    plan = load_acquisition_plan(READINESS / "probe-00.plan.json")
    with pytest.raises(AuthorizationRequiredError, match=reason):
        resolve_verified_production_admission(plan, store)


@pytest.mark.parametrize("value", ["unknown", "risk_accepted", "", "CLEAN"])
def test_arbitrary_risk_values_refuse(value: str) -> None:
    content = image()
    record = boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"][
        "essential_science"
    ]
    payload = boot.build_decision(record, HASHES, "authored").model_dump()
    with pytest.raises(ValidationError):
        AdmissionDecision.model_validate({**payload, "benchmark_risk": value})


def test_mitigation_cannot_disable_later_c05_gate() -> None:
    content = image()
    record = boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"][
        "essential_science"
    ]
    payload = boot.build_decision(record, HASHES, "authored").model_dump()
    for field, value in (
        ("mechanism", "arbitrary.module"),
        ("before_official_benchmark_claims", False),
        ("before_training", False),
        ("benchmarks", ["BLiMP"]),
    ):
        modified = copy.deepcopy(payload)
        modified["contamination_mitigation"][field] = value
        with pytest.raises(ValidationError):
            AdmissionDecision.model_validate(modified)


def committed_reviews() -> dict[str, Any]:
    return {
        name: json.loads((BOOTSTRAP / file).read_bytes())
        for name, file in boot.REVIEW_FILES.items()
    }


def test_committed_reviews_are_bound_and_do_not_overclaim(tool: Any) -> None:
    reviews, hashes = tool.load_reviews(BOOTSTRAP)
    manifest = json.loads((BOOTSTRAP / "review-manifest.json").read_bytes())
    assert manifest["sha256"] == {boot.REVIEW_FILES[name]: hashes[name] for name in hashes}
    risk = reviews["benchmark_risk"]
    assert risk["contamination_possible"] is True
    assert risk["permission_to_acquire_and_pretrain"] is True
    assert risk["permission_to_claim_uncontaminated_benchmark_results"] is False
    assert reviews["source_rights"]["underlying_content_caveat"]["per_document_rights"] == "unknown"


@pytest.mark.parametrize(
    "name,path,value",
    [
        ("source_rights", ("binding", "revision"), "0" * 40),
        ("benchmark_risk", ("binding", "repository"), "other/repo"),
        ("attribution", ("legal_advice",), True),
        ("source_rights", ("claims", "every_source_document_is_odc_by"), True),
        ("source_rights", ("claims", "underlying_content_rights_cleared"), True),
        ("source_rights", ("privacy_and_third_party_risk",), {}),
        ("source_rights", ("dataset_license_metadata", "license"), "mit"),
        ("benchmark_risk", ("zero_contamination_claimed",), True),
        ("benchmark_risk", ("contamination_possible",), False),
        ("benchmark_risk", ("permission_to_claim_uncontaminated_benchmark_results",), True),
        ("benchmark_risk", ("permission_to_acquire_and_pretrain",), False),
        ("benchmark_risk", ("benchmarks",), ["BLiMP"]),
        ("benchmark_risk", ("mitigation",), []),
        ("benchmark_risk", ("gate_value",), "suspect"),
        ("attribution", ("attributions",), []),
        ("external_evidence", ("sources",), []),
    ],
)
def test_review_overclaim_or_drift_refuses(name: str, path: tuple[str, ...], value: Any) -> None:
    reviews = committed_reviews()
    boot.check_reviews(reviews)
    target = reviews[name]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(boot.ReviewRefusal):
        boot.check_reviews(reviews)
    with pytest.raises(boot.ReviewRefusal):
        boot.check_reviews({key: item for key, item in committed_reviews().items() if key != name})


def store_at(home: Path) -> ArtifactStore:
    return ArtifactStore(ArtifactPaths(root=home))


def test_renewed_evidence_supersedes_and_preserves_the_failed_record(tmp_path: Path) -> None:
    store = store_at(tmp_path / "home")
    failed = ProbeEvidenceRecord(
        source_id="essential_web",
        view_id="essential_science",
        provider="huggingface",
        repository=ready.REPOSITORY,
        outcome=ProbeOutcome.BUDGET_EXHAUSTED,
    )
    base = "probe_essential_web_essential_science"
    assert latest_attempt(store, "probe_evidence", base) == 0
    first = Path(save_probe_evidence(failed, store, staging_dir=tmp_path / "s1"))
    content = image()
    record = boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"][
        "essential_science"
    ]
    with pytest.raises(ArtifactConflictError):
        save_probe_evidence(record, store, staging_dir=tmp_path / "s2")
    attempt = next_attempt(store, "probe_evidence", base)
    assert attempt == 2 and attempt_artifact_id(base, attempt) == base + ".attempt02"
    second = Path(save_probe_evidence(record, store, staging_dir=tmp_path / "s3", attempt=attempt))
    loaded = load_probe_evidence("essential_web", "essential_science", store)
    assert loaded is not None and loaded.probe_fingerprint == record.probe_fingerprint
    assert json.loads((first / "probe_evidence.json").read_bytes())["outcome"] == "budget_exhausted"
    # A damaged newest attempt is never skipped in favor of older evidence.
    (second / "probe_evidence.json").write_bytes(b"{}")
    assert load_probe_evidence("essential_web", "essential_science", store) is None
    assert load_probe_evidence("essential_web", "essential_prose", store) is None
    with pytest.raises(ValueError):
        attempt_artifact_id(base, 17)


def publish_views(home: Path, staging: Path, real: bool) -> None:
    content = image()
    records = boot.run_schema_probe(plan_for(content), FakeTransport(content))["records"]
    store = store_at(home)
    for view, record in records.items():
        save_probe_evidence(
            real_shaped(record) if real else record, store, staging_dir=staging / view
        )


def test_prepare_and_sealed_operator_admission(
    tmp_path: Path, tool: Any, isolated_xlm_home: Path
) -> None:
    content = image()
    result = boot.run_schema_probe(plan_for(content), FakeTransport(content))
    # Authored real-shaped schema receipt for exercising offline validation only.
    receipt = result["receipt"]
    receipt["evidence_type"] = "real_observed"
    receipt = redigest(receipt)
    published = {}
    store = store_at(isolated_xlm_home)
    for view, record in result["records"].items():
        record = real_shaped(record)
        record.resource_metrics["receipt_digest"] = receipt["digest"]
        save_probe_evidence(record, store, tmp_path / "staging" / view)
        published[view] = {"probe_fingerprint": record.probe_fingerprint}
    receipt["published"] = published
    receipt_path = tmp_path / "authored-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    output = tmp_path / "prepared.json"
    argv = [
        "prepare-admission",
        "--reviews",
        str(BOOTSTRAP),
        "--store",
        str(isolated_xlm_home),
        "--receipt",
        str(receipt_path),
        "--output",
        str(output),
    ]
    assert tool.main(argv) == 0
    rows = json.loads(output.read_bytes())
    assert len(rows) == 3
    assert all(not row["prepared_decision"]["operator_approved"] for row in rows)
    assert not (isolated_xlm_home / "admission_decision").exists()
    admit = [
        "admit",
        "--reviews",
        str(BOOTSTRAP),
        "--operator",
        "authored",
        "--operator-approve",
        "--prepared",
        str(output),
        "--output",
        str(tmp_path / "recorded.json"),
    ]
    original = output.read_bytes()
    rows[0]["prepared_decision"]["probe_fingerprint"] = "f" * 64
    output.write_text(json.dumps(rows), encoding="utf-8")
    assert tool.main(admit) == 1  # seal mismatch
    assert not (isolated_xlm_home / "admission_decision").exists()
    output.write_bytes(original)
    assert tool.main(admit) == 0


def test_operator_admission_then_production_probe_authorization(
    tmp_path: Path, isolated_xlm_home: Path, tool: Any
) -> None:
    plan = load_acquisition_plan(READINESS / "probe-00.plan.json")
    assert plan_requires_production_admission(plan) and not plan.is_pilot
    assert plan.limits.max_requests == 330 and plan.limits.max_transferred_bytes == 128 * 1024**2
    assert plan.row_ranges is not None and list(plan.row_ranges.values()) == [(0, 256)]
    store = store_at(isolated_xlm_home)
    record = tmp_path / "admission-record.json"
    argv = ["admit", "--reviews", str(BOOTSTRAP), "--operator", "authored", "--output", str(record)]
    with pytest.raises(AuthorizationRequiredError, match="no probe evidence"):
        resolve_verified_production_admission(plan, store)
    assert tool.main([*argv, "--operator-approve"]) == 1
    publish_views(isolated_xlm_home, tmp_path / "staging", real=True)
    with pytest.raises(AuthorizationRequiredError, match="no recorded operator admission"):
        resolve_verified_production_admission(plan, store)
    assert tool.main(["status"]) == 1
    assert tool.main(argv) == 1
    assert not (isolated_xlm_home / "admission_decision").exists() and not record.exists()
    assert tool.main([*argv, "--operator-approve"]) == 0
    assert tool.main(["status"]) == 0
    for view in selector.ADMITTED_COMPONENTS:
        decision = load_admission_decision("essential_web", view, store)
        assert decision is not None and decision.operator_approved
        assert decision.immutable_revision == selector.SOURCE_REVISION
    resolve_verified_production_admission(plan, store)
    with pytest.raises(AuthorizationRequiredError, match="explicit PlanAuthorization"):
        validate_plan_authorization(plan, True)
    authorized = plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.plan_hash,
                authorized_by="authored",
                authorized_at="authored",
                scope="production",
            )
        }
    )
    validate_plan_authorization(authorized, True)
    with pytest.raises(AuthorizationRequiredError, match="prior operator admission"):
        validate_plan_authorization(authorized, False)
    # The operator script's own plan command reproduces the frozen authorized hash.
    out = tmp_path / "probe-00.plan.json"
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "--source",
            "essential_web",
            "--view",
            "essential_science",
            "--catalog",
            str(READINESS / "production-catalog.json"),
            "--files",
            plan.selected_files[0],
            "--mode",
            "selected_records",
            "--row-ranges",
            str(READINESS / "probe-00.rows.json"),
            "--adapter-spec",
            "essential_web_bnormal:essential_science",
            "--seed",
            str(ready.SEED),
            "--limits",
            str(READINESS / "probe-00.limits.json"),
            "--parquet-window-scan-rows",
            "2048",
            "--parquet-window-buffer-bytes",
            "4194304",
            "--parquet-window-batch-rows",
            "256",
            "--parquet-window-policy-version",
            "2",
            "--authorization-hash",
            plan.plan_hash,
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    regenerated = load_acquisition_plan(out)
    assert regenerated.plan_hash == plan.plan_hash
    validate_plan_authorization(regenerated, True)


def test_synthetic_evidence_is_never_admitted(
    tmp_path: Path, isolated_xlm_home: Path, tool: Any
) -> None:
    publish_views(isolated_xlm_home, tmp_path / "staging", real=False)
    argv = ["admit", "--reviews", str(BOOTSTRAP), "--operator", "authored", "--operator-approve"]
    assert tool.main([*argv, "--output", str(tmp_path / "record.json")]) == 1
    assert not (isolated_xlm_home / "admission_decision").exists()


def test_live_probe_needs_explicit_network_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: Any
) -> None:
    argv = ["probe", "--plan", str(BOOTSTRAP / "schema-probe-plan.json")]
    argv += ["--output-dir", str(tmp_path / "out")]
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert tool.main([*argv, "--authorize-network"]) == 1
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    assert tool.main(argv) == 1
    assert not (tmp_path / "out").exists()


def test_committed_bootstrap_freeze(tool: Any) -> None:
    plan = json.loads((BOOTSTRAP / "schema-probe-plan.json").read_bytes())
    boot.check_probe_plan(plan)
    calibration = json.loads((READINESS / "calibration-plan.json").read_bytes())
    first = calibration["windows"][0]
    assert plan["file"]["file"] == first["file"]
    assert plan["file"]["strong_etag"] == first["strong_etag"]
    assert plan["file"]["footer_and_trailer_sha256"] == first["footer_sha256"]
    assert plan["planning_evidence"]["calibration_digest"] == calibration["digest"]
    assert plan["live_run"] is False
    assert tool.calibration_unchanged(READINESS)["unchanged"] is True
    dry = tool.production_probe_dry(READINESS)
    assert dry["classification"] == "production" and dry["executed"] is False
    assert dry["future_script_carries_matching_authorization_hash"] is True
    assert all(dry["refused_when"].values())
    readiness = json.loads((BOOTSTRAP / "readiness.json").read_bytes())
    assert readiness["vector"]["probe_plan_ready"] is True
    assert readiness["vector"]["calibration_plan_ready"] is True
    assert readiness["vector"]["production_admission_ok"] is False
    assert readiness["live_schema_probe_run"] is False
    for entry in json.loads((BOOTSTRAP / "admission-decisions.json").read_bytes()):
        assert entry["production_admission_ok"] is False
        decision = AdmissionDecision.model_validate(entry["prepared_decision"])
        assert len(decision.probe_fingerprint) == 64
        assert decision.benchmark_risk == "suspect_with_mitigation"
        assert not decision.operator_approved
        assert entry["official_benchmark_claims_allowed"] is False
        assert (
            entry["offline_verifier"] == "PASS in temporary store with simulated operator approval"
        )
    seal = json.loads((BOOTSTRAP / "admission-decisions.seal.json").read_bytes())
    assert seal["decisions_sha256"] == tool.sha256_file(BOOTSTRAP / "admission-decisions.json")
