"""P29 exact offline performance regression checks."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from tokenizers import AddedToken

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import canonical_normalize
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.tokenizers.byte import ByteTokenizer


def document(text: str, index: int = 0) -> CanonicalDocument:
    text = canonical_normalize(text)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return CanonicalDocument(
        str(index),
        "fixture",
        "v1",
        "authored.jsonl",
        index,
        digest,
        digest,
        text,
        len(text.encode()),
        "en",
        1.0,
        "prose",
        {},
        [],
        "authored",
        [],
        [],
        {},
        "train",
    )


def reference_encode(
    tokenizer: ByteLevelBPETokenizer, text: str, special: bool
) -> tuple[list[int], list[tuple[int, int]]]:
    clean = canonical_normalize(text)
    ids = tokenizer._tok.encode(clean, add_special_tokens=False).ids
    spans = []
    cursor = 0
    for token_id in ids:
        size = len(tokenizer.token_to_bytes(tokenizer.id_to_token(token_id)))
        spans.append((cursor, cursor + size))
        cursor += size
    if special:
        if not ids or ids[0] != tokenizer.bos_token_id:
            ids.insert(0, tokenizer.bos_token_id)
            spans.insert(0, (0, 0))
        if ids[-1] != tokenizer.eos_token_id:
            ids.append(tokenizer.eos_token_id)
            size = len(clean.encode())
            spans.append((size, size))
    return ids, spans


@pytest.mark.parametrize("special", [False, True])
def test_bpe_ids_offsets_batch_and_reload_exact(tmp_path: Path, special: bool) -> None:
    samples = [
        "",
        "hello river",
        "<eos><bos><unk><pad>",
        "e\u0301\r\n\u6771\u4eac \U0001f642",
        "\x00\x1c\t words ",
        "a" * 3000,
        "  leading  trailing  ",
    ]
    tokenizer = ByteLevelBPETokenizer.train_from_documents(
        [document(text, i) for i, text in enumerate(samples)],
        target_vocab_size=300,
    )
    tokenizer.save(tmp_path)
    for tok in (tokenizer, ByteLevelBPETokenizer.load(tmp_path)):
        for _ in range(2):  # Cold and warm cache.
            for text in samples:
                expected = reference_encode(tok, text, special)
                assert tok.encode_with_offsets(text, special) == expected
                assert tok.encode(text, special) == expected[0]
        texts = samples * 40  # Crosses multiple Rust batch boundaries.
        assert tok.batch_encode(texts, special) == [
            reference_encode(tok, t, special)[0] for t in texts
        ]
        assert tok.batch_encode([], special) == []


def test_added_token_whitespace_and_backend_mutation_preserve_spans() -> None:
    tokenizer = ByteLevelBPETokenizer.train_from_documents(
        [document("a river and the forest")], target_vocab_size=270
    )
    text = " the river and forest "
    tokenizer.encode_with_offsets(text)  # Warm lengths before backend mutation.
    tokenizer._tok.add_tokens([AddedToken("river", lstrip=True), AddedToken("forest", rstrip=True)])
    for special in (False, True):
        assert tokenizer.encode_with_offsets(text, special) == reference_encode(
            tokenizer, text, special
        )
        assert tokenizer.encode(text, special) == reference_encode(tokenizer, text, special)[0]


class FixtureTokenizer(ByteTokenizer):
    def __init__(self, ids: list[int], wide: bool) -> None:
        super().__init__()
        self.ids = ids
        self.wide = wide

    @property
    def actual_vocab_size(self) -> int:
        return 65537 if self.wide else 65536

    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        return self.ids[:], [(0, 0)] * len(self.ids)


@pytest.mark.parametrize("wide", [False, True])
@pytest.mark.parametrize("count", [0, 1, 4095, 4096, 4097, 8193])
def test_token_binary_exact_scalar_reference(tmp_path: Path, wide: bool, count: int) -> None:
    high = 4294967295 if wide else 65535
    ids = ([0, 1, 2, 3, high] * (count // 5 + 1))[:count]
    tokenizer = FixtureTokenizer(ids, wide)
    manifest = TokenShardWriter(tmp_path, "fixture", "fixture", tokenizer).write_documents(
        [document("a")]
    )
    expected = b"".join(struct.pack("<I" if wide else "<H", i) for i in ids)
    assert (tmp_path / "tokens.bin").read_bytes() == expected
    assert manifest.checksum_sha256 == hashlib.sha256(expected).hexdigest()
    reader = TokenShardReader(tmp_path)
    reader.verify_integrity()
    assert reader.read_tokens() == ids
    index = reader.read_document_offsets()[0]
    assert index["token_count"] == count
    assert index["bos_positions"] == [i for i, t in enumerate(ids) if t == 1]
    assert index["eos_positions"] == [i for i, t in enumerate(ids) if t == 2]
    assert index["token_byte_spans"] == [[0, 0]] * count
    assert json.loads((tmp_path / "shard_counters.json").read_text())["valid_targets"] == max(
        0, count - 1
    )


@pytest.mark.parametrize("bad", [-1, 65536])
def test_invalid_token_never_publishes_manifest(tmp_path: Path, bad: int) -> None:
    tokenizer = FixtureTokenizer([4] * 4096 + [bad], False)
    with pytest.raises(ValueError, match="cannot fit"):
        TokenShardWriter(tmp_path, "fixture", "fixture", tokenizer).write_documents([document("a")])
    assert not (tmp_path / "shard_manifest.json").exists()
