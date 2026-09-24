"""Spliced selected-record encoding is byte-identical to one full canonical encode."""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

import pytest

from xlm.data.acquisition.records import encode_record, selected_record

ALPHABET = ["a", "Z", "_", "x", "0", "é", " ", '"', "\\", "\n", "\x00", "\x1f", "😀", " "]
KEYS = ["id", "text", "Title", "_x", "_xlm", "_xlm_acquisitio", "_xlm_acquisitionz", "0", "é", ""]


def legacy_selected(record: dict[str, Any], locator: dict[str, Any], raw: bytes) -> bytes:
    """The historical definition: re-encode the whole record plus locator."""
    return (
        json.dumps(
            {
                **record,
                "_xlm_acquisition": {
                    **locator,
                    "original_record_sha256": hashlib.sha256(raw).hexdigest(),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def random_value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randrange(8 if depth < 2 else 5)
    if kind == 0:
        return "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(12)))
    if kind == 1:
        return rng.randrange(-(10**12), 10**12)
    if kind == 2:
        return rng.choice([0.1, -2.5e-300, 1e300, 3.0, -0.0])
    if kind == 3:
        return rng.choice([True, False, None])
    if kind == 4:
        return ""
    if kind == 5:
        return [random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    return {rng.choice(KEYS): random_value(rng, depth + 1) for _ in range(rng.randrange(4))}


def test_spliced_encoding_matches_full_encode_on_fuzzed_records() -> None:
    rng = random.Random(20260924)
    for _ in range(4000):
        record = {rng.choice(KEYS): random_value(rng) for _ in range(rng.randrange(5))}
        locator = {"row_index": rng.randrange(10**6), "etag": '"e"', "format": "parquet"}
        raw = encode_record(record)
        assert (
            raw
            == json.dumps(
                record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            + b"\n"
        )
        assert selected_record(record, locator, raw) == legacy_selected(record, locator, raw)


def test_non_canonical_raw_takes_the_full_encode() -> None:
    record = {"text": "b", "id": "a"}
    raw = b'{"text":"b","id":"a"}\n'  # original JSONL bytes, not canonical
    locator = {"row_index": 3}
    assert selected_record(record, locator, raw) == legacy_selected(record, locator, raw)
    other = encode_record({"text": "different"})
    assert selected_record(record, locator, other) == legacy_selected(record, locator, other)


def test_reserved_field_and_nan_are_still_refused() -> None:
    with pytest.raises(ValueError):
        selected_record({"_xlm_acquisition": 1}, {}, b"{}\n")
    with pytest.raises(ValueError):
        encode_record({"x": math.nan})
