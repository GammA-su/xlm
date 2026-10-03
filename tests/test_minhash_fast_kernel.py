"""Exact equivalence of the chunked in-place MinHash kernel and the C05 shingle path.

Oracles: the pure-Python reduction ``_signature_python`` and the historical NumPy
kernel ``_signature_vectorized_v1``; shingles: the historical ``DiskGroups.add``
construction (``stable_hash64`` of ``" ".join`` windows, empty view -> one ""
shingle). Every case compares full 128-value signatures and band keys.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from xlm.data.dedup import minhash
from xlm.data.dedup.matchview import match_normalize, match_tokens
from xlm.data.exclusion.policy import ProductionPolicy
from xlm.data.exclusion.scanprep import shingle_hashes

P = minhash._MERSENNE_PRIME
EDGES = [
    0,
    1,
    2,
    P - 2,
    P - 1,
    P,
    P + 1,
    P + 7,
    P + 8,
    2 * P,
    2 * P + 1,
    2**31 - 1,
    2**31,
    2**61,
    2**62 - 1,
    2**62,
    2**63 - 1,
    2**63,
    2**64 - 8,
    2**64 - 2,
    2**64 - 1,
]


@pytest.fixture(scope="module")
def params() -> list[tuple[int, int]]:
    return minhash.MinHasher(ProductionPolicy().minhash())._params


def historical_hashes(tokens: list[str], size: int = 5) -> set[int]:
    """``DiskGroups.add``: windows over ``range(max(1, n - size + 1))``."""
    return {
        minhash.stable_hash64(" ".join(tokens[start : start + size]))
        for start in range(max(1, len(tokens) - size + 1))
    }


def test_uint64_boundary_values(params: list[tuple[int, int]]) -> None:
    for size in range(1, len(EDGES) + 1):
        values = set(EDGES[:size])
        expected = minhash._signature_python(values, params)
        assert minhash._signature_vectorized(values, params) == expected
        assert minhash._signature_vectorized_v1(values, params) == expected
    for value in EDGES:
        assert minhash._signature_vectorized({value}, params) == minhash._signature_python(
            {value}, params
        )


@pytest.mark.parametrize("seed", range(8))
def test_random_sets_of_varying_sizes(params: list[tuple[int, int]], seed: int) -> None:
    rng = random.Random(seed)
    sizes = [1, 2, 3, 5, 63, 64, 65, 127, 255, 256, 257, 511, 512, 513, 1000, 2049, 5000]
    for case in range(250):
        size = rng.choice(sizes)
        values = {rng.getrandbits(64) for _ in range(size)}
        if case % 4 == 0:
            values |= set(rng.sample(EDGES, 4))
        oracle = (
            minhash._signature_python(values, params)
            if size <= 300
            else minhash._signature_vectorized_v1(values, params)
        )
        assert minhash._signature_vectorized(values, params) == oracle, (seed, case)


def test_duplicate_hashes_and_array_input(params: list[tuple[int, int]]) -> None:
    rng = np.random.default_rng(5)
    base = rng.integers(0, 2**63, 300, dtype=np.uint64)
    repeated = np.concatenate([base, base, base[::-1], base[:7]])
    expected = minhash._signature_python(set(base.tolist()), params)
    assert minhash.signature_from_array(repeated, params) == expected
    with pytest.raises(ValueError):
        minhash.signature_from_array(np.zeros(0, np.uint64), params)


def test_chunk_size_never_changes_results(
    params: list[tuple[int, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = random.Random(9)
    values = {rng.getrandbits(64) for _ in range(1500)}
    expected = minhash._signature_vectorized_v1(values, params)
    for chunk in (1, 7, 64, 255, 256, 257, 4096):
        monkeypatch.setattr(minhash, "FAST_KERNEL_CHUNK_SHINGLES", chunk)
        assert minhash._signature_vectorized(values, params) == expected


WORDS = ["alpha", "Beta", "ÇA", "naïve", "日本語", "x", "ﬁ", "ẞ", "the", "a", "--", "!!", "ǅ", "1"]


@pytest.mark.parametrize("seed", range(4))
def test_document_shingle_path_matches_historical(params: list[tuple[int, int]], seed: int) -> None:
    rng = random.Random(seed)
    hasher = minhash.MinHasher(ProductionPolicy().minhash())
    for _ in range(150):
        count = rng.choice([0, 1, 2, 4, 5, 6, 9, 40, 400, 3000])
        pieces = [
            rng.choice(WORDS) + rng.choice(["", " ", ",", ".\n", "\t", "  "]) for _ in range(count)
        ]
        if rng.random() < 0.2 and count:
            pieces = pieces[:3] * (count // 3 + 1)  # Adversarial repeated shingles.
        text = " ".join(pieces)
        normalized = match_normalize(text)
        tokens = match_tokens(text)
        old = historical_hashes(tokens)
        new = shingle_hashes(normalized, 5)
        assert set(new.tolist()) == old
        signature = minhash.signature_from_array(new, params)
        oracle = minhash._signature_vectorized_v1(old, params)
        assert signature == oracle
        assert hasher.band_keys(signature) == hasher.band_keys(oracle)
