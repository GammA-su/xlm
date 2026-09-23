"""Whole-shard workers and single-shard assembly preserve all authoritative bytes."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_performance_tokenization import document
from xlm.data.datasets.shards import ShardedJsonlWriter
from xlm.data.parallel_tokens import TokenizationLimits, tokenize_shards, tokenize_to_single_shard
from xlm.data.tokens import TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer


def prepare(root: Path) -> tuple[Path, Path, list]:
    docs = [document(f"passage {i} \U0001f642 <eos> " * (i + 1), i) for i in range(20)]
    tok = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=300)
    tok.save(root / "tokenizer")
    writer = ShardedJsonlWriter(root / "input", dataset_id="fixture", target_shard_bytes=4000)
    for doc in docs:
        writer.write_line(json.dumps(doc.to_dict(), ensure_ascii=False), doc.doc_id)
    writer.finish()
    TokenShardWriter(root / "reference", "reference", "fixture", tok, "pool").write_documents(
        docs, True
    )
    return root / "input", root / "tokenizer", docs


@pytest.mark.serial
def test_worker_count_and_assembly_bytes_exact(tmp_path: Path) -> None:
    source, tokenizer, _ = prepare(tmp_path)
    indexes = []
    for workers in (1, 2):
        output = tmp_path / f"parts{workers}"
        result = tokenize_shards(
            source,
            tokenizer,
            output,
            source_id="fixture",
            pool_hash="pool",
            workers=workers,
            batch_size=128 if workers == 2 else 1,
        )
        indexes.append((output / "token-dataset.json").read_bytes())
        assert sum(len(w["manifests"]) for w in result["workers"]) == len(result["index"]["shards"])
        combined = tmp_path / f"combined{workers}"
        tokenize_to_single_shard(
            source,
            tokenizer,
            combined,
            source_id="fixture",
            shard_id="reference",
            pool_hash="pool",
            workers=workers,
            batch_size=128 if workers == 2 else 1,
        )
        for path in (tmp_path / "reference").iterdir():
            if path.is_file():
                assert (combined / path.name).read_bytes() == path.read_bytes()
    assert indexes[0] == indexes[1]
    assert not list(tmp_path.glob(".token-workers-*"))


@pytest.mark.parametrize("kind", ["input", "output", "record", "source", "hash", "memory"])
def test_worker_failure_does_not_publish_index(tmp_path: Path, kind: str) -> None:
    source, tokenizer, _ = prepare(tmp_path)
    limits = TokenizationLimits()
    source_id = "fixture"
    if kind == "input":
        limits = replace(limits, max_documents=1)
    elif kind == "output":
        limits = replace(limits, max_output_bytes=70000)
    elif kind == "record":
        limits = replace(limits, max_record_bytes=10)
    elif kind == "source":
        source_id = "wrong"
    elif kind == "memory":
        limits = replace(limits, max_rss_bytes=1)
    else:
        path = next(source.glob("shard-*.jsonl"))
        path.write_bytes(path.read_bytes() + b"\n")
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        tokenize_shards(
            source, tokenizer, output, source_id=source_id, pool_hash="pool", limits=limits
        )
    assert not (output / "token-dataset.json").exists()


def test_rebase_preserves_spans_and_field_names_inside_escaped_ids() -> None:
    from xlm.data.parallel_tokens import _rebase_offset_line

    record = {
        "doc_id": '"token_start": 99 "byte_start": 7 \\ "byte_end": 4',
        "token_start": 4,
        "token_count": 3,
        "byte_start": 2,
        "byte_end": 9,
        "token_byte_spans": [[0, 0], [0, 7], [7, 7]],
        "lineage_id": "\U0001f642",
    }
    raw = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    expected = {**record, "token_start": 104, "byte_start": 1002, "byte_end": 1009}
    assert (
        _rebase_offset_line(raw, 100, 1000)
        == (json.dumps(expected, ensure_ascii=False) + "\n").encode()
    )
    with pytest.raises(ValueError, match="serialization"):
        _rebase_offset_line(b"{}", 1, 1)
