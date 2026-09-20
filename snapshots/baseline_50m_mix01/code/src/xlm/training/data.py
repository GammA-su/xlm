"""Deterministic training data batching with context carryover and replayable state.

Complying with XLM Contracts C07, C08, C09 and P05 Amendments 2 & 3:
- Exact prediction target budget completion (including non-multiples like 8+8+1).
- Context carryover: window k token T becomes window k+1 token 0.
- Causal target shift exactly once: input_ids = window[:-1], labels = window[1:].
- Masking: padding and BOS have loss_mask=0; valid EOS has loss_mask=1.
- Untrained suffix preservation on budget truncation.
- Replayable data cursor state with commit/rollback boundaries.
- Explicit error policies for empty data, exhausted data, and zero-valid targets.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from xlm.core.contracts import TrainingBatch
from xlm.data.tokens import TokenShardReader

try:
    import torch
except ImportError:
    torch = None  # type: ignore[assignment]


class DataError(Exception):
    """Base exception for data batching errors."""


class EmptyDataError(DataError):
    """Raised when data source contains no tokens or fewer than 2 tokens."""


class DataExhaustedError(DataError):
    """Raised when token stream is exhausted and repetition is not allowed."""


class ZeroValidTargetsError(DataError):
    """Raised when data source yields zero valid prediction targets."""


@dataclass
class DataCursorState:
    """Serializable snapshot of data streaming cursor and exposure state."""

    shard_id: str
    stream_token_offset: int = 0
    epoch: int = 0
    committed_valid_targets: int = 0
    processed_valid_targets: int = 0
    overlapping_context_token: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataCursorState:
        return cls(**data)


class BatcherProtocol(Protocol):
    """The data-source contract the trainer depends on.

    Both the single-source :class:`TrainingBatcher` and the multi-source
    ``MixtureBatcher`` satisfy it, so the trainer drives either without change.
    """

    def next_step_microbatches(self, remaining_budget: int | None = None) -> list[TrainingBatch]:
        """Assemble the microbatches for one optimizer update."""
        ...

    def commit(self) -> None:
        """Promote speculative progress after a successful update."""
        ...

    def rollback(self) -> None:
        """Discard speculative progress back to the last committed cursor."""
        ...

    def get_state(self) -> dict[str, Any]:
        """Serialize committed state for a checkpoint."""
        ...

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Restore committed state on resume."""
        ...


class TrainingBatcher:
    """Streaming batcher with context carryover and exact budget masking."""

    def __init__(
        self,
        data_source: TokenShardReader | Path | list[int],
        context_length: int = 512,
        global_batch_valid_targets: int = 65536,
        microbatch_sequences: int | None = None,
        pad_token_id: int = 0,
        bos_token_id: int = 1,
        eos_token_id: int = 2,
        source_id: str = "stream",
        max_document_exposures: int = 1,
        exhaustion_policy: str = "error",  # "error" | "repeat_bounded"
    ) -> None:
        if context_length <= 0:
            raise ValueError(f"context_length must be positive, got {context_length}")
        if global_batch_valid_targets <= 0:
            raise ValueError(
                f"global_batch_valid_targets must be positive, got {global_batch_valid_targets}"
            )
        if microbatch_sequences is not None and microbatch_sequences <= 0:
            raise ValueError(
                f"microbatch_sequences must be positive if specified, got {microbatch_sequences}"
            )
        if exhaustion_policy not in ("error", "repeat_bounded"):
            raise ValueError(
                f"Invalid exhaustion_policy '{exhaustion_policy}'. "
                "Must be 'error' or 'repeat_bounded'"
            )

        self.context_length = context_length
        self.global_batch_valid_targets = global_batch_valid_targets
        self.microbatch_sequences = microbatch_sequences
        self.pad_token_id = pad_token_id
        self.bos_token_id = bos_token_id
        self.eos_token_id = eos_token_id
        self.source_id = source_id
        self.max_document_exposures = max_document_exposures
        self.exhaustion_policy = exhaustion_policy

        # Initialize stream reader
        self._tokens_list: list[int] | None = None
        self._shard_reader: TokenShardReader | None = None

        if isinstance(data_source, list):
            self._tokens_list = list(data_source)
            shard_id = "in_memory"
            total_tokens = len(self._tokens_list)
        elif isinstance(data_source, TokenShardReader):
            self._shard_reader = data_source
            shard_id = data_source.manifest.shard_id
            total_tokens = data_source.manifest.num_tokens
        elif isinstance(data_source, Path):
            self._shard_reader = TokenShardReader(data_source)
            shard_id = self._shard_reader.manifest.shard_id
            total_tokens = self._shard_reader.manifest.num_tokens
        else:
            raise TypeError(f"Unsupported data_source type: {type(data_source)}")

        if total_tokens < 2:
            raise EmptyDataError(
                f"Data source has {total_tokens} tokens. At least 2 tokens are required."
            )

        self.total_stream_tokens = total_tokens
        self.shard_id = shard_id

        # Cursor states
        self.committed_cursor = DataCursorState(
            shard_id=shard_id,
            stream_token_offset=0,
            epoch=0,
            committed_valid_targets=0,
            processed_valid_targets=0,
            overlapping_context_token=None,
        )
        self.uncommitted_cursor = copy.deepcopy(self.committed_cursor)

    def _read_tokens(self, offset: int, count: int) -> list[int]:
        """Read count tokens starting at offset in the current stream."""
        if self._tokens_list is not None:
            return self._tokens_list[offset : offset + count]
        if self._shard_reader is not None:
            return self._shard_reader.read_tokens(start=offset, count=count)
        raise RuntimeError("No active token source")

    def get_state(self) -> dict[str, Any]:
        """Serialize committed data cursor state."""
        return self.committed_cursor.to_dict()

    def load_state(self, state_dict: dict[str, Any]) -> None:
        """Restore committed cursor state."""
        self.committed_cursor = DataCursorState.from_dict(state_dict)
        self.uncommitted_cursor = copy.deepcopy(self.committed_cursor)

    def commit(self) -> None:
        """Commit the uncommitted cursor state upon successful completion of an optimizer step."""
        self.committed_cursor = copy.deepcopy(self.uncommitted_cursor)

    def rollback(self) -> None:
        """Roll back uncommitted cursor state to the last committed checkpoint."""
        self.uncommitted_cursor = copy.deepcopy(self.committed_cursor)

    def next_step_microbatches(
        self,
        remaining_budget: int | None = None,
    ) -> list[TrainingBatch]:
        """Assemble microbatches for the next optimizer update.

        Args:
            remaining_budget: Optional remaining run target budget. If provided,
                the total valid targets across the step will not exceed
                min(global_batch_valid_targets, remaining_budget).

        Returns:
            List of TrainingBatch microbatches ready for accumulation.
        """
        step_target_limit = self.global_batch_valid_targets
        if remaining_budget is not None:
            step_target_limit = min(step_target_limit, remaining_budget)

        if step_target_limit <= 0:
            return []

        # We will collect sequences until target_limit is reached
        sequences_inputs: list[list[int]] = []
        sequences_labels: list[list[int]] = []
        sequences_loss_masks: list[list[int]] = []
        sequences_valid_counts: list[int] = []

        cursor = copy.deepcopy(self.uncommitted_cursor)
        step_valid_targets = 0

        while step_valid_targets < step_target_limit:
            needed_targets = step_target_limit - step_valid_targets

            # Determine carryover token
            carryover = cursor.overlapping_context_token
            if carryover is not None:
                needed_from_stream = self.context_length
            else:
                needed_from_stream = self.context_length + 1

            # Check stream bounds
            stream_offset = cursor.stream_token_offset
            available = self.total_stream_tokens - stream_offset

            if available < needed_from_stream:
                # Handle end of stream
                if self.exhaustion_policy == "error":
                    raise DataExhaustedError(
                        f"Data stream '{self.shard_id}' exhausted at token {stream_offset} "
                        f"(needed {needed_from_stream}, available {available}) "
                        f"in epoch {cursor.epoch}"
                    )
                else:
                    # repeat_bounded
                    next_epoch = cursor.epoch + 1
                    if next_epoch >= self.max_document_exposures:
                        raise DataExhaustedError(
                            f"Reached maximum document exposures ({self.max_document_exposures}) "
                            f"for shard '{self.shard_id}'"
                        )
                    cursor.epoch = next_epoch
                    cursor.stream_token_offset = 0
                    cursor.overlapping_context_token = None
                    carryover = None
                    needed_from_stream = self.context_length + 1
                    stream_offset = 0

            # Read from stream
            stream_tokens = self._read_tokens(stream_offset, needed_from_stream)
            if carryover is not None:
                window = [carryover] + stream_tokens
                stream_consumed_count = needed_from_stream
                stream_start_for_seq = stream_offset
            else:
                window = stream_tokens
                stream_consumed_count = needed_from_stream
                # The first token is context at stream_offset, targets start at stream_offset + 1
                stream_start_for_seq = stream_offset + 1

            input_ids = window[:-1]
            labels = window[1:]

            # Initial loss mask: padding and BOS excluded (0), EOS and content included (1)
            loss_mask = [
                1 if (tok != self.pad_token_id and tok != self.bos_token_id) else 0
                for tok in labels
            ]

            seq_valid_targets = sum(loss_mask)
            cursor.processed_valid_targets += seq_valid_targets

            if seq_valid_targets > needed_targets:
                # Budget truncation inside this sequence!
                # Keep exactly needed_targets valid targets, mask the rest to 0.
                kept_targets = 0
                last_committed_idx = -1
                for idx, valid in enumerate(loss_mask):
                    if valid == 1:
                        if kept_targets < needed_targets:
                            kept_targets += 1
                            last_committed_idx = idx
                        else:
                            loss_mask[idx] = 0

                step_valid_targets += needed_targets
                cursor.committed_valid_targets += needed_targets

                # Preserve untrained suffix:
                if last_committed_idx >= 0:
                    cursor.overlapping_context_token = labels[last_committed_idx]
                    cursor.stream_token_offset = stream_start_for_seq + last_committed_idx + 1
                sequences_inputs.append(input_ids)
                sequences_labels.append(labels)
                sequences_loss_masks.append(loss_mask)
                sequences_valid_counts.append(needed_targets)
                break
            else:
                # All valid targets in this sequence are committed
                step_valid_targets += seq_valid_targets
                cursor.committed_valid_targets += seq_valid_targets

                # The last token in labels is window[-1]
                cursor.overlapping_context_token = labels[-1]
                cursor.stream_token_offset = stream_offset + stream_consumed_count

                sequences_inputs.append(input_ids)
                sequences_labels.append(labels)
                sequences_loss_masks.append(loss_mask)
                sequences_valid_counts.append(seq_valid_targets)

        # Update uncommitted cursor to point to the end of this proposed step
        self.uncommitted_cursor = cursor

        if not sequences_inputs:
            return []

        # Split into microbatches
        mb_size = self.microbatch_sequences or len(sequences_inputs)
        microbatches: list[TrainingBatch] = []

        total_sequences = len(sequences_inputs)
        for start_idx in range(0, total_sequences, mb_size):
            end_idx = min(start_idx + mb_size, total_sequences)
            chunk_inputs = sequences_inputs[start_idx:end_idx]
            chunk_labels = sequences_labels[start_idx:end_idx]
            chunk_masks = sequences_loss_masks[start_idx:end_idx]
            chunk_batch_size = len(chunk_inputs)

            if torch is not None:
                tensor_inputs = torch.tensor(chunk_inputs, dtype=torch.long)
                tensor_labels = torch.tensor(chunk_labels, dtype=torch.long)
                tensor_masks = torch.tensor(chunk_masks, dtype=torch.bool)
                pos_ids = (
                    torch.arange(self.context_length, dtype=torch.long)
                    .unsqueeze(0)
                    .expand(chunk_batch_size, -1)
                )
            else:
                tensor_inputs = chunk_inputs
                tensor_labels = chunk_labels
                tensor_masks = chunk_masks
                pos_ids = [list(range(self.context_length)) for _ in range(chunk_batch_size)]

            mb = TrainingBatch(
                input_ids=tensor_inputs,
                labels=tensor_labels,
                loss_mask=tensor_masks,
                position_ids=pos_ids,
                source_attribution=[self.source_id] * chunk_batch_size,
                metadata={
                    "valid_target_count": sum(sequences_valid_counts[start_idx:end_idx]),
                    "sequence_count": chunk_batch_size,
                },
            )
            microbatches.append(mb)

        return microbatches
