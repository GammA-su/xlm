"""Embedding provider abstraction (P28-N): no model downloads, ever.

The dedup lane consumes *precomputed* vectors through the artifact contract in
:mod:`xlm.data.semantic.embeddings`. A provider produces those vectors; the
only providers shipped here are deterministic test/synthetic ones. A later
operator wires a locally installed encoder behind :class:`EmbeddingProvider`
without touching search, policy, or artifact code.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, Protocol


class EmbeddingProvider(Protocol):
    """Produces embedding batches for texts (operator-supplied for real models)."""

    @property
    def model_identity(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def dtype(self) -> str: ...

    @property
    def normalized(self) -> bool: ...

    @property
    def truncation_policy(self) -> str: ...

    def encode(self, texts: list[str]) -> Any:
        """Encode a batch: an ``(B, D)`` float array when NumPy is present,
        otherwise nested ``list[list[float]]`` rows (same values up to float
        normalization rounding; tests compare within one representation)."""
        ...


def _normalize_rows(rows: list[list[float]], dtype: str = "float32") -> Any:
    """L2-normalize rows, returning NumPy when present else nested lists."""
    try:
        import numpy as np  # type: ignore[import-not-found]

        array = np.array(rows, dtype=dtype)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return array / norms
    except ImportError:
        import math

        normalized: list[list[float]] = []
        for row in rows:
            norm = math.sqrt(sum(v * v for v in row)) or 1.0
            normalized.append([v / norm for v in row])
        return normalized


class SyntheticEmbeddingProvider:
    """Deterministic synthetic vectors for tests and benchmarks (NOT a model).

    Vectors stream from a seeded RNG: the provider holds an offset that
    advances by every encoded batch, so one instance encodes a reproducible
    stream. Separate instances restart the stream, and changing batch sizes
    realigns it — both documented test-only caveats. Similarity structure
    is meaningless by design; use :class:`TextHashEmbeddingProvider` when
    paraphrase-adjacent fixtures need stable per-text vectors.
    """

    def __init__(
        self,
        dim: int = 384,
        dtype: str = "float32",
        seed: int = 20260919,
        normalized: bool = True,
    ) -> None:
        self._dim = dim
        self._dtype = dtype
        self._seed = seed
        self._normalized = normalized
        self._offset = 0

    @property
    def model_identity(self) -> str:
        return f"synthetic-gaussian:v1:dim={self._dim}:seed={self._seed}"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def dtype(self) -> str:
        return self._dtype

    @property
    def normalized(self) -> bool:
        return self._normalized

    @property
    def truncation_policy(self) -> str:
        return "synthetic:v1:none"

    def encode(self, texts: list[str]) -> Any:
        # One stream position per call: fresh vectors every call, reproducible
        # for a fixed call sequence; batch sizes realign the stream (test-only
        # caveat, documented on the class).
        rng = random.Random(f"{self._seed}:{self._offset}")
        rows = [[rng.gauss(0.0, 1.0) for _ in range(self._dim)] for _ in texts]
        self._offset += len(texts)
        if not self._normalized:
            return rows
        return _normalize_rows(rows, self._dtype)


class TextHashEmbeddingProvider:
    """Deterministic per-text vectors from a keyed hash (test fixtures only).

    The same text always yields the same unit vector; different texts yield
    effectively independent directions. This is a fixture generator, not an
    embedding model: it gives paraphrase tests stable identities without
    weights, at the cost of carrying zero semantic structure.
    """

    def __init__(
        self,
        dim: int = 64,
        dtype: str = "float32",
        namespace: str = "test-namespace-v1",
    ) -> None:
        self._dim = dim
        self._dtype = dtype
        self._namespace = namespace

    @property
    def model_identity(self) -> str:
        return f"texthash:v1:dim={self._dim}:ns={self._namespace}"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def dtype(self) -> str:
        return self._dtype

    @property
    def normalized(self) -> bool:
        return True

    @property
    def truncation_policy(self) -> str:
        return "texthash:v1:full-text"

    def encode(self, texts: list[str]) -> Any:
        rows = []
        for text in texts:
            seed = int.from_bytes(
                hashlib.blake2b(f"{self._namespace}:{text}".encode(), digest_size=8).digest(),
                "big",
            )
            rng = random.Random(seed)
            rows.append([rng.gauss(0.0, 1.0) for _ in range(self._dim)])
        return _normalize_rows(rows, self._dtype)
