"""Byte-bounded native batches retain scalar token shard bytes and framing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from tokenizers import AddedToken

from test_performance_tokenization import document, reference_encode
from xlm.data.tokens import TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer


@pytest.mark.parametrize("batch_size", [1, 16, 64, 128, 512])
def test_batch_writer_matches_scalar(tmp_path: Path, batch_size: int) -> None:
    docs = [document(f"\U0001f642 text {i} <eos>\n" * (i % 8 + 1), i) for i in range(137)]
    tokenizer = ByteLevelBPETokenizer.train_from_documents(docs, target_vocab_size=300)
    TokenShardWriter(tmp_path / "scalar", "s", "fixture", tokenizer).write_documents(docs, True)
    TokenShardWriter(
        tmp_path / "batch", "s", "fixture", tokenizer, batch_size=batch_size
    ).write_documents(iter(docs), True)
    for path in (tmp_path / "scalar").iterdir():
        if path.is_file():
            assert path.read_bytes() == (tmp_path / "batch" / path.name).read_bytes()


def test_batch_offsets_added_token_whitespace() -> None:
    tokenizer = ByteLevelBPETokenizer.train_from_documents(
        [document("a river")], target_vocab_size=270
    )
    tokenizer._tok.add_tokens([AddedToken("river", lstrip=True, rstrip=True)])
    texts = ["  river  ", "", "\U0001f642 river\n"]
    for special in (False, True):
        assert tokenizer.batch_encode_with_offsets(texts, special) == [
            reference_encode(tokenizer, text, special) for text in texts
        ]


def test_writer_batch_byte_cap_and_scalar_fallback(tmp_path: Path) -> None:
    tokenizer = ByteTokenizer()
    docs = [document("x" * 400000, i) for i in range(4)]
    writer = TokenShardWriter(tmp_path, "s", "fixture", tokenizer, batch_size=512)
    with patch.object(
        tokenizer, "batch_encode_with_offsets", wraps=tokenizer.batch_encode_with_offsets
    ) as encode:
        for doc, (ids, spans) in writer._encoded_documents(iter(docs), True):
            assert len(ids) == len(doc.text) + 2 and len(spans) == len(ids)
    assert [len(call.args[0]) for call in encode.call_args_list] == [2, 2]
    assert all(
        sum(len(text.encode()) for text in call.args[0]) <= 1024**2
        for call in encode.call_args_list
    )
