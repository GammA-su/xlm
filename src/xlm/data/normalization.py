"""Canonical text normalization and hashing functions adhering to C02 and C03."""

from __future__ import annotations

import hashlib
import unicodedata


def compute_sha256(data: bytes | str) -> str:
    """Compute deterministic SHA-256 hexadecimal digest for bytes or UTF-8 string."""
    if isinstance(data, str):
        b = data.encode("utf-8")
    else:
        b = data
    return hashlib.sha256(b).hexdigest()


def decode_utf8_strict(raw_bytes: bytes) -> str:
    """Decode raw bytes strictly as UTF-8.

    Raises:
        UnicodeDecodeError: If raw_bytes contains malformed or invalid UTF-8 sequences.
    """
    return raw_bytes.decode("utf-8", errors="strict")


def canonical_normalize(text: str) -> str:
    """Apply versioned canonical normalization adhering to Contracts C02 and C03.

    Transforms:
    1. Unicode normalization form C (NFC).
    2. Newline normalization (\\r\\n and \\r -> \\n).

    Explicitly preserves:
    - Case sensitivity (no lowercasing).
    - Code indentation and whitespace (no whitespace collapsing or stripping).
    - Mathematical symbols, angle brackets, and punctuation.
    - Emoji and combining marks.

    This function is strictly idempotent:
    canonical_normalize(canonical_normalize(text)) == canonical_normalize(text).
    """
    if not text:
        return ""
    # NFC normalization
    normalized = unicodedata.normalize("NFC", text)
    # Newline normalization: \r\n -> \n, then any remaining \r -> \n
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized
