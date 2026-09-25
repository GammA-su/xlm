"""The small contract between science-v1 evaluators and the event controller."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from xlm.evaluation.cadence import EventTier
from xlm.evaluation.receipts import AttemptOutcome


class EvaluationFailure(RuntimeError):
    """An evaluation could not produce a trustworthy result."""


class InvalidMetricError(EvaluationFailure):
    """A metric is missing, non-finite or outside its mathematical range."""


class MissingDomainError(EvaluationFailure):
    """A required validation domain is absent or scored nothing."""


class EvaluationStateMutationError(EvaluationFailure):
    """Evaluation changed state it must never change.

    ``live`` is true when the training state itself was touched, which leaves
    the in-memory run unusable; false when only the evaluated replica changed,
    which invalidates the score but not training.
    """

    def __init__(self, message: str, *, components: tuple[str, ...], live: bool) -> None:
        super().__init__(message)
        self.components = components
        self.live = live


@dataclass
class EvaluationContext:
    """Per-boundary facts and caches shared by the events of one committed state."""

    event_id: str
    actual_committed_targets: int
    model_state_digest: str
    provenance: dict[str, Any]
    #: Per-document sufficient statistics, keyed by scoring identity and text
    #: digest. Valid only for the one model state of this boundary.
    document_cache: dict[tuple[str, str], Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationOutcome:
    """A finished evaluation. Failures raise instead of returning an outcome."""

    status: AttemptOutcome
    metrics: dict[str, Any]
    coverage: dict[str, Any]
    extra_files: dict[str, bytes] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is AttemptOutcome.FAILED:
            raise ValueError("a failed evaluation raises; it never returns an outcome")
        require_finite(self.metrics, "metrics")


class Evaluator(Protocol):
    """One evaluation tier's implementation, bound to frozen local inputs."""

    tier: EventTier

    def identity(self) -> dict[str, Any]: ...

    def evaluate(
        self, model: Any, *, device: str, context: EvaluationContext
    ) -> EvaluationOutcome: ...


def require_finite(value: Any, where: str) -> None:
    """Refuse NaN/inf anywhere in a metric tree; ``None`` is allowed only as 'not scored'."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidMetricError(f"{where} is not finite: {value!r}")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            require_finite(child, f"{where}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            require_finite(child, f"{where}[{index}]")
        return
    raise InvalidMetricError(f"{where} has non-metric type {type(value).__name__}")
