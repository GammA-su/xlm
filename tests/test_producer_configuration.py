"""The opt-in producer execution route retains the default and rejects bad modes."""

from __future__ import annotations

from typing import Any

import pytest

from test_prefetch import shards  # noqa: F401  (fixture)
from test_trainer_mixture import build_recipe
from xlm.config.schemas import TrainingConfig
from xlm.data.sampling import MixtureBatcher
from xlm.data.sampling.prefetch import PrefetchingBatcher
from xlm.data.tokens import TokenShardReader
from xlm.tokenizers.byte import ByteTokenizer
from xlm.training.inputs import MixtureInput, build_training_batcher


def test_configuration_default_and_lazy_opt_in(shards: dict[str, TokenShardReader]) -> None:  # noqa: F811
    assert TrainingConfig.model_fields["producer_prefetch"].default == "off"
    assert PrefetchingBatcher.requires_cuda_commit_barrier is True
    assert PrefetchingBatcher.close_on_trainer_exit is True
    settings: dict[str, Any] = {
        "context_length": 32,
        "global_batch_valid_targets": 64,
        "microbatch_sequences": 2,
        "budget": {"max_valid_targets": 128},
    }
    source = MixtureInput(build_recipe(), shards)
    with build_training_batcher(source, {}, settings, ByteTokenizer()) as ordinary:
        assert isinstance(ordinary, MixtureBatcher)
    settings["producer_prefetch"] = "process_depth1"
    with build_training_batcher(source, {}, settings, ByteTokenizer()) as producer:
        assert isinstance(producer, PrefetchingBatcher)
        assert producer.depth == 1
        assert producer.verify_content is True
        batches = producer.next_step_microbatches(64)
        assert sum(int(b.loss_mask.sum()) for b in batches) == 64
        producer.commit()
        assert producer.get_state()["committed_valid_targets"] == 64
    with pytest.raises(ValueError, match="explicit mixture"):
        build_training_batcher([1, 2, 3], {}, settings, None)
    settings["producer_prefetch"] = "ignored_typo"
    with pytest.raises(ValueError, match="Unknown"):
        build_training_batcher(source, {}, settings, ByteTokenizer())
