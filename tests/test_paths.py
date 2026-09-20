"""Verify artifact root resolution, directory layout, and path safety."""

from pathlib import Path

import pytest

from xlm.core.paths import ArtifactPaths, get_artifact_root


def test_get_artifact_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify default root resolves to ~/.xlm when XLM_HOME is unset."""
    monkeypatch.delenv("XLM_HOME", raising=False)
    root = get_artifact_root()
    expected = (Path.home() / ".xlm").resolve()
    assert root == expected


def test_get_artifact_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify XLM_HOME environment variable correctly overrides root."""
    custom_dir = tmp_path / "custom_xlm"
    monkeypatch.setenv("XLM_HOME", str(custom_dir))
    root = get_artifact_root()
    assert root == custom_dir.resolve()


def test_artifact_paths_structure(tmp_path: Path) -> None:
    """Verify structured directory locations and directory creation."""
    paths = ArtifactPaths(root=tmp_path / "root")
    assert paths.raw_data == tmp_path / "root" / "raw"
    assert paths.clean_data == tmp_path / "root" / "clean"
    assert paths.token_shards == tmp_path / "root" / "shards"
    assert paths.tokenizers == tmp_path / "root" / "tokenizers"
    assert paths.runs == tmp_path / "root" / "runs"
    assert paths.checkpoints == tmp_path / "root" / "checkpoints"
    assert paths.evaluation == tmp_path / "root" / "eval"
    assert paths.reports == tmp_path / "root" / "reports"
    assert paths.ledger == tmp_path / "root" / "ledger"

    # Before ensure_directories, directories should not exist
    assert not paths.root.exists()

    paths.ensure_directories()
    assert paths.root.is_dir()
    assert paths.raw_data.is_dir()
    assert paths.clean_data.is_dir()
    assert paths.token_shards.is_dir()
    assert paths.tokenizers.is_dir()
    assert paths.runs.is_dir()
    assert paths.checkpoints.is_dir()
    assert paths.evaluation.is_dir()
    assert paths.reports.is_dir()
    assert paths.ledger.is_dir()


def test_resolve_safe_subpath(tmp_path: Path) -> None:
    """Verify path traversal attacks are rejected."""
    paths = ArtifactPaths(root=tmp_path / "root")
    safe_sub = paths.resolve_safe_subpath("clean/pool_01.parquet")
    assert safe_sub == (tmp_path / "root" / "clean" / "pool_01.parquet").resolve()

    # Traversal attempt
    with pytest.raises(ValueError, match="Path traversal detected"):
        paths.resolve_safe_subpath("../../../etc/passwd")
