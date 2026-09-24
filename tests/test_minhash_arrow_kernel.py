"""The NumPy-free Arrow MinHash kernel equals the pure-Python reference exactly."""

from __future__ import annotations

import random

import pytest

from xlm.data.dedup import minhash
from xlm.data.dedup.minhash import (
    ARROW_KERNEL_CHUNK_SHINGLES,
    MinHashConfig,
    MinHasher,
    _permutation_params,
    _signature_arrow,
    _signature_python,
)

EDGES = {0, 1, (1 << 64) - 1, (1 << 61) - 1, (1 << 61) - 2, 1 << 61, (1 << 61) + 1, 1 << 63}


@pytest.mark.parametrize("permutations", [128, 64])
def test_arrow_kernel_matches_reference_on_fuzzed_sets(permutations: int) -> None:
    params = _permutation_params(MinHashConfig(num_permutations=permutations, bands=16))
    rng = random.Random(permutations)
    sizes = [
        1,
        2,
        3,
        31,
        32,
        33,
        156,
        397,
        ARROW_KERNEL_CHUNK_SHINGLES,
        ARROW_KERNEL_CHUNK_SHINGLES + 7,
    ]
    for trial in range(60):
        hashed = {rng.getrandbits(64) for _ in range(rng.choice(sizes))}
        if trial % 7 == 0:
            hashed |= EDGES
        assert _signature_arrow(hashed, params) == _signature_python(hashed, params)


def test_numpy_free_hasher_uses_the_arrow_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(minhash, "_signature_vectorized", lambda hashed, params: None)
    calls: list[int] = []
    original = minhash._signature_arrow

    def counting(hashed: set[int], params: list[tuple[int, int]]) -> list[int] | None:
        calls.append(len(hashed))
        return original(hashed, params)

    monkeypatch.setattr(minhash, "_signature_arrow", counting)
    hasher = MinHasher()
    text = " ".join(f"word{i % 97} token{i}" for i in range(400))
    signature = hasher.signature(text)
    tokens = minhash.match_tokens(text)
    reference = _signature_python(
        {minhash.stable_hash64(g) for g in minhash.shingles_from_tokens(tokens, 5)},
        _permutation_params(MinHashConfig()),
    )
    assert calls and signature == reference
