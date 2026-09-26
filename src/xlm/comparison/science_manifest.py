"""Science-v1 comparison manifest: a small, versioned, hashed preregistration (P35 M4).

The manifest does not store runs or results; it names the question, the track,
the arms and their declared intervention, the paired replicate roster, the
frozen primary endpoint/metric/direction, margins, multiplicity family, sample
size and stopping/failure/promotion rules. It wraps existing frozen execution
and evaluation evidence (``science_evidence``), never a second experiment store.

Every key is required; a missing or unknown field is a validation problem that
makes the comparison INELIGIBLE. Nothing is defaulted. The manifest hash is the
repository's canonical ``identity_digest`` over the whole document, so changing
a margin, family size or roster after seeing results creates a *different*
manifest, never a silent edit of the old one.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.comparison.science_stats import MetricDirection, QuestionType
from xlm.comparison.science_tracks import SCIENCE_TRACKS, SCIENTIFIC_FIELDS, FieldClass

MANIFEST_VERSION = "xlm-science-comparison-v1"
PROMOTION_RULE_VERSION = "xlm-p35-promotion-v1"
STOPPING_RULE = "fixed_n_all_pairs_no_early_success_v1"
FINAL_CHECKPOINT_REQUIREMENT = "exact_budget_endpoint_v1"
FAILURE_POLICY = "failed_or_missing_pair_blocks_confirmation_v1"
FROZEN = "frozen"
DRAFT = "draft_nonexecutable_no_results"
P35_CI_LEVEL = 0.95

#: §H/§R fixed confirmatory sample sizes (fresh paired tuples).
CONFIRMATION_PAIRS: dict[str, int] = {"50m": 5, "150m": 3, "300m": 3}
#: §G full-budget confirmation targets per scale.
CONFIRMATION_BUDGETS: dict[str, int] = {
    "50m": 1_000_000_000,
    "150m": 3_000_000_000,
    "300m": 6_000_000_000,
}
#: §R entry prerequisites for a larger-scale confirmation.
SCALE_PREREQUISITE_STATE: dict[str, str] = {"150m": "PROMOTE_TO_150M", "300m": "PROMOTE_TO_300M"}

SCALE_STAGES = ("50m", "150m", "300m")
STUDY_STAGES = ("screen", "replication", "confirmation")
ENDPOINT_TIERS = ("full_lm", "endpoint_confirmation")
LM_TIERS = ("quick_lm", "full_lm", "endpoint_confirmation")
BENCHMARK_TIERS = ("search_benchmark",)
ORDER_SENTINEL = "shard_native_no_order_manifest"
#: Pre-M5 runs bind no canonical train membership (P35 M5 evidence field).
MEMBERSHIP_SENTINEL = "canonical_membership_not_bound_pre_m5"

#: Frozen held-out LM primary metrics (§I). CE requires a fixed tokenizer; BPB is
#: the designated cross-tokenizer quantity.
LM_PRIMARY_METRICS: dict[str, MetricDirection] = {
    "equal_domain_text_ce_nats_per_token": MetricDirection.LOWER_IS_BETTER,
    "equal_domain_text_bpb": MetricDirection.LOWER_IS_BETTER,
}
TOKENIZER_DEPENDENT_METRICS = frozenset(
    {
        "equal_domain_text_ce_nats_per_token",
        "micro_text_ce_nats_per_token",
        "text_ce_nats_per_token",
        "text_perplexity",
        "perplexity",
    }
)

_LOWER = MetricDirection.LOWER_IS_BETTER
_HIGHER = MetricDirection.HIGHER_IS_BETTER
#: Direction by metric-name suffix/keyword; an unknown metric cannot be verified.
_DIRECTION_RULES: tuple[tuple[re.Pattern[str], MetricDirection], ...] = (
    (re.compile(r"(^|_)ce_nats_per_token(_diagnostic)?$"), _LOWER),
    (re.compile(r"(^|_)bpb$"), _LOWER),
    (re.compile(r"(^|_)perplexity$"), _LOWER),
    (re.compile(r"(^|_)nll(_nats)?$"), _LOWER),
    (re.compile(r"^peak_vram_gib$|^peak_rss_gib$|^wall_seconds$|^total_hours$"), _LOWER),
    (re.compile(r"(^|_)acc(_norm)?$|(^|_)accuracy$|^suite_index$"), _HIGHER),
    (re.compile(r"^successful_targets_per_second$"), _HIGHER),
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_KEYS = (
    "science_comparison_version",
    "comparison_id",
    "title",
    "description",
    "status",
    "preregistered_at",
    "parent_baseline",
    "track",
    "scale_stage",
    "study_stage",
    "question",
    "control_arm",
    "candidate_arms",
    "replicate_roster",
    "intended_differences",
    "required_invariants",
    "training_budget_targets",
    "fixed_values",
    "schedule",
    "primary_endpoint",
    "primary_metric",
    "secondary_metrics",
    "curve_metric",
    "practical_margin",
    "noninferiority_margin",
    "margin_rationale",
    "efficiency",
    "confirmatory_pairs",
    "multiplicity",
    "ci_level",
    "stopping_rule",
    "promotion_rule_version",
    "final_checkpoint_requirement",
    "failure_policy",
    "order_robustness",
    "scale_promotion",
)


def known_direction(metric: str) -> MetricDirection | None:
    """The verified direction of a metric name, or ``None`` when unknown."""
    if metric in LM_PRIMARY_METRICS:
        return LM_PRIMARY_METRICS[metric]
    for pattern, direction in _DIRECTION_RULES:
        if pattern.search(metric):
            return direction
    return None


def replicate_identity(
    init_seed: int, training_seed: int, data_seed: int, order_manifest_id: str
) -> str:
    """The scientific replicate identity. Same tuple = same replicate, whatever the attempt."""
    return identity_digest(
        {
            "init_seed": init_seed,
            "training_seed": training_seed,
            "data_seed": data_seed,
            "order_manifest_id": order_manifest_id,
        }
    )


@dataclass(frozen=True)
class ManifestProblem:
    field: str
    problem: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "problem": self.problem}


@dataclass
class ManifestValidation:
    """Field-level validation of one manifest document."""

    manifest_hash: str | None
    problems: list[ManifestProblem] = field(default_factory=list)
    #: Problems that do not invalidate the manifest but block promotion.
    promotion_blockers: list[ManifestProblem] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems

    def add(self, name: str, problem: str) -> None:
        self.problems.append(ManifestProblem(name, problem))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "manifest_hash": self.manifest_hash,
            "problems": [p.to_dict() for p in self.problems],
            "promotion_blockers": [p.to_dict() for p in self.promotion_blockers],
        }


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    """Canonical identity of a manifest document (``artifacts.manifest.identity_digest``)."""
    return identity_digest(dict(manifest))


def _is_int(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value < 2**63


def _is_positive_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value)) and float(value) > 0.0


def _text(v: ManifestValidation, doc: Mapping[str, Any], key: str) -> str | None:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        v.add(key, "must be a non-empty string")
        return None
    return value


def _metric(
    v: ManifestValidation, name: str, spec: Any, *, primary: bool = False
) -> tuple[str, MetricDirection] | None:
    if not isinstance(spec, Mapping):
        v.add(name, "must be a mapping with 'name' and 'direction'")
        return None
    metric, raw_direction = spec.get("name"), spec.get("direction")
    if not isinstance(metric, str) or not metric:
        v.add(f"{name}.name", "must be a metric name")
        return None
    try:
        direction = MetricDirection(str(raw_direction))
    except ValueError:
        v.add(f"{name}.direction", f"unknown direction {raw_direction!r}")
        return None
    expected = known_direction(metric)
    if expected is None:
        v.add(f"{name}.name", f"metric '{metric}' has no verified direction; refused")
    elif expected is not direction:
        v.add(
            f"{name}.direction",
            f"metric '{metric}' is {expected.value}; declared {direction.value} would invert it",
        )
    if primary and metric not in LM_PRIMARY_METRICS:
        v.add(
            f"{name}.name",
            f"primary metric must be frozen held-out LM quality {sorted(LM_PRIMARY_METRICS)}; "
            f"'{metric}' cannot be primary under {PROMOTION_RULE_VERSION}",
        )
    return metric, direction


def _source(v: ManifestValidation, name: str, spec: Any) -> None:
    if not isinstance(spec, Mapping):
        v.add(name, "must be a mapping {tier, planned_threshold, path}")
        return
    if spec.get("tier") not in (*LM_TIERS, *BENCHMARK_TIERS):
        v.add(f"{name}.tier", f"unknown evaluation tier {spec.get('tier')!r}")
    if not _is_int(spec.get("planned_threshold")):
        v.add(f"{name}.planned_threshold", "must be a non-negative integer")
    path = spec.get("path")
    if not (isinstance(path, list) and path and all(isinstance(p, str) and p for p in path)):
        v.add(f"{name}.path", "must be a non-empty list of metric keys")


def validate_manifest(doc: Any) -> ManifestValidation:
    """Validate a manifest document; every problem is reported at field level."""
    if not isinstance(doc, Mapping):
        result = ManifestValidation(None)
        result.add("<document>", "manifest must be a JSON mapping")
        return result
    try:
        digest: str | None = manifest_hash(doc)
    except (TypeError, ValueError) as exc:
        digest = None
        bad = ManifestValidation(None)
        bad.add("<document>", f"not canonical JSON data: {exc}")
        return bad
    v = ManifestValidation(digest)
    missing = [k for k in REQUIRED_KEYS if k not in doc]
    for key in missing:
        v.add(key, "missing required field (unknown is never defaulted)")
    for key in sorted(set(doc) - set(REQUIRED_KEYS)):
        v.add(key, "unknown field; manifests are strict")
    if missing:
        return v

    if doc["science_comparison_version"] != MANIFEST_VERSION:
        v.add("science_comparison_version", f"must be '{MANIFEST_VERSION}'")
    comparison_id = _text(v, doc, "comparison_id")
    if comparison_id is not None and not _ID.match(comparison_id):
        v.add("comparison_id", "must be a slug of letters, digits, '_', '.', '-'")
    _text(v, doc, "title")
    _text(v, doc, "description")
    _text(v, doc, "preregistered_at")
    if doc["status"] == DRAFT:
        v.add("status", "draft manifest: nonexecutable and carries no results")
    elif doc["status"] != FROZEN:
        v.add("status", f"must be '{FROZEN}' (or '{DRAFT}' for a nonexecutable example)")
    parent = doc["parent_baseline"]
    if not (
        isinstance(parent, Mapping)
        and set(parent) == {"id", "version"}
        and all(isinstance(parent[k], str) and parent[k] for k in ("id", "version"))
    ):
        v.add("parent_baseline", "must be {id, version} strings")

    track = SCIENCE_TRACKS.get(doc["track"]) if isinstance(doc["track"], str) else None
    if track is None:
        v.add("track", f"unknown track {doc['track']!r}; known: {sorted(SCIENCE_TRACKS)}")
    elif not track.certified:
        v.add("track", f"track '{track.track_id}' has no certified eligibility semantics in M4")

    scale = doc["scale_stage"]
    stage = doc["study_stage"]
    if scale not in SCALE_STAGES:
        v.add("scale_stage", f"must be one of {SCALE_STAGES}")
    if stage not in STUDY_STAGES:
        v.add("study_stage", f"must be one of {STUDY_STAGES}")
    try:
        question: QuestionType | None = QuestionType(doc["question"])
    except ValueError:
        question = None
        v.add("question", f"must be one of {[q.value for q in QuestionType]}")

    # -- intended differences / invariants against the track table
    intended = doc["intended_differences"]
    invariants = doc["required_invariants"]
    if not (isinstance(intended, list) and intended and all(isinstance(x, str) for x in intended)):
        v.add("intended_differences", "must be a non-empty list of field names")
        intended = []
    if not (isinstance(invariants, list) and all(isinstance(x, str) for x in invariants)):
        v.add("required_invariants", "must be a list of field names")
        invariants = []
    for name in (*intended, *invariants):
        if name not in SCIENTIFIC_FIELDS:
            v.add("intended_differences/required_invariants", f"unknown scientific field '{name}'")
    if len(set(intended)) != len(intended):
        v.add("intended_differences", "duplicate field")
    if track is not None:
        for name in intended:
            if name in SCIENTIFIC_FIELDS and track.classify(name) is not (
                FieldClass.INTENTIONALLY_VARIED
            ):
                v.add(
                    "intended_differences",
                    f"'{name}' is {track.classify(name).value} on track '{track.track_id}' "
                    "and cannot be declared as an intervention",
                )
        for name in intended:
            causes = track.consequences.get(name)
            if causes and not (causes & set(intended)):
                v.add(
                    "intended_differences",
                    f"'{name}' may only be declared together with one of {sorted(causes)}",
                )
        missing_invariants = [n for n in track.must_match() if n not in invariants]
        if missing_invariants:
            v.add(
                "required_invariants",
                f"omits track MUST_MATCH fields {missing_invariants}",
            )
        overlap = sorted(set(invariants) & set(intended))
        if overlap:
            v.add("required_invariants", f"also declared as intended differences: {overlap}")

    # -- arms
    arms: list[Mapping[str, Any]] = []
    control = doc["control_arm"]
    candidates = doc["candidate_arms"]
    if not isinstance(candidates, list) or not candidates:
        v.add("candidate_arms", "must be a non-empty list")
        candidates = []
    for position, arm in enumerate([control, *candidates]):
        label = "control_arm" if position == 0 else f"candidate_arms[{position - 1}]"
        if not (
            isinstance(arm, Mapping)
            and set(arm) == {"arm_id", "label", "intervention"}
            and isinstance(arm.get("arm_id"), str)
            and _ID.match(arm["arm_id"])
            and isinstance(arm.get("label"), str)
            and isinstance(arm.get("intervention"), Mapping)
        ):
            v.add(label, "must be {arm_id (slug), label, intervention (mapping)}")
            continue
        arms.append(arm)
        keys = set(arm["intervention"])
        primary_interventions = {
            n for n in intended if track is None or n not in track.consequences
        }
        if keys != primary_interventions:
            v.add(
                f"{label}.intervention",
                f"must set exactly the declared primary interventions "
                f"{sorted(primary_interventions)}, got {sorted(keys)}",
            )
    ids = [a["arm_id"] for a in arms]
    if len(set(ids)) != len(ids):
        v.add("candidate_arms", "arm ids must be unique")
    if arms and isinstance(control, Mapping) and control in arms:
        for arm in arms[1:]:
            same = [
                k
                for k in arm["intervention"]
                if k in control["intervention"]
                and control["intervention"][k] == arm["intervention"][k]
            ]
            if same:
                v.add(
                    f"candidate '{arm['arm_id']}'.intervention",
                    f"declared intervention not realized (equal to control): {same}",
                )

    # -- roster and sample size
    roster = doc["replicate_roster"]
    identities: list[str] = []
    if not isinstance(roster, list) or not roster:
        v.add("replicate_roster", "must be a non-empty list of paired tuples")
        roster = []
    tuple_ids: list[str] = []
    for i, entry in enumerate(roster):
        where = f"replicate_roster[{i}]"
        if not (
            isinstance(entry, Mapping)
            and set(entry)
            == {"tuple_id", "role", "init_seed", "training_seed", "data_seed", "order_manifest_id"}
        ):
            v.add(
                where,
                "must be {tuple_id, role, init_seed, training_seed, data_seed, order_manifest_id}",
            )
            continue
        if not (isinstance(entry["tuple_id"], str) and _ID.match(entry["tuple_id"])):
            v.add(f"{where}.tuple_id", "must be a slug")
        else:
            tuple_ids.append(entry["tuple_id"])
        if entry["role"] not in ("exploratory", "confirmation"):
            v.add(f"{where}.role", "must be 'exploratory' or 'confirmation'")
        seeds_ok = all(_is_int(entry[k]) for k in ("init_seed", "training_seed", "data_seed"))
        if not seeds_ok:
            v.add(where, "seeds must be integers in [0, 2**63)")
        order = entry["order_manifest_id"]
        if not (isinstance(order, str) and (order == ORDER_SENTINEL or _HEX64.match(order))):
            v.add(
                f"{where}.order_manifest_id",
                f"must be '{ORDER_SENTINEL}' (no independent order manifest) or a 64-hex "
                "order-manifest identity",
            )
        elif seeds_ok:
            identities.append(
                replicate_identity(
                    entry["init_seed"], entry["training_seed"], entry["data_seed"], order
                )
            )
    if len(set(tuple_ids)) != len(tuple_ids):
        v.add("replicate_roster", "tuple ids must be unique")
    if len(set(identities)) != len(identities):
        v.add(
            "replicate_roster",
            "two roster tuples share one replicate identity; a same-seed rerun is not a "
            "new replicate",
        )

    n_planned = len(roster)
    confirmatory = doc["confirmatory_pairs"]
    if stage == "confirmation" and scale in CONFIRMATION_PAIRS:
        required = CONFIRMATION_PAIRS[scale]
        if confirmatory != required:
            v.add(
                "confirmatory_pairs",
                f"P35 {scale} confirmation requires exactly {required} fresh pairs",
            )
        if n_planned != required:
            v.add("replicate_roster", f"{scale} confirmation roster must list {required} tuples")
        if any(isinstance(e, Mapping) and e.get("role") != "confirmation" for e in roster):
            v.add("replicate_roster", "confirmation requires fresh 'confirmation' tuples only")
        if doc["training_budget_targets"] != CONFIRMATION_BUDGETS.get(scale):
            v.add(
                "training_budget_targets",
                f"{scale} confirmation is full-budget: {CONFIRMATION_BUDGETS.get(scale)}",
            )
    elif stage in ("screen", "replication"):
        if confirmatory is not None:
            v.add("confirmatory_pairs", "must be null outside a confirmation stage")
        if stage == "replication" and n_planned < 2:
            v.add("replicate_roster", "a replication stage needs at least two tuples")
        if any(isinstance(e, Mapping) and e.get("role") != "exploratory" for e in roster):
            v.add("replicate_roster", "screen/replication tuples must be 'exploratory'")

    # -- budget, schedule, endpoint, metrics
    budget = doc["training_budget_targets"]
    if not _is_int(budget, minimum=1):
        v.add("training_budget_targets", "must be a positive integer")
        budget = None
    fixed = doc["fixed_values"]
    if not isinstance(fixed, Mapping):
        v.add("fixed_values", "must be a mapping {invariant field: frozen value}")
    else:
        for name, value in fixed.items():
            if name not in SCIENTIFIC_FIELDS:
                v.add("fixed_values", f"unknown scientific field '{name}'")
            elif name not in invariants:
                v.add("fixed_values", f"'{name}' is not a required invariant of this comparison")
            elif value is None:
                v.add("fixed_values", f"'{name}' is null; a frozen value must be known")
    schedule = doc["schedule"]
    schedule_keys = {"lr_policy", "base_lr", "warmup_targets", "horizon_targets", "min_lr_ratio"}
    if not (isinstance(schedule, Mapping) and set(schedule) == schedule_keys):
        v.add("schedule", f"must be exactly {sorted(schedule_keys)}")
    else:
        if schedule["lr_policy"] != "target_endpoint_before_update_v1":
            v.add("schedule.lr_policy", "science-v1 requires target_endpoint_before_update_v1")
        if not _is_positive_number(schedule["base_lr"]):
            v.add("schedule.base_lr", "must be positive")
        if not (
            _is_int(schedule["warmup_targets"]) and _is_int(schedule["horizon_targets"], minimum=1)
        ):
            v.add("schedule", "warmup/horizon must be integers")
        if not (
            type(schedule["min_lr_ratio"]) in (int, float) and 0 <= schedule["min_lr_ratio"] <= 1
        ):
            v.add("schedule.min_lr_ratio", "must be in [0, 1]")
    endpoint = doc["primary_endpoint"]
    if not (isinstance(endpoint, Mapping) and set(endpoint) == {"tier", "planned_threshold"}):
        v.add("primary_endpoint", "must be {tier, planned_threshold}")
    else:
        if endpoint["tier"] not in ENDPOINT_TIERS:
            v.add("primary_endpoint.tier", f"must be one of {ENDPOINT_TIERS}")
        if stage == "confirmation" and endpoint["tier"] != "endpoint_confirmation":
            v.add(
                "primary_endpoint.tier",
                "confirmation uses the frozen confirmation LM endpoint (§K)",
            )
        if budget is not None and endpoint["planned_threshold"] != budget:
            v.add(
                "primary_endpoint.planned_threshold",
                "the primary endpoint is the exact declared budget, not a best checkpoint",
            )
    _metric(v, "primary_metric", doc["primary_metric"], primary=True)
    secondaries = doc["secondary_metrics"]
    guardrails = 0
    if not isinstance(secondaries, list):
        v.add("secondary_metrics", "must be a list (possibly empty)")
        secondaries = []
    names: list[str] = []
    for i, spec in enumerate(secondaries):
        where = f"secondary_metrics[{i}]"
        if not (
            isinstance(spec, Mapping)
            and set(spec) == {"name", "direction", "role", "source", "regression_margin"}
        ):
            v.add(where, "must be {name, direction, role, source, regression_margin}")
            continue
        _metric(v, where, spec)
        names.append(str(spec["name"]))
        _source(v, f"{where}.source", spec["source"])
        if spec["role"] == "guardrail":
            guardrails += 1
            if not _is_positive_number(spec["regression_margin"]):
                v.add(f"{where}.regression_margin", "a guardrail needs a frozen positive margin")
        elif spec["role"] == "descriptive":
            if spec["regression_margin"] is not None:
                v.add(f"{where}.regression_margin", "descriptive metrics carry no margin")
        else:
            v.add(f"{where}.role", "must be 'guardrail' or 'descriptive'")
    if len(set(names)) != len(names):
        v.add("secondary_metrics", "metric names must be unique")
    curve = doc["curve_metric"]
    if curve is not None:
        keys = {"name", "direction", "tier", "start_target", "end_target"}
        if not (isinstance(curve, Mapping) and set(curve) == keys):
            v.add("curve_metric", f"must be null or exactly {sorted(keys)}")
        else:
            _metric(v, "curve_metric", curve)
            if curve["tier"] not in LM_TIERS:
                v.add("curve_metric.tier", "curve area uses an LM tier")
            if not (_is_int(curve["start_target"]) and _is_int(curve["end_target"], minimum=1)):
                v.add("curve_metric", "start/end targets must be integers")
            elif curve["start_target"] >= curve["end_target"]:
                v.add("curve_metric", "start_target must be below end_target")
            elif budget is not None and curve["end_target"] != budget:
                v.add("curve_metric.end_target", "the curve interval ends at the budget (§K)")

    # -- margins, efficiency, multiplicity
    for key in ("practical_margin", "noninferiority_margin"):
        if doc[key] is not None and not _is_positive_number(doc[key]):
            v.add(key, "must be null or a positive number in primary-metric units")
    if doc["margin_rationale"] is not None and not (
        isinstance(doc["margin_rationale"], str) and doc["margin_rationale"].strip()
    ):
        v.add("margin_rationale", "must be null or a decision-record reference")
    efficiency = doc["efficiency"]
    if efficiency is not None:
        keys = {"name", "direction", "minimum_relative_gain"}
        if not (isinstance(efficiency, Mapping) and set(efficiency) == keys):
            v.add("efficiency", f"must be null or exactly {sorted(keys)}")
        else:
            _metric(v, "efficiency", efficiency)
            if not _is_positive_number(efficiency["minimum_relative_gain"]):
                v.add("efficiency.minimum_relative_gain", "must be a positive frozen threshold")
    if question is QuestionType.SUPERIORITY and doc["practical_margin"] is None:
        v.promotion_blockers.append(
            ManifestProblem("practical_margin", "superiority promotion needs a frozen margin")
        )
    if question is QuestionType.NONINFERIORITY:
        if doc["noninferiority_margin"] is None:
            v.promotion_blockers.append(
                ManifestProblem("noninferiority_margin", "NI promotion needs a frozen NI margin")
            )
        if efficiency is None:
            v.promotion_blockers.append(
                ManifestProblem("efficiency", "an efficiency claim needs a frozen cost threshold")
            )
    if (doc["practical_margin"] is not None or doc["noninferiority_margin"] is not None) and (
        doc["margin_rationale"] is None
    ):
        v.add("margin_rationale", "frozen margins need a decision-record reference")

    multiplicity = doc["multiplicity"]
    keys = {"family_id", "family_size", "guardrail_family_size"}
    if not (isinstance(multiplicity, Mapping) and set(multiplicity) == keys):
        v.add("multiplicity", f"must be exactly {sorted(keys)}")
    else:
        if not (isinstance(multiplicity["family_id"], str) and multiplicity["family_id"].strip()):
            v.add("multiplicity.family_id", "must be a non-empty family id")
        size = multiplicity["family_size"]
        if not _is_int(size, minimum=1):
            v.add("multiplicity.family_size", "must be a positive integer frozen in advance")
        elif size < len(candidates):
            v.add(
                "multiplicity.family_size",
                f"family size {size} is smaller than the {len(candidates)} candidate arms "
                "compared here",
            )
        g_size = multiplicity["guardrail_family_size"]
        if not _is_int(g_size, minimum=0) or g_size < guardrails or (guardrails and g_size < 1):
            v.add(
                "multiplicity.guardrail_family_size",
                f"must be an integer >= the {guardrails} declared guardrails",
            )
    if doc["ci_level"] != P35_CI_LEVEL:
        v.add("ci_level", f"P35 §P uses a two-sided {P35_CI_LEVEL} family level")
    for key, expected in (
        ("stopping_rule", STOPPING_RULE),
        ("promotion_rule_version", PROMOTION_RULE_VERSION),
        ("final_checkpoint_requirement", FINAL_CHECKPOINT_REQUIREMENT),
        ("failure_policy", FAILURE_POLICY),
    ):
        if doc[key] != expected:
            v.add(key, f"must be '{expected}'")

    # -- order robustness (M5 slot) and scale promotion
    order = doc["order_robustness"]
    if not (
        isinstance(order, Mapping)
        and set(order) == {"required", "m5_order_evidence"}
        and isinstance(order["required"], bool)
    ):
        v.add("order_robustness", "must be {required: bool, m5_order_evidence: null|mapping}")
        order = {"required": True, "m5_order_evidence": None}
    promotion = doc["scale_promotion"]
    keys = {"intent", "prerequisite", "resource_plan_ref", "ablation_refs"}
    if not (isinstance(promotion, Mapping) and set(promotion) == keys):
        v.add("scale_promotion", f"must be exactly {sorted(keys)}")
    else:
        if not isinstance(promotion["intent"], bool):
            v.add("scale_promotion.intent", "must be a boolean")
        if promotion["intent"] and not order["required"]:
            v.add(
                "order_robustness.required",
                "a scale promotion requires document-order robustness (§H/§R)",
            )
        refs = promotion["ablation_refs"]
        if not (isinstance(refs, list) and all(isinstance(r, str) and r for r in refs)):
            v.add("scale_promotion.ablation_refs", "must be a list of references")
        prerequisite = promotion["prerequisite"]
        needed = SCALE_PREREQUISITE_STATE.get(str(scale))
        if needed is not None and stage == "confirmation":
            if not (
                isinstance(prerequisite, Mapping)
                and set(prerequisite) == {"manifest_hash", "required_state"}
                and isinstance(prerequisite["manifest_hash"], str)
                and _HEX64.match(prerequisite["manifest_hash"])
                and prerequisite["required_state"] == needed
            ):
                v.add(
                    "scale_promotion.prerequisite",
                    f"a {scale} confirmation must name the prior-scale comparison "
                    f"(manifest_hash) whose state reached {needed}",
                )
        elif prerequisite is not None and not isinstance(prerequisite, Mapping):
            v.add("scale_promotion.prerequisite", "must be null or a mapping")
    return v


def declared_guardrails(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in doc["secondary_metrics"] if s["role"] == "guardrail"]


def arm_ids(doc: Mapping[str, Any]) -> list[str]:
    return [doc["control_arm"]["arm_id"], *(a["arm_id"] for a in doc["candidate_arms"])]


def roster_by_identity(doc: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Roster entries keyed by replicate identity, in roster order."""
    return {
        replicate_identity(
            e["init_seed"], e["training_seed"], e["data_seed"], e["order_manifest_id"]
        ): e
        for e in doc["replicate_roster"]
    }


def roster_order(doc: Mapping[str, Any]) -> Sequence[str]:
    return [e["tuple_id"] for e in doc["replicate_roster"]]
