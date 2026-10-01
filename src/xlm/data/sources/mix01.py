"""XLM-Mix-01 source views, preset validation, treatment diffs and run gating.

Contract C04: a source is not trainable until revision, schema, tested adapter,
license/provenance review and explicit operator approval are all resolved. This
module binds the mix01 mixture components to their upstream families with exact
observed subset/config names (never assumed category names), validates the
mixture-preset pack exactly, diffs the M0-M5 treatments, and refuses to run a
mixture whose views are unknown, denied, unadmitted or insufficient. There is no
automatic fallback: the no-IFM treatment is an explicit separate preset, never a
runtime substitution.

Live-discovery observations used here are metadata-only (repository SHAs, config
names, licenses); no adapter has seen live rows and no operator admission exists,
so every view reports ``live_verified=False`` until a real pilot changes that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel
from xlm.data.pools.views import SourceView, ViewSelector
from xlm.data.sources.policy import is_denied_source

MIX01_REGISTRY_VERSION = "1"
MIX01_PRESET_VERSION = "1"


class PresetValidationError(ValueError):
    """Raised when a mixture preset is malformed or violates no-fallback policy."""


class Mix01BlockedError(RuntimeError):
    """Raised when a mixture cannot run: unknown, denied, unadmitted or insufficient views."""


class PilotNotAuthorizedError(PermissionError):
    """Raised when a data pilot is requested without explicit operator authorization."""


class ComponentReadiness(StrEnum):
    """Per-component run readiness, reported source by source."""

    READY = "ready"
    BLOCKED = "blocked"
    NOT_LIVE_VERIFIED = "not_live_verified"


class Mix01ViewSpec(StrictConfigModel):
    """One mixture component bound to its upstream family and adapter."""

    component_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    observed_revision: str | None = Field(default=None)
    observed_license: str | None = Field(default=None)
    observed_configs: list[str] = Field(default_factory=list)
    adapter_id: str = Field(min_length=1)
    extra_adapter_ids: list[str] = Field(
        default_factory=list,
        description="Additional adapters when one component spans several subset schemas.",
    )
    upstream_selector: dict[str, Any] = Field(default_factory=dict)
    english_only: bool = Field(default=True)
    live_verified: bool = Field(default=False)
    notes: list[str] = Field(default_factory=list)


class Mix01ViewRegistry(StrictConfigModel):
    """The validated data-only view registry for mix01 and its treatments."""

    schema_version: int = Field(default=1)
    kind: str = Field(default="mix01_view_registry")
    registry_id: str = Field(min_length=1)
    views: list[Mix01ViewSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_components(self) -> Mix01ViewRegistry:
        seen = [v.component_id for v in self.views]
        duplicates = sorted({c for c in seen if seen.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate component_id in mix01 view registry: {duplicates}")
        return self

    def get(self, component_id: str) -> Mix01ViewSpec:
        for view in self.views:
            if view.component_id == component_id:
                return view
        raise KeyError(f"unknown mix01 component '{component_id}'")


def load_mix01_views(path: Path | str) -> Mix01ViewRegistry:
    """Load and strictly validate the mix01 view registry YAML."""
    registry_path = Path(path)
    if not registry_path.is_file():
        raise FileNotFoundError(f"mix01 view registry not found: {registry_path}")
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"mix01 view registry at {registry_path} must be a YAML mapping")
    return Mix01ViewRegistry.model_validate(data)


class MixturePreset(StrictConfigModel):
    """One mixture treatment from the preset pack (M0-M5 and the no-IFM alternative)."""

    schema_version: int = Field(default=1)
    kind: str = Field(default="mixture_preset")
    id: str = Field(min_length=1)
    status: str = Field(default="draft_unvalidated")
    description: str = Field(default="")
    weight_unit: str = Field(default="valid_target_tokens")
    weights: dict[str, float] = Field(min_length=1)
    missing_source_policy: str = Field(default="error")
    exhaustion_policy: str = Field(default="error")
    allow_silent_renormalization: bool = Field(default=False)
    max_document_exposures: int = Field(default=1, ge=1)
    scheduler: str = Field(default="token_deficit_v1")
    source_seed: int = Field(default=20260918)
    pool_artifact: str | None = Field(default=None)
    tokenizer_artifact: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_no_silent_fallback(self) -> MixturePreset:
        if self.allow_silent_renormalization:
            raise ValueError(
                f"preset '{self.id}' allows silent renormalization; "
                "C04/C07 forbid silent substitution or fallback."
            )
        if self.missing_source_policy != "error":
            raise ValueError(
                f"preset '{self.id}' sets missing_source_policy="
                f"'{self.missing_source_policy}'; only 'error' is supported."
            )
        return self

    def weight_sum_exact(self) -> Fraction:
        """Exact decimal weight sum. Float arithmetic is not trusted here."""
        return sum((Fraction(str(w)) for w in self.weights.values()), Fraction(0))

    def identity(self) -> str:
        """Behavioral identity: a different treatment hashes differently."""
        payload = (
            f"preset:v{MIX01_PRESET_VERSION}:{self.id}:"
            + "|".join(f"{k}={self.weights[k]!r}" for k in sorted(self.weights))
            + f"#missing={self.missing_source_policy}#exh={self.exhaustion_policy}"
            + f"#sched={self.scheduler}#seed={self.source_seed}"
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:32]


def load_mixture_preset(path: Path | str) -> MixturePreset:
    """Load and strictly validate one mixture preset YAML."""
    preset_path = Path(path)
    if not preset_path.is_file():
        raise FileNotFoundError(f"mixture preset not found: {preset_path}")
    data = yaml.safe_load(preset_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"mixture preset at {preset_path} must be a YAML mapping")
    return MixturePreset.model_validate(data)


def validate_preset_weights_exact(preset: MixturePreset) -> Fraction:
    """Require the preset weights to sum to exactly one, not approximately one.

    A treatment whose shares do not partition the budget is rejected rather than
    renormalized. Returns the exact sum (always one) for reporting.
    """
    total = preset.weight_sum_exact()
    if total != 1:
        raise PresetValidationError(
            f"preset '{preset.id}' weights sum to exactly {total} ({float(total):.17f}), "
            "not 1. State the intended shares explicitly; nothing is renormalized."
        )
    for component_id, weight in preset.weights.items():
        if weight <= 0:
            raise PresetValidationError(
                f"preset '{preset.id}' gives component '{component_id}' "
                f"non-positive weight {weight!r}."
            )
    return total


def validate_preset_components(preset: MixturePreset, registry: Mix01ViewRegistry) -> None:
    """Require every named component to resolve to a declared mix01 view.

    An unknown component is an error, never a skipped weight or a fallback.
    """
    unknown = [c for c in preset.weights if c not in {v.component_id for v in registry.views}]
    if unknown:
        raise PresetValidationError(
            f"preset '{preset.id}' names undeclared mix01 components: {sorted(unknown)}. "
            "Unknown sources block the mixture."
        )


@dataclass(frozen=True)
class PresetDiff:
    """A structured, testable diff between two mixture treatments."""

    base_id: str
    variant_id: str
    base_identity: str
    variant_identity: str
    added: dict[str, float]
    removed: dict[str, float]
    changed: dict[str, tuple[float, float]]
    unchanged: list[str]

    def weight_delta(self, component_id: str) -> float:
        if component_id in self.changed:
            before, after = self.changed[component_id]
            return after - before
        if component_id in self.added:
            return self.added[component_id]
        if component_id in self.removed:
            return -self.removed[component_id]
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "variant_id": self.variant_id,
            "base_identity": self.base_identity,
            "variant_identity": self.variant_identity,
            "added": self.added,
            "removed": self.removed,
            "changed": {k: list(v) for k, v in sorted(self.changed.items())},
            "unchanged": sorted(self.unchanged),
        }


def diff_presets(base: MixturePreset, variant: MixturePreset) -> PresetDiff:
    """Diff two treatments with all other settings held fixed by construction.

    The diff covers weights only; scheduler, seeds and policies are reported as
    part of each identity, so a silent non-weight change still changes the hash.
    """
    added = {k: v for k, v in variant.weights.items() if k not in base.weights}
    removed = {k: v for k, v in base.weights.items() if k not in variant.weights}
    changed = {
        k: (base.weights[k], variant.weights[k])
        for k in base.weights
        if k in variant.weights and variant.weights[k] != base.weights[k]
    }
    unchanged = [k for k in base.weights if k in variant.weights and k not in changed]
    return PresetDiff(
        base_id=base.id,
        variant_id=variant.id,
        base_identity=base.identity(),
        variant_identity=variant.identity(),
        added=added,
        removed=removed,
        changed=changed,
        unchanged=unchanged,
    )


@dataclass
class ComponentStatus:
    """Readiness of one mixture component, with explicit reasons."""

    component_id: str
    source_id: str
    repository: str
    readiness: ComponentReadiness
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "source_id": self.source_id,
            "repository": self.repository,
            "readiness": self.readiness.value,
            "reasons": self.reasons,
        }


def component_admission_views(view: Mix01ViewSpec) -> list[str]:
    """Store view ids a component needs admitted: its observed configs, else its component id.

    This is the naming every admission path uses (``xlm data probe --view``):
    the upstream config for config-addressed sources (UltraX, FinePDFs, both
    IFM subsets) and the component id for the Essential-Web selector views.
    """
    return list(view.observed_configs) or [view.component_id]


@dataclass(frozen=True)
class LiveVerification:
    """Live-row verification of one component, derived from immutable store evidence.

    ``verified`` is true only when every view the component needs carries real-row
    certified evidence that still binds its exact source, view, revision and
    adapter and that its admission decision binds; ``reason`` says why or why not.
    """

    verified: bool
    reason: str


@dataclass(frozen=True)
class ComponentAdmission:
    """Store admission of one component: every view it needs, each re-evaluated."""

    views: Mapping[str, str]

    @property
    def state(self) -> str:
        states = set(self.views.values())
        if states == {"admitted"}:
            return "admitted"
        for state in ("blocked", "pending_review", "unadmitted"):
            if state in states:
                return state
        return "unadmitted"


def mix01_status(
    registry: Mix01ViewRegistry,
    admission: Mapping[str, str] | None = None,
    availability: Mapping[str, int] | None = None,
    component_admission: Mapping[str, ComponentAdmission] | None = None,
    live_evidence: Mapping[str, LiveVerification] | None = None,
) -> list[ComponentStatus]:
    """Report READY / BLOCKED / NOT LIVE-VERIFIED for every mix01 view.

    ``admission`` maps source_id to one of admitted/pending_review/unadmitted/blocked.
    ``component_admission`` maps component_id to the per-view store admission and,
    where present, takes precedence: admission is recorded per (source, view), so a
    source-level state cannot describe a component such as one Essential-Web view.
    ``availability`` maps component_id to available unique valid targets. Absent maps
    mean "no evidence", which never reads as ready. ``live_evidence`` maps
    component_id to live-row verification derived from certified store evidence;
    besides the registry flag, only a ``verified`` entry satisfies the live check.
    """
    admission = admission or {}
    availability = availability or {}
    component_admission = component_admission or {}
    live_evidence = live_evidence or {}
    report: list[ComponentStatus] = []

    for view in registry.views:
        reasons: list[str] = []

        if is_denied_source(view.repository):
            report.append(
                ComponentStatus(
                    component_id=view.component_id,
                    source_id=view.source_id,
                    repository=view.repository,
                    readiness=ComponentReadiness.BLOCKED,
                    reasons=[
                        f"repository '{view.repository}' is denied by XLM policy; "
                        "no fallback is permitted."
                    ],
                )
            )
            continue

        stored = component_admission.get(view.component_id)
        state = stored.state if stored is not None else admission.get(view.source_id, "unadmitted")
        if stored is not None and state != "admitted":
            detail = ", ".join(f"{name}={value}" for name, value in sorted(stored.views.items()))
            reasons.append(f"store admission of '{view.source_id}' views: {detail}.")
        if state == "blocked":
            readiness = ComponentReadiness.BLOCKED
            reasons.append(f"source '{view.source_id}' is blocked by admission review.")
        elif state != "admitted":
            readiness = ComponentReadiness.NOT_LIVE_VERIFIED
            reasons.append(
                f"source '{view.source_id}' has admission state '{state}'; "
                "operator approval and license/provenance review are missing."
            )
        elif not view.live_verified and not (
            (live := live_evidence.get(view.component_id)) is not None and live.verified
        ):
            readiness = ComponentReadiness.NOT_LIVE_VERIFIED
            reasons.append(
                "no adapter has been tested against live rows at the observed revision; "
                "metadata discovery is not a full adapter test (C04)."
            )
            if live is not None:
                reasons.append(f"certified evidence: {live.reason}")
        else:
            readiness = ComponentReadiness.READY
            reasons.append("admitted and live-verified.")
            if not view.live_verified:
                reasons.append(f"certified evidence: {live_evidence[view.component_id].reason}")

        if view.component_id in availability and availability[view.component_id] <= 0:
            readiness = ComponentReadiness.BLOCKED
            reasons.append(
                f"component '{view.component_id}' resolves but offers zero available "
                "unique targets; an insufficient source blocks the mixture."
            )

        report.append(
            ComponentStatus(
                component_id=view.component_id,
                source_id=view.source_id,
                repository=view.repository,
                readiness=readiness,
                reasons=reasons,
            )
        )
    return report


def gate_preset_for_run(
    preset: MixturePreset,
    registry: Mix01ViewRegistry,
    admission: Mapping[str, str] | None = None,
    availability: Mapping[str, int] | None = None,
    component_admission: Mapping[str, ComponentAdmission] | None = None,
    live_evidence: Mapping[str, LiveVerification] | None = None,
) -> list[ComponentStatus]:
    """Refuse to run a preset unless every named component is READY.

    Unknown, denied, unadmitted, unverified or insufficient components raise
    :class:`Mix01BlockedError`. The no-IFM alternative is never applied here: if
    the IFM view blocks mix01, mix01 fails loudly and the operator must select
    the explicit ``mix01_no_ifm`` preset themselves.
    """
    validate_preset_weights_exact(preset)
    validate_preset_components(preset, registry)
    report = mix01_status(registry, admission, availability, component_admission, live_evidence)

    blocking = [
        s
        for s in report
        if s.component_id in preset.weights and s.readiness is not ComponentReadiness.READY
    ]
    if blocking:
        details = "; ".join(
            f"{s.component_id} [{s.readiness.value}]: {' '.join(s.reasons)}" for s in blocking
        )
        raise Mix01BlockedError(
            f"preset '{preset.id}' is blocked by {len(blocking)} component(s): {details}. "
            "No silent fallback or renormalization is applied."
        )
    return [s for s in report if s.component_id in preset.weights]


def build_source_views(registry: Mix01ViewRegistry) -> list[SourceView]:
    """Build pool selectors for the mix01 components over adapter-recorded fields.

    Every mix01 adapter records ``mix01_component`` in ``source_metadata``; the
    selector matches it exactly, plus the English language gate. A selector that
    matches nothing over the adapted corpus is an empty view and blocks the run.
    """
    return [
        SourceView(
            view_id=view.component_id,
            family_id=view.source_id,
            selector=ViewSelector(
                languages=["en"],
                metadata_equals={"mix01_component": view.component_id},
            ),
            notes=[f"mix01 component '{view.component_id}' rendered by '{view.adapter_id}'."],
        )
        for view in registry.views
    ]


@dataclass(frozen=True)
class PilotAuthorization:
    """Explicit operator authorization envelope for a small real-data pilot.

    A pilot runs only for already approved sources, only when the operator sets
    ``operator_authorized=True``, and only within the aggregate caps. Anything
    else raises :class:`PilotNotAuthorizedError`. There is deliberately no default
    that authorizes: an omitted envelope is a refusal.
    """

    operator_authorized: bool = False
    allowlisted_source_ids: tuple[str, ...] = ()
    max_documents: int = 1000
    max_bytes: int = 10 * 1024 * 1024
    max_requests: int = 20
    authorized_by: str = ""
    ticket: str = ""

    def require(self, source_id: str, planned_documents: int, planned_bytes: int) -> None:
        """Enforce the envelope before any pilot byte is transferred."""
        if not self.operator_authorized:
            raise PilotNotAuthorizedError(
                "pilot refused: no explicit operator authorization envelope. "
                "Set operator_authorized=True with caps and a ticket to proceed."
            )
        if source_id not in self.allowlisted_source_ids:
            raise PilotNotAuthorizedError(
                f"pilot refused: source '{source_id}' is not in the authorized "
                f"allowlist {sorted(self.allowlisted_source_ids)}."
            )
        if planned_documents > self.max_documents:
            raise PilotNotAuthorizedError(
                f"pilot refused: {planned_documents:,} planned documents exceed the "
                f"authorized aggregate cap of {self.max_documents:,}."
            )
        if planned_bytes > self.max_bytes:
            raise PilotNotAuthorizedError(
                f"pilot refused: {planned_bytes:,} planned bytes exceed the authorized "
                f"aggregate cap of {self.max_bytes:,} bytes."
            )

    def describe(self) -> dict[str, Any]:
        return {
            "operator_authorized": self.operator_authorized,
            "allowlisted_source_ids": sorted(self.allowlisted_source_ids),
            "max_documents": self.max_documents,
            "max_bytes": self.max_bytes,
            "max_requests": self.max_requests,
            "authorized_by": self.authorized_by,
            "ticket": self.ticket,
        }
