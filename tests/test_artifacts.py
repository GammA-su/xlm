"""Unit tests for immutable artifact publication, checksum verification, and locking."""

import concurrent.futures
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.artifacts.store import ArtifactStore
from xlm.cli.main import app
from xlm.core.paths import ArtifactPaths

runner = CliRunner()


def test_atomic_publication_and_verification(tmp_path: Path) -> None:
    """Verify artifact is published atomically with manifest and completion marker."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    files: dict[str, bytes | str] = {
        "text_pool.parquet": b"mock parquet table content",
        "metadata.json": '{"documents": 100}',
    }

    art_dir = store.publish_artifact(
        artifact_id="pool_001",
        kind="clean",
        files=files,
        producer_code_hash="c" * 16,
        dependency_hash="d" * 16,
        resolved_config_hash="r" * 16,
    )

    assert art_dir.is_dir()
    assert (art_dir / "_COMPLETED").is_file()
    assert (art_dir / "manifest.json").is_file()
    assert (art_dir / "text_pool.parquet").read_bytes() == b"mock parquet table content"

    # Verify via store
    manifest = store.verify_artifact(art_dir)
    assert manifest.artifact_id == "pool_001"
    assert len(manifest.files) == 2


def test_idempotent_publication_and_conflict_rejection(tmp_path: Path) -> None:
    """Verify identical publication succeeds idempotently; conflicting content fails."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    files = {"weights.bin": b"12345"}
    dir1 = store.publish_artifact(
        artifact_id="art_idempotent",
        kind="checkpoints",
        files=files,
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="conf_1234",
    )

    # Identical publication attempt -> idempotent success
    dir2 = store.publish_artifact(
        artifact_id="art_idempotent",
        kind="checkpoints",
        files=files,
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="conf_1234",
    )
    assert dir1 == dir2

    # Conflicting publication attempt with different config/content -> ValueError
    with pytest.raises(ValueError, match="Artifact conflict"):
        store.publish_artifact(
            artifact_id="art_idempotent",
            kind="checkpoints",
            files={"weights.bin": b"DIFFERENT_PAYLOAD"},
            producer_code_hash="code_1234",
            dependency_hash="dep_1234",
            resolved_config_hash="DIFFERENT_CONFIG_HASH",
        )


def test_corrupt_artifact_rejection(tmp_path: Path) -> None:
    """Verify corrupted artifact files fail checksum verification."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    art_dir = store.publish_artifact(
        artifact_id="corrupt_test",
        kind="clean",
        files={"data.bin": b"original bytes"},
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="conf_1234",
    )

    # Tamper with file
    (art_dir / "data.bin").write_bytes(b"tampered bytes")

    with pytest.raises(ValueError, match="Corrupt artifact: file 'data.bin' checksum mismatch"):
        store.verify_artifact(art_dir)


def test_interrupted_artifact_rejection(tmp_path: Path) -> None:
    """Verify an artifact missing the '_COMPLETED' marker cannot be verified."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    art_dir = store.publish_artifact(
        artifact_id="interrupted_test",
        kind="clean",
        files={"data.bin": b"some bytes"},
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="conf_1234",
    )

    # Remove completion marker
    (art_dir / "_COMPLETED").unlink()

    with pytest.raises(ValueError, match="missing '_COMPLETED'"):
        store.verify_artifact(art_dir)


def test_path_traversal_rejection(tmp_path: Path) -> None:
    """Verify malicious relative paths in payload entries are rejected."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    with pytest.raises(ValueError, match="Path traversal detected"):
        store.publish_artifact(
            artifact_id="traversal_test",
            kind="clean",
            files={"../../escape.txt": b"evil"},
            producer_code_hash="code_1234",
            dependency_hash="dep_1234",
            resolved_config_hash="conf_1234",
        )


def test_concurrent_writer_lock_contention(tmp_path: Path) -> None:
    """Verify two concurrent writers for the same artifact ID synchronize without corruption."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    def writer_task(writer_id: int) -> Path:
        return store.publish_artifact(
            artifact_id="concurrent_art",
            kind="shards",
            files={"tokens.bin": b"same tokens across both"},
            producer_code_hash="code_common",
            dependency_hash="dep_common",
            resolved_config_hash="config_common",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(writer_task, 1)
        f2 = executor.submit(writer_task, 2)
        r1 = f1.result()
        r2 = f2.result()

    assert r1 == r2
    assert store.verify_artifact(r1)


def test_artifact_lineage_round_trip(tmp_path: Path) -> None:
    """Verify lineage tracking from parent artifact to child artifact."""
    paths = ArtifactPaths(root=tmp_path / "artifacts")
    store = ArtifactStore(paths)

    # 1. Publish parent (clean pool)
    _ = store.publish_artifact(
        artifact_id="parent_pool_01",
        kind="clean",
        files={"pool.txt": "canonical documents"},
        producer_code_hash="code_parent",
        dependency_hash="dep_parent",
        resolved_config_hash="cfg_parent",
    )

    # 2. Publish child (token shards) referencing parent
    child_dir = store.publish_artifact(
        artifact_id="child_shards_01",
        kind="shards",
        files={"shards.bin": b"token ids"},
        producer_code_hash="code_child",
        dependency_hash="dep_child",
        resolved_config_hash="cfg_child",
        input_artifact_ids=["parent_pool_01"],
    )

    child_manifest = store.load_manifest(child_dir)
    assert child_manifest.input_artifact_ids == ["parent_pool_01"]


def test_cli_artifact_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI artifact inspect and verify work without PyTorch."""
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "xlm_home"))
    paths = ArtifactPaths(root=tmp_path / "xlm_home")
    store = ArtifactStore(paths)

    art_dir = store.publish_artifact(
        artifact_id="cli_art_01",
        kind="clean",
        files={"test.txt": "content"},
        producer_code_hash="code_1234",
        dependency_hash="dep_1234",
        resolved_config_hash="cfg_1234",
    )

    # Inspect text
    res_insp = runner.invoke(app, ["artifact", "inspect", str(art_dir)])
    assert res_insp.exit_code == 0
    assert "cli_art_01" in res_insp.output

    # Inspect JSON
    res_json = runner.invoke(app, ["artifact", "inspect", str(art_dir), "--json"])
    assert res_json.exit_code == 0
    assert '"artifact_id": "cli_art_01"' in res_json.output

    # Verify
    res_ver = runner.invoke(app, ["artifact", "verify", str(art_dir)])
    assert res_ver.exit_code == 0
    assert "Verification SUCCESS" in res_ver.output


def test_artifact_lookup_by_id_covers_newly_introduced_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving an artifact by ID must work for every kind present in the store.

    The resolver previously scanned a hardcoded kind list, so artifacts published
    under later kinds (raw_dataset, probe_evidence, clean_dataset) were published
    successfully but could not be inspected or verified by ID.
    """
    root = tmp_path / "artifacts"
    monkeypatch.setenv("XLM_HOME", str(root))
    store = ArtifactStore(ArtifactPaths(root=root))

    for kind in ("clean_dataset", "raw_dataset", "probe_evidence"):
        artifact_id = f"lookup_{kind}"
        store.publish_artifact(
            artifact_id=artifact_id,
            kind=kind,
            files={"payload.txt": "content"},
            producer_code_hash="code_1234",
            dependency_hash="dep_1234",
            resolved_config_hash="cfg_1234",
        )

        result = runner.invoke(app, ["artifact", "verify", artifact_id])
        assert result.exit_code == 0, f"{kind} not resolvable by ID: {result.output}"
        assert "Verification SUCCESS" in result.output


def test_artifact_lookup_ignores_internal_store_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bookkeeping directories must not be mistaken for artifact kinds."""
    root = tmp_path / "artifacts"
    monkeypatch.setenv("XLM_HOME", str(root))
    paths = ArtifactPaths(root=root)
    paths.ensure_directories()
    (root / "ledger" / "decoy").mkdir(parents=True, exist_ok=True)

    result = runner.invoke(app, ["artifact", "verify", "decoy"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
