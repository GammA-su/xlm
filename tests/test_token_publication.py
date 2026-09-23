"""Token shard publication must fail closed and preserve immutable bytes."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from test_performance_tokenization import document
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4])
def test_fsync_failure_never_publishes_manifest(tmp_path: Path, failure_at: int) -> None:
    writer = TokenShardWriter(tmp_path, "s", "fixture", ByteTokenizer())
    calls = 0
    original = os.fsync

    def fail(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected fsync failure")
        original(fd)

    with patch("os.fsync", fail), pytest.raises(OSError, match="injected"):
        writer.write_documents([document("example")])
    assert not (tmp_path / "shard_manifest.json").exists()
    assert not list(tmp_path.glob(".token-stage-*"))


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4])
def test_replace_failure_has_no_completion(tmp_path: Path, failure_at: int) -> None:
    writer = TokenShardWriter(tmp_path, "s", "fixture", ByteTokenizer())
    calls = 0
    original = os.replace

    def fail(source: Path, dest: Path) -> None:
        nonlocal calls
        calls += 1
        assert not (tmp_path / "shard_manifest.json").exists()
        if calls == failure_at:
            raise OSError("injected replace failure")
        original(source, dest)

    with patch("os.replace", fail), pytest.raises(OSError, match="injected"):
        writer.write_documents([document("example")])
    assert not (tmp_path / "shard_manifest.json").exists()


def test_existing_complete_shard_is_immutable(tmp_path: Path) -> None:
    writer = TokenShardWriter(tmp_path, "s", "fixture", ByteTokenizer())
    with patch("os.fsync", wraps=os.fsync) as sync:
        writer.write_documents([document("example")])
    assert sync.call_count >= 4
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):
        writer.write_documents([document("different")])
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    TokenShardReader(tmp_path).verify_integrity()
