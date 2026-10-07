"""Bounded C07 training-index validation; v2 token payloads are read sequentially."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import numpy as np

from xlm.data.tokens import (
    INDEX_SCHEMA_V2,
    TokenShardReader,
    check_normalized_coverage,
    framing_positions,
)


def validate_v1_spans(record: dict[str, Any], tokens: int) -> None:
    """Self-consistency of explicit v1 spans, in normalized-text byte coordinates.

    Spans are the producer's cumulative token payloads: contiguous half-open intervals
    from 0 whose final end is ``covered_bytes``, zero-length at BOS/EOS. They are not
    bounded by the original ``byte_count`` (NFC can grow UTF-8); that relation is
    :func:`check_normalized_coverage`'s.
    """
    spans = record["token_byte_spans"]
    if type(spans) is not list or len(spans) != tokens:
        raise ValueError("invalid per-token canonical byte spans")
    end = 0
    for span in spans:
        if (
            type(span) is not list
            or len(span) != 2
            or type(span[0]) is not int
            or type(span[1]) is not int
            or span[0] != end
            or span[1] < end
        ):
            raise ValueError("invalid per-token canonical byte spans")
        end = span[1]
    covered = record.get("covered_bytes")
    if type(covered) is not int or covered != end:
        raise ValueError("v1 canonical byte coverage mismatch")
    bos, eos = framing_positions(record, tokens)
    if (bos and spans[0][1] != 0) or (eos and spans[-1][0] != end):
        raise ValueError("v1 structural byte spans are not zero-length framing")
    check_normalized_coverage(record.get("byte_count"), end, eos)


def validate_training_index(
    reader: TokenShardReader, check: Callable[[], None] | None = None
) -> None:
    cursor = count = 0
    v2 = reader.index_schema == INDEX_SCHEMA_V2
    ceiling = reader.index_record_bytes
    with (
        (reader.directory / "offsets.jsonl").open("rb") as stream,
        (reader.directory / "tokens.bin").open("rb", buffering=1024**2) as payload,
    ):
        while raw := stream.readline(ceiling + 1):
            if len(raw) > ceiling:
                raise ValueError("document index entry exceeds byte limit")
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError("document index entry must be an object")
            if record.get("source_id") != reader.manifest.source_id:
                raise ValueError("document source differs from shard source")
            if record.get("split") != "train":
                raise ValueError("training requires explicitly train-split documents")
            tokens = record.get("token_count")
            if type(tokens) is not int or tokens < 1 or record.get("token_start") != cursor:
                raise ValueError("document token index must be contiguous and nonempty")
            if v2:
                reader.check_read_window(tokens)
                raw_ids = payload.read(tokens * reader.token_bytes_size)
                dtype = "<u2" if reader.token_bytes_size == 2 else "<u4"
                ids = np.frombuffer(raw_ids, dtype=dtype)
                reader.validate_v2_ids(record, ids)
            elif record.get("token_byte_spans") is not None:
                validate_v1_spans(record, tokens)
            cursor += tokens
            count += 1
            if count % 4096 == 0 and check is not None:
                check()
    if cursor != reader.manifest.num_tokens or count != reader.manifest.num_documents:
        raise ValueError("document index coverage differs from shard manifest")
    if check is not None:
        check()
