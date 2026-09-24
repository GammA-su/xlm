"""ArtifactStore <-> ledger crash reconciliation: deterministic crash states.

Every test uses an isolated store root. The crash window (valid artifact in
the store, no ledger row) is produced by publishing without recording —
exactly the observable state of a crash between the two calls.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from xlm.artifacts.ledger import STATUS_UNVERIFIABLE, RunLedger
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths


def _setup(tmp_path: Path) -> tuple[ArtifactPaths, ArtifactStore, RunLedger]:
    paths = ArtifactPaths(root=tmp_path / "xlm_root")
    paths.ensure_directories()
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    return paths, store, ledger


def _publish(store: ArtifactStore, artifact_id: str, payload: bytes = b"payload-bytes") -> Path:
    return store.publish_artifact(
        artifact_id=artifact_id,
        kind="checkpoints",
        files={"model.pt": payload, "meta.json": b'{"step": 1}'},
        producer_code_hash="c" * 16,
        dependency_hash="d" * 16,
        resolved_config_hash="r" * 16,
    )


def test_a_crash_before_record_reconciles_and_reuses(tmp_path: Path) -> None:
    """A: valid unknown artifact -> recorded; then reusable via the ledger."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "crash_001")
    assert ledger.get_artifact("crash_001") is None
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 1
    assert results["already_recorded"] == 0
    row = ledger.get_artifact("crash_001")
    assert row is not None and row["status"] == "completed"
    # Reusable: the recorded manifest verifies the bytes on disk.
    manifest = store.verify_artifact(Path(row["path"]))
    assert manifest.artifact_id == "crash_001"
    assert art_dir.resolve() == Path(row["path"]).resolve()


def test_b_crash_before_completed_never_reconciles(tmp_path: Path) -> None:
    """B: payloads without any completion claim stay unknown."""
    paths, store, ledger = _setup(tmp_path)
    stray = paths.root / "checkpoints" / "half_written"
    stray.mkdir(parents=True)
    (stray / "model.pt").write_bytes(b"partial-bytes")
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 0
    assert ledger.get_artifact("half_written") is None


def test_c_manifest_without_marker_never_reconciles(tmp_path: Path) -> None:
    """C: manifest present but marker absent is incomplete, not corrupt."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "torn_001")
    (art_dir / "_COMPLETED").unlink()
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 0
    assert results["corrupt_artifacts"] == []
    assert any("torn_001" in item for item in results["incomplete_artifacts"])
    assert ledger.get_artifact("torn_001") is None


def test_c2_marker_without_manifest_never_reconciles(tmp_path: Path) -> None:
    """C (mirror): a lone completion marker proves nothing by itself."""
    paths, store, ledger = _setup(tmp_path)
    lonely = paths.root / "checkpoints" / "lonely_marker"
    lonely.mkdir(parents=True)
    (lonely / "_COMPLETED").write_text("COMPLETED\n", encoding="utf-8")
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 0
    assert any("lonely_marker" in item for item in results["incomplete_artifacts"])
    assert ledger.get_artifact("lonely_marker") is None


def test_d_corrupt_payload_never_reconciles(tmp_path: Path) -> None:
    """D: marker + manifest but tampered payload is refused and reported."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "tampered_001")
    with (art_dir / "model.pt").open("r+b") as stream:
        stream.write(b"XXXX")
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 0
    assert any("tampered_001" in item for item in results["corrupt_artifacts"])
    assert ledger.get_artifact("tampered_001") is None


def test_e_existing_record_is_a_true_noop(tmp_path: Path) -> None:
    """E: reconciling a recorded artifact changes nothing, not even timestamps."""
    paths, store, ledger = _setup(tmp_path)
    _publish(store, "stable_001")
    first = ledger.rebuild_from_filesystem(paths, store)
    assert first["rebuilt_artifacts"] == 1
    before = ledger.get_artifact("stable_001")
    assert before is not None
    second = ledger.rebuild_from_filesystem(paths, store)
    assert second["rebuilt_artifacts"] == 0
    assert second["already_recorded"] == 1
    assert ledger.get_artifact("stable_001") == before


def test_f_missing_artifact_marks_reference_unusable(tmp_path: Path) -> None:
    """F: ledger row without store bytes fails closed; identity preserved."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "doomed_001")
    manifest_json = store.load_manifest(art_dir).model_dump_json()
    ledger.record_artifact(
        artifact_id="doomed_001",
        kind="checkpoints",
        path=art_dir,
        manifest_json=manifest_json,
    )
    import shutil

    shutil.rmtree(art_dir)
    audit = ledger.audit_ledger_references(paths, store)
    assert audit["unusable"] == 1
    assert audit["ok"] == 0
    row = ledger.get_artifact("doomed_001")
    assert row is not None
    assert row["status"] == STATUS_UNVERIFIABLE
    assert row["manifest_json"] == manifest_json
    assert row["kind"] == "checkpoints"
    # Stable on repeat: no churn, no resurrection.
    again = ledger.audit_ledger_references(paths, store)
    assert again["unusable"] == 1
    assert ledger.get_artifact("doomed_001") == row


def test_f_corrupt_artifact_marks_reference_unusable(tmp_path: Path) -> None:
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "rotten_001")
    manifest_json = store.load_manifest(art_dir).model_dump_json()
    ledger.record_artifact(
        artifact_id="rotten_001",
        kind="checkpoints",
        path=art_dir,
        manifest_json=manifest_json,
    )
    (art_dir / "model.pt").write_bytes(b"not-the-published-bytes")
    audit = ledger.audit_ledger_references(paths, store)
    assert audit["unusable"] == 1
    row = ledger.get_artifact("rotten_001")
    assert row is not None and row["status"] == STATUS_UNVERIFIABLE


def test_audit_heals_repaired_reference(tmp_path: Path) -> None:
    """A reference that verifies again returns to completed."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "heals_001")
    manifest_json = store.load_manifest(art_dir).model_dump_json()
    ledger.record_artifact(
        artifact_id="heals_001",
        kind="checkpoints",
        path=art_dir,
        manifest_json=manifest_json,
        status=STATUS_UNVERIFIABLE,
    )
    audit = ledger.audit_ledger_references(paths, store)
    assert audit["healed"] == 1
    row = ledger.get_artifact("heals_001")
    assert row is not None and row["status"] == "completed"


def test_g_repeated_reconciliation_is_stable(tmp_path: Path) -> None:
    """G: three passes, one row, identical bytes every time."""
    paths, store, ledger = _setup(tmp_path)
    _publish(store, "steady_001")
    snapshots = []
    for _ in range(3):
        results = ledger.rebuild_from_filesystem(paths, store)
        snapshots.append(ledger.get_artifact("steady_001"))
    assert [s == snapshots[0] for s in snapshots] == [True, True, True]
    assert results["rebuilt_artifacts"] == 0
    assert results["already_recorded"] == 1


def test_h_multiple_unknown_artifacts_reconcile_deterministically(tmp_path: Path) -> None:
    """H: many unknowns across kinds, all recorded, reports deterministic."""
    paths, store, ledger = _setup(tmp_path)
    for kind in ("checkpoints", "clean"):
        for i in range(12):
            store.publish_artifact(
                artifact_id=f"bulk_{kind}_{i:02d}",
                kind=kind,
                files={"data.bin": bytes([i]) * 64},
                producer_code_hash="c" * 16,
                dependency_hash="d" * 16,
                resolved_config_hash="r" * 16,
            )
    first = ledger.rebuild_from_filesystem(paths, store)
    second = ledger.rebuild_from_filesystem(paths, store)
    assert first["rebuilt_artifacts"] == 24
    assert second["rebuilt_artifacts"] == 0
    assert second["already_recorded"] == 24
    assert first["corrupt_artifacts"] == second["corrupt_artifacts"] == []
    for kind in ("checkpoints", "clean"):
        for i in range(12):
            assert ledger.get_artifact(f"bulk_{kind}_{i:02d}") is not None


def test_identity_conflict_is_reported_not_papered(tmp_path: Path) -> None:
    """Same ID recorded elsewhere: conflict surfaces, disk truth wins nothing."""
    paths, store, ledger = _setup(tmp_path)
    art_dir = _publish(store, "clash_001")
    manifest_json = store.load_manifest(art_dir).model_dump_json()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ledger.record_artifact(
        artifact_id="clash_001",
        kind="checkpoints",
        path=elsewhere,
        manifest_json=manifest_json,
    )
    results = ledger.rebuild_from_filesystem(paths, store)
    assert results["rebuilt_artifacts"] == 0
    assert any("clash_001" in item for item in results["conflicts"])
    row = ledger.get_artifact("clash_001")
    assert row is not None and Path(row["path"]).resolve() == elsewhere.resolve()


def test_bounds_truncate_honestly(tmp_path: Path) -> None:
    """Count, byte, and deadline caps stop the scan with a reason."""
    paths, store, ledger = _setup(tmp_path)
    for i in range(10):
        _publish(store, f"bounded_{i:02d}")
    capped = ledger.rebuild_from_filesystem(paths, store, max_artifacts=3)
    assert capped["truncated"] is True
    assert capped["truncated_reason"] == "artifact count cap reached"
    assert capped["examined_artifacts"] == 3
    tiny = ledger.rebuild_from_filesystem(paths, store, max_verify_bytes=10)
    assert tiny["truncated"] is True
    assert tiny["truncated_reason"] == "verify byte cap reached"
    assert tiny["examined_artifacts"] == 0
    # Full pass afterwards converges completely.
    full = ledger.rebuild_from_filesystem(paths, store)
    assert full["truncated"] is False
    assert full["rebuilt_artifacts"] + full["already_recorded"] == 10
    with pytest.raises(ValueError, match="positive"):
        ledger.rebuild_from_filesystem(paths, store, max_artifacts=0)
    with pytest.raises(ValueError, match="positive"):
        ledger.audit_ledger_references(paths, store, deadline_seconds=0)


def test_concurrent_reconciliation_converges(tmp_path: Path) -> None:
    """Two recovery processes: one inserts, the other observes; no duplicates."""
    paths, store, ledger = _setup(tmp_path)
    for i in range(8):
        _publish(store, f"race_{i:02d}")
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []

    def worker() -> None:
        barrier.wait(timeout=30)
        outcomes.append(ledger.rebuild_from_filesystem(paths, store))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert len(outcomes) == 2
    total_rebuilt = sum(o["rebuilt_artifacts"] for o in outcomes)
    assert total_rebuilt == 8, outcomes
    for i in range(8):
        row = ledger.get_artifact(f"race_{i:02d}")
        assert row is not None
    # Converged: a third pass is a pure no-op.
    final = ledger.rebuild_from_filesystem(paths, store)
    assert final["rebuilt_artifacts"] == 0
    assert final["already_recorded"] == 8


def test_checkpoint_crash_before_record_recovers(tmp_path: Path) -> None:
    """Real CheckpointManager publish with the ledger write omitted (the
    exact crash window), then reconcile, then discover and reload."""
    pytest.importorskip("torch")
    from xlm.artifacts.ledger import RunLedger as _Ledger
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.core.paths import ArtifactPaths as _Paths
    from xlm.models.transformer import TransformerBaseline
    from xlm.training.checkpoint import CheckpointManager

    root = tmp_path / "ckpt_root"
    cpaths = _Paths(root=root)
    cpaths.ensure_directories()
    cstore = ArtifactStore(cpaths)
    cledger = _Ledger(cpaths.ledger / "ledger.sqlite")
    manager = CheckpointManager(artifact_store=cstore, run_ledger=cledger, paths=cpaths)
    config = TransformerBaselineConfig(
        architecture="transformer_baseline",
        vocab_size=32,
        num_layers=1,
        hidden_size=16,
        num_attention_heads=2,
        intermediate_size=32,
        context_length=8,
        attention_backend="eager",
        tie_embeddings=True,
    )
    model = TransformerBaseline(config, seed=1)
    recorded: list[str] = []

    def dropped_record(*args: object, **kwargs: object) -> None:
        recorded.append("dropped")

    import unittest.mock as mock

    with mock.patch.object(cledger, "record_artifact", dropped_record):
        chk_dir = manager.save_checkpoint(
            checkpoint_id="chk_crash",
            run_id="run_001",
            step=1,
            committed_valid_targets=0,
            processed_valid_targets=0,
            plan_id="plan_001_id",
            model=model,
            objective=None,
            optimizer=None,
            optimizer_manifest=None,
            schedule=None,
            batcher=None,
        )
    assert recorded == ["dropped"]
    assert cledger.get_artifact("chk_crash") is None
    results = cledger.rebuild_from_filesystem(cpaths, cstore)
    assert results["rebuilt_artifacts"] == 1
    row = cledger.get_artifact("chk_crash")
    assert row is not None and row["status"] == "completed"
    # Discovery and reload succeed off the reconciled record.
    manifest = cstore.verify_artifact(Path(row["path"]))
    assert manifest.artifact_id == "chk_crash"
    assert chk_dir.resolve() == Path(row["path"]).resolve()
    fresh = TransformerBaseline(config, seed=999)
    manager.load_checkpoint(chk_dir, model=fresh)
    assert fresh is not None


def test_reconcile_benchmark_timings(tmp_path: Path, capsys: object) -> None:
    """Benchmark 1/100/1000 tiny artifacts: scan/verify/ledger phases reported."""
    import time as _time

    for total in (1, 100, 1000):
        paths = ArtifactPaths(root=tmp_path / f"bench_{total}")
        paths.ensure_directories()
        store = ArtifactStore(paths)
        for i in range(total):
            store.publish_artifact(
                artifact_id=f"bench_{i:04d}",
                kind="clean",
                files={"tiny.txt": b"x"},
                producer_code_hash="c" * 16,
                dependency_hash="d" * 16,
                resolved_config_hash="r" * 16,
            )
        fresh = RunLedger(paths.ledger / "bench.sqlite")
        started = _time.monotonic()
        results = fresh.rebuild_from_filesystem(paths, store)
        wall = _time.monotonic() - started
        assert results["rebuilt_artifacts"] == total
        print(
            f"reconcile-{total}: wall={wall:.2f}s "
            f"scan={results['scan_seconds']:.2f}s "
            f"verify={results['verify_seconds']:.2f}s "
            f"ledger={results['ledger_seconds']:.2f}s"
        )
    capsys.readouterr()


def test_cli_rebuild_ledger_bounds_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery caller path: bounded rebuild plus reference audit via CLI."""
    from typer.testing import CliRunner as _Runner

    from xlm.cli.main import app as _app
    from xlm.core.paths import ArtifactPaths as _Paths

    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    paths = _Paths(root=tmp_path / "home")
    store = ArtifactStore(paths)
    for i in range(3):
        store.publish_artifact(
            artifact_id=f"cli_{i:02d}",
            kind="clean",
            files={"tiny.txt": b"x"},
            producer_code_hash="c" * 16,
            dependency_hash="d" * 16,
            resolved_config_hash="r" * 16,
        )
    runner = _Runner()
    result = runner.invoke(
        _app,
        [
            "artifact",
            "rebuild-ledger",
            "--max-artifacts",
            "10",
            "--max-verify-mib",
            "100",
            "--deadline-seconds",
            "300",
            "--audit-references",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Ledger rebuild complete:" in result.output
    assert "Already recorded:" in result.output
    assert "Reference audit:" in result.output
    second = runner.invoke(_app, ["artifact", "rebuild-ledger"])
    assert second.exit_code == 0, second.output
