"""Unit tests for transactional SQLite ledger, state transitions, and disk rebuilding."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.artifacts.ledger import RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.cli.main import app
from xlm.core.contracts import RunStatus
from xlm.core.paths import ArtifactPaths

runner = CliRunner()


def test_ledger_initialization_and_run_state_transitions(tmp_path: Path) -> None:
    """Verify transactional state transitions and invalid state rejection."""
    db_file = tmp_path / "test_ledger.sqlite"
    ledger = RunLedger(db_file)

    run_id = "run_test_01"
    plan_hash = "plan_hash_abcdef123456"

    # 1. Register in DRAFT
    ledger.register_run(run_id=run_id, experiment_id="exp_01", plan_hash=plan_hash)
    rec = ledger.get_run(run_id)
    assert rec is not None
    assert rec["status"] == RunStatus.DRAFT.value

    # 2. Transition DRAFT -> PLANNED
    ledger.transition_run(run_id, from_state=RunStatus.DRAFT, to_state=RunStatus.PLANNED)
    rec2 = ledger.get_run(run_id)
    assert rec2 is not None and rec2["status"] == RunStatus.PLANNED.value

    # 3. Invalid transition DRAFT -> RUNNING should fail
    with pytest.raises(ValueError, match="Illegal run state transition"):
        ledger.transition_run(run_id, from_state=RunStatus.DRAFT, to_state=RunStatus.RUNNING)

    # 4. Authorize with correct plan hash
    ledger.authorize_run(run_id, plan_hash=plan_hash, auth_token="auth_sig_xyz")
    rec_auth = ledger.get_run(run_id)
    assert rec_auth is not None
    assert rec_auth["status"] == RunStatus.AUTHORIZED.value
    assert rec_auth["authorization_token"] == "auth_sig_xyz"

    # 5. Transition AUTHORIZED -> RUNNING
    ledger.transition_run(run_id, from_state=RunStatus.AUTHORIZED, to_state=RunStatus.RUNNING)
    rec_run = ledger.get_run(run_id)
    assert rec_run is not None and rec_run["status"] == RunStatus.RUNNING.value

    # 6. Transition RUNNING -> SUCCEEDED
    ledger.transition_run(run_id, from_state=RunStatus.RUNNING, to_state=RunStatus.SUCCEEDED)
    rec_succ = ledger.get_run(run_id)
    assert rec_succ is not None and rec_succ["status"] == RunStatus.SUCCEEDED.value


def test_stale_authorization_rejection(tmp_path: Path) -> None:
    """Verify authorization fails if plan hash does not match."""
    db_file = tmp_path / "test_ledger.sqlite"
    ledger = RunLedger(db_file)

    run_id = "run_stale_test"
    ledger.register_run(run_id, "exp_stale", plan_hash="actual_hash_123")
    ledger.transition_run(run_id, from_state=RunStatus.DRAFT, to_state=RunStatus.PLANNED)

    with pytest.raises(ValueError, match="Stale authorization rejected"):
        ledger.authorize_run(run_id, plan_hash="stale_hash_456", auth_token="token")


def test_rebuild_from_filesystem(tmp_path: Path) -> None:
    """Verify catalog and durable runs are reconstructed from filesystem without hallucinating."""
    paths = ArtifactPaths(root=tmp_path / "xlm_root")
    paths.ensure_directories()
    store = ArtifactStore(paths)

    # 1. Publish two real artifacts
    store.publish_artifact(
        artifact_id="pub_art_1",
        kind="clean",
        files={"doc.txt": "data"},
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="cfg_1234",
    )
    store.publish_artifact(
        artifact_id="pub_art_2",
        kind="shards",
        files={"shards.bin": b"123"},
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="cfg_1234",
    )

    # 2. Write a durable run record
    run_dir = paths.runs / "run_durable_01"
    run_dir.mkdir(parents=True, exist_ok=True)
    durable_record = {
        "run_id": "run_durable_01",
        "experiment_id": "exp_durable",
        "status": "SUCCEEDED",
        "plan_hash": "plan_hash_999",
        "authorization_token": "token_999",
        "created_at": "2026-09-18T10:00:00Z",
        "updated_at": "2026-09-18T11:00:00Z",
    }
    (run_dir / "run_record.json").write_text(json.dumps(durable_record), encoding="utf-8")

    # 3. Create a fresh empty database
    new_db = paths.ledger / "fresh_ledger.sqlite"
    ledger = RunLedger(new_db)

    # Before rebuild
    assert ledger.get_artifact("pub_art_1") is None
    assert ledger.get_run("run_durable_01") is None

    # Rebuild
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 2
    assert results["rebuilt_runs"] == 1
    assert len(results["corrupt_artifacts"]) == 0

    # After rebuild
    art1 = ledger.get_artifact("pub_art_1")
    assert art1 is not None
    assert art1["kind"] == "clean"

    run_rec = ledger.get_run("run_durable_01")
    assert run_rec is not None
    assert run_rec["status"] == "SUCCEEDED"


def test_cli_rebuild_ledger_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI artifact rebuild-ledger runs cleanly."""
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "xlm_home"))
    res = runner.invoke(app, ["artifact", "rebuild-ledger"])
    assert res.exit_code == 0
    assert "Ledger rebuild complete:" in res.output


def test_catalog_collision_cannot_replace_existing_kind_or_path(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    original = tmp_path / "clean" / "shared"
    ledger.record_artifact("shared", "clean", original, "{}")
    before = ledger.get_artifact("shared")
    for kind, path in (("other_kind", original), ("clean", tmp_path / "different")):
        with pytest.raises(ValueError, match="catalog identity conflict"):
            ledger.record_artifact("shared", kind, path, "{}")
        assert ledger.get_artifact("shared") == before
