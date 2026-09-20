"""Comparison-track contracts and eligibility checking (C12, A30).

A comparison is eligible only when the two runs differ solely in ways their
track allows. Anything else is marked ineligible with a readable field-level
diff -- never given a misleading causal-improvement badge. Unknown fields fail
closed: an undocumented difference cannot be waved through.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

TRACK_CONTRACT_VERSION = "1"

# Fields every track requires to be known. "unknown" fails closed.
REQUIRED_FIELDS = (
    "architecture_id",
    "model_config",
    "total_params",
    "tokenizer_hash",
    "vocab_size",
    "mixture_id",
    "mixture_weights",
    "canonical_bytes",
    "packing",
    "data_seed",
    "objective_id",
    "objective_extras",
    "optimizer_id",
    "tuning_allowance",
    "init_seeds",
    "context_length",
    "budget_targets",
    "measured_compute_seconds",
    "horizon_kind",
    "precision",
    "matched_bytes",
    "matched_compute",
)

# Execution modes that must match on every causal track.
EXECUTION_FIXED = ("precision",)


@dataclass(frozen=True)
class TrackRule:
    """Allowed-differences schema for one comparison track."""

    track: str
    fixed: tuple[str, ...]
    allowed: tuple[str, ...]
    description: str = ""


TRACK_RULES: dict[str, TrackRule] = {
    "architecture": TrackRule(
        track="architecture",
        fixed=(
            "tokenizer_hash",
            "vocab_size",
            "mixture_id",
            "mixture_weights",
            "canonical_bytes",
            "packing",
            "data_seed",
            "objective_id",
            "objective_extras",
            "optimizer_id",
            "tuning_allowance",
            "context_length",
            "budget_targets",
            "horizon_kind",
            "precision",
        ),
        allowed=(
            "architecture_id",
            "model_config",
            "total_params",
            "init_seeds",
            "measured_compute_seconds",
        ),
        description="Fixed data/tokenizer/order/packing/objective with resource and "
        "parameter bounds; only the architecture (and paired initialization) may differ.",
    ),
    "objective": TrackRule(
        track="objective",
        fixed=(
            "architecture_id",
            "model_config",
            "total_params",
            "tokenizer_hash",
            "vocab_size",
            "mixture_id",
            "mixture_weights",
            "canonical_bytes",
            "packing",
            "data_seed",
            "optimizer_id",
            "tuning_allowance",
            "context_length",
            "budget_targets",
            "horizon_kind",
            "precision",
        ),
        allowed=("objective_id", "objective_extras", "init_seeds", "measured_compute_seconds"),
        description="Same architecture/data with extra supervision and compute disclosed.",
    ),
    "optimizer": TrackRule(
        track="optimizer",
        fixed=(
            "architecture_id",
            "model_config",
            "total_params",
            "tokenizer_hash",
            "vocab_size",
            "mixture_id",
            "mixture_weights",
            "canonical_bytes",
            "packing",
            "data_seed",
            "objective_id",
            "objective_extras",
            "context_length",
            "budget_targets",
            "horizon_kind",
            "precision",
        ),
        allowed=("optimizer_id", "tuning_allowance", "init_seeds", "measured_compute_seconds"),
        description="Equal tuning allowance and state accounting; only the optimizer may differ.",
    ),
    "tokenizer": TrackRule(
        track="tokenizer",
        fixed=(
            "architecture_id",
            "mixture_id",
            "mixture_weights",
            "packing",
            "data_seed",
            "objective_id",
            "objective_extras",
            "optimizer_id",
            "tuning_allowance",
            "context_length",
            "budget_targets",
            "horizon_kind",
            "precision",
        ),
        allowed=(
            "tokenizer_hash",
            "vocab_size",
            "total_params",
            "canonical_bytes",
            "init_seeds",
            "measured_compute_seconds",
        ),
        description="Matched canonical bytes and matched compute, total parameters "
        "including vocabulary, context policy explicit.",
    ),
    "data-mixture": TrackRule(
        track="data-mixture",
        fixed=(
            "architecture_id",
            "model_config",
            "total_params",
            "tokenizer_hash",
            "vocab_size",
            "packing",
            "data_seed",
            "objective_id",
            "objective_extras",
            "optimizer_id",
            "tuning_allowance",
            "context_length",
            "budget_targets",
            "horizon_kind",
            "precision",
        ),
        allowed=(
            "mixture_id",
            "mixture_weights",
            "canonical_bytes",
            "init_seeds",
            "measured_compute_seconds",
        ),
        description="Shared model/tokenizer/objective/optimizer; only the mixture may differ.",
    ),
    "unconstrained-system": TrackRule(
        track="unconstrained-system",
        fixed=("precision",),
        allowed=tuple(f for f in REQUIRED_FIELDS if f != "precision"),
        description="Anything may differ; labeled as a system comparison, never a "
        "causal claim about one mechanism.",
    ),
}


@dataclass(frozen=True)
class ComparisonRun:
    """One run's comparison-relevant facts, built from evidence and plan records."""

    run_id: str
    fields: dict[str, Any]

    def get(self, name: str) -> Any:
        return self.fields.get(name, "unknown")

    @classmethod
    def from_records(
        cls,
        run_id: str,
        evidence: Mapping[str, Any],
        plan: Mapping[str, Any],
        extra: Mapping[str, Any] | None = None,
    ) -> ComparisonRun:
        """Assemble facts from a P15 evidence dict and a P16 plan dict.

        Explicit ``extra`` facts (measured parameters, byte exposure, matched
        evidence) override anything inferred. Missing facts become "unknown"
        and fail closed at eligibility time.
        """
        resolved = plan.get("resolved_config", {})
        model = resolved.get("model", {})
        training = resolved.get("training", {})
        data = resolved.get("data", {})
        mixture = data.get("mixture_details", {}) if isinstance(data, dict) else {}
        identity = evidence.get("identity", {})
        fields: dict[str, Any] = {
            "architecture_id": model.get("architecture", "unknown"),
            "model_config": {
                k: model.get(k)
                for k in (
                    "num_layers",
                    "hidden_size",
                    "num_attention_heads",
                    "intermediate_size",
                    "vocab_size",
                    "context_length",
                )
            },
            "total_params": evidence.get("unique_parameters", "unknown"),
            "tokenizer_hash": identity.get("tokenizer_hash", "unknown"),
            "vocab_size": model.get("vocab_size", "unknown"),
            "mixture_id": mixture.get("id", data.get("mixture_preset", "unknown")),
            "mixture_weights": mixture.get("weights", "unknown"),
            "canonical_bytes": evidence.get("canonical_bytes", "unknown"),
            "packing": data.get("packing_policy", "unknown"),
            "data_seed": training.get("data_seed", "unknown"),
            "objective_id": resolved.get("objective", {}).get("type", "unknown"),
            "objective_extras": resolved.get("objective", {}).get("extras", None),
            "optimizer_id": resolved.get("optimizer", {}).get("type", "unknown"),
            "tuning_allowance": resolved.get("optimizer", {}).get("tuning_allowance", "unknown"),
            "init_seeds": training.get("init_seed", "unknown"),
            "context_length": training.get("context_length", "unknown"),
            "budget_targets": training.get("budget", {}).get("max_valid_targets", "unknown"),
            "measured_compute_seconds": evidence.get("measured_compute_seconds", "unknown"),
            "horizon_kind": plan.get("horizon_kind", "unknown"),
            "precision": training.get("precision", "unknown"),
            "matched_bytes": evidence.get("matched_bytes", None),
            "matched_compute": evidence.get("matched_compute", None),
        }
        for key, value in dict(extra or {}).items():
            if key in fields and value is not None:
                fields[key] = value
        return cls(run_id=run_id, fields=fields)


@dataclass(frozen=True)
class FieldDiff:
    """One field-level difference with its verdict."""

    field: str
    baseline: Any
    candidate: Any
    verdict: str  # 'same' | 'allowed' | 'violation' | 'unknown'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EligibilityResult:
    """Eligibility verdict for one track, with the readable diff."""

    track: str
    eligible: bool
    diffs: list[FieldDiff] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "eligible": self.eligible,
            "diffs": [d.to_dict() for d in self.diffs],
            "reasons": self.reasons,
        }


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return bool(left == right)
    if isinstance(left, float) and isinstance(right, float):
        return left == right
    return bool(left == right)


def check_track_eligibility(
    baseline: ComparisonRun, candidate: ComparisonRun, track: str
) -> EligibilityResult:
    """Check one pair against one track's allowed-differences schema."""
    if track not in TRACK_RULES:
        return EligibilityResult(track=track, eligible=False, reasons=[f"unknown track '{track}'"])
    rule = TRACK_RULES[track]
    result = EligibilityResult(track=track, eligible=True)

    for name in REQUIRED_FIELDS:
        left = baseline.get(name)
        right = candidate.get(name)
        if left == "unknown" or right == "unknown":
            result.diffs.append(FieldDiff(name, left, right, "unknown"))
            result.eligible = False
            result.reasons.append(
                f"field '{name}' is unknown on "
                f"{'baseline' if left == 'unknown' else 'candidate'}; "
                "undocumented differences fail closed"
            )
        elif _values_equal(left, right):
            result.diffs.append(FieldDiff(name, left, right, "same"))
        elif name in rule.fixed:
            result.diffs.append(FieldDiff(name, left, right, "violation"))
            result.eligible = False
            result.reasons.append(
                f"field '{name}' differs on track '{track}' but must be fixed "
                f"({left!r} vs {right!r})"
            )
        else:
            result.diffs.append(FieldDiff(name, left, right, "allowed"))

    # Track-specific requirements beyond field equality.
    if result.eligible and track == "tokenizer":
        matched_bytes = candidate.get("matched_bytes")
        matched_compute = candidate.get("matched_compute")
        if not (isinstance(matched_bytes, dict) and matched_bytes.get("matched") is True):
            result.eligible = False
            result.reasons.append(
                "tokenizer track requires matched-canonical-byte evidence "
                "(P12 matched plan) and it is absent"
            )
        if not (isinstance(matched_compute, dict) and matched_compute.get("matched") is True):
            result.eligible = False
            result.reasons.append(
                "tokenizer track requires matched-compute evidence and it is absent"
            )

    return result
