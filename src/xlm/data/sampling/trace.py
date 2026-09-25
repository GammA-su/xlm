"""Exact C07 trace hashing for the fixed, primitive per-target record schema."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from json.encoder import encode_basestring_ascii
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


def extend_target_trace_chain(
    previous: str,
    source_id: str,
    epoch: int,
    doc_ids: Sequence[str],
    lineage_ids: Sequence[str],
    token_offsets: Sequence[int],
    labels: Sequence[int],
    byte_spans: Sequence[Sequence[int]],
    indices: Sequence[int],
) -> str:
    """Fold ``target_trace_digest`` over the ``indices`` of one packed window.

    The result equals chaining ``target_trace_digest`` over the trace dict the
    batcher builds for each position. For exact ``str``/``int`` fields,
    ``json.dumps(sort_keys=True, separators=(",", ":"))`` writes precisely
    ``{"previous":<s>,"target":{"byte_span":[<i>,<i>],"doc_id":<s>,"epoch":<i>,
    "label":<i>,"lineage_id":<s>,"source_id":<s>,"token_offset":<i>}}``, where
    ``<s>`` is the same C ASCII string escaper and ``<i>`` is ``int.__repr__``,
    which ``%d`` reproduces. Strings are escaped once per document run instead
    of once per target. Any value outside that type domain takes the original
    per-target function, including its validation errors. The encoded byte
    limit is still checked per target.
    """
    if not indices:
        return previous
    spans = [byte_spans[i] for i in indices]
    if not (
        type(previous) is str
        and type(source_id) is str
        and type(epoch) is int
        and set(map(type, [doc_ids[i] for i in indices])) == {str}
        and set(map(type, [lineage_ids[i] for i in indices])) == {str}
        and set(map(type, [token_offsets[i] for i in indices])) == {int}
        and set(map(type, [labels[i] for i in indices])) == {int}
        and set(map(len, spans)) == {2}
        and set(map(type, [value for span in spans for value in span])) == {int}
    ):
        for i in indices:
            previous = target_trace_digest(
                previous,
                {
                    "source_id": source_id,
                    "doc_id": doc_ids[i],
                    "lineage_id": lineage_ids[i],
                    "token_offset": token_offsets[i],
                    "label": labels[i],
                    "byte_span": list(byte_spans[i]),
                    "epoch": epoch,
                },
            )
        return previous

    sha256 = hashlib.sha256
    limit = MAX_MANIFEST_BYTES
    source = encode_basestring_ascii(source_id).replace("%", "%%")
    # The escaper always quotes; only the incoming digest can be an arbitrary str.
    digest = encode_basestring_ascii(previous)[1:-1]
    run: tuple[str, str] | None = None
    template = ""
    for i in indices:
        key = (doc_ids[i], lineage_ids[i])
        if key != run:
            run = key
            template = (
                '{"previous":"%s","target":{"byte_span":[%d,%d],"doc_id":'
                + encode_basestring_ascii(key[0]).replace("%", "%%")
                + ',"epoch":'
                + repr(epoch)
                + ',"label":%d,"lineage_id":'
                + encode_basestring_ascii(key[1]).replace("%", "%%")
                + ',"source_id":'
                + source
                + ',"token_offset":%d}}'
            )
        start, end = byte_spans[i]
        raw = (template % (digest, start, end, labels[i], token_offsets[i])).encode()
        if len(raw) > limit:
            raise ValueError("Artifact manifest exceeds byte limit")
        # Every later "previous" is a lowercase hex digest, which JSON leaves unescaped.
        digest = sha256(raw).hexdigest()
    return digest
