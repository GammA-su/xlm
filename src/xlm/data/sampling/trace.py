"""Exact C07 trace hashing for the fixed, primitive per-target record schema."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from xlm.artifacts.manifest import MAX_MANIFEST_BYTES, identity_digest


def target_trace_digest(previous: str, trace: dict[str, Any]) -> str:
    """Avoid recursive generic validation only when its result is already provable.

    Fixed keys, exact primitive types and two integer byte coordinates imply a
    constant node count/depth. Canonical encoded length bounds all string lengths
    too. All other shapes take the original validator, including malformed input.
    The serialized bytes and SHA chain are unchanged.
    """
    if (
        trace.keys()
        == {"source_id", "doc_id", "lineage_id", "token_offset", "label", "byte_span", "epoch"}
        and type(previous) is str
        and all(type(trace[k]) is str for k in ("source_id", "doc_id", "lineage_id"))
        and all(type(trace[k]) is int for k in ("token_offset", "label", "epoch"))
        and type(trace["byte_span"]) is list
        and len(trace["byte_span"]) == 2
        and all(type(value) is int for value in trace["byte_span"])
    ):
        raw = json.dumps(
            {"previous": previous, "target": trace},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Artifact manifest exceeds byte limit")
        return hashlib.sha256(raw).hexdigest()
    return identity_digest({"previous": previous, "target": trace})
