"""Tests for canonical normalization, source adapters, and canonical IO (C02, C03, C04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters.jsonl import JsonlAdapter
from xlm.data.adapters.text import TextAdapter
from xlm.data.canonical_io import (
    CanonicalDatasetReader,
    CanonicalDatasetWriter,
    create_dataset_manifest,
)
from xlm.data.normalization import canonical_normalize, compute_sha256, decode_utf8_strict


def test_canonical_normalization_idempotence_and_preservation() -> None:
    """Verify canonical normalization idempotence and preservation of code/math/emoji."""
    samples = [
        "Simple ASCII text.",
        "Accented names: François, José, Märt, 李白, Søren.",
        "def foo():\n    # Indented code with spaces\n    return 42\n",
        "Equations: e^{i\\pi} + 1 = 0 and \\sum_{k=1}^n k = n(n+1)/2.",
        "Emoji test: 🚀 🎉 🤖 ✨ 👨‍👩‍👧‍👦.",
        "Line with windows newline:\r\nSecond line with CR:\rThird line.\n",
    ]
    for s in samples:
        norm1 = canonical_normalize(s)
        norm2 = canonical_normalize(norm1)
        # Idempotence
        assert norm1 == norm2
        # No lowercasing
        if "François" in s:
            assert "François" in norm1
        # No stripping of indentation
        if "    return 42" in s:
            assert "    return 42" in norm1
        # No stripping of math
        if "\\sum" in s:
            assert "\\sum" in norm1
        # Windows newlines converted
        assert "\r" not in norm1


def test_strict_utf8_decode_rejection() -> None:
    """Verify strict UTF-8 decoding raises UnicodeDecodeError on invalid bytes."""
    valid_bytes = "Valid text 🚀".encode()
    assert decode_utf8_strict(valid_bytes) == "Valid text 🚀"

    # Invalid trailing byte
    invalid_bytes = b"Hello \xff\xfe world"
    with pytest.raises(UnicodeDecodeError):
        decode_utf8_strict(invalid_bytes)


def test_text_adapter_file_and_directory(tmp_path: Path) -> None:
    """Test plain text adapter with single file and directory traversal."""
    doc1 = tmp_path / "doc1.txt"
    doc1.write_text("First document content.\nLine 2.\n", encoding="utf-8")
    doc2 = tmp_path / "doc2.txt"
    doc2.write_text("Second document content with François 🚀.\n", encoding="utf-8")

    adapter = TextAdapter(source_id="text_src", source_revision="rev1")

    # Single file
    c_doc1 = adapter.process_file(doc1, split="train")
    assert c_doc1.doc_id == "doc1"
    assert c_doc1.source_id == "text_src"
    assert c_doc1.source_revision == "rev1"
    assert c_doc1.split == "train"
    assert c_doc1.utf8_byte_count == len(c_doc1.text.encode("utf-8"))
    assert c_doc1.clean_hash == compute_sha256(c_doc1.text)

    # Rejection of duplicate doc_id
    with pytest.raises(ValueError, match="Duplicate document ID"):
        adapter.process_file(doc1, doc_id="doc1")

    # Directory processing with new adapter
    adapter2 = TextAdapter(source_id="text_src")
    docs = list(
        adapter2.process_directory(tmp_path, split_map={"doc1": "train", "doc2": "diagnostic_val"})
    )
    assert len(docs) == 2
    assert docs[0].doc_id == "doc1"
    assert docs[0].split == "train"
    assert docs[1].doc_id == "doc2"
    assert docs[1].split == "diagnostic_val"


def test_text_adapter_oversized_file(tmp_path: Path) -> None:
    """Test text adapter rejects oversized files exceeding configured byte cap."""
    big_file = tmp_path / "big.txt"
    big_file.write_bytes(b"A" * 1024)

    adapter = TextAdapter(source_id="src", max_doc_bytes=512)
    with pytest.raises(ValueError, match="exceeds max record limit"):
        adapter.process_file(big_file)


def test_jsonl_adapter_strict_validation(tmp_path: Path) -> None:
    """Test JSONL adapter enforcing strict schemas, types, and split validation."""
    adapter = JsonlAdapter(source_id="jsonl_src")

    # Valid record
    raw_valid = b'{"id": "doc1", "text": "Valid text 1", "split": "train"}\n'
    doc = adapter.process_line(raw_valid, source_file="test.jsonl", source_row=1)
    assert doc.doc_id == "doc1"
    assert doc.text == "Valid text 1"
    assert doc.split == "train"
    assert doc.raw_hash == compute_sha256(raw_valid)
    assert doc.clean_hash == compute_sha256("Valid text 1")

    # Duplicate doc ID
    with pytest.raises(ValueError, match="Duplicate document ID"):
        adapter.process_line(raw_valid, source_file="test.jsonl", source_row=2)

    # Malformed UTF-8
    adapter2 = JsonlAdapter(source_id="jsonl_src")
    with pytest.raises(ValueError, match="Malformed UTF-8"):
        adapter2.process_line(
            b'{"id": "d2", "text": "bad \xff\xfe", "split": "train"}', "f.jsonl", 1
        )

    # Malformed JSON syntax
    with pytest.raises(ValueError, match="Malformed JSON"):
        adapter2.process_line(b'{"id": "d2", "text": incomplete', "f.jsonl", 2)

    # Duplicate JSON keys
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        adapter2.process_line(
            b'{"id": "d3", "id": "d4", "text": "foo", "split": "train"}', "f.jsonl", 3
        )

    # Missing text field
    with pytest.raises(ValueError, match="Missing required text field"):
        adapter2.process_line(b'{"id": "d5", "split": "train"}', "f.jsonl", 4)

    # Non-string text field (structured data that should not be silently stringified)
    with pytest.raises(ValueError, match="must be a string.*Never stringifying"):
        adapter2.process_line(
            b'{"id": "d6", "text": ["nested", "list"], "split": "train"}', "f.jsonl", 5
        )

    # Invalid split assignment
    with pytest.raises(ValueError, match="Invalid split 'eval_test'"):
        adapter2.process_line(b'{"id": "d7", "text": "bar", "split": "eval_test"}', "f.jsonl", 6)

    # Empty text document (valid, 0 bytes)
    empty_doc = adapter2.process_line(
        b'{"id": "empty_doc", "text": "", "split": "train"}', "f.jsonl", 7
    )
    assert empty_doc.utf8_byte_count == 0
    assert empty_doc.text == ""


def test_canonical_dataset_writer_and_reader_roundtrip(tmp_path: Path) -> None:
    """Test CanonicalDatasetWriter and Reader with JSONL and Parquet round trip."""
    docs = [
        CanonicalDocument(
            doc_id=f"doc_{i}",
            source_id="test_src",
            source_revision="v1",
            source_file="f.jsonl",
            source_row=i,
            raw_hash=compute_sha256(f"raw {i}"),
            clean_hash=compute_sha256(f"clean {i}"),
            text=f"Clean text for document {i} with 🚀.",
            utf8_byte_count=len(f"Clean text for document {i} with 🚀.".encode()),
            language="en",
            language_confidence=1.0,
            document_kind="prose",
            source_metadata={"index": i},
            parent_ids=[],
            license_reference="CC0",
            transform_log=[{"transform": "nfc_and_newline_normalize", "version": "1.0"}],
            quality_reasons=[],
            cluster_ids={},
            split="train" if i < 3 else "diagnostic_val",
        )
        for i in range(5)
    ]

    writer = CanonicalDatasetWriter(tmp_path)
    jsonl_path = writer.write_jsonl(docs)
    parquet_path = writer.write_parquet(docs)

    # Verify JSONL round trip
    jsonl_docs = list(CanonicalDatasetReader.read_jsonl(jsonl_path))
    assert len(jsonl_docs) == 5
    for original, loaded in zip(docs, jsonl_docs, strict=True):
        assert original.to_dict() == loaded.to_dict()

    # Verify Parquet round trip
    parquet_docs = list(CanonicalDatasetReader.read_parquet(parquet_path))
    assert len(parquet_docs) == 5
    for original, loaded in zip(docs, parquet_docs, strict=True):
        assert original.to_dict() == loaded.to_dict()

    # Verify manifest creation
    manifest = create_dataset_manifest(docs, "test_src", "v1")
    assert manifest["num_documents"] == 5
    assert manifest["split_counts"] == {"train": 3, "diagnostic_val": 2}
    assert manifest["total_utf8_bytes"] > 0
