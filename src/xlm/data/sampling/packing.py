"""Causal-stream and isolated-document packing into training windows.

Contract C07: for a stream window of T+1 IDs, feed the first T and target the next T.
On continuation, overlap only the last context token, so target positions are neither
dropped nor double-counted. Final partial windows get explicit masks. Source ownership
and byte spans survive windowing.

Two policies, deliberately distinct:

* ``causal_stream`` -- a continuous stream with EOS document boundaries and
  cross-document attention enabled. One segment per window.
* ``isolated_document`` -- each document is its own segment, with segment IDs that
  block cross-document attention and position IDs that reset per document.

They are different experiment fields. Switching between them changes what the model
sees, so it creates a new comparison contract rather than a tweak.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PackedWindow:
    """One training window, already shifted into inputs and targets."""

    input_ids: list[int]
    labels: list[int]
    loss_mask: list[int]
    position_ids: list[int]
    segment_ids: list[int]
    source_attribution: list[str]
    doc_ids: list[str] = field(default_factory=list)
    byte_spans: list[tuple[int, int]] = field(default_factory=list)
    token_offsets: list[int] = field(default_factory=list)
    lineage_ids: list[str] = field(default_factory=list)
    is_partial: bool = False

    @property
    def valid_targets(self) -> int:
        return sum(self.loss_mask)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentSpan:
    """A document's tokens plus the provenance that must survive packing."""

    doc_id: str
    source_id: str
    token_ids: list[int]
    byte_start: int = 0
    byte_end: int = 0


def shift_window(
    window_ids: list[int],
    context_length: int,
    pad_token_id: int,
    ignore_positions: set[int] | None = None,
) -> tuple[list[int], list[int], list[int], bool]:
    """Split a T+1 window into inputs, labels and a loss mask.

    Given ``window_ids`` of length T+1, the first T are fed and the next T are
    targeted, so ``labels[i]`` is the token that follows ``input_ids[i]``. A shorter
    window is padded and masked rather than silently dropped.
    """
    if context_length <= 0:
        raise ValueError(f"context_length must be positive, got {context_length}")
    if len(window_ids) < 2:
        raise ValueError("a window needs at least 2 token IDs to yield one target")

    usable = min(len(window_ids) - 1, context_length)
    inputs = window_ids[:usable]
    labels = window_ids[1 : usable + 1]
    mask = [1] * usable

    if ignore_positions:
        for index in ignore_positions:
            if 0 <= index < usable:
                mask[index] = 0

    is_partial = usable < context_length
    if is_partial:
        pad = context_length - usable
        inputs = inputs + [pad_token_id] * pad
        labels = labels + [pad_token_id] * pad
        mask = mask + [0] * pad

    return inputs, labels, mask, is_partial


class CausalStreamPacker:
    """Baseline packing: a continuous stream with EOS document boundaries.

    Cross-document attention is enabled, so the whole window is one segment and
    positions run continuously across document boundaries.
    """

    mode = "causal_stream"

    def __init__(
        self,
        context_length: int = 512,
        pad_token_id: int = 0,
        bos_token_id: int = 1,
    ) -> None:
        self.context_length = context_length
        self.pad_token_id = pad_token_id
        self.bos_token_id = bos_token_id

    def pack(
        self,
        window_ids: list[int],
        source_attribution: list[str],
        doc_ids: list[str] | None = None,
        byte_spans: list[tuple[int, int]] | None = None,
        carry_context: bool = False,
    ) -> PackedWindow:
        """Pack one T+1 window.

        ``carry_context`` records that this window continues a previous one by
        overlapping exactly one context token; the overlapped token is context here
        and was already a target there, so it is not counted twice.
        """
        # BOS is context only: it is never itself a target position (C07, C11).
        ignore = {
            i
            for i, tid in enumerate(window_ids[1:])
            if tid in (self.bos_token_id, self.pad_token_id)
        }

        inputs, labels, mask, is_partial = shift_window(
            window_ids, self.context_length, self.pad_token_id, ignore
        )

        attribution = list(source_attribution[: len(inputs)])
        attribution += [attribution[-1] if attribution else "unknown"] * (
            len(inputs) - len(attribution)
        )

        return PackedWindow(
            input_ids=inputs,
            labels=labels,
            loss_mask=mask,
            # Continuous positions: cross-document attention is enabled, so nothing resets.
            position_ids=list(range(len(inputs))),
            segment_ids=[0] * len(inputs),
            source_attribution=attribution,
            doc_ids=list(doc_ids or []),
            byte_spans=list(byte_spans or []),
            is_partial=is_partial,
        )


class IsolatedDocumentPacker:
    """Isolated-document packing with segment masks and per-document position reset.

    Each document becomes its own segment. A target whose input and label fall in
    different documents is masked out, because under isolation the model must not be
    asked to predict across a boundary it cannot attend across.
    """

    mode = "isolated_document"

    def __init__(
        self,
        context_length: int = 512,
        pad_token_id: int = 0,
        bos_token_id: int = 1,
    ) -> None:
        self.context_length = context_length
        self.pad_token_id = pad_token_id
        self.bos_token_id = bos_token_id

    def pack(self, documents: list[DocumentSpan]) -> PackedWindow:
        """Pack whole documents into one window, isolating each as a segment."""
        window_ids: list[int] = []
        segment_ids: list[int] = []
        position_ids: list[int] = []
        attribution: list[str] = []
        doc_ids: list[str] = []
        byte_spans: list[tuple[int, int]] = []

        for segment_index, document in enumerate(documents):
            for position, token_id in enumerate(document.token_ids):
                if len(window_ids) >= self.context_length + 1:
                    break
                window_ids.append(token_id)
                segment_ids.append(segment_index)
                position_ids.append(position)  # positions reset per document
                attribution.append(document.source_id)
            doc_ids.append(document.doc_id)
            byte_spans.append((document.byte_start, document.byte_end))

        if len(window_ids) < 2:
            raise ValueError("isolated packing needs at least 2 tokens to yield one target")

        usable = min(len(window_ids) - 1, self.context_length)
        inputs = window_ids[:usable]
        labels = window_ids[1 : usable + 1]
        mask = [1] * usable

        for index in range(usable):
            # A target crossing a segment boundary is not predictable under isolation.
            if segment_ids[index] != segment_ids[index + 1]:
                mask[index] = 0
            if labels[index] in (self.bos_token_id, self.pad_token_id):
                mask[index] = 0

        is_partial = usable < self.context_length
        if is_partial:
            pad = self.context_length - usable
            inputs = inputs + [self.pad_token_id] * pad
            labels = labels + [self.pad_token_id] * pad
            mask = mask + [0] * pad
            segment_slice = segment_ids[:usable] + [-1] * pad
            position_slice = position_ids[:usable] + [0] * pad
            attribution_slice = attribution[:usable] + ["padding"] * pad
        else:
            segment_slice = segment_ids[:usable]
            position_slice = position_ids[:usable]
            attribution_slice = attribution[:usable]

        return PackedWindow(
            input_ids=inputs,
            labels=labels,
            loss_mask=mask,
            position_ids=position_slice,
            segment_ids=segment_slice,
            source_attribution=attribution_slice,
            doc_ids=doc_ids,
            byte_spans=byte_spans,
            is_partial=is_partial,
        )


def build_packer(
    mode: str, context_length: int, pad_token_id: int, bos_token_id: int
) -> CausalStreamPacker | IsolatedDocumentPacker:
    """Construct the packer for a declared policy mode."""
    if mode == "causal_stream":
        return CausalStreamPacker(context_length, pad_token_id, bos_token_id)
    if mode == "isolated_document":
        return IsolatedDocumentPacker(context_length, pad_token_id, bos_token_id)
    raise ValueError(
        f"unknown packing mode '{mode}'; supported: 'causal_stream', 'isolated_document'"
    )
