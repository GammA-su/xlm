"""Tests for minimal token shard reader and writer adhering to Contract C07."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


def make_doc(doc_id: str, text: str) -> CanonicalDocument:
    clean = canonical_normalize(text)
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="source_shards",
        source_revision="v1",
        source_file="f.jsonl",
        source_row=1,
        raw_hash=compute_sha256(text),
        clean_hash=compute_sha256(clean),
        text=clean,
        utf8_byte_count=len(clean.encode("utf-8")),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="CC0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_token_shard_writer_and_reader_roundtrip(tmp_path: Path) -> None:
    """Test TokenShardWriter creates valid shard and TokenShardReader verifies integrity."""
    tokenizer = ByteTokenizer()
    shard_dir = tmp_path / "shard_001"

    writer = TokenShardWriter(
        output_dir=shard_dir,
        shard_id="shard_001",
        source_id="source_shards",
        tokenizer=tokenizer,
    )

    docs = [
        make_doc("doc_1", "Hello world from document 1."),
        make_doc("doc_2", "Second document with François 🚀."),
        make_doc("doc_3", "Third document with code: def test(): pass"),
    ]

    manifest = writer.write_documents(docs, add_special_tokens=False)
    assert manifest.shard_id == "shard_001"
    assert manifest.source_id == "source_shards"
    assert manifest.num_documents == 3
    assert manifest.token_dtype == "uint16"
    assert manifest.endianness == "little"
    assert manifest.byte_coverage_ratio == 1.0

    # Read and verify with TokenShardReader
    reader = TokenShardReader(shard_dir)
    assert reader.manifest.shard_id == "shard_001"
    # Integrity check passes
    reader.verify_integrity()

    # Verify document offsets
    offsets = reader.read_document_offsets()
    assert len(offsets) == 3
    assert offsets[0]["doc_id"] == "doc_1"
    assert offsets[1]["doc_id"] == "doc_2"
    assert offsets[2]["doc_id"] == "doc_3"

    # Read tokens and compare with direct tokenizer output
    expected_all_ids: list[int] = []
    for d in docs:
        expected_all_ids.extend(tokenizer.encode(d.text, add_special_tokens=False))

    loaded_ids = reader.read_tokens()
    assert loaded_ids == expected_all_ids

    # Read slice
    slice_ids = reader.read_tokens(start=5, count=10)
    assert slice_ids == expected_all_ids[5:15]


def test_token_shard_corruption_detection(tmp_path: Path) -> None:
    """Test TokenShardReader detects corrupted or truncated binary files."""
    tokenizer = ByteTokenizer()
    shard_dir = tmp_path / "corrupt_shard"

    writer = TokenShardWriter(
        output_dir=shard_dir,
        shard_id="corrupt_shard",
        source_id="source_shards",
        tokenizer=tokenizer,
    )
    docs = [make_doc("d1", "Test content for corruption test.")]
    writer.write_documents(docs)

    reader = TokenShardReader(shard_dir)
    reader.verify_integrity()

    # Corrupt binary file by truncating bytes
    bin_path = shard_dir / "tokens.bin"
    raw_data = bin_path.read_bytes()
    bin_path.write_bytes(raw_data[:-2])

    with pytest.raises(ValueError, match="Binary checksum mismatch|Corrupted binary length"):
        reader.verify_integrity()
