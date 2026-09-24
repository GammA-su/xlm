"""Artifact/checkpoint durability: ordering, fsync observability, crash states.

Every test runs against the real filesystem in an isolated tmp directory.
Faults are injected through narrow wrappers (never by mocking away
publication); recovery assertions use the real `verify_artifact`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from xlm.artifacts import store as store_module
from xlm.artifacts.store import (
    ArtifactStore,
    DurabilityError,
    directory_sync_supported,
    sync_directory,
)
from xlm.core.paths import ArtifactPaths


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(ArtifactPaths(root=tmp_path / "artifacts"))


def _publish(
    store: ArtifactStore,
    files: dict | None = None,
    artifact_id: str = "dur_001",
) -> Path:
    return store.publish_artifact(
        artifact_id=artifact_id,
        kind="checkpoints",
        files=files
        if files is not None
        else {"model.pt": b"weights" * 100, "meta.json": b'{"step": 1}'},
        producer_code_hash="c" * 16,
        dependency_hash="d" * 16,
        resolved_config_hash="r" * 16,
    )


def test_fsync_covers_every_payload_manifest_and_marker(tmp_path: Path) -> None:
    """Path-level spy: each payload, the manifest, and the marker are synced."""
    store = _store(tmp_path)
    synced_paths: list[str] = []
    real_fsync_fileobj = store_module.fsync_fileobj
    real_write_durable = store_module.write_durable_bytes

    def spy_fsync(fileobj: object) -> None:
        synced_paths.append(str(getattr(fileobj, "name", "?")))
        real_fsync_fileobj(fileobj)

    def spy_write(path: Path, data: bytes) -> None:
        synced_paths.append(f"write:{path.name}")
        real_write_durable(path, data)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(store_module, "fsync_fileobj", spy_fsync)
    monkeypatch.setattr(store_module, "write_durable_bytes", spy_write)
    try:
        art_dir = _publish(store)
    finally:
        monkeypatch.undo()
    names = " ".join(synced_paths)
    assert "model.pt" in names
    assert "meta.json" in names
    assert "write:manifest.json" in names
    assert "write:_COMPLETED" in names
    store.verify_artifact(art_dir)


def test_os_fsync_syscall_actually_runs(tmp_path: Path) -> None:
    """Syscall-level counter: publication performs real fsync calls."""
    store = _store(tmp_path)
    calls: list[int] = []
    real_fsync = os.fsync

    def counting(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "fsync", counting)
    try:
        art_dir = _publish(store)
    finally:
        monkeypatch.undo()
    # Two payloads + manifest + marker, plus zero or more directory syncs.
    assert len(calls) >= 4, f"expected payload/manifest/marker fsyncs, got {len(calls)}"
    store.verify_artifact(art_dir)


def test_directory_sync_capability_matches_platform() -> None:
    assert directory_sync_supported() == (os.name != "nt")
    if os.name == "nt":
        assert sync_directory(Path(".")) is False
    else:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            assert sync_directory(Path(tmp)) is True


def test_durability_record_is_identity_neutral(tmp_path: Path) -> None:
    """The durability record rides in cosmetic metadata: verified, unhashed."""
    store = _store(tmp_path)
    art_dir = _publish(store)
    manifest = store.verify_artifact(art_dir)
    record = manifest.cosmetic_metadata.get("durability")
    assert record is not None
    assert record["file_sync"] == "fsync"
    assert record["directory_sync"] == (os.name != "nt")
    assert record["platform"] == os.name
    # Identity digests exclude cosmetic metadata by construction.
    manifest.check_identity()


def test_failure_during_payload_write_publishes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        store_module.ArtifactStore,
        "_chunks",
        staticmethod(lambda content: (_ for _ in ()).throw(ValueError("injected write fault"))),
    )
    try:
        with pytest.raises(ValueError, match="injected write fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    assert not (tmp_path / "artifacts" / "checkpoints" / "dur_001").exists()
    assert list((tmp_path / "artifacts" / ".staging").glob("*")) == []


def test_failure_before_payload_fsync_publishes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        store_module,
        "fsync_fileobj",
        lambda fileobj: (_ for _ in ()).throw(OSError("injected pre-fsync fault")),
    )
    try:
        with pytest.raises(OSError, match="injected pre-fsync fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    assert not (tmp_path / "artifacts" / "checkpoints" / "dur_001").exists()


def test_failure_during_manifest_publishes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_write = store_module.write_durable_bytes

    def faulting_write(path: Path, data: bytes) -> None:
        if path.name == "manifest.json":
            raise ValueError("injected manifest fault")
        real_write(path, data)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(store_module, "write_durable_bytes", faulting_write)
    try:
        with pytest.raises(ValueError, match="injected manifest fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    dest = tmp_path / "artifacts" / "checkpoints" / "dur_001"
    assert not dest.exists()
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        store.verify_artifact(dest)


def test_failure_before_marker_publishes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_write = store_module.write_durable_bytes

    def faulting_write(path: Path, data: bytes) -> None:
        if path.name == "_COMPLETED":
            raise ValueError("injected marker fault")
        real_write(path, data)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(store_module, "write_durable_bytes", faulting_write)
    try:
        with pytest.raises(ValueError, match="injected marker fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    dest = tmp_path / "artifacts" / "checkpoints" / "dur_001"
    assert not dest.exists()


def test_failure_before_rename_publishes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        Path,
        "rename",
        lambda self, target: (_ for _ in ()).throw(OSError("injected rename fault")),
    )
    try:
        with pytest.raises(OSError, match="injected rename fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    dest = tmp_path / "artifacts" / "checkpoints" / "dur_001"
    assert not dest.exists()
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        store.verify_artifact(dest)


def test_failure_after_rename_is_still_safe(tmp_path: Path) -> None:
    """A crash after the rename leaves either nothing or a fully valid artifact."""
    store = _store(tmp_path)
    real_sync = store_module.sync_directory
    calls = {"count": 0}

    def faulting_sync(path: Path) -> bool:
        calls["count"] += 1
        # Fail the final parent sync that follows the rename.
        if calls["count"] >= 3:
            raise DurabilityError("injected post-rename fault")
        return real_sync(path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(store_module, "sync_directory", faulting_sync)
    try:
        with pytest.raises(DurabilityError, match="injected post-rename fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    dest = tmp_path / "artifacts" / "checkpoints" / "dur_001"
    # Either the rename did not survive (absent) or the artifact verifies
    # completely. Present-but-invalid is the forbidden state.
    if dest.exists():
        store.verify_artifact(dest)


def test_directory_sync_failure_aborts_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        store_module,
        "sync_directory",
        lambda path: (_ for _ in ()).throw(DurabilityError("injected dir-sync fault")),
    )
    try:
        with pytest.raises(DurabilityError, match="injected dir-sync fault"):
            _publish(store)
    finally:
        monkeypatch.undo()
    assert not (tmp_path / "artifacts" / "checkpoints" / "dur_001").exists()


def test_torn_states_never_verify(tmp_path: Path) -> None:
    """Hand-built crash states: marker-only, manifest-only, torn bytes."""
    store = _store(tmp_path)
    art_dir = _publish(store, artifact_id="torn_base")

    marker_only = tmp_path / "marker_only"
    marker_only.mkdir()
    (marker_only / "_COMPLETED").write_text("COMPLETED\n", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        store.verify_artifact(marker_only)

    manifest_only = tmp_path / "manifest_only"
    manifest_only.mkdir()
    (manifest_only / "manifest.json").write_bytes((art_dir / "manifest.json").read_bytes())
    with pytest.raises(ValueError, match="missing '_COMPLETED'"):
        store.verify_artifact(manifest_only)

    torn_manifest = tmp_path / "torn_manifest"
    torn_manifest.mkdir()
    (torn_manifest / "_COMPLETED").write_text("COMPLETED\n", encoding="utf-8")
    (torn_manifest / "manifest.json").write_bytes(b'{"schema_version": 2, "truncated')
    with pytest.raises((ValueError, FileNotFoundError)):
        store.verify_artifact(torn_manifest)

    truncated_payload = tmp_path / "truncated_payload"
    truncated_payload.mkdir()
    (truncated_payload / "_COMPLETED").write_text("COMPLETED\n", encoding="utf-8")
    (truncated_payload / "manifest.json").write_bytes((art_dir / "manifest.json").read_bytes())
    (truncated_payload / "model.pt").write_bytes(b"short")
    with pytest.raises(ValueError, match="[Ss]ize mismatch|checksum|Corrupt"):
        store.verify_artifact(truncated_payload)


def test_legacy_manifest_without_durability_still_verifies(tmp_path: Path) -> None:
    """Historical artifacts (no durability key) remain readable."""
    store = _store(tmp_path)
    art_dir = _publish(store, artifact_id="legacy_compat")
    manifest_path = art_dir / "manifest.json"
    data = json.loads(manifest_path.read_bytes().decode("utf-8"))
    data["cosmetic_metadata"].pop("durability", None)
    manifest_path.write_bytes(json.dumps(data).encode("utf-8"))
    manifest = store.verify_artifact(art_dir)
    assert manifest.artifact_id == "legacy_compat"
    manifest.check_identity()


def test_nested_payload_subdirectories_are_durable(tmp_path: Path) -> None:
    """Payloads in nested dirs sync their own directory entries too."""
    store = _store(tmp_path)
    synced_dirs: list[str] = []
    real_sync = store_module.sync_directory

    def spy_sync(path: Path) -> bool:
        synced_dirs.append(path.name)
        return real_sync(path)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(store_module, "sync_directory", spy_sync)
    try:
        art_dir = _publish(
            store,
            files={"nested/deep/payload.bin": b"x" * 4096, "top.json": b"{}"},
            artifact_id="nested_001",
        )
    finally:
        monkeypatch.undo()
    assert "deep" in synced_dirs and "nested" in synced_dirs
    store.verify_artifact(art_dir)


def test_checkpoint_publish_fsyncs_and_verifies(tmp_path: Path) -> None:
    """A real CheckpointManager publish fsyncs payloads and verifies clean."""
    pytest.importorskip("torch")
    from xlm.artifacts.ledger import RunLedger
    from xlm.config.schemas import TransformerBaselineConfig
    from xlm.core.paths import ArtifactPaths as _Paths
    from xlm.models.transformer import TransformerBaseline
    from xlm.training.checkpoint import CheckpointManager

    paths = _Paths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)
    ledger = RunLedger(paths.ledger / "ledger.sqlite")
    manager = CheckpointManager(artifact_store=store, run_ledger=ledger, paths=paths)
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
    calls: list[int] = []
    real_fsync = os.fsync

    def counting(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "fsync", counting)
    try:
        chk_dir = manager.save_checkpoint(
            checkpoint_id="chk_durable",
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
    finally:
        monkeypatch.undo()
    assert (chk_dir / "_COMPLETED").is_file()
    manifest = store.verify_artifact(chk_dir)
    assert manifest.cosmetic_metadata["durability"]["file_sync"] == "fsync"
    payloads = [f for f in manifest.files if f.path.endswith(".pt")]
    assert payloads, "checkpoint must carry weight payloads"
    # Every weight/config payload plus manifest plus marker, at minimum.
    assert len(calls) >= len(manifest.files) + 2
