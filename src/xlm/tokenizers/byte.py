"""Deterministic byte-level tokenizer fixture complying with C06."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from xlm.data.normalization import canonical_normalize, compute_sha256
from xlm.tokenizers.base import BaseTokenizer

SPECIAL_TOKENS = {
    0: "<pad>",
    1: "<bos>",
    2: "<eos>",
    3: "<unk>",
}
TOKEN_TO_SPECIAL_ID = {v: k for k, v in SPECIAL_TOKENS.items()}


class ByteTokenizer(BaseTokenizer):
    """Deterministic byte tokenizer fixture.

    Vocabulary layout:
    - 0: <pad>
    - 1: <bos>
    - 2: <eos>
    - 3: <unk>
    - 4..259: Raw bytes 0x00 .. 0xFF (id = byte_val + 4)
    Total vocab size: 260.
    """

    def __init__(self) -> None:
        self._vocab_size = 260
        self._pad_id = 0
        self._bos_id = 1
        self._eos_id = 2
        self._unk_id = 3

    @property
    def pad_token_id(self) -> int:
        return self._pad_id

    @property
    def bos_token_id(self) -> int:
        return self._bos_id

    @property
    def eos_token_id(self) -> int:
        return self._eos_id

    @property
    def unk_token_id(self) -> int:
        return self._unk_id

    @property
    def vocab_size(self) -> int:
        return self._vocab_size

    @property
    def actual_vocab_size(self) -> int:
        return self._vocab_size

    @property
    def is_production_baseline(self) -> bool:
        return False

    @property
    def fingerprint(self) -> str:
        data = f"ByteTokenizer:v1:vocab={self._vocab_size}:specials={SPECIAL_TOKENS}"
        return compute_sha256(data)

    def id_to_token(self, token_id: int) -> str:
        if token_id in SPECIAL_TOKENS:
            return SPECIAL_TOKENS[token_id]
        if 4 <= token_id < 260:
            byte_val = token_id - 4
            return f"<0x{byte_val:02X}>"
        raise ValueError(f"Token ID {token_id} out of vocabulary range [0, 260)")

    def token_to_id(self, token: str) -> int | None:
        if token in TOKEN_TO_SPECIAL_ID:
            return TOKEN_TO_SPECIAL_ID[token]
        if token.startswith("<0x") and token.endswith(">") and len(token) == 6:
            try:
                b = int(token[3:5], 16)
                if 0 <= b < 256:
                    return b + 4
            except ValueError:
                return None
        return None

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        ids, _ = self.encode_with_offsets(text, add_special_tokens=add_special_tokens)
        return ids

    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        # Canonical normalization before tokenization
        clean_text = canonical_normalize(text)
        utf8_bytes = clean_text.encode("utf-8")
        num_bytes = len(utf8_bytes)

        content_ids = [b + 4 for b in utf8_bytes]
        content_offsets = [(i, i + 1) for i in range(num_bytes)]

        if not add_special_tokens:
            return content_ids, content_offsets

        # Frame with BOS and EOS, preventing duplicate framing
        final_ids: list[int] = []
        final_offsets: list[tuple[int, int]] = []

        has_bos = len(content_ids) > 0 and content_ids[0] == self._bos_id
        has_eos = len(content_ids) > 0 and content_ids[-1] == self._eos_id

        if not has_bos:
            final_ids.append(self._bos_id)
            final_offsets.append((0, 0))

        final_ids.extend(content_ids)
        final_offsets.extend(content_offsets)

        if not has_eos:
            final_ids.append(self._eos_id)
            final_offsets.append((num_bytes, num_bytes))

        return final_ids, final_offsets

    def decode(self, token_ids: Sequence[int], skip_special_tokens: bool = False) -> str:
        self.validate_token_ids(token_ids)
        raw_bytes = bytearray()
        for tid in token_ids:
            if tid in self.special_token_ids:
                if skip_special_tokens:
                    continue
                # If not skipping, special tokens cannot be represented as raw bytes
                # without an explicit representation; decode as their token string
                pass
            elif 4 <= tid < 260:
                raw_bytes.append(tid - 4)

        return raw_bytes.decode("utf-8", errors="replace")

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "type": "byte_fixture",
            "version": "1.0",
            "vocab_size": self._vocab_size,
            "actual_vocab_size": self.actual_vocab_size,
            "is_production_baseline": self.is_production_baseline,
            "special_tokens": SPECIAL_TOKENS,
            "fingerprint": self.fingerprint,
        }
        (directory / "tokenizer_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> ByteTokenizer:
        manifest_path = directory / "tokenizer_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"ByteTokenizer manifest not found at {manifest_path}")
        return cls()
