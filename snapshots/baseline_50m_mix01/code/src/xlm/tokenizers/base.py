"""Base tokenizer contract and interfaces complying with C06."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path


class BaseTokenizer(ABC):
    """Abstract base class for all XLM tokenizers."""

    @property
    @abstractmethod
    def pad_token_id(self) -> int:
        """ID of the padding token."""

    @property
    @abstractmethod
    def bos_token_id(self) -> int:
        """ID of the beginning-of-sequence token."""

    @property
    @abstractmethod
    def eos_token_id(self) -> int:
        """ID of the end-of-sequence token."""

    @property
    @abstractmethod
    def unk_token_id(self) -> int:
        """ID of the unknown token."""

    @property
    def special_token_ids(self) -> set[int]:
        """Set of all reserved control token IDs."""
        return {self.pad_token_id, self.bos_token_id, self.eos_token_id, self.unk_token_id}

    @property
    @abstractmethod
    def vocab_size(self) -> int:
        """Declared vocabulary size capacity."""

    @property
    @abstractmethod
    def actual_vocab_size(self) -> int:
        """Actual number of tokens in the vocabulary including special tokens."""

    @property
    @abstractmethod
    def is_production_baseline(self) -> bool:
        """Whether this tokenizer is certified as a production baseline artifact."""

    @property
    @abstractmethod
    def fingerprint(self) -> str:
        """Deterministic fingerprint of tokenizer configuration and vocabulary."""

    @abstractmethod
    def id_to_token(self, token_id: int) -> str:
        """Return token string representation for a token ID."""

    @abstractmethod
    def token_to_id(self, token: str) -> int | None:
        """Return token ID for a token string, or None if not found."""

    @abstractmethod
    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        """Encode text to token IDs.

        When add_special_tokens=False, ordinary text containing reserved token
        spellings (e.g. '<eos>') MUST NOT be converted to control IDs.
        """

    @abstractmethod
    def encode_with_offsets(
        self, text: str, add_special_tokens: bool = False
    ) -> tuple[list[int], list[tuple[int, int]]]:
        """Encode text to token IDs and half-open document-relative UTF-8 byte spans [start, end).

        Structural tokens (BOS, EOS) have zero-length byte spans: (0, 0) for BOS,
        and (len(utf8_bytes), len(utf8_bytes)) for EOS.
        """

    def batch_encode(
        self, texts: Sequence[str], add_special_tokens: bool = False
    ) -> list[list[int]]:
        """Encode multiple texts to token IDs."""
        return [self.encode(t, add_special_tokens=add_special_tokens) for t in texts]

    @abstractmethod
    def decode(self, token_ids: Sequence[int], skip_special_tokens: bool = False) -> str:
        """Decode token IDs back to a canonical UTF-8 string."""

    @abstractmethod
    def save(self, directory: Path) -> None:
        """Persist tokenizer model and manifest to a directory."""

    def validate_token_ids(self, token_ids: Sequence[int]) -> None:
        """Validate that all token IDs are integers within the valid vocabulary range."""
        v_size = self.actual_vocab_size
        for idx, tid in enumerate(token_ids):
            if not isinstance(tid, int):
                raise ValueError(
                    f"Token ID at index {idx} must be an integer, got {type(tid).__name__}"
                )
            if tid < 0 or tid >= v_size:
                raise ValueError(
                    f"Token ID at index {idx} ({tid}) is out of valid range [0, {v_size})"
                )
