"""Explicit trial enumeration: lists, grids and bounded mixture proposals (P16).

Every trial is materialized before any job starts. Random and simplex proposals
are deterministic functions of an explicit seed, and the total trial count is
capped: no opaque optimizer chooses thousands of trials in the background.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

SWEEP_VERSION = "1"
DEFAULT_MAX_TRIALS = 256


class SweepError(ValueError):
    """Raised when a sweep is underspecified or exceeds its trial budget."""


def expand_list(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """An explicit experiment list, validated and materialized."""
    if not items:
        raise SweepError("explicit experiment list is empty")
    return [dict(item) for item in items]


def grid_sweep(
    axes: Mapping[str, Sequence[Any]],
    *,
    max_trials: int = DEFAULT_MAX_TRIALS,
) -> list[dict[str, Any]]:
    """Cartesian product over named axes in deterministic (sorted-axis) order."""
    if not axes:
        raise SweepError("grid sweep needs at least one axis")
    names = sorted(axes)
    for name in names:
        if not axes[name]:
            raise SweepError(f"grid axis '{name}' is empty")
    trials = [
        dict(zip(names, combo, strict=True))
        for combo in itertools.product(*(axes[n] for n in names))
    ]
    if len(trials) > max_trials:
        raise SweepError(
            f"grid sweep proposes {len(trials)} trials, above the cap of {max_trials}; "
            "narrow the axes or raise the cap explicitly."
        )
    return trials


def _normalize_weights(raw: Mapping[str, float]) -> dict[str, float]:
    total = sum(raw.values())
    if total <= 0:
        raise SweepError("mixture weights must sum to a positive total")
    return {k: v / total for k, v in raw.items()}


def random_mixture_proposals(
    components: Sequence[str],
    count: int,
    seed: int,
    concentration: float = 2.0,
    round_digits: int = 4,
    max_trials: int = DEFAULT_MAX_TRIALS,
) -> list[dict[str, float]]:
    """Deterministic Dirichlet-like proposals around a uniform center.

    Uses an explicit seed and gamma sampling via ``random.gammavariate``; the
    same seed always yields the same proposals. Counts are capped like grids.
    """
    if count <= 0:
        raise SweepError("proposal count must be positive")
    if count > max_trials:
        raise SweepError(f"{count} proposals exceed the cap of {max_trials}")
    if not components:
        raise SweepError("mixture proposals need at least one component")
    if concentration <= 0:
        raise SweepError("concentration must be positive")
    rng = random.Random(seed)
    proposals: list[dict[str, float]] = []
    for _ in range(count):
        draws = [rng.gammavariate(concentration, 1.0) for _ in components]
        total = sum(draws)
        proposals.append(_quantize([draw / total for draw in draws], components, round_digits))
    return proposals


def _quantize(
    shares: list[float], components: Sequence[str], round_digits: int
) -> dict[str, float]:
    """Round shares to a fixed lattice with largest-remainder repair.

    Plain rounding can leave the total at 0.9999; the repair distributes the
    leftover units to the largest fractional parts, so proposals always sum to
    exactly one. Order is deterministic.
    """
    units = 10**round_digits
    scaled = [share * units for share in shares]
    floors = [math.floor(value) for value in scaled]
    remainder = units - sum(floors)
    order = sorted(range(len(shares)), key=lambda i: (scaled[i] - floors[i], i), reverse=True)
    for index in order[: max(0, remainder)]:
        floors[index] += 1
    return {name: floors[i] / units for i, name in enumerate(components)}


def simplex_mixture_proposals(
    components: Sequence[str],
    granularity: Fraction | float | str = "1/8",
    max_trials: int = DEFAULT_MAX_TRIALS,
) -> list[dict[str, float]]:
    """Coarse simplex grid: every weight vector on a fixed rational lattice.

    With granularity 1/n, enumerates all weak compositions summing to n. Order
    is lexicographic over components, so output is deterministic.
    """
    step = Fraction(str(granularity))
    if step <= 0 or step > 1:
        raise SweepError("granularity must be in (0, 1]")
    units = int(Fraction(1) / step)
    if Fraction(units) * step != 1:
        raise SweepError(f"granularity {step} does not divide 1 evenly")
    if not components:
        raise SweepError("mixture proposals need at least one component")

    results: list[dict[str, float]] = []

    def compositions(remaining: int, parts: int) -> list[list[int]]:
        if parts == 1:
            return [[remaining]]
        out: list[list[int]] = []
        for first in range(remaining + 1):
            for rest in compositions(remaining - first, parts - 1):
                out.append([first, *rest])
        return out

    for combo in compositions(units, len(components)):
        results.append(
            {name: float(Fraction(u, units)) for name, u in zip(components, combo, strict=True)}
        )
    if len(results) > max_trials:
        raise SweepError(
            f"simplex grid proposes {len(results)} trials, above the cap of {max_trials}; "
            "coarsen the granularity or raise the cap explicitly."
        )
    return results


def mixture_weight_diff(
    base: Mapping[str, float], variant: Mapping[str, float]
) -> dict[str, float]:
    """Signed per-component delta between two complete mixtures."""
    keys = set(base) | set(variant)
    return {k: variant.get(k, 0.0) - base.get(k, 0.0) for k in sorted(keys)}
