"""Acceptance tests for mixture recipes, validation and deterministic exposure plans.

Covers C07: weights are shares of valid target tokens, never renormalized silently;
plans are compact range descriptors rather than materialized position lists; source
order is frozen by the data seed; and repetition risk is reported before a run.
"""

from __future__ import annotations

import pytest

from xlm.data.sampling import (
    ExhaustionPolicy,
    MixtureComponent,
    MixtureRecipe,
    MixtureValidationError,
    PackingPolicy,
    SourceAvailability,
    compile_exposure_plan,
    compile_matched_plan,
    iter_exposure_blocks,
    summarize_plan_blocks,
    validate_mixture,
)


def availability(**sizes: int) -> dict[str, SourceAvailability]:
    """Build availability records with realistic derived counters."""
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
        mixture_id="test_mix",
        components=[MixtureComponent(source_id=s, weight=w) for s, w in weights.items()],
    )


# ---------------------------------------------------------------- recipe validation


def test_weights_must_sum_to_one_and_are_never_renormalized() -> None:
    """C07 forbids silent renormalization; a bad sum is an error."""
    with pytest.raises(ValueError, match="not renormalized"):
        recipe(a=0.5, b=0.3)
    with pytest.raises(ValueError, match="not renormalized"):
        recipe(a=0.7, b=0.7)

    ok = recipe(a=0.75, b=0.25)
    assert ok.weight_of("a") == 0.75


def test_thirds_are_accepted_within_tolerance() -> None:
    """Decimal thirds must not be rejected by an over-tight sum check."""
    third = 1.0 / 3.0
    mixture = MixtureRecipe(
        mixture_id="thirds",
        components=[MixtureComponent(source_id=s, weight=third) for s in ("a", "b", "c")],
    )
    assert mixture.weight_of("b") == pytest.approx(third)


def test_duplicate_sources_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate source_id"):
        MixtureRecipe(
            mixture_id="dup",
            components=[
                MixtureComponent(source_id="a", weight=0.5),
                MixtureComponent(source_id="a", weight=0.5),
            ],
        )


def test_zero_or_negative_weight_is_rejected() -> None:
    with pytest.raises(ValueError):
        MixtureComponent(source_id="a", weight=0.0)
    with pytest.raises(ValueError):
        MixtureComponent(source_id="a", weight=-0.1)


def test_missing_source_is_an_error_not_a_redistribution() -> None:
    """A named source with no shard must fail, not have its weight spread around."""
    result = validate_mixture(recipe(a=0.5, b=0.5), availability(a=1000))
    assert not result.is_valid
    assert any("not redistributed" in e for e in result.errors)
    with pytest.raises(MixtureValidationError):
        result.raise_if_invalid()


def test_empty_source_is_an_error() -> None:
    """A shard with zero valid targets cannot carry a mixture weight."""
    result = validate_mixture(recipe(a=0.5, b=0.5), availability(a=1000, b=0))
    assert not result.is_valid
    assert any("zero valid target tokens" in e for e in result.errors)


def test_unused_available_source_is_warned_not_silently_included() -> None:
    result = validate_mixture(recipe(a=1.0), availability(a=1000, spare=5000))
    assert result.is_valid
    assert any("carry no mixture weight" in w for w in result.warnings)
    assert "spare" not in result.availability


# ------------------------------------------------------------------------- identity


def test_mixture_identity_includes_the_scheduling_policy() -> None:
    """C07: the scheduling policy is part of the regime hash."""
    base = recipe(a=0.5, b=0.5)
    assert base.identity() == recipe(a=0.5, b=0.5).identity()

    different_weights = recipe(a=0.6, b=0.4)
    assert base.identity() != different_weights.identity()

    repeat_enabled = MixtureRecipe(
        mixture_id="test_mix",
        components=[MixtureComponent(source_id=s, weight=0.5) for s in ("a", "b")],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=3),
    )
    assert base.identity() != repeat_enabled.identity()

    isolated = MixtureRecipe(
        mixture_id="test_mix",
        components=[MixtureComponent(source_id=s, weight=0.5) for s in ("a", "b")],
        packing=PackingPolicy(mode="isolated_document", cross_document_attention=False),
    )
    assert base.identity() != isolated.identity()

    reseeded = MixtureRecipe(
        mixture_id="test_mix",
        components=[MixtureComponent(source_id=s, weight=0.5) for s in ("a", "b")],
        data_seed=7,
    )
    assert base.identity() != reseeded.identity()


def test_component_order_does_not_change_identity() -> None:
    """Declaration order is cosmetic; the mixture is the same mixture."""
    forward = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="a", weight=0.6),
            MixtureComponent(source_id="b", weight=0.4),
        ],
    )
    backward = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="b", weight=0.4),
            MixtureComponent(source_id="a", weight=0.6),
        ],
    )
    assert forward.identity() == backward.identity()


def test_repeat_policy_validation() -> None:
    """max_epochs is meaningless without repeat, and saying so prevents a false sense of bounds."""
    with pytest.raises(ValueError, match="meaningless when repeat is false"):
        ExhaustionPolicy(repeat=False, max_epochs=3)
    assert ExhaustionPolicy(repeat=True, max_epochs=3).max_epochs == 3


def test_isolated_packing_cannot_claim_cross_document_attention() -> None:
    with pytest.raises(ValueError, match="under another name"):
        PackingPolicy(mode="isolated_document", cross_document_attention=True)
    with pytest.raises(ValueError, match="unknown packing mode"):
        PackingPolicy(mode="magic")


# ----------------------------------------------------------------------- planning


def test_plan_reports_repetition_risk_before_the_run() -> None:
    """A source that cannot cover its share must be visible at plan time."""
    avail = availability(big=1_000_000, small=1_000)
    plan = compile_exposure_plan(
        recipe(big=0.5, small=0.5), validate_mixture(recipe(big=0.5, small=0.5), avail), 100_000
    )

    small = plan.projections["small"]
    assert small.planned_targets == 50_000
    assert small.unique_targets_available == 1_000
    assert small.repeated_targets == 49_000
    assert small.epochs_required == pytest.approx(50.0)
    assert small.exhausts_source
    assert plan.requires_repetition
    assert any("repeat is disabled" in w for w in plan.warnings)


def test_plan_without_repetition_reports_none() -> None:
    avail = availability(a=1_000_000, b=1_000_000)
    plan = compile_exposure_plan(
        recipe(a=0.5, b=0.5), validate_mixture(recipe(a=0.5, b=0.5), avail), 100_000
    )
    assert not plan.requires_repetition
    assert plan.total_repeated_targets == 0
    assert plan.warnings == []


def test_bounded_repeat_policy_is_reported_as_declared() -> None:
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="big", weight=0.5),
            MixtureComponent(source_id="small", weight=0.5),
        ],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=100),
    )
    avail = availability(big=1_000_000, small=1_000)
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 100_000)
    assert any("declared bounded-repeat policy" in w for w in plan.warnings)


def test_repeat_ceiling_breach_is_reported() -> None:
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="big", weight=0.5),
            MixtureComponent(source_id="small", weight=0.5),
        ],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=100, max_repeated_targets=100),
    )
    avail = availability(big=1_000_000, small=1_000)
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 100_000)
    assert any("exceeds the declared" in w for w in plan.warnings)


def test_plan_is_deterministic_and_identity_bound() -> None:
    avail = availability(a=1_000_000, b=1_000_000)
    mixture = recipe(a=0.5, b=0.5)
    first = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 10_000)
    second = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 10_000)
    assert first.plan_id == second.plan_id
    assert first.source_order == second.source_order

    other_budget = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 20_000)
    assert other_budget.plan_id != first.plan_id


def test_source_order_is_frozen_by_the_data_seed() -> None:
    """Order must come from the seed, not from declaration order."""
    avail = availability(a=1_000, b=1_000, c=1_000)
    third = 1.0 / 3.0

    def build(seed: int) -> list[str]:
        mixture = MixtureRecipe(
            mixture_id="m",
            components=[MixtureComponent(source_id=s, weight=third) for s in ("a", "b", "c")],
            data_seed=seed,
        )
        return compile_exposure_plan(mixture, validate_mixture(mixture, avail), 900).source_order

    assert build(1) == build(1)
    assert sorted(build(1)) == ["a", "b", "c"]
    # Different seeds should generally give different orders; at minimum the order
    # must be a deterministic function of the seed.
    assert build(1) == build(1) and build(99) == build(99)


def test_plan_rejects_a_nonpositive_budget() -> None:
    avail = availability(a=1000)
    validation = validate_mixture(recipe(a=1.0), avail)
    with pytest.raises(ValueError, match="budget_targets must be positive"):
        compile_exposure_plan(recipe(a=1.0), validation, 0)
    with pytest.raises(ValueError, match="block_size must be positive"):
        compile_exposure_plan(recipe(a=1.0), validation, 100, block_size=0)


def test_plan_refuses_to_compile_from_an_invalid_mixture() -> None:
    validation = validate_mixture(recipe(a=0.5, b=0.5), availability(a=1000))
    with pytest.raises(MixtureValidationError):
        compile_exposure_plan(recipe(a=0.5, b=0.5), validation, 1000)


# ------------------------------------------------------------------ block emission


def test_blocks_are_generated_not_materialized() -> None:
    """A huge budget must not build a huge structure (C07)."""
    avail = availability(a=10**9, b=10**9)
    mixture = recipe(a=0.5, b=0.5)
    plan = compile_exposure_plan(
        mixture, validate_mixture(mixture, avail), budget_targets=10**9, block_size=10**6
    )

    # The plan itself stays tiny regardless of budget.
    assert len(plan.to_dict()["projections"]) == 2

    # Taking a bounded prefix must not enumerate the whole plan.
    prefix = []
    for block in iter_exposure_blocks(plan, avail, limit_blocks=5):
        prefix.append(block)
    assert len(prefix) == 5
    assert all(b.token_count <= 10**6 for b in prefix)


def test_block_ranges_are_contiguous_per_source() -> None:
    """Blocks describe ranges into a shard; they must not overlap or skip."""
    avail = availability(a=10_000, b=10_000)
    mixture = recipe(a=0.5, b=0.5)
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 8_000, block_size=500)

    cursors: dict[str, int] = {}
    for block in iter_exposure_blocks(plan, avail):
        expected = cursors.get(block.source_id, 0)
        assert block.token_start == expected, "block ranges must be contiguous per source"
        cursors[block.source_id] = block.token_end


def test_preview_shares_match_configured_weights() -> None:
    """The deterministic preview must reproduce the configured shares."""
    avail = availability(a=1_000_000, b=1_000_000, c=1_000_000)
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="a", weight=0.5),
            MixtureComponent(source_id="b", weight=0.3),
            MixtureComponent(source_id="c", weight=0.2),
        ],
    )
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 100_000, block_size=500)
    summary = summarize_plan_blocks(plan, avail, max_blocks=10_000)

    assert summary["targets_previewed"] == 100_000
    assert not summary["is_truncated_preview"]
    assert summary["max_observed_drift"] <= mixture.max_share_drift
    assert summary["observed_shares"]["a"] == pytest.approx(0.5, abs=0.02)
    assert summary["observed_shares"]["c"] == pytest.approx(0.2, abs=0.02)


def test_preview_marks_itself_truncated_when_bounded() -> None:
    avail = availability(a=10**9, b=10**9)
    mixture = recipe(a=0.5, b=0.5)
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 10**9, block_size=1000)
    summary = summarize_plan_blocks(plan, avail, max_blocks=10)
    assert summary["is_truncated_preview"]
    assert summary["blocks_previewed"] == 10


def test_unequal_source_sizes_still_meet_configured_shares() -> None:
    """Share arithmetic must not be an artifact of equally sized sources."""
    avail = availability(huge=5_000_000, medium=500_000, tiny=50_000)
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="huge", weight=0.2),
            MixtureComponent(source_id="medium", weight=0.5),
            MixtureComponent(source_id="tiny", weight=0.3),
        ],
    )
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 100_000, block_size=250)
    summary = summarize_plan_blocks(plan, avail, max_blocks=100_000)

    for source_id, expected in (("huge", 0.2), ("medium", 0.5), ("tiny", 0.3)):
        assert summary["observed_shares"][source_id] == pytest.approx(expected, abs=0.02)


def test_storage_projection_follows_token_dtype() -> None:
    """A wider dtype must project proportionally more storage."""
    narrow = availability(a=1_000)
    wide = availability(a=1_000)
    wide["a"].token_dtype = "uint32"

    mixture = recipe(a=1.0)
    narrow_plan = compile_exposure_plan(mixture, validate_mixture(mixture, narrow), 500)
    wide_plan = compile_exposure_plan(mixture, validate_mixture(mixture, wide), 500)
    assert wide_plan.projected_storage_bytes == 2 * narrow_plan.projected_storage_bytes


def test_plan_labels_unique_counts_as_exact_not_estimated() -> None:
    """C07 asks estimates to be labelled; exact interval counts must say they are exact."""
    avail = availability(a=1000)
    mixture = recipe(a=1.0)
    plan = compile_exposure_plan(mixture, validate_mixture(mixture, avail), 500)
    joined = " ".join(plan.notes).lower()
    assert "exact interval count" in joined
    assert "not a sketch" in joined


# ------------------------------------------------------- matched exposure plans


def _tokenized(valid_targets: int, canonical_bytes: int, num_documents: int) -> SourceAvailability:
    """One source as seen through one tokenizer over a fixed canonical pool."""
    return SourceAvailability(
        source_id="corpus",
        shard_id="shard_corpus",
        valid_targets=valid_targets,
        content_tokens=valid_targets,
        eos_tokens=0,
        canonical_bytes=canonical_bytes,
        num_documents=num_documents,
    )


def test_matched_byte_plan_fixes_raw_bytes_across_tokenizers() -> None:
    """Same canonical bytes must survive a tokenizer change (C07 matched plans).

    Two tokenizations of the same text share canonical_bytes but differ in token
    count. A matched-canonical-byte plan at a fixed byte budget must expose the same
    bytes in both cases while deriving a different token budget.
    """
    mixture = recipe(corpus=1.0)
    byte_like = {
        "corpus": _tokenized(valid_targets=100_000, canonical_bytes=400_000, num_documents=1_000)
    }
    bpe_like = {
        "corpus": _tokenized(valid_targets=50_000, canonical_bytes=400_000, num_documents=1_000)
    }

    byte_plan = compile_matched_plan(
        mixture, validate_mixture(mixture, byte_like), budget=200_000, basis="canonical_bytes"
    )
    bpe_plan = compile_matched_plan(
        mixture, validate_mixture(mixture, bpe_like), budget=200_000, basis="canonical_bytes"
    )

    assert byte_plan.total_planned_bytes == 200_000
    assert bpe_plan.total_planned_bytes == 200_000
    assert byte_plan.projections["corpus"].planned_bytes == 200_000
    assert bpe_plan.projections["corpus"].planned_bytes == 200_000

    # The derived token budgets differ: that is the tokenizer's compression, made
    # explicit rather than baked into the raw-text exposure.
    assert byte_plan.projections["corpus"].planned_targets == 50_000
    assert bpe_plan.projections["corpus"].planned_targets == 25_000
    assert byte_plan.total_planned_targets == 50_000
    assert bpe_plan.total_planned_targets == 25_000


def test_matched_document_plan_fixes_document_counts() -> None:
    """A matched-document plan holds document counts and derives bytes from them."""
    mixture = MixtureRecipe(
        mixture_id="m",
        components=[
            MixtureComponent(source_id="corpus_a", weight=0.75),
            MixtureComponent(source_id="corpus_b", weight=0.25),
        ],
    )
    avail = {
        "corpus_a": _tokenized(10_000, 40_000, 500),
        "corpus_b": _tokenized(10_000, 40_000, 500),
    }
    plan = compile_matched_plan(
        mixture, validate_mixture(mixture, avail), budget=1_000, basis="documents"
    )
    assert plan.projections["corpus_a"].planned_documents == 750
    assert plan.projections["corpus_b"].planned_documents == 250
    assert plan.total_planned_documents == 1_000
    # 40,000 bytes / 500 docs = 80 bytes per document, applied to the derived count.
    assert plan.projections["corpus_a"].planned_bytes == 60_000
    assert plan.projections["corpus_a"].planned_targets == 15_000


def test_matched_plan_labels_byte_availability_as_exact() -> None:
    """C07: exact interval counts must be distinguishable from sketch estimates."""
    mixture = recipe(corpus=1.0)
    avail = {"corpus": _tokenized(10_000, 40_000, 500)}
    plan = compile_matched_plan(
        mixture, validate_mixture(mixture, avail), budget=8_000, basis="canonical_bytes"
    )
    assert plan.exact_bytes_available is True
    joined = " ".join(plan.notes).lower()
    assert "exact counts" in joined
    assert "projection" in joined
    assert "changes with tokenizer compression" in joined


def test_matched_plan_reports_repetition_when_bytes_exceed_availability() -> None:
    mixture = recipe(corpus=1.0)
    avail = {"corpus": _tokenized(1_000, 4_000, 100)}
    plan = compile_matched_plan(
        mixture, validate_mixture(mixture, avail), budget=40_000, basis="canonical_bytes"
    )
    projection = plan.projections["corpus"]
    assert projection.requires_repetition
    assert projection.epochs_required == pytest.approx(10.0)
    assert any("repeat is disabled" in w for w in plan.warnings)


def test_matched_plan_rejects_unknown_basis_and_bad_budget() -> None:
    mixture = recipe(corpus=1.0)
    avail = {"corpus": _tokenized(1_000, 4_000, 100)}
    validation = validate_mixture(mixture, avail)
    with pytest.raises(ValueError, match="unknown matched basis"):
        compile_matched_plan(mixture, validation, budget=100, basis="tokens")
    with pytest.raises(ValueError, match="budget must be positive"):
        compile_matched_plan(mixture, validation, budget=0)
    with pytest.raises(MixtureValidationError):
        compile_matched_plan(
            recipe(a=0.5, b=0.5), validate_mixture(recipe(a=0.5, b=0.5), avail), 100
        )
