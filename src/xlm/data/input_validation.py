"""Bounded C07 training-index validation; v2 token payloads are read sequentially."""

from __future__ import annotations

import json
from collections.abc import Callable

import numpy as np

from xlm.data.tokens import INDEX_SCHEMA_V2, TokenShardReader


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
            else:
                spans = record.get("token_byte_spans")
                if spans is not None and (
                    len(spans) != tokens
                    or any(
                        len(span) != 2
                        or any(type(n) is not int for n in span)
                        or not 0 <= span[0] <= span[1] <= record["byte_count"]
                        for span in spans
                    )
                ):
                    raise ValueError("invalid per-token canonical byte spans")
            cursor += tokens
            count += 1
            if count % 4096 == 0 and check is not None:
                check()
    if cursor != reader.manifest.num_tokens or count != reader.manifest.num_documents:
        raise ValueError("document index coverage differs from shard manifest")
    if check is not None:
        check()
