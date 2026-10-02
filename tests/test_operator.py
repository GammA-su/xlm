"""Acceptance tests for P21: sealed readiness, protected finals, release audits.

No real final data, no real sealed deployment, no keys. Synthetic fixtures only;
the two-identity deployment itself is NOT RUN here and is recorded as such.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from xlm.data.exclusion.receipt import (
    BenchmarkClaimBinding,
    issue_development_receipt,
    sign_receipt,
)
from xlm.operator.final import (
    AccessLog,
    FinalEvaluationError,
    FinalEvaluationReceipt,
    FinalEvaluationRequest,
    OperatorAuthorization,
    build_final_request,
    check_operator_environment,
    execute_final_request,
    prepare_exclusion_receipt,
    sanitize_aggregates,
    verify_receipt,
)
from xlm.operator.release import (
    ReleaseAuditError,
    audit_release,
    purge_final_split_artifacts,
    verify_no_final_examples,
)
from xlm.operator.sealed import check_sealed_readiness, require_ready


def _request(**overrides: Any) -> FinalEvaluationRequest:
    kwargs: dict[str, Any] = {
        "checkpoint_hash": "ckpt_abc",
        "suite_fingerprint": "suite_fp",
        "task_variants": ["xlm_arc_easy_final"],
        "max_items": 100,
        "max_requests": 2,
        "requester": "developer",
        "nonce": "fixed-nonce-001",
    }
    kwargs.update(overrides)
    return build_final_request(**kwargs)


def _sealed_tree(root: Path) -> dict[str, Path]:
    sealed = root / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    bundle = sealed / "scorer_bundle.py"
    bundle.write_text("# reviewed scorer bundle v1\n", encoding="utf-8")
    bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (sealed / "reviewed_scorers.json").write_text(
        json.dumps({"scorer_v1": bundle_hash}), encoding="utf-8"
    )
    return {
        "root": sealed,
        "bundle": bundle,
        "reviewed": sealed / "reviewed_scorers.json",
        "quota": sealed / "quota.json",
    }


def _auth(request: FinalEvaluationRequest, **overrides: Any) -> OperatorAuthorization:
    payload: dict[str, Any] = {
        "operator_id": "operator_1",
        "request_hash": request.request_hash,
        "ticket": "T-1",
        "revoked": False,
    }
    payload.update(overrides)
    return OperatorAuthorization(**payload)


def _supplier(request: FinalEvaluationRequest) -> tuple[dict[str, Any], int]:
    assert request.max_items >= 10
    return ({"arc_easy": {"acc": 0.5, "acc_norm": 0.4}}, 10)


# ------------------------------------------------------- sealed readiness


def test_missing_sealed_root_is_not_ready(tmp_path: Path) -> None:
    report = check_sealed_readiness(tmp_path / "absent", [tmp_path])
    assert report.ready is False
    with pytest.raises(Exception, match="sealed mode not established"):
        require_ready(report)


def test_writable_same_user_directory_is_not_ready(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    report = check_sealed_readiness(sealed, [tmp_path / "elsewhere"])
    assert report.ready is False
    assert any("same-user" in c["detail"] for c in report.checks if not c["passed"])


def test_sealed_root_inside_an_agent_mount_is_not_ready(tmp_path: Path) -> None:
    sealed = tmp_path / "workspace" / "sealed"
    sealed.mkdir(parents=True)
    report = check_sealed_readiness(sealed, [tmp_path / "workspace"])
    assert report.ready is False
    assert any("inside agent mount" in c["detail"] for c in report.checks if not c["passed"])


def test_yaml_flag_without_attestation_is_not_ready(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    report = check_sealed_readiness(
        sealed, [tmp_path / "elsewhere"], access=lambda path, mode: False
    )
    assert report.ready is False
    assert any("attestation" in c["check"] for c in report.checks if not c["passed"])


def test_bare_sealed_flag_attestation_is_refused(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "operator_attestation.json").write_text(
        json.dumps({"sealed": True}), encoding="utf-8"
    )
    report = check_sealed_readiness(
        sealed, [tmp_path / "elsewhere"], access=lambda path, mode: False
    )
    assert report.ready is False


def test_attested_nonwritable_root_reports_ready() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        sealed = Path(directory) / "sealed"
        sealed.mkdir()
        (sealed / "operator_attestation.json").write_text(
            json.dumps({"separation_method": "separate-account", "operator": "op_1"}),
            encoding="utf-8",
        )
        report = check_sealed_readiness(
            sealed, [Path(directory) / "workspace"], access=lambda path, mode: False
        )
        assert report.ready is True
        require_ready(report)
        assert any("operational" in limitation for limitation in report.limitations)


# ------------------------------------------------------------ final protocol


def test_unbounded_or_empty_requests_are_refused() -> None:
    with pytest.raises(FinalEvaluationError, match="positive item/request bounds"):
        build_final_request("c", "s", ["t"], max_items=0, max_requests=1, nonce="n")
    with pytest.raises(FinalEvaluationError, match="at least one task variant"):
        build_final_request("c", "s", [], max_items=10, max_requests=1, nonce="n")
    # Wall-clock timestamps are request inputs, so two builds are never asserted
    # equal; determinism means same object, same hash, and distinct nonces differ.
    first = _request()
    assert first.request_hash == first.request_hash
    assert first.request_hash != _request(nonce="different-nonce").request_hash


def test_execute_happy_path_returns_aggregates_only(tmp_path: Path) -> None:
    sealed = _sealed_tree(tmp_path)
    request = _request()
    receipt = execute_final_request(
        request,
        _auth(request),
        sealed["root"],
        sealed["bundle"],
        sealed["reviewed"],
        sealed["quota"],
        _supplier,
        "operator_1",
        "code_hash_1",
        "rolling",
        "fp32",
    )
    assert isinstance(receipt, FinalEvaluationReceipt)
    assert receipt.task_aggregates == {"arc_easy": {"acc": 0.5, "acc_norm": 0.4}}
    assert receipt.items_scored == 10
    assert receipt.exposure_classification == "final-protected"
    assert (
        set(receipt.to_dict())
        & {
            "predictions",
            "per_item",
            "items",
            "labels",
            "logits",
        }
        == set()
    )
    verdict = verify_receipt(receipt.to_dict(), sealed["root"])
    assert verdict["valid"] is True, verdict["findings"]
    assert verdict["official_benchmark_claims_allowed"] is False
    assert verdict["benchmark_contamination_status"] == "possibly_contaminated"
    assert verdict["zero_contamination_proven"] is False
    log = AccessLog(sealed["root"] / "access.log.jsonl").entries()
    assert log and log[-1]["action"] == "final_execute"


def test_official_claim_gate_requires_matching_c05_and_evaluation(tmp_path: Path) -> None:
    sealed = _sealed_tree(tmp_path)
    request = _request()
    result = execute_final_request(
        request,
        _auth(request),
        sealed["root"],
        sealed["bundle"],
        sealed["reviewed"],
        sealed["quota"],
        _supplier,
        "operator_1",
        "code_hash_1",
        "rolling",
        "fp32",
    )
    # Synthetic protected-shaped receipts: no real benchmark content or operator key.
    from test_exclusion_receipt import claim_binding, selected_receipt

    legacy = issue_development_receipt(["authored-doc"], [], "policy", "index")
    legacy.mode = "protected"
    legacy = sign_receipt(legacy, b"authored-key")
    exclusion = sign_receipt(selected_receipt(issuer_id=legacy.issuer_id), b"authored-key")
    binding = replace(
        claim_binding(exclusion),
        checkpoint_hash=request.checkpoint_hash,
        suite_fingerprint=request.suite_fingerprint,
    )
    assert isinstance(binding, BenchmarkClaimBinding)
    verdict = verify_receipt(
        result.to_dict(),
        sealed["root"],
        exclusion_receipt=legacy,
        training_pool=replace(
            binding,
            corpus_input_digest=legacy.corpus_input_digest,
            output_membership_digest=legacy.output_membership_digest,
            exclusion_policy_identity="policy",
            exclusion_index_identity="index",
        ),
        trusted_exclusion_issuers={legacy.issuer_id: b"authored-key"},
    )
    # Global kept membership (no exact selected training subset) cannot support claims.
    assert verdict["valid"] is True and verdict["official_benchmark_claims_allowed"] is False
    for candidate, allowed in (
        (binding, True),
        (replace(binding, checkpoint_hash="foreign"), False),
        (replace(binding, suite_fingerprint="foreign"), False),
        (replace(binding, output_membership_digest="foreign"), False),
        (replace(binding, selection_digest="foreign"), False),
    ):
        verdict = verify_receipt(
            result.to_dict(),
            sealed["root"],
            exclusion_receipt=exclusion,
            training_pool=candidate,
            trusted_exclusion_issuers={exclusion.issuer_id: b"authored-key"},
        )
        assert verdict["valid"] is True
        assert verdict["official_benchmark_claims_allowed"] is allowed
        assert verdict["zero_contamination_proven"] is False
    tampered = {**result.to_dict(), "items_scored": 999}
    verdict = verify_receipt(
        tampered,
        sealed["root"],
        exclusion_receipt=exclusion,
        training_pool=binding,
        trusted_exclusion_issuers={exclusion.issuer_id: b"authored-key"},
    )
    assert not verdict["valid"] and not verdict["official_benchmark_claims_allowed"]


def test_replay_revocation_mismatch_and_quota_are_refused(tmp_path: Path) -> None:
    sealed = _sealed_tree(tmp_path)
    request = _request()
    execute_final_request(
        request,
        _auth(request),
        sealed["root"],
        sealed["bundle"],
        sealed["reviewed"],
        sealed["quota"],
        _supplier,
        "operator_1",
        "code_hash_1",
        "rolling",
        "fp32",
    )
    with pytest.raises(FinalEvaluationError, match="already executed"):
        execute_final_request(
            request,
            _auth(request),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code_hash_1",
            "rolling",
            "fp32",
        )
    with pytest.raises(FinalEvaluationError, match="revoked|empty operator"):
        execute_final_request(
            _request(nonce="other"),
            _auth(request, revoked=True),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code_hash_1",
            "rolling",
            "fp32",
        )
    with pytest.raises(FinalEvaluationError, match="exact request hash"):
        execute_final_request(
            _request(nonce="other"),
            _auth(request),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code_hash_1",
            "rolling",
            "fp32",
        )


def test_expired_and_unreviewed_and_unbounded_suppliers_refused(tmp_path: Path) -> None:
    # Each authorization below is issued for the exact request object executed:
    # request hashes embed wall-clock construction time, so authorizing one
    # construction and executing another can straddle a clock tick and mismatch.
    # Refusal assertions (expired / unreviewed / aggregates-only) are unchanged.
    sealed = _sealed_tree(tmp_path)
    expired = build_final_request(
        "ckpt_abc",
        "suite_fp",
        ["t"],
        max_items=10,
        max_requests=1,
        nonce="expired-1",
        ttl_seconds=-10,
    )
    with pytest.raises(FinalEvaluationError, match="expired"):
        execute_final_request(
            expired,
            _auth(expired),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code",
            "rolling",
            "fp32",
        )
    foreign = sealed["root"] / "unreviewed.py"
    foreign.write_text("# not reviewed\n", encoding="utf-8")
    unreviewed = _request(nonce="unrev")
    with pytest.raises(FinalEvaluationError, match="not in the reviewed registry"):
        execute_final_request(
            unreviewed,
            _auth(unreviewed),
            sealed["root"],
            foreign,
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code",
            "rolling",
            "fp32",
        )

    def forbidden_supplier(request: Any) -> tuple[dict[str, Any], int]:
        return ({"arc_easy": {"acc": 0.5}, "predictions": ["a", "b"]}, 2)

    forbidden = _request(nonce="forbidden")
    with pytest.raises(FinalEvaluationError, match="aggregates only"):
        execute_final_request(
            forbidden,
            _auth(forbidden),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            forbidden_supplier,
            "operator_1",
            "code",
            "rolling",
            "fp32",
        )


def test_missing_credentials_refuse_execution() -> None:
    with pytest.raises(FinalEvaluationError, match="cannot"):
        check_operator_environment(None)
    with pytest.raises(FinalEvaluationError, match="not found"):
        check_operator_environment("/nonexistent/creds")


def test_receipt_alteration_and_unknown_ids_fail_verification(tmp_path: Path) -> None:
    sealed = _sealed_tree(tmp_path)
    request = _request()
    receipt = execute_final_request(
        request,
        _auth(request),
        sealed["root"],
        sealed["bundle"],
        sealed["reviewed"],
        sealed["quota"],
        _supplier,
        "operator_1",
        "code_hash_1",
        "rolling",
        "fp32",
    )
    tampered = dict(receipt.to_dict())
    tampered["task_aggregates"] = {"arc_easy": {"acc": 0.99, "acc_norm": 0.99}}
    verdict = verify_receipt(tampered, sealed["root"])
    assert verdict["valid"] is False
    assert any("altered" in f for f in verdict["findings"])

    with_per_item = dict(receipt.to_dict())
    with_per_item["predictions"] = ["x"]
    verdict = verify_receipt(with_per_item, sealed["root"])
    assert verdict["valid"] is False

    verdict = verify_receipt({**receipt.to_dict(), "receipt_id": "nope"}, sealed["root"])
    assert verdict["valid"] is False


def test_decontamination_receipts_carry_counts_not_content() -> None:
    receipt = prepare_exclusion_receipt("batch_hash_1", "13gram-match", 900, 100, "op_1")
    assert receipt.kept_documents == 900 and receipt.excluded_documents == 100
    dumped = json.dumps(receipt.to_dict())
    assert "matched" not in dumped and "SECRET" not in dumped
    with pytest.raises(FinalEvaluationError, match="cannot be negative"):
        prepare_exclusion_receipt("b", "m", -1, 0, "op")


def test_sanitize_aggregates_rejects_non_scalars() -> None:
    with pytest.raises(FinalEvaluationError, match="not a scalar"):
        sanitize_aggregates({"t": {"acc": [0.5]}})


def test_quota_bounds_adaptive_oracles(tmp_path: Path) -> None:
    sealed = _sealed_tree(tmp_path)
    first = build_final_request("ckpt_q", "s", ["t"], max_items=10, max_requests=1, nonce="q1")
    execute_final_request(
        first,
        _auth(first),
        sealed["root"],
        sealed["bundle"],
        sealed["reviewed"],
        sealed["quota"],
        _supplier,
        "operator_1",
        "code",
        "rolling",
        "fp32",
    )
    second = build_final_request("ckpt_q", "s", ["t"], max_items=10, max_requests=1, nonce="q2")
    with pytest.raises(FinalEvaluationError, match="quota exhausted"):
        execute_final_request(
            second,
            _auth(second),
            sealed["root"],
            sealed["bundle"],
            sealed["reviewed"],
            sealed["quota"],
            _supplier,
            "operator_1",
            "code",
            "rolling",
            "fp32",
        )


# ------------------------------------------------------------ release audits


def _good_release(root: Path) -> None:
    (root / "export_manifest.json").write_text(
        json.dumps({"files": [{"path": "model.safetensors"}]}), encoding="utf-8"
    )
    (root / "model.safetensors").write_bytes(b"weights")
    (root / "code_hashes.json").write_text(
        json.dumps({"uv_lock_sha256": "a" * 64}), encoding="utf-8"
    )
    (root / "admission.json").write_text(
        json.dumps({"sources": [{"source_id": "s1", "license_review": "approved"}]}),
        encoding="utf-8",
    )
    (root / "lineage.json").write_text(
        json.dumps({"sources": [{"source_id": "s1", "origin": "catalog"}]}), encoding="utf-8"
    )
    (root / "rights.json").write_text(
        json.dumps({"entries": [{"source_id": "s1", "status": "cleared"}]}), encoding="utf-8"
    )
    (root / "REPRODUCE.md").write_text("# reproduce\n", encoding="utf-8")
    (root / "exposure.json").write_text(
        json.dumps({"tier": "search", "final_scores": "none"}), encoding="utf-8"
    )
    (root / "disclosure.json").write_text(
        json.dumps({"unique_parameters": 1000, "compute_seconds": 12.5}), encoding="utf-8"
    )
    (root / "ATTRIBUTION").write_text("sources cited\n", encoding="utf-8")


def test_release_audit_passes_a_complete_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _good_release(release)
    audit = audit_release(release)
    assert audit.verdict == "RELEASE", audit.to_dict()
    assert all(f.status == "pass" for f in audit.findings)


def test_release_audit_blocks_unknown_rights_and_fails_gaps(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _good_release(release)
    (release / "rights.json").write_text(
        json.dumps({"entries": [{"source_id": "s2", "status": "unknown"}]}), encoding="utf-8"
    )
    (release / "ATTRIBUTION").unlink()
    (release / "api_token.txt").write_text("hf_secret", encoding="utf-8")
    audit = audit_release(release)
    by_check = {f.check: f for f in audit.findings}
    assert by_check["rights_provenance"].status == "blocked"
    assert "never silently certified" in by_check["rights_provenance"].detail
    assert by_check["attribution"].status == "fail"
    assert by_check["secrets_absent"].status == "fail"
    assert audit.verdict in ("HOLD", "BLOCKED")

    labels = tmp_path / "release_labels"
    labels.mkdir()
    _good_release(labels)
    (labels / "scores.json").write_text(json.dumps({"final_predictions": ["a"]}), encoding="utf-8")
    audit = audit_release(labels)
    assert [f for f in audit.findings if f.check == "final_labels_absent"][0].status == "fail"

    with pytest.raises(ReleaseAuditError, match="not found"):
        audit_release(tmp_path / "absent")


# ------------------------------------------------- final-data absence checks


def _fake_hf_cache(root: Path, with_test_split: bool) -> Path:
    cache = root / "hf_cache"
    repo = cache / "datasets" / "allenai___ai2_arc" / "ARC-Easy" / "0.0.0" / "rev1"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "ai2_arc-train.arrow").write_bytes(b"train")
    if with_test_split:
        (repo / "ai2_arc-test.arrow").write_bytes(b"test-bytes")
    hub = cache / "hub" / "datasets--allenai--ai2_arc" / "snapshots" / "rev1"
    hub.mkdir(parents=True, exist_ok=True)
    (hub / "train-00000-of-00001.parquet").write_bytes(b"train")
    if with_test_split:
        (hub / "test-00000-of-00001.parquet").write_bytes(b"test-bytes")
    return cache


def test_final_split_artifacts_are_detected_in_caches(tmp_path: Path) -> None:
    cache = _fake_hf_cache(tmp_path, with_test_split=True)
    result = verify_no_final_examples(hf_caches=[cache / "datasets", cache / "hub"])
    assert result["clean"] is False
    paths = [f["path"] for f in result["findings"]]
    assert any("ai2_arc-test.arrow" in p for p in paths)
    assert any("test-00000-of-00001.parquet" in p for p in paths)
    assert all(
        f["split"] == "test" and f["repository"] == "allenai/ai2_arc" for f in result["findings"]
    )


def test_clean_cache_passes_and_purge_needs_confirmation(tmp_path: Path) -> None:
    cache = _fake_hf_cache(tmp_path, with_test_split=False)
    result = verify_no_final_examples(hf_caches=[cache / "datasets", cache / "hub"])
    assert result["clean"] is True

    dirty = _fake_hf_cache(tmp_path / "dirty", with_test_split=True)
    dirty_result = verify_no_final_examples(hf_caches=[dirty / "datasets", dirty / "hub"])
    with pytest.raises(ReleaseAuditError, match="require_confirm=False"):
        purge_final_split_artifacts(dirty_result["findings"], require_confirm=True)
    purged = purge_final_split_artifacts(dirty_result["findings"], require_confirm=False)
    assert len(purged["removed"]) == 2
    rerun = verify_no_final_examples(hf_caches=[dirty / "datasets", dirty / "hub"])
    assert rerun["clean"] is True


# ------------------------------------------------------------------------ CLI


def _invoke(args: list[str]) -> Any:
    from typer.testing import CliRunner

    from xlm.cli.main import app

    return CliRunner().invoke(app, args)


def test_cli_final_request_builds_but_never_executes(tmp_path: Path) -> None:
    out = tmp_path / "request.json"
    result = _invoke(
        [
            "final",
            "request",
            "--checkpoint-hash",
            "ckpt_1",
            "--suite-fingerprint",
            "suite_fp",
            "--task-variants",
            "xlm_arc_easy_final",
            "--max-items",
            "50",
            "--max-requests",
            "1",
            "--output",
            str(out),
        ]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["checkpoint_hash"] == "ckpt_1"

    unbounded = _invoke(
        [
            "final",
            "request",
            "--checkpoint-hash",
            "ckpt_1",
            "--suite-fingerprint",
            "suite_fp",
            "--task-variants",
            "t",
            "--max-items",
            "0",
            "--output",
            str(tmp_path / "bad.json"),
        ]
    )
    assert unbounded.exit_code == 1


def test_cli_final_execute_refuses_without_operator(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request().to_dict()), encoding="utf-8")
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"operator_id": "op", "request_hash": "x", "ticket": "t"}),
        encoding="utf-8",
    )
    result = _invoke(
        [
            "final",
            "execute",
            "--request",
            str(request_path),
            "--authorization",
            str(auth_path),
            "--sealed-root",
            str(tmp_path / "sealed"),
            "--scorer-bundle",
            str(tmp_path / "bundle.py"),
            "--reviewed-bundles",
            str(tmp_path / "reviewed.json"),
            "--output",
            str(tmp_path / "receipt.json"),
        ]
    )
    assert result.exit_code == 1
    assert "operator" in result.output.lower()


def test_cli_release_audit_gates_releases(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _good_release(release)
    result = _invoke(["release", "audit", str(release)])
    assert result.exit_code == 0, result.output
    assert "RELEASE" in result.output

    (release / "ATTRIBUTION").unlink()
    result = _invoke(["release", "audit", str(release)])
    assert result.exit_code == 1
    assert "HOLD" in result.output

    result = _invoke(["release", "audit", str(tmp_path / "absent")])
    assert result.exit_code == 1
