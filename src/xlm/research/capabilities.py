"""Plugin capability declarations and plan-time combination checks (P18, A33).

Every plugin declares what it needs; every combination is checked before any
training plan executes. Unsupported combinations are refused at plan time with
named reasons, never discovered mid-run as silent misbehavior. The checks cover
recurrent state reset, auxiliary-output requirements, accumulation protocols,
optimizer closures, deterministic tokenizer scoring, parameter caps, cache
support and inference-only input availability.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

CAPABILITY_VERSION = "1"


@dataclass(frozen=True)
class PluginCapabilities:
    """Declared needs and properties of one research plugin."""

    category: str
    # Architecture / model concerns.
    has_recurrent_state: bool = False
    supports_state_reset: bool = True
    declares_auxiliary_outputs: bool = False
    requires_auxiliary_outputs: bool = False
    supports_kv_cache: bool = False
    uses_inference_only_inputs: bool = True
    # Objective concerns.
    loss_protocol: str = "token_additive"
    supports_microbatching: bool = True
    has_auxiliary_parameters: bool = False
    requires_hidden_states: bool = False
    # Optimizer concerns.
    requires_closure: bool = False
    has_optimizer_state: bool = True
    # Tokenizer concerns.
    deterministic_scoring: bool = True
    # Resource bounds.
    max_parameters: int | None = None
    extra_compute_per_step: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PluginCapabilities:
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown capability fields: {unknown}")
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class ExecutionContext:
    """What the surrounding run provides to a plugin combination."""

    provides_hidden_states: bool = False
    supports_optimizer_closures: bool = False
    allows_auxiliary_parameters: bool = True
    parameter_cap: int | None = None
    tokenizer_scoring_required: bool = True
    inference_inputs_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_combination(
    model_caps: PluginCapabilities | None = None,
    objective_caps: PluginCapabilities | None = None,
    optimizer_caps: PluginCapabilities | None = None,
    tokenizer_caps: PluginCapabilities | None = None,
    context: ExecutionContext | None = None,
    total_parameters: int | None = None,
) -> list[str]:
    """Check one plugin combination against the execution context.

    Returns the list of violations; empty means the combination may run. Every
    refusal names the capability and the missing support.
    """
    ctx = context or ExecutionContext()
    violations: list[str] = []

    if model_caps is not None:
        if model_caps.has_recurrent_state and not model_caps.supports_state_reset:
            violations.append(
                "model has recurrent state without a reset operation; "
                "branch-state tests cannot pass"
            )
        if model_caps.requires_auxiliary_outputs and not model_caps.declares_auxiliary_outputs:
            violations.append("model requires auxiliary outputs it does not declare")
        if not model_caps.uses_inference_only_inputs:
            violations.append(
                "model does not restrict itself to inference-only inputs; "
                "gold labels must not enter the inference path"
            )

    if objective_caps is not None:
        if objective_caps.requires_hidden_states and not ctx.provides_hidden_states:
            violations.append(
                "objective requires hidden states the trainer does not provide; "
                "request them explicitly or reject the combination"
            )
        if objective_caps.loss_protocol not in ("token_additive", "custom_batch"):
            violations.append(
                f"objective loss_protocol '{objective_caps.loss_protocol}' is not a "
                "supported accumulation protocol"
            )
        if objective_caps.loss_protocol == "custom_batch" and objective_caps.supports_microbatching:
            violations.append(
                "objective declares a custom-batch protocol yet claims microbatching "
                "support; declare the alternative accumulation protocol instead"
            )
        if objective_caps.has_auxiliary_parameters and not ctx.allows_auxiliary_parameters:
            violations.append("objective-owned parameters are disallowed by this context")

    if optimizer_caps is not None:
        if optimizer_caps.requires_closure and not ctx.supports_optimizer_closures:
            violations.append(
                "optimizer requires closures the trainer never supplies; "
                "reject the combination at plan time"
            )

    if tokenizer_caps is not None and ctx.tokenizer_scoring_required:
        if not tokenizer_caps.deterministic_scoring:
            violations.append(
                "tokenizer scoring is not deterministic; evaluation likelihoods would not reproduce"
            )

    cap = ctx.parameter_cap
    if cap is not None and total_parameters is not None and total_parameters > cap:
        violations.append(f"total parameters {total_parameters:,} exceed the cap {cap:,}")
    for caps in (model_caps, objective_caps, optimizer_caps, tokenizer_caps):
        if caps is not None and caps.max_parameters is not None:
            if total_parameters is not None and total_parameters > caps.max_parameters:
                violations.append(
                    f"total parameters {total_parameters:,} exceed the plugin-declared "
                    f"cap {caps.max_parameters:,} ({caps.category})"
                )

    return violations


@dataclass
class CapabilityReport:
    """Capability check outcome for one plugin or combination."""

    subject: str
    violations: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "supported": self.supported, "violations": self.violations}
