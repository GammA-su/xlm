"""Tiered evaluation suites, split firewall and the four-task index (C11, A26/A27).

The evaluation policy fixes which official split each feedback tier may touch:

| Task | Search | Confirmation | Final |
|---|---|---|---|
| ARC-Easy | official train | official validation | official test |
| HellaSwag | grouped train | disjoint grouped train | official validation |
| PIQA | grouped train | disjoint grouped train | official validation |
| BLiMP | ~20% of subdatasets | disjoint ~20% | remaining ~60% |

The ordinary developer command can never resolve a final split: ``resolve_suite``
refuses the final tier unless a frozen operator authorization matches the exact
request. Dataset revisions are pinned from ``manifests/eval_dataset_pins.yaml``
(metadata-only resolution) and injected into the harness task config, so a moved
upstream ``main`` cannot silently change a suite.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

SUITE_POLICY_VERSION = "1"

OFFICIAL_DATASET_REPOS = {
    "arc_easy": "allenai/ai2_arc",
    "hellaswag": "Rowan/hellaswag",
    "piqa": "baber/piqa",
    "blimp": "nyu-mll/blimp",
}

# Chance references from the evaluation policy. ARC uses the mean inverse choice
# count per item ("mean_inverse_n_choices"); the others are fixed.
FIXED_CHANCE_REFERENCES = {"blimp": 0.5, "hellaswag": 0.25, "piqa": 0.5}

REQUIRED_TASKS_FOR_INDEX = ("blimp", "arc_easy", "hellaswag", "piqa")


class SuiteTier(StrEnum):
    """Feedback tier of an evaluation run."""

    SEARCH = "search"
    CONFIRMATION = "confirmation"
    FINAL = "final"


class FinalAuthorizationRequiredError(PermissionError):
    """Raised when a final split is requested without operator authorization."""


class SplitFirewallError(RuntimeError):
    """Raised when a developer tier references a final split or vice versa."""


class IncompleteCoverageError(RuntimeError):
    """Raised when an aggregate is requested without complete task coverage."""


@dataclass(frozen=True)
class DatasetPin:
    """An immutable official dataset revision resolved by bounded metadata probing."""

    name: str
    repository: str
    revision: str | None
    declared_license: str | None = None
    is_gated: bool = False
    resolved: bool = False

    def require_resolved(self) -> str:
        if not self.resolved or not self.revision:
            raise ValueError(
                f"dataset '{self.name}' ({self.repository}) has no pinned revision; "
                "resolve manifests/eval_dataset_pins.yaml before running official tasks."
            )
        return self.revision


def load_dataset_pins(path: Path | str) -> dict[str, DatasetPin]:
    """Load the pinned official dataset revisions."""
    pins_path = Path(path)
    if not pins_path.is_file():
        raise FileNotFoundError(f"eval dataset pins not found: {pins_path}")
    raw = yaml.safe_load(pins_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"eval dataset pins at {pins_path} must be a mapping")
    pins: dict[str, DatasetPin] = {}
    for name, payload in raw.items():
        if name.startswith("_") or not isinstance(payload, dict):
            continue
        pins[name] = DatasetPin(
            name=name,
            repository=str(payload.get("repository", "")),
            revision=payload.get("revision"),
            declared_license=payload.get("declared_license"),
            is_gated=bool(payload.get("is_gated", False)),
            resolved=bool(payload.get("resolved", False)),
        )
    return pins


@dataclass(frozen=True)
class TaskVariant:
    """One explicit, pinned task variant for a feedback tier."""

    variant_id: str
    lm_eval_task: str
    tier: SuiteTier
    split: str
    normalized_metric: str
    chance_reference: str
    chance_value: float | None = None
    dataset_revision: str | None = None
    dataset_kwargs: Mapping[str, Any] = field(default_factory=dict)
    blimp_subdatasets: tuple[str, ...] = ()
    group_key: str | None = None
    notes: tuple[str, ...] = ()

    def identity(self) -> str:
        payload = json.dumps(
            {
                "policy": SUITE_POLICY_VERSION,
                "variant_id": self.variant_id,
                "task": self.lm_eval_task,
                "tier": str(self.tier),
                "split": self.split,
                "metric": self.normalized_metric,
                "chance": self.chance_reference,
                "revision": self.dataset_revision,
                "blimp": list(self.blimp_subdatasets),
                "group_key": self.group_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def to_task_spec(self) -> dict[str, Any]:
        """Inline harness config dict with the pinned dataset revision applied."""
        spec: dict[str, Any] = {"task": self.lm_eval_task}
        kwargs: dict[str, Any] = dict(self.dataset_kwargs)
        if self.dataset_revision:
            kwargs["revision"] = self.dataset_revision
        if kwargs:
            spec["dataset_kwargs"] = kwargs
        return spec

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tier"] = str(self.tier)
        payload["identity"] = self.identity()
        return payload


def _variant(
    task: str,
    tier: SuiteTier,
    split: str,
    pins: Mapping[str, DatasetPin],
    *,
    normalized_metric: str,
    chance_reference: str,
    chance_value: float | None = None,
    group_key: str | None = None,
    blimp_subdatasets: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> TaskVariant:
    revision: str | None = None
    pin = pins.get(task)
    if pin is not None and pin.resolved:
        revision = pin.revision
    return TaskVariant(
        variant_id=f"xlm_{task}_{tier.value}",
        lm_eval_task=task,
        tier=tier,
        split=split,
        normalized_metric=normalized_metric,
        chance_reference=chance_reference,
        chance_value=chance_value,
        dataset_revision=revision,
        blimp_subdatasets=tuple(blimp_subdatasets),
        group_key=group_key,
        notes=tuple(notes),
    )


def resolve_suite(
    tier: SuiteTier,
    pins: Mapping[str, DatasetPin],
    *,
    blimp_subdatasets: Sequence[str] | None = None,
    final_authorization: FinalAuthorization | None = None,
) -> list[TaskVariant]:
    """Resolve the explicit task variants for a tier, enforcing the split firewall.

    Search/confirmation never touch a final split. Final raises unless an
    operator authorization matches the request (P21 executes it).
    """
    if tier is SuiteTier.FINAL:
        if final_authorization is None or not final_authorization.operator_authorized:
            raise FinalAuthorizationRequiredError(
                "final tier refused: no operator authorization. The ordinary developer "
                "command must never resolve a final split; request it and let the "
                "operator-side path execute it (P21)."
            )

    blimp_names = list(blimp_subdatasets or ())
    variants: list[TaskVariant] = []

    if tier is SuiteTier.SEARCH:
        variants.append(
            _variant(
                "arc_easy",
                tier,
                "train",
                pins,
                normalized_metric="acc_norm",
                chance_reference="mean_inverse_n_choices",
            )
        )
        variants.append(
            _variant(
                "hellaswag",
                tier,
                "train",
                pins,
                normalized_metric="acc_norm",
                chance_reference="fixed",
                chance_value=0.25,
                group_key="ctx",
            )
        )
        variants.append(
            _variant(
                "piqa",
                tier,
                "train",
                pins,
                normalized_metric="acc",
                chance_reference="fixed",
                chance_value=0.5,
                group_key="goal",
            )
        )
    elif tier is SuiteTier.CONFIRMATION:
        variants.append(
            _variant(
                "arc_easy",
                tier,
                "validation",
                pins,
                normalized_metric="acc_norm",
                chance_reference="mean_inverse_n_choices",
            )
        )
        variants.append(
            _variant(
                "hellaswag",
                tier,
                "train",
                pins,
                normalized_metric="acc_norm",
                chance_reference="fixed",
                chance_value=0.25,
                group_key="ctx",
                notes=("disjoint grouped subdivision of train",),
            )
        )
        variants.append(
            _variant(
                "piqa",
                tier,
                "train",
                pins,
                normalized_metric="acc",
                chance_reference="fixed",
                chance_value=0.5,
                group_key="goal",
                notes=("disjoint grouped subdivision of train",),
            )
        )
    else:  # FINAL
        variants.append(
            _variant(
                "arc_easy",
                tier,
                "test",
                pins,
                normalized_metric="acc_norm",
                chance_reference="mean_inverse_n_choices",
            )
        )
        variants.append(
            _variant(
                "hellaswag",
                tier,
                "validation",
                pins,
                normalized_metric="acc_norm",
                chance_reference="fixed",
                chance_value=0.25,
            )
        )
        variants.append(
            _variant(
                "piqa",
                tier,
                "validation",
                pins,
                normalized_metric="acc",
                chance_reference="fixed",
                chance_value=0.5,
            )
        )

    if blimp_names:
        partition = partition_blimp_subdatasets(blimp_names)
        selected = partition[tier]
        variants.append(
            _variant(
                "blimp",
                tier,
                "train",
                pins,
                normalized_metric="acc",
                chance_reference="fixed",
                chance_value=0.5,
                blimp_subdatasets=selected,
                notes=(f"{len(selected)} of {len(blimp_names)} whole BLiMP subdatasets",),
            )
        )

    assert_no_final_split_leak(variants, tier)
    return variants


def assert_no_final_split_leak(variants: Sequence[TaskVariant], tier: SuiteTier) -> None:
    """A developer tier must not reference a task whose policy split is final.

    The final split names per task are fixed by the evaluation policy. HellaSwag
    and PIQA use official *validation* for final, not test, but that split is
    still forbidden to search/confirmation.
    """
    final_splits = {
        "arc_easy": "test",
        "hellaswag": "validation",
        "piqa": "validation",
    }
    if tier is SuiteTier.FINAL:
        return
    for variant in variants:
        forbidden = final_splits.get(variant.lm_eval_task)
        if forbidden is not None and variant.split == forbidden:
            raise SplitFirewallError(
                f"variant '{variant.variant_id}' ({tier}) references final split "
                f"'{variant.split}' for task '{variant.lm_eval_task}'."
            )


def partition_blimp_subdatasets(
    names: Sequence[str],
    seed: int = 20260918,
    search_fraction: float = 0.2,
    confirmation_fraction: float = 0.2,
) -> dict[SuiteTier, list[str]]:
    """Partition whole BLiMP subdatasets across tiers, deterministically.

    Subdatasets are indivisible: a subdataset never appears in more than one set,
    and search/confirmation are disjoint by construction. Sizes are "about"
    20/20/60; with tiny inputs each non-final tier keeps at least one subdataset
    when arithmetically possible.
    """
    if not names:
        raise ValueError("BLiMP partition requires at least one subdataset name")
    if len({*names}) != len(names):
        raise ValueError("BLiMP subdataset names must be unique")
    if not (0.0 < search_fraction < 1.0 and 0.0 < confirmation_fraction < 1.0):
        raise ValueError("partition fractions must be in (0, 1)")
    if search_fraction + confirmation_fraction >= 1.0:
        raise ValueError("search + confirmation fractions must leave a final remainder")

    ordered = sorted(names, key=lambda n: (hashlib.sha256(f"{seed}:{n}".encode()).hexdigest(), n))
    total = len(ordered)
    n_search = max(1, int(total * search_fraction))
    n_confirmation = max(1, int(total * confirmation_fraction))
    if n_search + n_confirmation >= total:
        n_search = 1
        n_confirmation = 1

    return {
        SuiteTier.SEARCH: ordered[:n_search],
        SuiteTier.CONFIRMATION: ordered[n_search : n_search + n_confirmation],
        SuiteTier.FINAL: ordered[n_search + n_confirmation :],
    }


def assert_blimp_tier_membership(
    subdatasets: Sequence[str], universe: Sequence[str], tier: SuiteTier
) -> None:
    """Every declared BLiMP subdataset must belong to this tier's partition.

    BLiMP uses the ``train`` split at every tier, so the split firewall alone
    cannot keep a developer run away from final subdatasets. The partition is
    computed over the declared full subdataset universe, the same way
    :func:`resolve_suite` assigns tiers, and a subdataset outside the requested
    tier's share is refused.
    """
    if tier is SuiteTier.FINAL:
        raise SplitFirewallError("developer BLiMP membership checks never admit the final tier")
    if not universe:
        raise SplitFirewallError(
            "BLiMP selections require the declared full subdataset universe; without it the "
            "tier partition, and therefore the final-split firewall, cannot be established"
        )
    unknown = sorted(set(subdatasets) - set(universe))
    if unknown:
        raise SplitFirewallError(f"BLiMP subdatasets {unknown} are not in the declared universe")
    allowed = set(partition_blimp_subdatasets(universe)[tier])
    outside = sorted(set(subdatasets) - allowed)
    if outside:
        raise SplitFirewallError(
            f"BLiMP subdatasets {outside} belong to another tier's partition, not '{tier}'"
        )


def partition_grouped_items(
    items: Sequence[Mapping[str, Any]],
    tier: SuiteTier,
    group_key: str,
    seed: int = 20260918,
) -> list[Mapping[str, Any]]:
    """Split grouped train items into disjoint search/confirmation subdivisions.

    All items sharing a group key stay together; the assignment of whole groups
    is a deterministic function of the seed, so a task's final validation split
    is never touched and the two developer subdivisions never overlap.
    """
    if tier is SuiteTier.FINAL:
        raise SplitFirewallError(
            "partition_grouped_items is for developer subdivisions only; final uses "
            "the official validation split."
        )

    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        value = item.get(group_key)
        if value is None:
            raise KeyError(f"item is missing required group key '{group_key}'")
        groups.setdefault(str(value), []).append(item)

    ordered = sorted(
        groups.items(),
        key=lambda kv: (hashlib.sha256(f"{seed}:{kv[0]}".encode()).hexdigest(), kv[0]),
    )
    midpoint = max(1, len(ordered) // 2)
    selected = ordered[:midpoint] if tier is SuiteTier.SEARCH else ordered[midpoint:]

    result: list[Mapping[str, Any]] = []
    for _, group_items in selected:
        result.extend(group_items)
    return result


# ------------------------------------------------------------------ four-task index


@dataclass(frozen=True)
class TaskScore:
    """One task's score with its chance reference and coverage accounting."""

    task: str
    metric_name: str
    value: float
    chance: float
    scored_items: int
    total_items: int
    omissions: int = 0
    errors: int = 0
    truncated_items: int = 0
    subdataset_scores: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["subdataset_scores"] = dict(sorted(self.subdataset_scores.items()))
        return payload


@dataclass(frozen=True)
class SuiteIndex:
    """The four-task index, present only with complete declared coverage."""

    complete: bool
    index: float | None
    components: dict[str, float]
    missing: list[str]
    chance_references: dict[str, float]
    scope_label: str = "undeclared"
    scope_kind: str = "undeclared"
    withheld_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_four_task_index(
    scores: Mapping[str, TaskScore],
    required: Sequence[str] = REQUIRED_TASKS_FOR_INDEX,
    coverage: Any = None,
) -> SuiteIndex:
    """Compute S = 100 * mean((acc - chance) / (1 - chance)) over four tasks.

    BLiMP contributes one macro-averaged component over its whole subdatasets;
    a missing task yields ``index=None`` with the missing list, never a partial
    score presented as the suite index. Negative components are not clipped.

    When a :class:`~xlm.evaluation.coverage.SuiteCoverage` is supplied, the index
    is additionally withheld unless the declared scope was covered exactly. A
    task can be *present* yet incomplete, and a present-but-incomplete suite is
    not a suite score. The formula, the chance references, the denominators and
    the four-task weighting are untouched by this gate: coverage decides only
    whether the computed index may be published.
    """
    scope_label = str(getattr(coverage, "scope_label", "undeclared"))
    scope_kind = str(getattr(coverage, "scope_kind", "undeclared"))
    if coverage is not None and not coverage.complete:
        return SuiteIndex(
            complete=False,
            index=None,
            components={},
            missing=sorted({task for task in required if task not in scores}),
            chance_references={},
            scope_label=scope_label,
            scope_kind=scope_kind,
            withheld_reasons=list(coverage.eligibility_reasons()),
        )

    missing = sorted({task for task in required if task not in scores})
    if missing:
        return SuiteIndex(
            complete=False,
            index=None,
            components={},
            missing=missing,
            chance_references={},
            scope_label=scope_label,
            scope_kind=scope_kind,
            withheld_reasons=[f"required task(s) absent: {missing}"],
        )

    components: dict[str, float] = {}
    chances: dict[str, float] = {}
    for task in required:
        score = scores[task]
        if score.scored_items <= 0:
            return SuiteIndex(
                complete=False,
                index=None,
                components={},
                missing=[f"{task}:no_scored_items"],
                chance_references={},
                scope_label=scope_label,
                scope_kind=scope_kind,
                withheld_reasons=[f"task '{task}' scored no items"],
            )
        if score.subdataset_scores:
            # Macro-average BLiMP subdatasets before giving BLiMP one quarter weight.
            value = sum(score.subdataset_scores.values()) / len(score.subdataset_scores)
            chance = score.chance
        else:
            value = score.value
            chance = score.chance
        if not (0.0 <= chance < 1.0):
            raise ValueError(f"invalid chance reference {chance} for task '{task}'")
        chances[task] = chance
        components[task] = (value - chance) / (1.0 - chance)

    index = 100.0 * sum(components.values()) / len(required)
    return SuiteIndex(
        complete=True,
        index=index,
        components={k: components[k] for k in required},
        missing=[],
        chance_references=chances,
        scope_label=scope_label,
        scope_kind=scope_kind,
        withheld_reasons=[],
    )


@dataclass(frozen=True)
class FinalAuthorization:
    """Operator authorization matching one frozen final-evaluation request."""

    operator_authorized: bool
    request_hash: str
    authorized_by: str = ""
    ticket: str = ""

    def matches(self, request_hash: str) -> bool:
        return self.operator_authorized and self.request_hash == request_hash


def build_final_request(
    checkpoint_hash: str,
    tokenizer_hash: str,
    pins: Mapping[str, DatasetPin],
    *,
    blimp_subdatasets: Sequence[str] | None = None,
    limit: int | None = None,
    precision: str = "fp32",
) -> dict[str, Any]:
    """Freeze an operator-only final-evaluation request (execution is P21's)."""
    variants = resolve_suite(
        SuiteTier.FINAL,
        pins,
        blimp_subdatasets=blimp_subdatasets,
        final_authorization=FinalAuthorization(True, "request-generation", "xlm", "request"),
    )
    payload = {
        "kind": "final_evaluation_request",
        "policy_version": SUITE_POLICY_VERSION,
        "checkpoint_hash": checkpoint_hash,
        "tokenizer_hash": tokenizer_hash,
        "precision": precision,
        "limit": limit,
        "variants": [v.to_dict() for v in variants],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["request_hash"] = hashlib.sha256(encoded).hexdigest()
    return payload
