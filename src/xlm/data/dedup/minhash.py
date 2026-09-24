"""Deterministic shingling, MinHash signatures and banded LSH keys.

Contract C05 requires a scalable near-duplicate method with frozen algorithm, seeds
and thresholds, and forbids all-pairs comparison. This module provides the signature
side of that: shingles are hashed with BLAKE2b (never Python's randomized built-in
``hash``), and permutations are derived deterministically from the configured seed,
so signatures are reproducible across processes, platforms and interpreter runs.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel
from xlm.data.dedup.matchview import MATCH_VIEW_VERSION, match_tokens

MINHASH_ALGORITHM_VERSION = "1"

# Mersenne prime just above 2**61; large enough for 64-bit shingle hashes.
_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH = (1 << 64) - 1
_MASK31 = (1 << 31) - 1


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
    return shingles_from_tokens(match_tokens(text), shingle_size)


def shingles_from_tokens(tokens: list[str], shingle_size: int) -> list[str]:
    """Return ordered shingles for pre-tokenized match-view tokens (same values).

    Lets callers that already hold the match view (exact hashing uses the same
    normalized string) skip the second normalization pass.
    """
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
        return self.signature_from_tokens(match_tokens(text))

    def signature_from_view(self, view: str) -> list[int]:
        """Signature for an already-computed match view (same values, one fewer pass)."""
        return self.signature_from_tokens(view.split(" ") if view else [])

    def signature_from_tokens(self, tokens: list[str]) -> list[int]:
        """Signature for pre-tokenized match-view tokens (same values)."""
        grams = shingles_from_tokens(tokens, self.config.shingle_size)
        if not grams:
            return [_MAX_HASH] * self.config.num_permutations
        hashed = {stable_hash64(g) for g in grams}
        return self.signature_from_hashes(hashed)

    def signature_from_hashes(self, hashed: set[int]) -> list[int]:
        """Return the signature for an already-hashed shingle set (same values)."""
        if not hashed:
            return [_MAX_HASH] * self.config.num_permutations
        vectorized = _signature_vectorized(hashed, self._params)
        if vectorized is not None:
            return vectorized
        if len(hashed) >= ARROW_KERNEL_MIN_SHINGLES:
            arrow = _signature_arrow(hashed, self._params)
            if arrow is not None:
                return arrow
        return _signature_python(hashed, self._params)

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


def _signature_python(hashed: set[int], params: list[tuple[int, int]]) -> list[int]:
    """Reference MinHash reduction in pure Python (exact; also the no-NumPy fallback)."""
    return [min(((a * h + b) % _MERSENNE_PRIME) for h in hashed) for a, b in params]


def _mersenne_fold(values: Any) -> Any:
    """Fold ``values < 2**64`` modulo 2**61 - 1 with one conditional subtract.

    For x < 2**64: x = hi * 2**61 + lo with hi < 8, and x mod P = lo + hi,
    which is below 2P, so a single subtract normalizes exactly.
    """
    import numpy as np  # type: ignore[import-not-found]

    prime = np.uint64(_MERSENNE_PRIME)
    folded = (values & prime) + (values >> np.uint64(61))
    return np.where(folded >= prime, folded - prime, folded)


def _signature_vectorized(hashed: set[int], params: list[tuple[int, int]]) -> list[int] | None:
    """Exact vectorized MinHash reduction in uint64 arithmetic.

    Computes ``min_h((a*h + b) % P)`` per permutation with P = 2**61 - 1 using
    only uint64 operations. The 122-bit product is never formed: h is split
    into ``h1 * 2**61 + h0`` (using 2**61 = 1 mod P), and ``(a*h0) % P`` for
    a, h0 < 2**61 is evaluated by 31-bit limbs::

        a*h0 = a1*g1 * 2**62 + (a1*g0 + a0*g1) * 2**31 + a0*g0

    with 2**62 = 2 mod P. Every intermediate is bounded to fit uint64
    (largest: ``part_a + part_b`` below 2**61 + 2**63, still below 2**64;
    every fold input is below 2**64 by construction), so wrapping can never
    occur and every output bit matches the reference loop. Returns None when
    NumPy is unavailable so the caller falls back to :func:`_signature_python`.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    prime = np.uint64(_MERSENNE_PRIME)
    mask31 = np.uint64(_MASK31)
    perms = np.array(params, dtype=np.uint64)
    avals = perms[:, 0].reshape(-1, 1)
    bvals = perms[:, 1].reshape(-1, 1)
    hvals = np.array(list(hashed), dtype=np.uint64).reshape(1, -1)

    # h = h1 * 2**61 + h0; (a*h + b) % P = (a*h1 + a*h0 + b) % P.
    h1 = hvals >> np.uint64(61)
    h0 = hvals & prime
    term_hi = avals * h1  # < 2**61 * 8 < 2**64: exact.

    # (a*h0) % P by 31-bit limbs; a, h0 < 2**61.
    a1 = avals >> np.uint64(31)  # < 2**30
    a0 = avals & mask31  # < 2**31
    g1 = h0 >> np.uint64(31)  # < 2**30
    g0 = h0 & mask31  # < 2**31
    part_a = np.uint64(2) * a1 * g1  # < 2**61: already reduced (even, below P + 1).
    mid = a1 * g0 + a0 * g1  # < 2**62: exact.
    s1 = mid >> np.uint64(31)  # < 2**31
    s0 = mid & mask31  # < 2**31
    # (mid * 2**31) % P = (2*s1 + s0*2**31) % P; s0*2**31 < 2**62: exact.
    part_b = np.uint64(2) * s1 + s0 * np.uint64(1 << 31)  # < 2**63: exact.
    part_c = a0 * g0  # < 2**62: exact.
    reduced = _mersenne_fold(_mersenne_fold(part_a + part_b) + part_c)

    folded_hi = _mersenne_fold(term_hi)
    total = _mersenne_fold(_mersenne_fold(folded_hi + reduced) + bvals)
    rows: list[int] = total.min(axis=1).tolist()
    return rows


#: Below this many shingles the per-call overhead of Arrow kernels exceeds the
#: pure-Python reduction (measured crossover between 16 and 36 shingles).
ARROW_KERNEL_MIN_SHINGLES = 32
#: Shingles per Arrow pass: bounds every temporary to k * chunk uint64 values
#: and the cached constants to five such arrays per process.
ARROW_KERNEL_CHUNK_SHINGLES = 2048

_ARROW_STATE: dict[tuple[tuple[int, int], ...], Any] = {}


def _arrow_state(params: list[tuple[int, int]]) -> Any:
    """Cached per-parameter constants, tiled shingle-major for zero-copy slicing."""
    key = tuple(params)
    state = _ARROW_STATE.get(key)
    if state is None:
        import pyarrow as pa
        import pyarrow.compute as pc

        u64 = pa.uint64()
        rows = ARROW_KERNEL_CHUNK_SHINGLES

        def tiled(values: list[int]) -> Any:
            return pa.concat_arrays([pa.array(values, u64)] * rows)

        state = (
            tiled([a & _MASK31 for a, _ in params]),
            tiled([a >> 31 for a, _ in params]),
            tiled([2 * (a >> 31) for a, _ in params]),
            tiled([b for _, b in params]),
            pc.divide(pa.array(range(len(params) * rows), u64), pa.scalar(len(params), u64)),
        )
        _ARROW_STATE.clear()
        _ARROW_STATE[key] = state
    return state


def _signature_arrow(hashed: set[int], params: list[tuple[int, int]]) -> list[int] | None:
    """Exact MinHash reduction with ``pyarrow.compute`` (a declared dependency).

    Each hash is first reduced in Python to ``g = h mod P < 2**61`` (valid since
    ``(a*h + b) % P == (a*g + b) % P``). Elements are laid out shingle-major
    (``row * k + permutation``) with per-permutation constants as zero-copy
    slices of cached tiles. With ``a = a1*2**31 + a0`` and ``g = g1*2**31 + g0``
    (a1, g1 < 2**30; a0, g0 < 2**31), using ``2**62 = 2`` and ``2**61 = 1 mod P``:

    - ``part_a = (2*a1) * g1 < 2**61``;
    - ``mid = a1*g0 + a0*g1 < 2**62`` and ``mid * 2**31 = 2*s1 + s0*2**31 (mod P)``
      with ``s1 = mid >> 31``, ``s0 = mid & (2**31 - 1)``, so
      ``part_b < 2**32 + 2**62``;
    - ``part_c = a0*g0 < 2**62``.

    Every sum is below 2**64 before each Mersenne fold (whose output is in
    ``[0, P)``), so no uint64 operation wraps and every value equals the
    reference ``(a*h + b) % P``. The per-permutation minimum is a log2 tree of
    element-wise minima over row halves; shingles are processed in bounded
    chunks whose minima combine exactly. Used when NumPy is unavailable; returns
    None when pyarrow cannot be imported.
    """
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
    except ImportError:
        return None
    u64 = pa.uint64()
    prime, mask31 = pa.scalar(_MERSENNE_PRIME, u64), pa.scalar(_MASK31, u64)
    shift61, shift31 = pa.scalar(61, u64), pa.scalar(31, u64)
    two31 = pa.scalar(1 << 31, u64)

    def fold(values: Any) -> Any:
        folded = pc.add(pc.bit_wise_and(values, prime), pc.shift_right(values, shift61))
        return pc.if_else(pc.greater_equal(folded, prime), pc.subtract(folded, prime), folded)

    tile_a0, tile_a1, tile_a1x2, tile_b, expand = _arrow_state(params)
    permutations = len(params)
    reduced = [value % _MERSENNE_PRIME for value in hashed]
    best: list[int] | None = None
    for start in range(0, len(reduced), ARROW_KERNEL_CHUNK_SHINGLES):
        chunk = pa.array(reduced[start : start + ARROW_KERNEL_CHUNK_SHINGLES], u64)
        rows = len(chunk)
        size = rows * permutations
        index = expand.slice(0, size)
        g1 = pc.take(pc.shift_right(chunk, shift31), index)
        g0 = pc.take(pc.bit_wise_and(chunk, mask31), index)
        a0, a1 = tile_a0.slice(0, size), tile_a1.slice(0, size)
        part_a = pc.multiply(tile_a1x2.slice(0, size), g1)
        mid = pc.add(pc.multiply(a1, g0), pc.multiply(a0, g1))
        high = pc.shift_right(mid, shift31)
        part_b = pc.add(pc.add(high, high), pc.multiply(pc.bit_wise_and(mid, mask31), two31))
        total = fold(pc.add(part_a, part_b))
        total = fold(pc.add(total, pc.multiply(a0, g0)))
        total = fold(pc.add(total, tile_b.slice(0, size)))
        while rows > 1:
            half = rows // 2
            width = half * permutations
            merged = pc.min_element_wise(total.slice(0, width), total.slice(width, width))
            if rows % 2:
                merged = pa.concat_arrays([merged, total.slice(2 * width, permutations)])
            total, rows = merged, half + rows % 2
        minima = total.to_pylist()
        best = minima if best is None else [min(x, y) for x, y in zip(best, minima, strict=True)]
    return best


def estimated_jaccard(left: list[int], right: list[int]) -> float:
    """Estimate Jaccard similarity as the fraction of agreeing signature positions."""
    if len(left) != len(right):
        raise ValueError("signatures must have equal length to be compared")
    if not left:
        return 0.0
    agreements = sum(1 for a, b in zip(left, right, strict=True) if a == b)
    return agreements / len(left)
