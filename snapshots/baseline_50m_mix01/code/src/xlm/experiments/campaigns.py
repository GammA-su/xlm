"""Campaign expansion: mixtures x sizes x seeds with horizon rules (P16, A29).

A campaign draft names a model/data template, mixture presets and
successive-budget rounds. Expansion is pure planning: every trial becomes a
complete resolved plan before anything executes, later rounds stay gated on an
explicit selection, and schedule-horizon rules are enforced per trial:

* continuation trials share the campaign-declared horizon (matched schedules);
* standalone trials restart with horizon-specific schedules (horizon == budget).

Continuation and standalone trials carry different horizon tags, so P17 can
never compare them as identical. Totals (trials, tokens by size, cost range,
storage) and approval blockers are reported; over-budget sweeps are refused.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from xlm.config.composer import ConfigComposer
from xlm.experiments.authorization import SMOKE_MAX_VALID_TARGETS, AuthorizationTicket

CAMPAIGN_VERSION = "1"


class CampaignError(RuntimeError):
    """Raised when a campaign cannot be expanded or authorized safely."""


@dataclass(frozen=True)
class BudgetRound:
    """One successive-budget round of a campaign."""

    round_id: str
    model_preset: str
    valid_targets_per_run: int
    init_seeds: tuple[int, ...]
    schedule_horizon_valid_targets: int
    restart_policy: str
    requires_selection: bool = False

    def horizon_kind(self) -> str:
        if self.restart_policy == "matched_full_horizon_prefix":
            return "continuation_prefix"
        return "standalone"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CampaignTrial:
    """One fully specified trial plan (complete config before execution)."""

    trial_id: str
    campaign_id: str
    round_id: str
    model_preset: str
    mixture_preset: str
    mixture_weights: dict[str, float]
    budget_valid_targets: int
    schedule_horizon_valid_targets: int
    horizon_kind: str
    init_seed: int
    data_seed: int
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CampaignPlan:
    """Expanded campaign: trials, totals, cost range and blockers."""

    campaign_id: str
    trials: list[CampaignTrial]
    total_trials: int
    tokens_by_size: dict[str, int]
    total_valid_targets: int
    cost_basis: str
    eta_seconds_range: list[float] | None
    storage_estimate_gib: float | None
    blockers: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "trials": [t.to_dict() for t in self.trials],
            "total_trials": self.total_trials,
            "tokens_by_size": self.tokens_by_size,
            "total_valid_targets": self.total_valid_targets,
            "cost_basis": self.cost_basis,
            "eta_seconds_range": self.eta_seconds_range,
            "storage_estimate_gib": self.storage_estimate_gib,
            "blockers": self.blockers,
            "created_at": self.created_at,
        }


def load_campaign_draft(path: Path | str) -> dict[str, Any]:
    """Load a campaign draft mapping."""
    draft_path = Path(path)
    if not draft_path.is_file():
        raise CampaignError(f"campaign draft not found: {draft_path}")
    data = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CampaignError(f"campaign draft at {draft_path} must be a mapping")
    return data


def _mixture_weights(composer: ConfigComposer, mixture_name: str) -> dict[str, float]:
    details = composer.load_mixture_preset(mixture_name)
    weights = details.get("weights", {}) if isinstance(details, dict) else {}
    if not weights:
        raise CampaignError(f"mixture preset '{mixture_name}' carries no weights")
    return {str(k): float(v) for k, v in weights.items()}


def _parse_rounds(draft: Mapping[str, Any]) -> list[BudgetRound]:
    rounds: list[BudgetRound] = []
    first = draft.get("first_round")
    if isinstance(first, dict):
        rounds.append(
            BudgetRound(
                round_id="round_0",
                model_preset=str(first["model_preset"]),
                valid_targets_per_run=int(first["valid_targets_per_run"]),
                init_seeds=tuple(int(s) for s in first.get("init_seeds", [101])),
                schedule_horizon_valid_targets=int(first["schedule_horizon_valid_targets"]),
                restart_policy=str(first.get("restart_policy", "matched_full_horizon_prefix")),
            )
        )
    later = draft.get("later_rounds_require_selection", [])
    if not isinstance(later, list):
        raise CampaignError("'later_rounds_require_selection' must be a list")
    for index, raw in enumerate(later, start=1):
        if not isinstance(raw, dict):
            raise CampaignError(f"later round {index} must be a mapping")
        horizon = raw.get("schedule_horizon_valid_targets")
        rounds.append(
            BudgetRound(
                round_id=f"round_{index}",
                model_preset=str(raw["model_preset"]),
                valid_targets_per_run=int(raw["valid_targets_per_run"]),
                init_seeds=tuple(int(s) for s in raw.get("init_seeds", [101])),
                schedule_horizon_valid_targets=int(
                    horizon if horizon is not None else raw["valid_targets_per_run"]
                ),
                restart_policy=str(raw.get("restart_policy", "standalone")),
                requires_selection=True,
            )
        )
    if not rounds:
        raise CampaignError("campaign draft defines no budget rounds")
    return rounds


def expand_campaign(
    draft_path: Path | str,
    workspace_root: Path | str,
    *,
    data_seed: int = 20260918,
    selections: Mapping[str, list[str]] | None = None,
    throughput_range: tuple[float, float] | None = None,
) -> CampaignPlan:
    """Expand a campaign draft into complete trial plans (planning only)."""
    draft = load_campaign_draft(draft_path)
    campaign_id = str(draft.get("id", Path(draft_path).stem))
    mixtures: list[str] = [str(m) for m in draft.get("mixtures", [])]
    if not mixtures:
        raise CampaignError("campaign draft names no mixtures")
    rounds = _parse_rounds(draft)
    composer = ConfigComposer(Path(workspace_root))
    selections = selections or {}

    trials: list[CampaignTrial] = []
    blockers: list[str] = []
    for mixture_name in mixtures:
        weights = _mixture_weights(composer, mixture_name)
        for budget_round in rounds:
            allowed = selections.get(budget_round.round_id)
            for init_seed in budget_round.init_seeds:
                trial_id = (
                    f"{campaign_id}_{mixture_name}_{budget_round.model_preset}_"
                    f"{budget_round.valid_targets_per_run}_{init_seed}"
                )
                blocked: str | None = None
                if budget_round.requires_selection and (
                    allowed is None or mixture_name not in allowed
                ):
                    blocked = (
                        f"round '{budget_round.round_id}' requires an explicit selection "
                        f"naming '{mixture_name}'"
                    )
                horizon = budget_round.schedule_horizon_valid_targets
                if budget_round.horizon_kind() == "standalone":
                    horizon = budget_round.valid_targets_per_run
                trials.append(
                    CampaignTrial(
                        trial_id=trial_id,
                        campaign_id=campaign_id,
                        round_id=budget_round.round_id,
                        model_preset=budget_round.model_preset,
                        mixture_preset=mixture_name,
                        mixture_weights=dict(weights),
                        budget_valid_targets=budget_round.valid_targets_per_run,
                        schedule_horizon_valid_targets=horizon,
                        horizon_kind=budget_round.horizon_kind(),
                        init_seed=init_seed,
                        data_seed=data_seed,
                        blocked_reason=blocked,
                    )
                )
    for trial in trials:
        if trial.blocked_reason and trial.blocked_reason not in blockers:
            blockers.append(trial.blocked_reason)

    tokens_by_size: dict[str, int] = {}
    total = 0
    for trial in trials:
        tokens_by_size[trial.model_preset] = (
            tokens_by_size.get(trial.model_preset, 0) + trial.budget_valid_targets
        )
        total += trial.budget_valid_targets

    cost_basis = "unmeasured"
    eta_range: list[float] | None = None
    if throughput_range is not None:
        lo, hi = throughput_range
        if lo > 0 and hi > 0:
            cost_basis = "measured_profile"
            eta_range = [total / hi, total / lo]

    return CampaignPlan(
        campaign_id=campaign_id,
        trials=trials,
        total_trials=len(trials),
        tokens_by_size=tokens_by_size,
        total_valid_targets=total,
        cost_basis=cost_basis,
        eta_seconds_range=eta_range,
        storage_estimate_gib=None,
        blockers=blockers,
        created_at=datetime.now(UTC).isoformat(),
    )


def record_selection(
    selections_path: Path | str, round_id: str, mixture_names: Sequence[str] | None = None
) -> dict[str, list[str]]:
    """Record an explicit operator selection gating a later round."""
    path = Path(selections_path)
    current: dict[str, list[str]] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = {str(k): [str(m) for m in v] for k, v in loaded.items()}
    current[round_id] = [str(m) for m in (mixture_names or [])]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    return current


def campaign_hash(plan: CampaignPlan) -> str:
    """Identity of a campaign expansion: trial IDs and budgets, hashed."""
    payload = {
        "campaign": plan.campaign_id,
        "trials": sorted(
            (t.trial_id, t.budget_valid_targets, t.init_seed, t.mixture_preset) for t in plan.trials
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_campaign_budget(plan: CampaignPlan, ticket: AuthorizationTicket | None) -> None:
    """Refuse an over-budget or unauthorized sweep before anything is submitted.

    Without a ticket, every executable trial must fit the smoke caps. With a
    ticket, it must bind this exact campaign expansion and cover its total.
    """
    executable = [t for t in plan.trials if t.blocked_reason is None]
    if ticket is None:
        over = [t for t in executable if t.budget_valid_targets > SMOKE_MAX_VALID_TARGETS]
        if over:
            raise CampaignError(
                f"campaign has {len(over)} trial(s) above the smoke cap with no ticket; "
                f"first offender '{over[0].trial_id}' needs "
                f"{over[0].budget_valid_targets:,} targets"
            )
        return
    expected = campaign_hash(plan)
    if ticket.plan_hash != expected:
        raise CampaignError(
            f"ticket '{ticket.ticket_id}' binds a different campaign (expected '{expected[:12]}')"
        )
    if plan.total_valid_targets > ticket.max_valid_targets:
        raise CampaignError(
            f"campaign total {plan.total_valid_targets:,} targets exceeds ticket "
            f"allowance {ticket.max_valid_targets:,}"
        )
