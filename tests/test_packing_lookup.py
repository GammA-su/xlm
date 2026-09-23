"""Sparse native lookup preserves dense/partial C07 masks and attribution."""

from __future__ import annotations

import random

import pytest

from xlm.data.sampling.packing import CausalStreamPacker, shift_window


@pytest.mark.parametrize("length", [2, 17, 513, 700])
@pytest.mark.parametrize("specials", [(0, 1), (1, 1)])
def test_structural_lookup_matches_reference(length: int, specials: tuple[int, int]) -> None:
    pad, bos = specials
    rng = random.Random(42)
    cases = [
        [rng.randrange(65536) for _ in range(length)],
        [rng.randrange(4) for _ in range(length)],
        [bos] * length,
        [pad] * length,
    ]
    for ids in cases:
        ignored = {i for i, tid in enumerate(ids[1:]) if tid in (bos, pad)}
        inputs, labels, mask, partial = shift_window(ids, 512, pad, ignored)
        packed = CausalStreamPacker(512, pad, bos).pack(
            ids, ["s"] * length, ["doc"], [(0, 1)], True
        )
        assert (packed.input_ids, packed.labels, packed.loss_mask, packed.is_partial) == (
            inputs,
            labels,
            mask,
            partial,
        )
        assert packed.source_attribution == ["s"] * 512
        assert packed.doc_ids == ["doc"] and packed.byte_spans == [(0, 1)]
