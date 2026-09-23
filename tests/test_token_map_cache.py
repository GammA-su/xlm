"""Cache lifetime, corruption, bounded handles and exact replay on authored shards."""

from __future__ import annotations

import gc
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_performance_tokenization import document
from xlm.data.token_cache import TokenMapCache
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


def shard(root: Path) -> TokenShardReader:
    TokenShardWriter(root, "fixture", "fixture", ByteTokenizer()).write_documents(
        [document("a river\n\U0001f642 " * 100)], True
    )
    return TokenShardReader(root)


def test_cache_reuses_mapping_evicts_and_releases_windows_handles(tmp_path: Path) -> None:
    readers = [shard(tmp_path / str(i)) for i in range(3)]
    cache = TokenMapCache(2)
    import mmap

    with patch("xlm.data.token_cache.mmap.mmap", wraps=mmap.mmap) as constructor:
        for start in range(20):
            assert cache.read(readers[0], start, 9) == readers[0].read_tokens(start, 9)
        assert constructor.call_count == 1
        first = next(iter(cache._entries.values()))
        cache.read(readers[1], 0, 1)
        cache.read(readers[2], 0, 1)
        assert first.mapping.closed and first.handle.closed
        assert len(cache._entries) == 2
    entries = list(cache._entries.values())
    cache.close()
    cache.close()
    assert all(e.mapping.closed and e.handle.closed for e in entries)
    with pytest.raises(ValueError, match="closed"):
        cache.read(readers[0], 0, 1)
    for reader in readers:
        path = reader.directory / "tokens.bin"
        path.rename(path.with_suffix(".moved"))


@pytest.mark.parametrize("name", ["offsets.jsonl", "shard_manifest.json"])
def test_cache_detects_artifact_replacement(tmp_path: Path, name: str) -> None:
    reader = shard(tmp_path)
    with TokenMapCache() as cache:
        cache.read(reader, 0, 1)
        path = tmp_path / name
        replacement = path.with_suffix(".new")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with pytest.raises(ValueError, match="changed"):
            cache.read(reader, 0, 1)
        assert not cache._entries


def test_cache_refuses_corruption_and_old_reader(tmp_path: Path) -> None:
    reader = shard(tmp_path)
    binary = tmp_path / "tokens.bin"
    binary.write_bytes(b"\x00" * binary.stat().st_size)
    with TokenMapCache() as cache, pytest.raises(ValueError, match="checksum"):
        cache.read(reader, 0, 1)
    manifest = tmp_path / "shard_manifest.json"
    data = json.loads(manifest.read_text())
    data["pool_hash"] = "changed"
    manifest.write_text(json.dumps(data))
    with TokenMapCache() as cache, pytest.raises(ValueError, match="manifest changed"):
        cache.read(reader, 0, 1)


def test_cache_gc_and_slice_bounds(tmp_path: Path) -> None:
    reader = shard(tmp_path)
    cache = TokenMapCache()
    assert cache.read(reader, reader.manifest.num_tokens, 1) == []
    for start, count in [(-1, 1), (0, -1), (reader.manifest.num_tokens + 1, 0)]:
        with pytest.raises(ValueError, match="slice"):
            cache.read(reader, start, count)
    cache.read(reader, 0, 1)
    entry = next(iter(cache._entries.values()))
    del cache
    gc.collect()
    assert entry.mapping.closed and entry.handle.closed


def test_mixture_cache_exact_batches_commit_rollback_and_resume(tmp_path: Path) -> None:
    from test_mixture_stream import mixture, write_shards
    from xlm.data.sampling.stream import MixtureBatcher

    readers = write_shards(tmp_path, {"encyclopedia": 40, "web_forum": 40}, ByteTokenizer())
    recipe = mixture(encyclopedia=0.6, web_forum=0.4)
    with (
        MixtureBatcher(
            recipe, readers, context_length=32, global_batch_valid_targets=128, max_open_shards=0
        ) as old,
        MixtureBatcher(
            recipe, readers, context_length=32, global_batch_valid_targets=128, max_open_shards=8
        ) as new,
    ):
        for _ in range(12):
            assert old.next_step_microbatches() == new.next_step_microbatches()
            old.rollback()
            new.rollback()
            assert old.next_step_microbatches(117) == new.next_step_microbatches(117)
            old.commit()
            new.commit()
            assert old.get_state() == new.get_state()
        state = new.get_state()
        expected = old.next_step_microbatches()
        new.next_step_microbatches()
        new.load_state(state)
        assert new.next_step_microbatches() == expected
