"""Strict JSON for hot loops: the same acceptance and values as
``canonical.loads_bytes_strict``, without its per-call decoder construction and its
post-parse walk over every value.

``canonical.loads_bytes_strict`` rejects a UTF-8 BOM, invalid UTF-8, duplicate object
keys, NaN/Infinity constants and (by walking the parsed value) any non-finite float.
A non-finite float can only come from a NaN/Infinity constant (``parse_constant``) or
an overflowing float literal such as ``1e999`` (``parse_float``), so rejecting both
while parsing accepts exactly the same documents and returns equal values.
"""

from __future__ import annotations

import json
import math
from typing import Any

BOM = b"\xef\xbb\xbf"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate object key")
        document[key] = value
    return document


def _finite_float(literal: str) -> float:
    value = float(literal)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _no_constant(_name: str) -> Any:
    raise ValueError("non-finite constant")


_DECODER = json.JSONDecoder(
    object_pairs_hook=_unique_pairs, parse_constant=_no_constant, parse_float=_finite_float
)


def loads_strict_bytes(raw: bytes) -> Any:
    """Parse strict UTF-8 JSON; any violation raises ``ValueError`` (content-free)."""
    if raw.startswith(BOM):
        raise ValueError("byte-order mark is not canonical")
    return _DECODER.decode(raw.decode("utf-8"))
