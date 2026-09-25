"""P34 speculative producer: exactness, rollback, regeneration and process hygiene.

Every test uses authored shards and the real spawned producer process.
"""

from __future__ import annotations

import functools
import gc
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from prefetch_faults import faulty_batcher
from test_trainer_mixture import BODIES, make_doc
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe, MixtureStreamError
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.sampling.prefetch import (
    PrefetchEncodingError,
    PrefetchingBatcher,
    PrefetchProducerError,
    PrefetchProtocolError,
    ProducerSpec,
    _encode,
)
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer

torch = pytest.importorskip("torch")

CONTEXT = 32
GLOBAL = 96
UPDATES = 6


@pytest.fixture
def shards(tmp_path: Path) -> dict[str, TokenShardReader]:
    readers = {}
    for source_id, body in BODIES.items():
        directory = tmp_path / "shards" / source_id
        TokenShardWriter(
            directory, f"shard_{source_id}", source_id, ByteTokenizer(), pool_hash="pool_test"
        ).write_documents(
            [make_doc(f"{source_id}_{i}", body * (1 + i % 3), source_id) for i in range(12)],
            add_special_tokens=True,
        )
        readers[source_id] = TokenShardReader(directory)
    return readers


def make_batcher(
    shards: dict[str, TokenShardReader], global_targets: int = GLOBAL
) -> MixtureBatcher:
    tokenizer = ByteTokenizer()
    recipe = MixtureRecipe(
        mixture_id="prefetch_mix",
        components=[
            MixtureComponent(source_id="prose_src", weight=0.6),
            MixtureComponent(source_id="code_src", weight=0.4),
        ],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=64),
    )
    return MixtureBatcher(
        recipe,
        dict(shards),
        context_length=CONTEXT,
        global_batch_valid_targets=global_targets,
        microbatch_sequences=2,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        emit_tensors=True,
        max_open_shards=2,
    )


def prefetcher(
    shards: dict[str, TokenShardReader], factory: Any = None, **kwargs: Any
) -> PrefetchingBatcher:
    source = make_batcher(shards)
    spec = ProducerSpec.from_batcher(source)
    if factory is not None:
        spec = ProducerSpec(**{**spec.__dict__, "factory": factory})
    return PrefetchingBatcher(spec, source.get_state(), verify_content=True, **kwargs)


def record(batches: list[Any]) -> list[tuple[Any, ...]]:
    return [
        (
            batch.input_ids.tolist(),
            batch.labels.tolist(),
            batch.loss_mask.tolist(),
            batch.position_ids.tolist(),
            [[int(v) for v in row] for row in batch.metadata["input_attention_mask"]],
            batch.metadata["valid_target_count"],
        )
        for batch in batches
    ]


def reference_run(
    shards: dict[str, TokenShardReader], updates: int, budget: int
) -> tuple[list[Any], list[dict[str, Any]]]:
    batcher = make_batcher(shards)
    seen, states = [], []
    for _ in range(updates):
        batches = batcher.next_step_microbatches(budget)
        seen.append(record(batches))
        budget -= sum(int(b.loss_mask.sum()) for b in batches)
        batcher.commit()
        states.append(batcher.get_state())
    return seen, states


def test_prefetch_is_exact_against_synchronous_batcher(
    shards: dict[str, TokenShardReader],
) -> None:
    sync = make_batcher(shards)
    budget = GLOBAL * 50
    with prefetcher(shards) as pre:
        for _ in range(UPDATES):
            expected = sync.next_step_microbatches(budget)
            actual = pre.next_step_microbatches(budget)
            assert len(expected) == len(actual)
            for index, (a, b) in enumerate(zip(expected, actual, strict=True)):
                for name in ("input_ids", "labels", "loss_mask", "position_ids"):
                    left, right = getattr(a, name), getattr(b, name)
                    assert left.dtype == right.dtype and torch.equal(left, right)
                mask = torch.tensor(a.metadata["input_attention_mask"], dtype=torch.bool)
                assert torch.equal(mask, b.metadata["input_attention_mask"])
                assert a.segment_ids == b.segment_ids.tolist()
                assert {k: a.metadata[k] for k in b.metadata if k != "input_attention_mask"} == {
                    k: v for k, v in b.metadata.items() if k != "input_attention_mask"
                }
                provenance = pre.pending_update.microbatch_provenance(index)  # type: ignore[union-attr]
                assert provenance["source_attribution"] == a.source_attribution
                for key in (
                    "target_doc_ids",
                    "target_lineage_ids",
                    "target_byte_spans",
                    "target_token_offsets",
                ):
                    assert provenance[key] == a.metadata[key]
            budget -= sum(int(b.loss_mask.sum()) for b in expected)
            sync.commit()
            pre.commit()
            assert pre.get_state() == sync.get_state()
        stats = pre.stats()
    assert stats["takes"] == UPDATES and stats.get("discarded_stale", 0) == 0


def test_depth_two_and_final_partial_update(shards: dict[str, TokenShardReader]) -> None:
    budget = GLOBAL * 3 + 17  # the last update is partial and exact
    expected, states = reference_run(shards, 4, budget)
    with prefetcher(shards, depth=2) as pre:
        for index in range(4):
            batches = pre.next_step_microbatches(budget)
            assert record(batches) == expected[index]
            budget -= sum(int(b.loss_mask.sum()) for b in batches)
            pre.commit()
            assert pre.get_state() == states[index]
        assert budget == 0
        assert pre.get_state()["committed_valid_targets"] == GLOBAL * 3 + 17


@pytest.mark.parametrize("stage", ["sampling", "packing", "encode", "exit"])
def test_producer_failures_never_skip_or_duplicate(
    shards: dict[str, TokenShardReader], tmp_path: Path, stage: str
) -> None:
    budget = GLOBAL * 50
    expected, states = reference_run(shards, UPDATES, budget)
    factory = functools.partial(
        faulty_batcher, stage=stage, at=2, marker=str(tmp_path / f"{stage}.fired")
    )
    committed: list[Any] = []
    failures = 0
    with prefetcher(shards, factory=factory) as pre:
        while len(committed) < UPDATES:
            before = pre.get_state()
            try:
                batches = pre.next_step_microbatches(budget)
            except (PrefetchProducerError, RuntimeError):
                failures += 1
                assert pre.get_state() == before  # committed state never moves on failure
                if stage == "exit":
                    pre.restart()
                continue
            committed.append(record(batches))
            budget -= sum(int(b.loss_mask.sum()) for b in batches)
            pre.commit()
            assert pre.get_state() == states[len(committed) - 1]
    assert failures == 1
    assert committed == expected


def test_producer_startup_failure_is_reported(
    shards: dict[str, TokenShardReader], tmp_path: Path
) -> None:
    factory = functools.partial(
        faulty_batcher, stage="startup", at=0, marker=str(tmp_path / "startup.fired")
    )
    with pytest.raises(PrefetchProducerError, match="RuntimeError"):
        prefetcher(shards, factory=factory)
    with prefetcher(shards, factory=factory) as pre:  # the fault fired once
        assert pre.next_step_microbatches(GLOBAL)


def test_killed_producer_regenerates_identical_update(shards: dict[str, TokenShardReader]) -> None:
    budget = GLOBAL * 50
    with prefetcher(shards) as pre:
        for _ in range(2):
            batches = pre.next_step_microbatches(budget)
            budget -= sum(int(b.loss_mask.sum()) for b in batches)
            pre.commit()
        pending = pre.next_step_microbatches(budget)
        expected = pre.pending_update
        assert expected is not None
        pre.rollback()  # e.g. a consumer failure before commit
        psutil.Process(pre.producer_pid).kill()  # type: ignore[arg-type]
        with pytest.raises(PrefetchProducerError):
            pre.next_step_microbatches(budget)
        pre.restart()
        regenerated = pre.next_step_microbatches(budget)
        update = pre.pending_update
        assert update is not None
        assert update.content_digest == expected.content_digest
        assert update.start_state_digest == expected.start_state_digest
        assert update.end_state == expected.end_state
        assert record(regenerated) == record(pending)


def test_rollback_discards_stale_speculation_and_replays(
    shards: dict[str, TokenShardReader],
) -> None:
    budget = GLOBAL * 50
    with prefetcher(shards) as pre:
        first = record(pre.next_step_microbatches(budget))
        time.sleep(3)  # let the speculative next update arrive in the pipe
        pre.rollback()
        assert record(pre.next_step_microbatches(budget)) == first
        assert pre.stats()["discarded_stale"] >= 1
        pre.commit()


def test_budget_misprediction_regenerates_with_the_actual_budget(
    shards: dict[str, TokenShardReader],
) -> None:
    budget = GLOBAL * 50
    with prefetcher(shards) as pre:
        batches = pre.next_step_microbatches(budget)
        pre.commit()
        consumed = sum(int(b.loss_mask.sum()) for b in batches)
        # Not the predicted budget - consumed: the prefetched update is unusable.
        partial = pre.next_step_microbatches(40)
        assert sum(int(b.loss_mask.sum()) for b in partial) == 40
        assert pre.stats()["budget_mispredictions"] == 1
        pre.commit()
    sync = make_batcher(shards)
    assert sum(int(b.loss_mask.sum()) for b in sync.next_step_microbatches(budget)) == consumed
    sync.commit()
    assert record(sync.next_step_microbatches(40)) == record(partial)


def test_protocol_misuse_and_invalid_state_fail_closed(
    shards: dict[str, TokenShardReader],
) -> None:
    with prefetcher(shards) as pre:
        with pytest.raises(PrefetchProtocolError):
            pre.commit()
        pre.next_step_microbatches(GLOBAL)
        with pytest.raises(PrefetchProtocolError):
            pre.next_step_microbatches(GLOBAL)
        pre.rollback()
        committed = pre.get_state()
        foreign = dict(committed, mixture_identity="another-mixture")
        with pytest.raises(MixtureStreamError, match="mixture identity"):
            pre.load_state(foreign)
        assert pre.get_state() == committed
        assert pre.next_step_microbatches(GLOBAL)  # still serviceable after refusal
        pre.commit()
        resumed = pre.get_state()
        pre.load_state(resumed)
        assert pre.get_state() == resumed


def test_lossless_encoding_refuses_non_integer_provenance() -> None:
    from xlm.core.contracts import TrainingBatch

    row = [5, 6]
    batch = TrainingBatch(
        input_ids=[row],
        labels=[row],
        loss_mask=[[1, 1]],
        position_ids=[[0, 1]],
        segment_ids=[[0, 0]],
        source_attribution=[["s", "s"]],
        metadata={
            "packing_mode": "causal_stream",
            "valid_targets": 2,
            "valid_target_count": 2,
            "sequence_count": 1,
            "is_partial": False,
            "target_byte_spans": [[(0, 1), (1, 2.0)]],
            "target_doc_ids": [["d", "d"]],
            "target_token_offsets": [[0, 1]],
            "target_lineage_ids": [["", ""]],
            "input_attention_mask": [[1, 1]],
        },
    )
    with pytest.raises(PrefetchEncodingError):
        _encode([batch], 0, 0, None, "", None, 0.0)


def test_close_and_garbage_collection_leave_no_producer(
    shards: dict[str, TokenShardReader],
) -> None:
    pre = prefetcher(shards)
    pid = pre.producer_pid
    pre.close()
    pre.close()  # idempotent
    assert pid is not None and not psutil.pid_exists(pid)
    pre = prefetcher(shards)
    pid = pre.producer_pid
    del pre
    gc.collect()
    assert pid is not None
    assert _gone(pid, 15)


def test_producer_exits_when_consumer_process_dies(
    shards: dict[str, TokenShardReader], tmp_path: Path
) -> None:
    source = make_batcher(shards)
    spec = ProducerSpec.from_batcher(source)
    script = tmp_path / "orphan.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json, os, sys
            from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec
            if __name__ == "__main__":
                spec = ProducerSpec(**json.loads(sys.argv[1]))
                state = json.loads(sys.argv[2])
                pre = PrefetchingBatcher(spec, state)
                pre.next_step_microbatches({GLOBAL})
                print(pre.producer_pid, flush=True)
                os._exit(0)  # hard death: no close, no finalizers
            """
        )
    )
    import json

    completed = subprocess.run(
        [sys.executable, str(script), json.dumps(spec.__dict__), json.dumps(source.get_state())],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert _gone(int(completed.stdout.strip().splitlines()[-1]), 15)


def _gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False
