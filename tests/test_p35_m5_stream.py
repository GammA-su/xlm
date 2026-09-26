"""P35 M5: the mixture stream and the P34 producer consume the frozen order (AUTHORED).

Data-level only: no model, no optimizer, no torch. The real spawned P34 producer
child is exercised through its public protocol; the only substitution is the
torch conversion of a prepared update, which this environment cannot import.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

import pytest

from p35_m5_support import (
    GLOBAL,
    TEXTS,
    batcher,
    orders,
    target_doc_sequence,
    write_sources,
)
from xlm.data.ordering import (
    OrderManifestError,
    build_membership,
    build_order_manifest,
    check_order_resume,
    write_order_manifest,
)
from xlm.data.sampling import MixtureStreamError
from xlm.data.sampling.prefetch import PrefetchingBatcher, PreparedUpdate, ProducerSpec


@pytest.fixture
def readers(tmp_path: Path) -> dict[str, Any]:
    return write_sources(tmp_path / "shards")


def _run(stream: Any, updates: int) -> tuple[list[Any], list[dict[str, Any]]]:
    batches: list[Any] = []
    states = []
    for _ in range(updates):
        step = stream.next_step_microbatches()
        assert step
        batches += step
        stream.commit()
        states.append(stream.get_state())
    return batches, states


def _per_source(batches: list[Any], source: str) -> list[str]:
    return [d for d in target_doc_sequence(batches) if d.startswith(f"{source}_")]


def _collapse(sequence: list[str]) -> list[str]:
    out: list[str] = []
    for doc in sequence:
        if not out or out[-1] != doc:
            out.append(doc)
    return out


def test_stream_follows_the_declared_order(readers: dict[str, Any]) -> None:
    order_a, order_b = orders(readers)
    for order in (order_a, order_b):
        batches, _ = _run(batcher(readers, order), 12)
        for source in TEXTS:
            expected = order["sources"][source]["ordered_doc_ids"]
            seen = _collapse(_per_source(batches, source))
            assert seen[: len(expected)] == expected[: len(seen)]
            assert len(seen) >= 2


def test_shard_native_stream_is_unchanged_without_an_order(readers: dict[str, Any]) -> None:
    batches, states = _run(batcher(readers, None), 6)
    for source in TEXTS:
        seen = _collapse(_per_source(batches, source))
        native = [f"{source}_{i}" for i in range(len(TEXTS[source]))]
        assert seen == native[: len(seen)]
    assert "document_order" not in states[-1]


def test_orders_change_documents_not_quotas_or_scheduling(readers: dict[str, Any]) -> None:
    """Quotas, global update sizes and the scheduler algorithm are order-independent.

    Per-window valid targets depend on where document boundaries fall (a BOS
    label is never a target), so the realized window interleaving may differ by
    order; the per-source quota deficits it serves may not drift beyond one window.
    """
    from xlm.data.sampling import compile_exposure_plan, validate_mixture

    order_a, order_b = orders(readers)
    native = batcher(readers, None)
    budget = 20 * GLOBAL + 7
    plan = compile_exposure_plan(
        native.recipe, validate_mixture(native.recipe, native.availability), budget, block_size=64
    ).to_dict()
    totals: dict[str, list[int]] = {}
    counters: dict[str, list[dict[str, int]]] = {}
    finals = {}
    traces = {}
    exposures = {}
    for label, order in (("native", None), ("a", order_a), ("b", order_b)):
        stream = batcher(readers, order, exposure_plan=copy.deepcopy(plan))
        remaining = budget
        totals[label], counters[label] = [], []
        while remaining > 0:
            step = stream.next_step_microbatches(remaining)
            assert step
            size = sum(b.metadata["valid_target_count"] for b in step)
            stream.commit()
            remaining -= size
            totals[label].append(size)
            counters[label].append(
                {
                    s: c["valid_targets"]
                    for s, c in stream.get_state()["scheduler"]["counters"].items()
                }
            )
        state = stream.get_state()
        finals[label] = {s: c["valid_targets"] for s, c in state["scheduler"]["counters"].items()}
        traces[label] = state["trace_digest"]
        exposures[label] = state["exposure_identity"]
    assert len(set(exposures.values())) == 1  # one frozen exposure plan for every order
    # Identical update sizes and the exact planned per-source quotas under every order.
    assert totals["native"] == totals["a"] == totals["b"]
    planned = {s: p["planned_targets"] for s, p in plan["projections"].items()}
    assert finals["native"] == finals["a"] == finals["b"] == planned
    for label in ("a", "b"):
        for native_step, ordered_step in zip(counters["native"], counters[label], strict=True):
            for source in TEXTS:
                assert abs(native_step[source] - ordered_step[source]) <= 2 * 16
    # The committed trace binds the order: different orders, different traces.
    assert len(set(traces.values())) == 3


def test_same_order_resume_is_exact(readers: dict[str, Any]) -> None:
    order_a, _ = orders(readers)
    reference, _ = _run(batcher(readers, order_a), 10)
    first = batcher(readers, order_a)
    part, states = _run(first, 4)
    resumed = batcher(readers, copy.deepcopy(order_a))
    resumed.load_state(json.loads(json.dumps(states[-1])))
    rest, _ = _run(resumed, 6)

    def flat(batches: list[Any]) -> list[Any]:
        return [
            (b.input_ids, b.labels, b.loss_mask, b.position_ids, b.metadata["target_doc_ids"])
            for b in batches
        ]

    assert flat(part + rest) == flat(reference)
    assert (
        resumed.get_state()["document_order"]["order_manifest_id"] == (order_a["order_manifest_id"])
    )


def test_epoch_repeat_replays_the_same_frozen_order(readers: dict[str, Any]) -> None:
    order_a, _ = orders(readers)
    stream = batcher(readers, order_a)
    batches, states = _run(stream, 40)
    epochs = states[-1]["epochs"]
    assert max(epochs.values()) >= 1, "the run must cross an epoch boundary"
    for source in TEXTS:
        expected = order_a["sources"][source]["ordered_doc_ids"]
        seen = _collapse(_per_source(batches, source))
        # Each epoch is the same frozen order again (no per-epoch reshuffle).
        cycle = expected * (len(seen) // len(expected) + 2)
        assert any(seen == cycle[k : k + len(seen)] for k in range(len(expected)))
        counters = states[-1]["scheduler"]["counters"][source]
        if epochs[source] >= 1:
            assert counters["repeated_targets"] > 0


def test_no_runtime_shuffle_global_rng_is_irrelevant(readers: dict[str, Any]) -> None:
    order_a, _ = orders(readers)
    random.seed(1)
    first, _ = _run(batcher(readers, order_a), 5)
    random.seed(999)
    random.random()
    second, _ = _run(batcher(readers, order_a), 5)
    assert [b.input_ids for b in first] == [b.input_ids for b in second]


def test_ordinary_resume_across_orders_is_refused(readers: dict[str, Any]) -> None:
    order_a, order_b = orders(readers)
    _, states_a = _run(batcher(readers, order_a), 3)
    _, states_native = _run(batcher(readers, None), 3)
    for state, order in (
        (states_a[-1], order_b),  # A -> B
        (states_a[-1], None),  # A -> shard-native
        (states_native[-1], order_a),  # pre-M5 -> A
    ):
        stream = batcher(readers, order)
        before = stream.get_state()
        with pytest.raises(MixtureStreamError, match="document order identity differs"):
            stream.load_state(state)
        assert stream.get_state() == before  # nothing was restored
        with pytest.raises(OrderManifestError) as info:
            check_order_resume(state, stream.get_state())
        assert info.value.code == "order_resume_mismatch"
    check_order_resume(states_a[-1], batcher(readers, order_a).get_state())
    check_order_resume(states_native[-1], batcher(readers, None).get_state())


def test_tampered_or_foreign_order_is_refused_at_stream_construction(
    readers: dict[str, Any], tmp_path: Path
) -> None:
    order_a, _ = orders(readers)
    forged = copy.deepcopy(order_a)
    ids = forged["sources"]["beta"]["ordered_doc_ids"]
    ids[0], ids[-1] = ids[-1], ids[0]
    with pytest.raises(MixtureStreamError, match="document order refused"):
        batcher(readers, forged)
    # A valid order for the same documents in other physical shards is not these shards.
    other = write_sources(tmp_path / "other", reverse=True)
    foreign = build_order_manifest(build_membership(other), order_seed=1)
    with pytest.raises(MixtureStreamError, match="shard"):
        batcher(readers, foreign)


# ----------------------------------------------------------------- P34 producer


def test_producer_spec_carries_and_rebuilds_the_same_order(readers: dict[str, Any]) -> None:
    order_a, _ = orders(readers)
    sync = batcher(readers, order_a)
    spec = ProducerSpec.from_batcher(sync)
    assert spec.document_order == order_a
    child_side = spec.build()  # exactly what the producer child executes
    expected, _ = _run(sync, 6)
    produced, _ = _run(child_side, 6)
    assert [(b.input_ids, b.metadata["target_doc_ids"]) for b in expected] == [
        (b.input_ids, b.metadata["target_doc_ids"]) for b in produced
    ]
    forged = copy.deepcopy(order_a)
    forged["derivation"]["order_seed"] += 1
    with pytest.raises(MixtureStreamError):
        ProducerSpec(**{**spec.__dict__, "document_order": forged}).build()


def test_spawned_producer_serves_the_synchronous_order(
    readers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real P34 child process produces the same next documents as the sync path.

    ``PreparedUpdate.to_microbatches`` builds torch tensors; without torch here it
    is replaced by the identity so the consumer returns the prepared update. The
    producer child, its batcher, the pipe protocol, state binding and content
    verification are all real.
    """
    monkeypatch.setattr(PreparedUpdate, "to_microbatches", lambda self: [self])
    order_a, _ = orders(readers)
    sync = batcher(readers, order_a)
    budget = GLOBAL * 20
    with PrefetchingBatcher(
        ProducerSpec.from_batcher(batcher(readers, order_a)),
        sync.get_state(),
        verify_content=True,
    ) as producer:
        for _ in range(6):
            expected = sync.next_step_microbatches(budget)
            (update,) = producer.next_step_microbatches(budget)
            docs = [row for b in expected for row in b.metadata["target_doc_ids"]]
            got = update.provenance.materialize(0, sum(update.rows))["target_doc_ids"]
            assert got == docs
            assert update.input_ids.tolist() == [row for b in expected for row in b.input_ids]
            sync.commit()
            producer.commit()
            budget -= update.valid_targets
            assert producer.get_state() == sync.get_state()
            assert (
                producer.get_state()["document_order"]["order_manifest_id"]
                == (order_a["order_manifest_id"])
            )


# ----------------------------------------------------------------- byte overhead


def test_index_representation_duplicates_no_token_payload(
    readers: dict[str, Any], tmp_path: Path
) -> None:
    order_a, order_b = orders(readers)
    before = {p: p.stat().st_size for p in (tmp_path / "shards").rglob("*") if p.is_file()}
    a = write_order_manifest(order_a, tmp_path / "orders" / "a.json")
    b = write_order_manifest(order_b, tmp_path / "orders" / "b.json")
    after = {p: p.stat().st_size for p in (tmp_path / "shards").rglob("*") if p.is_file()}
    assert before == after  # no shard file is written, copied or duplicated
    token_bytes = sum(size for p, size in before.items() if p.name == "tokens.bin")
    manifest_bytes = a.stat().st_size + b.stat().st_size
    assert token_bytes > 0 and manifest_bytes > 0
    # Two orders cost two small index files, never a second token payload.
    assert not list((tmp_path / "orders").rglob("tokens.bin"))
