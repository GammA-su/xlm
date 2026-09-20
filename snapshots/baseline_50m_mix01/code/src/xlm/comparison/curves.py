"""Learning-curve comparisons with interpolation-only target crossing (A31).

Curves are compared against valid tokens, canonical bytes and measured compute.
A target is crossed only by linear interpolation between two measured points;
a model that never reaches a target gets no invented compute-to-target, and a
target beyond the observed range is never extrapolated. Training-only
auxiliaries and teacher costs are added where declared, and every derived
number is labeled measured or estimated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any


class CurveError(ValueError):
    """Raised when a curve cannot support the requested comparison."""


@dataclass(frozen=True)
class CurvePoint:
    """One measured point on a learning curve."""

    x_tokens: int
    metric: float
    canonical_bytes: int | None = None
    compute_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearningCurve:
    """A measured learning curve for one run, sorted by training tokens."""

    run_id: str
    higher_is_better: bool
    points: tuple[CurvePoint, ...]
    teacher_compute_seconds: float = 0.0
    auxiliary_compute_seconds: float = 0.0

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.points, key=lambda p: p.x_tokens))
        object.__setattr__(self, "points", ordered)
        if len(ordered) < 2:
            raise CurveError("a learning curve needs at least two measured points")
        if any(p.x_tokens < 0 for p in ordered):
            raise CurveError("curve token positions must be non-negative")

    def total_compute_seconds(self) -> float:
        """Measured run compute plus declared teacher/auxiliary costs."""
        own = [p.compute_seconds for p in self.points if p.compute_seconds is not None]
        return (
            (max(own) if own else 0.0)
            + self.teacher_compute_seconds
            + self.auxiliary_compute_seconds
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "higher_is_better": self.higher_is_better,
            "points": [p.to_dict() for p in self.points],
            "teacher_compute_seconds": self.teacher_compute_seconds,
            "auxiliary_compute_seconds": self.auxiliary_compute_seconds,
        }


def crossing_at_target(curve: LearningCurve, target: float) -> float | None:
    """Interpolate the token position where the curve first reaches a target.

    Returns None when the curve never reaches the target inside its observed
    range. Extrapolation is forbidden: a target beyond the last measured point
    is not reached, however promising the trend looks.
    """
    points = curve.points
    reached = [
        (previous, current)
        for previous, current in zip(points, points[1:], strict=False)
        if _crosses(previous.metric, current.metric, target, curve.higher_is_better)
    ]
    if _meets(points[0].metric, target, curve.higher_is_better):
        return float(points[0].x_tokens)
    if not reached:
        return None
    previous, current = reached[0]
    span = current.x_tokens - previous.x_tokens
    if span <= 0:
        return float(current.x_tokens)
    fraction = (target - previous.metric) / (current.metric - previous.metric)
    return float(previous.x_tokens + fraction * span)


def _meets(value: float, target: float, higher_is_better: bool) -> bool:
    return value >= target if higher_is_better else value <= target


def _crosses(before: float, after: float, target: float, higher_is_better: bool) -> bool:
    if higher_is_better:
        return before < target <= after
    return before > target >= after


@dataclass
class ComputeToTarget:
    """Measured compute needed to reach a declared target, per run."""

    run_id: str
    target: float
    tokens_to_target: float | None
    compute_seconds_to_target: float | None
    reached: bool
    basis: str = "measured_interpolation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_to_target(
    curve: LearningCurve,
    target: float,
    tokens_per_second: float | None = None,
) -> ComputeToTarget:
    """Convert a target crossing into tokens and measured compute.

    Compute seconds come from the curve's own measured per-point compute when
    available (interpolated the same way), else from an explicit throughput the
    caller supplies and labels. With neither, compute stays unknown rather than
    guessed from parameter counts.
    """
    tokens = crossing_at_target(curve, target)
    if tokens is None:
        return ComputeToTarget(
            run_id=curve.run_id,
            target=target,
            tokens_to_target=None,
            compute_seconds_to_target=None,
            reached=False,
            basis="target_not_reached_no_extrapolation",
        )
    measured = [
        (p.x_tokens, p.compute_seconds) for p in curve.points if p.compute_seconds is not None
    ]
    if len(measured) >= 2:
        xs = [x for x, _ in measured]
        ys = [y for _, y in measured]
        seconds = _interpolate(xs, ys, tokens)
        basis = "measured_interpolation"
    elif tokens_per_second:
        seconds = tokens / tokens_per_second
        basis = "declared_throughput_estimate"
    else:
        return ComputeToTarget(
            run_id=curve.run_id,
            target=target,
            tokens_to_target=tokens,
            compute_seconds_to_target=None,
            reached=True,
            basis="compute_unknown_no_throughput",
        )
    return ComputeToTarget(
        run_id=curve.run_id,
        target=target,
        tokens_to_target=tokens,
        compute_seconds_to_target=seconds,
        reached=True,
        basis=basis,
    )


def _interpolate(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    pairs = zip(zip(xs, ys, strict=True), zip(xs[1:], ys[1:], strict=False), strict=False)
    for (x0, y0), (x1, y1) in pairs:
        if min(x0, x1) <= x <= max(x0, x1):
            if x1 == x0:
                return float(y1)
            fraction = (x - x0) / (x1 - x0)
            return float(y0 + fraction * (y1 - y0))
    raise CurveError(f"position {x} is outside the measured range; no extrapolation")


@dataclass
class CurveComparison:
    """Head-to-head compute-to-target comparison of two runs."""

    baseline: ComputeToTarget
    candidate: ComputeToTarget
    compute_saving_fraction: float | None
    comparable: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "compute_saving_fraction": self.compute_saving_fraction,
            "comparable": self.comparable,
            "reasons": self.reasons,
        }


def compare_compute_to_target(
    baseline: ComputeToTarget, candidate: ComputeToTarget
) -> CurveComparison:
    """Compare only when both runs actually reached the target."""
    reasons: list[str] = []
    if not baseline.reached:
        reasons.append(f"baseline '{baseline.run_id}' never reached the target")
    if not candidate.reached:
        reasons.append(f"candidate '{candidate.run_id}' never reached the target")
    if reasons:
        return CurveComparison(
            baseline=baseline,
            candidate=candidate,
            compute_saving_fraction=None,
            comparable=False,
            reasons=reasons,
        )
    if baseline.compute_seconds_to_target is None or candidate.compute_seconds_to_target is None:
        return CurveComparison(
            baseline=baseline,
            candidate=candidate,
            compute_saving_fraction=None,
            comparable=False,
            reasons=["compute seconds unknown on at least one arm"],
        )
    if baseline.compute_seconds_to_target <= 0:
        return CurveComparison(
            baseline=baseline,
            candidate=candidate,
            compute_saving_fraction=None,
            comparable=False,
            reasons=["baseline compute is not positive"],
        )
    saving = 1.0 - candidate.compute_seconds_to_target / baseline.compute_seconds_to_target
    return CurveComparison(
        baseline=baseline,
        candidate=candidate,
        compute_saving_fraction=saving,
        comparable=True,
        reasons=[],
    )


def curves_from_mapping(data: Mapping[str, Any]) -> LearningCurve:
    """Rebuild a curve from a serialized mapping, validating its shape."""
    try:
        points = tuple(CurvePoint(**p) for p in data["points"])
        return LearningCurve(
            run_id=str(data["run_id"]),
            higher_is_better=bool(data["higher_is_better"]),
            points=points,
            teacher_compute_seconds=float(data.get("teacher_compute_seconds", 0.0)),
            auxiliary_compute_seconds=float(data.get("auxiliary_compute_seconds", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CurveError(f"malformed learning curve mapping: {exc}") from exc
