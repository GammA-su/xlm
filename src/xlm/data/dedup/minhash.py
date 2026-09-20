"""Deterministic shingling, MinHash signatures and banded LSH keys.

Contract C05 requires a scalable near-duplicate method with frozen algorithm, seeds
and thresholds, and forbids all-pairs comparison. This module provides the signature
side of that: shingles are hashed with BLAKE2b (never Python's randomized built-in
``hash``), and permutations are derived deterministically from the configured seed,
so signatures are reproducible across processes, platforms and interpreter runs.
"""

from __future__ import annotations

import hashlib

from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel
from xlm.data.dedup.matchview import MATCH_VIEW_VERSION, match_tokens

MINHASH_ALGORITHM_VERSION = "1"

# Mersenne prime just above 2**61; large enough for 64-bit shingle hashes.
_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH = (1 << 64) - 1


class MinHashConfig(StrictConfigModel):
    """Frozen configuration for the near-duplicate signature scheme."""

    shingle_size: int = Field(default=5, ge=1, le=32, description="Words per shingle.")
    num_permutations: int = Field(default=128, ge=8, le=1024)
    bands: int = Field(default=32, ge=1, description="LSH bands; must divide num_permutations.")
    seed: int = Field(default=20260919, ge=0, description="Frozen permutation seed.")
    jaccard_threshold: float = Field(
        default=0.8,
        gt=0.0,
        le=1.0,
        description=(
            "Estimated Jaccard similarity at or above which two documents count as near duplicates."
        ),
    )

    @model_validator(mode="after")
    def validate_banding(self) -> MinHashConfig:
        if self.num_permutations % self.bands != 0:
            raise ValueError(
                f"bands ({self.bands}) must divide num_permutations ({self.num_permutations}); "
                "uneven banding would make LSH keys depend on permutation ordering."
            )
        return self

    def identity(self) -> str:
        """Stable identity of the signature scheme, for artifact keys."""
        payload = (
            f"minhash:v{MINHASH_ALGORITHM_VERSION}:view{MATCH_VIEW_VERSION}:"
            f"k={self.shingle_size}:perm={self.num_permutations}:bands={self.bands}:"
            f"seed={self.seed}:thr={self.jaccard_threshold}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stable_hash64(data: str) -> int:
    """Return a stable 64-bit hash of ``data``.

    BLAKE2b is used rather than the built-in ``hash``, which is randomized per
    process and would make duplicate clusters irreproducible (C02).
    """
    digest = hashlib.blake2b(data.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def shingles(text: str, shingle_size: int) -> list[str]:
    """Return the ordered word shingles of ``text``'s match view.

    Documents shorter than one shingle fall back to a single whole-text shingle, so
    that very short records still receive a usable signature instead of an empty one.
    """
    tokens = match_tokens(text)
    if not tokens:
        return []
    if len(tokens) < shingle_size:
        return [" ".join(tokens)]
    return [" ".join(tokens[i : i + shingle_size]) for i in range(len(tokens) - shingle_size + 1)]


def _permutation_params(config: MinHashConfig) -> list[tuple[int, int]]:
    """Derive (a, b) permutation coefficients deterministically from the seed."""
    params: list[tuple[int, int]] = []
    for i in range(config.num_permutations):
        seed_bytes = f"{config.seed}:{i}".encode()
        digest = hashlib.blake2b(seed_bytes, digest_size=16).digest()
        a = int.from_bytes(digest[:8], "big") % (_MERSENNE_PRIME - 1) + 1
        b = int.from_bytes(digest[8:], "big") % _MERSENNE_PRIME
        params.append((a, b))
    return params


class MinHasher:
    """Computes deterministic MinHash signatures and banded LSH keys."""

    def __init__(self, config: MinHashConfig | None = None) -> None:
        self.config = config or MinHashConfig()
        self._params = _permutation_params(self.config)

    def signature(self, text: str) -> list[int]:
        """Return the MinHash signature of ``text``.

        An empty document yields an all-maximum signature, which shares no band with
        any non-empty document, so empty records never cluster with real content.
        """
        grams = shingles(text, self.config.shingle_size)
        if not grams:
            return [_MAX_HASH] * self.config.num_permutations

        hashed = {stable_hash64(g) for g in grams}
        signature: list[int] = []
        for a, b in self._params:
            signature.append(min(((a * h + b) % _MERSENNE_PRIME) for h in hashed))
        return signature

    def band_keys(self, signature: list[int]) -> list[str]:
        """Return one LSH bucket key per band.

        Two documents share a band key only when that whole slice of their signatures
        matches, which is what keeps candidate generation sub-quadratic.
        """
        if len(signature) != self.config.num_permutations:
            raise ValueError(
                f"signature length {len(signature)} does not match configured "
                f"num_permutations {self.config.num_permutations}"
            )
        rows = self.config.num_permutations // self.config.bands
        keys: list[str] = []
        for band_index in range(self.config.bands):
            chunk = signature[band_index * rows : (band_index + 1) * rows]
            payload = f"{band_index}:" + ",".join(str(v) for v in chunk)
            keys.append(hashlib.blake2b(payload.encode("utf-8"), digest_size=12).hexdigest())
        return keys


def estimated_jaccard(left: list[int], right: list[int]) -> float:
    """Estimate Jaccard similarity as the fraction of agreeing signature positions."""
    if len(left) != len(right):
        raise ValueError("signatures must have equal length to be compared")
    if not left:
        return 0.0
    agreements = sum(1 for a, b in zip(left, right, strict=True) if a == b)
    return agreements / len(left)
