"""Ordered-document view over an unchanged token shard (P35 M5 stream integration).

``MixtureBatcher`` addresses a source through a per-source token cursor. With a
frozen order manifest, that cursor addresses a *virtual* stream: the source's
documents concatenated whole, in manifest order. This index maps a virtual
position to (document, local offset) and reads the unchanged physical payload.

- Documents are never split, merged, retokenized or reordered internally: a
  virtual range is served from each document's own contiguous physical span.
- Returned index records are the physical records with ``token_start``
  rebased to the virtual start (``physical_token_start`` keeps the original);
  document-local fields (byte spans, BOS/EOS positions, lineage, split, ...)
  are untouched.
- Memory is O(documents): four integers per document plus a doc-id map during
  construction. Records are read lazily by seeking ``offsets.jsonl``.
"""

from __future__ import annotations

import json
from array import array
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from xlm.data.ordering.manifest import OrderManifestError
from xlm.data.ordering.membership import MAX_DOCUMENTS, MAX_INDEX_LINE_BYTES, TRAIN_SPLIT
from xlm.data.tokens import TokenShardReader

TokenFetch = Callable[[int, int], list[int]]


class OrderedSourceIndex:
    """Virtual token coordinates of one source under a frozen document order."""

    def __init__(
        self,
        reader: TokenShardReader,
        ordered_doc_ids: Sequence[str],
        *,
        max_documents: int = MAX_DOCUMENTS,
        cache_records: int = 8,
    ) -> None:
        self.reader = reader
        self.source_id = reader.manifest.source_id
        native: dict[str, int] = {}
        index_offsets = array("q")
        physical_starts = array("q")
        counts = array("q")
        path = reader.directory / "offsets.jsonl"
        with path.open("rb") as stream:
            while True:
                offset = stream.tell()
                raw = stream.readline(MAX_INDEX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_INDEX_LINE_BYTES:
                    raise OrderManifestError("document index entry exceeds 8 MiB")
                if not raw.strip():
                    continue
                record = json.loads(raw)
                doc_id = record.get("doc_id")
                if record.get("split") != TRAIN_SPLIT:
                    raise OrderManifestError(
                        f"source '{self.source_id}' holds non-train document {doc_id!r}; "
                        "an ordered training stream admits train documents only",
                        code="membership_split_violation",
                    )
                if not isinstance(doc_id, str) or doc_id in native:
                    raise OrderManifestError(f"source '{self.source_id}' doc ids are not unique")
                if len(native) >= max_documents:
                    raise OrderManifestError(f"source index exceeds {max_documents} documents")
                native[doc_id] = len(counts)
                index_offsets.append(offset)
                physical_starts.append(int(record["token_start"]))
                counts.append(int(record["token_count"]))
        if len(ordered_doc_ids) != len(native) or len(set(ordered_doc_ids)) != len(native):
            raise OrderManifestError(
                f"order for '{self.source_id}' is not a permutation of the shard's documents",
                code="order_membership_mismatch",
            )
        self._order = array("q")
        self._virtual_starts = array("q")
        position = 0
        for doc_id in ordered_doc_ids:
            ordinal = native.get(doc_id)
            if ordinal is None:
                raise OrderManifestError(
                    f"ordered document '{doc_id}' is not in source '{self.source_id}'",
                    code="order_membership_mismatch",
                )
            self._order.append(ordinal)
            self._virtual_starts.append(position)
            position += counts[ordinal]
        if position != reader.manifest.num_tokens:
            raise OrderManifestError(f"ordered documents of '{self.source_id}' miss tokens")
        self.total_tokens = position
        self._index_offsets = index_offsets
        self._physical_starts = physical_starts
        self._counts = counts
        self._records: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._cache_records = max(1, cache_records)

    @property
    def document_count(self) -> int:
        return len(self._order)

    def _position(self, position: int) -> int:
        if not 0 <= position < self.total_tokens:
            raise OrderManifestError(f"virtual position {position} outside '{self.source_id}'")
        return bisect_right(self._virtual_starts, position) - 1

    def physical_position(self, position: int) -> int:
        """Physical ``tokens.bin`` offset of one virtual position."""
        k = self._position(position)
        return self._physical_starts[self._order[k]] + position - self._virtual_starts[k]

    def read(self, fetch: TokenFetch, start: int, count: int) -> list[int]:
        """Tokens of the virtual range, served per whole-document physical span."""
        if start < 0 or count < 0 or start > self.total_tokens:
            raise OrderManifestError("invalid virtual token slice")
        end = min(self.total_tokens, start + count)
        tokens: list[int] = []
        position = start
        while position < end:
            k = self._position(position)
            ordinal = self._order[k]
            local = position - self._virtual_starts[k]
            take = min(end - position, self._counts[ordinal] - local)
            tokens.extend(fetch(self._physical_starts[ordinal] + local, take))
            position += take
        return tokens

    def document_at(self, position: int) -> dict[str, Any]:
        """The index record of the document holding a virtual position."""
        k = self._position(position)
        cached = self._records.get(k)
        if cached is not None:
            self._records.move_to_end(k)
            return cached
        ordinal = self._order[k]
        with (self.reader.directory / "offsets.jsonl").open("rb") as stream:
            stream.seek(self._index_offsets[ordinal])
            raw = stream.readline(MAX_INDEX_LINE_BYTES + 1)
        record: dict[str, Any] = json.loads(raw)
        if int(record["token_start"]) != self._physical_starts[ordinal]:
            raise OrderManifestError("document index changed while in use")
        record["physical_token_start"] = record["token_start"]
        record["token_start"] = self._virtual_starts[k]
        self._records[k] = record
        if len(self._records) > self._cache_records:
            self._records.popitem(last=False)
        return record

    def ordered_doc_ids(self, first: int, last: int) -> list[str]:
        """Doc ids at virtual document positions ``[first, last)`` (for evidence)."""
        return [
            str(self.document_at(self._virtual_starts[k])["doc_id"]) for k in range(first, last)
        ]
