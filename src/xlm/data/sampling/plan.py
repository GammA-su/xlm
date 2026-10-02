"""Deterministic exposure plans over reusable source shards.

Contract C07: compile mixture recipes into deterministic exposure plans, report
available unique exposure and repetition risk, and freeze source and record order
with separate data and model seeds.

A large plan is described by compact block ranges plus a deterministic generator, not
by a materialized list of positions. A trillion-token plan is a few kilobytes of
descriptors here; enumerating it as a Python list would be the exact failure mode the
contract forbids.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.exclusion.gates import PRODUCTION_COMPONENTS, MembershipGate
from xlm.data.exclusion.policy import C05Error
from xlm.data.sampling.mixture import (
    MixtureRecipe,
    MixtureValidation,
    SourceAvailability,
)

EXPOSURE_PLAN_VERSION = "2"
MATCHED_PLAN_VERSION = "1"

# Bytes per stored token id, by dtype, for storage projections.
_DTYPE_BYTES = {"uint16": 2, "uint32": 4}

# A matched plan fixes raw-text exposure on one of two bases so a tokenizer
# comparison changes the token budget rather than the amount of source text seen.
MATCHED_BASES = ("canonical_bytes", "documents")


@dataclass(frozen=True)
class ExposureBlock:
    """A contiguous run of target tokens drawn from one source.

    Blocks are ranges, not enumerations: ``token_start`` and ``token_count`` describe
    a window into the source's shard, so a block covering a billion tokens costs the
    same to store as one covering ten.
    """

    source_id: str
    epoch: int
    token_start: int
    token_count: int

    @property
    def token_end(self) -> int:
        return self.token_start + self.token_count

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceProjection:
    """Per-source accounting for a planned budget."""

    source_id: str
    configured_weight: float
    planned_targets: int
    unique_targets_available: int
    repeated_targets: int
    epochs_required: float
    exhausts_source: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExposurePlan:
    """A frozen, deterministic plan for exposing a token budget over sources."""

    plan_id: str
    mixture_id: str
    mixture_identity: str
    budget_targets: int
    data_seed: int
    model_seed: int
    block_size: int
    projections: dict[str, SourceProjection]
    source_order: list[str]
    total_unique_targets: int
    total_repeated_targets: int
    projected_storage_bytes: int
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    c05_binding: dict[str, str] | None = None

    @property
    def requires_repetition(self) -> bool:
        return self.total_repeated_targets > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            **({"c05_binding": self.c05_binding} if self.c05_binding is not None else {}),
            "plan_id": self.plan_id,
            "mixture_id": self.mixture_id,
            "mixture_identity": self.mixture_identity,
            "budget_targets": self.budget_targets,
            "data_seed": self.data_seed,
            "model_seed": self.model_seed,
            "block_size": self.block_size,
            "projections": {k: v.to_dict() for k, v in sorted(self.projections.items())},
            "source_order": self.source_order,
            "total_unique_targets": self.total_unique_targets,
            "total_repeated_targets": self.total_repeated_targets,
            "requires_repetition": self.requires_repetition,
            "projected_storage_bytes": self.projected_storage_bytes,
            "warnings": self.warnings,
            "notes": self.notes,
        }


def _order_key(seed: int, value: str) -> str:
    """Deterministic, content-derived ordering key."""
    return hashlib.blake2b(f"{seed}:{value}".encode(), digest_size=16).hexdigest()


def _verify_c05(
    recipe: MixtureRecipe,
    validation: MixtureValidation,
    c05_gate: MembershipGate | None,
    c05_shards: Mapping[str, Path] | None,
    rehearsal: bool = False,
) -> dict[str, str] | None:
    final = recipe.mixture_id.casefold().replace("_", "-").startswith("mix-01")
    production = final or any(c.source_id in PRODUCTION_COMPONENTS for c in recipe.components)
    if production or c05_gate is not None:
        if c05_gate is None or c05_shards is None:
            raise C05Error("final baseline mixture requires verified C05 token membership")
        # An authored rehearsal is labeled as such and can never be a Mix-01 plan.
        if rehearsal and (final or c05_gate.mode != "authored"):
            raise C05Error("authored rehearsal cannot compile a Mix-01 or protected plan")
        c05_gate.db.execute("DELETE FROM seen")
        shard_bindings = {}
        for component in recipe.components:
            if component.source_id not in c05_shards:
                raise C05Error("C05 token shard missing for mixture component")
            proof = c05_gate.verify_token_shard(
                c05_shards[component.source_id], reset_seen=False, rehearsal=rehearsal
            )
            shard_bindings[component.source_id] = proof
            available = validation.availability[component.source_id]
            if (
                proof["manifest"]["source_id"] != component.source_id
                or proof["manifest"]["shard_id"] != available.shard_id
                or proof["manifest"]["num_documents"] != available.num_documents
                or proof["counters"]["valid_targets"] != available.valid_targets
            ):
                raise C05Error("mixture availability differs from screened shard")
        from xlm.data.evidence_v2.canonical import digest

        return {
            **({"mode": "authored"} if rehearsal else {}),
            "plan_digest": c05_gate.plan_digest,
            "completion_digest": c05_gate.receipt_digest,
            "shards_digest": digest(shard_bindings),
        }
    return None


def compile_exposure_plan(
    recipe: MixtureRecipe,
    validation: MixtureValidation,
    budget_targets: int,
    block_size: int = 8192,
    *,
    c05_gate: MembershipGate | None = None,
    c05_shards: Mapping[str, Path] | None = None,
    c05_rehearsal: bool = False,
) -> ExposurePlan:
    """Compile a validated recipe into a deterministic exposure plan.

    The plan records *projections*, not positions. Actual block emission is a
    generator (:func:`iter_exposure_blocks`), so plan size is independent of budget.
    """
    validation.raise_if_invalid()
    c05_binding = _verify_c05(recipe, validation, c05_gate, c05_shards, c05_rehearsal)
    if budget_targets <= 0:
        raise ValueError(f"budget_targets must be positive, got {budget_targets}")
    if block_size <= 0:
        raise ValueError(f"block_size must be positive, got {block_size}")

    projections: dict[str, SourceProjection] = {}
    warnings: list[str] = []
    total_unique = 0
    total_repeated = 0
    storage_bytes = 0

    # Exact integer apportionment: independent rounding can lose/add targets.
    # Frozen v1 plans remain historical; newly compiled v2 plans sum to budget.
    from fractions import Fraction

    exact = {c.source_id: Fraction(str(c.weight)) * budget_targets for c in recipe.components}
    allocated = {source: value.numerator // value.denominator for source, value in exact.items()}
    residual = budget_targets - sum(allocated.values())
    if not 0 <= residual <= len(allocated):
        raise ValueError(
            "mixture decimal weights cannot apportion this budget without renormalization"
        )
    ranked = sorted(
        exact, key=lambda s: (-(exact[s] - allocated[s]), _order_key(recipe.data_seed, s), s)
    )
    for source_id in ranked[:residual]:
        allocated[source_id] += 1

    for component in recipe.components:
        source = validation.availability[component.source_id]
        planned = allocated[component.source_id]
        available = source.valid_targets

        unique_used = min(planned, available)
        repeated = max(0, planned - available)
        epochs = planned / available if available else float("inf")

        projections[component.source_id] = SourceProjection(
            source_id=component.source_id,
            configured_weight=component.weight,
            planned_targets=planned,
            unique_targets_available=available,
            repeated_targets=repeated,
            epochs_required=epochs,
            exhausts_source=planned >= available,
        )
        total_unique += unique_used
        total_repeated += repeated
        storage_bytes += source.valid_targets * _DTYPE_BYTES.get(source.token_dtype, 4)

        if repeated > 0 and not recipe.exhaustion.repeat:
            warnings.append(
                f"source '{component.source_id}' would need {repeated:,} repeated target "
                f"tokens ({epochs:.2f} epochs) but repeat is disabled; this plan will "
                "fail on exhaustion rather than silently repeat."
            )
        elif repeated > 0:
            allowed = recipe.exhaustion.max_epochs
            if epochs > allowed:
                warnings.append(
                    f"source '{component.source_id}' needs {epochs:.2f} epochs but the "
                    f"policy allows {allowed}; the run will stop on exhaustion."
                )
            else:
                warnings.append(
                    f"source '{component.source_id}' will repeat {repeated:,} target tokens "
                    f"({epochs:.2f} epochs) under the declared bounded-repeat policy."
                )

    if recipe.exhaustion.max_repeated_targets is not None:
        if total_repeated > recipe.exhaustion.max_repeated_targets:
            warnings.append(
                f"planned repeated exposure {total_repeated:,} exceeds the declared "
                f"ceiling of {recipe.exhaustion.max_repeated_targets:,} repeated targets."
            )

    # Source order is frozen by the data seed, independent of declaration order.
    source_order = sorted(projections, key=lambda s: (_order_key(recipe.data_seed, s), s))

    plan_id = (
        "plan_"
        + hashlib.sha256(
            f"v{EXPOSURE_PLAN_VERSION}:{recipe.identity()}:{budget_targets}:{block_size}".encode()
        ).hexdigest()[:20]
    )

    if c05_binding is not None:
        from xlm.data.evidence_v2.canonical import digest

        if total_repeated:
            raise C05Error("screened final mixture has a component deficit; no repetition")
        plan_id = "plan_" + digest([plan_id, c05_binding])[:20]
    return ExposurePlan(
        c05_binding=c05_binding,
        plan_id=plan_id,
        mixture_id=recipe.mixture_id,
        mixture_identity=recipe.identity(),
        budget_targets=budget_targets,
        data_seed=recipe.data_seed,
        model_seed=recipe.model_seed,
        block_size=block_size,
        projections=projections,
        source_order=source_order,
        total_unique_targets=total_unique,
        total_repeated_targets=total_repeated,
        projected_storage_bytes=storage_bytes,
        warnings=warnings,
        notes=[
            "Runtime enforces exact per-source target quotas and block_size as an upper "
            "bound per visit. Physical context, document caps and boundaries may shorten "
            "a visit; deficits are reconsidered after each visit.",
            "The block generator is an unpacked quota preview, not a packed document trace.",
            "Blocks are emitted by a deterministic generator from compact range "
            "descriptors; no position list is materialized.",
            "Unique-token availability is an exact interval count over shard extents, "
            "not a sketch estimate.",
        ],
    )


def iter_exposure_blocks(
    plan: ExposurePlan,
    availability: dict[str, SourceAvailability],
    limit_blocks: int | None = None,
) -> Iterator[ExposureBlock]:
    """Generate the plan's exposure blocks deterministically.

    This is an unpacked quota preview. Runtime visits may be shorter under physical
    context/document caps; both obey frozen source quotas and seeded deficit order.
    Memory stays constant: one block at a time.
    """
    deficits = {s: 0.0 for s in plan.source_order}
    emitted = {s: 0 for s in plan.source_order}
    cursors = {s: 0 for s in plan.source_order}
    epochs = {s: 0 for s in plan.source_order}

    total_planned = sum(p.planned_targets for p in plan.projections.values())
    if total_planned <= 0:
        return

    produced = 0
    blocks_emitted = 0

    while produced < total_planned:
        if limit_blocks is not None and blocks_emitted >= limit_blocks:
            return

        # Largest deficit wins; ties break on the frozen source order.
        for source_id in plan.source_order:
            projection = plan.projections[source_id]
            share = projection.planned_targets / total_planned
            deficits[source_id] = share * produced - emitted[source_id]

        chosen = max(
            plan.source_order,
            key=lambda s: (deficits[s], -plan.source_order.index(s)),
        )
        projection = plan.projections[chosen]
        remaining_for_source = projection.planned_targets - emitted[chosen]
        if remaining_for_source <= 0:
            # This source is complete; fall back to any source still owing tokens.
            candidates = [
                s for s in plan.source_order if plan.projections[s].planned_targets > emitted[s]
            ]
            if not candidates:
                return
            chosen = candidates[0]
            projection = plan.projections[chosen]
            remaining_for_source = projection.planned_targets - emitted[chosen]

        source = availability[chosen]
        available_in_epoch = max(0, source.valid_targets - cursors[chosen])
        if available_in_epoch <= 0:
            epochs[chosen] += 1
            cursors[chosen] = 0
            available_in_epoch = source.valid_targets
            if available_in_epoch <= 0:
                return

        count = min(plan.block_size, remaining_for_source, available_in_epoch)
        if count <= 0:
            return

        yield ExposureBlock(
            source_id=chosen,
            epoch=epochs[chosen],
            token_start=cursors[chosen],
            token_count=count,
        )
        cursors[chosen] += count
        emitted[chosen] += count
        produced += count
        blocks_emitted += 1


def summarize_plan_blocks(
    plan: ExposurePlan,
    availability: dict[str, SourceAvailability],
    max_blocks: int = 1000,
) -> dict[str, Any]:
    """Summarize a bounded prefix of the plan, for preview output.

    Deliberately bounded: previewing a full production plan block by block would
    defeat the point of compact descriptors.
    """
    counts: dict[str, int] = {s: 0 for s in plan.source_order}
    blocks = 0
    for block in iter_exposure_blocks(plan, availability, limit_blocks=max_blocks):
        counts[block.source_id] += block.token_count
        blocks += 1

    total = sum(counts.values())
    observed = {s: (c / total if total else 0.0) for s, c in counts.items()}
    return {
        "blocks_previewed": blocks,
        "targets_previewed": total,
        "is_truncated_preview": blocks >= max_blocks,
        "observed_shares": observed,
        "configured_weights": {s: plan.projections[s].configured_weight for s in plan.source_order},
        "max_observed_drift": max(
            (abs(observed[s] - plan.projections[s].configured_weight) for s in plan.source_order),
            default=0.0,
        ),
    }


@dataclass(frozen=True)
class MatchedSourceProjection:
    """Per-source matched exposure for a tokenizer/compute comparison.

    ``planned_bytes`` and ``planned_documents`` are the quantities a matched plan
    holds fixed across tokenizers; ``planned_targets`` is the derived token budget and
    therefore changes with tokenizer compression.
    """

    source_id: str
    configured_weight: float
    planned_bytes: int
    planned_documents: int
    planned_targets: int
    available_bytes: int
    available_documents: int
    bytes_per_target: float
    bytes_per_document: float
    epochs_required: float
    requires_repetition: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatchedExposurePlan:
    """A deterministic plan that matches raw-text exposure, not token counts.

    C07 asks for matched-document/byte exposure plans for tokenizer comparisons:
    holding the canonical-byte (or document) budget fixed keeps the raw text, its
    normalization and its source attribution constant, while the tokenizer-derived
    token budget varies. That is what makes a cross-tokenizer comparison fair; a
    token-matched comparison silently exposes different amounts of text.
    """

    plan_id: str
    mixture_id: str
    mixture_identity: str
    basis: str
    budget: int
    data_seed: int
    model_seed: int
    projections: dict[str, MatchedSourceProjection]
    total_planned_bytes: int
    total_planned_documents: int
    total_planned_targets: int
    exact_bytes_available: bool = True
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    c05_binding: dict[str, str] | None = None

    @property
    def token_budget(self) -> int:
        """The derived valid-target budget this matched plan implies."""
        return self.total_planned_targets

    def to_dict(self) -> dict[str, Any]:
        return {
            **({"c05_binding": self.c05_binding} if self.c05_binding is not None else {}),
            "plan_id": self.plan_id,
            "mixture_id": self.mixture_id,
            "mixture_identity": self.mixture_identity,
            "kind": "matched_exposure",
            "basis": self.basis,
            "budget": self.budget,
            "data_seed": self.data_seed,
            "model_seed": self.model_seed,
            "projections": {k: v.to_dict() for k, v in sorted(self.projections.items())},
            "total_planned_bytes": self.total_planned_bytes,
            "total_planned_documents": self.total_planned_documents,
            "total_planned_targets": self.total_planned_targets,
            "token_budget": self.token_budget,
            "exact_bytes_available": self.exact_bytes_available,
            "warnings": self.warnings,
            "notes": self.notes,
        }


def compile_matched_plan(
    recipe: MixtureRecipe,
    validation: MixtureValidation,
    budget: int,
    basis: str = "canonical_bytes",
    *,
    c05_gate: MembershipGate | None = None,
    c05_shards: Mapping[str, Path] | None = None,
) -> MatchedExposurePlan:
    """Compile a matched-canonical-byte or matched-document exposure plan.

    ``basis`` selects what is held equal across a comparison. With
    ``canonical_bytes`` the same UTF-8 byte budget is split by weight and the token
    budget is derived from each source's measured bytes-per-target. With
    ``documents`` the same document count is split and bytes follow from each
    source's measured bytes-per-document.
    """
    validation.raise_if_invalid()
    c05_binding = _verify_c05(recipe, validation, c05_gate, c05_shards)
    if basis not in MATCHED_BASES:
        raise ValueError(f"unknown matched basis '{basis}'; supported: {', '.join(MATCHED_BASES)}")
    if budget <= 0:
        raise ValueError(f"budget must be positive, got {budget}")

    projections: dict[str, MatchedSourceProjection] = {}
    warnings: list[str] = []
    total_bytes = 0
    total_documents = 0
    total_targets = 0

    for component in recipe.components:
        source = validation.availability[component.source_id]
        documents = max(1, source.num_documents)
        bytes_per_target = source.canonical_bytes / source.valid_targets
        bytes_per_document = source.canonical_bytes / documents

        if basis == "canonical_bytes":
            planned_bytes = int(round(budget * component.weight))
            planned_documents = int(round(planned_bytes / bytes_per_document))
            planned_targets = int(round(planned_bytes / bytes_per_target))
        else:
            planned_documents = int(round(budget * component.weight))
            planned_bytes = int(round(planned_documents * bytes_per_document))
            planned_targets = int(round(planned_documents * source.valid_targets / documents))

        epochs = planned_bytes / source.canonical_bytes if source.canonical_bytes else float("inf")
        requires_repetition = planned_bytes > source.canonical_bytes

        projections[component.source_id] = MatchedSourceProjection(
            source_id=component.source_id,
            configured_weight=component.weight,
            planned_bytes=planned_bytes,
            planned_documents=planned_documents,
            planned_targets=planned_targets,
            available_bytes=source.canonical_bytes,
            available_documents=source.num_documents,
            bytes_per_target=bytes_per_target,
            bytes_per_document=bytes_per_document,
            epochs_required=epochs,
            requires_repetition=requires_repetition,
        )
        total_bytes += planned_bytes
        total_documents += planned_documents
        total_targets += planned_targets

        if requires_repetition and not recipe.exhaustion.repeat:
            warnings.append(
                f"source '{component.source_id}' would repeat canonical bytes "
                f"({epochs:.2f} epochs) but repeat is disabled; the matched run will "
                "fail on exhaustion rather than silently change raw-text exposure."
            )
        elif requires_repetition:
            allowed = recipe.exhaustion.max_epochs
            if epochs > allowed:
                warnings.append(
                    f"source '{component.source_id}' needs {epochs:.2f} epochs but the "
                    f"policy allows {allowed}; the matched run will stop on exhaustion."
                )

    plan_id = (
        "matched_"
        + hashlib.sha256(
            f"v{MATCHED_PLAN_VERSION}:{basis}:{recipe.identity()}:{budget}".encode()
        ).hexdigest()[:20]
    )

    if c05_binding is not None:
        from xlm.data.evidence_v2.canonical import digest

        plan_id = "matched_" + digest([plan_id, c05_binding])[:20]
    return MatchedExposurePlan(
        c05_binding=c05_binding,
        plan_id=plan_id,
        mixture_id=recipe.mixture_id,
        mixture_identity=recipe.identity(),
        basis=basis,
        budget=budget,
        data_seed=recipe.data_seed,
        model_seed=recipe.model_seed,
        projections=projections,
        total_planned_bytes=total_bytes,
        total_planned_documents=total_documents,
        total_planned_targets=total_targets,
        exact_bytes_available=True,
        warnings=warnings,
        notes=[
            "Canonical-byte, document-count and token availability are exact counts "
            "read from shard metadata, not sketch estimates.",
            "Derived planned token and byte totals for a matched budget use each "
            "source's measured bytes-per-target and are projections, not measurements "
            "of a completed run.",
            "Holding basis and budget fixed across two tokenizations of the same "
            "canonical pool fixes raw-text exposure; the derived token budget changes "
            "with tokenizer compression and is reported separately.",
        ],
    )
