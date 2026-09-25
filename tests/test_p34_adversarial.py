"""Bounded adversarial checks of the final P34 optimizer and pipe boundaries."""

from __future__ import annotations

import copy
import dataclasses
import threading
import time

import psutil
import pytest

from test_prefetch import GLOBAL, make_batcher, prefetcher, shards  # noqa: F401
from xlm.data.sampling.prefetch import PrefetchProducerError, PrefetchProtocolError
from xlm.data.tokens import TokenShardReader


def test_control_send_has_a_deadline(shards: dict[str, TokenShardReader]) -> None:  # noqa: F811
    with prefetcher(shards) as pre:
        child = psutil.Process(pre.producer_pid)
        child.suspend()
        before = pre.get_state()
        pre.timeout_seconds = 0.1

        def rescue() -> None:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass

        # The test itself remains bounded even on the broken candidate.
        watchdog = threading.Timer(3, rescue)
        watchdog.start()
        begin = time.monotonic()
        try:
            with pytest.raises(PrefetchProducerError):
                pre._send(("reset", 0, {"padding": "x" * (2 * 1024**2)}))
            assert time.monotonic() - begin < 2
            assert pre.get_state() == before
            assert pre.pending_update is None
        finally:
            rescue()
            watchdog.cancel()
            watchdog.join(timeout=1)


@pytest.mark.parametrize("budget", [GLOBAL * 2, GLOBAL * 2 - 1, GLOBAL * 2 + 1, 1])
def test_final_budget_rollback_and_reload(
    shards: dict[str, TokenShardReader],  # noqa: F811
    budget: int,
) -> None:
    from test_prefetch import record

    with make_batcher(shards) as sync, prefetcher(shards) as pre:
        remaining = budget
        while remaining:
            expected = sync.next_step_microbatches(remaining)
            before = pre.get_state()
            first = pre.next_step_microbatches(remaining)
            assert record(first) == record(expected)
            if remaining <= GLOBAL:
                pre.rollback()
                pre.load_state(before)
                assert record(pre.next_step_microbatches(remaining)) == record(expected)
            consumed = sum(int(b.loss_mask.sum()) for b in expected)
            assert 0 < consumed <= remaining
            remaining -= consumed
            sync.commit()
            pre.commit()
            assert pre.get_state() == sync.get_state()
        assert pre.get_state()["committed_valid_targets"] == budget


@pytest.mark.parametrize(
    "field",
    [
        "input_ids",
        "labels",
        "loss_mask",
        "position_ids",
        "segment_ids",
        "input_attention_mask",
        "attribution",
        "doc_ids",
        "lineage_ids",
        "byte_spans",
        "token_offsets",
        "strings",
        "end_state",
        "start_state_digest",
        "valid_targets",
        "rows",
        "generation",
        "ordinal",
        "remaining_budget",
    ],
)
def test_independent_update_tampering_is_rejected(
    shards: dict[str, TokenShardReader],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    with prefetcher(shards) as pre:
        initial = pre.get_state()
        update = copy.deepcopy(pre._take(GLOBAL))
        if field in ("attribution", "doc_ids", "lineage_ids", "byte_spans", "token_offsets"):
            getattr(update.provenance, field).flat[0] += 1
        elif field == "strings":
            update = dataclasses.replace(
                update,
                provenance=dataclasses.replace(
                    update.provenance, strings=update.provenance.strings + ("tampered",)
                ),
            )
        elif field == "end_state":
            assert update.end_state is not None
            update.end_state["trace_digest"] = "tampered"
        elif field == "start_state_digest":
            update = dataclasses.replace(update, start_state_digest="tampered")
        elif field == "rows":
            update = dataclasses.replace(update, rows=())
        elif field in ("valid_targets", "generation", "ordinal", "remaining_budget"):
            update = dataclasses.replace(update, **{field: getattr(update, field) + 1})
        elif field == "input_attention_mask":
            update.input_attention_mask.flat[0] = not update.input_attention_mask.flat[0]
        else:
            getattr(update, field).flat[0] += 1
        monkeypatch.setattr(pre, "_take", lambda budget: update)
        with pytest.raises(PrefetchProtocolError):
            pre.next_step_microbatches(GLOBAL)
        assert pre.pending_update is None
        assert pre.get_state() == initial


def test_frame_admission_rejects_before_unpickling(monkeypatch: pytest.MonkeyPatch) -> None:
    import multiprocessing

    import xlm.data.sampling.prefetch_transport as transport

    monkeypatch.setattr(transport, "MAX_FRAME_BYTES", 1024)
    parent, child = multiprocessing.get_context("spawn").Pipe()
    bounded = transport.BoundedConnection(parent, 2, 1)
    try:
        child.send_bytes(b"x" * 2048)
        with pytest.raises(OSError, match="receive failed"):
            bounded.recv()
        with pytest.raises(OSError, match="byte limit"):
            transport.send_message(child, ("oversize", b"x" * 2048))
    finally:
        child.close()
        bounded.close()


def test_large_numpy_frame_uses_bounded_pickle_buffers() -> None:
    import multiprocessing

    import numpy as np

    from xlm.data.sampling.prefetch_transport import BoundedConnection, send_message

    parent, child = multiprocessing.get_context("spawn").Pipe()
    bounded = BoundedConnection(parent, 2, 2)
    expected = np.arange(65536, dtype=np.int64)
    try:
        send_message(child, ("array", expected))
        kind, actual = bounded.recv()
        assert kind == "array" and np.array_equal(expected, actual)
    finally:
        child.close()
        bounded.close()
