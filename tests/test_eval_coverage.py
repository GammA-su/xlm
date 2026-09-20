"""Expected population, coverage reconciliation and index eligibility (D05).

The expected populations here are written out by hand, never taken from the
observation lists they are checked against. That separation is the whole point:
a test that derived its expectation from the returned items would reproduce the
defect it is meant to prevent.

Every metric assertion is hand-computable from the numbers in the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.evaluation.coverage import (
    COVERAGE_POLICY_VERSION,
    CoverageStatus,
    ExpectedPopulation,
    ExpectedSelection,
    ObservedItem,
    SuiteCoverage,
    TaskCoverage,
    expected_population,
    legacy_coverage,
    reconcile_coverage,
    undeclared_coverage,
)
from xlm.evaluation.inputs import load_evaluation_inputs, verify_evaluation_inputs
from xlm.evaluation.suites import SuiteTier, TaskScore, compute_four_task_index

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_INPUTS = REPO_ROOT / "fixtures" / "eval" / "inputs"
COMPLETE_MANIFEST = FIXTURE_INPUTS / "dev_fixture_v1" / "manifest.yaml"
SUBSET_MANIFEST = FIXTURE_INPUTS / "dev_fixture_subset" / "manifest.yaml"

FOUR_TASKS = ("blimp", "arc_easy", "hellaswag", "piqa")


def _selection(task: str, ids: list[str], subdataset: str | None = None) -> ExpectedSelection:
    namespace = f"{task}/{subdataset}" if subdataset else task
    return ExpectedSelection(
        namespace=namespace,
        task=task,
        subdataset=subdataset,
        bound_task_name=f"xlmdev_{subdataset or task}",
        item_ids=tuple(f"{namespace}#{i}" for i in ids),
    )


def _population(runtime_limit: int | None = None) -> ExpectedPopulation:
    """Four tasks: two items each for the MC tasks, two BLiMP subdatasets."""
    return ExpectedPopulation(
        manifest_id="test-manifest",
        scope_label="hand-written scope",
        scope_kind="frozen_development_subset",
        exposure_class="development_exposed",
        tier="search",
        selections=(
            _selection("arc_easy", ["a1", "a2"]),
            _selection("hellaswag", ["h1", "h2"]),
            _selection("piqa", ["p1", "p2"]),
            _selection("blimp", ["b1", "b2"], subdataset="sub_alpha"),
            _selection("blimp", ["b3", "b4"], subdataset="sub_beta"),
        ),
        required_tasks=FOUR_TASKS,
        required_blimp_subdatasets=("sub_alpha", "sub_beta"),
        runtime_limit=runtime_limit,
    )


def _full_observations() -> dict[str, list[ObservedItem]]:
    return {
        "arc_easy": [ObservedItem("arc_easy", "a1"), ObservedItem("arc_easy", "a2")],
        "hellaswag": [ObservedItem("hellaswag", "h1"), ObservedItem("hellaswag", "h2")],
        "piqa": [ObservedItem("piqa", "p1"), ObservedItem("piqa", "p2")],
        "blimp": [
            ObservedItem("blimp/sub_alpha", "b1", subdataset="sub_alpha"),
            ObservedItem("blimp/sub_alpha", "b2", subdataset="sub_alpha"),
            ObservedItem("blimp/sub_beta", "b3", subdataset="sub_beta"),
            ObservedItem("blimp/sub_beta", "b4", subdataset="sub_beta"),
        ],
    }


def _scores() -> dict[str, TaskScore]:
    """Scores chosen so the index is trivially hand-computable.

    arc_easy 0.75 @ chance 0.5 -> 0.5; hellaswag 0.625 @ 0.25 -> 0.5;
    piqa 0.75 @ 0.5 -> 0.5; blimp macro (0.6 + 0.9)/2 = 0.75 @ 0.5 -> 0.5.
    Mean 0.5 -> index 50.0 exactly.
    """
    return {
        "arc_easy": TaskScore("arc_easy", "acc_norm", 0.75, 0.5, 2, 2),
        "hellaswag": TaskScore("hellaswag", "acc_norm", 0.625, 0.25, 2, 2),
        "piqa": TaskScore("piqa", "acc", 0.75, 0.5, 2, 2),
        "blimp": TaskScore("blimp", "acc", 0.75, 0.5, 4, 4, subdataset_scores={"a": 0.6, "b": 0.9}),
    }


# ------------------------------------------------------------------ positive control


def test_full_coverage_is_complete_and_publishes_the_index() -> None:
    coverage = reconcile_coverage(_population(), _full_observations())
    assert coverage.status is CoverageStatus.DECLARED
    assert coverage.complete is True
    assert coverage.covers_full_suite is True
    assert coverage.research_eligible() is True
    assert coverage.eligibility_reasons() == []

    index = compute_four_task_index(_scores(), required=FOUR_TASKS, coverage=coverage)
    assert index.complete is True
    assert index.index == pytest.approx(50.0)
    assert index.scope_label == "hand-written scope"
    assert index.withheld_reasons == []


def test_coverage_is_independent_of_observation_order() -> None:
    forward = reconcile_coverage(_population(), _full_observations())
    shuffled = _full_observations()
    for items in shuffled.values():
        items.reverse()
    reversed_coverage = reconcile_coverage(_population(), shuffled)
    assert forward.to_dict() == reversed_coverage.to_dict()


# ------------------------------------------------------------------ counterexamples


def test_all_four_tasks_present_but_partially_scored_is_incomplete() -> None:
    """The headline D05 defect: presence is not coverage."""
    partial = _full_observations()
    for task in ("arc_easy", "hellaswag", "piqa"):
        partial[task] = partial[task][:1]
    partial["blimp"] = partial["blimp"][:2]

    coverage = reconcile_coverage(_population(runtime_limit=1), partial)
    assert set(coverage.tasks) == set(FOUR_TASKS)  # every task IS present
    assert coverage.missing_tasks == ()
    assert coverage.complete is False
    assert coverage.tasks["arc_easy"].missing_item_ids == ("arc_easy#a2",)
    assert coverage.tasks["blimp"].missing_subdatasets == ("sub_beta",)

    index = compute_four_task_index(_scores(), required=FOUR_TASKS, coverage=coverage)
    assert index.complete is False
    assert index.index is None
    assert any("never scored" in reason for reason in index.withheld_reasons)


def test_runtime_limit_smaller_than_the_population_is_flagged_before_results() -> None:
    population = _population(runtime_limit=1)
    assert sorted(population.limit_truncates()) == [
        "arc_easy",
        "blimp/sub_alpha",
        "blimp/sub_beta",
        "hellaswag",
        "piqa",
    ]
    coverage = reconcile_coverage(population, _full_observations())
    assert any("cannot be complete" in note for note in coverage.notes)


def test_a_failed_item_does_not_shrink_the_expected_population() -> None:
    observations = _full_observations()
    observations["piqa"][1] = ObservedItem("piqa", "p2", failed=True)

    coverage = reconcile_coverage(_population(), observations)
    piqa = coverage.tasks["piqa"]
    assert piqa.expected_items == 2  # unchanged
    assert piqa.scored_items == 1
    assert piqa.failed_item_ids == ("piqa#p2",)
    assert piqa.missing_item_ids == ("piqa#p2",)
    assert coverage.complete is False


def test_a_duplicate_scored_item_cannot_masquerade_as_coverage() -> None:
    observations = _full_observations()
    # Two copies of a1, and a2 never scored: the counts still add up to two.
    observations["arc_easy"] = [ObservedItem("arc_easy", "a1")] * 2

    coverage = reconcile_coverage(_population(), observations)
    arc = coverage.tasks["arc_easy"]
    assert arc.scored_items == 2
    assert arc.expected_items == 2
    assert arc.duplicate_item_ids == ("arc_easy#a1",)
    assert arc.missing_item_ids == ("arc_easy#a2",)
    assert coverage.complete is False


def test_an_unexpected_item_is_reported_not_absorbed() -> None:
    observations = _full_observations()
    observations["hellaswag"].append(ObservedItem("hellaswag", "h_never_declared"))

    coverage = reconcile_coverage(_population(), observations)
    assert coverage.tasks["hellaswag"].unexpected_item_ids == ("hellaswag#h_never_declared",)
    assert coverage.complete is False


def test_a_missing_blimp_subdataset_is_incomplete_even_with_enough_items() -> None:
    observations = _full_observations()
    # Four BLiMP items, all from one subdataset: the count matches, the
    # required grouping does not.
    observations["blimp"] = [
        ObservedItem("blimp/sub_alpha", item, subdataset="sub_alpha")
        for item in ("b1", "b2", "b3", "b4")
    ]
    coverage = reconcile_coverage(_population(), observations)
    blimp = coverage.tasks["blimp"]
    assert blimp.scored_items == 4
    assert blimp.expected_items == 4
    assert blimp.missing_subdatasets == ("sub_beta",)
    assert blimp.observed_subdatasets == ("sub_alpha",)
    assert coverage.complete is False


def test_a_task_returning_nothing_is_a_missing_task() -> None:
    observations = _full_observations()
    observations["piqa"] = []
    coverage = reconcile_coverage(_population(), observations)
    assert coverage.missing_tasks == ("piqa",)
    assert coverage.complete is False


def test_an_interrupted_run_is_never_complete() -> None:
    coverage = reconcile_coverage(
        _population(),
        _full_observations(),
        interrupted=True,
        interruption_reason="operator stopped the run",
    )
    assert coverage.complete is False
    assert any("interrupted" in reason for reason in coverage.eligibility_reasons())


# ------------------------------------------------------------------ declared scopes


def test_a_declared_smaller_scope_is_complete_within_itself_only() -> None:
    population = ExpectedPopulation(
        manifest_id="two-task",
        scope_label="declared two-task scope",
        scope_kind="frozen_development_subset",
        exposure_class="development_exposed",
        tier="search",
        selections=(_selection("arc_easy", ["a1"]), _selection("piqa", ["p1"])),
        required_tasks=("arc_easy", "piqa"),
        required_blimp_subdatasets=(),
    )
    coverage = reconcile_coverage(
        population,
        {
            "arc_easy": [ObservedItem("arc_easy", "a1")],
            "piqa": [ObservedItem("piqa", "p1")],
        },
    )
    assert coverage.complete is True
    # Complete, but explicitly NOT the four-task suite.
    assert coverage.covers_full_suite is False
    assert "frozen development subset 'declared two-task scope' (complete)" == (
        coverage.scope_statement()
    )


def test_authored_fixture_scope_is_complete_but_never_research_evidence() -> None:
    population = ExpectedPopulation(
        manifest_id="fixture",
        scope_label="authored fixture scope",
        scope_kind="authored_fixture",
        exposure_class="authored_fixture",
        tier="search",
        selections=(_selection("arc_easy", ["a1"]),),
        required_tasks=("arc_easy",),
        required_blimp_subdatasets=(),
    )
    coverage = reconcile_coverage(population, {"arc_easy": [ObservedItem("arc_easy", "a1")]})
    assert coverage.complete is True
    assert coverage.research_eligible() is False
    assert "authored synthetic fixture scope" in coverage.scope_statement()


# ------------------------------------------------------------------ unknown scopes


def test_undeclared_coverage_never_claims_an_expected_population() -> None:
    coverage = undeclared_coverage(
        observed={"arc_easy": [ObservedItem("arc_easy", "0"), ObservedItem("arc_easy", "1")]}
    )
    assert coverage.status is CoverageStatus.UNDECLARED
    assert coverage.complete is False
    # Scored is an observed fact; expected stays zero rather than copying it.
    assert coverage.tasks["arc_easy"].scored_items == 2
    assert coverage.tasks["arc_easy"].expected_items == 0

    index = compute_four_task_index(_scores(), required=FOUR_TASKS, coverage=coverage)
    assert index.complete is False
    assert index.index is None
    assert any("no evaluation-input manifest" in r for r in index.withheld_reasons)


def test_legacy_coverage_is_not_retroactively_completed() -> None:
    coverage = legacy_coverage()
    assert coverage.status is CoverageStatus.LEGACY_UNVERIFIED
    assert coverage.complete is False
    assert coverage.research_eligible() is False
    assert "legacy" in coverage.scope_statement()


def test_coverage_survives_a_round_trip() -> None:
    coverage = reconcile_coverage(_population(), _full_observations())
    restored = SuiteCoverage.from_dict(coverage.to_dict())
    assert restored.complete == coverage.complete
    assert restored.status is coverage.status
    assert restored.policy_version == COVERAGE_POLICY_VERSION
    assert restored.tasks["blimp"].expected_item_ids == coverage.tasks["blimp"].expected_item_ids


def test_task_coverage_complete_requires_every_condition() -> None:
    base = TaskCoverage(
        task="piqa",
        expected_items=1,
        scored_items=1,
        expected_item_ids=("piqa#p1",),
        scored_item_ids=("piqa#p1",),
    )
    assert base.complete is True
    assert base.reasons() == []
    import dataclasses

    for field_name, value in (
        ("missing_item_ids", ("piqa#p2",)),
        ("unexpected_item_ids", ("piqa#px",)),
        ("duplicate_item_ids", ("piqa#p1",)),
        ("failed_item_ids", ("piqa#p1",)),
        ("missing_subdatasets", ("sub",)),
    ):
        broken = dataclasses.replace(base, **{field_name: value})
        assert broken.complete is False, field_name
        assert broken.reasons(), field_name


# ------------------------------------------------------------------ from a manifest


def test_expected_population_comes_from_the_manifest_alone() -> None:
    verified = verify_evaluation_inputs(
        load_evaluation_inputs(COMPLETE_MANIFEST), tier=SuiteTier.SEARCH
    )
    population = expected_population(verified, runtime_limit=None)

    assert population.scope_kind == "authored_fixture"
    assert sorted(population.required_tasks) == ["arc_easy", "blimp", "hellaswag", "piqa"]
    # 4 + 4 + 4 for the MC tasks, 3 + 3 across the two BLiMP subdatasets.
    assert population.expected_items_for("arc_easy") == 4
    assert population.expected_items_for("blimp") == 6
    assert population.required_blimp_subdatasets == (
        "blimp_adjunct_island",
        "blimp_anaphor_gender_agreement",
    )


def test_a_manifest_declaring_fewer_tasks_requires_only_those() -> None:
    verified = verify_evaluation_inputs(
        load_evaluation_inputs(SUBSET_MANIFEST), tier=SuiteTier.SEARCH
    )
    population = expected_population(verified)
    assert population.required_tasks == ("arc_easy", "piqa")
