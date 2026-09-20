"""Tests for byte tokenizer fixture, Byte-Level BPE, special-token protection, and offsets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.cli.main import app
from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer


def make_test_doc(doc_id: str, text: str, split: str = "train") -> CanonicalDocument:
    """Helper to construct valid CanonicalDocument instances for tests."""
    clean = canonical_normalize(text)
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="test_source",
        source_revision="v1",
        source_file="test.jsonl",
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
        split=split,
    )


def test_byte_tokenizer_fixture_properties_and_reversibility() -> None:
    """Verify ByteTokenizer fixture properties, reversibility, and byte offsets."""
    tok = ByteTokenizer()
    assert tok.vocab_size == 260
    assert tok.actual_vocab_size == 260
    assert not tok.is_production_baseline
    assert tok.pad_token_id == 0
    assert tok.bos_token_id == 1
    assert tok.eos_token_id == 2
    assert tok.unk_token_id == 3

    samples = [
        "ASCII string 123",
        "François Müller and 李白 visited Göttingen.",
        "Emoji: 🚀 ✨ 👨‍👩‍👧‍👦",
        "Equations: \\int_0^1 x dx = 0.5",
        "Literal <eos> and <pad> and <bos>.",
    ]

    for s in samples:
        # Encode with and without special tokens
        ids_no_special = tok.encode(s, add_special_tokens=False)
        ids_with_special = tok.encode(s, add_special_tokens=True)

        assert ids_with_special[0] == tok.bos_token_id
        assert ids_with_special[-1] == tok.eos_token_id
        assert ids_with_special[1:-1] == ids_no_special

        # Control IDs must NOT appear in content encoding
        control_hits = tok.special_token_ids & set(ids_no_special)
        assert control_hits == set(), f"Control IDs {control_hits} emitted for literal text in {s}"

        # Byte offsets check
        ids, offsets = tok.encode_with_offsets(s, add_special_tokens=False)
        assert ids == ids_no_special
        utf8_bytes = canonical_normalize(s).encode("utf-8")
        assert len(offsets) == len(utf8_bytes)
        for idx, (st, end) in enumerate(offsets):
            assert st == idx
            assert end == idx + 1

        # Reversibility
        assert tok.decode(ids_no_special) == canonical_normalize(s)


def test_byte_level_bpe_training_and_reversibility() -> None:
    """Verify ByteLevelBPETokenizer training, reversibility, and true byte spans."""
    docs = [
        make_test_doc("d1", "Language modeling research requires rigorous engineering."),
        make_test_doc("d2", "Scholars from Göttingen and Zürich wrote extensive treatises."),
        make_test_doc(
            "d3", "def compute_loss(logits, targets):\n    return cross_entropy(logits, targets)\n"
        ),
        make_test_doc(
            "d4", "Mathematics: \\sum_{i=1}^n i = \\frac{n(n+1)}{2} and e^{i\\pi} + 1 = 0."
        ),
        make_test_doc("d5", "Emoji test: 🚀 🎉 🤖 ✨ and sequences: 👨‍👩‍👧‍👦."),
    ]

    tok = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=300)
    assert tok.vocab_size == 300
    assert tok.actual_vocab_size >= 260
    assert not tok.is_production_baseline

    for doc in docs:
        content_ids, offsets = tok.encode_with_offsets(doc.text, add_special_tokens=False)
        # Content IDs identical to encode()
        assert content_ids == tok.encode(doc.text, add_special_tokens=False)

        # Full byte reconstruction from token byte payloads
        canonical_bytes = doc.text.encode("utf-8")
        token_bytes_list = [tok.token_to_bytes(tok.id_to_token(tid)) for tid in content_ids]
        concatenated_bytes = b"".join(token_bytes_list)
        assert concatenated_bytes == canonical_bytes

        # No gaps or overlaps in half-open byte spans
        curr = 0
        for st, end in offsets:
            assert st == curr
            curr = end
        assert curr == len(canonical_bytes)

        # Decode round-trip
        decoded = tok.decode(content_ids)
        assert decoded == doc.text


def test_bpe_fit_on_ascii_reconstructs_unseen_unicode_without_unk() -> None:
    """Verify BPE fit on ASCII text reconstructs previously unseen Unicode/emoji without UNK."""
    ascii_docs = [
        make_test_doc("a1", "The quick brown fox jumps over the lazy dog."),
        make_test_doc("a2", "Standard causal transformer language model pretraining."),
        make_test_doc("a3", "Simple words and sentences without foreign characters."),
    ]
    tok = ByteLevelBPETokenizer.train_from_documents(ascii_docs, target_vocab_size=280)

    # Text containing characters completely unseen in training:
    unseen_text = "François Müller, Märt Raud, José García, 李白, and 🚀 rocket ✨."
    clean_unseen = canonical_normalize(unseen_text)

    ids = tok.encode(clean_unseen, add_special_tokens=False)

    # Must NOT emit UNK
    assert tok.unk_token_id not in ids

    # Exact lossless reconstruction
    decoded = tok.decode(ids)
    assert decoded == clean_unseen


def test_literal_special_token_protection() -> None:
    """Verify standalone, adjacent, repeated, whitespace, and code occurrences of special tokens."""
    docs = [
        make_test_doc("d1", "Pretraining text with common tokens and words."),
    ]
    tok = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=280)

    test_spellings = [
        "<eos>",
        "<pad>",
        "<bos>",
        "<unk>",
        "<eos><bos>",
        "<pad><unk><eos>",
        "<eos><eos><eos>",
        "   <eos>   ",
        "if token == '<eos>': break",
        "def check(): return '<pad>' + '<bos>'",
    ]

    for spelling in test_spellings:
        ids, offsets = tok.encode_with_offsets(spelling, add_special_tokens=False)

        # Absence of reserved control IDs
        control_ids_in_output = tok.special_token_ids & set(ids)
        assert control_ids_in_output == set(), (
            f"Literal spelling {repr(spelling)} emitted control IDs {control_ids_in_output}!"
        )

        # Exact lossless reconstruction
        decoded = tok.decode(ids)
        assert decoded == spelling


def test_structural_framing_and_duplicate_prevention() -> None:
    """Verify add_special_tokens=True frames sequence and prevents duplicate framing."""
    tok = ByteTokenizer()
    text = "Hello world"
    framed_ids, offsets = tok.encode_with_offsets(text, add_special_tokens=True)

    assert framed_ids[0] == tok.bos_token_id
    assert framed_ids[-1] == tok.eos_token_id
    # Structural tokens have zero-length byte spans
    assert offsets[0] == (0, 0)
    assert offsets[-1] == (len(text.encode("utf-8")), len(text.encode("utf-8")))

    # Test duplicate framing prevention
    re_framed_ids, _ = tok.encode_with_offsets(tok.decode(framed_ids), add_special_tokens=True)
    # Count of BOS and EOS should not be doubled
    assert re_framed_ids.count(tok.bos_token_id) == 1
    assert re_framed_ids.count(tok.eos_token_id) == 1


def test_train_only_fitting_enforcement() -> None:
    """Verify lower-level fitting interface rejects documents from non-train splits."""
    mixed_docs = [
        make_test_doc("t1", "Valid train doc.", split="train"),
        make_test_doc("v1", "Validation doc leaking into train.", split="diagnostic_val"),
    ]

    with pytest.raises(ValueError, match="Only 'train' split documents may be used"):
        ByteLevelBPETokenizer.train_from_documents(mixed_docs, target_vocab_size=280)


def test_bpe_save_load_identity(tmp_path: Path) -> None:
    """Verify save/load identity of ByteLevelBPETokenizer."""
    docs = [
        make_test_doc("d1", "Testing save and load serialization identity."),
    ]
    tok1 = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=280)
    save_dir = tmp_path / "tokenizer_save"
    tok1.save(save_dir)

    tok2 = ByteLevelBPETokenizer.load(save_dir)
    assert tok2.vocab_size == tok1.vocab_size
    assert tok2.actual_vocab_size == tok1.actual_vocab_size
    assert tok2.fingerprint == tok1.fingerprint
    assert tok2.special_token_ids == tok1.special_token_ids

    test_text = "François with literal <eos> 🚀."
    assert tok2.encode(test_text) == tok1.encode(test_text)
    assert tok2.decode(tok1.encode(test_text)) == test_text


def test_out_of_bounds_and_negative_id_validation() -> None:
    """Verify out-of-range, nonexistent, and negative token IDs are rejected on decode."""
    tok = ByteTokenizer()
    with pytest.raises(ValueError, match="out of valid range"):
        tok.decode([-1])
    with pytest.raises(ValueError, match="out of valid range"):
        tok.decode([260])
    with pytest.raises(ValueError, match="out of valid range"):
        tok.decode([999999])


def test_cli_tokenizer_workflow(tmp_path: Path) -> None:
    """Test full CLI tokenizer workflow: train -> inspect -> encode -> verify."""
    runner = CliRunner()

    # 1. Create canonical jsonl
    docs_file = tmp_path / "documents.jsonl"
    d1 = make_test_doc(
        "doc1", "Causal language model pretraining on canonical data.", split="train"
    )
    d2 = make_test_doc("doc2", "Multilingual test with François and 李白.", split="train")
    d3 = make_test_doc("doc3", "Diagnostic validation document.", split="diagnostic_val")
    with docs_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps(d1.to_dict()) + "\n")
        f.write(json.dumps(d2.to_dict()) + "\n")
        f.write(json.dumps(d3.to_dict()) + "\n")

    tok_dir = tmp_path / "trained_tok"

    # Train
    res_train = runner.invoke(
        app,
        [
            "tokenizer",
            "train",
            "--data-path",
            str(docs_file),
            "--vocab-size",
            "300",
            "--output-dir",
            str(tok_dir),
        ],
    )
    assert res_train.exit_code == 0
    assert "Tokenizer (bpe) created successfully" in res_train.stdout

    # Inspect
    res_inspect = runner.invoke(app, ["tokenizer", "inspect", str(tok_dir)])
    assert res_inspect.exit_code == 0
    assert "Target Vocabulary Size: 300" in res_inspect.stdout

    # Encode
    res_encode = runner.invoke(app, ["tokenizer", "encode", str(tok_dir), "Hello François <eos>"])
    assert res_encode.exit_code == 0
    assert "Control IDs emitted: []" in res_encode.stdout

    # Verify
    res_verify = runner.invoke(app, ["tokenizer", "verify", str(tok_dir)])
    assert res_verify.exit_code == 0
    assert "All tokenizer verification checks PASSED" in res_verify.stdout
