"""P35 M4: paired seed-level Student-t statistics, Bonferroni and decisions.

All values are SYNTHETIC. Reference values: closed-form Student-t quantiles for
df = 1, 2, 4 (independent of the implementation) and published table values.
"""

from __future__ import annotations

import math
import random

import pytest

from xlm.comparison.science_stats import (
    DecisionResult,
    MetricDirection,
    PairValue,
    QuestionType,
    StatisticsError,
    bonferroni_test_alpha,
    classify_effect,
    paired_seed_statistics,
    student_t_critical,
    student_t_upper_tail,
)

LOWER = MetricDirection.LOWER_IS_BETTER
HIGHER = MetricDirection.HIGHER_IS_BETTER
Z_975 = 1.959963984540054

# SYNTHETIC hand-computed case: improvements 0.02, 0.03, 0.01, 0.03, 0.03.
CONTROL = [3.00, 3.10, 3.20, 3.05, 3.15]
CANDIDATE = [2.98, 3.07, 3.19, 3.02, 3.12]


def _pairs(control: list[float], candidate: list[float]) -> list[PairValue]:
    return [
        PairValue(f"C{i}", a, b) for i, (a, b) in enumerate(zip(control, candidate, strict=True))
    ]


def _stats(pairs: list[PairValue], direction: MetricDirection = LOWER, size: int = 1):  # type: ignore[no-untyped-def]
    return paired_seed_statistics(
        pairs, metric="ce", direction=direction, family_ci_level=0.95, family_size=size
    )


def _closed_form(q: float, df: int) -> float:
    if df == 1:
        return 1.0 / math.tan(math.pi * q)
    if df == 2:
        return (1 - 2 * q) / math.sqrt(2 * q * (1 - q))
    p = 1.0 - q
    alpha = 4 * p * (1 - p)
    root = math.cos(math.acos(math.sqrt(alpha)) / 3) / math.sqrt(alpha)
    return 2.0 * math.sqrt(root - 1.0)


@pytest.mark.parametrize("df", [1, 2, 4])
@pytest.mark.parametrize("q", [0.25, 0.1, 0.05, 0.025, 0.0125, 0.005, 0.001])
def test_t_critical_matches_closed_forms(df: int, q: float) -> None:
    assert student_t_critical(q, df) == pytest.approx(_closed_form(q, df), rel=1e-11)


@pytest.mark.parametrize(
    ("df", "expected"),
    [(3, 3.182446305284263), (9, 2.262157162798205), (30, 2.042272456301238)],
)
def test_t_critical_matches_published_tables(df: int, expected: float) -> None:
    assert student_t_critical(0.025, df) == pytest.approx(expected, rel=1e-9)


def test_t_cdf_matches_cauchy_and_df2_closed_forms() -> None:
    for t in (-3.0, -0.5, 0.0, 0.7, 2.0, 25.0):
        assert student_t_upper_tail(t, 1) == pytest.approx(0.5 - math.atan(t) / math.pi, abs=1e-13)
        assert student_t_upper_tail(t, 2) == pytest.approx(
            0.5 - t / (2 * math.sqrt(2 + t * t)), abs=1e-13
        )


def test_known_hand_computed_mean_sd_and_t_interval() -> None:
    stats = _stats(_pairs(CONTROL, CANDIDATE))
    assert stats.n_pairs == 5 and stats.df == 4
    assert stats.raw_deltas == pytest.approx((-0.02, -0.03, -0.01, -0.03, -0.03))
    assert stats.mean_improvement == pytest.approx(0.024, abs=1e-15)
    assert stats.mean_raw_delta == pytest.approx(-0.024, abs=1e-15)
    assert stats.sd == pytest.approx(math.sqrt(8e-5), rel=1e-12)
    assert stats.standard_error == pytest.approx(0.004, rel=1e-12)
    assert stats.t_critical == pytest.approx(2.776445105197793, rel=1e-12)
    assert stats.ci_improvement == pytest.approx(
        (0.024 - 0.011105780420791, 0.024 + 0.011105780420791), rel=1e-10
    )
    assert stats.ci_raw_delta == pytest.approx((-0.035105780420791, -0.012894219579209), rel=1e-10)
    assert stats.sign_summary == {"candidate_better": 5, "candidate_worse": 0, "tied": 0}
    assert stats.leave_one_pair_out_mean_improvement is not None
    assert stats.leave_one_pair_out_mean_improvement["C2"] == pytest.approx(0.0275)


def test_seed_interval_uses_student_t_never_z() -> None:
    stats = _stats(_pairs(CONTROL, CANDIDATE))
    assert stats.t_critical is not None and abs(stats.t_critical - Z_975) > 0.8
    half = stats.ci_improvement[1] - stats.mean_improvement  # type: ignore[index]
    assert half == pytest.approx(2.776445105197793 * 0.004, rel=1e-10)
    three = _stats(_pairs(CONTROL[:3], CANDIDATE[:3]))
    assert three.t_critical == pytest.approx(4.302652729749464, rel=1e-12)


def test_identical_scores_give_zero_delta_and_no_decision() -> None:
    stats = _stats(_pairs(CONTROL, CONTROL))
    assert stats.mean_improvement == 0.0 and stats.mean_raw_delta == 0.0
    assert stats.zero_variance and stats.ci_improvement == (0.0, 0.0)
    decision = classify_effect(
        stats, question=QuestionType.SUPERIORITY, practical_margin=0.01, noninferiority_margin=None
    )
    assert decision.result is DecisionResult.AMBIGUOUS
    assert decision.qualifier == "degenerate_zero_variance"


def test_zero_variance_nonzero_mean_is_never_a_decision() -> None:
    stats = _stats(_pairs([3.0, 3.1, 3.2], [2.9, 3.0, 3.1]))
    assert stats.zero_variance and stats.ci_improvement[0] == pytest.approx(0.1)  # type: ignore[index]
    decision = classify_effect(
        stats, question=QuestionType.SUPERIORITY, practical_margin=0.01, noninferiority_margin=None
    )
    assert decision.result is DecisionResult.AMBIGUOUS


def test_swapping_arms_reverses_effect() -> None:
    forward = _stats(_pairs(CONTROL, CANDIDATE))
    backward = _stats(_pairs(CANDIDATE, CONTROL))
    assert backward.mean_improvement == pytest.approx(-forward.mean_improvement)
    assert backward.mean_raw_delta == pytest.approx(-forward.mean_raw_delta)
    assert backward.ci_improvement == pytest.approx(
        (-forward.ci_improvement[1], -forward.ci_improvement[0])  # type: ignore[index]
    )


def test_pair_order_does_not_change_anything() -> None:
    pairs = _pairs(CONTROL, CANDIDATE)
    reference = _stats(pairs).to_dict()
    rng = random.Random(350035)
    for _ in range(5):
        shuffled = list(pairs)
        rng.shuffle(shuffled)
        assert _stats(shuffled).to_dict() == reference


def test_one_pair_has_no_seed_interval() -> None:
    stats = _stats([PairValue("E0", 3.0, 2.9)])
    assert stats.n_pairs == 1
    assert stats.sd is None and stats.standard_error is None and stats.df is None
    assert stats.t_critical is None and stats.ci_improvement is None and stats.ci_raw_delta is None
    assert "no seed-level confidence interval" in stats.notes[0]
    decision = classify_effect(
        stats, question=QuestionType.SUPERIORITY, practical_margin=0.01, noninferiority_margin=None
    )
    assert decision.result is DecisionResult.NO_SEED_INTERVAL


def test_empty_duplicate_and_nonfinite_pairs_refused() -> None:
    with pytest.raises(StatisticsError):
        _stats([])
    with pytest.raises(StatisticsError, match="duplicate"):
        _stats([PairValue("C0", 3.0, 2.9), PairValue("C0", 3.1, 3.0)])
    with pytest.raises(StatisticsError, match="non-finite"):
        _stats([PairValue("C0", float("nan"), 2.9), PairValue("C1", 3.1, 3.0)])


def test_lower_and_higher_is_better_orientation() -> None:
    lower = _stats([PairValue("a", 3.0, 2.9), PairValue("b", 3.2, 3.0)], LOWER)
    assert lower.mean_raw_delta == pytest.approx(-0.15) and lower.mean_improvement == pytest.approx(
        0.15
    )
    higher = _stats([PairValue("a", 0.60, 0.65), PairValue("b", 0.62, 0.66)], HIGHER)
    assert higher.mean_raw_delta == pytest.approx(
        0.045
    ) and higher.mean_improvement == pytest.approx(0.045)
    worse = _stats([PairValue("a", 3.0, 3.1), PairValue("b", 3.2, 3.3)], LOWER)
    assert worse.mean_improvement < 0 < worse.mean_raw_delta


def test_bonferroni_family_size_widens_interval_and_raises_threshold() -> None:
    assert bonferroni_test_alpha(0.95, 1) == pytest.approx(0.05)
    assert bonferroni_test_alpha(0.95, 2) == pytest.approx(0.025)
    widths = []
    crits = []
    for size in (1, 2, 3):
        stats = _stats(_pairs(CONTROL, CANDIDATE), size=size)
        low, high = stats.ci_improvement  # type: ignore[misc]
        widths.append(high - low)
        crits.append(stats.t_critical)
        assert stats.family_size == size
        assert stats.per_comparison_ci_level == pytest.approx(1 - 0.05 / size)
    assert widths[0] < widths[1] < widths[2]
    assert crits[1] == pytest.approx(3.4954059325164364, rel=1e-10)
    decisions = [
        classify_effect(
            _stats(_pairs(CONTROL, CANDIDATE), size=size),
            question=QuestionType.SUPERIORITY,
            practical_margin=0.01,
            noninferiority_margin=None,
        ).result
        for size in (1, 2, 3)
    ]
    assert decisions == [
        DecisionResult.CLEAR_WIN,
        DecisionResult.CLEAR_WIN,
        DecisionResult.AMBIGUOUS,
    ]
    with pytest.raises(StatisticsError):
        bonferroni_test_alpha(0.95, 0)


def _decide(control: list[float], candidate: list[float], **kwargs):  # type: ignore[no-untyped-def]
    question = kwargs.pop("question", QuestionType.SUPERIORITY)
    return classify_effect(
        _stats(_pairs(control, candidate)),
        question=question,
        practical_margin=kwargs.get("practical"),
        noninferiority_margin=kwargs.get("ni"),
    )


def test_clear_win_clear_loss_and_ambiguous() -> None:
    assert _decide(CONTROL, CANDIDATE, practical=0.01).result is DecisionResult.CLEAR_WIN
    assert _decide(CANDIDATE, CONTROL, practical=0.01).result is DecisionResult.CLEAR_LOSS
    positive = _decide(CONTROL, CANDIDATE, practical=0.015)
    assert positive.result is DecisionResult.AMBIGUOUS
    assert positive.qualifier == "positive_mean_uncertain_practical_gain"
    negative = _decide(CANDIDATE, CONTROL, practical=0.015)
    assert negative.result is DecisionResult.AMBIGUOUS
    assert negative.qualifier == "negative_mean_uncertain_practical_loss"


def test_meaningful_mean_with_ci_crossing_margin_is_ambiguous() -> None:
    # Mean improvement 0.024 exceeds the 0.02 margin, but the lower bound does not.
    decision = _decide(CONTROL, CANDIDATE, practical=0.02)
    assert decision.result is DecisionResult.AMBIGUOUS


def test_bound_exactly_on_margin_is_not_beyond_it() -> None:
    stats = _stats(_pairs(CONTROL, CANDIDATE))
    low = stats.ci_improvement[0]  # type: ignore[index]
    for margin in (low, math.nextafter(low, 0.0), math.nextafter(low, 1.0)):
        decision = classify_effect(
            stats,
            question=QuestionType.SUPERIORITY,
            practical_margin=margin,
            noninferiority_margin=None,
        )
        assert decision.result is DecisionResult.AMBIGUOUS
    clear = classify_effect(
        stats,
        question=QuestionType.SUPERIORITY,
        practical_margin=low * 0.999,
        noninferiority_margin=None,
    )
    assert clear.result is DecisionResult.CLEAR_WIN


def test_noninferiority_requires_bound_within_margin() -> None:
    # Tight, slightly negative improvements: L = -0.0068 > -0.02 -> non-inferior.
    control = [3.00, 3.10, 3.20, 3.05, 3.15]
    candidate = [3.004, 3.103, 3.206, 3.052, 3.155]
    ni = _decide(control, candidate, question=QuestionType.NONINFERIORITY, ni=0.02)
    assert ni.result is DecisionResult.NON_INFERIOR
    loss = _decide(
        control, [c + 0.1 for c in control], question=QuestionType.NONINFERIORITY, ni=0.02
    )
    assert loss.result is DecisionResult.AMBIGUOUS or loss.result is DecisionResult.CLEAR_LOSS


def test_nonsignificant_difference_is_not_noninferiority() -> None:
    # Wide interval containing zero (nonsignificant) that also reaches beyond -margin.
    control = [3.00, 3.10, 3.20, 3.05, 3.15]
    candidate = [2.95, 3.16, 3.14, 3.11, 3.15]
    stats = _stats(_pairs(control, candidate))
    low, high = stats.ci_improvement  # type: ignore[misc]
    assert low < 0 < high  # not significant
    decision = classify_effect(
        stats,
        question=QuestionType.NONINFERIORITY,
        practical_margin=None,
        noninferiority_margin=0.02,
    )
    assert decision.result is DecisionResult.AMBIGUOUS
    assert decision.qualifier == "noninferiority_not_shown"


def test_clear_noninferiority_loss() -> None:
    control = [3.00, 3.10, 3.20, 3.05, 3.15]
    candidate = [3.10, 3.21, 3.29, 3.16, 3.25]
    decision = _decide(control, candidate, question=QuestionType.NONINFERIORITY, ni=0.02)
    assert decision.result is DecisionResult.CLEAR_LOSS


def test_missing_margin_gives_no_decision() -> None:
    assert _decide(CONTROL, CANDIDATE).result is DecisionResult.NO_MARGIN
    ni = _decide(CONTROL, CANDIDATE, question=QuestionType.NONINFERIORITY)
    assert ni.result is DecisionResult.NO_MARGIN
    with pytest.raises(StatisticsError):
        _decide(CONTROL, CANDIDATE, practical=-0.01)
