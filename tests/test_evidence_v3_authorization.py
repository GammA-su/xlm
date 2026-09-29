"""Adversarial tests: authorization, operator approval, trusted objects, identity.

Each Astra second-review exploit is reproduced against the supported API
(``executor.check_authorization`` / ``perform_genesis``) and must refuse.
"""

from __future__ import annotations

import copy
import hashlib
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evidence_v3_support import REPO, block_network, build, load_json, write_json
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import (
    authorization,
    envidentity,
    executor,
    frozen_v3,
    genesis,
    plan,
    synthetic,
    trust,
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _check(ep: synthetic.SyntheticEpoch) -> dict[str, Any]:
    return executor.check_authorization(**ep.paths(), harness=ep.harness())


def _edit_auth(ep: synthetic.SyntheticEpoch, **changes: Any) -> None:
    body = load_json(ep.auth_path)
    body.update(changes)
    ep.auth_path.write_bytes(synthetic.reseal(body))
    # Keep the approval bound to the edited authorization so only the edit is tested.
    new_digest = load_json(ep.auth_path)["digest"]
    write_json(ep.approval_path, synthetic.approval_body(new_digest))


def test_valid_synthetic_authorization_and_approval_pass(tmp_path: Path) -> None:
    ep = build(tmp_path)
    result = _check(ep)
    assert result["mode"] == "SYNTHETIC"
    assert not ep.root.exists(), "offline validation must not touch the execution root"


@pytest.mark.parametrize("arms", ["MT", "M,T", ["T", "M"], ["M"], ["M", "T", "D"], ["M", "M"]])
def test_arms_must_be_exact_ordered_array(tmp_path: Path, arms: Any) -> None:
    ep = build(tmp_path)
    _edit_auth(ep, arms=arms)
    with pytest.raises(executor.PhasePError, match="arms"):
        _check(ep)


def test_invented_environment_resealed_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    env = dict(load_json(ep.auth_path)["environment_identity"])
    env["torch_build"] = "cuda-12.4"
    _edit_auth(ep, environment_identity=env)
    with pytest.raises(executor.PhasePError, match="environment_identity"):
        _check(ep)


def test_invented_python_version_resealed_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    env = dict(load_json(ep.auth_path)["environment_identity"])
    env["python_version"] = "3.12.99"
    _edit_auth(ep, environment_identity=env)
    with pytest.raises(executor.PhasePError, match="environment_identity"):
        _check(ep)


def test_code_hash_width_40_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    code = dict(load_json(ep.auth_path)["code_hashes"])
    first = sorted(code)[0]
    code[first] = code[first][:40]
    _edit_auth(ep, code_hashes=code)
    with pytest.raises(executor.PhasePError, match="64-hex"):
        _check(ep)


def test_code_hash_of_other_bytes_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    code = dict(load_json(ep.auth_path)["code_hashes"])
    code["src/xlm/data/evidence_v3/executor.py"] = hashlib.sha256(b"other").hexdigest()
    _edit_auth(ep, code_hashes=code)
    with pytest.raises(executor.PhasePError, match="code_hashes"):
        _check(ep)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewer_decision", "APPROVED"),
        ("reviewer_decision", "BLOCKED"),
        ("phase", "D"),
        ("phase", "p"),
        ("authorization_scope", "phase-P-only"),
        ("root_precondition", "anything"),
        ("schema_version", "2"),
        ("schema_version", True),
        ("schema_version", 2.0),
        ("epoch_id", "essential-web-evidence-v3.0:essential-web:epoch-0002"),
        ("execution_root", "G:/Project/xlm-evidence-v3/essential-web"),
        ("m_phase_p_plan_digest", "0" * 64),
        ("child_manifest_digest", "1" * 64),
        ("review_artifact_digest", "2" * 64),
        ("freeze_commit", "0" * 40),
        ("implementation_commit", "b" * 40),
        ("implementation_commit", "short"),
        ("resource_caps_digest", "3" * 64),
    ],
)
def test_any_binding_mismatch_refuses(tmp_path: Path, field: str, value: Any) -> None:
    ep = build(tmp_path)
    _edit_auth(ep, **{field: value})
    with pytest.raises(executor.PhasePError):
        _check(ep)


def test_resource_cap_bool_or_float_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    caps = copy.deepcopy(load_json(ep.auth_path)["resource_caps"])
    caps["M"]["max_retries"] = 2.0
    _edit_auth(ep, resource_caps=caps)
    with pytest.raises(executor.PhasePError, match="resource_caps"):
        _check(ep)


def test_unknown_and_missing_fields_refuse(tmp_path: Path) -> None:
    ep = build(tmp_path)
    _edit_auth(ep, authorization_ok=True)
    with pytest.raises(executor.PhasePError, match="unknown"):
        _check(ep)
    ep = build(tmp_path / "b")
    body = load_json(ep.auth_path)
    del body["phase"]
    ep.auth_path.write_bytes(synthetic.reseal(body))
    with pytest.raises(executor.PhasePError, match="missing"):
        _check(ep)


def test_non_canonical_or_tampered_bytes_refuse(tmp_path: Path) -> None:
    ep = build(tmp_path)
    raw = ep.auth_path.read_bytes()
    ep.auth_path.write_bytes(raw + b"\n")
    with pytest.raises(executor.PhasePError, match="canonical"):
        _check(ep)
    body = canonical.loads_bytes_strict(raw)
    body["source_revision"] = "0" * 40  # edited without resealing
    ep.auth_path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(executor.PhasePError):
        _check(ep)


# --------------------------------------------------------------------------
# Operator approval: exact typed semantics, no free text.
# --------------------------------------------------------------------------


def _approval(ep: synthetic.SyntheticEpoch, **changes: Any) -> None:
    body = synthetic.approval_body(load_json(ep.auth_path)["digest"])
    body.update(changes)
    ep.approval_path.write_bytes(synthetic.reseal(body))


@pytest.mark.parametrize(
    "decision",
    [
        "I do NOT approve phase-P execution",
        "phase-P",
        "approve_phase_p",
        "APPROVE_PHASE_P ",
        "APPROVE_PHASE_D",
        "APPROVE_PHASE_P_AUTHORIZATION",
        True,
    ],
)
def test_only_exact_positive_decision_approves(tmp_path: Path, decision: Any) -> None:
    ep = build(tmp_path)
    _approval(ep, decision=decision)
    with pytest.raises(executor.PhasePError, match="decision"):
        _check(ep)


def test_notes_have_zero_effect(tmp_path: Path) -> None:
    ep = build(tmp_path)
    _approval(ep, notes="I do NOT approve phase-P execution")
    assert _check(ep)["mode"] == "SYNTHETIC"  # decision literal governs; notes are inert
    _approval(ep, notes="APPROVE_PHASE_P", decision="DENY")
    with pytest.raises(executor.PhasePError, match="decision"):
        _check(ep)


def test_empty_or_wrong_digest_approval_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    body = synthetic.approval_body(load_json(ep.auth_path)["digest"])
    body["digest"] = ""
    write_json(ep.approval_path, body)
    with pytest.raises(executor.PhasePError, match="digest"):
        _check(ep)
    _approval(ep, authorization_core_digest="f" * 64)
    with pytest.raises(executor.PhasePError, match="authorization_core_digest"):
        _check(ep)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approved_at_utc", "2026-02-30T00:00:00Z"),
        ("approved_at_utc", "yesterday"),
        ("operator_identity", ""),
        ("operator_identity", "bad\nname"),
        ("phase", "D"),
        ("epoch_id", "other"),
        ("kind", "essential-web-evidence-v3-reviewer-approval"),
    ],
)
def test_approval_fields_strict(tmp_path: Path, field: str, value: Any) -> None:
    ep = build(tmp_path)
    _approval(ep, **{field: value})
    with pytest.raises(executor.PhasePError):
        _check(ep)


def test_approval_unknown_field_refuses(tmp_path: Path) -> None:
    ep = build(tmp_path)
    _approval(ep, approved=True)
    with pytest.raises(executor.PhasePError, match="unknown"):
        _check(ep)


# --------------------------------------------------------------------------
# Trusted objects cannot be fabricated.
# --------------------------------------------------------------------------


def test_trusted_classes_cannot_be_constructed() -> None:
    for cls in (
        authorization.ValidatedPhasePAuthorization,
        authorization.ValidatedOperatorApproval,
        authorization.Expectations,
        genesis.VerifiedRootContext,
        plan.ValidatedPhasePPlan,
    ):
        with pytest.raises(trust.TrustError):
            cls()
        forged = object.__new__(cls)
        with pytest.raises(trust.TrustError, match="not produced by its validator"):
            trust.require_minted(forged, cls)


def test_minted_objects_are_immutable_and_uncopyable(tmp_path: Path) -> None:
    ep = build(tmp_path)
    minted = plan.validate_plan_bytes(ep.plan_m, arm="M", synthetic=True)
    with pytest.raises(trust.TrustError):
        minted.arm = "T"
    with pytest.raises(trust.TrustError):
        copy.copy(minted)
    with pytest.raises(trust.TrustError):
        pickle.dumps(minted)
    with pytest.raises(trust.TrustError):
        trust._mint(plan.ValidatedPhasePPlan, object(), arm="M")


def test_fabricated_mappings_cannot_enter_genesis() -> None:
    fabricated = {"authorization_ok": True, "digest": "0" * 64, "epoch_id": "x", "root": "y"}
    with pytest.raises(trust.TrustError):
        genesis.publish(
            auth=fabricated,  # type: ignore[arg-type]
            approval=fabricated,  # type: ignore[arg-type]
            root_ctx=fabricated,  # type: ignore[arg-type]
            utc_now="2026-09-29T00:00:00Z",
            monotonic_ns=0,
            sampling_interval_s=0.1,
        )


def test_executor_public_api_accepts_no_validation_objects() -> None:
    import inspect

    for fn in (executor.perform_genesis, executor.execute_phase_p, executor.check_authorization):
        params = set(inspect.signature(fn).parameters)
        assert params <= {
            "repo_root",
            "root",
            "authorization_path",
            "approval_path",
            "review_path",
            "harness",
            "max_operations",
        }


def test_real_mode_refuses_non_frozen_root_and_harness_at_frozen_root(tmp_path: Path) -> None:
    ep = build(tmp_path)
    with pytest.raises(executor.PhasePError, match="REAL mode"):
        executor.execute_phase_p(**ep.paths())  # no harness: REAL mode, tmp root refused
    frozen = synthetic.SyntheticEpoch(
        **{**ep.__dict__, "root": Path("G:/Project/xlm-evidence-v3/x")}
    )
    with pytest.raises(executor.PhasePError, match="frozen project root"):
        executor.perform_genesis(**frozen.paths(), harness=frozen.harness())
    assert not Path("G:/Project/xlm-evidence-v3").exists()


def test_only_offline_fake_transport_accepted_in_harness(tmp_path: Path) -> None:
    from xlm.data.evidence_v3 import harness, transport

    ep = build(tmp_path)
    h = ep.harness()

    class Sneaky(synthetic.FakeTransport):
        pass

    for other in (transport._LiveHttpsTransport(), Sneaky(ep.server), object()):
        swapped = harness.SyntheticHarness(**{**h.__dict__, "transport": other})
        with pytest.raises(executor.PhasePError, match="only the offline FakeTransport"):
            executor.perform_genesis(**ep.paths(), harness=swapped)
    assert not ep.root.exists()


def test_live_transport_refuses_requests_not_issued_by_executor() -> None:
    from xlm.data.evidence_v3 import transport

    request = transport._make_request(
        url="https://huggingface.co/datasets/x/y/resolve/" + "0" * 40 + "/f.parquet",
        range_start=0,
        range_end=3,
        deadline_ns=2**62,
        timeout_seconds=1.0,
        max_read_bytes=65537,
        attempt_id="forged",
    )
    with pytest.raises(transport.TransportError, match="not durably issued"):
        transport._LiveHttpsTransport().open(request)


# --------------------------------------------------------------------------
# Code / environment identity from committed Git objects.
# --------------------------------------------------------------------------


def test_committed_blob_identity_uses_git_objects() -> None:
    head = envidentity.resolve_commit(REPO, "HEAD")
    blob = envidentity.committed_blob(REPO, head, "pyproject.toml")
    expected = subprocess.run(
        ["git", "cat-file", "blob", f"{head}:pyproject.toml"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    assert blob == expected
    working = (REPO / "pyproject.toml").read_bytes()
    rep = envidentity.representation(working, blob)
    assert rep in ("exact", "crlf")
    if rep == "crlf":
        assert hashlib.sha256(working).hexdigest() != hashlib.sha256(blob).hexdigest()


def test_representation_refuses_content_drift() -> None:
    with pytest.raises(envidentity.EnvIdentityError):
        envidentity.representation(b"a\r\nb\r\nc", b"a\nb\nd")


def test_runtime_environment_exact_keys_without_importing_torch() -> None:
    config = envidentity.worktree_identity(REPO, envidentity.CONFIG_PATHS)
    env = envidentity.runtime_environment(REPO, config_identity=config)
    assert set(env) == set(envidentity.ENVIRONMENT_KEYS)
    assert env["python_executable_class"] == "project-venv"
    assert env["python_implementation"] == "CPython"
    envidentity.check_environment_schema(env)


# --------------------------------------------------------------------------
# Typed review decision bound to the exact authorization request
# --------------------------------------------------------------------------


def test_review_decision_must_bind_this_request(tmp_path: Path) -> None:
    a = build(tmp_path / "a")
    b = build(tmp_path / "b", m_count=1)
    # b's review was issued for a different request: reusing it for a refuses
    a.review_path.write_bytes(b.review_path.read_bytes())
    body = load_json(a.auth_path)
    body["review_artifact_digest"] = hashlib.sha256(b.review_path.read_bytes()).hexdigest()
    a.auth_path.write_bytes(synthetic.reseal(body))
    write_json(a.approval_path, synthetic.approval_body(load_json(a.auth_path)["digest"]))
    with pytest.raises(executor.PhasePError, match="review request digest"):
        _check(a)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "BLOCKED"),
        ("decision", "APPROVE_PHASE_P"),
        ("phase", "D"),
        ("reviewer_identity", ""),
        ("review_report_sha256", "abc"),
        ("extra", 1),
    ],
)
def test_review_decision_fields_strict(tmp_path: Path, field: str, value: Any) -> None:
    ep = build(tmp_path)
    auth = load_json(ep.auth_path)
    review = synthetic.review_body(authorization.authorization_request_digest(auth))
    review[field] = value
    raw = synthetic.reseal(review)
    ep.review_path.write_bytes(raw)
    auth["review_artifact_digest"] = hashlib.sha256(raw).hexdigest()
    ep.auth_path.write_bytes(synthetic.reseal(auth))
    write_json(ep.approval_path, synthetic.approval_body(load_json(ep.auth_path)["digest"]))
    with pytest.raises(executor.PhasePError, match="review"):
        _check(ep)


def test_synthetic_plan_cannot_name_the_real_source(tmp_path: Path) -> None:
    ep = build(tmp_path)
    body = canonical.loads_bytes_strict(ep.plan_m)
    body["payload"]["source"]["repository"] = frozen_v3.SOURCE_REPOSITORY
    body["payload"]["source"]["revision"] = frozen_v3.SOURCE_REVISION
    body["digest"] = canonical.self_digest(body)
    with pytest.raises(plan.PlanError, match="synthetic source"):
        plan.validate_plan_bytes(canonical.canonical_bytes(body), arm="M", synthetic=True)


_REAL_VALIDATION = r"""
import hashlib, sys
from pathlib import Path
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v3 import authorization, envidentity, frozen_v3, plan, synthetic
repo, scratch = Path(sys.argv[1]), Path(sys.argv[2])
review_path = scratch / "review.json"
root = Path(frozen_v3.EXECUTION_ROOT)
first = authorization.derive_expectations(
    repo=repo, root=root, review_path=review_path, review_bytes=b"{}", harness=None)
children = plan.load_committed_children(repo)
envidentity.check_code_map_schema(dict(children.code_hashes))
body = {
    "kind": authorization.AUTH_KIND, "schema_version": authorization.AUTH_SCHEMA_VERSION,
    "protocol_sha256": frozen_v3.PROTOCOL_SHA256, "freeze_digest": frozen_v3.FREEZE_DIGEST,
    "freeze_commit": frozen_v3.FROZEN_PROTOCOL_COMMIT,
    "implementation_commit": children.implementation_commit,
    "child_manifest_digest": children.manifest_digest, "epoch_id": frozen_v3.EPOCH_ID,
    "execution_root": frozen_v3.EXECUTION_ROOT, "phase": "P", "arms": ["M", "T"],
    "m_phase_p_plan_digest": children.plans["M"].digest,
    "t_phase_p_plan_digest": children.plans["T"].digest,
    "scientific_namespace": frozen_v3.SCIENTIFIC_NAMESPACE,
    "selection_digest": frozen_v3.SELECTION_DIGEST, "source_revision": frozen_v3.SOURCE_REVISION,
    "resource_caps": {"M": frozen_v3.ARM_M_CAPS, "T": frozen_v3.ARM_T_CAPS},
    "resource_caps_digest": frozen_v3.RESOURCE_CAPS_DIGEST,
    "code_hashes": dict(sorted(children.code_hashes.items())),
    "environment_identity": dict(first.environment),
    "reviewer_decision": authorization.REVIEWER_DECISION,
    "review_artifact_digest": "",
    "authorization_scope": authorization.AUTHORIZATION_SCOPE,
    "root_precondition": authorization.ROOT_PRECONDITION,
}
review = synthetic.authorization_review_bytes(body)
review_path.write_bytes(review)
body["review_artifact_digest"] = hashlib.sha256(review).hexdigest()
body["digest"] = canonical.self_digest(body)
expect = authorization.derive_expectations(
    repo=repo, root=root, review_path=review_path, review_bytes=review, harness=None)
try:
    authorization.validate_authorization(canonical.canonical_bytes(body), expect)
    print("UNEXPECTED-ACCEPT")
except authorization.AuthorizationError as exc:
    print("REFUSED-AT:", exc)
assert not root.exists()
"""


def test_actual_child_code_map_passes_actual_real_validator(tmp_path: Path) -> None:
    """Committed child code map + actual runtime pass every REAL binding check.

    Runs in a clean interpreter (REAL mode also requires every loaded ``xlm``
    module to be bound code). The review decision is deliberately NOT
    committed (this task must not fabricate a review), so the only permitted
    refusal is the final "review must be committed in the repository" rule:
    every earlier check - code identity from committed Git blobs, working-tree
    representation, runtime environment, child manifest and plan digests,
    caps - has passed. Nothing is persisted; the frozen root is never touched.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _REAL_VALIDATION, str(REPO), str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert "REFUSED-AT: REAL review artifact must live inside the repository" in completed.stdout
