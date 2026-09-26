"""Paired seed-level statistics and decision semantics for science-v1 (P35 M4, §P).

This is independent of the item/cluster bootstrap in ``bootstrap.py``: the unit
of replication here is one *independent paired training tuple*, and the
interval is a two-sided Student-t interval over the paired differences with
``df = n - 1``. Item-level uncertainty never enters these numbers.

The Student-t critical value is computed from the regularized incomplete beta
function (continued fraction) and inverted by bisection on the upper tail, so
no statistical dependency is needed. Closed forms for df = 1, 2 and 4 verify it
in the tests. A normal (z) critical value is never substituted.

Effects are reported twice: the raw metric delta ``candidate - control`` and a
normalized *improvement* whose sign means "candidate better" for both metric
directions. Decisions compare the improvement interval with frozen margins
taken from the comparison manifest; no threshold is invented here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

STATISTICS_VERSION = "xlm-p35-paired-seed-t-v1"
DECISION_RULE_VERSION = "xlm-p35-decision-v1"
UNCERTAINTY_SOURCE = "between_independent_training_seed_pairs"

#: Relative tolerance for margin comparisons. A bound within this distance of a
#: margin counts as ON the boundary, which never satisfies a strict inequality.
#: Floating noise can therefore only make a decision more conservative.
BOUNDARY_TOLERANCE = 1e-12

_BETA_EPS = 1e-16
_BETA_MAX_ITER = 10_000
_BISECTION_ITER = 400


class StatisticsError(ValueError):
    """Raised when paired statistics cannot be validly computed."""


class MetricDirection(StrEnum):
    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"


class QuestionType(StrEnum):
    SUPERIORITY = "superiority"
    NONINFERIORITY = "noninferiority"


class DecisionResult(StrEnum):
    CLEAR_WIN = "CLEAR_WIN"
    CLEAR_LOSS = "CLEAR_LOSS"
    AMBIGUOUS = "AMBIGUOUS"
    NON_INFERIOR = "NON_INFERIOR"
    INELIGIBLE = "INELIGIBLE"
    INCOMPLETE = "INCOMPLETE"
    #: n < 2 pairs: no seed-level interval exists, so no inferential decision.
    NO_SEED_INTERVAL = "NO_SEED_INTERVAL"
    #: The frozen margin needed for a decision is absent from the manifest.
    NO_MARGIN = "NO_MARGIN"


# --------------------------------------------------------------------- Student t


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for m in range(1, _BETA_MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        result *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < _BETA_EPS:
            return result
    raise StatisticsError("incomplete beta continued fraction did not converge")


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)`` for ``a, b > 0`` and ``0 <= x <= 1``."""
    if a <= 0 or b <= 0:
        raise StatisticsError("incomplete beta requires positive shape parameters")
    if not 0.0 <= x <= 1.0:
        raise StatisticsError(f"incomplete beta argument {x} outside [0, 1]")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_upper_tail(t: float, df: int) -> float:
    """``P(T > t)`` for Student's t with ``df`` degrees of freedom."""
    if df < 1:
        raise StatisticsError("Student t requires df >= 1")
    if not math.isfinite(t):
        raise StatisticsError("Student t upper tail requires a finite t")
    tail = 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1.0 - tail


def student_t_critical(upper_tail: float, df: int) -> float:
    """The ``t`` with ``P(T > t) = upper_tail`` for ``0 < upper_tail < 0.5``.

    Bisection on the monotone upper tail; the result is accurate to about
    1e-12 relative, far below any decision-relevant scale.
    """
    if df < 1:
        raise StatisticsError("a t critical value requires df >= 1 (at least two pairs)")
    if not 0.0 < upper_tail < 0.5:
        raise StatisticsError(f"upper tail probability {upper_tail} outside (0, 0.5)")
    low, high = 0.0, 1.0
    while student_t_upper_tail(high, df) > upper_tail:
        high *= 2.0
        if high > 1e12:
            raise StatisticsError("t critical value exceeds the supported range")
    for _ in range(_BISECTION_ITER):
        mid = 0.5 * (low + high)
        if student_t_upper_tail(mid, df) > upper_tail:
            low = mid
        else:
            high = mid
        if high - low <= 1e-15 * max(1.0, high):
            break
    return 0.5 * (low + high)


def bonferroni_test_alpha(ci_level: float, family_size: int) -> float:
    """Two-sided per-comparison alpha ``(1 - ci_level) / family_size``.

    ``family_size`` is the frozen, preregistered number of confirmatory
    comparisons in the family. It is never inferred from how many comparisons
    happened to finish.
    """
    if not 0.0 < ci_level < 1.0:
        raise StatisticsError(f"confidence level {ci_level} outside (0, 1)")
    if type(family_size) is not int or family_size < 1:
        raise StatisticsError("Bonferroni family size must be a positive integer")
    return (1.0 - ci_level) / family_size


# ----------------------------------------------------------------- paired effect


@dataclass(frozen=True)
class PairValue:
    """One independent paired training tuple's control and candidate metric values."""

    pair_id: str
    control: float
    candidate: float


def improvement(control: float, candidate: float, direction: MetricDirection) -> float:
    """Positive means the candidate is better, for either metric direction."""
    if direction is MetricDirection.LOWER_IS_BETTER:
        return control - candidate
    return candidate - control


@dataclass(frozen=True)
class PairedSeedStatistics:
    """Paired Student-t summary over independent training-seed pairs."""

    metric: str
    direction: MetricDirection
    n_pairs: int
    pairs: tuple[PairValue, ...]
    raw_deltas: tuple[float, ...]
    improvements: tuple[float, ...]
    mean_raw_delta: float
    mean_improvement: float
    sd: float | None
    standard_error: float | None
    df: int | None
    family_ci_level: float
    family_size: int
    per_comparison_alpha: float
    per_comparison_ci_level: float
    t_critical: float | None
    ci_raw_delta: tuple[float, float] | None
    ci_improvement: tuple[float, float] | None
    zero_variance: bool
    sign_summary: dict[str, int]
    leave_one_pair_out_mean_improvement: dict[str, float] | None
    notes: tuple[str, ...] = field(default=())

    @property
    def has_seed_interval(self) -> bool:
        return self.ci_improvement is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "statistics_version": STATISTICS_VERSION,
            "uncertainty_source": UNCERTAINTY_SOURCE,
            "interval_method": "two_sided_student_t_bonferroni",
            "metric": self.metric,
            "direction": self.direction.value,
            "n_pairs": self.n_pairs,
            "pairs": [
                {
                    "pair_id": p.pair_id,
                    "control": p.control,
                    "candidate": p.candidate,
                    "raw_delta": d,
                    "improvement": i,
                }
                for p, d, i in zip(self.pairs, self.raw_deltas, self.improvements, strict=True)
            ],
            "mean_raw_delta": self.mean_raw_delta,
            "mean_improvement": self.mean_improvement,
            "sd": self.sd,
            "standard_error": self.standard_error,
            "df": self.df,
            "family_ci_level": self.family_ci_level,
            "family_size": self.family_size,
            "per_comparison_alpha": self.per_comparison_alpha,
            "per_comparison_ci_level": self.per_comparison_ci_level,
            "t_critical": self.t_critical,
            "ci_raw_delta": list(self.ci_raw_delta) if self.ci_raw_delta else None,
            "ci_improvement": list(self.ci_improvement) if self.ci_improvement else None,
            "zero_variance": self.zero_variance,
            "sign_summary": dict(self.sign_summary),
            "leave_one_pair_out_mean_improvement": self.leave_one_pair_out_mean_improvement,
            "notes": list(self.notes),
        }


def paired_seed_statistics(
    pairs: Sequence[PairValue],
    *,
    metric: str,
    direction: MetricDirection,
    family_ci_level: float,
    family_size: int,
) -> PairedSeedStatistics:
    """Mean, sample SD, SE and a Bonferroni-adjusted two-sided t interval.

    ``d_i = candidate_i - control_i`` is preserved as the raw delta. The
    interval uses ``t_(1 - alpha/(2m), n-1)`` with ``alpha = 1 - family_ci_level``
    and ``m = family_size``. With fewer than two pairs no interval exists and
    none is fabricated. Sums use ``math.fsum``, so the result does not depend on
    the order of the pairs.
    """
    if not pairs:
        raise StatisticsError("paired statistics need at least one complete pair")
    ids = [p.pair_id for p in pairs]
    if len(set(ids)) != len(ids):
        raise StatisticsError(f"duplicate pair identities in {sorted(ids)}")
    for p in pairs:
        if not (math.isfinite(p.control) and math.isfinite(p.candidate)):
            raise StatisticsError(f"pair '{p.pair_id}' has a non-finite metric value")
    alpha = bonferroni_test_alpha(family_ci_level, family_size)
    ordered = tuple(sorted(pairs, key=lambda p: p.pair_id))
    raw = tuple(p.candidate - p.control for p in ordered)
    imp = tuple(improvement(p.control, p.candidate, direction) for p in ordered)
    n = len(ordered)
    mean_imp = math.fsum(imp) / n
    mean_raw = math.fsum(raw) / n
    signs = {
        "candidate_better": sum(1 for x in imp if x > 0),
        "candidate_worse": sum(1 for x in imp if x < 0),
        "tied": sum(1 for x in imp if x == 0),
    }
    notes: list[str] = []
    if n < 2:
        notes.append(
            "n_pairs=1: no between-seed variance can be estimated; no seed-level "
            "confidence interval is reported"
        )
        return PairedSeedStatistics(
            metric=metric,
            direction=direction,
            n_pairs=n,
            pairs=ordered,
            raw_deltas=raw,
            improvements=imp,
            mean_raw_delta=mean_raw,
            mean_improvement=mean_imp,
            sd=None,
            standard_error=None,
            df=None,
            family_ci_level=family_ci_level,
            family_size=family_size,
            per_comparison_alpha=alpha,
            per_comparison_ci_level=1.0 - alpha,
            t_critical=None,
            ci_raw_delta=None,
            ci_improvement=None,
            zero_variance=False,
            sign_summary=signs,
            leave_one_pair_out_mean_improvement=None,
            notes=tuple(notes),
        )
    sd = math.sqrt(math.fsum((x - mean_imp) ** 2 for x in imp) / (n - 1))
    se = sd / math.sqrt(n)
    df = n - 1
    crit = student_t_critical(alpha / 2.0, df)
    ci_imp = (mean_imp - crit * se, mean_imp + crit * se)
    if direction is MetricDirection.LOWER_IS_BETTER:
        ci_raw = (-ci_imp[1], -ci_imp[0])
    else:
        ci_raw = ci_imp
    zero_variance = sd == 0.0
    if zero_variance:
        notes.append(
            "zero variance across pairs: the t interval has zero width; this indicates "
            "collapsed pairing or identical scores and cannot support a decision"
        )
    total = math.fsum(imp)
    loo = {p.pair_id: (total - x) / (n - 1) for p, x in zip(ordered, imp, strict=True)}
    return PairedSeedStatistics(
        metric=metric,
        direction=direction,
        n_pairs=n,
        pairs=ordered,
        raw_deltas=raw,
        improvements=imp,
        mean_raw_delta=mean_raw,
        mean_improvement=mean_imp,
        sd=sd,
        standard_error=se,
        df=df,
        family_ci_level=family_ci_level,
        family_size=family_size,
        per_comparison_alpha=alpha,
        per_comparison_ci_level=1.0 - alpha,
        t_critical=crit,
        ci_raw_delta=ci_raw,
        ci_improvement=ci_imp,
        zero_variance=zero_variance,
        sign_summary=signs,
        leave_one_pair_out_mean_improvement=loo,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------- decision


def strictly_greater(value: float, bound: float) -> bool:
    """``value > bound`` with a value within tolerance of the bound treated as equal."""
    return value > bound + BOUNDARY_TOLERANCE * max(1.0, abs(bound))


def strictly_less(value: float, bound: float) -> bool:
    return value < bound - BOUNDARY_TOLERANCE * max(1.0, abs(bound))


@dataclass(frozen=True)
class Decision:
    """A versioned decision from a seed-level interval and frozen margins."""

    result: DecisionResult
    question: QuestionType | None
    margin: float | None
    reasons: tuple[str, ...]
    qualifier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_rule_version": DECISION_RULE_VERSION,
            "result": self.result.value,
            "question": self.question.value if self.question else None,
            "margin": self.margin,
            "qualifier": self.qualifier,
            "reasons": list(self.reasons),
        }


def classify_effect(
    stats: PairedSeedStatistics,
    *,
    question: QuestionType,
    practical_margin: float | None,
    noninferiority_margin: float | None,
) -> Decision:
    """Classify an eligible, complete comparison against frozen margins.

    In improvement orientation (positive = candidate better), with interval
    ``[L, U]``:

    - superiority, margin ``p > 0``: CLEAR_WIN if ``L > p``; CLEAR_LOSS if
      ``U < -p``; otherwise AMBIGUOUS (a positive mean whose interval crosses the
      margin is "positive mean, uncertain practical gain", never a win);
    - non-inferiority, margin ``m > 0``: NON_INFERIOR if ``L > -m``; CLEAR_LOSS
      if ``U < -m``; otherwise AMBIGUOUS. A nonsignificant difference (an
      interval containing zero) is **not** non-inferiority unless the bound
      clears the margin.

    For a lower-is-better metric these are exactly §P's "upper CI of
    ``CE_cand - CE_ctrl`` below ``-delta_practical``" (win), "lower CI above
    ``delta_practical``" (loss) and "upper CI below ``delta_NI``" (NI). A bound
    on the margin is not beyond it. Zero variance and n < 2 never decide.
    """
    if not stats.has_seed_interval:
        return Decision(
            DecisionResult.NO_SEED_INTERVAL,
            question,
            None,
            ("fewer than two complete pairs: no seed-level interval, no inferential decision",),
        )
    margin = practical_margin if question is QuestionType.SUPERIORITY else noninferiority_margin
    label = "practical margin" if question is QuestionType.SUPERIORITY else "non-inferiority margin"
    if margin is None:
        return Decision(
            DecisionResult.NO_MARGIN,
            question,
            None,
            (f"the manifest freezes no {label}; no decision can be issued",),
        )
    if not (math.isfinite(margin) and margin > 0):
        raise StatisticsError(f"{label} must be a positive finite number")
    if stats.zero_variance:
        return Decision(
            DecisionResult.AMBIGUOUS,
            question,
            margin,
            ("zero variance across pairs: degenerate interval, no decision",),
            qualifier="degenerate_zero_variance",
        )
    assert stats.ci_improvement is not None
    low, high = stats.ci_improvement
    mean = stats.mean_improvement
    interval = f"improvement interval [{low:.6g}, {high:.6g}]"
    if question is QuestionType.SUPERIORITY:
        if strictly_greater(low, margin):
            return Decision(
                DecisionResult.CLEAR_WIN,
                question,
                margin,
                (f"{interval} lies entirely above the practical margin {margin:.6g}",),
            )
        if strictly_less(high, -margin):
            return Decision(
                DecisionResult.CLEAR_LOSS,
                question,
                margin,
                (f"{interval} lies entirely below minus the practical margin {-margin:.6g}",),
            )
        qualifier = (
            "positive_mean_uncertain_practical_gain"
            if mean > 0
            else ("negative_mean_uncertain_practical_loss" if mean < 0 else "zero_mean")
        )
        return Decision(
            DecisionResult.AMBIGUOUS,
            question,
            margin,
            (f"{interval} crosses a decision boundary (margin ±{margin:.6g})",),
            qualifier=qualifier,
        )
    if strictly_greater(low, -margin):
        return Decision(
            DecisionResult.NON_INFERIOR,
            question,
            margin,
            (f"lower improvement bound {low:.6g} lies above minus the NI margin {-margin:.6g}",),
        )
    if strictly_less(high, -margin):
        return Decision(
            DecisionResult.CLEAR_LOSS,
            question,
            margin,
            (f"{interval} lies entirely below minus the NI margin {-margin:.6g}",),
        )
    return Decision(
        DecisionResult.AMBIGUOUS,
        question,
        margin,
        (
            f"{interval} does not exclude a loss larger than the NI margin {margin:.6g}; "
            "absence of a significant difference is not non-inferiority",
        ),
        qualifier="noninferiority_not_shown",
    )
