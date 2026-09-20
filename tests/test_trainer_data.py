"""Tests for deterministic training data batching and replayable cursor state."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.training.data import (
    DataExhaustedError,
    EmptyDataError,
    TrainingBatcher,
)


def test_carryover_context_and_causal_shifting() -> None:
    """Verify context carryover: window k token T becomes window k+1 token 0."""
    # 10 tokens: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    tokens = list(range(10, 20))
    context_length = 3

    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=3,  # 1 sequence per step
        microbatch_sequences=1,
    )

    # Step 1: Window 0 -> reads 4 tokens: [10, 11, 12, 13]
    mbs_1 = batcher.next_step_microbatches()
    assert len(mbs_1) == 1
    batch_1 = mbs_1[0]
    assert batch_1.input_ids.tolist() == [[10, 11, 12]]
    assert batch_1.labels.tolist() == [[11, 12, 13]]
    assert batch_1.loss_mask.tolist() == [[True, True, True]]
    batcher.commit()

    # Step 2: Window 1 -> carryover is 13, reads 3 tokens: [14, 15, 16]
    mbs_2 = batcher.next_step_microbatches()
    assert len(mbs_2) == 1
    batch_2 = mbs_2[0]
    assert batch_2.input_ids.tolist() == [[13, 14, 15]]
    assert batch_2.labels.tolist() == [[14, 15, 16]]
    assert batch_2.loss_mask.tolist() == [[True, True, True]]
    batcher.commit()

    # Step 3: Window 2 -> carryover is 16, reads 3 tokens: [17, 18, 19]
    mbs_3 = batcher.next_step_microbatches()
    assert len(mbs_3) == 1
    batch_3 = mbs_3[0]
    assert batch_3.input_ids.tolist() == [[16, 17, 18]]
    assert batch_3.labels.tolist() == [[17, 18, 19]]
    assert batch_3.loss_mask.tolist() == [[True, True, True]]
    batcher.commit()


def test_masking_excludes_padding_bos_and_retains_eos() -> None:
    """Verify masking policy: pad=0, bos=1 excluded (0); eos=2 retained (1)."""
    # Stream with BOS, content, EOS, and PAD
    # BOS=1, content=10, 11, EOS=2, PAD=0, PAD=0
    tokens = [1, 10, 11, 2, 0, 0, 12]
    context_length = 6

    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=4,
        pad_token_id=0,
        bos_token_id=1,
        eos_token_id=2,
    )

    mbs = batcher.next_step_microbatches()
    assert len(mbs) == 1
    b = mbs[0]
    assert b.input_ids.tolist() == [[1, 10, 11, 2, 0, 0]]
    # labels: [10, 11, 2, 0, 0, 12]
    # mask: 10 (1), 11 (1), EOS 2 (1), PAD 0 (0), PAD 0 (0), 12 (1)
    assert b.labels.tolist() == [[10, 11, 2, 0, 0, 12]]
    assert b.loss_mask.tolist() == [[True, True, True, False, False, True]]
    assert b.metadata["valid_target_count"] == 4


def test_exact_budget_completion_8_8_1() -> None:
    """Verify exact token budget completion on non-multiples (8 + 8 + 1 = 17 targets)."""
    # 25 tokens -> enough for 24 targets
    tokens = list(range(100, 125))
    total_budget = 17
    global_batch_targets = 8
    context_length = 8

    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=global_batch_targets,
    )

    committed_targets = 0

    # Update 1: remaining budget 17 -> step limit min(8, 17) = 8
    mbs_1 = batcher.next_step_microbatches(remaining_budget=total_budget - committed_targets)
    assert len(mbs_1) == 1
    assert mbs_1[0].metadata["valid_target_count"] == 8
    assert int(mbs_1[0].loss_mask.sum().item()) == 8
    batcher.commit()
    committed_targets += 8
    assert committed_targets == 8

    # Update 2: remaining budget 9 -> step limit min(8, 9) = 8
    mbs_2 = batcher.next_step_microbatches(remaining_budget=total_budget - committed_targets)
    assert len(mbs_2) == 1
    assert mbs_2[0].metadata["valid_target_count"] == 8
    assert int(mbs_2[0].loss_mask.sum().item()) == 8
    batcher.commit()
    committed_targets += 8
    assert committed_targets == 16

    # Update 3: remaining budget 1 -> step limit min(8, 1) = 1
    mbs_3 = batcher.next_step_microbatches(remaining_budget=total_budget - committed_targets)
    assert len(mbs_3) == 1
    assert mbs_3[0].metadata["valid_target_count"] == 1
    assert int(mbs_3[0].loss_mask.sum().item()) == 1
    # Only the first target is active
    assert mbs_3[0].loss_mask[0].tolist() == [True, False, False, False, False, False, False, False]
    batcher.commit()
    committed_targets += 1
    assert committed_targets == 17

    # Run complete: remaining budget 0
    mbs_done = batcher.next_step_microbatches(remaining_budget=0)
    assert len(mbs_done) == 0


def test_untrained_suffix_preservation_and_replay() -> None:
    """Verify that budget truncation preserves untrained suffix for exact continuation."""
    tokens = list(range(100, 150))
    context_length = 4

    # Run A: 3 targets then commit, then 3 targets
    batcher_a = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=4,
    )

    # Step 1: Truncate to 3 targets (out of 4)
    mbs_1 = batcher_a.next_step_microbatches(remaining_budget=3)
    assert int(mbs_1[0].loss_mask.sum().item()) == 3
    # Targets committed: 101, 102, 103 (input context 100)
    assert mbs_1[0].labels[0][:3].tolist() == [101, 102, 103]
    batcher_a.commit()
    saved_state = batcher_a.get_state()

    # Step 2: Next 3 targets
    mbs_2_a = batcher_a.next_step_microbatches(remaining_budget=3)
    targets_2_a = [
        label
        for label, mask in zip(
            mbs_2_a[0].labels[0].tolist(), mbs_2_a[0].loss_mask[0].tolist(), strict=True
        )
        if mask
    ]

    # Run B: Fresh batcher restored from saved_state
    batcher_b = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=4,
    )
    batcher_b.load_state(saved_state)

    mbs_2_b = batcher_b.next_step_microbatches(remaining_budget=3)
    targets_2_b = [
        label
        for label, mask in zip(
            mbs_2_b[0].labels[0].tolist(), mbs_2_b[0].loss_mask[0].tolist(), strict=True
        )
        if mask
    ]

    # Both must predict exact same targets
    assert targets_2_a == targets_2_b
    # Target 4 in stream was 104, so targets should be [104, 105, 106]
    assert targets_2_a == [104, 105, 106]


def test_microbatch_sequences_chunking() -> None:
    """Verify microbatch splitting into smaller chunks."""
    tokens = list(range(1000, 1050))
    context_length = 4
    # 4 sequences of 4 targets = 16 targets
    # microbatch_sequences = 2 -> should yield 2 microbatches of 2 sequences each
    batcher = TrainingBatcher(
        data_source=tokens,
        context_length=context_length,
        global_batch_valid_targets=16,
        microbatch_sequences=2,
    )

    mbs = batcher.next_step_microbatches()
    assert len(mbs) == 2
    assert mbs[0].input_ids.shape == (2, 4)
    assert mbs[1].input_ids.shape == (2, 4)
    assert mbs[0].metadata["valid_target_count"] == 8
    assert mbs[1].metadata["valid_target_count"] == 8


def test_empty_and_exhausted_data_error_policies() -> None:
    """Verify explicit error handling for empty data and exhausted sources."""
    # Empty data
    with pytest.raises(EmptyDataError):
        TrainingBatcher(data_source=[], context_length=4)

    # 1 token is fewer than 2
    with pytest.raises(EmptyDataError):
        TrainingBatcher(data_source=[42], context_length=4)

    # Exhausted data with error policy
    batcher = TrainingBatcher(
        data_source=[1, 2, 3, 4],
        context_length=4,  # needs 5 tokens for first window
        exhaustion_policy="error",
    )
    with pytest.raises(DataExhaustedError):
        batcher.next_step_microbatches()


def test_training_batcher_with_token_shard_reader(tmp_path: Path) -> None:
    """Verify TrainingBatcher reads from TokenShardReader on disk."""
    from xlm.core.contracts import CanonicalDocument
    from xlm.data.tokens import TokenShardReader, TokenShardWriter
    from xlm.tokenizers.byte import ByteTokenizer

    tok = ByteTokenizer()
    writer = TokenShardWriter(
        output_dir=tmp_path / "shard_001",
        shard_id="shard_001",
        source_id="test_src",
        tokenizer=tok,
    )
    docs = [
        CanonicalDocument(
            doc_id="doc_1",
            source_id="test_src",
            source_revision="1",
            source_file="test.txt",
            source_row=1,
            raw_hash="hash1",
            clean_hash="hash1",
            text="Hello world from XLM token shard!",
            utf8_byte_count=len(b"Hello world from XLM token shard!"),
            language="en",
            language_confidence=1.0,
            document_kind="text",
            source_metadata={},
            parent_ids=[],
            license_reference="CC-0",
            transform_log=[],
            quality_reasons=[],
            cluster_ids={},
            split="train",
        )
    ]
    _ = writer.write_documents(docs)
    reader = TokenShardReader(tmp_path / "shard_001")

    batcher = TrainingBatcher(
        data_source=reader,
        context_length=8,
        global_batch_valid_targets=8,
    )
    mbs = batcher.next_step_microbatches()
    assert len(mbs) == 1
    assert mbs[0].input_ids.shape == (1, 8)
    assert mbs[0].labels.shape == (1, 8)
    assert mbs[0].metadata["valid_target_count"] == 8
