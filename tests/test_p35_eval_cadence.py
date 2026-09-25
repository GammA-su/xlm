"""P35 M2: absolute committed-target evaluation cadence planner (pure data, no torch work)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xlm.config.schemas import ScienceEvaluationConfig
from xlm.evaluation.cadence import (
    AUTHORED_FIXTURE,
    CONTRACT_CADENCES,
    CadenceError,
    EvaluationPlan,
    EventTier,
    build_plan,
    rebase_plan,
)

M = 1_000_000


def test_contract_tables_are_transcribed_from_section_k() -> None:
    """Literal §K values; a changed table must fail here, not drift silently."""
    expected = {
        "pilot_32m": (
            32 * M,
            {
                "quick_lm": [0, 1, 4, 8, 16, 32],
                "full_lm": [0, 32],
                "search_benchmark": [0, 32],
                "endpoint_confirmation": [],
            },
        ),
        "screen_128m": (
            128 * M,
            {
                "quick_lm": [0, 1, 4, 8, 16, 32, 64, 96, 128],
                "full_lm": [0, 32, 128],
                "search_benchmark": [128],
                "endpoint_confirmation": [],
            },
        ),
        "full_1b": (
            1000 * M,
            {
                "quick_lm": [0, 1, 4, 8, 16, 32, 64, 128, 256, 512, 768, 1000],
                "full_lm": [0, 128, 256, 512, 768, 1000],
                "search_benchmark": [256, 1000],
                "endpoint_confirmation": [1000],
            },
        ),
    }
    assert set(CONTRACT_CADENCES) == set(expected)
    for name, (budget, tiers) in expected.items():
        table = CONTRACT_CADENCES[name]
        assert table.budget_valid_targets == budget and table.research
        assert {t.value: [v // M for v in table.thresholds[t]] for t in EventTier} == tiers


def test_first_crossing_fires_at_the_committed_count_not_the_threshold() -> None:
    plan = build_plan("pilot_32m", 32 * M, confirmation_registered=False)
    initial = [e.event_id for e in plan.due(0)]
    assert initial == ["quick_lm@0", "full_lm@0", "search_benchmark@0"]
    # 15 natural 65,536-target updates: C = 983,040, below the 1M threshold.
    assert plan.due(15 * 65_536, handled=initial) == []
    # The 16th update commits C = 1,048,576: the 1M event is due there, once.
    due = plan.due(16 * 65_536, handled=initial)
    assert [e.event_id for e in due] == ["quick_lm@1000000"]
    assert due[0].threshold == 1_000_000 and not due[0].is_endpoint
    assert plan.due(16 * 65_536, handled=[*initial, "quick_lm@1000000"]) == []


def test_exact_crossing_and_multiple_crossings_are_ordered_deterministically() -> None:
    plan = build_plan(
        AUTHORED_FIXTURE,
        40,
        confirmation_registered=False,
        fixture_thresholds={"quick_lm": [10, 20], "full_lm": [20], "search_benchmark": [15]},
    )
    assert [e.event_id for e in plan.due(10)] == ["quick_lm@10"]  # exact crossing
    # One update from C=0 to C=25 crosses three thresholds and four events.
    assert [e.event_id for e in plan.due(25)] == [
        "quick_lm@10",
        "search_benchmark@15",
        "quick_lm@20",
        "full_lm@20",
    ]
    assert plan.research is False


def test_endpoint_events_are_bound_to_the_exact_budget() -> None:
    plan = build_plan("pilot_32m", 32 * M, confirmation_registered=False)
    endpoints = [e.event_id for e in plan.events if e.is_endpoint]
    assert endpoints == ["quick_lm@32000000", "full_lm@32000000", "search_benchmark@32000000"]
    with pytest.raises(CadenceError, match="frozen for a 32000000-target budget"):
        build_plan("pilot_32m", 31 * M, confirmation_registered=False)
    unregistered = build_plan("full_1b", 1000 * M, confirmation_registered=False)
    assert all(e.tier is not EventTier.ENDPOINT_CONFIRMATION for e in unregistered.events)
    registered = build_plan("full_1b", 1000 * M, confirmation_registered=True)
    confirmation = [e for e in registered.events if e.tier is EventTier.ENDPOINT_CONFIRMATION]
    assert [(e.threshold, e.is_endpoint) for e in confirmation] == [(1000 * M, True)]
    with pytest.raises(CadenceError, match="no endpoint confirmation"):
        build_plan("screen_128m", 128 * M, confirmation_registered=True)


@pytest.mark.parametrize(
    ("thresholds", "message"),
    [
        ({"quick_lm": [0, 50]}, "exceeds the run budget"),
        ({"quick_lm": [20, 10]}, "strictly increasing"),
        ({"quick_lm": [10, 10]}, "strictly increasing"),
        ({"quick_lm": [-1]}, "negative"),
        ({"quick_lm": [True]}, "not an integer"),
        ({"quick_lm": ["10"]}, "not an integer"),
        ({"quick_lm": [1.0]}, "not an integer"),
        ({"weekly": [10]}, "unknown fixture tiers"),
        ({}, "contains no event"),
    ],
)
def test_fixture_thresholds_are_validated_data(thresholds: dict, message: str) -> None:
    with pytest.raises(CadenceError, match=message):
        build_plan(
            AUTHORED_FIXTURE, 40, confirmation_registered=False, fixture_thresholds=thresholds
        )


def test_tables_cannot_be_mixed_or_invented() -> None:
    with pytest.raises(CadenceError, match="unknown cadence"):
        build_plan("hourly", 40, confirmation_registered=False)
    with pytest.raises(CadenceError, match="only accepted for authored_fixture"):
        build_plan(
            "pilot_32m",
            32 * M,
            confirmation_registered=False,
            fixture_thresholds={"quick_lm": [0]},
        )
    with pytest.raises(CadenceError, match="requires explicit fixture thresholds"):
        build_plan(AUTHORED_FIXTURE, 40, confirmation_registered=False)


def test_plan_identity_round_trips_and_changes_with_material_fields() -> None:
    plan = build_plan("screen_128m", 128 * M, confirmation_registered=False)
    restored = EvaluationPlan.from_dict(plan.to_dict())
    assert restored == plan and restored.digest() == plan.digest()
    other_budget = build_plan(
        AUTHORED_FIXTURE, 40, confirmation_registered=False, fixture_thresholds={"quick_lm": [0]}
    )
    rebased = rebase_plan(
        build_plan(
            AUTHORED_FIXTURE,
            40,
            confirmation_registered=False,
            fixture_thresholds={"quick_lm": [0, 10]},
        ),
        5,
    )
    assert len({plan.digest(), other_budget.digest(), rebased.digest()}) == 3


def test_fork_origin_excludes_parent_thresholds_explicitly() -> None:
    plan = build_plan(
        AUTHORED_FIXTURE,
        40,
        confirmation_registered=False,
        fixture_thresholds={"quick_lm": [0, 10, 30], "full_lm": [10]},
    )
    forked = rebase_plan(plan, 10)
    assert [e.event_id for e in forked.events] == ["quick_lm@10", "full_lm@10", "quick_lm@30"]
    assert forked.excluded_before_origin == ("quick_lm@0",)
    assert forked.origin_committed_targets == 10


def _config(**changes: object) -> dict:
    base: dict = {
        "version": "xlm-eval-cadence-v1",
        "cadence": "authored_fixture",
        "fixture_thresholds": {"quick_lm": [0, 16]},
        "confirmation_registered": False,
        "quick_lm": {"manifest": "q.json", "manifest_id": "a" * 64},
        "full_lm": None,
        "search_benchmark": None,
        "endpoint_confirmation": None,
        "scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64", "rolling_stride": 4},
    }
    base.update(changes)
    return base


@pytest.mark.parametrize(
    "changes",
    [
        {"fixture_thresholds": {"quick_lm": ["16"]}},  # strings are not coerced
        {"fixture_thresholds": {"quick_lm": [16.0]}},  # neither are floats
        {"fixture_thresholds": None},  # a fixture cadence names its thresholds
        {"cadence": "pilot_32m"},  # a contract table takes none
        {"quick_lm": {"manifest": "q.json", "manifest_id": "latest"}},  # pinned only
        {"scoring": {"forward_precision": "fp32", "logprob_dtype": "fp64"}},  # no defaults
        {"on_threshold": "python:os.system"},  # never executable configuration
    ],
)
def test_science_evaluation_config_is_strict_data(changes: dict) -> None:
    ScienceEvaluationConfig.model_validate(_config())
    with pytest.raises(ValidationError):
        ScienceEvaluationConfig.model_validate(_config(**changes))
