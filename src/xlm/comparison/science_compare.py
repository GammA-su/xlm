"""Science-v1 comparison: eligibility, pairing, statistics and decisions (P35 M4).

``compare_science`` consumes a validated comparison manifest and run evidence
records (``science_evidence``) and returns one immutable, hashed comparison
record. It computes; it never trains, schedules, authorizes or edits evidence.

Order of refusal, each visible in the record:

1. manifest problems (field level) -> INELIGIBLE;
2. per run: failed/in-doubt attempts, unverifiable evidence, unknown arm,
   replicate tuple not in the frozen roster, values contradicting the manifest
   (budget, schedule, LR policy, declared intervention) -> excluded or
   INELIGIBLE, never silently dropped;
3. per (arm, replicate): more than one at-budget attempt is a duplicate, not a
   replicate -> INELIGIBLE;
4. per pair: three-way field diff (MUST_MATCH / INTENTIONALLY_VARIED /
   RECORDED_MAY_DIFFER); any undeclared or unknown material difference ->
   INELIGIBLE with the exact field;
5. missing or incomplete pairs -> INCOMPLETE (a partial view is PROVISIONAL);
6. only then paired seed statistics, guardrails, curve area and the decision.

Pairs are formed by explicit replicate identity (init, training and data seed
plus order manifest), never by list position, time, checkpoint or name.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xlm.artifacts.manifest import identity_digest
from xlm.comparison.curves import (
    LINEAR_TARGET_AREA_VERSION,
    CurveError,
    fixed_linear_target_area,
)
from xlm.comparison.science_evidence import (
    EVIDENCE_VERSION,
    EvidenceError,
    evidence_fields,
    verify_evidence_record,
)
from xlm.comparison.science_manifest import (
    MANIFEST_VERSION,
    PROMOTION_RULE_VERSION,
    TOKENIZER_DEPENDENT_METRICS,
    ManifestValidation,
    declared_guardrails,
    replicate_identity,
    roster_by_identity,
    validate_manifest,
)
from xlm.comparison.science_promotion import (
    CandidateEvidence,
    GuardrailOutcome,
    PromotionState,
    evaluate_promotion_v1,
)
from xlm.comparison.science_stats import (
    DECISION_RULE_VERSION,
    STATISTICS_VERSION,
    UNCERTAINTY_SOURCE,
    Decision,
    DecisionResult,
    MetricDirection,
    PairedSeedStatistics,
    PairValue,
    QuestionType,
    StatisticsError,
    classify_effect,
    paired_seed_statistics,
    strictly_greater,
    strictly_less,
)
from xlm.comparison.science_tracks import (
    SCIENTIFIC_FIELDS,
    TRACK_TABLE_VERSION,
    FieldClass,
    ScienceTrack,
    get_track,
)

COMPARISON_RECORD_VERSION = "xlm-science-comparison-record-v1"
RUN_ROSTER_VERSION = "xlm-science-comparison-runs-v1"
REPLICATE_SELECTION_RULE = "single_at_budget_attempt_per_replicate_v1"
RUN_STATUSES = ("completed", "failed", "in_doubt", "cancelled", "incomplete")


class ComparisonError(ValueError):
    """Raised when comparison inputs are structurally unusable."""


@dataclass(frozen=True)
class RunEntry:
    """One attempt listed for a comparison: its evidence, or why it has none."""

    label: str
    arm_id: str
    status: str
    failure: str | None
    evidence: Mapping[str, Any] | None
    measurements: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    synthetic: bool = False


def _short(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {"digest": identity_digest(value)}


def _metric_at(metrics: Any, path: Sequence[str]) -> float | None:
    node = metrics
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    if type(node) in (int, float) and math.isfinite(node):
        return float(node)
    return None


def _model_label(fields: Mapping[str, Any]) -> str | None:
    component = fields.get("model_component") or {}
    arch = fields.get("model_architecture") or {}
    if not isinstance(component, Mapping) or not isinstance(arch, Mapping):
        return None
    shape = " ".join(
        f"{tag}{arch[key]}"
        for tag, key in (("L", "num_layers"), ("d", "hidden_size"), ("h", "num_attention_heads"))
        if key in arch
    )
    return f"{component.get('key', 'unknown')} {shape}".strip()


def _event(evidence: Mapping[str, Any], tier: str, threshold: int) -> Mapping[str, Any] | None:
    for event in evidence.get("evaluations", {}).values():
        if event.get("tier") == tier and event.get("planned_threshold") == threshold:
            return event  # type: ignore[no-any-return]
    return None


# ------------------------------------------------------------------ run analysis


def _manifest_checks(
    manifest: Mapping[str, Any], evidence: Mapping[str, Any], arm: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Evidence values that contradict values frozen in the manifest."""
    fields = evidence_fields(evidence)
    schedule = manifest["schedule"]
    schedule_config = (fields.get("schedule") or {}).get("config") or {}
    optimizer_config = (fields.get("optimizer") or {}).get("config") or {}
    expected: list[tuple[str, Any, Any]] = [
        ("budget_targets", manifest["training_budget_targets"], fields.get("budget_targets")),
        ("lr_policy", schedule["lr_policy"], fields.get("lr_policy")),
        ("optimizer.lr", schedule["base_lr"], optimizer_config.get("lr")),
        (
            "schedule.warmup_valid_targets",
            schedule["warmup_targets"],
            schedule_config.get("warmup_valid_targets"),
        ),
        (
            "schedule.horizon_valid_targets",
            schedule["horizon_targets"],
            schedule_config.get("horizon_valid_targets"),
        ),
        ("schedule.min_lr_ratio", schedule["min_lr_ratio"], schedule_config.get("min_lr_ratio")),
    ]
    for name, value in manifest["fixed_values"].items():
        expected.append((name, value, fields.get(name)))
    for name, value in arm["intervention"].items():
        expected.append((name, value, fields.get(name)))
    diffs: list[dict[str, Any]] = []
    for name, want, got in expected:
        if want != got:
            diffs.append(
                {
                    "field": name,
                    "expected": _short(want),
                    "observed": _short(got),
                    "classification": "MANIFEST_FROZEN",
                    "reason": "evidence contradicts the value frozen in the comparison manifest"
                    if got is not None
                    else "unknown in evidence; never assumed equal",
                }
            )
    return diffs


def _analyze_run(
    entry: RunEntry,
    manifest: Mapping[str, Any],
    arms: Mapping[str, Mapping[str, Any]],
    roster: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "label": entry.label,
        "arm_id": entry.arm_id,
        "declared_status": entry.status,
        "failure": entry.failure,
        "synthetic": entry.synthetic,
        "state": "complete",
        "reasons": [],
        "manifest_diff": [],
        "tuple_id": None,
        "replicate_identity": None,
        "run_id": None,
        "execution_hash": None,
        "checkpoint_id": None,
        "evidence_digest": None,
        "primary": None,
        "secondary": {},
        "curve_points": None,
        "curve_reason": None,
        "measurements": {k: dict(v) for k, v in sorted(entry.measurements.items())},
        "parameter_count": None,
        "committed_targets": None,
        "model": None,
        "at_budget": False,
    }
    if entry.status not in RUN_STATUSES:
        raise ComparisonError(f"run '{entry.label}' has unknown status '{entry.status}'")
    if entry.arm_id not in arms:
        row["state"] = "invalid"
        row["reasons"].append(f"arm '{entry.arm_id}' is not declared in the manifest")
    evidence = entry.evidence
    if evidence is not None:
        try:
            verify_evidence_record(evidence)
        except EvidenceError as exc:
            row["state"] = "invalid"
            row["reasons"].append(f"evidence does not verify: {exc}")
            return row
        fields = evidence_fields(evidence)
        row.update(
            run_id=fields.get("run_id"),
            execution_hash=fields.get("execution_hash"),
            checkpoint_id=fields.get("checkpoint_id"),
            evidence_digest=evidence["evidence_digest"],
            parameter_count=fields.get("model_parameter_count"),
            committed_targets=fields.get("committed_targets"),
            model=_model_label(fields),
        )
    if entry.status != "completed" or evidence is None:
        row["state"] = "failed"
        row["reasons"].append(
            f"attempt {entry.status}: {entry.failure or 'no evidence'}; kept visible, never counted"
        )
        return row
    if row["state"] == "invalid":
        return row
    replicate = evidence["replicate"]
    seeds = [replicate.get(k) for k in ("init_seed", "training_seed", "data_seed")]
    order = replicate.get("order_manifest_id")
    if not all(type(s) is int for s in seeds) or not isinstance(order, str):
        row["state"] = "invalid"
        row["reasons"].append("replicate seed tuple unknown in evidence")
        return row
    identity = replicate_identity(seeds[0], seeds[1], seeds[2], order)
    row["replicate_identity"] = identity
    tuple_entry = roster.get(identity)
    if tuple_entry is None:
        row["state"] = "invalid"
        row["reasons"].append(
            f"replicate tuple init={seeds[0]} training={seeds[1]} data={seeds[2]} "
            f"order={order} is not in the frozen roster; unregistered seeds are never added"
        )
        return row
    row["tuple_id"] = tuple_entry["tuple_id"]
    row["manifest_diff"] = _manifest_checks(manifest, evidence, arms[entry.arm_id])
    if row["manifest_diff"]:
        row["state"] = "invalid"
        row["reasons"].append(
            "evidence contradicts the manifest: "
            + ", ".join(d["field"] for d in row["manifest_diff"])
        )
    endpoint = evidence["endpoint"]
    row["at_budget"] = bool(endpoint.get("at_budget"))
    incomplete: list[str] = []
    if not endpoint.get("at_budget"):
        incomplete.append(
            f"committed {endpoint.get('committed_targets')} of budget "
            f"{endpoint.get('budget_targets')} targets"
        )
    spec = manifest["primary_endpoint"]
    event = _event(evidence, spec["tier"], spec["planned_threshold"])
    metric = manifest["primary_metric"]["name"]
    if event is None:
        incomplete.append(
            f"primary endpoint {spec['tier']}@{spec['planned_threshold']} not planned"
        )
    elif not event.get("complete"):
        incomplete.append(
            f"primary endpoint {spec['tier']}@{spec['planned_threshold']} not COMPLETE: "
            + "; ".join(event.get("reasons", []))
        )
    else:
        row["primary"] = _metric_at(event.get("metrics"), [metric])
        if row["primary"] is None:
            incomplete.append(f"primary metric '{metric}' missing or non-finite")
    for secondary in manifest["secondary_metrics"]:
        source = secondary["source"]
        found = _event(evidence, source["tier"], source["planned_threshold"])
        value = (
            _metric_at(found.get("metrics"), source["path"])
            if found is not None and found.get("complete")
            else None
        )
        row["secondary"][secondary["name"]] = value
        if value is None and secondary["role"] == "guardrail":
            incomplete.append(f"guardrail '{secondary['name']}' evidence missing or incomplete")
    curve = manifest["curve_metric"]
    if curve is not None:
        points: list[list[Any]] = []
        missing: list[str] = []
        for ev_id, ev in sorted(evidence.get("evaluations", {}).items()):
            threshold = ev.get("planned_threshold")
            if ev.get("tier") != curve["tier"] or not (
                curve["start_target"] <= threshold <= curve["end_target"]
            ):
                continue
            value = _metric_at(ev.get("metrics"), [curve["name"]]) if ev.get("complete") else None
            if value is None:
                missing.append(ev_id)
            else:
                points.append([threshold, ev.get("actual_committed_targets"), value])
        if missing:
            row["curve_reason"] = f"curve points not complete: {missing}"
        else:
            row["curve_points"] = sorted(points)
    if incomplete and row["state"] == "complete":
        row["state"] = "incomplete"
    row["reasons"].extend(incomplete)
    return row


# ---------------------------------------------------------------- field diffs


def field_diff(
    track: ScienceTrack,
    intended: Sequence[str],
    invariants: Sequence[str],
    control: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Three-way classified diff of two runs' scientific fields."""
    rows: list[dict[str, Any]] = []
    for name in SCIENTIFIC_FIELDS:
        cls = track.classify(name)
        if name in invariants and cls is FieldClass.RECORDED_MAY_DIFFER:
            cls = FieldClass.MUST_MATCH  # the manifest promoted it to an invariant
        left, right = control.get(name), candidate.get(name)
        same = left == right and left is not None
        row: dict[str, Any] = {"field": name, "classification": cls.value}
        if cls is FieldClass.RECORDED_MAY_DIFFER:
            row.update(
                status="match" if same else "recorded_difference",
                control=_short(left),
                candidate=_short(right),
                reason=None,
            )
        elif left is None or right is None:
            row.update(
                status="UNKNOWN",
                control=_short(left),
                candidate=_short(right),
                reason="unknown value: null means unknown, never equal (fails closed)",
            )
        elif same:
            row.update(status="match", control=_short(left), candidate=_short(right), reason=None)
        elif cls is FieldClass.MUST_MATCH:
            row.update(
                status="VIOLATION",
                control=left,
                candidate=right,
                reason=f"MUST_MATCH field differs on track '{track.track_id}'",
            )
        elif name not in intended:
            row.update(
                status="VIOLATION",
                control=left,
                candidate=right,
                reason="difference allowed on this track only when declared; it is undeclared",
            )
        else:
            causes = track.consequences.get(name)
            if causes and all(control.get(c) == candidate.get(c) for c in causes):
                row.update(
                    status="VIOLATION",
                    control=left,
                    candidate=right,
                    reason=f"declared consequence differs although its causes {sorted(causes)} "
                    "are identical",
                )
            else:
                row.update(status="declared_difference", control=left, candidate=right, reason=None)
        rows.append(row)
    return rows


# ------------------------------------------------------------------- statistics


def _stats(
    pairs: list[PairValue], metric: str, direction: MetricDirection, level: float, size: int
) -> PairedSeedStatistics | None:
    if not pairs:
        return None
    return paired_seed_statistics(
        pairs, metric=metric, direction=direction, family_ci_level=level, family_size=size
    )


def _guardrail(stats: PairedSeedStatistics | None, name: str, margin: float) -> GuardrailOutcome:
    if stats is None:
        return GuardrailOutcome(name, "incomplete", "no complete guardrail pairs")
    if stats.ci_improvement is None:
        return GuardrailOutcome(name, "not_shown", "n < 2: no seed interval")
    low, high = stats.ci_improvement
    if strictly_greater(low, -margin):
        return GuardrailOutcome(name, "pass", f"lower bound {low:.6g} > -{margin:.6g}")
    if strictly_less(high, -margin):
        return GuardrailOutcome(name, "fail", f"upper bound {high:.6g} < -{margin:.6g}")
    return GuardrailOutcome(
        name, "not_shown", f"interval [{low:.6g}, {high:.6g}] crosses -{margin:.6g}"
    )


def _relative_gains(
    pairs: Sequence[tuple[str, Mapping[str, Any], Mapping[str, Any]]],
    name: str,
    direction: MetricDirection,
) -> tuple[list[PairValue], list[str]]:
    values: list[PairValue] = []
    problems: list[str] = []
    for tuple_id, control, candidate in pairs:
        a = (control["measurements"].get(name) or {}).get("value")
        b = (candidate["measurements"].get(name) or {}).get("value")
        if not (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and not isinstance(a, bool)
            and not isinstance(b, bool)
            and math.isfinite(a)
            and math.isfinite(b)
            and a > 0
            and b > 0
        ):
            problems.append(f"{tuple_id}: '{name}' not measured on both arms")
            continue
        gain = (b - a) / a if direction is MetricDirection.HIGHER_IS_BETTER else (a - b) / a
        values.append(PairValue(tuple_id, 0.0, gain))
    return values, problems


def _candidate(
    manifest: Mapping[str, Any],
    validation: ManifestValidation,
    track: ScienceTrack | None,
    arm: Mapping[str, Any],
    cells: Mapping[tuple[str, str], list[dict[str, Any]]],
    stray: Sequence[Mapping[str, Any]],
    prerequisite_state: str | None,
    item_uncertainty: Any,
) -> dict[str, Any]:
    control_id = manifest["control_arm"]["arm_id"]
    arm_ids = {
        manifest["control_arm"]["arm_id"],
        *(a["arm_id"] for a in manifest["candidate_arms"]),
    }
    candidate_id = arm["arm_id"]
    reasons: list[str] = []
    pairs_out: list[dict[str, Any]] = []
    complete_pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    violations: list[dict[str, Any]] = []
    tokenizer_mismatch = False
    for entry in manifest["replicate_roster"]:
        tuple_id = entry["tuple_id"]
        pair: dict[str, Any] = {"tuple_id": tuple_id, "state": None, "field_diff": None}
        sides: dict[str, dict[str, Any] | None] = {}
        for side, arm_id in (("control", control_id), ("candidate", candidate_id)):
            attempts = cells.get((arm_id, tuple_id), [])
            # Only attempts that reached their declared budget compete for the
            # replicate; partial ones are retries/interruptions, kept visible.
            at_budget = [r for r in attempts if r["at_budget"]]
            pair[f"{side}_attempts"] = [
                {
                    "label": r["label"],
                    "state": r["state"],
                    "run_id": r["run_id"],
                    "at_budget": r["at_budget"],
                }
                for r in attempts
            ]
            if len(at_budget) > 1:
                pair["state"] = "DUPLICATE"
                reasons.append(
                    f"{tuple_id}/{arm_id}: {len(at_budget)} attempts of one replicate identity; "
                    f"same-seed reruns are repeats, not replicates ({REPLICATE_SELECTION_RULE})"
                )
                sides[side] = None
            else:
                sides[side] = at_budget[0] if at_budget else None
        control, candidate = sides.get("control"), sides.get("candidate")
        if pair["state"] == "DUPLICATE":
            pairs_out.append(pair)
            continue
        for side, row in (("control", control), ("candidate", candidate)):
            if row is not None and row["state"] == "invalid":
                pair["state"] = "INELIGIBLE"
                reasons.append(f"{tuple_id}/{side}: " + "; ".join(row["reasons"]))
                violations.extend(
                    {**d, "tuple_id": tuple_id, "side": side} for d in row["manifest_diff"]
                )
        if control is None or candidate is None:
            # A side with attempts but none at budget is an incomplete pair, not a gap.
            partial = any(
                row is None and cells.get((arm_id, tuple_id))
                for row, arm_id in ((control, control_id), (candidate, candidate_id))
            )
            pair["state"] = pair["state"] or ("INCOMPLETE" if partial else "MISSING")
            pair["missing"] = [
                s for s, r in (("control", control), ("candidate", candidate)) if r is None
            ]
            pairs_out.append(pair)
            continue
        if track is not None:
            control_fields = control["_fields"]
            candidate_fields = candidate["_fields"]
            diff = field_diff(
                track,
                manifest["intended_differences"],
                manifest["required_invariants"],
                control_fields,
                candidate_fields,
            )
            pair["field_diff"] = diff
            bad = [d for d in diff if d["status"] in ("VIOLATION", "UNKNOWN")]
            if bad:
                pair["state"] = "INELIGIBLE"
                violations.extend({**d, "tuple_id": tuple_id} for d in bad)
                for d in bad:
                    reasons.append(f"{tuple_id}: field '{d['field']}' {d['status']}: {d['reason']}")
            if control_fields.get("tokenizer_identity") != candidate_fields.get(
                "tokenizer_identity"
            ):
                tokenizer_mismatch = True
        if pair["state"] is None:
            if control["state"] == "incomplete" or candidate["state"] == "incomplete":
                pair["state"] = "INCOMPLETE"
                pair["incomplete"] = {
                    "control": control["reasons"],
                    "candidate": candidate["reasons"],
                }
            else:
                pair["state"] = "COMPLETE"
                complete_pairs.append((tuple_id, control, candidate))
        pairs_out.append(pair)

    for stray_row in stray:
        if stray_row["arm_id"] in (control_id, candidate_id) or stray_row["arm_id"] not in arm_ids:
            reasons.append(f"run '{stray_row['label']}': " + "; ".join(stray_row["reasons"]))
    metric = manifest["primary_metric"]["name"]
    if tokenizer_mismatch and metric in TOKENIZER_DEPENDENT_METRICS:
        reasons.append(
            f"cross_tokenizer_primary_metric: '{metric}' depends on the tokenizer and cannot "
            "declare a quality winner across tokenizers; BPB on identical canonical bytes "
            "is required"
        )
    if track is None or not track.certified:
        reasons.append("track has no certified eligibility semantics in M4")
    eligible = validation.valid and not reasons
    n_required = len(manifest["replicate_roster"])
    stages = [p["state"] for p in pairs_out]
    result: dict[str, Any] = {
        "arm_id": candidate_id,
        "label": arm["label"],
        "intervention": dict(arm["intervention"]),
        "eligible": eligible,
        "ineligible_reasons": reasons,
        "field_violations": violations,
        "pairing": {
            "rule": "explicit_replicate_identity_v1",
            "replicate_selection_rule": REPLICATE_SELECTION_RULE,
            "n_required": n_required,
            "n_complete": len(complete_pairs),
            "states": stages,
            "pairs": pairs_out,
        },
        "statistics": None,
        "decision": None,
        "guardrails": [],
        "secondary": [],
        "curve": None,
        "efficiency": None,
        "item_level_uncertainty": None,
    }
    if item_uncertainty is not None:
        result["item_level_uncertainty"] = {
            "source": "within_run_item_or_cluster_bootstrap",
            "used_for_decision": False,
            "note": "item-level uncertainty is reported separately and never replaces or "
            "narrows the between-seed interval",
            "value": item_uncertainty,
        }
    decision: Decision | None = None
    stats: PairedSeedStatistics | None = None
    guardrails: list[GuardrailOutcome] = []
    efficiency_shown: bool | None = None
    efficiency_reason = "no efficiency threshold declared"
    level = float(manifest["ci_level"])
    if eligible and complete_pairs:
        direction = MetricDirection(manifest["primary_metric"]["direction"])
        values = [
            PairValue(t, float(c["primary"]), float(d["primary"])) for t, c, d in complete_pairs
        ]
        family = manifest["multiplicity"]
        stats = _stats(values, metric, direction, level, int(family["family_size"]))
        assert stats is not None
        result["statistics"] = {
            **stats.to_dict(),
            "multiplicity": {
                "method": "bonferroni",
                "family_id": family["family_id"],
                "family_size": family["family_size"],
                "source": "frozen in the comparison manifest before outcomes",
            },
        }
        question = QuestionType(manifest["question"])
        decision = classify_effect(
            stats,
            question=question,
            practical_margin=manifest["practical_margin"],
            noninferiority_margin=manifest["noninferiority_margin"],
        )
        g_size = int(family["guardrail_family_size"])
        for spec in declared_guardrails(manifest):
            name = spec["name"]
            g_pairs = [
                PairValue(t, float(c["secondary"][name]), float(d["secondary"][name]))
                for t, c, d in complete_pairs
            ]
            g_stats = _stats(g_pairs, name, MetricDirection(spec["direction"]), level, g_size)
            outcome = _guardrail(g_stats, name, float(spec["regression_margin"]))
            guardrails.append(outcome)
            result["guardrails"].append(
                {
                    "name": name,
                    "margin": spec["regression_margin"],
                    "status": outcome.status,
                    "reason": outcome.reason,
                    "statistics": g_stats.to_dict() if g_stats else None,
                    "family": "guardrail family (Bonferroni, separate from the primary)",
                }
            )
        for spec in manifest["secondary_metrics"]:
            if spec["role"] != "descriptive":
                continue
            name = spec["name"]
            d_pairs = [
                PairValue(t, float(c["secondary"][name]), float(d["secondary"][name]))
                for t, c, d in complete_pairs
                if c["secondary"].get(name) is not None and d["secondary"].get(name) is not None
            ]
            missing = len(complete_pairs) - len(d_pairs)
            d_stats = _stats(d_pairs, name, MetricDirection(spec["direction"]), level, 1)
            result["secondary"].append(
                {
                    "name": name,
                    "role": "descriptive (unadjusted, outside the claim family)",
                    "coverage": f"{len(d_pairs)}/{len(complete_pairs)} pairs"
                    + (" PARTIAL" if missing else ""),
                    "statistics": d_stats.to_dict() if d_stats else None,
                }
            )
        curve = manifest["curve_metric"]
        if curve is not None:
            result["curve"] = _curve(curve, complete_pairs, level)
        efficiency = manifest["efficiency"]
        if efficiency is not None:
            e_direction = MetricDirection(efficiency["direction"])
            gains, problems = _relative_gains(complete_pairs, efficiency["name"], e_direction)
            e_stats = _stats(
                gains,
                f"relative_gain:{efficiency['name']}",
                MetricDirection.HIGHER_IS_BETTER,
                level,
                1,
            )
            threshold = float(efficiency["minimum_relative_gain"])
            if problems:
                efficiency_shown, efficiency_reason = False, "; ".join(problems)
            elif e_stats is None or e_stats.ci_improvement is None:
                efficiency_shown, efficiency_reason = False, "n < 2: no timing interval"
            else:
                low = e_stats.ci_improvement[0]
                efficiency_shown = low > 0 and not strictly_less(
                    e_stats.mean_improvement, threshold
                )
                efficiency_reason = (
                    f"mean relative gain {e_stats.mean_improvement:.4g} vs frozen minimum "
                    f"{threshold:.4g}; interval lower bound {low:.4g} (must exclude zero)"
                )
            result["efficiency"] = {
                "metric": efficiency["name"],
                "minimum_relative_gain": threshold,
                "shown": efficiency_shown,
                "reason": efficiency_reason,
                "statistics": e_stats.to_dict() if e_stats else None,
                "measurement_source": "operator-declared measurements; not receipt-bound in M4",
            }
    if not eligible:
        result["decision"] = {
            "decision_rule_version": DECISION_RULE_VERSION,
            "result": DecisionResult.INELIGIBLE.value,
            "reasons": result["ineligible_reasons"],
        }
    elif len(complete_pairs) < n_required:
        provisional = decision.to_dict() if decision is not None else None
        result["decision"] = {
            "decision_rule_version": DECISION_RULE_VERSION,
            "result": DecisionResult.INCOMPLETE.value,
            "reasons": [
                f"{len(complete_pairs)} of {n_required} registered pairs complete; missing or "
                "incomplete pairs are not dropped"
            ],
            "provisional_view": provisional,
            "provisional_note": "descriptive view of the complete pairs; never a confirmation",
        }
    elif decision is not None:
        result["decision"] = decision.to_dict()
        if manifest["study_stage"] != "confirmation":
            result["decision"]["scope"] = (
                "exploratory screen/replication: decision support only, never a confirmation"
            )
    pair_improvements = stats.improvements if stats is not None else ()
    order_ids = {t: str(c["_fields"].get("order_manifest_id")) for t, c, _ in complete_pairs}
    membership_ids = {
        t: str(c["_fields"].get("canonical_membership_id")) for t, c, _ in complete_pairs
    }
    promotion = evaluate_promotion_v1(
        manifest,
        validation.valid,
        [f"{p.field}: {p.problem}" for p in validation.problems],
        [f"{p.field}: {p.problem}" for p in validation.promotion_blockers],
        CandidateEvidence(
            arm_id=candidate_id,
            eligible=eligible,
            ineligible_reasons=tuple(reasons),
            n_complete_pairs=len(complete_pairs),
            n_required_pairs=n_required,
            decision=decision.result if decision is not None else None,
            pair_improvements=tuple(pair_improvements),
            guardrails=tuple(guardrails),
            efficiency_shown=efficiency_shown,
            efficiency_reason=efficiency_reason,
            order_manifest_ids=order_ids,
            canonical_membership_ids=membership_ids,
        ),
        prerequisite_state=prerequisite_state,
    )
    result["promotion"] = promotion.to_dict()
    return result


def _curve(
    curve: Mapping[str, Any],
    complete_pairs: list[tuple[str, dict[str, Any], dict[str, Any]]],
    level: float,
) -> dict[str, Any]:
    start, end = int(curve["start_target"]), int(curve["end_target"])
    per_pair: list[dict[str, Any]] = []
    values: list[PairValue] = []
    for tuple_id, control, candidate in complete_pairs:
        entry: dict[str, Any] = {"tuple_id": tuple_id, "status": None}
        if control["curve_points"] is None or candidate["curve_points"] is None:
            entry.update(
                status="INCOMPLETE",
                reason=control["curve_reason"] or candidate["curve_reason"],
            )
            per_pair.append(entry)
            continue
        grid_c = [(p[0], p[1]) for p in control["curve_points"]]
        grid_d = [(p[0], p[1]) for p in candidate["curve_points"]]
        if grid_c != grid_d:
            entry.update(
                status="INCOMPARABLE",
                reason="arms differ in planned thresholds or actual committed target points",
            )
            per_pair.append(entry)
            continue
        try:
            a = fixed_linear_target_area([tuple(p) for p in control["curve_points"]], start, end)
            b = fixed_linear_target_area([tuple(p) for p in candidate["curve_points"]], start, end)
        except CurveError as exc:
            entry.update(status="INCOMPLETE", reason=str(exc))
            per_pair.append(entry)
            continue
        entry.update(
            status="COMPLETE", control_area=a, candidate_area=b, grid=[list(p) for p in grid_c]
        )
        values.append(PairValue(tuple_id, a, b))
        per_pair.append(entry)
    complete = len(values) == len(complete_pairs) and bool(values)
    stats = (
        _stats(values, f"curve_area:{curve['name']}", MetricDirection(curve["direction"]), level, 1)
        if complete
        else None
    )
    return {
        "method": LINEAR_TARGET_AREA_VERSION,
        "x_axis": "actual committed targets (never wall time)",
        "interval": [start, end],
        "complete": complete,
        "pairs": per_pair,
        "statistics": stats.to_dict() if stats else None,
        "role": "reported separately from the endpoint; never chosen post hoc",
    }


# ----------------------------------------------------------------- entry point


def _prerequisite_state(
    manifest: Mapping[str, Any], prerequisites: Sequence[Mapping[str, Any]]
) -> str | None:
    wanted = (manifest.get("scale_promotion") or {}).get("prerequisite")
    if not isinstance(wanted, Mapping):
        return None
    for record in prerequisites:
        verify_comparison_record(record)
        if record["manifest_hash"] != wanted.get("manifest_hash"):
            continue
        states = [c["promotion"]["state"] for c in record["candidates"]]
        if wanted.get("required_state") in states:
            return str(wanted["required_state"])
        return states[0] if states else None
    return None


def compare_science(
    manifest: Mapping[str, Any],
    runs: Sequence[RunEntry],
    *,
    prerequisites: Sequence[Mapping[str, Any]] = (),
    item_uncertainty: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable science-v1 comparison record."""
    validation = validate_manifest(manifest)
    labels = [r.label for r in runs]
    if len(set(labels)) != len(labels):
        raise ComparisonError("run labels must be unique")
    track = get_track(str(manifest.get("track"))) if isinstance(manifest, Mapping) else None
    run_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    evidence_seen: dict[str, str] = {}
    if validation.valid:
        arms = {a["arm_id"]: a for a in (manifest["control_arm"], *manifest["candidate_arms"])}
        roster = roster_by_identity(manifest)
        cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
        stray: list[dict[str, Any]] = []
        for entry in runs:
            row = _analyze_run(entry, manifest, arms, roster)
            digest = row["evidence_digest"]
            if digest is not None and digest in evidence_seen:
                row["state"] = "invalid"
                row["reasons"].append(
                    f"the same evidence record is listed twice (also '{evidence_seen[digest]}')"
                )
            elif digest is not None:
                evidence_seen[digest] = entry.label
            run_rows.append(row)
            if row["tuple_id"] is not None and row["state"] != "failed":
                row["_fields"] = evidence_fields(entry.evidence) if entry.evidence else {}
                cells.setdefault((entry.arm_id, row["tuple_id"]), []).append(row)
            elif row["state"] == "invalid":
                # Unknown arm, unverifiable evidence or an unregistered seed tuple:
                # an integrity problem for the comparison, never silently ignored.
                stray.append(row)
        prerequisite_state = _prerequisite_state(manifest, prerequisites)
        for arm in manifest["candidate_arms"]:
            uncertainty = (item_uncertainty or {}).get(arm["arm_id"])
            candidates.append(
                _candidate(
                    manifest, validation, track, arm, cells, stray, prerequisite_state, uncertainty
                )
            )
    else:
        for entry in runs:
            run_rows.append(
                {
                    "label": entry.label,
                    "arm_id": entry.arm_id,
                    "declared_status": entry.status,
                    "failure": entry.failure,
                    "synthetic": entry.synthetic,
                    "state": "not_analyzed",
                    "reasons": ["manifest invalid: no run is analyzed"],
                }
            )
    for row in run_rows:
        row.pop("_fields", None)
    states = [c["promotion"]["state"] for c in candidates]
    record: dict[str, Any] = {
        "record_version": COMPARISON_RECORD_VERSION,
        "versions": {
            "manifest": MANIFEST_VERSION,
            "evidence": EVIDENCE_VERSION,
            "tracks": TRACK_TABLE_VERSION,
            "statistics": STATISTICS_VERSION,
            "decision": DECISION_RULE_VERSION,
            "promotion": PROMOTION_RULE_VERSION,
            "curve_area": LINEAR_TARGET_AREA_VERSION,
        },
        "manifest_hash": validation.manifest_hash,
        "manifest": dict(manifest) if isinstance(manifest, Mapping) else None,
        "manifest_validation": validation.to_dict(),
        "track": track.to_dict() if track is not None else None,
        "uncertainty_sources": {
            "decision": UNCERTAINTY_SOURCE,
            "item_level": "reported separately when supplied; never used for decisions",
        },
        "synthetic_evidence": any(r.synthetic for r in runs),
        "runs": run_rows,
        "candidates": candidates,
        "summary": {
            "manifest_valid": validation.valid,
            "candidate_states": dict(zip([c["arm_id"] for c in candidates], states, strict=True)),
            "failed_or_excluded_runs": sum(1 for r in run_rows if r["state"] != "complete"),
        },
        "executes_nothing": True,
    }
    if not validation.valid:
        record["summary"]["overall"] = DecisionResult.INELIGIBLE.value
    record["record_hash"] = identity_digest(record)
    return record


def verify_comparison_record(record: Mapping[str, Any]) -> None:
    """Refuse legacy or altered records: a report never changes the decision."""
    if record.get("record_version") != COMPARISON_RECORD_VERSION:
        raise ComparisonError(
            "not a science-v1 comparison record (legacy P17 outputs keep their historical "
            "meaning and are never re-evaluated under P35)"
        )
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    if record.get("record_hash") != identity_digest(payload):
        raise ComparisonError("comparison record hash does not verify (record was altered)")
    manifest = record.get("manifest")
    if manifest is not None and identity_digest(manifest) != record.get("manifest_hash"):
        raise ComparisonError("embedded manifest does not match the bound manifest hash")


def summary_state(record: Mapping[str, Any]) -> str:
    """One word for a record: the manifest verdict or the candidates' promotion states."""
    if not record["manifest_validation"]["valid"]:
        return DecisionResult.INELIGIBLE.value
    states = {c["promotion"]["state"] for c in record["candidates"]}
    return ",".join(sorted(states)) if states else PromotionState.NOT_ELIGIBLE.value


def load_run_roster(
    doc: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]
) -> list[RunEntry]:
    """Build run entries from a roster document and already-extracted evidence by label."""
    if doc.get("roster_version") != RUN_ROSTER_VERSION:
        raise ComparisonError(f"run roster must be '{RUN_ROSTER_VERSION}'")
    entries: list[RunEntry] = []
    for item in doc.get("runs", []):
        if not isinstance(item, Mapping):
            raise ComparisonError("each roster run must be a mapping")
        label = item.get("label")
        if not isinstance(label, str) or not label:
            raise ComparisonError("each roster run needs a label")
        measurements = item.get("measurements") or {}
        for name, value in measurements.items():
            if not (
                isinstance(value, Mapping)
                and set(value) == {"value", "source"}
                and isinstance(value["source"], str)
                and value["source"].strip()
            ):
                raise ComparisonError(
                    f"run '{label}' measurement '{name}' must be {{value, source}} with a "
                    "declared source"
                )
        entries.append(
            RunEntry(
                label=label,
                arm_id=str(item.get("arm_id")),
                status=str(item.get("status")),
                failure=item.get("failure"),
                evidence=evidence.get(label),
                measurements=measurements,
                synthetic=bool(item.get("synthetic", False)),
            )
        )
    return entries


__all__ = [
    "COMPARISON_RECORD_VERSION",
    "ComparisonError",
    "RUN_ROSTER_VERSION",
    "RunEntry",
    "StatisticsError",
    "compare_science",
    "field_diff",
    "load_run_roster",
    "summary_state",
    "verify_comparison_record",
]
