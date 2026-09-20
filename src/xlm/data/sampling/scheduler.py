"""Token-quota / deficit scheduler with observed-share accounting.

Contract C07: observed valid-target token shares must match configured weights within
a documented bound; EOS is attributed to its document's source; BOS and padding are
excluded from the budget; content-token shares are reported alongside; and source
exhaustion follows the declared policy with no silent renormalization.

The scheduler decides *which source* supplies the next run of tokens. It does not
read tokens itself -- that keeps the share arithmetic testable in isolation from IO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import blake2b
from typing import Any

from xlm.data.sampling.mixture import MixtureRecipe, SourceAvailability


class SourceExhaustedError(RuntimeError):
    """Raised when a source runs out of tokens and the policy forbids repetition."""


class RepeatBudgetExceededError(RuntimeError):
    """Raised when bounded repetition exceeds its declared ceiling."""


@dataclass
class SourceCounters:
    """Per-source exposure counters, separating unique from repeated exposure."""

    source_id: str
    valid_targets: int = 0
    repeated_targets: int = 0
    content_targets: int = 0
    eos_targets: int = 0
    bos_excluded: int = 0
    padding_excluded: int = 0
    documents_visited: int = 0
    canonical_bytes: int = 0
    repeated_bytes: int = 0
    epoch: int = 0
    cursor: int = 0

    @property
    def unique_targets(self) -> int:
        """Targets drawn from text not yet seen in this run."""
        return self.valid_targets - self.repeated_targets

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "unique_targets": self.unique_targets}


@dataclass
class ScheduleState:
    """Serializable scheduler state, restored exactly on resume."""

    counters: dict[str, SourceCounters]
    total_valid_targets: int = 0
    total_repeated_targets: int = 0
    deficits: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counters": {k: v.to_dict() for k, v in sorted(self.counters.items())},
            "total_valid_targets": self.total_valid_targets,
            "total_repeated_targets": self.total_repeated_targets,
            "deficits": self.deficits,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ScheduleState:
        counters = {}
        for source_id, payload in data["counters"].items():
            fields = {k: v for k, v in payload.items() if k != "unique_targets"}
            counters[source_id] = SourceCounters(**fields)
        return ScheduleState(
            counters=counters,
            total_valid_targets=data["total_valid_targets"],
            total_repeated_targets=data["total_repeated_targets"],
            deficits=dict(data.get("deficits", {})),
        )


@dataclass
class ShareReport:
    """Observed versus configured shares, in both target and content terms."""

    target_shares: dict[str, float]
    content_shares: dict[str, float]
    configured_weights: dict[str, float]
    max_drift: float
    within_bound: bool
    drift_bound: float
    total_valid_targets: int
    total_repeated_targets: int
    eos_share: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def structural_gap(self) -> dict[str, float]:
        """Per-source difference between target share and content-token share.

        C07 asks the planning report to show where structural tokens move a share.
        """
        return {
            source: self.target_shares[source] - self.content_shares.get(source, 0.0)
            for source in self.target_shares
        }


class QuotaScheduler:
    """Deficit-based source selection over valid target tokens."""

    def __init__(
        self,
        recipe: MixtureRecipe,
        availability: dict[str, SourceAvailability],
        state: ScheduleState | None = None,
    ) -> None:
        self.recipe = recipe
        self.availability = availability
        self.source_ids = sorted(
            (c.source_id for c in recipe.components),
            key=lambda s: blake2b(f"{recipe.data_seed}:{s}".encode(), digest_size=16).hexdigest(),
        )

        missing = [s for s in self.source_ids if s not in availability]
        if missing:
            raise KeyError(f"no availability recorded for source(s): {missing}")

        self.state = state or ScheduleState(
            counters={s: SourceCounters(source_id=s) for s in self.source_ids}
        )

    def deficit_of(self, source_id: str) -> float:
        """Tokens this source is owed relative to its configured weight."""
        weight = self.recipe.weight_of(source_id)
        expected = weight * self.state.total_valid_targets
        return expected - self.state.counters[source_id].valid_targets

    def select_source(self) -> str:
        """Pick the source with the largest deficit, breaking ties deterministically.

        At the very start every deficit is zero, so the tie-break decides; ordering by
        source_id keeps the first selection reproducible rather than arbitrary.
        """
        return max(self.source_ids, key=lambda s: (self.deficit_of(s), -self.source_ids.index(s)))

    def record_exposure(
        self,
        source_id: str,
        valid_targets: int,
        content_targets: int = 0,
        eos_targets: int = 0,
        bos_excluded: int = 0,
        padding_excluded: int = 0,
        canonical_bytes: int = 0,
        repeated: bool = False,
        documents: int = 0,
    ) -> None:
        """Record tokens actually exposed from a source.

        ``valid_targets`` excludes BOS and padding by construction: those are passed
        separately so they can be reported without ever entering the budget (C07).
        """
        if valid_targets < 0:
            raise ValueError(f"valid_targets must be non-negative, got {valid_targets}")

        counters = self.state.counters[source_id]
        counters.valid_targets += valid_targets
        counters.content_targets += content_targets
        counters.eos_targets += eos_targets
        counters.bos_excluded += bos_excluded
        counters.padding_excluded += padding_excluded
        counters.canonical_bytes += canonical_bytes
        counters.documents_visited += documents

        if repeated:
            counters.repeated_targets += valid_targets
            counters.repeated_bytes += canonical_bytes
            self.state.total_repeated_targets += valid_targets

        self.state.total_valid_targets += valid_targets
        self.state.deficits = {s: self.deficit_of(s) for s in self.source_ids}

        ceiling = self.recipe.exhaustion.max_repeated_targets
        if ceiling is not None and self.state.total_repeated_targets > ceiling:
            raise RepeatBudgetExceededError(
                f"repeated exposure {self.state.total_repeated_targets:,} exceeded the "
                f"declared ceiling of {ceiling:,} repeated target tokens"
            )

    def advance_epoch(self, source_id: str) -> None:
        """Move a source to its next epoch, enforcing the exhaustion policy.

        With ``repeat: false`` this raises. There is no fallback that quietly draws
        from another source, because that would silently change the mixture.
        """
        counters = self.state.counters[source_id]
        policy = self.recipe.exhaustion

        if not policy.repeat:
            raise SourceExhaustedError(
                f"source '{source_id}' is exhausted after {counters.valid_targets:,} "
                "target tokens and the mixture sets repeat=false. Weight is not "
                "redistributed to other sources."
            )

        next_epoch = counters.epoch + 1
        if next_epoch >= policy.max_epochs:
            raise SourceExhaustedError(
                f"source '{source_id}' reached its maximum of {policy.max_epochs} epoch(s); "
                "bounded repetition is exhausted."
            )
        counters.epoch = next_epoch
        counters.cursor = 0

    def share_report(self) -> ShareReport:
        """Report observed shares against configured weights."""
        total = self.state.total_valid_targets
        target_shares = {
            s: (self.state.counters[s].valid_targets / total if total else 0.0)
            for s in self.source_ids
        }

        total_content = sum(self.state.counters[s].content_targets for s in self.source_ids)
        content_shares = {
            s: (self.state.counters[s].content_targets / total_content if total_content else 0.0)
            for s in self.source_ids
        }

        configured = {s: self.recipe.weight_of(s) for s in self.source_ids}
        drifts = [abs(target_shares[s] - configured[s]) for s in self.source_ids]
        max_drift = max(drifts) if drifts else 0.0
        total_eos = sum(self.state.counters[s].eos_targets for s in self.source_ids)

        return ShareReport(
            target_shares=target_shares,
            content_shares=content_shares,
            configured_weights=configured,
            max_drift=max_drift,
            within_bound=max_drift <= self.recipe.max_share_drift,
            drift_bound=self.recipe.max_share_drift,
            total_valid_targets=total,
            total_repeated_targets=self.state.total_repeated_targets,
            eos_share=(total_eos / total if total else 0.0),
        )

    def get_state(self) -> dict[str, Any]:
        return self.state.to_dict()

    def load_state(self, payload: dict[str, Any]) -> None:
        self.state = ScheduleState.from_dict(payload)
