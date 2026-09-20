"""Acceptance tests for packing semantics and the quota scheduler.

Covers C07: the T+1 shift with one-token overlap so no target is dropped or
double-counted; isolated-document segment masks and position reset; EOS attribution;
BOS and padding excluded from the budget; observed shares within a documented bound;
and exhaustion handled by the declared policy with no silent renormalization.
"""

from __future__ import annotations

import pytest

from xlm.data.sampling import (
    CausalStreamPacker,
    DocumentSpan,
    ExhaustionPolicy,
    IsolatedDocumentPacker,
    MixtureComponent,
    MixtureRecipe,
    QuotaScheduler,
    RepeatBudgetExceededError,
    ScheduleState,
    SourceAvailability,
    SourceExhaustedError,
    build_packer,
    shift_window,
)

PAD, BOS, EOS = 0, 1, 2


def availability(**sizes: int) -> dict[str, SourceAvailability]:
    return {
        source_id: SourceAvailability(
            source_id=source_id,
            shard_id=f"shard_{source_id}",
            valid_targets=targets,
            content_tokens=int(targets * 0.99),
            eos_tokens=int(targets * 0.01),
            canonical_bytes=targets * 4,
            num_documents=max(1, targets // 100),
        )
        for source_id, targets in sizes.items()
    }


def recipe(**weights: float) -> MixtureRecipe:
    return MixtureRecipe(
        mixture_id="m",
        components=[MixtureComponent(source_id=s, weight=w) for s, w in weights.items()],
    )


# ------------------------------------------------------------------- shift window


def test_shift_feeds_first_t_and_targets_next_t() -> None:
    """A T+1 window yields T inputs and T labels, each label following its input."""
    window = [10, 11, 12, 13, 14]
    inputs, labels, mask, partial = shift_window(window, context_length=4, pad_token_id=PAD)

    assert inputs == [10, 11, 12, 13]
    assert labels == [11, 12, 13, 14]
    assert mask == [1, 1, 1, 1]
    assert not partial
    for i in range(len(inputs)):
        assert labels[i] == window[i + 1], "label must be the token following its input"


def test_partial_window_is_padded_and_masked_not_dropped() -> None:
    """A short tail must be masked, never silently discarded."""
    inputs, labels, mask, partial = shift_window([7, 8, 9], context_length=6, pad_token_id=PAD)

    assert partial
    assert len(inputs) == len(labels) == len(mask) == 6
    assert mask == [1, 1, 0, 0, 0, 0]
    assert inputs[:2] == [7, 8]
    assert labels[:2] == [8, 9]
    assert sum(mask) == 2, "only real targets count toward the budget"


def test_window_shorter_than_two_tokens_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 2 token IDs"):
        shift_window([5], context_length=4, pad_token_id=PAD)


def test_one_token_overlap_neither_drops_nor_double_counts_targets() -> None:
    """The contract's continuation rule, checked against the whole stream.

    Chaining windows with a single overlapping context token must reproduce exactly
    the set of (input, label) pairs of the unbroken stream: nothing missing, nothing
    counted twice.
    """
    stream = list(range(100, 130))
    context = 6
    packer = CausalStreamPacker(context_length=context, pad_token_id=PAD, bos_token_id=BOS)

    collected: list[tuple[int, int]] = []
    cursor = 0
    carry: int | None = None

    while cursor < len(stream):
        need = context if carry is not None else context + 1
        chunk = stream[cursor : cursor + need]
        window = ([carry] + chunk) if carry is not None else chunk
        if len(window) < 2:
            break
        packed = packer.pack(window, ["src"] * context)
        for inp, lab, flag in zip(packed.input_ids, packed.labels, packed.loss_mask, strict=True):
            if flag:
                collected.append((inp, lab))
        cursor += len(chunk)
        carry = window[-1]

    expected = [(stream[i], stream[i + 1]) for i in range(len(stream) - 1)]
    assert collected == expected, "overlap dropped or duplicated a target position"
    assert len(collected) == len(set(collected)), "a target was counted twice"


def test_bos_is_context_only_and_never_a_target_position() -> None:
    """C07/C11: BOS supplies context but is not itself scored."""
    packer = CausalStreamPacker(context_length=4, pad_token_id=PAD, bos_token_id=BOS)
    packed = packer.pack([BOS, 20, 21, 22, 23], ["src"] * 4)

    assert packed.input_ids[0] == BOS
    assert packed.labels == [20, 21, 22, 23]
    assert packed.loss_mask == [1, 1, 1, 1], "the first text token is predicted from BOS"
    assert packed.valid_targets == 4
    boundary = packer.pack([20, EOS, BOS, 21, 22], ["src"] * 4)
    assert boundary.labels == [EOS, BOS, 21, 22]
    assert boundary.loss_mask == [1, 0, 1, 1], "mask BOS labels, not their following text"


def test_causal_stream_uses_one_segment_and_continuous_positions() -> None:
    """Cross-document attention is enabled, so nothing resets mid-window."""
    packer = CausalStreamPacker(context_length=6, pad_token_id=PAD, bos_token_id=BOS)
    packed = packer.pack([10, 11, EOS, 12, 13, 14, 15], ["src"] * 6)

    assert set(packed.segment_ids) == {0}
    assert packed.position_ids == list(range(6))
    # The EOS boundary is a token in the stream, not a mask break.
    assert packed.loss_mask == [1] * 6


# -------------------------------------------------------------- isolated packing


def test_isolated_packing_resets_positions_and_masks_boundaries() -> None:
    """Each document is its own segment; cross-boundary targets are masked."""
    packer = IsolatedDocumentPacker(context_length=8, pad_token_id=PAD, bos_token_id=BOS)
    documents = [
        DocumentSpan(doc_id="d0", source_id="src", token_ids=[10, 11, EOS]),
        DocumentSpan(doc_id="d1", source_id="src", token_ids=[20, 21, 22, EOS]),
    ]
    packed = packer.pack(documents)

    # Seven tokens yield six usable positions, then padding fills the window.
    assert packed.position_ids == [0, 1, 2, 0, 1, 2, 0, 0]
    # Positions restart inside each document rather than running continuously.
    assert packed.position_ids[:3] == [0, 1, 2]
    assert packed.position_ids[3:6] == [0, 1, 2]

    # Segments identify the owning document; padding is marked -1, not segment 0.
    assert packed.segment_ids[:3] == [0, 0, 0]
    assert packed.segment_ids[3:6] == [1, 1, 1]
    assert packed.segment_ids[6:] == [-1, -1]

    # The target at the boundary crosses documents, so it is masked out.
    boundary = 2
    assert packed.segment_ids[boundary] != packed.segment_ids[boundary + 1]
    assert packed.loss_mask[boundary] == 0


def test_isolated_packing_preserves_document_and_byte_provenance() -> None:
    """Source ownership and byte spans must survive windowing (C07)."""
    packer = IsolatedDocumentPacker(context_length=8, pad_token_id=PAD, bos_token_id=BOS)
    documents = [
        DocumentSpan("d0", "src_a", [10, 11, EOS], byte_start=0, byte_end=40),
        DocumentSpan("d1", "src_b", [20, 21, EOS], byte_start=40, byte_end=95),
    ]
    packed = packer.pack(documents)

    assert packed.doc_ids == ["d0", "d1"]
    assert packed.byte_spans == [(0, 40), (40, 95)]
    assert packed.source_attribution[0] == "src_a"
    assert "src_b" in packed.source_attribution


def test_isolated_packing_rejects_a_window_without_a_target() -> None:
    packer = IsolatedDocumentPacker(context_length=4, pad_token_id=PAD, bos_token_id=BOS)
    with pytest.raises(ValueError, match="at least 2 tokens"):
        packer.pack([DocumentSpan("d0", "src", [10])])


def test_build_packer_selects_the_declared_policy() -> None:
    assert isinstance(build_packer("causal_stream", 8, PAD, BOS), CausalStreamPacker)
    assert isinstance(build_packer("isolated_document", 8, PAD, BOS), IsolatedDocumentPacker)
    with pytest.raises(ValueError, match="unknown packing mode"):
        build_packer("something_else", 8, PAD, BOS)


def test_the_two_packing_policies_produce_different_masks() -> None:
    """They are distinct experiment fields, not cosmetic variants."""
    tokens = [10, 11, EOS, 20, 21, EOS]
    stream = CausalStreamPacker(6, PAD, BOS).pack(tokens + [22], ["src"] * 6)
    isolated = IsolatedDocumentPacker(6, PAD, BOS).pack(
        [
            DocumentSpan("d0", "src", [10, 11, EOS]),
            DocumentSpan("d1", "src", [20, 21, EOS, 22]),
        ]
    )
    assert stream.loss_mask != isolated.loss_mask
    assert set(stream.segment_ids) != set(isolated.segment_ids)


# ----------------------------------------------------------------------- scheduler


def test_deficit_scheduling_tracks_configured_weights() -> None:
    """Observed shares must converge on configured weights, within the bound."""
    mixture = recipe(a=0.7, b=0.3)
    scheduler = QuotaScheduler(mixture, availability(a=1_000_000, b=1_000_000))

    for _ in range(1000):
        chosen = scheduler.select_source()
        scheduler.record_exposure(chosen, valid_targets=10, content_targets=10)

    report = scheduler.share_report()
    assert report.within_bound, f"drift {report.max_drift} exceeded {report.drift_bound}"
    assert report.target_shares["a"] == pytest.approx(0.7, abs=0.02)
    assert report.target_shares["b"] == pytest.approx(0.3, abs=0.02)


def test_first_selection_is_deterministic_not_arbitrary() -> None:
    """With all deficits zero at the start, the tie-break must be reproducible."""
    mixture = recipe(a=0.5, b=0.5)
    picks = {
        QuotaScheduler(mixture, availability(a=1000, b=1000)).select_source() for _ in range(20)
    }
    assert len(picks) == 1


def test_eos_is_attributed_to_its_own_source_and_reported_separately() -> None:
    """C07: attribute EOS to its document's source and report its share."""
    mixture = recipe(a=0.5, b=0.5)
    scheduler = QuotaScheduler(mixture, availability(a=1000, b=1000))

    scheduler.record_exposure("a", valid_targets=100, content_targets=90, eos_targets=10)
    scheduler.record_exposure("b", valid_targets=100, content_targets=99, eos_targets=1)

    counters = scheduler.state.counters
    assert counters["a"].eos_targets == 10
    assert counters["b"].eos_targets == 1

    report = scheduler.share_report()
    assert report.eos_share == pytest.approx(11 / 200)
    # Structural tokens move the content share away from the target share: the
    # EOS-heavy source takes a larger share of targets than of content tokens,
    # which is exactly the difference C07 asks the planning report to surface.
    gap = report.structural_gap()
    assert gap["a"] > 0 > gap["b"]
    assert report.target_shares["a"] == pytest.approx(0.5)
    assert report.content_shares["a"] < report.target_shares["a"]


def test_bos_and_padding_are_excluded_from_the_budget() -> None:
    """Excluded tokens are counted for reporting but never enter valid targets."""
    mixture = recipe(a=1.0)
    scheduler = QuotaScheduler(mixture, availability(a=1000))
    scheduler.record_exposure(
        "a", valid_targets=50, content_targets=50, bos_excluded=3, padding_excluded=7
    )

    counters = scheduler.state.counters["a"]
    assert counters.valid_targets == 50
    assert counters.bos_excluded == 3
    assert counters.padding_excluded == 7
    assert scheduler.share_report().total_valid_targets == 50


def test_repeat_false_raises_on_exhaustion_without_redistributing() -> None:
    """C07: repeat=false fails; weight is not handed to another source."""
    mixture = recipe(a=0.5, b=0.5)
    scheduler = QuotaScheduler(mixture, availability(a=1000, b=1000))

    with pytest.raises(SourceExhaustedError, match="not redistributed"):
        scheduler.advance_epoch("a")


def test_bounded_repeat_allows_declared_epochs_then_stops() -> None:
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[MixtureComponent(source_id="a", weight=1.0)],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=3),
    )
    scheduler = QuotaScheduler(mixture, availability(a=1000))

    scheduler.advance_epoch("a")
    scheduler.advance_epoch("a")
    assert scheduler.state.counters["a"].epoch == 2

    with pytest.raises(SourceExhaustedError, match="bounded repetition is exhausted"):
        scheduler.advance_epoch("a")


def test_repeated_exposure_is_counted_separately_from_unique() -> None:
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[MixtureComponent(source_id="a", weight=1.0)],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=5),
    )
    scheduler = QuotaScheduler(mixture, availability(a=1000))

    scheduler.record_exposure("a", valid_targets=100, canonical_bytes=400, repeated=False)
    scheduler.record_exposure("a", valid_targets=60, canonical_bytes=240, repeated=True)

    counters = scheduler.state.counters["a"]
    assert counters.valid_targets == 160
    assert counters.repeated_targets == 60
    assert counters.unique_targets == 100
    assert counters.repeated_bytes == 240
    assert scheduler.share_report().total_repeated_targets == 60


def test_repeat_ceiling_is_enforced_at_runtime() -> None:
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[MixtureComponent(source_id="a", weight=1.0)],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=5, max_repeated_targets=100),
    )
    scheduler = QuotaScheduler(mixture, availability(a=1000))

    scheduler.record_exposure("a", valid_targets=80, repeated=True)
    with pytest.raises(RepeatBudgetExceededError, match="declared ceiling"):
        scheduler.record_exposure("a", valid_targets=40, repeated=True)


def test_scheduler_state_round_trips_exactly() -> None:
    """Resume must restore deficits and every counter (C07)."""
    mixture = recipe(a=0.6, b=0.4)
    scheduler = QuotaScheduler(mixture, availability(a=10_000, b=10_000))

    for _ in range(50):
        chosen = scheduler.select_source()
        scheduler.record_exposure(chosen, valid_targets=7, content_targets=6, eos_targets=1)

    saved = scheduler.get_state()
    restored = QuotaScheduler(mixture, availability(a=10_000, b=10_000))
    restored.load_state(saved)

    assert restored.get_state() == saved
    assert restored.share_report().to_dict() == scheduler.share_report().to_dict()
    # And it continues identically from the restore point.
    assert restored.select_source() == scheduler.select_source()


def test_schedule_state_survives_a_dict_round_trip() -> None:
    mixture = recipe(a=1.0)
    scheduler = QuotaScheduler(mixture, availability(a=1000))
    scheduler.record_exposure("a", valid_targets=25, content_targets=24, eos_targets=1)

    payload = scheduler.get_state()
    rebuilt = ScheduleState.from_dict(payload)
    assert rebuilt.to_dict() == payload


def test_scheduler_rejects_a_source_without_availability() -> None:
    with pytest.raises(KeyError, match="no availability recorded"):
        QuotaScheduler(recipe(a=0.5, b=0.5), availability(a=1000))


def test_negative_exposure_is_rejected() -> None:
    scheduler = QuotaScheduler(recipe(a=1.0), availability(a=1000))
    with pytest.raises(ValueError, match="non-negative"):
        scheduler.record_exposure("a", valid_targets=-1)
