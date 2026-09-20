"""Mixture recipes and their validation against available token shards.

Contract C07: mixture weights refer to **valid target-token exposures** under the
frozen tokenizer -- not documents and not publisher token estimates. There is no
silent renormalization: a recipe whose weights do not sum to one, or which names a
source with no shard, is rejected rather than quietly adjusted.

A recipe is deliberately separate from the shards it draws on. Changing a mixture
must reuse the same per-source shards, which is what makes a ratio change cheap.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel

MIXTURE_SCHEMA_VERSION = "1"

# Weights must sum to 1 within this tolerance. Tight enough that a real mistake is
# caught, loose enough to tolerate decimal representation of shares like 1/3.
WEIGHT_SUM_TOLERANCE = 1e-6


class MixtureValidationError(ValueError):
    """Raised when a mixture recipe is invalid or inconsistent with its shards."""


class ExhaustionPolicy(StrictConfigModel):
    """What happens when a source runs out of unique tokens.

    C07 forbids silent renormalization, substitution or unreported repetition. Either
    exhaustion is an error, or repetition is explicitly bounded and separately counted.
    """

    repeat: bool = Field(
        default=False,
        description="False means exhaustion is a hard error; True enables bounded repetition.",
    )
    max_epochs: int = Field(
        default=1,
        ge=1,
        description="Maximum passes over a source's tokens when repeat is enabled.",
    )
    max_repeated_targets: int | None = Field(
        default=None,
        ge=0,
        description="Optional hard ceiling on repeated target tokens across all sources.",
    )

    @model_validator(mode="after")
    def validate_repeat(self) -> ExhaustionPolicy:
        if not self.repeat and self.max_epochs != 1:
            raise ValueError(
                "max_epochs is meaningless when repeat is false; exhaustion is an error"
            )
        return self

    def identity(self) -> str:
        return f"repeat={self.repeat}:epochs={self.max_epochs}:maxrep={self.max_repeated_targets}"


class MixtureComponent(StrictConfigModel):
    """One weighted source within a mixture."""

    source_id: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0, description="Share of valid target tokens.")
    shard_id: str | None = Field(
        default=None, description="Optional explicit shard binding for this source."
    )

    def identity(self) -> str:
        return f"{self.source_id}:{self.weight!r}:{self.shard_id}"


class PackingPolicy(StrictConfigModel):
    """How documents are packed into training windows.

    ``causal_stream`` is the C07 baseline: a continuous stream with EOS document
    boundaries and cross-document attention explicitly enabled.
    ``isolated_document`` is a distinct supported policy with segment masks and
    position reset. They are different experiment fields, never interchangeable.
    """

    mode: str = Field(default="causal_stream")
    cross_document_attention: bool = Field(default=True)
    max_document_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Cap on tokens drawn from one document per visit, bounding share drift.",
    )

    @model_validator(mode="after")
    def validate_mode(self) -> PackingPolicy:
        if self.mode not in ("causal_stream", "isolated_document"):
            raise ValueError(
                f"unknown packing mode '{self.mode}'; "
                "supported: 'causal_stream', 'isolated_document'"
            )
        if self.mode == "isolated_document" and self.cross_document_attention:
            raise ValueError(
                "isolated_document packing cannot enable cross-document attention; "
                "that combination is the causal_stream policy under another name"
            )
        if self.mode == "causal_stream" and not self.cross_document_attention:
            raise ValueError(
                "causal_stream requires cross_document_attention; "
                "select isolated_document explicitly"
            )
        return self

    def identity(self) -> str:
        return f"{self.mode}:xdoc={self.cross_document_attention}:cap={self.max_document_tokens}"


class MixtureRecipe(StrictConfigModel):
    """A named mixture over source shards, weighted by valid target tokens."""

    mixture_id: str = Field(min_length=1)
    components: list[MixtureComponent] = Field(min_length=1)
    exhaustion: ExhaustionPolicy = Field(default_factory=ExhaustionPolicy)
    packing: PackingPolicy = Field(default_factory=PackingPolicy)
    data_seed: int = Field(default=20260919, ge=0, description="Seeds source/record order.")
    model_seed: int = Field(
        default=20260920,
        ge=0,
        description=(
            "Seeds model initialization; kept separate so data order and "
            "initialization vary independently."
        ),
    )
    max_share_drift: float = Field(
        default=0.02,
        gt=0.0,
        le=1.0,
        description="Documented bound on |observed share - configured weight| per source.",
    )

    @model_validator(mode="after")
    def validate_weights(self) -> MixtureRecipe:
        seen = [c.source_id for c in self.components]
        duplicates = sorted({s for s in seen if seen.count(s) > 1})
        if duplicates:
            raise ValueError(f"duplicate source_id in mixture components: {duplicates}")

        total = sum(c.weight for c in self.components)
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"mixture weights sum to {total!r}, not 1.0. Weights are not renormalized "
                "silently (C07); state the intended shares explicitly."
            )
        return self

    def weight_of(self, source_id: str) -> float:
        for component in self.components:
            if component.source_id == source_id:
                return component.weight
        raise KeyError(f"source '{source_id}' is not part of mixture '{self.mixture_id}'")

    def identity(self) -> str:
        """Behavioral identity, including the scheduling policy (C07)."""
        payload = (
            f"mixture:v{MIXTURE_SCHEMA_VERSION}:{self.mixture_id}:"
            + "|".join(sorted(c.identity() for c in self.components))
            + f"#exh:{self.exhaustion.identity()}#pack:{self.packing.identity()}"
            + f"#dataseed:{self.data_seed}#drift:{self.max_share_drift}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class SourceAvailability:
    """What one source actually offers, measured from its shard."""

    source_id: str
    shard_id: str
    valid_targets: int
    content_tokens: int
    eos_tokens: int
    canonical_bytes: int
    num_documents: int
    token_dtype: str = "uint16"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MixtureValidation:
    """The outcome of validating a recipe against real shard availability."""

    mixture_id: str
    mixture_identity: str
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    availability: dict[str, SourceAvailability] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mixture_id": self.mixture_id,
            "mixture_identity": self.mixture_identity,
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "availability": {k: v.to_dict() for k, v in sorted(self.availability.items())},
        }

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            raise MixtureValidationError(
                f"mixture '{self.mixture_id}' is invalid: " + "; ".join(self.errors)
            )


def validate_mixture(
    recipe: MixtureRecipe,
    availability: dict[str, SourceAvailability],
) -> MixtureValidation:
    """Validate a recipe against the sources actually available.

    A source named by the recipe but absent, empty, or holding no valid targets is an
    error. Substituting another source or redistributing its weight would be exactly
    the silent renormalization C07 forbids.
    """
    errors: list[str] = []
    warnings: list[str] = []
    used: dict[str, SourceAvailability] = {}

    for component in recipe.components:
        source = availability.get(component.source_id)
        if source is None:
            errors.append(
                f"source '{component.source_id}' has weight {component.weight} but no shard; "
                "its weight is not redistributed"
            )
            continue
        if source.valid_targets <= 0:
            errors.append(
                f"source '{component.source_id}' has a shard but zero valid target tokens; "
                "an empty view cannot carry a mixture weight"
            )
            continue
        used[component.source_id] = source

    unused = sorted(set(availability) - {c.source_id for c in recipe.components})
    if unused:
        warnings.append(
            f"{len(unused)} available source(s) carry no mixture weight and will "
            f"contribute nothing: {unused[:5]}"
        )

    return MixtureValidation(
        mixture_id=recipe.mixture_id,
        mixture_identity=recipe.identity(),
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        availability=used,
    )
